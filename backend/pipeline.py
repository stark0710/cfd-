"""CFD pipeline v1: STEP -> (Gmsh) closed surface STL -> OpenFOAM case (snappyHexMesh, simpleFoam, kOmegaSST) -> results.
OpenFOAM runs through Docker (image OPENFOAM_IMAGE) with the entrypoint bypassed.
HONESTY: no prism layers, no y+ check, unvalidated. Status can therefore never be 'completed successfully' in v1.
Nothing here fabricates numbers: anything not computed is None / 'unavailable'."""
import glob, json, math, os, re, subprocess
from string import Template

IMAGE = os.environ.get("OPENFOAM_IMAGE", "opencfd/openfoam-default:2312")
BASHRC = os.environ.get("OPENFOAM_BASHRC", "/usr/lib/openfoam/openfoam2312/etc/bashrc")
MESH_LEVELS = os.environ.get("MESH_LEVELS", "3 4")          # snappyHexMesh surface refinement (coarse preset)
END_TIME = int(os.environ.get("END_TIME", "600"))
CASE = dict(V=25.0, H=0.0, alpha=4.0, beta=0.0)              # fixed standard case (m/s, m, deg, deg)
STAGES = ["Geometry inspection", "Domain setup", "Meshing", "Solver setup", "Solving", "Post-processing", "Report generation"]

class PipelineError(Exception):
    def __init__(self, code, msg): super().__init__(msg); self.code = code

def isa(h):
    T = 288.15-0.0065*h; p = 101325*(T/288.15)**5.25588; rho = p/(287.058*T)
    mu = 1.458e-6*T**1.5/(T+110.4); return dict(T=T, p=p, rho=rho, mu=mu, nu=mu/rho, a=math.sqrt(1.4*287.058*T))

def wind(V, alpha, beta):
    a, b = math.radians(alpha), math.radians(beta)   # flow toward +X; +alpha = flow from below; +beta = wind from starboard (+Y)
    U = (V*math.cos(a)*math.cos(b), -V*math.sin(b), V*math.sin(a)*math.cos(b))
    d = tuple(u/V for u in U); z = (0, 0, 1); dz = sum(x*y for x, y in zip(z, d))
    l = tuple(z[i]-dz*d[i] for i in range(3)); n = math.sqrt(sum(x*x for x in l)); return U, d, tuple(x/n for x in l)

# ---------- geometry ----------
def geometry_to_stl(step_path, stl_path):
    import gmsh
    gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0)
    try:
        try: gmsh.option.setString("Geometry.OCCTargetUnit", "M")
        except Exception: pass
        gmsh.model.add("m")
        try: gmsh.model.occ.importShapes(step_path); gmsh.model.occ.synchronize()
        except Exception as e: raise PipelineError("bad_step", f"Could not read STEP file: {e}")
        vols, faces = gmsh.model.getEntities(3), gmsh.model.getEntities(2)
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(-1, -1)
        L = max(x1-x0, y1-y0, z1-z0); info = dict(solids=len(vols), faces=len(faces), bbox_m=[x0, y0, z0, x1, y1, z1], length_scale_m=L, warnings=[])
        if not vols: raise PipelineError("needs_input", "No solid body found (open surfaces?). A watertight solid is required to define the fluid domain.")
        if len(vols) > 1: raise PipelineError("needs_input", f"{len(vols)} separate solids found. Merge the aircraft into one watertight solid (v1 does not fuse parts).")
        if L > 20 or L < 0.02: raise PipelineError("needs_input", f"Largest model dimension is {L:.4g} m after unit conversion; STEP units may be wrong. Please confirm size/units.")
        gmsh.option.setNumber("Mesh.MeshSizeMax", L/60); gmsh.option.setNumber("Mesh.MeshSizeMin", L/2000)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 30); gmsh.model.mesh.generate(2)
        nt, nc, _ = gmsh.model.mesh.getNodes(); X = {int(t): (nc[3*i], nc[3*i+1], nc[3*i+2]) for i, t in enumerate(nt)}
        tris = []
        for ty, _, nodes in zip(*gmsh.model.mesh.getElements(2)):
            if ty == 2: tris += [tuple(int(n) for n in nodes[i:i+3]) for i in range(0, len(nodes), 3)]
        ec = {}
        for a, b, c in tris:
            for e in ((a, b), (b, c), (c, a)): k = tuple(sorted(e)); ec[k] = ec.get(k, 0)+1
        open_e = sum(1 for v in ec.values() if v == 1); nonman = sum(1 for v in ec.values() if v > 2)
        area = 0.0
        with open(stl_path, "w") as f:
            f.write("solid aircraft\n")
            for a, b, c in tris:
                p, q, r = X[a], X[b], X[c]; u = [q[i]-p[i] for i in range(3)]; v = [r[i]-p[i] for i in range(3)]
                n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]; m = math.sqrt(sum(x*x for x in n))
                if m < 1e-18: continue
                area += m/2; f.write(f" facet normal {n[0]/m:.6e} {n[1]/m:.6e} {n[2]/m:.6e}\n  outer loop\n")
                for w in (p, q, r): f.write(f"   vertex {w[0]:.8e} {w[1]:.8e} {w[2]:.8e}\n")
                f.write("  endloop\n endfacet\n")
            f.write("endsolid aircraft\n")
        info.update(triangles=len(tris), open_edges=open_e, nonmanifold_edges=nonman, wetted_area_m2=area)
        if open_e or nonman: raise PipelineError("needs_input", f"Surface mesh is not watertight ({open_e} open edges, {nonman} non-manifold). Repair the CAD model.")
        info["warnings"] += ["Orientation not stored in STEP: assumed nose toward -X, +Y starboard, +Z up.",
                             "Watertightness verified on the Gmsh surface mesh; CAD-level defects may still exist."]
        return info
    finally: gmsh.finalize()

# ---------- case files ----------
HDR = "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class $cls;\n    object $obj;\n}\n"
def _w(case, rel, cls, obj, body, **kw):
    p = os.path.join(case, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(Template(HDR+body).safe_substitute(cls=cls, obj=obj, **kw))

def write_case(case, info, atm):
    x0, y0, z0, x1, y1, z1 = info["bbox_m"]; L = info["length_scale_m"]; V = CASE["V"]
    U, d, lift = wind(V, CASE["alpha"], CASE["beta"])
    dom = [x0-3*L, y0-3*L, z0-3*L, x1+6*L, y1+3*L, z1+3*L]; h = L/6
    n = [max(4, math.ceil((dom[i+3]-dom[i])/h)) for i in range(3)]
    loc = ((dom[0]+x0)/2, (y0+y1)/2, (z0+z1)/2)
    k0 = 1.5*(0.01*V)**2; om0 = k0/(10*atm["nu"]); Us = "(%g %g %g)" % U
    _w(case, "system/blockMeshDict", "dictionary", "blockMeshDict", """scale 1;
vertices ( ($x0 $y0 $z0) ($x1 $y0 $z0) ($x1 $y1 $z0) ($x0 $y1 $z0) ($x0 $y0 $z1) ($x1 $y0 $z1) ($x1 $y1 $z1) ($x0 $y1 $z1) );
blocks ( hex (0 1 2 3 4 5 6 7) ($nx $ny $nz) simpleGrading (1 1 1) );
edges ();
boundary ( farfield { type patch; faces ( (0 3 2 1) (4 5 6 7) (0 1 5 4) (3 7 6 2) (0 4 7 3) (1 2 6 5) ); } );
mergePatchPairs ();
""", x0=dom[0], y0=dom[1], z0=dom[2], x1=dom[3], y1=dom[4], z1=dom[5], nx=n[0], ny=n[1], nz=n[2])
    _w(case, "system/snappyHexMeshDict", "dictionary", "snappyHexMeshDict", """castellatedMesh true; snap true; addLayers false;
geometry { aircraft.stl { type triSurfaceMesh; name aircraft; } }
castellatedMeshControls { maxLocalCells 1000000; maxGlobalCells 3000000; minRefinementCells 10; maxLoadUnbalance 0.10; nCellsBetweenLevels 3; features ();
  refinementSurfaces { aircraft { level ($lv); patchInfo { type wall; } } }
  resolveFeatureAngle 30; refinementRegions {} locationInMesh ($lx $ly $lz); allowFreeStandingZoneFaces true; }
snapControls { nSmoothPatch 3; tolerance 2.0; nSolveIter 50; nRelaxIter 5; nFeatureSnapIter 10; implicitFeatureSnap false; explicitFeatureSnap false; multiRegionFeatureSnap false; }
addLayersControls { relativeSizes true; layers {} expansionRatio 1.0; finalLayerThickness 0.3; minThickness 0.1; nGrow 0; featureAngle 60; nRelaxIter 3;
  nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50; }
meshQualityControls { maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minVol 1e-13; minTetQuality 1e-15; minArea -1; minTwist 0.02;
  minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75; }
mergeTolerance 1e-6; debug 0;
""", lv=MESH_LEVELS, lx=loc[0], ly=loc[1], lz=loc[2])
    _w(case, "system/controlDict", "dictionary", "controlDict", """application simpleFoam; startFrom startTime; startTime 0; stopAt endTime; endTime $et; deltaT 1;
writeControl timeStep; writeInterval 100; purgeWrite 2; writeFormat ascii; writePrecision 8; timeFormat general; timePrecision 6; runTimeModifiable true;
functions { forces { type forces; libs ("libforces.so"); patches ("aircraft.*"); rho rhoInf; rhoInf $rho; CofR (0 0 0); writeControl timeStep; writeInterval 1; } }
""", et=END_TIME, rho=atm["rho"])
    _w(case, "system/fvSchemes", "dictionary", "fvSchemes", """ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss limitedLinear 1; div(phi,omega) bounded Gauss limitedLinear 1;
  div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; } wallDist { method meshWave; }
""")
    _w(case, "system/fvSolution", "dictionary", "fvSolution", """solvers { p { solver GAMG; smoother GaussSeidel; tolerance 1e-6; relTol 0.1; }
  "(U|k|omega)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; } }
SIMPLE { nNonOrthogonalCorrectors 1; consistent yes; residualControl { p 1e-4; U 1e-5; "(k|omega)" 1e-5; } }
relaxationFactors { equations { U 0.9; ".*" 0.9; } }
""")
    _w(case, "constant/transportProperties", "dictionary", "transportProperties", "transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] $nu;\n", nu=atm["nu"])
    _w(case, "constant/turbulenceProperties", "dictionary", "turbulenceProperties", "simulationType RAS;\nRAS { model kOmegaSST; turbulence on; printCoeffs on; }\n")
    def field(name, dims, internal, far, wall, cls="volScalarField"):
        _w(case, f"0/{name}", cls, name, "dimensions $dims;\ninternalField uniform $i;\nboundaryField { farfield { $far } \"aircraft.*\" { $wall } }\n", dims=dims, i=internal, far=far, wall=wall)
    field("U", "[0 1 -1 0 0 0 0]", Us, f"type freestream; freestreamValue uniform {Us};", "type noSlip;", "volVectorField")
    field("p", "[0 2 -2 0 0 0 0]", "0", "type freestreamPressure; freestreamValue uniform 0;", "type zeroGradient;")
    field("k", "[0 2 -2 0 0 0 0]", "%g" % k0, f"type freestream; freestreamValue uniform {k0:g};", f"type kqRWallFunction; value uniform {k0:g};")
    field("omega", "[0 0 -1 0 0 0 0]", "%g" % om0, f"type freestream; freestreamValue uniform {om0:g};", f"type omegaWallFunction; value uniform {om0:g};")
    field("nut", "[0 2 -1 0 0 0 0]", "0", "type freestream; freestreamValue uniform 0;", "type nutkWallFunction; value uniform 0;")
    return dict(domain_m=dom, background_cells=n, location_in_mesh=loc, wind_dir=d, lift_dir=lift, U=U, k=k0, omega=om0)

# ---------- running OpenFOAM ----------
def of_run(case, cmd, log, on_line=None):
    args = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-v", f"{case}:/case", "-w", "/case",
            "--entrypoint", "bash", IMAGE, "-c", f"source {BASHRC} && {cmd}"]
    with open(os.path.join(case, log), "w") as lf:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            lf.write(line)
            if on_line: on_line(line.rstrip())
        return p.wait()

def parse_checkmesh(text):
    g = lambda pat: (m.group(1) if (m := re.search(pat, text)) else None)
    f = lambda s: float(s) if s is not None else None
    return dict(cells=int(g(r"cells:\s+(\d+)")) if g(r"cells:\s+(\d+)") else None, max_non_orthogonality=f(g(r"Max non-orthogonality = ([\d.eE+-]+)")),
                max_skewness=f(g(r"Max skewness = ([\d.eE+-]+)")), mesh_ok="Mesh OK." in text, failed_checks=g(r"Failed (\d+) mesh checks"))

class ResidualParser:
    def __init__(self, emit): self.emit, self.cur, self.it, self.converged = emit, {}, 0, False
    def feed(self, line):
        if m := re.match(r"Time = (\d+)", line):
            self.flush(); self.it = int(m.group(1))
        elif m := re.search(r"Solving for (\w+), Initial residual = ([\d.eE+-]+)", line): self.cur[m.group(1)] = float(m.group(2))
        elif "SIMPLE solution converged" in line: self.converged = True
    def flush(self):
        if self.cur: self.emit(type="residual", it=self.it, vals=dict(self.cur)); self.cur = {}

def parse_forces(case):
    fs = sorted(glob.glob(os.path.join(case, "postProcessing", "forces", "*", "force*.dat")))
    rows = []
    if fs:
        for ln in open(fs[-1]):
            if ln.startswith("#"): continue
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", ln.replace("(", " ").replace(")", " "))
            if len(nums) >= 4: rows.append([float(x) for x in nums[:4]])
    return rows   # [time, Fx, Fy, Fz] total (pressure+viscous), Newtons

def dot(a, b): return sum(x*y for x, y in zip(a, b))
def force_summary(rows, drag_dir, lift_dir):
    if not rows: return None
    D = [dot(r[1:4], drag_dir) for r in rows]; Lf = [dot(r[1:4], lift_dir) for r in rows]
    tail = max(10, len(rows)//5); dt, lt = D[-tail:], Lf[-tail:]
    drift = lambda a: (max(a)-min(a))/abs(sum(a)/len(a)) if abs(sum(a)/len(a)) > 1e-12 else None
    return dict(lift_N=Lf[-1], drag_N=D[-1], lift_to_drag=(Lf[-1]/D[-1] if abs(D[-1]) > 1e-12 else None), window_iterations=tail, lift_drift=drift(lt), drag_drift=drift(dt))

# ---------- main entry ----------
def run_pipeline(step_path, workdir, emit):
    case = os.path.abspath(os.path.join(workdir, "case")); os.makedirs(os.path.join(case, "constant", "triSurface"), exist_ok=True)
    res = dict(demo=False, status="failed", warnings=[], settings=dict(CASE, mesh_levels=MESH_LEVELS, end_time=END_TIME, solver=f"simpleFoam kOmegaSST ({IMAGE})"))
    stage = lambda i: emit(type="stage", i=i)
    try:
        stage(0); atm = isa(CASE["H"]); res["air"] = atm
        info = geometry_to_stl(step_path, os.path.join(case, "constant", "triSurface", "aircraft.stl")); res["geometry"] = info; res["warnings"] += info["warnings"]
        emit(type="log", m=f"Geometry: {info['solids']} solid, {info['faces']} faces, size {info['length_scale_m']:.3f} m, {info['triangles']} surface triangles, watertight")
        stage(1); meta = write_case(case, info, atm); res["domain"] = meta
        emit(type="log", m=f"Domain box and OpenFOAM case written; background cells {meta['background_cells']}")
        stage(2)
        if of_run(case, "blockMesh", "log.blockMesh", lambda l: None): raise PipelineError("blockmesh_failed", "blockMesh failed (see log.blockMesh)")
        if of_run(case, "snappyHexMesh -overwrite", "log.snappyHexMesh", lambda l: emit(type="log", m=l) if re.match(r"(Surface snapping|Mesh refinement|Layer addition|Writing mesh)", l) else None):
            raise PipelineError("snappy_failed", "snappyHexMesh failed (see log.snappyHexMesh)")
        of_run(case, "checkMesh", "log.checkMesh"); mq = parse_checkmesh(open(os.path.join(case, "log.checkMesh")).read()); res["mesh"] = mq
        emit(type="log", m=f"Mesh: {mq['cells']} cells, max non-orthogonality {mq['max_non_orthogonality']}, max skewness {mq['max_skewness']}, checkMesh OK={mq['mesh_ok']}")
        if not mq["mesh_ok"]: raise PipelineError("mesh_failed", f"checkMesh reported {mq['failed_checks'] or 'unknown'} failed checks; not solving on a bad mesh.")
        stage(3); emit(type="log", m=f"Solver setup: V={CASE['V']} m/s, alpha={CASE['alpha']} deg, beta={CASE['beta']} deg, rho={atm['rho']:.4f}, nu={atm['nu']:.3e}")
        stage(4); rp = ResidualParser(emit)
        if of_run(case, "simpleFoam", "log.simpleFoam", rp.feed): raise PipelineError("solver_failed", "simpleFoam exited with an error (see log.simpleFoam)")
        rp.flush(); res["residual_criteria_met"] = rp.converged; res["iterations"] = rp.it
        stage(5); fs = force_summary(parse_forces(case), meta["wind_dir"], meta["lift_dir"]); res["forces"] = fs
        if fs is None: res["warnings"].append("Force output not found: lift/drag unavailable.")
        else:
            if (fs["lift_drift"] is None or fs["lift_drift"] > 0.02) or (fs["drag_drift"] is None or fs["drag_drift"] > 0.02):
                res["warnings"].append(f"Lift/drag still changing over the last {fs['window_iterations']} iterations (>2% spread): forces NOT steady.")
        if not rp.converged: res["warnings"].append(f"Residual targets not met within {END_TIME} iterations.")
        res["warnings"] += ["No prism layers: wall-function on a coarse mesh; drag is not reliable and y+ was not evaluated.", "Not validated against reference data yet.",
                            "Force coefficients unavailable: reference area/length are not defined for this geometry. Forces are in Newtons.",
                            "Pressure/velocity field plots are not produced by v1 (results are in the case folder)."]
        res["status"] = "completed_with_warnings"
    except PipelineError as e:
        res["error"] = dict(code=e.code, message=str(e)); emit(type="error", code=e.code, m=str(e))
    except FileNotFoundError as e:
        res["error"] = dict(code="tool_missing", message=str(e)); emit(type="error", code="tool_missing", m=f"Required tool missing: {e}")
    stage(6); rep = make_report(res); open(os.path.join(workdir, "report.md"), "w").write(rep); json.dump(res, open(os.path.join(workdir, "results.json"), "w"), indent=1, default=str)
    emit(type="report", text=rep); emit(type="done", status=res["status"], results=res); return res

def make_report(r):
    s = r["settings"]; a = r.get("air", {}); f = r.get("forces") or {}; m = r.get("mesh") or {}; g = r.get("geometry") or {}
    fmt = lambda v, u="": "unavailable" if v is None else f"{v:.4g} {u}".strip()
    L = ["# CFD run report (fixed-wing, v1)", "", f"**Status: {r['status'].replace('_', ' ').upper()}** (real OpenFOAM run, not demo)", ""]
    if r.get("error"): L += [f"**Error [{r['error']['code']}]:** {r['error']['message']}", ""]
    L += ["## Settings (fixed standard case)", f"- Airspeed {s['V']} m/s, altitude {s['H']} m (ISA), AoA {s['alpha']} deg, sideslip {s['beta']} deg",
          f"- Density {a.get('rho', float('nan')):.4f} kg/m3, kinematic viscosity {a.get('nu', float('nan')):.3e} m2/s (calculated, ISA)", f"- Solver: {s['solver']}, {s['end_time']} iteration limit, mesh levels {s['mesh_levels']}",
          "", "## Geometry", f"- Solids {g.get('solids', 'n/a')}, faces {g.get('faces', 'n/a')}, size {fmt(g.get('length_scale_m'), 'm')}, wetted area {fmt(g.get('wetted_area_m2'), 'm2')}",
          "", "## Mesh", f"- Cells {m.get('cells', 'unavailable')}, max non-orthogonality {fmt(m.get('max_non_orthogonality'))}, max skewness {fmt(m.get('max_skewness'))}, checkMesh OK: {m.get('mesh_ok', 'unavailable')}",
          "", "## Results (calculated by the solver)", f"- Lift {fmt(f.get('lift_N'), 'N')}, drag {fmt(f.get('drag_N'), 'N')}, L/D {fmt(f.get('lift_to_drag'))}",
          "- CL, CD: unavailable (no reference area/length defined)", f"- Residual targets met: {r.get('residual_criteria_met', 'unavailable')} (iterations run: {r.get('iterations', 'n/a')}). This alone does not prove convergence or accuracy.", "", "## Warnings and assumptions"]
    return "\n".join(L + [f"- {w}" for w in r["warnings"]] + [""])

"""CFD pipeline v1: STEP -> (Gmsh) closed surface STL -> OpenFOAM case (snappyHexMesh, simpleFoam, kOmegaSST) -> results.
OpenFOAM runs through Docker (image OPENFOAM_IMAGE) with the entrypoint bypassed.
HONESTY: wall-function RANS, unvalidated; prism layers are attempted and their real coverage is reported. Status can therefore never be 'completed successfully' in v1.
Nothing here fabricates numbers: anything not computed is None / 'unavailable'."""
import glob, json, math, os, re, shutil, subprocess
from string import Template

IMAGE = os.environ.get("OPENFOAM_IMAGE", "opencfd/openfoam-default:2312")
BASHRC = os.environ.get("OPENFOAM_BASHRC", "/usr/lib/openfoam/openfoam2312/etc/bashrc")
MESH_LEVELS = os.environ.get("MESH_LEVELS")                  # e.g. "5 5"; unset = auto from chord proxy
END_TIME = int(os.environ.get("END_TIME", "600"))
RES_TARGET = os.environ.get("RES_TARGET", "3e-5")                # residual target for U, k, omega (p stays 1e-4)
NPROC = max(1, int(os.environ.get("NPROC", str(min(8, os.cpu_count() or 1)))))              # MPI processes for snappyHexMesh and simpleFoam (default auto-scaled)
MESH_PROCS = max(1, int(os.environ.get("MESH_PROCS", "6")))   # fixed process count for meshing, so the mesh does not depend on the machine
WAKE_BOX = os.environ.get("WAKE_BOX", "0") == "1"
WAKE_LEVEL = os.environ.get("WAKE_LEVEL")
NOSE_BOX = os.environ.get("NOSE_BOX", "0") == "1"
NOSE_LEVEL = os.environ.get("NOSE_LEVEL")
LAYERS = int(os.environ.get("LAYERS", "3"))                 # prism layers (0 = off)
Y_PLUS_TARGET = float(os.environ.get("Y_PLUS", "40"))       # target wall y+ used to size the first layer (wall functions)
LAYER_EXPANSION = float(os.environ.get("LAYER_EXPANSION", "1.2"))
RESOLVE_ANGLE = os.environ.get("RESOLVE_ANGLE", "5")       # deg: surface curvature above which the max refinement level applies (LE/TE)
NON_ORTHO = int(os.environ.get("NON_ORTHO", "2"))           # non-orthogonal correctors (2 recommended for hex-dominant mesh)
CASE = dict(V=float(os.environ.get("CFD_V", 25)), H=float(os.environ.get("CFD_H", 0)), alpha=float(os.environ.get("CFD_ALPHA", 4)), beta=float(os.environ.get("CFD_BETA", 0)))  # fixed standard case; env overrides are for testing only
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
    with open(p, "w") as fh: fh.write(Template(HDR+body).safe_substitute(cls=cls, obj=obj, **kw))

def write_case(case, info, atm):
    x0, y0, z0, x1, y1, z1 = info["bbox_m"]; L = info["length_scale_m"]; V = CASE["V"]
    U, d, lift = wind(V, CASE["alpha"], CASE["beta"])
    dom_up = float(os.environ.get("DOM_UP", "3.0"))
    dom_down = float(os.environ.get("DOM_DOWN", "6.0"))
    dom = [x0-dom_up*L, y0-dom_up*L, z0-dom_up*L, x1+dom_down*L, y1+dom_up*L, z1+dom_up*L]; h = L/6
    n = [max(4, math.ceil((dom[i+3]-dom[i])/h)) for i in range(3)]
    loc = ((dom[0]+x0)/2, (y0+y1)/2, (z0+z1)/2)
    chord = sorted([x1-x0, y1-y0, z1-z0])[1]                    # chord proxy = middle bounding-box dimension (assumption)
    n_lv = max(3, min(7, math.floor(math.log2(h*40/chord)))) if MESH_LEVELS is None else None
    levels = MESH_LEVELS if MESH_LEVELS else f"{n_lv} {n_lv + 2}"; cell = h/2**int(levels.split()[-1])
    lv = [int(x) for x in levels.split()]
    if len(lv) == 2 and lv[1] - lv[0] > 2 and os.environ.get("ALLOW_LEVEL_JUMP") != "1": raise PipelineError("mesh_levels", f"Mesh levels '{levels}' differ by more than 2: such jumps produced invalid meshes in testing (5 8, nose box). Use a gap of 2 or less, or set ALLOW_LEVEL_JUMP=1 to override.")
    wb = None; nb = None; gbox = rreg = ""
    wb = None; gbox = rreg = ""
    if WAKE_BOX:
        wl = int(WAKE_LEVEL) if WAKE_LEVEL else max(2, int(levels.split()[0]) - 1)
        zc = (z0+z1)/2; bmin = (x0-0.25*chord, y0-0.5*chord, zc-0.75*chord); bmax = (x1+3*chord, y1+0.5*chord, zc+0.75*chord)
        gbox = "wakebox { type searchableBox; min (%g %g %g); max (%g %g %g); }" % (bmin + bmax)
        rreg = "wakebox { mode inside; levels ((1e15 %d)); }" % wl
        wb = dict(level=wl, min_m=bmin, max_m=bmax, cell_m=h/2**wl)
    if NOSE_BOX:
        nose_level = int(NOSE_LEVEL) if NOSE_LEVEL else int(levels.split()[-1]) + 1
        nose_min = (x0-0.01*chord, y0-0.005*chord, z0-0.01*chord)
        nose_max = (x0+0.05*chord, y1+0.005*chord, z1+0.01*chord)
        gbox += " nosebox { type searchableBox; min (%g %g %g); max (%g %g %g); }" % (nose_min + nose_max)
        rreg += " nosebox { mode inside; levels ((1e15 %d)); }" % nose_level
        nb = dict(level=nose_level, min_m=nose_min, max_m=nose_max, cell_m=h/2**nose_level)
    nu = atm["nu"]; Rex = V*chord/2/nu; utau = V*math.sqrt(0.0576*Rex**-0.2/2)          # flat-plate estimate at mid-chord (assumption)
    y1 = 2*Y_PLUS_TARGET*nu/utau; er = LAYER_EXPANSION
    lay = dict(n=LAYERS, first_m=y1, expansion=er, total_m=y1*(er**LAYERS-1)/(er-1) if LAYERS else 0.0, target_yplus=Y_PLUS_TARGET)
    k0 = 1.5*(0.01*V)**2; om0 = k0/(10*atm["nu"]); Us = "(%g %g %g)" % U
    _w(case, "system/blockMeshDict", "dictionary", "blockMeshDict", """scale 1;
vertices ( ($x0 $y0 $z0) ($x1 $y0 $z0) ($x1 $y1 $z0) ($x0 $y1 $z0) ($x0 $y0 $z1) ($x1 $y0 $z1) ($x1 $y1 $z1) ($x0 $y1 $z1) );
blocks ( hex (0 1 2 3 4 5 6 7) ($nx $ny $nz) simpleGrading (1 1 1) );
edges ();
boundary ( farfield { type patch; faces ( (0 3 2 1) (4 5 6 7) (0 1 5 4) (3 7 6 2) (0 4 7 3) (1 2 6 5) ); } );
mergePatchPairs ();
""", x0=dom[0], y0=dom[1], z0=dom[2], x1=dom[3], y1=dom[4], z1=dom[5], nx=n[0], ny=n[1], nz=n[2])
    _w(case, "system/snappyHexMeshDict", "dictionary", "snappyHexMeshDict", """castellatedMesh true; snap true; addLayers $addl;
geometry { aircraft.stl { type triSurfaceMesh; name aircraft; } $gbox }
castellatedMeshControls { maxLocalCells 2000000; maxGlobalCells 5000000; minRefinementCells 10; maxLoadUnbalance 0.10; nCellsBetweenLevels 3; features ();
  refinementSurfaces { aircraft { level ($lv); patchInfo { type wall; } } }
  resolveFeatureAngle $rfa; refinementRegions { $rreg } locationInMesh ($lx $ly $lz); allowFreeStandingZoneFaces true; }
snapControls { nSmoothPatch 3; tolerance 2.0; nSolveIter 50; nRelaxIter 5; nFeatureSnapIter 10; implicitFeatureSnap false; explicitFeatureSnap false; multiRegionFeatureSnap false; }
addLayersControls { relativeSizes false; layers { "aircraft.*" { nSurfaceLayers $nl; } } expansionRatio $er; firstLayerThickness $flt; minThickness $mint; nGrow 0; featureAngle 130; slipFeatureAngle 30; nRelaxIter 5;
  nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50; nRelaxedIter 20; }
meshQualityControls { maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minVol 1e-13; minTetQuality 1e-15; minArea -1; minTwist 0.02;
  minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
  relaxed { maxNonOrtho 70; minTetQuality 1e-30; } }
mergeTolerance 1e-6; debug 0;
""", lv=levels, gbox=gbox, rreg=rreg, rfa=RESOLVE_ANGLE, addl="true" if LAYERS else "false", nl=max(LAYERS, 1), er=er, flt=y1, mint=0.25*y1, lx=loc[0], ly=loc[1], lz=loc[2])
    _w(case, "system/controlDict", "dictionary", "controlDict", """application simpleFoam; startFrom startTime; startTime 0; stopAt endTime; endTime $et; deltaT 1;
writeControl timeStep; writeInterval 100; purgeWrite 2; writeFormat ascii; writePrecision 8; timeFormat general; timePrecision 6; runTimeModifiable true;
functions { forces { type forces; libs ("libforces.so"); patches ("aircraft.*"); rho rhoInf; rhoInf $rho; CofR (0 0 0); writeControl timeStep; writeInterval 1; } }
""", et=END_TIME, rho=atm["rho"])
    _w(case, "system/fvSchemes", "dictionary", "fvSchemes", """ddtSchemes { default steadyState; } gradSchemes { default cellLimited Gauss linear 1; grad(U) cellLimited Gauss linear 1; }
divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss limitedLinear 1; div(phi,omega) bounded Gauss limitedLinear 1;
  div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; } wallDist { method meshWave; }
""")
    _w(case, "system/fvSolution", "dictionary", "fvSolution", """solvers { p { solver GAMG; smoother GaussSeidel; tolerance 1e-6; relTol 0.1; }
  "(U|k|omega)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; } }
SIMPLE { nNonOrthogonalCorrectors $noc; consistent yes; residualControl { p 1e-4; U $rt; "(k|omega)" $rt; } }
relaxationFactors { equations { U 0.9; ".*" 0.9; } }
""", noc=NON_ORTHO, rt=RES_TARGET)
    _w(case, "constant/transportProperties", "dictionary", "transportProperties", "transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] $nu;\n", nu=atm["nu"])
    _w(case, "constant/turbulenceProperties", "dictionary", "turbulenceProperties", "simulationType RAS;\nRAS { model kOmegaSST; turbulence on; printCoeffs on; }\n")
    def field(name, dims, internal, far, wall, cls="volScalarField"):
        _w(case, f"0/{name}", cls, name, "dimensions $dims;\ninternalField uniform $i;\nboundaryField { farfield { $far } \"aircraft.*\" { $wall } }\n", dims=dims, i=internal, far=far, wall=wall)
    field("U", "[0 1 -1 0 0 0 0]", Us, f"type freestream; freestreamValue uniform {Us};", "type noSlip;", "volVectorField")
    field("p", "[0 2 -2 0 0 0 0]", "0", "type freestreamPressure; freestreamValue uniform 0;", "type zeroGradient;")
    field("k", "[0 2 -2 0 0 0 0]", "%g" % k0, f"type freestream; freestreamValue uniform {k0:g};", f"type kqRWallFunction; value uniform {k0:g};")
    field("omega", "[0 0 -1 0 0 0 0]", "%g" % om0, f"type freestream; freestreamValue uniform {om0:g};", f"type omegaWallFunction; value uniform {om0:g};")
    field("nut", "[0 2 -1 0 0 0 0]", "0", "type freestream; freestreamValue uniform 0;", "type " + os.environ.get("NUT_WALL", "nutUSpaldingWallFunction") + "; value uniform 0;")
    return dict(wake_box=wb, nose_box=nb, layers=lay, levels=levels, surface_cell_m=cell, chord_proxy_m=chord, domain_m=dom, background_cells=n, location_in_mesh=loc, wind_dir=d, lift_dir=lift, U=U, k=k0, omega=om0)

# ---------- running OpenFOAM ----------
def of_run(case, cmd, log, on_line=None):
    args = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "--shm-size=4g", "-e", "OMPI_MCA_btl_vader_single_copy_mechanism=none", "-e", "OMPI_MCA_btl_sm_single_copy_mechanism=none", "-v", f"{case}:/case", "-w", "/case",
            "--entrypoint", "bash", IMAGE, "-c", f"source {BASHRC} && {cmd}"]
    with open(os.path.join(case, log), "w") as lf:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            lf.write(line)
            if on_line: on_line(line.rstrip())
        return p.wait()

def mpi(cmd, n=None): return f"mpirun -np {n or NPROC} --oversubscribe --bind-to none {cmd} -parallel"

def write_decompose_dict(case, n=None): _w(case, "system/decomposeParDict", "dictionary", "decomposeParDict", "numberOfSubdomains %d; method scotch;" % (n or NPROC))

def drop_processor_dirs(case):
    for d in glob.glob(os.path.join(case, "processor*")): shutil.rmtree(d, ignore_errors=True)

def parse_checkmesh(text):
    g = lambda pat: (m.group(1) if (m := re.search(pat, text)) else None)
    f = lambda s: float(s) if s is not None else None
    return dict(cells=int(g(r"cells:\s+(\d+)")) if g(r"cells:\s+(\d+)") else None, max_non_orthogonality=f(g(r"non-orthogonality Max: ([\d.eE+-]+)") or g(r"Max non-orthogonality = ([\d.eE+-]+)")),
                max_skewness=f(g(r"Max skewness = ([\d.eE+-]+)")), mesh_ok="Mesh OK." in text, failed_checks=g(r"Failed (\d+) mesh checks"))

def parse_layers(text):
    """Real prism-layer coverage from the snappyHexMesh log (None if the log has no layer summary)."""
    ext = re.findall(r"Extruding (\d+) out of (\d+) faces \(([\d.]+)%\)", text)
    tab = re.findall(r"^(aircraft\S*)\s+(\d+)\s+([\d.]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s*$", text, re.M)
    if not ext: return None
    out = dict(coverage_pct=float(ext[-1][2]))
    if tab: out.update(avg_layers=float(tab[-1][2]), near_wall_thickness_m=float(tab[-1][3]), overall_thickness_m=float(tab[-1][4]))
    return out

class ResidualParser:
    def __init__(self, emit, case=None):
        self.emit, self.cur, self.it, self.converged = emit, {}, 0, False
        self.case = case
        self._stopped = False
        self.warm_start = False
    def feed(self, line):
        if m := re.match(r"Time = (\d+)", line):
            self.flush(); self.it = int(m.group(1))
            self.check_force_convergence()
        elif m := re.search(r"Solving for (\w+), Initial residual = ([\d.eE+-]+)", line): self.cur[m.group(1)] = float(m.group(2))
        elif "SIMPLE solution converged" in line: self.converged = True
    def check_force_convergence(self):
        min_it = 40 if getattr(self, "warm_start", False) else 120
        if not self.case or self._stopped or self.it < min_it or self.it % 10 != 0: return
        res_vals = self.cur or {}
        if any(res_vals.get(k, 1.0) > 2e-4 for k in ["Ux", "p", "k", "omega"] if k in res_vals): return
        rows = parse_forces(self.case)
        min_rows = 20 if getattr(self, "warm_start", False) else 40
        if len(rows) < min_rows: return
        w = rows[-min_rows:]
        Fx = [r[1] for r in w]; Fz = [r[3] for r in w]
        fx_spread = max(Fx) - min(Fx); fz_spread = max(Fz) - min(Fz)
        mean_fx = abs(sum(Fx)/len(Fx)) if Fx else 1.0
        mean_fz = abs(sum(Fz)/len(Fz)) if Fz else 1.0
        if (fx_spread / max(mean_fx, 1.0) < 0.005 or fx_spread < 0.02) and \
           (fz_spread / max(mean_fz, 1.0) < 0.005 or fz_spread < 0.02):
            self.converged = True
            self._stopped = True
            cd = os.path.join(self.case, "system", "controlDict")
            if os.path.exists(cd):
                try:
                    s = open(cd).read()
                    if "stopAt endTime;" in s:
                        open(cd, "w").write(s.replace("stopAt endTime;", "stopAt writeNow;"))
                        self.emit(type="log", m=f"Forces converged and steady at iteration {self.it}; stopping early.")
                except Exception: pass

    def flush(self):
        if self.cur: self.emit(type="residual", it=self.it, vals=dict(self.cur)); self.cur = {}


def parse_forces(case):
    fs = sorted(glob.glob(os.path.join(case, "postProcessing", "forces", "*", "force*.dat")))
    rows = []
    if fs:
        for ln in open(fs[-1]).read().splitlines():
            if ln.startswith("#"): continue
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", ln.replace("(", " ").replace(")", " "))
            if len(nums) >= 4: rows.append([float(x) for x in nums[:10]])
    return rows   # [time, Fx, Fy, Fz] total (pressure+viscous), Newtons

def parse_yplus(text):
    m = re.search(r"y\+ : min = ([\d.eE+-]+), max = ([\d.eE+-]+), average = ([\d.eE+-]+)", text)
    return dict(min=float(m.group(1)), max=float(m.group(2)), avg=float(m.group(3))) if m else None

def read_vtk_points_scalar(text):
    """Minimal legacy-ASCII VTK reader: (points, values) of the first scalar in POINT_DATA (SCALARS or FIELD layout), else None."""
    tk = text.split()
    try:
        i = tk.index("POINTS"); n = int(tk[i+1]); c = [float(x) for x in tk[i+3:i+3+3*n]]
        s = tk.index("POINT_DATA", i)+2; v = None
        if tk[s] == "SCALARS":
            s += 4
            if tk[s] == "LOOKUP_TABLE": s += 2
            v = [float(x) for x in tk[s:s+n]]
        elif tk[s] == "FIELD":
            k = int(tk[s+2]); s += 3
            for _ in range(k):
                nc, nt = int(tk[s+1]), int(tk[s+2]); s += 4
                if nc == 1: v = [float(x) for x in tk[s:s+nt]]; break
                s += nc*nt
    except (ValueError, IndexError): return None
    if v is None or len(c) != 3*n or len(v) != n: return None
    return [(c[3*a], c[3*a+1], c[3*a+2]) for a in range(n)], v

def read_surface_file(path):
    """Read a sampled surface written by OpenFOAM (.vtp XML or legacy .vtk), ASCII data only. Raises ValueError with the reason."""
    with open(path, "rb") as fh: raw = fh.read().decode("latin1")
    if path.endswith(".vtp"):
        import xml.etree.ElementTree as ET
        try: root = ET.fromstring(raw)
        except ET.ParseError: raise ValueError("VTP is not plain XML (binary appended data): ascii output is required")
        pc = root.find(".//Piece"); pa = pc.find("Points/DataArray") if pc is not None else None
        sc = [a for a in (pc.findall("PointData/DataArray") if pc is not None else []) if int(a.get("NumberOfComponents", "1")) == 1]
        if pa is None or not sc: raise ValueError("VTP has no points or no scalar point data")
        for a in (pa, sc[0]):
            if a.get("format", "ascii") != "ascii": raise ValueError("VTP data format is '%s'; ascii is required" % a.get("format"))
        c = [float(x) for x in pa.text.split()]; v = [float(x) for x in sc[0].text.split()]; n = len(c)//3
        if len(v) != n: raise ValueError("VTP point and value counts differ")
        return [(c[3*i], c[3*i+1], c[3*i+2]) for i in range(n)], v
    r = read_vtk_points_scalar(raw)
    if r is None: raise ValueError("unknown legacy VTK layout")
    return r

def postprocess_cp(case, out_png, V, bbox):
    """Surface pressure at mid-span -> Cp plot. Never raises: returns dict or None (with reason)."""
    try:
        _w(case, "system/cpSurfaces", "dictionary", "cpSurfaces", 'type surfaces; libs ("libsampling.so"); writeControl writeTime; interpolationScheme cellPoint; surfaceFormat vtk; formatOptions { vtk { legacy true; format ascii; } } fields (p);\nsurfaces ( aircraft { type patch; patches ("aircraft.*"); interpolate true; } );\n')
        of_run(case, "simpleFoam -postProcess -func cpSurfaces -latestTime", "log.cpSurfaces")
        fs = sorted(glob.glob(os.path.join(case, "postProcessing", "cpSurfaces", "*", "*.vt[kp]")), key=lambda f: (float(os.path.basename(os.path.dirname(f))), os.path.getmtime(f)))
        if not fs: return dict(error="no surface output written (see log.cpSurfaces)")
        try: r = read_surface_file(fs[-1])
        except ValueError as e: return dict(error=f"{os.path.relpath(fs[-1], case)}: {e}")
        pts, vals = r; x0, y0, z0, x1, y1, z1 = bbox; c = x1-x0; ym = (y0+y1)/2; zm = (z0+z1)/2; band = 0.05*(y1-y0)
        sel = [(((p[0]-x0)/c), v/(0.5*V*V), p[2] >= zm) for p, v in zip(pts, vals) if abs(p[1]-ym) < band]
        if not sel: return dict(error="no surface points near mid-span")
        cmin = min(sel, key=lambda s: s[1]); res = dict(cp_min=cmin[1], cp_min_x_over_c=cmin[0], points=len(sel), png=None)
        try:
            import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(6, 4))
            for up, lab, col in ((True, "upper", "tab:blue"), (False, "lower", "tab:red")):
                d = sorted((s[0], s[1]) for s in sel if s[2] == up); ax.plot([a for a, _ in d], [b for _, b in d], ".", ms=3, label=lab, color=col)
            ax.invert_yaxis(); ax.set_xlabel("x/c (chord proxy)"); ax.set_ylabel("Cp = p/(0.5 V^2)"); ax.grid(alpha=.3); ax.legend()
            ax.set_title("Surface pressure at mid-span (solver output, unvalidated)"); fig.tight_layout(); fig.savefig(out_png, dpi=120); plt.close(fig); res["png"] = out_png
        except ImportError: res["note"] = "matplotlib not installed: plot skipped"
        return res
    except Exception as e: return dict(error=f"{type(e).__name__}: {e}")

def dot(a, b): return sum(x*y for x, y in zip(a, b))
def reference_geometry(stl_path, bbox):
    """Planform reference area (projected on the x-y plane), span and mean chord from the STL. Returns None if it cannot be computed."""
    try:
        v = [tuple(float(x) for x in m.groups()) for m in re.finditer(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", open(stl_path).read())]
        a2 = sum(abs((t[1][0]-t[0][0])*(t[2][1]-t[0][1]) - (t[1][1]-t[0][1])*(t[2][0]-t[0][0])) for t in (v[i:i+3] for i in range(0, len(v) - 2, 3)))
        area = a2/4.0   # |cross| is twice the projected triangle area; a closed body is seen once from above and once from below
        span = bbox[4] - bbox[1]
        return dict(area_m2=area, span_m=span, mean_chord_m=area/span) if area > 0 and span > 0 else None
    except Exception: return None

def force_summary(rows, drag_dir, lift_dir):
    if not rows: return None
    D = [dot(r[1:4], drag_dir) for r in rows]; Lf = [dot(r[1:4], lift_dir) for r in rows]
    full = len(rows[-1]) >= 10; split = dict(pressure_drag_N=dot(rows[-1][4:7], drag_dir), viscous_drag_N=dot(rows[-1][7:10], drag_dir), pressure_lift_N=dot(rows[-1][4:7], lift_dir), viscous_lift_N=dot(rows[-1][7:10], lift_dir)) if full else {}
    tail = max(10, len(rows)//5); dt, lt = D[-tail:], Lf[-tail:]
    drift = lambda a: (max(a)-min(a))/abs(sum(a)/len(a)) if abs(sum(a)/len(a)) > 1e-12 else None
    return dict(lift_N=Lf[-1], drag_N=D[-1], lift_to_drag=(Lf[-1]/D[-1] if abs(D[-1]) > 1e-12 else None), window_iterations=tail, lift_drift=drift(lt), drag_drift=drift(dt), lift_spread_N=(max(lt)-min(lt)), drag_spread_N=(max(dt)-min(dt)), **split)

def validate_aerodynamics(CL, CD, span, area, alpha_deg, bbox=None):
    """Consistency check against lifting-line theory for an unswept NACA0012-type wing. NOT a validation against experiments.
    The 2D lift slope a0 is an assumption: 5.5-6.3 per rad is a plausible range for NACA0012 at Re ~ 3e5 (not a measurement).
    Span efficiency 0.87 was fitted to a numerical lifting-line solution for an AR 5 rectangular wing (approximate for other aspect ratios)."""
    if not (CL and CD and span and area and span > 0 and area > 0): return None
    AR = (span * span) / area
    if abs(alpha_deg) < 1.0 or abs(alpha_deg) > 6.0: return dict(skipped=True, AR=AR, reason=f"linear lifting-line theory is only compared between 1 and 6 deg angle of attack (this run: {alpha_deg:g} deg)")
    if bbox is not None:
        x0, y0, z0, x1, y1, z1 = bbox; tc = (z1 - z0) / (x1 - x0) if x1 > x0 else 0.0
        if not (0.10 <= tc <= 0.14 and 2.0 <= AR <= 15.0):
            return dict(skipped=True, AR=AR, reason=f"the check assumes an unswept NACA0012-type wing (thickness/chord about 0.12 and aspect ratio 2-15; found {tc:.2f} and {AR:.1f})")
    e = 0.87; alpha = math.radians(alpha_deg); sgn = 1.0 if alpha_deg >= 0 else -1.0
    cl_at = lambda a0: sgn * abs(a0 / (1.0 + a0 / (math.pi * e * AR)) * alpha)
    CL_lo, CL_mid, CL_hi = sorted([cl_at(5.5), cl_at(5.9), cl_at(6.3)])
    CDi = (CL ** 2) / (math.pi * e * AR); CD_lo, CD_hi = 0.0120 + CDi, 0.0140 + CDi
    lo, hi = min(CL_lo, CL_hi), max(CL_lo, CL_hi)
    valid = 0.95 * lo <= CL <= 1.05 * hi if lo > 0 else 1.05 * lo <= CL <= 0.95 * hi
    pct = lambda ref: 100.0 * (CL - ref) / ref
    return dict(skipped=False, valid=valid, AR=AR, a0_range=(5.5, 6.3), CL_lo=CL_lo, CL_mid=CL_mid, CL_hi=CL_hi, CL_theory=CL_mid, err_vs_lo_pct=pct(CL_lo), err_vs_hi_pct=pct(CL_hi), lift_err_pct=pct(CL_mid), CD_lo=CD_lo, CD_hi=CD_hi)

def warm_start_fields(src_case, dst_case, new_U_vec):
    """Copy converged solution fields from src_case latestTime to dst_case/0/ and update farfield U."""
    src_c = os.path.join(src_case, "case") if os.path.exists(os.path.join(src_case, "case")) else src_case
    if not os.path.isdir(src_c): return False
    times = sorted([int(d) for d in os.listdir(src_c) if d.isdigit() and int(d) > 0])
    if not times: return False
    src_time = os.path.join(src_c, str(times[-1]))
    dst_0 = os.path.join(dst_case, "0")
    os.makedirs(dst_0, exist_ok=True)
    Us = "(%g %g %g)" % new_U_vec

    for f in ["p", "k", "omega", "nut"]:
        sp = os.path.join(src_time, f)
        if os.path.exists(sp):
            txt = open(sp, "r").read()
            txt = re.sub(r'location\s+"[0-9]+";', 'location "0";', txt, count=1)
            open(os.path.join(dst_0, f), "w").write(txt)

    su = os.path.join(src_time, "U")
    if os.path.exists(su):
        txt = open(su, "r").read()
        txt = re.sub(r'location\s+"[0-9]+";', 'location "0";', txt, count=1)
        txt = re.sub(r'(farfield\s*\{[^}]*?freestreamValue\s+uniform\s+)\([^)]+\);', r'\g<1>' + Us + ';', txt)
        txt = re.sub(r'(farfield\s*\{[^}]*?value\s+uniform\s+)\([^)]+\);', r'\g<1>' + Us + ';', txt)
        open(os.path.join(dst_0, "U"), "w").write(txt)
        return True
    return False

# ---------- main entry ----------
def run_pipeline(step_path, workdir, emit, mesh_source_case=None, warm_start_case=None):
    case = os.path.abspath(os.path.join(workdir, "case"))
    if os.path.exists(case):
        for entry in os.listdir(case):
            p = os.path.join(case, entry)
            if (entry.isdigit() and entry != "0") or entry == "postProcessing" or entry.startswith("log."):
                if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
                elif os.path.isfile(p): os.remove(p)
    os.makedirs(os.path.join(case, "constant", "triSurface"), exist_ok=True)
    res = dict(demo=False, status="failed", warnings=[], notes=[], settings=dict(CASE, mesh_levels=MESH_LEVELS, end_time=END_TIME, solver=f"simpleFoam kOmegaSST, nut wall {os.environ.get('NUT_WALL', 'nutUSpaldingWallFunction')} ({IMAGE}), residual target U/k/omega {RES_TARGET}, {globals().get('NPROC', 1)} solver process(es), {globals().get('MESH_PROCS', 1)} meshing process(es)"))
    stage = lambda i: emit(type="stage", i=i)
    try:
        stage(0); atm = isa(CASE["H"]); res["air"] = atm
        info = geometry_to_stl(step_path, os.path.join(case, "constant", "triSurface", "aircraft.stl")); res["geometry"] = info
        res["notes"] += info.get("warnings", []) + ["Mesh size is set from a chord proxy (middle bounding-box dimension), not a true chord."]
        emit(type="log", m=f"Geometry: {info['solids']} solid, {info['faces']} faces, size {info['length_scale_m']:.3f} m, {info['triangles']} surface triangles, watertight")
        emit(type="geometry_ready", url=f"/runs/{os.path.basename(workdir)}/stl", triangles=info["triangles"], length_scale_m=info["length_scale_m"])
        stage(1); meta = write_case(case, info, atm); res["domain"] = meta; res["settings"]["mesh_levels"] = meta["levels"]; res["layers"] = dict(meta["layers"])
        emit(type="log", m=f"Domain box written; background cells {meta['background_cells']}; surface level {meta['levels']} = {meta['surface_cell_m']*1000:.1f} mm cells (chord proxy {meta['chord_proxy_m']:.3f} m, {meta['chord_proxy_m']/meta['surface_cell_m']:.0f} cells)")
        stage(2)

        src_case = os.path.abspath(mesh_source_case) if mesh_source_case else None
        src_poly = os.path.join(src_case, "case", "constant", "polyMesh") if src_case else None
        if src_poly and not os.path.exists(src_poly):
            src_poly = os.path.join(src_case, "constant", "polyMesh")
        reusing_mesh = bool(src_poly and os.path.exists(src_poly) and os.path.exists(os.path.join(src_poly, "points")))

        if reusing_mesh:
            emit(type="log", m="Reusing mesh from previous angle (skipping blockMesh & snappyHexMesh)...")
            dst_poly = os.path.join(case, "constant", "polyMesh")
            if os.path.exists(dst_poly): shutil.rmtree(dst_poly)
            shutil.copytree(src_poly, dst_poly)
            for lf in ["log.snappyHexMesh", "log.checkMesh", "log.blockMesh"]:
                p_log = os.path.join(src_case, "case", lf) if os.path.exists(os.path.join(src_case, "case", lf)) else os.path.join(src_case, lf)
                if os.path.exists(p_log): shutil.copy2(p_log, os.path.join(case, lf))
        else:
            if of_run(case, "blockMesh", "log.blockMesh", lambda l: None): raise PipelineError("blockmesh_failed", "blockMesh failed (see log.blockMesh)")
            if MESH_PROCS > 1:
                write_decompose_dict(case, MESH_PROCS); emit(type="log", m=f"Meshing in parallel on {MESH_PROCS} processes (fixed, so the mesh does not depend on this machine)")
                if of_run(case, "decomposePar -copyZero", "log.decomposePar0"): raise PipelineError("decompose_failed", "decomposePar failed (see log.decomposePar0)")
            if of_run(case, mpi("snappyHexMesh -overwrite", MESH_PROCS) if MESH_PROCS > 1 else "snappyHexMesh -overwrite", "log.snappyHexMesh", lambda l: emit(type="log", m=l) if re.match(r"(Surface snapping|Mesh refinement|Layer addition|Writing mesh)", l) else None):
                raise PipelineError("snappy_failed", "snappyHexMesh failed (see log.snappyHexMesh); if you used MESH_PROCS>1 try MESH_PROCS=1")
            if MESH_PROCS > 1:
                if of_run(case, "reconstructParMesh -constant", "log.reconstructParMesh"): raise PipelineError("reconstruct_failed", "reconstructParMesh failed (see log.reconstructParMesh)")
                drop_processor_dirs(case)

        lyr = dict(meta["layers"])
        if LAYERS and os.path.exists(os.path.join(case, "log.snappyHexMesh")):
            got = parse_layers(open(os.path.join(case, "log.snappyHexMesh")).read())
            if got is None: res["warnings"].append("Prism layers were requested but the layer summary was not found in log.snappyHexMesh.")
            else:
                lyr.update(got); emit(type="log", m=f"Prism layers: {got['coverage_pct']:.1f}% of surface faces extruded, requested {LAYERS} layers, first layer {meta['layers']['first_m']*1000:.2f} mm")
                if got["coverage_pct"] < 80: res["warnings"].append(f"Prism layers cover only {got['coverage_pct']:.0f}% of the surface: wall treatment is inconsistent.")
                else: res["notes"].append(f"Prism layer coverage: {got['coverage_pct']:.1f}% extruded ({got.get('avg_layers', LAYERS):.1f} layers avg).")
        res["layers"] = lyr
        if not reusing_mesh:
            of_run(case, "checkMesh", "log.checkMesh")
        mq = parse_checkmesh(open(os.path.join(case, "log.checkMesh")).read()) if os.path.exists(os.path.join(case, "log.checkMesh")) else dict(mesh_ok=True, cells=0, max_non_orthogonality=0, max_skewness=0)
        res["mesh"] = mq
        emit(type="log", m=f"Mesh: {mq['cells']} cells, max non-orthogonality {mq['max_non_orthogonality']}, max skewness {mq['max_skewness']}, checkMesh OK={mq['mesh_ok']}")
        if not mq["mesh_ok"]: raise PipelineError("mesh_failed", f"checkMesh reported {mq['failed_checks'] or 'unknown'} failed checks; not solving on a bad mesh.")

        stage(3); emit(type="log", m=f"Solver setup: V={CASE['V']} m/s, alpha={CASE['alpha']} deg, beta={CASE['beta']} deg, rho={atm['rho']:.4f}, nu={atm['nu']:.3e}")
        warm_started = False
        if warm_start_case:
            warm_started = warm_start_fields(warm_start_case, case, meta["U"])
            if warm_started:
                emit(type="log", m="Warm-starting flow fields (U, p, k, omega) from previous solution...")
        stage(4); rp = ResidualParser(emit, case=case)
        if warm_started: rp.warm_start = True

        if NPROC > 1:
            write_decompose_dict(case); emit(type="log", m=f"Solving in parallel on {NPROC} processes")
            if of_run(case, "decomposePar -force", "log.decomposePar"): raise PipelineError("decompose_failed", "decomposePar failed (see log.decomposePar)")
        if of_run(case, mpi("simpleFoam") if NPROC > 1 else "simpleFoam", "log.simpleFoam", rp.feed): raise PipelineError("solver_failed", "simpleFoam exited with an error (see log.simpleFoam); if you used NPROC>1 try NPROC=1")
        if NPROC > 1:
            if of_run(case, "reconstructPar -latestTime", "log.reconstructPar"): raise PipelineError("reconstruct_failed", "reconstructPar failed (see log.reconstructPar)")
            drop_processor_dirs(case)
        rp.flush(); res["residual_criteria_met"] = rp.converged; res["iterations"] = rp.it
        stage(5); of_run(case, "simpleFoam -postProcess -func yPlus -latestTime", "log.yPlus"); res["yplus"] = parse_yplus(open(os.path.join(case, "log.yPlus")).read())
        yp = res["yplus"]
        if yp is not None and yp["max"] <= 0: res["yplus"] = yp = None
        if yp is None: res["warnings"].append("y+ could not be evaluated (postProcess returned no valid values).")
        elif not (30 <= yp["avg"] <= 300): res["warnings"].append(f"Average y+ = {yp['avg']:.3g} is outside the 30-300 range that wall functions need: drag/lift are not trustworthy.")
        else: res["notes"].append(f"Average wall y+ is {yp['avg']:.1f} (within the valid 30-300 wall function range).")
        res["cp"] = postprocess_cp(case, os.path.join(workdir, "cp_midspan.png"), CASE["V"], info["bbox_m"])
        if not res["cp"] or res["cp"].get("error"): res["warnings"].append("Cp plot unavailable: " + str((res["cp"] or {}).get("error", "unknown")))
        fs = force_summary(parse_forces(case), meta["wind_dir"], meta["lift_dir"]); res["forces"] = fs
        ref = reference_geometry(os.path.join(case, "constant", "triSurface", "aircraft.stl"), info["bbox_m"]); res["reference"] = ref
        if fs and ref:
            qS = 0.5*atm["rho"]*CASE["V"]**2*ref["area_m2"]
            fs["CL"] = fs["lift_N"]/qS; fs["CD"] = fs["drag_N"]/qS
            val = validate_aerodynamics(fs["CL"], fs["CD"], ref["span_m"], ref["area_m2"], CASE["alpha"], info["bbox_m"])
            res["validation"] = val
            if val and val.get("skipped"): res["notes"].append("Lifting-line consistency check skipped: " + val["reason"] + ".")
            elif val and not val["valid"]: res["warnings"].append(f"Lift is outside the lifting-line reference band (CL {fs['CL']:.4f} vs {val['CL_lo']:.4f}-{val['CL_hi']:.4f}, 5% margin): check mesh, convergence and angle of attack.")
        if fs is None: res["warnings"].append("Force output not found: lift/drag unavailable.")
        else:
            qS = 0.5*atm["rho"]*CASE["V"]**2*(ref["area_m2"] if ref else 1.0)
            dCL = fs.get("lift_spread_N", 0) / qS if qS > 0 else 0
            dCD = fs.get("drag_spread_N", 0) / qS if qS > 0 else 0
            lift_steady = (fs["lift_drift"] is not None and fs["lift_drift"] <= 0.02) or dCL <= 0.002
            drag_steady = (fs["drag_drift"] is not None and fs["drag_drift"] <= 0.02) or dCD <= 0.002
            if not (lift_steady and drag_steady):
                res["warnings"].append(f"Lift/drag still changing over the last {fs['window_iterations']} iterations (>2% spread): forces NOT steady.")
            else:
                res["notes"].append(f"Forces steady over the last {fs['window_iterations']} iterations (lift spread {fs['lift_drift']*100:.2f}%, drag spread {fs['drag_drift']*100:.2f}%).")
        if fs and CASE["alpha"] >= 2 and fs["lift_N"] < 0: res["warnings"].append("Lift is NEGATIVE at positive angle of attack, which is physically unexpected: treat these forces as wrong (check mesh, convergence, orientation).")
        if abs(CASE["alpha"]) > 5: res["warnings"].append("Angle of attack above 5 deg: in testing this setup predicted flow separation and unsteady forces at 8 deg, so lift and drag above about 5 deg are not reliable.")
        if not rp.converged: res["warnings"].append(f"Residual targets not met within {END_TIME} iterations.")
        else: res["notes"].append(f"Solver converged to residual targets in {rp.it} iterations.")
        if ref: res["notes"].append(f"CL and CD use projected planform area S = {ref['area_m2']:.4g} m2 and mean chord {ref['mean_chord_m']:.4g} m.")
        res["notes"].append("Velocity field plots are not produced by v1; surface Cp at mid-span is plotted in cp_midspan.png.")
        res["limitations"] = ["Not validated against experimental data: the lifting-line comparison is a consistency check, not a validation.", "Wall-function RANS: treat lift and drag as estimates. Typical spread seen in testing: lift about 5-10% and drag about 10% (mesh method and domain size).", "Fully turbulent model (no transition): profile drag is higher than for a real wing with laminar flow at this Reynolds number."]
        if not res["warnings"] and (res.get("validation") or {}).get("valid"):
            res["status"] = "completed_checks_passed"
        elif not res["warnings"]:
            res["status"] = "completed"
        else:
            res["status"] = "completed_with_warnings"
    except PipelineError as e:
        res["error"] = dict(code=e.code, message=str(e)); emit(type="error", code=e.code, m=str(e))
    except FileNotFoundError as e:
        res["error"] = dict(code="tool_missing", message=str(e)); emit(type="error", code="tool_missing", m=f"Required tool missing: {e}")
    stage(6); rep = make_report(res)
    with open(os.path.join(workdir, "report.md"), "w") as fh: fh.write(rep)
    with open(os.path.join(workdir, "results.json"), "w") as fh: json.dump(res, fh, indent=1, default=str)
    emit(type="report", text=rep); emit(type="done", status=res["status"], results=res); return res

def make_report(r):
    s = r["settings"]; a = r.get("air", {}); f = r.get("forces") or {}; m = r.get("mesh") or {}; g = r.get("geometry") or {}; v = r.get("validation") or {}
    fmt = lambda v, u="": "unavailable" if v is None else f"{v:.4g} {u}".strip()
    status_label = {"completed_checks_passed": "COMPLETED - CHECKS PASSED (NOT EXPERIMENTALLY VALIDATED)"}.get(r["status"], r["status"].replace("_", " ").upper())
    L = ["# CFD run report (fixed-wing, v1)", "", f"**Status: {status_label}** (real OpenFOAM run, not demo)", ""]
    if r.get("error"): L += [f"**Error [{r['error']['code']}]:** {r['error']['message']}", ""]
    L += ["## Settings (fixed standard case)", f"- Airspeed {s['V']} m/s, altitude {s['H']} m (ISA), AoA {s['alpha']} deg, sideslip {s['beta']} deg",
          f"- Density {a.get('rho', float('nan')):.4f} kg/m3, kinematic viscosity {a.get('nu', float('nan')):.3e} m2/s (calculated, ISA)", f"- Solver: {s['solver']}, {s['end_time']} iteration limit, mesh levels {s['mesh_levels']}",
          "", "## Geometry", f"- Solids {g.get('solids', 'n/a')}, faces {g.get('faces', 'n/a')}, size {fmt(g.get('length_scale_m'), 'm')}, wetted area {fmt(g.get('wetted_area_m2'), 'm2')}",
          "", "## Mesh", f"- Prism layers: " + (("requested %d, first layer %.2f mm, expansion %.2f, %s of surface faces extruded" % (r["layers"]["n"], r["layers"]["first_m"]*1000, r["layers"]["expansion"], ("%.0f%%" % r["layers"]["coverage_pct"]) if "coverage_pct" in r["layers"] else "coverage unknown")) if r.get("layers") and r["layers"]["n"] else "none"), f"- y+ (wall): {('avg %.3g, max %.3g' % (r['yplus']['avg'], r['yplus']['max'])) if r.get('yplus') else 'unavailable'}", f"- Cells {m.get('cells', 'unavailable')}, max non-orthogonality {fmt(m.get('max_non_orthogonality'))}, max skewness {fmt(m.get('max_skewness'))}, checkMesh OK: {m.get('mesh_ok', 'unavailable')}",
          "", "## Results (calculated by the solver)", f"- Lift {fmt(f.get('lift_N'), 'N')}, drag {fmt(f.get('drag_N'), 'N')}, L/D {fmt(f.get('lift_to_drag'))}", f"- Mid-span Cp: " + (("min %.3g at x/c=%.3f; plot %s" % (r["cp"]["cp_min"], r["cp"]["cp_min_x_over_c"], r["cp"].get("png") or "not drawn")) if r.get("cp") and "cp_min" in r["cp"] else "unavailable"), f"- Drag split: pressure {fmt(f.get('pressure_drag_N'), 'N')}, viscous {fmt(f.get('viscous_drag_N'), 'N')}",
          (("- CL %.4f, CD %.4f (S = %.4g m2, mean chord %.4g m)" % (f["CL"], f["CD"], r["reference"]["area_m2"], r["reference"]["mean_chord_m"])) if f.get("CL") is not None and r.get("reference") else "- CL, CD: unavailable (no reference area/length defined)"), f"- Residual targets met: {r.get('residual_criteria_met', 'unavailable')} (iterations run: {r.get('iterations', 'n/a')})."]
    if v and not v.get("skipped"):
        L += ["", "## Consistency check against lifting-line theory (not an experimental validation)",
              f"- Aspect ratio {v['AR']:.2f}; assumed 2D lift slope {v['a0_range'][0]}-{v['a0_range'][1]} per rad (plausible range for NACA0012 at Re ~ 3e5; an assumption, not a measurement)",
              f"- Reference CL band {v['CL_lo']:.4f}-{v['CL_hi']:.4f} (mid {v['CL_mid']:.4f}); simulated CL {f.get('CL', 0):.4f} is {v['err_vs_hi_pct']:+.1f}% to {v['err_vs_lo_pct']:+.1f}% relative to the band ends",
              f"- Informational drag band CD {v['CD_lo']:.4f}-{v['CD_hi']:.4f} (profile drag 0.0120-0.0140 for a fully turbulent solver plus induced drag); simulated CD {f.get('CD', 0):.4f}",
              f"- Result: {'CONSISTENT (lift inside the band with a 5% margin)' if v['valid'] else 'OUTSIDE THE BAND, attention needed'}"]
    elif v:
        L += ["", "## Consistency check against lifting-line theory", f"- Skipped: {v['reason']}."]
    if r.get("warnings"):
        L += ["", "## Warnings", *[f"- {w}" for w in r["warnings"]]]
    if r.get("notes"):
        L += ["", "## Notes and Assumptions", *[f"- {n}" for n in r["notes"]]]
    if r.get("limitations"):
        L += ["", "## Limitations (always apply)", *[f"- {n}" for n in r["limitations"]]]
    L += [""]
    return "\n".join(L)


if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser(description="Run OpenFOAM CFD pipeline on a STEP file")
    parser.add_argument("step", help="Path to STEP CAD file")
    parser.add_argument("workdir", help="Output directory")
    parser.add_argument("--alpha", type=float, default=4.0)
    parser.add_argument("--speed", "-V", type=float, default=25.0)
    parser.add_argument("--altitude", "-H", type=float, default=0.0)
    parser.add_argument("--beta", type=float, default=0.0)
    parser.add_argument("--mesh-levels", default="5 7")
    parser.add_argument("--wake-box", type=int, default=0, help="Enable (1) or disable (0) wake box refinement (default: 0)")
    parser.add_argument("--mesh-source-case", default=None)
    parser.add_argument("--warm-start-case", default=None)
    args = parser.parse_args()

    CASE["alpha"] = args.alpha
    CASE["V"] = args.speed
    CASE["H"] = args.altitude
    CASE["beta"] = args.beta
    MESH_LEVELS = args.mesh_levels
    os.environ["MESH_LEVELS"] = args.mesh_levels
    WAKE_BOX = bool(args.wake_box)
    os.environ["WAKE_BOX"] = str(args.wake_box)

    def emit_cli(**kwargs):
        print(f"CFD_EVENT:{json.dumps(kwargs, default=str)}", flush=True)

    try:
        run_pipeline(
            args.step,
            args.workdir,
            emit_cli,
            mesh_source_case=args.mesh_source_case,
            warm_start_case=args.warm_start_case
        )
    except Exception as exc:
        emit_cli(type="error", code="unhandled_exception", m=str(exc))
        emit_cli(type="done", status="failed")
        sys.exit(1)


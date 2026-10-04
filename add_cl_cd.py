"""Adds CL and CD to the run results and report.   Usage (repo root):  python add_cl_cd.py [backend/pipeline.py] [backend/test_pipeline.py]
Reference area S = projected planform area of the model (top view, from the STL); reference length = mean chord = S / span (bbox y extent).
For a full aircraft this S includes the fuselage, which the report states. Makes .bak backups; stops without changes if an anchor is not found exactly once."""
import shutil, sys
p = sys.argv[1] if len(sys.argv) > 1 else "backend/pipeline.py"
tp = sys.argv[2] if len(sys.argv) > 2 else "backend/test_pipeline.py"
s = open(p).read()
if "def reference_geometry" in s: print("already patched"); sys.exit(0)
def rep(a, b):
    global s
    if s.count(a) != 1: print(f"anchor found {s.count(a)} times (need exactly 1), nothing changed:\n  {a[:90]}"); sys.exit(1)
    s = s.replace(a, b)
rep("def force_summary(rows, drag_dir, lift_dir):", '''def reference_geometry(stl_path, bbox):
    """Planform reference area (projected on the x-y plane), span and mean chord from the STL. Returns None if it cannot be computed."""
    try:
        v = [tuple(float(x) for x in m.groups()) for m in re.finditer(r"vertex\\s+(\\S+)\\s+(\\S+)\\s+(\\S+)", open(stl_path).read())]
        a2 = sum(abs((t[1][0]-t[0][0])*(t[2][1]-t[0][1]) - (t[1][1]-t[0][1])*(t[2][0]-t[0][0])) for t in (v[i:i+3] for i in range(0, len(v) - 2, 3)))
        area = a2/4.0   # |cross| is twice the projected triangle area; a closed body is seen once from above and once from below
        span = bbox[4] - bbox[1]
        return dict(area_m2=area, span_m=span, mean_chord_m=area/span) if area > 0 and span > 0 else None
    except Exception: return None

def force_summary(rows, drag_dir, lift_dir):''')
rep('fs = force_summary(parse_forces(case), meta["wind_dir"], meta["lift_dir"]); res["forces"] = fs',
    'fs = force_summary(parse_forces(case), meta["wind_dir"], meta["lift_dir"]); res["forces"] = fs\n'
    '        ref = reference_geometry(os.path.join(case, "constant", "triSurface", "aircraft.stl"), info["bbox_m"]); res["reference"] = ref\n'
    '        if fs and ref: qS = 0.5*atm["rho"]*CASE["V"]**2*ref["area_m2"]; fs["CL"] = fs["lift_N"]/qS; fs["CD"] = fs["drag_N"]/qS')
rep('"Force coefficients unavailable: reference area/length are not defined for this geometry. Forces are in Newtons.",',
    '(f"CL and CD use the projected planform area S = {ref[\'area_m2\']:.4g} m2 and mean chord {ref[\'mean_chord_m\']:.4g} m, both computed from the geometry (for a full aircraft S includes the fuselage)." if ref else "Force coefficients unavailable: the reference area could not be computed from the geometry. Forces are in Newtons."),')
rep('"- CL, CD: unavailable (no reference area/length defined)",',
    '(("- CL %.4f, CD %.4f (S = %.4g m2, mean chord %.4g m)" % (f["CL"], f["CD"], r["reference"]["area_m2"], r["reference"]["mean_chord_m"])) if f.get("CL") is not None and r.get("reference") else "- CL, CD: unavailable (no reference area/length defined)"),')
shutil.copy(p, p + ".bak"); open(p, "w").write(s); print("patched", p, "(backup", p + ".bak)")
try: t = open(tp).read()
except OSError: print("test file not found; skipped the unit test"); sys.exit(0)
if "test_reference_area" not in t and t.count("    def test_forces(self):") == 1:
    new = '''    def test_reference_area(self):
        x0, x1, y0, y1, z0, z1 = 0.0, 0.2, -0.5, 0.5, -0.012, 0.012
        P8 = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        f = os.path.join(self.d, "box.stl")
        with open(f, "w") as fh:
            fh.write("solid box\\n")
            for a, b, c, d in [(0,1,3,2), (4,6,7,5), (0,4,5,1), (2,3,7,6), (0,2,6,4), (1,5,7,3)]:
                for tri in ((a, b, c), (a, c, d)): fh.write("facet normal 0 0 0\\nouter loop\\n" + "".join("vertex %g %g %g\\n" % P8[i] for i in tri) + "endloop\\nendfacet\\n")
            fh.write("endsolid box\\n")
        r = P.reference_geometry(f, (x0, y0, z0, x1, y1, z1)); self.assertAlmostEqual(r["area_m2"], 0.2, 6); self.assertAlmostEqual(r["span_m"], 1.0); self.assertAlmostEqual(r["mean_chord_m"], 0.2, 6)
        self.assertIsNone(P.reference_geometry(os.path.join(self.d, "missing.stl"), (0, 0, 0, 1, 1, 1)))
'''
    shutil.copy(tp, tp + ".bak"); open(tp, "w").write(t.replace("    def test_forces(self):", new + "    def test_forces(self):")); print("added test_reference_area to", tp)
else: print("unit test not added (already present or anchor not unique)")

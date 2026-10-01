"""Compare the surface the solver used (snapped mesh) with the true STL outline at mid-span.
Usage: python backend/geometry_check.py runs/<id>     -> writes runs/<id>/geometry_midspan.png and prints nose/tail numbers.
Self-contained (reads the binary .vtp itself); does not import pipeline.py."""
import base64, glob, json, os, re, struct, sys
import xml.etree.ElementTree as ET
import numpy as np

def read_vtp_points(path):
    root = ET.parse(path).getroot(); ht = root.get("header_type", "UInt32"); hs = 8 if ht == "UInt64" else 4
    if root.get("compressor"): raise SystemExit("VTP is compressed; not supported")
    a = root.find(".//Piece/Points/DataArray")
    if a is None: raise SystemExit("no Points array in VTP")
    if a.get("format") == "ascii": return np.array(a.text.split(), float).reshape(-1, 3)
    raw = base64.b64decode("".join(a.text.split())); n = struct.unpack("<Q" if hs == 8 else "<I", raw[:hs])[0]
    dt = np.float32 if a.get("type") == "Float32" else np.float64
    return np.frombuffer(raw[hs:hs+n], dtype=dt).reshape(-1, 3).astype(float)

def read_stl_vertices(path):
    return np.array([[float(x) for x in m.groups()] for m in re.finditer(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", open(path).read())])

run = os.path.abspath(sys.argv[1]); case = os.path.join(run, "case")
x0, y0, z0, x1, y1, z1 = json.load(open(os.path.join(run, "results.json")))["geometry"]["bbox_m"]
c = x1-x0; ym = (y0+y1)/2; band = 0.05*(y1-y0)
vtp = sorted(glob.glob(os.path.join(case, "postProcessing", "cpSurfaces", "*", "*.vtp")), key=lambda f: float(os.path.basename(os.path.dirname(f))))[-1]
S = read_vtp_points(vtp); T = read_stl_vertices(os.path.join(case, "constant", "triSurface", "aircraft.stl"))
S = S[abs(S[:, 1]-ym) < band]; T = T[abs(T[:, 1]-ym) < band]
f = lambda P: ((P[:, 0]-x0)/c, (P[:, 2]-(z0+z1)/2)/c)
(sx, sz), (tx, tz) = f(S), f(T)
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
fig, ax = plt.subplots(3, 1, figsize=(7, 10))
for a, (lo, hi, t) in zip(ax, [(0, 1, "full chord"), (0, 0.1, "nose"), (0.9, 1.0, "tail")]):
    a.plot(tx, tz, "k.", ms=2, label="true STL outline"); a.plot(sx, sz, "r.", ms=3, label="surface used by solver")
    m = (tx >= lo) & (tx <= hi); zz = tz[m] if m.any() else tz
    a.set_xlim(lo, hi); a.set_ylim(zz.min()-(0.003 if hi < 1 else 0.04), zz.max()+(0.003 if hi < 1 else 0.04)); a.set_aspect("equal", adjustable="box")
    a.set_title(t); a.grid(alpha=.3); a.set_xlabel("x/c (chord proxy)"); a.set_ylabel("z/c")
ax[0].legend(); fig.tight_layout(); out = os.path.join(run, "geometry_midspan.png"); fig.savefig(out, dpi=110)
def thick(x, z, lo, hi):
    m = (x >= lo) & (x <= hi); return (z[m].max()-z[m].min())*c*1000 if m.any() else float("nan")
print(f"points at mid-span: solver {len(sx)}, STL {len(tx)}")
print(f"thickness near x/c 0.90-0.95: STL {thick(tx,tz,.90,.95):.2f} mm, solver surface {thick(sx,sz,.90,.95):.2f} mm")
print(f"thickness near x/c 0.98-1.00: STL {thick(tx,tz,.98,1):.2f} mm, solver surface {thick(sx,sz,.98,1):.2f} mm")
print(f"thickness near x/c 0.00-0.02: STL {thick(tx,tz,0,.02):.2f} mm, solver surface {thick(sx,sz,0,.02):.2f} mm")
print("wrote", out)

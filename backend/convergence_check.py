"""Convergence check for a finished run:  python backend/convergence_check.py runs/<id>
Reads case/log.simpleFoam, case/system/fvSolution (targets), case/0/U (flow direction) and postProcessing/forces/*/force.dat.
Prints, per field: final residual vs target, orders of magnitude dropped, trend over the last N iterations; and the force
drift over the same window. Writes runs/<id>/convergence.png. Self-contained. WINDOW=200 changes N."""
import glob, math, os, re, sys
import numpy as np

run = os.path.abspath(sys.argv[1]); case = os.path.join(run, "case"); N = int(os.environ.get("WINDOW", "200"))

def parse_log(path):
    it, hist = 0, {}
    for ln in open(path, errors="ignore"):
        m = re.match(r"Time = (\d+)", ln)
        if m: it = int(m.group(1)); continue
        m = re.search(r"Solving for (\w+), Initial residual = ([\d.eE+-]+)", ln)
        if m: hist.setdefault(m.group(1), []).append((it, float(m.group(2))))
    return hist

def read_targets(case):
    t = {"p": 1e-4, "Ux": 1e-5, "Uy": 1e-5, "Uz": 1e-5, "k": 1e-5, "omega": 1e-5}
    try:
        s = open(os.path.join(case, "system", "fvSolution")).read(); b = re.search(r"residualControl\s*\{(.*?)\}", s, re.S).group(1)
        for key, val in re.findall(r'("?[^\s;"]+"?)\s+([\d.eE+-]+)\s*;', b):
            key = key.strip('"'); v = float(val)
            if key == "p": t["p"] = v
            elif key == "U": t.update(Ux=v, Uy=v, Uz=v)
            elif "omega" in key or key == "k": t.update(k=v, omega=v)
    except (OSError, AttributeError): print("note: fvSolution targets not found, using defaults")
    return t

def flow_dirs(case):
    try: u = [float(x) for x in re.search(r"internalField\s+uniform\s+\(([^)]*)\)", open(os.path.join(case, "0", "U")).read()).group(1).split()]
    except (OSError, AttributeError): u = [1.0, 0.0, 0.0]; print("note: 0/U not read, assuming flow along +x")
    n = math.sqrt(sum(x*x for x in u)); d = [x/n for x in u]; dz = d[2]; l = [-dz*d[0], -dz*d[1], 1-dz*d[2]]; ln = math.sqrt(sum(x*x for x in l))
    return d, [x/ln for x in l]

log = os.path.join(case, "log.simpleFoam")
if not os.path.exists(log): raise SystemExit("no log.simpleFoam in " + case)
hist, tgt = parse_log(log), read_targets(case)
print(f"\nRESIDUALS (window = last {N} iterations)")
print(f"{'field':6} {'target':>8} {'final':>9} {'final/target':>13} {'orders dropped':>15} {'trend':>8} {'% of window above target':>26}")
missing = []
for f in ["p", "Ux", "Uy", "Uz", "k", "omega"]:
    if f not in hist: continue
    v = np.array([x for _, x in hist[f]]); w = v[-N:]; half = max(len(w)//2, 1)
    drop = math.log10(max(v[:10].max(), 1e-30)/max(v[-1], 1e-30)); r = np.median(w[-half:])/max(np.median(w[:half]), 1e-30)
    trend = "falling" if r < 0.8 else "rising" if r > 1.25 else "flat"; above = 100*np.mean(w > tgt[f])
    if v[-1] >= tgt[f]: missing.append((f, v[-1]/tgt[f], trend))
    print(f"{f:6} {tgt[f]:8.0e} {v[-1]:9.2e} {v[-1]/tgt[f]:13.1f} {drop:15.1f} {trend:>8} {above:26.0f}")

rows = []
for fn in sorted(glob.glob(os.path.join(case, "postProcessing", "forces", "*", "force*.dat"))):
    for ln in open(fn).read().splitlines():
        if ln.startswith("#"): continue
        nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", ln.replace("(", " ").replace(")", " "))
        if len(nums) >= 4: rows.append([float(x) for x in nums[:4]])
fdrift = None
if rows:
    R = np.array(rows); d, l = flow_dirs(case); D = R[:, 1:4] @ np.array(d); L = R[:, 1:4] @ np.array(l); n = min(N, len(R)); half = max(n//2, 1)
    dm, lm = D[-n:].mean(), L[-n:].mean(); scale_l = max(abs(lm), abs(dm))
    stats = lambda a, s: (a[-n:].mean(), 100*(a[-n:].max()-a[-n:].min())/s, 100*(a[-half:].mean()-a[-n:-half].mean())/s)
    (lmean, lspr, lhalf), (dmean, dspr, dhalf) = stats(L, scale_l), stats(D, abs(dm))
    print(f"\nFORCES over the last {n} iterations (N)")
    print(f"  lift {lmean:9.4f}   spread {lspr:5.2f}%   change first->second half {lhalf:+6.2f}%  (percentages relative to the larger of |lift|, |drag|)")
    print(f"  drag {dmean:9.4f}   spread {dspr:5.2f}%   change first->second half {dhalf:+6.2f}%")
    fdrift = max(abs(lhalf), abs(dhalf), lspr, dspr)
else: print("\nno force.dat found")

print("\nASSESSMENT")
if not missing: print("  residual targets met in the final iteration")
for f, ratio, trend in missing: print(f"  {f}: final residual is {ratio:.1f}x the target and {trend} over the window")
if fdrift is not None:
    print("  forces: " + ("steady (spread and drift below 1%)" if fdrift < 1.0 else "NOT steady (spread or drift above 1%)"))
    if missing and fdrift < 1.0 and all(t == "flat" and r < 10 for _, r, t in missing):
        print("  -> a flat residual floor within 10x of the target with steady forces is typical of mesh noise (non-orthogonal cells, tip/wake regions); the forces can be used, but this is NOT proof of accuracy")
    if missing and any(r >= 10 for _, r, _ in missing): print("  -> a residual far above target: look for a local problem (bad cells, boundary condition, unsteady flow)")

import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
fig, ax = plt.subplots(2 if rows else 1, 1, figsize=(8, 8 if rows else 4.5), squeeze=False); ax = ax[:, 0]
for i, f in enumerate(["p", "Ux", "Uy", "Uz", "k", "omega"]):
    if f in hist:
        x, y = zip(*hist[f]); c = f"C{i}"; ax[0].semilogy(x, y, color=c, label=f); ax[0].axhline(tgt[f], color=c, ls=":", lw=.8)
ax[0].set_ylabel("initial residual (dotted = target)"); ax[0].legend(ncol=6, fontsize=8); ax[0].grid(alpha=.3)
if rows:
    ax[1].plot(R[:, 0], L, label="lift (N)"); ax[1].plot(R[:, 0], D, label="drag (N)"); ax[1].set_xlabel("iteration"); ax[1].set_ylabel("force (N)"); ax[1].legend(); ax[1].grid(alpha=.3)
fig.tight_layout(); out = os.path.join(run, "convergence.png"); fig.savefig(out, dpi=110); print("\nwrote", out)

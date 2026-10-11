"""Run the pipeline from the terminal (no UI): python backend/run_local.py test_wing.step [workdir]"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
# Add virtual environment site-packages for gmsh import
import sys, os
venv_site = os.path.join(os.path.dirname(__file__), '..', '.venv', 'lib', 'python3.14', 'site-packages')
if venv_site not in sys.path:
    sys.path.insert(0, venv_site)
import pipeline
step = os.path.abspath(sys.argv[1]); wd = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else f"runs/{int(time.time())}"); os.makedirs(wd, exist_ok=True)
def emit(**e):
    t = e["type"]
    if t == "stage": print(f"\n== {pipeline.STAGES[e['i']]}", flush=True)
    elif t == "residual": print(f"  iter {e['it']}: " + "  ".join(f"{k}={v:.2e}" for k, v in e["vals"].items()), end="\r", flush=True)
    elif t == "log": print("  " + e["m"], flush=True)
    elif t == "error": print(f"\n  ERROR [{e['code']}]: {e['m']}", flush=True)
    elif t == "report": print("\n" + e["text"], flush=True)
    elif t == "done": print(f"\nSTATUS: {e['status']}   (files in {wd})", flush=True)
pipeline.run_pipeline(step, wd, emit)

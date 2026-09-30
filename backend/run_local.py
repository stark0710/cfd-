"""Run the pipeline from the terminal (no UI): python backend/run_local.py test_wing.step [workdir]"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__)); import pipeline
step = os.path.abspath(sys.argv[1]); wd = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else f"runs/{int(time.time())}"); os.makedirs(wd, exist_ok=True)
def emit(**e):
    t = e["type"]
    if t == "stage": print(f"\n== {pipeline.STAGES[e['i']]}")
    elif t == "residual": print(f"  iter {e['it']}: " + "  ".join(f"{k}={v:.2e}" for k, v in e["vals"].items()), end="\r")
    elif t == "log": print("  " + e["m"])
    elif t == "error": print(f"\n  ERROR [{e['code']}]: {e['m']}")
    elif t == "report": print("\n" + e["text"])
    elif t == "done": print(f"\nSTATUS: {e['status']}   (files in {wd})")
pipeline.run_pipeline(step, wd, emit)

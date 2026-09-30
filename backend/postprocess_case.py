"""Re-run only the Cp post-processing on an existing run: python backend/postprocess_case.py runs/<id>"""
import json, os, sys; sys.path.insert(0, os.path.dirname(__file__)); import pipeline as P
run = os.path.abspath(sys.argv[1]); info = json.load(open(os.path.join(run, "results.json")))["geometry"]
print(json.dumps(P.postprocess_cp(os.path.join(run, "case"), os.path.join(run, "cp_midspan.png"), P.CASE["V"], info["bbox_m"]), indent=1))

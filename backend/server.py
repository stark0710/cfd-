"""Backend service. GET /health | POST /runs?name=x.step (raw STEP body) | GET /runs/<id>/events (SSE) | GET /runs/<id>/report
Never fabricates results: missing tools -> error events, status 'failed'."""
import json, os, queue, shutil, subprocess, sys, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
venv_site = os.path.join(os.path.dirname(__file__), '..', '.venv', 'lib', f"python{sys.version_info.major}.{sys.version_info.minor}", 'site-packages')
if os.path.exists(venv_site) and venv_site not in sys.path: sys.path.insert(0, venv_site)
import pipeline

FRONTEND_HTML = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
WORK = os.environ.get("CFD_WORK", os.path.join(os.path.dirname(__file__), "..", "runs", "web-runs"))
os.makedirs(WORK, exist_ok=True)
MAX_MB = int(os.environ.get("MAX_STEP_MB", "50"))
RUNS = {}; LOCK = threading.Lock()   # one solver run at a time (extra runs wait in line)

def tool_status():
    t = {"docker": bool(shutil.which("docker"))}
    try: import gmsh; t["gmsh_python"] = True
    except Exception: t["gmsh_python"] = False
    t["openfoam_image"] = t["docker"] and subprocess.run(["docker", "image", "inspect", pipeline.IMAGE], capture_output=True).returncode == 0
    return t

def worker(rid, step, q, params):
    with LOCK:
        emit = lambda **e: q.put(e)
        st = tool_status(); miss = [k for k, v in st.items() if not v]
        if miss:
            emit(type="error", code="solver_unavailable", m="Missing: " + ", ".join(miss) + ". No simulation was run."); emit(type="done", status="failed"); return
        try:
            if "alpha" in params: pipeline.CASE["alpha"] = float(params["alpha"][0])
            if "V" in params: pipeline.CASE["V"] = float(params["V"][0])
            if "H" in params: pipeline.CASE["H"] = float(params["H"][0])
            if "beta" in params: pipeline.CASE["beta"] = float(params["beta"][0])
            if "mesh_levels" in params:
                pipeline.MESH_LEVELS = params["mesh_levels"][0]
                os.environ["MESH_LEVELS"] = params["mesh_levels"][0]
            pipeline.run_pipeline(step, os.path.join(WORK, rid), emit)
        except Exception as e:
            emit(type="error", code="internal_error", m=str(e))
            emit(type="done", status="failed")

class H(BaseHTTPRequestHandler):
    def _j(self, code, obj, ct="application/json"):
        b = (obj if isinstance(obj, (bytes, bytearray)) else (obj if isinstance(obj, str) else json.dumps(obj)).encode())
        self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = urlparse(self.path).path.strip("/").split("/")
        if p == [""] or p == [] or p == ["index.html"]:
            if os.path.exists(FRONTEND_HTML):
                return self._j(200, open(FRONTEND_HTML, "rb").read(), "text/html")
        if p == ["health"]: return self._j(200, {"tools": tool_status(), "ready": True})
        if len(p) == 3 and p[0] == "runs" and p[1] in RUNS and p[2] == "events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
            while True:
                e = RUNS[p[1]].get(); self.wfile.write(f"data: {json.dumps(e, default=str)}\n\n".encode()); self.wfile.flush()
                if e.get("type") == "done": break
            return
        if len(p) == 3 and p[0] == "runs" and p[2] == "report":
            f = os.path.join(WORK, os.path.basename(p[1]), "report.md")
            return self._j(200, open(f).read(), "text/markdown") if os.path.exists(f) else self._j(404, {"error": "no report yet"})
        if len(p) == 3 and p[0] == "runs" and p[2] == "results":
            f = os.path.join(WORK, os.path.basename(p[1]), "results.json")
            return self._j(200, open(f).read(), "application/json") if os.path.exists(f) else self._j(404, {"error": "no results yet"})
        if len(p) == 3 and p[0] == "runs" and p[2] == "cp.png":
            f = os.path.join(WORK, os.path.basename(p[1]), "cp_midspan.png")
            return self._j(200, open(f, "rb").read(), "image/png") if os.path.exists(f) else self._j(404, {"error": "no cp plot yet"})
        self._j(404, {"error": "not found"})
    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/runs": return self._j(404, {"error": "not found"})
        params = parse_qs(u.query)
        name = os.path.basename(params.get("name", ["model.step"])[0]); n = int(self.headers.get("Content-Length", 0))
        if not name.lower().endswith((".step", ".stp")): return self._j(400, {"error": "need .step/.stp"})
        if n > MAX_MB*1024*1024: return self._j(413, {"error": f"file larger than {MAX_MB} MB"})
        rid = uuid.uuid4().hex[:8]; d = os.path.join(WORK, rid); os.makedirs(d, exist_ok=True); path = os.path.join(d, name)
        open(path, "wb").write(self.rfile.read(n)); q = RUNS[rid] = queue.Queue()
        threading.Thread(target=worker, args=(rid, path, q, params), daemon=True).start(); self._j(202, {"id": rid})
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers()


if __name__ == "__main__": ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()

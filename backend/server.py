"""Backend service. GET /health | POST /runs?name=x.step (raw STEP body) | GET /runs/<id>/events (SSE) | GET /runs/<id>/report
Never fabricates results: missing tools -> error events, status 'failed'."""
import json, os, queue, shutil, subprocess, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import pipeline

WORK = os.environ.get("CFD_WORK", "/tmp/cfd-runs"); MAX_MB = int(os.environ.get("MAX_STEP_MB", "50"))
RUNS = {}; LOCK = threading.Lock()   # one solver run at a time (extra runs wait in line)

def tool_status():
    t = {"docker": bool(shutil.which("docker"))}
    try: import gmsh; t["gmsh_python"] = True
    except Exception: t["gmsh_python"] = False
    t["openfoam_image"] = t["docker"] and subprocess.run(["docker", "image", "inspect", pipeline.IMAGE], capture_output=True).returncode == 0
    return t

def worker(rid, step, q):
    with LOCK:
        emit = lambda **e: q.put(e)
        st = tool_status(); miss = [k for k, v in st.items() if not v]
        if miss:
            emit(type="error", code="solver_unavailable", m="Missing: " + ", ".join(miss) + ". No simulation was run."); emit(type="done", status="failed"); return
        pipeline.run_pipeline(step, os.path.join(WORK, rid), emit)

class H(BaseHTTPRequestHandler):
    def _j(self, code, obj, ct="application/json"):
        b = (obj if isinstance(obj, str) else json.dumps(obj)).encode(); self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = urlparse(self.path).path.strip("/").split("/")
        if p == ["health"]: return self._j(200, {"tools": tool_status()})
        if len(p) == 3 and p[0] == "runs" and p[1] in RUNS and p[2] == "events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
            while True:
                e = RUNS[p[1]].get(); self.wfile.write(f"data: {json.dumps(e, default=str)}\n\n".encode()); self.wfile.flush()
                if e.get("type") == "done": break
            return
        if len(p) == 3 and p[0] == "runs" and p[2] == "report":
            f = os.path.join(WORK, os.path.basename(p[1]), "report.md")
            return self._j(200, open(f).read(), "text/markdown") if os.path.exists(f) else self._j(404, {"error": "no report yet"})
        self._j(404, {"error": "not found"})
    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/runs": return self._j(404, {"error": "not found"})
        name = os.path.basename(parse_qs(u.query).get("name", ["model.step"])[0]); n = int(self.headers.get("Content-Length", 0))
        if not name.lower().endswith((".step", ".stp")): return self._j(400, {"error": "need .step/.stp"})
        if n > MAX_MB*1024*1024: return self._j(413, {"error": f"file larger than {MAX_MB} MB"})
        rid = uuid.uuid4().hex[:8]; d = os.path.join(WORK, rid); os.makedirs(d, exist_ok=True); path = os.path.join(d, name)
        open(path, "wb").write(self.rfile.read(n)); q = RUNS[rid] = queue.Queue()
        threading.Thread(target=worker, args=(rid, path, q), daemon=True).start(); self._j(202, {"id": rid})
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers()

if __name__ == "__main__": ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()

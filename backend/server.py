"""CFD backend scaffold (stdlib only). Same event protocol the UI adapter uses.
Endpoints: GET /health | POST /runs?name=x.step (raw STEP body) | GET /runs/<id>/events (SSE)
Never fabricates results: if the toolchain is missing the run fails with 'solver_unavailable'."""
import json, os, shutil, threading, time, uuid, queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TOOLS = ["simpleFoam", "blockMesh", "snappyHexMesh", "gmsh"]
WORK = os.environ.get("CFD_WORK", "/tmp/cfd-runs")
RUNS = {}  # id -> queue of events

def tool_status():
    found = {t: bool(shutil.which(t)) for t in TOOLS}
    try:
        import gmsh; found["gmsh_python"] = True
    except Exception:
        found["gmsh_python"] = False
    return found

def run_case(rid, step_path, q):
    emit = lambda **e: q.put(e)
    st = tool_status()
    missing = [t for t in ("simpleFoam", "snappyHexMesh") if not st[t]] + ([] if st["gmsh"] or st["gmsh_python"] else ["gmsh"])
    emit(type="stage", i=0); emit(type="log", m=f"Received {os.path.basename(step_path)}")
    if missing:
        emit(type="error", code="solver_unavailable", m="Missing tools: " + ", ".join(missing) + ". No simulation was run.")
        emit(type="done", status="failed"); return
    # TODO step 3: STEP -> STL via gmsh (check watertight/solids), far-field domain, snappyHexMesh, checkMesh
    # TODO step 4: simpleFoam + forceCoeffs; stream residuals parsed from log.simpleFoam as {type:'residual',...}
    emit(type="error", code="not_implemented", m="Meshing/solve pipeline not implemented yet (steps 3-4).")
    emit(type="done", status="failed")

class H(BaseHTTPRequestHandler):
    def _j(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = urlparse(self.path).path.strip("/").split("/")
        if p == ["health"]: return self._j(200, {"tools": tool_status()})
        if len(p) == 3 and p[0] == "runs" and p[2] == "events" and p[1] in RUNS:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
            q = RUNS[p[1]]
            while True:
                e = q.get(); self.wfile.write(f"data: {json.dumps(e)}\n\n".encode()); self.wfile.flush()
                if e.get("type") == "done": break
            return
        self._j(404, {"error": "not found"})
    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/runs": return self._j(404, {"error": "not found"})
        name = os.path.basename(parse_qs(u.query).get("name", ["model.step"])[0])
        if not name.lower().endswith((".step", ".stp")): return self._j(400, {"error": "need .step/.stp"})
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        rid = uuid.uuid4().hex[:8]; d = os.path.join(WORK, rid); os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name); open(path, "wb").write(body)
        q = RUNS[rid] = queue.Queue(); threading.Thread(target=run_case, args=(rid, path, q), daemon=True).start()
        self._j(202, {"id": rid})
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers()

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()

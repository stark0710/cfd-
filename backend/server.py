"""CFD Web Backend Server.
Provides:
  - GET /               -> Web Dashboard UI
  - GET /health         -> Environment & tool check
  - POST /runs          -> Submit STEP file for OpenFOAM CFD run
  - GET /runs/<id>/events  -> Real-time Server-Sent Events (SSE)
  - GET /runs/<id>/results -> JSON force & aero summary
  - GET /runs/<id>/report  -> Full Markdown engineering report
  - GET /runs/<id>/cp.png  -> Midspan pressure coefficient plot
Runs each CFD simulation in an isolated subprocess to guarantee Gmsh & OpenCASCADE thread-safety.
"""
import json, os, queue, shutil, subprocess, sys, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
venv_site = os.path.join(os.path.dirname(__file__), '..', '.venv', 'lib', f"python{sys.version_info.major}.{sys.version_info.minor}", 'site-packages')
if os.path.exists(venv_site) and venv_site not in sys.path:
    sys.path.insert(0, venv_site)

import pipeline

FRONTEND_HTML = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
WORK = os.environ.get("CFD_WORK", os.path.join(os.path.dirname(__file__), "..", "runs", "web-runs"))
os.makedirs(WORK, exist_ok=True)
MAX_MB = int(os.environ.get("MAX_STEP_MB", "50"))

RUNS = {}
RUNS_LOCK = threading.Lock()

def tool_status():
    t = {"docker": bool(shutil.which("docker"))}
    try:
        import gmsh
        t["gmsh_python"] = True
    except Exception:
        t["gmsh_python"] = False
    t["openfoam_image"] = t["docker"] and subprocess.run(
        ["docker", "image", "inspect", pipeline.IMAGE],
        capture_output=True
    ).returncode == 0
    return t

class RunManager:
    def __init__(self, rid, step_path, workdir, params):
        self.rid = rid
        self.step_path = step_path
        self.workdir = workdir
        self.params = params
        self.events = []
        self.subscribers = []
        self.done = False
        self.lock = threading.Lock()
        self.proc = None

    def emit(self, event):
        with self.lock:
            self.events.append(event)
            dead = []
            for q in self.subscribers:
                try:
                    q.put(event)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.subscribers.remove(q)
            if event.get("type") == "done":
                self.done = True

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            for ev in self.events:
                q.put(ev)
            if not self.done:
                self.subscribers.append(q)
            else:
                q.put({"type": "done_stream"})
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def run(self):
        st = tool_status()
        miss = [k for k, v in st.items() if not v]
        if miss:
            self.emit({"type": "error", "code": "solver_unavailable", "m": "Missing: " + ", ".join(miss) + ". Simulation aborted."})
            self.emit({"type": "done", "status": "failed"})
            return

        cmd = [
            sys.executable, "-u", "-m", "backend.pipeline",
            self.step_path,
            self.workdir
        ]
        if "alpha" in self.params:
            cmd += ["--alpha", str(float(self.params["alpha"][0]))]
        if "V" in self.params:
            cmd += ["-V", str(float(self.params["V"][0]))]
        if "H" in self.params:
            cmd += ["-H", str(float(self.params["H"][0]))]
        if "beta" in self.params:
            cmd += ["--beta", str(float(self.params["beta"][0]))]
        if "mesh_levels" in self.params:
            cmd += ["--mesh-levels", self.params["mesh_levels"][0]]
        if "wake_box" in self.params:
            cmd += ["--wake-box", str(int(self.params["wake_box"][0]))]
        else:
            cmd += ["--wake-box", "0"]

        env = os.environ.copy()
        pythonpath = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env["PYTHONPATH"] = f"{pythonpath}:{env.get('PYTHONPATH', '')}"

        self.emit({"type": "log", "m": f"[server] Starting subprocess: {' '.join(cmd)}"})
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )
            for line in iter(self.proc.stdout.readline, ''):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("CFD_EVENT:"):
                    try:
                        ev = json.loads(line[len("CFD_EVENT:"):])
                        self.emit(ev)
                    except Exception:
                        self.emit({"type": "log", "m": line})
                else:
                    self.emit({"type": "log", "m": line})
            self.proc.stdout.close()
            ret = self.proc.wait()
            if not self.done:
                status = "completed" if ret == 0 else "failed"
                self.emit({"type": "done", "status": status})
        except Exception as exc:
            self.emit({"type": "error", "code": "process_error", "m": str(exc)})
            if not self.done:
                self.emit({"type": "done", "status": "failed"})

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _j(self, code, obj, ct="application/json"):
        b = (obj if isinstance(obj, (bytes, bytearray)) else (obj if isinstance(obj, str) else json.dumps(obj)).encode())
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        p = urlparse(self.path).path.strip("/").split("/")
        if p == [""] or p == [] or p == ["index.html"] or p == ["frontend", "index.html"] or (len(p) == 1 and p[0].endswith(".html")):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
        elif p == ["health"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path.strip("/").split("/")
        if p == [""] or p == [] or p == ["index.html"] or p == ["frontend", "index.html"] or (len(p) == 1 and p[0].endswith(".html")):
            if os.path.exists(FRONTEND_HTML):
                return self._j(200, open(FRONTEND_HTML, "rb").read(), "text/html")
        if p == ["health"]:
            return self._j(200, {"tools": tool_status(), "ready": True})
        if len(p) == 3 and p[0] == "runs" and p[1] in RUNS and p[2] == "events":
            run_mgr = RUNS[p[1]]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q = run_mgr.subscribe()
            try:
                while True:
                    try:
                        e = q.get(timeout=15.0)
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        continue
                    if e.get("type") == "done_stream":
                        break
                    self.wfile.write(f"data: {json.dumps(e, default=str)}\n\n".encode())
                    self.wfile.flush()
                    if e.get("type") == "done":
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                run_mgr.unsubscribe(q)
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
        if len(p) == 3 and p[0] == "runs" and p[2] == "stl":
            f = os.path.join(WORK, os.path.basename(p[1]), "case", "constant", "triSurface", "aircraft.stl")
            return self._j(200, open(f, "rb").read(), "model/stl") if os.path.exists(f) else self._j(404, {"error": "no stl yet"})
        if len(p) == 3 and p[0] == "runs" and p[2] == "polar.png":
            f = os.path.join(WORK, os.path.basename(p[1]), "polar.png")
            if not os.path.exists(f):
                f = os.path.join(os.path.dirname(__file__), "..", "runs", "plane-fast-polar", "polar.png")
            return self._j(200, open(f, "rb").read(), "image/png") if os.path.exists(f) else self._j(404, {"error": "no polar plot yet"})
        if p == ["models", "aircraft.stl"]:
            f = os.path.join(WORK, "fa8891fe", "case", "constant", "triSurface", "aircraft.stl")
            if not os.path.exists(f):
                # find any aircraft.stl in WORK
                for root, _, files in os.walk(WORK):
                    if "aircraft.stl" in files:
                        f = os.path.join(root, "aircraft.stl")
                        break
            return self._j(200, open(f, "rb").read(), "model/stl") if os.path.exists(f) else self._j(404, {"error": "no stl available"})
        if p == ["polar.png"]:
            f = os.path.join(os.path.dirname(__file__), "..", "runs", "plane-fast-polar", "polar.png")
            return self._j(200, open(f, "rb").read(), "image/png") if os.path.exists(f) else self._j(404, {"error": "no polar plot available"})
        self._j(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/runs":
            return self._j(404, {"error": "not found"})
        params = parse_qs(u.query)
        name = os.path.basename(params.get("name", ["model.step"])[0])
        n = int(self.headers.get("Content-Length", 0))
        if not name.lower().endswith((".step", ".stp")):
            return self._j(400, {"error": "need .step/.stp"})
        if n > MAX_MB * 1024 * 1024:
            return self._j(413, {"error": f"file larger than {MAX_MB} MB"})

        rid = uuid.uuid4().hex[:8]
        d = os.path.join(WORK, rid)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        open(path, "wb").write(self.rfile.read(n))

        run_mgr = RunManager(rid, path, d, params)
        with RUNS_LOCK:
            RUNS[rid] = run_mgr
        threading.Thread(target=run_mgr.run, daemon=True).start()
        self._j(202, {"id": rid})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.end_headers()

if __name__ == "__main__":
    print("[server] Starting CFD HTTP Server on 0.0.0.0:8000...", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()

# TorqWings CFD Agent (fixed-wing UAV)

AI-assisted CFD workflow: upload STEP -> standard flight case -> run lifecycle -> results/report.

## Status (be honest about this)
| Part | State |
|---|---|
| `frontend/index.html` | Working UI. **Demo mode only**: all progress, residuals, fields and coefficients are labeled sample data |
| `backend/server.py` | Scaffold: `/health`, `POST /runs`, SSE events. Fails clearly with `solver_unavailable` if OpenFOAM/Gmsh are missing |
| `backend/Dockerfile` | **Untested** (OpenFOAM 2312 + Gmsh) |
| Meshing + solving pipeline | **Not built yet** (TODO in `server.py`) |
| Frontend -> backend connection (`HttpAdapter`) | **Not built yet** |

## Fixed v1 flight case
25 m/s, 0 m (ISA sea level), AoA 4 deg, sideslip 0. Custom inputs planned later.

## Run locally
Frontend: open `frontend/index.html` in a browser.
Backend: `python3 backend/server.py` (health check: http://localhost:8000/health), or build the Dockerfile.

## Deploy
- Frontend (now): upload the contents of `frontend/` to your normal web host (it is plain HTML, no build step). Vercel can be used later.
- Backend: needs a Linux server/VPS with Docker (shared hosting/cPanel cannot run OpenFOAM). Add CORS restrictions, API key, upload size limit and a run queue before exposing it publicly.

## Rules
Never show demo values as real results. Never claim convergence from a plausible-looking graph.

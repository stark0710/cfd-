# TorqWings CFD Agent (fixed-wing UAV)

AI-assisted CFD workflow: upload STEP -> standard flight case -> run lifecycle -> results/report.

## Status (be honest about this)
| Part | State |
|---|---|
| `frontend/index.html` | Working UI, **demo mode only** (labeled sample data). Not yet connected to the backend |
| `backend/pipeline.py` | STEP -> Gmsh watertight surface -> OpenFOAM case -> snappyHexMesh -> checkMesh -> simpleFoam (kOmegaSST) -> forces -> report |
| Tested (no OpenFOAM needed) | STEP read, unit handling, watertight check, case-file generation, log/force parsers, failure path (7 unit tests) |
| **Not tested yet** | The OpenFOAM stages on a real STEP (first run happens in Codespace) |
| `backend/server.py` | `/health`, `POST /runs`, SSE `/runs/<id>/events`, `/runs/<id>/report`; one run at a time, size limit |
| Frontend -> backend (`HttpAdapter`), pressure/velocity plots, CL/CD, prism layers, validation | **Not built yet** |

v1 results are always "completed with warnings" (no prism layers, coarse mesh, unvalidated).

## Try it (Ubuntu with Docker, e.g. GitHub Codespaces)
```
./install_codespace.sh
python tools/make_test_wing.py test_wing.step
python backend/run_local.py test_wing.step
python backend/test_pipeline.py        # unit tests
```
Server: `python backend/server.py` then open http://localhost:8000/health

## Fixed v1 flight case
25 m/s, 0 m (ISA sea level), AoA 4 deg, sideslip 0. Custom inputs planned later.

## Deploy
- Frontend (now): upload the contents of `frontend/` to your normal web host (it is plain HTML, no build step). Vercel can be used later.
- Backend: needs a Linux server/VPS with Docker (OpenFOAM runs via the official image) (shared hosting/cPanel cannot run OpenFOAM). Add CORS restrictions, API key, upload size limit and a run queue before exposing it publicly.

## Rules
Never show demo values as real results. Never claim convergence from a plausible-looking graph.

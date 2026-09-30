# CFD run report (fixed-wing, v1)

**Status: COMPLETED WITH WARNINGS** (real OpenFOAM run, not demo)

## Settings (fixed standard case)
- Airspeed 25.0 m/s, altitude 0.0 m (ISA), AoA 4.0 deg, sideslip 0.0 deg
- Density 1.2250 kg/m3, kinematic viscosity 1.461e-05 m2/s (calculated, ISA)
- Solver: simpleFoam kOmegaSST (opencfd/openfoam-default:2312), 600 iteration limit, mesh levels 5 5

## Geometry
- Solids 1, faces 4, size 1 m, wetted area 0.4144 m2

## Mesh
- y+ (wall): avg 0, max 0
- Cells unavailable, max non-orthogonality unavailable, max skewness unavailable, checkMesh OK: unavailable

## Results (calculated by the solver)
- Lift 3.917 N, drag 3.452 N, L/D 1.135
- CL, CD: unavailable (no reference area/length defined)
- Residual targets met: False (iterations run: 600). This alone does not prove convergence or accuracy.

## Warnings and assumptions
- Orientation not stored in STEP: assumed nose toward -X, +Y starboard, +Z up.
- Watertightness verified on the Gmsh surface mesh; CAD-level defects may still exist.
- Mesh size is set from a chord proxy (middle bounding-box dimension), not a true chord.
- Average y+ = 0 is outside the 30-300 range that wall functions need: drag/lift are not trustworthy.
- Lift/drag still changing over the last 120 iterations (>2% spread): forces NOT steady.
- Residual targets not met within 600 iterations.
- No prism layers and a coarse mesh: treat drag and lift as unreliable until refined and validated.
- Not validated against reference data yet.
- Force coefficients unavailable: reference area/length are not defined for this geometry. Forces are in Newtons.
- Pressure/velocity field plots are not produced by v1 (results are in the case folder).

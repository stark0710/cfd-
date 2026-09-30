# CFD run report (fixed-wing, v1)

**Status: COMPLETED WITH WARNINGS** (real OpenFOAM run, not demo)

## Settings (fixed standard case)
- Airspeed 25.0 m/s, altitude 0.0 m (ISA), AoA 4.0 deg, sideslip 0.0 deg
- Density 1.2250 kg/m3, kinematic viscosity 1.461e-05 m2/s (calculated, ISA)
- Solver: simpleFoam kOmegaSST (opencfd/openfoam-default:2312), 600 iteration limit, mesh levels 3 4

## Geometry
- Solids 1, faces 4, size 1 m, wetted area 0.4144 m2

## Mesh
- Cells 114520, max non-orthogonality unavailable, max skewness 0.9499, checkMesh OK: True

## Results (calculated by the solver)
- Lift -3.809 N, drag 4.138 N, L/D -0.9205
- CL, CD: unavailable (no reference area/length defined)
- Residual targets met: False (iterations run: 600). This alone does not prove convergence or accuracy.

## Warnings and assumptions
- Orientation not stored in STEP: assumed nose toward -X, +Y starboard, +Z up.
- Watertightness verified on the Gmsh surface mesh; CAD-level defects may still exist.
- Residual targets not met within 600 iterations.
- No prism layers: wall-function on a coarse mesh; drag is not reliable and y+ was not evaluated.
- Not validated against reference data yet.
- Force coefficients unavailable: reference area/length are not defined for this geometry. Forces are in Newtons.
- Pressure/velocity field plots are not produced by v1 (results are in the case folder).

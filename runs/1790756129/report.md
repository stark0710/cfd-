# CFD run report (fixed-wing, v1)

**Status: COMPLETED WITH WARNINGS** (real OpenFOAM run, not demo)

## Settings (fixed standard case)
- Airspeed 25.0 m/s, altitude 0.0 m (ISA), AoA 4.0 deg, sideslip 0.0 deg
- Density 1.2250 kg/m3, kinematic viscosity 1.461e-05 m2/s (calculated, ISA)
- Solver: simpleFoam kOmegaSST (opencfd/openfoam-default:2312), 1200 iteration limit, mesh levels 5 7

## Geometry
- Solids 1, faces 4, size 1 m, wetted area 0.4144 m2

## Mesh
- y+ (wall): avg 32.5, max 220
- Cells 559619, max non-orthogonality 48.37, max skewness 2.09, checkMesh OK: True

## Results (calculated by the solver)
- Lift 8.856 N, drag 2.129 N, L/D 4.159
- Drag split: pressure 1.96 N, viscous 0.1699 N
- CL, CD: unavailable (no reference area/length defined)
- Residual targets met: False (iterations run: 1200). This alone does not prove convergence or accuracy.

## Warnings and assumptions
- Orientation not stored in STEP: assumed nose toward -X, +Y starboard, +Z up.
- Watertightness verified on the Gmsh surface mesh; CAD-level defects may still exist.
- Mesh size is set from a chord proxy (middle bounding-box dimension), not a true chord.
- Residual targets not met within 1200 iterations.
- No prism layers and a coarse mesh: treat drag and lift as unreliable until refined and validated.
- Not validated against reference data yet.
- Force coefficients unavailable: reference area/length are not defined for this geometry. Forces are in Newtons.
- Pressure/velocity field plots are not produced by v1 (results are in the case folder).

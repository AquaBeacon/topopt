# Print this: `tall_tripod.stl`

> ## Half of this is a finished part. Know which half.
>
> The **interface** was re-cut from the source CAD on a 0.50 mm grid, so a feature width is good to within **0.50 mm** instead of 4.0 mm. Not exact — quote that tolerance, do not quote the drawing. The **optimised body** is still the analysis grid: resampling it added no detail it never had, and no mating dimension may be taken off it.
>
> A **CAD face** in this mesh does better than that: it is placed from the body's sub-cell occupancy rather than from which cells it fills, so it lands within **0.17 mm** of where CAD put it. That bound is for the faces of the added and cut CAD bodies in `tall_tripod.stl` only — the 0.50 mm above still applies to the optimised body and to the voxel file.
>
> The analysis did **not** see this shape. The FEA ran on the 4.0 mm interface, so stresses in and around it are resolved no better than that grid allows.

> ## The analysis verdict is FAIL.
>
> Printing this is fine for looking at, fitting, and checking the layout. It is not signed off to carry load. See section 3.

## 1. Files

| file | what it is |
|---|---|
| `tall_tripod.stl` | **the mesh to slice** — optimiser body (smoothed, sigma 4.00 mm, volume held) with the interface re-cut from CAD on a 0.50 mm grid |
| `tall_tripod_raw.stl` | what the optimiser produced, on the analysis grid |
| `tall_tripod.step` | solid model for CAD, if you are rebuilding the interfaces (you are) |
| `preview_shape.png` | what the optimiser produced, in section |
| `preview_design_space.png` | what it was allowed to fill — compare against the shape |
| `preview_stress.png` | safety factor on the **governing** load case — LC3sw |
| `preview_orientation.png` | how it sits on the plate, and what overhangs |

> There is no `preview_print.png` for this run: the mesh is too big to draw (see `write_mesh_preview`). Open the STL in the slicer instead.

Threshold **0.40** of the density field.

_Why this one: no candidate passes every check; this is the strongest of the connected ones and is NOT a pass. Outstanding: mesh resolution (2.0 (thin 8.00 mm) / 2.0 (typical 8.00 mm), required >= 3 elements across the thinnest section)_

## 2. How to print it

| | |
|---|---|
| orientation the analysis assumed | model **Z** axis up |
| fits the build volume | yes |
| size as modelled | 201.3 × 174.0 × 196.5 mm |
| lowest-support orientation | +Z up |
| support volume in this orientation | 952768 mm³ |

**Do not reorient it to save support.** The material is anisotropic and the analysis was run with the axis above pointing up; laying the part on its side moves the layer planes and the safety factors no longer apply to it. If another orientation is wanted, change `build_axis` in the study and re-run.

**Support on mating faces.** support rests on 2 preserve voxel(s) (32.0 mm2). Those faces come off the printer rough and out of tolerance. Reorient the part, or accept the finish and say so explicitly.

## 3. What is still wrong with it

These failed the stop conditions (spec 18). They are listed in full, with the fix, in `../03_optimization/report.md`.

- **The density field converged (not merely out of iterations)** — termination: max_iterations
- **Mesh convergence confirmed** — the numbers settled but the hotspot moved 17.49 mm (allowed 7.00); the two meshes are not describing the same failure, so it is a property of the discretisation, not of the part
- **Self-supporting along the build axis (no voxel without material in the 45 degree cone below it)** — 1 voxel(s), 16 mm2 of material with nothing under it within 45 degrees (build axis +Z, at [[-40.0, -44.0, 64.0], [-36.0, -40.0, 68.0]]); it would print on support or not at all. Enable design.overhang, or move the build axis
- **No support lands on a mating (preserve) face** — support rests on 2 preserve voxel(s) (32.0 mm2). Those faces come off the printer rough and out of tolerance. Reorient the part, or accept the finish and say so explicitly.
- _not checked:_ Minimum wall thickness is met

_This list covers the stop conditions the report checks. **How much support the part needs is not one of them** -- that is judged by `topopt print <stl>`, which applies a 15 % overhang-area limit and is worth running before you slice._

## 4. Where the rest of it is

| | |
|---|---|
| what was assumed (loads, material, machine, what was fixed) | `../01_assumptions/assumptions.md` |
| the design space and its validation | `../02_setup/` |
| the full analysis and every number | `../03_optimization/report.md` |


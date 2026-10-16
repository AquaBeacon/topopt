# Print this: `tall_tripod.stl`

> ## Half of this is a finished part. Know which half.
>
> The **interface** was re-cut from the source CAD on a 1.00 mm grid, so a feature width is good to within **1.00 mm** instead of 4.0 mm. Not exact — quote that tolerance, do not quote the drawing. The **optimised body** is still the analysis grid: resampling it added no detail it never had, and no mating dimension may be taken off it.
>
> The analysis did **not** see this shape. The FEA ran on the 4.0 mm interface, so stresses in and around it are resolved no better than that grid allows.

> ## The analysis verdict is FAIL.
>
> Printing this is fine for looking at, fitting, and checking the layout. It is not signed off to carry load. See section 3.

## 1. Files

| file | what it is |
|---|---|
| `tall_tripod.stl` | **the mesh to slice** — optimiser body (smoothed, sigma 2.00 mm, volume held) with the interface re-cut from CAD on a 1.00 mm grid |
| `tall_tripod_raw.stl` | what the optimiser produced, on the analysis grid |
| `tall_tripod_smooth.stl` | the same shape, Taubin-smoothed — nicer surface, slightly different dimensions |
| `tall_tripod.step` | solid model for CAD, if you are rebuilding the interfaces (you are) |
| `preview_shape.png` | what the optimiser produced, in section |
| `preview_design_space.png` | what it was allowed to fill — compare against the shape |
| `preview_stress.png` | safety factor on the **governing** load case — LC3se |
| `preview_orientation.png` | how it sits on the plate, and what overhangs |

Threshold **0.40** of the density field.

_Why this one: no candidate passes every check; this is the strongest of the connected ones and is NOT a pass. Outstanding: mesh resolution (2.0 (thin 8.00 mm) / 4.5 (typical 17.89 mm), required >= 3 elements across the thinnest section)_

## 2. How to print it

| | |
|---|---|
| orientation the analysis assumed | model **Z** axis up |
| fits the build volume | yes |
| size as modelled | 204.0 × 168.0 × 144.0 mm |
| lowest-support orientation | +Z up |
| support volume in this orientation | 268480 mm³ |

**Do not reorient it to save support.** The material is anisotropic and the analysis was run with the axis above pointing up; laying the part on its side moves the layer planes and the safety factors no longer apply to it. If another orientation is wanted, change `build_axis` in the study and re-run.

## 3. What is still wrong with it

These failed the stop conditions (spec 18). They are listed in full, with the fix, in `../03_optimization/report.md`.

- **The density field converged (not merely out of iterations)** — termination: max_iterations
- **Mesh convergence confirmed** — TOO COARSE (voxel FEA only: 2.0 (thin 8.00 mm) / 4.0 (typical 16.00 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.
- _not checked:_ Load/reaction balance within 1 %
- _not checked:_ Minimum wall thickness is met

_This list covers the stop conditions the report checks. **How much support the part needs is not one of them** -- that is judged by `topopt print <stl>`, which applies a 15 % overhang-area limit and is worth running before you slice._

## 4. Where the rest of it is

| | |
|---|---|
| what was assumed (loads, material, machine, what was fixed) | `../01_assumptions/assumptions.md` |
| the design space and its validation | `../02_setup/` |
| the full analysis and every number | `../03_optimization/report.md` |


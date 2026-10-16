# topopt report -- tall_tripod_study

## Verdict: **FAIL**

**FAIL** — 3 stop condition(s) failed. This part is not signed off.

| | conditions |
|---|---|
| failed | **3** |
| not checked | 1 |
| passed | 12 |

_The inputs this rests on — loads, material, machine, and what was fixed before the optimiser ran — are in `assumptions.md`. This page reports only what was measured._

## 1. What failed

These are errors, not warnings (spec 18). Each one names the fix.

- **The density field converged (not merely out of iterations)**  
  termination: max_iterations
- **Mesh convergence confirmed**  
  the numbers settled but the hotspot moved 17.49 mm (allowed 7.00); the two meshes are not describing the same failure, so it is a property of the discretisation, not of the part
- **Self-supporting along the build axis (no voxel without material in the 45 degree cone below it)**  
  1 voxel(s), 9 mm2 of material with nothing under it within 45 degrees (build axis +Z, at [[-63.0, -33.0, 36.0], [-60.0, -30.0, 39.0]]); it would print on support or not at all. Enable design.overhang, or move the build axis

## 2. What was not checked

1 condition(s) have no answer in this run. Not checked is not the same as fine: an analysis that was never run is not evidence the part is sound, so these count against it and not for it.

- Minimum wall thickness is met — _not checked in this run_

## 3. What passed

<details><summary>12 condition(s) passed</summary>

- Units are declared and are mm / N / MPa (mm / N / MPa)
- Material properties have no unresolved nulls (orthotropic_equivalent_solid, provisional card (no values measured on this printer))
- Material coordinate system (build_axis) is defined (build axis Z)
- Setup validation passed (preserve/obstacle, loads on preserve) (0 error(s), 14 warning(s))
- All preserve regions are connected (1 component(s))
- The optimiser's stress constraint held on the final density field (not requested (design.stress off); strength is judged by the final FEA only)
- The final shape was re-analysed (6 load case(s) re-solved on the thresholded solid)
- Safety factor meets the requirement on every case (p99.5 201.25 vs required 1.50 (worst element 45.10))
- Load/reaction balance within 1 % (worst 0.000 %)
- Final shape is watertight (0 open edges, 1 body, no self-intersection)
- No support lands on a mating (preserve) face (no support lands on a preserve surface)
- No support inside a sealed cavity (all support is reachable from outside)

</details>

## 4. Which shape this is

The optimiser produces a density field, not a shape. A threshold turns it into one, and different thresholds give different parts.

| threshold | volume (mm³) | safety factor (p99.5) | thinnest member (mm) |
|---|---|---|---|
| 0.40 **← chosen** | 297891 | 215.82 | 6.00 — grid-limited |
| 0.50 | 281313 | 201.51 | 6.00 — grid-limited |
| 0.60 | 263871 | 178.27 | 6.00 — grid-limited |

**Why this one:** no candidate passes every check; this is the strongest of the connected ones and is NOT a pass. Outstanding: mesh resolution (2.0 (thin 6.00 mm) / 5.7 (typical 16.97 mm), required >= 3 elements across the thinnest section)

> A thinnest member marked *grid-limited* is exactly two voxels across, which is the finest the grid can represent. That number describes the mesh, not the structure.

## 5. How it was solved

| | |
|---|---|
| iterations | 80 of 80 requested |
| converged | **False** |
| why it stopped | `max_iterations` |
| volume fraction (of free cells) | 0.04 |
| filter radius | 20.0 mm |
| build axis | Z |
| material model | `orthotropic_equivalent_solid` |
| optimiser | oc |
| stress constraint | none (volume and stiffness only) |
| wall-clock | 1146 s |

> The material card is provisional — none of its values were measured on this printer. Use these results to compare options (build direction, layout, load case), not as an absolute strength.

## 6. Structural results

| load case | class | required | safety factor (p99.5) | safety factor (worst element) | max displacement (mm) |
|---|---|---|---|---|---|
| LC1f  push 1.0 N horizontally at the camera mount | normal | 3.00 | 2691.16 | 1313.09 | 0.001 |
| LC2   camera weight plus 5x vertical shock | normal | 3.00 | 712.47 | 391.54 | 0.002 |
| LC3n  lift by the head, north foot caught | abnormal | 1.50 | 231.78 | 72.41 | 0.017 |
| LC3sw lift by the head, south-west foot caught | abnormal | 1.50 | 212.51 | 47.03 | 0.025 |
| LC3se lift by the head, south-east foot caught | abnormal | 1.50 | 201.25 | 45.10 | 0.026 |
| LC4t  twist the head about the vertical axis | normal | 3.00 | 1198.64 | 731.81 | 0.001 |

Required: safety factor ≥ 3.00 on every normal case; abnormal cases (drop, one foot caught) are judged at their own `required` column (spec 15.1).

> The p99.5 value decides pass/fail; the worst element decides whether a person has to go and look, because a single stair-stepped voxel at a load point is an artefact of the grid rather than of the part.

## 7. Can it be made

| | |
|---|---|
| machine | Bambu Lab X1-Carbon |
| usable build volume | 250 × 250 × 250 mm |
| design space | 240 × 240 × 147 mm |
| fits with Z up | **yes** |

| | |
|---|---|
| build axis | Z |
| flat overhang area | 8658.0 mm² |
| support volume | 243189.0 mm³ |
| support on mating faces | no support lands on a preserve surface |
| enclosed support | all support is reachable from outside |

> measured on the voxel grid, so overhang angles are only 0 or 90 degrees. A 60-degree face that would self-support is not counted here; `topopt overhang` reads real angles off the STL.

## 8. What the slicer says

| | |
|---|---|
| slicer | ? |
| profiles | Bambu Lab X1 Carbon 0.4 nozzle / 0.20mm Standard @BBL X1C / Bambu PLA Basic @BBL X1C |
| supports | normal(auto) |
| orientation | keep |
| print time | 7h 14m 4s (26044 s) |
| filament | 303.25 g / 240673 mm3 |
| of which support | 139.13 g / 110421 mm3 (45.9 % of filament) |
| layers | 1159 (object 983) at 0.2 mm |
| fits the plate as sliced | **yes** |
| spec 13.3 support conditions | **UNKNOWN** (5) |

> Support here is extruded 1.75 mm filament -- the material thrown away -- and is not the same quantity as `printcheck.json`'s `support_volume_mm3`, which is part-space volume on the voxel grid. All five spec 13.3 conditions ask *where* the support is, and the slicer only ever says how much; `printcheck` answers the mating-face and sealed-cavity ones.

## 9. How much of the shape was optimised

| | |
|---|---|
| share of the domain free to decide | 99 % |
| share of the expected part that is preserve | 6 % |

Positions fixed by the CAD model, not chosen by the optimiser:

| | region | position (mm) |
|---|---|---|
| load | `mount_head` | [2.2, 0.0, 133.9] |
| load | `head_plus_x` | [7.1, 0.0, 134.2] |
| load | `head_minus_x` | [-5.3, 0.0, 133.5] |

> Loads and supports may only act on preserve, so where the part is held is an input to this study. The optimiser decided how to reach those points, not where they are.

## 10. Independent re-analysis (CalculiX)

| mesh (mm) | nodes | safety factor (p99.5) | max displacement (mm) |
|---|---|---|---|
| 14.00 | 45309 | 268.54 | 0.0223 |
| 7.00 | 69249 | 296.93 | 0.0230 |
| 3.50 | 208751 | 308.09 | 0.0224 |
## 11. Reproducibility

| | |
|---|---|
| commit | `57541b1feb77cf083f5656a8ec85ee27448cfd18` **(working tree dirty)** |
| tool version | 1.0.0 |
| python | 3.13.5 on Windows-11-10.0.26200-SP0 |
| numpy / scipy | 2.4.2 / 1.18.0 |
| run at | 2026-09-09T15:02:33Z (- s) |

> The working tree had uncommitted changes when this ran, so the commit above does not fully describe the code that produced these numbers.

**External tools on this machine:**

- calculix: `This is Version 2.23`
- freecad: **not installed**
- gmsh: `4.15.2`
- openscad: `OpenSCAD version 2021.01`
- slicer: `None`
> A stage that needs a tool this machine does not have was not run. Its conditions are reported as not checked above, never as passing.


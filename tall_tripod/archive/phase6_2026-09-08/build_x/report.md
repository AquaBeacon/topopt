# topopt report -- build_x_study

## Verdict: **FAIL**

**FAIL** — 3 stop condition(s) failed. This part is not signed off.

| | conditions |
|---|---|
| failed | **3** |
| not checked | 3 |
| passed | 10 |

_The inputs this rests on — loads, material, machine, and what was fixed before the optimiser ran — are in `assumptions.md`. This page reports only what was measured._

## 1. What failed

These are errors, not warnings (spec 18). Each one names the fix.

- **The density field converged (not merely out of iterations)**  
  termination: max_iterations
- **Mesh convergence confirmed**  
  TOO COARSE (voxel FEA only: 2.0 (thin 20.00 mm) / 2.0 (typical 20.00 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.
- **Self-supporting along the build axis (no voxel without material in the 45 degree cone below it)**  
  3 voxel(s), 300 mm2 of material with nothing under it within 45 degrees (build axis +X, at [[-90.0, -30.0, 0.0], [-10.0, 30.0, 60.0]]); it would print on support or not at all. Enable design.overhang, or move the build axis

## 2. What was not checked

3 condition(s) have no answer in this run. Not checked is not the same as fine: an analysis that was never run is not evidence the part is sound, so these count against it and not for it.

- Load/reaction balance within 1 % — _not checked in this run_
- Final shape is watertight — _not checked in this run_
- Minimum wall thickness is met — _not checked in this run_

## 3. What passed

<details><summary>10 condition(s) passed</summary>

- Units are declared and are mm / N / MPa (mm / N / MPa)
- Material properties have no unresolved nulls (orthotropic_equivalent_solid, provisional card (no values measured on this printer))
- Material coordinate system (build_axis) is defined (build axis X)
- Setup validation passed (preserve/obstacle, loads on preserve) (0 error(s), 2 warning(s))
- All preserve regions are connected (2 component(s); 1000 mm3 of floating islands were dropped)
- The optimiser's stress constraint held on the final density field (not requested (design.stress off); strength is judged by the final FEA only)
- The final shape was re-analysed (6 load case(s) re-solved on the thresholded solid)
- Safety factor meets the requirement on every case (p99.5 125.85 vs required 3.00 (worst element 80.96))
- No support lands on a mating (preserve) face (no support lands on a preserve surface)
- No support inside a sealed cavity (all support is reachable from outside)

</details>

## 4. Which shape this is

The optimiser produces a density field, not a shape. A threshold turns it into one, and different thresholds give different parts.

| threshold | volume (mm³) | safety factor (p99.5) | thinnest member (mm) |
|---|---|---|---|
| 0.40 **← chosen** | 320000 | 130.91 | 20.00 — grid-limited |
| 0.50 | 316000 | 125.85 | 20.00 — grid-limited |
| 0.60 | 306000 | 93.46 | 20.00 — grid-limited |

**Why this one:** no candidate passes every check; this is the strongest of the connected ones and is NOT a pass. Outstanding: mesh resolution (2.0 (thin 20.00 mm) / 2.0 (typical 20.00 mm), required >= 3 elements across the thinnest section)

> A thinnest member marked *grid-limited* is exactly two voxels across, which is the finest the grid can represent. That number describes the mesh, not the structure.

## 5. How it was solved

| | |
|---|---|
| iterations | 80 of 80 requested |
| converged | **False** |
| why it stopped | `max_iterations` |
| volume fraction (of free cells) | 0.04 |
| filter radius | 20.0 mm |
| build axis | X |
| material model | `orthotropic_equivalent_solid` |
| optimiser | oc |
| stress constraint | none (volume and stiffness only) |
| wall-clock | 85 s |

> The material card is provisional — none of its values were measured on this printer. Use these results to compare options (build direction, layout, load case), not as an absolute strength.

## 6. Structural results

| load case | class | required | safety factor (p99.5) | safety factor (worst element) | max displacement (mm) |
|---|---|---|---|---|---|
| LC1f  push 1.0 N horizontally at the camera mount | normal | 3.00 | 2450.90 | 1961.59 | 0.001 |
| LC2   camera weight plus 5x vertical shock | normal | 3.00 | 746.04 | 689.42 | 0.002 |
| LC3n  lift by the head, north foot caught | normal | 3.00 | 145.56 | 110.27 | 0.017 |
| LC3sw lift by the head, south-west foot caught | normal | 3.00 | 125.85 | 99.22 | 0.022 |
| LC3se lift by the head, south-east foot caught | normal | 3.00 | 138.82 | 80.96 | 0.034 |
| LC4t  twist the head about the vertical axis | normal | 3.00 | 1811.68 | 1758.12 | 0.001 |

Required: safety factor ≥ 3.00 on every normal case; abnormal cases (drop, one foot caught) are judged at their own `required` column (spec 15.1).

> The p99.5 value decides pass/fail; the worst element decides whether a person has to go and look, because a single stair-stepped voxel at a load point is an artefact of the grid rather than of the part.

## 7. Can it be made

| | |
|---|---|
| machine | Bambu Lab X1-Carbon |
| usable build volume | 250 × 250 × 250 mm |
| design space | 240 × 240 × 150 mm |
| fits with X up | **yes** |

| | |
|---|---|
| build axis | X |
| flat overhang area | 12300.0 mm² |
| support volume | 1382000.0 mm³ |
| support on mating faces | no support lands on a preserve surface |
| enclosed support | all support is reachable from outside |

> measured on the voxel grid, so overhang angles are only 0 or 90 degrees. A 60-degree face that would self-support is not counted here; `topopt overhang` reads real angles off the STL.

## 8. How much of the shape was optimised

| | |
|---|---|
| share of the domain free to decide | 99 % |
| share of the expected part that is preserve | 4 % |

Positions fixed by the CAD model, not chosen by the optimiser:

| | region | position (mm) |
|---|---|---|
| load | `mount_head` | [0.0, 0.0, 135.0] |
| load | `head_plus_x` | [5.0, 0.0, 135.0] |
| load | `head_minus_x` | [-5.0, 0.0, 135.0] |

> Loads and supports may only act on preserve, so where the part is held is an input to this study. The optimiser decided how to reach those points, not where they are.

## 9. Independent re-analysis (CalculiX)

**Not run.** The voxel solver has not been checked against a second solver on a second mesh, so its results carry only its own assumptions.

## 10. Reproducibility

| | |
|---|---|
| commit | `7efd824afd8e57888b259d27260641588fc31e17` **(working tree dirty)** |
| tool version | 1.0.0 |
| python | 3.13.5 on Windows-11-10.0.26200-SP0 |
| numpy / scipy | 2.4.2 / 1.18.0 |
| run at | 2026-09-08T05:15:42Z (- s) |

> The working tree had uncommitted changes when this ran, so the commit above does not fully describe the code that produced these numbers.

**External tools on this machine:**

- calculix: `This is Version 2.23`
- freecad: **not installed**
- gmsh: `4.15.2`
- openscad: `OpenSCAD version 2021.01`
- slicer: **not installed**
> A stage that needs a tool this machine does not have was not run. Its conditions are reported as not checked above, never as passing.


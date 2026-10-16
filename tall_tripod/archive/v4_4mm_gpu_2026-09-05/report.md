# topopt report -- tall_tripod_study

## Verdict: **FAIL**

**FAIL** — 2 stop condition(s) failed. This part is not signed off.

| | conditions |
|---|---|
| failed | **2** |
| not checked | 3 |
| passed | 9 |

_The inputs this rests on — loads, material, machine, and what was fixed before the optimiser ran — are in `assumptions.md`. This page reports only what was measured._

## 1. What failed

These are errors, not warnings (spec 18). Each one names the fix.

- **The density field converged (not merely out of iterations)**  
  termination: max_iterations
- **Mesh convergence confirmed**  
  TOO COARSE (voxel FEA only: 2.2 (thin 8.99 mm) / 4.5 (typical 17.89 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.

## 2. What was not checked

3 condition(s) have no answer in this run. Not checked is not the same as fine: an analysis that was never run is not evidence the part is sound, so these count against it and not for it.

- Load/reaction balance within 1 % — _not checked in this run_
- Final shape is watertight — _not checked in this run_
- Minimum wall thickness is met — _not checked in this run_

## 3. What passed

<details><summary>9 condition(s) passed</summary>

- Units are declared and are mm / N / MPa (mm / N / MPa)
- Material properties have no unresolved nulls (orthotropic_equivalent_solid, provisional card (no values measured on this printer))
- Material coordinate system (build_axis) is defined (build axis Z)
- Setup validation passed (preserve/obstacle, loads on preserve) (0 error(s), 14 warning(s))
- All preserve regions are connected (1 component(s))
- The final shape was re-analysed (6 load case(s) re-solved on the thresholded solid)
- Safety factor meets the requirement on every case (p99.5 90.84 vs required 3.00 (worst element 26.58))
- No support lands on a mating (preserve) face (no support lands on a preserve surface)
- No support inside a sealed cavity (all support is reachable from outside)

</details>

## 4. Which shape this is

The optimiser produces a density field, not a shape. A threshold turns it into one, and different thresholds give different parts.

| threshold | volume (mm³) | safety factor (p99.5) | thinnest member (mm) |
|---|---|---|---|
| 0.40 **← chosen** | 281344 | 124.58 | 8.00 — grid-limited |
| 0.50 | 266624 | 90.84 | 8.00 — grid-limited |
| 0.60 | 251584 | 61.66 | 8.00 — grid-limited |

**Why this one:** no candidate passes every check; this is the strongest of the connected ones and is NOT a pass. Outstanding: mesh resolution (2.0 (thin 8.00 mm) / 4.5 (typical 17.89 mm), required >= 3 elements across the thinnest section)

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
| wall-clock | 342 s |

> The material card is provisional — none of its values were measured on this printer. Use these results to compare options (build direction, layout, load case), not as an absolute strength.

## 6. Structural results

| load case | safety factor (p99.5) | safety factor (worst element) | max displacement (mm) |
|---|---|---|---|
| LC1f  push 1.0 N horizontally at the camera mount | 3525.74 | 2226.58 | 0.001 |
| LC2   camera weight plus 5x vertical shock | 808.45 | 502.27 | 0.003 |
| LC3n  lift by the head, north foot caught | 238.89 | 82.24 | 0.018 |
| LC3sw lift by the head, south-west foot caught | 90.84 | 26.58 | 0.046 |
| LC3se lift by the head, south-east foot caught | 158.83 | 50.19 | 0.024 |
| LC4t  twist the head about the vertical axis | 2027.05 | 1242.90 | 0.001 |

Required: safety factor ≥ 3.00 on every case.

> The p99.5 value decides pass/fail; the worst element decides whether a person has to go and look, because a single stair-stepped voxel at a load point is an artefact of the grid rather than of the part.

## 7. Can it be made

| | |
|---|---|
| machine | Bambu Lab X1-Carbon |
| usable build volume | 250 × 250 × 250 mm |
| design space | 240 × 240 × 144 mm |
| fits with Z up | **yes** |

| | |
|---|---|
| build axis | Z |
| flat overhang area | 8224.0 mm² |
| support volume | 243968.0 mm³ |
| support on mating faces | no support lands on a preserve surface |
| enclosed support | all support is reachable from outside |

> measured on the voxel grid, so overhang angles are only 0 or 90 degrees. A 60-degree face that would self-support is not counted here; `topopt overhang` reads real angles off the STL.

## 8. How much of the shape was optimised

| | |
|---|---|
| share of the domain free to decide | 99 % |
| share of the expected part that is preserve | 7 % |

Positions fixed by the CAD model, not chosen by the optimiser:

| | region | position (mm) |
|---|---|---|
| load | `mount_head` | [2.5, 0.0, 135.0] |
| load | `head_plus_x` | [6.8, 0.0, 135.2] |
| load | `head_minus_x` | [-4.7, 0.0, 134.7] |

> Loads and supports may only act on preserve, so where the part is held is an input to this study. The optimiser decided how to reach those points, not where they are.

## 9. Independent re-analysis (CalculiX)

**Not run.** The voxel solver has not been checked against a second solver on a second mesh, so its results carry only its own assumptions.

## 10. Reproducibility

| | |
|---|---|
| commit | `1ffa0c88feb867a77f4af8b1ab36e8b11eb7f5ef` **(working tree dirty)** |
| tool version | 1.0.0 |
| python | 3.13.5 on Windows-11-10.0.26200-SP0 |
| numpy / scipy | 2.4.2 / 1.18.0 |
| run at | 2026-09-05T03:58:26Z (- s) |

> The working tree had uncommitted changes when this ran, so the commit above does not fully describe the code that produced these numbers.

**External tools on this machine:**

- calculix: **not installed**
- freecad: **not installed**
- gmsh: **not installed**
- openscad: `OpenSCAD version 2021.01`
- slicer: **not installed**
> A stage that needs a tool this machine does not have was not run. Its conditions are reported as not checked above, never as passing.


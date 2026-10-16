# topopt report -- tall_tripod_study

## Verdict: **FAIL**

**FAIL** — 1 stop condition(s) failed. This part is not signed off.

| | conditions |
|---|---|
| failed | **1** |
| not checked | 6 |
| passed | 8 |

_The inputs this rests on — loads, material, machine, and what was fixed before the optimiser ran — are in `assumptions.md`. This page reports only what was measured._

## 1. What failed

These are errors, not warnings (spec 18). Each one names the fix.

- **The density field converged (not merely out of iterations)**  
  termination: max_iterations

## 2. What was not checked

6 condition(s) have no answer in this run. Not checked is not the same as fine: an analysis that was never run is not evidence the part is sound, so these count against it and not for it.

- Safety factor meets the requirement on every case — _not checked in this run_
- Load/reaction balance within 1 % — _not checked in this run_
- Final shape is watertight — _not checked in this run_
- Minimum wall thickness is met — _not checked in this run_
- No support lands on a mating (preserve) face — _not checked in this run_
- No support inside a sealed cavity — _not checked in this run_

## 3. What passed

<details><summary>8 condition(s) passed</summary>

- Units are declared and are mm / N / MPa (mm / N / MPa)
- Material properties have no unresolved nulls (orthotropic_equivalent_solid, provisional card (no values measured on this printer))
- Material coordinate system (build_axis) is defined (build axis Z)
- Setup validation passed (preserve/obstacle, loads on preserve) (0 error(s), 14 warning(s))
- All preserve regions are connected (1 component(s))
- The final shape was re-analysed (6 load case(s) re-solved on the thresholded solid)
- Mesh convergence confirmed (adequate (voxel FEA only: 4.0 (thin 12.00 mm) / 5.7 (typical 16.97 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.)
- Self-supporting along the build axis (no voxel without material in the 45 degree cone below it) (every optimised voxel has material in the 45 degree cone below it (build axis +Z); 1 voxel(s) / 9 mm2 hang off a preserve body -- that is the interface's own underside, chamfer or roof it in CAD)

</details>

## 4. How it was solved

| | |
|---|---|
| iterations | 150 of 150 requested |
| converged | **False** |
| why it stopped | `max_iterations` |
| volume fraction (of free cells) | 0.04 |
| filter radius | 20.0 mm |
| build axis | Z |
| material model | `orthotropic_equivalent_solid` |
| wall-clock | 3343 s |

> The material card is provisional — none of its values were measured on this printer. Use these results to compare options (build direction, layout, load case), not as an absolute strength.

## 5. Structural results

| load case | safety factor (p99.5) | safety factor (worst element) | max displacement (mm) |
|---|---|---|---|
| LC1f  push 1.0 N horizontally at the camera mount | 4638.72 | 3271.65 | 0.001 |
| LC2   camera weight plus 5x vertical shock | 1039.18 | 648.34 | 0.002 |
| LC3n  lift by the head, north foot caught | 280.72 | 71.75 | 0.016 |
| LC3sw lift by the head, south-west foot caught | 202.64 | 44.79 | 0.026 |
| LC3se lift by the head, south-east foot caught | 193.42 | 43.76 | 0.027 |
| LC4t  twist the head about the vertical axis | 2243.30 | 1361.58 | 0.001 |

> The p99.5 value decides pass/fail; the worst element decides whether a person has to go and look, because a single stair-stepped voxel at a load point is an artefact of the grid rather than of the part.

## 6. Can it be made

| | |
|---|---|
| machine | Bambu Lab X1-Carbon |
| usable build volume | 250 × 250 × 250 mm |
| design space | 240 × 240 × 147 mm |
| fits with Z up | **yes** |

## 7. How much of the shape was optimised

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

## 8. Independent re-analysis (CalculiX)

**Not run.** The voxel solver has not been checked against a second solver on a second mesh, so its results carry only its own assumptions.

## 9. Reproducibility

| | |
|---|---|
| commit | `1ffa0c88feb867a77f4af8b1ab36e8b11eb7f5ef` **(working tree dirty)** |
| tool version | 1.0.0 |
| python | 3.13.5 on Windows-11-10.0.26200-SP0 |
| numpy / scipy | 2.4.2 / 1.18.0 |
| run at | 2026-09-05T09:35:40Z (- s) |

> The working tree had uncommitted changes when this ran, so the commit above does not fully describe the code that produced these numbers.

**External tools on this machine:**

- calculix: **not installed**
- freecad: **not installed**
- gmsh: **not installed**
- openscad: `OpenSCAD version 2021.01`
- slicer: **not installed**
> A stage that needs a tool this machine does not have was not run. Its conditions are reported as not checked above, never as passing.


# topopt report -- stress_study

## Verdict: **FAIL**

**FAIL** — 3 stop condition(s) failed. This part is not signed off.

| | conditions |
|---|---|
| failed | **3** |
| not checked | 2 |
| passed | 11 |

_The inputs this rests on — loads, material, machine, and what was fixed before the optimiser ran — are in `assumptions.md`. This page reports only what was measured._

## 1. What failed

These are errors, not warnings (spec 18). Each one names the fix.

- **The density field converged (not merely out of iterations)**  
  termination: max_iterations
- **Mesh convergence confirmed**  
  TOO COARSE (voxel FEA only: 2.0 (thin 20.00 mm) / 2.0 (typical 20.00 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.
- **Self-supporting along the build axis (no voxel without material in the 45 degree cone below it)**  
  7 voxel(s), 700 mm2 of material with nothing under it within 45 degrees (build axis +Z, at [[-60.0, -40.0, 10.0], [80.0, 90.0, 70.0]]); it would print on support or not at all. Enable design.overhang, or move the build axis

## 2. What was not checked

2 condition(s) have no answer in this run. Not checked is not the same as fine: an analysis that was never run is not evidence the part is sound, so these count against it and not for it.

- Load/reaction balance within 1 % — _not checked in this run_
- Minimum wall thickness is met — _not checked in this run_

## 3. What passed

<details><summary>11 condition(s) passed</summary>

- Units are declared and are mm / N / MPa (mm / N / MPa)
- Material properties have no unresolved nulls (orthotropic_equivalent_solid, provisional card (no values measured on this printer))
- Material coordinate system (build_axis) is defined (build axis Z)
- Setup validation passed (preserve/obstacle, loads on preserve) (0 error(s), 2 warning(s))
- All preserve regions are connected (1 component(s))
- The optimiser's stress constraint held on the final density field (SF 1.50 (abnormal) x relaxed max-stress index 0.024 in 'LC3sw lift by the head, south-west foot caught' (interlayer_tension), on the analysis grid at the final field)
- The final shape was re-analysed (6 load case(s) re-solved on the thresholded solid)
- Safety factor meets the requirement on every case (p99.5 90.27 vs required 3.00 (worst element 70.82))
- Final shape is watertight (0 open edges, 1 body, no self-intersection)
- No support lands on a mating (preserve) face (no support lands on a preserve surface)
- No support inside a sealed cavity (all support is reachable from outside)

</details>

## 4. Which shape this is

The optimiser produces a density field, not a shape. A threshold turns it into one, and different thresholds give different parts.

| threshold | volume (mm³) | safety factor (p99.5) | thinnest member (mm) |
|---|---|---|---|
| 0.40 | 296000 | 90.27 | 20.00 — grid-limited |
| 0.50 | 293000 | 90.27 | 20.00 — grid-limited |
| 0.60 **← chosen** | 291000 | 91.01 | 20.00 — grid-limited |

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
| build axis | Z |
| material model | `orthotropic_equivalent_solid` |
| optimiser | mma |
| stress constraint | SF 3.00 (abnormal case(s) at SF 1.50) x max-stress index <= 1 per case (p-norm 8, rho^0.5 relaxation) -- **held** on the final field |
| wall-clock | 677 s |

> The material card is provisional — none of its values were measured on this printer. Use these results to compare options (build direction, layout, load case), not as an absolute strength.

## 6. Structural results

| load case | class | required | safety factor (p99.5) | safety factor (worst element) | max displacement (mm) |
|---|---|---|---|---|---|
| LC1f  push 1.0 N horizontally at the camera mount | normal | 3.00 | 3546.83 | 2860.79 | 0.001 |
| LC2   camera weight plus 5x vertical shock | normal | 3.00 | 645.34 | 506.04 | 0.003 |
| LC3n  lift by the head, north foot caught | normal | 3.00 | 155.02 | 140.84 | 0.018 |
| LC3sw lift by the head, south-west foot caught | normal | 3.00 | 90.27 | 70.82 | 0.026 |
| LC3se lift by the head, south-east foot caught | normal | 3.00 | 94.73 | 86.02 | 0.031 |
| LC4t  twist the head about the vertical axis | normal | 3.00 | 1699.54 | 1628.20 | 0.001 |

Required: safety factor ≥ 3.00 on every normal case; abnormal cases (drop, one foot caught) are judged at their own `required` column (spec 15.1).

> The p99.5 value decides pass/fail; the worst element decides whether a person has to go and look, because a single stair-stepped voxel at a load point is an artefact of the grid rather than of the part.

## 7. Can it be made

| | |
|---|---|
| machine | Bambu Lab X1-Carbon |
| usable build volume | 250 × 250 × 250 mm |
| design space | 240 × 240 × 150 mm |
| fits with Z up | **yes** |

| | |
|---|---|
| build axis | Z |
| flat overhang area | 7800.0 mm² |
| support volume | 325000.0 mm³ |
| support on mating faces | no support lands on a preserve surface |
| enclosed support | all support is reachable from outside |

> measured on the voxel grid, so overhang angles are only 0 or 90 degrees. A 60-degree face that would self-support is not counted here; `topopt overhang` reads real angles off the STL.

## 8. What the slicer says

**UNKNOWN** -- no `slice.json` in this run. Print time, real filament use and real support quantity come from a slicer and from nothing else; they are not estimated here.

    topopt slice PART.stl --out DIR

## 9. How much of the shape was optimised

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

## 10. Independent re-analysis (CalculiX)

**Not run.** The voxel solver has not been checked against a second solver on a second mesh, so its results carry only its own assumptions.

## 11. Reproducibility

| | |
|---|---|
| commit | `bd7fc67d574e424a816af99331583e7978d217a0` **(working tree dirty)** |
| tool version | 1.0.0 |
| python | 3.13.5 on Windows-11-10.0.26200-SP0 |
| numpy / scipy | 2.4.2 / 1.18.0 |
| run at | 2026-09-08T07:19:58Z (- s) |

> The working tree had uncommitted changes when this ran, so the commit above does not fully describe the code that produced these numbers.

**External tools on this machine:**

- calculix: `This is Version 2.23`
- freecad: **not installed**
- gmsh: `4.15.2`
- openscad: `OpenSCAD version 2021.01`
- slicer: `BambuStudio-02.05.00.66`
> A stage that needs a tool this machine does not have was not run. Its conditions are reported as not checked above, never as passing.


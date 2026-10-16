# topopt report -- run

## Verdict: **FAIL**

4 stop condition(s) failed. These are errors, not warnings (spec 18).

- **FAIL — The density field converged (not merely out of iterations)**  
  termination: max_iterations
- **FAIL — Mesh convergence confirmed**  
  TOO COARSE (voxel FEA only: 2.0 (thin 8.00 mm) / 2.0 (typical 8.00 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.
- **FAIL — No support lands on a mating (preserve) face**  
  support rests on 86 preserve voxel(s) (1376.0 mm2). Those faces come off the printer rough and out of tolerance. Reorient the part, or accept the finish and say so explicitly.
- **FAIL — No support inside a sealed cavity**  
  7488.0 mm3 of support sits in a cavity with no way out. It cannot be removed after printing. Add an access or drain opening, split the part, or reorient so the cavity is not spanned.

3 condition(s) were **not checked**. Not checked is not the same as fine: an analysis that was never run is not evidence the part is sound.

- UNKNOWN — Load/reaction balance within 1 %  
  not checked in this run
- UNKNOWN — Final shape is watertight  
  not checked in this run
- UNKNOWN — Minimum wall thickness is met  
  not checked in this run

<details><summary>7 condition(s) passed</summary>

- PASS — Units are declared and are mm / N / MPa (mm / N / MPa)
- PASS — Material properties have no unresolved nulls (orthotropic_equivalent_solid, provisional card (no values measured on this printer))
- PASS — Material coordinate system (build_axis) is defined (build axis Z)
- PASS — Setup validation passed (preserve/obstacle, loads on preserve) (0 error(s), 9 warning(s))
- PASS — All preserve regions are connected (1 component(s))
- PASS — The final shape was re-analysed (4 load case(s) re-solved on the thresholded solid)
- PASS — Safety factor meets the requirement on every case (p99.5 61.21 vs required 3.00 (worst element 20.04))

</details>

## Optimisation

| | |
|---|---|
| iterations | 40 of 40 |
| converged | False |
| termination | `max_iterations` |
| volume fraction (free cells) | 0.22 |
| filter | 6.0 mm |
| build axis | Z |
| seconds | 13012.7 |

**Material model:** `orthotropic_equivalent_solid`

> The material card is provisional -- none of its values were measured on this printer. Use it to compare build directions, not as an absolute strength.

## Structural results

| case | SF p99.5 | SF worst element | max displacement |
|---|---|---|---|
| LC1f  push 1.0 N horizontally at the camera top (tipping limit) | 1121.11 | 619.57 | 0.007 mm |
| LC2   camera weight plus 5x vertical shock | 278.15 | 172.44 | 0.018 mm |
| LC3x  lift by the head with one foot caught | 61.21 | 20.04 | 0.359 mm |
| LC4t  twist the head about the mast axis | 288.26 | 172.88 | 0.042 mm |

The p99.5 value decides pass/fail; the worst element decides whether a person has to go and look, because a single stair-stepped voxel at a load point is an artefact of the grid rather than of the part.

## Threshold candidates

| level | volume mm3 | SF p99.5 | min thickness |
|---|---|---|---|
| 0.40 **chosen** | 429504 | 61.22 | 8.00 (grid-limited) |
| 0.50 | 429376 | 61.21 | 8.00 (grid-limited) |
| 0.60 | 429248 | 61.21 | 8.00 (grid-limited) |

_no candidate passes every check; this is the strongest of the connected ones and is NOT a pass. Outstanding: mesh resolution (2.0 (thin 8.00 mm) / 2.0 (typical 8.00 mm), required >= 3 elements across the thinnest section)_

## Printability

| | |
|---|---|
| build axis | Z |
| flat overhang area | 32784.0 mm2 |
| support volume | 488064.0 mm3 |
| support on mating faces | support rests on 86 preserve voxel(s) (1376.0 mm2). Those faces come off the printer rough and out of tolerance. Reorient the part, or accept the finish and say so explicitly. |
| enclosed support | 7488.0 mm3 of support sits in a cavity with no way out. It cannot be removed after printing. Add an access or drain opening, split the part, or reorient so the cavity is not spanned. |

_measured on the voxel grid, so overhang angles are only 0 or 90 degrees. A 60-degree face that would self-support is not counted here; `topopt overhang` reads real angles off the STL._

## Independent re-analysis (CalculiX)

**Not run.** The voxel solver has not been checked against a second solver on a second mesh, so its results carry only its own assumptions.

## Reproducibility

| | |
|---|---|
| commit | `not recorded` |
| working tree dirty | None |
| python | None |
| run at | None |

**External tools on this machine:**

- calculix: **not installed**
- freecad: **not installed**
- gmsh: **not installed**
- openscad: `OpenSCAD version 2021.01`
- slicer: **not installed**

_A stage that needs a tool this machine does not have was not run. Its conditions are reported as UNKNOWN above, never as passing._


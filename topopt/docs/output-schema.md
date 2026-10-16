# Output JSON schemas

Generated from `topopt/src/topopt/outschema.py` -- do not hand-edit; run `python -m topopt validate --print-doc` and check the result in.

Every file below carries a `schema` key of the form `topopt/<kind>/<version>` (one exception noted in its section) so `topopt validate` can identify it without guessing from its keys. An unknown top-level key is a warning, not an error -- these files accumulate fields across phases the same way study files accumulate legacy keys, and a validator that rejects a file the tools would have read correctly is worse than none.

## `assumptions`

The 前提条件 page as data: loads, material, machine, what was fixed.

- **Written by:** topopt assumptions (also `topopt run`, writes `assumptions.json`)
- **`schema` value:** `topopt/assumptions/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `build_volume` | any | no | printer-fit report, same shape as setup's info.build_volume. | - |
| `cases` | arr | yes | every load case, in the words of what it represents. | - |
| `caveat` | any | yes | the study's `_not_a_part`/`_caveat` comment, or null. | - |
| `design_freedom` | any | no | spec 16.4.2 freedom-of-design-space report. | - |
| `design_settings` | obj | yes | volfrac, filter_mm, symmetry, voxel_mm, target_elements. | - |
| `design_space` | obj | no | voxel size, grid shape, bbox -- present once a setup was built. | - |
| `geometry_sources` | obj | yes | which files/regions fed preserve, obstacle, design_space, starting_shape. | - |
| `manufacturing` | obj | yes | printer, build axis, raster axis, walls, infill, and whether each was declared. | - |
| `material` | obj | yes | the full material card and where its numbers came from. | - |
| `part` | any | yes | the part name (falls back to the study filename). | - |
| `requirements` | obj | yes | safety factor, max deflection, required cases. | - |
| `schema` | str | yes | `topopt/assumptions/1`. | - |
| `setup_errors` | arr | no | setup validation errors. | - |
| `setup_valid` | bool | no | whether the setup validation passed. | - |
| `setup_warnings` | arr | no | setup validation warnings. | - |
| `study_file` | any | yes | basename of the study file. | - |
| `units` | str | yes | always 'mm / N / MPa (...)' -- the only system this tool implements. | - |
| `what` | any | yes | the study's `_what`/`_purpose` comment, or null. | - |
| `why` | any | yes | the study's `_why`/`_rationale` comment, or null. | - |

## `ccx`

Independent re-analysis with CalculiX on a tet mesh.

- **Written by:** topopt ccx (writes `ccx_summary.json`)
- **`schema` value:** `topopt/ccx/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `map_mode` | str | yes | how boundary conditions were mapped onto the tet mesh: volume (default) or surface. | - |
| `mesh_convergence` | obj | no | present only with --converge: pass/note across the mesh sizes solved. | cli/report.py::_mesh_converged |
| `runs` | arr | yes | one entry per mesh size solved (just one unless --converge): nodes, per-case results, mass properties, mesh quality. | cli/report.py::render (cc['runs']), _equilibrium |
| `schema` | str | yes | `topopt/ccx/1`. | - |
| `stl` | str | yes | the STL that was meshed and re-analysed. | - |
| `stl_sha256` | str | no | SHA-256 of the input STL, used to identify the finished shape. | cli/report.py |
| `study` | str | yes | the study the loads/supports/material came from. | - |
| `study_sha256` | str | no | SHA-256 of the study used for this analysis. | cli/report.py |
| `vs_voxel_fea` | obj | no | present only with --voxel-disp: the finest run's max displacement vs. a single voxel-FEA number. | cli/report.py::render |
| `vs_voxel_fea_cases` | obj | no | present only with --voxel-fea PATH: case-by-case comparison against a `topopt fea` run (spec 14.4). | - |

## `extract`

Density field -> shape candidates at several thresholds.

- **Written by:** topopt extract (writes `<base>_extract.json`)
- **`schema` value:** `topopt/extract/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `candidates` | arr | yes | one entry per threshold: volumes, thickness, connectivity, FEA. | cli/report.py::render (ex['candidates']) |
| `chosen_level` | num | yes | the threshold picked (by --pick, or by `_choose`). | cli/report.py::collect, cli/run.py::_chosen_stl |
| `chosen_reason` | str | yes | why this threshold, in the words of the checks that decided. | - |
| `finish` | obj | no | present only when the study declares a `finish` block: the CAD-dimension rebuild report. | - |
| `grid_origin_mm` | arr | yes | the grid's origin in study coordinates. | - |
| `levels` | arr | yes | the thresholds evaluated. | - |
| `material_model` | str | yes | isotropic_two_constant or orthotropic_equivalent_solid. | - |
| `outputs` | obj | yes | paths written for the chosen threshold (stl, smooth_stl, step, finish files). | cli/run.py::_chosen_stl (outputs.stl) |
| `required_safety_factor` | num | yes | the safety factor every candidate is judged against. | cli/report.py::collect (required_safety_factor) |
| `schema` | str | yes | `topopt/extract/1`. | - |
| `shell` | obj | no | spec 19.3: one shell report per written STL, keyed by the same tags as `outputs` (open edges, bodies, self-intersections). | cli/report.py::collect, _watertight |
| `source_dir` | str | yes | the `topopt opt` output directory this was built from. | - |
| `study` | str | yes | the study file. | - |
| `voxel_mm` | num | yes | the grid the density field was solved on -- must match the rebuilt setup or the candidates are not comparable to the run they came from. | - |

## `fea`

Voxel FEA on an STL: deflection, stress, safety factor per case.

- **Written by:** topopt fea (writes `<base>_fea.json`)
- **`schema` value:** `topopt/fea/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `cases` | arr | yes | one entry per load case: displacement, von Mises, safety factor, checks, hotspots (see `report.summarise`). | cli/report.py::collect (data['fea']), cli/ccx.py::_compare_voxel_fea, tests/test_regression_osmo.py (cases[i].voxel_mm/case/safety_factor_p99_5) |
| `material` | str | yes | material name. | - |
| `part` | str | yes | the STL that was re-analysed. | - |
| `pass` | bool | yes | whether every case passed its checks. | cli/run.py::_voxel_fea (presence), cli/ccx.py::_compare_voxel_fea |

## `fea-convergence`

[2H, H, H/2] mesh convergence, judged per load case.

- **Written by:** topopt fea --converge (writes `<base>_fea_convergence.json`)
- **`schema` value:** `topopt/fea-convergence/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `cases` | obj | yes | per-case name -> {runs: [...], verdict: {...}}. | - |
| `governing_case` | str | yes | the case with the lowest fine-mesh safety factor. | - |
| `note` | str | yes | human summary of which cases (if any) are still moving. | - |
| `part` | str | yes | the STL that was re-analysed. | - |
| `pass` | bool | yes | every case's answer stopped moving with the mesh, and the fine-mesh result meets its safety factor. | - |
| `schema` | str | yes | `topopt/fea-convergence/1`. | - |
| `voxel_mm` | arr | yes | the three mesh sizes solved: [coarse, medium, fine]. | - |

## `manifest`

Everything needed to reproduce one run.

- **Written by:** the CLI wrapper (`cli/__init__.py`, writes `manifest.json` next to every command's output)
- **`schema` value:** `topopt/manifest/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `command` | obj | yes | {name, argv, line, cwd} of the command that ran. | - |
| `environment` | obj | yes | {python, python_full, platform, machine, packages, external_tools}. | cli/report.py::render |
| `inputs` | arr | yes | hashed input files named on the command line. | - |
| `random_seed` | any | yes | the declared seed, or null (with random_seed_note) when none was declared. | - |
| `random_seed_note` | str | no | present when random_seed is null: why (currently, always -- nothing seeds the voxel path yet). | - |
| `run` | obj | yes | {started_utc, finished_utc, seconds, exit_code, exit_code_meaning}. | cli/report.py::render |
| `schema` | str | yes | `topopt/manifest/1`. | - |
| `source` | obj | yes | {git_commit, git_dirty[, git_commit_note]}. | cli/report.py::render |
| `tool` | obj | yes | {name, version} of topopt itself. | cli/report.py::render |

## `opt`

The run summary for a study with a `geometry` block.

- **Written by:** topopt opt, generative-design (`geometry` block) schema (writes `<base>.json`)
- **`schema` value:** `geometry`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `checks` | arr | no | spec 19.2: the run's own verdict rows (connectivity, volume constraint, solver, final FEA). | cli/optimize.py::run_checks |
| `connectivity` | obj | yes | component count and dropped-island volume. | cli/report.py::_connected, tests/test_regression_osmo.py (connectivity.components/island_mm3) |
| `converged` | bool | no | spec 11.3: whether the density field actually converged (not merely ran out of iterations). | cli/report.py::_converged |
| `convergence_streak_required` | int | no | consecutive passing iterations required to call it converged. | - |
| `density_converged` | bool | no | density-change convergence sub-check. | - |
| `extracted_members` | obj | yes | beams/plates fitted to the density field, or {'error': ...} if extraction failed. | - |
| `final_fea` | any | yes | per-case FEA summaries on the final solid geometry (list), or null if the geometry was not connected. | cli/report.py::_reanalysed, _safety, tests/test_regression_osmo.py (final_fea[i].case/safety_factor_p99_5) |
| `final_smooth_stl_triangles` | any | yes | triangle count of `<base>_final_smooth.stl` (int), or null when `--quick` skipped the smooth STL. | - |
| `final_step` | any | yes | STEP export stats (dict), or null if the mask was empty. | - |
| `final_stl_triangles` | int | yes | triangle count of `<base>_final.stl`. | - |
| `history` | arr | yes | per-iteration compliance/volume/change trace. | - |
| `material` | any | yes | material card (dict), or a bare name (str) in runs recorded before Phase 2. | cli/report.py::_material_model |
| `objective_converged` | bool | no | objective-change convergence sub-check. | - |
| `outputs` | obj | yes | basenames of every file this run wrote. | - |
| `pass` | bool | no | every row of `checks` passed; this is the exit code, not a second opinion computed next to it. | cli/optimize.py::main |
| `schema` | str | yes | always the literal string 'geometry' (see module docstring) -- marks this as the generative-design `opt` summary, not a version tag. | - |
| `seconds` | num | yes | wall-clock time the optimisation took. | cli/report.py::render |
| `settings` | obj | yes | the resolved optimiser settings (build_axis, volfrac, optimizer, stress, ...). | cli/report.py::render, _build_axis |
| `setup` | obj | yes | the setup validation report (same shape as `topopt/setup/1`), embedded rather than re-read. | cli/report.py::_setup_ok, _build_axis |
| `shape_converged` | bool | no | shape-stability convergence sub-check. | - |
| `solver` | any | yes | which backend/preconditioner solved it (cpu\|cuda x jacobi\|mg). | - |
| `stress_constraint` | any | no | spec 11.6: whether design.stress held on the final density field, or null when no stress constraint was requested. | cli/report.py::_stress_constraint |
| `stress_converged` | any | no | stress-constraint convergence sub-check (bool), or null when there was no stress constraint to converge. | - |
| `study` | str | yes | path to the study file that was run. | - |
| `termination_reason` | str | no | why the optimiser stopped. | cli/report.py::_converged |
| `volume_converged` | bool | no | volume-constraint convergence sub-check. | - |
| `volumes` | obj | yes | final/preserve volumes, volume fractions, and the self-support (`unsupported`) report. | cli/report.py::_self_supporting, tests/test_regression_osmo.py (volumes.final_mm3) |

## `opt-legacy`

The run summary for a study without a `geometry` block.

- **Written by:** topopt opt, legacy (no `geometry` block) schema (writes `<base>.json`)
- **`schema` value:** `topopt/opt-legacy/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `blocky_stl_triangles` | int | yes | triangle count of the thresholded STL. | - |
| `domain_voxels` | int | yes | solid voxel count of the domain. | - |
| `extracted_members` | obj | yes | beams/plates fitted to the density field, or {'error': ...} if extraction failed. | - |
| `filter_mm` | num | yes | density filter radius, in mm. | - |
| `history` | arr | yes | per-iteration compliance/volume/change trace. | - |
| `kept_volume_mm3` | num | yes | volume of the thresholded (density >= 0.5) result. | - |
| `mass_saving_pct` | num | yes | percentage mass removed vs. the domain. | - |
| `material` | str | yes | material name (legacy schema has no material card). | - |
| `original_volume_mm3` | num | yes | volume of the un-optimised domain. | - |
| `overhang_filter` | bool | yes | whether the self-support filter was on. | - |
| `part` | str | yes | the domain STL that was optimised. | - |
| `ribs` | obj | yes | member/rib extraction report. | - |
| `smooth_stl_triangles` | int | yes | triangle count of the smoothed STL. | - |
| `symmetry` | arr | yes | axes the field was symmetrised across. | - |
| `volume_fraction_final` | num | yes | volume fraction actually reached. | - |
| `volume_fraction_target` | num | yes | requested volume fraction. | - |
| `voxel_mm` | num | yes | voxel edge length used for the grid. | - |

## `printability`

Printer fit, best orientation, min feature, where to cut.

- **Written by:** topopt print (writes `printability.json` via --json)
- **`schema` value:** `topopt/printability/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `bbox_as_modelled_mm` | arr | yes | bounding box in the orientation the STL was modelled in. | cli/run.py::_print_fit |
| `best_orientation` | obj | yes | orientations[0], repeated for convenience; `{}` when nothing fits. | cli/run.py::_print_fit |
| `build_volume_nominal_mm` | arr | yes | the machine's nominal build volume. | - |
| `build_volume_usable_mm` | arr | yes | build volume after the safety margin. | - |
| `checks` | arr | yes | build-volume / support / min-feature / load-vs-layers checks, each with pass/required/got. | - |
| `excluded_orientations` | arr | no | spec 19.5: orientations dropped from the candidates because they miss the envelope, each with the overshoot per axis in `note`. | - |
| `fits_as_modelled` | bool | yes | does it fit without reorienting. | cli/run.py::_print_fit |
| `fits_in_some_orientation` | bool | yes | does any axis-aligned orientation fit. | cli/run.py::_print_fit |
| `min_features` | obj | no | present unless --no-min-features: thinnest section vs. the printable wall minimum. | - |
| `orientations` | arr | yes | up to 8 candidate orientations, best first, with size/overhang/score. Only orientations that fit the machine are candidates. | - |
| `part` | str | yes | the STL analysed. | - |
| `pass` | bool | yes | whether every check passed. | - |
| `printer` | str | yes | machine name checked against. | - |
| `split` | obj | yes | how (if at all) the part would need to be split to fit the bed. | - |
| `triangles` | int | yes | triangle count of the input STL. | - |

## `printcheck`

Support-on-mating-face and enclosed-support checks on the chosen shape.

- **Written by:** topopt run (internal `printcheck.report()`, writes `printcheck.json`)
- **`schema` value:** `topopt/printcheck/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `build_axis` | str | yes | which axis is up (X/Y/Z). | cli/report.py::render |
| `enclosed_support` | obj | yes | is any support inside a sealed cavity -- pass/note/volume. | cli/report.py::_enclosed_support |
| `flat_overhang_area_mm2` | num | yes | solid cells with nothing directly beneath them, excluding the first layer. | cli/report.py::render |
| `note` | str | yes | caveat: measured on the voxel grid, so only 0/90 degree overhangs are seen. | cli/report.py::render |
| `support_on_critical_faces` | obj | yes | does support land on a preserve (mating) face -- pass/note/area. | cli/report.py::_support_on_faces |
| `support_volume_mm3` | num | yes | part-space volume under the overhangs, on the voxel grid. NOT the slicer's number: `slice.json`'s `support_mm3` is extruded 1.75 mm filament, a different quantity in different units. | cli/report.py::render |
| `threshold` | num | yes | the density threshold the checked shape was extracted at. | - |
| `voxel_mm` | num | yes | voxel size the occupancy grid was checked at. | - |

## `report`

One file that says whether the part is good, and why not.

- **Written by:** topopt report (writes `report.json`)
- **`schema` value:** `topopt/report/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `external_tools` | obj | yes | which external tools (FreeCAD/CalculiX/Gmsh/OpenSCAD/slicer) this machine has. | - |
| `fail` | arr | yes | stop conditions that failed: key/condition/detail. | - |
| `pass` | arr | yes | stop conditions that passed. | - |
| `schema` | str | yes | `topopt/report/1`. | - |
| `slicer` | obj | no | print time / filament / support from `slice.json`, or state UNKNOWN. Kept out of fail/unknown/pass on purpose: spec 19.5 -- a slicer that did not run must not move the structural verdict either way. Optional: recorded runs predate it. | - |
| `unknown` | arr | yes | stop conditions that were never checked in this run. | - |
| `verdict` | str | yes | FAIL / INCOMPLETE / PASS. | cli/run.py::main (exit code) |

## `run`

Setup -> opt -> extract -> ccx -> print -> report -> publish, in order.

- **Written by:** topopt run (writes `run_summary.json`)
- **`schema` value:** `topopt/run/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `model_dir` | any | yes | the --model directory, or null under plain --out. | - |
| `outdir` | str | yes | the working directory the analysis stages shared (03_optimization/ under --model). | - |
| `schema` | str | yes | `topopt/run/1`. | - |
| `stages` | arr | yes | one entry per pipeline stage: exit code, error, skipped, note. | cli/run.py::_valid_through |
| `stopped_at` | any | yes | the stage that stopped the pipeline, or null if nothing did. | - |
| `study` | str | yes | the study file the pipeline was run on. | - |
| `valid_through` | arr | yes | spec 19.6: which stages produced results a reader may rely on. | - |

## `setup`

The design-space validation report, before anything is solved.

- **Written by:** topopt setup (writes `<base>_setup.json`)
- **`schema` value:** `topopt/setup/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `bodies` | arr | yes | per-body diagnostics: which preserve/obstacle body is which, and whether it voxelised to nothing. | - |
| `errors` | arr | yes | blocking problems (preserve/obstacle overlap, loads off preserve, ...), each with a path and a message. | cli/report.py::_setup_ok, assumptions.py::collect |
| `info` | obj | yes | voxel size, volumes, build-volume fit, design-freedom report -- everything `assumptions.md` and `report.md` quote about the setup. | cli/report.py::render (build_volume, design_freedom), assumptions.py::collect, tests/make_baseline.py::setup_volumes |
| `ok` | bool | yes | whether the setup validation passed (no errors). | cli/report.py::_setup_ok, assumptions.py::collect |
| `warnings` | arr | yes | non-blocking findings worth knowing about. | cli/report.py::_setup_ok, assumptions.py::collect |

## `slice`

What a real slicer says: print time, filament, support and plate fit.

- **Written by:** topopt slice (writes `slice.json`)
- **`schema` value:** `topopt/slice/1`

| key | type | required | meaning | consumer |
|---|---|---|---|---|
| `bed_fits` | any | no | the slicer's own arrangement result for the orientation it sliced. | cli/report.py::_slicer_rows, slicer_summary |
| `bed_type` | str | no | requested slicer plate type, or `slicer default`. | - |
| `checks` | arr | yes | spec 13.3 support conditions plus the two the slicer can answer, each PASS/FAIL/UNKNOWN with its reason. All five 13.3 conditions are UNKNOWN: they ask where the support is and the slicer only says how much. | cli/report.py::_slicer_rows |
| `error` | any | yes | why the slicer produced nothing, or null. A slice that failed is recorded here and is never a structural verdict (spec 19.5). | cli/report.py::_slicer_rows, slicer_summary |
| `exit_code_slicer` | any | no | the slicer process's own exit status, or null when it never ran to completion. Not the command's exit code. | - |
| `filament_by_feature_mm` | obj | no | filament per `; FEATURE:` block; the evidence behind the support split. | - |
| `filament_g` | any | no | total filament mass. | cli/report.py::slicer_summary |
| `filament_mm` | any | no | total filament length. | - |
| `filament_mm3` | any | no | total filament volume, from length x wire cross-section (the G-code header's own `[cm^3]` figure is mislabelled mm3 and is not used). | cli/report.py::slicer_summary |
| `filament_mm3_source` | any | no | which of the two routes produced `filament_mm3`: wire geometry, or weight over the materials.py density. | - |
| `layer_mm` | num | yes | layer height the process profile prints at. | cli/report.py::_slicer_rows |
| `layers` | any | no | every layer the machine executes. | cli/report.py::slicer_summary |
| `layers_note` | any | no | why `layers` can exceed `object_layers`. | - |
| `material` | str | yes | materials.py material name. | - |
| `material_density_g_cm3` | any | no | the materials.py density for this material -- the toolkit's own value. | - |
| `max_z_mm` | any | no | height of the sliced object on the plate. | - |
| `model_print_time_text` | any | no | the slicer's model-only print time, before its own overheads. | - |
| `note` | str | yes | what `pass` does and does not mean. | - |
| `object_layers` | any | no | max_z / layer height. Lower than `layers` when the profile lets support print at its own layer heights. | cli/report.py::_slicer_rows |
| `orient` | str | yes | `keep` (sliced as modelled, the default) or `auto` (the slicer was allowed to turn it over, which changes which way the layers run). | cli/report.py::_slicer_rows |
| `part` | str | yes | the STL that was sliced. | - |
| `pass` | bool | yes | the slice ran and the object fits the plate. NOT a spec 13.3 verdict. | cli/report.py::slicer_summary |
| `plate_gcode` | any | yes | the plate G-code the slicer drops beside the 3mf -- the file the machine executes -- or null. | - |
| `presets` | obj | yes | the machine/process/filament profile names used and the flattened files written beside this one -- the CLI does not resolve `inherits`, so the files are the provenance, not the names alone. | cli/report.py::_slicer_rows |
| `print_time_s` | any | no | the slicer's own prediction, in seconds. | cli/report.py::slicer_summary |
| `print_time_text` | any | no | the same time as the slicer words it. | cli/report.py::_slicer_rows |
| `printer` | str | yes | printers.py key the slicer profile was chosen for. | - |
| `printer_name` | str | yes | the machine's human name. | - |
| `project_3mf` | any | yes | the sliced 3mf, or null when nothing was sliced. | - |
| `schema` | str | yes | `topopt/slice/1`. | - |
| `seconds` | num | yes | wall-clock time the slicer took. | - |
| `slicer_filament_density_g_cm3` | any | no | the density the slicer profile assumes. Recorded next to the card's because they differ (PLA: 1.24 vs 1.26). | - |
| `support_filament_g` | any | no | filament laid down as scaffolding, or null when it could not be checked. The slicer states no support total; it is summed over the G-code's `; FEATURE: Support*` blocks and only reported when that sum reproduces the slicer's own filament total. | cli/report.py::slicer_summary |
| `support_fraction` | any | no | support / total filament. | cli/report.py::_slicer_rows |
| `support_mm` | any | no | support filament as length of wire. | - |
| `support_mm3` | any | no | the same, as extruded 1.75 mm wire -- NOT the part-space volume printcheck.json reports. | cli/report.py::slicer_summary |
| `support_note` | any | no | how the support figure was obtained, or why there is none. | - |
| `support_type` | any | yes | the slicer's own support_type, or null when support is off. | cli/report.py::_slicer_rows |
| `support_used` | any | no | whether the slicer generated any support. | - |
| `supports` | str | yes | none / auto / tree, as asked for. | cli/report.py::_slicer_rows |
| `tool` | obj | yes | name / path / version of the slicer that produced these numbers. | cli/report.py::_slicer_rows |
| `warnings` | arr | yes | the slicer's own warning/error lines. | cli/report.py::_slicer_rows |

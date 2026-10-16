"""Output JSON schemas: what shape the files `topopt` writes are guaranteed to have.

`schema.py` validates study *input* -- it has never had a counterpart for the
other direction. Every CLI writes a JSON summary next to its other outputs, and
nothing has ever checked that those files are shaped the way the readers of
them (`cli/report.py`, `cli/run.py`, `assumptions.py`, the regression tests)
assume. A key silently renamed or dropped only shows up when a downstream
reader crashes or -- worse -- silently reads `None` and reports "not checked"
for something that in fact ran.

The tables below follow `schema.py`'s convention (type names "str" "num" "int"
"bool" "obj" "arr" "any") with one addition: `"required": True` on a key a
named consumer depends on. "Depends on" is read narrowly, the same way
`schema.py` reads it for study input: a key a writer includes **every time it
writes that kind of file** (not only on some code paths) is required; a key
that is only sometimes present (a flag was passed, a later phase added it, an
old recorded run predates it) is optional even if a consumer reads it, because
`.get()` and "not checked" are exactly how this project already handles an
absent finding (AGENTS.md: "not checked is never rendered as fine", the same
rule the tool applies to its own output must apply to reading it back).

Every kind carries a `schema` string, `"topopt/<kind>/<version>"`, so a reader
can identify a file without guessing from its keys. One exception: the
generative-design `opt` summary already used `"schema": "geometry"` (added
before this module existed, to distinguish the two `topopt opt` output shapes)
and that value is a "what kind of study built this" tag, not a version string
in this module's format. It is left alone -- AGENTS.md is explicit that
existing keys and values do not change -- and registered here under its actual
value so it can still be validated.

`validate_output(data)` dispatches on `data["schema"]`, and reports an unknown
top-level key as a warning and a missing-required or wrong-typed key as an
error, in that same asymmetric spirit: study files accumulate legacy keys and
so do result files, and a validator that rejects a file the tools would have
read correctly is worse than no validator.
"""

import json

from topopt.schema import Problem

# schema-string -> (kind name, field table, written-by, one-line what-it-is)
_REGISTRY = {}


def _type_ok(value, want):
    """Same primitive vocabulary as `schema.py`'s study validator."""
    if want == "any":
        return True
    if want == "str":
        return isinstance(value, str)
    if want == "bool":
        return isinstance(value, bool)
    if want == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "obj":
        return isinstance(value, dict)
    if want == "arr":
        return isinstance(value, list)
    return True


def _register(schema_value, kind, table, written_by, what):
    _REGISTRY[schema_value] = (kind, table, written_by, what)
    return table


# --------------------------------------------------------------- the tables
# Each field: {"type": ..., "required": bool, "doc": "one-line meaning",
#              "consumer": "who reads it, or '-'"}. `required` defaults to
# False when absent, exactly like `schema.py`'s rules default an unlisted
# constraint to "not checked".

SETUP = _register("topopt/setup/1", "setup", {
    "ok": {"type": "bool", "required": True,
           "doc": "whether the setup validation passed (no errors).",
           "consumer": "cli/report.py::_setup_ok, assumptions.py::collect"},
    "errors": {"type": "arr", "required": True,
               "doc": "blocking problems (preserve/obstacle overlap, loads "
                      "off preserve, ...), each with a path and a message.",
               "consumer": "cli/report.py::_setup_ok, assumptions.py::collect"},
    "warnings": {"type": "arr", "required": True,
                 "doc": "non-blocking findings worth knowing about.",
                 "consumer": "cli/report.py::_setup_ok, assumptions.py::collect"},
    "info": {"type": "obj", "required": True,
             "doc": "voxel size, volumes, build-volume fit, design-freedom "
                    "report -- everything `assumptions.md` and `report.md` "
                    "quote about the setup.",
             "consumer": "cli/report.py::render (build_volume, "
                         "design_freedom), assumptions.py::collect, "
                         "tests/make_baseline.py::setup_volumes"},
    "bodies": {"type": "arr", "required": True,
               "doc": "per-body diagnostics: which preserve/obstacle body is "
                      "which, and whether it voxelised to nothing.",
               "consumer": "-"},
}, "topopt setup (writes `<base>_setup.json`)",
   "the design-space validation report, before anything is solved")


OPT_LEGACY = _register("topopt/opt-legacy/1", "opt-legacy", {
    "part": {"type": "str", "required": True,
             "doc": "the domain STL that was optimised.", "consumer": "-"},
    "material": {"type": "str", "required": True,
                 "doc": "material name (legacy schema has no material card).",
                 "consumer": "-"},
    "voxel_mm": {"type": "num", "required": True,
                 "doc": "voxel edge length used for the grid.",
                 "consumer": "-"},
    "domain_voxels": {"type": "int", "required": True,
                      "doc": "solid voxel count of the domain.",
                      "consumer": "-"},
    "volume_fraction_target": {"type": "num", "required": True,
                               "doc": "requested volume fraction.",
                               "consumer": "-"},
    "volume_fraction_final": {"type": "num", "required": True,
                              "doc": "volume fraction actually reached.",
                              "consumer": "-"},
    "kept_volume_mm3": {"type": "num", "required": True,
                        "doc": "volume of the thresholded (density >= 0.5) "
                               "result.", "consumer": "-"},
    "original_volume_mm3": {"type": "num", "required": True,
                            "doc": "volume of the un-optimised domain.",
                            "consumer": "-"},
    "mass_saving_pct": {"type": "num", "required": True,
                        "doc": "percentage mass removed vs. the domain.",
                        "consumer": "-"},
    "filter_mm": {"type": "num", "required": True,
                 "doc": "density filter radius, in mm.", "consumer": "-"},
    "symmetry": {"type": "arr", "required": True,
                "doc": "axes the field was symmetrised across.",
                "consumer": "-"},
    "overhang_filter": {"type": "bool", "required": True,
                        "doc": "whether the self-support filter was on.",
                        "consumer": "-"},
    "history": {"type": "arr", "required": True,
               "doc": "per-iteration compliance/volume/change trace.",
               "consumer": "-"},
    "blocky_stl_triangles": {"type": "int", "required": True,
                             "doc": "triangle count of the thresholded STL.",
                             "consumer": "-"},
    "smooth_stl_triangles": {"type": "int", "required": True,
                             "doc": "triangle count of the smoothed STL.",
                             "consumer": "-"},
    "ribs": {"type": "obj", "required": True,
            "doc": "member/rib extraction report.", "consumer": "-"},
    "extracted_members": {"type": "obj", "required": True,
                          "doc": "beams/plates fitted to the density field, "
                                 "or {'error': ...} if extraction failed.",
                          "consumer": "-"},
}, "topopt opt, legacy (no `geometry` block) schema (writes `<base>.json`)",
   "the run summary for a study without a `geometry` block")


# The generative-design `opt` summary is registered under its actual `schema`
# value, `"geometry"` -- see the module docstring for why that string is not
# renamed to the `topopt/opt/1` form every other kind uses.
OPT = _register("geometry", "opt", {
    "study": {"type": "str", "required": True,
              "doc": "path to the study file that was run.", "consumer": "-"},
    "schema": {"type": "str", "required": True,
              "doc": "always the literal string 'geometry' (see module "
                     "docstring) -- marks this as the generative-design "
                     "`opt` summary, not a version tag.", "consumer": "-"},
    "material": {"type": "any", "required": True,
                "doc": "material card (dict), or a bare name (str) in runs "
                       "recorded before Phase 2.",
                "consumer": "cli/report.py::_material_model"},
    "setup": {"type": "obj", "required": True,
             "doc": "the setup validation report (same shape as "
                    "`topopt/setup/1`), embedded rather than re-read.",
             "consumer": "cli/report.py::_setup_ok, _build_axis"},
    "settings": {"type": "obj", "required": True,
                "doc": "the resolved optimiser settings (build_axis, "
                       "volfrac, optimizer, stress, ...).",
                "consumer": "cli/report.py::render, _build_axis"},
    "solver": {"type": "any", "required": True,
              "doc": "which backend/preconditioner solved it "
                     "(cpu|cuda x jacobi|mg).", "consumer": "-"},
    "volumes": {"type": "obj", "required": True,
               "doc": "final/preserve volumes, volume fractions, and the "
                      "self-support (`unsupported`) report.",
               "consumer": "cli/report.py::_self_supporting, "
                           "tests/test_regression_osmo.py "
                           "(volumes.final_mm3)"},
    "connectivity": {"type": "obj", "required": True,
                     "doc": "component count and dropped-island volume.",
                     "consumer": "cli/report.py::_connected, "
                                 "tests/test_regression_osmo.py "
                                 "(connectivity.components/island_mm3)"},
    "seconds": {"type": "num", "required": True,
               "doc": "wall-clock time the optimisation took.",
               "consumer": "cli/report.py::render"},
    "history": {"type": "arr", "required": True,
               "doc": "per-iteration compliance/volume/change trace.",
               "consumer": "-"},
    "final_stl_triangles": {"type": "int", "required": True,
                            "doc": "triangle count of `<base>_final.stl`.",
                            "consumer": "-"},
    "final_smooth_stl_triangles": {"type": "any", "required": True,
                                   "doc": "triangle count of "
                                          "`<base>_final_smooth.stl` (int), "
                                          "or null when `--quick` skipped "
                                          "the smooth STL.",
                                   "consumer": "-"},
    "final_step": {"type": "any", "required": True,
                  "doc": "STEP export stats (dict), or null if the mask was "
                         "empty.", "consumer": "-"},
    "final_fea": {"type": "any", "required": True,
                 "doc": "per-case FEA summaries on the final solid geometry "
                        "(list), or null if the geometry was not connected.",
                 "consumer": "cli/report.py::_reanalysed, _safety, "
                             "tests/test_regression_osmo.py "
                             "(final_fea[i].case/safety_factor_p99_5)"},
    "extracted_members": {"type": "obj", "required": True,
                          "doc": "beams/plates fitted to the density field, "
                                 "or {'error': ...} if extraction failed.",
                          "consumer": "-"},
    "outputs": {"type": "obj", "required": True,
               "doc": "basenames of every file this run wrote.",
               "consumer": "-"},
    # Added after the fields above, in different commits -- see the module
    # docstring. Real recorded runs predate some of these, so none of them are
    # required: their absence means "recorded before this field existed", not
    # "the run is broken".
    "converged": {"type": "bool", "required": False,
                 "doc": "spec 11.3: whether the density field actually "
                        "converged (not merely ran out of iterations).",
                 "consumer": "cli/report.py::_converged"},
    "termination_reason": {"type": "str", "required": False,
                           "doc": "why the optimiser stopped.",
                           "consumer": "cli/report.py::_converged"},
    "density_converged": {"type": "bool", "required": False,
                          "doc": "density-change convergence sub-check.",
                          "consumer": "-"},
    "objective_converged": {"type": "bool", "required": False,
                            "doc": "objective-change convergence sub-check.",
                            "consumer": "-"},
    "volume_converged": {"type": "bool", "required": False,
                         "doc": "volume-constraint convergence sub-check.",
                         "consumer": "-"},
    "shape_converged": {"type": "bool", "required": False,
                        "doc": "shape-stability convergence sub-check.",
                        "consumer": "-"},
    "stress_converged": {"type": "any", "required": False,
                         "doc": "stress-constraint convergence sub-check "
                                "(bool), or null when there was no stress "
                                "constraint to converge.",
                         "consumer": "-"},
    "convergence_streak_required": {"type": "int", "required": False,
                                    "doc": "consecutive passing iterations "
                                           "required to call it converged.",
                                    "consumer": "-"},
    "stress_constraint": {"type": "any", "required": False,
                          "doc": "spec 11.6: whether design.stress held on "
                                 "the final density field, or null when no "
                                 "stress constraint was requested.",
                          "consumer": "cli/report.py::_stress_constraint"},
    "checks": {"type": "arr", "required": False,
               "doc": "spec 19.2: the run's own verdict rows (connectivity, "
                      "volume constraint, solver, final FEA).",
               "consumer": "cli/optimize.py::run_checks"},
    "pass": {"type": "bool", "required": False,
             "doc": "every row of `checks` passed; this is the exit code, "
                    "not a second opinion computed next to it.",
             "consumer": "cli/optimize.py::main"},
}, "topopt opt, generative-design (`geometry` block) schema "
   "(writes `<base>.json`)",
   "the run summary for a study with a `geometry` block")


EXTRACT = _register("topopt/extract/1", "extract", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/extract/1`.",
              "consumer": "-"},
    "source_dir": {"type": "str", "required": True,
                   "doc": "the `topopt opt` output directory this was built "
                          "from.", "consumer": "-"},
    "study": {"type": "str", "required": True,
             "doc": "the study file.", "consumer": "-"},
    "voxel_mm": {"type": "num", "required": True,
                "doc": "the grid the density field was solved on -- must "
                       "match the rebuilt setup or the candidates are not "
                       "comparable to the run they came from.",
                "consumer": "-"},
    "grid_origin_mm": {"type": "arr", "required": True,
                       "doc": "the grid's origin in study coordinates.",
                       "consumer": "-"},
    "required_safety_factor": {"type": "num", "required": True,
                               "doc": "the safety factor every candidate is "
                                      "judged against.",
                               "consumer": "cli/report.py::collect "
                                          "(required_safety_factor)"},
    "material_model": {"type": "str", "required": True,
                       "doc": "isotropic_two_constant or "
                              "orthotropic_equivalent_solid.",
                       "consumer": "-"},
    "levels": {"type": "arr", "required": True,
              "doc": "the thresholds evaluated.", "consumer": "-"},
    "candidates": {"type": "arr", "required": True,
                   "doc": "one entry per threshold: volumes, thickness, "
                          "connectivity, FEA.",
                   "consumer": "cli/report.py::render (ex['candidates'])"},
    "chosen_level": {"type": "num", "required": True,
                     "doc": "the threshold picked (by --pick, or by "
                            "`_choose`).",
                     "consumer": "cli/report.py::collect, "
                                "cli/run.py::_chosen_stl"},
    "chosen_reason": {"type": "str", "required": True,
                      "doc": "why this threshold, in the words of the "
                             "checks that decided.", "consumer": "-"},
    "outputs": {"type": "obj", "required": True,
               "doc": "paths written for the chosen threshold (stl, "
                      "smooth_stl, step, finish files).",
               "consumer": "cli/run.py::_chosen_stl (outputs.stl)"},
    "finish": {"type": "obj", "required": False,
              "doc": "present only when the study declares a `finish` "
                     "block: the CAD-dimension rebuild report.",
              "consumer": "-"},
    "shell": {"type": "obj", "required": False,
             "doc": "spec 19.3: one shell report per written STL, keyed by "
                    "the same tags as `outputs` (open edges, bodies, "
                    "self-intersections).",
             "consumer": "cli/report.py::collect, _watertight"},
}, "topopt extract (writes `<base>_extract.json`)",
   "density field -> shape candidates at several thresholds")


FEA = _register("topopt/fea/1", "fea", {
    "part": {"type": "str", "required": True,
            "doc": "the STL that was re-analysed.", "consumer": "-"},
    "material": {"type": "str", "required": True,
                "doc": "material name.", "consumer": "-"},
    "pass": {"type": "bool", "required": True,
            "doc": "whether every case passed its checks.",
            "consumer": "cli/run.py::_voxel_fea (presence), "
                        "cli/ccx.py::_compare_voxel_fea"},
    "cases": {"type": "arr", "required": True,
             "doc": "one entry per load case: displacement, von Mises, "
                    "safety factor, checks, hotspots (see `report.summarise`).",
             "consumer": "cli/report.py::collect (data['fea']), "
                         "cli/ccx.py::_compare_voxel_fea, "
                         "tests/test_regression_osmo.py "
                         "(cases[i].voxel_mm/case/safety_factor_p99_5)"},
}, "topopt fea (writes `<base>_fea.json`)",
   "voxel FEA on an STL: deflection, stress, safety factor per case")


FEA_CONVERGENCE = _register("topopt/fea-convergence/1", "fea-convergence", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/fea-convergence/1`.",
              "consumer": "-"},
    "part": {"type": "str", "required": True,
            "doc": "the STL that was re-analysed.", "consumer": "-"},
    "voxel_mm": {"type": "arr", "required": True,
                "doc": "the three mesh sizes solved: [coarse, medium, fine].",
                "consumer": "-"},
    "cases": {"type": "obj", "required": True,
             "doc": "per-case name -> {runs: [...], verdict: {...}}.",
             "consumer": "-"},
    "governing_case": {"type": "str", "required": True,
                       "doc": "the case with the lowest fine-mesh safety "
                              "factor.", "consumer": "-"},
    "pass": {"type": "bool", "required": True,
            "doc": "every case's answer stopped moving with the mesh, and "
                   "the fine-mesh result meets its safety factor.",
            "consumer": "-"},
    "note": {"type": "str", "required": True,
            "doc": "human summary of which cases (if any) are still moving.",
            "consumer": "-"},
}, "topopt fea --converge (writes `<base>_fea_convergence.json`)",
   "[2H, H, H/2] mesh convergence, judged per load case")


CCX = _register("topopt/ccx/1", "ccx", {
    "stl_sha256": {"type": "str", "required": False,
                   "doc": "SHA-256 of the input STL, used to identify the finished shape.", "consumer": "cli/report.py"},
    "study_sha256": {"type": "str", "required": False,
                     "doc": "SHA-256 of the study used for this analysis.", "consumer": "cli/report.py"},
    "schema": {"type": "str", "required": True, "doc": "`topopt/ccx/1`.",
              "consumer": "-"},
    "stl": {"type": "str", "required": True,
           "doc": "the STL that was meshed and re-analysed.",
           "consumer": "-"},
    "study": {"type": "str", "required": True,
             "doc": "the study the loads/supports/material came from.",
             "consumer": "-"},
    "map_mode": {"type": "str", "required": True,
                "doc": "how boundary conditions were mapped onto the tet "
                       "mesh: volume (default) or surface.",
                "consumer": "-"},
    "runs": {"type": "arr", "required": True,
            "doc": "one entry per mesh size solved (just one unless "
                   "--converge): nodes, per-case results, mass properties, "
                   "mesh quality.",
            "consumer": "cli/report.py::render (cc['runs']), _equilibrium"},
    "mesh_convergence": {"type": "obj", "required": False,
                         "doc": "present only with --converge: pass/note "
                                "across the mesh sizes solved.",
                         "consumer": "cli/report.py::_mesh_converged"},
    "vs_voxel_fea": {"type": "obj", "required": False,
                     "doc": "present only with --voxel-disp: the finest "
                            "run's max displacement vs. a single voxel-FEA "
                            "number.", "consumer": "cli/report.py::render"},
    "vs_voxel_fea_cases": {"type": "obj", "required": False,
                           "doc": "present only with --voxel-fea PATH: "
                                  "case-by-case comparison against a "
                                  "`topopt fea` run (spec 14.4).",
                           "consumer": "-"},
}, "topopt ccx (writes `ccx_summary.json`)",
   "independent re-analysis with CalculiX on a tet mesh")


PRINTCHECK = _register("topopt/printcheck/1", "printcheck", {
    "build_axis": {"type": "str", "required": True,
                  "doc": "which axis is up (X/Y/Z).",
                  "consumer": "cli/report.py::render"},
    "voxel_mm": {"type": "num", "required": True,
                "doc": "voxel size the occupancy grid was checked at.",
                "consumer": "-"},
    "flat_overhang_area_mm2": {"type": "num", "required": True,
                              "doc": "solid cells with nothing directly "
                                     "beneath them, excluding the first "
                                     "layer.", "consumer": "cli/report.py::render"},
    "support_volume_mm3": {"type": "num", "required": True,
                           "doc": "part-space volume under the overhangs, on "
                                  "the voxel grid. NOT the slicer's number: "
                                  "`slice.json`'s `support_mm3` is extruded "
                                  "1.75 mm filament, a different quantity in "
                                  "different units.",
                           "consumer": "cli/report.py::render"},
    "support_on_critical_faces": {"type": "obj", "required": True,
                                  "doc": "does support land on a preserve "
                                         "(mating) face -- pass/note/area.",
                                  "consumer": "cli/report.py::_support_on_faces"},
    "enclosed_support": {"type": "obj", "required": True,
                         "doc": "is any support inside a sealed cavity -- "
                                "pass/note/volume.",
                         "consumer": "cli/report.py::_enclosed_support"},
    "note": {"type": "str", "required": True,
            "doc": "caveat: measured on the voxel grid, so only 0/90 degree "
                   "overhangs are seen.", "consumer": "cli/report.py::render"},
    "threshold": {"type": "num", "required": True,
                 "doc": "the density threshold the checked shape was "
                        "extracted at.", "consumer": "-"},
}, "topopt run (internal `printcheck.report()`, writes `printcheck.json`)",
   "support-on-mating-face and enclosed-support checks on the chosen shape")


PRINTABILITY = _register("topopt/printability/1", "printability", {
    "part": {"type": "str", "required": True,
            "doc": "the STL analysed.", "consumer": "-"},
    "printer": {"type": "str", "required": True,
               "doc": "machine name checked against.", "consumer": "-"},
    "build_volume_nominal_mm": {"type": "arr", "required": True,
                                "doc": "the machine's nominal build volume.",
                                "consumer": "-"},
    "build_volume_usable_mm": {"type": "arr", "required": True,
                              "doc": "build volume after the safety margin.",
                              "consumer": "-"},
    "triangles": {"type": "int", "required": True,
                 "doc": "triangle count of the input STL.", "consumer": "-"},
    "bbox_as_modelled_mm": {"type": "arr", "required": True,
                           "doc": "bounding box in the orientation the STL "
                                  "was modelled in.",
                           "consumer": "cli/run.py::_print_fit"},
    "fits_as_modelled": {"type": "bool", "required": True,
                         "doc": "does it fit without reorienting.",
                         "consumer": "cli/run.py::_print_fit"},
    "fits_in_some_orientation": {"type": "bool", "required": True,
                                 "doc": "does any axis-aligned orientation "
                                        "fit.", "consumer": "cli/run.py::_print_fit"},
    "orientations": {"type": "arr", "required": True,
                     "doc": "up to 8 candidate orientations, best first, "
                            "with size/overhang/score. Only orientations "
                            "that fit the machine are candidates.",
                     "consumer": "-"},
    "excluded_orientations": {"type": "arr", "required": False,
                              "doc": "spec 19.5: orientations dropped from "
                                     "the candidates because they miss the "
                                     "envelope, each with the overshoot per "
                                     "axis in `note`.", "consumer": "-"},
    "best_orientation": {"type": "obj", "required": True,
                         "doc": "orientations[0], repeated for convenience; "
                                "`{}` when nothing fits.",
                         "consumer": "cli/run.py::_print_fit"},
    "split": {"type": "obj", "required": True,
             "doc": "how (if at all) the part would need to be split to "
                    "fit the bed.", "consumer": "-"},
    "min_features": {"type": "obj", "required": False,
                     "doc": "present unless --no-min-features: thinnest "
                            "section vs. the printable wall minimum.",
                     "consumer": "-"},
    "checks": {"type": "arr", "required": True,
              "doc": "build-volume / support / min-feature / load-vs-layers "
                     "checks, each with pass/required/got.", "consumer": "-"},
    "pass": {"type": "bool", "required": True,
            "doc": "whether every check passed.", "consumer": "-"},
}, "topopt print (writes `printability.json` via --json)",
   "printer fit, best orientation, min feature, where to cut")


REPORT = _register("topopt/report/1", "report", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/report/1`.",
              "consumer": "-"},
    "verdict": {"type": "str", "required": True,
               "doc": "FAIL / INCOMPLETE / PASS.",
               "consumer": "cli/run.py::main (exit code)"},
    "fail": {"type": "arr", "required": True,
            "doc": "stop conditions that failed: key/condition/detail.",
            "consumer": "-"},
    "unknown": {"type": "arr", "required": True,
               "doc": "stop conditions that were never checked in this run.",
               "consumer": "-"},
    "pass": {"type": "arr", "required": True,
            "doc": "stop conditions that passed.", "consumer": "-"},
    "external_tools": {"type": "obj", "required": True,
                       "doc": "which external tools (FreeCAD/CalculiX/Gmsh/"
                              "OpenSCAD/slicer) this machine has.",
                       "consumer": "-"},
    "slicer": {"type": "obj",
              "doc": "print time / filament / support from `slice.json`, or "
                     "state UNKNOWN. Kept out of fail/unknown/pass on "
                     "purpose: spec 19.5 -- a slicer that did not run must "
                     "not move the structural verdict either way. Optional: "
                     "recorded runs predate it.",
              "consumer": "-"},
}, "topopt report (writes `report.json`)",
   "one file that says whether the part is good, and why not")


SLICE = _register("topopt/slice/1", "slice", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/slice/1`.",
              "consumer": "-"},
    "part": {"type": "str", "required": True, "doc": "the STL that was sliced.",
            "consumer": "-"},
    "tool": {"type": "obj", "required": True,
            "doc": "name / path / version of the slicer that produced these "
                   "numbers.", "consumer": "cli/report.py::_slicer_rows"},
    "printer": {"type": "str", "required": True,
               "doc": "printers.py key the slicer profile was chosen for.",
               "consumer": "-"},
    "printer_name": {"type": "str", "required": True,
                    "doc": "the machine's human name.", "consumer": "-"},
    "material": {"type": "str", "required": True,
                "doc": "materials.py material name.", "consumer": "-"},
    "bed_type": {"type": "str", "required": False,
                 "doc": "requested slicer plate type, or `slicer default`.",
                 "consumer": "-"},
    "layer_mm": {"type": "num", "required": True,
                "doc": "layer height the process profile prints at.",
                "consumer": "cli/report.py::_slicer_rows"},
    "supports": {"type": "str", "required": True,
                "doc": "none / auto / tree, as asked for.",
                "consumer": "cli/report.py::_slicer_rows"},
    "support_type": {"type": "any", "required": True,
                    "doc": "the slicer's own support_type, or null when "
                           "support is off.",
                    "consumer": "cli/report.py::_slicer_rows"},
    "orient": {"type": "str", "required": True,
              "doc": "`keep` (sliced as modelled, the default) or `auto` "
                     "(the slicer was allowed to turn it over, which changes "
                     "which way the layers run).",
              "consumer": "cli/report.py::_slicer_rows"},
    "presets": {"type": "obj", "required": True,
               "doc": "the machine/process/filament profile names used and "
                      "the flattened files written beside this one -- the "
                      "CLI does not resolve `inherits`, so the files are the "
                      "provenance, not the names alone.",
               "consumer": "cli/report.py::_slicer_rows"},
    "project_3mf": {"type": "any", "required": True,
                   "doc": "the sliced 3mf, or null when nothing was sliced.",
                   "consumer": "-"},
    "plate_gcode": {"type": "any", "required": True,
                   "doc": "the plate G-code the slicer drops beside the 3mf "
                          "-- the file the machine executes -- or null.",
                   "consumer": "-"},
    "error": {"type": "any", "required": True,
             "doc": "why the slicer produced nothing, or null. A slice that "
                    "failed is recorded here and is never a structural "
                    "verdict (spec 19.5).",
             "consumer": "cli/report.py::_slicer_rows, slicer_summary"},
    "warnings": {"type": "arr", "required": True,
                "doc": "the slicer's own warning/error lines.",
                "consumer": "cli/report.py::_slicer_rows"},
    "checks": {"type": "arr", "required": True,
              "doc": "spec 13.3 support conditions plus the two the slicer "
                     "can answer, each PASS/FAIL/UNKNOWN with its reason. "
                     "All five 13.3 conditions are UNKNOWN: they ask where "
                     "the support is and the slicer only says how much.",
              "consumer": "cli/report.py::_slicer_rows"},
    "pass": {"type": "bool", "required": True,
            "doc": "the slice ran and the object fits the plate. NOT a spec "
                   "13.3 verdict.", "consumer": "cli/report.py::slicer_summary"},
    "note": {"type": "str", "required": True,
            "doc": "what `pass` does and does not mean.", "consumer": "-"},
    "seconds": {"type": "num", "required": True,
               "doc": "wall-clock time the slicer took.", "consumer": "-"},
    "print_time_s": {"type": "any",
                    "doc": "the slicer's own prediction, in seconds.",
                    "consumer": "cli/report.py::slicer_summary"},
    "print_time_text": {"type": "any",
                       "doc": "the same time as the slicer words it.",
                       "consumer": "cli/report.py::_slicer_rows"},
    "filament_g": {"type": "any", "doc": "total filament mass.",
                  "consumer": "cli/report.py::slicer_summary"},
    "filament_mm": {"type": "any", "doc": "total filament length.",
                   "consumer": "-"},
    "filament_mm3": {"type": "any",
                    "doc": "total filament volume, from length x wire "
                           "cross-section (the G-code header's own `[cm^3]` "
                           "figure is mislabelled mm3 and is not used).",
                    "consumer": "cli/report.py::slicer_summary"},
    "support_filament_g": {"type": "any",
                          "doc": "filament laid down as scaffolding, or null "
                                 "when it could not be checked. The slicer "
                                 "states no support total; it is summed over "
                                 "the G-code's `; FEATURE: Support*` blocks "
                                 "and only reported when that sum reproduces "
                                 "the slicer's own filament total.",
                          "consumer": "cli/report.py::slicer_summary"},
    "support_mm3": {"type": "any",
                   "doc": "the same, as extruded 1.75 mm wire -- NOT the "
                          "part-space volume printcheck.json reports.",
                   "consumer": "cli/report.py::slicer_summary"},
    "support_fraction": {"type": "any",
                        "doc": "support / total filament.",
                        "consumer": "cli/report.py::_slicer_rows"},
    "support_note": {"type": "any",
                    "doc": "how the support figure was obtained, or why there "
                           "is none.", "consumer": "-"},
    "filament_by_feature_mm": {"type": "obj",
                              "doc": "filament per `; FEATURE:` block; the "
                                     "evidence behind the support split.",
                              "consumer": "-"},
    "layers": {"type": "any",
              "doc": "every layer the machine executes.",
              "consumer": "cli/report.py::slicer_summary"},
    "object_layers": {"type": "any",
                     "doc": "max_z / layer height. Lower than `layers` when "
                            "the profile lets support print at its own layer "
                            "heights.", "consumer": "cli/report.py::_slicer_rows"},
    "bed_fits": {"type": "any",
                "doc": "the slicer's own arrangement result for the "
                       "orientation it sliced.",
                "consumer": "cli/report.py::_slicer_rows, slicer_summary"},
    "support_used": {"type": "any",
                    "doc": "whether the slicer generated any support.",
                    "consumer": "-"},
    "support_mm": {"type": "any",
                  "doc": "support filament as length of wire.",
                  "consumer": "-"},
    "max_z_mm": {"type": "any",
                "doc": "height of the sliced object on the plate.",
                "consumer": "-"},
    "exit_code_slicer": {"type": "any",
                        "doc": "the slicer process's own exit status, or null "
                               "when it never ran to completion. Not the "
                               "command's exit code.", "consumer": "-"},
    "model_print_time_text": {"type": "any",
                             "doc": "the slicer's model-only print time, "
                                    "before its own overheads.",
                             "consumer": "-"},
    "layers_note": {"type": "any",
                   "doc": "why `layers` can exceed `object_layers`.",
                   "consumer": "-"},
    "filament_mm3_source": {"type": "any",
                           "doc": "which of the two routes produced "
                                  "`filament_mm3`: wire geometry, or weight "
                                  "over the materials.py density.",
                           "consumer": "-"},
    "material_density_g_cm3": {"type": "any",
                              "doc": "the materials.py density for this "
                                     "material -- the toolkit's own value.",
                              "consumer": "-"},
    "slicer_filament_density_g_cm3": {"type": "any",
                                     "doc": "the density the slicer profile "
                                            "assumes. Recorded next to the "
                                            "card's because they differ "
                                            "(PLA: 1.24 vs 1.26).",
                                     "consumer": "-"},
}, "topopt slice (writes `slice.json`)",
   "what a real slicer says: print time, filament, support and plate fit")


RUN = _register("topopt/run/1", "run", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/run/1`.",
              "consumer": "-"},
    "study": {"type": "str", "required": True,
             "doc": "the study file the pipeline was run on.",
             "consumer": "-"},
    "outdir": {"type": "str", "required": True,
              "doc": "the working directory the analysis stages shared "
                     "(03_optimization/ under --model).", "consumer": "-"},
    "model_dir": {"type": "any", "required": True,
                 "doc": "the --model directory, or null under plain --out.",
                 "consumer": "-"},
    "stages": {"type": "arr", "required": True,
              "doc": "one entry per pipeline stage: exit code, error, "
                     "skipped, note.",
              "consumer": "cli/run.py::_valid_through"},
    "stopped_at": {"type": "any", "required": True,
                  "doc": "the stage that stopped the pipeline, or null if "
                         "nothing did.", "consumer": "-"},
    "valid_through": {"type": "arr", "required": True,
                      "doc": "spec 19.6: which stages produced results a "
                             "reader may rely on.", "consumer": "-"},
}, "topopt run (writes `run_summary.json`)",
   "setup -> opt -> extract -> ccx -> print -> report -> publish, in order")


MANIFEST = _register("topopt/manifest/1", "manifest", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/manifest/1`.",
              "consumer": "-"},
    "tool": {"type": "obj", "required": True,
            "doc": "{name, version} of topopt itself.",
            "consumer": "cli/report.py::render"},
    "command": {"type": "obj", "required": True,
               "doc": "{name, argv, line, cwd} of the command that ran.",
               "consumer": "-"},
    "run": {"type": "obj", "required": True,
           "doc": "{started_utc, finished_utc, seconds, exit_code, "
                  "exit_code_meaning}.", "consumer": "cli/report.py::render"},
    "source": {"type": "obj", "required": True,
              "doc": "{git_commit, git_dirty[, git_commit_note]}.",
              "consumer": "cli/report.py::render"},
    "environment": {"type": "obj", "required": True,
                   "doc": "{python, python_full, platform, machine, "
                          "packages, external_tools}.",
                   "consumer": "cli/report.py::render"},
    "inputs": {"type": "arr", "required": True,
              "doc": "hashed input files named on the command line.",
              "consumer": "-"},
    "random_seed": {"type": "any", "required": True,
                    "doc": "the declared seed, or null (with "
                           "random_seed_note) when none was declared.",
                    "consumer": "-"},
    "random_seed_note": {"type": "str", "required": False,
                         "doc": "present when random_seed is null: why "
                                "(currently, always -- nothing seeds the "
                                "voxel path yet).", "consumer": "-"},
}, "the CLI wrapper (`cli/__init__.py`, writes `manifest.json` next to every "
   "command's output)",
   "everything needed to reproduce one run")


ASSUMPTIONS = _register("topopt/assumptions/1", "assumptions", {
    "schema": {"type": "str", "required": True, "doc": "`topopt/assumptions/1`.",
              "consumer": "-"},
    "part": {"type": "any", "required": True,
            "doc": "the part name (falls back to the study filename).",
            "consumer": "-"},
    "study_file": {"type": "any", "required": True,
                  "doc": "basename of the study file.", "consumer": "-"},
    "what": {"type": "any", "required": True,
            "doc": "the study's `_what`/`_purpose` comment, or null.",
            "consumer": "-"},
    "why": {"type": "any", "required": True,
           "doc": "the study's `_why`/`_rationale` comment, or null.",
           "consumer": "-"},
    "caveat": {"type": "any", "required": True,
              "doc": "the study's `_not_a_part`/`_caveat` comment, or null.",
              "consumer": "-"},
    "units": {"type": "str", "required": True,
             "doc": "always 'mm / N / MPa (...)' -- the only system this "
                    "tool implements.", "consumer": "-"},
    "material": {"type": "obj", "required": True,
                "doc": "the full material card and where its numbers came "
                       "from.", "consumer": "-"},
    "manufacturing": {"type": "obj", "required": True,
                      "doc": "printer, build axis, raster axis, walls, "
                             "infill, and whether each was declared.",
                      "consumer": "-"},
    "requirements": {"type": "obj", "required": True,
                     "doc": "safety factor, max deflection, required cases.",
                     "consumer": "-"},
    "cases": {"type": "arr", "required": True,
             "doc": "every load case, in the words of what it represents.",
             "consumer": "-"},
    "design_settings": {"type": "obj", "required": True,
                        "doc": "volfrac, filter_mm, symmetry, voxel_mm, "
                               "target_elements.", "consumer": "-"},
    "geometry_sources": {"type": "obj", "required": True,
                         "doc": "which files/regions fed preserve, "
                                "obstacle, design_space, starting_shape.",
                         "consumer": "-"},
    # Only present when `setup`/`report` were passed to `assumptions.write` --
    # true for every run through `topopt run` or `topopt assumptions`, but not
    # a guarantee of the bare `collect()` call.
    "design_space": {"type": "obj", "required": False,
                     "doc": "voxel size, grid shape, bbox -- present once a "
                            "setup was built.", "consumer": "-"},
    "design_freedom": {"type": "any", "required": False,
                       "doc": "spec 16.4.2 freedom-of-design-space report.",
                       "consumer": "-"},
    "build_volume": {"type": "any", "required": False,
                     "doc": "printer-fit report, same shape as setup's "
                            "info.build_volume.", "consumer": "-"},
    "setup_valid": {"type": "bool", "required": False,
                    "doc": "whether the setup validation passed.",
                    "consumer": "-"},
    "setup_errors": {"type": "arr", "required": False,
                     "doc": "setup validation errors.", "consumer": "-"},
    "setup_warnings": {"type": "arr", "required": False,
                       "doc": "setup validation warnings.", "consumer": "-"},
}, "topopt assumptions (also `topopt run`, writes `assumptions.json`)",
   "the 前提条件 page as data: loads, material, machine, what was fixed")


# ---------------------------------------------------------------- checking

def validate_output(data, source=None):
    """-> [Problem]. Dispatches on `data['schema']`.

    Mirrors `schema.py.validate`'s asymmetry: an unknown top-level key is a
    warning (result files accumulate fields across phases, same as study
    files accumulate legacy keys), a missing required key or a key with the
    wrong type is an error.
    """
    problems = []

    def add(path, message, severity="error"):
        problems.append(Problem(path, message, None, severity))

    if not isinstance(data, dict):
        return [Problem("", "expected an object at the top level, got %s"
                        % type(data).__name__)]

    schema_value = data.get("schema")
    if schema_value is None:
        add("schema", "no `schema` key; cannot identify what kind of "
                      "output this is")
        return problems
    if not isinstance(schema_value, str) or schema_value not in _REGISTRY:
        add("schema", "unknown schema %r; known: %s"
            % (schema_value, ", ".join(sorted(_REGISTRY))))
        return problems

    _kind, table, _written_by, _what = _REGISTRY[schema_value]
    for key, value in data.items():
        rule = table.get(key)
        if rule is None:
            if key == "schema":
                continue
            add(key, "unknown key for schema %r; it is not read by anything "
                     "that knows about this file" % schema_value,
                severity="warning")
            continue
        if not _type_ok(value, rule.get("type", "any")):
            add(key, "expected %s, got %s"
                % (rule.get("type", "any"), type(value).__name__))

    for key, rule in table.items():
        if rule.get("required") and key not in data:
            add(key, "missing required key (schema %r)" % schema_value)

    return problems


def kinds():
    """-> [(schema_value, kind, table, written_by, what)], for the doc and CLI."""
    return sorted(((sv,) + rest for sv, rest in _REGISTRY.items()),
                  key=lambda row: row[1])


# ------------------------------------------------------------------ the doc

def render_doc():
    """`topopt/docs/output-schema.md`, generated from the tables above so the
    checked-in file cannot drift from what `validate_output` actually checks."""
    L = ["# Output JSON schemas", "",
        "Generated from `topopt/src/topopt/outschema.py` -- do not hand-edit; "
        "run `python -m topopt validate --print-doc` and check the result in.",
        "",
        "Every file below carries a `schema` key of the form "
        "`topopt/<kind>/<version>` (one exception noted in its section) so "
        "`topopt validate` can identify it without guessing from its keys. "
        "An unknown top-level key is a warning, not an error -- these files "
        "accumulate fields across phases the same way study files "
        "accumulate legacy keys, and a validator that rejects a file the "
        "tools would have read correctly is worse than none.",
        ""]
    for schema_value, kind, table, written_by, what in kinds():
        L.append("## `%s`" % kind)
        L.append("")
        L.append("%s." % (what[0].upper() + what[1:]))
        L.append("")
        L.append("- **Written by:** %s" % written_by)
        L.append("- **`schema` value:** `%s`" % schema_value)
        L.append("")
        L.append("| key | type | required | meaning | consumer |")
        L.append("|---|---|---|---|---|")
        for key in sorted(table):
            rule = table[key]
            L.append("| `%s` | %s | %s | %s | %s |" % (
                key, rule.get("type", "any"),
                "yes" if rule.get("required") else "no",
                rule.get("doc", "").replace("|", "\\|"),
                rule.get("consumer", "-").replace("|", "\\|")))
        L.append("")
    return "\n".join(L).rstrip() + "\n"


# ------------------------------------------------------------------- files

def load_and_validate(path):
    """-> (data, [Problem]) for one file. Raises OSError/ValueError if the
    file cannot be read at all -- that is exit code 2 territory, same as a
    study that cannot be read (schema.py), and the CLI below treats it as
    such rather than folding it into the problem list."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data, validate_output(data, source=path)

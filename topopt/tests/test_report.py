"""`topopt report`: the verdict, and the rule that UNKNOWN is not PASS.

The failure mode this file guards against is a report that reads as a pass
because the checks that would have failed were never run. Spec 18 lists
fourteen stop conditions; a run that skipped CalculiX has no answer for four of
them, and "no answer" must count against the part.
"""

import json
import os

import pytest

from topopt.cli import report as rep


def _min_data(**kw):
    d = {"setup": {"ok": True, "errors": [], "warnings": []},
         "optimisation": {"converged": True,
                          "termination_reason": "converged",
                          "settings": {"build_axis": "Z"},
                          "material": {"material_model": "orthotropic_"
                                                         "equivalent_solid",
                                       "measured_for_specific_printer": False}},
         "fea": [{"case": "LC1", "safety_factor_p99_5": 4.0,
                  "min_safety_factor": 3.1, "max_displacement_mm": 0.2}],
         "required_safety_factor": 3.0,
         "connectivity": {"connected": True, "components": 1}}
    d.update(kw)
    return d


# ----------------------------------------------------------- the three states

def test_a_condition_that_was_not_checked_is_unknown_not_pass():
    fails, passes, unknown = rep.evaluate(_min_data())
    keys = {k for k, _t, _d in unknown}
    # No CalculiX in this data, so these have no answer.
    assert "equilibrium" in keys and "mesh_convergence" in keys
    assert not any(k in {k2 for k2, _t, _d in passes}
                   for k in ("equilibrium", "mesh_convergence"))


def test_an_incomplete_run_does_not_report_pass():
    md = rep.render(_min_data())
    assert "## Verdict: **INCOMPLETE**" in md
    assert "not the same as fine" in md


def test_a_failing_condition_makes_the_verdict_fail():
    d = _min_data()
    d["fea"][0]["safety_factor_p99_5"] = 1.2
    fails, _p, _u = rep.evaluate(d)
    assert any(k == "safety_factor" for k, _t, _d in fails)
    assert "## Verdict: **FAIL**" in rep.render(d)


def test_failures_come_before_the_reassuring_numbers():
    """A report whose FAILs are on page three gets skimmed to page two.

    Anchored on the section titles rather than on a heading number, because
    the numbering shifts with which stages actually ran."""
    d = _min_data()
    d["fea"][0]["safety_factor_p99_5"] = 1.2
    md = rep.render(d)
    assert md.index("What failed") < md.index("How it was solved")
    assert md.index("What failed") < md.index("Structural results")


def test_the_verdict_is_stated_in_a_sentence_not_only_as_a_word():
    """'FAIL' alone tells a reader the outcome but not the consequence."""
    d = _min_data()
    d["fea"][0]["safety_factor_p99_5"] = 1.2
    assert "This part is not signed off" in rep.render(d)
    assert "there is no pass either" in rep.render(_min_data())


def test_the_counts_are_shown_at_the_top():
    """So the shape of the result is visible without reading the lists."""
    md = rep.render(_min_data())
    assert "| failed | **0** |" in md
    assert "| not checked |" in md and "| passed |" in md


def test_the_report_points_at_the_assumptions_page():
    """The report says what was measured; it is not the place that says what
    was assumed, and a reader needs to be told where that lives."""
    assert "assumptions.md" in rep.render(_min_data())


def test_sections_are_numbered_and_the_numbering_does_not_skip():
    import re
    nums = [int(n) for n in re.findall(r"^## (\d+)\. ", rep.render(_min_data()),
                                       re.M)]
    assert nums == list(range(1, len(nums) + 1))


def test_the_build_volume_reaches_the_report():
    d = _min_data(setup={"ok": True, "errors": [], "warnings": [], "info": {
        "build_volume": {"build_axis": "Z",
                         "printer": {"name": "Bambu Lab X1-Carbon",
                                     "usable_mm": [250, 250, 250]},
                         "domain": {"size_mm": [300, 300, 184],
                                    "fits_as_declared": False}}}})
    md = rep.render(d)
    assert "300 × 300 × 184" in md
    assert "| fits with Z up | **NO** |" in md


def test_how_much_was_optimised_reaches_the_report():
    d = _min_data(setup={"ok": True, "errors": [], "warnings": [], "info": {
        "design_freedom": {"free_fraction_of_domain": 0.96,
                           "preserve_fraction_of_expected_part": 0.17,
                           "fixed_points": [{"kind": "support",
                                             "region": "foot_A",
                                             "centroid_mm": [0, 110, 4]}]}}})
    md = rep.render(d)
    assert "96 %" in md and "17 %" in md
    assert "foot_A" in md
    assert "not chosen by the optimiser" in md


def test_the_commit_hash_reaches_the_report():
    """It read `manifest["git"]["commit"]`; `manifest.py` writes
    `source.git_commit`. Every report ever produced therefore said "commit:
    not recorded" with the hash sitting in the file beside it. For a
    paper-facing deliverable this is the one reproducibility field a reviewer
    looks for, and it was silently blank."""
    d = _min_data(manifest={
        "tool": {"version": "1.0.0"},
        "source": {"git_commit": "cf0616ce3787", "git_dirty": False},
        "environment": {"python": "3.13.5", "platform": "Windows-11",
                        "packages": {"numpy": "2.4.2", "scipy": "1.18.0"}},
        "run": {"started_utc": "2026-08-05T11:00:00Z", "seconds": 845.9}})
    md = rep.render(d)
    assert "cf0616ce3787" in md
    assert "3.13.5" in md and "2.4.2" in md
    assert "2026-08-05T11:00:00Z" in md
    assert "not recorded" not in md


def test_a_dirty_working_tree_is_called_out():
    """A commit hash does not describe code that was edited after it."""
    d = _min_data(manifest={"source": {"git_commit": "abc123",
                                       "git_dirty": True},
                            "environment": {}, "run": {}})
    md = rep.render(d)
    assert "working tree dirty" in md or "dirty" in md
    assert "does not fully describe the code" in md


# ------------------------------------------------------- individual checks

def test_reaching_the_iteration_cap_is_reported_as_not_converged():
    d = _min_data()
    d["optimisation"]["converged"] = False
    d["optimisation"]["termination_reason"] = "max_iterations"
    fails, _p, _u = rep.evaluate(d)
    assert any(k == "converged" for k, _t, _d in fails)
    assert "max_iterations" in rep.render(d)


def test_a_provisional_material_card_passes_but_says_so():
    _f, passes, _u = rep.evaluate(_min_data())
    detail = next(d for k, _t, d in passes if k == "material_values")
    assert "provisional" in detail
    assert "no values measured on this printer" in detail


def test_null_material_values_are_a_failure():
    d = _min_data()
    d["optimisation"]["material"]["Ez_MPa"] = None
    fails, _p, _u = rep.evaluate(d)
    assert any(k == "material_values" for k, _t, _d in fails)


def test_a_legacy_string_material_does_not_crash_the_report():
    """Runs recorded before Phase 2 wrote `material` as a bare string. The
    report exists to summarise those archives, so it must read them."""
    d = _min_data()
    d["optimisation"]["material"] = "PLA"
    md = rep.render(d)
    assert "isotropic_two_constant" in md


def test_build_axis_is_unknown_rather_than_failed_when_unknowable():
    """An old summary has no build_axis field at all. Reading that absence as
    'undeclared' puts a FAIL on studies that declare it perfectly well, and a
    check that cries wolf on the archive gets switched off."""
    d = _min_data()
    d["optimisation"]["settings"] = {}
    d["fea"] = [{"case": "x", "safety_factor_p99_5": 4.0,
                 "min_safety_factor": 4.0, "max_displacement_mm": 0.1}]
    _f, _p, unknown = rep.evaluate(d)
    assert "build_axis" in {k for k, _t, _d in unknown}


def test_build_axis_missing_from_a_study_that_was_read_is_a_failure():
    d = _min_data()
    d["optimisation"]["settings"] = {}
    d["study_raw"] = {"material": "PLA"}          # read, and it is not there
    fails, _p, _u = rep.evaluate(d)
    assert any(k == "build_axis" for k, _t, _d in fails)


def test_a_grid_limited_thickness_is_not_a_wall_measurement():
    """2 x voxel is the finest the grid can hold. Judging a wall requirement
    against it would pass or fail the part on the mesh density."""
    d = _min_data(thickness={"optimised_region": {"min_mm": 3.274,
                                                  "grid_limited": True}})
    _f, passes, unknown = rep.evaluate(d)
    assert "min_wall" in {k for k, _t, _d in unknown}
    assert "min_wall" not in {k for k, _t, _d in passes}


def test_a_measured_thickness_is_judged():
    d = _min_data(thickness={"optimised_region": {"min_mm": 4.5,
                                                  "grid_limited": False}},
                  min_wall_mm=2.0)
    _f, passes, _u = rep.evaluate(d)
    assert "min_wall" in {k for k, _t, _d in passes}


def test_support_checks_are_read_from_printcheck():
    d = _min_data(printcheck={
        "support_on_critical_faces": {"pass": False, "note": "lands on a seat"},
        "enclosed_support": {"pass": True, "note": "reachable"}})
    fails, passes, _u = rep.evaluate(d)
    assert "support_faces" in {k for k, _t, _d in fails}
    assert "enclosed_support" in {k for k, _t, _d in passes}


# --------------------------------------------------------------- rendering

def test_no_format_placeholder_survives_into_the_rendered_report():
    """A "%d condition(s) passed" heading shipped because the literal was put
    in a list that gets joined later, so the format spec was never
    substituted. Cheap to check, and it is the first thing a reader sees."""
    md = rep.render(_min_data())
    for bad in ("%d", "%s", "%.2f"):
        assert bad not in md, "unsubstituted %r in the report" % bad


def test_the_passed_count_matches_the_list():
    d = _min_data()
    _f, passes, _u = rep.evaluate(d)
    assert "<summary>%d condition(s) passed</summary>" % len(passes)         in rep.render(d)


def test_the_report_says_when_calculix_was_not_run():
    md = rep.render(_min_data())
    assert "Not run." in md
    assert "carry only its own assumptions" in md


def test_the_report_lists_the_tools_this_machine_has():
    md = rep.render(_min_data())
    assert "External tools on this machine" in md
    assert "never as passing" in md


def test_a_check_that_raises_becomes_unknown_not_a_crash():
    """One malformed section must not take the whole report down -- the report
    is most needed exactly when a run went wrong."""
    d = _min_data()
    d["extract"] = {"chosen_thickness": "not a dict"}
    _f, _p, unknown = rep.evaluate(d)
    assert any(k == "min_wall" for k, _t, _d in unknown)


# ------------------------------------------------------------------ the CLI

def test_report_on_an_empty_directory_says_so(tmp_path):
    with pytest.raises(FileNotFoundError, match="nothing to report"):
        rep.main([str(tmp_path)])


def test_report_writes_both_markdown_and_json(tmp_path):
    with open(tmp_path / "opt.json", "w", encoding="utf-8") as f:
        json.dump({"settings": {"build_axis": "Z"}, "history": [],
                   "converged": True, "termination_reason": "converged",
                   "material": {"material_model": "orthotropic_"
                                                  "equivalent_solid"}}, f)
    code = rep.main([str(tmp_path), "--quiet"])
    assert os.path.exists(tmp_path / "report.md")
    blob = json.load(open(tmp_path / "report.json", encoding="utf-8"))
    assert blob["verdict"] in ("FAIL", "INCOMPLETE", "PASS")
    # Incomplete is not a pass, so the exit code is non-zero.
    assert code == 1


# --------------------------------------------------------------- topopt run

def test_a_skipped_stage_does_not_erase_the_stages_after_it():
    """`print` does not depend on `ccx`. A missing CalculiX must not make a
    printability check that ran perfectly well vanish from the record --
    that under-reports the work, which is the opposite error to
    over-reporting it and is still wrong."""
    from topopt.cli.run import _valid_through
    log = [{"stage": "setup", "exit_code": 0, "error": None, "skipped": None},
           {"stage": "opt", "exit_code": 1, "error": None, "skipped": None},
           {"stage": "extract", "exit_code": 1, "error": None, "skipped": None},
           {"stage": "ccx", "exit_code": None, "error": None, "skipped": True},
           {"stage": "print", "exit_code": 0, "error": None, "skipped": None},
           {"stage": "report", "exit_code": 1, "error": None, "skipped": None}]
    assert _valid_through(log, None) == ["setup", "opt", "extract", "print"]


def test_an_errored_stage_does_truncate():
    """Anything after a crash ran on a state nobody has checked."""
    from topopt.cli.run import _valid_through
    log = [{"stage": "setup", "exit_code": 0, "error": None, "skipped": None},
           {"stage": "opt", "exit_code": None, "error": "boom", "skipped": None},
           {"stage": "extract", "exit_code": 0, "error": None, "skipped": None}]
    assert _valid_through(log, "opt") == ["setup"]


def test_the_pipeline_order_matches_the_spec():
    """Spec 16.2: setup -> opt -> extract -> ccx -> print -> slice -> report
    -> publish. The module docstring said so while the code ran print before
    ccx.

    `slice` sits between `print` and `report`: geometry first, then the
    toolpath, then the report that quotes both. `publish` is last because it
    copies the chosen shape out and quotes the verdict on it, so it cannot run
    before `report` has produced one."""
    from topopt.cli.run import STAGES
    assert STAGES == ("setup", "opt", "extract", "ccx", "print", "slice",
                      "report", "publish")



def test_a_crashed_stage_is_not_announced_as_a_verdict_on_the_part():
    """Measured: `opt` died with MemoryError and the pipeline printed "shape
    does not meet the requirement" -- a sentence about a bracket, when the
    fact was that the machine ran out of memory. The note is written for a
    stage's *expected* non-zero exit; a crash must not borrow it. This is the
    project's oldest rule (a tool defect reported as the user's problem is the
    worst kind) applied to the console line rather than to an exit code."""
    import io
    import contextlib
    from topopt.cli import run as R

    buf = io.StringIO()
    log = []

    def record(name, code, err=None, skipped=None, note=None):
        # the same body as run()'s local `record`, exercised directly
        state = ("SKIPPED" if skipped else "ERROR" if err else
                 "ok" if code == 0 else "exit %d" % code)
        print("[%-7s] %-8s %s" % (name, state, err or note or ""))
        log.append((name, code, err, note))

    with contextlib.redirect_stdout(buf):
        record("opt", None, "MemoryError: Unable to allocate 3.94 MiB",
               note="shape does not meet the requirement")
    out = buf.getvalue()
    assert "MemoryError" in out
    assert "shape does not meet the requirement" not in out
    # and the source really does prefer err over note
    src = io.open(R.__file__, encoding="utf-8").read()
    assert 'err or note or ""' in src
    assert 'note or err or ""' not in src

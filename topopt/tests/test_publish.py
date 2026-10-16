"""`04_print/`: does the person at the printer get told the truth?

A run leaves three thresholds' worth of STLs in one directory and exactly one
of them was chosen. In a flat folder the chosen file sits next to `_final.stl`
-- a *different* threshold -- and next to the density fields, indistinguishable
from working files. This module copies the right one out and writes the page
that goes with it, so the tests here are about what that page refuses to leave
unsaid.
"""

import json
import os

import pytest

from topopt import publish


def _run_dir(tmp_path, verdict="FAIL", level=0.4, chosen_exists=True):
    d = tmp_path / "03_optimization"
    d.mkdir()
    stl = d / "demo_study_t40.stl"
    step = d / "demo_study_t40.step"
    if chosen_exists:
        stl.write_text("solid x\nendsolid x\n", encoding="utf-8")
        step.write_text("ISO-10303-21;\n", encoding="utf-8")
    # All three preview sources, because the fixture that made only the first
    # one let a row with an empty description ship: the test saw three rows
    # where the real folder had six.
    for f in ("demo_study_topo.png", "demo_study_setup.png",
              "demo_study_fea_1.png", "demo_study_fea_2.png"):
        (d / f).write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    # LC2 has the lower safety factor, so it is the one worth a picture.
    (d / "demo_study.json").write_text(json.dumps({"final_fea": [
        {"case": "LC1 tipping", "safety_factor_p99_5": 1277.0},
        {"case": "LC2 lift", "safety_factor_p99_5": 66.0}]}),
        encoding="utf-8")
    (d / "demo_study_extract.json").write_text(json.dumps({
        "study": "demo_study.json", "voxel_mm": 3.0, "chosen_level": level,
        "chosen_reason": "lightest candidate that passes every check",
        "outputs": {"stl": "some/other/machine\\demo_study_t40.stl",
                    "step": "some/other/machine\\demo_study_t40.step"},
    }), encoding="utf-8")
    # A PASS means every condition was checked and met, so it carries neither
    # failures nor unknowns -- a fixture with both would describe a report the
    # tool cannot emit, and the test built on it would prove nothing.
    (d / "report.json").write_text(json.dumps({
        "verdict": verdict,
        "fail": [{"key": "converged", "condition": "The density field "
                                                   "converged",
                  "detail": "termination: max_iterations"}]
        if verdict == "FAIL" else [],
        "unknown": [] if verdict == "PASS" else
        [{"key": "equilibrium",
          "condition": "Load/reaction balance within 1 %",
          "detail": "not checked in this run"}],
        "pass": [],
    }), encoding="utf-8")
    (d / "printcheck.json").write_text(json.dumps({
        "build_axis": 2, "support_volume_mm3": 1234.0,
        "enclosed_support": {"pass": False,
                             "note": "7488 mm3 sealed inside a cavity"},
        "support_on_critical_faces": {"pass": True, "note": "clear"},
    }), encoding="utf-8")
    return d


def _with_finish(d):
    """Turn the fixture into a run whose finish stage produced the ship file,
    with a `*_smooth.stl` sitting beside it the way a real run leaves one."""
    (d / "demo_study_t40_finish.stl").write_text("solid x\nendsolid x\n",
                                                 encoding="utf-8")
    (d / "demo_study_t40_smooth.stl").write_text("solid x\nendsolid x\n",
                                                 encoding="utf-8")
    f = d / "demo_study_extract.json"
    blob = json.loads(f.read_text(encoding="utf-8"))
    blob["outputs"]["finish_stl"] = "demo_study_t40_finish.stl"
    blob["outputs"]["smooth_stl"] = "demo_study_t40_smooth.stl"
    blob["finish"] = {"voxel_mm": 1.0, "feature_tolerance_mm": 1.0,
                      "smooth": {"sigma_mm": 3.0, "level": 0.455,
                                 "cad_tolerance_mm": 0.333}}
    f.write_text(json.dumps(blob), encoding="utf-8")
    return d


def test_the_preserve_rounding_smooth_stl_is_not_published_beside_the_part(tmp_path):
    """`*_smooth.stl` is a Gaussian over the whole coarse density field --
    preserve included -- so it rounds the CAD interface into a blob: the tall
    tripod's camera fingers merge and its nut pocket closes. In 03_optimization
    it is a fair diagnostic of the optimised body and it is what `topopt ccx`
    re-analyses. In the folder headed "print this" it is a trap, because
    "smooth" reads as "the nicer one" -- and it is the file a user opened
    before reporting that the smoothing had wrecked the part it was told not
    to touch."""
    d = _with_finish(_run_dir(tmp_path))
    publish.publish(str(d), str(tmp_path / "04_print"), part="demo",
                    quiet=True)
    out = os.listdir(tmp_path / "04_print")
    assert "demo.stl" in out                    # the finished mesh
    assert "demo_raw.stl" in out                # the analysis grid
    assert not any(f.endswith("_smooth.stl") for f in out), out


def test_a_run_without_a_finish_stage_still_publishes_its_smooth_stl(tmp_path):
    """Nothing rebuilt the interface there, so the smoothed mesh is not
    misleading -- it is the only nice-looking file the run produced."""
    d = _run_dir(tmp_path)
    (d / "demo_study_t40_smooth.stl").write_text("solid x\nendsolid x\n",
                                                 encoding="utf-8")
    f = d / "demo_study_extract.json"
    blob = json.loads(f.read_text(encoding="utf-8"))
    blob["outputs"]["smooth_stl"] = "demo_study_t40_smooth.stl"
    f.write_text(json.dumps(blob), encoding="utf-8")
    publish.publish(str(d), str(tmp_path / "04_print"), part="demo",
                    quiet=True)
    assert "demo_smooth.stl" in os.listdir(tmp_path / "04_print")


def test_the_page_quotes_the_cad_tolerance_as_well_as_the_voxel_one(tmp_path):
    """Two bounds, and quoting the wrong one either over-trusts the file or
    re-cuts something that was already right."""
    d = _with_finish(_run_dir(tmp_path))
    publish.publish(str(d), str(tmp_path / "04_print"), part="demo",
                    quiet=True)
    md = (tmp_path / "04_print" / "PRINT.md").read_text(encoding="utf-8")
    assert "1.00 mm" in md                      # feature_tolerance_mm
    assert "0.33 mm" in md                      # smooth.cad_tolerance_mm
    assert "CAD face" in md


# ------------------------------------------------------------ the right file

def test_the_chosen_threshold_is_the_one_copied_out(tmp_path):
    d = _run_dir(tmp_path)
    (d / "demo_study_final.stl").write_text("solid wrong\n", encoding="utf-8")
    w = publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    assert os.path.basename(w["stl"]) == "demo.stl"
    assert "solid x" in open(w["stl"], encoding="utf-8").read()


def test_a_path_recorded_on_another_machine_still_resolves(tmp_path):
    """`_extract.json` keeps the path as it was written -- another working
    directory, Windows separators. Only the basename is reliable."""
    d = _run_dir(tmp_path)
    w = publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    assert os.path.exists(w["stl"])


def test_the_part_is_named_after_the_part_not_the_threshold(tmp_path):
    """`demo_study_t40.stl` says which threshold produced it, which is not
    what the person at the printer is looking for."""
    d = _run_dir(tmp_path)
    w = publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    assert os.path.basename(w["stl"]) == "demo.stl"
    assert os.path.basename(w["step"]) == "demo.step"


def test_previews_are_named_for_what_they_show(tmp_path):
    d = _run_dir(tmp_path)
    w = publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    assert "preview_shape.png" in w
    assert "preview_design_space.png" in w


def test_the_stress_figure_shows_the_case_that_governs(tmp_path):
    """It copied `_fea_1.png` blindly, which on the tall tripod is the tipping
    limit at a safety factor of 1277 -- the least demanding of four. A figure
    captioned "stress" showing the case that governs nothing is worse than no
    figure."""
    d = _run_dir(tmp_path)
    assert publish._governing_case(str(d)) == (2, "LC2 lift")
    publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    md = open(tmp_path / "04_print" / "PRINT.md", encoding="utf-8").read()
    assert "governing" in md and "LC2" in md


def test_support_quantity_is_flagged_as_judged_elsewhere(tmp_path):
    """`topopt print` applies a 15 % overhang limit that is not one of the
    report's stop conditions, so section 3 is not the complete list of things
    wrong with the part -- and reads as though it were."""
    md = _md(tmp_path)
    assert "topopt print" in md
    assert "overhang" in md


# ----------------------------------------------------- what the page must say

def _md(tmp_path, **kw):
    d = _run_dir(tmp_path, **kw)
    publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    return open(tmp_path / "04_print" / "PRINT.md", encoding="utf-8").read()


def test_the_page_says_this_is_not_a_finished_part(tmp_path):
    """AGENTS.md: the raw optimisation output is never a part. Mating
    dimensions are quantised to the voxel grid -- a 3.50 mm slot came out at
    3.21 mm on a real run -- and 04_print is exactly where someone stops
    reading and starts printing."""
    md = _md(tmp_path)
    assert "not a finished part" in md
    assert "rebuilt in CAD" in md
    assert "3.0 mm voxel grid" in md


def test_a_failing_verdict_is_stated_before_the_files(tmp_path):
    md = _md(tmp_path, verdict="FAIL")
    assert "verdict is FAIL" in md
    assert md.index("verdict is FAIL") < md.index("## 1. Files")


def test_every_failed_condition_is_listed(tmp_path):
    md = _md(tmp_path, verdict="FAIL")
    assert "The density field converged" in md
    assert "max_iterations" in md


def test_unchecked_conditions_are_listed_too(tmp_path):
    """The operator is exactly who should not be told an unrun check passed."""
    md = _md(tmp_path)
    assert "not checked:" in md
    assert "Load/reaction balance" in md


def test_a_passing_run_still_carries_the_cad_rebuild_warning(tmp_path):
    md = _md(tmp_path, verdict="PASS")
    assert "verdict is" not in md          # no scary banner
    assert "not a finished part" in md     # but the quantisation still applies
    assert "still applies" in md


def test_reorienting_to_save_support_is_warned_against(tmp_path):
    """The material is anisotropic and the analysis assumed an axis. Laying
    the part down to dodge support silently invalidates every safety factor."""
    md = _md(tmp_path)
    assert "Do not reorient" in md
    assert "no longer apply" in md


def test_enclosed_support_is_called_out_by_name(tmp_path):
    md = _md(tmp_path)
    assert "sealed cavity" in md.lower() or "Sealed cavity" in md
    assert "7488" in md


def test_the_page_says_where_the_rest_of_the_work_is(tmp_path):
    md = _md(tmp_path)
    for path in ("01_assumptions/assumptions.md", "02_setup/",
                 "03_optimization/report.md"):
        assert path in md


def test_no_format_placeholder_survives(tmp_path):
    md = _md(tmp_path)
    for bad in ("%d", "%s", "%.2f", "{}"):
        assert bad not in md


# ------------------------------------------------------------- missing inputs

def test_a_run_with_no_chosen_stl_still_writes_the_page(tmp_path):
    """A failed run deserves whatever it did produce to be findable, and the
    page is where someone finds out why there is nothing to print."""
    d = _run_dir(tmp_path, chosen_exists=False)
    w = publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    assert "stl" not in w
    assert os.path.exists(w["print_md"])


def test_an_empty_directory_does_not_raise(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    w = publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    assert os.path.exists(w["print_md"])
    md = open(w["print_md"], encoding="utf-8").read()
    assert "nothing here has been judged" in md


# --------------------------------------------------------- wired into `run`

def test_the_model_layout_has_the_four_stages():
    from topopt.cli.run import MODEL_DIRS
    assert list(MODEL_DIRS.values()) == ["01_assumptions", "02_setup",
                                         "03_optimization", "04_print"]


def test_publish_runs_after_report():
    """It quotes the verdict, so it cannot run before there is one."""
    from topopt.cli.run import STAGES
    assert STAGES.index("publish") > STAGES.index("report")


def test_every_listed_file_says_what_it_is(tmp_path):
    """An empty 'what it is' cell is worse than no row: it looks like the tool
    knows and declined to say. The previews were added to the folder without
    being added to the table's legend."""
    d = _run_dir(tmp_path)
    publish.publish(str(d), str(tmp_path / "04_print"), quiet=True)
    md = open(tmp_path / "04_print" / "PRINT.md", encoding="utf-8").read()
    body = md[md.index("## 1. Files"):md.index("## 2.")]
    rows = [l for l in body.splitlines()
            if l.startswith("| `") and l.endswith("|")]
    assert rows
    for r in rows:
        cells = [c.strip() for c in r.split("|")[1:-1]]
        assert len(cells) == 2 and cells[1], "no description for %s" % cells[0]


def test_a_preview_that_cannot_be_drawn_takes_the_stale_one_with_it(tmp_path):
    """The one figure the repo tells everyone to look at must not be last
    run's.

    `write_mesh_preview` gives up above 400k triangles -- a picture is not
    worth failing a run over. MEASURED (tall tripod v7): the 1.0 mm finish is
    226k triangles and draws, the 0.5 mm finish is 905k and does not, and the
    1.0 mm picture stayed in `04_print/` next to a different STL. A reader
    doing what `AGENTS.md` tells them to do -- look at `preview_print.png`
    rather than at the numbers -- would have been looking at the wrong shape.
    """
    d = _run_dir(tmp_path)
    dest = tmp_path / "04_print"
    dest.mkdir()
    stale = dest / "preview_print.png"
    stale.write_bytes(bytes.fromhex("89504e470d0a1a0a"))

    # The fixture's chosen STL is an empty shell, so there is nothing to draw
    # and `write_mesh_preview` returns None -- the same path a 905k-triangle
    # mesh takes.
    written = publish.publish(str(d), str(dest), quiet=True)

    assert "preview_print.png" not in written
    assert not stale.exists(), "last run's picture was left behind"
    page = (dest / "PRINT.md").read_text(encoding="utf-8")
    assert "preview_print.png" in page and "REMOVED" in page

    # Publishing the same run again must still say the picture is missing.
    # Only the first pass has a stale file to delete, and a note tied to the
    # deletion would leave the second pass silent -- which reads as though the
    # figure was never meant to be there.
    publish.publish(str(d), str(dest), quiet=True)
    page = (dest / "PRINT.md").read_text(encoding="utf-8")
    assert "preview_print.png" in page and "too big to draw" in page
    assert "REMOVED" not in page

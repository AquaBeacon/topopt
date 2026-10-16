"""`topopt run --model DIR` end to end, on a beam small enough to be a test.

Everything else about the model layout is checked against hand-built dicts,
which is exactly the wrong place to check it. Two things changed together:

- `render()` started reading `setup.info.build_volume` and
  `setup.info.design_freedom`,
- `--model` moved `*_setup.json` out of the directory `report.collect()` globs.

So the two most user-relevant sections of the report now survive only because
`optimize.py` embeds the whole `validate()` report under `"setup"` in its own
summary. Nothing in a unit test can notice if that stops being true: the
synthetic dicts would keep passing while the real deliverable quietly lost the
sections. This runs the actual pipeline and reads the actual files.

576 free cells, 3 iterations, one load case -- seconds, not minutes.
"""

import json
import os

import pytest

pytestmark = pytest.mark.slow


STUDY = {
    "_what": "A beam held at one end, loaded at the other.",
    "units": "mm_N_MPa", "material": "PLA",
    "build_axis": "Z", "raster_axis": "X",
    "printer": "X1C",
    "safety_factor": 2.0, "max_deflection_mm": 5.0,
    "voxel_mm": 4.0,
    "design": {"volfrac": 0.4, "filter_mm": 12.0},
    "geometry": {
        "voxel_mm": 4.0, "pad_voxels": 1,
        "design_space": {"type": "box", "min": [0, 0, 0], "max": [80, 24, 24]},
        "preserve": {
            "left": {"type": "box", "min": [0, 0, 0], "max": [8, 24, 24]},
            "right": {"type": "box", "min": [72, 0, 0], "max": [80, 24, 24]}},
    },
    "regions": {
        "a": {"type": "box", "min": [0, 0, 0], "max": [8, 24, 24]},
        "b": {"type": "box", "min": [72, 0, 0], "max": [80, 24, 24]}},
    "cases": [{"name": "bend",
               "_note": "Someone leans on the free end.",
               "supports": [{"region": "a", "fix": "xyz"}],
               "loads": [{"region": "b", "force": [0, 0, -40]}]}],
}


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    from topopt.cli.run import run
    d = tmp_path_factory.mktemp("beam")
    study = d / "beam_study.json"
    study.write_text(json.dumps(STUDY), encoding="utf-8")
    model = d / "model"
    run(str(study), str(d / "unused"), iters=3, levels="0.5",
        do_ccx=False, quiet=True, model_dir=str(model))
    return model


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------- the layout

def test_the_four_stages_are_written(run_dir):
    for name in ("01_assumptions", "02_setup", "03_optimization", "04_print"):
        assert os.path.isdir(run_dir / name), "%s is missing" % name


def test_the_setup_previews_go_next_to_the_geometry(run_dir):
    got = os.listdir(run_dir / "02_setup")
    assert any(f.endswith("_setup.json") for f in got)
    assert any(f.endswith("_preserve.stl") for f in got)


def test_the_working_files_stay_out_of_the_print_folder(run_dir):
    """`04_print/` is for the person at the printer. A density field in it is
    one more thing to be unsure about."""
    got = os.listdir(run_dir / "04_print")
    assert not any(f.endswith(".npy") for f in got)
    assert "PRINT.md" in got


def test_the_chosen_shape_is_in_the_print_folder(run_dir):
    got = os.listdir(run_dir / "04_print")
    assert any(f.endswith(".stl") for f in got), got


def test_the_print_folder_has_preview_images(run_dir):
    """The requirement is "the final STL *and preview images*". The setup
    preview lives in `02_setup/` under the model layout, so publish has to
    look in the sibling folder as well; the orientation figure is produced
    here because no analysis stage makes one."""
    got = set(os.listdir(run_dir / "04_print"))
    assert "preview_shape.png" in got
    assert "preview_design_space.png" in got
    assert "preview_orientation.png" in got


# --------------------------------------------- the report survives the split

def test_the_build_volume_section_survives(run_dir):
    """The regression this file exists for. `*_setup.json` is no longer in the
    directory the report globs, so this only works because `opt` records the
    validation inside its own summary."""
    md = _read(run_dir / "03_optimization" / "report.md")
    assert "Can it be made" in md
    assert "Bambu Lab X1-Carbon" in md
    assert "| fits with Z up |" in md


def test_the_design_freedom_section_survives(run_dir):
    md = _read(run_dir / "03_optimization" / "report.md")
    assert "How much of the shape was optimised" in md
    assert "share of the domain free to decide" in md
    assert "not chosen by the optimiser" in md


def test_the_setup_info_really_carries_the_new_keys(run_dir):
    """Named separately from the two above so a failure says *why* they broke
    rather than only that they did."""
    import glob
    summary = glob.glob(str(run_dir / "03_optimization" / "beam_study.json"))
    assert summary, "the opt summary was not written"
    info = json.load(open(summary[0], encoding="utf-8"))["setup"]["info"]
    assert "build_volume" in info
    assert "design_freedom" in info


def test_the_manifest_is_written_under_model(run_dir):
    """`--model` takes no `--out`, and the manifest lookup only knew `--out`,
    so the layout meant for publishing recorded nothing."""
    from topopt.manifest import out_dir_from_argv
    assert out_dir_from_argv(["s.json", "--model", str(run_dir)])


# ------------------------------------------------------- the two documents

def test_the_assumptions_page_is_written_and_specific(run_dir):
    md = _read(run_dir / "01_assumptions" / "assumptions.md")
    assert "A beam held at one end" in md
    assert "Someone leans on the free end" in md
    assert "(0, 0, -40)" in md
    assert "Bambu Lab X1-Carbon" in md
    assert "does *not* establish" in md


def test_the_print_page_names_the_file_and_the_caveat(run_dir):
    md = _read(run_dir / "04_print" / "PRINT.md")
    assert "Print this:" in md
    assert "not a finished part" in md
    assert "Do not reorient" in md


def test_neither_page_leaks_a_format_placeholder(run_dir):
    for p in (run_dir / "01_assumptions" / "assumptions.md",
              run_dir / "04_print" / "PRINT.md",
              run_dir / "03_optimization" / "report.md"):
        md = _read(p)
        for bad in ("%d", "%s", "%.2f"):
            assert bad not in md, "%s in %s" % (bad, p)


# ------------------------------------------- a run that stops at the setup

def test_a_setup_that_fails_still_leaves_the_assumptions_page(tmp_path):
    """The measured failure in reverse. A design space bigger than the machine
    now stops the pipeline at `setup`, and that is exactly the run whose inputs
    someone needs to read -- so `01_assumptions/` must still be written, with
    the reason on it. A pipeline that stops silently is how the first tall
    tripod got three hours into a solve nobody should have started."""
    from topopt.cli.run import run
    big = json.loads(json.dumps(STUDY))
    big["geometry"]["design_space"] = {"type": "box", "min": [0, 0, 0],
                                       "max": [800, 240, 240]}
    big["geometry"]["preserve"]["right"] = {"type": "box",
                                            "min": [792, 0, 0],
                                            "max": [800, 240, 240]}
    big["regions"]["b"] = {"type": "box", "min": [792, 0, 0],
                           "max": [800, 240, 240]}
    big["voxel_mm"] = big["geometry"]["voxel_mm"] = 20.0
    study = tmp_path / "big_study.json"
    study.write_text(json.dumps(big), encoding="utf-8")
    model = tmp_path / "model"

    s = run(str(study), str(tmp_path / "unused"), iters=2, levels="0.5",
            do_ccx=False, quiet=True, model_dir=str(model))

    assert s["stopped_at"] == "setup"
    md = _read(model / "01_assumptions" / "assumptions.md")
    assert "setup did not validate" in md
    assert "DOES NOT FIT" in md
    # and nothing downstream claims to have happened
    assert "opt" not in s["valid_through"]


def test_the_report_can_see_the_manifest(run_dir):
    """The CLI wrapper writes manifest.json when the whole command returns --
    one second *after* the report is rendered. Every published report therefore
    came out with an empty reproducibility block while the commit hash sat in
    the file beside it. `run` now writes it before the report stage."""
    md = _read(run_dir / "03_optimization" / "report.md")
    assert "| commit |" in md
    assert "not recorded" not in md


# ----------------------------------------------------------- --quick

def test_quick_preset_keeps_the_verdict_and_drops_the_slow_exports(tmp_path):
    """`topopt run --quick` is for trying many variants: the optimisation,
    the final FEA and the printability check still run and are reported;
    CalculiX, the slicer, the smooth STL, the STEP and the finish grid do
    not, and every summary says the run was a quick one."""
    from topopt.cli.run import run
    study = tmp_path / "beam_study.json"
    study.write_text(json.dumps(STUDY), encoding="utf-8")
    model = tmp_path / "model"
    s = run(str(study), str(tmp_path / "unused"), iters=3, quiet=True,
            model_dir=str(model), quick=True)
    assert s["quick"] is True
    stages = {e["stage"]: e for e in s["stages"]}
    assert stages["ccx"]["skipped"] and "quick" in stages["ccx"]["note"]
    assert stages["slice"]["skipped"] and "quick" in stages["slice"]["note"]
    assert stages["opt"]["exit_code"] in (0, 1) and not stages["opt"]["error"]
    assert stages["print"]["exit_code"] == 0
    assert all("seconds" in e for e in s["stages"])
    opt_dir = model / "03_optimization"
    files = os.listdir(opt_dir)
    assert any(f.endswith("_final.stl") for f in files)
    assert any(f.endswith("_t50.stl") for f in files)
    assert not any(f.endswith("_smooth.stl") for f in files)
    assert not any(f.endswith(".step") for f in files)
    assert not any(f.endswith("_finish.stl") for f in files)
    with open(opt_dir / "beam_study.json", encoding="utf-8") as f:
        opt = json.load(f)
    assert opt["quick"] is True
    # The verdict still runs: the final FEA is only ever skipped for the
    # reason the full run skips it (a disconnected shape), never for --quick.
    if opt["connectivity"]["connected"]:
        assert opt["final_fea"]
    assert opt["checks"]
    with open(opt_dir / "beam_study_extract.json", encoding="utf-8") as f:
        ex = json.load(f)
    assert ex["quick"] is True and ex["levels"] == [0.5]
    assert os.path.exists(model / "04_print" / "PRINT.md")


def test_the_sliced_and_measured_mesh_is_the_finished_one(tmp_path):
    """`slice` and the fit table answer "what comes off the printer".

    They used to read `outputs["stl"]`, which after a finish stage is the
    analysis-grid body -- published under its `_raw` name precisely because it
    is NOT the file to print. MEASURED (tall tripod v7): the sliced mesh was
    missing the camera mount, 143 cm3 of the 424 cm3 shipped, so the print
    time and the mass on `PRINT.md` were for a different object.

    `ccx --ccx-geometry analysis` can explicitly select the raw mesh.
    """
    import json as _json
    from topopt.cli.run import _chosen_stl

    d = tmp_path / "03_optimization"
    d.mkdir()
    raw = d / "demo_t40.stl"
    fin = d / "demo_t40_finish.stl"
    for f in (raw, fin):
        f.write_text("solid x\nendsolid x\n", encoding="utf-8")
    (d / "demo_extract.json").write_text(_json.dumps({"outputs": {
        "stl": str(raw), "finish_stl": str(fin)}}), encoding="utf-8")

    assert _chosen_stl(str(d)) == str(raw)
    assert _chosen_stl(str(d), prefer_finished=True) == str(fin)

    # No finish stage: both answers are the analysis-grid mesh.
    (d / "demo_extract.json").write_text(_json.dumps({"outputs": {
        "stl": str(raw)}}), encoding="utf-8")
    assert _chosen_stl(str(d), prefer_finished=True) == str(raw)

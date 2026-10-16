"""Output JSON schemas: every file `topopt` writes carries a `schema` key and
is shaped the way `outschema.py` says it is.

This is the output-side counterpart to `test_schema.py` (which checks study
*input*). Three things are checked:

1. **Real recorded output validates with zero errors.** Both the frozen
   examples under `osmo_tripod/out/` and `tall_tripod/` (kinds whose `schema`
   key predates this module: `extract`, `fea-convergence`, `ccx`, `report`,
   `run`, `manifest`, `assumptions`, and the generative `opt` summary) and a
   *freshly produced* run (kinds this module's change just gave a `schema`
   key: `setup`, `fea`, `printcheck`, `printability`) -- the frozen examples
   for those five kinds predate the key entirely and are not re-validated
   here (see the module docstring in `outschema.py`: adding a key must not
   touch the regression baselines, so those old files are left alone).
2. **A doctored file missing a required key fails.**
3. **The checked-in doc equals what the tables render**, so the doc cannot
   drift from what `validate_output` actually checks.

The fresh run reuses `test_run_layout.py`'s beam fixture instead of running
the pipeline a second time -- same reasoning as that file's own docstring:
this only has to prove the *shape* of the output, and one small solve already
produces every kind except `fea`, `printability` and `opt-legacy`.
"""

import json
import os

import pytest

from topopt import outschema
from topopt.cli import validate as validate_cli
from test_run_layout import run_dir, STUDY                      # noqa: F401,E501 -- reuse, don't re-run

pytestmark = pytest.mark.slow      # pulls in `run_dir`, which solves a beam

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OSMO = os.path.join(ROOT, "osmo_tripod")
TALL = os.path.join(ROOT, "tall_tripod")
DOC = os.path.join(os.path.dirname(HERE), "docs", "output-schema.md")

# Recorded examples whose `schema` key predates this change (see module
# docstring). Skipped, not failed, when osmo_tripod/tall_tripod are not
# checked out next to the package.
RECORDED = [
    os.path.join(OSMO, "out", "gd_mount", "gd_mount_study.json"),          # opt
    os.path.join(TALL, "03_optimization", "tall_tripod_study.json"),       # opt
    os.path.join(TALL, "03_optimization", "tall_tripod_study_extract.json"),
    os.path.join(TALL, "03_optimization", "reanalysis",
                "tall_tripod_study_extract.json"),
    os.path.join(OSMO, "out", "fea_conv_auto", "osmo_tripod_fea_convergence.json"),
    os.path.join(OSMO, "out", "ccx_conv", "ccx_summary.json"),
    os.path.join(OSMO, "out", "ccx_r4_surface", "ccx_summary.json"),
    os.path.join(OSMO, "out", "ccx_r4_volume", "ccx_summary.json"),
    os.path.join(TALL, "03_optimization", "reanalysis", "ccx_8mm",
                "ccx_summary.json"),
    os.path.join(TALL, "03_optimization", "report.json"),
    os.path.join(TALL, "03_optimization", "run_summary.json"),
    os.path.join(TALL, "03_optimization", "manifest.json"),
    os.path.join(OSMO, "out", "ccx_conv", "manifest.json"),
    os.path.join(OSMO, "out", "fea_conv_auto", "manifest.json"),
    os.path.join(TALL, "01_assumptions", "assumptions.json"),
]


@pytest.mark.parametrize("path", RECORDED)
def test_recorded_output_validates(path):
    if not os.path.exists(path):
        pytest.skip("reference model not present next to the package")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = outschema.validate_output(data)
    errors = [p for p in problems if p.severity == "error"]
    assert not errors, "\n".join(str(p) for p in errors)


# --------------------------------------------------- a fresh run, every kind
# `run_dir` (imported above) runs `topopt run --model DIR` once on a beam
# small enough to be a test (576 free cells, 3 iterations) and is shared with
# test_run_layout.py's own checks on the same directory.

def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return json.load(f)


def test_fresh_setup_validates(run_dir):
    p = os.path.join(str(run_dir), "02_setup", "beam_study_setup.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_opt_validates(run_dir):
    p = os.path.join(str(run_dir), "03_optimization", "beam_study.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_extract_validates(run_dir):
    p = os.path.join(str(run_dir), "03_optimization", "beam_study_extract.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_printcheck_validates(run_dir):
    p = os.path.join(str(run_dir), "03_optimization", "printcheck.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_report_validates(run_dir):
    p = os.path.join(str(run_dir), "03_optimization", "report.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_run_summary_validates(run_dir):
    p = os.path.join(str(run_dir), "03_optimization", "run_summary.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_manifest_validates(run_dir):
    p = os.path.join(str(run_dir), "03_optimization", "manifest.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_assumptions_validates(run_dir):
    p = os.path.join(str(run_dir), "01_assumptions", "assumptions.json")
    problems = outschema.validate_output(_read(p))
    assert not [x for x in problems if x.severity == "error"]


def _solid_beam_stl(tmp_path):
    """A trivially-connected stand-in for `topopt fea` / `topopt print` --
    the whole design-space box, solid, at the beam study's own voxel size.

    The `run_dir` beam only runs 3 iterations (test_run_layout.py: "seconds,
    not minutes"), which is not enough to guarantee a *connected* result --
    and `fea`/`printability` need something the study's own regions ("a" at
    x=0..8, "b" at x=72..80) actually intersect, not a proof that the
    optimiser converged. That is already `test_regression_osmo.py`'s job.
    """
    import numpy as np
    from topopt.meshout import write_blocky_stl

    h = STUDY["geometry"]["voxel_mm"]
    shape = (int(80 / h), int(24 / h), int(24 / h))
    mask = np.ones(shape, bool)
    p = str(tmp_path / "beam_solid.stl")
    write_blocky_stl(p, mask, h, [0.0, 0.0, 0.0])
    return p


def test_fresh_fea_validates(tmp_path):
    """`topopt fea` is not a `run` stage, so this calls it directly -- same
    thing `topopt fea rebuild_study.json` does by hand after a real run."""
    from topopt.cli.fea import run_study
    from topopt.loadcase import load_case_from_json

    rebuild = dict(STUDY)
    rebuild["part"] = _solid_beam_stl(tmp_path)
    sp = tmp_path / "rebuild_study.json"
    sp.write_text(json.dumps(rebuild), encoding="utf-8")
    study = load_case_from_json(str(sp))
    run_study(study, outdir=str(tmp_path), quiet=True, png=False)

    import glob
    got = glob.glob(str(tmp_path / "*_fea.json"))
    assert got, "topopt fea wrote no *_fea.json"
    problems = outschema.validate_output(_read(got[0]))
    assert not [x for x in problems if x.severity == "error"]


def test_fresh_printability_validates(tmp_path):
    from topopt.cli.printability import analyse
    r = analyse(_solid_beam_stl(tmp_path), printer="X1C")
    problems = outschema.validate_output(r)
    assert not [x for x in problems if x.severity == "error"]


# ------------------------------------------------------------- opt-legacy
# No recorded example carries the `schema` key this change adds (the legacy,
# no-`geometry`-block path is not exercised anywhere else in this suite
# either), so this checks the table against a hand-built dict shaped exactly
# like `cli/optimize.py`'s legacy `summary = {...}` literal.

def _opt_legacy_dict(**overrides):
    d = {
        "schema": "topopt/opt-legacy/1", "part": "part.stl", "material": "PLA",
        "voxel_mm": 1.0, "domain_voxels": 100, "volume_fraction_target": 0.3,
        "volume_fraction_final": 0.31, "kept_volume_mm3": 100.0,
        "original_volume_mm3": 300.0, "mass_saving_pct": 66.0, "filter_mm": 3.0,
        "symmetry": [], "overhang_filter": False, "history": [],
        "blocky_stl_triangles": 12, "smooth_stl_triangles": 24,
        "ribs": {}, "extracted_members": {},
    }
    d.update(overrides)
    return d


def test_opt_legacy_table_accepts_its_own_shape():
    problems = outschema.validate_output(_opt_legacy_dict())
    assert not [p for p in problems if p.severity == "error"]


# --------------------------------------------------------- doctored files

def test_a_missing_required_key_is_an_error():
    d = _opt_legacy_dict()
    del d["material"]
    problems = outschema.validate_output(d)
    errors = [p for p in problems if p.severity == "error"]
    assert any(p.path == "material" for p in errors)


def test_a_wrong_typed_required_key_is_an_error():
    d = _opt_legacy_dict(history="not a list")
    errors = [p for p in outschema.validate_output(d) if p.severity == "error"]
    assert any(p.path == "history" for p in errors)


def test_an_unknown_top_level_key_is_only_a_warning():
    d = _opt_legacy_dict(some_future_field=1)
    problems = outschema.validate_output(d)
    assert not [p for p in problems if p.severity == "error"]
    assert any(p.path == "some_future_field" and p.severity == "warning"
              for p in problems)


def test_no_schema_key_is_an_error():
    problems = outschema.validate_output({"part": "x.stl"})
    assert any(p.path == "schema" for p in problems
              if p.severity == "error")


def test_an_unknown_schema_value_is_an_error():
    problems = outschema.validate_output({"schema": "topopt/made-up/7"})
    assert any(p.path == "schema" for p in problems
              if p.severity == "error")


# ------------------------------------------------------------------- doc

def test_the_checked_in_doc_matches_the_rendered_tables():
    assert os.path.exists(DOC), "topopt/docs/output-schema.md is missing"
    with open(DOC, encoding="utf-8") as f:
        checked_in = f.read()
    assert checked_in == outschema.render_doc(), (
        "topopt/docs/output-schema.md is out of date -- regenerate it with "
        "`python -m topopt validate --print-doc`")


# -------------------------------------------------------------- the CLI

def test_cli_valid_file_is_exit_0(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(_opt_legacy_dict()), encoding="utf-8")
    assert validate_cli.main([str(p)]) == 0


def test_cli_invalid_file_is_exit_1(tmp_path, capsys):
    d = _opt_legacy_dict()
    del d["material"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert validate_cli.main([str(p)]) == 1
    assert "material" in capsys.readouterr().out


def test_cli_missing_file_is_exit_2(tmp_path, capsys):
    assert validate_cli.main([str(tmp_path / "nope.json")]) == 2
    assert "nope.json" in capsys.readouterr().err


def test_cli_print_doc_matches_the_checked_in_file():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = validate_cli.main(["--print-doc"])
    assert code == 0
    with open(DOC, encoding="utf-8") as f:
        assert buf.getvalue() == f.read()


def test_validate_is_registered_in_the_main_cli():
    from topopt import cli
    assert "validate" in cli.COMMANDS

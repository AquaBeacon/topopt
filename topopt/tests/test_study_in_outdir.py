"""A study placed inside its own output directory would be overwritten by the
run summary that carries its name; the run refuses before solving."""

import os

import pytest

from topopt.cli.optimize import refuse_overwriting_the_study
from topopt.schema import InputError


def test_a_study_inside_outdir_with_the_summary_name_is_refused(tmp_path):
    study = tmp_path / "out" / "part_study.json"
    study.parent.mkdir()
    study.write_text("{}")
    with pytest.raises(InputError) as e:
        refuse_overwriting_the_study(str(study), str(tmp_path / "out"),
                                     "part_study")
    assert "overwrite" in str(e.value)


def test_a_study_next_to_outdir_is_fine(tmp_path):
    study = tmp_path / "part_study.json"
    study.write_text("{}")
    refuse_overwriting_the_study(str(study), str(tmp_path / "out"),
                                 "part_study")


def test_a_different_basename_inside_outdir_is_fine(tmp_path):
    study = tmp_path / "out" / "study.json"
    study.parent.mkdir()
    study.write_text("{}")
    refuse_overwriting_the_study(str(study), str(tmp_path / "out"), "other")

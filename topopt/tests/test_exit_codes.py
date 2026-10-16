"""Exit codes are findings, not crashes (AGENTS.md).

    0 = pass / valid      1 = fail / invalid
    2 = input unreadable  3 = external tool missing

The distinction that keeps getting lost is 1 vs 2. "I ran your study and it did
not meet the requirement" and "I could not read your study" are different
answers, and a caller that cannot tell them apart -- a script, a Skill, a
person reading CI -- will treat a typo as a structural failure.

These drive `topopt.cli.main` rather than the subcommand functions, because the
mapping lives in the front end and that is what a user actually invokes.
"""

import os

import pytest

from topopt import cli

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OSMO = os.path.join(ROOT, "osmo_tripod")
STUDY = os.path.join(OSMO, "gd_mount_study.json")


def test_no_such_study_is_2(tmp_path, capsys):
    code = cli.main(["setup", str(tmp_path / "nope.json"),
                     "--out", str(tmp_path)])
    assert code == 2
    assert "no such study file" in capsys.readouterr().err


def test_malformed_json_is_2(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{ oops", encoding="utf-8")
    code = cli.main(["setup", str(p), "--out", str(tmp_path)])
    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_schema_violation_is_2_and_lists_every_problem(tmp_path, capsys):
    p = tmp_path / "s.json"
    p.write_text('{"units": "inch_lbf_psi", "safety_factor": -1,\n'
                 ' "cases": [{"name": "c", "loads": [1]}]}\n', encoding="utf-8")
    code = cli.main(["setup", str(p), "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "inch_lbf_psi" in err
    assert "safety_factor" in err, "only the first problem was reported"


def test_a_directory_where_a_study_belongs_is_2(tmp_path):
    assert cli.main(["setup", str(tmp_path), "--out", str(tmp_path)]) == 2


def test_unknown_command_is_2():
    assert cli.main(["nonsense"]) == 2


def test_a_valid_study_is_0(tmp_path):
    if not os.path.exists(STUDY):
        pytest.skip("reference study not present")
    assert cli.main(["setup", STUDY, "--out", str(tmp_path)]) == 0


def test_yaml_without_pyyaml_explains_itself(tmp_path, monkeypatch, capsys):
    """Exit 2 with an install line, not an ImportError. A missing optional
    dependency is a fact about the environment, and the message has to be
    enough to fix it without reading the source."""
    import builtins
    real = builtins.__import__

    def no_yaml(name, *a, **kw):
        if name == "yaml":
            raise ImportError("no yaml")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    p = tmp_path / "s.yaml"
    p.write_text("material: PLA\n", encoding="utf-8")
    code = cli.main(["setup", str(p), "--out", str(tmp_path)])
    assert code == 2
    assert "pip install" in capsys.readouterr().err


def test_a_real_bug_still_raises(tmp_path, monkeypatch):
    """The exit-2 handler must not swallow everything.

    A TypeError from inside the solver is not "bad input"; turning it into
    exit 2 would report a defect in the tool as a defect in the user's file.
    """
    import topopt.cli.setup as setup_mod
    monkeypatch.setattr(setup_mod, "main",
                        lambda argv: (_ for _ in ()).throw(TypeError("boom")))
    with pytest.raises(TypeError):
        cli.main(["setup", "whatever.json", "--out", str(tmp_path)])

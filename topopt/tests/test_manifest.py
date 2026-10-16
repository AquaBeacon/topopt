"""`manifest.json` carries every field spec 17.1 asks for.

The spec lists ten things a run has to record to be reproducible. A manifest
that silently drops one is worse than no manifest, because the gap is invisible
until someone tries to reproduce the run and cannot.

The second thing tested here is the rule the module is built on: **a field that
could not be determined is null with a note saying why, never a guess.** This
machine has no git repository and no CalculiX, so those are null here -- and
that is the correct answer, provided it is labelled.
"""

import json
import os
import tempfile

import pytest

from topopt import manifest


def _built(**kw):
    return manifest.build("setup", ["study.json", "--out", "DIR"],
                          probe_external=False, **kw)


def test_spec_17_1_fields_are_all_present():
    m = _built(exit_code=0, seconds=1.5)
    assert m["source"]["git_commit"] is not None or \
        "git_commit_note" in m["source"], "git commit neither found nor explained"
    assert m["tool"]["version"]
    assert m["environment"]["python"]
    assert set(("freecad", "calculix", "gmsh")) <= \
        set(m["environment"]["external_tools"]), "external tool slots missing"
    assert m["environment"]["platform"]
    assert "inputs" in m                       # input + config file hashes
    assert m["run"]["finished_utc"]
    assert "random_seed" in m
    assert m["command"]["line"]


def test_unknown_fields_are_null_with_a_reason():
    """No plausible-looking guesses. This is the whole discipline of the file."""
    m = _built()
    if m["source"]["git_commit"] is None:
        assert m["source"]["git_commit_note"], "null commit with no explanation"
    if m["random_seed"] is None:
        assert m["random_seed_note"], "null seed with no explanation"
    for name, ver in m["environment"]["external_tools"].items():
        assert ver is None or isinstance(ver, str), name


def test_config_file_is_hashed():
    """spec 17.1 wants the config hashed, not just named. Only files that
    actually exist are hashed -- an `--out` path is an output, not an input."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "study.json")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{"material": "PLA"}')
        m = manifest.build("setup", [cfg, "--out", tmp], probe_external=False)

    paths = [i["path"] for i in m["inputs"]]
    assert os.path.abspath(cfg) in paths, "the study file was not hashed"
    entry = next(i for i in m["inputs"] if i["path"] == os.path.abspath(cfg))
    assert len(entry["sha256"]) == 64
    assert entry["bytes"] == 19
    assert tmp not in [p for p in paths if p == tmp], \
        "the output directory should not be recorded as an input"


def test_write_round_trips_and_records_the_verdict():
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest.write(tmp, "opt", ["s.json", "--out", tmp],
                              exit_code=1, seconds=2.0, probe_external=False)
        assert path and os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    assert m["run"]["exit_code"] == 1
    assert m["run"]["exit_code_meaning"] == "fail / invalid"
    assert m["schema"] == manifest.SCHEMA


def test_no_output_directory_means_no_manifest():
    """`topopt overhang part.stl` writes only to stdout. Dropping a manifest
    into the working directory would be litter, so none is written."""
    assert manifest.out_dir_from_argv(["part.stl"]) is None
    assert manifest.out_dir_from_argv(["s.json", "--out", "D"]) == "D"
    assert manifest.out_dir_from_argv(["s.json", "--out=D"]) == "D"


# ------------------------------------------------- `topopt run --model DIR`

def test_the_model_layout_still_gets_a_manifest():
    """`out_dir_from_argv` only knew `--out`, so `topopt run --model DIR` --
    the form meant for publishing -- was the one that recorded nothing at all,
    and the report's reproducibility block came out empty for exactly the runs
    intended to be cited."""
    from topopt.manifest import out_dir_from_argv
    got = out_dir_from_argv(["study.json", "--model", "tall_tripod"])
    assert got.replace("\\", "/") == "tall_tripod/03_optimization"
    got = out_dir_from_argv(["study.json", "--model=m"])
    assert got.replace("\\", "/") == "m/03_optimization"


def test_an_explicit_out_still_wins():
    from topopt.manifest import out_dir_from_argv
    assert out_dir_from_argv(["s.json", "--out", "here"]) == "here"


def test_a_command_that_writes_nothing_gets_no_manifest():
    """Dropping one into the working directory would be litter."""
    from topopt.manifest import out_dir_from_argv
    assert out_dir_from_argv(["part.stl"]) is None


# ------------------------------- a manifest that is not worth writing (19.6)

def test_a_manifest_missing_a_required_field_is_refused():
    """The rule has two halves. "Null with a note" is the honest half; the
    other half is that the *key* is still there -- a reader who does not find
    `random_seed` cannot tell "this run had no seed" from "this tool forgot"."""
    m = _built()
    for key in manifest.REQUIRED:
        doctored = {k: v for k, v in m.items() if k != key}
        with pytest.raises(manifest.IncompleteManifest) as exc:
            manifest.check_complete(doctored)
        assert key in str(exc.value)


def test_a_null_without_a_reason_is_refused():
    m = _built()
    m["random_seed"] = None
    m.pop("random_seed_note", None)
    with pytest.raises(manifest.IncompleteManifest):
        manifest.check_complete(m)
    m["random_seed_note"] = "no seed was declared by the study"
    assert manifest.check_complete(m) is m


def test_nothing_incomplete_reaches_the_disk(tmp_path, monkeypatch):
    """The guard is on `write`, not only on `build`: the point is the file."""
    bad = {k: v for k, v in _built().items() if k != "environment"}
    monkeypatch.setattr(manifest, "build", lambda *a, **k: bad)
    with pytest.raises(manifest.IncompleteManifest):
        manifest.write(str(tmp_path), "opt", ["s.json"], probe_external=False)
    assert not os.path.exists(os.path.join(str(tmp_path), "manifest.json"))


def test_the_writers_promise_and_the_readers_schema_still_agree():
    """`REQUIRED` here and `outschema.MANIFEST`'s required keys are written
    down twice on purpose -- one is what this module promises to write, the
    other is what a reader may rely on finding. This is the test that they
    have not drifted."""
    from topopt import outschema
    _kind, table, _by, _what = outschema._REGISTRY[manifest.SCHEMA]
    assert set(manifest.REQUIRED) == {
        k for k, rule in table.items() if rule.get("required")}

"""Regressions for reuse across parts, finished bodies and failed runs."""
import json

import pytest

from topopt.cli import report, run
from topopt.provenance import sha256_file


def test_documented_root_voxel_size_is_used():
    from topopt.loadcase import Study
    assert Study({"voxel_mm": 4}).resolution_mm == 4
    assert Study({"voxel_mm": 4, "resolution_mm": 2}).resolution_mm == 2


def test_bracket_template_uses_requested_grid():
    from pathlib import Path
    from topopt.loadcase import load_case_from_json
    from topopt.cli.setup import prepare
    path = Path(__file__).resolve().parents[2] / "examples/underwater_bracket/study.json"
    setup, validation = prepare(load_case_from_json(str(path)), verbose=False)
    assert validation["ok"]
    assert setup.h == 4


def test_resolved_section_is_not_mesh_convergence():
    data = {"fea": [{"checks": [{"name": "mesh resolution", "pass": True,
                                "got": 4, "required": 3}]}]}
    assert report._mesh_converged(data) is None
    data["fea"][0]["checks"][0]["pass"] = False
    assert report._mesh_converged(data)[0] is False


def test_good_percentile_cannot_hide_failed_worst_element():
    data = {"required_safety_factor": 3, "fea": [
        {"safety_factor_p99_5": 8, "min_safety_factor": 1.2}]}
    assert report._safety(data)[0] is False
    del data["fea"][0]["min_safety_factor"]
    assert report._safety(data) is None


def test_ccx_failure_and_missing_cases_do_not_pass():
    data = {"study_raw": {"cases": [{"name": "forward"}, {"name": "reverse"}]},
            "ccx": {"runs": [{"pass": True, "cases": [
                {"case": "forward", "pass": True}]}]}}
    assert report._independent_analysis(data) is None
    data["ccx"]["runs"][0]["cases"].append({"case": "reverse", "pass": False})
    assert report._independent_analysis(data)[0] is False


def test_finished_shape_requires_content_identity(tmp_path):
    mesh = tmp_path / "finished.stl"
    mesh.write_bytes(b"mesh A")
    data = {"extract": {"outputs": {"finish_stl": str(mesh)}},
            "fea": [{"case": "forward"}],
            "ccx": {"stl_sha256": sha256_file(mesh),
                    "runs": [{"cases": [{"case": "forward"}]}]}}
    assert report._reanalysed(data)[0] is True
    mesh.write_bytes(b"mesh B")
    assert report._reanalysed(data) is None


def test_missing_finished_mesh_does_not_fall_back_to_raw(tmp_path):
    raw = tmp_path / "raw.stl"
    raw.touch()
    (tmp_path / "demo_extract.json").write_text(json.dumps({"outputs": {
        "stl": str(raw), "finish_stl": str(tmp_path / "missing.stl")}}))
    assert run._chosen_stl(str(tmp_path), prefer_finished=True) is None
    assert run._chosen_stl(str(tmp_path)) == str(raw)


def test_failed_run_does_not_reuse_ccx_results(tmp_path):
    (tmp_path / "ccx_summary.json").write_text(json.dumps({"runs": [{"pass": True}]}))
    (tmp_path / "run_summary.json").write_text(json.dumps({"stages": [
        {"stage": "ccx", "exit_code": None, "skipped": True}]}))
    assert "ccx" not in report.collect(str(tmp_path))


@pytest.mark.parametrize("code", [2, 3])
def test_invalid_opt_stops_before_old_extract_can_be_used(tmp_path, monkeypatch, code):
    calls = []
    def fake_call(name, args):
        calls.append(name)
        return code, None
    monkeypatch.setattr(run, "_call", fake_call)
    summary = run.run("study.json", str(tmp_path), only=["opt", "extract"], quiet=True)
    assert summary["stopped_at"] == "opt"
    assert summary["valid_through"] == []
    assert calls == ["optimize"]


def test_run_passes_material_and_support_configuration(tmp_path, monkeypatch):
    study = tmp_path / "study.json"
    study.write_text(json.dumps({"material": "PETG", "build_axis": "Z",
                                "loads": [{"region": "all", "force": [1, 0, 0]}]}))
    mesh = tmp_path / "raw.stl"
    mesh.touch()
    (tmp_path / "study_extract.json").write_text(json.dumps({"outputs": {"stl": str(mesh)}}))
    calls = []
    monkeypatch.setattr(run, "find", lambda _: "installed")
    monkeypatch.setattr(run, "_call", lambda name, args: (calls.append((name, args)) or (0, None)))
    run.run(str(study), str(tmp_path), only=["slice"], supports="tree", layer_mm=0.16,
            bed_type="Textured PEI Plate", quiet=True)
    args = calls[0][1]
    assert args[args.index("--material") + 1] == "PETG"
    assert args[args.index("--supports") + 1] == "tree"
    assert args[args.index("--layer-mm") + 1] == "0.16"
    assert args[args.index("--orient") + 1] == "keep"
    assert args[args.index("--bed-type") + 1] == "Textured PEI Plate"


def test_default_ccx_analyses_finished_shape(tmp_path, monkeypatch):
    raw, finished = tmp_path / "raw.stl", tmp_path / "finished.stl"
    raw.touch(); finished.touch()
    (tmp_path / "demo_extract.json").write_text(json.dumps({"outputs": {
        "stl": str(raw), "finish_stl": str(finished)}}))
    calls = []
    monkeypatch.setattr(run, "find", lambda _: "installed")
    monkeypatch.setattr(run, "_call", lambda name, args: (calls.append(args) or (0, None)))
    run.run("study.json", str(tmp_path), only=["ccx"], quiet=True)
    assert calls[0][0] == str(finished)
    assert "--voxel-fea" not in calls[0]
    run.run("study.json", str(tmp_path), only=["ccx"], ccx_geometry="analysis", quiet=True)
    assert calls[1][0] == str(raw)


def test_isotropic_ccx_uses_the_legacy_voxel_failure_rule():
    import numpy as np
    from topopt.cli.ccx import _case_results
    from topopt.loadcase import Study
    from topopt.hex8 import layer_failure_index
    from topopt.materials import get_material
    study = Study({"material": "PLA", "build_axis": "Z"})
    sig = np.array([[10., 0, 0, 0, 0, 0], [0, 0, 5., 0, 0, 0]])
    expected = layer_failure_index(sig, get_material("PLA").sigma_xy,
                                   get_material("PLA").sigma_z, 2)
    step = {"displacement": {1: [0.01, 0, 0]},
            "stress": {1: sig[0], 2: sig[1]}, "strain": {}, "reaction": {}}
    case = {"fixed_nodes": [2], "loads": [(1, [1, 0, 0])], "load_points": [[1, 0, 0]]}
    result = {"nodes": {1: [1, 0, 0], 2: [0, 0, 0]}}
    mesh = {"xyz": np.array([[1., 0, 0], [0., 0, 0]]), "index": {1: 0, 2: 1}}
    c = {"required_safety_factor": 3.0}
    _case_results(c, case, step, result, mesh, None, study, 0,
                  {"reaction_totals": [[-1, 0, 0]]}, 0, 1240, 1)
    assert c["failure_criterion"] == "layer_two_constant"
    assert c["safety_factor_p99_5"] == pytest.approx(1 / np.percentile(expected, 99.5), abs=0.0005)
    assert c["worst_element_safety_factor"] == pytest.approx(1 / expected.max(), abs=0.0005)

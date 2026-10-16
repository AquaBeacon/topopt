"""Spec A3 / 15.2: `topopt fea --converge` runs three voxel sizes, not one.

`topopt fea` on its own solves a single voxel size and reports "N elements
across the thinnest section" -- a proxy for whether the answer would still
change on a finer mesh, not a check of it. `--converge` actually refines the
mesh (2H / H / H/2) and asks `ccx.mesh_convergence` whether the numbers moved.
These tests pin the two behaviours that make that trustworthy: the extra
output only appears when asked for, and it says three sizes were really
solved, worst-first, with a verdict per case.

Built from a plain box beam on purpose (not an osmo_tripod study): the point
here is what the CLI writes and how it decides pass/fail, not any particular
part's numbers, and a box solves in a couple of seconds at every size used.
"""

import json
import os

import numpy as np

from topopt.cli.fea import main
from topopt.meshout import _write_binary_stl


def _box_stl(path, lo, hi):
    """A watertight axis-aligned box, 12 triangles (reused from test_finish.py)."""
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    c = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]],
                  [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
                  [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
                  [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]])
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, d, e in quads:
        tris.append([c[a], c[b], c[d]])
        tris.append([c[a], c[d], c[e]])
    _write_binary_stl(path, np.asarray(tris))
    return path


def _write_study(tmp_path, stl_name="beam.stl"):
    """A 40x8x8 mm cantilever: fixed at the min-X face, loaded at the max-X
    face, exactly the `face` region syntax from `TEMPLATE` in cli/fea.py."""
    _box_stl(str(tmp_path / stl_name), [0, 0, 0], [40, 8, 8])
    study = {
        "part": stl_name,
        "material": "PLA",
        "build_axis": "Z",
        "target_elements": 4000,
        "safety_factor": 2.0,
        "max_deflection_mm": 5.0,
        "regions": {
            "mount": {"type": "face", "axis": "X", "side": "min", "depth": 3.0},
            "load": {"type": "face", "axis": "X", "side": "max", "depth": 3.0},
        },
        "cases": [{
            "name": "tip load",
            "supports": [{"region": "mount", "fix": "xyz"}],
            "loads": [{"region": "load", "force": [0, 0, -20.0]}],
        }],
    }
    p = tmp_path / "study.json"
    p.write_text(json.dumps(study), encoding="utf-8")
    return str(p)


def test_converge_writes_three_sizes_and_a_verdict_per_case(tmp_path):
    study_path = _write_study(tmp_path)
    out = tmp_path / "out"
    rc = main([study_path, "--converge", "--voxel", "2.0",
              "--out", str(out), "--no-png", "--quiet"])
    assert rc in (0, 1)          # a real pass/fail, not an exception

    conv_path = out / "beam_fea_convergence.json"
    assert conv_path.exists()
    with open(conv_path, encoding="utf-8") as f:
        conv = json.load(f)

    assert conv["schema"] == "topopt/fea-convergence/1"
    sizes = conv["voxel_mm"]
    assert len(sizes) == 3
    assert sizes[0] > sizes[1] > sizes[2]                # coarse -> fine
    assert sizes[1] == 2.0                                # H is --voxel

    assert "tip load" in conv["cases"]
    case = conv["cases"]["tip load"]
    assert len(case["runs"]) == 3
    verdict = case["verdict"]
    assert isinstance(verdict["pass"], bool)
    assert isinstance(conv["pass"], bool)
    assert conv["governing_case"] == "tip load"

    # Downstream `topopt report` reads the plain `_fea.json` of the finest
    # run; --converge must not stop writing it.
    fea_path = out / "beam_fea.json"
    assert fea_path.exists()
    with open(fea_path, encoding="utf-8") as f:
        fea = json.load(f)
    assert abs(fea["cases"][0]["voxel_mm"] - sizes[2]) < 1e-6


def test_converge_derives_h_when_voxel_is_not_given(tmp_path):
    study_path = _write_study(tmp_path)
    out = tmp_path / "out"
    main([study_path, "--converge", "--out", str(out), "--no-png", "--quiet"])

    with open(out / "beam_fea_convergence.json", encoding="utf-8") as f:
        conv = json.load(f)
    sizes = conv["voxel_mm"]
    assert sizes[0] > sizes[1] > sizes[2]
    # medium == 2x fine and 0.5x coarse, whatever H got derived to.
    assert abs(sizes[0] - 2 * sizes[1]) < 1e-9
    assert abs(sizes[2] - sizes[1] / 2) < 1e-9


def test_without_converge_no_convergence_file_is_written(tmp_path):
    study_path = _write_study(tmp_path)
    out = tmp_path / "out"
    main([study_path, "--voxel", "2.0", "--out", str(out), "--no-png",
         "--quiet"])

    assert (out / "beam_fea.json").exists()
    assert not (out / "beam_fea_convergence.json").exists()

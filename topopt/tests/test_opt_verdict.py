"""What `topopt opt` refuses to call a pass (spec 19.2, abnormal cases).

Two failures that used to leave no trace at all:

1. **A stiffness matrix with no unique solution.** Nothing held, everything
   held, or a loaded body that shares no node with any support. The old code
   raised a bare `ValueError` out of `prepare_case`, which reached the CLI
   wrapper as an unhandled exception: a traceback, no exit code, and a
   manifest marked `crashed`. It is a finding about the study, so it is
   exit 1 with one sentence -- and not exit 2, which means "the input could
   not be read" and this input read fine.

2. **A volume constraint that was not honoured.** The run reported
   `converged: false` and a volume well off `volfrac` and still exited 0,
   because the exit code was computed from connectivity and the final FEA
   only. Measured case that motivated it (AGENTS.md, the SF 6 stress
   experiment): volume 0.419 against a target of 0.300.

The verdict now lives in the summary as `checks` / `pass`, and the exit code
is that verdict rather than a second opinion computed next to it.
"""

import json
import os

import numpy as np
import pytest

from topopt import generative as gen
from topopt.cli import optimize as opt_cli
from topopt.solve import (Assembled, SingularStiffness, SolverDidNotConverge,
                          solve_cases)
from topopt.voxel import Grid


_ROOT = os.path.abspath(os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", ".."))


def _bar(nx=2, ny=1, nz=1, h=10.0):
    return Grid(np.ones((nx, ny, nz), bool), h, np.zeros(3))


def _empty_bcs(grid):
    n = tuple(s + 1 for s in grid.shape)
    return np.zeros(n + (3,), bool), np.zeros(n + (3,))


# ------------------------------------------------------- singular stiffness

def test_a_case_with_a_load_and_no_fixed_node_is_singular():
    """The two-voxel study whose only support was taken away."""
    grid = _bar()
    fixed, force = _empty_bcs(grid)
    force[-1, 0, 0, 2] = -10.0
    with pytest.raises(SingularStiffness) as exc:
        solve_cases(grid, 3000.0, 0.35, [(fixed, force)])
    assert "no supports" in str(exc.value)


def test_holding_every_dof_is_singular_too():
    grid = _bar()
    fixed, force = _empty_bcs(grid)
    fixed[...] = True
    force[-1, 0, 0, 2] = -10.0
    with pytest.raises(SingularStiffness):
        solve_cases(grid, 3000.0, 0.35, [(fixed, force)])


def test_a_load_on_a_body_nothing_holds_is_singular():
    """Two blocks with a gap between them: the supported one is fine, the
    loaded one is a free body, and CG on it wanders off along a rigid mode
    while reporting a perfectly plausible number for the other."""
    solid = np.zeros((5, 1, 1), bool)
    solid[[0, 1, 3, 4], 0, 0] = True
    grid = Grid(solid, 10.0, np.zeros(3))
    fixed, force = _empty_bcs(grid)
    fixed[0, :, :, :] = True           # hold the left block
    force[5, 0, 0, 2] = -10.0          # push the right one
    with pytest.raises(SingularStiffness) as exc:
        solve_cases(grid, 3000.0, 0.35, [(fixed, force)])
    assert "shares no node with any support" in str(exc.value)


def test_a_loaded_body_that_is_held_is_not_reported_as_adrift():
    """The counter-test: the same two blocks, both held, must still solve.
    Without this the check above passes by refusing everything."""
    solid = np.zeros((5, 1, 1), bool)
    solid[[0, 1, 3, 4], 0, 0] = True
    grid = Grid(solid, 10.0, np.zeros(3))
    fixed, force = _empty_bcs(grid)
    fixed[0, :, :, :] = True
    fixed[5, :, :, :] = True
    force[4, 0, 0, 2] = -10.0
    (res,) = solve_cases(grid, 3000.0, 0.35, [(fixed, force)])
    assert res.converged


def test_the_component_labels_are_computed_once_per_grid():
    """`Assembled` is rebuilt every optimiser iteration; labelling the domain
    every time would put a full connected-component pass on the hot path."""
    grid = _bar(nx=4)
    a = Assembled(grid, 3000.0, 0.35)
    first = a.node_components()
    b = Assembled(grid, 3000.0, 0.35)
    assert b.node_components() is first


# ------------------------------------------------------------- the exit code

SINGULAR_STUDY = {
    "_what": "Three cells, the outer two preserve, both of them clamped.",
    "units": "mm_N_MPa", "material": "PLA", "build_axis": "Z",
    "safety_factor": 2.0, "voxel_mm": 10.0,
    "design": {"volfrac": 0.5, "filter_mm": 10.0, "overhang": False},
    "geometry": {
        "voxel_mm": 10.0, "pad_voxels": 0,
        "design_space": {"type": "box", "min": [0, 0, 0], "max": [30, 10, 10]},
        "preserve": {
            "left": {"type": "box", "min": [0, 0, 0], "max": [10, 10, 10]},
            "right": {"type": "box", "min": [20, 0, 0], "max": [30, 10, 10]}},
    },
    "regions": {
        "everything": {"type": "box", "min": [-1, -1, -1], "max": [31, 11, 11]},
        "right": {"type": "box", "min": [20, 0, 0], "max": [30, 10, 10]}},
    "cases": [{"name": "held everywhere",
               "supports": [{"region": "everything", "fix": "xyz"}],
               "loads": [{"region": "right", "force": [0, 0, -40]}]}],
}


@pytest.mark.slow
def test_a_singular_study_exits_one_and_says_why(tmp_path):
    study = tmp_path / "singular_study.json"
    study.write_text(json.dumps(SINGULAR_STUDY), encoding="utf-8")
    out = tmp_path / "out"
    code = opt_cli.main([str(study), "--out", str(out), "--iters", "2",
                         "--quiet", "--force", "--solver", "cpu:jacobi"])
    # 1, not 2: the study parsed, validated and built a grid. What failed is
    # the physics it describes.
    assert code == 1
    with open(out / "singular_study.json", encoding="utf-8") as f:
        summary = json.load(f)
    assert summary["termination_reason"] == "singular_stiffness"
    assert summary["pass"] is False
    assert summary["checks"][0]["name"] == "solve"


def test_the_stopped_summary_carries_every_key_the_schema_requires(tmp_path):
    from topopt import outschema
    s = opt_cli.write_stopped_summary(
        str(tmp_path), "beam", "beam.json", {"ok": False}, {},
        "singular_stiffness", "no supports defined")
    problems = outschema.validate_output(s)
    assert not [p for p in problems if p.severity == "error"], \
        "\n".join(str(p) for p in problems)


# ------------------------------------------------------- volume constraint

def _summary(fraction, target=0.30, connected=True, fea=None, bad_solves=0):
    return {"connectivity": {"connected": connected, "components": 1,
                             "preserve_bodies": 2},
            "settings": {"volfrac_of_free_cells": target},
            "volumes": {"volume_fraction_of_free_cells": fraction},
            "solver": {"total_solves": 60,
                       "non_converged_solves": bad_solves},
            "final_fea": fea}


def _row(summary, name):
    return next(c for c in opt_cli.run_checks(summary) if c["name"] == name)


def test_a_run_that_overshot_its_volume_is_not_a_pass():
    """The SF 6 experiment's numbers: 0.419 of the free cells against a
    target of 0.300."""
    row = gen.volume_check(_summary(0.419)["volumes"], _summary(0.419)["settings"])
    assert row["pass"] is False
    assert "0.419" in row["got"] and "0.3000" in row["required"]
    assert not opt_cli.run_checks(_summary(0.419))[1]["pass"]


def test_the_tolerance_is_the_one_the_convergence_test_already_uses():
    """2 % of the target, and not a second number invented for the verdict:
    continuation moves the projected volume every time beta doubles."""
    assert gen.VOLUME_TOLERANCE == 0.02
    assert gen.volume_check(*_at(0.30 * 1.019))["pass"] is True
    assert gen.volume_check(*_at(0.30 * 1.021))["pass"] is False


def _at(fraction):
    s = _summary(fraction)
    return s["volumes"], s["settings"]


def test_coming_in_under_the_target_is_not_a_failure():
    """Light is the optimiser doing better than it was asked, not a fault."""
    assert gen.volume_check(*_at(0.10))["pass"] is True


def test_a_run_with_no_recorded_target_abstains():
    assert gen.volume_check({"volume_fraction_of_free_cells": 0.4}, {}) is None


def test_a_final_fea_that_fell_over_is_not_a_pass():
    """`all(s.get("pass", True) for s in fea if "error" not in s)` skipped
    exactly the entries saying the analysis never happened, so a crashed
    re-analysis came out of `topopt opt` as exit 0."""
    checks = opt_cli.run_checks(
        _summary(0.30, fea=[{"error": "solver blew up"}]))
    row = next(c for c in checks if c["name"] == "final FEA")
    assert row["pass"] is False and "blew up" in row["got"]


def test_cg_that_stopped_converging_mid_run_is_not_a_pass():
    """The first iteration failing stops the run outright; a run that starts
    well and degrades is allowed to finish, because by then there is a shape
    to look at -- but spec 19.2 still asks for exit code 1, so it is a row."""
    assert _row(_summary(0.30), "solver")["pass"] is True
    bad = _row(_summary(0.30, bad_solves=7), "solver")
    assert bad["pass"] is False
    assert "7 of 60" in bad["got"]


def test_the_recorded_runs_still_have_no_failed_solves():
    """The reason the row above can be a hard failure rather than a note: on
    this repo's two recorded runs it is 0 of 136 and 0 of 480."""
    import json
    for rel, total in (("osmo_tripod/out/gd_mount/gd_mount_study.json", 136),
                       ("tall_tripod/03_optimization/tall_tripod_study.json",
                        480)):
        path = os.path.join(_ROOT, *rel.split("/"))
        if not os.path.exists(path):
            pytest.skip("reference model not present next to the package")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        assert d["solver"]["non_converged_solves"] == 0
        assert d["solver"]["total_solves"] == total
        assert _row(d, "solver")["pass"] is True
        assert _row(d, "volume constraint")["pass"] is True


def test_a_disconnected_result_still_fails():
    checks = opt_cli.run_checks(_summary(0.30, connected=False))
    assert checks[0]["name"] == "connectivity" and checks[0]["pass"] is False

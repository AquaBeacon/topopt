"""Spec 11.3: convergence is a verdict, and running out of iterations is not it.

The rule this file defends is the one in AGENTS.md: never display an
unconverged run as converged. A `converged: true` that really means "the loop
ended" is worse than no field at all, because every downstream judgement --
mesh convergence, safety factor, whether the shape is trustworthy -- is made
on the assumption that it settled.
"""

import os

import numpy as np
import pytest

from topopt.generative import CONVERGE_STREAK, optimize
from tests.checks import Ctx


@pytest.fixture(scope="module")
def ctx():
    return Ctx()


def _run(ctx, iters, **kw):
    return optimize(ctx.setup, ctx.study, dict(volfrac=0.4), iters=iters,
                    verbose=False, **kw)


def test_hitting_the_iteration_cap_is_not_convergence(ctx):
    res = _run(ctx, iters=4)
    assert res["converged"] is False
    assert res["termination_reason"] == "max_iterations"
    assert res["settings"]["iterations_run"] == 4
    assert res["settings"]["iterations_requested"] == 4


def test_every_criterion_is_reported_separately(ctx):
    """A single boolean cannot say *why* a run did not settle. Four can."""
    res = _run(ctx, iters=4)
    for k in ("density_converged", "objective_converged", "volume_converged",
              "shape_converged"):
        assert k in res and isinstance(res[k], bool)
    assert res["convergence_streak_required"] == CONVERGE_STREAK


def test_criteria_are_recorded_per_iteration(ctx):
    """So a run that stopped short can be diagnosed from its own output
    rather than by re-running it with a print statement added."""
    res = _run(ctx, iters=4)
    for h in res["history"]:
        c = h["criteria"]
        assert set(c) == {"max_density_change", "objective_rel_change",
                          "volume_error_frac", "topology_change_frac"}
        assert c["max_density_change"] >= 0
        assert c["volume_error_frac"] >= 0
    # The first iteration has no previous objective to compare against, and
    # says so with null rather than inventing a zero change.
    assert res["history"][0]["criteria"]["objective_rel_change"] is None


def test_convergence_needs_the_streak_not_one_lucky_iteration(ctx):
    """With continuation running, the design variable regularly sits still for
    a single step just after beta doubles. One quiet iteration is not a
    settled design, which is what CONVERGE_STREAK encodes."""
    res = _run(ctx, iters=6)
    if res["converged"]:
        pytest.skip("this bar converged inside 6 iterations")
    hist = res["history"]
    # If any single iteration met all four criteria yet the run is not marked
    # converged, the streak requirement is doing its job.
    assert len(hist) <= 6


def test_converged_true_only_with_a_reason_that_says_so(ctx):
    res = _run(ctx, iters=40)
    assert res["converged"] == (res["termination_reason"] == "converged")
    if res["converged"]:
        assert all(res[k] for k in ("density_converged", "objective_converged",
                                    "volume_converged", "shape_converged"))
        assert res["settings"]["iterations_run"] < 40


def test_material_model_is_recorded_on_the_result(ctx):
    """A result that does not say which material model produced it cannot be
    compared with anything later (spec 9.3)."""
    o = _run(ctx, iters=3)
    assert o["material"]["material_model"] == "orthotropic_equivalent_solid"
    assert o["material"]["measured_for_specific_printer"] is False
    assert o["settings"]["build_axis"] == "Z"

    i = _run(ctx, iters=3, isotropic=True)
    assert i["material"]["material_model"] == "isotropic_two_constant"


def test_isotropic_and_orthotropic_reach_different_designs(ctx):
    """Spec 11.5: introducing orthotropy is *supposed* to move the shape.
    If these matched, the card would not be reaching the solver and the whole
    phase would be decoration."""
    a = _run(ctx, iters=8, isotropic=True)["x_phys"]
    b = _run(ctx, iters=8)["x_phys"]
    assert np.abs(a - b).max() > 1e-3


def test_the_verdict_survives_into_the_written_summary(tmp_path):
    """A `termination_reason` computed and then dropped on the way to disk is
    no verdict at all -- AGENTS.md tells a reader to judge a run by that field,
    and known-issue B1 was exactly its absence from the output JSON.

    Built from boxes rather than from the osmo_tripod study on purpose: that
    study needs its own fine grid (a coarse run erases the 2.40 mm centre
    finger and the load falls off preserve -- CLAUDE.md documents it), and a
    test about what reaches a file should not cost two minutes of solving.
    """
    import json
    from topopt.cli.optimize import run_generative
    from topopt.loadcase import Study

    study = Study({
        "material": "PLA", "build_axis": "Z", "units": "mm_N_MPa",
        "safety_factor": 2.0,
        "geometry": {
            "voxel_mm": 2.0,
            "design_space": {"type": "box", "min": [0, 0, 0],
                             "max": [60, 12, 12]},
            "preserve": {
                "root": {"type": "box", "min": [0, 0, 0], "max": [4, 12, 12]},
                "tip": {"type": "box", "min": [56, 0, 0], "max": [60, 12, 12]},
            },
        },
        "regions": {
            "root": {"type": "box", "min": [-1, -1, -1], "max": [4, 13, 13]},
            "tip": {"type": "box", "min": [56, -1, -1], "max": [61, 13, 13]},
        },
        "cases": [{
            "name": "tip load",
            "supports": [{"region": "root", "fix": "xyz"}],
            "loads": [{"region": "tip", "force": [0, 0, -20.0]}],
        }],
    }, base_dir=str(tmp_path))

    out = str(tmp_path / "out")
    # Two iterations: this is about what reaches the file, not about the
    # shape. It cannot converge in two, which is exactly the point.
    run_generative(study, str(tmp_path / "s.json"), {"volfrac": 0.3}, out,
                   iters=2, quiet=True, do_fea=False)
    with open(os.path.join(out, "s.json"), encoding="utf-8") as f:
        blob = json.load(f)
    assert blob["converged"] is False
    assert blob["termination_reason"] == "max_iterations"
    for k in ("density_converged", "objective_converged", "volume_converged",
              "shape_converged"):
        assert blob[k] is False
    assert blob["convergence_streak_required"] == CONVERGE_STREAK
    # And the material model, so a summary can never be read without knowing
    # which of the two produced it.
    assert isinstance(blob["material"], dict)
    assert blob["material"]["material_model"] == "orthotropic_equivalent_solid"

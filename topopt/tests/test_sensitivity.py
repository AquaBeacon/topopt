"""Finite-difference check of the optimiser's sensitivity (spec 22.1).

The SIMP sensitivity is the one number in the optimiser that no output ever
displays, so a wrong sign or a missing factor does not fail loudly -- the run
just converges somewhere worse and still prints a shape. These tests differ
the *shipped* `objective_and_sensitivity`, not a copy of its formula.

Central differences with a step of 1e-4 on the physical density field: the
truncation error is O(h^2) ~ 1e-8 while the CG tolerance leaks ~1e-6 into each
compliance, so agreement to ~1e-4 relative is as tight as this can honestly be
asked to be, and a chain-rule error is orders of magnitude larger than that.
"""

import numpy as np
import pytest

from topopt.generative import objective_and_sensitivity
from topopt.loadcase import Study, build_bcs
from topopt.materials import get_material, material_model
from topopt.voxel import Grid


def _problem(nx=6, ny=3, nz=3, h=1.0):
    grid = Grid(solid=np.ones((nx, ny, nz), bool), h=h, origin=np.zeros(3))
    lo, hi = grid.origin, grid.origin + np.array(grid.solid.shape) * h
    pad = 0.1 * h
    study = Study({
        "material": "PLA",
        "build_axis": "Z",
        "regions": {
            "root": {"type": "box",
                     "min": [lo[0] - pad, lo[1] - pad, lo[2] - pad],
                     "max": [lo[0] + 0.5 * h + pad, hi[1] + pad, hi[2] + pad]},
            "tip": {"type": "box",
                    "min": [hi[0] - 0.5 * h - pad, lo[1] - pad, lo[2] - pad],
                    "max": [hi[0] + pad, hi[1] + pad, hi[2] + pad]},
        },
        "cases": [{
            "name": "tip load",
            "supports": [{"region": "root", "fix": "xyz"}],
            "loads": [{"region": "tip", "force": [0, 0, -50.0]}],
        }],
    }, base_dir=".")
    mat = get_material("PLA")
    bcs = [build_bcs(grid, c, study.regions, mat, study.infill, study.walls)
           for c in study.cases]
    return grid, study, mat, bcs


def _fd_check(isotropic, penal, seed=0, cells=6, step=1e-4):
    grid, study, mat, bcs = _problem()
    if isotropic:
        ortho = None
    else:
        _, ortho, _ = material_model("PLA")
    E = mat.E_effective(across_layers=False)

    rng = np.random.default_rng(seed)
    # A field away from 0 and 1: the SIMP derivative is singular at rho = 0 and
    # the move limits keep a real run off the bounds anyway.
    x = rng.uniform(0.35, 0.85, size=grid.solid.shape)

    c_ref = [None] * len(bcs)
    _, _, dc = objective_and_sensitivity(grid, bcs, x, penal, E, mat.nu,
                                         ortho, study, c_ref)
    # c_ref is now fixed, so the perturbed objectives below use the same
    # normalisation as the gradient -- otherwise the finite difference would
    # be measuring a moving target.
    ref = list(c_ref)

    idx = [tuple(i) for i in
           rng.integers(0, grid.solid.shape, size=(cells, 3))]
    worst = 0.0
    for cell in idx:
        xp, xm = x.copy(), x.copy()
        xp[cell] += step
        xm[cell] -= step
        op = objective_and_sensitivity(grid, bcs, xp, penal, E, mat.nu,
                                       ortho, study, list(ref))[0]
        om = objective_and_sensitivity(grid, bcs, xm, penal, E, mat.nu,
                                       ortho, study, list(ref))[0]
        fd = (op - om) / (2 * step)
        # `dc` is already the gradient of the same normalised objective the
        # perturbed runs report, because `ref` pins c_ref for all three calls.
        an = dc[cell]
        # A cell the load never reaches (every node fixed) has an exact zero
        # sensitivity; the finite difference there is solver rounding noise
        # (1e-11 seen from a GPU Cholesky), and its sign means nothing.
        scale = float(np.abs(dc).max())
        if abs(fd) < 1e-8 * scale and abs(an) < 1e-8 * scale:
            continue
        rel = abs(fd - an) / max(abs(fd), 1e-12)
        worst = max(worst, rel)
        assert np.sign(fd) == np.sign(an), (
            "sensitivity sign is wrong at %s: FD %.6e, analytic %.6e"
            % (cell, fd, an))
        assert rel < 2e-3, (
            "sensitivity at %s: FD %.6e vs analytic %.6e (%.2e relative)"
            % (cell, fd, an, rel))
    return worst


@pytest.mark.parametrize("penal", [1.0, 2.0, 3.0])
def test_isotropic_sensitivity_matches_finite_differences(penal):
    _fd_check(isotropic=True, penal=penal)


@pytest.mark.parametrize("penal", [1.0, 2.0, 3.0])
def test_orthotropic_sensitivity_matches_finite_differences(penal):
    """The chain rule is unchanged by anisotropy -- SIMP still scales one
    scalar per element -- and this is what says so rather than assuming it."""
    _fd_check(isotropic=False, penal=penal)


def test_compliance_sensitivity_is_negative_everywhere():
    """Adding material can only stiffen a compliance-minimisation problem, so
    every d(compliance)/d(rho) must be <= 0. A positive entry means the OC
    update would be told to remove material where it is carrying load."""
    grid, study, mat, bcs = _problem()
    _, ortho, _ = material_model("PLA")
    x = np.full(grid.solid.shape, 0.6)
    _, _, dc = objective_and_sensitivity(
        grid, bcs, x, 3.0, mat.E_effective(across_layers=False), mat.nu,
        ortho, study, [None] * len(bcs))
    assert dc.max() <= 0.0


def test_orthotropic_and_isotropic_sensitivities_differ():
    """If they agreed, the orthotropic D would not be reaching the element and
    every test above would be checking the isotropic path twice."""
    grid, study, mat, bcs = _problem()
    _, ortho, _ = material_model("PLA")
    E = mat.E_effective(across_layers=False)
    x = np.full(grid.solid.shape, 0.6)
    _, _, di = objective_and_sensitivity(grid, bcs, x, 3.0, E, mat.nu, None,
                                         study, [None] * len(bcs))
    _, _, do = objective_and_sensitivity(grid, bcs, x, 3.0, E, mat.nu, ortho,
                                         study, [None] * len(bcs))
    rel = np.abs(do - di).max() / max(np.abs(di).max(), 1e-12)
    assert rel > 1e-3, "orthotropic D is not reaching the element"

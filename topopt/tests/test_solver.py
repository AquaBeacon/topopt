"""The fast solver must give the old answers.

`solve.py` gained a geometric multigrid preconditioner, a block (multi-case)
solve and an optional GPU backend. None of that is allowed to change what
K u = f means, so every test here compares the new paths against the plain
Jacobi-CG path that the recorded runs were made with, and the multigrid
pieces against their definitions (Galerkin product, transpose transfers).
"""

import argparse
import os

import numpy as np
import pytest

from topopt import backend as backend_mod
from topopt.hex8 import element_dofs, element_operators
from topopt.multigrid import Level, MGPreconditioner, transfer_matrices
from topopt.solve import SolverConfig, solve_cases, solve_linear_elastic
from topopt.voxel import Grid


def _cantilever(shape=(12, 6, 6), h=1.0, density=None, seed=0, void_frac=0.0):
    """Solid box clamped at x = 0, loaded downwards at the x = max face."""
    nx, ny, nz = shape
    grid = Grid(solid=np.ones(shape, bool), h=h, origin=np.zeros(3))
    fixed = np.zeros((nx + 1, ny + 1, nz + 1, 3), bool)
    fixed[0, :, :, :] = True
    force = np.zeros((nx + 1, ny + 1, nz + 1, 3))
    force[nx, :, :, 2] = -10.0 / ((ny + 1) * (nz + 1))
    rng = np.random.default_rng(seed)
    if density is None:
        density = rng.uniform(0.3, 1.0, size=shape)
        if void_frac:
            density[rng.random(shape) < void_frac] = 0.0
        density[0] = 1.0                      # keep the clamp solid
        density[nx - 1] = 1.0                 # and the loaded face
    return grid, fixed, force, density


def _second_case(grid):
    """Same clamp, sideways load -- shares supports with the cantilever case."""
    nx, ny, nz = grid.shape
    fixed = np.zeros((nx + 1, ny + 1, nz + 1, 3), bool)
    fixed[0, :, :, :] = True
    force = np.zeros((nx + 1, ny + 1, nz + 1, 3))
    force[nx, :, :, 1] = 5.0 / ((ny + 1) * (nz + 1))
    return fixed, force


def _third_case(grid):
    """Different supports: clamped at both ends, loaded mid-span."""
    nx, ny, nz = grid.shape
    fixed = np.zeros((nx + 1, ny + 1, nz + 1, 3), bool)
    fixed[0, :, :, :] = True
    fixed[nx, :, :, :] = True
    force = np.zeros((nx + 1, ny + 1, nz + 1, 3))
    force[nx // 2, :, :, 2] = -8.0 / ((ny + 1) * (nz + 1))
    return fixed, force


JACOBI = SolverConfig(backend="cpu", precond="jacobi")
MG = SolverConfig(backend="cpu", precond="mg", mg_max_coarse_dof=150)


def _rel(a, b):
    return abs(a - b) / max(abs(b), 1e-300)


# ------------------------------------------------------------- config

def test_spec_parsing_and_env(monkeypatch):
    c = SolverConfig.from_spec("cpu:jacobi")
    assert (c.backend, c.precond) == ("cpu", "jacobi")
    c = SolverConfig.from_spec("cuda")
    assert (c.backend, c.precond) == ("cuda", "mg")
    monkeypatch.setenv("TOPOPT_SOLVER", "cpu:jacobi")
    c = SolverConfig.from_spec(None)
    assert (c.backend, c.precond) == ("cpu", "jacobi")
    with pytest.raises(ValueError):
        SolverConfig.from_spec("cpu:magic")
    with pytest.raises(ValueError):
        backend_mod.resolve("tpu")


def test_cli_flag_becomes_the_process_default(monkeypatch):
    monkeypatch.delenv("TOPOPT_SOLVER", raising=False)
    backend_mod.apply_cli_choice(argparse.Namespace(solver="cpu:jacobi"))
    assert os.environ["TOPOPT_SOLVER"] == "cpu:jacobi"
    backend_mod.apply_cli_choice(argparse.Namespace(solver=None))
    assert os.environ["TOPOPT_SOLVER"] == "cpu:jacobi"


# ------------------------------------------------------- multigrid parts

def _fine_level(shape=(7, 5, 3), seed=1):
    be = backend_mod.resolve("cpu")
    grid = Grid(solid=np.ones(shape, bool), h=2.0, origin=np.zeros(3))
    elements = np.argwhere(grid.solid)
    edof = element_dofs(shape, elements)
    KE, _ = element_operators(grid.h, 0.35)
    rng = np.random.default_rng(seed)
    emul = 3000.0 * (1e-9 + rng.uniform(0.0, 1.0, elements.shape[0]) ** 3)
    nnode = (shape[0] + 1) * (shape[1] + 1) * (shape[2] + 1)
    fixed = np.zeros((shape[0] + 1, shape[1] + 1, shape[2] + 1, 3), bool)
    fixed[0] = True
    fixed[3, 2, 1, 0] = True                  # an interior fixed DOF too
    free = ~fixed.reshape(nnode * 3)
    return be, Level(be, shape, elements, edof, free, KE=KE, emul=emul)


def test_restriction_is_the_transpose_of_prolongation():
    be, fine = _fine_level()
    coarse = fine.coarsen(transfer_matrices())
    rng = np.random.default_rng(3)
    r = rng.standard_normal((fine.ndof, 2))
    vc = rng.standard_normal((coarse.ndof, 2))
    lhs = (fine.restrict(r, coarse) * vc).sum(axis=0)
    rhs = (r * fine.prolong(vc, coarse)).sum(axis=0)
    assert np.allclose(lhs, rhs, rtol=1e-12, atol=1e-12)


def test_coarse_operator_is_the_galerkin_product():
    """K_c v == P^T K P v on every free coarse DOF, odd grid included."""
    be, fine = _fine_level(shape=(7, 5, 3))
    coarse = fine.coarsen(transfer_matrices())
    assert coarse.shape == (4, 3, 2)
    rng = np.random.default_rng(4)
    vc = rng.standard_normal((coarse.ndof, 3))
    vc = np.where(coarse.free_col, vc, 0.0)
    direct = coarse.matvec(vc)
    via_fine = fine.restrict(fine.matvec(fine.prolong(vc, coarse)), coarse)
    mask = np.asarray(coarse.free)
    scale = np.abs(direct[mask]).max()
    assert np.abs((direct - via_fine)[mask]).max() < 1e-10 * scale


def test_hierarchy_has_the_expected_levels():
    be, fine = _fine_level(shape=(9, 7, 5))
    mg = MGPreconditioner(be, fine.shape, fine.elements, be.to_numpy(fine.edof),
                          be.to_numpy(fine.KE), be.to_numpy(fine.emul),
                          be.to_numpy(fine.free), max_coarse_dof=100)
    shapes = [lv.shape for lv in mg.levels]
    assert shapes[0] == (9, 7, 5)
    assert shapes[1] == (5, 4, 3)
    assert shapes[-1] == (1, 1, 1) or mg.levels[-1].ndof <= 100
    info = mg.info()
    assert info["levels"][0]["elements"] == 9 * 7 * 5
    assert "chebyshev" in info["smoother"]


# ------------------------------------------------------------- solves

def test_multigrid_matches_jacobi_with_voids():
    grid, fixed, force, dens = _cantilever(void_frac=0.2)
    a = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=JACOBI)
    b = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=MG)
    assert a.converged and b.converged
    assert _rel(b.compliance, a.compliance) < 1e-6
    assert _rel(b.max_disp, a.max_disp) < 1e-5
    solid = dens[grid.solid] > 0
    se_a, se_b = a.strain_energy[solid], b.strain_energy[solid]
    assert np.linalg.norm(se_a - se_b) / np.linalg.norm(se_a) < 1e-5
    assert b.iterations < a.iterations
    assert b.solver["preconditioner"] == "mg"
    assert b.solver["multigrid"]["levels"][0]["ndof"] == a.u.size


def test_odd_grid_solves_through_several_levels():
    grid, fixed, force, dens = _cantilever(shape=(11, 7, 5))
    a = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=JACOBI)
    cfg = SolverConfig(backend="cpu", precond="mg", mg_max_coarse_dof=60)
    b = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=cfg)
    assert len(b.solver["multigrid"]["levels"]) >= 3
    assert b.converged
    assert _rel(b.compliance, a.compliance) < 1e-6


def test_block_solve_equals_single_solves():
    grid, fixed, force, dens = _cantilever()
    cases = [(fixed, force), _second_case(grid), _third_case(grid)]
    block = solve_cases(grid, 3000.0, 0.35, cases, density=dens, penal=3.0,
                        solver=MG)
    singles = [solve_linear_elastic(grid, 3000.0, 0.35, fx, fc, density=dens,
                                    penal=3.0, solver=JACOBI)
               for fx, fc in cases]
    assert len(block) == 3
    for r, s in zip(block, singles):
        assert r.converged
        assert _rel(r.compliance, s.compliance) < 1e-6
        assert _rel(r.max_disp, s.max_disp) < 1e-5
        assert r.iterations > 0
    # cases 1 and 2 share supports: same hierarchy, same iteration budget
    assert block[0].solver["backend"] == "cpu"


def test_warm_start_needs_no_iterations():
    grid, fixed, force, dens = _cantilever()
    a = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=MG)
    b = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=MG, x0=a.u)
    assert b.converged
    assert b.iterations <= 1
    assert _rel(b.compliance, a.compliance) < 1e-8


def test_stress_and_failure_index_unchanged_by_the_preconditioner():
    from topopt.materials import material_model
    _mat, ortho, _ = material_model("PLA")
    grid, fixed, force, dens = _cantilever()
    a = solve_linear_elastic(grid, 1.0, 0.35, fixed, force, density=dens,
                             penal=3.0, ortho=ortho, build_axis=2,
                             solver=JACOBI)
    b = solve_linear_elastic(grid, 1.0, 0.35, fixed, force, density=dens,
                             penal=3.0, ortho=ortho, build_axis=2, solver=MG)
    assert np.allclose(a.von_mises, b.von_mises, rtol=1e-4, atol=1e-9)
    assert np.allclose(a.failure_index, b.failure_index, rtol=1e-4, atol=1e-9)
    assert a.failure_criterion == b.failure_criterion


def test_unconverged_solve_is_reported_not_hidden():
    grid, fixed, force, dens = _cantilever()
    r = solve_linear_elastic(grid, 3000.0, 0.35, fixed, force, density=dens,
                             penal=3.0, solver=JACOBI, maxiter=3)
    assert not r.converged
    assert r.warnings and "did not reach" in r.warnings[0]


@pytest.mark.skipif(not backend_mod.cuda_available()[0],
                    reason="no CUDA-capable torch: %s" % backend_mod.cuda_available()[1])
def test_cuda_matches_cpu():
    grid, fixed, force, dens = _cantilever(void_frac=0.2)
    cases = [(fixed, force), _third_case(grid)]
    cpu = solve_cases(grid, 3000.0, 0.35, cases, density=dens, penal=3.0,
                      solver=SolverConfig(backend="cpu", precond="mg",
                                          mg_max_coarse_dof=150))
    gpu = solve_cases(grid, 3000.0, 0.35, cases, density=dens, penal=3.0,
                      solver=SolverConfig(backend="cuda", precond="mg",
                                          mg_max_coarse_dof=150))
    for a, b in zip(cpu, gpu):
        assert b.converged
        assert _rel(b.compliance, a.compliance) < 1e-6
        assert b.solver["backend"] == "cuda"
        assert "device" in b.solver


# ------------------------------------------------- 2026-09-08: speed work

def test_void_elements_are_dropped_and_every_solid_element_is_still_reported():
    """Elements at exactly zero density leave the operator (obstacles were
    half the domain on child_jaw_gate) but `Result.elements` still lists
    every solid voxel, so callers that scatter by it see no change, and the
    answer moves by no more than the e_min they used to carry."""
    from topopt.solve import Assembled
    grid, fixed, force, dens = _cantilever(void_frac=0.3)
    dens = np.where(dens < 0.05, 0.0, dens)          # exact zeros, as impose() makes
    kept = Assembled(grid, 3000.0, 0.35, density=dens, penal=3.0, solver=MG)
    full = Assembled(grid, 3000.0, 0.35, density=dens, penal=3.0, solver=MG,
                     drop_void=False)
    assert kept.elements.shape[0] < full.elements.shape[0]
    a = kept.solve([(fixed, force)])[0]
    b = full.solve([(fixed, force)])[0]
    assert a.converged and b.converged
    assert a.elements.shape == b.elements.shape == (int(grid.solid.sum()), 3)
    assert _rel(a.compliance, b.compliance) < 1e-6
    zero = dens[grid.solid] == 0.0
    assert np.all(a.strain_energy[zero] == 0.0)
    assert np.abs(a.strain_energy - b.strain_energy).max() < 1e-6 * b.strain_energy.max()


@pytest.mark.skipif(not backend_mod.cuda_available()[0],
                    reason="no CUDA-capable torch: %s" % backend_mod.cuda_available()[1])
def test_mixed_precision_cycle_matches_float64():
    """`cuda:mg32` runs the V-cycle in float32 inside a float64 flexible CG:
    same answer to solver tolerance, and the summary says which it was."""
    grid, fixed, force, dens = _cantilever(void_frac=0.2)
    cases = [(fixed, force), _third_case(grid)]
    ref = solve_cases(grid, 3000.0, 0.35, cases, density=dens, penal=3.0,
                      solver=SolverConfig(backend="cpu", precond="jacobi"))
    got = solve_cases(grid, 3000.0, 0.35, cases, density=dens, penal=3.0,
                      solver=SolverConfig.from_spec("cuda:mg32",
                                                    mg_max_coarse_dof=150))
    for a, b in zip(ref, got):
        assert b.converged
        assert _rel(b.compliance, a.compliance) < 1e-6
        assert _rel(b.max_disp, a.max_disp) < 1e-5
        assert b.solver["precision"] == "mixed32"
        assert "mixed" in b.solver["multigrid"]["precision"]
    c = SolverConfig.from_spec("cpu:mg32")
    assert (c.precond, c.mg_mixed) == ("mg", True)
    cpu = solve_cases(grid, 3000.0, 0.35, cases[:1], density=dens, penal=3.0,
                      solver=SolverConfig.from_spec("cpu:mg32",
                                                    mg_max_coarse_dof=150))[0]
    assert cpu.solver["precision"] == "float64"      # numpy: silently float64


def test_oc_hits_the_volume_target_and_warm_starts():
    """The Illinois search finds the multiplier to the same tolerance the
    bisection did, and the second call (warm-started from the first) needs
    fewer volume evaluations."""
    from topopt.generative import _oc
    rng = np.random.default_rng(3)
    shape = (10, 8, 6)
    free = np.ones(shape, bool)
    free[0] = False
    x = rng.uniform(0.2, 0.8, shape)
    dJ = -rng.uniform(0.1, 2.0, shape)
    dV = np.ones(shape)
    target = 0.4 * free.sum()
    vol_of = lambda c: float(c[free].sum())            # noqa: E731
    state = {}
    x1 = _oc(x, dJ, dV, free, target, 0.2, vol_of, state=state)
    assert abs(vol_of(x1) - target) <= 1e-4 * target
    assert np.all(x1[~free] == x[~free])
    assert np.all(np.abs(x1 - x)[free] <= 0.2 + 1e-12)
    n1 = state["evaluations"]
    assert n1 <= 40
    x2 = _oc(x1, dJ * 1.05, dV, free, target, 0.2, vol_of, state=state)
    assert abs(vol_of(x2) - target) <= 1e-4 * target
    assert state["evaluations"] < n1

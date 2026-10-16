"""The stress constraint must be the failure rule the final FEA uses, and
its adjoint gradient must be the derivative of the number it reports.

Same policy as `test_sensitivity.py`: central differences against the
*shipped* `StressConstraint.evaluate`, on the physical density field, with
the adaptive scale frozen so the finite difference sees a fixed function.
"""

import numpy as np
import pytest

from topopt.materials import get_material, material_model
from topopt.solve import Assembled, solve_cases
from topopt.stressconstraint import StressConstraint, mode_vectors
from topopt.hex8 import max_stress_failure_index

from test_sensitivity import _problem


def _constraint(samples="all", sf=2.0, q=0.5, P=8.0):
    grid, study, mat, bcs = _problem()
    _, ortho, _ = material_model("PLA")
    E = mat.E_effective(across_layers=False)
    sc = StressConstraint(ortho, study.build_axis, study.raster_axis, sf,
                          p_norm=P, relax_q=q, samples=samples)
    return grid, study, mat, bcs, ortho, E, sc


def _value(grid, mat, bcs, ortho, E, sc, x_phys, penal, scale, study):
    asm = Assembled(grid, E, mat.nu, density=x_phys, penal=penal, ortho=ortho,
                    build_axis=study.build_axis, raster_axis=study.raster_axis,
                    solver="cpu:jacobi")
    rs = asm.solve(bcs, want_stress=False, rtol=1e-10)
    return sc.evaluate(asm, rs, bcs, scale=scale)[0]


@pytest.mark.parametrize("samples", ["centroid", "all"])
@pytest.mark.parametrize("penal", [1.0, 3.0])
def test_adjoint_gradient_matches_finite_differences(samples, penal):
    grid, study, mat, bcs, ortho, E, sc = _constraint(samples=samples)
    rng = np.random.default_rng(1)
    x = rng.uniform(0.3, 1.0, size=grid.shape)
    base = _value(grid, mat, bcs, ortho, E, sc, x, penal, [1.0], study)
    grad = base["grad"]
    assert base["max_ratio"] > 0
    cells = [tuple(c) for c in rng.integers(0, grid.shape, size=(6, 3))]
    step = 1e-4
    for c in cells:
        xp = x.copy()
        xp[c] += step
        xm = x.copy()
        xm[c] -= step
        fp = _value(grid, mat, bcs, ortho, E, sc, xp, penal, [1.0], study)["value"]
        fm = _value(grid, mat, bcs, ortho, E, sc, xm, penal, [1.0], study)["value"]
        fd = (fp - fm) / (2 * step)
        assert abs(fd - grad[c]) <= 2e-4 * max(abs(fd), abs(grad[c]), 1e-3), \
            (c, fd, grad[c])


def test_max_ratio_is_the_final_fea_failure_index_on_a_solid():
    # On a fully solid field rho^q = 1 and the relaxed index is the plain
    # max-stress index `solve_cases` reports (worst of the 9 samples).
    grid, study, mat, bcs, ortho, E, sc = _constraint(samples="all", sf=3.0)
    x = np.ones(grid.shape)
    asm = Assembled(grid, E, mat.nu, density=x, penal=3.0, ortho=ortho,
                    build_axis=study.build_axis, raster_axis=study.raster_axis,
                    solver="cpu:jacobi")
    rs = asm.solve(bcs, want_stress=True, rtol=1e-10)
    got = sc.evaluate(asm, rs, bcs)[0]
    fi = rs[0].failure_index.max()
    assert abs(got["max_ratio"] - 3.0 * fi) <= 1e-6 * max(1.0, 3.0 * fi)
    # The p-norm over-reads the max; the scale makes c G equal the max on
    # the first call.
    assert abs(got["scale"] * got["pnorm"] - got["max_ratio"]) < 1e-9


def test_mode_vectors_are_one_sided_per_component():
    _, ortho, _ = material_model("PLA")
    W = mode_vectors(ortho)
    s = np.array([[10.0, -5.0, 3.0, 2.0, -1.0, 0.5]])
    idx = np.maximum(0.0, s @ W.T)              # (1, 12)
    # Compare with the shipped rule, in material axes (identity rotation).
    fi = max_stress_failure_index(s, ortho, build_axis=2, raster_axis=0)
    assert abs(idx.max() - fi[0]) < 1e-12
    # Tension and compression rows of one axis are never both active.
    for a in range(0, 12, 2):
        assert not (idx[0, a] > 0 and idx[0, a + 1] > 0)


def test_isotropic_model_is_refused():
    with pytest.raises(ValueError):
        StressConstraint(None, 2, None, 2.0)


# ---------------------------------------------------------------- end to end

def _cantilever(nx=30, ny=10, nz=6, load=-8.0):
    from topopt.voxel import Grid
    from topopt.designspace import Setup
    from checks import build_case
    grid = Grid(np.ones((nx, ny, nz), bool), 1.0, np.zeros(3))
    study = build_case(grid, tip_force=load)
    preserve = np.zeros(grid.shape, bool)
    preserve[:2] = True
    preserve[nx - 2:] = True
    zero = np.zeros(grid.shape, bool)
    setup = Setup(grid=grid, bodies=[], design_space=np.ones(grid.shape, bool),
                  preserve=preserve, obstacle=zero, obstacle_raw=zero)
    return grid, study, setup


def test_a_feasible_stress_constraint_is_met_and_recorded():
    from topopt.generative import optimize
    grid, study, setup = _cantilever()
    r = optimize(setup, study, dict(volfrac=0.35, filter_mm=2.0, overhang=False,
                                    stress={"safety_factor": 2.0}),
                 iters=30, verbose=False)
    sc = r["stress_constraint"]
    assert sc and sc["enabled"] and sc["optimizer"] == "mma"
    assert sc["satisfied"], sc["final"]
    assert sc["final"][0]["max_ratio"] <= 1.0 + 1e-3
    assert r["settings"]["optimizer"] == "mma"
    assert r["settings"]["stress"]["safety_factor"] == 2.0
    # The volume constraint is still a constraint.
    v = r["history"][-1]["volume_fraction_free"]
    assert abs(v - 0.35) < 0.02, v
    # Every iteration recorded what the constraint saw.
    assert all("stress" in h and "stress_feasible" in h["criteria"]
               for h in r["history"])


def test_mma_without_stress_matches_oc_on_the_volume():
    from topopt.generative import optimize
    grid, study, setup = _cantilever()
    d = dict(volfrac=0.35, filter_mm=2.0, overhang=False)
    oc = optimize(setup, study, dict(d), iters=25, verbose=False)
    mm = optimize(setup, study, dict(d, optimizer="mma"), iters=25, verbose=False)
    assert mm["stress_constraint"] is None
    assert mm["settings"]["optimizer"] == "mma" and oc["settings"]["optimizer"] == "oc"
    assert abs(mm["history"][-1]["volume_fraction_free"] - 0.35) < 0.02
    # Same problem, two optimisers: within a few percent of each other.
    a, b = oc["history"][-1]["objective"], mm["history"][-1]["objective"]
    assert abs(a - b) / max(a, b) < 0.1, (a, b)


def test_stress_constraint_refuses_the_isotropic_model():
    from topopt.generative import optimize
    from topopt.schema import InputError
    grid, study, setup = _cantilever()
    with pytest.raises(InputError):
        optimize(setup, study, dict(volfrac=0.35, stress=True), iters=2,
                 verbose=False, isotropic=True)


def test_stress_constraint_refuses_oc():
    from topopt.generative import optimize
    from topopt.schema import InputError
    grid, study, setup = _cantilever()
    with pytest.raises(InputError):
        optimize(setup, study, dict(volfrac=0.35, stress=True, optimizer="oc"),
                 iters=2, verbose=False)


def test_report_condition_reads_the_summary():
    from topopt.cli import report as rep

    def verdict(summary):
        fails, passes, unknown = rep.evaluate({"optimisation": summary})
        if "stress_constraint" in {k for k, _, _ in unknown}:
            return None
        return "stress_constraint" in {k for k, _, _ in passes}

    base = {"history": [], "settings": {}}
    # No optimisation at all: nothing to say. Optimised without the
    # constraint: not a skipped analysis, strength is the final FEA's call.
    assert verdict(None) is None
    assert verdict(dict(base, stress_constraint=None)) is True
    held = {"enabled": True, "safety_factor": 2.0, "satisfied": True,
            "final": [{"name": "tip", "max_ratio": 0.77, "worst_mode": "raster_tension"}]}
    assert verdict(dict(base, stress_constraint=held)) is True
    broken = dict(held, satisfied=False,
                  final=[{"name": "tip", "max_ratio": 1.6, "worst_mode": "raster_compression"}])
    assert verdict(dict(base, stress_constraint=broken)) is False


# ------------------------------------------------- per load class (spec 15.1)

def _two_case_cantilever(safety_factor=3.0):
    """The same bar with two cases: a normal tip push and an abnormal tip lift.

    Spec 15.1 asks for the study's factor on the normal one and 1.5 on the
    abnormal one, and until now the stress constraint held both to a single
    number (spec 24).
    """
    from topopt.loadcase import LoadCase
    grid, study, setup = _cantilever()
    study.safety_factor = safety_factor
    study.cases.append(LoadCase("tip lift (abnormal)",
                                [{"region": "root", "fix": "xyz"}],
                                [{"region": "tip", "force": [0, 0, 8.0]}],
                                None, "abnormal"))
    return grid, study, setup


def test_parse_stress_gives_each_case_its_own_bar():
    from topopt.generative import parse_stress
    _, study, _ = _two_case_cantilever(safety_factor=3.0)
    kw = parse_stress({"stress": True}, study)
    assert kw["safety_factor"] == 3.0
    assert kw["case_safety_factors"] == [3.0, 1.5]
    # An explicit `stress.safety_factor` is the normal bar; the abnormal one
    # follows it down but never up (a topology search at 1.2 is not held to
    # 1.5 on the case it cares least about).
    kw = parse_stress({"stress": {"safety_factor": 1.2}}, study)
    assert kw["case_safety_factors"] == [1.2, 1.2]
    kw = parse_stress({"stress": {"safety_factor": 6.0}}, study)
    assert kw["case_safety_factors"] == [6.0, 1.5]


def test_a_study_without_load_class_asks_the_same_of_every_case():
    from topopt.generative import parse_stress
    grid, study, setup = _cantilever()
    study.safety_factor = 2.5
    kw = parse_stress({"stress": True}, study)
    assert kw["case_safety_factors"] == [2.5]


def test_per_case_factors_are_bit_identical_when_they_are_all_the_same():
    # The guarantee that made this change safe to ship: a study with no
    # abnormal case must produce exactly the run it produced before, down to
    # the last bit of the gradient. `sf` stays a Python float multiplying the
    # same arrays in the same order, so this is equality, not a tolerance.
    grid, study, mat, bcs, ortho, E, sc = _constraint(sf=2.0)
    per = StressConstraint(ortho, study.build_axis, study.raster_axis, 2.0,
                           case_safety_factors=[2.0])
    rng = np.random.default_rng(3)
    x = rng.uniform(0.3, 1.0, size=grid.shape)
    a = _value(grid, mat, bcs, ortho, E, sc, x, 3.0, [1.0], study)
    b = _value(grid, mat, bcs, ortho, E, per, x, 3.0, [1.0], study)
    assert a["value"] == b["value"]
    assert a["max_ratio"] == b["max_ratio"]
    assert np.array_equal(a["grad"], b["grad"])


def test_lowering_one_case_bar_scales_that_case_only():
    # `max_ratio` is (required SF) x (failure index), so halving the bar on a
    # case halves its number exactly -- which is what "the abnormal case is
    # constrained at 1.5 instead of 3.0" means numerically.
    grid, study, mat, bcs, ortho, E, _ = _constraint(sf=3.0)
    at3 = StressConstraint(ortho, study.build_axis, study.raster_axis, 3.0,
                           case_safety_factors=[3.0])
    at15 = StressConstraint(ortho, study.build_axis, study.raster_axis, 3.0,
                            case_safety_factors=[1.5])
    x = np.ones(grid.shape)
    a = _value(grid, mat, bcs, ortho, E, at3, x, 3.0, [1.0], study)
    b = _value(grid, mat, bcs, ortho, E, at15, x, 3.0, [1.0], study)
    assert abs(b["max_ratio"] - 0.5 * a["max_ratio"]) <= 1e-12 * a["max_ratio"]
    assert at15.sf_for(0) == 1.5 and at3.sf_for(0) == 3.0


def test_the_summary_records_the_bar_each_case_was_held_to():
    from topopt.generative import optimize
    grid, study, setup = _two_case_cantilever(safety_factor=3.0)
    r = optimize(setup, study, dict(volfrac=0.35, filter_mm=2.0,
                                    overhang=False, stress=True),
                 iters=12, verbose=False)
    sc = r["stress_constraint"]
    assert sc["safety_factor"] == 3.0
    assert sc["case_safety_factors"] == [3.0, 1.5]
    normal, abnormal = sc["final"]
    assert normal["load_class"] == "normal"
    assert normal["required_safety_factor"] == 3.0
    assert abnormal["load_class"] == "abnormal"
    assert abnormal["required_safety_factor"] == 1.5
    # `min_safety_factor_relaxed` divides by the case's own bar, not the run's.
    for c in (normal, abnormal):
        assert abs(c["min_safety_factor_relaxed"]
                   - c["required_safety_factor"] / c["max_ratio"]) < 5e-3
    # And the settings block carries the same list, so a reader of the file
    # can see what was asked without re-deriving it from the study.
    assert r["settings"]["stress"]["case_safety_factors"] == [3.0, 1.5]

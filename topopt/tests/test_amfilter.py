"""The self-support filter: its gradient is exact and its rule is the one
`topopt print` measures."""

import numpy as np
import pytest

from topopt.amfilter import AMFilter, parse_overhang, unsupported_voxels


def _rand(shape, seed):
    return np.random.default_rng(seed).uniform(0.05, 0.95, size=shape)


@pytest.mark.parametrize("axis,up", [(2, +1), (2, -1), (0, +1), (1, -1)])
def test_adjoint_matches_central_differences(axis, up):
    shape = (5, 4, 6)
    rho = _rand(shape, 1)
    w = np.random.default_rng(2).standard_normal(shape)
    am = AMFilter(shape, axis=axis, up=up)

    def J(r):
        return float((w * am.forward(r)).sum())

    am.forward(rho)
    grad = am.backward(w)
    rng = np.random.default_rng(3)
    for _ in range(12):
        c = tuple(rng.integers(0, s) for s in shape)
        h = 1e-6
        rp, rm = rho.copy(), rho.copy()
        rp[c] += h
        rm[c] -= h
        fd = (J(rp) - J(rm)) / (2 * h)
        assert abs(fd - grad[c]) <= 1e-6 * max(1.0, abs(fd)), (c, fd, grad[c])


def test_plate_layer_is_never_reduced_and_air_cannot_hold_material():
    shape = (4, 4, 5)
    rho = np.zeros(shape)
    rho[:, :, 0] = 0.7                 # on the plate
    rho[1, 1, 3] = 1.0                 # a cell with nothing under it
    am = AMFilter(shape, axis=2)
    xi = am.forward(rho)
    assert np.allclose(xi[:, :, 0], 0.7)
    assert xi[1, 1, 3] < 0.05


def test_a_full_column_does_not_decay_with_height():
    shape = (3, 3, 40)
    rho = np.zeros(shape)
    rho[1, 1, :] = 1.0
    xi = AMFilter(shape, axis=2).forward(rho)
    assert xi[1, 1, -1] > 0.99


def test_45_degree_stair_is_kept_and_a_bridge_is_not():
    shape = (8, 3, 8)
    rho = np.zeros(shape)
    for k in range(8):                 # one cell sideways per layer: 45 deg
        rho[k, 1, k] = 1.0
    xi = AMFilter(shape, axis=2).forward(rho)
    assert xi[7, 1, 7] > 0.9
    rho2 = np.zeros(shape)
    rho2[:, 1, 0] = 1.0                # plate row
    rho2[0, 1, :] = 1.0                # a pillar
    rho2[5, 1, 4] = 1.0                # floating cell far from the pillar
    xi2 = AMFilter(shape, axis=2).forward(rho2)
    assert xi2[5, 1, 4] < 0.05


def test_unsupported_voxels_is_the_binary_rule():
    shape = (6, 3, 4)
    m = np.zeros(shape, bool)
    m[:, 1, 0] = True
    m[2, 1, 1] = True                  # directly above the plate row
    m[3, 1, 2] = True                  # one step sideways: 45 deg, fine
    m[5, 1, 3] = True                  # two steps sideways: unsupported
    u = unsupported_voxels(m, axis=2)
    assert u.sum() == 1 and u[5, 1, 3]


def test_parse_overhang_knob():
    d = parse_overhang({}, 2)
    assert (d["axis"], d["up"], d["p"]) == (2, 1, 40.0) and 0 < d["ramp"] < 1
    assert parse_overhang({"overhang": False}, 2) is None
    d = parse_overhang({"overhang": {"up": "-Z", "p": 30, "ramp": 0.5}}, 2)
    assert (d["axis"], d["up"], d["p"], d["ramp"]) == (2, -1, 30.0, 0.5)
    with pytest.raises(ValueError):
        parse_overhang({"overhang": {"up": "+X"}}, 2)


def test_optimiser_with_the_filter_leaves_fewer_unsupported_voxels():
    """End to end on the 100 x 4 x 4 bar: the same run with and without the
    self-support rule. Not zero -- eight iterations at beta 1 leave the field
    grey -- but strictly fewer, and the objective still descends."""
    import os
    from tests.checks import Ctx
    from topopt.generative import measure, optimize
    os.environ.setdefault("TOPOPT_SOLVER", "cpu")
    ctx = Ctx()
    out = {}
    for oh in (False, True):
        res = optimize(ctx.setup, ctx.study,
                       {"volfrac": 0.4, "filter_mm": 3.0, "overhang": oh,
                        "penal": 1.0, "beta": 1.0}, iters=8, verbose=False)
        obj = [h["objective"] for h in res["history"]]
        assert obj[-1] < obj[0]
        out[oh] = measure(res["x_phys"] >= 0.5, ctx.setup)["unsupported"]["voxels"]
        assert (res["settings"]["overhang"] is not None) == oh
    assert out[True] < out[False]


def test_plate_is_the_first_domain_layer_not_the_array_edge():
    """A grid that starts under the plate (empty layers first) must treat the
    first domain layer as the plate, or everything above is 'in the air'."""
    from topopt.amfilter import plate_index
    shape = (3, 3, 12)
    domain = np.zeros(shape, bool)
    domain[:, :, 3:] = True                  # plate at layer 3
    assert plate_index(domain, 2, +1) == 3
    rho = np.zeros(shape)
    rho[1, 1, 3:] = 1.0                      # a column standing on the plate
    xi = AMFilter(shape, axis=2, base=plate_index(domain, 2, +1)).forward(rho)
    assert xi[1, 1, -1] > 0.99
    assert AMFilter(shape, axis=2).forward(rho)[1, 1, -1] < 0.05   # the bug
    m = rho >= 0.5
    assert unsupported_voxels(m, 2, +1, base=3).sum() == 0
    assert unsupported_voxels(m, 2, +1).sum() == 1
    # adjoint still exact with a base
    am = AMFilter(shape, axis=2, base=3)
    w = np.random.default_rng(9).standard_normal(shape)
    r = np.random.default_rng(8).uniform(0.1, 0.9, shape)
    am.forward(r)
    grad = am.backward(w)
    for c in [(1, 1, 3), (1, 1, 5), (0, 2, 7), (2, 0, 11)]:
        h = 1e-6
        rp, rm = r.copy(), r.copy()
        rp[c] += h
        rm[c] -= h
        fd = ((w * am.forward(rp)).sum() - (w * am.forward(rm)).sum()) / (2 * h)
        assert abs(fd - grad[c]) <= 1e-6 * max(1.0, abs(fd))


def test_prune_keeps_the_printable_subset_and_preserve():
    from topopt.amfilter import prune_unsupported
    shape = (6, 3, 5)
    m = np.zeros(shape, bool)
    m[:, 1, 0] = True                      # plate row
    m[2, 1, 1] = True                      # supported
    m[3, 1, 2] = True                      # 45 deg, supported
    m[5, 1, 3] = True                      # hangs in the air
    m[5, 1, 4] = True                      # stands on the hanging one
    keep = np.zeros(shape, bool)
    keep[0, 1, 4] = True                   # a preserve voxel in the air
    m |= keep
    out, n = prune_unsupported(m, 2, +1, 0, keep=keep)
    assert n == 2 and not out[5, 1, 3] and not out[5, 1, 4]
    assert out[0, 1, 4] and out[3, 1, 2]


@pytest.mark.parametrize("reach", [1, 2])
def test_adjoint_is_exact_with_support_columns_and_a_free_region(reach):
    shape = (5, 4, 9)
    rho = _rand(shape, 11)
    w = np.random.default_rng(12).standard_normal(shape)
    free = np.zeros(shape, bool)
    free[0:2, :, 5:7] = True
    am = AMFilter(shape, axis=2, base=1, reach=reach, free=free)
    am.forward(rho)
    grad = am.backward(w)
    rng = np.random.default_rng(13)
    for _ in range(14):
        c = tuple(rng.integers(0, s) for s in shape)
        h = 1e-6
        rp, rm = rho.copy(), rho.copy()
        rp[c] += h
        rm[c] -= h
        fd = ((w * am.forward(rp)).sum() - (w * am.forward(rm)).sum()) / (2 * h)
        assert abs(fd - grad[c]) <= 1e-6 * max(1.0, abs(fd)), (c, fd, grad[c])


def test_short_support_columns_are_allowed_when_asked():
    shape = (5, 3, 8)
    rho = np.zeros(shape)
    rho[2, 1, 0] = 1.0                     # one cell on the plate
    rho[2, 1, 3] = 1.0                     # three layers up, air between
    strict = AMFilter(shape, axis=2).forward(rho)
    lax = AMFilter(shape, axis=2, reach=2).forward(rho)
    assert strict[2, 1, 3] < 0.05          # no support: erased
    assert lax[2, 1, 3] > 0.9              # within reach of the plate: allowed
    m = rho >= 0.5
    assert unsupported_voxels(m, 2, +1).sum() == 1
    assert unsupported_voxels(m, 2, +1, reach=2).sum() == 0
    assert unsupported_voxels(m, 2, +1, reach=1).sum() == 1
    # a cell in an allowed region keeps its density with nothing under it
    free = np.zeros(shape, bool)
    free[4, 2, 6] = True
    rho2 = np.zeros(shape)
    rho2[4, 2, 6] = 0.8
    assert AMFilter(shape, axis=2, free=free).forward(rho2)[4, 2, 6] == 0.8
    assert unsupported_voxels(rho2 >= 0.5, 2, +1, free=free).sum() == 0


def test_a_column_of_material_supports_through_air_within_reach():
    from topopt.amfilter import prune_unsupported
    shape = (5, 3, 9)
    m = np.zeros(shape, bool)
    m[2, 1, 0:3] = True                    # pillar on the plate, 3 layers
    m[2, 1, 5] = True                      # two layers of air, then material
    assert unsupported_voxels(m, 2, +1).sum() == 1
    assert unsupported_voxels(m, 2, +1, reach=2).sum() == 0
    pruned, n = prune_unsupported(m, 2, +1, reach=2)
    assert n == 0 and pruned[2, 1, 5]


def test_parse_support_conditions():
    d = parse_overhang({"overhang": {"max_support_mm": 12, "allow_support": "seat"}}, 2)
    assert d["max_support_mm"] == 12.0 and d["allow_support"] == ["seat"]
    with pytest.raises(ValueError):
        parse_overhang({"overhang": {"max_support_mm": -1}}, 2)
    with pytest.raises(ValueError):
        parse_overhang({"overhang": {"allow_support": [1]}}, 2)

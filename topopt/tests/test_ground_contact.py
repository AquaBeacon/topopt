"""`"contact": "ground"` supports: standing on the plate without naming the feet.

The rule everywhere else in this tool is that loads and supports may only act
on preserve, because the optimiser can delete free material out from under
them. The cost of that rule is that somebody has to draw the feet, so the
finished tripod's stance was chosen by whoever wrote the CAD, not by the
optimiser -- and the report then has to disclaim it.

A ground support keeps the protection and drops the drawing: it may act on free
material, but *only* on the one-voxel band of the domain that sits on the build
plane, and it fixes only the nodes on that plane. Those two limits are what
these tests pin. Without the second one the support would clamp the node plane
a voxel above the plate just as rigidly, and the optimiser could hang the part
in mid-air and still measure as fully supported.
"""

import numpy as np
import pytest

from topopt.designspace import Setup, ground_mask, validate
from topopt.loadcase import Study, build_bcs
from topopt.voxel import Grid


def _setup(nx=6, ny=6, nz=5, h=2.0):
    """A block of domain with preserve as a lid on top -- nothing preserved
    anywhere near the plate, which is the whole point."""
    solid = np.ones((nx, ny, nz), bool)
    grid = Grid(solid=solid, h=h, origin=np.zeros(3))
    preserve = np.zeros((nx, ny, nz), bool)
    preserve[:, :, nz - 1] = True
    design = np.ones((nx, ny, nz), bool)
    g, g_mm = ground_mask(solid, 2, h, grid.origin)
    return Setup(grid=grid, bodies=[], design_space=design, preserve=preserve,
                 obstacle=np.zeros((nx, ny, nz), bool),
                 obstacle_raw=np.zeros((nx, ny, nz), bool),
                 ground=g, ground_axis=2, ground_mm=g_mm), h


def _study(support, nz=5, h=2.0):
    return Study({
        "material": "PLA", "build_axis": "Z",
        "regions": {
            "floor": {"type": "face", "axis": "Z", "side": "min", "depth": 1.0},
            "lid": {"type": "box", "min": [-1, -1, (nz - 1) * h],
                    "max": [99, 99, 99]},
            "midair": {"type": "box", "min": [-1, -1, 2 * h + 0.1],
                       "max": [99, 99, 3 * h - 0.1]},
        },
        "cases": [{
            "name": "stand on it",
            "supports": [support],
            "loads": [{"region": "lid", "force": [0, 0, -10.0]}],
        }],
    }, base_dir=".")


def test_ground_band_is_one_voxel_on_the_lowest_face():
    setup, h = _setup()
    assert setup.ground_mm == 0.0
    assert setup.ground[:, :, 0].all()
    assert not setup.ground[:, :, 1:].any(), \
        "the contact band reached above the plate"


def test_ground_support_fixes_the_plate_and_not_the_layer_above():
    setup, h = _setup()
    study = _study({"region": "floor", "fix": "xyz", "contact": "ground"})
    fixed, _force = build_bcs(setup.grid, study.cases[0], study.regions,
                              restrict=setup.preserve, ground=setup.ground,
                              ground_axis=setup.ground_axis)
    assert fixed[:, :, 0, :].all(), "the plate is not fixed"
    assert not fixed[:, :, 1:, :].any(), (
        "nodes above the plate are fixed too -- a leg could then hang one "
        "voxel off the ground and still be fully supported")


def test_a_ground_support_may_act_on_free_material():
    """The point of the feature. The same region without `contact` is an error,
    which is the counter-test: it shows the old invariant is still in force and
    that `contact` is what lifts it."""
    setup, _h = _setup()

    ok = _study({"region": "floor", "fix": "xyz", "contact": "ground"})
    rep = validate(setup, ok)
    assert rep["ok"], rep["errors"]
    row = [r for r in rep["info"]["boundary_conditions"]
           if r["kind"] == "support"][0]
    assert row["contact"] == "ground"
    assert row["voxels_on_preserve"] == 0, \
        "this fixture is meant to have no preserve on the floor"
    assert row["voxels_on_ground"] > 0

    bad = _study({"region": "floor", "fix": "xyz"})
    rep2 = validate(setup, bad)
    assert not rep2["ok"]
    assert any("does not touch any preserve" in e for e in rep2["errors"])


def test_a_ground_support_that_misses_the_plate_is_an_error():
    setup, _h = _setup()
    study = _study({"region": "midair", "fix": "xyz", "contact": "ground"})
    rep = validate(setup, study)
    assert not rep["ok"]
    assert any("does not reach the contact plane" in e for e in rep["errors"]), \
        rep["errors"]


def test_ground_support_needs_the_band_to_be_supplied():
    """A caller that has not been taught about ground contact must fail loudly
    rather than silently fall back to clipping against preserve."""
    setup, _h = _setup()
    study = _study({"region": "floor", "fix": "xyz", "contact": "ground"})
    with pytest.raises(ValueError, match="ground contact"):
        build_bcs(setup.grid, study.cases[0], study.regions,
                  restrict=setup.preserve)


def test_measure_reports_what_the_shape_stands_on():
    from topopt.generative import measure
    setup, h = _setup()
    setup.free = setup.grid.solid & ~setup.preserve
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, :, -1] = True                      # the lid
    mask[1:3, 1:3, :] = True                   # one leg down to the plate
    mask[4:6, 4:6, :] = True                   # and a second one
    m = measure(mask, setup)["ground_contact"]
    assert m["pads"] == 2
    assert m["area_mm2"] == pytest.approx(2 * (2 * h) ** 2)
    assert m["plane_mm"] == 0.0

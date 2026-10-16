"""Acceptance conditions spec 19 marked `[済]` and nothing was checking.

Every item here was implemented long ago; what was missing was the automated
check the section's own rule demands ("`[x]` means a test looks at it"). They
are gathered in one file because each is two or three lines against machinery
that already exists -- splitting them across five files would hide how small
they are, and putting them in the files that own each module would bury them
among tests about something else.

Covered: the two setup refusals (19.1), multi-case normalisation, the
Heaviside/continuation schedule and preserve connectivity (19.2), preserve
re-union and obstacle re-subtraction and a watertight STL plus a STEP (19.3),
minimum wall against the nozzle and a member under the filter radius (19.5).
"""

import os

import numpy as np
import pytest

from topopt import generative as gen, geom
from topopt.designspace import Setup, build_setup, connectivity_report, validate
from topopt.loadcase import Study
from topopt.voxel import Grid


# ------------------------------------------------------------------ fixtures

def _study(**over):
    """A 40 mm cube design space with a preserve pad at each end."""
    raw = {
        "units": "mm_N_MPa", "material": "PLA", "build_axis": "Z",
        "safety_factor": 2.0, "voxel_mm": 4.0,
        "design": {"volfrac": 0.3, "filter_mm": 8.0},
        "geometry": {
            "voxel_mm": 4.0, "pad_voxels": 0,
            "design_space": {"type": "box", "min": [0, 0, 0],
                             "max": [40, 20, 20]},
            "preserve": {
                "left": {"type": "box", "min": [0, 0, 0], "max": [16, 20, 20]},
                "right": {"type": "box", "min": [24, 0, 0],
                          "max": [40, 20, 20]}},
        },
        "regions": {
            "a": {"type": "box", "min": [0, 0, 0], "max": [16, 20, 20]},
            "b": {"type": "box", "min": [24, 0, 0], "max": [40, 20, 20]},
            "midair": {"type": "box", "min": [17, 5, 5], "max": [23, 15, 15]}},
        "cases": [{"name": "bend",
                   "supports": [{"region": "a", "fix": "xyz"}],
                   "loads": [{"region": "b", "force": [0, 0, -40]}]}],
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(raw.get(k), dict):
            raw[k] = dict(raw[k], **v)
        else:
            raw[k] = v
    return Study(raw, base_dir=".")


def _bar_setup(nx=12, ny=6, nz=6, h=2.0):
    solid = np.ones((nx, ny, nz), bool)
    preserve = np.zeros_like(solid)
    preserve[0, :, :] = True
    preserve[-1, :, :] = True
    return Setup(grid=Grid(solid=solid, h=h, origin=np.zeros(3)), bodies=[],
                 design_space=solid.copy(), preserve=preserve,
                 obstacle=np.zeros_like(solid),
                 obstacle_raw=np.zeros_like(solid))


# ---------------------------------------------------------- 19.1 abnormal

def test_preserve_driven_through_an_obstacle_stops_the_setup():
    """Spec 19.1: an overlap deeper than one cell boundary is an error, so the
    run does not start. The one-cell case is the counter-test below -- a bore
    and the boss it is drilled through share a surface, and voxelising them
    separately disagrees by a cell there."""
    st = _study(geometry={"obstacle": {
        "cable": {"type": "box", "min": [0, 0, 0], "max": [12, 20, 20]}}})
    rep = validate(build_setup(st, verbose=False), st)
    assert rep["ok"] is False
    assert any("intersects obstacle" in e for e in rep["errors"]), rep["errors"]


def test_an_obstacle_that_only_shares_a_surface_is_a_warning_not_a_refusal():
    st = _study(geometry={"obstacle": {
        "cable": {"type": "box", "min": [12, 0, 0], "max": [20, 20, 20]}}})
    rep = validate(build_setup(st, verbose=False), st)
    assert rep["ok"] is True, rep["errors"]


def test_a_load_that_misses_every_preserve_body_stops_the_setup():
    """Spec 19.1. A load on free material lets the optimiser delete the very
    thing being pushed on, so this is refused rather than clipped to nothing."""
    st = _study(cases=[{"name": "bend",
                        "supports": [{"region": "a", "fix": "xyz"}],
                        "loads": [{"region": "midair",
                                   "force": [0, 0, -40]}]}])
    rep = validate(build_setup(st, verbose=False), st)
    assert rep["ok"] is False
    assert any("does not touch any preserve" in e for e in rep["errors"])


def test_a_support_that_misses_every_preserve_body_stops_the_setup():
    st = _study(cases=[{"name": "bend",
                        "supports": [{"region": "midair", "fix": "xyz"}],
                        "loads": [{"region": "b", "force": [0, 0, -40]}]}])
    rep = validate(build_setup(st, verbose=False), st)
    assert rep["ok"] is False
    assert any("does not touch any preserve" in e for e in rep["errors"])


# ------------------------------------------------------------ 19.2 normal

def test_each_load_case_is_normalised_by_its_own_first_compliance():
    """Spec 19.2. Summing raw compliances hands the whole design to whichever
    case happens to be numerically largest. After normalisation the first
    objective is exactly the number of cases, whatever their magnitudes -- and
    the 20x load below is what makes that a test rather than a tautology."""
    setup = _bar_setup()
    st = Study({"material": "PLA", "build_axis": "Z",
                "regions": {}, "cases": []}, base_dir=".")
    grid = setup.grid
    n = tuple(s + 1 for s in grid.shape)
    bcs = []
    for scale in (1.0, 20.0):
        fixed = np.zeros(n + (3,), bool)
        force = np.zeros(n + (3,))
        fixed[0, :, :, :] = True
        force[-1, :, :, 2] = -scale
        bcs.append((fixed, force))
    x = np.ones(grid.shape)
    c_ref = [None] * 2
    obj, case_c, _dc = gen.objective_and_sensitivity(
        grid, bcs, x, 1.0, 3000.0, 0.36, None, st, c_ref)
    assert case_c[1] > 100 * case_c[0], "the fixture is not lopsided enough"
    assert obj == pytest.approx(2.0, rel=1e-9)
    assert c_ref == [pytest.approx(case_c[0]), pytest.approx(case_c[1])]


def test_the_heaviside_projection_and_its_continuation_do_what_they_claim():
    """Spec 19.2. Two separate claims: the projection is a smoothed Heaviside
    that sharpens with beta and fixes the 0.5 point, and the continuation
    schedule actually reaches its end value inside the run it is given."""
    for beta in (1.0, 8.0, 64.0):
        assert gen.project(np.array([0.5]), beta)[0] == pytest.approx(0.5)
    grey = np.array([0.35, 0.65])
    soft = gen.project(grey, 1.0)
    sharp = gen.project(grey, 64.0)
    assert abs(sharp[0] - 0.0) < abs(soft[0] - 0.0)
    assert abs(sharp[1] - 1.0) < abs(soft[1] - 1.0)
    # The derivative the sensitivity chain multiplies in, against a difference.
    d = gen.dproject(np.array([0.4]), 8.0)[0]
    eps = 1e-6
    fd = (gen.project(np.array([0.4 + eps]), 8.0)[0]
          - gen.project(np.array([0.4 - eps]), 8.0)[0]) / (2 * eps)
    assert d == pytest.approx(fd, rel=1e-5)

    iters = 40
    penal_at = gen._schedule(None, 1.0, 3.0, max(3, iters // 8), 0.5)
    beta_at = gen._schedule(None, 1.0, 16.0, max(4, iters // 5), 2.0,
                            geometric=True)
    assert penal_at(0) == 1.0 and penal_at(iters - 1) == 3.0
    assert beta_at(0) == 1.0 and beta_at(iters - 1) == 16.0
    assert penal_at(iters // 2) > penal_at(0), "penalisation never ramps"
    assert beta_at(iters // 2) > beta_at(0), "beta never ramps"


def test_every_preserve_body_reached_by_material_is_connected():
    setup = _bar_setup()
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, 2:4, 2:4] = True                  # a rib joining both pads
    mask |= setup.preserve
    rep, kept = connectivity_report(mask, setup)
    assert rep["connected"] is True
    assert rep["preserve_bodies"] == 2
    assert rep["island_mm3"] == 0.0
    assert np.array_equal(kept, mask)


def test_an_island_is_dropped_and_its_volume_reported():
    setup = _bar_setup()
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, 2:4, 2:4] = True
    mask |= setup.preserve
    mask[5:7, 0, 5] = True                    # a crumb touching nothing
    rep, kept = connectivity_report(mask, setup)
    assert rep["island_voxels"] == 2
    assert rep["island_mm3"] == pytest.approx(2 * setup.h ** 3)
    assert any("not connected to any preserve" in v
               for v in rep["violations"])
    assert not kept[5:7, 0, 5].any(), "the island was reported but not dropped"


def test_a_preserve_body_nothing_reaches_fails():
    """Spec 19.2 abnormal: an isolated preserve body is a failed run, not a
    result with a note. Nothing carries load to it."""
    setup = _bar_setup()
    mask = np.zeros(setup.grid.shape, bool)
    mask |= setup.preserve                    # two pads, no rib between them
    rep, _kept = connectivity_report(mask, setup)
    assert rep["connected"] is False
    assert rep["components"] == 2


# ------------------------------------------------------------ 19.3 normal

def test_extract_puts_preserve_back_and_takes_the_obstacle_out():
    """Spec 19.3. The threshold is applied to the density field, and then the
    two things the optimiser was never allowed to move are re-applied: every
    preserve cell is in, every obstacle cell is out, whatever the field says."""
    setup = _bar_setup()
    setup.obstacle[6, :, :] = True
    setup.free[...] = (setup.design_space & ~setup.preserve & ~setup.obstacle)
    x = np.zeros(setup.grid.shape)            # a field that keeps nothing ...
    x[6, :, :] = 1.0                          # ... except inside the obstacle
    mask, _conn = gen.final_geometry(x, setup, level=0.5)
    assert mask[setup.preserve].all(), "preserve was not re-unioned"
    assert not mask[setup.obstacle].any(), "obstacle was not re-subtracted"


def test_the_written_shape_is_a_watertight_stl_and_a_step(tmp_path):
    """Spec 19.3. Watertight is checked by counting edges rather than by
    trusting the writer: `write_blocky_stl` emits one facet per exposed voxel
    face, and an off-by-one in which faces are exposed is invisible in a
    picture and fatal in a slicer."""
    from topopt.meshout import write_blocky_stl, write_step_faceted

    mask = np.zeros((6, 6, 6), bool)
    mask[1:5, 1:5, 1:5] = True
    stl = os.path.join(str(tmp_path), "part.stl")
    n = write_blocky_stl(stl, mask, 2.0, np.zeros(3))
    rep = geom.shell_report(geom.load_stl(stl))
    assert n == rep["triangles"] > 0
    assert rep["closed_shell"] is True and rep["open_edges"] == 0
    assert rep["components"] == 1

    step = os.path.join(str(tmp_path), "part.step")
    info = write_step_faceted(step, mask, 2.0, np.zeros(3), name="part")
    assert info and info["faces"] > 0 and info["open_edges"] == 0
    with open(step, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("ISO-10303-21;") and "END-ISO-10303-21;" in text


# ------------------------------------------------------------------- 19.5

def test_a_wall_thinner_than_two_perimeters_fails_the_min_feature_check(
        tmp_path):
    """Spec 19.5. The bar is what the nozzle can actually lay down -- two
    perimeters -- so the check is against `printers.py`'s nozzle and not
    against a number typed into the printability command."""
    from topopt.cli.printability import check_min_features
    from topopt.meshout import write_blocky_stl
    from topopt.printers import resolve

    nozzle = resolve("X1C").nozzle_mm
    # A cross, not a slab: `local_thickness` reads the ridge of the distance
    # transform, and a plate that fills its own bounding box has no void to
    # be a distance from -- it measures nothing and reports the array size.
    # The arms are 0.6 mm across, under the 0.8 mm two perimeters need.
    mask = np.zeros((40, 40, 10), bool)
    mask[:, 17:23, :] = True
    mask[17:23, :, :] = True
    stl = os.path.join(str(tmp_path), "cross.stl")
    write_blocky_stl(stl, mask, 0.1, np.zeros(3))
    got = check_min_features(stl, nozzle)
    assert got["min_printable_wall_mm"] == pytest.approx(2 * nozzle)
    assert got["p1_thickness_mm"] < got["min_printable_wall_mm"]
    assert got["pass"] is False
    assert got["p1_thickness_mm"] == pytest.approx(0.6, abs=0.02)
    assert got["fraction_below_min"] > 0.5
    assert got["resolved"] is True


def test_a_member_under_the_filter_radius_is_reported_as_such(tmp_path):
    """Spec 19.5's minimum-member violation, on the number the run is judged
    by: `min_member_optimised` covers the cells the optimiser could move,
    which is the only region `filter_mm` ever governed."""
    from topopt.cli.extract import thickness_report

    setup = _bar_setup(nx=12, ny=8, nz=8, h=1.0)
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, 3:5, 3:5] = True                  # 2 mm across, filter asked 8 mm
    mask |= setup.preserve
    rep = thickness_report(mask, setup, setup.h)
    opt = rep["optimised_region"]
    assert opt["min_mm"] < 8.0, "the fixture is not a violation"
    assert opt["grid_limited"] is True, (
        "2 x voxel exactly: this must be labelled as the grid talking, not "
        "quoted as a structural measurement (AGENTS.md)")
    assert rep["whole_part"]["min_mm"] <= opt["min_mm"]

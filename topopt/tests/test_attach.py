"""`attach`: where the optimiser may join a preserve body.

The design space used to touch every face of every interface, so the result
grew onto faces that have to stay clear -- the top of a camera head, the
flanks of a boss -- and the shape looked nothing like a part anyone would
draw. A preserve body may now say which of its faces (or which region next
to it) material is allowed to meet; everywhere else it gets a no-contact
shell that is an obstacle from setup onwards.

These tests pin the rule geometrically on a small block, not by reading the
code: the shell must be obstacle, the attach zone must stay free, an attach
zone that cannot be reached is a setup error, and a study that never says
`attach` must be exactly as free as it was before.
"""

import numpy as np
import pytest

from topopt import designspace as ds
from topopt.loadcase import load_case_from_json


def _study(tmp_path, attach, standoff=None, extra_regions=None):
    """A 60 mm cube of design space with a 20 mm preserve block in the middle
    of its top, on a 5 mm grid. One load on the block, one support on the
    floor."""
    geom = {
        "voxel_mm": 5.0,
        "pad_voxels": 1,
        "design_space": {"type": "box", "min": [0, 0, 0], "max": [60, 60, 60]},
        "preserve": {"block": {"type": "box", "min": [20, 20, 40],
                               "max": [40, 40, 60]}},
    }
    if attach is not None:
        geom["preserve"]["block"]["attach"] = attach
    if standoff is not None:
        geom["attach_standoff_mm"] = standoff
    regions = {"floor": {"type": "face", "axis": "Z", "side": "min",
                         "depth": 2.0},
               "top": {"type": "box", "min": [20, 20, 55], "max": [40, 40, 60]}}
    regions.update(extra_regions or {})
    study = {
        "material": "PLA", "build_axis": "Z", "safety_factor": 2.0,
        "geometry": geom, "regions": regions,
        "cases": [{"name": "push",
                   "supports": [{"region": "floor", "fix": "xyz",
                                 "contact": "ground"}],
                   "loads": [{"region": "top", "force": [0, 0, -10]}]}],
        "design": {"volfrac": 0.3},
    }
    p = tmp_path / "study.json"
    import json
    p.write_text(json.dumps(study), encoding="utf-8")
    return load_case_from_json(str(p))


def _cell(setup, x, y, z):
    """Index of the cell whose centre is nearest (x, y, z) mm."""
    return tuple(int((v - o) // setup.h) for v, o in zip((x, y, z),
                                                          setup.origin))


def test_without_attach_every_face_is_joinable(tmp_path):
    st = _study(tmp_path, attach=None)
    setup = ds.build_setup(st, verbose=False)
    assert not setup.no_attach.any()
    assert not setup.attach.any()
    # the cell beside the block's flank is free, as it always was
    assert setup.free[_cell(setup, 17.5, 30, 50)]
    rep = ds.validate(setup, st)
    assert rep["ok"], rep["errors"]
    assert "attach" not in rep["info"]


def test_attach_face_keeps_only_that_face_free(tmp_path):
    st = _study(tmp_path, attach="-Z")
    setup = ds.build_setup(st, verbose=False)
    below = _cell(setup, 30, 30, 37.5)          # under the block
    beside = _cell(setup, 17.5, 30, 50)         # beside it, on -X
    above = _cell(setup, 30, 30, 62.5)          # above it (outside the domain)
    assert setup.attach[below] and setup.free[below]
    assert setup.no_attach[beside] and setup.obstacle[beside]
    assert not setup.free[beside]
    assert not setup.free[above]
    # The shell is one voxel: two cells out from the flank is free again.
    assert setup.free[_cell(setup, 12.5, 30, 50)]
    rep = ds.validate(setup, st)
    assert rep["ok"], rep["errors"]
    a = rep["info"]["attach"]["block"]
    assert a["attach_usable_voxels"] == 16        # 4 x 4 cells under a 20 mm block
    assert a["attach_area_mm2"] == pytest.approx(400.0)
    assert a["no_attach_voxels"] == 4 * 16       # four flanks, 4 x 4 each


def test_a_face_that_cannot_be_reached_is_a_setup_error(tmp_path):
    """+Z of a block on the top of the design space: nothing is there."""
    st = _study(tmp_path, attach="+Z")
    setup = ds.build_setup(st, verbose=False)
    rep = ds.validate(setup, st)
    assert not rep["ok"]
    assert any("attach zone" in e and "block" in e for e in rep["errors"])


def test_an_empty_attach_list_is_a_setup_error(tmp_path):
    st = _study(tmp_path, attach=[])
    setup = ds.build_setup(st, verbose=False)
    rep = ds.validate(setup, st)
    assert not rep["ok"]
    assert any("'attach' is empty" in e for e in rep["errors"])


def test_attach_accepts_a_region_and_clips_it_to_the_shell(tmp_path):
    """A region drawn generously loses the cells that are not next to the
    body -- the same rule loads and supports follow."""
    st = _study(tmp_path, attach=["pad"],
                extra_regions={"pad": {"type": "box", "min": [0, 25, 0],
                                       "max": [22, 35, 60]}})
    setup = ds.build_setup(st, verbose=False)
    flank = _cell(setup, 17.5, 30, 50)          # in the region, on the -X flank
    far = _cell(setup, 7.5, 30, 50)             # in the region, not next to it
    other = _cell(setup, 30, 30, 37.5)          # under the block, not in it
    assert setup.attach[flank] and setup.free[flank]
    assert not setup.attach[far] and setup.free[far]     # ordinary free cell
    assert setup.no_attach[other] and not setup.free[other]


def test_a_thicker_standoff_widens_the_shell_and_the_column(tmp_path):
    st = _study(tmp_path, attach="-Z", standoff=10.0)
    setup = ds.build_setup(st, verbose=False)
    assert setup.attach_standoff_mm == pytest.approx(10.0)
    # two cells out from the flank is now shell, three is free
    assert setup.obstacle[_cell(setup, 12.5, 30, 50)]
    assert setup.free[_cell(setup, 7.5, 30, 50)]
    # and the attachable column under the block is two cells deep
    assert setup.attach[_cell(setup, 30, 30, 37.5)]
    assert setup.attach[_cell(setup, 30, 30, 32.5)]
    assert not setup.attach[_cell(setup, 30, 30, 27.5)]


def test_attach_on_an_obstacle_is_refused(tmp_path):
    st = _study(tmp_path, attach=None)
    st.raw["geometry"]["obstacle"] = {"hole": {"type": "box", "min": [0, 0, 0],
                                               "max": [5, 5, 5],
                                               "attach": "-Z"}}
    with pytest.raises(ValueError, match="only meaningful on a preserve"):
        ds.build_setup(st, verbose=False)


def test_the_shell_survives_the_threshold_and_the_optimiser_respects_it(tmp_path):
    """End to end on a tiny grid: the final geometry never holds material in
    the no-contact shell, whatever the density field says."""
    from topopt import generative as gen

    st = _study(tmp_path, attach="-Z")
    setup = ds.build_setup(st, verbose=False)
    x = np.ones(setup.grid.shape)                 # "everything solid"
    x = setup.impose(x)
    assert not x[setup.no_attach].any()
    mask, _conn = gen.final_geometry(x, setup, level=0.5)
    assert not mask[setup.no_attach].any()
    assert mask[setup.attach].all()

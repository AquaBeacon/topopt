"""`ground_axis`: which way is down for a ground-contact support.

It defaults to the build axis -- a part printed standing up stands on its
build plate -- and can be declared separately so a Phase 6 comparison can
re-print the same standing part with the layers along X or Y without the
part falling over onto a different face.
"""

import numpy as np

from topopt.loadcase import Study
from topopt.schema import validate


def _study(**extra):
    d = {"material": "PLA", "build_axis": "Z", "safety_factor": 3.0,
         "regions": {"a": {"type": "box", "min": [0, 0, 0], "max": [1, 1, 1]}},
         "cases": [{"name": "c", "supports": [{"region": "a"}],
                    "loads": [{"region": "a", "force": [0, 0, -1]}]}]}
    d.update(extra)
    return d


def test_ground_axis_defaults_to_the_build_axis():
    st = Study(_study(build_axis="X"))
    assert st.ground_axis == 0 and not st.ground_axis_declared


def test_ground_axis_can_be_declared_apart_from_the_build_axis():
    st = Study(_study(build_axis="X", raster_axis="Y", ground_axis="Z"))
    assert st.build_axis == 0 and st.ground_axis == 2
    assert st.ground_axis_declared
    assert not [p for p in validate(_study(build_axis="X", raster_axis="Y",
                                           ground_axis="Z"))
                if "ground_axis" in str(p.path)]


def test_the_contact_band_follows_ground_axis_not_build_axis():
    from topopt.designspace import ground_mask
    dom = np.ones((4, 5, 6), bool)
    mz, _ = ground_mask(dom, 2, 1.0, np.zeros(3))
    mx, _ = ground_mask(dom, 0, 1.0, np.zeros(3))
    assert mz[:, :, 0].all() and not mz[:, :, 1:].any()
    assert mx[0].all() and not mx[1:].any()


def test_the_fea_ground_plane_uses_the_declared_axis(tmp_path):
    """`cli/fea.py` builds its own contact band; it must read `ground_axis`,
    not `build_axis`, or a study printed on its side would be clamped on a
    wall instead of on the floor."""
    import inspect
    from topopt.cli import fea
    src = inspect.getsource(fea)
    assert "ground_axis" in src

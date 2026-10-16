"""Cells that touch only along an edge or at a corner are joined in the voxel
FEA through their shared nodes and nowhere else: the STL has a non-manifold
edge there, a tet mesher refuses it, MeshFix cuts it (tall_tripod lost 21 % of
its volume that way, 2026-09-08), and a printer lays two beads that touch.
`final_geometry` fills one bridging cell per contact so the part that is
analysed, exported and printed is the same solid.
"""

import numpy as np

from topopt.generative import find_pinches, heal_pinches


def _grid():
    return np.zeros((4, 4, 4), bool)


def test_pinch_repair_never_adds_an_unsupported_bridge():
    from topopt.amfilter import unsupported_voxels
    m = np.zeros((6, 6, 4), bool)
    m[1, 2, 0] = m[4, 3, 0] = True
    m[2, 2, 1] = m[3, 3, 1] = True
    # Each diagonal cell is supported from its outer side; neither bridge is.
    assert not unsupported_voxels(m).any()
    spec = {"axis": 2, "up": 1, "base": 0, "reach": 0, "free": None}
    allowed = m.copy()
    allowed[2, 3, 1] = allowed[3, 2, 1] = True
    out, _, left = heal_pinches(m, allowed=allowed, self_support=spec)
    assert not unsupported_voxels(out).any()
    assert left == len(find_pinches(out))
    assert left > 0  # leave an explicit failure, never invent support


def test_an_edge_contact_is_found_once_with_its_two_bridges():
    m = _grid()
    m[1, 1, 1] = True
    m[2, 2, 1] = True
    p = find_pinches(m)
    assert len(p) == 1
    a, b, bridges = p[0]
    assert {a, b} == {(1, 1, 1), (2, 2, 1)}
    assert set(bridges) == {(2, 1, 1), (1, 2, 1)}


def test_a_corner_contact_is_found_with_three_bridges():
    m = _grid()
    m[1, 1, 1] = True
    m[2, 2, 2] = True
    p = find_pinches(m)
    assert len(p) == 1
    assert set(p[0][2]) == {(2, 1, 1), (1, 2, 1), (1, 1, 2)}


def test_face_neighbours_are_not_a_pinch():
    m = _grid()
    m[1, 1, 1] = True
    m[2, 1, 1] = True
    m[2, 2, 1] = True          # joined to (1,1,1) through (2,1,1)
    assert find_pinches(m) == []


def test_healing_fills_the_lowest_bridge_along_the_build_axis():
    m = _grid()
    m[1, 1, 2] = True
    m[1, 2, 1] = True          # edge contact in the y-z plane
    out, healed, left = heal_pinches(m, axis=2)
    assert healed == 1 and left == 0
    # bridges are (1,2,2) and (1,1,1); the lower one along z is (1,1,1)
    assert out[1, 1, 1] and not out[1, 2, 2]
    assert find_pinches(out) == []


def test_a_bridge_that_is_obstacle_is_not_filled_and_is_counted():
    m = _grid()
    m[1, 1, 1] = True
    m[2, 2, 1] = True
    allowed = np.ones_like(m)
    allowed[2, 1, 1] = False
    allowed[1, 2, 1] = False
    out, healed, left = heal_pinches(m, allowed=allowed)
    assert healed == 0 and left == 1
    assert out.sum() == 2


def test_a_shape_without_pinches_is_returned_unchanged():
    m = _grid()
    m[1:3, 1:3, 1:3] = True
    out, healed, left = heal_pinches(m)
    assert healed == 0 and left == 0
    assert np.array_equal(out, m)


def test_the_recorded_tall_tripod_result_has_exactly_three_pinches():
    """The number the 2026-09-08 CalculiX attempt tripped over."""
    import os
    # The v4 run (10 mm, no `attach`). v5 re-ran the model in place on
    # 2026-09-08, so the file this number was measured on lives in the archive.
    p = os.path.join(os.path.dirname(__file__), "..", "..", "tall_tripod",
                     "archive", "v4_before_attach_2026-09-08",
                     "03_optimization", "tall_tripod_study_binary.npy")
    if not os.path.exists(p):
        import pytest
        pytest.skip("recorded tall_tripod run not present")
    m = np.load(p).astype(bool)
    assert len(find_pinches(m)) == 3
    out, healed, left = heal_pinches(m)
    assert healed == 3 and left == 0
    assert find_pinches(out) == []

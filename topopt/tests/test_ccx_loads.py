"""Consistent nodal loads on quadratic elements.

Splitting a region's force equally over its nodes loads the corner nodes of
C3D10 / TRI6 elements, which the quadratic shape functions turn into a
stress ripple at the loaded nodes (osmo_tripod LC1: worst element 0.95 where
the voxel path had 5.3). The consistent weights are what a uniform body force
or traction actually puts on each node.
"""

import numpy as np
import pytest

from topopt import ccx


def _one_tet():
    """A single C3D10 with mid-edge nodes exactly on the edges."""
    c = np.array([[0, 0, 0], [6, 0, 0], [0, 6, 0], [0, 0, 6]], float)
    pairs = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
    mids = np.array([(c[a] + c[b]) / 2 for a, b in pairs])
    xyz = np.vstack([c, mids])
    ids = np.arange(1, 11)
    elements = np.array([ids])
    return xyz, ids, elements


def test_body_force_weights_put_a_fifth_on_each_mid_edge_and_minus_a_twentieth_on_corners():
    xyz, ids, els = _one_tet()
    w, method = ccx.load_weights(xyz, els, ids, "volume", ids=ids)
    assert "1 fully selected elements" in method
    assert sum(w.values()) == pytest.approx(1.0)
    for n in ids[:4]:
        assert w[int(n)] == pytest.approx(-1 / 20)
    for n in ids[4:]:
        assert w[int(n)] == pytest.approx(1 / 5)


def test_traction_weights_put_nothing_on_the_corners_of_a_boundary_face():
    xyz, ids, els = _one_tet()
    # the face z = 0: corners 1,2,3 and mid-edges 5 (1-2), 6 (2-3), 7 (3-1)
    face = [1, 2, 3, 5, 6, 7]
    w, method = ccx.load_weights(xyz, els, face, "surface", ids=ids)
    assert "1 fully selected boundary faces" in method
    assert set(w) == set(face)
    assert sum(w.values()) == pytest.approx(1.0)
    for n in (1, 2, 3):
        assert w[n] == pytest.approx(0.0)
    for n in (5, 6, 7):
        assert w[n] == pytest.approx(1 / 3)


def test_a_selection_smaller_than_an_element_gets_no_weights_so_the_caller_falls_back():
    xyz, ids, els = _one_tet()
    w, method = ccx.load_weights(xyz, els, [1, 2], "volume", ids=ids)
    assert w == {} and method == "equal"


def test_the_resultant_is_exact_whatever_the_split():
    """`build_cases` hands `write_inp` one force per weighted node; their sum
    must be the region's force to the last digit, or the equilibrium check
    becomes circular."""
    xyz, ids, els = _one_tet()
    w, _ = ccx.load_weights(xyz, els, ids, "volume", ids=ids)
    total = np.array([0.0, -150.0, 12.5])
    parts = np.array([total * v for v in w.values()])
    assert parts.sum(axis=0) == pytest.approx(total, abs=1e-12)

"""A face at exactly the overhang threshold needs support (known-issue D1).

The old strict inequality on a rounded normal made the same 45.0 deg roof
report 0.0 mm2 or 40.2 mm2 depending on an unrelated dimension.
"""

import numpy as np

from topopt.geom import support_area


def _roof(angle_deg, w=10.0):
    """One downward-facing square of width w whose normal is `angle_deg`
    from vertical (|nz| = sin), plus a flat bed face far below so the bed
    rule does not swallow the roof. 45 deg is the threshold either way."""
    t = np.radians(angle_deg)
    # normal pointing down and sideways: (0, cos, -sin) after rotation
    a = np.array([0, 0, 0.0]); b = np.array([w, 0, 0.0])
    c = np.array([w, w * np.sin(t), -w * np.cos(t)])
    d = np.array([0, w * np.sin(t), -w * np.cos(t)])
    tri = np.array([[a, c, b], [a, d, c]])
    # a bed-contact face far below so `bed` does not swallow the roof
    z = -50.0
    e = np.array([0, 0, z]); f = np.array([w, 0, z]); g = np.array([w, w, z]); h = np.array([0, w, z])
    tri = np.vstack([tri, np.array([[e, g, f], [e, h, g]])])
    return tri


def _overhang(tri):
    r = support_area(tri)
    return r["support_mm2"]


def test_a_face_exactly_at_45_degrees_counts_as_overhang():
    area = _overhang(_roof(45.0))
    assert area > 99.0          # the 10 x 10 roof, not 0


def test_the_verdict_does_not_depend_on_rounding_of_the_same_angle():
    """Build the same 45 deg face two ways (float rotation vs exact 1/sqrt2)
    and demand the same answer."""
    tri = _roof(45.0)
    exact = tri.copy()
    s = 1.0 / np.sqrt(2.0)
    exact[0][1] = [10, 10 * s, -10 * s]; exact[0][0] = [0, 0, 0]
    assert abs(_overhang(tri) - _overhang(exact)) < 1e-6


def test_a_face_well_inside_the_printable_cone_is_not_overhang():
    assert _overhang(_roof(30.0)) < 1e-9       # |nz| = 0.5 < sin 45


def test_a_face_well_outside_the_printable_cone_is_overhang():
    assert _overhang(_roof(60.0)) > 99.0       # |nz| = 0.87 > sin 45

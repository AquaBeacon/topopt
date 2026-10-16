"""A self-supporting bore has to point *up*, and the assert did not check that.

`tall_tripod.scad` carries

    bore_roof_deg = 90 - asin(1 / bore_apex_k)
    assert(bore_roof_deg >= 50, "bolt bore roof is too shallow")

which reads as "the bore is self-supporting". It passed for three versions of
the model while the teardrop's apex pointed sideways, because the assert is
arithmetic on a constant -- it never sees the orientation the profile is
extruded in. `rotate([0,90,0])` alone gets the extrusion axis right and leaves
the 2D shape's +Y on global +Y, so the roof of the hole stayed horizontal and
the whole point of the teardrop was lost. The shipped `osmo_tripod.scad` has
`rotate([90,0,0]) rotate([0,90,0])` and a comment saying why; the validation
model was copied from it without the comment, and without the first rotate.

So this test does what the assert cannot: it measures the exported solid. A
claim about the *shape* has to be checked against the shape.

Skips cleanly when the model has not been exported (OpenSCAD is not a test
dependency, and `tall_mount_finished.stl` is a build product).

Counter-checked against the broken geometry rather than assumed: exporting the
head with the first rotate removed and running both tests against it fires both
of them -- the bore measures 7.80 mm across Y instead of 5.60 (the apex is
lying in that plane) and the nut pocket comes out symmetric about the axis,
5.20 mm above and 5.20 below. A guard that would pass with the defect present
is not a guard.
"""

import os

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
STL = os.path.join(ROOT, "tall_tripod", "02_setup", "tall_mount_finished.stl")

# From tall_tripod.scad: the hinge axis, the bore and the teardrop's shape.
Z_HINGE = 136.0
BOLT_D = 5.60
APEX_K = 1.80


def _void_span(col, coords, centre, h):
    """Extent of the contiguous void through `centre`. -> (lo, hi)"""
    i = int(np.argmin(np.abs(coords - centre)))
    assert not col[i], "expected a hole on the axis, found material"
    a = i
    while a > 0 and not col[a - 1]:
        a -= 1
    b = i
    while b < len(col) - 1 and not col[b + 1]:
        b += 1
    return coords[a] - h / 2, coords[b] + h / 2


@pytest.mark.skipif(not os.path.exists(STL),
                    reason="tall_mount_finished.stl not exported")
def test_the_bolt_bore_teardrop_points_up():
    from topopt.voxel import voxelize_stl

    h = 0.1
    origin = np.array([-1.0, -8.0, Z_HINGE - 6.0])
    shape = np.array([20, 160, 120])
    g = voxelize_stl(STL, h=h, origin=origin, shape=shape)
    ys = origin[1] + (np.arange(shape[1]) + 0.5) * h
    zs = origin[2] + (np.arange(shape[2]) + 0.5) * h
    i = shape[0] // 2
    j = int((0.0 - origin[1]) / h)
    k = int((Z_HINGE - origin[2]) / h)

    ylo, yhi = _void_span(g.solid[i, :, k], ys, 0.0, h)
    assert abs((yhi - ylo) - BOLT_D) < 3 * h, (
        "the bore is %.2f mm across, the CAD says %.2f" % (yhi - ylo, BOLT_D))

    zlo, zhi = _void_span(g.solid[i, j, :], zs, Z_HINGE, h)
    above = zhi - Z_HINGE
    below = Z_HINGE - zlo
    # A teardrop of apex factor k on a bore of radius r reaches k*r above the
    # centre and r below it. Anything else means the profile was rotated into
    # the wrong plane and the roof of the hole is flat.
    assert above > below + 1.0, (
        "the teardrop apex is not pointing up: %.2f mm above the axis vs "
        "%.2f mm below. The bore's roof is horizontal and will need support, "
        "whatever bore_roof_deg says." % (above, below))
    assert abs(above - APEX_K * BOLT_D / 2) < 4 * h, (
        "apex reaches %.2f mm above the axis, expected %.2f"
        % (above, APEX_K * BOLT_D / 2))


@pytest.mark.skipif(not os.path.exists(STL),
                    reason="tall_mount_finished.stl not exported")
def test_the_nut_pocket_roof_points_up():
    """Same failure, same fix, second feature. The hex pocket's pitched roof is
    there so the top of the nut recess is not a flat ceiling; rotated into the
    wrong plane it pitches sideways and the ceiling is flat again."""
    from topopt.voxel import voxelize_stl

    h = 0.1
    # The nut pocket lives at the +X end of the head (x 6.8 .. 11.0).
    origin = np.array([7.5, -8.0, Z_HINGE - 8.0])
    shape = np.array([20, 160, 160])
    g = voxelize_stl(STL, h=h, origin=origin, shape=shape)
    ys = origin[1] + (np.arange(shape[1]) + 0.5) * h
    zs = origin[2] + (np.arange(shape[2]) + 0.5) * h
    i = shape[0] // 2
    j = int((0.0 - origin[1]) / h)
    k = int((Z_HINGE - origin[2]) / h)

    ylo, yhi = _void_span(g.solid[i, :, k], ys, 0.0, h)
    zlo, zhi = _void_span(g.solid[i, j, :], zs, Z_HINGE, h)
    assert (zhi - Z_HINGE) > (Z_HINGE - zlo) + 0.5, (
        "the nut pocket's roof is not pitched upward: %.2f mm above the axis "
        "vs %.2f below" % (zhi - Z_HINGE, Z_HINGE - zlo))
    assert (zhi - zlo) > (yhi - ylo), (
        "the pocket is wider across Y than across Z, so the pitched roof is "
        "lying on its side")

"""Every writer must put the part on the same plane the analysis stood it on.

The bug this pins: `tall_tripod_smooth.stl` came out with its bottom face at
z = -2.86 mm while `tall_tripod.stl` -- the same result, same grid -- had it at
z = 0.000. Half a voxel of that was `marching_cubes` being read as if index k
meant the *corner* of voxel k rather than its centre; the rest was the Gaussian
pre-smooth doming a face that is flat in the mask. Dropped onto a build plate,
the doming leaves the outside of each foot one or two layers in the air, and the
half-voxel shift puts the whole shape outside the bounding box `printcheck`
signed off as fitting the machine.

Three separate claims, so three tests. The first is the one that would have
caught it: a 3x3x3 block of solid cells, where every correct answer is an
integer and there is nothing to argue about.
"""

import os
import tempfile

import numpy as np
import pytest

pytest.importorskip("skimage", reason="smooth STL needs scikit-image")


def _bounds(path):
    from topopt.voxel import load_stl
    v = load_stl(path).reshape(-1, 3)
    return v.min(axis=0), v.max(axis=0)


def _block(n=7, lo=2, hi=5, h=1.0):
    d = np.zeros((n, n, n))
    d[lo:hi, lo:hi, lo:hi] = 1.0
    return d, h, np.zeros(3)


def test_smooth_and_blocky_agree_on_where_the_part_is():
    """Same voxels in, same box out. The blocky writer is the reference: it
    emits voxel faces directly, so its answer is the definition."""
    from topopt.meshout import write_blocky_stl, write_smooth_stl

    d, h, origin = _block()
    with tempfile.TemporaryDirectory() as tmp:
        b = os.path.join(tmp, "b.stl")
        s = os.path.join(tmp, "s.stl")
        write_blocky_stl(b, d >= 0.5, h, origin)
        assert write_smooth_stl(s, d, h, origin, smooth=0.0)
        blo, bhi = _bounds(b)
        slo, shi = _bounds(s)

    assert np.allclose(blo, [2, 2, 2]) and np.allclose(bhi, [5, 5, 5])
    assert np.allclose(slo, blo, atol=1e-4), (
        "smooth mesh starts at %s, blocky at %s -- half a voxel is %.3f mm"
        % (slo, blo, 0.5 * h))
    assert np.allclose(shi, bhi, atol=1e-4)


def test_smooth_normals_point_out_of_the_solid():
    from topopt.meshout import write_smooth_stl
    from topopt.voxel import load_stl

    d, h, origin = _block()
    with tempfile.TemporaryDirectory() as tmp:
        s = os.path.join(tmp, "s.stl")
        write_smooth_stl(s, d, h, origin, smooth=0.0)
        t = load_stl(s)

    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    zc = t.mean(axis=1)[:, 2]
    bottom = zc <= t.reshape(-1, 3)[:, 2].min() + 1e-6
    assert bottom.any(), "no triangles on the bottom face"
    assert n[bottom, 2].max() < 0, (
        "the bottom face of a solid block has normals pointing up -- the mesh "
        "is inside out")


def test_the_bottom_face_is_flat_on_the_plate():
    """A foot that is flat in the mask has to be flat in the STL.

    `floor_mask` keeps preserve from being eroded, but it does not stop the
    Gaussian from bulging the *outside* of the isosurface downward, and it
    bulges most where the face is widest. That is what leaves the rim of a
    foot floating: the middle of the pad touches the plate and the edge does
    not.
    """
    from topopt.meshout import write_smooth_stl
    from topopt.voxel import load_stl

    # A wide flat pad two cells thick, sitting on the bottom of the grid, with
    # a column on top of it -- the tall tripod's foot in miniature.
    n = 20
    h = 4.0
    origin = np.array([0.0, 0.0, -8.0])
    d = np.zeros((n, n, n))
    d[3:17, 3:17, 2:4] = 1.0                 # the pad: z = 0 .. 8 mm
    d[8:12, 8:12, 4:14] = 1.0                # the column above it
    preserve = np.zeros((n, n, n), bool)
    preserve[3:17, 3:17, 2:4] = True
    mask = d >= 0.5
    ground = origin[2] + 2 * h               # 0.0 mm

    with tempfile.TemporaryDirectory() as tmp:
        s = os.path.join(tmp, "s.stl")
        write_smooth_stl(s, d, h, origin, smooth=1.0, floor_mask=preserve,
                         envelope=mask)
        t = load_stl(s)

    v = t.reshape(-1, 3)
    assert abs(v[:, 2].min() - ground) < 1e-6, (
        "lowest point is %.3f mm, the analysed foot is at %.3f mm"
        % (v[:, 2].min(), ground))

    # Everything within a layer of the plate must be *on* the plate, not
    # somewhere in the first two layers of a 0.2 mm print.
    near = v[v[:, 2] < ground + 0.4]
    assert near.size, "nothing near the plate at all"
    spread = near[:, 2].max() - near[:, 2].min()
    assert spread < 1e-6, (
        "the bottom face spans %.3f mm of z -- at 0.2 mm layers that is %.1f "
        "layers of the foot in the air" % (spread, spread / 0.2))


def test_feet_that_are_not_preserve_still_reach_the_plate():
    """The case the first version of this fix missed.

    Clamping to the envelope stops the isosurface going *below* the analysed
    shape. Nothing stops it pulling *up*, and a foot two cells across is eroded
    bodily by a sigma-1 Gaussian. Measured on a real run once the foot pads
    stopped being preserve: the feet came out 4.11 mm off the plate and the
    part was standing on nothing.

    So the callers floor `preserve | (mask & ground)`, not preserve alone. This
    is the ground half of that, with no preserve anywhere near the floor.
    """
    from topopt.designspace import ground_mask
    from topopt.meshout import write_smooth_stl
    from topopt.voxel import load_stl

    n, h = 20, 10.0
    origin = np.array([0.0, 0.0, -20.0])
    d = np.zeros((n, n, n))
    d[8:12, 8:12, 2:16] = 1.0                # a column standing on the plate
    d[6:14, 6:14, 12:16] = 1.0               # a head, so there is a body to smooth
    mask = d >= 0.5
    domain = np.zeros((n, n, n), bool)
    domain[2:18, 2:18, 2:18] = True          # the design space
    ground, ground_mm = ground_mask(domain, 2, h, origin)
    assert ground_mm == 0.0

    with tempfile.TemporaryDirectory() as tmp:
        bare = os.path.join(tmp, "bare.stl")
        write_smooth_stl(bare, d, h, origin, smooth=1.0, floor_mask=None,
                         envelope=mask)
        lifted = load_stl(bare).reshape(-1, 3)[:, 2].min()

        held = os.path.join(tmp, "held.stl")
        write_smooth_stl(held, d, h, origin, smooth=1.0,
                         floor_mask=(mask & ground), envelope=mask)
        stood = load_stl(held).reshape(-1, 3)[:, 2].min()

    assert lifted > ground_mm + 0.5, (
        "this fixture no longer smooths hard enough to lift the feet, so it is "
        "not testing anything (lowest point %.3f mm)" % lifted)
    assert abs(stood - ground_mm) < 1e-6, (
        "the feet are %.3f mm off the plate; flooring the ground band did not "
        "hold them down" % (stood - ground_mm))


def test_smooth_mesh_stays_inside_the_analysed_envelope():
    """`printcheck` certifies the blocky mask against the build volume. A
    smoothed mesh that sticks out of it has not been checked against anything.
    """
    from topopt.meshout import write_smooth_stl

    n = 20
    h = 4.0
    origin = np.zeros(3)
    d = np.zeros((n, n, n))
    d[4:16, 4:16, 4:16] = 1.0
    mask = d >= 0.5
    lo = origin + 4 * h
    hi = origin + 16 * h

    with tempfile.TemporaryDirectory() as tmp:
        s = os.path.join(tmp, "s.stl")
        write_smooth_stl(s, d, h, origin, smooth=1.5, envelope=mask)
        slo, shi = _bounds(s)

    assert (slo >= lo - 1e-6).all(), "smooth mesh %s is below the mask at %s" % (slo, lo)
    assert (shi <= hi + 1e-6).all(), "smooth mesh %s is above the mask at %s" % (shi, hi)

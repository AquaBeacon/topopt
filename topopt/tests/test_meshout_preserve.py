"""Smoothing must not erode preserve (spec 19.3).

`check_preserve_survives_run` in the smoke suite watches the *density field* --
filter, projection, symmetry, threshold. It stops there. Marching cubes and the
Gaussian pre-smooth run afterwards, on the way out to STL, and they are a
separate opportunity to round a bolt face off. `write_smooth_stl` takes a
`floor_mask` for exactly that reason; nothing was checking that it works.

The test is deliberately geometric rather than an argument about the code: write
the smoothed mesh, voxelise it back, and look at whether the preserve cells are
still solid.
"""

import os
import tempfile

import numpy as np
import pytest

skimage = pytest.importorskip("skimage",
                              reason="smooth STL needs scikit-image")


def _field():
    """A thin slab plus a preserve block, on a grid coarse enough that a
    Gaussian of sigma 1 cell would visibly eat the block's corners."""
    n = 24
    d = np.zeros((n, n, n))
    d[4:20, 10:14, 10:14] = 1.0            # the slab: 4 cells thick
    preserve = np.zeros((n, n, n), bool)
    preserve[4:8, 8:16, 8:16] = True       # a fat mounting boss on one end
    d[preserve] = 1.0
    return d, preserve, 1.0, np.zeros(3)


def _voxelise_back(path, h, origin, shape):
    from topopt.voxel import voxelize_stl
    back = voxelize_stl(path, h=h, pad=2)
    off = np.round((back.origin - origin) / h).astype(int)
    out = np.zeros(shape, bool)
    src, dst = [], []
    for a in range(3):
        lo = max(0, off[a])
        hi = min(shape[a], off[a] + back.shape[a])
        if hi <= lo:
            return out
        dst.append(slice(lo, hi))
        src.append(slice(lo - off[a], hi - off[a]))
    out[tuple(dst)] = back.solid[tuple(src)]
    return out


def test_smoothing_does_not_erode_preserve():
    from topopt.meshout import write_smooth_stl

    d, preserve, h, origin = _field()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "smooth.stl")
        faces = write_smooth_stl(path, d, h, origin, floor_mask=preserve)
        assert faces, "no smoothed surface was written"
        back = _voxelise_back(path, h, origin, d.shape)

    # Interior preserve cells must survive. The outermost shell of the block is
    # where the isosurface legitimately sits, so it is excluded: marching cubes
    # places the surface *at* level 0.5, and a cell the surface passes through
    # is neither wholly in nor out.
    interior = np.zeros_like(preserve)
    interior[5:7, 9:15, 9:15] = True
    lost = int((interior & ~back).sum())
    assert lost == 0, ("smoothing ate %d of %d interior preserve cells"
                       % (lost, int(interior.sum())))


def test_floor_mask_is_what_saves_it():
    """The counter-test: without `floor_mask` the same call erodes.

    A guard that would pass with the mechanism removed is not a guard. This
    pins the fact that `floor_mask` is doing the work, so a future refactor
    that drops the argument fails loudly instead of quietly.
    """
    from topopt.meshout import write_smooth_stl

    d, preserve, h, origin = _field()
    thin = d.copy()
    thin[preserve] = 0.55          # only just above the 0.5 isolevel
    with tempfile.TemporaryDirectory() as tmp:
        bare = os.path.join(tmp, "bare.stl")
        write_smooth_stl(bare, thin, h, origin, smooth=1.5, floor_mask=None)
        without = _voxelise_back(bare, h, origin, d.shape)

        held = os.path.join(tmp, "held.stl")
        write_smooth_stl(held, thin, h, origin, smooth=1.5, floor_mask=preserve)
        with_mask = _voxelise_back(held, h, origin, d.shape)

    assert int((preserve & with_mask).sum()) > int((preserve & without).sum()), (
        "floor_mask made no difference -- either the mask stopped being applied "
        "or this fixture no longer smooths hard enough to erode anything")

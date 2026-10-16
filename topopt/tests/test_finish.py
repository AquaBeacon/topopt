"""The finish stage: interface features that are smaller than the analysis voxel.

The tall tripod's camera interface has a 2.40 mm centre finger and 3.80 mm
slots. At the 4 mm grid the study was solved on, those are at or under one
cell; at 10 mm they are not there at all. The old answer -- "rebuild it in CAD"
-- is right and is also why a coarse grid was treated as unaffordable, because
the shipped file had no interface in it to check against.

Since the interface is preserve, the optimiser never touched it, and it can be
put back at the dimensions it went in with. These tests pin the two things that
have to be true for that to be worth anything: the feature has to survive at
its real size, and the resampling must not move the optimised shape.
"""

import json
import os
import tempfile

import numpy as np
import pytest

from topopt import finish
from topopt.meshout import _write_binary_stl


def _box_stl(path, lo, hi):
    """A watertight axis-aligned box, 12 triangles."""
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    c = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]],
                  [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
                  [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
                  [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]])
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, d, e in quads:
        tris.append([c[a], c[b], c[d]])
        tris.append([c[a], c[d], c[e]])
    _write_binary_stl(path, np.asarray(tris))
    return path


@pytest.fixture()
def coarse():
    """A 40 mm cube of result on a 10 mm grid, origin at 0."""
    h = 10.0
    mask = np.zeros((6, 6, 6), bool)
    mask[1:5, 1:5, 1:5] = True                 # 10..50 mm on every axis
    return mask, h, np.zeros(3)


def test_refine_factor_is_a_whole_number():
    """A fractional subdivision would put the fine cell boundaries somewhere
    other than the coarse ones and shift the analysed shape."""
    assert finish.refine_factor(10.0, 1.0) == (10, 1.0)
    assert finish.refine_factor(4.0, 1.0) == (4, 1.0)
    f, fine = finish.refine_factor(10.0, 3.0)
    assert f == 3 and fine == pytest.approx(10.0 / 3.0)


def test_resampling_alone_does_not_move_the_shape(coarse):
    mask, h, origin = coarse
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "add.stl"), [20, 20, 50], [30, 30, 60])
        spec = {"voxel_mm": 1.0, "add": [("lug", add)], "cut": [],
                "max_cells": 10 ** 9}
        fine, fine_h, forigin, info = finish.build(mask, h, origin, spec,
                                                   verbose=False)
    assert info["refine_factor"] == 10
    # The resampled result must occupy exactly the same millimetres.
    idx = np.argwhere(fine)
    lo = forigin + idx.min(axis=0) * fine_h
    assert np.allclose(lo, [10, 10, 10]), lo
    # and the lug must be there, on top.
    assert fine[:, :, 50:60].any()


def test_a_slot_narrower_than_the_analysis_voxel_survives(coarse):
    """The claim the whole stage rests on. A 2.4 mm cut cannot exist on a 10 mm
    grid; after finishing at 1 mm it is there and it is 2.4 mm wide."""
    mask, h, origin = coarse
    with tempfile.TemporaryDirectory() as tmp:
        cut = _box_stl(os.path.join(tmp, "cut.stl"),
                       [23.8, 5, 5], [26.2, 55, 55])       # 2.4 mm wide in X
        spec = {"voxel_mm": 0.2, "add": [], "cut": [("slot", cut)],
                "max_cells": 10 ** 9}
        fine, fine_h, forigin, info = finish.build(mask, h, origin, spec,
                                                   verbose=False)

    # Measure the gap along X through the middle of the block.
    j = int((30.0 - forigin[1]) / fine_h)
    k = int((30.0 - forigin[2]) / fine_h)
    line = fine[:, j, k]
    holes = np.flatnonzero(~line[int(10 / fine_h):int(50 / fine_h)])
    assert holes.size, "the slot is not in the finished shape at all"
    width = (holes.max() - holes.min() + 1) * fine_h
    assert width == pytest.approx(2.4, abs=1.5 * fine_h), width
    assert info["cut"][0]["removed_mm3"] > 0


def test_cuts_are_applied_after_adds(coarse):
    """Order is the whole correctness argument: a bore cut before its boss is
    unioned in is a bore that gets filled straight back in."""
    mask, h, origin = coarse
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "add.stl"), [20, 20, 50], [40, 40, 60])
        cut = _box_stl(os.path.join(tmp, "cut.stl"), [28, 28, 45], [32, 32, 65])
        spec = {"voxel_mm": 1.0, "add": [("boss", add)],
                "cut": [("bore", cut)], "max_cells": 10 ** 9}
        fine, fine_h, forigin, _info = finish.build(mask, h, origin, spec,
                                                    verbose=False)
    i = int(30.0 / fine_h)
    assert not fine[i, i, int(55.0 / fine_h)], \
        "the bore through the boss was filled in -- cuts ran before adds"
    assert fine[int(22.0 / fine_h), i, int(55.0 / fine_h)], \
        "the boss itself is missing"


def test_an_unaffordable_finish_grid_is_refused_not_attempted(coarse):
    mask, h, origin = coarse
    spec = {"voxel_mm": 0.01, "add": [], "cut": [], "max_cells": 1_000_000}
    with pytest.raises(finish.FinishError, match="max_cells"):
        finish.build(mask, h, origin, spec, verbose=False)


def test_parse_reports_a_missing_stl_as_an_input_problem():
    with pytest.raises(finish.FinishError, match="not found"):
        finish.parse({"add": {"head": "nope.stl"}}, ".")


def test_parse_returns_none_when_there_is_nothing_to_do():
    assert finish.parse(None, ".") is None
    assert finish.parse({"voxel_mm": 1.0}, ".") is None


def test_merged_stl_is_the_same_solid_with_fewer_triangles(coarse):
    """The finish grid resamples a 10 mm face into 100 one-millimetre squares.
    Merging puts it back to one rectangle; voxelising the file must return the
    shape it started from."""
    from topopt.meshout import write_blocky_stl
    from topopt.voxel import voxelize_stl

    mask, h, origin = coarse
    with tempfile.TemporaryDirectory() as tmp:
        spec = {"voxel_mm": 1.0, "add": [], "cut": [], "max_cells": 10 ** 9}
        fine, fine_h, forigin, _ = finish.build(mask, h, origin, spec,
                                                verbose=False)
        plain = os.path.join(tmp, "plain.stl")
        merged = os.path.join(tmp, "merged.stl")
        n_plain = write_blocky_stl(plain, fine, fine_h, forigin)
        n_merged = write_blocky_stl(merged, fine, fine_h, forigin, merge=True)
        back = voxelize_stl(merged, h=fine_h, pad=1)

    assert n_merged < n_plain / 10, (n_merged, n_plain)
    assert n_merged == 12, "a cube has six faces, so twelve triangles"
    assert back.n_solid == int(fine.sum())


def test_merged_stl_normals_point_out_of_the_solid():
    """The merged writer builds its loops from the STEP writer's corner order.
    That order is right for a BREP face; nothing was checking it also gives an
    outward triangle normal, which is what an STL means by the same thing."""
    import tempfile as _t
    from topopt.meshout import write_blocky_stl
    from topopt.voxel import load_stl

    d = np.zeros((7, 7, 7), bool)
    d[2:5, 2:5, 2:5] = True
    with _t.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "m.stl")
        write_blocky_stl(p, d, 1.0, np.zeros(3), merge=True)
        t = load_stl(p)

    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    centre = t.mean(axis=1) - np.array([3.5, 3.5, 3.5])
    assert ((centre * n).sum(axis=1) > 0).all(), "merged faces are wound inwards"


# ---------------------------------------------------------------------------
# 2026-09-08: replacing the analysed preserve with its CAD, and smoothing the
# optimised body without touching that CAD.

def _setup_for(mask, h, preserve, obstacle=None, ground_axis=2,
               no_attach=None):
    """A hand-built Setup around a coarse mask: one preserve body `head`.

    `no_attach` is the one-voxel shell `attach` puts around the faces a
    preserve body may not be joined at. It is already part of `obstacle` in a
    real setup, so it is folded in here too."""
    from topopt.designspace import Body, Setup
    from topopt.voxel import Grid
    shape = mask.shape
    obstacle = np.zeros(shape, bool) if obstacle is None else obstacle
    if no_attach is not None:
        obstacle = obstacle | no_attach
    body = Body(name="head", kind="preserve", spec={"type": "box"},
                mask=preserve, mask_offset=preserve)
    domain = np.ones(shape, bool)
    return Setup(grid=Grid(solid=domain, h=h, origin=np.zeros(3)),
                 bodies=[body], design_space=domain, preserve=preserve,
                 obstacle=obstacle, obstacle_raw=obstacle,
                 no_attach=(np.zeros(shape, bool) if no_attach is None
                            else no_attach),
                 ground_axis=ground_axis)


@pytest.fixture()
def strut_and_head():
    """A 10 mm grid: a vertical strut 10..30 mm in X/Y standing on the plate
    from z = 0 to 40, and a preserve head block 0..40 x 0..40 x 40..60 on top
    of it. The CAD head is smaller than its voxelisation: 12..28 in X/Y and
    starts 2 mm above the analysed face, at z = 42."""
    h = 10.0
    shape = (6, 6, 8)
    mask = np.zeros(shape, bool)
    mask[1:3, 1:3, 0:4] = True                  # strut
    preserve = np.zeros(shape, bool)
    preserve[0:4, 0:4, 4:6] = True              # head, as voxelised
    mask |= preserve
    return mask, h, preserve


def test_a_replaced_preserve_body_drops_its_analysed_voxels(strut_and_head):
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 0.0,
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    fine = part.fine
    assert part.info["replaced_preserve"][0]["preserve"] == "head"
    # The corner of the 40 x 40 x 20 voxelised head is gone ...
    assert not fine[2, 2, 55], "analysed preserve voxel survived replacement"
    # ... the CAD head is there at its own size ...
    assert fine[20, 20, 50] and fine[13, 13, 50] and not fine[11, 11, 50]
    # ... and the bridge closes the 2 mm gap between the strut's analysed top
    # (z = 40) and the CAD face (z = 42): the column is solid straight through.
    assert fine[20, 20, 39] and fine[20, 20, 41] and fine[20, 20, 43]
    assert part.info["bridge_mm3"] > 0


def test_without_a_replaces_entry_the_old_union_behaviour_stands(strut_and_head):
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("lug", add)], "cut": [],
                "replaces": {}, "smooth_mm": 0.0, "max_cells": 10 ** 9}
        fine, _, _, info = finish.build(mask, h, np.zeros(3), spec,
                                        verbose=False, setup=setup)
    assert fine[2, 2, 55], "analysed preserve was dropped without a replaces"
    assert info["replaced_preserve"] == []


def test_smoothing_rounds_the_strut_and_leaves_the_cad_head_exact(strut_and_head):
    """The point of doing the smoothing here: the optimised body loses its
    stair steps, the CAD body does not lose a single cell, and the cut
    through the CAD body stays open."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        cut = _box_stl(os.path.join(tmp, "bore.stl"), [18, 18, 40], [22, 22, 60])
        spec = {"voxel_mm": 1.0, "add": [("head", add)],
                "cut": [("bore", cut)], "replaces": {"head": "head"},
                "smooth_mm": 5.0, "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
        files = finish.write_part(part, tmp, "t", verbose=False)
        assert os.path.exists(files["finish_stl"])
        assert part.info["finish_stl_kind"] == "isosurface"
    fine, field = part.fine, part.field
    sm = part.info["smooth"]
    # CAD head: every cell of it (outside the bore) is still solid, exactly.
    head = np.zeros(fine.shape, bool)
    head[12:28, 12:28, 42:58] = True
    bore = np.zeros(fine.shape, bool)
    bore[18:22, 18:22, 40:60] = True
    assert fine[head & ~bore].all(), "smoothing ate into the CAD body"
    assert not fine[bore].any(), "the bore was filled back in"
    # Strut: its vertical corner edge at (10, 10) is rounded off ...
    assert not fine[10, 10, 20], "the strut's corner was not rounded"
    # ... its centre and its faces are still there ...
    assert fine[20, 20, 20] and fine[10, 20, 20] and fine[20, 10, 20]
    # ... it still stands on the plate ...
    assert fine[20, 20, 0]
    # ... and it kept its volume (the threshold was chosen to match).
    assert abs(sm["volume_change_pct"]) < 2.0, sm
    assert 0.3 <= sm["level"] <= 0.5


def test_smoothing_does_not_grow_the_cad_body(strut_and_head):
    """The complaint that produced this test: "it smooths the part I told it
    not to touch". Holding the CAD cells at 1 stops the filter EATING the
    interface and does nothing about it MOVING: until 2026-09-09 the Gaussian
    ran over a mask that already contained the head, so the head blurred into
    the space outside itself and the isosurface came out proud of the CAD
    face, with the convex edges rounded off.

    The head here is 12..28 mm in X and Y. Nothing outside it, away from the
    strut, may be solid -- at any sigma. "Away from the strut" is the whole
    of the exception: where the two bodies actually meet there IS a fillet,
    on purpose, and it is measured in
    `test_the_joint_is_filleted_rather_than_necked`."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 5.0,
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    fine = part.fine
    # The +X face of the head is at x = 28. Read it at z = 56, the top of the
    # head: the strut tops out at z = 40 and the joint's fillet has died away
    # well below here.
    assert fine[27, 20, 56], "the CAD face was eroded"
    assert not fine[28, 20, 56], "the CAD face moved outwards"
    # Its convex vertical edge is still an edge, not a fillet.
    assert fine[27, 27, 56], "the CAD corner was rounded off"
    assert not fine[28, 28, 56]
    # And the head did not gain volume anywhere clear of the joint. The bridge
    # -- analysed preserve kept where the strut meets the CAD -- is
    # optimised-side material and does stick out, below z = 50, by design.
    head = np.zeros(fine.shape, bool)
    head[12:28, 12:28, 42:58] = True
    grown = int((fine[:, :, 54:58] & ~head[:, :, 54:58]).sum())
    assert grown == 0, "%d cells of smear outside the CAD head" % grown


def test_a_pocket_that_opens_on_a_cad_face_stays_open(strut_and_head):
    """What the smear actually broke. The nut pocket in the tall tripod is cut
    to the nominal face and no further; when the smoothing pushed that face
    0.25 mm outwards, what was left was a membrane across the mouth of the
    pocket and the part could not be bolted to the camera."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        # A pocket in the +X face, stopping exactly on it (x = 28).
        cut = _box_stl(os.path.join(tmp, "pocket.stl"), [24, 18, 46], [28, 22, 54])
        spec = {"voxel_mm": 1.0, "add": [("head", add)],
                "cut": [("pocket", cut)], "replaces": {"head": "head"},
                "smooth_mm": 5.0, "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    fine = part.fine
    # Every cell across the mouth of the pocket is open, all the way out.
    assert not fine[24:29, 19:21, 49:51].any(), "the pocket was capped"


def test_a_cad_face_off_the_grid_lands_where_cad_put_it(strut_and_head):
    """A boolean mask can only put a surface on a cell boundary. The occupancy
    field carries the fraction of the cell the CAD fills, so a face at 12.5 mm
    comes out near 12.5 mm rather than snapped to 12 or 13."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        # Cell 27 spans x = 27..28, so a face at 27.75 leaves it 75 % full and
        # cell 28 empty. Deliberately not 27.5: a face on a cell's mid-plane
        # leaves that cell at exactly 0.5, which sits on the threshold and
        # would make the test depend on which side of 0.5 the volume match
        # happened to land.
        add = _box_stl(os.path.join(tmp, "head.stl"),
                       [12.25, 12.25, 42], [27.75, 27.75, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 5.0,
                "max_cells": 10 ** 9, "occupancy_samples": 4}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
        occ = finish.occupancy(add, 1.0, np.zeros(3), part.fine.shape,
                               samples=4)
    # The occupancy carries the sub-cell position: a boolean voxelisation can
    # only say "cell 27 is in", which puts the face at 28.00; 0.75 says where
    # in the cell it actually is, and that is what marching cubes reads.
    assert occ[27, 20, 50] == pytest.approx(0.75, abs=0.13)
    assert occ[28, 20, 50] == pytest.approx(0.0)
    assert occ[20, 20, 50] == pytest.approx(1.0)
    # The thresholded mask agrees with it either way round. Read at the top
    # of the head, clear of the fillet at the joint.
    fine = part.fine
    assert fine[27, 20, 56] and not fine[28, 20, 56]
    assert part.info["smooth"]["cad_tolerance_mm"] == 0.25


def test_occupancy_is_the_fraction_of_the_cell_the_solid_fills(tmp_path):
    box = _box_stl(str(tmp_path / "b.stl"), [1.0, 1.0, 1.0], [3.5, 3.0, 3.0])
    occ = finish.occupancy(box, 1.0, np.zeros(3), (6, 6, 6), samples=4)
    assert occ[2, 2, 2] == pytest.approx(1.0)          # wholly inside
    assert occ[3, 2, 2] == pytest.approx(0.5, abs=0.13)  # half filled in x
    assert occ[5, 2, 2] == pytest.approx(0.0)          # wholly outside
    # samples=1 is the old boolean answer: a half-filled cell is all or nothing
    coarse = finish.occupancy(box, 1.0, np.zeros(3), (6, 6, 6), samples=1)
    assert set(np.unique(coarse)) <= {0.0, 1.0}
    # ... and the DEFAULT must not be that. A silent change of
    # DEFAULT_OCCUPANCY_SAMPLES to 1 would put every CAD face back on the
    # finish grid without a single test going red, which is how the bug this
    # module exists for got shipped in the first place.
    assert finish.DEFAULT_OCCUPANCY_SAMPLES >= 2
    default = finish.occupancy(box, 1.0, np.zeros(3), (6, 6, 6))
    assert 0.0 < default[3, 2, 2] < 1.0, "the default occupancy is boolean"


def test_auto_smoothing_is_a_whole_analysis_voxel(strut_and_head):
    """MEASURED on the tall tripod (3 mm analysis grid, 1.0 mm finish): at half
    a voxel -- what "auto" used to mean -- the staircase is not removed, it is
    rounded into itself, and the legs ship as a row of beads, one per analysis
    cell. The user's words were "the optimised part is still faceted". A full
    voxel removes them and the shell is still closed and one body."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": None,   # "auto"
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    assert part.info["smooth"]["sigma_mm"] == pytest.approx(h)


def test_the_joint_is_filleted_rather_than_necked(strut_and_head):
    """The complaint after the smoothing was fixed: "the connection between
    the part and the optimised body is floating in places".

    Two causes, both real. The optimised material's own blur falls away as it
    approaches the CAD face, so the strut necks just before it lands. And
    `attach` deliberately holds the optimised material one voxel off every
    face it may not join, so the CAD body overhangs that stand-off with
    nothing under it -- MEASURED on the tall tripod, a 2.00 mm gap under a
    30 x 30 mm pedestal everywhere outside its 18 x 18 mm attach patch.

    Neither is a defect in the analysis; both make a part that prints on
    support and reads as broken. So the finish blends towards a blur of both
    bodies where they meet, and that fillet is allowed into the stand-off --
    and only into the stand-off, and only where both bodies are close."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 5.0,
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    fine = part.fine
    head = np.zeros(fine.shape, bool)
    head[12:28, 12:28, 42:58] = True
    # A collar of material outside the CAD body, at the height where the strut
    # lands on it.
    assert int((fine & ~head)[:, :, 42:48].sum()) > 0, "the joint has no fillet"
    # It dies away with height: the top of the head is untouched.
    assert int((fine & ~head)[:, :, 54:58].sum()) == 0
    # And it is a fillet, not a collar. Unbounded, the same blend wraps the
    # whole CAD body in a cushion wider than either part -- which is what the
    # user saw next: "the part looks like it is sitting on a cushion". The
    # blend is bounded to cells within a bridge-width of BOTH bodies, so it
    # cannot reach further out than the strut it is filleting.
    strut = np.zeros(fine.shape, bool)
    strut[10:30, 10:30, :] = True
    reach = np.argwhere(fine[:, :, 42:48] & ~head[:, :, 42:48])
    assert reach[:, 0].min() >= 10 - int(h) and reach[:, 0].max() <= 30 + int(h)
    # And the strut does not neck as it approaches: its cross-section at the
    # CAD face is no smaller than it is a couple of centimetres lower down.
    at_face = int(fine[:, :, 41].sum())
    below = int(fine[:, :, 20].sum())
    assert at_face >= 0.9 * below, (at_face, below)
    assert part.info["smooth"]["bridge_mm"] == pytest.approx(h)


def test_the_attach_stand_off_under_a_cad_body_is_bridged():
    """`attach` holds the optimised material one voxel off every face it may
    not join. Where the CAD body is WIDER than the material under it -- which
    is the usual case for an interface on a pedestal -- what ships is the CAD
    body overhanging that stand-off with nothing under it. MEASURED on the
    tall tripod: a 2.00 mm gap under a 30 x 30 mm pedestal everywhere outside
    its 18 x 18 mm attach patch. The finish stage closes it; `bridge_mm: false`
    leaves it.

    A narrower strut than `strut_and_head` has, because that fixture's strut
    is wider than its head and there is no overhang to bridge."""
    h = 10.0
    shape = (6, 6, 8)
    mask = np.zeros(shape, bool)
    mask[2:4, 2:4, 0:4] = True                   # strut, 20..40 mm in X/Y
    preserve = np.zeros(shape, bool)
    preserve[1:5, 1:5, 4:6] = True               # head, 10..50 mm, as analysed
    mask |= preserve
    shell = np.zeros(shape, bool)
    shell[1:5, 1:5, 3:4] = True                  # the stand-off under the head
    shell[2:4, 2:4, 3:4] = False                 # ... except where it joins
    setup = _setup_for(mask, h, preserve, no_attach=shell)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [48, 48, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 5.0,
                "max_cells": 10 ** 9}
        bridged = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                    setup=setup)
        spec["bridge_mm"] = 0.0
        bare = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    # Under the head, clear of the strut, inside the stand-off.
    zone = np.zeros(bridged.fine.shape, bool)
    zone[14:19, 25:35, 33:39] = True
    assert bridged.fine[zone].any(), "the stand-off was left as a gap"
    assert not bare.fine[zone].any(), "bridge_mm: false still bridged it"
    assert bridged.info["smooth"]["bridge_mm3"] > 0
    assert bare.info["smooth"]["bridge_mm3"] == 0


def test_a_clearance_the_user_declared_is_not_bridged(strut_and_head):
    """The fillet may close the stand-off `attach` put there -- that is the
    tool's own doing. A clearance the study declared, however close to the
    joint it passes, stays empty: the room to turn a knob is not the finish
    stage's to fill in."""
    mask, h, preserve = strut_and_head
    obstacle = np.zeros(mask.shape, bool)
    obstacle[3, 1:3, 3:5] = True             # right at the strut/head corner
    setup = _setup_for(mask, h, preserve, obstacle=obstacle)
    assert not setup.no_attach.any(), "this obstacle must not be a shell"
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 5.0,
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    ob = np.zeros(part.fine.shape, bool)
    ob[30:40, 10:30, 30:50] = True
    assert not part.fine[ob].any(), "the fillet was let into a declared clearance"


def test_the_first_layer_keeps_the_smoothed_outline(strut_and_head):
    """"The bottom was not smoothed." It was not: the plate layer used to be
    pinned at 1 cell by cell, which pins the analysis grid's staircase, so the
    foot shipped as a smooth dome standing on a one-cell skirt with a jagged
    outline. The plate is where the part was CUT, so the filter is given a
    reflection of the material below it instead -- symmetric, so the
    cross-section is neither eroded nor grown, and the foot keeps its
    smoothed outline, stays flat and cannot lift."""
    mask, h, preserve = strut_and_head
    setup = _setup_for(mask, h, preserve)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 5.0,
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    fine = part.fine
    assert part.info["smooth"]["plate_mirrored"] is True
    # It still stands on the plate: the first layer carries material.
    assert fine[:, :, 0].any(), "the foot lifted off the plate"
    # The first layer is the shape just above it, not a separate one: pinning
    # the staircase left the first layer standing proud of the smoothed body
    # by up to a cell all the way round. Compare it with the layer three cells
    # up, which the filter has shaped freely.
    first = int(fine[:, :, 0].sum())
    third = int(fine[:, :, 3].sum())
    assert abs(first - third) <= 0.05 * max(third, 1), (first, third)


def test_smoothing_stays_out_of_obstacles(strut_and_head):
    """A cell the optimiser was kept out of -- the no-contact shell around a
    preserve body, a clearance -- stays empty after smoothing too."""
    mask, h, preserve = strut_and_head
    obstacle = np.zeros(mask.shape, bool)
    obstacle[3, 1:3, 0:4] = True                 # a wall right beside the strut
    setup = _setup_for(mask, h, preserve, obstacle=obstacle)
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "head.stl"), [12, 12, 42], [28, 28, 58])
        spec = {"voxel_mm": 1.0, "add": [("head", add)], "cut": [],
                "replaces": {"head": "head"}, "smooth_mm": 8.0,
                "max_cells": 10 ** 9}
        part = finish.build_part(mask, h, np.zeros(3), spec, verbose=False,
                                 setup=setup)
    ob = np.zeros(part.fine.shape, bool)
    ob[30:40, 10:30, 0:40] = True
    assert not part.fine[ob].any()


def test_parse_reads_smooth_and_replaces(tmp_path):
    add = _box_stl(str(tmp_path / "head.stl"), [0, 0, 0], [1, 1, 1])
    spec = finish.parse({"voxel_mm": 0.5,
                         "add": {"mount": {"stl": "head.stl",
                                           "replaces": "mount_head"},
                                 "lug": {"stl": "head.stl", "replaces": None}},
                         "smooth_mm": 2.0}, str(tmp_path))
    assert spec["replaces"] == {"mount": "mount_head"}
    assert spec["smooth_mm"] == 2.0
    assert finish.parse({"add": {"mount": "head.stl"}},
                        str(tmp_path))["replaces"] == {"mount": "mount"}
    assert finish.parse({"add": {"mount": "head.stl"}},
                        str(tmp_path))["smooth_mm"] is None      # auto
    assert finish.parse({"add": {"mount": "head.stl"}, "smooth_mm": False},
                        str(tmp_path))["smooth_mm"] == 0.0
    with pytest.raises(finish.FinishError, match="smooth_mm"):
        finish.parse({"add": {"mount": "head.stl"}, "smooth_mm": "lots"},
                     str(tmp_path))


def test_a_cad_body_taller_than_the_domain_is_not_clipped(coarse):
    """The finish grid grows to hold `add`; it used to end at the domain wall.

    The optimised body cannot exist outside the domain it was solved in. A CAD
    body can: the tall tripod's camera mount is 84.5 mm tall on a domain whose
    ceiling is 40 mm above the interface, and the top half of it was silently
    cut off. Growth is in whole analysis voxels, so the resampled result must
    still stand in exactly the same millimetres.
    """
    mask, h, origin = coarse                       # 0..60 mm, 10 mm voxels
    with tempfile.TemporaryDirectory() as tmp:
        # 45 mm of mast on a domain that stops at 60.
        add = _box_stl(os.path.join(tmp, "mast.stl"),
                       [20, 20, 40], [30, 30, 105])
        spec = {"voxel_mm": 1.0, "add": [("mast", add)], "cut": [],
                "max_cells": 10 ** 9}
        fine, fine_h, forigin, info = finish.build(mask, h, origin, spec,
                                                   verbose=False)

    assert info["grid_grown_voxels"]["hi"][2] > 0
    assert info["grid_grown_voxels"]["lo"] == [0, 0, 0]
    # The result is where it was analysed: 10..50 mm on every axis.
    idx = np.argwhere(fine)
    assert np.allclose(forigin + idx.min(axis=0) * fine_h, [10, 10, 10])
    # ... and the mast reaches its full height, with void above it to close on.
    top = forigin[2] + (idx[:, 2].max() + 1) * fine_h
    assert top == pytest.approx(105.0, abs=fine_h)
    assert fine.shape[2] * fine_h + forigin[2] > 105.0


def test_a_cad_body_inside_the_domain_does_not_grow_the_grid(coarse):
    """Only a body that crosses the wall moves it. Existing results stay put."""
    mask, h, origin = coarse
    with tempfile.TemporaryDirectory() as tmp:
        add = _box_stl(os.path.join(tmp, "lug.stl"), [20, 20, 50], [30, 30, 60])
        spec = {"voxel_mm": 1.0, "add": [("lug", add)], "cut": [],
                "max_cells": 10 ** 9}
        fine, fine_h, forigin, info = finish.build(mask, h, origin, spec,
                                                   verbose=False)
    assert info["grid_grown_voxels"] == {"lo": [0, 0, 0], "hi": [0, 0, 0]}
    assert np.allclose(forigin, origin)
    assert list(info["grid_shape"]) == [60, 60, 60]

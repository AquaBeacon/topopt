"""Isolated meshes, self-intersections, and a boolean that fails (spec 19.3).

`shell_report` already named an STL with holes in it. Two failures it did not
see, and both of them ship silently:

- **more than one body.** A threshold or a boolean can leave an island; the
  volume total, the safety factor and the bounding box are all still
  reasonable, and what arrives at the printer is a part plus a crumb.
- **surfaces that pass through each other.** Two solids unioned by something
  that did not actually cut them. Every number about it is meaningless and
  nothing in the summary says so.

The third is the finish stage's boolean. If it throws, what must *not* be on
disk afterwards is a half-cut part that looks finished.

The two tuning decisions here are load-bearing and each has a test of its own:
faces that merely touch are not an intersection, and a shell whose coplanar
faces have been merged is one body with many non-conforming edges rather than
65 bodies. Get either wrong and the check fires on every file this tool
writes, at which point it stops being read.
"""

import os

import numpy as np
import pytest

from topopt import finish, geom
from topopt.meshout import _write_binary_stl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
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
    return np.asarray(tris, float)


# ------------------------------------------------------------- the findings

def test_two_separate_boxes_are_two_bodies():
    r = geom.shell_report(np.vstack([_box([0, 0, 0], [10, 10, 10]),
                                     _box([20, 0, 0], [30, 10, 10])]))
    assert r["components"] == 2
    assert r["open_edges"] == 0, "each box is closed; only the count is wrong"


def test_one_box_is_one_body():
    r = geom.shell_report(_box([0, 0, 0], [10, 10, 10]))
    assert r["components"] == 1 and r["self_intersections"] == 0


def test_two_boxes_that_interpenetrate_are_reported():
    r = geom.shell_report(np.vstack([_box([0, 0, 0], [10, 10, 10]),
                                     _box([5, 5, 5], [15, 15, 15])]))
    assert r["self_intersections"] > 0
    ex = r["self_intersection_examples"][0]
    assert len(ex["triangles"]) == 2 and len(ex["near_mm"]) == 3


def test_boxes_that_only_touch_are_not_intersecting():
    """Face to face and corner to corner. Both are contact; calling either an
    intersection would fire on every voxel export this tool writes."""
    for a, b in (([10, 0, 0], [20, 10, 10]), ([10, 10, 10], [20, 20, 20])):
        r = geom.shell_report(np.vstack([_box([0, 0, 0], [10, 10, 10]),
                                         _box(a, b)]))
        assert r["self_intersections"] == 0, (a, b)


def test_a_corner_contact_is_still_two_bodies_by_edge_count():
    """The two readings disagree here, on purpose -- `by="edge"` is the strict
    one and is what explains a merged export's non-conforming edges."""
    tri = np.vstack([_box([0, 0, 0], [10, 10, 10]),
                     _box([10, 10, 10], [20, 20, 20])])
    assert geom.mesh_components(tri, by="edge") == 2


# --------------------------------------------- quiet on the recorded shapes

RECORDED = [
    ("osmo_tripod/out/osmo_tripod.stl", 2052),
    # v4's re-analysis shell; the model was re-run in place on 2026-09-08
    # (v5, `attach` + smoothed finish), so the measured file is in the archive.
    ("tall_tripod/archive/v4_before_attach_2026-09-08/03_optimization/"
     "reanalysis/tall_tripod_study_t40.stl", 1408),
]


@pytest.mark.parametrize("rel,ntri", RECORDED)
def test_the_recorded_shapes_are_one_clean_body(rel, ntri):
    """The regression that keeps the check usable. If either of these starts
    reporting an intersection, the test is wrong before the file is."""
    path = os.path.join(ROOT, *rel.split("/"))
    if not os.path.exists(path):
        pytest.skip("reference model not present next to the package")
    r = geom.shell_report(geom.load_stl(path))
    assert r["triangles"] == ntri
    assert r["components"] == 1
    assert r["self_intersections"] == 0


def test_a_merged_blocky_export_is_one_body_with_many_shells():
    """`write_blocky_stl(merge=True)` leaves T-junctions: a merged rectangle's
    edge is subdivided by its neighbours, so exact edge matching reports
    thousands of "open" edges and dozens of shells on a solid that is neither
    open nor in pieces. Measured on the tall_tripod print file as shipped by
    v4 (2026-08-06 .. 2026-09-08): 2,502 open edges, 65 edge-shells, one
    body. v5's print file is the smoothed isosurface, which is watertight, so
    the merged-blocky specimen is the archived one."""
    path = os.path.join(ROOT, "tall_tripod", "archive",
                        "v4_before_attach_2026-09-08", "04_print",
                        "tall_tripod.stl")
    if not os.path.exists(path):
        pytest.skip("reference model not present next to the package")
    r = geom.shell_report(geom.load_stl(path))
    assert r["components"] == 1
    assert r["edge_components"] > 1 and r["open_edges"] > 0
    assert r["self_intersections"] == 0


def test_a_shell_too_big_for_the_pair_search_says_so_rather_than_stalling():
    tri = _box([0, 0, 0], [1, 1, 1])
    old = geom._INTERSECT_TRIANGLE_CAP
    try:
        geom._INTERSECT_TRIANGLE_CAP = 4
        got = geom.self_intersections(tri)
    finally:
        geom._INTERSECT_TRIANGLE_CAP = old
    assert got["pairs"] is None and "not checked" in got["note"]


# ----------------------------------------------- a boolean that gives up

def _cube_result():
    mask = np.zeros((6, 6, 6), bool)
    mask[1:5, 1:5, 1:5] = True
    return mask, 10.0, np.zeros(3)


def test_a_failed_boolean_leaves_the_uncut_shape(monkeypatch, tmp_path):
    """What must not happen: the add lands, the cut throws, and the file on
    disk is a part with a lug on it and no bore through the lug."""
    mask, h, origin = _cube_result()
    add = tmp_path / "add.stl"
    cut = tmp_path / "cut.stl"
    _write_binary_stl(str(add), _box([20, 20, 50], [30, 30, 60]))
    _write_binary_stl(str(cut), _box([25, 25, 20], [35, 35, 30]))

    calls = {"n": 0}

    def flaky(path, **kw):
        calls["n"] += 1
        if calls["n"] == 2:                       # the cut
            raise RuntimeError("the kernel gave up")
        return _real(path, **kw)

    _real = finish.voxelize_stl
    monkeypatch.setattr(finish, "voxelize_stl", flaky)

    spec = {"voxel_mm": 5.0, "add": [("lug", str(add))],
            "cut": [("bore", str(cut))], "max_cells": 10 ** 8}
    fine, fine_h, forigin, info = finish.build(mask, h, origin, spec,
                                               verbose=False)

    assert info.get("rolled_back") is True
    assert "the kernel gave up" in info["error"]
    assert info["added"] == [] and info["cut"] == []
    # Bit for bit the resampled optimisation result: no lug, no bore, no
    # half-finished anything.
    f = int(round(h / 5.0))
    expect = np.repeat(np.repeat(np.repeat(mask, f, 0), f, 1), f, 2)
    assert np.array_equal(fine, expect)
    assert info["final_mm3"] == info["resampled_mm3"]


def test_the_written_file_is_the_rolled_back_shape(monkeypatch, tmp_path):
    """`never leave a half-cut file` is about the file, so check the file."""
    mask, h, origin = _cube_result()
    cut = tmp_path / "cut.stl"
    _write_binary_stl(str(cut), _box([25, 25, 20], [35, 35, 30]))
    monkeypatch.setattr(finish, "voxelize_stl",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no kernel")))
    spec = {"voxel_mm": 5.0, "add": [], "cut": [("bore", str(cut))],
            "max_cells": 10 ** 8}
    fine, fine_h, forigin, info = finish.build(mask, h, origin, spec,
                                               verbose=False)
    finish.write(fine, fine_h, forigin, str(tmp_path), "part", info=info,
                 verbose=False)
    tri = geom.load_stl(os.path.join(str(tmp_path), "part_finish.stl"))
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    assert np.allclose(lo, [10, 10, 10]) and np.allclose(hi, [50, 50, 50])


def test_a_boolean_that_works_is_untouched(tmp_path):
    """The counter-test. Roll-back must not be reachable on a good run."""
    mask, h, origin = _cube_result()
    cut = tmp_path / "cut.stl"
    _write_binary_stl(str(cut), _box([15, 15, 5], [25, 25, 55]))
    spec = {"voxel_mm": 5.0, "add": [], "cut": [("bore", str(cut))],
            "max_cells": 10 ** 8}
    fine, _h, _o, info = finish.build(mask, h, origin, spec, verbose=False)
    assert "error" not in info and info["cut"][0]["removed_mm3"] > 0
    assert info["final_mm3"] < info["resampled_mm3"]

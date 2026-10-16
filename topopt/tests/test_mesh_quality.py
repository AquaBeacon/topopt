"""A leaky input STL has to name itself, at setup, not much later.

Ray-parity voxelisation is exact for a watertight mesh and *nearly* right for a
holed one -- it degrades to a scatter of stray voxels near the hole rather than
failing. That is the worst possible behaviour for a defect, because the
symptom appears far from the cause and looks like a solver problem.

The second half of this file is the part that took a correction: an STL with
zero open edges but a couple of non-manifold edges is **not** leaky. Two solids
meeting along a line is ordinary CSG output -- `osmo_tripod.stl` has exactly
that -- and warning about it would put a line on every run and train the reader
to ignore warnings.
"""

import os

import numpy as np
import pytest

from topopt.geom import shell_report

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OSMO = os.path.join(ROOT, "osmo_tripod")


def _cube_tris(size=10.0):
    """A closed axis-aligned cube, 12 triangles."""
    s = size
    v = np.array([[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0],
                  [0, 0, s], [s, 0, s], [s, s, s], [0, s, s]], float)
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return np.array([[v[a], v[b], v[c]] for a, b, c in faces])


def test_a_closed_cube_is_closed():
    r = shell_report(_cube_tris())
    assert r["closed_shell"]
    assert r["open_edges"] == 0
    assert r["triangles"] == 12


def test_a_hole_is_found_and_counted():
    holed = _cube_tris()[:-1]                   # drop one triangle
    r = shell_report(holed)
    assert not r["closed_shell"]
    assert r["open_edges"] == 3, "a missing triangle leaves exactly 3 open edges"


def test_vertices_are_matched_with_tolerance():
    """STL stores every triangle independently, so shared corners agree only
    to float32. Exact matching would report a perfect mesh as full of holes."""
    tris = _cube_tris().astype(np.float32).astype(float)
    tris += (np.random.default_rng(0).random(tris.shape) - 0.5) * 1e-9
    assert shell_report(tris)["closed_shell"]


def test_degenerate_triangles_are_reported_not_counted_as_holes():
    tris = np.concatenate([_cube_tris(),
                           np.zeros((1, 3, 3))])       # a zero-area triangle
    r = shell_report(tris)
    assert r["degenerate"] == 1
    assert r["open_edges"] == 0


@pytest.mark.osmo
def test_the_reference_part_has_no_holes():
    """`osmo_tripod.stl` is CSG output: no open edges, but a couple of
    non-manifold edges where two solids touch. It must not be called leaky."""
    p = os.path.join(OSMO, "out", "osmo_tripod.stl")
    if not os.path.exists(p):
        pytest.skip("reference part not present")
    from topopt.voxel import load_stl
    r = shell_report(load_stl(p))
    assert r["open_edges"] == 0, "the printed part has a hole in its mesh"


@pytest.mark.osmo
def test_setup_stays_quiet_about_non_manifold_only_meshes():
    """The regression that matters: a warning on every run is a warning nobody
    reads. Only open edges may raise one."""
    from topopt.loadcase import load_case_from_json
    from topopt.designspace import build_setup, validate

    p = os.path.join(OSMO, "gd_mount_study.json")
    if not os.path.exists(p):
        pytest.skip("reference study not present")
    study = load_case_from_json(p)
    setup = build_setup(study)
    report = validate(setup, study)
    leaky = [w for w in report["warnings"] if "not watertight" in w]
    assert not leaky, "spurious watertight warning: %s" % leaky

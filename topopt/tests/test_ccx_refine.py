"""The STL shell is refined to the target size before Gmsh sees it.

Gmsh keeps a merged STL's triangles as the surface mesh and only fills the
inside, so without this step the boundary -- where the stress is read and the
supports are mapped -- stays at the CAD export's resolution whatever
`--mesh-mm` says (osmo_tripod: 2048 triangles in, 2048 boundary faces out at
4, 2 and 1 mm alike; the "mesh convergence" study refined nothing that
mattered).
"""

import numpy as np
import pytest

from topopt import ccx
from topopt.geom import shell_report


def _box(a=20.0, b=10.0, c=5.0):
    """Twelve triangles, two per face, the way OpenSCAD exports a cube."""
    p = np.array([[0, 0, 0], [a, 0, 0], [a, b, 0], [0, b, 0],
                  [0, 0, c], [a, 0, c], [a, b, c], [0, b, c]], float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tri = []
    for q in quads:
        tri.append(p[[q[0], q[1], q[2]]])
        tri.append(p[[q[0], q[2], q[3]]])
    return np.array(tri)


def _longest_edge(tri):
    return max(np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1).max(),
               np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1).max(),
               np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1).max())


def _volume(tri):
    return float(np.einsum("ij,ij->i", tri[:, 0],
                           np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)


def test_no_edge_is_longer_than_the_target_afterwards():
    out, passes = ccx.refine_triangles(_box(), 2.0)
    assert _longest_edge(_box()) > 2.0
    assert _longest_edge(out) <= 2.0 + 1e-9
    assert passes >= 1


def test_the_shell_stays_closed_and_keeps_its_volume():
    out, _ = ccx.refine_triangles(_box(), 2.0)
    rep = shell_report(out)
    assert rep["closed_shell"], rep
    assert rep["open_edges"] == 0 and rep["non_manifold_edges"] == 0
    assert _volume(out) == pytest.approx(20 * 10 * 5, rel=1e-9)


def test_every_new_vertex_lies_on_the_original_surface():
    """Splitting adds points on edges only; the box's faces are the planes
    x,y,z in {0, a}, so every vertex must sit on one of them."""
    out, _ = ccx.refine_triangles(_box(), 3.0)
    v = out.reshape(-1, 3)
    on_face = ((np.isclose(v[:, 0], 0) | np.isclose(v[:, 0], 20))
               | (np.isclose(v[:, 1], 0) | np.isclose(v[:, 1], 10))
               | (np.isclose(v[:, 2], 0) | np.isclose(v[:, 2], 5)))
    assert on_face.all()


def test_a_shell_already_fine_enough_is_returned_unchanged():
    tri = _box()
    out, passes = ccx.refine_triangles(tri, 1000.0)
    assert passes == 0
    assert np.array_equal(out, tri)


def test_refine_stl_reports_what_it_did(tmp_path):
    from topopt.stepio import write_binary_stl
    tri = _box()
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    src = str(tmp_path / "box.stl")
    write_binary_stl(src, [(n[i], tri[i, 0], tri[i, 1], tri[i, 2])
                           for i in range(len(tri))])
    dst = str(tmp_path / "box_fine.stl")
    info = ccx.refine_stl(src, dst, 2.0)
    assert info["triangles_before"] == 12
    assert info["triangles_after"] > 12
    assert info["max_edge_before_mm"] > 2.0
    assert info["max_edge_after_mm"] <= 2.0
    assert ccx.shell_check(dst)["closed_shell"]


def test_the_geo_uses_hxt_and_meshes_the_shell_it_is_given(tmp_path):
    geo = str(tmp_path / "part.geo")
    ccx.write_geo(geo, str(tmp_path / "part_h2.00_shell.stl"), 2.0)
    txt = open(geo, encoding="utf-8").read()
    assert "Mesh.Algorithm3D = 10" in txt
    assert "part_h2.00_shell.stl" in txt
    assert "Mesh.ElementOrder = 2" in txt

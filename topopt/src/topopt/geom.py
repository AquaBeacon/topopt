"""Triangle-soup geometry: sections, support area, swept-clearance.

These work directly on an STL's triangles rather than on a voxel grid, because
all three questions they answer are about *surfaces*:

    cross_section    what is the real width at z = 23? (read dimensions off a
                     drawing instead of trusting the model)
    support_area     which faces actually need support, with the bed-contact
                     face excluded -- `topopt print` counts the bed itself
                     as overhang, so a flat-bottomed part reads as "24 %
                     overhang" when it in fact needs none
    points_inside    does a moving part sweep through a fixed one?

`points_inside` casts rays and therefore needs a watertight mesh. A mesh that
came through `stepio` is not watertight (cylindrical faces are dropped), so
judge those with a bounding box instead -- the box contains the true solid, so
"the fixed part clears the box" proves "the fixed part clears the body".
"""

import numpy as np

from .voxel import load_stl

__all__ = [
    "load_stl", "cross_section", "section_spans", "support_area",
    "sample_points", "points_inside", "min_gap", "rotation_about_x",
]


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def cross_section(tri, axis, val):
    """Segments where the plane `axis = val` cuts the triangle soup.

    Returns a list of (p, q) point pairs in 3D.
    """
    segs = []
    a = tri[:, :, axis] - val
    hit = ~((a > 1e-7).all(1) | (a < -1e-7).all(1))
    for t, av in zip(tri[hit], a[hit]):
        pts = []
        for i in range(3):
            j = (i + 1) % 3
            if abs(av[i]) < 1e-7:
                pts.append(t[i])
            elif av[i] * av[j] < 0:
                s = av[i] / (av[i] - av[j])
                pts.append(t[i] + s * (t[j] - t[i]))
        if len(pts) >= 2:
            segs.append((pts[0], pts[1]))
    return segs


def section_spans(segs, ax):
    """Extent of a section along axis `ax` -- a rough width report."""
    vals = sorted(set(round(p[ax], 2) for s in segs for p in s))
    return (min(vals), max(vals)) if vals else (None, None)


# --------------------------------------------------------------------------
# overhang / support
# --------------------------------------------------------------------------

# Faces within this much (in normal-z units) of the overhang threshold are
# counted as overhang: "exactly at the limit" means "needs support".
OVERHANG_TOL = 1e-6


def support_area(tri, axis=2, angle=45.0, bed_tol=0.4):
    """Face area that genuinely needs support, printed along +`axis`.

    A downward-facing facet is only counted if it is steeper than `angle`
    degrees from horizontal *and* is not the bed-contact face itself (normal
    straight down and sitting within `bed_tol` of the lowest point).
    """
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm = np.linalg.norm(cr, axis=1)
    area = nrm / 2
    nz = cr[:, axis] / np.maximum(nrm, 1e-12)

    zmin = tri[:, :, axis].min()
    cz = tri[:, :, axis].mean(1)
    bed = (nz < -0.99) & (cz < zmin + bed_tol)
    # A face at exactly the threshold angle needs support. The comparison
    # used to be a strict inequality on a rounded normal, so a roof drawn at
    # 45.0 deg came out as 0.0 mm2 or 40.2 mm2 depending on nothing but the
    # bore diameter next to it (known-issue D1, lessons-learned 27). The
    # tolerance is in units of the normal's z component, i.e. about 1e-4 deg
    # -- far below anything a slicer distinguishes, and enough to make the
    # verdict a property of the geometry rather than of the rounding.
    over = nz <= -np.sin(np.radians(angle)) + OVERHANG_TOL
    real = over & ~bed

    vol = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)

    out = {
        "size_mm": (hi - lo).tolist(),
        "volume_mm3": float(vol),
        "area_mm2": float(area.sum()),
        "bed_mm2": float(area[bed].sum()),
        "support_mm2": float(area[real].sum()),
        "support_pct": float(100 * area[real].sum() / max(area.sum(), 1e-12)),
        "bed_pct": float(100 * area[bed].sum() / max(area.sum(), 1e-12)),
        "angle_deg": float(angle),
        "axis": int(axis),
    }
    if real.any():
        p = tri[real].reshape(-1, 3)
        ang = 90 - np.degrees(np.arcsin(np.clip(-nz[real], 0, 1)))
        out["support_bbox"] = [p.min(0).tolist(), p.max(0).tolist()]
        out["shallowest_deg"] = float(ang.min())
    return out


# --------------------------------------------------------------------------
# clearance
# --------------------------------------------------------------------------

def weld(tri, tol=1e-5):
    """-> (ntri, 3) int vertex ids, corners equal to within `tol` sharing one.

    STL stores every triangle independently, so shared corners agree only to
    float32 precision. Everything below that asks "do these two triangles
    touch" has to ask it of welded ids, not of coordinates.
    """
    q = np.round(np.asarray(tri, float) / tol).astype(np.int64)
    _uniq, idx = np.unique(q.reshape(-1, 3), axis=0, return_inverse=True)
    return idx.reshape(-1, 3)


def mesh_components(tri, tol=1e-5, v=None, by="vertex"):
    """How many separate pieces the triangle soup is in. -> int

    Two readings, and they answer different questions:

    - `by="vertex"` -- triangles are one piece if they share any welded
      corner. This is the **body** count: one part or two, i.e. did a
      threshold or a boolean leave an island behind.
    - `by="edge"` -- one piece only if they share a whole edge. This is the
      **conforming-shell** count, and on a mesh with T-junctions it is much
      larger than the body count without anything being wrong: merging the
      coplanar faces of a voxel export (`write_blocky_stl(merge=True)`)
      leaves a big rectangle whose edge is subdivided by its neighbours, so
      the recorded `tall_tripod_study_t40_finish.stl` reads as 65 shells and
      one body. Reported alongside because it is the same measurement that
      explains that file's 2,502 "open" edges.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    tri = np.asarray(tri, float)
    if tri.shape[0] == 0:
        return 0
    if v is None:
        v = weld(tri, tol)
    ntri = v.shape[0]
    if by == "vertex":
        rows = np.repeat(np.arange(ntri), 3)
        inc = coo_matrix((np.ones(rows.size, np.int8), (rows, v.reshape(-1))),
                         shape=(ntri, int(v.max()) + 1)).tocsr()
        return int(connected_components(inc @ inc.T, directed=False)[0])
    e = np.concatenate([v[:, [0, 1]], v[:, [1, 2]], v[:, [2, 0]]])
    owner = np.tile(np.arange(ntri), 3)
    keep = e[:, 0] != e[:, 1]
    e, owner = np.sort(e[keep], axis=1), owner[keep]
    if e.shape[0] == 0:
        return 0
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, owner = e[order], owner[order]
    same = np.all(e[1:] == e[:-1], axis=1)
    a, b = owner[:-1][same], owner[1:][same]
    g = coo_matrix((np.ones(a.size, np.int8), (a, b)), shape=(ntri, ntri))
    return int(connected_components(g, directed=False)[0])


# Triangles whose bounding boxes never share a grid cell cannot intersect, so
# the pair test only ever sees neighbours. The cell size is the mean triangle
# extent: smaller and a big triangle lands in thousands of cells, larger and
# every triangle is a candidate against every other.
#
# The cap is set from the largest shell this tool actually writes -- the
# 40,110-triangle refined osmo_tripod shell, 1.4 s -- with headroom. Past it
# the answer is "not checked" rather than a check that turns a shell report
# into a coffee break, because `ccx` runs one of these before every mesh.
_INTERSECT_TRIANGLE_CAP = 60_000

# The other half of the same ceiling: total (triangle, cell) insertions.
# A uniform 40,110-triangle shell makes about one insertion per triangle.
_INTERSECT_CELL_CAP = 2_000_000


def self_intersections(tri, tol=1e-5, v=None, max_report=8):
    """Pairs of triangles that pass through each other. -> dict

    Only *transversal* crossings count: each triangle must have vertices
    strictly on both sides of the other's plane. Coplanar and edge-touching
    pairs are not intersections here, and that is the whole reason this is
    usable on the shapes this tool writes -- a blocky STL is coplanar
    neighbours from end to end, and a test that counted those would report
    thousands of "self-intersections" on every voxel export and be switched
    off within a day. Pairs sharing any welded vertex are skipped for the
    same reason.

    What it does catch is the failure it exists for: two solids unioned by a
    kernel that did not cut them, i.e. surfaces that genuinely pass through
    one another.
    """
    tri = np.asarray(tri, float)
    n = tri.shape[0]
    out = {"pairs": 0, "examples": []}
    if n < 2:
        return out
    if n > _INTERSECT_TRIANGLE_CAP:
        return {"pairs": None, "examples": [],
                "note": "%d triangles is over the %d the pair search is sized "
                        "for; not checked" % (n, _INTERSECT_TRIANGLE_CAP)}
    if v is None:
        v = weld(tri, tol)

    lo, hi = tri.min(axis=1), tri.max(axis=1)
    span = float(np.mean(hi - lo)) or 1.0
    org = lo.min(axis=0)
    cell = np.maximum(span, 1e-9)
    c0 = np.floor((lo - org) / cell).astype(np.int64)
    c1 = np.floor((hi - org) / cell).astype(np.int64)

    # One triangle far larger than the mean -- a CAD export with a ground
    # plane under fine detail -- spans (big/mean)^3 cells, and 200^3 is not
    # hard to reach. The recorded shells are uniform and would never show
    # this, so the ceiling is on the work rather than on the triangle count.
    spans = np.prod(c1 - c0 + 1, axis=1)
    if int(spans.sum()) > _INTERSECT_CELL_CAP:
        return {"pairs": None, "examples": [],
                "note": "the triangles are too unevenly sized to bucket "
                        "cheaply (%d cell insertions, ceiling %d); not checked"
                        % (int(spans.sum()), _INTERSECT_CELL_CAP)}

    buckets = {}
    for t in range(n):
        for i in range(c0[t, 0], c1[t, 0] + 1):
            for j in range(c0[t, 1], c1[t, 1] + 1):
                for k in range(c0[t, 2], c1[t, 2] + 1):
                    buckets.setdefault((i, j, k), []).append(t)

    pairs = set()
    for ts in buckets.values():
        if len(ts) < 2:
            continue
        arr = np.asarray(ts)
        ii, jj = np.triu_indices(arr.size, 1)
        for a, b in zip(arr[ii], arr[jj]):
            if a > b:
                a, b = b, a
            pairs.add((int(a), int(b)))
    if not pairs:
        return out

    pa = np.fromiter((p[0] for p in pairs), int, len(pairs))
    pb = np.fromiter((p[1] for p in pairs), int, len(pairs))
    # A shared corner is contact, not interpenetration.
    share = np.zeros(pa.size, bool)
    for i in range(3):
        for j in range(3):
            share |= v[pa, i] == v[pb, j]
    keep = ~share
    pa, pb = pa[keep], pb[keep]
    if pa.size == 0:
        return out

    hit = _triangles_cross(tri[pa], tri[pb], span)
    idx = np.nonzero(hit)[0]
    out["pairs"] = int(idx.size)
    out["examples"] = [
        {"triangles": [int(pa[i]), int(pb[i])],
         "near_mm": [round(float(x), 3)
                     for x in 0.5 * (tri[pa[i]].mean(0) + tri[pb[i]].mean(0))]}
        for i in idx[:max_report]]
    return out


def _side(tri, other, eps):
    """Signed distance of each vertex of `other` from each `tri`'s plane."""
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    unit = nrm / np.maximum(ln, 1e-30)[:, None]
    d = np.einsum("pi,pvi->pv", unit, other - tri[:, 0][:, None, :])
    return nrm, np.where(np.abs(d) < eps, 0.0, d)


def _triangles_cross(A, B, scale):
    """Möller's transversal triangle-triangle test, vectorised. -> bool (npair,)

    Transversal only: both triangles must cross the other's plane strictly, so
    "just touching" is not a hit. See `self_intersections` for why that is the
    definition this tool wants rather than a limitation of the test.
    """
    eps = 1e-9 * max(float(scale), 1.0)
    n2, dA = _side(B, A, eps)      # A's vertices against B's plane
    n1, dB = _side(A, B, eps)      # B's vertices against A's plane
    ok = ((dA > 0).any(1) & (dA < 0).any(1) & (dA != 0).all(1)
          & (dB > 0).any(1) & (dB < 0).any(1) & (dB != 0).all(1))
    res = np.zeros(A.shape[0], bool)
    if not ok.any():
        return res
    A, B, dA, dB = A[ok], B[ok], dA[ok], dB[ok]
    D = np.cross(n1[ok], n2[ok])
    axis = np.argmax(np.abs(D), axis=1)
    rows = np.arange(A.shape[0])
    pA = A[rows[:, None], np.arange(3)[None, :], axis[:, None]]
    pB = B[rows[:, None], np.arange(3)[None, :], axis[:, None]]
    ia = _interval(pA, dA)
    ib = _interval(pB, dB)
    # Strictly overlapping, not merely touching. Two faces of a merged voxel
    # export routinely cross each other's *planes* and meet the intersection
    # line at one shared endpoint -- a T-junction, not interpenetration. With
    # `<=` the recorded tall_tripod finish STL reported 16 of those; the whole
    # check is only worth having if it stays quiet on the files this tool
    # already ships.
    slack = 1e-6 * max(float(scale), 1.0)
    res[np.nonzero(ok)[0]] = ((ia[:, 0] < ib[:, 1] - slack)
                              & (ib[:, 0] < ia[:, 1] - slack))
    return res


def _interval(p, d):
    """Where a triangle meets the intersection line, as [lo, hi].

    `p` is each vertex projected onto the line's dominant axis, `d` its signed
    distance from the other plane. Exactly one vertex is alone on its side of
    that plane (the caller guarantees a strict crossing), and the two segments
    from it to the other two cross the line.
    """
    sign = np.sign(d)
    # The lone vertex is the one whose sign differs from the other two, i.e.
    # the one furthest from the sign sum: with signs (+, +, -) the sum is +1
    # and only the minus vertex is 3 away from it. Taking the *nearest*
    # instead picks a majority vertex, whose two segments then run to one
    # same-sign vertex each and the interpolation divides by zero.
    lone = np.argmax(np.abs(sign.sum(axis=1)[:, None] - 2 * sign), axis=1)
    rows = np.arange(p.shape[0])
    others = np.stack([(lone + 1) % 3, (lone + 2) % 3], axis=1)
    p0, d0 = p[rows, lone], d[rows, lone]
    hits = []
    for k in range(2):
        o = others[:, k]
        p1, d1 = p[rows, o], d[rows, o]
        hits.append(p0 + (p1 - p0) * d0 / (d0 - d1))
    h = np.stack(hits, axis=1)
    return np.stack([h.min(axis=1), h.max(axis=1)], axis=1)


def shell_report(tri, tol=1e-5, intersections=True):
    """Is this triangle soup a closed surface, and is it one body? -> dict.

    Ray-parity voxelisation is exact for a watertight mesh and only *mostly*
    right for a leaky one, so a hole in an input STL shows up later as a few
    stray voxels rather than as an error -- which is the worst way for it to
    show up. Checking here means the file gets named while the reader still
    knows which file they passed.

    `components` and `self_intersections` are spec 19.3's two abnormal cases.
    They are reported, not folded into `closed_shell`: an island and an
    interpenetration are both real findings and neither one is an open edge,
    and the callers that refuse a shell before meshing refuse it on open
    edges. `intersections=False` skips the pair search for a caller that only
    wants the edge counts.

    Vertices are quantised to `tol` before edges are matched: STL stores every
    triangle independently, so shared corners are equal only to float32
    precision.
    """
    tri = np.asarray(tri, float)
    if tri.size == 0:
        return {"triangles": 0, "open_edges": 0, "non_manifold_edges": 0,
                "closed_shell": False, "degenerate": 0, "components": 0,
                "edge_components": 0, "self_intersections": 0,
                "self_intersection_examples": []}

    v = weld(tri, tol)

    degenerate = int(((v[:, 0] == v[:, 1]) | (v[:, 1] == v[:, 2])
                      | (v[:, 2] == v[:, 0])).sum())

    e = np.concatenate([v[:, [0, 1]], v[:, [1, 2]], v[:, [2, 0]]])
    e = e[e[:, 0] != e[:, 1]]
    und = np.sort(e, axis=1)
    _ue, counts = np.unique(und, axis=0, return_counts=True)
    open_edges = int((counts == 1).sum())
    nonmanifold = int((counts > 2).sum())
    out = {
        "triangles": int(tri.shape[0]),
        "open_edges": open_edges,
        "non_manifold_edges": nonmanifold,
        "degenerate": degenerate,
        "closed_shell": bool(open_edges == 0 and nonmanifold == 0),
        "components": mesh_components(tri, tol, v=v),
        "edge_components": mesh_components(tri, tol, v=v, by="edge"),
    }
    if intersections:
        si = self_intersections(tri, tol, v=v)
        out["self_intersections"] = si["pairs"]
        out["self_intersection_examples"] = si["examples"]
        if si.get("note"):
            out["self_intersection_note"] = si["note"]
    return out


def sample_points(tri, per_tri=4):
    """Vertices + centroid (+ edge midpoints if per_tri > 3) of every triangle."""
    pts = [tri.reshape(-1, 3), tri.mean(1)]
    if per_tri > 3:
        for i in range(3):
            pts.append((tri[:, i] + tri[:, (i + 1) % 3]) / 2)
    return np.unique(np.round(np.vstack(pts), 4), axis=0)


def points_inside(tri, pts, axis=0):
    """Point-in-mesh by counting ray crossings along +`axis`.

    Needs a watertight mesh. See the module docstring for the STEP caveat.
    """
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    u, v = b - a, c - a
    o = [i for i in range(3) if i != axis]
    # 2D edge test in the plane perpendicular to the ray
    det = u[:, o[0]] * v[:, o[1]] - u[:, o[1]] * v[:, o[0]]
    ok = np.abs(det) > 1e-12
    a, u, v, det = a[ok], u[ok], v[ok], det[ok]
    res = np.zeros(len(pts), bool)
    chunk = 4000
    for s in range(0, len(pts), chunk):
        p = pts[s:s + chunk]
        w0 = p[:, None, o[0]] - a[None, :, o[0]]
        w1 = p[:, None, o[1]] - a[None, :, o[1]]
        s1 = (w0 * v[None, :, o[1]] - w1 * v[None, :, o[0]]) / det[None, :]
        s2 = (u[None, :, o[0]] * w1 - u[None, :, o[1]] * w0) / det[None, :]
        in_tri = (s1 >= 0) & (s2 >= 0) & (s1 + s2 <= 1)
        t = (a[None, :, axis] + s1 * u[None, :, axis] + s2 * v[None, :, axis]
             - p[:, None, axis])
        res[s:s + chunk] = ((in_tri & (t > 1e-9)).sum(1) % 2) == 1
    return res


def min_gap(pts_a, pts_b):
    """Smallest point-to-point distance between two clouds, in chunks."""
    best = np.inf
    for s in range(0, len(pts_a), 2000):
        d = np.linalg.norm(pts_a[s:s + 2000, None, :] - pts_b[None, :, :], axis=2)
        best = min(best, d.min())
    return best


def rotation_about_x(deg):
    """Right-handed rotation matrix about +X, for a hinge on the x axis."""
    t = np.radians(deg)
    return np.array([[1, 0, 0],
                     [0, np.cos(t), -np.sin(t)],
                     [0, np.sin(t), np.cos(t)]])

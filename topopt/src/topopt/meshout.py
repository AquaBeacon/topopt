"""Writing voxel masks and density fields out as STL and STEP.

Three writers live here:

- `write_blocky_stl`   the boundary faces of a voxel mask, exactly as analysed
- `write_smooth_stl`   marching cubes on a density field (needs scikit-image)
- `write_step_faceted` the same voxel mask as a planar-BREP STEP solid

All three write in the *same* frame: voxel (i, j, k) occupies
`origin + [i, j, k] * h` to `origin + [i+1, j+1, k+1] * h`. That is not a
formality. If a part's feet are the bottom face of the lowest solid layer, then
every writer must put that face on the same plane, or the shape that gets
printed does not stand on the plane the analysis supported it on.

The STEP writer is the interesting one. There is no CAD kernel in this
environment, so the only honest export is a faceted solid -- but a faceted
solid does not have to be a bad one:

1. Coplanar voxel faces are merged into maximal rectangles (greedy meshing).
   Measured on real optimisation results that is 2.6-5.7x fewer faces; on
   slab-like shapes it is far more (184 voxel faces -> 22).
2. Merging rectangles leaves T-junctions, where a corner of one rectangle sits
   in the middle of a neighbour's edge. Rather than splitting faces to fix that
   (which undoes the merge), every hanging vertex is *inserted into the
   neighbouring face's loop as a collinear point*. The face stays one planar
   face, and the T-junction stops being one.

Every write reports `open_edges`, `non_manifold_edges` and `misoriented_edges`,
counted from the shell's own edge use. Measured on two real optimisation
results: no open edges and no orientation mismatches -- the merge and the
T-junction repair are exact -- but a handful of non-manifold edges, all of them
places where two voxels of the *result* touch only along an edge, so four faces
meet there. That is a property of a voxel boundary, not of this writer, and no
rectangle bookkeeping can remove it. Kernels sew such a shell with tolerance;
some flag it. The counts are in the summary so the claim is checkable rather
than asserted.

The result is a `MANIFOLD_SOLID_BREP` with real `PLANE` faces. It is still a
faceted shape, so it is a reference body for Fusion -- not a substitute for
rebuilding the part from primitives (see references/topology-optimization.md).
"""

import numpy as np
from scipy.ndimage import gaussian_filter

_CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                     [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)

_FACES = {(-1, 0, 0): [0, 4, 7, 3], (1, 0, 0): [1, 2, 6, 5],
          (0, -1, 0): [0, 1, 5, 4], (0, 1, 0): [3, 7, 6, 2],
          (0, 0, -1): [0, 3, 2, 1], (0, 0, 1): [4, 5, 6, 7]}


# ------------------------------------------------------------------ STL

def write_blocky_stl(path, mask, h, origin, merge=False):
    """Write the boundary faces of a voxel mask as an STL. No dependencies, and
    it is exactly what was analysed -- useful when you need the truth rather
    than a smoothed impression of it.

    `merge` runs the same greedy rectangle cover the STEP writer uses. The
    geometry is identical; there is just far less of it. That matters for the
    finish grid, where a 10 mm face resampled to 1 mm would otherwise ship as
    100 squares of a plane instead of one.
    """
    if merge:
        return _write_merged_stl(path, mask, h, origin)
    tris = []
    nx, ny, nz = mask.shape
    padded = np.zeros((nx + 2, ny + 2, nz + 2), bool)
    padded[1:-1, 1:-1, 1:-1] = mask
    idx = np.argwhere(mask)
    if idx.size == 0:
        _write_binary_stl(path, np.zeros((0, 3, 3)))
        return 0
    for d, quad in _FACES.items():
        nb = padded[1 + idx[:, 0] + d[0], 1 + idx[:, 1] + d[1], 1 + idx[:, 2] + d[2]]
        exposed = idx[~nb]
        if exposed.size == 0:
            continue
        base = origin + exposed * h
        pts = [base + _CORNERS[q] * h for q in quad]
        tris.append(np.stack([pts[0], pts[1], pts[2]], axis=1))
        tris.append(np.stack([pts[0], pts[2], pts[3]], axis=1))
    tris = np.concatenate(tris, axis=0) if tris else np.zeros((0, 3, 3))
    _write_binary_stl(path, tris)
    return tris.shape[0]


def _write_merged_stl(path, mask, h, origin):
    """The same boundary, with coplanar faces merged into maximal rectangles."""
    if not np.asarray(mask).any():
        _write_binary_stl(path, np.zeros((0, 3, 3)))
        return 0
    tris = []
    origin = np.asarray(origin, float)
    for r in _mask_rectangles(np.asarray(mask, bool)):
        pts = [origin + np.asarray(c, float) * h for c in _rect_corners(*r)]
        tris.append([pts[0], pts[1], pts[2]])
        tris.append([pts[0], pts[2], pts[3]])
    tris = np.asarray(tris, float)
    _write_binary_stl(path, tris)
    return tris.shape[0]


def write_smooth_stl(path, density, h, origin, level=0.5, smooth=1.0,
                     floor_mask=None, envelope=None):
    """Marching-cubes surface of the density field, when scikit-image is around.

    `floor_mask` is held at 1.0 before smoothing. Pass the preserve mask: the
    isosurface then cannot cut into geometry that is not allowed to be eroded.

    `envelope` is the boolean mask that was actually analysed. The surface is
    clamped to its bounding box, so the smoothed mesh never sticks out of the
    volume `printcheck` certified as fitting the plate -- and, at the plate
    itself, that clamp is what puts the first layer flat on z = 0.

    Three things here are corrections to how marching cubes was being read, all
    of them measured on a 3x3x3 block in a 7^3 grid (`test_meshout_ground.py`):

    1. **Half a voxel.** `marching_cubes(spacing=h)` returns index k at k*h,
       but voxel k *spans* origin + k*h .. origin + (k+1)*h and is centred half
       a cell further on. Adding only `origin` put the whole smoothed mesh
       h/2 low on every axis: on the tall tripod, 2 mm below its own feet, and
       2 mm outside the bounding box the print check had signed off.
    2. **Winding.** The triangles came out wound so the normals point *into*
       the solid. Slicers repair that silently, CAD kernels do not.
    3. **A flat bottom.** Gaussian pre-smoothing domes a face that is flat in
       the mask -- lower in the middle of a wide face than at its edge, by a
       few tenths of a mm. Dropped onto a build plate the part then rests on
       the middle of each foot with the rest of the face one or two layers in
       the air. Clamping to the analysed envelope flattens exactly that.

    The clamp stops the surface going *below* the analysed shape. It cannot
    stop it pulling *up*: a foot two cells across is eroded bodily by a sigma-1
    Gaussian, and a measured run left the feet 4.11 mm off the plate -- the
    part was standing on nothing. That is what `floor_mask` is for, and why the
    callers pass `preserve | (mask & ground)` rather than preserve alone. The
    two things a smoothed mesh may not move are the interface and the plate.
    """
    try:
        from skimage import measure
    except Exception:
        return None
    # A float32 field stays float32: the finish stage hands over a fine-grid
    # field of 10^8 cells, and widening it here would double the peak.
    d = (density.astype(np.float32) if np.asarray(density).dtype == np.float32
         else density.astype(float))
    if floor_mask is not None:
        d = np.maximum(d, floor_mask.astype(d.dtype))
    if smooth:
        d = gaussian_filter(d, smooth)
    if floor_mask is not None:
        d = np.maximum(d, floor_mask.astype(float))
    if d.max() <= level or d.min() >= level:
        return None
    verts, faces, _, _ = measure.marching_cubes(d, level=level, spacing=(h, h, h))
    # (1) index k -> the centre of voxel k, which is where its density lives.
    verts = verts + np.asarray(origin, float) + 0.5 * h

    # (3) the analysed envelope, in mm.
    env = envelope if envelope is not None else (np.asarray(density) >= level)
    env = np.asarray(env, bool)
    if env.any():
        idx = np.argwhere(env)
        lo = np.asarray(origin, float) + idx.min(axis=0) * h
        hi = np.asarray(origin, float) + (idx.max(axis=0) + 1) * h
        verts = np.clip(verts, lo, hi)

    # (4) snap to a quantum the STL can actually store. An STL holds every
    # triangle's corners independently in float32, whose spacing at 100 mm is
    # about 8e-6 mm -- so two corners that marching cubes computed for the
    # same point, differing in the last bits of a float64, can land on
    # different float32 values and the file then has a hole in it that no
    # welding tolerance under 1e-4 mm can close. MEASURED on the tall tripod:
    # 3 open edges out of 444,000 in a mesh that is otherwise closed, and the
    # shell check refuses it, which stops `topopt ccx` meshing it. 1e-4 of a
    # cell is 50,000 times finer than the grid the surface came off, so this
    # moves nothing that was ever real.
    q = h * 1e-4
    verts = np.round(verts / q) * q

    tris = verts[faces]
    # (2) reverse each loop so the normal points out of the solid.
    tris = tris[:, ::-1, :]
    # Clamping and snapping both collapse points onto each other. A triangle
    # with two identical corners is not geometry: some slicers flag it, and
    # the shell check counts the edge it loses as an open one.
    same = (np.all(tris[:, 0] == tris[:, 1], axis=1)
            | np.all(tris[:, 1] == tris[:, 2], axis=1)
            | np.all(tris[:, 2] == tris[:, 0], axis=1))
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    area2 = np.linalg.norm(np.cross(e1, e2), axis=1)
    tris = tris[(area2 > 1e-9 * h * h) & ~same]
    _write_binary_stl(path, tris)
    return tris.shape[0]


def _write_binary_stl(path, tris):
    tris = np.asarray(tris, np.float32)
    n = tris.shape[0]
    rec = np.zeros(n, dtype=np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)),
                                      ("attr", "<u2")]))
    if n:
        e1 = tris[:, 1] - tris[:, 0]
        e2 = tris[:, 2] - tris[:, 0]
        nrm = np.cross(e1, e2)
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        rec["normal"] = np.divide(nrm, np.where(ln == 0, 1, ln))
        rec["v"] = tris
    with open(path, "wb") as f:
        f.write(b"topo_opt".ljust(80, b" "))
        f.write(np.uint32(n).tobytes())
        f.write(rec.tobytes())


def merge_stls(path, sources):
    """Concatenate several binary STLs into one (geometry only, no colour)."""
    from .voxel import load_stl
    tris = [load_stl(s) for s in sources]
    tris = [t for t in tris if t.size]
    _write_binary_stl(path, np.concatenate(tris, axis=0) if tris
                      else np.zeros((0, 3, 3)))
    return sum(t.shape[0] for t in tris)


# ---------------------------------------------------------------- greedy mesh

def _greedy_rectangles(plane):
    """Maximal-rectangle cover of a 2D boolean array.

    Returns [(u0, v0, u1, v1)] as half-open index ranges. This is the standard
    voxel greedy-mesh: grow along u first, then along v while the whole row
    matches.
    """
    plane = plane.copy()
    nu, nv = plane.shape
    rects = []
    for v in range(nv):
        u = 0
        while u < nu:
            if not plane[u, v]:
                u += 1
                continue
            u1 = u
            while u1 < nu and plane[u1, v]:
                u1 += 1
            v1 = v + 1
            while v1 < nv and plane[u:u1, v1].all():
                v1 += 1
            plane[u:u1, v:v1] = False
            rects.append((u, v, u1, v1))
            u = u1
    return rects


def _mask_rectangles(mask):
    """All exposed faces of `mask`, merged into rectangles.

    Yields (axis, plane_index, normal_sign, u0, v0, u1, v1) in *grid index*
    units, where (u, v) are the two axes other than `axis`, in increasing order.
    """
    nx, ny, nz = mask.shape
    padded = np.zeros((nx + 2, ny + 2, nz + 2), bool)
    padded[1:-1, 1:-1, 1:-1] = mask
    out = []
    for axis in (0, 1, 2):
        inner = padded[1:-1, 1:-1, 1:-1]
        for sign in (-1, 1):
            shifted = np.roll(padded, -sign, axis=axis)[1:-1, 1:-1, 1:-1]
            exposed = inner & ~shifted
            if not exposed.any():
                continue
            n_slices = mask.shape[axis]
            for i in range(n_slices):
                plane = np.take(exposed, i, axis=axis)
                if not plane.any():
                    continue
                # The face sits at the low side of voxel i for sign=-1 and at
                # the high side for sign=+1.
                p = i if sign < 0 else i + 1
                for (u0, v0, u1, v1) in _greedy_rectangles(plane):
                    out.append((axis, p, sign, u0, v0, u1, v1))
    return out


def _rect_corners(axis, p, sign, u0, v0, u1, v1):
    """The four corner grid-index points, ordered so the loop is CCW seen from
    outside (i.e. right-handed about the outward normal).

    Walking u then v gives a loop whose normal is e_a x e_b. That equals
    +e_axis for axis 0 and 2 but -e_axis for axis 1 (because e_x x e_z = -e_y),
    so the Y faces need one extra reversal.
    """
    a, b = [k for k in (0, 1, 2) if k != axis]
    quad = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    if (sign < 0) != (axis == 1):
        quad = quad[::-1]
    pts = []
    for (u, v) in quad:
        c = [0, 0, 0]
        c[axis] = p
        c[a] = u
        c[b] = v
        pts.append(tuple(c))
    return pts


# ---------------------------------------------------------------- STEP

def write_step_faceted(path, mask, h, origin, name="topo_result",
                       author="3d-print-design"):
    """Write a voxel mask as a planar-BREP STEP (AP214) solid.

    Returns a dict with the face/vertex/entity counts, or None when the mask is
    empty.
    """
    if not mask.any():
        return None

    rects = _mask_rectangles(mask)

    # ---- collect every corner, then repair T-junctions by inserting the
    # hanging vertices into the neighbouring loops as collinear points.
    corners = set()
    loops = []
    for r in rects:
        pts = _rect_corners(*r)
        corners.update(pts)
        loops.append((r, pts))

    # by_line[(axis, other1_value, other2_value)] -> sorted coords along `axis`
    by_line = {}
    for c in corners:
        for axis in (0, 1, 2):
            a, b = [k for k in (0, 1, 2) if k != axis]
            by_line.setdefault((axis, c[a], c[b]), []).append(c[axis])
    for k in by_line:
        by_line[k] = np.array(sorted(set(by_line[k])))

    def densify(p, q):
        """Points strictly between grid points p and q, in order."""
        diff = [i for i in range(3) if p[i] != q[i]]
        if len(diff) != 1:
            return []
        axis = diff[0]
        a, b = [k for k in (0, 1, 2) if k != axis]
        vals = by_line.get((axis, p[a], p[b]))
        if vals is None:
            return []
        lo, hi = min(p[axis], q[axis]), max(p[axis], q[axis])
        inner = vals[(vals > lo) & (vals < hi)]
        if p[axis] > q[axis]:
            inner = inner[::-1]
        out = []
        for t in inner:
            c = list(p)
            c[axis] = int(t)
            out.append(tuple(c))
        return out

    polys = []
    for (_, pts) in loops:
        poly = []
        for i in range(4):
            p, q = pts[i], pts[(i + 1) % 4]
            poly.append(p)
            poly.extend(densify(p, q))
        polys.append(poly)

    # ---- entity emission
    lines = []
    nid = [0]

    def new(text):
        nid[0] += 1
        lines.append("#%d=%s;" % (nid[0], text))
        return nid[0]

    vert_ids = {}

    def vertex(c):
        if c in vert_ids:
            return vert_ids[c]
        xyz = origin + np.asarray(c, float) * h
        cp = new("CARTESIAN_POINT('',(%s))"
                 % ",".join("%.6f" % x for x in xyz))
        vid = new("VERTEX_POINT('',#%d)" % cp)
        vert_ids[c] = vid
        return vid

    dir_ids = {}

    def direction(vec):
        key = tuple(vec)
        if key not in dir_ids:
            dir_ids[key] = new("DIRECTION('',(%s))"
                               % ",".join("%.6f" % x for x in vec))
        return dir_ids[key]

    edge_ids = {}

    def edge_curve(p, q):
        """Shared EDGE_CURVE for the segment p-q, plus the sense to use."""
        key = (p, q) if p <= q else (q, p)
        same = (p, q) == key
        if key in edge_ids:
            return edge_ids[key], same
        v0, v1 = vertex(key[0]), vertex(key[1])
        a = np.asarray(key[0], float)
        b = np.asarray(key[1], float)
        d = b - a
        n = np.linalg.norm(d)
        d = d / n if n else np.array([1.0, 0, 0])
        p0 = new("CARTESIAN_POINT('',(%s))"
                 % ",".join("%.6f" % x for x in origin + a * h))
        dr = direction(d)
        vec = new("VECTOR('',#%d,%.6f)" % (dr, n * h))
        ln = new("LINE('',#%d,#%d)" % (p0, vec))
        ec = new("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (v0, v1, ln))
        edge_ids[key] = ec
        return ec, same

    plane_ids = {}

    def plane_of(axis, p, sign):
        key = (axis, p, sign)
        if key in plane_ids:
            return plane_ids[key]
        o = [0.0, 0.0, 0.0]
        o[axis] = p
        loc = new("CARTESIAN_POINT('',(%s))"
                  % ",".join("%.6f" % x for x in origin + np.array(o) * h))
        nvec = [0.0, 0.0, 0.0]
        nvec[axis] = float(sign)
        ref = [0.0, 0.0, 0.0]
        ref[(axis + 1) % 3] = 1.0
        ax = new("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)"
                 % (loc, direction(nvec), direction(ref)))
        pl = new("PLANE('',#%d)" % ax)
        plane_ids[key] = pl
        return pl

    # Edge bookkeeping: a closed orientable shell uses every edge exactly twice,
    # once in each direction. Counting it is the only way to know whether the
    # T-junction repair actually worked -- a geometric round-trip cannot see it.
    edge_use = {}

    face_ids = []
    for (r, _), poly in zip(loops, polys):
        axis, p, sign = r[0], r[1], r[2]
        oriented = []
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            ec, same = edge_curve(a, b)
            u = edge_use.setdefault(ec, [0, 0])
            u[0 if same else 1] += 1
            oriented.append(new("ORIENTED_EDGE('',*,*,#%d,%s)"
                                % (ec, ".T." if same else ".F.")))
        loop = new("EDGE_LOOP('',(%s))" % ",".join("#%d" % e for e in oriented))
        bound = new("FACE_OUTER_BOUND('',#%d,.T.)" % loop)
        face = new("ADVANCED_FACE('',(#%d),#%d,.T.)" % (bound, plane_of(axis, p, sign)))
        face_ids.append(face)

    shell = new("CLOSED_SHELL('',(%s))" % ",".join("#%d" % f for f in face_ids))
    brep = new("MANIFOLD_SOLID_BREP('%s',#%d)" % (name, shell))

    # ---- the product / context boilerplate AP214 needs
    ax_o = new("CARTESIAN_POINT('',(0.,0.,0.))")
    ax_z = direction([0.0, 0.0, 1.0])
    ax_x = direction([1.0, 0.0, 0.0])
    ax = new("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (ax_o, ax_z, ax_x))
    len_u = new("(NAMED_UNIT(*)LENGTH_UNIT()SI_UNIT(.MILLI.,.METRE.))")
    ang_u = new("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    sol_u = new("(NAMED_UNIT(*)SOLID_ANGLE_UNIT()SI_UNIT($,.STERADIAN.))")
    tol = new("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(%.6f),#%d,"
              "'closure','tolerance')" % (h * 1e-4, len_u))
    ctx = new("(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
              "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#%d))"
              "GLOBAL_UNIT_ASSIGNED_CONTEXT((#%d,#%d,#%d))"
              "REPRESENTATION_CONTEXT('',''))" % (tol, len_u, ang_u, sol_u))
    shape = new("ADVANCED_BREP_SHAPE_REPRESENTATION('%s',(#%d,#%d),#%d)"
                % (name, ax, brep, ctx))
    app_ctx = new("APPLICATION_CONTEXT('automotive design')")
    new("APPLICATION_PROTOCOL_DEFINITION('international standard',"
        "'automotive_design',2000,#%d)" % app_ctx)
    prod = new("PRODUCT('%s','%s','',(#%d))"
               % (name, name, new("PRODUCT_CONTEXT('',#%d,'mechanical')" % app_ctx)))
    pdf = new("PRODUCT_DEFINITION_FORMATION('','',#%d)" % prod)
    pd = new("PRODUCT_DEFINITION('design','',#%d,#%d)"
             % (pdf, new("PRODUCT_DEFINITION_CONTEXT('part definition',#%d,'design')"
                         % app_ctx)))
    pds = new("PRODUCT_DEFINITION_SHAPE('','',#%d)" % pd)
    new("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (pds, shape))

    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('faceted BREP from a voxel topology-optimisation "
        "result'),'2;1');\n"
        "FILE_NAME('%s','',('%s'),(''),'%s','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 3 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n" % (name, author, author))
    with open(path, "w", encoding="ascii", errors="replace") as f:
        f.write(header)
        f.write("\n".join(lines))
        f.write("\nENDSEC;\nEND-ISO-10303-21;\n")

    # count 1 = a hole in the shell; count 4 = two voxels touching only along an
    # edge, where four faces meet and the surface is non-manifold there. Neither
    # is fatal for import (kernels sew with tolerance) but both must be reported
    # rather than claimed away.
    open_edges = sum(1 for u in edge_use.values() if u[0] + u[1] < 2)
    nonmanifold = sum(1 for u in edge_use.values() if u[0] + u[1] > 2)
    misoriented = sum(1 for u in edge_use.values()
                      if u[0] + u[1] == 2 and (u[0] != 1 or u[1] != 1))
    return {"faces": len(face_ids), "vertices": len(vert_ids),
            "edges": len(edge_ids), "entities": nid[0],
            "voxel_faces_before_merge": int(_exposed_face_count(mask)),
            "open_edges": open_edges,
            "non_manifold_edges": nonmanifold,
            "misoriented_edges": misoriented,
            "closed_shell": bool(open_edges == 0 and nonmanifold == 0
                                 and misoriented == 0)}


def _exposed_face_count(mask):
    nx, ny, nz = mask.shape
    padded = np.zeros((nx + 2, ny + 2, nz + 2), bool)
    padded[1:-1, 1:-1, 1:-1] = mask
    inner = padded[1:-1, 1:-1, 1:-1]
    n = 0
    for axis in (0, 1, 2):
        for sign in (-1, 1):
            shifted = np.roll(padded, -sign, axis=axis)[1:-1, 1:-1, 1:-1]
            n += int((inner & ~shifted).sum())
    return n


# Where the light comes from in `write_mesh_preview`. Off to the left, in
# front and above: a head-on light flattens exactly the thing these pictures
# exist to show, which is whether the surface is smooth or stepped.
_PREVIEW_LIGHT = np.array([0.40, -0.72, 0.57])
_PREVIEW_LIGHT = _PREVIEW_LIGHT / np.linalg.norm(_PREVIEW_LIGHT)

# elevation, azimuth, caption -- three views, because one hides half the part.
_PREVIEW_VIEWS = ((22, -60, "iso"), (0, -90, "front (+Y towards you)"),
                  (0, 0, "side (+X towards you)"))


def write_mesh_preview(path, tris, title=None, max_triangles=400_000):
    """Shaded pictures of the mesh that is actually going to be printed.

    Every other figure in a run is a picture of a *field* -- density, stress,
    overhang -- drawn on the analysis grid. None of them shows the surface, and
    the surface is what a person opening the STL sees and judges: whether the
    optimised material came out smooth or came out as a row of beads, and
    whether the CAD interface is still crisp. A reader who cannot see that has
    to open the file in a slicer to find out, which is exactly when they find
    out too late.

    Flat-shaded on purpose (one normal per triangle, no interpolation): a
    smooth-shaded render hides faceting, and faceting is the finding.

    Returns the path, or None when matplotlib is missing or the mesh is too
    big to draw -- a picture is never worth failing a run over.
    """
    tris = np.asarray(tris, float)
    if tris.size == 0 or len(tris) > max_triangles:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except Exception:                                             # noqa: BLE001
        return None

    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(n, axis=1)
    keep = length > 1e-12
    tris, n = tris[keep], n[keep] / length[keep][:, None]
    if not len(tris):
        return None
    shade = 0.30 + 0.70 * np.clip(n @ _PREVIEW_LIGHT, 0.0, 1.0)
    colours = np.column_stack([shade * 0.62, shade * 0.68, shade * 0.78,
                               np.ones(len(shade))])

    v = tris.reshape(-1, 3)
    lo, hi = v.min(axis=0), v.max(axis=0)
    fig = plt.figure(figsize=(5.0 * len(_PREVIEW_VIEWS), 5.6))
    for i, (elev, azim, name) in enumerate(_PREVIEW_VIEWS):
        ax = fig.add_subplot(1, len(_PREVIEW_VIEWS), i + 1, projection="3d")
        ax.add_collection3d(Poly3DCollection(tris, facecolors=colours,
                                             edgecolors="none"))
        ax.set_xlim(lo[0], hi[0])
        ax.set_ylim(lo[1], hi[1])
        ax.set_zlim(lo[2], hi[2])
        ax.set_box_aspect(np.maximum(hi - lo, 1e-6))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        ax.set_title(name, fontsize=10)
    head = title or "the mesh as it will be printed"
    fig.suptitle("%s  --  %d triangles, %.0f x %.0f x %.0f mm"
                 % (head, len(tris), *(hi - lo)), fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path

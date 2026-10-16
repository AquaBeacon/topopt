"""Minimal STEP (AP214, planar BREP) -> triangles, with no CAD kernel.

Only valid for files that are a tessellated model re-exported as BREP, i.e.
almost every face is a `PLANE`. `CYLINDRICAL_SURFACE` faces are approximated by
their bounding polygon (edges only), so the result is good enough to look at
and to measure a bounding box from, but it is **not watertight** -- do not
ray-cast through it. Take hole diameters and fillet radii from the analytic
entities instead:

    grep -E 'CYLINDRICAL_SURFACE|CIRCLE' part.step

`meshout.write_step_faceted` is the other direction: a voxel result out to a
planar-BREP STEP a CAD kernel will read.
"""

import re
import struct

import numpy as np

__all__ = ["read_step_triangles", "write_binary_stl", "triangulate_polygon"]

_NUM = r"[-+]?[0-9]*\.?[0-9]+(?:[EeDd][-+]?[0-9]+)?"


def _refs(s):
    return [int(x) for x in re.findall(r"#(\d+)", s)]


def _topargs(s):
    """Split a STEP argument list at top-level commas."""
    out, depth, cur, quoted = [], 0, [], False
    for ch in s:
        if quoted:
            cur.append(ch)
            if ch == "'":
                quoted = False
            continue
        if ch == "'":
            quoted = True
            cur.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def triangulate_polygon(pts, normal):
    """Ear clipping in the face plane."""
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [(pts[0], pts[1], pts[2])]
    nz = normal / (np.linalg.norm(normal) + 1e-30)
    ax = np.array([1.0, 0, 0]) if abs(nz[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(nz, ax)
    u /= np.linalg.norm(u)
    v = np.cross(nz, u)
    proj = np.array([[np.dot(p, u), np.dot(p, v)] for p in pts])
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += proj[i, 0] * proj[j, 1] - proj[j, 0] * proj[i, 1]
    idx = list(range(n)) if area > 0 else list(range(n))[::-1]

    def cross2(o, a, b):
        return ((proj[a, 0] - proj[o, 0]) * (proj[b, 1] - proj[o, 1])
                - (proj[a, 1] - proj[o, 1]) * (proj[b, 0] - proj[o, 0]))

    tris, guard = [], 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        done = False
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross2(a, b, c) <= 1e-12:
                continue
            bad = False
            for m in idx:
                if m in (a, b, c):
                    continue
                if cross2(a, b, m) >= 0 and cross2(b, c, m) >= 0 and cross2(c, a, m) >= 0:
                    bad = True
                    break
            if bad:
                continue
            tris.append((pts[a], pts[b], pts[c]))
            idx.pop(k)
            done = True
            break
        if not done:
            break
    if len(idx) == 3:
        tris.append((pts[idx[0]], pts[idx[1]], pts[idx[2]]))
    elif len(idx) > 3:                                     # fallback fan
        for k in range(1, len(idx) - 1):
            tris.append((pts[idx[0]], pts[idx[k]], pts[idx[k + 1]]))
    return tris


def read_step_triangles(path):
    """Parse a planar-BREP STEP file.

    Returns (tris, skipped) where tris is a list of (normal, a, b, c).
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)

    ent = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*?)\)\s*;", src, flags=re.S):
        ent[int(m.group(1))] = (m.group(2), m.group(3))

    pt_cache = {}

    def point(eid):
        if eid in pt_cache:
            return pt_cache[eid]
        t, a = ent[eid]
        if t == "VERTEX_POINT":
            v = point(_refs(a)[0])
        elif t == "CARTESIAN_POINT":
            nums = re.findall(_NUM, a.split("(", 1)[1])
            v = np.array([float(x) for x in nums[:3]])
        else:
            raise ValueError(t)
        pt_cache[eid] = v
        return v

    def loop_points(loop_id):
        t, a = ent[loop_id]
        assert t == "EDGE_LOOP", t
        pts = []
        for oe in _refs(a):
            ta, aa = ent[oe]
            if ta != "ORIENTED_EDGE":
                continue
            args = _topargs(aa)
            sense = args[-1].strip().upper().startswith(".T")
            ec = _refs(args[3])[0]
            _te, ae = ent[ec]
            ea = _topargs(ae)
            v1, v2 = _refs(ea[1])[0], _refs(ea[2])[0]
            edge_sense = ea[-1].strip().upper().startswith(".T")
            s, e = (v1, v2) if edge_sense else (v2, v1)
            s, e = (s, e) if sense else (e, s)
            p = point(s)
            if not pts or np.linalg.norm(p - pts[-1]) > 1e-9:
                pts.append(p)
        if len(pts) > 1 and np.linalg.norm(pts[0] - pts[-1]) < 1e-9:
            pts.pop()
        return pts

    tris, skipped = [], 0
    for _eid, (t, a) in ent.items():
        if t != "ADVANCED_FACE":
            continue
        args = _topargs(a)
        bounds = _refs(args[1])
        surf = _refs(args[2])[0]
        st, sa = ent[surf]
        same = args[3].strip().upper().startswith(".T")
        # plane normal from AXIS2_PLACEMENT_3D -> first DIRECTION
        nrm = None
        if st == "PLANE":
            r = _refs(ent[_refs(sa)[0]][1])
            if len(r) >= 2:
                nums = re.findall(_NUM, ent[r[1]][1].split("(", 1)[1])
                nrm = np.array([float(x) for x in nums[:3]])
        outer = None
        for b in bounds:
            bt, ba = ent[b]
            pts = loop_points(_refs(ba)[0])
            if bt == "FACE_OUTER_BOUND" or outer is None:
                outer = pts
        if not outer or len(outer) < 3:
            skipped += 1
            continue
        if nrm is None:
            p = np.array(outer)
            nrm = np.cross(p[1] - p[0], p[2] - p[0])
        if not same:
            nrm = -nrm
        for tri in triangulate_polygon(outer, nrm):
            a3, b3, c3 = tri
            fn = np.cross(b3 - a3, c3 - a3)
            if np.dot(fn, nrm) < 0:
                a3, b3, c3 = a3, c3, b3
                fn = -fn
            tris.append((fn, a3, b3, c3))
    return tris, skipped


def write_binary_stl(path, tris):
    """Write (normal, a, b, c) tuples as a binary STL."""
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for fn, a, b, c in tris:
            nl = np.linalg.norm(fn)
            n = fn / nl if nl > 1e-30 else np.zeros(3)
            f.write(struct.pack("<12fH", *n, *a, *b, *c, 0))

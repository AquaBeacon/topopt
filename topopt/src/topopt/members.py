"""Turn an optimised density field into an explicit list of structural members.

Counting members per slice tells you *how many* ribs there are and how thick.
It does not tell you *where* they run, so rebuilding the result as parametric
OpenSCAD still means squinting at a density plot and guessing coordinates.

This module closes that gap: skeletonise the kept material, walk the skeleton
graph, and emit one straight segment per member with endpoints in mm and a
width measured from the distance transform. Those numbers drop straight into a
`.scad` as `cube()` / `cylinder()` calls.
"""

import numpy as np

try:
    from scipy.ndimage import distance_transform_edt, convolve
except Exception:                                          # pragma: no cover
    distance_transform_edt = convolve = None


def _skeletonize(mask):
    from skimage.morphology import skeletonize
    return skeletonize(mask)


def detect_prismatic_axis(mask, min_iou=0.55):
    """Return (axis, iou) for the axis along which the section repeats, or (None, iou).

    A frame optimised in a slab-shaped domain usually comes out prismatic: the
    same truss repeated along the thin direction. Recognising that lets us work
    in 2D, where the skeleton is clean and the members are unambiguous.

    The test is mean intersection-over-union of each slice against the fullest
    one -- not near-identity. A density filter always thins the free surfaces,
    so demanding identical slices rejects structures that are obviously
    prismatic to the eye (a real result scored IoU 0.74 on its prismatic axis
    against 0.42 and 0.30 on the others, yet failed a strict-equality test).
    """
    best, best_iou = None, 0.0
    if not mask.any():
        return None, 0.0
    for axis in range(3):
        n = mask.shape[axis]
        if n < 3:
            continue
        slices = [np.take(mask, i, axis=axis) for i in range(n)]
        areas = np.array([s.sum() for s in slices], float)
        ref = slices[int(np.argmax(areas))]
        if ref.sum() == 0:
            continue
        inner = slices[1:-1] or slices
        iou = float(np.mean([(s & ref).sum() / max((s | ref).sum(), 1)
                             for s in inner]))
        if iou > best_iou:
            best, best_iou = axis, iou
    if best is not None and best_iou >= min_iou:
        return best, best_iou
    return None, best_iou


def _graph_segments(skel):
    """Walk a 2D skeleton and return paths between its nodes."""
    k = np.ones((3, 3), int)
    k[1, 1] = 0
    deg = convolve(skel.astype(int), k, mode="constant")
    deg = np.where(skel, deg, 0)
    nodes = skel & ((deg == 1) | (deg >= 3))
    node_set = {tuple(p) for p in np.argwhere(nodes)}

    nbrs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    h, w = skel.shape

    def neighbours(p):
        for d in nbrs:
            q = (p[0] + d[0], p[1] + d[1])
            if 0 <= q[0] < h and 0 <= q[1] < w and skel[q]:
                yield q

    paths = []
    visited_edges = set()
    for start in node_set:
        for first in neighbours(start):
            edge = tuple(sorted([start, first]))
            if edge in visited_edges:
                continue
            path = [start, first]
            visited_edges.add(edge)
            cur, prev = first, start
            while cur not in node_set:
                nxt = [q for q in neighbours(cur) if q != prev]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                visited_edges.add(tuple(sorted([prev, cur])))
                path.append(cur)
            paths.append(path)

    # A closed loop has no nodes at all; keep it as one path so it is not lost.
    if not node_set and skel.any():
        paths.append([tuple(p) for p in np.argwhere(skel)])
    return paths


def _rdp(points, tol):
    """Ramer-Douglas-Peucker simplification of a polyline.

    Collapsing a whole skeleton path to one straight line between its endpoints
    silently discards bends, and a bent chord matters: on a real cantilever the
    top chord runs horizontally for two thirds of the span before angling down
    to the load. Straight-lining it pulls the chord inward, shortens the lever
    arm, and cost 28 % of the structure's stiffness in a measured rebuild.
    """
    pts = np.asarray(points, float)
    if len(pts) < 3:
        return pts
    start, end = pts[0], pts[-1]
    seg = end - start
    n = np.linalg.norm(seg)
    if n < 1e-9:
        d = np.linalg.norm(pts - start, axis=1)
    else:
        d = np.abs(np.cross(np.tile(seg, (len(pts), 1)), pts - start)) / n
    i = int(np.argmax(d))
    if d[i] > tol:
        left = _rdp(pts[:i + 1], tol)
        right = _rdp(pts[i:], tol)
        return np.vstack([left[:-1], right])
    return np.vstack([start, end])


def _weld_nodes(nodes, radius):
    """Greedy clustering of skeleton nodes -> {node: cluster centroid}."""
    pts = [tuple(n) for n in nodes]
    clusters = []
    for p in pts:
        for c in clusters:
            if np.linalg.norm(np.array(p, float) - c["centre"]) <= radius:
                c["members"].append(p)
                c["centre"] = np.mean(np.array(c["members"], float), axis=0)
                break
        else:
            clusters.append({"members": [p], "centre": np.array(p, float)})
    out = {}
    for c in clusters:
        for p in c["members"]:
            out[p] = c["centre"]
    return out


def extract_members(density, grid, level=0.5, min_length_mm=None):
    """-> dict with the prismatic axis (if any) and a list of members.

    Each member: endpoints in mm, length, mean width, and the plane it lies in.
    """
    if distance_transform_edt is None:
        return {"error": "scipy is required for member extraction"}
    try:
        _skeletonize(np.ones((3, 3), bool))
    except Exception as exc:
        return {"error": "scikit-image is required for member extraction (%s)" % exc}

    mask = density >= level
    if not mask.any():
        return {"members": [], "prismatic_axis": None}
    h = grid.h
    if min_length_mm is None:
        min_length_mm = 4 * h

    axis, iou = detect_prismatic_axis(mask)
    out = {"prismatic_axis": (None if axis is None else "XYZ"[axis]),
           "prismatic_iou": round(iou, 3), "level": level, "members": []}

    if axis is None:
        # Fall back to a 3D skeleton. Segments are still straight lines between
        # graph nodes, which is all a primitive rebuild needs.
        from skimage.morphology import skeletonize
        skel3 = skeletonize(mask)
        edt = distance_transform_edt(mask) * h
        pts = np.argwhere(skel3)
        out["note"] = ("structure is not prismatic; %d skeleton voxels reported "
                       "as a point cloud rather than clean members. Slice the "
                       "domain or add symmetry to get a cleaner result."
                       % len(pts))
        out["skeleton_points_mm"] = [
            [round(float(v), 2) for v in (grid.origin + (p + 0.5) * h)]
            for p in pts[:: max(1, len(pts) // 400)]]
        out["skeleton_width_mm"] = round(float(2 * np.mean(edt[skel3])), 2)
        return out

    # Prismatic: collapse along `axis` and work on the 2D section. Majority
    # rather than `any`, so the thinned free surfaces do not inflate the
    # section into members that are not really there.
    plane = [a for a in range(3) if a != axis]
    flat = mask.sum(axis=axis) >= max(1, mask.shape[axis] // 2)
    if not flat.any():
        flat = mask.any(axis=axis)
    edt2 = distance_transform_edt(flat) * h
    skel = _skeletonize(flat)
    paths = _graph_segments(skel)

    # Extent along `axis` = how many slices hold material, not how many voxels.
    thickness = int(mask.any(axis=tuple(plane)).sum()) * h

    mid = grid.origin[axis] + 0.5 * grid.shape[axis] * h
    simplify_tol = max(1.0, 1.5 * h) / h        # in voxels

    # Weld skeleton nodes that sit within a member-length of each other.
    # Without this, a joint made of two nodes two voxels apart emits a leg too
    # short to keep, the leg is dropped, and the frame silently comes out in
    # two disconnected pieces -- which the FEA then rejects with "support
    # region selected no material".
    weld = _weld_nodes([p[0] for p in paths] + [p[-1] for p in paths],
                       min_length_mm / h)

    for pi, path in enumerate(paths):
        idx = np.asarray(path, float)
        # Keep the bends: simplify the path to a polyline, then emit one
        # straight member per leg.
        poly = _rdp(idx, simplify_tol)
        poly[0] = weld.get(tuple(path[0]), poly[0])
        poly[-1] = weld.get(tuple(path[-1]), poly[-1])
        for k in range(len(poly) - 1):
            q0, q1 = poly[k], poly[k + 1]
            a0 = grid.origin[plane[0]] + (q0[0] + 0.5) * h
            b0 = grid.origin[plane[1]] + (q0[1] + 0.5) * h
            a1 = grid.origin[plane[0]] + (q1[0] + 0.5) * h
            b1 = grid.origin[plane[1]] + (q1[1] + 0.5) * h
            length = float(np.hypot(a1 - a0, b1 - b0))
            if length < min_length_mm:
                continue
            # Width from the skeleton points that belong to this leg.
            lo = int(np.argmin(np.linalg.norm(idx - q0, axis=1)))
            hi = int(np.argmin(np.linalg.norm(idx - q1, axis=1)))
            lo, hi = min(lo, hi), max(lo, hi)
            seg_pts = path[lo:hi + 1] or path
            widths = [2 * edt2[p[0], p[1]] for p in seg_pts]
            start = [0.0, 0.0, 0.0]
            end = [0.0, 0.0, 0.0]
            start[plane[0]], start[plane[1]] = a0, b0
            end[plane[0]], end[plane[1]] = a1, b1
            start[axis] = end[axis] = mid
            angle = float(np.degrees(np.arctan2(b1 - b0, a1 - a0)))
            out["members"].append({
                # float() matters: numpy scalars survive round() and then print
                # as "np.float64(19.4)" straight into the generated .scad.
                "start_mm": [round(float(v), 2) for v in start],
                "end_mm": [round(float(v), 2) for v in end],
                "length_mm": round(length, 2),
                "width_mm": round(float(np.median(widths)), 2),
                "min_width_mm": round(float(np.min(widths)), 2),
                "angle_deg": round(angle, 1),
                "path": pi,
                "plane": "".join("XYZ"[p] for p in plane),
            })

    out["members"].sort(key=lambda m: -m["length_mm"])
    out["extrusion_axis"] = "XYZ"[axis]
    out["extrusion_thickness_mm"] = round(float(thickness), 2)
    out["member_count"] = len(out["members"])
    return out


def scad_sketch(extract, module_name="topo_frame"):
    """Emit a first-cut parametric .scad from the extracted members.

    This is a *starting point*, not a finished part: it has no bolt bosses, no
    fillet call-outs, no cable routes and no asserts. Take it, name the
    parameters properly, and add everything the optimiser could not know.
    """
    if extract.get("error") or not extract.get("members"):
        return "// no members extracted: %s" % extract.get(
            "error", extract.get("note", "empty result"))

    ax = extract["extrusion_axis"]
    th = extract["extrusion_thickness_mm"]
    plane = extract["members"][0]["plane"]
    lines = [
        "// %s -- first cut generated from a topology-optimisation result." % module_name,
        "// Members lie in the %s plane and are extruded %.1f mm along %s."
        % (plane, th, ax),
        "//",
        "// TODO before this is a part:",
        "//   - rename these to meaningful parameters and derive them",
        "//   - snap coordinates to the real interfaces (part_index.py lock)",
        "//   - add bolt bosses, cable routes, assembly clearance",
        "//   - add assert() clearance checks",
        "//   - re-run `topopt fea` and compare compliance with the optimiser",
        "",
        "thick = %.2f;   // extrusion along %s" % (th, ax),
        "",
    ]
    for i, m in enumerate(extract["members"]):
        lines.append("w%-2d = %6.2f;  // member %d, length %.1f mm, %.0f deg"
                     % (i, m["width_mm"], i, m["length_mm"], m["angle_deg"]))
    # The rotation depends on which plane the members lie in. Rotating about Z
    # for an XZ-plane member puts every single one in the wrong place, so this
    # is generated per plane rather than assumed.
    i0, i1 = "XYZ".index(plane[0]), "XYZ".index(plane[1])
    body = {
        ("X", "Y"): ["    l = norm([d[0], d[1]]);",
                     "    a = atan2(d[1], d[0]);",
                     "    translate(p0) rotate([0, 0, a])",
                     "        translate([0, -w/2, -t/2]) cube([l, w, t]);"],
        ("X", "Z"): ["    l = norm([d[0], d[2]]);",
                     "    a = atan2(d[2], d[0]);",
                     "    translate(p0) rotate([0, -a, 0])",
                     "        translate([0, -t/2, -w/2]) cube([l, t, w]);"],
        ("Y", "Z"): ["    l = norm([d[1], d[2]]);",
                     "    a = atan2(d[2], d[1]);",
                     "    translate(p0) rotate([a, 0, 0])",
                     "        translate([-t/2, 0, -w/2]) cube([t, l, w]);"],
    }[("XYZ"[i0], "XYZ"[i1])]

    lines += ["", "// w = width in the %s plane, t = extrusion along %s"
              % (plane, ax),
              "module member(p0, p1, w, t) {",
              "    d = p1 - p0;"] + body + ["}", "",
              "module %s() {" % module_name, "    union() {"]
    for i, m in enumerate(extract["members"]):
        lines.append("        member(%s, %s, w%d, thick);"
                     % (m["start_mm"], m["end_mm"], i))
    # Gussets at every node. Two flat-ended rectangles meeting at an angle
    # overlap only in a small lens near the inside of the corner, so the joint
    # behaves as a hinge: a measured frame with the correct topology came out
    # 6x softer than the optimum until these were added.
    nodes = {}
    for m in extract["members"]:
        for p in (m["start_mm"], m["end_mm"]):
            key = tuple(round(v, 1) for v in p)
            nodes[key] = max(nodes.get(key, 0.0), m["width_mm"])
    i0n, i1n = "XYZ".index(plane[0]), "XYZ".index(plane[1])
    lines += ["    }", "",
              "    // Gussets: without these the mitred joints act as hinges.",
              "    union() {"]
    for p, w in sorted(nodes.items()):
        c = [0.0, 0.0, 0.0]
        c[i0n], c[i1n] = p[i0n], p[i1n]
        c["XYZ".index(ax)] = p["XYZ".index(ax)]
        lines.append("        translate(%s) node_gusset(%.2f, thick);"
                     % ([round(v, 2) for v in c], w * 1.15))
    lines += ["    }", "}", ""]

    gusset = {
        ("X", "Y"): "        translate([-s/2, -s/2, -t/2]) cube([s, s, t]);",
        ("X", "Z"): "        translate([-s/2, -t/2, -s/2]) cube([s, t, s]);",
        ("Y", "Z"): "        translate([-t/2, -s/2, -s/2]) cube([t, s, s]);",
    }[("XYZ"[i0], "XYZ"[i1])]
    lines = lines[:1] + ["", "module node_gusset(s, t) {", gusset, "}"] + lines[1:]
    lines += ["%s();" % module_name]
    return "\n".join(lines)

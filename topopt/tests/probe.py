"""Read exact dimensions off an STL by ray casting, for regression tests.

Not a voxel measurement. The features these tests care about -- slot walls,
finger faces, bore -- are planes, so a ray along one axis returns their
positions at full float precision. Voxelising to measure would put a
discretisation error on top of the very numbers being watched.

This lives in `tests/` rather than in the package because it exists to answer
"did the exported geometry change", not to be a feature.
"""

import numpy as np


def ray_crossings(tris, a, b, axis=0):
    """Sorted coordinates where the ray through (a, b) crosses the mesh.

    `axis` is the ray direction; `a` and `b` are the other two coordinates in
    increasing axis order (for axis=0 they are y and z).
    """
    o = [i for i in range(3) if i != axis]
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    e1u = v1[:, o[0]] - v0[:, o[0]]
    e2u = v2[:, o[0]] - v0[:, o[0]]
    e1v = v1[:, o[1]] - v0[:, o[1]]
    e2v = v2[:, o[1]] - v0[:, o[1]]
    det = e1u * e2v - e2u * e1v
    ok = np.abs(det) > 1e-12
    pu = a - v0[:, o[0]]
    pv = b - v0[:, o[1]]
    safe = np.where(ok, det, 1.0)
    s = np.where(ok, (pu * e2v - e2u * pv) / safe, -1.0)
    t = np.where(ok, (e1u * pv - pu * e1v) / safe, -1.0)
    inside = ok & (s >= 0) & (t >= 0) & (s + t <= 1)
    xs = (v0[:, axis] + s * (v1[:, axis] - v0[:, axis])
          + t * (v2[:, axis] - v0[:, axis]))
    xs = np.sort(xs[inside])
    if xs.size == 0:
        return xs
    return xs[np.concatenate(([True], np.diff(xs) > 1e-7))]


def solid_spans(tris, a, b, axis=0):
    """[(lo, hi), ...] intervals of material along the ray."""
    xs = ray_crossings(tris, a, b, axis)
    return [(float(xs[i]), float(xs[i + 1])) for i in range(0, len(xs) - 1, 2)]


def gaps(spans):
    """[(lo, hi), ...] empty intervals between consecutive solid spans."""
    return [(spans[i][1], spans[i + 1][0]) for i in range(len(spans) - 1)]


def open_extent(tris, axis, fixed_axis, fixed_value, sweep, lo, hi, step=0.02):
    """Width of the contiguous open run through a bore.

    Sweeps `sweep` (an axis index) from `lo` to `hi`, casting a ray along
    `axis` at each position, and returns the span over which the ray meets no
    material at all -- i.e. the clear opening of a through-hole.
    """
    vals = []
    for p in np.arange(lo, hi, step):
        coords = {sweep: float(p), fixed_axis: float(fixed_value)}
        o = [i for i in range(3) if i != axis]
        if ray_crossings(tris, coords[o[0]], coords[o[1]], axis).size == 0:
            vals.append(float(p))
    if not vals:
        return None, None, 0.0
    return min(vals), max(vals), max(vals) - min(vals)


def clear_radius(tris, axis, centre_a, centre_b, probe, r_lo=1.0, r_hi=20.0,
                 step=0.05, n_ang=72, floor=None, floor_axis=2):
    """Largest radius about an axis that is clear of material at `probe`.

    This is the "how big a knob actually fits" measurement: sweep a circle
    about the hinge axis at increasing radius and stop at the first radius
    where any sampled direction is blocked. `floor` drops samples below a
    plane (the bed is not an obstruction to a part sitting on it).

    Note: the `floor` test assumes `axis=0`, i.e. that the two swept axes are
    Y and Z so `floor_axis=2` is one of them. For any other ray axis the floor
    silently does nothing. Its one caller is the X-axis hinge.
    """
    o = [i for i in range(3) if i != axis]
    best = 0.0
    for r in np.arange(r_lo, r_hi, step):
        for ang in np.arange(0, 360, 360 / n_ang):
            a = centre_a + r * np.cos(np.radians(ang))
            b = centre_b + r * np.sin(np.radians(ang))
            coord = {o[0]: a, o[1]: b}
            if floor is not None and coord.get(floor_axis, 0.0) < floor:
                continue
            for lo, hi in solid_spans(tris, a, b, axis):
                if lo <= probe <= hi:
                    return best
        best = float(r)
    return best

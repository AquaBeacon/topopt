"""STL -> regular voxel grid.

Ray-parity fill along +Z: for every grid column (x, y) collect the z of every
triangle the column passes through, sort them, and mark the spans between
crossing 1-2, 3-4, ... as solid. This is exact for a watertight mesh and
degrades gracefully (a few stray voxels) for a leaky one.

Columns are sampled at voxel centres, which are irrational multiples of the
mesh coordinates often enough that ray-through-edge ties are rare; the parity
fill also tolerates the odd duplicate crossing because we de-duplicate
crossings that land within a tolerance of each other.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class Grid:
    """A voxel grid. `solid[i, j, k]` is True where material exists.

    origin is the *corner* of voxel (0,0,0); voxel centres sit at
    origin + (idx + 0.5) * h.
    """
    solid: np.ndarray      # bool, shape (nx, ny, nz)
    h: float               # voxel edge length, mm
    origin: np.ndarray     # (3,) mm

    @property
    def shape(self):
        return self.solid.shape

    @property
    def n_solid(self) -> int:
        return int(self.solid.sum())

    @property
    def volume_mm3(self) -> float:
        return self.n_solid * self.h ** 3

    def centres(self) -> np.ndarray:
        """(nx, ny, nz, 3) voxel-centre coordinates in mm."""
        nx, ny, nz = self.shape
        i = (np.arange(nx) + 0.5) * self.h + self.origin[0]
        j = (np.arange(ny) + 0.5) * self.h + self.origin[1]
        k = (np.arange(nz) + 0.5) * self.h + self.origin[2]
        return np.stack(np.meshgrid(i, j, k, indexing="ij"), axis=-1)

    def bbox(self):
        return self.origin, self.origin + np.array(self.shape) * self.h

    def box_mask(self, lo, hi) -> np.ndarray:
        """Voxels whose centre lies inside the axis-aligned box [lo, hi] (mm)."""
        c = self.centres()
        lo = np.asarray(lo, float)
        hi = np.asarray(hi, float)
        m = np.ones(self.shape, bool)
        for a in range(3):
            m &= (c[..., a] >= lo[a]) & (c[..., a] <= hi[a])
        return m


def load_stl(path) -> np.ndarray:
    """Read binary or ASCII STL -> (ntri, 3, 3) float array of vertices."""
    with open(path, "rb") as f:
        head = f.read(84)
        if len(head) < 84:
            raise ValueError("file too short to be an STL: %s" % path)
        n_tri = int(np.frombuffer(head[80:84], dtype="<u4")[0])
        body = f.read()

    # A binary STL body is exactly 50 bytes per triangle. ASCII files declare a
    # nonsense triangle count here, so the size check is the reliable test.
    if n_tri > 0 and len(body) == n_tri * 50:
        rec = np.frombuffer(body, dtype=np.dtype([
            ("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")]))
        return rec["v"].astype(np.float64)

    with open(path, "r", errors="replace") as f:
        verts = [tuple(float(x) for x in line.split()[1:4])
                 for line in f if line.strip().startswith("vertex")]
    if not verts or len(verts) % 3:
        raise ValueError("could not parse STL (neither binary nor ASCII): %s" % path)
    return np.asarray(verts, float).reshape(-1, 3, 3)


def voxelize_stl(path_or_tris, h=None, target_elements=60000, pad=0,
                 origin=None, shape=None) -> Grid:
    """Voxelise an STL (path or triangle array) into a Grid.

    h            voxel size in mm; when None it is chosen so the *bounding box*
                 holds roughly `target_elements` voxels (the solid fraction is
                 usually 20-60 % of that, which is the count that drives runtime)
    pad          extra voxels of empty space around the part on every side
    origin/shape pin the grid explicitly (used to keep several parts on one grid)
    """
    tris = path_or_tris
    if not isinstance(tris, np.ndarray):
        tris = load_stl(tris)
    tris = np.asarray(tris, float)
    if tris.ndim != 3 or tris.shape[1:] != (3, 3):
        raise ValueError("expected (ntri, 3, 3) triangles, got %r" % (tris.shape,))

    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    size = np.maximum(hi - lo, 1e-9)

    if h is None:
        h = float((size.prod() / max(target_elements, 1)) ** (1.0 / 3.0))
        h = max(h, 1e-3)

    if origin is None:
        origin = lo - pad * h
    else:
        origin = np.asarray(origin, float)
    if shape is None:
        shape = np.ceil((hi + pad * h - origin) / h).astype(int)
        shape = np.maximum(shape, 1)
    nx, ny, nz = (int(v) for v in shape)

    # Column sample points (voxel centres in x and y).
    cx = origin[0] + (np.arange(nx) + 0.5) * h
    cy = origin[1] + (np.arange(ny) + 0.5) * h

    # Per-column crossing lists. A python list of lists is fine: we append whole
    # numpy arrays, not scalars.
    crossings = [[] for _ in range(nx * ny)]

    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    e1 = v1 - v0
    e2 = v2 - v0
    # Signed area in XY; near-zero means the triangle is vertical (parallel to
    # the ray) and cannot define a crossing.
    det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    live = np.abs(det) > 1e-12

    tlo = tris.min(axis=1)
    thi = tris.max(axis=1)

    for t in np.nonzero(live)[0]:
        i0 = np.searchsorted(cx, tlo[t, 0], "left")
        i1 = np.searchsorted(cx, thi[t, 0], "right")
        j0 = np.searchsorted(cy, tlo[t, 1], "left")
        j1 = np.searchsorted(cy, thi[t, 1], "right")
        if i0 >= i1 or j0 >= j1:
            continue

        px = cx[i0:i1]
        py = cy[j0:j1]
        dx = px[:, None] - v0[t, 0]
        dy = py[None, :] - v0[t, 1]

        # Barycentric coordinates of the column point in the XY projection.
        u = (dx * e2[t, 1] - dy * e2[t, 0]) / det[t]
        v = (dy * e1[t, 0] - dx * e1[t, 1]) / det[t]
        inside = (u >= 0) & (v >= 0) & (u + v <= 1.0)
        if not inside.any():
            continue

        z = v0[t, 2] + u * e1[t, 2] + v * e2[t, 2]
        ii, jj = np.nonzero(inside)
        col = (ii + i0) * ny + (jj + j0)
        zz = z[ii, jj]
        for c, zv in zip(col, zz):
            crossings[c].append(zv)

    solid = np.zeros((nx, ny, nz), bool)
    zc = origin[2] + (np.arange(nz) + 0.5) * h
    tol = h * 1e-4

    for c, zs in enumerate(crossings):
        if len(zs) < 2:
            continue
        z = np.sort(np.asarray(zs, float))
        # Drop near-duplicate crossings (shared edges hit twice); keeping them
        # would flip parity twice and punch a hole through the part.
        keep = np.ones(z.size, bool)
        keep[1:] = np.diff(z) > tol
        z = z[keep]
        if z.size < 2:
            continue
        if z.size % 2:
            z = z[:-1]
        i, j = divmod(c, ny)
        col = solid[i, j]
        for a in range(0, z.size, 2):
            col |= (zc >= z[a]) & (zc < z[a + 1])

    return Grid(solid=solid, h=float(h), origin=np.asarray(origin, float))


def largest_connected(grid: Grid) -> Grid:
    """Keep only the largest 6-connected blob.

    Floating specks are meaningless to FEA but they add singular DOFs, so the
    solver would otherwise have to deal with a structurally singular system.
    """
    solid = grid.solid
    labels = np.zeros(solid.shape, np.int32)
    cur = 0
    best_label, best_size = 0, 0
    idx = np.argwhere(solid)
    seen = np.zeros(solid.shape, bool)
    nx, ny, nz = solid.shape

    for start in idx:
        si, sj, sk = (int(v) for v in start)
        if seen[si, sj, sk]:
            continue
        cur += 1
        stack = [(si, sj, sk)]
        seen[si, sj, sk] = True
        size = 0
        while stack:
            i, j, k = stack.pop()
            labels[i, j, k] = cur
            size += 1
            for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                               (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                a, b, c = i + di, j + dj, k + dk
                if 0 <= a < nx and 0 <= b < ny and 0 <= c < nz \
                        and solid[a, b, c] and not seen[a, b, c]:
                    seen[a, b, c] = True
                    stack.append((a, b, c))
        if size > best_size:
            best_size, best_label = size, cur

    return Grid(solid=(labels == best_label), h=grid.h, origin=grid.origin)

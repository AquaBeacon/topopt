"""Self-support (overhang) filter for the build direction.

Why. A SIMP result that only answers "where does the load path want material"
comes out with members hanging in the air: horizontal bridges, undersides at
0 degrees, and islands that are structurally connected but printed on
nothing. Removing support afterwards does not fix a load path that runs
through a face the printer could not make. The way to get a printable shape
is to make the optimiser see the layers while it decides.

What. The additive-manufacturing filter of Langelaar (Struct Multidisc Optim
55, 2017): a printed density field xi is built layer by layer from the design
density rho, starting at the build plate. A cell can be as dense as its
design value only if something under it can carry it:

    xi[k] = smin( rho[k], smax( xi[k-1] on the cross footprint ) )

where the footprint is the cell directly below plus its four in-plane
neighbours, which on a cubic grid is exactly a 45 degree cone. `smin` and
`smax` are the smooth forms so the whole thing is differentiable, and the
gradient is taken by an adjoint sweep from the top layer down -- it is exact,
not an approximation, and `tests/test_amfilter.py` checks it against central
differences.

Support may be allowed on two conditions, because sometimes it is simply
needed (a seat that has to be where it is, a boss under a flange):

- **short support columns** (`reach` layers): a cell is also supported by
  material up to `reach` layers further down inside the widening cone -- a
  slicer would put a support column of that height there -- and by the
  plate when it is within `reach` layers of it;
- **allowed regions** (`free` mask): inside them the rule is off entirely.

Where it sits. Density filter -> **this** -> Heaviside projection -> symmetry
-> impose. The density filter first, so the length scale is set before the
layering; the projection after, so the printed field is what gets sharpened.

What it does not do. It knows one build direction (`axis`, `up`). It does not
touch preserve: preserve is re-imposed after everything and its own
undersides are CAD's business (the finish stage cuts them from CAD).
"""

import numpy as np


def _pow(base, e):
    """base ** e, by repeated squaring when `e` is a small whole number.

    The filter's p-norm is p = 40 by default, and `np.power(x, 40.0)` goes
    through exp/log per element; six multiplies give the same number to
    round-off and were measured several times faster on the layer stacks
    this file works on. Non-integer exponents (the 1/q roots) still use
    np.power.
    """
    if float(e) != int(e) or not (1 <= int(e) <= 64):
        return np.power(base, e)
    n = int(e)
    result = None
    sq = base
    while n:
        if n & 1:
            result = sq if result is None else result * sq
        n >>= 1
        if n:
            sq = sq * sq
    return result


class AMFilter:
    """Layer-by-layer self-support filter along `axis` (0, 1 or 2).

    `up` is +1 when the part grows toward increasing index (plate at the low
    end) and -1 when the plate is at the high end of the grid. `base` is the
    layer index, counted from the plate side, where the domain starts: the
    grid usually has a few empty layers under the design space (the
    tall_tripod grid begins 20 mm below the plate), and the plate is the
    first layer that can hold material, not the first layer of the array.
    Getting this wrong makes the entire domain "in the air" and the filter
    erases it -- measured, 2026-09-05.

    `reach` is the allowed support-column height in layers (0 = material
    must sit directly in the cone below). `free` is a boolean grid mask of
    cells where the rule does not apply.
    """

    def __init__(self, shape, axis=2, up=+1, p=40.0, eps=1e-4, base=0,
                 reach=0, free=None):
        self.shape = tuple(int(n) for n in shape)
        self.axis = int(axis)
        self.up = 1 if int(up) >= 0 else -1
        self.base = int(base)
        self.reach = max(0, int(reach))
        self.p = float(p)
        self.eps = float(eps)
        # Langelaar's correction: the smooth max is (sum c^p)^(1/q) with q
        # chosen so that n equal supports at 0.5 give exactly 0.5. A plain
        # p-norm gives n^(1/p) per layer, which compounds: a grey column at
        # 0.45 on the plate reads 0.5 four layers up and 0.6 at eight, the
        # projection then keeps the top and drops the plate, and the printed
        # shape floats. Measured on tall_tripod, 2026-09-05. Neutral at 0.5
        # because 0.5 is the projection threshold: nothing above an
        # unprintable layer can then survive thresholding.
        self.q5 = self._q(5)
        self.qn = self._q(self.reach + 1)
        self._free = None
        if free is not None:
            self._free = self._to_layers(np.asarray(free, bool)).astype(bool)
        self._st = None

    def _q(self, n):
        return self.p + (np.log(n) / np.log(0.5) if n > 1 else 0.0)

    # ------------------------------------------------------------ helpers

    def _to_layers(self, a):
        """(nx, ny, nz) -> (nlayers, ...) with layer 0 on the plate."""
        a = np.moveaxis(np.asarray(a), self.axis, 0)
        return a[::-1] if self.up < 0 else a

    def _from_layers(self, a):
        a = a[::-1] if self.up < 0 else a
        return np.moveaxis(a, 0, self.axis)

    @staticmethod
    def _footprint(prev):
        """The 5 support candidates of every cell: the cell below and its
        in-plane neighbours, as a (5, ny, nz) stack. Outside the grid there
        is nothing, so the shifted copies are zero-padded, not wrapped."""
        c = np.zeros((5,) + prev.shape)
        c[0] = prev
        c[1, 1:, :] = prev[:-1, :]
        c[2, :-1, :] = prev[1:, :]
        c[3, :, 1:] = prev[:, :-1]
        c[4, :, :-1] = prev[:, 1:]
        return c

    @staticmethod
    def _footprint_T(g):
        """Transpose of `_footprint`: (5, ny, nz) -> (ny, nz)."""
        out = g[0].copy()
        out[:-1, :] += g[1, 1:, :]
        out[1:, :] += g[2, :-1, :]
        out[:, :-1] += g[3, :, 1:]
        out[:, 1:] += g[4, :, :-1]
        return out

    def _smax(self, c, q):
        """Corrected p-norm over axis 0. -> (S, N) with N the plain p-norm
        the adjoint needs."""
        T = _pow(np.clip(c, 0.0, None), self.p).sum(axis=0)
        N = np.power(T, 1.0 / self.p)
        S = np.power(T, 1.0 / q)
        return S, N

    def _dsmax(self, c, S, N, q):
        """d(smax)/d(c_i) for every candidate, written with the ratio
        c_i / N <= 1 so nothing overflows when N is tiny."""
        Nn = np.where(N > 0, N, 1.0)
        ratio = _pow(np.clip(c, 0.0, None) / Nn, self.p - 1.0)
        coef = np.where(N > 0, (self.p / q) * S / Nn, 0.0)
        return ratio * coef[None]

    def _smin(self, a, b):
        d = a - b
        r = np.sqrt(d * d + self.eps)
        return 0.5 * (a + b - r + np.sqrt(self.eps))

    def _dsmin(self, a, b):
        d = a - b
        r = np.sqrt(d * d + self.eps)
        return 0.5 * (1.0 - d / r), 0.5 * (1.0 + d / r)

    def _bypass(self, k, n_shape):
        """Cells at layer k the rule does not apply to: inside an allowed
        region, or close enough to the plate for a short support column."""
        by = np.zeros(n_shape, bool)
        if k - self.base <= self.reach:
            by[:] = True
        if self._free is not None:
            by |= self._free[k]
        return by

    # ------------------------------------------------------------ forward

    # The J cone terms of one layer are computed as one batch. Term j at
    # layer k comes from term j-1 at layer k-1 (term 1 from the printed
    # layer below), so the stack [xi[k-1], A[0, k-1], ..., A[J-2, k-1]] is
    # everything layer k needs and one footprint / one p-norm serves all of
    # them. A term with k - j < b (nothing that far below the plate) is zero
    # in the reference formulation; here it is zero because its input is
    # zero -- the same condition, one layer earlier -- and 0 ** (1/q) = 0.
    # Why: `vol_of` in the optimiser's volume bisection calls `forward`
    # ~17 times per iteration, and with reach 4 (five terms) the per-layer
    # Python overhead was most of the optimiser's non-solver time.

    @staticmethod
    def _footprint_batch(prev):
        """(J, ny, nz) -> (J, 5, ny, nz), zero-padded shifts."""
        c = np.zeros((prev.shape[0], 5) + prev.shape[1:])
        c[:, 0] = prev
        c[:, 1, 1:, :] = prev[:, :-1, :]
        c[:, 2, :-1, :] = prev[:, 1:, :]
        c[:, 3, :, 1:] = prev[:, :, :-1]
        c[:, 4, :, :-1] = prev[:, :, 1:]
        return c

    @staticmethod
    def _footprint_T_batch(g):
        """Transpose of `_footprint_batch`: (J, 5, ny, nz) -> (J, ny, nz)."""
        out = g[:, 0].copy()
        out[:, :-1, :] += g[:, 1, 1:, :]
        out[:, 1:, :] += g[:, 2, :-1, :]
        out[:, :, :-1] += g[:, 3, :, 1:]
        out[:, :, 1:] += g[:, 4, :, :-1]
        return out

    def _smax_batch(self, c, q):
        """Corrected p-norm over axis 1 of (J, 5, ny, nz). -> (S, N)."""
        T = _pow(np.clip(c, 0.0, None), self.p).sum(axis=1)
        N = np.power(T, 1.0 / self.p)
        S = np.power(T, 1.0 / q)
        return S, N

    def _dsmax_batch(self, c, S, N, q):
        """d(smax)/d(c) for (J, 5, ny, nz) candidates with (J, ny, nz) S, N."""
        Nn = np.where(N > 0, N, 1.0)
        ratio = _pow(np.clip(c, 0.0, None) / Nn[:, None], self.p - 1.0)
        coef = np.where(N > 0, (self.p / q) * S / Nn, 0.0)
        return ratio * coef[:, None]

    def _stack_below(self, xi, A, k):
        """[xi[k-1], A[0, k-1], ..., A[J-2, k-1]] as (J, ny, nz)."""
        J = A.shape[0]
        if J == 1:
            return xi[k - 1][None]
        return np.concatenate([xi[k - 1][None], A[:J - 1, k - 1]], axis=0)

    def forward(self, rho):
        """Design density -> printed density (same shape)."""
        r = self._to_layers(np.asarray(rho, float))
        n = r.shape[0]
        b = min(self.base, n - 1)
        J = self.reach + 1                         # support terms per cell
        xi = np.empty_like(r)
        A = np.zeros((J,) + r.shape)               # A[j-1][k]: cone term j
        NA = np.zeros((J,) + r.shape)              # plain norms of the same
        S = np.ones_like(r)
        NS = np.ones_like(r)
        bypass = np.zeros(r.shape, bool)
        xi[:b + 1] = r[:b + 1]                     # on (or under) the plate
        for k in range(b + 1, n):
            # cone terms: j = 1 from the printed layer below, j >= 2 from the
            # term one layer down and one order shallower -- all J at once
            A[:, k], NA[:, k] = self._smax_batch(
                self._footprint_batch(self._stack_below(xi, A, k)), self.q5)
            by = self._bypass(k, r.shape[1:])
            bypass[k] = by
            if J == 1:
                S[k], NS[k] = A[0, k], NA[0, k]
            else:
                S[k], NS[k] = self._smax(A[:, k], self.qn)
            xi[k] = np.where(by, r[k], self._smin(r[k], S[k]))
        self._st = (r, xi, A, NA, S, NS, bypass, b)
        return self._from_layers(xi)

    # ------------------------------------------------------------ adjoint

    def backward(self, dxi):
        """d(J)/d(rho) given d(J)/d(xi), for the last `forward` call."""
        if self._st is None:
            raise RuntimeError("backward() before forward()")
        g = self._to_layers(np.asarray(dxi, float))
        r, xi, A, NA, S, NS, bypass, b = self._st
        n = r.shape[0]
        J = A.shape[0]
        drho = np.zeros_like(r)
        carry = np.zeros_like(r)                   # dJ/dxi from layers above
        GA = np.zeros_like(A)                      # dJ/dA[j][k]
        for k in range(n - 1, b, -1):
            lam = g[k] + carry[k]
            da, db = self._dsmin(r[k], S[k])
            by = bypass[k]
            drho[k] = np.where(by, lam, lam * da)
            t = np.where(by, 0.0, lam * db)        # dJ/dS_k
            if J == 1:
                GA[0, k] += t
            else:
                GA[:, k] += self._dsmax(A[:, k], S[k], NS[k], self.qn) * t[None]
            # A[j][k] is used by S_k (done) and by A[j+1][k+1] (already
            # processed), so GA[:, k] is complete: push it one layer down,
            # all J terms at once (a term that is zero has a zero derivative,
            # so the k - j < b cases contribute nothing, as before).
            c = self._footprint_batch(self._stack_below(xi, A, k))
            push = self._footprint_T_batch(
                self._dsmax_batch(c, A[:, k], NA[:, k], self.q5)
                * GA[:, k][:, None])
            carry[k - 1] += push[0]
            if J > 1:
                GA[:J - 1, k - 1] += push[1:]
        drho[:b + 1] = g[:b + 1] + carry[:b + 1]
        return self._from_layers(drho)


def plate_index(domain, axis=2, up=+1):
    """Layer index (from the plate side) of the first layer of `domain` that
    holds material -- the plate. 0 when the domain reaches the array edge."""
    idx = np.argwhere(np.asarray(domain, bool))
    if idx.size == 0:
        return 0
    n = domain.shape[axis]
    return int(idx[:, axis].min()) if up > 0 else int(n - 1 - idx[:, axis].max())


def _cross_dilate(m):
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _supported_layer(m, k, base, reach, free):
    """Hard rule: which cells of layer k have support -- material inside the
    45 degree cone up to `reach` layers further down, the plate within
    `reach` layers, or an allowed region."""
    sup = np.zeros(m.shape[1:], bool)
    if k - base <= reach:
        sup[:] = True
    if free is not None:
        sup |= free[k]
    for j in range(1, reach + 2):
        if k - j < base:
            break
        d = m[k - j]
        for _ in range(j):
            d = _cross_dilate(d)
        sup |= d
    return sup


def _layers(mask, axis, up):
    m = np.moveaxis(np.asarray(mask, bool), axis, 0)
    return m[::-1] if up < 0 else m


def unsupported_voxels(mask, axis=2, up=+1, base=0, reach=0, free=None):
    """Solid voxels with no support under the hard version of the rule, for
    reporting on the thresholded geometry. -> bool mask"""
    m = _layers(mask, axis, up)
    fr = _layers(free, axis, up) if free is not None else None
    out = np.zeros_like(m)
    for k in range(int(base) + 1, m.shape[0]):
        out[k] = m[k] & ~_supported_layer(m, k, int(base), int(reach), fr)
    if up < 0:
        out = out[::-1]
    return np.moveaxis(out, 0, axis)


def supportable_voxels(mask, axis=2, up=+1, base=0, reach=0, free=None):
    """Locations where adding material obeys the same hard support rule."""
    m = _layers(mask, axis, up)
    fr = _layers(free, axis, up) if free is not None else None
    out = np.zeros_like(m)
    out[int(base)] = True
    for k in range(int(base) + 1, m.shape[0]):
        out[k] = _supported_layer(m, k, int(base), int(reach), fr)
    if up < 0:
        out = out[::-1]
    return np.moveaxis(out, 0, axis)


def prune_unsupported(mask, axis=2, up=+1, base=0, keep=None, reach=0, free=None):
    """The largest printable subset of `mask`: sweep from the plate up and
    drop every voxel without support under the hard rule. `keep` voxels
    (preserve) are never dropped and always count as support.
    -> (pruned mask, number of voxels removed)

    Why this exists at all when the filter already enforces the rule: the
    smooth min sits up to 0.005 above the true min, so in a band 0.005 wide
    a cell can project above the threshold while the cell holding it up
    projects below. One or two voxels per part, measured -- but one is
    enough to make the print fail at that layer, so the thresholded shape is
    projected onto the printable set and the count is reported."""
    m = _layers(mask, axis, up).copy()
    kp = _layers(keep, axis, up) if keep is not None else np.zeros_like(m)
    fr = _layers(free, axis, up) if free is not None else None
    before = int(m.sum())
    for k in range(int(base) + 1, m.shape[0]):
        m[k] &= (_supported_layer(m, k, int(base), int(reach), fr) | kp[k])
    if up < 0:
        m = m[::-1]
    return np.moveaxis(m, 0, axis), before - int(m.sum())


def parse_overhang(design, build_axis):
    """The `design.overhang` knob -> dict or None.

    true / absent  -> self-support along `build_axis`, plate at the low end
    false          -> off
    {"up": "-Z", "p": 40, "ramp": 0.33,
     "max_support_mm": 0, "allow_support": []}
                   -> explicit direction (must match build_axis); `ramp` is
                      the fraction of the iterations over which the rule is
                      blended in; `max_support_mm` allows support columns up
                      to that height (and material within that height of the
                      plate); `allow_support` names regions where support is
                      simply allowed.

    Why a ramp. The corrected smooth max is neutral at density 0.5 (it has to
    be: that is the projection threshold, and matching them is what
    guarantees nothing above an unprintable layer survives thresholding).
    Below 0.5 it decays layer by layer -- 14 % per layer at a uniform 0.04 --
    so a study that starts from a low uniform density has its whole field
    erased above the first few layers before the optimiser has drawn a load
    path (measured: tall_tripod at volfrac 0.04 collapsed to zero volume).
    Blending the printed field in over the first third lets the structure
    form first and then be made self-supporting while beta is still low.
    """
    spec = design.get("overhang", True)
    if spec is False or spec is None:
        return None
    axis = int(build_axis)
    up = +1
    p = 40.0
    ramp = 1.0 / 3.0
    max_support_mm = 0.0
    allow = []
    if isinstance(spec, dict):
        ramp = float(spec.get("ramp", ramp))
        if not 0.0 <= ramp <= 1.0:
            raise ValueError("overhang.ramp must be between 0 and 1 (fraction "
                             "of the iterations over which the rule is phased in)")
        u = str(spec.get("up", "+" + "XYZ"[axis])).upper().strip()
        sign = -1 if u.startswith("-") else +1
        letter = u.lstrip("+-")
        if letter and "XYZ".index(letter) != axis:
            raise ValueError("overhang.up %r does not run along build_axis %s -- "
                             "the layers are perpendicular to the build axis by "
                             "definition" % (spec.get("up"), "XYZ"[axis]))
        up = sign
        p = float(spec.get("p", 40.0))
        max_support_mm = float(spec.get("max_support_mm", 0.0))
        if max_support_mm < 0:
            raise ValueError("overhang.max_support_mm must be >= 0")
        allow = spec.get("allow_support", []) or []
        if isinstance(allow, str):
            allow = [allow]
        if not all(isinstance(a, str) for a in allow):
            raise ValueError("overhang.allow_support must be a list of region names")
    return {"axis": axis, "up": up, "p": p, "ramp": ramp,
            "max_support_mm": max_support_mm, "allow_support": list(allow)}

"""Geometric multigrid preconditioner for the voxel stiffness operator.

Why this exists. Jacobi-preconditioned conjugate gradients needs a number of
iterations that grows with the grid and with how discrete the density field
has become: measured on the tall_tripod study at 4 mm (101,808 elements) it
was 743 iterations at uniform density and 4,195 once the field was 0/1. Every
optimisation iteration pays that for every load case, and it is what turned
an 80-iteration run into an overnight job. Multigrid makes the iteration
count nearly independent of the grid -- tens rather than thousands -- and it
does so on the same matrix-free element operator, so nothing about the
physics changes: the preconditioner only decides how fast K u = f is solved,
not what u is.

How it works. The voxel grid coarsens by merging 2x2x2 fine elements into one
coarse element. Displacements move between levels by trilinear interpolation
(`prolong`) and its transpose (`restrict`). The coarse operator is the exact
Galerkin product P^T K P, built element by element: each fine element's
stiffness (already scaled by its SIMP density) is mapped onto its parent
coarse element through a fixed 24x24 transfer matrix, one per sub-position,
and summed. That keeps every level a list of hexahedra with their own 24x24
matrices, so the same gather / multiply / scatter matvec serves every level
and both array backends. Fixed degrees of freedom are handled by zeroing the
rows of P that land on them, which is what makes the coarse operators
consistent with the constrained fine one. The coarsest level is small enough
to factorise densely.

Smoothing is a Chebyshev polynomial in D^-1 K, which needs no sequential
sweep (so it runs on a GPU) and is symmetric, so the V-cycle is a valid SPD
preconditioner for plain CG. The largest eigenvalue it needs is estimated by
a few power iterations per level when the hierarchy is built.

Limits. Everything here is per solve of one stiffness operator: the hierarchy
is rebuilt whenever the density or the constraints change, which in the
optimiser is once per iteration per group of load cases that share their
supports. That costs a few fine-level matvec equivalents and is included in
the reported solve time.
"""

import time

import numpy as np

from .hex8 import element_dofs

# Sub-position s = a + 2b + 4c of a fine element inside its coarse parent.
_SUBPOS = [(s & 1, (s >> 1) & 1, (s >> 2) & 1) for s in range(8)]

# The coarsening *pattern* -- which fine element lands in which coarse one,
# at which sub-position, and the coarse DOF table -- depends only on the
# element list, not on the density. The optimiser rebuilds the hierarchy
# every iteration for the same element list, and `np.unique(axis=0)` on
# 100k rows was 20 ms per level per group per iteration of pure repetition.
_COARSEN_CACHE = {}
_COARSEN_CACHE_MAX = 64


def _coarsen_pattern(shape, elements):
    from .solve import mesh_key
    key = mesh_key(shape, elements)
    hit = _COARSEN_CACHE.get(key)
    if hit is not None:
        return hit
    el = np.asarray(elements)
    parent = el // 2
    sub = el - 2 * parent
    s_of = sub[:, 0] + 2 * sub[:, 1] + 4 * sub[:, 2]
    cel, inv = np.unique(parent, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    cshape = tuple((n + 1) // 2 for n in shape)
    cedof = element_dofs(cshape, cel)
    sels = [np.nonzero(s_of == s)[0] for s in range(8)]
    out = {"cel": cel, "inv": inv, "cshape": cshape, "cedof": cedof,
           "sels": sels}
    if len(_COARSEN_CACHE) >= _COARSEN_CACHE_MAX:
        _COARSEN_CACHE.pop(next(iter(_COARSEN_CACHE)))
    _COARSEN_CACHE[key] = out
    return out


def transfer_matrices():
    """T[s] (24, 24): coarse-element nodal DOFs -> fine-element nodal DOFs for
    the fine element at sub-position s. Trilinear weights; local node order
    n = a + 2b + 4c on both levels (hex8 convention)."""
    T = np.zeros((8, 24, 24))
    for s, (a, b, c) in enumerate(_SUBPOS):
        for nf in range(8):
            af, bf, cf = nf & 1, (nf >> 1) & 1, (nf >> 2) & 1
            xi, eta, zeta = (a + af) / 2.0, (b + bf) / 2.0, (c + cf) / 2.0
            for nc in range(8):
                A, B, C = nc & 1, (nc >> 1) & 1, (nc >> 2) & 1
                w = ((xi if A else 1.0 - xi) * (eta if B else 1.0 - eta)
                     * (zeta if C else 1.0 - zeta))
                if w > 0.0:
                    for comp in range(3):
                        T[s, 3 * nf + comp, 3 * nc + comp] = w
    return T


def _prod(shape):
    out = 1
    for n in shape:
        out *= int(n)
    return out


def _index_add_unique(be, target, idx, vals):
    if be.name == "cpu":
        target[idx] += vals
        return target
    return target.index_add_(0, idx, vals)


def _diag_from(be, d):
    if be.name == "cpu":
        return np.diag(d)
    return be.torch.diag(d)


class Level:
    """One grid level: an element list with 24x24 stiffness matrices.

    The finest level shares one `KE` scaled per element by `emul`; coarser
    levels carry an explicit (nel, 24, 24) `Ke`. `free` marks the DOFs the
    operator acts on; every other DOF is an identity row.
    """

    def __init__(self, be, shape, elements, edof, free, KE=None, emul=None, Ke=None,
                 mixed=False):
        self.be = be
        # `mixed`: keep float32 copies of the element matrices and D^-1 and
        # run the smoother's matvecs in float32 when handed a float32
        # vector. On the GPU the float64 element multiply is the whole cost
        # of a matvec (measured 1.5 ms of 2.4 ms at 50k elements, x3 right-
        # hand sides; float32 0.23 ms), and the V-cycle is a preconditioner
        # -- it only has to be *close* to K^-1, the outer CG in float64
        # decides the answer. See `MGPreconditioner.apply`.
        self.mixed = bool(mixed)
        self.shape = tuple(int(n) for n in shape)
        self.nshape = tuple(n + 1 for n in self.shape)
        self.nnode = _prod(self.nshape)
        self.ndof = 3 * self.nnode
        self.elements = np.asarray(elements)              # numpy, (nel, 3)
        self.nel = int(self.elements.shape[0])
        self.edof = be.asarray(np.asarray(edof, np.int64))
        self.edof_flat = self.edof.reshape(-1)
        self.free = be.asarray(np.asarray(free, bool))
        self.free_col = self.free[:, None]
        self.KE = None if KE is None else be.asarray(np.asarray(KE, float))
        self.emul = None if emul is None else be.asarray(np.asarray(emul, float))
        self.Ke = Ke                                       # backend (nel,24,24) or None
        self.dinv = None
        self.lam_max = None
        self.coarse = None                                 # dense Cholesky factor
        self._build_diag()
        self.KE32 = self.emul32 = self.Ke32 = self.dinv32 = None
        if self.mixed:
            f32 = be.float32
            self.KE32 = None if self.KE is None else be.cast(self.KE, f32)
            self.emul32 = None if self.emul is None else be.cast(self.emul, f32)
            self.Ke32 = None if self.Ke is None else be.cast(self.Ke, f32)
            self.dinv32 = be.cast(self.dinv, f32)

    def _is32(self, v):
        return self.mixed and self.be.dtype_of(v) == self.be.float32

    # ---------------------------------------------------------- operator

    def elem_matrices(self, sel=None):
        """(n, 24, 24) unmasked element stiffness for `sel` (backend index) or all."""
        if self.Ke is not None:
            return self.Ke if sel is None else self.Ke[sel]
        em = self.emul if sel is None else self.emul[sel]
        return self.KE[None, :, :] * em[:, None, None]

    def _apply_elements(self, ue):
        """(nel, 24, m) element displacements -> element forces."""
        be = self.be
        nel, _, m = ue.shape
        f32 = self._is32(ue)
        KE, emul, Ke = ((self.KE32, self.emul32, self.Ke32) if f32
                        else (self.KE, self.emul, self.Ke))
        if Ke is None:
            # One large GEMM rather than nel tiny ones.
            x = be.moveaxis(ue, 1, 2).reshape(nel * m, 24) @ KE
            fe = be.moveaxis(x.reshape(nel, m, 24), 2, 1)
            return fe * emul[:, None, None]
        return Ke @ ue

    def matvec(self, v):
        """K v for v of shape (ndof, m). Identity on non-free DOFs."""
        be = self.be
        vf = be.where(self.free_col, v, 0.0)
        ue = vf[self.edof]
        fe = self._apply_elements(ue)
        out = be.scatter_add(self.edof_flat, fe.reshape(self.nel * 24, -1),
                             self.ndof)
        return be.where(self.free_col, out, v)

    def _build_diag(self):
        be = self.be
        if self.Ke is None:
            d_e = be.asarray(np.diag(be.to_numpy(self.KE)))[None, :] * self.emul[:, None]
        else:
            d_e = self._diag_of(self.Ke)
        diag = be.scatter_add(self.edof_flat, d_e.reshape(-1), self.ndof)
        # A dead DOF (no element, or fixed) is an identity row.
        self.free = self.free & (diag > 0)
        self.free_col = self.free[:, None]
        self.diag = be.where(self.free, diag, 1.0)
        self.dinv = 1.0 / self.diag

    def _diag_of(self, Ke):
        be = self.be
        if be.name == "cpu":
            return np.ascontiguousarray(np.einsum("eii->ei", Ke))
        return be.torch.diagonal(Ke, dim1=1, dim2=2).contiguous()

    # ---------------------------------------------------------- smoother

    def estimate_lam_max(self, iters=15, margin=1.15, seed=0):
        """Largest eigenvalue of D^-1 K by power iteration, with a safety margin.
        Chebyshev needs an upper bound: an underestimate makes it diverge, an
        overestimate only smooths a little less well."""
        be = self.be
        v = be.randn((self.ndof, 1), seed)
        v = be.where(self.free_col, v, 0.0)
        lam = 1.0
        for _ in range(iters):
            n = be.col_norm(v)
            v = v / be.where(n > 0, n, 1.0)
            w = self.dinv[:, None] * self.matvec(v)
            w = be.where(self.free_col, w, 0.0)
            lam = be.item(be.col_dot(v, w)[0])
            v = w
        self.lam_max = float(lam) * margin
        return self.lam_max

    def smooth(self, b, x, degree, lam_ratio=30.0):
        """Chebyshev semi-iteration on K x = b with D^-1 as the inner
        preconditioner (Adams et al. 2003). `x` None means start from 0."""
        hi = self.lam_max
        lo = hi / lam_ratio
        theta = 0.5 * (hi + lo)
        delta = 0.5 * (hi - lo)
        sigma = theta / delta
        rho = 1.0 / sigma
        dinv = (self.dinv32 if self._is32(b) else self.dinv)[:, None]
        r = b if x is None else b - self.matvec(x)
        d = (dinv * r) / theta
        x = d if x is None else x + d
        for _ in range(degree - 1):
            rho_new = 1.0 / (2.0 * sigma - rho)
            r = r - self.matvec(d)
            d = (rho_new * rho) * d + (2.0 * rho_new / delta) * (dinv * r)
            x = x + d
            rho = rho_new
        return x

    # ---------------------------------------------------------- transfers

    def _interp_axis(self, a, axis, n_fine):
        be = self.be
        a = be.moveaxis(a, axis, 0)
        ne = (n_fine + 1) // 2
        no = n_fine // 2
        out = be.zeros((n_fine,) + tuple(a.shape[1:]), dtype=be.dtype_of(a))
        out[0::2] = a[:ne]
        if no:
            out[1::2] = 0.5 * (a[:no] + a[1:no + 1])
        return be.moveaxis(out, 0, axis)

    def _restrict_axis(self, a, axis, n_coarse):
        be = self.be
        a = be.moveaxis(a, axis, 0)
        n_fine = a.shape[0]
        ne = (n_fine + 1) // 2
        no = n_fine // 2
        out = be.zeros((n_coarse,) + tuple(a.shape[1:]), dtype=be.dtype_of(a))
        out[:ne] += a[0::2]
        if no:
            odd = 0.5 * a[1::2]
            out[:no] += odd
            out[1:no + 1] += odd
        return be.moveaxis(out, 0, axis)

    def prolong(self, xc, coarse):
        """Coarse nodal field -> this level, zero on non-free DOFs."""
        be = self.be
        m = xc.shape[1]
        a = xc.reshape(coarse.nshape + (3, m))
        for axis in range(3):
            a = self._interp_axis(a, axis, self.nshape[axis])
        out = a.reshape(self.ndof, m)
        return be.where(self.free_col, out, 0.0)

    def restrict(self, r, coarse):
        """This level's residual -> coarse nodal field (transpose of prolong)."""
        be = self.be
        m = r.shape[1]
        rf = be.where(self.free_col, r, 0.0)
        a = rf.reshape(self.nshape + (3, m))
        for axis in range(3):
            a = self._restrict_axis(a, axis, coarse.nshape[axis])
        out = a.reshape(coarse.ndof, m)
        return be.where(coarse.free_col, out, 0.0)

    # ---------------------------------------------------------- coarsening

    def coarsen(self, T):
        """-> the next coarser Level (Galerkin P^T K P, built per element)."""
        be = self.be
        pat = _coarsen_pattern(self.shape, self.elements)
        cel, inv, cshape, cedof = (pat["cel"], pat["inv"], pat["cshape"],
                                   pat["cedof"])
        Kc = be.zeros((int(cel.shape[0]), 24, 24))
        mask_e = be.where(self.free[self.edof], 1.0, 0.0)          # (nel, 24)
        for s in range(8):
            sel_np = pat["sels"][s]
            if sel_np.size == 0:
                continue
            sel = be.asarray(sel_np)
            Ks = self.elem_matrices(sel)
            m = mask_e[sel]
            Ks = Ks * m[:, :, None] * m[:, None, :]
            Ts = be.asarray(T[s])
            M = self._sandwich(Ts, Ks)
            # Parents are unique within one sub-position, so a plain fancy
            # index add is exact here (and much faster than np.add.at).
            Kc = _index_add_unique(be, Kc, be.asarray(inv[sel_np]), M)
        cfree = np.ones(3 * _prod(tuple(n + 1 for n in cshape)), bool)
        return Level(be, cshape, cel, cedof, cfree, Ke=Kc, mixed=self.mixed)

    def _sandwich(self, Ts, Ks):
        """T^T Ks T for a stack Ks (n, 24, 24), as two large GEMMs."""
        be = self.be
        n = Ks.shape[0]
        left = Ts.T @ be.moveaxis(Ks, 1, 0).reshape(24, n * 24)
        left = be.moveaxis(left.reshape(24, n, 24), 1, 0)
        return (left.reshape(n * 24, 24) @ Ts).reshape(n, 24, 24)

    def factorise(self):
        """Assemble this level densely and Cholesky-factorise it."""
        be = self.be
        ndof = self.ndof
        Ke = self.elem_matrices()
        rows = self.edof[:, :, None] * ndof + self.edof[:, None, :]
        A = be.scatter_add(rows.reshape(-1), Ke.reshape(-1), ndof * ndof)
        A = A.reshape(ndof, ndof)
        fm = be.where(self.free, 1.0, 0.0)
        A = A * fm[:, None] * fm[None, :]
        A = A + _diag_from(be, be.where(self.free, 0.0, 1.0))
        # A field that has gone to void around a load leaves the coarse
        # operator numerically singular (E_min is 1e-9 of E). The
        # preconditioner does not have to be exact -- CG still solves the
        # fine system to tolerance -- so shift the diagonal by the smallest
        # amount that factorises rather than abort the iteration.
        dmax = float(be.to_numpy(self.diag).max())
        shift = 0.0
        for eps in (0.0, 1e-10, 1e-8, 1e-6, 1e-4, 1e-2):
            try:
                self.coarse = be.cholesky(
                    A if eps == 0.0
                    else A + _diag_from(be, be.where(self.free, eps * dmax, 0.0)))
                shift = eps
                break
            except Exception:                      # numpy or torch LinAlgError
                continue
        else:
            raise np.linalg.LinAlgError(
                "coarse level is singular even with a 1e-2 diagonal shift; "
                "the density field has no stiffness left around a load")
        self.coarse_shift = shift

    def coarse_solve(self, b):
        """The coarse Cholesky stays in float64 whatever the cycle runs in:
        a SIMP coarse matrix spans nine orders of magnitude and a float32
        factorisation of it is not reliably positive."""
        be = self.be
        if self._is32(b):
            return be.cast(be.cho_solve(self.coarse, be.cast(b, be.float64)),
                           be.float32)
        return be.cho_solve(self.coarse, b)


class MGPreconditioner:
    """V-cycle preconditioner over a hierarchy built from the fine operator.

    `apply(r)` returns an approximation of K^-1 r for a block r (ndof, m).
    """

    def __init__(self, be, shape, elements, edof, KE, emul, free,
                 max_coarse_dof=4000, degree=2, lam_ratio=30.0,
                 power_iters=15, max_levels=12, mixed=False):
        t0 = time.time()
        self.be = be
        self.degree = int(degree)
        self.lam_ratio = float(lam_ratio)
        self.mixed = bool(mixed)
        T = transfer_matrices()
        fine = Level(be, shape, elements, edof, free, KE=KE, emul=emul,
                     mixed=self.mixed)
        self.levels = [fine]
        while (self.levels[-1].ndof > max_coarse_dof
               and max(self.levels[-1].shape) > 1
               and len(self.levels) < max_levels):
            self.levels.append(self.levels[-1].coarsen(T))
        for lv in self.levels[:-1]:
            lv.estimate_lam_max(iters=power_iters)
        self.levels[-1].factorise()
        be.synchronize()
        self.setup_seconds = time.time() - t0

    @property
    def fine(self):
        return self.levels[0]

    def info(self):
        return {"levels": [{"shape": list(lv.shape), "elements": lv.nel,
                            "ndof": lv.ndof} for lv in self.levels],
                "smoother": "chebyshev(degree %d, D^-1 K, lambda ratio %g)"
                            % (self.degree, self.lam_ratio),
                "coarse_solver": "dense cholesky",
                "precision": "mixed (float32 cycle, float64 coarse solve "
                             "and outer CG)" if self.mixed else "float64",
                "setup_seconds": round(self.setup_seconds, 3)}

    def _vcycle(self, i, b):
        lv = self.levels[i]
        if i == len(self.levels) - 1:
            return lv.coarse_solve(b)
        x = lv.smooth(b, None, self.degree, self.lam_ratio)
        r = b - lv.matvec(x)
        coarse = self.levels[i + 1]
        rc = lv.restrict(r, coarse)
        xc = self._vcycle(i + 1, rc)
        x = x + lv.prolong(xc, coarse)
        return lv.smooth(b, x, self.degree, self.lam_ratio)

    def apply(self, r):
        """~K^-1 r. In mixed mode the cycle runs in float32 and the result
        is handed back in float64; the outer CG must then be the flexible
        variant (`solve.pcg_block(..., flexible=True)`), because a
        preconditioner rounded to float32 is not exactly the same linear
        operator from one iteration to the next."""
        be = self.be
        if self.mixed:
            x = self._vcycle(0, be.cast(r, be.float32))
            return be.cast(x, be.float64)
        return self._vcycle(0, r)

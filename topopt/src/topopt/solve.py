"""Matrix-free linear-elastic solve on a voxel grid.

Assembling a global sparse matrix costs ~16 bytes per element-matrix entry, i.e.
~9 kB per element -- at 10^5 elements that is a gigabyte before the solver even
starts. Since every voxel shares one element stiffness matrix we never assemble:
the matvec gathers displacements per element, multiplies by KE, and scatters the
result back. Memory is O(nel).

Two things decide how long a solve takes, and both are chosen here rather than
in the physics:

- **the preconditioner.** Jacobi needs thousands of conjugate-gradient
  iterations once a SIMP field has gone 0/1 (4,195 measured at 4 mm on the
  tall_tripod study). The geometric multigrid V-cycle in `multigrid.py` needs
  tens. Both solve the same K u = f to the same tolerance, so the answer is the
  same to that tolerance; only the bill differs. `precond="jacobi"` keeps the
  old path for cross-checking.
- **the array backend.** `backend.py` runs the same gather / multiply /
  scatter on numpy or on a CUDA GPU through torch (optional). A GPU makes one
  CG iteration ~30x cheaper on a 10^5-element grid. Results are float64 on
  both and agree to solver tolerance.

`solve_cases` solves several load cases against one stiffness operator at
once: cases that share their supports share one multigrid hierarchy and are
solved as a block, which is what the optimiser needs every iteration.
`solve_linear_elastic` is the single-case wrapper every caller used before
and still returns the same `Result`.
"""

import os
import time
from dataclasses import dataclass, field

import numpy as np

from . import backend as _backend
from .hex8 import element_operators, element_dofs, node_ids
from .hex8 import von_mises, layer_failure_index
from .hex8 import orthotropic_D, max_stress_failure_index


class SingularStiffness(ValueError):
    """`K u = f` has no unique solution for this case.

    Three ways to get here: nothing is held, everything is held, or a body
    that carries load shares no node with any support and is free to fly away.
    In all three the displacement CG would report is arbitrary, so the run
    stops rather than draw a picture of it.

    A `ValueError` subclass because that is what these guards have raised
    since the first version and several callers already catch it; the name is
    only what lets `cli/optimize.py` stop with one sentence and exit code 1
    instead of a traceback. It is deliberately **not** an `InputError`: a
    study can be perfectly readable and still describe a mechanism, and
    reporting a singular solve as "the input could not be read" is exactly the
    confusion the exit codes exist to avoid (AGENTS.md).
    """


class SolverDidNotConverge(RuntimeError):
    """CG ran out of iterations on every case of the very first solve.

    Everything the optimiser does afterwards -- sensitivities, the volume
    controller, the shape -- is computed from a displacement field that does
    not solve `K u = f`. A run in that state produces a plausible-looking
    result that means nothing, so it is stopped and reported, not continued.
    """


@dataclass
class SolverConfig:
    """How to solve, not what to solve. `spec` strings: "auto", "cpu", "cuda",
    optionally with a preconditioner after a colon: "cpu:jacobi", "cuda:mg"."""
    backend: str = None          # None -> TOPOPT_SOLVER env or "auto"
    precond: str = "mg"          # "mg" | "jacobi"
    mg_mixed: bool = True        # float32 V-cycle inside a float64 CG (GPU only)
    mg_degree: int = 2
    mg_max_coarse_dof: int = 4000
    mg_lam_ratio: float = 30.0
    mg_power_iters: int = 15

    @classmethod
    def from_spec(cls, spec=None, **kw):
        if isinstance(spec, SolverConfig):
            return spec
        if spec is None:
            spec = os.environ.get("TOPOPT_SOLVER") or "auto"
        spec = str(spec).strip().lower()
        be, _, pc = spec.partition(":")
        cfg = cls(backend=be or None, **kw)
        if pc:
            # "mg" is multigrid with the float32 cycle on a GPU (the default
            # since 2026-09-08; same answer to solver tolerance, half the
            # time). "mg64" keeps the whole cycle in float64, "mg32" says
            # the default out loud.
            if pc in ("mg32", "mg64"):
                cfg.mg_mixed = (pc == "mg32")
                pc = "mg"
            if pc not in ("mg", "jacobi"):
                raise ValueError("unknown preconditioner %r (use mg, mg32, "
                                 "mg64 or jacobi)" % pc)
            cfg.precond = pc
        return cfg

    def describe(self, be):
        d = _backend.describe(be)
        d["preconditioner"] = self.precond
        if self.precond == "mg":
            d["precision"] = ("mixed32" if self.mg_mixed and be.name == "cuda"
                              else "float64")
        return d


@dataclass
class Result:
    u: np.ndarray                 # (ndof,) displacement, mm
    elements: np.ndarray          # (nel, 3) voxel indices of active elements
    stress: np.ndarray            # (nel, 6) Voigt stress at centroids, MPa
    von_mises: np.ndarray         # (nel,) MPa
    failure_index: np.ndarray     # (nel,) >= 1 predicts failure
    strain_energy: np.ndarray     # (nel,) per-element energy, N*mm
    compliance: float             # F . u, N*mm (lower = stiffer)
    max_disp: float               # mm
    max_disp_node: np.ndarray     # (3,) mm location of the worst node
    converged: bool
    iterations: int
    warnings: list = field(default_factory=list)
    # Which rule turned stress into `failure_index`. Carried on the result
    # rather than re-derived downstream, so a summary can never label an
    # isotropic number as an orthotropic one.
    failure_criterion: str = "none"
    material_axes: dict = field(default_factory=dict)
    # How it was solved -- for the manifest, so a GPU multigrid number and a
    # CPU Jacobi number can be told apart when they disagree in the sixth digit.
    solver: dict = field(default_factory=dict)
    solve_seconds: float = 0.0

    @property
    def min_safety_factor(self) -> float:
        fi = self.failure_index.max()
        return float("inf") if fi <= 0 else float(1.0 / fi)


def _percentile_hotspot(values, elements, grid, q=99.5):
    """Location of the q-th percentile of `values` -- a stress reading that is
    not dominated by the single stair-stepped voxel at a load point."""
    if values.size == 0:
        return float("nan"), np.zeros(3)
    thr = np.percentile(values, q)
    idx = int(np.argmin(np.abs(values - thr)))
    pos = grid.origin + (elements[idx] + 0.5) * grid.h
    return float(thr), pos


# ------------------------------------------------------------------ PCG

def pcg_block(matvec, precond, b, x0, rtol, maxiter, be, flexible=False):
    """Preconditioned CG on a block of right-hand sides (ndof, m), each column
    independent. A column that has met ||r|| <= rtol ||b|| is frozen (its step
    lengths become zero) so the block finishes when the slowest column does.

    `flexible` uses the Polak-Ribiere form of beta (z_new . (r_new - r_old)
    over z_old . r_old), which tolerates a preconditioner that is not
    exactly the same operator every iteration -- the float32 V-cycle. For a
    fixed preconditioner the two forms agree in exact arithmetic; the plain
    form is kept as the default so the float64 path is unchanged.

    -> (x, iterations per column, converged per column)
    """
    m = b.shape[1]
    bnorm = be.col_norm(b)
    tol = rtol * bnorm
    if x0 is None:
        x = be.zeros_like(b)
        r = be.copy(b)
    else:
        x = be.copy(x0)
        r = b - matvec(x)
    rn = be.col_norm(r)
    done = rn <= tol
    iters_np = np.zeros(m, int)
    done_np = be.to_numpy(done).copy()
    z = precond(r)
    p = be.copy(z)
    rz = be.col_dot(r, z)
    it = 0
    for it in range(1, maxiter + 1):
        if done_np.all():
            it -= 1
            break
        Ap = matvec(p)
        pAp = be.col_dot(p, Ap)
        active = ~done & (pAp > 0)
        alpha = be.where(active, rz / be.where(pAp > 0, pAp, 1.0), 0.0)
        x = x + alpha[None, :] * p
        r_old = r if flexible else None
        r = r - alpha[None, :] * Ap
        rn = be.col_norm(r)
        done = done | (rn <= tol)
        z = precond(r)
        rz_new = be.col_dot(r, z)
        num = rz_new - be.col_dot(r_old, z) if flexible else rz_new
        beta = be.where(done, 0.0, num / be.where(rz != 0, rz, 1.0))
        p = z + beta[None, :] * p
        rz = rz_new
        # One device -> host read per iteration. This is the only point the
        # host waits for the GPU; everything above is queued asynchronously.
        now_np = be.to_numpy(done)
        iters_np[now_np & ~done_np] = it
        done_np = now_np
    iters_np[~done_np] = it
    return x, iters_np, done_np


# ---------------------------------------------------------------- solves

_STRUCT_CACHE = {}
_STRUCT_CACHE_MAX = 16


def mesh_key(shape, elements):
    """A hashable identity for an element list on a grid. -> bytes"""
    import hashlib
    h = hashlib.blake2b(np.ascontiguousarray(elements, np.int64).tobytes(),
                        digest_size=16)
    h.update(np.asarray(shape, np.int64).tobytes())
    return h.digest()


def _mesh_structure(shape, elements, keep):
    """Everything about the mesh that depends only on *which* voxels are in
    it -- DOF table, live DOFs, node-sharing components. -> dict

    The optimiser rebuilds `Assembled` every iteration with a new density
    but (almost always) the same element set, so this is cached by the
    element list's content, and a hit costs one hash of the list.
    """
    key = mesh_key(shape, elements)
    hit = _STRUCT_CACHE.get(key)
    if hit is not None:
        return hit
    from scipy.ndimage import label
    nx, ny, nz = shape
    nnode = (nx + 1) * (ny + 1) * (nz + 1)
    edof = element_dofs(shape, elements)
    lab, _n = label(keep, structure=np.ones((3, 3, 3), bool))
    vals = lab[elements[:, 0], elements[:, 1], elements[:, 2]].astype(np.int32)
    nodes = np.zeros((nx + 1, ny + 1, nz + 1), np.int32)
    touched = np.zeros((nx + 1, ny + 1, nz + 1), bool)
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                idx = (elements[:, 0] + a, elements[:, 1] + b, elements[:, 2] + c)
                nodes[idx] = vals
                touched[idx] = True
    live = np.repeat(touched.reshape(-1), 3)
    assert live.shape == (3 * nnode,)
    out = {"key": key, "edof": edof, "live": live,
           "components": nodes.reshape(-1)}
    if len(_STRUCT_CACHE) >= _STRUCT_CACHE_MAX:
        _STRUCT_CACHE.pop(next(iter(_STRUCT_CACHE)))
    _STRUCT_CACHE[key] = out
    return out


class Assembled:
    """The stiffness operator for one density field, ready to solve against
    any number of right-hand sides.

    Built once per optimisation iteration and reused for the adjoint solves
    a stress constraint needs (`stressconstraint.py`): the multigrid
    hierarchy for a given set of supports is the expensive part of a solve,
    and the adjoint equation K lambda = b has the same K and the same
    supports as the primal K u = f, so it must not be rebuilt for it.
    """

    def __init__(self, grid, E, nu, density=None, penal=1.0, e_min_ratio=1e-9,
                 ortho=None, build_axis=2, raster_axis=None, solver=None,
                 drop_void=True):
        self.cfg = SolverConfig.from_spec(solver)
        self.be = _backend.resolve(self.cfg.backend)
        # Mixed precision is a GPU trade (float32 is 6x cheaper there); on
        # numpy it would only lose digits, so it is silently float64.
        self.mixed = bool(self.cfg.precond == "mg" and self.cfg.mg_mixed
                          and self.be.name == "cuda")
        self.grid = grid
        self.penal = float(penal)
        self.e_min_ratio = float(e_min_ratio)
        self.ortho = ortho
        self.build_axis = build_axis
        self.raster_axis = raster_axis

        solid = grid.solid
        self.shape = shape = solid.shape
        nx, ny, nz = shape
        self.nnode = (nx + 1) * (ny + 1) * (nz + 1)
        self.ndof = 3 * self.nnode

        # Elements whose density is *exactly* zero -- obstacles, and free
        # cells the projection has emptied completely -- carry E * 1e-9 and
        # decide nothing. They were still gathered, multiplied and scattered
        # on every conjugate-gradient iteration: on the child_jaw_gate study
        # 49 % of the domain is obstacle, so half of every matvec was spent
        # on voxels that can never hold material. They are left out of the
        # operator. The answer moves by O(e_min_ratio), i.e. below solver
        # tolerance; the sensitivity at those cells is zero either way
        # (`generative.objective_and_sensitivity` masks x_phys <= 1e-6).
        keep = solid
        if density is not None and drop_void:
            keep = solid & (np.asarray(density) > 0.0)
            if not keep.any():
                keep = solid
        self.keep = keep
        self.elements = elements = np.argwhere(keep)
        if elements.size == 0:
            raise SingularStiffness("no solid voxels: nothing to analyse")
        # `Result.elements` keeps listing every solid voxel, as it always
        # has: a dropped element is reported with zero energy and stress,
        # which is what its E * 1e-9 used to give to within noise. Callers
        # that scatter per-element arrays by `Result.elements` see no change.
        self.all_elements = elements if keep is solid else np.argwhere(solid)
        self.kept_pos = (None if keep is solid
                         else np.flatnonzero(keep[solid]))
        struct = _mesh_structure(shape, elements, keep)
        self.edof = struct["edof"]

        if ortho is not None:
            # D is divided by Ex, so `E_ref` below plays exactly the role `E`
            # plays in the isotropic path and SIMP still scales one scalar
            # per element.
            Dn, E_ref = orthotropic_D(ortho, build_axis, raster_axis)
            self.KE, self.SOPS = element_operators(grid.h, D=Dn)
            E = float(E_ref)
        else:
            self.KE, self.SOPS = element_operators(grid.h, nu)  # E factored out
        self.E = float(E)
        if density is None:
            self.rho = np.ones(elements.shape[0])
            self.emul = np.full(elements.shape[0], self.E)
        else:
            self.rho = np.clip(np.asarray(density)[keep], 0.0, 1.0)
            self.emul = self.E * (e_min_ratio + (1.0 - e_min_ratio)
                                  * self.rho ** self.penal)

        self.live = struct["live"]
        self._components = struct["components"]
        self._ops = {}
        self.solver_info = self.cfg.describe(self.be)

    def _expand(self, per_kept):
        """(n_kept, ...) -> (n_solid, ...) with zeros for dropped elements."""
        out = np.zeros((self.all_elements.shape[0],) + per_kept.shape[1:],
                       per_kept.dtype)
        out[self.kept_pos] = per_kept
        return out

    def demul_drho(self):
        """d(element modulus)/d(density) per element -- what SIMP puts in
        front of KE when the adjoint asks how K moves with the design."""
        return (self.E * (1.0 - self.e_min_ratio) * self.penal
                * np.maximum(self.rho, 0.0) ** (self.penal - 1.0))

    def _operator(self, free):
        key = free.tobytes()
        if key in self._ops:
            return self._ops[key]
        be = self.be
        if self.cfg.precond == "mg":
            from .multigrid import MGPreconditioner
            mg = MGPreconditioner(be, self.shape, self.elements, self.edof,
                                  self.KE, self.emul, free,
                                  max_coarse_dof=self.cfg.mg_max_coarse_dof,
                                  degree=self.cfg.mg_degree,
                                  lam_ratio=self.cfg.mg_lam_ratio,
                                  power_iters=self.cfg.mg_power_iters,
                                  mixed=self.mixed)
            op, precond = mg.fine, mg.apply
            self.solver_info.setdefault("multigrid", mg.info())
        else:
            from .multigrid import Level
            op = Level(be, self.shape, self.elements, self.edof, free,
                       KE=self.KE, emul=self.emul)
            dinv = op.dinv[:, None]
            precond = lambda r, dinv=dinv: dinv * r          # noqa: E731
        self._ops[key] = (op, precond)
        return self._ops[key]

    def node_components(self):
        """Which group of node-sharing voxels each node belongs to (0 = none).

        Two elements that share so much as a corner share a node, so the
        26-connected components of `grid.solid` are exactly the node-disjoint
        pieces of the mesh: a piece with no support in it has all six rigid
        body modes and its block of K is singular.

        Cached on the grid rather than on `self`, because `Assembled` is
        rebuilt every optimiser iteration while `grid.solid` is fixed for the
        whole run -- labelling it per iteration would put a full-domain
        connected-component pass on the hot path for a question whose answer
        cannot change.
        """
        return self._components

    def prepare_case(self, fixed_mask, force):
        """-> (free (ndof,) bool, f (ndof,)) with the validation every caller
        relied on: something must be held, something must be loaded, and the
        load must reach a free DOF.

        Plus one the callers did not have: a loaded body that shares no node
        with any support. That is not a bad study -- the file reads fine and
        every region selects material -- it is a mechanism, and CG solves it
        by wandering off along a rigid body mode. `SingularStiffness` names
        which of the four it is.
        """
        nnode = self.nnode
        fixed_flat = np.asarray(fixed_mask).reshape(nnode, 3).ravel().astype(bool)
        free = self.live & ~fixed_flat
        if not free.any():
            raise SingularStiffness(
                "every DOF is constrained -- check the supports")
        if not fixed_flat.any():
            raise SingularStiffness(
                "no supports defined -- the part would float away")
        f = np.asarray(force).reshape(nnode, 3).ravel().astype(float).copy()
        f[~free] = 0.0
        if not np.any(f):
            raise SingularStiffness(
                "no load reaches a free DOF -- check the load regions")
        comp = self.node_components()
        held = np.unique(comp[fixed_flat.reshape(nnode, 3).any(axis=1)])
        pushed = np.unique(comp[(f.reshape(nnode, 3) != 0).any(axis=1)])
        adrift = [int(c) for c in pushed if c != 0 and c not in set(held.tolist())]
        if adrift:
            loose = np.isin(comp, adrift)
            raise SingularStiffness(
                "load case pushes on %d node(s) of a body that shares no node "
                "with any support (%d node(s) adrift): the stiffness matrix is "
                "singular along its rigid body modes. Connect it, or support it."
                % (int(((f.reshape(nnode, 3) != 0).any(axis=1) & loose).sum()),
                   int(loose.sum())))
        return free, f

    def solve(self, bcs, x0=None, rtol=1e-6, maxiter=20000, want_stress=True,
              sigma_xy=None, sigma_z=None):
        """Solve K u = f for every (fixed_mask, force) pair. -> list of Result.
        Cases with identical supports are solved as one block and share the
        multigrid hierarchy; a hierarchy built here is kept for later calls."""
        be = self.be
        ndof, nnode = self.ndof, self.nnode
        nx, ny, nz = self.shape
        grid, elements, edof, KE, SOPS, emul = (
            self.grid, self.elements, self.edof, self.KE, self.SOPS, self.emul)
        ortho, build_axis, raster_axis = (
            self.ortho, self.build_axis, self.raster_axis)

        ncase = len(bcs)
        if x0 is None:
            x0 = [None] * ncase
        frees, forces = [], []
        for fixed_mask, force in bcs:
            free, f = self.prepare_case(fixed_mask, force)
            frees.append(free)
            forces.append(f)

        groups = {}
        for i, free in enumerate(frees):
            groups.setdefault(free.tobytes(), []).append(i)

        us = [None] * ncase
        iters = np.zeros(ncase, int)
        conv = np.zeros(ncase, bool)
        secs = np.zeros(ncase)
        for idx in groups.values():
            t0 = time.time()
            free = frees[idx[0]]
            op, precond = self._operator(free)
            B = be.asarray(np.stack([forces[i] for i in idx], axis=1))
            X0 = None
            if any(x0[i] is not None for i in idx):
                X0 = be.asarray(np.stack([np.zeros(ndof) if x0[i] is None
                                          else np.asarray(x0[i], float)
                                          for i in idx], axis=1))
            X, its, ok = pcg_block(op.matvec, precond, B, X0, rtol, maxiter, be,
                                   flexible=self.mixed)
            be.synchronize()
            Xn = be.to_numpy(X)
            free_np = be.to_numpy(op.free)
            dt = (time.time() - t0) / len(idx)
            for k, i in enumerate(idx):
                us[i] = np.where(free_np, Xn[:, k], 0.0)
                iters[i] = its[k]
                conv[i] = ok[k]
                secs[i] = dt

        # ---- per-case post-processing (numpy, once per case)
        results = []
        for i in range(ncase):
            u = us[i]
            f = forces[i]
            warnings = []
            if not conv[i]:
                warnings.append(
                    "CG did not reach the tolerance in %d iterations; "
                    "results are indicative only" % iters[i])
            disp = u.reshape(nnode, 3)
            dmag = np.linalg.norm(disp, axis=1)
            imax = int(np.argmax(dmag))
            ijk = np.array(np.unravel_index(imax, (nx + 1, ny + 1, nz + 1)))
            max_disp_node = grid.origin + ijk * grid.h

            ue = u[edof]
            fe = (ue @ KE.T) * emul[:, None]
            strain_energy = 0.5 * np.einsum("ei,ei->e", ue, fe)
            compliance = float(f @ u)

            if want_stress:
                # Stress at 8 corners + centroid; keep the worst sample per
                # element. SOPS is (nsample, 6, 24) for E = 1.
                samples = np.einsum("sij,ej->sei", SOPS, ue) * emul[None, :, None]
                vm_s = np.stack([von_mises(samples[s])
                                 for s in range(samples.shape[0])])
                best = np.argmax(vm_s, axis=0)
                stress = samples[best, np.arange(best.size)]
                vm = vm_s[best, np.arange(best.size)]
                if ortho is not None:
                    fi_s = np.stack([max_stress_failure_index(samples[s], ortho,
                                                              build_axis,
                                                              raster_axis)
                                     for s in range(samples.shape[0])])
                    fi = fi_s.max(axis=0)
                    criterion = "max_stress_material_axes"
                elif sigma_xy and sigma_z:
                    fi_s = np.stack([layer_failure_index(samples[s], sigma_xy,
                                                         sigma_z, build_axis)
                                     for s in range(samples.shape[0])])
                    fi = fi_s.max(axis=0)
                    criterion = "layer_two_constant"
                else:
                    fi = np.zeros(stress.shape[0])
                    criterion = "none"
            else:
                stress = np.zeros((elements.shape[0], 6))
                vm = np.zeros(elements.shape[0])
                fi = np.zeros(elements.shape[0])
                criterion = "none"

            if self.kept_pos is not None:
                strain_energy = self._expand(strain_energy)
                stress = self._expand(stress)
                vm = self._expand(vm)
                fi = self._expand(fi)

            axes = {"build_axis": "XYZ"[int(build_axis)],
                    "raster_axis": (None if raster_axis is None
                                    else "XYZ"[int(raster_axis)]),
                    "stiffness": "orthotropic" if ortho is not None else "isotropic"}
            if ortho is not None:
                axes["card_id"] = ortho.card_id

            results.append(Result(
                u=u, elements=self.all_elements, stress=stress, von_mises=vm,
                failure_index=fi, strain_energy=strain_energy,
                compliance=compliance, max_disp=float(dmag[imax]),
                max_disp_node=max_disp_node, converged=bool(conv[i]),
                iterations=int(iters[i]), warnings=warnings,
                failure_criterion=criterion, material_axes=axes,
                solver=dict(self.solver_info),
                solve_seconds=round(float(secs[i]), 3)))
        return results


def solve_cases(grid, E, nu, bcs, density=None, penal=1.0, e_min_ratio=1e-9,
                x0=None, rtol=1e-6, maxiter=20000, sigma_xy=None, sigma_z=None,
                build_axis=2, want_stress=True, ortho=None, raster_axis=None,
                solver=None, assembled=None):
    """Solve K u = f for every (fixed_mask, force) pair in `bcs` on the solid
    voxels of `grid`, sharing the element operator. -> list of Result.

    fixed_mask  (nx+1, ny+1, nz+1, 3) bool -- True where a nodal DOF is held
    force       (nx+1, ny+1, nz+1, 3) float -- nodal loads in N
    density     (nx, ny, nz) float in [0, 1]; None means fully solid.
                Elements are scaled by density**penal (SIMP).
    x0          list of warm-start displacement vectors (or None entries)
    ortho       an `materials.Orthotropic` card. When given it *replaces*
                `E`, `nu`, `sigma_xy` and `sigma_z`: the element uses the
                card's D rotated into global axes by `build_axis` /
                `raster_axis`, and the failure index becomes max-stress
                against the nine strengths in material axes. For an infill
                knock-down pass a pre-scaled card (`ortho.scaled(...)`) --
                the alternative, a second modulus knob here, is how a run
                ends up applying the derating twice.
    solver      a SolverConfig or spec string (see SolverConfig.from_spec)
    assembled   a dict to receive the `Assembled` operator under "assembled",
                for a caller that has more right-hand sides to solve against
                the same stiffness (the stress constraint's adjoints)

    rtol default is 1e-6, not 1e-8: the extra two digits cost roughly twice
    the iterations and change reported stress and deflection in the fifth
    significant figure, which is far below the uncertainty in the material
    data and the load case.
    """
    asm = Assembled(grid, E, nu, density=density, penal=penal,
                    e_min_ratio=e_min_ratio, ortho=ortho, build_axis=build_axis,
                    raster_axis=raster_axis, solver=solver)
    if assembled is not None:
        assembled["assembled"] = asm
    return asm.solve(bcs, x0=x0, rtol=rtol, maxiter=maxiter,
                     want_stress=want_stress, sigma_xy=sigma_xy, sigma_z=sigma_z)


def solve_linear_elastic(grid, E, nu, fixed_mask, force, density=None,
                         penal=1.0, e_min_ratio=1e-9, x0=None,
                         rtol=1e-6, maxiter=20000, sigma_xy=None, sigma_z=None,
                         build_axis=2, want_stress=True,
                         ortho=None, raster_axis=None, solver=None):
    """Solve K u = f on the solid voxels of `grid` for one load case.
    See `solve_cases` for the arguments; this is the single-case form."""
    return solve_cases(grid, E, nu, [(fixed_mask, force)], density=density,
                       penal=penal, e_min_ratio=e_min_ratio, x0=[x0],
                       rtol=rtol, maxiter=maxiter, sigma_xy=sigma_xy,
                       sigma_z=sigma_z, build_axis=build_axis,
                       want_stress=want_stress, ortho=ortho,
                       raster_axis=raster_axis, solver=solver)[0]

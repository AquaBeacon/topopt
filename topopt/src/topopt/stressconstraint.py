"""A stress constraint the optimiser can see (spec 11.6, known-issue A4).

Why. Until now the only things shaping a part were the volume fraction and
the stiffness. The safety factor was checked afterwards, on the finished
shape, and when it failed the answer was "raise volfrac and run again". The
tall tripod (known-issue G3) is the case in point: strength never entered the
optimisation at all. This module puts the same failure rule the final FEA
uses -- max-stress against the nine strengths in material axes -- inside the
loop, as a constraint per load case: `required safety factor x failure index
<= 1` everywhere. The required factor is the one spec 15.1 asks of that case,
not one number for the run: a case declared `load_class: "abnormal"` (a drop,
a lift with one foot hooked) is held to 1.5 rather than to the study's normal
figure, exactly as `topopt fea` and `topopt ccx` judge the finished shape.

How. Three standard pieces (Le, Norato, Bruns, Ha, Tortorelli 2010; Bruggi
2008; Holmberg, Torstenfelt, Klarbring 2013), none invented here:

1.  **qp relaxation.** The stress in a SIMP element with density rho is
    taken as `rho^q` times the stress of a solid element with the same
    strains, with `q < p` (default 0.5 against penal 3). Without it a grey
    element that is about to vanish still carries full stress, the feasible
    set has "singular" corners the optimiser cannot reach, and the design
    never removes material near a stressed region.
2.  **p-norm aggregation.** A constraint per element is 10^5 constraints;
    MMA cannot take that. The failure indices of every element, sample point
    and failure mode in a load case are folded into one number
    `G = (sum g^P)^(1/P)` with P = 8. That is an upper bound on the maximum
    but a loose one (up to N^(1/P)), so a scale `c = max g / G` measured
    each iteration and damped is applied: `c G <= 1` tracks the true
    maximum without the constraint being non-smooth. This is Le et al.'s
    adaptive normalisation; it is what makes P = 8 usable.
3.  **Adjoint sensitivity.** `dG/drho` has an explicit part (through rho^q)
    and an implicit one (through the displacements). The implicit part is
    one extra linear solve per load case per iteration, with the same K as
    the primal solve and the same supports, so it reuses the primal solve's
    multigrid hierarchy (`solve.Assembled`).

What this constrains is the *relaxed, aggregated* index on the analysis
grid at the iteration's density field, which is not what `topopt fea` will
report on the thresholded solid afterwards. The reported number is the
final FEA's; this constraint is there so that number comes out on the right
side of the line, and the summary says whether it held at the last
iteration. Mesh convergence (known-issue G9) applies to both.

Failure modes are the one-sided max-stress ones of `hex8.max_stress_failure_index`:
tension and compression are separate modes per material axis (Xt / Xc,
Yt / Yc, Zt / Zc) and each shear is measured against its own strength. Every
mode is a *linear* function of the global stress vector clipped at zero, so
the aggregate is smooth almost everywhere and the adjoint load is exact.
Only the orthotropic material model is supported: the legacy two-constant
criterion mixes von Mises into the rule and is frozen for regression use.
"""

import numpy as np

from .hex8 import material_rotation, rotate_stress_to_material

MODE_NAMES = ("raster_tension", "raster_compression",
              "in_plane_tension", "in_plane_compression",
              "interlayer_tension", "interlayer_compression",
              "in_plane_shear+", "in_plane_shear-",
              "interlayer_shear_yz+", "interlayer_shear_yz-",
              "interlayer_shear_zx+", "interlayer_shear_zx-")


def mode_vectors(ortho):
    """(12, 6) rows w such that `max(0, w . s_material)` is one failure-mode
    index. Tension and compression of each normal component, and each sign of
    each shear, are separate rows; for a given stress exactly one of each pair
    is non-zero, so the max over the pair is the one-sided index."""
    eps = 1e-9
    W = np.zeros((12, 6))
    W[0, 0] = 1.0 / max(ortho.Xt, eps)
    W[1, 0] = -1.0 / max(ortho.Xc, eps)
    W[2, 1] = 1.0 / max(ortho.Yt, eps)
    W[3, 1] = -1.0 / max(ortho.Yc, eps)
    W[4, 2] = 1.0 / max(ortho.Zt, eps)
    W[5, 2] = -1.0 / max(ortho.Zc, eps)
    W[6, 3] = 1.0 / max(ortho.Sxy, eps)
    W[7, 3] = -1.0 / max(ortho.Sxy, eps)
    W[8, 4] = 1.0 / max(ortho.Syz, eps)
    W[9, 4] = -1.0 / max(ortho.Syz, eps)
    W[10, 5] = 1.0 / max(ortho.Szx, eps)
    W[11, 5] = -1.0 / max(ortho.Szx, eps)
    return W


class StressConstraint:
    """One aggregated stress constraint per load case.

    safety_factor   the required factor; the constraint is
                    `safety_factor * failure index <= 1`
    p_norm          aggregation exponent P (8)
    relax_q         qp-relaxation exponent (0.5); must be below `penal`
    samples         "all" (8 corners + centroid, what `fea` reports) or
                    "centroid"
    scale_damping   alpha in `c <- alpha c_prev + (1 - alpha) max/G` (0.5)
    case_safety_factors
                    one required factor per load case, in `study.cases`
                    order (spec 15.1: a normal case is judged at the
                    configured factor, an abnormal one at 1.5 -- or at the
                    configured factor when that is already lower). `None`
                    means every case is held to `safety_factor`, which is
                    what a study with no `load_class` produces, so such a
                    run is bit-identical to the one before this existed.
    """

    def __init__(self, ortho, build_axis, raster_axis, safety_factor,
                 p_norm=8.0, relax_q=0.5, samples="all", scale_damping=0.5,
                 case_safety_factors=None):
        if ortho is None:
            raise ValueError(
                "the stress constraint needs the orthotropic material model; "
                "it is not available with --isotropic (the legacy criterion "
                "mixes von Mises into the rule and is frozen for regression)")
        self.ortho = ortho
        self.build_axis = int(build_axis)
        self.raster_axis = raster_axis
        self.sf = float(safety_factor)
        self.P = float(p_norm)
        self.q = float(relax_q)
        self.samples = str(samples)
        self.damping = float(scale_damping)
        self.case_sf = (None if case_safety_factors is None
                        else [float(v) for v in case_safety_factors])
        if self.samples not in ("all", "centroid"):
            raise ValueError("stress.samples must be 'all' or 'centroid'")
        R = material_rotation(self.build_axis, raster_axis)
        # s_mat = sigma_glob @ M  (rows of M are the images of the basis)
        M = rotate_stress_to_material(np.eye(6), R)
        # w . s_mat = (M w) . sigma_glob
        self.V = mode_vectors(ortho) @ M.T          # (12, 6) in global axes
        self.scale = None                           # per case, adaptive
        self._L = None

    # ------------------------------------------------------------ set-up

    def _operators(self, asm):
        """(nsamp, 12, 24): element displacements -> mode indices of the
        *solid* element (modulus E_ref), one row per sample point and mode."""
        if self._L is None:
            SOPS = asm.SOPS if self.samples == "all" else asm.SOPS[-1:]
            # SOPS[s] is (6, 24) for unit modulus; the solid stress is E times it.
            self._L = asm.E * np.einsum("kj,sjd->skd", self.V, SOPS)
        return self._L

    def sf_for(self, i):
        """The required factor for load case `i` (spec 15.1).

        A plain Python float, kept scalar on purpose: it multiplies the
        per-element array inside `evaluate`, and turning it into an array
        would change the broadcast order and with it the last bits of a
        run that asks for the same number on every case.
        """
        if self.case_sf is None or i >= len(self.case_sf):
            return self.sf
        return self.case_sf[i]

    def describe(self):
        return {"criterion": "max_stress_material_axes",
                "safety_factor": self.sf,
                "case_safety_factors": (None if self.case_sf is None
                                        else list(self.case_sf)),
                "p_norm": self.P,
                "relax_q": self.q, "samples": self.samples,
                "scale_damping": self.damping,
                "aggregation": "p-norm with adaptive scaling (Le et al. 2010)",
                "relaxation": "qp (stress x rho^q, Bruggi 2008)",
                "modes": list(MODE_NAMES)}

    # ------------------------------------------------------------ evaluate

    def evaluate(self, asm, results, bcs, scale=None, grad=True):
        """-> list over cases of dicts:
            value      c G - 1   (feasible when <= 0)
            grad       (nx, ny, nz) d(c G)/d(rho) on the grid
            max_ratio  max over elements of this case's required safety
                       factor x failure index (relaxed); <= 1 means the
                       requirement is met
            pnorm      G before scaling
            scale      c used
        The adjoint solves go through `asm` so the hierarchy is shared with
        the primal solve. `scale` freezes c (for finite-difference checks);
        by default it is updated from the previous call with damping.
        `grad=False` skips the adjoint solves and returns `grad` as None --
        for measuring the final field, where nothing is updated afterwards.
        """
        L = self._operators(asm)                    # (nsamp, 12, 24)
        rho = asm.rho                               # (nel,)
        elements, edof = asm.elements, asm.edof
        P, q = self.P, self.q
        demul = asm.demul_drho()                    # (nel,)
        KE = asm.KE
        nel = rho.shape[0]
        rho_q = np.where(rho > 0, np.maximum(rho, 1e-300) ** q, 0.0)
        out = []
        if self.scale is None:
            self.scale = [None] * len(results)
        adjoint_bcs = []
        partial = []
        for i, r in enumerate(results):
            # Spec 15.1: the bar is per load case, not per run -- an
            # abnormal case (a drop, a lift with one foot caught) is held
            # to 1.5 while the normal ones are held to the study's number.
            sf = self.sf_for(i)
            ue = r.u[edof]                          # (nel, 24)
            raw = np.einsum("skd,ed->esk", L, ue)   # (nel, nsamp, 12)
            g = sf * rho_q[:, None, None] * np.maximum(raw, 0.0)
            gmax = float(g.max()) if g.size else 0.0
            if gmax <= 0.0:
                out.append({"value": -1.0, "grad": np.zeros(asm.shape),
                            "max_ratio": 0.0, "pnorm": 0.0, "scale": 1.0,
                            "worst_mode": None, "worst_element_mm": None})
                partial.append(None)
                adjoint_bcs.append(None)
                continue
            rnorm = g / gmax
            S = float((rnorm ** P).sum())
            G = gmax * S ** (1.0 / P)
            if scale is not None:
                c = float(scale[i] if hasattr(scale, "__len__") else scale)
            elif self.scale[i] is None:
                c = gmax / G
            else:
                c = self.damping * self.scale[i] + (1.0 - self.damping) * gmax / G
            self.scale[i] = c
            # dG/dg = c (g / G)^(P-1), written on the normalised values.
            dGdg = c * rnorm ** (P - 1.0) * S ** ((1.0 - P) / P)
            # explicit part: d(rho^q)/d rho = q rho^(q-1)
            with np.errstate(divide="ignore", invalid="ignore"):
                explicit = np.where(rho > 0, q * (dGdg * g).sum(axis=(1, 2))
                                    / np.maximum(rho, 1e-300), 0.0)
            # adjoint load: dG/du = sum dGdg * sf * rho^q * H(raw) * L
            coef = dGdg * sf * rho_q[:, None, None] * (raw > 0.0)
            be_ = np.einsum("esk,skd->ed", coef, L)      # (nel, 24)
            b = np.zeros(asm.ndof)
            np.add.at(b, edof.ravel(), be_.ravel())
            fixed_mask = bcs[i][0]
            try:
                if not grad:
                    raise ValueError("no gradient requested")
                asm.prepare_case(fixed_mask, b)
                adjoint_bcs.append((fixed_mask, b.reshape(asm.nnode, 3)))
            except ValueError:
                # No gradient wanted, or every stressed node is held (the
                # displacements do not move with the design there, so the
                # implicit part is zero).
                adjoint_bcs.append(None)
            flat = int(np.argmax(g.reshape(nel, -1).max(axis=1)))
            sk = np.unravel_index(int(np.argmax(g[flat])), g.shape[1:])
            partial.append({"explicit": explicit, "ue": ue, "c": c, "G": G,
                            "gmax": gmax,
                            "worst_mode": MODE_NAMES[int(sk[1])],
                            "worst_element_mm": (asm.grid.origin
                                                 + (elements[flat] + 0.5)
                                                 * asm.grid.h).round(2).tolist()})
            out.append(None)

        # Adjoint solves, all cases that need one at once (shared hierarchy).
        for i, pr in enumerate(partial):
            if pr is not None and adjoint_bcs[i] is None:
                g_ = None
                if grad:
                    g_ = np.zeros(asm.shape)
                    g_[elements[:, 0], elements[:, 1], elements[:, 2]] = pr["explicit"]
                out[i] = {"value": pr["c"] * pr["G"] - 1.0, "grad": g_,
                          "max_ratio": pr["gmax"], "pnorm": pr["G"],
                          "scale": pr["c"], "worst_mode": pr["worst_mode"],
                          "worst_element_mm": pr["worst_element_mm"],
                          "adjoint_converged": True, "adjoint_iterations": 0}
        todo = [i for i, a in enumerate(adjoint_bcs) if a is not None]
        if todo:
            lam = asm.solve([adjoint_bcs[i] for i in todo], want_stress=False)
            for k, i in enumerate(todo):
                pr = partial[i]
                lam_e = lam[k].u[edof]                      # (nel, 24)
                # implicit: -lambda_e . (dK_e/drho) u_e
                implicit = -demul * np.einsum("ei,ij,ej->e", lam_e, KE, pr["ue"])
                grad = np.zeros(asm.shape)
                grad[elements[:, 0], elements[:, 1], elements[:, 2]] = (
                    pr["explicit"] + implicit)
                out[i] = {"value": pr["c"] * pr["G"] - 1.0, "grad": grad,
                          "max_ratio": pr["gmax"], "pnorm": pr["G"],
                          "scale": pr["c"], "worst_mode": pr["worst_mode"],
                          "worst_element_mm": pr["worst_element_mm"],
                          "adjoint_converged": bool(lam[k].converged),
                          "adjoint_iterations": int(lam[k].iterations)}
        return out

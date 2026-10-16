"""SIMP topology optimisation over a preserve / obstacle / design-space setup.

This is the solver half of the generative-design workflow; `designspace.py` is
the setup half. What it adds over the plain SIMP loop:

- **normalised multi-load compliance.** Summing raw compliances lets whichever
  load case happens to be numerically largest own the whole design. Each case is
  divided by its own first-iteration compliance, so "stiff enough in every case"
  is what actually gets optimised. The same weight multiplies that case's
  sensitivity -- objective and gradient have to agree or the optimiser walks
  downhill on a function it is not measuring.
- **Heaviside projection with beta continuation.** The density filter that buys
  mesh independence also smears the boundary into a grey band that no printer
  can make. Projection squeezes that band back to a step while keeping the
  filter's minimum length scale; ramping beta keeps the problem differentiable
  long enough to find the topology first.
- **penalisation continuation.** Starting at penal 1 (a convex-ish problem)
  and ramping to 3 avoids the local minimum that a cold start at 3 falls into.
- **preserve is re-imposed last.** Filtering, projection and symmetry all pull
  material *down*. Applying them after the preserve value -- which is what the
  older loop did -- quietly eats the mounting faces. Order here is
  filter -> project -> symmetry -> impose, every time, without exception.

- **self-support along the build direction.** `amfilter.py` (Langelaar's AM
  filter) sits between the density filter and the projection, so a cell can
  only hold material if the 45 degree cone under it does. It is differentiable
  and its adjoint is exact, so it is a constraint the optimiser designs
  around rather than a clean-up that breaks the load path afterwards. On by
  default (`design.overhang`); the plate is the low end of the build axis.
- **a stress constraint, per load case.** `stressconstraint.py` puts the
  final FEA's max-stress rule inside the loop as `required SF x failure
  index <= 1`, aggregated with a p-norm and relaxed with rho^q, with an
  adjoint sensitivity. `required SF` is spec 15.1's, case by case: a case
  declared `load_class: "abnormal"` is held to 1.5, the rest to the study's
  own number. That is a second constraint, so the update switches from
  optimality criteria to MMA (`mma.py`). Off by default (`design.stress`);
  every recorded run is unchanged.
"""

import time

import numpy as np
from scipy.ndimage import convolve

from .amfilter import (AMFilter, parse_overhang, plate_index, prune_unsupported,
                       unsupported_voxels)


def self_support_params(setup, study, spec):
    """Resolve an `overhang` spec (from `parse_overhang` or a recorded
    `settings.overhang`) against a grid: axis and direction as integers, the
    plate layer, the allowed support height in layers, and the mask of the
    regions where support is allowed. -> dict or None"""
    if not spec:
        return None
    axis = spec["axis"]
    if isinstance(axis, str):
        axis = "XYZ".index(axis.upper())
    up = spec.get("up", 1)
    if isinstance(up, str):
        up = -1 if up.startswith("-") else 1
    h = float(setup.h)
    max_mm = float(spec.get("max_support_mm", 0.0) or 0.0)
    reach = int(np.floor(max_mm / h + 1e-9))
    names = list(spec.get("allow_support") or [])
    free = None
    if names:
        from .loadcase import region_mask
        free = np.zeros(setup.grid.shape, bool)
        for name in names:
            free |= region_mask(setup.grid, name, study.regions, clip=False)
        free &= setup.grid.solid
    return {"axis": int(axis), "up": int(up),
            "base": plate_index(setup.grid.solid, axis, up),
            "reach": reach, "max_support_mm": max_mm,
            "allow_support": names, "free": free,
            "allow_voxels": int(free.sum()) if free is not None else 0}
from .designspace import connectivity_report, min_member_size
from .loadcase import build_bcs
from .materials import get_material, material_model
from .mma import MMA
from .solve import solve_cases, SolverDidNotConverge
from .stressconstraint import StressConstraint

_AXIS = {"X": 0, "Y": 1, "Z": 2}


# ------------------------------------------------------------- ingredients

def density_filter_kernel(rmin_vox):
    """Cone-shaped weights: the classic mesh-independence filter."""
    r = int(np.ceil(rmin_vox))
    ax = np.arange(-r, r + 1)
    X, Y, Z = np.meshgrid(ax, ax, ax, indexing="ij")
    d = np.sqrt(X ** 2 + Y ** 2 + Z ** 2)
    k = np.maximum(0.0, rmin_vox - d)
    s = k.sum()
    return k / s if s > 0 else k


class DensityFilter:
    """The cone filter as an exact adjoint pair.

    Zero-padded convolution renormalised by the in-box weight sum -- the
    classic Sigmund filter. Its transpose is the same convolution applied to
    g / N, exactly. The previous nearest-padded form was not self-adjoint at
    the box faces, and on a 4-voxel-thick section (where every cell is near a
    face) that put a ~10 % error into the gradient (measured against central
    differences, 2026-09-05).
    """

    def __init__(self, kernel, shape):
        self.kernel = kernel
        self.norm = convolve(np.ones(shape), kernel, mode="constant", cval=0.0)

    def apply(self, x):
        return convolve(x, self.kernel, mode="constant", cval=0.0) / self.norm

    def transpose(self, g):
        return convolve(g / self.norm, self.kernel, mode="constant", cval=0.0)


def project(xt, beta, eta=0.5):
    """Smoothed Heaviside. beta -> 0 is the identity, beta -> inf is a step."""
    if beta is None or beta <= 1e-9:
        return xt
    den = np.tanh(beta * eta) + np.tanh(beta * (1.0 - eta))
    return (np.tanh(beta * eta) + np.tanh(beta * (xt - eta))) / den


def dproject(xt, beta, eta=0.5):
    """d(project)/d(xt) -- multiply this into the sensitivity *before* the
    filter transpose, not after."""
    if beta is None or beta <= 1e-9:
        return np.ones_like(xt)
    den = np.tanh(beta * eta) + np.tanh(beta * (1.0 - eta))
    return beta * (1.0 - np.tanh(beta * (xt - eta)) ** 2) / den


def apply_symmetry(x, axes, free):
    """Mirror-average, but only between cells that are both free.

    Averaging a free cell with its preserve or obstacle mirror would bleed a
    fixed value into a design variable and make the constraint ambiguous.
    """
    for a in axes:
        i = _AXIS[str(a).upper()]
        xf = np.flip(x, axis=i)
        both = free & np.flip(free, axis=i)
        x = np.where(both, 0.5 * (x + xf), x)
    return x


# Spec 11.3: how many consecutive iterations each numeric criterion must hold.
CONVERGE_STREAK = 5

# How far the volume may sit from `volfrac` and still be called honoured.
# Not a new number: it is the tolerance the convergence test in `optimize`
# has always used (`vol_ok` below), lifted to module scope so the run's
# verdict and the run's convergence test cannot drift apart. Continuation
# moves the projected volume every time beta doubles, so a run that lands
# within 2 % of its target has done what it was asked; one that does not has
# traded volume for something else and must not be shown as a pass
# (measured on the SF 6 stress-constraint experiment: volume 0.419 against a
# target of 0.300, and the run still reported no error).
VOLUME_TOLERANCE = 0.02


def volume_check(volumes, settings):
    """Spec 19.2: did the finished shape honour the volume constraint?

    -> a check row (`name` / `required` / `got` / `pass`), or None when the
    run recorded neither a target nor a measurement to judge it by.

    `volumes` is `measure()`'s dict and `settings` the optimiser's resolved
    settings, so this reads a summary and nothing else -- which is what lets
    a test doctor one number and see the verdict change.

    The fraction compared is `volume_fraction_of_free_cells`, because that is
    what `volfrac` means: preserve is not up for optimisation and counting it
    would fail every study that has any (the same misreading `measure()`
    warns about).
    """
    target = (settings or {}).get("volfrac_of_free_cells")
    got = (volumes or {}).get("volume_fraction_of_free_cells")
    if target is None or got is None:
        return None
    slack = VOLUME_TOLERANCE * max(float(target), 1e-9)
    return {
        "name": "volume constraint",
        "required": "volume fraction of the free cells <= %.4f (+%.0f %% of "
                    "the target)" % (float(target), 100 * VOLUME_TOLERANCE),
        # Name what was measured. This is the *thresholded* shape, not the
        # projected field the optimiser constrained -- the two agree on a
        # settled run and can differ on a short one, and a reader who takes
        # this for the optimiser's own constraint value would draw the wrong
        # conclusion from the difference.
        "got": "%.4f of the free cells, measured on the thresholded shape"
               % float(got),
        # One-sided on purpose: coming in under the target is the optimiser
        # doing better than asked, and failing a run for being light would
        # send someone off to add material for no reason.
        "pass": bool(float(got) <= float(target) + slack),
    }


def objective_and_sensitivity(grid, bcs, x_phys, penal, E, nu, ortho, study,
                              c_ref, u_prev=None, solver=None, free=None,
                              it=0, solver_cfg=None, details=None):
    """-> (objective, per-case compliance, d(objective)/d(x_phys)).

    `details`, when a dict, receives the assembled stiffness operator
    ("assembled") and the per-case solve results ("results"), so a stress
    constraint can run its adjoint solves against the same operator.

    Pulled out of the loop so the finite-difference test can call *this*
    function rather than a copy of the formula. A sensitivity test that
    re-implements the derivative it is checking proves only that the tester
    and the author made the same assumption.

    `c_ref` is mutated in place: the first solve of each case fixes that
    case's normalisation, so the multi-case objective is a sum of ratios and
    a case that happens to be numerically large cannot dominate the design.
    """
    obj = 0.0
    case_c = []
    dc = np.zeros(grid.shape)
    # All cases at once: they share the stiffness operator, and cases with the
    # same supports share one multigrid hierarchy (solve.py).
    rs = solve_cases(grid, E, nu, list(bcs), density=x_phys, penal=penal,
                     x0=None if u_prev is None else list(u_prev),
                     rtol=1e-6, want_stress=False,
                     ortho=ortho, build_axis=study.build_axis,
                     raster_axis=study.raster_axis, solver=solver_cfg,
                     assembled=details)
    if details is not None:
        details["results"] = rs
    for i, r in enumerate(rs):
        if u_prev is not None:
            u_prev[i] = r.u
        if solver is not None:
            solver["total_solves"] += 1
            solver["solve_seconds"] = round(
                solver.get("solve_seconds", 0.0) + r.solve_seconds, 3)
            solver["cg_iterations"] = solver.get("cg_iterations", 0) + r.iterations
            if "backend" not in solver:
                solver.update({k: v for k, v in r.solver.items()})
            if not r.converged:
                solver["non_converged_solves"] += 1
                if len(solver["warnings"]) < 5:
                    solver["warnings"].append(
                        "iter %d, case %d: %s" % (it + 1, i + 1,
                                                  "; ".join(r.warnings)))
        if c_ref[i] is None:
            c_ref[i] = max(float(r.compliance), 1e-12)
        w = 1.0 / c_ref[i]
        obj += w * r.compliance
        case_c.append(float(r.compliance))
        se = np.zeros(grid.shape)
        se[r.elements[:, 0], r.elements[:, 1], r.elements[:, 2]] = r.strain_energy
        # dc/drho = -p * rho^(p-1) * u^T k0 u, written with the element's own
        # strain energy (= 0.5 u^T k_e u, already carrying rho^p) so the same
        # line is correct for the orthotropic D: SIMP still scales one scalar
        # per element, so the chain rule is unchanged by anisotropy.
        with np.errstate(divide="ignore", invalid="ignore"):
            dc += w * np.where(x_phys > 1e-6,
                               -penal * 2.0 * se / np.maximum(x_phys, 1e-6),
                               0.0)
    # Only free cells are design variables; a sensitivity on a preserve or
    # obstacle cell is meaningless and would leak through the filter.
    if free is not None:
        dc[~free] = 0.0
    return obj, case_c, dc


def parse_stress(design, study):
    """Normalise `design.stress` -> kwargs for `StressConstraint`, or None.

    Accepts `true` (defaults, required safety factor from the study) or a
    dict with any of `safety_factor`, `p_norm`, `relax_q`, `samples`,
    `scale_damping`. Absent / false / null means no stress constraint.

    `case_safety_factors` applies spec 15.1 case by case: the resolved
    `safety_factor` is the bar for a normal case, and an abnormal one
    (`load_class: "abnormal"`) is held to 1.5 -- or to the resolved figure
    when that is already lower, so a deliberately relaxed topology search is
    never held to *more* on its abnormal cases than on its normal ones. That
    is `Study.required_safety_factor` with `design.stress.safety_factor`
    standing in for the study's own number when it was overridden; with no
    override the two are the same function. A study whose cases are all
    normal gets one repeated value, i.e. the behaviour before this existed.
    """
    spec = design.get("stress")
    if not spec:
        return None
    if spec is True:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("design.stress must be true/false or an object")
    from .materials import MIN_SAFETY_FACTOR
    base = float(spec.get("safety_factor", study.safety_factor))
    per_case = [float(min(base, MIN_SAFETY_FACTOR["abnormal"]))
                if getattr(c, "load_class", "normal") == "abnormal" else base
                for c in study.cases]
    return {"safety_factor": base,
            "case_safety_factors": per_case,
            "p_norm": float(spec.get("p_norm", 8.0)),
            "relax_q": float(spec.get("relax_q", 0.5)),
            "samples": str(spec.get("samples", "all")),
            "scale_damping": float(spec.get("scale_damping", 0.5))}


def _schedule(spec, start, end, step, increment=None, geometric=False):
    """Normalise a continuation setting into a function of the iteration.

    Accepts a number (constant), or {"start", "end"/"max", "step", "increment"}.
    """
    if isinstance(spec, (int, float)):
        v = float(spec)
        return lambda it: v
    s = dict(spec or {})
    a = float(s.get("start", start))
    b = float(s.get("end", s.get("max", end)))
    st = max(1, int(s.get("step", step)))
    inc = s.get("increment", increment)
    if geometric:
        f = float(inc if inc is not None else 2.0)
        return lambda it: float(min(b, a * f ** (it // st)))
    d = float(inc if inc is not None else 0.5)
    return lambda it: float(min(b, a + d * (it // st)))


# --------------------------------------------------------------- the loop

def optimize(setup, study, design, iters=60, verbose=True, isotropic=False,
             solver=None):
    """`solver`: a SolverConfig / spec string (see solve.SolverConfig); None
    means the process default (TOPOPT_SOLVER, else auto)."""
    solver_cfg_arg = solver
    grid = setup.grid
    if isotropic:
        mat = get_material(study.material)
        ortho = None
        mat_info = material_model(study.material, isotropic=True)[2]
    else:
        # An orthotropic run reads the material axes, so a defaulted build
        # axis is refused rather than assumed (spec 9.2).
        study.require_build_axis("orthotropic optimisation")
        mat, ortho, mat_info = material_model(study.material)
    E = mat.E_effective(across_layers=False)

    volfrac = float(design.get("volfrac", 0.3))
    rmin_mm = float(design.get("filter_mm", max(3.0, 3 * grid.h)))
    rmin = max(1.5, rmin_mm / grid.h)
    kernel = density_filter_kernel(rmin)
    filt = DensityFilter(kernel, grid.shape)
    sym = [str(a).upper() for a in (design.get("symmetry") or [])]
    proj_eta = float(design.get("projection_eta", 0.5))
    move = float(design.get("move", 0.2))
    # The move limit shrinks as the projection sharpens: at beta 16 a 0.2
    # step in the design variable flips a cell outright, and the update then
    # bounces between two designs instead of settling (measured on
    # tall_tripod: the objective swung 2x between consecutive iterations at
    # beta 16). Halving the step for every doubling of beta past 4 keeps the
    # late iterations a descent. `move_taper: false` turns this off.
    move_taper = bool(design.get("move_taper", True))
    tol_change = float(design.get("tol_change", 0.002))
    # Stress constraint (spec 11.6): one aggregated constraint per load case,
    # which needs an optimiser that takes more than one constraint -- MMA.
    # OC stays the default without it: every recorded run was made with OC
    # and the two do not walk the same path.
    stress_spec = parse_stress(design, study)
    optimizer = str(design.get("optimizer")
                    or ("mma" if stress_spec else "oc")).lower()
    from .schema import InputError
    if optimizer not in ("oc", "mma"):
        raise InputError("design.optimizer must be 'oc' or 'mma', not %r"
                         % optimizer)
    if stress_spec and optimizer == "oc":
        raise InputError("design.stress needs design.optimizer 'mma'; the "
                         "optimality-criteria update handles the volume "
                         "constraint only")
    if stress_spec and ortho is None:
        raise InputError(
            "design.stress needs the orthotropic material model: the "
            "constraint is the max-stress rule in material axes, and the "
            "legacy two-constant model has no such rule. Drop --isotropic.")
    stress = (StressConstraint(ortho, study.build_axis, study.raster_axis,
                               **stress_spec) if stress_spec else None)
    oh = parse_overhang(design, study.build_axis)
    # With the self-support filter the physical volume is a strongly
    # non-linear function of the design variable, and the cheap proxy
    # controller below oscillates (measured: +-0.01 around volfrac 0.04 at
    # beta 16). The exact form costs one filter pass per bisection step,
    # which the AM filter makes cheap relative to a solve.
    exact_volume = bool(design.get("volume_exact", oh is not None))
    ssp = self_support_params(setup, study, oh)
    am = (AMFilter(grid.shape, axis=oh["axis"], up=oh["up"], p=oh["p"],
                   base=ssp["base"], reach=ssp["reach"], free=ssp["free"])
          if oh else None)
    am_ramp = max(1, int(round(oh["ramp"] * iters))) if oh else 1

    def am_alpha(it):
        """How much of the self-support rule applies at iteration `it`."""
        if am is None:
            return 0.0
        return float(min(1.0, it / float(am_ramp)))

    # Continuation has to finish inside the run it is given. Tying the default
    # step to `iters` is what stops a 40-iteration job from stopping at beta 4
    # with a third of the domain still grey.
    penal_at = _schedule(design.get("penal"), 1.0, 3.0,
                         max(3, iters // 8), 0.5)
    beta_at = _schedule(design.get("beta"), 1.0, 16.0,
                        max(4, iters // 5), 2.0, geometric=True)

    free = setup.free
    n_free = int(free.sum())
    if n_free == 0:
        raise ValueError("no free cells to optimise")
    target_vol = volfrac * n_free

    x = setup.initial_density(volfrac)
    x = apply_symmetry(x, sym, free)
    x = setup.impose(x)

    # Loads and supports act on preserve only -- clipped here rather than
    # demanded of the study author, because a load region drawn across a bolt
    # hole is a drawing artefact, not an intent to load thin air.
    restrict = setup.preserve if setup.preserve.any() else None
    bcs = [build_bcs(grid, c, study.regions, mat, study.infill, study.walls,
                     restrict=restrict, ground=setup.ground,
                     ground_axis=setup.ground_axis)
           for c in study.cases]
    c_ref = [None] * len(bcs)
    u_prev = [None] * len(bcs)
    mma = None
    if optimizer == "mma":
        mma = MMA(n=n_free, m=1 + (len(bcs) if stress is not None else 0),
                  xmin=0.0, xmax=1.0, move=move)

    # The volume constraint acts on the *projected* field, but the design
    # variable is what the optimiser moves. Rather than re-filtering and
    # re-projecting inside every bisection step (which costs one convolution per
    # step, ~30x per iteration), track the design-space volume that lands on the
    # right physical volume and correct it from the measured error each
    # iteration. `volume_exact: true` switches to the expensive exact form.
    design_target = target_vol
    history = []
    beta_prev = beta_at(0)
    prev_bin = None
    prev_obj = None
    streak = {"density": 0, "objective": 0, "volume": 0, "shape": 0}
    if stress is not None:
        streak["stress"] = 0
    stress_hist = []
    met = {k: False for k in streak}
    converged = False
    # Reaching `iters` is the default outcome, and it is not convergence.
    # Starting from that value rather than assigning it in an `else` branch is
    # deliberate: there is no path through this loop that leaves the reason
    # unset and defaults to something reassuring.
    termination_reason = "max_iterations"
    # Obstacle cells sit at exactly zero density, i.e. E*1e-9, which pushes the
    # condition number to ~1e9 against a Jacobi preconditioner. When CG gives up,
    # every sensitivity after that point is fiction -- so count it and say so
    # rather than letting the run look clean.
    solver = {"non_converged_solves": 0, "total_solves": 0, "warnings": []}

    if verbose:
        print("domain %d voxels (%d free, %d preserve, %d obstacle) at %.3f mm"
              % (grid.solid.sum(), n_free, setup.preserve.sum(),
                 (setup.obstacle & grid.solid).sum(), grid.h))
        print("volfrac %.3f of the free cells, filter %.2f mm, %d load case(s)%s%s"
              % (volfrac, rmin * grid.h, len(bcs),
                 ", symmetry " + "+".join(sym) if sym else "",
                 (", self-supporting along %s%s (45 deg%s%s)"
                  % ("+" if oh["up"] > 0 else "-", "XYZ"[oh["axis"]],
                     (", support columns up to %g mm allowed"
                      % ssp["max_support_mm"]) if ssp["reach"] else "",
                     (", support allowed in %s"
                      % "+".join(ssp["allow_support"]))
                     if ssp["allow_support"] else ""))
                 if oh else ", no overhang constraint"))
        if stress is not None:
            per = [stress.sf_for(i) for i in range(len(bcs))]
            spread = ("" if min(per) == max(per) else
                      " (abnormal case(s) at SF %.2f: %s)"
                      % (min(per),
                         ", ".join(study.cases[i].name.split()[0]
                                   for i in range(len(bcs))
                                   if per[i] < max(per))))
            print("stress constraint: SF %.2f x max-stress index <= 1 in every "
                  "normal case%s (p-norm %g, rho^%g relaxation, %s samples), "
                  "optimiser MMA"
                  % (stress.sf, spread, stress.P, stress.q, stress.samples))
        elif optimizer == "mma":
            print("optimiser MMA (volume constraint only)")
        print("%5s %6s %5s %11s %9s %9s %9s%s"
              % ("iter", "penal", "beta", "objective", "vol", "grey", "dshape",
                 "   SFxFI" if stress is not None else ""))

    t0 = time.time()
    x_phys = None
    oc_state = {}
    for it in range(iters):
        penal = penal_at(it)
        beta = beta_at(it)

        xt = filt.apply(x)
        alpha = am_alpha(it)
        xa = xt if am is None else (1.0 - alpha) * xt + alpha * am.forward(xt)
        xb = project(xa, beta, proj_eta)
        if sym:
            xb = apply_symmetry(xb, sym, free)
        x_phys = setup.impose(xb)

        details = {}
        obj, case_c, dc = objective_and_sensitivity(
            grid, bcs, x_phys, penal, E, mat.nu, ortho, study, c_ref,
            u_prev, solver, free, it, solver_cfg=solver_cfg_arg,
            details=details)
        # Spec 19.2: stop on a solve that did not solve. The test is the first
        # iteration only -- if CG reached tolerance once, the run is left
        # exactly as it was, so no recorded run can change behaviour here.
        # A later iteration that stops converging is still only counted
        # (`solver["warnings"]`, `solver_never_converged`), because by then
        # there is a shape to look at and the warning is the honest report.
        if it == 0 and solver["total_solves"] and \
                solver["non_converged_solves"] == solver["total_solves"]:
            raise SolverDidNotConverge(
                "none of the %d conjugate-gradient solves in the first "
                "iteration reached the tolerance, so every sensitivity after "
                "this point would be fiction. Check the supports (a nearly "
                "free-floating body), or re-run with --solver cpu:mg."
                % solver["total_solves"])
        cons = (stress.evaluate(details["assembled"], details["results"], bcs)
                if stress is not None else [])
        dpr = dproject(xa, beta, proj_eta)
        gJ = dc * dpr
        gV = free.astype(float) * dpr
        if am is not None:
            # Chain rule through the blended layering, exact adjoint
            # (amfilter.py): d(xa)/d(xt) = (1 - alpha) I + alpha AM'.
            gJ = (1.0 - alpha) * gJ + alpha * am.backward(gJ)
            gV = (1.0 - alpha) * gV + alpha * am.backward(gV)
        dJ = filt.transpose(gJ)
        dV = filt.transpose(gV)
        if sym:
            # The design variable is mirror-symmetric by construction, so the
            # gradient in that parametrisation is the mirror-averaged one. With
            # an asymmetric load case, skipping this makes the OC step chase a
            # direction the symmetrisation then throws away.
            dJ = apply_symmetry(dJ, sym, free)
            dV = apply_symmetry(dV, sym, free)

        move_now = move * min(1.0, 4.0 / max(beta, 1e-9)) if move_taper else move
        v_phys = float(x_phys[free].sum())
        grey = float(np.mean(4.0 * x_phys[free] * (1.0 - x_phys[free])))
        # Once beta is high the design variable keeps bouncing off the move
        # limit even though the shape has stopped moving, so "has it converged"
        # is asked of the geometry: what fraction of free cells changed side of
        # the threshold this iteration.
        cur_bin = x_phys[free] >= 0.5
        flip = 1.0 if prev_bin is None else float(np.mean(cur_bin != prev_bin))
        prev_bin = cur_bin

        if mma is not None:
            # Volume as a constraint on the measured projected volume with
            # its chain-ruled gradient; the linearisation error is corrected
            # by the next measurement, the same way the OC proxy is.
            fvals = [v_phys / target_vol - 1.0]
            dfs = [dV[free] / target_vol]
            for con in cons:
                gG = np.where(free, con["grad"], 0.0) * dpr
                if am is not None:
                    gG = (1.0 - alpha) * gG + alpha * am.backward(gG)
                gG = filt.transpose(gG)
                if sym:
                    gG = apply_symmetry(gG, sym, free)
                fvals.append(con["value"])
                dfs.append(gG[free])
            # The objective is rescaled to 1 every step (i.e. MMA sees its
            # logarithm): the normalised compliance starts at the number of
            # cases and can grow by orders of magnitude while the topology
            # forms, and MMA's elastic slack (weight 1000 per unit of
            # violation) is only a constraint when the objective's gradient
            # is of order one.
            f_scale = max(abs(obj), 1e-12)
            xnew = x.copy()
            xnew[free] = mma.step(x[free], obj / f_scale, dJ[free] / f_scale,
                                  np.array(fvals), np.stack(dfs), move=move_now)
        elif exact_volume:
            def vol_of(cand):
                ct = filt.apply(cand)
                if am is not None:
                    ct = (1.0 - alpha) * ct + alpha * am.forward(ct)
                c = project(ct, beta, proj_eta)
                if sym:
                    c = apply_symmetry(c, sym, free)
                return float(np.clip(c, 0, 1)[free].sum())
            xnew = _oc(x, dJ, dV, free, target_vol, move_now, vol_of,
                       state=oc_state)
        else:
            err = target_vol - v_phys
            design_target = float(np.clip(
                design_target + np.clip(err, -0.1 * n_free, 0.1 * n_free),
                0.005 * n_free, 0.995 * n_free))
            xnew = _oc(x, dJ, dV, free, design_target, move_now,
                       lambda cand: float(cand[free].sum()), state=oc_state)

        if sym:
            xnew = apply_symmetry(xnew, sym, free)
        xnew = setup.impose(xnew)
        change = float(np.abs(xnew - x)[free].max()) if n_free else 0.0
        x = xnew

        history.append({"iter": it + 1, "penal": penal, "beta": beta,
                        "overhang_alpha": round(alpha, 4),
                        "oc_evaluations": oc_state.get("evaluations"),
                        "objective": obj,
                        "compliance_per_case_Nmm": [round(c, 4) for c in case_c],
                        "normalised_per_case": [round(c / cr, 4)
                                                for c, cr in zip(case_c, c_ref)],
                        "volume_fraction_free": v_phys / n_free,
                        "greyness": grey, "change": change,
                        "topology_change_frac": round(flip, 5)})
        if stress is not None:
            history[-1]["stress"] = [
                {"case": i + 1, "max_ratio": round(con["max_ratio"], 5),
                 "constraint": round(con["value"], 6),
                 "scale": round(con["scale"], 5),
                 "worst_mode": con["worst_mode"]}
                for i, con in enumerate(cons)]
            stress_hist.append(max(con["max_ratio"] for con in cons))
        if verbose and (it % 5 == 0 or it == iters - 1):
            print("%5d %6.2f %5.1f %11.4f %9.3f %9.4f %9.4f%s"
                  % (it + 1, penal, beta, obj, v_phys / n_free, grey, flip,
                     ("  %7.3f" % stress_hist[-1]) if stress is not None else ""))

        settled = (penal >= penal_at(iters - 1) - 1e-9
                   and beta >= beta_at(iters - 1) - 1e-9
                   and beta == beta_prev)
        vol_ok = abs(v_phys - target_vol) <= VOLUME_TOLERANCE * max(target_vol, 1.0)

        # Spec 11.3: on top of the shape test, three numeric conditions must
        # hold together for five iterations in a row. One iteration that
        # happens to sit still is not convergence -- with continuation running,
        # the design variable regularly pauses for a step just after beta
        # doubles and then moves again.
        obj_rel = (abs(obj - prev_obj) / max(abs(prev_obj), 1e-12)
                   if prev_obj is not None else float("inf"))
        vol_err = abs(v_phys - target_vol) / max(target_vol, 1.0)
        crit = {"density": change < 0.01,
                "objective": obj_rel < 0.001,
                "volume": vol_err < 0.001,
                "shape": flip < tol_change}
        if stress is not None:
            # Feasible to within the p-norm's own resolution; the shipped
            # verdict is re-measured on the final field below.
            crit["stress"] = all(con["value"] <= 1e-3 for con in cons)
        prev_obj = obj
        for k, ok in crit.items():
            streak[k] = streak[k] + 1 if ok else 0
        met = {k: streak[k] >= CONVERGE_STREAK for k in streak}
        history[-1]["criteria"] = {
            "max_density_change": round(change, 6),
            "objective_rel_change": (None if obj_rel == float("inf")
                                     else round(obj_rel, 8)),
            "volume_error_frac": round(vol_err, 6),
            "topology_change_frac": round(flip, 6),
        }
        if stress is not None:
            history[-1]["criteria"]["stress_feasible"] = bool(crit["stress"])

        if all(met.values()) and settled and vol_ok and it > 10:
            converged = True
            termination_reason = "converged"
            if verbose:
                print("converged: density<0.01, objective<0.1%%, volume<0.1%%, "
                      "shape<%.3f%s, all for %d iterations (beta %.0f, penal %.1f)"
                      % (tol_change,
                         ", stress feasible" if stress is not None else "",
                         CONVERGE_STREAK, beta, penal))
            break
        beta_prev = beta

    # Final field, with the same order: filter, project, symmetry, then impose.
    beta = beta_at(max(len(history) - 1, 0))
    xt = filt.apply(x)
    if am is not None:
        xt = am.forward(xt)
    xb = project(xt, beta, proj_eta)
    if sym:
        xb = apply_symmetry(xb, sym, free)
    x_phys = setup.impose(xb)

    stress_out = None
    if stress is not None:
        # The loop's last measurement was taken one update before this field.
        # One more primal solve (no adjoint) puts the verdict on the field
        # that is actually written out.
        penal_final = penal_at(max(len(history) - 1, 0))
        details = {}
        objective_and_sensitivity(grid, bcs, x_phys, penal_final, E, mat.nu,
                                  ortho, study, c_ref, None, solver, free,
                                  len(history), solver_cfg=solver_cfg_arg,
                                  details=details)
        final = stress.evaluate(details["assembled"], details["results"], bcs,
                                scale=[c["scale"] for c in
                                       history[-1]["stress"]] if history
                                else None, grad=False)
        # Spec 15.1: each case carries the bar it was actually held to, so
        # a reader of the summary can see why two cases with the same
        # `max_ratio` are not the same result. `max_ratio` is already
        # required-SF x index, so the relaxed safety factor divides by the
        # *case's* factor, not by the run's normal one.
        per_case = [{"case": i + 1, "name": study.cases[i].name,
                     "load_class": getattr(study.cases[i], "load_class",
                                           "normal"),
                     "required_safety_factor": stress.sf_for(i),
                     "max_ratio": round(con["max_ratio"], 5),
                     "min_safety_factor_relaxed": (
                         round(stress.sf_for(i) / con["max_ratio"], 3)
                         if con["max_ratio"] > 0 else None),
                     "satisfied": bool(con["max_ratio"] <= 1.0 + 1e-3),
                     "worst_mode": con["worst_mode"],
                     "worst_element_mm": con["worst_element_mm"]}
                    for i, con in enumerate(final)]
        stress_out = dict(stress.describe())
        stress_out.update({
            "enabled": True,
            "optimizer": "mma",
            "final": per_case,
            "satisfied": all(c["satisfied"] for c in per_case),
            "note": ("max_ratio is this case's required SF (spec 15.1: "
                     "`required_safety_factor` per case, 1.5 for an abnormal "
                     "one) x relaxed max-stress index on the analysis grid at "
                     "the final density field (rho^q relaxation, %s samples). "
                     "It is not the final FEA's safety factor: that is "
                     "measured on the thresholded solid and reported "
                     "separately." % stress.samples)})
        if verbose:
            worst = max(per_case, key=lambda c: c["max_ratio"])
            print("stress constraint on the final field: %s -- worst case %r "
                  "(%s, required SF %.2f) SF x FI %.3f (%s)"
                  % ("SATISFIED" if stress_out["satisfied"] else "VIOLATED",
                     worst["name"], worst["load_class"],
                     worst["required_safety_factor"], worst["max_ratio"],
                     worst["worst_mode"]))

    if verbose and solver["non_converged_solves"]:
        print("WARN  %d of %d CG solves did not reach tolerance -- the "
              "sensitivities from those iterations are indicative only"
              % (solver["non_converged_solves"], solver["total_solves"]))

    if not converged and solver["non_converged_solves"] == solver["total_solves"] \
            and solver["total_solves"] > 0:
        termination_reason = "solver_never_converged"

    return {
        "x_raw": x,
        "x_phys": x_phys,
        "history": history,
        "solver": solver,
        "seconds": round(time.time() - t0, 1),
        # Spec 11.3. `converged` is true only when every criterion held for
        # CONVERGE_STREAK iterations; running out of iterations reports itself.
        "converged": bool(converged),
        "termination_reason": termination_reason,
        "density_converged": bool(met["density"]),
        "objective_converged": bool(met["objective"]),
        "volume_converged": bool(met["volume"]),
        "shape_converged": bool(met["shape"]),
        "stress_converged": (bool(met["stress"]) if stress is not None
                             else None),
        "convergence_streak_required": CONVERGE_STREAK,
        "material": mat_info,
        "stress_constraint": stress_out,
        "settings": {
            "optimizer": optimizer,
            "stress": (None if stress is None
                       else {k: v for k, v in stress.describe().items()
                             if k != "modes"}),
            "volfrac_of_free_cells": volfrac,
            "filter_mm": round(rmin * grid.h, 3),
            "projection_eta": proj_eta,
            "beta_final": beta,
            "penal_final": penal_at(max(len(history) - 1, 0)),
            "symmetry": sym,
            "overhang": (None if oh is None else
                         {"self_support": True, "rule": "45 deg cone, AM filter",
                          "axis": "XYZ"[oh["axis"]],
                          "up": "+" if oh["up"] > 0 else "-", "p": oh["p"],
                          "ramp_iterations": am_ramp,
                          "max_support_mm": ssp["max_support_mm"],
                          "reach_layers": ssp["reach"],
                          "allow_support": ssp["allow_support"],
                          "allow_support_voxels": ssp["allow_voxels"]}),
            "move_limit": move,
            "move_taper": move_taper,
            "volume_exact": exact_volume,
            "iterations_run": len(history),
            "iterations_requested": iters,
            "build_axis": "XYZ"[study.build_axis],
            "raster_axis": (None if study.raster_axis is None
                            else "XYZ"[study.raster_axis]),
            "load_case_reference_compliance_Nmm": [
                None if c is None else round(c, 6) for c in c_ref],
        },
    }


def _oc(x, dJ, dV, free, target, move, vol_of, eta=0.5, max_steps=40,
        state=None, tol_rel=1e-4):
    """Optimality-criteria step: find the Lagrange multiplier whose update
    hits the volume target, then return that update.

    `vol_of(candidate)` is whatever volume measure the constraint is written
    against, so the same routine serves both the cheap design-variable proxy and
    the exact projected-volume form.

    The multiplier is found by a bracketed secant search (Illinois) on
    log(lambda), warm-started from the previous iteration's value in
    `state`. The plain log-space bisection it replaces started from
    [1e-12, 1e12] every iteration and took ~17 evaluations of `vol_of`;
    with the exact projected volume each evaluation is a density filter, a
    self-support sweep and a projection, and that bisection was a quarter
    of the optimiser's wall time (measured 1.45 s of 6.1 s per iteration on
    a 100k-element study). Same stopping rule (|volume - target| within
    `tol_rel` of the target), typically 4-7 evaluations.
    """
    lo_all, hi_all = 1e-12, 1e12
    # A cell whose density moves neither the objective nor the volume has
    # nothing to say -- the self-support filter makes whole regions like that
    # (material "in the air" is not printed, so neither derivative sees it).
    # The classic update reads 0/0 as 0 and walks such cells down by the move
    # limit every iteration, which drains the field from the top until the
    # part is gone (measured: tall_tripod collapsed to zero volume). They are
    # held where they are instead; when support reaches them from below they
    # get a real gradient and move like any other cell.
    sJ = float(np.abs(dJ[free]).max()) if free.any() else 0.0
    sV = float(np.abs(dV[free]).max()) if free.any() else 0.0
    dead = (np.abs(dJ) <= 1e-9 * sJ) & (np.abs(dV) <= 1e-9 * sV)
    tol = tol_rel * max(target, 1.0)
    evals = [0]

    def at(lam):
        """-> (candidate, volume - target) for multiplier `lam`."""
        with np.errstate(divide="ignore", invalid="ignore"):
            be = np.maximum(0.0, -dJ / (lam * np.maximum(dV, 1e-12)))
        be = np.nan_to_num(be, nan=0.0, posinf=0.0)
        be = np.where(dead, 1.0, be)
        cand = np.clip(np.clip(x * be ** eta, x - move, x + move), 0.0, 1.0)
        xnew = np.where(free, cand, x)
        evals[0] += 1
        return xnew, vol_of(xnew) - target

    def finish(xbest, lam):
        if state is not None:
            state["lmid"] = float(lam)
            state["evaluations"] = int(evals[0])
        return xbest

    prev = (state or {}).get("lmid")
    if prev is not None and lo_all < prev < hi_all:
        l1, l2 = max(prev / 64.0, lo_all), min(prev * 64.0, hi_all)
    else:
        l1, l2 = lo_all, hi_all
    x1, f1 = at(l1)                    # small multiplier -> too much volume
    if abs(f1) <= tol:
        return finish(x1, l1)
    x2, f2 = at(l2)                    # large multiplier -> too little
    if abs(f2) <= tol:
        return finish(x2, l2)
    # Widen the bracket when the warm start missed the root.
    while f1 <= 0.0 and l1 > lo_all and evals[0] < max_steps:
        l2, x2, f2 = l1, x1, f1
        l1 = max(l1 / 4096.0, lo_all)
        x1, f1 = at(l1)
        if abs(f1) <= tol:
            return finish(x1, l1)
    while f2 >= 0.0 and l2 < hi_all and evals[0] < max_steps:
        l1, x1, f1 = l2, x2, f2
        l2 = min(l2 * 4096.0, hi_all)
        x2, f2 = at(l2)
        if abs(f2) <= tol:
            return finish(x2, l2)
    best = (x1, f1, l1) if abs(f1) <= abs(f2) else (x2, f2, l2)
    if not (f1 > 0.0 > f2):
        # No multiplier in range reaches the target (every free cell is
        # already at its move limit); return the closer end, as before.
        return finish(best[0], best[2])

    s1, s2 = np.log(l1), np.log(l2)
    side = 0
    while evals[0] < max_steps:
        s = s2 - f2 * (s2 - s1) / (f2 - f1)
        if not (min(s1, s2) < s < max(s1, s2)):
            s = 0.5 * (s1 + s2)
        lam = float(np.exp(s))
        xs, fs = at(lam)
        if abs(fs) < abs(best[1]):
            best = (xs, fs, lam)
        if abs(fs) <= tol:
            break
        if fs > 0.0:
            s1, f1 = s, fs
            if side == -1:
                f2 *= 0.5                       # Illinois: stop one end sticking
            side = -1
        else:
            s2, f2 = s, fs
            if side == 1:
                f1 *= 0.5
            side = 1
        if s2 - s1 < 1e-9:
            break
    return finish(best[0], best[2])


# ------------------------------------------------------------ final checks

def find_pinches(mask):
    """Pairs of solid cells that touch only along an edge or at a corner.
    -> list of (cell_a, cell_b, [bridging cells]) with cells as index triples.

    A voxel FEA joins two such cells through their shared nodes, so the
    optimiser is free to leave a member hanging on a line of contact -- it
    carries load in the model. Nothing else treats it that way: the STL has a
    non-manifold edge there, a tet mesher refuses it or MeshFix cuts it (on
    tall_tripod it discarded 21 % of the volume, 2026-09-08), and a printer
    lays two beads that touch at a corner. The bridging cells are the ones
    that would make the contact a face.
    """
    m = np.asarray(mask, bool)
    out = []
    nx, ny, nz = m.shape
    pad = np.zeros((nx + 2, ny + 2, nz + 2), bool)
    pad[1:-1, 1:-1, 1:-1] = m

    def at(idx, d):
        return pad[1 + idx[:, 0] + d[0], 1 + idx[:, 1] + d[1],
                   1 + idx[:, 2] + d[2]]

    idx = np.argwhere(m)
    if idx.size == 0:
        return out
    # Edge contacts: A and A + ea + eb solid, A + ea and A + eb empty.
    # Only the positive direction along the first axis is scanned so each
    # pair is found once.
    for ax in range(3):
        a, b = [x for x in range(3) if x != ax]
        for db in (1, -1):
            ea = np.zeros(3, int); ea[a] = 1
            eb = np.zeros(3, int); eb[b] = db
            hit = at(idx, ea + eb) & ~at(idx, ea) & ~at(idx, eb)
            for cell in idx[hit]:
                out.append((tuple(int(v) for v in cell),
                            tuple(int(v) for v in cell + ea + eb),
                            [tuple(int(v) for v in cell + ea),
                             tuple(int(v) for v in cell + eb)]))
    # Corner contacts: A and A + (dx,dy,dz) solid with no shared face or
    # edge path between them (all six cells between are empty).
    for dy in (1, -1):
        for dz in (1, -1):
            d = np.array([1, dy, dz])
            steps = [np.array(v) for v in ((1, 0, 0), (0, dy, 0), (0, 0, dz))]
            between = [steps[0], steps[1], steps[2],
                       steps[0] + steps[1], steps[0] + steps[2],
                       steps[1] + steps[2]]
            hit = at(idx, d)
            for v in between:
                hit &= ~at(idx, v)
            for cell in idx[hit]:
                out.append((tuple(int(v) for v in cell),
                            tuple(int(v) for v in cell + d),
                            [tuple(int(v) for v in cell + st) for st in steps]))
    return out


def heal_pinches(mask, allowed=None, axis=2, max_passes=5, self_support=None):
    """Fill one bridging cell per pinch so every contact is a face.
    -> (mask, healed count, left count).

    The cell chosen is the lowest one along the build `axis` (so the fix is
    the one a printer can build on) that `allowed` permits -- a pinch whose
    bridges are all obstacle is left and counted, because filling an
    obstacle is the one thing worse than a pinch.
    """
    m = np.asarray(mask, bool).copy()
    ok = np.ones_like(m) if allowed is None else np.asarray(allowed, bool)
    healed, left = 0, 0
    for _ in range(max_passes):
        pinches = find_pinches(m)
        if not pinches:
            return m, healed, left
        left = 0
        added = 0
        eligible = ok
        if self_support:
            from .amfilter import supportable_voxels
            s = self_support
            eligible = ok & supportable_voxels(m, s["axis"], s["up"],
                                               s["base"], s["reach"], s["free"])
        for _a, _b, bridges in pinches:
            # Re-check: an earlier fill this pass may already have joined it.
            cands = [c for c in sorted(bridges, key=lambda c: c[axis])
                     if eligible[c]]
            if any(m[c] for c in bridges):
                continue
            if not cands:
                left += 1
                continue
            m[cands[0]] = True
            added += 1
        healed += added
        if added == 0:
            break
    return m, healed, len(find_pinches(m))


def final_geometry(x_phys, setup, level=0.5, self_support=None, study=None):
    """Threshold, then keep only what is attached to preserve.

    Preserve cells are forced true regardless of the threshold -- that is rule 2
    and it is the last place it can be violated.

    `self_support` is the run's recorded `settings.overhang` (axis, up) or
    None. When given, the thresholded shape is also projected onto the
    printable set (`amfilter.prune_unsupported`) -- read from the run, not
    from the study, so a run made without the rule is not re-cut -- and
    the number of voxels that cost is reported as
    `unsupported_pruned_voxels` -- a handful is the smooth/hard mismatch of
    the filter, hundreds mean the run did not respect the rule.
    """
    mask = (x_phys >= level) & setup.grid.solid
    mask |= setup.preserve
    mask &= ~setup.obstacle
    pruned = None
    ssp = self_support_params(setup, study, self_support) if self_support else None
    if ssp:
        mask, pruned = prune_unsupported(mask, ssp["axis"], ssp["up"], ssp["base"],
                                         keep=setup.preserve, reach=ssp["reach"],
                                         free=ssp["free"])
    conn, kept = connectivity_report(mask, setup)
    if pruned is not None:
        conn["unsupported_pruned_voxels"] = int(pruned)
        conn["unsupported_pruned_mm3"] = round(float(pruned) * setup.h ** 3, 1)
    kept = kept | setup.preserve
    kept &= ~setup.obstacle
    if ssp:
        # Connectivity cleanup can remove an island that supported a layer.
        # Both operations only remove cells, so this reaches a fixed subset.
        while True:
            before = kept.copy()
            kept, n = prune_unsupported(kept, ssp["axis"], ssp["up"], ssp["base"],
                                       keep=setup.preserve, reach=ssp["reach"], free=ssp["free"])
            pruned += n
            conn, kept = connectivity_report(kept, setup)
            kept = (kept | setup.preserve) & ~setup.obstacle
            if np.array_equal(before, kept):
                break
        conn["unsupported_pruned_voxels"] = int(pruned)
        conn["unsupported_pruned_mm3"] = round(float(pruned) * setup.h ** 3, 1)
    # A member joined to the rest along an edge is connected in the voxel
    # FEA and nowhere else; make the contact a face before anything reads
    # the shape as a part (see `find_pinches`).
    kept, healed, left = heal_pinches(kept, allowed=setup.grid.solid
                                      & ~setup.obstacle,
                                      axis=(int(study.build_axis) if study
                                            is not None else
                                            getattr(setup, "ground_axis", 2)),
                                      self_support=ssp)
    conn["pinches_healed"] = int(healed)
    conn["pinches_healed_mm3"] = round(float(healed) * setup.h ** 3, 1)
    conn["pinches_left"] = int(left)
    if left:
        conn["pinches_note"] = (
            "%d contact(s) between cells remain an edge or a corner because "
            "no permitted, supported bridge was found; the STL has a non-manifold "
            "edge there and the printed part a line contact" % left)
    return kept, conn


def final_fea(mask, setup, study, verbose=True, isotropic=False, solver=None):
    """Re-run every load case on the finished, fully-solid geometry.

    The compliance printed during optimisation is a SIMP number: it includes
    intermediate-density material nobody can print. This is the number that
    belongs in the part's `.md`.
    """
    from .report import summarise
    from .voxel import Grid

    grid = Grid(solid=mask.copy(), h=setup.h, origin=setup.origin)
    if isotropic:
        mat, ortho = get_material(study.material), None
    else:
        study.require_build_axis("orthotropic analysis")
        mat, ortho, _ = material_model(study.material)
    restrict = setup.preserve if setup.preserve.any() else None
    out = []
    bcs = [build_bcs(grid, case, study.regions, mat,
                     infill=study.infill, walls=study.walls,
                     restrict=restrict, ground=setup.ground,
                     ground_axis=setup.ground_axis) for case in study.cases]
    rs = solve_cases(
        grid, mat.E_effective(across_layers=False), mat.nu, bcs,
        sigma_xy=mat.sigma_xy, sigma_z=mat.sigma_z,
        build_axis=study.build_axis, ortho=ortho,
        raster_axis=study.raster_axis, solver=solver)
    for case, r in zip(study.cases, rs):
        # Each case at its own bar (spec 15.1): a drop or a one-foot lift is
        # abnormal and judged at 1.5, not at the study's normal value.
        need = study.required_safety_factor(case)
        s = summarise(r, grid, mat, need,
                      study.max_deflection_mm, study.build_axis)
        s["case"] = case.name
        s["load_class"] = case.load_class
        s["required_safety_factor"] = need
        s["solver"] = r.solver
        s["solve_seconds"] = r.solve_seconds
        s["cg_iterations"] = r.iterations
        out.append((case, r, s))
        if verbose:
            print("  %-44s SF(p99.5) %6.2f   max %.3f mm   compliance %.3f N*mm"
                  % (case.name[:44], s["safety_factor_p99_5"],
                     s["max_displacement_mm"], s["compliance_Nmm"]))
    return grid, out


def measure(mask, setup, self_support=None, study=None):
    """Volume accounting a person can check the result against.

    `self_support` is the run's recorded `settings.overhang` (or a
    `parse_overhang` dict) and `study` the study it was made with; together
    they decide the hard support rule the `unsupported` figure is measured
    against. Without them the strict rule (45 degree cone, plate at the low
    end of the build axis, no support allowed) is used."""
    h = setup.h
    v = h ** 3
    free_kept = mask & setup.free
    return {
        "final_mm3": round(float(mask.sum()) * v, 1),
        "preserve_mm3": round(float(setup.preserve.sum()) * v, 1),
        "design_space_mm3": round(float(setup.design_space.sum()) * v, 1),
        "domain_mm3": round(float(setup.grid.solid.sum()) * v, 1),
        # The number people misread: volfrac applies to the free cells only, so
        # the fraction of the whole domain is always higher.
        "volume_fraction_of_free_cells": round(
            float(free_kept.sum()) / max(int(setup.free.sum()), 1), 4),
        "volume_fraction_of_domain": round(
            float(mask.sum()) / max(int(setup.grid.solid.sum()), 1), 4),
        "mass_saving_vs_domain_pct": round(
            100.0 * (1.0 - float(mask.sum()) / max(int(setup.grid.solid.sum()), 1)), 1),
        # Two figures, because they answer different questions: the whole part
        # includes preserve (whatever the CAD says), while `filter_mm` only ever
        # governed the cells the optimiser was free to move.
        "min_member": min_member_size(mask, h),
        "min_member_optimised": min_member_size(mask, h, where=setup.free),
        # What the finished shape actually stands on. With `"contact": "ground"`
        # supports nobody drew the feet, so this is the first place their size
        # and number can be read -- and a part that ends up touching the plate
        # nowhere was solved against supports that were not holding it.
        "ground_contact": _ground_contact(mask, setup),
        # The hard version of the self-support rule on the thresholded shape:
        # voxels with nothing in the 45 degree cone under them. Zero is what
        # the AM filter is for; anything else prints on support or not at all.
        "unsupported": _unsupported(mask, setup, self_support, study),
    }


def _unsupported(mask, setup, self_support=None, study=None):
    from scipy.ndimage import binary_dilation
    ssp = self_support_params(setup, study, self_support) if self_support else None
    if ssp is None:
        # Printability is about the *build* direction, which is the ground
        # axis unless the study decoupled them (`ground_axis`).
        axis = (int(study.build_axis) if study is not None
                else setup.ground_axis)
        up = +1
        ssp = {"axis": axis, "up": up,
               "base": plate_index(setup.grid.solid, axis, up),
               "reach": 0, "free": None, "max_support_mm": 0.0,
               "allow_support": []}
    axis, up = ssp["axis"], ssp["up"]
    u_all = unsupported_voxels(mask, axis=axis, up=up, base=ssp["base"],
                               reach=ssp["reach"], free=ssp["free"])
    # A voxel hanging off a preserve body is the underside of that body: it
    # prints (or not) with the CAD interface, which the optimiser never
    # controlled and `finish` re-cuts from CAD. Counted separately so the
    # self-support verdict is about the optimised material.
    on_preserve = binary_dilation(setup.preserve, iterations=1)
    u_pres = u_all & on_preserve
    u = u_all & ~on_preserve
    n = int(u.sum())
    out = {"voxels": n, "area_mm2": round(n * setup.h * setup.h, 1),
           "on_preserve_voxels": int(u_pres.sum()),
           "on_preserve_area_mm2": round(float(u_pres.sum()) * setup.h * setup.h, 1),
           "axis": "XYZ"[axis], "up": "+" if up > 0 else "-",
           "max_support_mm": ssp["max_support_mm"],
           "allow_support": list(ssp["allow_support"]),
           "rule": ("solid voxel with no solid voxel in the 45 degree cone below"
                    + (" within %g mm" % ssp["max_support_mm"]
                       if ssp["reach"] else "")
                    + (", outside the allowed regions"
                       if ssp["allow_support"] else "")
                    + "; voxels touching preserve are listed separately")}
    if n:
        idx = np.argwhere(u)
        lo = setup.origin + idx.min(axis=0) * setup.h
        hi = setup.origin + (idx.max(axis=0) + 1) * setup.h
        out["bbox_mm"] = [[round(float(v), 1) for v in lo],
                          [round(float(v), 1) for v in hi]]
    return out


def _ground_contact(mask, setup):
    on = mask & setup.ground
    h = setup.h
    axis = setup.ground_axis
    foot = on.any(axis=axis)
    n_pads = 0
    if foot.any():
        from scipy.ndimage import label
        n_pads = int(label(foot)[1])
    return {"plane_mm": round(setup.ground_mm, 3),
            "axis": "XYZ"[axis],
            "area_mm2": round(float(foot.sum()) * h * h, 1),
            "pads": n_pads,
            "band_area_mm2": round(
                float(setup.ground.any(axis=axis).sum()) * h * h, 1)}

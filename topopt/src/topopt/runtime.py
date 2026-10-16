"""How long will this run take? -- answered by timing a solve, not by a formula.

A study's voxel size is the one knob that decides whether an optimisation is a
coffee break or an overnight job, and it is chosen *before* anything runs. The
cost of getting that wrong is recorded twice in this repo: once as 3.6 hours
spent on a design space that could not be printed, and once as a three-hour run
killed at iteration 31 because a progress line every five iterations was read
as one per iteration and the finish time came out five times too far away
(lessons-learned #47). Both would have been avoided by a number on screen
before the solver started.

**Why a timed probe rather than a scaling law.** The obvious approach is to fit
`t = a * cells^b` to the runs already recorded. That was tried. It does not
work: the six recorded runs disagree by more than a factor of two on cost per
cell, and not in a way cells explain --

    tall_tripod   17,902 cells   16.5 s / iteration / case
    gd_mount      32,744 cells    9.3 s / iteration / case

Nearly twice the cells for just over half the cost. The optimiser's cost is
dominated by the conjugate-gradient iteration count, which is set by the
conditioning of the stiffness matrix -- slenderness, how much near-void
material is in the domain, how discrete the field has become -- and not by the
element count alone. A fitted exponent would look authoritative and be wrong on
the next study.

So: build the real grid, solve the real first load case once, and report what
that cost. It is one solve, it runs on the machine that will do the job, and
the number it produces is measured rather than asserted.

**What it still cannot know.** The probe is a *cold* solve at uniform density.
During a run, each iteration warm-starts from the last (cheaper) while
continuation drives the field towards 0/1 and makes the system stiffer to solve
(dearer). Those pull opposite ways, so the naive product is not the answer, it
is the middle of a range. Measured on this machine against the two runs whose
wall time is recorded:

    study         probe    predicted   actual    actual / predicted
    tall_tripod   12.2 s    65 min     88 min          1.35
    gd_mount      17.4 s    39 min     21 min          0.54

`SPREAD` is set to cover that. Re-measure it rather than trusting it: both
numbers came from `topopt setup --iters N` against `seconds` in the recorded
summary, which is two commands.

**2026-09-05: the calibration above is for the old Jacobi-CG solver.** The
multigrid preconditioner (`multigrid.py`) and the optional GPU backend cut a
solve from minutes to seconds, and the ratio between the cold uniform-density
probe and the 0/1-density iterations of a real run is different (measured 30
vs 74 CG iterations at 4 mm on tall_tripod). The probe is still the right way
to price a run and the estimate line now names the backend; `SPREAD` needs
re-measuring against runs recorded with the new solver. First data point:

    study                       probe    predicted   actual    actual / predicted
    tall_tripod v4, 4 mm, GPU   1.9 s    15 min      5.7 min         0.38
    (101,808 cells, 6 cases, 80 iterations, 480 solves, 10,272 CG iterations)

**2026-09-08.** The probe is a uniform-density solve (~250 CG iterations on
child_jaw_gate). A study whose structure nearly disconnects during
continuation spends ~1,000 CG iterations per solve in that phase, and the
estimate is then low by 2x or more regardless of the solver:

    child_jaw_gate, 3 mm, GPU   1.9 s    13 min/80   23 min/40 it     3.5
    (99,450 cells of which 48,802 obstacle, 5 cases, 200 solves, 193,469 CG)

Read `cg_iterations` in the summary before blaming the element count.
"""

import time

import numpy as np

# Measured, not assumed -- see the table above and `calibration` below.
SPREAD = (0.5, 1.5)


def _fmt(seconds):
    s = float(seconds)
    if s < 90:
        return "%.0f s" % s
    if s < 5400:
        return "%.0f min" % (s / 60.0)
    return "%.1f h" % (s / 3600.0)


def format_estimate(est):
    """One line for a terminal. -> str"""
    if est.get("error"):
        return "runtime estimate unavailable: %s" % est["error"]
    sv = est.get("solver") or {}
    how = ""
    if sv:
        how = " [%s/%s]" % (sv.get("backend", "?"), sv.get("preconditioner", "?"))
    return ("%d iterations x %d case(s) ~ %s  (range %s - %s; one cold solve "
            "of case 1 took %.1f s on %d elements%s)"
            % (est["iters"], est["cases"], _fmt(est["seconds"]),
               _fmt(est["seconds_low"]), _fmt(est["seconds_high"]),
               est["probe_seconds"], est["elements"], how))


def estimate(setup, study, iters=80, isotropic=False, volfrac=None):
    """Time one solve on the real grid and scale it. -> dict

    Returns `{"error": ...}` rather than raising: an estimate is a courtesy,
    and a study that cannot be solved will say so through the setup checks,
    which are the place that judgement belongs.
    """
    out = {"iters": int(iters), "cases": len(study.cases),
           "elements": int(setup.grid.solid.sum()),
           "voxel_mm": round(float(setup.h), 4)}
    if not study.cases:
        out["error"] = "the study has no load cases"
        return out
    try:
        from .loadcase import build_bcs
        from .materials import get_material, material_model
        from .solve import solve_linear_elastic

        if isotropic:
            mat, ortho = get_material(study.material), None
        else:
            mat, ortho, _ = material_model(study.material)

        if volfrac is None:
            volfrac = float((study.raw.get("design") or {}).get("volfrac", 0.3))
        x = setup.initial_density(volfrac)

        restrict = setup.preserve if setup.preserve.any() else None
        fixed, force = build_bcs(setup.grid, study.cases[0], study.regions,
                                 mat, study.infill, study.walls,
                                 restrict=restrict, ground=setup.ground,
                                 ground_axis=setup.ground_axis)
        t0 = time.time()
        r = solve_linear_elastic(
            setup.grid, mat.E_effective(across_layers=False), mat.nu,
            fixed, force, density=x, penal=1.0, rtol=1e-6, want_stress=False,
            ortho=ortho, build_axis=study.build_axis,
            raster_axis=study.raster_axis)
        probe = time.time() - t0
    except Exception as exc:                                        # noqa: BLE001
        out["error"] = str(exc)
        return out

    total = probe * len(study.cases) * int(iters)
    out.update({
        "solver": dict(getattr(r, "solver", {}) or {}),
        "probe_cg_iterations": int(getattr(r, "iterations", 0)),
        "probe_seconds": round(probe, 2),
        "probe_converged": bool(getattr(r, "converged", True)),
        "seconds": round(total, 1),
        "seconds_low": round(total * SPREAD[0], 1),
        "seconds_high": round(total * SPREAD[1], 1),
        "human": _fmt(total),
        "basis": ("one cold conjugate-gradient solve of load case 1 at uniform "
                  "density, times cases times iterations. Warm starting makes "
                  "later iterations cheaper; continuation towards a 0/1 field "
                  "makes them dearer. Measured runs land within %gx - %gx."
                  % SPREAD),
    })
    if not out["probe_converged"]:
        out["warning"] = ("the probe solve did not converge, so the timing is "
                          "a floor rather than an estimate -- and the run "
                          "itself will be reporting fiction unless the "
                          "conditioning improves")
    return out


def calibration():
    """The recorded runs this module's `SPREAD` is measured against.

    Kept as data rather than prose so that re-checking it is a matter of
    running the numbers again, not of trusting a comment.
    """
    return [
        # (study, domain cells, cases, iterations, wall seconds)
        ("tall_tripod v3", 17902, 4, 80, 5268.8),
        ("tall_tripod v2", 17902, 4, 40, 4864.7),
        ("tall_tripod v1", 27820, 4, 40, 13012.7),
        ("osmo gd_mount", 32744, 4, 34, 1268.0),
        ("osmo gd_mount v2grid", 37181, 4, 34, 1403.8),
        ("osmo gd", 11260, 4, 42, 409.1),
    ]


def per_iteration_case(rows=None):
    """-> {name: seconds per iteration per load case} for the recorded runs."""
    return {n: sec / (it * nc) for n, _cells, nc, it, sec in (rows or calibration())}


def elements_from_voxel(setup_volume_mm3, voxel_mm):
    """Rough cell count for a candidate voxel size, from a volume already
    measured at another one. Cheap enough to tabulate before building a grid."""
    return int(round(float(setup_volume_mm3) / float(voxel_mm) ** 3))

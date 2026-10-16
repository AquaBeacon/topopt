#!/usr/bin/env python3
"""Topology optimisation (SIMP) for a printed frame.

    topopt opt study.json --out OUTDIR --volfrac 0.3 --iters 60

Two setup schemas, chosen by what the study file contains.

**Generative-design schema (preferred).** A `geometry` block with the four
Fusion Generative Design body types. Full detail in references/design-space.md
and topopt/designspace.py:

  "geometry": {
    "clearance_mm": 1.0,
    "design_space": "envelope.stl",
    "preserve": {"mount": "mount.stl",
                 "foot":  {"type": "cylinder", "axis": "Z",
                           "centre": [0,47,0], "r": 8, "range": [0,3]}},
    "obstacle": {"cable": {"type": "box", "min": [...], "max": [...]},
                 "lens":  {"stl": "fov.stl", "clearance_mm": 2.0}},
    "optional_starting_shape": "current_part.stl"
  }

Preserve is density 1 and is never eroded; obstacles are density 0 and get a
clearance offset; only cells inside the design space are optimisation
variables; loads and supports must land on preserve. The setup is validated and
a 3D preview is written before the first solve -- `topopt setup` runs that step
on its own.

**Legacy schema.** No `geometry` block: the `part` STL is the domain and
`design.keep_in` / `design.keep_out` name regions inside it. Kept working
unchanged, including the overhang filter, so existing studies still reproduce.

What comes out is a *load-path map*, not a finished part. Read the result as
"where does material need to be", then rebuild it as parametric OpenSCAD from
cubes and cylinders (which keeps the STEP clean and editable in Fusion), and
re-run `topopt fea` on the rebuild to confirm you kept the stiffness. The raw
optimised mesh is also written as STL and STEP for reference and for lofting in
Fusion.

Exit codes, `geometry` schema only: 0 = every row of the summary's `checks`
passed (connected, the volume constraint honoured, and the final FEA run and
clear on every load case), 1 = one of them did not, or the solve itself had no
answer to give (`SingularStiffness`, `SolverDidNotConverge`). A 1 here is a
finding, not a crash -- it usually means the volume fraction is below what one
of the load cases needs. It is never 2: exit 2 is reserved for a study that
could not be read, and a mechanism described by a readable study is not that.
The legacy schema always returns 0.
"""

import argparse
import json
import os
import sys
import time

import numpy as np
from scipy.ndimage import convolve


# Long runs are common here; without line buffering a redirected run shows an
# empty log for tens of minutes and looks hung.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from topopt.loadcase import load_case_from_json, build_bcs, region_mask   # noqa: E402
from topopt import backend as _backend                                   # noqa: E402
from topopt.materials import get_material                                 # noqa: E402
from topopt.meshout import (write_blocky_stl, write_smooth_stl,           # noqa: E402
                             write_step_faceted, _write_binary_stl)
from topopt.solve import (solve_linear_elastic, SingularStiffness,      # noqa: E402
                         SolverDidNotConverge)
from topopt.voxel import voxelize_stl, largest_connected, Grid            # noqa: E402


def density_filter_kernel(rmin_vox):
    """Cone-shaped weights used to keep the result mesh-independent."""
    r = int(np.ceil(rmin_vox))
    ax = np.arange(-r, r + 1)
    X, Y, Z = np.meshgrid(ax, ax, ax, indexing="ij")
    d = np.sqrt(X ** 2 + Y ** 2 + Z ** 2)
    k = np.maximum(0.0, rmin_vox - d)
    s = k.sum()
    return k / s if s > 0 else k


def apply_symmetry(x, axes, shape):
    for a in axes:
        idx = {"X": 0, "Y": 1, "Z": 2}[a.upper()]
        x = 0.5 * (x + np.flip(x, axis=idx))
    return x


def overhang_filter(x, build_axis=2, floor=0.3):
    """Bias densities so material tends to sit on material already below it.

    Sweeping upward, cap each layer by the strongest density in the 45-degree
    cone beneath it (the cell below plus its four in-plane neighbours). `floor`
    leaves room for genuinely printable short overhangs and bridges. This is a
    heuristic that steers the optimiser away from islands and unsupported
    spires -- it is not a guarantee, so always confirm with `topopt print`.
    """
    x = np.moveaxis(x, build_axis, 0).copy()
    for k in range(1, x.shape[0]):
        prev = x[k - 1]
        below = np.maximum.reduce([
            prev,
            np.roll(prev, 1, axis=0), np.roll(prev, -1, axis=0),
            np.roll(prev, 1, axis=1), np.roll(prev, -1, axis=1)])
        x[k] = np.minimum(x[k], np.maximum(below, floor))
    return np.moveaxis(x, 0, build_axis)


def oc_update(x, dc, dv, free, volfrac, move=0.2, eta=0.5):
    """Optimality-criteria step with a bisection on the Lagrange multiplier."""
    l1, l2 = 1e-12, 1e12
    target = volfrac * free.sum()
    xnew = x.copy()
    while (l2 - l1) / (l1 + l2) > 1e-6:
        lmid = 0.5 * (l1 + l2)
        # dc is negative (compliance decreases with more material).
        be = np.maximum(0.0, -dc / (lmid * np.maximum(dv, 1e-12)))
        cand = np.clip(np.clip(x * be ** eta, x - move, x + move), 0.0, 1.0)
        xnew = np.where(free, cand, x)
        if xnew[free].sum() > target:
            l1 = lmid
        else:
            l2 = lmid
    return xnew


def rib_report(density, grid, level=0.5, build_axis=2):
    """Turn the density field into numbers a person can rebuild a part from.

    Slice-by-slice along each axis: how much material, and where its mass sits.
    The point is to answer "how many ribs, how thick, running which way" so the
    result can be re-authored as primitives instead of shipped as a mesh.
    """
    mask = density >= level
    out = {"axes": {}}
    for axis, name in enumerate("XYZ"):
        n = mask.shape[axis]
        rows = []
        for i in range(n):
            sl = np.take(mask, i, axis=axis)
            area = float(sl.sum()) * grid.h ** 2
            if sl.any():
                pts = np.argwhere(sl)
                spread = (pts.max(axis=0) - pts.min(axis=0) + 1) * grid.h
                # Count separated members in this slice by run-length along
                # each in-plane direction: this is the "how many ribs" answer.
                members = _count_members(sl)
            else:
                spread = np.zeros(2)
                members = 0
            rows.append({"index": i,
                         "coord_mm": round(float(grid.origin[axis] + (i + 0.5) * grid.h), 2),
                         "area_mm2": round(area, 1),
                         "extent_mm": [round(float(v), 1) for v in spread],
                         "members": members})
        out["axes"][name] = rows
    return out


def _count_members(sl):
    """Connected components of a 2D slice (4-connectivity), i.e. how many
    separate ribs the cut passes through."""
    from scipy.ndimage import label
    return int(label(sl)[1])


def run(study, design, outdir, iters=60, quiet=False):
    mat = get_material(study.material)
    grid = voxelize_stl(study.part_path, h=study.resolution_mm,
                        target_elements=study.target_elements)
    grid = largest_connected(grid)

    volfrac = float(design.get("volfrac", 0.3))
    rmin_mm = float(design.get("filter_mm", max(3.0, 3 * grid.h)))
    rmin = max(1.5, rmin_mm / grid.h)
    penal = float(design.get("penal", 3.0))
    sym = design.get("symmetry", [])
    use_overhang = bool(design.get("overhang", False))

    keep_in = np.zeros(grid.shape, bool)
    keep_out = np.zeros(grid.shape, bool)
    for r in design.get("keep_in", []):
        keep_in |= region_mask(grid, r, study.regions)
    for r in design.get("keep_out", []):
        keep_out |= region_mask(grid, r, study.regions)

    # Load and support footprints must survive: optimising them away produces a
    # structure that is stiff and cannot be attached to anything.
    for case in study.cases:
        for s in case.supports:
            keep_in |= region_mask(grid, s.get("region", s), study.regions)
        for ld in case.loads:
            keep_in |= region_mask(grid, ld.get("region", ld), study.regions)

    free = grid.solid & ~keep_in & ~keep_out
    x = np.zeros(grid.shape)
    x[grid.solid] = volfrac
    x[keep_in] = 1.0
    x[keep_out] = 0.0

    kernel = density_filter_kernel(rmin)
    bcs = [build_bcs(grid, c, study.regions, mat, study.infill, study.walls)
           for c in study.cases]

    E = mat.E_effective(across_layers=False)
    history = []
    u_prev = None
    if not quiet:
        print("domain %d voxels at %.2f mm, volfrac %.2f, filter %.1f mm, "
              "%d load case(s)" % (grid.solid.sum(), grid.h, volfrac,
                                   rmin * grid.h, len(study.cases)))
        print("%5s %12s %9s %9s" % ("iter", "compliance", "vol", "change"))

    for it in range(iters):
        # Order matters: symmetry averages a cell with its mirror and the
        # overhang filter caps a cell by what is under it, so both pull keep_in
        # cells below 1.0 if they run after the assignment. Impose the hard
        # values last, every time.
        xf = convolve(x, kernel, mode="nearest")
        if sym:
            xf = apply_symmetry(xf, sym, grid.shape)
        if use_overhang:
            xf = overhang_filter(xf, study.build_axis)
        xf = np.clip(xf, 0.0, 1.0)
        xf[keep_in] = 1.0
        xf[keep_out] = 0.0

        total_c = 0.0
        dc = np.zeros(grid.shape)
        for (fixed, force) in bcs:
            r = solve_linear_elastic(grid, E, mat.nu, fixed, force,
                                     density=xf, penal=penal, x0=u_prev,
                                     rtol=1e-6, want_stress=False)
            u_prev = r.u
            total_c += r.compliance
            # d(compliance)/d(rho) = -penal * rho^(penal-1) * u_e^T k0 u_e
            se = np.zeros(grid.shape)
            se[r.elements[:, 0], r.elements[:, 1], r.elements[:, 2]] = r.strain_energy
            with np.errstate(divide="ignore", invalid="ignore"):
                dc += np.where(xf > 1e-6,
                               -penal * 2.0 * se / np.maximum(xf, 1e-6), 0.0)

        dc = convolve(dc, kernel, mode="nearest")
        dv = np.ones(grid.shape)
        xnew = oc_update(x, dc, dv, free, volfrac)
        change = float(np.abs(xnew - x).max())
        x = xnew
        history.append({"iter": it + 1, "compliance": total_c,
                        "volume_fraction": float(x[grid.solid].mean()),
                        "change": change})
        if not quiet and (it % 5 == 0 or it == iters - 1):
            print("%5d %12.3f %9.3f %9.4f"
                  % (it + 1, total_c, x[grid.solid].mean(), change))
        if change < 0.01 and it > 10:
            if not quiet:
                print("converged (change < 0.01)")
            break

    xf = convolve(x, kernel, mode="nearest")
    if sym:
        xf = apply_symmetry(xf, sym, grid.shape)
    xf = np.clip(xf, 0, 1)
    xf[keep_in] = 1.0
    xf[keep_out] = 0.0
    xf = xf * grid.solid

    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(study.part_path))[0] + "_topo"
    np.save(os.path.join(outdir, base + "_density.npy"), xf)

    mask = xf >= 0.5
    ntri = write_blocky_stl(os.path.join(outdir, base + "_voxel.stl"), mask,
                            grid.h, grid.origin)
    nsm = write_smooth_stl(os.path.join(outdir, base + "_smooth.stl"), xf,
                           grid.h, grid.origin, envelope=mask)

    ribs = rib_report(xf, grid, build_axis=study.build_axis)
    try:
        from topopt.members import extract_members, scad_sketch
        extracted = extract_members(xf, grid)
        if extracted.get("members"):
            with open(os.path.join(outdir, base + "_members.scad"), "w",
                      encoding="utf-8") as f:
                f.write(scad_sketch(extracted, base))
    except Exception as exc:                              # pragma: no cover
        extracted = {"error": str(exc)}
    summary = {
        "schema": "topopt/opt-legacy/1",
        "part": study.part_path,
        "material": mat.name,
        "voxel_mm": grid.h,
        "domain_voxels": int(grid.solid.sum()),
        "volume_fraction_target": volfrac,
        "volume_fraction_final": float(xf[grid.solid].mean()),
        "kept_volume_mm3": float(mask.sum() * grid.h ** 3),
        "original_volume_mm3": float(grid.solid.sum() * grid.h ** 3),
        "mass_saving_pct": round(100 * (1 - mask.sum() / max(grid.solid.sum(), 1)), 1),
        "filter_mm": rmin * grid.h,
        "symmetry": sym,
        "overhang_filter": use_overhang,
        "history": history,
        "blocky_stl_triangles": ntri,
        "smooth_stl_triangles": nsm,
        "ribs": ribs,
        "extracted_members": extracted,
    }
    with open(os.path.join(outdir, base + ".json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    write_png(os.path.join(outdir, base + ".png"), xf, grid, history)
    return summary, xf, grid


def write_png(path, density, grid, history, metric="compliance",
              metric_label="compliance (N mm)"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))
    planes = [(2, "XY (top)", 0, 1), (1, "XZ (front)", 0, 2), (0, "YZ (side)", 1, 2)]
    for ax, (axis, label, ha, va) in zip(axes, planes):
        proj = density.max(axis=axis)
        extent = [grid.origin[ha], grid.origin[ha] + grid.shape[ha] * grid.h,
                  grid.origin[va], grid.origin[va] + grid.shape[va] * grid.h]
        im = ax.imshow(proj.T, origin="lower", extent=extent, cmap="viridis",
                       vmin=0, vmax=1, aspect="equal")
        ax.set_title("density -- %s" % label, fontsize=9)
        ax.set_xlabel("XYZ"[ha] + " (mm)", fontsize=8)
        ax.set_ylabel("XYZ"[va] + " (mm)", fontsize=8)
        fig.colorbar(im, ax=ax, fraction=0.046)
    ax = axes[3]
    ax.plot([h["iter"] for h in history], [h[metric] for h in history], "-o",
            ms=3)
    ax.set_yscale("log")
    ax.set_xlabel("iteration")
    ax.set_ylabel(metric_label)
    ax.set_title("convergence", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ------------------------------------------------- generative-design schema

def refuse_overwriting_the_study(study_path, outdir, base):
    """The run summary is written as `<study basename>.json` in `outdir`.
    Put the study file inside its own output directory and that summary
    lands on top of it -- silently. The next stage then reads a run summary
    where it expects a study and stops with "material: expected str, got
    dict", two stages and several minutes after the real mistake. (Measured
    2026-09-08 on the Phase 6 variants of tall_tripod.)
    """
    from topopt.schema import InputError
    target = os.path.abspath(os.path.join(outdir, base + ".json"))
    if os.path.abspath(study_path) == target:
        raise InputError(
            "the study %s sits where this run writes its summary (%s); the "
            "run would overwrite its own input. Keep the study outside --out "
            "or give it another name." % (study_path, target))


def run_checks(summary):
    """The run's own verdict, as rows a reader can disagree with.

    One list, not two: before this the exit code was computed in `main` from
    connectivity and the final FEA while the summary carried no verdict at
    all, so a file could read perfectly well next to an exit code of 1 and
    nothing in it said why. Everything that decides the exit code is a row
    here, and `pass` is the conjunction.

    Reads only the summary, so a test can doctor one number and watch the
    verdict move.
    """
    from topopt import generative as gen

    checks = []
    conn = summary.get("connectivity") or {}
    checks.append({
        "name": "connectivity",
        "required": "every preserve region connected through material",
        "got": "%d component(s), %d preserve body(ies)"
               % (conn.get("components", 0), conn.get("preserve_bodies", 0)),
        "pass": bool(conn.get("connected")),
    })
    vol = gen.volume_check(summary.get("volumes"),
                           summary.get("settings"))
    if vol:
        checks.append(vol)
    s = summary.get("solver") or {}
    if s.get("total_solves"):
        bad = int(s.get("non_converged_solves", 0))
        checks.append({
            "name": "solver",
            "required": "every CG solve reaches the tolerance",
            "got": ("%d of %d solves converged" % (s["total_solves"],
                                                   s["total_solves"]))
                   if not bad else
                   "%d of %d CG solves did not reach the tolerance; the "
                   "sensitivities from those iterations are fiction"
                   % (bad, s["total_solves"]),
            # Spec 19.2 asks for exit code 1 on a CG that did not converge.
            # A run whose *first* iteration fails is stopped outright
            # (`SolverDidNotConverge`); one that starts well and degrades is
            # allowed to finish, because there is a shape to look at by then
            # -- but it is not a pass.
            "pass": bad == 0,
        })
    fea = summary.get("final_fea")
    if fea is None:
        # Not run at all (--no-fea, or the geometry was not connected). The
        # connectivity row already carries the second case; abstaining here is
        # right, because "not measured" is not "failed".
        pass
    elif any("error" in s for s in fea):
        # Spec 18: an analysis that fell over is never shown as a verdict.
        # The old `all(... for s in fea if "error" not in s)` skipped exactly
        # the entries that say the analysis did not happen, so a run whose
        # final FEA crashed came out as a pass.
        checks.append({
            "name": "final FEA",
            "required": "the thresholded geometry is re-analysed",
            "got": "; ".join(s["error"] for s in fea if "error" in s)[:200],
            "pass": False,
        })
    else:
        bad = [s for s in fea if not s.get("pass", True)]
        checks.append({
            "name": "final FEA",
            "required": "every load case passes its own checks",
            "got": ("%d case(s) all pass" % len(fea)) if not bad
                   else "%d of %d case(s) fail: %s"
                        % (len(bad), len(fea),
                           ", ".join(str(s.get("case")) for s in bad)),
            "pass": not bad,
        })
    return checks


def write_stopped_summary(outdir, base, study_path, report, paths, reason,
                          message):
    """The summary of a run that stopped before it had a shape.

    Every key `outschema.OPT` calls required is present and empty rather than
    absent, so the file still validates and a reader (or `topopt report`) can
    open it and find `termination_reason` where it looks for it. Filling them
    with plausible-looking zeros would be the other failure -- these are
    nulls and empty lists, which say "there was no shape", not "the shape
    weighed nothing".
    """
    summary = {
        "study": study_path, "schema": "geometry", "material": None,
        "setup": report, "settings": {}, "solver": None,
        "volumes": {}, "connectivity": {}, "seconds": 0,
        "history": [], "final_stl_triangles": 0,
        "final_smooth_stl_triangles": 0, "final_step": None,
        "final_fea": None, "extracted_members": {},
        "outputs": {k: os.path.basename(v) if isinstance(v, str) else v
                    for k, v in (paths or {}).items()},
        "converged": False, "termination_reason": reason,
        "checks": [{"name": "solve", "required": "K u = f is solvable for "
                                                 "every load case",
                    "got": message, "pass": False}],
    }
    summary["pass"] = False
    with open(os.path.join(outdir, base + ".json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    return summary


def run_generative(study, study_path, design, outdir, iters=60, elements=None,
                   voxel=None, quiet=False, force=False, do_fea=True,
                   isotropic=False, quick=False):
    """The `geometry`-block path: validate, preview, optimise, verify, export.

    `quick` keeps everything that decides the verdict (the optimisation, the
    final FEA, the density fields, the blocky STL and the result picture)
    and skips what only makes the result nicer to look at -- smooth STL,
    STEP, the member sketch, the per-case FEA pictures. For trying many
    variants; the full run is for the one you keep.
    """
    from topopt import generative as gen
    from topopt.report import write_png as write_fea_png
    from topopt.setup_preview import export_setup, print_report
    from topopt.cli.setup import prepare

    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(study_path))[0]
    refuse_overwriting_the_study(study_path, outdir, base)
    npy = {}

    setup, report = prepare(study, elements, voxel, verbose=not quiet)
    paths = export_setup(setup, study, outdir, base)
    if not quiet:
        print_report(report)
    if not report["ok"]:
        if not force:
            raise SystemExit(
                "setup is invalid -- fix the errors above, or re-run with "
                "--force to optimise anyway (the result will inherit the "
                "problem). The preview is in %s" % paths.get("png", outdir))
        print("\n!! --force: optimising an invalid setup !!\n")


    if not quiet:
        print()
    try:
        res = gen.optimize(setup, study, design, iters=iters, verbose=not quiet,
                           isotropic=isotropic)
    except (SingularStiffness, SolverDidNotConverge) as exc:
        # Spec 19.2: stop, say why in the file as well as on the terminal, and
        # leave exit code 1 to `main`. The record is written because "the run
        # stopped and here is the reason" is a result -- a directory holding
        # only a preview and a manifest makes the reader guess.
        write_stopped_summary(
            outdir, base, study_path, report, paths,
            "singular_stiffness" if isinstance(exc, SingularStiffness)
            else "solver_did_not_converge", str(exc))
        raise
    x_raw, x_phys = res["x_raw"], res["x_phys"]

    mask, conn = gen.final_geometry(x_phys, setup,
                                    self_support=res["settings"].get("overhang"),
                                    study=study)
    vols = gen.measure(mask, setup, self_support=res["settings"].get("overhang"),
                       study=study)

    # ---- outputs (section 7 of the spec, in one place)
    for tag, arr in (("density_raw", x_raw), ("density_phys", x_phys),
                     ("binary", mask)):
        p = os.path.join(outdir, "%s_%s.npy" % (base, tag))
        np.save(p, arr)
        npy[tag] = p

    out_files = dict(paths)
    out_files.update(npy)
    p = os.path.join(outdir, base + "_final.stl")
    ntri = write_blocky_stl(p, mask, setup.h, setup.origin)
    out_files["final_stl"] = p
    # --quick: the smooth STL and the STEP are for a shape that is being
    # kept, not for one of many being compared.
    nsm = step = None
    if not quick:
        # Preserve is floored at 1 so the smoothing can never cut into it.
        p = os.path.join(outdir, base + "_final_smooth.stl")
        # Held at 1.0: preserve, so smoothing cannot cut into an interface,
        # and the cells standing on the build plane, so it cannot lift the
        # part off it.
        nsm = write_smooth_stl(p, np.where(mask, 1.0, x_phys), setup.h,
                               setup.origin,
                               floor_mask=setup.preserve | (mask & setup.ground),
                               envelope=mask)
        if nsm:
            out_files["final_smooth_stl"] = p
        p = os.path.join(outdir, base + "_final.step")
        step = write_step_faceted(p, mask, setup.h, setup.origin, name=base)
        if step:
            out_files["final_step"] = p

    p = os.path.join(outdir, base + "_topo.png")
    write_result_png(p, x_phys, mask, setup, res["history"])
    out_files["result_png"] = p

    extracted = {"note": "skipped by --quick"}
    if not quick:
        try:
            from topopt.members import extract_members, scad_sketch
            extracted = extract_members(x_phys, setup.grid)
            if extracted.get("members"):
                with open(os.path.join(outdir, base + "_members.scad"), "w",
                          encoding="utf-8") as f:
                    f.write(scad_sketch(extracted, base))
        except Exception as exc:                          # pragma: no cover
            extracted = {"error": str(exc)}

    # ---- final FEA on the finished solid geometry
    fea = None
    if do_fea and conn.get("connected") and mask.any():
        if not quiet:
            print("\nfinal FEA on the thresholded solid geometry:")
        try:
            fgrid, results = gen.final_fea(mask, setup, study,
                                           verbose=not quiet,
                                           isotropic=isotropic)
            fea = [s for _, _, s in results]
            for i, (case, r, s) in enumerate(results):
                if quick:
                    break
                p = os.path.join(outdir, "%s_fea_%d.png" % (base, i + 1))
                write_fea_png(
                    p, fgrid, r, "%s -- %s (SF %.2f, max %.3f mm)"
                    % (base, case.name, s["safety_factor_p99_5"],
                       s["max_displacement_mm"]))
                out_files["fea_png_%d" % (i + 1)] = p
        except Exception as exc:
            fea = [{"error": str(exc)}]
            print("  final FEA failed: %s" % exc)
    elif do_fea and not conn.get("connected"):
        print("\nfinal FEA skipped: the geometry is not connected, so the "
              "stiffness matrix would be singular. Fix connectivity first.")

    summary = {
        "study": study_path,
        "schema": "geometry",
        "material": res.get("material", study.material),
        "setup": report,
        # Spec 11.3 / known-issue B1. AGENTS.md tells a reader to judge a run by
        # `termination_reason` rather than by `converged`, so both have to
        # survive the trip from the optimiser into the file people actually
        # read -- a verdict computed and then dropped is no verdict at all.
        "converged": res.get("converged"),
        "termination_reason": res.get("termination_reason"),
        "density_converged": res.get("density_converged"),
        "objective_converged": res.get("objective_converged"),
        "volume_converged": res.get("volume_converged"),
        "shape_converged": res.get("shape_converged"),
        "stress_converged": res.get("stress_converged"),
        "convergence_streak_required": res.get("convergence_streak_required"),
        "settings": res["settings"],
        # Spec 11.6: whether the stress constraint held on the final field.
        # None when the run had no stress constraint.
        "stress_constraint": res.get("stress_constraint"),
        "solver": res.get("solver"),
        "volumes": vols,
        "connectivity": conn,
        "seconds": res["seconds"],
        "history": res["history"],
        "final_stl_triangles": ntri,
        "final_smooth_stl_triangles": nsm,
        "final_step": step,
        "final_fea": fea,
        "quick": bool(quick),
        "extracted_members": extracted,
        "outputs": {k: os.path.basename(v) if isinstance(v, str) else v
                    for k, v in out_files.items()},
    }
    summary["checks"] = run_checks(summary)
    summary["pass"] = all(c["pass"] for c in summary["checks"])
    with open(os.path.join(outdir, base + ".json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    return summary


def _print_generative_summary(summary, outdir, study_path):
    v = summary["volumes"]
    c = summary["connectivity"]
    print("\n--- result ---")
    print("  final volume        %.0f mm3  (preserve %.0f mm3)"
          % (v["final_mm3"], v["preserve_mm3"]))
    print("  volume fraction     %.3f of the free cells / %.3f of the domain"
          % (v["volume_fraction_of_free_cells"], v["volume_fraction_of_domain"]))
    u = v.get("unsupported") or {}
    if u:
        oh = (summary.get("settings") or {}).get("overhang")
        print("  self-support        %d voxel(s) / %.0f mm2 with nothing in the 45 "
              "deg cone below (build axis %s%s; constraint %s)"
              % (u.get("voxels", 0), u.get("area_mm2", 0.0), u.get("up", "+"),
                 u.get("axis", "?"), "on" if oh else "OFF"))
    mo = v.get("min_member_optimised") or {}
    if mo:
        print("  optimised members   min %.2f mm, p5 %.2f mm, median %.2f mm "
              "(asked for >= %.2f mm)"
              % (mo["min_mm"], mo["p5_mm"], mo["median_mm"],
                 summary["settings"]["filter_mm"]))
    mm = v.get("min_member") or {}
    if mm:
        print("  whole part          min %.2f mm, median %.2f mm "
              "(includes preserve, which filter_mm does not govern)"
              % (mm["min_mm"], mm["median_mm"]))
    print("  connectivity        %s -- %d component(s), %d preserve body(ies), "
          "%.1f mm3 of islands dropped"
          % ("OK" if c.get("connected") else "BROKEN", c.get("components", 0),
             c.get("preserve_bodies", 0), c.get("island_mm3", 0)))
    for v_ in c.get("violations", []):
        print("    ! %s" % v_)
    st = summary.get("final_step")
    if st:
        print("  STEP                %d planar faces (from %d voxel faces), "
              "%d open / %d non-manifold edge(s)"
              % (st["faces"], st["voxel_faces_before_merge"],
                 st["open_edges"], st["non_manifold_edges"]))
        if st["non_manifold_edges"]:
            print("                      (voxels touching only along an edge -- "
                  "four faces meet there; kernels sew it, some flag it)")
    sc = summary.get("stress_constraint")
    if sc:
        worst = max(sc["final"], key=lambda c: c["max_ratio"])
        # Spec 15.1: quote the worst case's own required factor. Printing the
        # run's normal figure beside an abnormal case's number would say the
        # constraint asked for something it did not.
        print("  stress constraint   %s -- SF %.2f (%s) x max-stress index "
              "%.3f in %r (%s); on the analysis grid, not the final FEA"
              % ("SATISFIED" if sc["satisfied"] else "VIOLATED",
                 worst.get("required_safety_factor", sc["safety_factor"]),
                 worst.get("load_class", "normal"),
                 worst["max_ratio"], worst["name"], worst["worst_mode"]))
    s = summary.get("solver") or {}
    if s.get("non_converged_solves"):
        print("  solver              %d of %d CG solves did not converge -- "
              "treat the optimisation history as indicative"
              % (s["non_converged_solves"], s["total_solves"]))
    fea = summary.get("final_fea")
    if fea and "error" not in fea[0]:
        worst = min(fea, key=lambda s: s["safety_factor_p99_5"])
        print("  worst case          %r  SF(p99.5) %.2f, max %.3f mm"
              % (worst["case"], worst["safety_factor_p99_5"],
                 worst["max_displacement_mm"]))
    ex = summary.get("extracted_members") or {}
    if ex.get("members"):
        print("\nextracted %d members in the %s plane, extruded %.1f mm along %s:"
              % (len(ex["members"]), ex["members"][0]["plane"],
                 ex.get("extrusion_thickness_mm", 0), ex.get("extrusion_axis")))
        print("  %-24s %-24s %8s %8s" % ("start (mm)", "end (mm)", "length", "width"))
        for m in ex["members"]:
            print("  %-24s %-24s %8.1f %8.2f"
                  % (m["start_mm"], m["end_mm"], m["length_mm"], m["width_mm"]))
    print("\nwrote %s (%s s)" % (outdir, summary["seconds"]))
    print("\nNEXT: this is a load-path map, not a part. Rebuild it as "
          "parametric OpenSCAD from cubes/cylinders (a starting sketch is in "
          "*_members.scad), keeping every preserve interface exactly, then:")
    print("  topopt compare %s <rebuild>.stl --study %s"
          % (outdir, study_path))
    print("  (the grid origin is read from the summary; no --origin needed)")


def write_result_png(path, x_phys, mask, setup, history):
    """Read-the-result picture for the generative path.

    Max projections are close to useless on a 3D result -- one thin rib makes a
    whole column read as solid. Top row is the *mean* density along each axis,
    i.e. how much material a ray actually passes through; bottom row is the
    mid-plane slice through the finished binary geometry, which is where the
    topology is legible. Right-hand column carries the continuation schedule,
    because the objective rising when beta or penal steps is expected and looks
    like divergence if you cannot see why.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = setup.grid
    fig, axes = plt.subplots(2, 4, figsize=(19, 8.5))
    planes = [(2, "XY (top)", 0, 1), (1, "XZ (front)", 0, 2), (0, "YZ (side)", 1, 2)]

    for col, (axis, label, ha, va) in enumerate(planes):
        extent = [g.origin[ha], g.origin[ha] + g.shape[ha] * g.h,
                  g.origin[va], g.origin[va] + g.shape[va] * g.h]
        ax = axes[0][col]
        depth = x_phys.sum(axis=axis) * g.h
        im = ax.imshow(depth.T, origin="lower", extent=extent, cmap="viridis",
                       aspect="equal")
        ax.set_title("material depth through %s" % label, fontsize=9)
        cb = fig.colorbar(im, ax=ax, fraction=0.046)
        cb.set_label("mm of material", fontsize=8)

        ax = axes[1][col]
        mid = mask.shape[axis] // 2
        sl = np.take(mask, mid, axis=axis).astype(float)
        pres = np.take(setup.preserve, mid, axis=axis)
        rgb = np.ones(sl.shape + (3,))
        rgb[sl > 0.5] = (0.15, 0.20, 0.35)
        rgb[pres] = (0.13, 0.72, 0.33)
        ax.imshow(np.transpose(rgb, (1, 0, 2)), origin="lower", extent=extent,
                  aspect="equal", interpolation="nearest")
        ax.set_title("mid-slice of the final geometry -- %s" % label, fontsize=9)
        for a in (axes[0][col], axes[1][col]):
            a.set_xlabel("XYZ"[ha] + " (mm)", fontsize=8)
            a.set_ylabel("XYZ"[va] + " (mm)", fontsize=8)
            a.tick_params(labelsize=7)

    it = [h["iter"] for h in history]
    ax = axes[0][3]
    ax.plot(it, [h["objective"] for h in history], "-o", ms=3, color="C0")
    ax.set_ylabel("normalised compliance (sum of cases)", fontsize=8, color="C0")
    ax.set_xlabel("iteration", fontsize=8)
    ax.set_title("convergence", fontsize=9)
    ax.grid(alpha=0.3)
    tw = ax.twinx()
    tw.step(it, [h["beta"] for h in history], where="post", color="C3", lw=1)
    tw.step(it, [h["penal"] for h in history], where="post", color="C2", lw=1)
    tw.set_ylabel("beta (red) / penal (green)", fontsize=8)

    ax = axes[1][3]
    ax.plot(it, [h["volume_fraction_free"] for h in history], "-", label="volume fraction")
    ax.plot(it, [h["greyness"] for h in history], "-", label="greyness")
    ax.plot(it, [h.get("topology_change_frac", 0) for h in history], "-",
            label="shape change")
    ax.set_yscale("log")
    ax.set_xlabel("iteration", fontsize=8)
    ax.set_title("volume, discreteness, convergence", fontsize=9)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt opt", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study")
    ap.add_argument("--out", default="topo_out")
    ap.add_argument("--volfrac", type=float)
    ap.add_argument("--iters", type=int, default=60)
    ap.add_argument("--elements", type=int)
    ap.add_argument("--voxel", type=float, help="voxel size in mm "
                    "(geometry schema only; overrides --elements)")
    ap.add_argument("--force", action="store_true",
                    help="optimise even when setup validation failed")
    ap.add_argument("--no-fea", action="store_true",
                    help="skip the final FEA on the thresholded geometry")
    ap.add_argument("--quick", action="store_true",
                    help="exploration mode: keep the optimisation, the final "
                         "FEA, the density fields, the blocky STL and the "
                         "result picture; skip the smooth STL, STEP, member "
                         "sketch and per-case FEA pictures")
    ap.add_argument("--quiet", action="store_true")
    _backend.add_cli_argument(ap)
    ap.add_argument("--isotropic", action="store_true",
                    help="use the legacy two-constant isotropic model "
                         "instead of the orthotropic card. Reproduces "
                         "results recorded before Phase 2; the shape it "
                         "finds does not know about the build direction.")
    a = ap.parse_args(argv)
    _backend.apply_cli_choice(a)

    study = load_case_from_json(a.study)
    if a.elements:
        study.target_elements = a.elements
    design = dict(study.raw.get("design", {}))
    if a.volfrac is not None:
        design["volfrac"] = a.volfrac

    t0 = time.time()
    if study.raw.get("geometry"):
        try:
            summary = run_generative(study, a.study, design, a.out, a.iters,
                                     a.elements, a.voxel, a.quiet, a.force,
                                     not a.no_fea, isotropic=a.isotropic,
                                     quick=a.quick)
        except (SingularStiffness, SolverDidNotConverge) as exc:
            # Spec 19.2: a stiffness matrix with no unique solution, or a CG
            # that never converged, is a finding about this study -- exit 1,
            # one sentence, no traceback. Not exit 2: the study read fine.
            # The reason is in `<base>.json` as well (write_stopped_summary).
            print("opt: the optimisation stopped before it had a shape: %s"
                  % exc, file=sys.stderr)
            return 1
        if not a.quiet:
            # The verdict first, then the numbers behind it -- the same order
            # `report.md` uses, and for the same reason: the last thing on the
            # screen after a long run is a NEXT: block telling the reader what
            # to do with a result that may not be one.
            print("\n--- verdict: %s ---"
                  % ("PASS" if summary["pass"] else "FAIL"))
            for c in summary["checks"]:
                print("  [%s] %-18s %s -> %s"
                      % ("PASS" if c["pass"] else "FAIL", c["name"],
                         c["required"], c["got"]))
            _print_generative_summary(summary, a.out, a.study)
        return 0 if summary["pass"] else 1

    summary, xf, grid = run(study, design, a.out, a.iters, a.quiet)
    print("\nmass saving      %.1f %% (%.0f -> %.0f mm3)"
          % (summary["mass_saving_pct"], summary["original_volume_mm3"],
             summary["kept_volume_mm3"]))
    ex = summary.get("extracted_members") or {}
    if ex.get("members"):
        print("\nextracted %d members in the %s plane, extruded %.1f mm along %s:"
              % (len(ex["members"]), ex["members"][0]["plane"],
                 ex.get("extrusion_thickness_mm", 0), ex.get("extrusion_axis")))
        print("  %-24s %-24s %8s %8s" % ("start (mm)", "end (mm)", "length", "width"))
        for m in ex["members"]:
            print("  %-24s %-24s %8.1f %8.2f"
                  % (m["start_mm"], m["end_mm"], m["length_mm"], m["width_mm"]))
    elif ex.get("note"):
        print("\nmember extraction: %s" % ex["note"])
    print("wrote %s (%.0f s)" % (a.out, time.time() - t0))
    print("\nNEXT: this is a load-path map, not a part. Rebuild it as "
          "parametric OpenSCAD from cubes/cylinders following those members "
          "(a starting sketch is in *_members.scad), then verify with:")
    print("  topopt compare %s <rebuild>.stl --study <study>.json"
          % a.out)
    print("Expect the first rebuild to be several times softer than the "
          "optimum -- see references/topology-optimization.md stage 4.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

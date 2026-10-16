"""`topopt extract` — turn an optimisation's density field into candidate shapes.

What this is for
----------------
The optimiser leaves behind a *grey* field: every voxel holds a number between
0 and 1, and the part is whatever you decide "solid enough" means. That
decision is a real design choice and it is not obvious -- move the cut-off from
0.5 to 0.4 and the part gets heavier and stiffer; move it to 0.6 and thin ribs
disappear. `topopt opt` used to make the choice for you at 0.5 and never say so.

This command makes the choice visible: it builds the part at several cut-offs,
re-runs the real FEA on each, and prints them side by side so you can see what
the choice costs. Nothing here re-optimises -- the density field is fixed; only
the interpretation of it changes.

How it works
------------
Read the saved density field, pick a cut-off, keep every voxel above it, add
back the preserve regions (which are never allowed to disappear), subtract the
obstacles, throw away anything not connected to the rest, and measure what is
left. Then do it again at the next cut-off.

Limits worth knowing
--------------------
- **The grid is pinned to the optimisation's grid.** Re-deriving it would give
  a slightly different voxel size and every number here would then be
  incomparable with the run it came from.
- **Thin-feature readings hit a floor.** Nothing thinner than two voxels can
  exist on this grid, so a minimum thickness of exactly 2 x voxel is the grid
  talking, not the structure. It is labelled as such rather than reported as a
  measurement.
- **The result is still not a part.** Interface dimensions are quantised to the
  voxel. Rebuild them in CAD and prove it with `topopt compare`.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt import finish, generative as gen, schema              # noqa: E402
from topopt.cli.setup import prepare                              # noqa: E402
from topopt import backend as _backend                                   # noqa: E402
from topopt.designspace import min_member_size                    # noqa: E402
from topopt.external import require                               # noqa: E402
from topopt.loadcase import load_case_from_json                   # noqa: E402
from topopt.meshout import (write_blocky_stl, write_smooth_stl,   # noqa: E402
                            write_step_faceted)

DEFAULT_LEVELS = (0.4, 0.5, 0.6)


# ------------------------------------------------------------------ inputs

def load_density(topo_dir):
    """-> (base, density field, summary dict). Raises for a directory that
    holds no optimisation output, rather than inventing an empty field."""
    if not os.path.isdir(topo_dir):
        raise FileNotFoundError("not a directory: %s" % topo_dir)
    cands = sorted(f for f in os.listdir(topo_dir)
                   if f.endswith("_density_phys.npy"))
    if not cands:
        raise FileNotFoundError(
            "no density field in %s -- expected <base>_density_phys.npy, "
            "which `topopt opt` writes. Run the optimisation first."
            % topo_dir)
    base = cands[0][: -len("_density_phys.npy")]
    dens = np.load(os.path.join(topo_dir, cands[0]))
    summary = {}
    p = os.path.join(topo_dir, base + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            summary = json.load(f)
    return base, dens, summary


def recorded_grid(summary):
    """-> (voxel_mm, origin) recorded by the optimisation, or (None, None).

    Rebuilding the setup from the study alone would re-derive the voxel size
    from the input bounding boxes, and any difference -- even in the fourth
    decimal -- makes every number below incomparable with the run that
    produced the field (lessons-learned 30).
    """
    info = (summary.get("setup") or {}).get("info") or {}
    h = info.get("voxel_mm") or (summary.get("volumes") or {}).get("voxel_mm")
    o = info.get("origin_mm")
    return (float(h) if h else None,
            np.array(o, float) if o is not None else None)


# ------------------------------------------------------------- measurement

def thickness_report(mask, setup, h):
    """Minimum member size, with the grid floor called out.

    Two figures, because they answer different questions. `whole` includes
    preserve, which is whatever the CAD says it is -- a 2.5 mm camera tab is
    not a filter violation. `optimised` covers only the cells the optimiser was
    free to move, which is the number that says whether the filter radius held.
    """
    whole = min_member_size(mask, h)
    opt = min_member_size(mask, h, where=setup.free)
    floor = 2.0 * h
    out = {"voxel_mm": round(h, 4),
           "grid_floor_mm": round(floor, 4),
           "whole_part": whole,
           "optimised_region": opt}
    for key, rep in (("whole_part", whole), ("optimised_region", opt)):
        if not rep:
            continue
        # `min_member_size` rounds to 3 decimals, so an exact comparison never
        # fires: at a 1.6370 mm voxel the floor is 3.27393 and the report says
        # 3.274. The tolerance is the rounding, not a fudge factor -- and
        # getting this wrong means the one reading AGENTS.md says must never be
        # quoted as a structural value gets quoted as one.
        limited = abs(rep["min_mm"] - floor) <= 1e-3
        rep["grid_limited"] = bool(limited)
        rep["note"] = (
            "min_mm is exactly 2 x voxel: this is the finest feature the grid "
            "can represent, not a measurement of the structure. Re-run at a "
            "smaller --voxel to measure it." if limited else
            "measured on the medial axis (inscribed-sphere diameter)")
    return out


def downward_face_area(mask, h, build_axis=2):
    """Area of faces that have nothing under them, mm^2.

    A cheap stand-in for support area that needs no STL: on a voxel model the
    only overhang angles are 0 and 90 degrees, so the honest thing to report is
    the flat downward-facing area and to say that is what it is. The real
    number, with actual angles, comes from `topopt overhang` on the STL.
    """
    below = np.roll(mask, 1, axis=build_axis)
    idx = [slice(None)] * 3
    idx[build_axis] = 0
    # The first layer sits on the build plate, which supports it. Marking it
    # `True` (supported) rather than `False` matters: `np.roll` wraps, so
    # without this the bottom layer is compared against the *top* one, and a
    # solid block flat on the plate reports its whole footprint as overhang.
    below[tuple(idx)] = True
    return round(float(np.count_nonzero(mask & ~below)) * h * h, 1)


def evaluate(level, x_phys, setup, study, h, do_fea=True, isotropic=False,
             quiet=False, self_support=None):
    """One threshold candidate, measured the same way as every other."""
    mask, conn = gen.final_geometry(x_phys, setup, level=level,
                                    self_support=self_support, study=study)
    out = {"level": level,
           "connected": bool(conn.get("connected")),
           "connectivity": conn,
           "volumes": gen.measure(mask, setup, self_support=self_support,
                                  study=study),
           "thickness": thickness_report(mask, setup, h),
           "downward_face_area_mm2": downward_face_area(mask, h,
                                                        study.build_axis),
           "solid_voxels": int(mask.sum())}
    if not mask.any():
        out["error"] = "nothing survives this threshold"
        return mask, out
    if do_fea and out["connected"]:
        try:
            _, results = gen.final_fea(mask, setup, study, verbose=False,
                                       isotropic=isotropic)
            out["fea"] = [s for _, _, s in results]
            out["min_safety_factor_p99_5"] = min(
                s["safety_factor_p99_5"] for s in out["fea"])
            out["worst_element_safety_factor"] = min(
                s["min_safety_factor"] for s in out["fea"])
            out["max_displacement_mm"] = max(
                s["max_displacement_mm"] for s in out["fea"])
            out["pass"] = all(s.get("pass", False) for s in out["fea"])
        except Exception as exc:                                  # noqa: BLE001
            # A candidate whose FEA fell over is reported as unevaluated, not
            # as passing: spec 18 forbids showing an incomplete analysis as a
            # verdict.
            out["fea_error"] = str(exc)
            out["pass"] = False
    elif not out["connected"]:
        out["pass"] = False
        out["note"] = ("not connected -- some preserve region is not reachable "
                       "through material at this threshold")
    return mask, out


# ---------------------------------------------------------------- printing

def print_table(cands, required_sf):
    print("\n%-7s %10s %9s %9s %9s %9s %8s  %s"
          % ("level", "volume", "SF p99.5", "SF worst", "max mm",
             "min thk", "down A", "verdict"))
    for c in cands:
        v = c["volumes"]["final_mm3"]
        thk = (c["thickness"]["optimised_region"] or {}).get("min_mm")
        lim = (c["thickness"]["optimised_region"] or {}).get("grid_limited")
        print("%-7.2f %10.0f %9s %9s %9s %9s %8.0f  %s"
              % (c["level"], v,
                 _f(c.get("min_safety_factor_p99_5")),
                 _f(c.get("worst_element_safety_factor")),
                 _f(c.get("max_displacement_mm"), "%.3f"),
                 ("%.2f*" % thk) if lim else _f(thk, "%.2f"),
                 c["downward_face_area_mm2"],
                 _verdict(c, required_sf)[:60]))
    if any((c["thickness"]["optimised_region"] or {}).get("grid_limited")
           for c in cands):
        print("  * grid-limited: min thickness is exactly 2 x voxel, i.e. the "
              "finest the grid can hold. Not a measurement of the structure.")


def _f(v, fmt="%.2f"):
    return "-" if v is None else (fmt % v)


def _verdict(c, required_sf):
    """Why this candidate failed -- the actual check, not an assumed one.

    Naming the wrong reason is worse than naming none: a run whose mesh is too
    coarse would be reported as "FAIL, SF 61.22 < 3.00", which is both false
    and self-contradictory, and sends someone off to add material to a part
    that already has 20x the strength it needs.
    """
    if c.get("error"):
        return c["error"]
    if not c["connected"]:
        return "NOT CONNECTED"
    if c.get("fea_error"):
        return "FEA FAILED (not evaluated)"
    if c.get("min_safety_factor_p99_5") is None:
        return "not analysed"
    if c.get("pass"):
        return "PASS"
    bad = failing_checks(c)
    if not bad:
        return "FAIL (no check reported why)"
    return "FAIL: " + "; ".join(bad)


def failing_checks(c):
    """-> ["strength: got X vs required Y", ...] over every load case.

    Deduplicated: four load cases failing the same mesh check is one problem,
    not four.
    """
    seen, out = set(), []
    for s in c.get("fea") or []:
        for chk in s.get("checks") or []:
            if chk.get("pass"):
                continue
            key = chk.get("name")
            if key in seen:
                continue
            seen.add(key)
            out.append("%s (%s, required %s)"
                       % (key, chk.get("got"), chk.get("required")))
    return out


# ------------------------------------------------------------ what shipped

def shell_reports(files, quiet=False):
    """Spec 19.3: inspect every STL this command actually wrote. -> dict

    Three questions per file, all of them about failures that are invisible
    in a volume total: open edges (is it watertight), how many separate
    bodies came out, and whether any two faces pass through each other. A
    part that ships as two bodies, or with a surface running through itself,
    goes into a slicer and comes out as something nobody designed -- and the
    numbers in the summary next to it are all perfectly reasonable.

    Keyed by the same tags as `outputs`, so a reader can line the two up.
    """
    from topopt import geom

    out = {}
    for tag, path in sorted(files.items()):
        if not str(path).lower().endswith(".stl") or not os.path.exists(path):
            continue
        try:
            rep = geom.shell_report(geom.load_stl(path))
        except Exception as exc:                                  # noqa: BLE001
            out[tag] = {"error": "%s: %s" % (type(exc).__name__, exc)}
            continue
        rep["file"] = os.path.basename(path)
        out[tag] = rep
        if quiet:
            continue
        bad = []
        if rep["open_edges"]:
            bad.append("%d open edge(s)" % rep["open_edges"])
        if rep["components"] > 1:
            bad.append("%d separate bodies" % rep["components"])
        if rep.get("self_intersections"):
            bad.append("%d self-intersecting face pair(s)"
                       % rep["self_intersections"])
        print("  shell   %-12s %6d triangles%s"
              % (tag, rep["triangles"], ("  !! " + ", ".join(bad)) if bad
                 else "  watertight, one body, no self-intersection"))
    return out


# -------------------------------------------------------------------- main

def run(topo_dir, study_path, outdir=None, levels=DEFAULT_LEVELS, pick=None,
        do_fea=True, isotropic=False, quiet=False, step_in=None, quick=False):
    """`quick`: the candidate table and the blocky STL of the chosen
    threshold only -- no smooth STL, STEP, finish grid or shell inspection.
    Those are what a shape you are going to keep needs, not what a shape
    you are comparing against six others needs."""
    if step_in:
        # Spec 19.3 / Phase 3: a STEP solid needs a real kernel. Detected at
        # run time and reported as exit code 3, never as a failed part.
        require("freecad", "reading a STEP solid as input geometry")

    study = load_case_from_json(study_path)
    base, x_phys, summary = load_density(topo_dir)
    h_rec, o_rec = recorded_grid(summary)

    setup, report = prepare(study, voxel=h_rec, verbose=not quiet)
    h = setup.h
    if h_rec and abs(h - h_rec) > 1e-6:
        raise ValueError(
            "rebuilt grid is %.4f mm but the optimisation ran at %.4f mm -- "
            "the candidates would not be comparable with the run they came "
            "from. Check that --study points at the same study file."
            % (h, h_rec))
    if x_phys.shape != setup.grid.shape:
        raise ValueError(
            "density field is %s but the rebuilt setup is %s. The study and "
            "the output directory do not belong together."
            % (x_phys.shape, setup.grid.shape))

    if not quiet:
        print("\ndensity field %s at %.4f mm voxel, %s"
              % (base, h, "x".join(str(n) for n in x_phys.shape)))
        print("thresholds: %s" % ", ".join("%.2f" % v for v in levels))

    cands = []
    masks = {}
    for lv in levels:
        m, c = evaluate(lv, x_phys, setup, study, h, do_fea=do_fea,
                        isotropic=isotropic, quiet=quiet,
                        self_support=(summary.get("settings") or {}).get("overhang"))
        masks[lv] = m
        cands.append(c)

    if not quiet:
        print_table(cands, study.safety_factor)

    chosen = pick if pick is not None else _choose(cands)
    out = {"schema": "topopt/extract/1",
           "source_dir": os.path.abspath(topo_dir),
           "study": study_path,
           "voxel_mm": round(h, 4),
           "grid_origin_mm": [round(float(v), 4) for v in setup.origin],
           "required_safety_factor": study.safety_factor,
           "material_model": ("isotropic_two_constant" if isotropic
                              else "orthotropic_equivalent_solid"),
           "levels": list(levels),
           "candidates": cands,
           "chosen_level": chosen,
           "chosen_reason": _choose_reason(cands, chosen, pick is not None)}

    if outdir:
        os.makedirs(outdir, exist_ok=True)
        files = {}
        mask = masks[chosen]
        p = os.path.join(outdir, "%s_t%02d.stl" % (base, round(chosen * 100)))
        write_blocky_stl(p, mask, h, setup.origin)
        files["stl"] = p
        if quick:
            out["outputs"] = files
            out["quick"] = True
            out["shell"] = {}
            with open(os.path.join(outdir, base + "_extract.json"), "w",
                      encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            if not quiet:
                print("\nwrote threshold %.2f to %s (--quick: blocky STL "
                      "only)" % (chosen, outdir))
            return out
        p2 = os.path.join(outdir,
                          "%s_t%02d_smooth.stl" % (base, round(chosen * 100)))
        # Preserve is floored at 1 so smoothing can never cut into it.
        if write_smooth_stl(p2, np.where(mask, 1.0, x_phys), h, setup.origin,
                            floor_mask=setup.preserve | (mask & setup.ground),
                            envelope=mask):
            files["smooth_stl"] = p2
        p3 = os.path.join(outdir, "%s_t%02d.step" % (base, round(chosen * 100)))
        if write_step_faceted(p3, mask, h, setup.origin, name=base):
            files["step"] = p3

        # The interface goes back in at its CAD dimensions. Without this the
        # shipped shape is the analysis grid all the way through, and any
        # mating feature under a voxel across is simply not in the file.
        try:
            spec = finish.parse(study.raw.get("finish"), study.base_dir)
        except finish.FinishError as exc:
            raise schema.InputError("finish: %s" % exc)
        if spec:
            if not quiet:
                print("\nfinish (interface at CAD dimensions, %.2f mm grid)"
                      % spec["voxel_mm"])
            # The setup goes along so the stage knows which analysed voxels
            # are preserve (to replace with the CAD, or to hold while
            # smoothing), which are obstacle (to keep the smoothing out of)
            # and where the plate is.
            part = finish.build_part(mask, h, setup.origin, spec,
                                     verbose=not quiet, setup=setup)
            files.update(finish.write_part(
                part, outdir, "%s_t%02d" % (base, round(chosen * 100)),
                verbose=not quiet))
            out["finish"] = part.info
        out["outputs"] = files
        out["shell"] = shell_reports(files, quiet=quiet)
        with open(os.path.join(outdir, base + "_extract.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        if not quiet:
            print("\nwrote threshold %.2f to %s" % (chosen, outdir))
            for k, v in files.items():
                print("  %-11s %s" % (k, v))
    return out


def _choose(cands):
    """Prefer a passing candidate, lightest first; else the stiffest one.

    Deliberately not "always 0.5": the whole point of generating candidates is
    that the default is sometimes wrong, and a rule that always returns the
    default would hide that.
    """
    ok = [c for c in cands if c.get("pass")]
    if ok:
        return min(ok, key=lambda c: c["volumes"]["final_mm3"])["level"]
    conn = [c for c in cands if c["connected"]
            and c.get("min_safety_factor_p99_5") is not None]
    if conn:
        return max(conn, key=lambda c: c["min_safety_factor_p99_5"])["level"]
    return cands[len(cands) // 2]["level"]


def _choose_reason(cands, chosen, forced):
    """Why this threshold, in the words of the checks that actually decided.

    Same rule as `_verdict`: never name a check that passed. Saying "no
    candidate meets the required safety factor" about a part with a safety
    factor of 61 is worse than saying nothing -- and this string lands in the
    summary JSON, which is what a reader opens.
    """
    if forced:
        return "chosen by --pick; no candidate comparison was used"
    c = next(x for x in cands if x["level"] == chosen)
    if c.get("pass"):
        return ("lightest candidate that meets every check on every load case")
    bad = failing_checks(c)
    if bad:
        return ("no candidate passes every check; this is the strongest of "
                "the connected ones and is NOT a pass. Outstanding: %s"
                % "; ".join(bad))
    if c.get("min_safety_factor_p99_5") is not None:
        return ("no candidate passes; this is the strongest of the connected "
                "ones and is NOT a pass (no check reported why)")
    return "no candidate could be analysed; this is a placeholder, not a choice"


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="topopt extract", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topo_dir", help="an output directory from `topopt opt`")
    ap.add_argument("--study", required=True,
                    help="the study the optimisation was run from")
    ap.add_argument("--out", help="write STL/STEP and the JSON summary here")
    ap.add_argument("--levels", default="0.4,0.5,0.6",
                    help="threshold candidates (default 0.4,0.5,0.6)")
    ap.add_argument("--pick", type=float,
                    help="write this threshold instead of the chosen one")
    ap.add_argument("--no-fea", action="store_true",
                    help="skip the re-analysis; geometry measures only")
    ap.add_argument("--isotropic", action="store_true",
                    help="re-analyse with the legacy two-constant model")
    ap.add_argument("--step-in", help="STEP solid as input geometry "
                                      "(needs FreeCAD; exit code 3 without it)")
    ap.add_argument("--quick", action="store_true",
                    help="exploration mode: candidate table and the blocky "
                         "STL of the chosen threshold only (no smooth STL, "
                         "STEP, finish grid or shell inspection)")
    ap.add_argument("--quiet", action="store_true")
    _backend.add_cli_argument(ap)
    a = ap.parse_args(argv)
    _backend.apply_cli_choice(a)

    levels = tuple(float(v) for v in a.levels.split(","))
    for lv in levels:
        if not 0.0 < lv < 1.0:
            raise SystemExit("threshold %r is outside (0, 1)" % lv)
    if a.pick is not None and a.pick not in levels:
        levels = tuple(sorted(set(levels) | {a.pick}))

    out = run(a.topo_dir, a.study, a.out, levels, a.pick, not a.no_fea,
              a.isotropic, a.quiet, a.step_in, quick=a.quick)

    chosen = next(c for c in out["candidates"]
                  if c["level"] == out["chosen_level"])
    if not a.quiet:
        print("\nchosen threshold %.2f -- %s"
              % (out["chosen_level"], out["chosen_reason"]))
        print("\nNEXT: this is still not a part. Interface dimensions are "
              "quantised to %.3f mm. Rebuild them as parametric CAD and prove "
              "it carries the load with:" % out["voxel_mm"])
        print("  topopt compare %s <rebuild>.stl --study %s"
              % (a.topo_dir, a.study))
    return 0 if chosen.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Can this part be printed on a Bambu Lab X1-Carbon, and in what orientation?

    topopt print part.stl                       # check + best orientation
    topopt print part.stl --printer X1C --material PETG
    topopt print part.stl --json out.json --png out.png
    topopt print part.stl --keep-flat Z         # do not reorient this axis

Reports, for each of the 24 axis-aligned orientations: does it fit the build
volume, how much overhang needs support, how big the first layer is, and how
tall it prints. Then it names the best one and, when nothing fits, says where
to cut the part and what joint to use.

Orientation is a *structural* decision as well as a printing one: FDM parts are
weakest across the layers, so the chosen orientation must put the principal
tensile stress in the layer plane. Feed `--load-axis` with the direction of the
main tensile load and orientations that put it across the layers are penalised.
"""

import argparse
import json
import math
import os
import sys

import numpy as np


# Long runs are common here; without line buffering a redirected run shows an
# empty log for tens of minutes and looks hung.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from topopt.voxel import load_stl, voxelize_stl                    # noqa: E402
from topopt.report import local_thickness                          # noqa: E402

# Build volumes live in `topopt.printers` -- one module owns them, the same way
# `materials.py` owns the material numbers. This command used to carry its own
# copy, which is how a part could be checked against the envelope here and
# never checked against it at setup, where it would have been cheap.
from topopt.printers import (PRINTERS, fits_somehow,               # noqa: E402
                             resolve, suggest_split)

# The 24 rotations that map the axes onto each other, as (axis permutation,
# sign vector). Only these keep a part's flat faces flat on the bed, which is
# what you want for a functional print.
def _orientations():
    out = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for sx in (1, -1):
            for sy in (1, -1):
                M = np.zeros((3, 3))
                M[0, perm[0]] = sx
                M[1, perm[1]] = sy
                # Third row fixed by right-handedness so we never mirror the part.
                M[2] = np.cross(M[0], M[1])
                out.append(M)
    return out


ORIENTATIONS = _orientations()


def _name_orientation(M):
    """Describe an orientation as 'which model axis now points up'."""
    up = M[2]
    axis = int(np.argmax(np.abs(up)))
    sign = "+" if up[axis] > 0 else "-"
    return "%s%s up" % (sign, "XYZ"[axis])


def triangle_areas_normals(tris):
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    n = np.cross(e1, e2)
    a = np.linalg.norm(n, axis=1)
    good = a > 1e-12
    normals = np.zeros_like(n)
    normals[good] = n[good] / a[good, None]
    return 0.5 * a, normals


def analyse_orientation(tris, M, overhang_deg=45.0, first_layer_tol=0.4):
    """Geometry metrics for the part rotated by M (rows are the new axes)."""
    v = tris.reshape(-1, 3) @ M.T
    r = v.reshape(-1, 3, 3)
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    size = hi - lo

    area, normals = triangle_areas_normals(r)
    total_area = float(area.sum())

    # A downward face needs support when it leans more than overhang_deg from
    # vertical; that is exactly normal_z < -sin(overhang_deg).
    thr = -math.sin(math.radians(overhang_deg))
    overhang = normals[:, 2] < thr
    overhang_area = float(area[overhang].sum())

    # First layer: downward faces sitting within a tolerance of the lowest point.
    zmin = lo[2]
    centroid_z = r[:, :, 2].mean(axis=1)
    flat_down = (normals[:, 2] < -0.99) & (centroid_z < zmin + first_layer_tol)
    first_layer = float(area[flat_down].sum())

    return {"size": size, "lo": lo, "hi": hi, "overhang_area": overhang_area,
            "overhang_fraction": overhang_area / total_area if total_area else 0.0,
            "first_layer_area": first_layer, "height": float(size[2]),
            "total_area": total_area}


def score_orientation(m, usable, load_axis_model=None, M=None,
                      w_support=1.0, w_bed=1.0, w_height=0.3, w_load=2.0):
    """Lower is better. `fits` dominates everything else."""
    fits = bool(np.all(m["size"] <= np.array(usable) + 1e-6))
    s = 0.0
    s += w_support * m["overhang_fraction"] * 10.0
    # Bed adhesion: reward a first layer that is a decent share of the footprint.
    footprint = max(m["size"][0] * m["size"][1], 1e-9)
    s += w_bed * (1.0 - min(1.0, m["first_layer_area"] / footprint)) * 3.0
    s += w_height * (m["height"] / max(usable[2], 1e-9)) * 3.0
    load_penalty = None
    if load_axis_model is not None and M is not None:
        d = M @ np.asarray(load_axis_model, float)
        d = d / max(np.linalg.norm(d), 1e-12)
        # |d_z| = 1 means the tensile load pulls straight across the layers.
        load_penalty = float(abs(d[2]))
        s += w_load * load_penalty * 10.0
    return {"fits": fits, "score": float(s) if fits else float(s) + 1e6,
            "load_across_layers": load_penalty}


def check_min_features(stl_path, nozzle, target_elements=200000):
    """Find sections thinner than the printer can actually make."""
    grid = voxelize_stl(stl_path, target_elements=target_elements)
    vals = local_thickness(grid)
    if vals is None or vals.size == 0:
        return None
    min_wall = 2 * nozzle          # two perimeters; anything less is a gap-fill
    thin = float(np.percentile(vals, 1))
    return {
        "voxel_mm": grid.h,
        "min_printable_wall_mm": min_wall,
        "p1_thickness_mm": round(thin, 3),
        "p5_thickness_mm": round(float(np.percentile(vals, 5)), 3),
        "median_thickness_mm": round(float(np.percentile(vals, 50)), 3),
        "fraction_below_min": round(float((vals < min_wall).mean()), 4),
        "resolved": bool(grid.h <= min_wall / 2),
        "pass": bool(thin >= min_wall),
    }


def _overshoot(size, usable):
    """Why this orientation is out, in millimetres on the named bed axis.

    "does not fit" on its own sends someone to measure the part again. The
    axis and the overshoot say whether it is 3 mm or 300 -- i.e. whether to
    reorient, to trim, or to split.
    """
    over = [(float(s) - float(u), ax)
            for s, u, ax in zip(size, usable, ("bed X", "bed Y", "Z up"))
            if float(s) > float(u) + 1e-6]
    if not over:                                            # pragma: no cover
        return "does not fit"
    over.sort(reverse=True)
    return ("excluded: does not fit the %s mm usable envelope -- %s"
            % ([round(float(u), 1) for u in usable],
               ", ".join("%.1f mm over on %s" % (d, ax) for d, ax in over)))


def analyse(stl_path, printer="X1C", overhang_deg=45.0, load_axis=None,
            keep_flat=None, min_features=True):
    spec = resolve(printer)
    usable = spec.usable_mm
    tris = load_stl(stl_path)

    rows = []
    for M in ORIENTATIONS:
        if keep_flat is not None:
            # Only consider orientations that leave this model axis vertical.
            ax = {"X": 0, "Y": 1, "Z": 2}[keep_flat.upper()]
            if abs(M[2][ax]) < 0.99:
                continue
        m = analyse_orientation(tris, M, overhang_deg)
        sc = score_orientation(m, usable, load_axis, M)
        rows.append({"M": M, "metrics": m, **sc})
    rows.sort(key=lambda r: r["score"])

    # A symmetric part yields several rotations that print identically; showing
    # them all just buries the genuinely different options.
    seen, unique = set(), []
    for r in rows:
        key = (_name_orientation(r["M"]),
               tuple(np.round(r["metrics"]["size"], 2)),
               round(r["metrics"]["overhang_fraction"], 4))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    rows = unique

    as_is = next(r for r in rows if np.allclose(r["M"], np.eye(3))) \
        if any(np.allclose(r["M"], np.eye(3)) for r in rows) else None

    # Spec 19.5: an orientation the machine cannot print is not a candidate.
    # Listing it as one (which is what sorting by a score with a +1e6 penalty
    # did) puts an unbuildable row at the top of the table whenever nothing
    # fits, and someone reads the overhang percentage off it. It is kept, with
    # the reason and the overshoot in millimetres, under
    # `excluded_orientations` -- excluded is not the same as unmentioned.
    excluded = [r for r in rows if not r["fits"]]
    rows = [r for r in rows if r["fits"]]

    out = {
        "schema": "topopt/printability/1",
        "part": os.path.basename(stl_path),
        "printer": spec.name,
        "build_volume_nominal_mm": list(spec.nominal_mm),
        "build_volume_usable_mm": list(usable),
        "triangles": int(tris.shape[0]),
        "bbox_as_modelled_mm": [round(float(v), 2)
                                for v in (tris.reshape(-1, 3).max(axis=0)
                                          - tris.reshape(-1, 3).min(axis=0))],
    }
    out["fits_as_modelled"] = bool(as_is and as_is["fits"])
    out["fits_in_some_orientation"] = bool(rows)

    def _row(r):
        return {
            "orientation": _name_orientation(r["M"]),
            "size_mm": [round(float(v), 1) for v in r["metrics"]["size"]],
            "fits": r["fits"],
            "overhang_pct": round(100 * r["metrics"]["overhang_fraction"], 1),
            "first_layer_mm2": round(r["metrics"]["first_layer_area"], 1),
            "height_mm": round(r["metrics"]["height"], 1),
            "load_across_layers": (None if r["load_across_layers"] is None
                                   else round(r["load_across_layers"], 3)),
            "score": round(r["score"], 3),
        }

    out["orientations"] = [_row(r) for r in rows[:8]]
    out["excluded_orientations"] = [
        dict(_row(r), note=_overshoot(r["metrics"]["size"], usable))
        for r in excluded]
    # `{}` and not `None` when nothing fits: `best_orientation` is a required
    # object in the output schema, and every reader in the tree either tests
    # it for truth or reaches it through `.get`, so an empty one is the shape
    # that says "there is no candidate" without breaking anything.
    out["best_orientation"] = out["orientations"][0] if rows else {}

    model_size = (tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0))
    out["split"] = suggest_split(model_size, usable)

    if min_features:
        out["min_features"] = check_min_features(stl_path, spec.nozzle_mm)

    best = out["best_orientation"]
    checks = [
        {"name": "build volume",
         "required": "fits %s mm in some axis-aligned orientation" % list(usable),
         "got": ("yes, best is %s at %s mm"
                 % (best["orientation"], best["size_mm"])
                 if out["fits_in_some_orientation"]
                 else "no -- %s mm, all %d orientation(s) excluded"
                      % (out["bbox_as_modelled_mm"],
                         len(out["excluded_orientations"]))),
         "pass": out["fits_in_some_orientation"]},
    ]
    checks.append(
        {"name": "supports",
         "required": "overhang area <= 15 % in the chosen orientation",
         "got": "%.1f %%" % best["overhang_pct"],
         "pass": best["overhang_pct"] <= 15.0} if best else
        # Not "0 % overhang, pass". There is no chosen orientation to measure
        # it in, and a support check that abstains has to say so rather than
        # contribute a quiet pass to the verdict.
        {"name": "supports",
         "required": "overhang area <= 15 % in the chosen orientation",
         "got": "not measured: no orientation fits the machine",
         "pass": False})
    if out.get("min_features"):
        mf = out["min_features"]
        checks.append({
            "name": "min feature size",
            "required": "no section thinner than %.1f mm (2 perimeters)"
                        % mf["min_printable_wall_mm"],
            "got": "p1 %.2f mm, %.2f %% of sections below the limit"
                   % (mf["p1_thickness_mm"], 100 * mf["fraction_below_min"]),
            "pass": mf["pass"]})
    if best.get("load_across_layers") is not None:
        lal = best["load_across_layers"]
        checks.append({
            "name": "load vs layers",
            "required": "main tensile load should lie in the layer plane "
                        "(cos <= 0.5)",
            "got": "cos = %.2f" % lal,
            "pass": lal <= 0.5})
    out["checks"] = checks
    out["pass"] = all(c["pass"] for c in checks)
    return out


def write_png(path, stl_path, result):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tris = load_stl(stl_path)
    # Nothing fits: draw the excluded ones instead of drawing nothing. The
    # picture is the fastest way to see *how* they miss the envelope.
    shown = (result["orientations"] or result.get("excluded_orientations")
             or [])[:3]
    names = [o["orientation"] for o in shown]
    Ms = []
    for nm in names:
        for M in ORIENTATIONS:
            if _name_orientation(M) == nm:
                Ms.append(M)
                break
    usable = result["build_volume_usable_mm"]

    fig, axes = plt.subplots(1, max(len(Ms), 1), figsize=(5 * max(len(Ms), 1), 5))
    if len(Ms) <= 1:
        axes = [axes]
    for ax, M, o in zip(axes, Ms, shown):
        v = (tris.reshape(-1, 3) @ M.T)
        v -= v.min(axis=0)
        ax.plot([0, usable[0], usable[0], 0, 0],
                [0, 0, usable[2], usable[2], 0], "g--", lw=1,
                label="build volume")
        ax.scatter(v[::7, 0], v[::7, 2], s=0.4, c="tab:blue", alpha=0.35)
        ok = "fits" if o["fits"] else "DOES NOT FIT"
        ax.set_title("%s -- %s\n%s mm, overhang %.1f %%"
                     % (o["orientation"], ok, o["size_mm"], o["overhang_pct"]),
                     fontsize=9)
        ax.set_xlabel("bed X (mm)")
        ax.set_ylabel("Z up (mm)")
        ax.set_aspect("equal")
        ax.grid(alpha=0.2)
    fig.suptitle("%s on %s -- front view per orientation"
                 % (result["part"], result["printer"]), fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# Folders that hold geometry nobody is going to print. Auditing a purchased
# acrylic tube and advising where to saw it in half is noise.
DEFAULT_SKIP_DIRS = ("cots", "pcb", "cabling", "ref", "__pycache__")


def audit(root, printer="X1C", overhang_deg=45.0, skip_dirs=DEFAULT_SKIP_DIRS):
    """Walk a repository and report every printed STL's printability."""
    spec = resolve(printer)
    rows = []
    skip = {s.lower() for s in skip_dirs}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in skip]
        for f in sorted(files):
            if not f.lower().endswith(".stl"):
                continue
            p = os.path.join(dirpath, f)
            try:
                r = analyse(p, printer, overhang_deg, None, None, True)
            except Exception as exc:
                rows.append({"part": os.path.splitext(f)[0], "error": str(exc)})
                continue
            # A part that fits in no orientation has no best one; the row says
            # "-" rather than quoting the numbers of an unprintable candidate.
            best = r["best_orientation"] or {}
            rows.append({
                "part": os.path.splitext(f)[0],
                "path": os.path.relpath(p, root).replace("\\", "/"),
                "fits": r["fits_in_some_orientation"],
                "bbox_mm": r["bbox_as_modelled_mm"],
                "best_orientation": best.get("orientation", "-"),
                "overhang_pct": best.get("overhang_pct", 0.0),
                "thin_mm": (r["min_features"]["p1_thickness_mm"]
                            if r.get("min_features") else None),
                "split": r["split"] if r["split"].get("required") else None,
            })

    print("%-34s %-8s %-24s %-9s %-9s %s"
          % ("part", printer, "bbox mm (as modelled)", "best", "overhang", "thin mm"))
    for r in sorted(rows, key=lambda x: (x.get("fits", True), x["part"])):
        if r.get("error"):
            print("%-34s %-8s %s" % (r["part"], "ERROR", r["error"][:60]))
            continue
        print("%-34s %-8s %-24s %-9s %8.1f%% %s"
              % (r["part"], "fits" if r["fits"] else "TOO BIG", str(r["bbox_mm"]),
                 r["best_orientation"], r["overhang_pct"], r["thin_mm"]))

    big = [r for r in rows if r.get("fits") is False]
    sup = [r for r in rows if r.get("fits") and r["overhang_pct"] > 15]
    thin = [r for r in rows if r.get("thin_mm") and
            r["thin_mm"] < spec.min_feature_mm]
    print("\n%d STL(s): %d too big, %d over 15%% overhang, %d thinner than %.1f mm"
          % (len(rows), len(big), len(sup), len(thin), spec.min_feature_mm))
    for r in big:
        plans = ", ".join("%s into %d" % (p["axis"], p["pieces"])
                          for p in (r["split"] or {}).get("plans", []))
        print("  TOO BIG : %-28s %s  -> split %s" % (r["part"], r["bbox_mm"], plans))
    for r in sup:
        print("  SUPPORT : %-28s %.1f%% at %s"
              % (r["part"], r["overhang_pct"], r["best_orientation"]))
    for r in thin:
        print("  THIN    : %-28s p1 section %.2f mm" % (r["part"], r["thin_mm"]))
    return rows, len(big)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt print", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl", help="an STL, or a directory with --audit")
    ap.add_argument("--audit", action="store_true",
                    help="walk the given directory and check every printed STL")
    ap.add_argument("--skip-dirs", default=",".join(DEFAULT_SKIP_DIRS),
                    help="comma-separated folder names to skip when auditing")
    ap.add_argument("--printer", default="X1C", choices=sorted(PRINTERS))
    ap.add_argument("--overhang", type=float, default=45.0,
                    help="max unsupported overhang angle from vertical (deg)")
    ap.add_argument("--load-axis", help="main tensile load direction in model "
                                        "coords, e.g. 0,0,1")
    ap.add_argument("--keep-flat", help="only consider orientations with this "
                                        "model axis vertical (X/Y/Z)")
    ap.add_argument("--no-min-features", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--png")
    a = ap.parse_args(argv)

    if a.audit:
        rows, nbig = audit(a.stl, a.printer, a.overhang,
                           tuple(x.strip() for x in a.skip_dirs.split(",") if x.strip()))
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, ensure_ascii=False)
            print("\nwrote %s" % a.json)
        return 0 if nbig == 0 else 1

    load_axis = None
    if a.load_axis:
        load_axis = [float(x) for x in a.load_axis.replace(" ", "").split(",")]

    r = analyse(a.stl, a.printer, a.overhang, load_axis, a.keep_flat,
                not a.no_min_features)

    print("%s on %s" % (r["part"], r["printer"]))
    print("  as modelled       %s mm" % r["bbox_as_modelled_mm"])
    print("  usable volume     %s mm" % r["build_volume_usable_mm"])
    print("\n  %-12s %-20s %6s %9s %11s %7s"
          % ("orientation", "size (mm)", "fits", "overhang", "1st layer", "score"))
    for o in r["orientations"][:6]:
        print("  %-12s %-20s %6s %8.1f%% %10.0f %7s"
              % (o["orientation"], str(o["size_mm"]), "yes",
                 o["overhang_pct"], o["first_layer_mm2"], "%.2f" % o["score"]))
    if not r["orientations"]:
        print("  (no candidates: every orientation is excluded)")
    for o in r["excluded_orientations"][:6]:
        print("  %-12s %-20s %s" % (o["orientation"], str(o["size_mm"]),
                                    o["note"]))

    print()
    for c in r["checks"]:
        print("  [%s] %-18s %s -> %s" % ("PASS" if c["pass"] else "FAIL",
                                         c["name"], c["required"], c["got"]))

    if r["split"].get("required"):
        print("\n  SPLIT REQUIRED (positions are in model coordinates)")
        for p in r["split"]["plans"]:
            print("    cut along %s (%.1f mm) into %d pieces of %.1f mm at %s"
                  % (p["axis"], p["model_extent_mm"], p["pieces"],
                     p["piece_length_mm"], p["cut_positions_mm"]))
        if r["split"].get("resulting_piece_mm"):
            print("    each piece then measures %s mm"
                  % r["split"]["resulting_piece_mm"])
        print("    %s" % r["split"]["guidance"])

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        print("\nwrote %s" % a.json)
    if a.png:
        write_png(a.png, a.stl, r)
        print("wrote %s" % a.png)

    print("\n=== %s ===" % ("PASS" if r["pass"] else "FAIL"))
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

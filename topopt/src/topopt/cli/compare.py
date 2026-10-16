#!/usr/bin/env python3
"""Compare a parametric rebuild against the topology-optimisation result it came from.

    topopt compare TOPO_OUT_DIR rebuild.stl --study study.json --out DIR

Prints, and writes a PNG of:
  * geometric agreement -- what the optimum has that the rebuild lacks, and
    what the rebuild adds that the optimum did not want
  * structural agreement -- deflection and compliance of both shapes under the
    same load case

Why both: geometric overlap alone is misleading. A rebuild that overlapped the
optimum 46 % was measurably stiffer than one that overlapped it 81 %, because
overlap does not care *where* the missing material was. Only the FEA comparison
tells you whether the rebuild kept the load path.

The reference is the optimiser's *thresholded shape*, re-analysed as solid --
not the SIMP compliance printed during optimisation. Those are different
numbers: SIMP compliance includes intermediate-density material that you cannot
print, so comparing a solid rebuild against it is not like for like.
"""

import argparse
import json
import os
import sys

import numpy as np


try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from topopt.voxel import voxelize_stl, Grid          # noqa: E402
from topopt import backend as _backend                                   # noqa: E402


def load_optimum(topo_dir):
    """Find the optimiser's density field and its summary, in either layout.

    Generative (`geometry` schema): `<base>_density_phys.npy` + `<base>.json`.
    Legacy: `<base>_topo_density.npy` + `<base>_topo.json`.

    The summary is looked up from the density file's own prefix rather than by
    scanning for any `.json` -- the generative run writes a second one
    (`<base>_setup.json`) and a scan picks whichever sorts last.
    """
    names = sorted(os.listdir(topo_dir))
    dens_file = base = None
    for suffix in ("_density_phys.npy", "_density.npy"):
        for f in names:
            if f.endswith(suffix):
                dens_file, base = f, f[:-len(suffix)]
                break
        if dens_file:
            break
    if not dens_file:
        raise SystemExit(
            "no density field in %s -- expected <base>_density_phys.npy "
            "(geometry schema) or <base>_topo_density.npy (legacy)" % topo_dir)

    meta_path = os.path.join(topo_dir, base + ".json")
    if not os.path.exists(meta_path):
        raise SystemExit("found %s but no matching summary %s.json"
                         % (dens_file, base))
    with open(meta_path, "r", encoding="utf-8-sig") as fh:
        meta = json.load(fh)

    # The generative run saves the shape it actually analysed. Thresholding the
    # density instead would miss the preserve floor, the obstacle cut and the
    # island removal, so the comparison would not be against the exported STL.
    bin_path = os.path.join(topo_dir, base + "_binary.npy")
    binary = np.load(bin_path) if os.path.exists(bin_path) else None
    return np.load(os.path.join(topo_dir, dens_file)), meta, base, binary


def grid_of(meta):
    """-> (voxel_mm, origin_mm or None) from either summary layout."""
    info = (meta.get("setup") or {}).get("info") or {}
    if "voxel_mm" in info:
        o = info.get("origin_mm")
        return float(info["voxel_mm"]), (np.array(o, float) if o else None)
    if "voxel_mm" in meta:
        # The legacy summary never recorded the origin, which is why --origin
        # exists and why forgetting it reports 0 % overlap.
        return float(meta["voxel_mm"]), None
    raise SystemExit("summary JSON has no voxel size -- is it a topo_opt output?")


def optimum_stl(topo_dir, base):
    for suffix in ("_final.stl", "_voxel.stl"):
        p = os.path.join(topo_dir, base + suffix)
        if os.path.exists(p):
            return p
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt compare", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topo_dir", help="output directory from `topopt opt`")
    ap.add_argument("rebuild", help="the rebuilt part as STL")
    ap.add_argument("--study", help="study JSON to run FEA on both shapes")
    ap.add_argument("--out", default=".", help="where to write the PNG/JSON")
    ap.add_argument("--level", type=float, default=0.5)
    ap.add_argument("--origin", default=None,
                    help="grid origin of the optimisation domain, mm. Read from "
                         "the summary automatically for geometry-schema runs; "
                         "required for legacy ones, which did not record it.")
    _backend.add_cli_argument(ap)
    a = ap.parse_args(argv)
    _backend.apply_cli_choice(a)

    dens, meta, base, binary = load_optimum(a.topo_dir)
    h, origin = grid_of(meta)
    if a.origin:
        origin = np.array([float(v) for v in a.origin.split(",")], float)
    elif origin is None:
        raise SystemExit(
            "this summary does not record the grid origin, so --origin is "
            "required. Without it the rebuild is voxelised at the wrong place "
            "and the overlap reads 0 %. Use "
            "origin = bbox_centre - shape * voxel / 2 of the design domain.")
    print("optimum from %s (voxel %.3f mm, origin %s)"
          % (base, h, np.round(origin, 3).tolist()))

    opt = binary.astype(bool) if binary is not None else (dens >= a.level)
    reb = voxelize_stl(a.rebuild, h=h, origin=origin, shape=dens.shape).solid

    inter = int((opt & reb).sum())
    missing = int((opt & ~reb).sum())
    extra = int((reb & ~opt).sum())
    vox = h ** 3
    print("geometry (voxel %.3f mm)" % h)
    print("  optimum          %7d voxels  %9.0f mm3" % (opt.sum(), opt.sum() * vox))
    print("  rebuild          %7d voxels  %9.0f mm3  (%+.1f %% mass)"
          % (reb.sum(), reb.sum() * vox,
             100 * (reb.sum() - opt.sum()) / max(opt.sum(), 1)))
    print("  overlap          %7d voxels  (%.1f %% of the optimum)"
          % (inter, 100 * inter / max(opt.sum(), 1)))
    print("  missing          %7d voxels  (optimum wanted it, rebuild has not)"
          % missing)
    print("  extra            %7d voxels  (rebuild added it, optimum did not)"
          % extra)

    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, "topo_compare.png")
    _plot(dens, opt, reb, h, origin, png)
    print("  wrote %s" % png)

    result = {"voxel_mm": h, "optimum_voxels": int(opt.sum()),
              "rebuild_voxels": int(reb.sum()), "overlap": inter,
              "missing": missing, "extra": extra}

    if a.study:
        from topopt.cli.fea import run_study
        from topopt.loadcase import load_case_from_json
        opt_stl = optimum_stl(a.topo_dir, base)
        if not opt_stl:
            print("\nno %s_final.stl or %s_voxel.stl in the topo directory; "
                  "skipping FEA comparison" % (base, base))
        else:
            print("\nstructure (same study, same voxel size)")
            rows = []
            for tag, part in (("optimum shape", opt_stl), ("rebuild", a.rebuild)):
                study = load_case_from_json(a.study)
                study.part = os.path.abspath(part)
                res = run_study(study, None, None, quiet=True, png=False, voxel=h)
                for case, _r, s in res:
                    rows.append((tag, case.name, s["max_displacement_mm"],
                                 s["compliance_Nmm"], s["safety_factor_p99_5"]))
            print("  %-14s %-24s %10s %12s %8s"
                  % ("shape", "case", "defl mm", "compliance", "SF"))
            for t, c, d, comp, sf in rows:
                print("  %-14s %-24s %10.3f %12.2f %8.2f" % (t, c, d, comp, sf))
            by_case = {}
            for t, c, d, comp, sf in rows:
                by_case.setdefault(c, {})[t] = (d, comp)
            print()
            for c, v in by_case.items():
                if "optimum shape" in v and "rebuild" in v:
                    o, r = v["optimum shape"], v["rebuild"]
                    ratio = r[1] / max(o[1], 1e-9)
                    print("  %-24s rebuild is %.2fx the optimum's compliance "
                          "(%.0f %% %s)"
                          % (c, ratio, abs(100 * (ratio - 1)),
                             "softer" if ratio > 1 else "stiffer"))
                    if ratio > 1.25:
                        print("      -> the rebuild lost the load path. Look at "
                              "MISSING in the PNG: material the optimum put "
                              "near the outer surfaces matters most, because "
                              "bending stiffness goes with the square of the "
                              "distance from the neutral axis. Widen those "
                              "members or move them outward and re-check.")
            result["fea"] = [{"shape": t, "case": c, "deflection_mm": d,
                              "compliance_Nmm": comp, "safety_factor": sf}
                             for t, c, d, comp, sf in rows]

    with open(os.path.join(a.out, "topo_compare.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return 0


def _plot(dens, opt, reb, h, origin, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Project along the thinnest axis so the comparison is readable.
    axis = int(np.argmin(dens.shape))
    plane = [a for a in range(3) if a != axis]
    ext = [origin[plane[0]], origin[plane[0]] + dens.shape[plane[0]] * h,
           origin[plane[1]], origin[plane[1]] + dens.shape[plane[1]] * h]

    panels = [(dens.max(axis=axis), "optimiser density"),
              (reb.sum(axis=axis), "rebuild"),
              ((opt & ~reb).sum(axis=axis), "MISSING from rebuild"),
              ((reb & ~opt).sum(axis=axis), "EXTRA in rebuild")]
    fig, axes = plt.subplots(1, 4, figsize=(20, 4.4))
    for ax, (m, t) in zip(axes, panels):
        im = ax.imshow(np.asarray(m, float).T, origin="lower", extent=ext,
                       cmap="inferno", aspect="equal")
        ax.set_title(t, fontsize=10)
        ax.set_xlabel("XYZ"[plane[0]] + " (mm)")
        ax.set_ylabel("XYZ"[plane[1]] + " (mm)")
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle("rebuild vs topology optimum (projected along %s)"
                 % "XYZ"[axis], fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())

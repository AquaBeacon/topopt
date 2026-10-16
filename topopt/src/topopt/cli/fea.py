#!/usr/bin/env python3
"""Check a printed part's stiffness and strength with voxel FEA.

    topopt fea study.json [--out OUTDIR] [--elements N] [--quiet]
    topopt fea study.json --converge [--voxel H]   # 3-size mesh convergence
    topopt fea part.stl --template > study.json   # start a study file

The study file schema is documented in topopt/loadcase.py. Every case is
solved independently and the part passes only if every case passes.

Exit code 0 = all cases pass, 1 = at least one fails, 2 = the run could not be
set up (bad file, empty region, no supports...).
"""

import argparse
import json
import os
import sys
import time

import numpy as np


# Long runs are common here; without line buffering a redirected run shows an
# empty log for tens of minutes and looks hung.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from topopt.loadcase import load_case_from_json, build_bcs, total_load  # noqa: E402
from topopt.materials import get_material, material_model                               # noqa: E402
from topopt.report import summarise, write_png                          # noqa: E402
from topopt.solve import solve_cases                                    # noqa: E402
from topopt import backend as _backend                                   # noqa: E402
from topopt.voxel import voxelize_stl, largest_connected                # noqa: E402
from topopt.ccx import mesh_convergence                                 # noqa: E402

# Above this many solid elements, halving the voxel size again (for the fine
# grid of --converge) is a real time commitment, not a formality. Spec A3
# says warn and keep going -- the user asked for convergence, so silently
# refusing the fine solve would answer a different question than the one
# they asked.
_CONVERGE_FINE_ELEMENT_WARNING = 400_000

TEMPLATE = {
    "part": "PART.stl",
    "material": "PETG",
    "build_axis": "Z",
    "target_elements": 60000,
    "safety_factor": 2.0,
    "max_deflection_mm": 1.0,
    "regions": {
        "mount": {"type": "face", "axis": "Z", "side": "min", "depth": 3.0},
        "load": {"type": "face", "axis": "Z", "side": "max", "depth": 3.0}
    },
    "cases": [{
        "name": "describe the real worst case here",
        "supports": [{"region": "mount", "fix": "xyz"}],
        "loads": [{"region": "load", "force": [0, 0, -50]}],
        "gravity": {"direction": [0, 0, -1], "g_factor": 3.0}
    }]
}


def run_study(study, outdir=None, elements=None, quiet=False, png=True,
              voxel=None, isotropic=False, solver=None):
    path = study.part_path
    if not path or not os.path.exists(path):
        raise SystemExit("part STL not found: %r" % path)

    if isotropic:
        mat, ortho = get_material(study.material), None
        mat_info = material_model(study.material, isotropic=True)[2]
    else:
        study.require_build_axis("orthotropic analysis")
        # Deliberately *not* derated by infill/walls. Both material models
        # report a fully-solid part (spec 12.2); derating only the orthotropic
        # one would confound every isotropic-vs-orthotropic comparison with a
        # stiffness knock-down nobody asked for.
        mat, ortho, mat_info = material_model(study.material)
    target = elements or study.target_elements
    t0 = time.time()
    grid = voxelize_stl(path, h=voxel or study.resolution_mm,
                        target_elements=target)
    if grid.n_solid == 0:
        raise SystemExit("voxelisation produced no material -- is the STL "
                         "watertight and in mm?")
    grid = largest_connected(grid)
    if not quiet:
        print("voxelised %s: %d solid voxels at %.3f mm (%.1f s)"
              % (os.path.basename(path), grid.n_solid, grid.h, time.time() - t0))
        # Iteration count grows with the grid, so cost is worse than linear.
        # Say so before someone waits 20 minutes wondering if it hung.
        if grid.n_solid > 120000:
            print("  NOTE  %d elements x %d case(s) is a long run (tens of "
                  "minutes). Thin-walled parts are expensive in voxel FEA "
                  "because resolving the wall sets the voxel size for the "
                  "whole part. Iterate at a coarser --voxel and refine once."
                  % (grid.n_solid, len(study.cases)))

    # A finished part has no preserve to clip to, but "it stands on the plate"
    # still means the same plane, so `"contact": "ground"` has to work here too
    # -- otherwise a study cannot be re-analysed with the same supports it was
    # optimised under.
    from topopt.designspace import ground_mask                     # noqa: E402
    g_axis = int(getattr(study, "ground_axis", study.build_axis))
    g_mask, _g_mm = ground_mask(grid.solid, g_axis, grid.h, grid.origin)

    results = []
    bcs = [build_bcs(grid, case, study.regions, mat,
                     infill=study.infill, walls=study.walls,
                     ground=g_mask, ground_axis=g_axis) for case in study.cases]
    # Every case against one operator: cases that share supports share the
    # multigrid hierarchy, and a GPU (when present) sees them as one block.
    rs = solve_cases(
        grid, mat.E_effective(across_layers=False), mat.nu, bcs,
        sigma_xy=mat.sigma_xy, sigma_z=mat.sigma_z,
        build_axis=study.build_axis, ortho=ortho,
        raster_axis=study.raster_axis, solver=solver)
    for case, (fixed, force), r in zip(study.cases, bcs, rs):
        tl = total_load(force)
        need = study.required_safety_factor(case)
        s = summarise(r, grid, mat, need,
                      study.max_deflection_mm, study.build_axis)
        s["case"] = case.name
        s["load_class"] = case.load_class
        s["required_safety_factor"] = need
        s["total_applied_load_N"] = [round(float(v), 3) for v in tl]
        s["resultant_load_N"] = round(float(np.linalg.norm(tl)), 3)
        s["material"] = mat.name
        s["material_model"] = mat_info
        s["solve_seconds"] = r.solve_seconds
        s["cg_iterations"] = r.iterations
        s["solver"] = r.solver
        results.append((case, r, s))

        if not quiet:
            print("\n--- case: %s ---" % case.name)
            print("  applied load        %.2f N %s"
                  % (s["resultant_load_N"], s["total_applied_load_N"]))
            print("  max deflection      %.3f mm at %s"
                  % (s["max_displacement_mm"],
                     [round(v, 1) for v in s["max_displacement_at"]]))
            print("  von Mises           max %.2f MPa / p99.5 %.2f MPa"
                  % (s["von_mises_max_MPa"], s["von_mises_p99_5_MPa"]))
            print("  safety factor       %.2f (worst) / %.2f (p99.5)   "
                  "[need >= %.2f, %s load]"
                  % (s["min_safety_factor"], s["safety_factor_p99_5"],
                     need, case.load_class))
            for c in s["checks"]:
                mark = "PASS" if c["pass"] else "FAIL"
                print("  [%s] %-16s %s -> %s" % (mark, c["name"],
                                                 c["required"], c["got"]))
                if c.get("fix"):
                    print("         %s" % c["fix"])
            if s["stress_concentration_ratio"] > 3:
                print("  NOTE  max/p99.5 stress ratio is %.1f -- the peak is "
                      "probably a stair-step or clamp artefact. Check the "
                      "hotspot list before trusting it."
                      % s["stress_concentration_ratio"])
            for w in s["warnings"]:
                print("  WARN  %s" % w)
            print("  hotspots (worst first):")
            for h in s["hotspots"]:
                print("    at %-22s SF %s" % (h["pos"], h["safety_factor"]))

    if outdir:
        os.makedirs(outdir, exist_ok=True)
        base = os.path.splitext(os.path.basename(path))[0]
        summary = [s for _, _, s in results]
        with open(os.path.join(outdir, base + "_fea.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"schema": "topopt/fea/1",
                       "part": path, "material": mat.name,
                       "pass": all(s["pass"] for s in summary),
                       "cases": summary}, f, indent=2, ensure_ascii=False)
        if png:
            for i, (case, r, s) in enumerate(results):
                p = os.path.join(outdir, "%s_fea_%d.png" % (base, i + 1))
                write_png(p, grid, r, "%s -- %s (SF %.2f, max %.3f mm)"
                          % (base, case.name, s["safety_factor_p99_5"],
                             s["max_displacement_mm"]))
        if not quiet:
            print("\nwrote %s" % outdir)

    return results


def run_converge(study, outdir=None, elements=None, quiet=False, png=True,
                 voxel=None, isotropic=False):
    """Spec A3 / 15.2: solve coarse/medium/fine and judge convergence per case.

    `run_study` only ever reports "N elements across the thinnest section" --
    a proxy, not a measurement. This solves the study three times (voxel sizes
    2H / H / H/2) and checks whether the answer is still moving when the mesh
    is refined, the way `topopt ccx --converge` already does for the tet path.

    H is `voxel` if given. Otherwise H is *not* guessed: a normal run is
    solved first (letting `run_study` derive h from --elements/target_elements
    exactly as an un-converged `fea` run would), and its own voxel size is
    reused as H. Either way this costs three solves, never four.
    """
    path = study.part_path

    if voxel is not None:
        H = float(voxel)
        medium = run_study(study, outdir=None, elements=None, quiet=True,
                           png=False, voxel=H, isotropic=isotropic)
    else:
        medium = run_study(study, outdir=None, elements=elements, quiet=True,
                           png=False, voxel=None, isotropic=isotropic)
        H = medium[0][2]["voxel_mm"]

    sizes = {"coarse": 2.0 * H, "medium": H, "fine": H / 2.0}

    # Element count scales ~1/h^3, so halving h once more (medium -> fine)
    # multiplies the cell count by ~8x. Estimate from the medium run, which is
    # already solved, before committing to the fine one.
    base_n = medium[0][2]["elements"]
    est_fine_n = base_n * 8
    if not quiet and est_fine_n > _CONVERGE_FINE_ELEMENT_WARNING:
        print("  NOTE  fine mesh (%.3f mm) is an estimated ~%d solid elements "
              "(8x the %.3f mm grid's %d) -- this can take a long time; "
              "continuing because --converge was asked for"
              % (sizes["fine"], est_fine_n, sizes["medium"], base_n))
    if not quiet:
        print("mesh convergence: %.3f mm (coarse) / %.3f mm (medium) / "
              "%.3f mm (fine)" % (sizes["coarse"], sizes["medium"], sizes["fine"]))

    coarse = run_study(study, outdir=None, elements=None, quiet=True,
                       png=False, voxel=sizes["coarse"], isotropic=isotropic)
    # Only the finest run gets written out: it is the one downstream `topopt
    # report` should read, exactly like an un-converged `fea` run would.
    fine = run_study(study, outdir=outdir, elements=None, quiet=True,
                     png=png, voxel=sizes["fine"], isotropic=isotropic)

    case_names = [c.name for c, _r, _s in medium]
    if len(set(case_names)) != len(case_names):
        # Two cases sharing a name would collapse into one dict entry below,
        # and mesh_convergence would then compare two same-size runs against
        # each other and report a false "stopped moving".
        from topopt.schema import InputError
        raise InputError("study has duplicate case names: %r -- "
                         "each case must be named uniquely for --converge "
                         "to track it across the three mesh sizes"
                         % [n for n in case_names
                            if case_names.count(n) > 1])
    levels = [("coarse", sizes["coarse"], coarse),
             ("medium", sizes["medium"], medium),
             ("fine", sizes["fine"], fine)]

    per_case_runs = {name: [] for name in case_names}
    for level_name, h, results in levels:
        for case, _r, s in results:
            entry = {
                "level": level_name,
                "target_mm": round(float(h), 4),
                # Voxel FEA has no distinct node/element count worth quoting
                # twice; "elements" is the honest one (a node count here
                # would just be the element count relabelled).
                "elements": s["elements"],
                "max_disp_mm": s["max_displacement_mm"],
                # Both stress keys: mesh_convergence may read either name
                # depending on which version of ccx.py this is running
                # against (it is being extended concurrently).
                "max_stress_MPa": s["von_mises_max_MPa"],
                "p99_5_stress_MPa": s["von_mises_p99_5_MPa"],
                "safety_factor_p99_5": s["safety_factor_p99_5"],
                "min_safety_factor": s["min_safety_factor"],
            }
            if s.get("hotspots"):
                entry["hotspot_xyz_mm"] = s["hotspots"][0]["pos"]
            per_case_runs[case.name].append(entry)

    cases_out = {}
    for name in case_names:
        verdict = mesh_convergence(per_case_runs[name])
        verdict.pop("runs", None)     # already have it at the case level
        cases_out[name] = {"runs": per_case_runs[name], "verdict": verdict}

    conv_pass = all(cases_out[n]["verdict"].get("pass") for n in case_names)
    fine_sf = {case.name: s["safety_factor_p99_5"] for case, _r, s in fine}
    governing_case = min(fine_sf, key=lambda n: fine_sf[n])
    fine_strength_pass = all(s["pass"] for _c, _r, s in fine)
    ok = bool(conv_pass and fine_strength_pass)

    failing = [n for n in case_names if not cases_out[n]["verdict"].get("pass")]
    note = ("every case's answer stopped moving with the mesh"
            if not failing else
            "still moving with the mesh in: "
            + ", ".join("%s (%s)" % (n, cases_out[n]["verdict"].get("note"))
                        for n in failing))

    out = {
        "schema": "topopt/fea-convergence/1",
        "part": path,
        "voxel_mm": [sizes["coarse"], sizes["medium"], sizes["fine"]],
        "cases": cases_out,
        "governing_case": governing_case,
        "pass": ok,
        "note": note,
    }

    if outdir:
        os.makedirs(outdir, exist_ok=True)
        base = os.path.splitext(os.path.basename(path))[0]
        p = os.path.join(outdir, base + "_fea_convergence.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        if not quiet:
            print("wrote %s" % p)

    if not quiet:
        print("\n%-24s %10s %10s %10s   %8s %8s %8s   verdict"
              % ("case", "d@%.2f" % sizes["coarse"], "d@%.2f" % sizes["medium"],
                 "d@%.2f" % sizes["fine"], "SF@%.2f" % sizes["coarse"],
                 "SF@%.2f" % sizes["medium"], "SF@%.2f" % sizes["fine"]))
        for name in case_names:
            runs = per_case_runs[name]
            d = [r["max_disp_mm"] for r in runs]
            sf = [r["safety_factor_p99_5"] for r in runs]
            mark = "PASS" if cases_out[name]["verdict"].get("pass") else "FAIL"
            print("%-24s %10.4f %10.4f %10.4f   %8.2f %8.2f %8.2f   %s"
                  % (name[:24], d[0], d[1], d[2], sf[0], sf[1], sf[2], mark))
        print("governing case: %s (SF p99.5 %.2f at %.3f mm)"
              % (governing_case, fine_sf[governing_case], sizes["fine"]))
        print("mesh convergence: %s -- %s"
              % ("PASS" if conv_pass else "FAIL", note))

    return ok, out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt fea", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study", help="study JSON (or an STL with --template)")
    ap.add_argument("--out", help="output directory for JSON + PNG")
    ap.add_argument("--elements", type=int, help="override target element count")
    ap.add_argument("--voxel", type=float,
                    help="voxel size in mm (overrides --elements; this is what "
                         "the mesh-resolution check suggests)")
    ap.add_argument("--template", action="store_true",
                    help="print a starter study file for the given STL")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    _backend.add_cli_argument(ap)
    ap.add_argument("--isotropic", action="store_true",
                    help="use the legacy two-constant isotropic model "
                         "instead of the orthotropic card")
    ap.add_argument("--converge", action="store_true",
                    help="solve coarse/medium/fine voxel sizes (2H / H / H/2) and "
                         "judge mesh convergence per case, instead of "
                         "reporting one solve's numbers as final")
    a = ap.parse_args(argv)
    _backend.apply_cli_choice(a)

    if a.template:
        t = dict(TEMPLATE)
        t["part"] = os.path.basename(a.study)
        print(json.dumps(t, indent=2))
        return 0

    study = load_case_from_json(a.study)
    if a.converge:
        ok, _conv = run_converge(study, a.out, a.elements, a.quiet,
                                 not a.no_png, a.voxel, isotropic=a.isotropic)
    else:
        results = run_study(study, a.out, a.elements, a.quiet, not a.no_png,
                            a.voxel, isotropic=a.isotropic)
        ok = all(s["pass"] for _, _, s in results)
    if not a.quiet:
        print("\n=== %s ===" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:                          # pragma: no cover
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(2)

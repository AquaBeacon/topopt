#!/usr/bin/env python3
"""Validate a generative-design setup and export a 3D preview -- before solving.

    topopt setup study.json --out DIR
    topopt setup study.json --out DIR --voxel 1.0
    topopt setup study.json --out DIR --voxel 10.0 --iters 80

Reads the `geometry` block of a study file (schema in topopt/designspace.py and
references/design-space.md), voxelises every body onto one shared grid, runs the
setup checks, and writes:

    DIR/<base>_setup.png    orthographic + isometric view, colour-coded
    DIR/<base>_setup.scad   the same in 3D with colour, openable in OpenSCAD
    DIR/<base>_setup.json   the full validation report
    DIR/<base>_preserve.stl, _obstacle.stl, _design_space.stl, _setup.stl

It also times one solve on the grid it just built and reports what `--iters`
iterations would cost. That is the number the voxel size is really chosen
against, and it is cheap here and expensive to discover later. `--no-estimate`
skips it.

Checks: preserve-obstacle intersection, preserve outside the design space,
preserve bodies that no material could ever join, loads or supports that land on
free material instead of preserve, obstacle clearances too fine for the grid,
and bodies that voxelised to nothing.

Exit code 0 = the setup is valid, 1 = it is not, 2 = it could not be read.
"""

import argparse
import os
import sys


try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from topopt.designspace import build_setup, validate                 # noqa: E402
from topopt import backend as _backend                                   # noqa: E402
from topopt.loadcase import load_case_from_json                      # noqa: E402
from topopt.runtime import estimate, format_estimate                 # noqa: E402
from topopt.setup_preview import export_setup, print_report          # noqa: E402


def prepare(study, elements=None, voxel=None, verbose=True, strict_bc=None):
    """Build the shared grid and validate it. -> (setup, report)"""
    design = dict(study.raw.get("design", {}))
    if strict_bc is None:
        strict_bc = bool(design.get("strict_bc", False))
    setup = build_setup(study, target_elements=elements, voxel_mm=voxel,
                        verbose=verbose)
    report = validate(setup, study, strict_bc=strict_bc)
    return setup, report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt setup", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study")
    ap.add_argument("--out", default="setup_out")
    ap.add_argument("--elements", type=int, help="target voxel count")
    ap.add_argument("--voxel", type=float, help="voxel size in mm")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--iters", type=int, default=80, metavar="N",
                    help="iterations to cost the runtime estimate for "
                         "(default 80)")
    ap.add_argument("--no-estimate", action="store_true",
                    help="skip the timed solve behind the runtime estimate")
    ap.add_argument("--isotropic", action="store_true",
                    help="cost the estimate with the isotropic material model")
    ap.add_argument("--quiet", action="store_true")
    _backend.add_cli_argument(ap)
    a = ap.parse_args(argv)
    _backend.apply_cli_choice(a)

    study = load_case_from_json(a.study)
    setup, report = prepare(study, a.elements, a.voxel, not a.quiet)

    # The voxel size is chosen here and paid for later. Costing it now is one
    # solve; not costing it is how a study gets started at a resolution nobody
    # priced (see topopt/runtime.py).
    if not a.no_estimate and report["ok"]:
        est = estimate(setup, study, iters=a.iters, isotropic=a.isotropic)
        report["info"]["runtime_estimate"] = est
        if not a.quiet:
            print("\nruntime  %s" % format_estimate(est))
            if est.get("warning"):
                print("  WARNING  %s" % est["warning"])

    base = os.path.splitext(os.path.basename(a.study))[0]
    paths = export_setup(setup, study, a.out, base, png=not a.no_png)
    if not a.quiet:
        print_report(report)
        print("\nwrote %s" % a.out)
        for k in ("png", "scad"):
            if k in paths:
                print("  look at %s" % paths[k])
        if report["ok"]:
            print("\nNEXT: topopt opt %s --out DIR" % a.study)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:                              # pragma: no cover
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(2)

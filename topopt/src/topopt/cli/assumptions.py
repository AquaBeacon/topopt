#!/usr/bin/env python3
"""`topopt assumptions` — write the 前提条件 page for a study.

    topopt assumptions study.json --out DIR
    topopt assumptions study.json --out DIR --no-setup   # study file only

Produces `assumptions.md` and `assumptions.json`: what the part is for, every
load case and the use case it stands for, the material card and where its
numbers came from, the machine that has to print it, how much of the shape was
fixed before the optimiser ran, what counts as a pass — and a derived list of
what the study does *not* establish.

By default it builds the design space first, so the volumes, the bounding box
and the fixed load/support positions are measured rather than restated from the
study file. `--no-setup` skips that when you only want the declared inputs.

Exit code 0 always, unless the study cannot be read (2). This command reports
inputs; it does not judge them, and a study with problems still needs its
assumptions written down — arguably more than one without.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt import assumptions as A                              # noqa: E402
from topopt.loadcase import load_case_from_json                  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt assumptions", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study")
    ap.add_argument("--out", default="assumptions_out")
    ap.add_argument("--no-setup", action="store_true",
                    help="do not build the design space; report only what the "
                         "study file declares")
    ap.add_argument("--isotropic", action="store_true",
                    help="describe the legacy two-constant material model")
    ap.add_argument("--elements", type=int)
    ap.add_argument("--voxel", type=float)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    study = load_case_from_json(a.study)
    setup = report = None
    why_no_setup = None
    if not a.no_setup:
        from topopt.cli.setup import prepare
        try:
            # A setup that fails validation still yields real volumes and real
            # fixed points, and its errors belong on this page rather than
            # being the reason the page does not exist.
            setup, report = prepare(study, a.elements, a.voxel, verbose=False)
        except Exception as exc:                                  # noqa: BLE001
            # Plenty of legitimate studies have no design space at all: a
            # `topopt fea` study analyses an STL that already exists. Its
            # loads, material and requirements are exactly what this page is
            # for, and crashing on it would make the page unavailable for the
            # studies that need no optimisation. Say what was skipped and
            # carry on -- the design-space sections simply do not appear.
            why_no_setup = "%s: %s" % (type(exc).__name__, exc)

    paths = A.write(a.out, study, setup, report, isotropic=a.isotropic)
    if not a.quiet:
        print("wrote %s" % paths["md"])
        print("      %s" % paths["json"])
        if why_no_setup:
            print("\nnote: the design space was not built (%s), so this page "
                  "covers the declared inputs only -- no volumes, no build "
                  "volume check, no fixed-point list." % why_no_setup)
        if report is not None and not report.get("ok"):
            print("\nnote: the setup does NOT validate (%d error(s)); they are "
                  "listed at the end of the page." % len(report["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

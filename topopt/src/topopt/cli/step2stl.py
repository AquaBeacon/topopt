"""STEP (planar BREP) -> STL, with no CAD kernel.

    topopt step2stl part.step out.stl

Cylindrical faces are approximated by their bounding polygon, so the result is
good for looking at and for measuring a bounding box, but it is NOT watertight.
Take hole diameters and radii from the analytic entities instead:

    grep -E 'CYLINDRICAL_SURFACE|CIRCLE' part.step
"""

import argparse
import sys

import numpy as np

from topopt.stepio import read_step_triangles, write_binary_stl


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt step2stl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step")
    ap.add_argument("stl")
    a = ap.parse_args(argv)

    tris, skipped = read_step_triangles(a.step)
    print("faces=%d skipped=%d" % (len(tris), skipped))
    if not tris:
        print("error: no planar faces found -- is this a real BREP file?",
              file=sys.stderr)
        return 2
    write_binary_stl(a.stl, tris)
    v = np.array([p for _, x, y, z in tris for p in (x, y, z)])
    print("bbox min", v.min(0).round(3), "max", v.max(0).round(3),
          "size", (v.max(0) - v.min(0)).round(3))
    return 0


if __name__ == "__main__":
    sys.exit(main())

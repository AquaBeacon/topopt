"""Real support area for a part printed in its modelled orientation.

    topopt overhang part.stl [--angle 45] [--axis Z]

`topopt print` counts the bed-contact face itself as overhang (its normal
points straight down), which can read as "24 % overhang" for a part that in
fact needs no support at all. This separates the two.
"""

import argparse
import sys

from topopt.geom import load_stl, support_area


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt overhang", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl")
    ap.add_argument("--angle", type=float, default=45.0,
                    help="max unsupported overhang, degrees from horizontal")
    ap.add_argument("--axis", default="Z", help="build direction (X/Y/Z)")
    ap.add_argument("--json", help="also write the numbers here")
    a = ap.parse_args(argv)

    axis = {"X": 0, "Y": 1, "Z": 2}[a.axis.upper()]
    r = support_area(load_stl(a.stl), axis=axis, angle=a.angle)

    print("%s" % a.stl)
    print("  size            %.2f x %.2f x %.2f mm" % tuple(r["size_mm"]))
    print("  volume          %.2f cm3   (solid PLA %.1f g)"
          % (r["volume_mm3"] / 1000, r["volume_mm3"] * 1.24e-3))
    print("  surface area    %.1f mm2" % r["area_mm2"])
    print("  bed contact     %.1f mm2  (%.1f %%)" % (r["bed_mm2"], r["bed_pct"]))
    print("  NEEDS SUPPORT   %.1f mm2  (%.1f %%)" % (r["support_mm2"], r["support_pct"]))
    if "support_bbox" in r:
        lo, hi = r["support_bbox"]
        print("    located at  x %.1f..%.1f  y %.1f..%.1f  z %.1f..%.1f"
              % (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))
        print("    shallowest  %.0f deg from horizontal (limit %.0f)"
              % (r["shallowest_deg"], a.angle))

    if a.json:
        import json
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2)
        print("  wrote %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

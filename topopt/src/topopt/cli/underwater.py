"""`topopt underwater-loads` — what water does to a part, in numbers.

What this is for
----------------
Three different things happen to a submerged part and they are easy to mix up:
it gets squeezed (pressure), it gets lighter (buoyancy), and if the water is
moving it gets pushed (drag). This prints all three, and — more usefully —
tells you which of them your part actually has to care about.

  topopt underwater-loads part.stl --depth 10000 --velocity 1.0
  topopt underwater-loads part.stl --depth 3000 --sealed      # a housing
  topopt underwater-loads part.stl --velocity 2.0 --cd cylinder

The one that catches people
---------------------------
`--sealed` is off by default, and that is deliberate. On an **open frame**
water reaches both sides of every member, so the pressure very nearly cancels
and the part does not feel a crushing load at all. Applying pressure to an
open frame as if it were a sealed housing is the classic way to conclude that
a perfectly sound bracket needs to be three times thicker. Pass `--sealed`
only for a genuinely closed hollow.

Honest limits
-------------
The drag figure is a screening number: silhouette area times a table drag
coefficient. It has no wake, no interference between members, no Reynolds
number. Use it to choose between two candidates, not to predict a force.
There is no wave loading, no added mass, and no allowance for the polymer
taking up water over months.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt import underwater as uw                               # noqa: E402
from topopt.materials import get_material                         # noqa: E402
from topopt.voxel import voxelize_stl                             # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="topopt underwater-loads", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl")
    ap.add_argument("--material", default="PLA")
    ap.add_argument("--depth", type=float, default=0.0,
                    help="depth of the part's top below the surface, mm")
    ap.add_argument("--velocity", type=float, default=0.0,
                    help="water speed, m/s")
    ap.add_argument("--cd", default="open_frame",
                    help="drag coefficient, or one of: %s"
                         % ", ".join(sorted(uw.CD)))
    ap.add_argument("--sealed", action="store_true",
                    help="the part is a closed hollow, so pressure does NOT "
                         "cancel across its walls. Off by default: an open "
                         "frame treated as a pressure vessel gets three times "
                         "thicker for nothing.")
    ap.add_argument("--seawater", action="store_true")
    ap.add_argument("--submerged", type=float, default=1.0,
                    help="fraction submerged, 0-1 (default fully under)")
    ap.add_argument("--elements", type=int, default=60000)
    ap.add_argument("--voxel", type=float)
    ap.add_argument("--out", help="write the JSON here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    from topopt.schema import InputError
    for name, value in (("depth", a.depth), ("velocity", a.velocity)):
        if not math.isfinite(value) or value < 0:
            raise InputError("--%s must be finite and non-negative" % name)
    if not math.isfinite(a.submerged) or not 0 <= a.submerged <= 1:
        raise InputError("--submerged must be between 0 and 1")
    if a.voxel is not None and (not math.isfinite(a.voxel) or a.voxel <= 0):
        raise InputError("--voxel must be finite and positive")
    if a.elements <= 0:
        raise InputError("--elements must be positive")

    # Usage errors first: telling someone their file is missing when the real
    # problem is that they asked for nothing sends them looking in the wrong
    # place.
    if a.depth <= 0 and a.velocity <= 0:
        raise SystemExit(
            "nothing to compute: give --depth (pressure and buoyancy) or "
            "--velocity (drag), or both.")
    if not os.path.exists(a.stl):
        raise FileNotFoundError("part STL not found: %s" % a.stl)
    cd = a.cd
    if cd not in uw.CD:
        try:
            cd = float(cd)
        except ValueError:
            raise SystemExit("--cd must be a number or one of: %s"
                             % ", ".join(sorted(uw.CD)))
        if not math.isfinite(cd) or cd <= 0:
            raise InputError("--cd must be finite and positive")

    mat = get_material(a.material)
    grid = voxelize_stl(a.stl, h=a.voxel, target_elements=a.elements)
    if grid.n_solid == 0:
        raise SystemExit("voxelisation produced no material -- is the STL "
                         "watertight and in mm?")

    out = uw.report(grid, grid.solid, mat.density, a.depth, a.velocity,
                    cd, a.sealed, a.seawater, a.submerged)
    out["stl"] = a.stl
    out["material"] = mat.name
    out["voxel_mm"] = round(grid.h, 4)
    out["schema"] = "topopt/underwater/1"

    if not a.quiet:
        b = out["buoyancy"]
        print("\n%s in %s at %.3f mm voxel"
              % (os.path.basename(a.stl), out["water"], grid.h))
        print("\nbuoyancy")
        print("  volume            %10.0f mm3" % b["volume_mm3"])
        print("  displaced         %10.0f mm3" % b["displaced_volume_mm3"])
        print("  dry weight        %10.3f N" % b["dry_weight_N"])
        print("  buoyant force     %10.3f N" % b["buoyant_force_N"])
        print("  net weight in water %8.3f N  (%.0f %% of dry)"
              % (b["net_weight_in_water_N"],
                 100 * (b["weight_fraction_of_dry"] or 0)))
        if b["net_weight_in_water_N"] < 0:
            print("  NOTE  this part floats: every gravity case changes sign.")
        if "hydrostatic" in out:
            h = out["hydrostatic"]
            print("\nhydrostatic pressure")
            print("  max pressure      %10.5f MPa" % h["max_pressure_MPa"])
            print("  wetted area       %10.1f mm2" % h["wetted_area_mm2"])
            print("  sealed            %10s" % h["sealed"])
            print("  %s" % h["note"])
        if "drag" in out:
            d = out["drag"]
            print("\ndrag at %.2f m/s (SIMPLIFIED MODEL)" % a.velocity)
            print("  %-6s %14s %12s" % ("flow", "area mm2", "drag N"))
            for e in d["per_axis"]:
                print("  %-6s %14.0f %12.3f"
                      % (e["flow_axis"], e["projected_area_mm2"],
                         e["drag_force_N"]))
            print("  worst: along %s at %.3f N" % (d["worst_axis"],
                                                   d["worst_drag_N"]))
            print("  %s" % d["per_axis"][0]["note"])
        print("\nassumptions")
        for s in out["assumptions"]:
            print("  - %s" % s)

    if a.out:
        os.makedirs(a.out, exist_ok=True)
        p = os.path.join(a.out, "underwater_loads.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        if not a.quiet:
            print("\nwrote %s" % p)
    # 0 is "computed", not "safe": this command reports loads, it does not
    # judge the part. Feed the numbers into a study and run `topopt fea`.
    return 0


if __name__ == "__main__":
    sys.exit(main())

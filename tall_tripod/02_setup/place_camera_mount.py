"""Put the DJI Osmo Action 6 mount where the tripod's interface used to be.

    python tall_tripod/02_setup/place_camera_mount.py      # run from topopt/

Reads   ../dji_osmo_action6_mount.stl   (the part as it is drawn, base on z = 0)
Writes  tall_tripod/02_setup/tall_camera_mount.stl

WHAT THIS IS
------------
v7 replaces the tripod's own camera head -- three fingers on a pedestal, drawn
in `tall_tripod.scad` -- with the mount designed for the child unit
(`mechanical/parts/child/exterior/printed/dji_osmo_action6_mount/`). That part
holds the case against a rear plate and takes the OEM screw through a
three-finger receiver; it does not hinge, and it is 104 x 79.45 x 84.5 mm
against the old head's 21 x 19 x 36.

Both parts use the same convention -- +Y is the camera front, X is the screw
axis, Z is up and is the build direction -- so placing it is one translation.

WHY +112 mm IN Z, AND NOT +110
------------------------------
The mount's screw axis is `axis_z` = 24.0 mm above its own base. The tripod's
whole reason to exist is the camera height (spec 5.3), every load region in
`tall_tripod_study.json` is written about `z_hinge` = 136, and the tipping
calculation in `tall_tripod.scad` is too. So the AXIS is the datum: 136 - 24 =
112, and the base plate then sits 2 mm above the pedestal face the optimiser
was told to attach to (z = 110).

That 2 mm is not a gap in the part. The analysed head's voxels are dropped by
the finish stage and a one-voxel bridge is kept where optimised material
touched them (`finish.build_part`); on the 3 mm analysis grid that bridge is
the cell spanning z = 111..114, which the base plate at 112 sits inside. The
same 2 mm is what `attach` deliberately held open under the old pedestal, and
`finish.bridge_mm` was already sized for it.

WHAT THIS SCRIPT DOES NOT DO
----------------------------
It does not check anything. `check_mount_interface.py` measures the shipped
part; this only moves triangles, and it moves ALL of them, so the STL it writes
is the source file exactly, 112 mm higher.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))            # topopt/
sys.path.insert(0, os.path.join(ROOT, "topopt", "src"))

from topopt.meshout import _write_binary_stl              # noqa: E402
from topopt.voxel import load_stl                         # noqa: E402

SOURCE = os.path.join(ROOT, "dji_osmo_action6_mount.stl")
TARGET = os.path.join(HERE, "tall_camera_mount.stl")

# The mount's own screw axis, from `axis_z` in dji_osmo_action6_mount.scad.
AXIS_Z_IN_PART = 24.0
# The tripod's hinge datum, from `z_hinge` in tall_tripod.scad.
Z_HINGE = 136.0
OFFSET = np.array([0.0, 0.0, Z_HINGE - AXIS_Z_IN_PART])


def main():
    tri = load_stl(SOURCE)
    if not len(tri):
        raise SystemExit("no triangles in %s" % SOURCE)
    lo, hi = tri.reshape(-1, 3).min(axis=0), tri.reshape(-1, 3).max(axis=0)
    if abs(lo[2]) > 1e-6:
        raise SystemExit(
            "the source mount is expected to sit on z = 0 (its base plate is "
            "the print bed face); this one starts at z = %.3f, so the offset "
            "below would not put the screw axis at z = %.1f" % (lo[2], Z_HINGE))
    _write_binary_stl(TARGET, tri + OFFSET)
    print("%s  ->  %s" % (os.path.basename(SOURCE), os.path.basename(TARGET)))
    print("  %d triangles, translated by %s" % (len(tri), tuple(OFFSET)))
    print("  extents  x %.2f..%.2f  y %.2f..%.2f  z %.2f..%.2f"
          % (lo[0], hi[0], lo[1], hi[1],
             lo[2] + OFFSET[2], hi[2] + OFFSET[2]))
    print("  screw axis at z = %.2f (tripod z_hinge %.2f)"
          % (AXIS_Z_IN_PART + OFFSET[2], Z_HINGE))


if __name__ == "__main__":
    main()

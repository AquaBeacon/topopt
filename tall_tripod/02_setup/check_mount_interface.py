"""Did the new camera mount survive the finish stage, and is it still bolt-able?

    python tall_tripod/02_setup/check_mount_interface.py \
        tall_tripod/04_print/tall_tripod.stl

Run from topopt/.  v7 replaced the tripod's own camera head with the child
unit's DJI Osmo Action 6 mount (`02_setup/tall_camera_mount.stl`, placed by
`place_camera_mount.py`).  `check_interface.py` measures the OLD head and its
numbers do not apply: that one hinges, opens its nut pocket towards +X and has
a 2.40 mm centre finger.  This one is fixed, opens towards -X and has a 2.70 mm
centre finger.

Four questions, answered with numbers rather than with a picture:

  1. nothing may have been clipped: the mount reaches z = 196.5 on an analysis
     domain that ends at 156, so the part is only whole if the finish grid grew
  2. the nut pocket must be reachable from -X -- a nut is pushed in, so every
     line across its mouth has to see daylight (lessons-learned #92). The probe
     is r <= 3.0 and not the pocket's own 4.1 mm inradius: at 4.1 it is
     measuring the rounding of the hexagon's wall, and it reports a pocket that
     is open as a blind hole.
  3. the pocket's WIDTH at three depths, which is the number that decides
     whether the part can be bolted up. A cut body rounds outward and a
     concave feature of an `add` body rounds inward, so this pocket comes out
     UNDERSIZE and tapered -- see the study's finish._voxel_mm_note.
  4. the finger and slot widths, and the screw bore, measured

Run it on the source mount as well as the shipped part. The source is the same
geometry with no finish stage in front of it, so the difference between the two
columns is what the finish stage did, and the measuring grid's own rounding
cancels.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "topopt", "src"))

from topopt.voxel import load_stl, voxelize_stl                   # noqa: E402

H = 0.125
MOUNT = os.path.join(HERE, "tall_camera_mount.stl")

# All from dji_osmo_action6_mount.scad, plus the +112 mm placement.
Z_AXIS = 136.0            # axis_z 24.0 + 112
NUT_FACE = -10.65         # `left`: the face the nut pocket opens on, towards -X
NUT_DEPTH = 4.2
PLATE_TOP = 120.0         # base_t 8 + 112
MOUNT_TOP = 196.5         # plate_top + 112


def runs_of(row, coord, h):
    """-> [(start_mm, end_mm)] of the True runs in `row`."""
    out, s = [], None
    for i, v in enumerate(row):
        if v and s is None:
            s = i
        if not v and s is not None:
            out.append((coord[s] - h / 2, coord[i - 1] + h / 2))
            s = None
    if s is not None:
        out.append((coord[s] - h / 2, coord[-1] + h / 2))
    return out


def main(paths):
    origin = np.array([-20.0, -16.0, 118.0])
    shape = (int(40 / H), int(32 / H), int(30 / H))
    X = origin[0] + (np.arange(shape[0]) + 0.5) * H
    Y = origin[1] + (np.arange(shape[1]) + 0.5) * H
    Z = origin[2] + (np.arange(shape[2]) + 0.5) * H
    xx, yy, zz = np.meshgrid(X, Y, Z, indexing="ij")
    r = np.hypot(yy, zz - Z_AXIS)

    for path in paths:
        print("\n== %s" % path)
        tri = load_stl(path)
        v = tri.reshape(-1, 3)
        print("   extents    : x %.2f..%.2f  y %.2f..%.2f  z %.2f..%.2f"
              % (v[:, 0].min(), v[:, 0].max(), v[:, 1].min(),
                 v[:, 1].max(), v[:, 2].min(), v[:, 2].max()))
        top = v[:, 2].max()
        print("   clipped?   : top of part %.2f mm, mount top %.2f mm  %s"
              % (top, MOUNT_TOP,
                 "(whole)" if top >= MOUNT_TOP - 0.5 else "<-- CLIPPED"))

        m = voxelize_stl(path, h=H, origin=origin, shape=shape).solid
        iy0 = int(round((0.0 - origin[1]) / H))
        iz = int(round((Z_AXIS - origin[2]) / H))

        # 2. nut pocket: reachable from -X? r <= 3.0, well inside the
        # hexagon's 4.1 mm inradius, so this asks "is there a membrane" and
        # not "how much did the wall round".
        pocket = ((r <= 3.0) & (xx >= NUT_FACE + 0.5)
                  & (xx <= NUT_FACE + NUT_DEPTH - 0.5))
        blocked = 0
        for iy, iz2 in zip(*np.nonzero(pocket.any(axis=0))):
            col = m[:, iy, iz2]
            last = np.flatnonzero(pocket[:, iy, iz2])[-1]
            if col[:last + 1].any():
                blocked += 1
        n = int(pocket.any(axis=0).sum())
        print("   nut pocket : %d of %d mouth lines blocked towards -X %s"
              % (blocked, n, "<-- BLIND HOLE" if blocked else "(open)"))

        # 3. and how WIDE is it, at the mouth, in the middle and at the back.
        # An M5 nut is 8.00 across flats nominal, 8.10 max, and 4.00 thick.
        near_y = np.abs(Y) < 6.0
        near_z = np.abs(Z - Z_AXIS) < 6.0
        for depth in (0.65, 2.15, 3.65):
            ix = int(round((NUT_FACE + depth - origin[0]) / H))
            fy = np.flatnonzero(~m[ix, :, iz] & near_y)
            fz = np.flatnonzero(~m[ix, iy0, :] & near_z)
            af = (Y[fy.max()] - Y[fy.min()]) if len(fy) else 0.0
            ac = (Z[fz.max()] - Z[fz.min()]) if len(fz) else 0.0
            print("     %4.2f mm deep : across flats %5.2f (CAD 8.20, nut "
                  "8.10 max)   across corners %5.2f (CAD 9.47)"
                  % (depth, af, ac))

        # 4. finger and slot widths across X at the axis height, off the bore
        iy = int(round((-5.0 - origin[1]) / H))
        got_runs = runs_of(m[:, iy, iz], X, H)
        print("   fingers    : %s"
              % "  ".join("%.2f..%.2f (%.2f)" % (a, b, b - a)
                          for a, b in got_runs))
        gaps = [(got_runs[i][1], got_runs[i + 1][0])
                for i in range(len(got_runs) - 1)]
        print("   slots      : %s"
              % "  ".join("%.2f..%.2f (%.2f)" % (a, b, b - a)
                          for a, b in gaps))

        # 5. the screw bore, across Z through the axis. Bounded to the fingers
        # (they end at axis + lug_r = 143) so the free air above the part is
        # not reported as a hole.
        ix = int(round((0.0 - origin[0]) / H))
        window = (Z > PLATE_TOP) & (Z < Z_AXIS + 7.0)
        holes = runs_of(~m[ix, iy0, :] & window, Z, H)
        print("   bore       : %s"
              % ("  ".join("%.2f..%.2f (%.2f)" % (a, b, b - a)
                           for a, b in holes) or "none <-- FILLED IN"))


if __name__ == "__main__":
    main(sys.argv[1:] or [os.path.join(ROOT, "tall_tripod", "04_print",
                                       "tall_tripod.stl")])

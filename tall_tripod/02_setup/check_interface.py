"""Is the nut side open, and does the case prong path clear?

SUPERSEDED FOR v7 (2026-09-10). This measures the head `tall_tripod.scad`
draws -- a hinge, nut pocket opening towards +X, 2.40 mm centre finger. v7
replaced it with the child unit's DJI Osmo Action 6 mount, which does not
hinge, opens towards -X and has a 2.70 mm centre finger, so every number below
is about a part that is no longer on the tripod. Use
`check_mount_interface.py` for the shipped file. Kept because the old head is
still in `tall_mount_solid.stl` / `tall_mount_cuts.stl` and this is what says
whether THOSE are right.

    python tall_tripod/02_setup/check_interface.py         tall_tripod/02_setup/tall_mount_finished.stl tall_tripod/04_print/tall_tripod.stl

Run from topopt/.  Takes the finished mount (or the shipped part) and
answers three questions with numbers rather than with a picture:

  1. the nut pocket must be reachable from +X  -- a nut is pushed in, so every
     cell of the pocket mouth has to see daylight along +X
  2. the male prong envelope must be empty     -- radius 7.5 about the hinge
     axis over the prong's x range, and the corridor it enters through
  3. the slot and finger widths, measured, not drawn
"""
import sys

import numpy as np

from topopt.voxel import voxelize_stl

Z_HINGE = 136.0
H = 0.125
# fin_nut_x1 in tall_tripod.scad -- the face the nut pocket opens on.
NUT_FACE = float(__import__("os").environ.get("NUT_FACE", "12.25"))


def grid(path, origin, shape):
    return voxelize_stl(path, h=H, origin=origin, shape=shape).solid


def main(paths):
    origin = np.array([-30.0, -16.0, 120.0])
    shape = (int(64 / H), int(32 / H), int(32 / H))
    X = origin[0] + (np.arange(shape[0]) + 0.5) * H
    Y = origin[1] + (np.arange(shape[1]) + 0.5) * H
    Z = origin[2] + (np.arange(shape[2]) + 0.5) * H
    xx, yy, zz = np.meshgrid(X, Y, Z, indexing="ij")
    r = np.hypot(yy, zz - Z_HINGE)

    for path in paths:
        m = grid(path, origin, shape)
        print("\n== %s" % path)

        # 1. nut pocket: the hexagon about the hinge axis, just inside the face
        pocket = (r <= 4.0) & (xx >= NUT_FACE - 3.0) & (xx <= NUT_FACE - 0.5)
        # everything at a larger x than a pocket cell, on the same (y, z) line
        blocked = 0
        for iy, iz in zip(*np.nonzero(pocket.any(axis=0))):
            col = m[:, iy, iz]
            first = np.flatnonzero(pocket[:, iy, iz])[0]
            if col[first:].any():
                blocked += 1
        n = int(pocket.any(axis=0).sum())
        print("   nut pocket : %d of %d mouth lines blocked towards +X %s"
              % (blocked, n, "<-- BLIND HOLE" if blocked else "(open)"))

        # 2. prong envelope and its entry corridor
        prong = (np.abs(xx) >= 1.5) & (np.abs(xx) <= 4.5) & (r <= 7.5)
        corridor = ((np.abs(xx) >= 1.5) & (np.abs(xx) <= 4.5)
                    & (np.abs(zz - Z_HINGE) <= 7.5) & (yy >= 0))
        print("   prong path : %.1f mm3 in the envelope, %.1f mm3 in the corridor"
              % ((m & prong).sum() * H ** 3, (m & corridor).sum() * H ** 3))

        # 3. widths across X at the hinge height, off the bore
        iy = int(round((-6.0 - origin[1]) / H))
        iz = int(round((Z_HINGE - origin[2]) / H))
        row = m[:, iy, iz]
        runs, s = [], None
        for i, v in enumerate(row):
            if v and s is None:
                s = i
            if not v and s is not None:
                runs.append((X[s] - H / 2, X[i - 1] + H / 2))
                s = None
        if s is not None:
            runs.append((X[s] - H / 2, X[-1] + H / 2))
        print("   x runs     : %s"
              % "  ".join("%.2f..%.2f (%.2f)" % (a, b, b - a) for a, b in runs))
        gaps = [(runs[i][1], runs[i + 1][0]) for i in range(len(runs) - 1)]
        print("   slots      : %s"
              % "  ".join("%.2f..%.2f (%.2f)" % (a, b, b - a) for a, b in gaps))


if __name__ == "__main__":
    main(sys.argv[1:])

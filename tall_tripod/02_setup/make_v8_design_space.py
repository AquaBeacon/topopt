"""The v8 design space: one cylinder under the camera mount.

    python tall_tripod/02_setup/make_v8_design_space.py   # run from topopt/

Reads   tall_camera_mount.stl   (the placed mount -- `place_camera_mount.py`)
Writes  tall_v8_design_space.stl

WHY THIS IS NOT IN `tall_tripod.scad`
-------------------------------------
Up to v7 the design space came out of `tall_tripod.scad`, which also drew the
camera head. v8's head is not drawn here at all -- it is somebody else's part,
`dji_osmo_action6_mount.stl` -- so the .scad has nothing left to say about the
top of this model, and its asserts (`z_hinge + finger_r <= ds_top`, "the mount
head is wider than the design space") describe a head that is no longer on the
tripod. Copying the mount's dimensions back into the .scad to keep those
asserts alive would put the same geometry in two files, which is the thing this
repository does not do.

So the design space is derived from **the mount itself**: the script reads the
placed STL and puts the cylinder's top at the mount's own base-plate top. Two
numbers are chosen here and nowhere else -- the radius and which face of the
plate to stop at -- and both are checked below.

WHAT THE SHAPE IS
-----------------
A cylinder, r = 120 mm, from the build plate (z = 0) to the TOP of the mount's
base plate. Two consequences worth being explicit about:

  * The design space **overlaps the base plate** rather than stopping under it.
    Preserve wins inside the overlap, so no material is placed in the plate;
    what the overlap buys is a ring of free cells around the plate's edge, so
    the optimiser may buttress the plate from the side as well as from below.
    Stopping at the underside instead would leave preserve and design space
    touching on a single plane, and every attachment decision would then be
    made by which side of that plane a cell centre falls on.
  * **Nothing above the plate is free.** The volume the camera occupies, the
    room to turn the nut, the space between the rear plate and the front
    supports -- none of it needs declaring as `obstacle`, because none of it is
    in the design space to begin with. v7's `obstacle` (the +/-30 deg camera
    swing of a head that hinged, and the old thumbscrew) described a machine
    that no longer exists; it is simply gone in v8.

THE CHECKS
----------
`topopt setup` checks the design space against the printer. It does NOT check
the design space **plus preserve**, and in v8 the preserve is the taller half
of the part -- so the height that has to fit the machine is measured here.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "topopt", "src"))

from topopt.meshout import _write_binary_stl                      # noqa: E402
from topopt.printers import PRINTERS                              # noqa: E402
from topopt.voxel import load_stl                                 # noqa: E402

MOUNT = os.path.join(HERE, "tall_camera_mount.stl")
TARGET = os.path.join(HERE, "tall_v8_design_space.stl")

DS_R = 120.0              # half the plate, less the margin below
PRINT_MARGIN = 5.0        # brim, elephant-foot compensation, plate edge
PLATE_T = 8.0             # base_t in dji_osmo_action6_mount.scad
SEGMENTS = 96


def cylinder(r, z0, z1, n=SEGMENTS):
    """A closed cylinder as triangles. -> (m, 3, 3)"""
    a = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    ring = np.column_stack([r * np.cos(a), r * np.sin(a)])
    tris = []
    for i in range(n):
        j = (i + 1) % n
        p0 = [ring[i][0], ring[i][1], z0]
        p1 = [ring[j][0], ring[j][1], z0]
        q0 = [ring[i][0], ring[i][1], z1]
        q1 = [ring[j][0], ring[j][1], z1]
        tris += [[p0, p1, q1], [p0, q1, q0]]            # side
        tris += [[[0, 0, z0], p1, p0]]                  # bottom fan
        tris += [[[0, 0, z1], q0, q1]]                  # top fan
    return np.asarray(tris, float)


def main():
    v = load_stl(MOUNT).reshape(-1, 3)
    if not len(v):
        raise SystemExit("no triangles in %s" % MOUNT)
    plate_bottom = float(v[:, 2].min())
    mount_top = float(v[:, 2].max())
    ds_top = plate_bottom + PLATE_T

    bed = PRINTERS["X1C"]
    env_xy = min(bed.usable_mm[0], bed.usable_mm[1]) - 2 * PRINT_MARGIN
    env_z = bed.usable_mm[2] - 2 * PRINT_MARGIN

    # The design space on its own, the way `topopt setup` will see it.
    assert 2 * DS_R <= env_xy, (
        "the design space is %.1f mm across but only %.1f mm fits the plate"
        % (2 * DS_R, env_xy))
    # The design space PLUS the preserve -- the object that gets printed. This
    # is the check `topopt setup` does not do: in v8 the preserve reaches
    # 84.5 mm above the design space, so the design space fitting the machine
    # says nothing about the part fitting it.
    assert mount_top <= env_z, (
        "design space + camera mount is %.1f mm tall but only %.1f mm of "
        "headroom is usable" % (mount_top, env_z))
    # The mount has to sit ON the design space, not float above it or bury
    # itself in it. Preserve wins inside the overlap; the overlap is the plate.
    assert 0.0 < ds_top - plate_bottom <= PLATE_T + 1e-9, (
        "the design space top (%.2f) is not on the base plate (%.2f..%.2f)"
        % (ds_top, plate_bottom, plate_bottom + PLATE_T))
    # And there has to be a structure to design. Below this the model stops
    # testing anything the short tripod did not.
    assert plate_bottom >= 100.0, (
        "only %.1f mm between the ground and the mount" % plate_bottom)

    _write_binary_stl(TARGET, cylinder(DS_R, 0.0, ds_top))
    print("wrote %s" % os.path.basename(TARGET))
    print("  cylinder r %.1f, z 0.00..%.2f  (%.0f mm3)"
          % (DS_R, ds_top, np.pi * DS_R ** 2 * ds_top))
    print("  mount base plate %.2f..%.2f, mount top %.2f"
          % (plate_bottom, plate_bottom + PLATE_T, mount_top))
    print("  printed object %.1f x %.1f x %.1f mm against %.0f x %.0f x %.0f "
          "usable" % (2 * DS_R, 2 * DS_R, mount_top, env_xy, env_xy, env_z))


if __name__ == "__main__":
    main()

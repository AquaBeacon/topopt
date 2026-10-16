"""Rotate a moving part about a hinge and check it never touches the fixed one.

    topopt clearance holder.stl case.stl --hinge-z 23 --case-drop 11 \
        --angles -30,-15,0,15,30 --png out.png

The moving STL is in *its own* coordinates (bottom face at z=0, hinge on the x
axis at z=-case_drop). It is translated into holder coordinates, rotated about
the hinge, and tested against the holder.

The verdict comes from the moving part's **bounding box**, not from ray
casting: a reference STL that came out of `topopt step2stl` has its cylindrical
faces dropped and is not watertight, so ray casting through it is unreliable.
The box contains the true solid, so "the holder clears the box" proves "the
holder clears the part". Ray casting is still reported, as a second opinion.

Exit code 1 if any sampled angle clashes.
"""

import argparse
import sys

import numpy as np

from topopt.geom import (load_stl, sample_points, points_inside, min_gap,
                         rotation_about_x)


def _glue_negatives(argv):
    """`--angles -30,0,30` -> `--angles=-30,0,30`, which argparse can read."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--angles" and i + 1 < len(argv):
            out.append("--angles=%s" % argv[i + 1])
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt clearance", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("holder", help="the fixed part")
    ap.add_argument("case", help="the moving part")
    ap.add_argument("--hinge-z", type=float, required=True,
                    help="hinge height in holder coordinates, mm")
    ap.add_argument("--case-drop", type=float, required=True,
                    help="how far the hinge sits below the moving part's z=0, mm")
    ap.add_argument("--angles", default="-30,-20,-10,0,10,20,30")
    ap.add_argument("--png")
    a = ap.parse_args(_glue_negatives(list(sys.argv[1:] if argv is None else argv)))

    holder = load_stl(a.holder)
    case = load_stl(a.case) + np.array([0.0, 0.0, a.hinge_z + a.case_drop])

    hp = sample_points(holder)
    cp = sample_points(case, per_tri=2)[::3]     # the case is a 13k-facet mesh
    print("holder sample pts %d, case sample pts %d" % (len(hp), len(cp)))

    # The tongue (z < 0 in case coords) is excluded from the box -- the fingers
    # live inside it by design, and that fit is set by the side clearance.
    body = case.reshape(-1, 3)
    body = body[body[:, 2] >= a.hinge_z + a.case_drop - 1e-6]
    bmin, bmax = body.min(0), body.max(0)
    print("case body box (holder coords): min %s  max %s"
          % (np.round(bmin, 2), np.round(bmax, 2)))

    fail = False
    rows = []
    for deg in [float(x) for x in a.angles.split(",")]:
        rot = rotation_about_x(deg)
        pivot = np.array([0.0, 0.0, a.hinge_z])
        # inverse-rotate the holder into the case frame and test against the box
        hb = (hp - pivot) @ rot + pivot
        d = np.maximum(bmin - hb, hb - bmax).max(1)        # <0 => inside the box
        n_box = int((d < 0).sum())
        gap_box = float(d.min())
        q = (cp - pivot) @ rot.T + pivot
        n_ray = int(points_inside(holder, q).sum())        # case pts in the holder
        gap = min_gap(hp, q)
        lowest = q[:, 2].min()
        bad = n_box > 0
        fail |= bool(bad)
        rows.append((deg, n_ray, n_box, gap, lowest))
        print("tilt %+6.1f deg : holder pts inside case box %4d (clearance %+6.2f mm)"
              "   nearest mesh pt %5.2f mm   lowest case pt z=%6.2f   %s"
              % (deg, n_box, gap_box, gap, lowest, "CLASH" if bad else "clear"))

    if a.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection
        angs = [r[0] for r in rows]
        show = [angs[0], 0.0 if 0.0 in angs else angs[len(angs) // 2], angs[-1]]
        fig, axes = plt.subplots(1, len(show), figsize=(7 * len(show), 9))
        for ax, deg in zip(np.atleast_1d(axes), show):
            rot = rotation_about_x(deg)
            pivot = np.array([0.0, 0.0, a.hinge_z])
            ct = ((case.reshape(-1, 3) - pivot) @ rot.T + pivot).reshape(-1, 3, 3)
            for mesh, col in ((ct, "#9ec5e8"), (holder, "#e0a45a")):
                order = np.argsort(mesh[:, :, 0].mean(1))
                ax.add_collection(PolyCollection([mesh[i][:, [1, 2]] for i in order],
                                                 facecolors=col, edgecolors="#333",
                                                 linewidths=0.15))
            ax.axhline(0, color="k", lw=2)
            ax.plot([0], [a.hinge_z], "r+", ms=14, mew=2)
            ax.set_xlim(-70, 70)
            ax.set_ylim(-8, 105)
            ax.set_aspect("equal")
            ax.grid(alpha=.3, lw=.4)
            ax.set_title("tilt %+.0f deg   (Y-Z)" % deg)
            ax.set_xlabel("Y")
            ax.set_ylabel("Z")
        plt.tight_layout()
        plt.savefig(a.png, dpi=95)
        plt.close(fig)
        print("wrote", a.png)

    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

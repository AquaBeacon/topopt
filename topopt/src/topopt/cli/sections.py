"""Slice an STL on a grid of planes and read the real dimensions off it.

    topopt sections part.stl out.png
    topopt sections part.stl out.png --z -16,-12,-6,-1,2,6,20,40,50,53 \
                                     --y -14,0,14,18 --x -30,0,20,36

Each cell is one section, titled with the extent of the cut along both
in-plane axes. That extent is the number to check a feature against -- the
model may build and still be the wrong size, and a watertight STL says nothing
about whether the shape is the intended one.

With no --x/--y/--z, planes are spaced evenly across the bounding box.
"""

import argparse
import sys

import numpy as np

from topopt.geom import load_stl, cross_section, section_spans


def _glue_negatives(argv):
    """`--z -16,0,6` -> `--z=-16,0,6`.

    A plane list that starts below the origin begins with '-', which argparse
    reads as the next option and rejects. Users should not have to know that.
    """
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--x", "--y", "--z") and i + 1 < len(argv):
            out.append("%s=%s" % (a, argv[i + 1]))
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def _planes(arg, lo, hi, n):
    if arg:
        return [float(v) for v in arg.replace(" ", "").split(",") if v]
    # evenly spaced, inset from the faces so the end caps do not degenerate
    return list(np.round(np.linspace(lo, hi, n + 2)[1:-1], 2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt sections", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl")
    ap.add_argument("png")
    ap.add_argument("--x", default="", help="comma-separated X positions")
    ap.add_argument("--y", default="", help="comma-separated Y positions")
    ap.add_argument("--z", default="", help="comma-separated Z positions")
    ap.add_argument("--n", type=int, default=6,
                    help="auto planes per axis when none are given")
    ap.add_argument("--dpi", type=int, default=70)
    a = ap.parse_args(_glue_negatives(list(sys.argv[1:] if argv is None else argv)))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tri = load_stl(a.stl)
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)

    plan = [("Z", 2, _planes(a.z, lo[2], hi[2], a.n)),
            ("Y", 1, _planes(a.y, lo[1], hi[1], a.n)),
            ("X", 0, _planes(a.x, lo[0], hi[0], a.n))]
    ncol = max(len(p[2]) for p in plan)

    fig, axes = plt.subplots(3, ncol, figsize=(4 * ncol, 13), squeeze=False)
    for r, (nm, ax_i, vals) in enumerate(plan):
        o = [i for i in range(3) if i != ax_i]
        for c in range(ncol):
            ax = axes[r][c]
            if c >= len(vals):
                ax.axis("off")
                continue
            v = vals[c]
            segs = cross_section(tri, ax_i, v)
            for p, q in segs:
                ax.plot([p[o[0]], q[o[0]]], [p[o[1]], q[o[1]]], "k-", lw=0.7)
            e0 = section_spans(segs, o[0])
            e1 = section_spans(segs, o[1])
            ax.set_title("%s=%g  %s:%s..%s\n%s:%s..%s"
                         % (nm, v, "XYZ"[o[0]], e0[0], e0[1],
                            "XYZ"[o[1]], e1[0], e1[1]), fontsize=9)
            ax.set_aspect("equal")
            ax.grid(alpha=.35, lw=.4)
            ax.set_xlabel("XYZ"[o[0]])
            ax.set_ylabel("XYZ"[o[1]])
            print("%s=%6g: %s %s..%s   %s %s..%s   segs=%d"
                  % (nm, v, "XYZ"[o[0]], e0[0], e0[1],
                     "XYZ"[o[1]], e1[0], e1[1], len(segs)))
    plt.tight_layout()
    plt.savefig(a.png, dpi=a.dpi)
    plt.close(fig)
    print("wrote", a.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())

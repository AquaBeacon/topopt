"""Two printability failures that geometry checks alone do not catch (spec 19.5).

`topopt print` already answers "does it fit, which way up is best, how much
support". Both of the checks here are about *where the support lands*, which is
a different question and the one that ruins parts:

1. **Support touching a mating face.** Support leaves a rough, dimensionally
   unreliable surface. On a decorative face that is cosmetic; on a bolt seat or
   a bearing face it is a fit failure, and it is invisible in a support-volume
   total because the volume may be tiny.

2. **Support inside a sealed cavity.** Support that cannot be reached cannot be
   removed. The part comes off the printer permanently full of scaffolding, and
   no amount of orientation search finds this because the support total looks
   perfectly reasonable.

Both work on the voxel occupancy grid rather than the STL. On a voxel model the
only overhang angles are 0 and 90 degrees, so these report *flat* downward
faces -- which is the conservative direction (a 60-degree face that self
supports is not counted) and is stated rather than hidden.
"""

import numpy as np
from scipy import ndimage


def _shift_down(mask, axis, fill=False):
    """`mask` moved one voxel toward the build plate, with no wraparound."""
    out = np.roll(mask, 1, axis=axis)
    idx = [slice(None)] * 3
    idx[axis] = 0
    out[tuple(idx)] = fill
    return out


def overhang_cells(mask, build_axis=2):
    """Solid cells with nothing directly beneath them.

    The first layer is excluded: it rests on the build plate, which is support
    enough. Treating it as unsupported reports a block lying flat on the bed as
    100 % overhang.
    """
    below = _shift_down(mask, build_axis, fill=True)
    return mask & ~below


def support_columns(mask, build_axis=2):
    """Empty cells a support pillar would occupy. -> bool array.

    Every overhang cell is held by a column of support running straight down to
    whatever it lands on. Sweeping the whole grid at once with a cumulative
    maximum along the build axis is the same statement as tracing each column,
    and is a few orders of magnitude faster.
    """
    over = overhang_cells(mask, build_axis)
    # Mark, for every cell, whether any overhang exists above it in this column.
    flipped = np.flip(over, axis=build_axis)
    covered = np.flip(np.maximum.accumulate(flipped, axis=build_axis),
                      axis=build_axis)
    # A support column stops at the first solid it meets going down, so any
    # empty cell under an overhang and above the next solid is support.
    return covered & ~mask


def support_lands_on(mask, build_axis=2):
    """For each support column, the cell it rests on. -> bool array of landings.

    A landing is a solid cell whose top face carries support, or the build
    plate (not represented here -- plate landings are simply absent).
    """
    cols = support_columns(mask, build_axis)
    # A solid cell is a landing if the cell directly above it is support.
    up = np.roll(cols, -1, axis=build_axis)
    idx = [slice(None)] * 3
    idx[build_axis] = -1
    up[tuple(idx)] = False
    return mask & up


def support_on_critical_faces(mask, preserve, h, build_axis=2,
                              critical=None):
    """Does support land on a face that has to mate with something?

    `critical` defaults to `preserve` -- preserve is exactly "the surfaces that
    bolt, locate or bear", which is what makes it the right default. Pass a
    narrower mask to check only some of them.
    """
    crit = preserve if critical is None else critical
    landings = support_lands_on(mask, build_axis)
    hits = landings & crit
    area = float(np.count_nonzero(hits)) * h * h
    out = {"support_contact_area_mm2": round(area, 2),
           "contact_voxels": int(np.count_nonzero(hits)),
           "total_landing_area_mm2": round(
               float(np.count_nonzero(landings)) * h * h, 2),
           "pass": bool(area <= 0.0),
           "required": "no support may land on a preserve surface"}
    if hits.any():
        idx = np.argwhere(hits)
        out["locations_voxel"] = idx[:20].tolist()
        out["note"] = (
            "support rests on %d preserve voxel(s) (%.1f mm2). Those faces "
            "come off the printer rough and out of tolerance. Reorient the "
            "part, or accept the finish and say so explicitly."
            % (out["contact_voxels"], area))
    else:
        out["note"] = "no support lands on a preserve surface"
    return out


def enclosed_support(mask, h, build_axis=2):
    """Support inside a cavity with no path to the outside. -> dict.

    "Outside" is the void component that touches the grid boundary. Anything
    else is sealed, and support in a sealed void is support that stays in the
    part forever.

    Deliberately uses 6-connectivity for the void: two voxels meeting only at
    an edge or a corner are not a channel anyone can get a tool through, and
    26-connectivity would call that an escape route.
    """
    void = ~mask
    lab, n = ndimage.label(void, structure=ndimage.generate_binary_structure(3, 1))
    if n == 0:
        outside = set()
    else:
        edge = set()
        for a in range(3):
            for i in (0, -1):
                sl = [slice(None)] * 3
                sl[a] = i
                edge.update(np.unique(lab[tuple(sl)]).tolist())
        edge.discard(0)
        outside = edge
    cols = support_columns(mask, build_axis)
    sealed = cols & ~np.isin(lab, list(outside)) if outside else cols.copy()
    vol = float(np.count_nonzero(sealed)) * h ** 3
    out = {"enclosed_support_mm3": round(vol, 2),
           "enclosed_voxels": int(np.count_nonzero(sealed)),
           "total_support_mm3": round(
               float(np.count_nonzero(cols)) * h ** 3, 2),
           "pass": bool(vol <= 0.0),
           "required": "no support inside a sealed cavity"}
    if sealed.any():
        idx = np.argwhere(sealed)
        out["locations_voxel"] = idx[:20].tolist()
        out["note"] = (
            "%.1f mm3 of support sits in a cavity with no way out. It cannot "
            "be removed after printing. Add an access or drain opening, split "
            "the part, or reorient so the cavity is not spanned."
            % vol)
    else:
        out["note"] = "all support is reachable from outside"
    return out


def report(mask, preserve, h, build_axis=2):
    """Both checks plus the totals, in the shape the report expects."""
    cols = support_columns(mask, build_axis)
    over = overhang_cells(mask, build_axis)
    return {
        "schema": "topopt/printcheck/1",
        "build_axis": "XYZ"[int(build_axis)],
        "voxel_mm": round(float(h), 4),
        "flat_overhang_area_mm2": round(
            float(np.count_nonzero(over)) * h * h, 2),
        "support_volume_mm3": round(float(np.count_nonzero(cols)) * h ** 3, 2),
        "support_on_critical_faces": support_on_critical_faces(
            mask, preserve, h, build_axis),
        "enclosed_support": enclosed_support(mask, h, build_axis),
        "note": ("measured on the voxel grid, so overhang angles are only 0 "
                 "or 90 degrees. A 60-degree face that would self-support is "
                 "not counted here; `topopt overhang` reads real angles off "
                 "the STL."),
    }

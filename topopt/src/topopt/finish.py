"""Putting the CAD interface back into the optimiser's output.

The standing rule in this tool is that the raw result is not a part: every
mating dimension in it is quantised to the analysis grid, so a 3.50 mm slot
comes out at 3.21 mm and the assembly does not go together. The published
answer has always been "rebuild it in CAD". That is correct and it is also
where the tall tripod stalled -- the camera's centre finger is 2.40 mm, which
is under one voxel at 4 mm and invisible at 10 mm, so the interface in the
shipped STL was not approximate, it was absent.

But the interface is *preserve*. The optimiser never had a say in it. There is
no reason for it to come out of the grid at all: it went in as CAD and it can
come out as CAD.

So this stage rebuilds the chosen shape on a fine grid:

    1. the optimisation result, resampled from the coarse grid by whole-number
       replication -- no interpolation, no shift, the staircase stays exactly
       where it was analysed. **Minus** every preserve body that an `add`
       entry replaces (see below): its analysis voxels are dropped, except a
       one-voxel bridge where the optimised material joins it,
    2. `add` bodies (the preserve geometry, as drawn in CAD) unioned in,
    3. the optimised material smoothed -- and only that material: the CAD
       bodies, the preserve the CAD did not replace and the plate contact are
       held exactly, the obstacles stay empty,
    4. `cut` bodies (its slots, bores and pockets) subtracted.

Order matters and is not negotiable: cuts go last, for the same reason
`Setup.impose` re-applies preserve last. A cut applied before the union would
be filled straight back in by it. Smoothing goes before the cuts for the same
reason -- a Gaussian run across a 3.8 mm slot fills it.

**Why replace, and why bridge.** Before 2026-09-08 step 1 kept every analysed
voxel, so the CAD head was unioned *into* its own 10 mm voxelisation and the
shipped part carried a 30 x 30 x 30 mm block with a 19 mm head lost inside
it. The analysed preserve voxels are the CAD body rounded outward by up to one
voxel; once the CAD body is back, they are noise. An `add` entry whose name
matches a preserve body (or that says `"replaces": "<body>"`) therefore drops
that body's voxels. What stays is a *bridge*: the analysed preserve cells
directly adjacent to optimised material, so a strut that met the voxelised
face 2 mm below the CAD face still reaches the CAD face. Without it the
optimised body and the interface can end up as two solids with a gap between
them the width of the voxelisation error.

**The finish grid is not the analysis domain.** It used to be exactly that --
the analysis shape times the refinement, same origin -- which is right for the
optimised body and wrong for the CAD. `add` and `cut` are bodies someone drew;
nothing says the part that bolts to the interface fits inside the design space
the optimiser was given. A body that reached outside it was voxelised against
the analysis shape and CLIPPED, with no message and no sign in any statistic:
the shipped STL simply ended at the domain wall. MEASURED (tall tripod v7): a
camera mount 84.5 mm tall on a domain whose ceiling is 40 mm above the
interface lost its top half. `cover_pad` therefore grows the grid, in whole
analysis voxels so the resampled optimised body stays where it was analysed,
and `info.grid_grown_voxels` says by how much -- which is also the honest
statement that the shipped shape now covers millimetres the FEA never saw.

**Why smooth here and not on the analysis grid.** `*_smooth.stl` runs marching
cubes on the coarse density with a one-voxel Gaussian. At 10 mm that erases a
one-voxel leg (its centre value falls to 0.38) and turns the head into a
blob, because the Gaussian sees the preserve too. Here the Gaussian runs on
the fine grid, on the optimised material *only*, and the CAD bodies come back
as their own occupancy -- how much of each fine cell the CAD solid fills --
which the isosurface then follows exactly. The threshold is chosen so the
smoothed optimised body has the same volume as the staircase it came from,
and the CAD term is shifted onto that same threshold, so corners round, the
CAD faces stay where CAD put them, and a member does not lose a third of its
width to the filter.

**Holding a cell at 1 is not the same as holding a face.** Until 2026-09-09
the Gaussian ran over a mask that already contained the CAD bodies and the
cells inside them were then written back at 1. That stops the filter *eroding*
the interface and does nothing about it *moving*: the head blurred into the
space outside itself, so the isosurface came out proud of the CAD face and
every convex edge was rounded off. Measured on the tall tripod (3 mm analysis,
1.0 mm finish, sigma 1.50 mm): the nut-side finger 6.00 mm -> 6.25 mm, 322 mm3
of the CAD head rounded away, and -- because the smear grew 0.25 mm past the
mouth of the nut pocket while the pocket cut stopped at the nominal face --
a membrane across the pocket that made the part impossible to bolt up.
The user reported it as "it smooths the part I told it not to touch, and now
the case will not go on". lessons-learned #91.

**What this does and does not buy.** The interface stops being quantised at the
*analysis* voxel and becomes quantised at the *finish* voxel instead. That is a
large win and it is not the same as exact. Measured on the tall tripod, 10 mm
analysis grid finished at 0.5 mm:

    feature              CAD      finished    analysis grid alone
    outer finger        4.00 mm    4.00 mm    (not present)
    slot                3.80 mm    4.00 mm    (not present)
    centre finger       2.40 mm    2.00 mm    (not present)
    bolt bore          Ø5.60 mm   Ø5.60 mm    (not present)

A feature's width can be out by up to one *whole* finish voxel -- not half of
one -- because both of its faces round independently, and cuts round outward (a
cell whose centre is in the cut is removed, so both faces of a slot move the
same way). `feature_tolerance_mm` in the returned info is that bound; quote it
rather than claiming the drawing size.

The other two limits are unchanged. The *optimised body* is still quantised at
the analysis voxel -- resampling adds no information it never had, and
smoothing adds none either, it only removes the stair steps. No mating
dimension may be taken off it. And the FEA still saw the coarse interface, so
stresses in and around the head are resolved no better than before. The shape
is finished; the analysis is not re-done. `PRINT.md` says all of this.
"""

import os
from dataclasses import dataclass, field as _field

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter

from .voxel import load_stl, voxelize_stl

# 6-connectivity, the same structuring element `designspace` uses: a bridge
# cell has to share a face with optimised material, not just a corner.
_CONN = np.array([[[0, 0, 0], [0, 1, 0], [0, 0, 0]],
                  [[0, 1, 0], [1, 1, 1], [0, 1, 0]],
                  [[0, 0, 0], [0, 1, 0], [0, 0, 0]]], bool)


class FinishError(Exception):
    """A finish spec that cannot be honoured. Reported as an input problem."""


def parse(spec, base_dir):
    """`finish` block -> a normalised dict, or None when there is none.

    `add` entries may be a path, or `{"stl": path, "replaces": "<preserve>"}`.
    An entry whose name matches a preserve body replaces it (its analysis
    voxels are dropped in favour of the CAD); `"replaces": null` keeps the
    old union-only behaviour for an add that is not standing in for any
    preserve body.
    """
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise FinishError("`finish` must be an object")

    replaces = {}

    def paths(key):
        entries = spec.get(key) or {}
        if isinstance(entries, str):
            entries = {key: entries}
        if isinstance(entries, (list, tuple)):
            entries = {"%s_%d" % (key, i): e for i, e in enumerate(entries)}
        out = []
        for name, e in entries.items():
            if str(name).startswith("_"):
                continue
            p = e.get("stl", e) if isinstance(e, dict) else e
            full = p if os.path.isabs(p) else os.path.join(base_dir, p)
            if not os.path.exists(full):
                raise FinishError("finish %s %r: STL not found: %s"
                                  % (key, name, full))
            out.append((str(name), full))
            if key == "add":
                target = e.get("replaces", name) if isinstance(e, dict) \
                    else name
                if target:
                    replaces[str(name)] = str(target)
        return out

    smooth = spec.get("smooth_mm", "auto")
    if smooth is None or smooth is False:
        smooth = 0.0
    elif isinstance(smooth, str):
        if smooth.lower() != "auto":
            raise FinishError("finish.smooth_mm must be a number in mm, "
                              "\"auto\" or false")
        smooth = None                                   # decided from h
    else:
        smooth = float(smooth)
        if smooth < 0:
            raise FinishError("finish.smooth_mm must not be negative")

    samples = int(spec.get("occupancy_samples", DEFAULT_OCCUPANCY_SAMPLES))
    if samples < 1:
        raise FinishError("finish.occupancy_samples must be at least 1")

    out = {"voxel_mm": float(spec.get("voxel_mm", 1.0)),
           "add": paths("add"), "cut": paths("cut"),
           "replaces": replaces,
           "smooth_mm": smooth,
           "occupancy_samples": samples,
           # How far the finish stage may bridge a gap between the optimised
           # body and a CAD body AT THE JOINT. None -> one analysis voxel,
           # which is exactly the stand-off `attach` creates; 0 / false turns
           # it off and the CAD body then overhangs that stand-off.
           "bridge_mm": (None if spec.get("bridge_mm") is None
                         else float(spec.get("bridge_mm") or 0.0)),
           # Measured: the tall tripod's 0.5 mm finish grid is 119M cells and
           # peaks around 350 MB, because the result mask and each add/cut mask
           # are allocated separately. This machine has run out of memory on
           # this repo before (known-issues G5), so the ceiling is set where a
           # mistyped voxel size is refused rather than swapped to death.
           "max_cells": int(spec.get("max_cells", 250_000_000))}
    if out["voxel_mm"] <= 0:
        raise FinishError("finish.voxel_mm must be positive")
    if not out["add"] and not out["cut"]:
        return None
    return out


def refine_factor(h, fine_mm):
    """Whole-number subdivision of the analysis voxel.

    A non-integer ratio would put the fine grid's cell boundaries somewhere
    other than the coarse ones, and the resampled result would no longer be the
    shape that was analysed -- it would be that shape shifted by a fraction of a
    cell, which is the class of bug this stage exists downstream of.
    """
    f = max(1, int(round(float(h) / float(fine_mm))))
    return f, float(h) / f


def _up(mask, f):
    """Whole-number replication onto the fine grid."""
    m = np.asarray(mask, bool)
    return np.repeat(np.repeat(np.repeat(m, f, axis=0), f, axis=1), f, axis=2)


def _bbox_slices(mask, margin):
    """Slices covering the True cells of `mask` plus `margin`, or None."""
    sl = []
    for a in range(3):
        others = tuple(b for b in range(3) if b != a)
        line = np.flatnonzero(mask.any(axis=others))
        if line.size == 0:
            return None
        sl.append(slice(max(0, int(line[0]) - margin),
                        min(mask.shape[a], int(line[-1]) + 1 + margin)))
    return tuple(sl)


DEFAULT_OCCUPANCY_SAMPLES = 3


def occupancy(path, h, origin, shape, samples=DEFAULT_OCCUPANCY_SAMPLES,
              max_cells=200_000_000, out=None):
    """How much of each finish cell a CAD body fills, 0..1. -> float32 array

    `voxelize_stl` answers a different question -- is the *centre* of this cell
    inside the solid -- and that answer is all a boolean mask can carry. It
    quantises the CAD surface to the finish grid, which is what
    `feature_tolerance_mm` reports and is fine for a mask. It is not fine for
    the field the isosurface is taken from: a cylinder comes out as a
    staircase, and the surface can only ever sit on a cell boundary.

    So the body is voxelised `samples` times finer and block-averaged. A cell
    the CAD half fills reads 0.5, which is exactly where marching cubes puts
    the surface, and the shipped mesh follows the drawing to a fraction of a
    cell instead of to a whole one.

    Only the body's own bounding box is sampled, so the cost is set by the size
    of the interface rather than by the size of the part: the tall tripod's
    head is 31 x 31 x 37 cells at 1.0 mm, which is under a million samples at
    `samples=3`. `max_cells` drops back to the boolean answer rather than
    allocating something absurd for a body that fills the plate.

    `out` accumulates several bodies into one field with `maximum`.
    """
    shape = tuple(int(v) for v in shape)
    origin = np.asarray(origin, float)
    if out is None:
        out = np.zeros(shape, np.float32)

    tris = load_stl(path)
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    i0 = np.maximum(np.floor((lo - origin) / h).astype(int) - 1, 0)
    i1 = np.minimum(np.ceil((hi - origin) / h).astype(int) + 1, shape)
    if np.any(i1 <= i0):
        return out                                   # body is off the grid

    win = i1 - i0
    s = int(max(1, samples))
    while s > 1 and int(np.prod(win.astype(np.int64))) * s ** 3 > max_cells:
        s -= 1

    sub = voxelize_stl(path, h=h / s, origin=origin + i0 * h,
                       shape=win * s).solid
    if s > 1:
        sub = sub.reshape(win[0], s, win[1], s, win[2], s)
        frac = sub.mean(axis=(1, 3, 5), dtype=np.float32)
    else:
        frac = sub.astype(np.float32)
    block = out[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]]
    np.maximum(block, frac, out=block)
    return out


def _layer(ax, k):
    """Index tuple for one layer of a 3-D array along `ax`."""
    sl = [slice(None)] * 3
    sl[ax] = slice(k, k + 1)
    return tuple(sl)


def blur_across_plate(out, mask, sigma_cells, reach, ground=None,
                     window=None):
    """Gaussian of `mask` into `out`, reflected across the build plate.

    The plate is where the part was CUT, not where its material ends. A
    Gaussian that sees empty cells below it erodes the first layer, and the
    fix that was in place for that -- pin every cell of the first layer at 1 --
    pins the *staircase*: the foot then ships as a smooth dome standing on a
    one-cell skirt with the analysis grid's jagged outline still on it, which
    is what a user sees and reports as "the bottom was not smoothed".

    Reflecting the material makes the filter symmetric about the plate
    instead: the cross-section there is neither eroded nor grown, so the foot
    keeps its smoothed outline, stays flat and cannot lift. The reflection is
    built by padding rather than by writing into the cells below the plate,
    because there may not be any -- the grid can start exactly on it.

    `window` restricts the work to a sub-box (used for the second, union blur,
    which only matters around the CAD bodies). It is ignored when the box
    reaches within `reach` of the plate, because then the reflection matters.
    """
    if ground is None:
        sl = window or _bbox_slices(mask, reach + 1)
        if sl is not None:
            gaussian_filter(mask[sl].astype(np.float32), sigma_cells,
                            output=out[sl], mode="constant", truncate=3.0)
        return out

    ax, k0 = ground
    if window is not None and window[ax].start is not None \
            and window[ax].start >= k0 + reach:
        gaussian_filter(mask[window].astype(np.float32), sigma_cells,
                        output=out[window], mode="constant", truncate=3.0)
        return out

    keep = _layer_from(ax, k0)
    body = mask[keep]
    d = min(int(reach), body.shape[ax])
    take = [slice(None)] * 3
    take[ax] = slice(0, d)
    src = np.concatenate([np.flip(body[tuple(take)], axis=ax), body], axis=ax)
    buf = np.zeros(src.shape, np.float32)
    sl = _bbox_slices(src, reach + 1)
    if sl is not None:
        gaussian_filter(src[sl].astype(np.float32), sigma_cells,
                        output=buf[sl], mode="constant", truncate=3.0)
    drop = [slice(None)] * 3
    drop[ax] = slice(d, None)
    out[keep] = buf[tuple(drop)]
    return out


def _layer_from(ax, k):
    """Index tuple for everything at or above layer `k` along `ax`."""
    sl = [slice(None)] * 3
    sl[ax] = slice(k, None)
    return tuple(sl)


def smooth_field(blur, cad, hard, void, sigma_cells, target_cells=None,
                 union=None, ground=None, no_blend=None, blend_zone=None):
    """Gaussian of `blur`, unioned with the CAD occupancy `cad`, with `hard`
    cells held at 1 and `void` at 0. -> (field float32, level)

    `blur` is the OPTIMISED material only. The CAD bodies are not blurred and
    never were meant to be: `cad` already crosses 0.5 on the CAD surface, and
    the last step here shifts it so it crosses `level` there instead. The
    isosurface therefore lands on the CAD face whatever threshold the volume
    match picks, and the CAD's convex edges stay sharp.

    `level` is picked so that the thresholded field has the same number of
    cells as the shape going in had (within a cell or so): a Gaussian at the
    default 0.5 threshold erodes every convex corner and gives nothing back,
    so a member one analysis voxel across would come out a third thinner than
    the one the FEA passed. Matching the volume moves flat faces out by a
    fraction of a fine cell and rounds the corners; nothing is lost. The
    search range is kept off the extremes: a level under 0.3 starts bridging
    gaps the optimiser left, and above 0.5 it would only ever erode.

    The search does not have to re-compose the field for each candidate:
    `cad + (level - 0.5) >= level` is `cad >= 0.5` for every level, so the CAD
    contributes the same cells throughout and a boolean stands in for it.
    """
    reach = int(np.ceil(3 * sigma_cells))
    field = np.zeros(blur.shape, np.float32)
    blur_across_plate(field, blur, sigma_cells, reach, ground)
    sl = _bbox_slices(blur, reach + 1)

    # The joint. Away from the CAD bodies the field above is the answer: the
    # optimised material alone, so a CAD face is wherever `cad` says it is.
    # Right where the two meet that is wrong -- the optimised material's own
    # blur falls off as it approaches the CAD face and the strut necks away
    # from it, leaving the interface perched on a smaller stump than the one
    # the analysis connected. So near the optimised material the field blends
    # towards a blur of BOTH bodies, which fillets the corner instead. The
    # weight is the optimised body's own blurred value, clipped at its own
    # surface value, so it is 1 inside the optimised material, about a third
    # one sigma out and nothing at two -- continuous, and needing no distance
    # transform. `blend_zone` bounds it to the corner the two bodies actually
    # meet in: unbounded, the same blend grows a collar all the way round the
    # CAD body, which is worse than the neck it was fixing.
    if union is not None and sl is not None:
        w = _bbox_slices(union & ~blur, reach + 1)     # around the CAD bodies
        if w is not None:
            full = np.zeros(blur.shape, np.float32)
            blur_across_plate(full, union, sigma_cells, reach, ground,
                              window=w)
            g_uni = full[w].copy()
            del full
            weight = np.clip(2.0 * field[w], 0.0, 1.0)
            if blend_zone is not None:
                weight *= blend_zone[w]
            if no_blend is not None:
                weight[no_blend[w]] = 0.0
            g_uni -= field[w]
            g_uni *= weight
            field[w] += g_uni

    solid = hard if cad is None else (hard | (cad >= 0.5))
    search = field.copy()
    search[solid] = 1.0
    search[void] = 0.0
    target = int(blur.sum()) if target_cells is None else int(target_cells)
    lo, hi = 0.3, 0.5
    level = 0.5
    for _ in range(16):
        mid = 0.5 * (lo + hi)
        n = int(np.count_nonzero(search >= mid))
        if n > target:
            lo = mid
        else:
            hi = mid
        level = mid
    level = float(round(level, 4))
    del search

    if cad is not None:
        np.maximum(field, cad + np.float32(level - 0.5), out=field)
    field[hard] = 1.0
    field[void] = 0.0
    return field, level


@dataclass
class Finished:
    """What the finish stage made. `field` and `level` are set only when the
    optimised body was smoothed; the mesh is then the isosurface of `field`
    and `fine` is that same surface on the voxel grid."""
    fine: np.ndarray
    h: float
    origin: np.ndarray
    info: dict
    field: np.ndarray = None
    level: float = 0.5
    files: dict = _field(default_factory=dict)


def cover_pad(shape, h, origin, paths):
    """Analysis voxels the finish grid must grow by to hold `paths` whole.

    -> (lo, hi), two integer triples, both zero when everything already fits.

    **Why this exists.** The finish grid used to be the analysis grid and
    nothing else: `shape = mask.shape * f`, same origin. That is right for the
    optimised body, which cannot exist outside the domain it was solved in,
    and wrong for `add` and `cut`, which are CAD. Nothing says the part that
    bolts to the interface fits inside the design space -- and a body that
    reaches outside it was voxelised against the analysis shape, so the
    overhanging half was CLIPPED, silently, with the shipped STL simply
    ending at the domain wall. MEASURED (tall tripod v7): the camera mount is
    84.5 mm tall on a domain whose ceiling is 40 mm above the interface, so
    the top half of the mount was not in the file and nothing said so.

    The padding is in WHOLE analysis voxels, so every `_up()` in `build_part`
    stays aligned with the coarse grid and the resampled optimised body is
    still the shape that was analysed, in the same millimetres.

    A body that merely reaches the wall is left alone -- that is the old
    behaviour and existing results stay bit-identical. A body that crosses it
    gets one extra empty voxel beyond it as well, so the isosurface has a
    void cell to close against instead of an open edge at the array edge.
    """
    lo = np.zeros(3, int)
    hi = np.zeros(3, int)
    origin = np.asarray(origin, float)
    top = origin + np.asarray(shape, float) * h
    for p in paths:
        tri = load_stl(p)
        if not len(tri):
            continue
        v = tri.reshape(-1, 3)
        under = (origin - v.min(axis=0)) / h
        over = (v.max(axis=0) - top) / h
        for ax in range(3):
            if under[ax] > 0:
                lo[ax] = max(lo[ax], int(np.ceil(under[ax])) + 1)
            if over[ax] > 0:
                hi[ax] = max(hi[ax], int(np.ceil(over[ax])) + 1)
    return lo, hi


def build(mask, h, origin, spec, verbose=True, setup=None):
    """-> (fine_mask, fine_h, origin, info). See `build_part`."""
    p = build_part(mask, h, origin, spec, verbose=verbose, setup=setup)
    return p.fine, p.h, p.origin, p.info


def build_part(mask, h, origin, spec, verbose=True, setup=None):
    """The finish stage proper. -> `Finished`

    `setup` is the run's `Setup` (preserve, obstacle, ground, bodies). Without
    it -- a caller that only has a mask -- nothing is replaced and nothing is
    held during smoothing except the add bodies, which is still the old
    behaviour plus smoothing.
    """
    f, fine_h = refine_factor(h, spec["voxel_mm"])
    origin = np.asarray(origin, float)
    body = np.asarray(mask, bool)

    # ---- the finish grid may have to be BIGGER than the analysis domain
    # `add` and `cut` are CAD bodies and are not bound by the design space.
    # Growing here, by whole analysis voxels, is what stops them being
    # clipped at the domain wall. See `cover_pad`.
    pad_lo, pad_hi = cover_pad(body.shape, h, origin,
                               [p for _n, p in spec["add"] + spec["cut"]])
    grown = bool(pad_lo.any() or pad_hi.any())
    padw = tuple((int(a), int(b)) for a, b in zip(pad_lo, pad_hi))

    def grow(m):
        """An analysis-resolution mask on the padded grid (empty outside)."""
        if m is None:
            return None
        m = np.asarray(m, bool)
        return np.pad(m, padw, mode="constant") if grown else m

    if grown:
        body = grow(body)
        origin = origin - pad_lo * h
        if verbose:
            print("  finish  = grid grown by %s / %s analysis voxels so the "
                  "CAD bodies are not clipped at the domain wall"
                  % (tuple(int(v) for v in pad_lo),
                     tuple(int(v) for v in pad_hi)))

    shape = np.asarray(body.shape) * f
    cells = int(np.prod(shape.astype(np.int64)))
    if cells > spec["max_cells"]:
        raise FinishError(
            "a %.3f mm finish grid over this part is %d cells (%.2f GB per "
            "mask, and this stage holds several). Raise finish.voxel_mm, or "
            "finish.max_cells if you have the memory and mean it."
            % (fine_h, cells, cells / 1e9))
    zeros = np.zeros(body.shape, bool)
    preserve = (grow(setup.preserve) & body) if setup is not None else zeros
    obstacle = grow(setup.obstacle) if setup is not None else zeros
    ground_mask = grow(getattr(setup, "ground", None)) \
        if setup is not None else None
    no_attach_mask = grow(getattr(setup, "no_attach", None)) \
        if setup is not None else None

    # ---- which analysed preserve voxels the CAD replaces
    replaced = zeros.copy()
    rep_rows, warnings = [], []
    reps = spec.get("replaces") or {}
    if setup is not None and reps:
        pres = {b.name: b for b in setup.bodies if b.kind == "preserve"}
        for add_name, target in reps.items():
            if target in pres:
                m = grow(pres[target].mask) & body
                replaced |= m
                rep_rows.append({"add": add_name, "preserve": target,
                                 "voxels": int(m.sum()),
                                 "mm3": round(float(m.sum()) * h ** 3, 1)})
            else:
                warnings.append(
                    "finish add %r would replace preserve %r, which the study "
                    "does not declare; the analysed voxels are kept. Name the "
                    "add after the preserve body, or set \"replaces\"."
                    % (add_name, target))
    elif reps and setup is None:
        warnings.append("finish: no setup was given, so no preserve body is "
                        "replaced by its CAD (the analysed voxels are kept)")
    optimised = body & ~preserve
    bridge = binary_dilation(optimised, structure=_CONN) & replaced
    keep = (body & ~replaced) | bridge

    fine = _up(keep, f)
    # Spec 19.3: a boolean that fails must not destroy the shape it was given.
    # One extra mask on a stage the docstring already says holds several --
    # released before the write, so the peak is unchanged for a run that works
    # and a run that does not still ships the shape that was analysed instead
    # of one with half its cuts in it.
    resampled = fine.copy()

    smooth_mm = spec.get("smooth_mm", 0.0)
    if smooth_mm is None:
        # "auto" = one analysis voxel. MEASURED (tall tripod, 3 mm analysis
        # grid, 1.0 mm finish): at half a voxel the staircase is not removed,
        # it is rounded into it -- the legs came out as a row of beads, one
        # bead per analysis cell, which reads as "you did not smooth it". At a
        # full voxel the beads are gone and the shell is still closed and one
        # body. A voxel and a half smooths no better and starts producing
        # marching-cubes seams the shell check calls open edges.
        smooth_mm = 1.0 * h                             # "auto"
    smooth_mm = float(smooth_mm or 0.0)

    info = {"voxel_mm": round(fine_h, 4), "refine_factor": f,
            # Both faces of a feature round independently, so its *width* can
            # be out by a whole cell -- not half of one. Say so, so the number
            # is quoted instead of "at CAD dimensions".
            "feature_tolerance_mm": round(fine_h, 4),
            "grid_shape": [int(v) for v in shape],
            # Whole analysis voxels the finish grid was grown by so a
            # CAD body reaching outside the analysed domain is not
            # clipped. Non-zero means the shipped shape covers
            # millimetres the FEA never looked at.
            "grid_grown_voxels": {"lo": [int(v) for v in pad_lo],
                                  "hi": [int(v) for v in pad_hi]},
            "analysed_mm3": round(float(body.sum()) * h ** 3, 1),
            "replaced_preserve": rep_rows,
            "replaced_mm3": round(float(replaced.sum()) * h ** 3, 1),
            "bridge_mm3": round(float(bridge.sum()) * h ** 3, 1),
            "resampled_mm3": round(float(fine.sum()) * fine_h ** 3, 1),
            "smooth_mm": round(smooth_mm, 4),
            "added": [], "cut": []}
    if warnings:
        info["warnings"] = warnings
    if verbose and rep_rows:
        for r in rep_rows:
            print("  finish  ~ %-18s replaces preserve %r (%d analysed cells "
                  "dropped, bridge %.0f mm3)"
                  % (r["add"], r["preserve"], r["voxels"], info["bridge_mm3"]))

    field, level = None, 0.5
    try:
        # `cad` is the occupancy of the CAD bodies: how much of each fine cell
        # each one fills, not merely which cells they are in. It is what keeps
        # the smoothing off the interface (see the module docstring), so it is
        # only built when there is smoothing to keep off it.
        samples = int(spec.get("occupancy_samples", DEFAULT_OCCUPANCY_SAMPLES))
        cad = np.zeros(fine.shape, np.float32) if smooth_mm > 0 else None

        # The cuts are voxelised HERE and applied at the end. Two things need
        # them: the fillet at the joint must not grow across the mouth of a
        # pocket that opens on a CAD face (that is how the nut pocket got
        # capped in the first place, lessons-learned #91), and the cut itself
        # then costs no second voxelisation.
        cuts = [(name, path,
                 voxelize_stl(path, h=fine_h, origin=origin,
                              shape=shape).solid)
                for name, path in spec["cut"]]

        add_fine = np.zeros(fine.shape, bool)
        for name, path in spec["add"]:
            m = voxelize_stl(path, h=fine_h, origin=origin, shape=shape).solid
            gained = int((m & ~fine).sum())
            fine |= m
            add_fine |= m
            if cad is not None:
                occupancy(path, fine_h, origin, shape, samples=samples, out=cad)
            info["added"].append({"name": name, "stl": os.path.basename(path),
                                  "voxels": int(m.sum()),
                                  "added_mm3": round(gained * fine_h ** 3, 1)})
            if verbose:
                print("  finish  + %-18s %8d cells (%+.1f mm3)"
                      % (name, int(m.sum()), gained * fine_h ** 3))

        # Smoothing, of the optimised material only. The Gaussian never sees a
        # CAD body: it runs on the resampled optimisation result (the bridge
        # cells included, so the joint does not neck) and the CAD comes back as
        # its own occupancy. Held exactly besides: the analysed preserve that
        # no CAD replaced, and the first fine layer on the plate (so a foot
        # cannot lift). Held empty: the obstacles, including the no-contact
        # shells around preserve, so the filter cannot push material onto a
        # face the optimiser was kept off -- except where the CAD body itself
        # reaches into one, which is the CAD's business and not the filter's.
        if smooth_mm > 0:
            # Preserve that no CAD replaced is shipped as its analysed voxels.
            # It is kept out of the blur for the same reason the CAD bodies
            # are -- it is geometry the user placed, and the filter has no
            # business growing it outwards -- and pinned to 1 afterwards.
            pres_fine = _up(preserve & ~replaced, f)
            hard = pres_fine.copy()
            void = np.zeros(fine.shape, bool)
            ground = None
            if setup is not None:
                void = _up(obstacle, f) & (cad <= 0.0)
                ax = int(getattr(setup, "ground_axis", 2))
                g = (_up(ground_mask, f) if ground_mask is not None
                     else np.zeros(fine.shape, bool))
                if g.any():
                    k0 = int(np.flatnonzero(g.any(axis=tuple(
                        b for b in range(3) if b != ax)))[0])
                    ground = (ax, k0)
                    # Everything below the plate is a reflection made for the
                    # filter's benefit (see `mirror_across_plate`) and is not
                    # part of anything. Void, so the volume match does not
                    # count it and the isosurface stops at the plate.
                    if k0:
                        below = [slice(None)] * 3
                        below[ax] = slice(0, k0)
                        void[tuple(below)] = True

            # The bridge zone. `attach` deliberately holds the optimised
            # material one voxel off every face of a preserve body it may not
            # join, and the shipped part then carries the CAD body overhanging
            # that stand-off with nothing under it -- MEASURED on the tall
            # tripod: a 2.00 mm gap under the pedestal everywhere outside the
            # 18 x 18 mm attach patch, on a 30 x 30 mm face. It is not a
            # smoothing artefact, it is what the study asked for, and it is
            # still not a part: it prints as an overhang and reads as "the
            # connection is floating".
            #
            # So the fillet is allowed into obstacle cells -- and ONLY into
            # cells that are close to BOTH bodies, which is the concave corner
            # where they meet and nothing else. A clearance far from the joint
            # stays empty. `cut` bodies run afterwards and still win, so a
            # pocket or a bore cannot be filled in by this.
            bridge_mm = spec.get("bridge_mm")
            if bridge_mm is None:
                bridge_mm = h                            # one analysis voxel
            bridge_mm = float(bridge_mm or 0.0)
            joint = np.zeros(fine.shape, bool)
            opt = resampled & ~pres_fine
            rb = int(round(bridge_mm / fine_h))
            # ONLY into the shell `attach` put there. A stand-off the tool
            # itself added around a preserve body is the tool's to close; a
            # clearance the user declared -- the camera's swing, the room to
            # turn a knob -- is not, however close to the joint it happens to
            # pass. Without this the bridge is "the fillet may enter any
            # obstacle near the joint", which is a licence to fill in the one
            # thing the study was most careful about.
            shell = (_up(no_attach_mask, f) if no_attach_mask is not None
                     else np.zeros(fine.shape, bool))
            # Where the two bodies are within a bridge-width of EACH OTHER.
            # That is the corner they meet in, and it is the only place the
            # blend below is allowed to act. Without this bound the blend
            # reaches two sigma in every direction from the optimised
            # material, so it grows a collar right round the CAD body --
            # wider than the interface and wider than the strut under it --
            # and what ships reads as a cushion with the part sitting on it
            # rather than as one piece. lessons-learned #95.
            blend_zone = np.zeros(fine.shape, bool)
            if rb > 0 and add_fine.any() and opt.any():
                blend_zone = (binary_dilation(add_fine, structure=_CONN,
                                              iterations=rb)
                              & binary_dilation(opt, structure=_CONN,
                                                iterations=rb))
                # The obstacle veto is lifted only inside the shell `attach`
                # put there -- see below.
                joint = blend_zone & shell
                void &= ~joint

            # Nowhere near a cut. A fillet is material grown OUTSIDE a CAD
            # face; a cut that stops exactly on that face does not remove it,
            # and what is left is a membrane across the mouth of the pocket --
            # which is the failure this whole module was rewritten for. The
            # fillet is therefore kept a bridge-width clear of every cut body.
            # (The other half of the rule lives in the CAD: run a hole that
            # crosses a nominal face past it. See tall_tripod.scad `cut_over`.)
            keep_clear = np.zeros(fine.shape, bool)
            for _n, _p, m in cuts:
                keep_clear |= m
            if keep_clear.any():
                keep_clear = binary_dilation(keep_clear, structure=_CONN,
                                             iterations=max(rb, 1))
                joint &= ~keep_clear
                void |= keep_clear & _up(obstacle, f) & (cad <= 0.0)

            before = int(fine.sum())
            field, level = smooth_field(opt, cad, hard, void,
                                        smooth_mm / fine_h,
                                        target_cells=before,
                                        union=fine, ground=ground,
                                        no_blend=keep_clear,
                                        blend_zone=blend_zone)
            fine = field >= level
            info["smooth"] = {
                "sigma_mm": round(smooth_mm, 4),
                "level": level,
                "held_cells": int(hard.sum()),
                "void_cells": int(void.sum()),
                "cad_cells": int(np.count_nonzero(cad > 0.0)),
                "occupancy_samples": samples,
                # What a CAD face is good to in the ISOSURFACE. The whole-cell
                # `feature_tolerance_mm` above still applies to the voxel file
                # and to the optimised body, which has no sub-cell information
                # to recover.
                "cad_tolerance_mm": round(fine_h / samples, 4),
                "bridge_mm": round(bridge_mm, 4),
                "bridge_mm3": round(float((fine & joint & ~resampled
                                           & ~add_fine).sum())
                                    * fine_h ** 3, 1),
                "plate_mirrored": ground is not None,
                "cells_before": before,
                "cells_after": int(fine.sum()),
                "volume_change_pct": round(
                    100.0 * (int(fine.sum()) - before) / max(before, 1), 2),
                "note": "Gaussian on the optimised material only, blended "
                        "towards a blur of both bodies where they join; the "
                        "CAD bodies enter as sub-cell occupancy and their "
                        "faces are not moved; unreplaced preserve is held at "
                        "1; the plate is mirrored rather than pinned so the "
                        "first layer keeps the smoothed outline; obstacles "
                        "are held at 0 except in the joint; and the threshold "
                        "is chosen so the volume matches the analysed "
                        "staircase"}
            if verbose:
                print("  finish  ~ smoothed optimised body: sigma %.2f mm, "
                      "level %.3f, volume %+.2f %%, fillet at the joint "
                      "%.0f mm3"
                      % (smooth_mm, level, info["smooth"]["volume_change_pct"],
                         info["smooth"]["bridge_mm3"]))

        # Cuts last. See the module docstring: a cut applied before the union
        # is a cut that gets filled back in, and a cut before the smoothing is
        # a cut the Gaussian fills. A cut goes into the isosurface the way the
        # CAD bodies did -- as occupancy, shifted onto the chosen level -- so a
        # bore comes out at its diameter instead of rounded outwards to the
        # nearest whole cell.
        for name, path, m in cuts:
            removed = int((m & fine).sum())
            fine &= ~m
            if field is not None:
                occ = occupancy(path, fine_h, origin, shape, samples=samples)
                np.minimum(field, (1.0 - occ) + np.float32(level - 0.5),
                           out=field)
                fine = field >= level
            info["cut"].append({"name": name, "stl": os.path.basename(path),
                                "voxels": int(m.sum()),
                                "removed_mm3": round(removed * fine_h ** 3, 1)})
            if verbose:
                print("  finish  - %-18s %8d cells (%-.1f mm3)"
                      % (name, int(m.sum()), removed * fine_h ** 3))
            if removed == 0:
                info.setdefault("warnings", []).append(
                    "finish cut %r removed nothing -- it may be outside the "
                    "part, or below the finish voxel size" % name)
    except Exception as exc:                                      # noqa: BLE001
        # Spec 19.3: the un-cut shape survives a failed boolean. Rolled all
        # the way back to the resampled result rather than to the last good
        # operation: "add the mount, then cut its bore" is one intent, and
        # shipping the mount with no bore in it is a part that looks finished
        # and is wrong. What ships is the shape that was analysed, and the
        # error says so in the summary as well as on the terminal.
        info["error"] = ("the finish boolean failed (%s: %s); the shipped "
                         "shape is the optimisation result as analysed, with "
                         "no CAD interface added or cut back in"
                         % (type(exc).__name__, exc))
        info["rolled_back"] = True
        info["added"], info["cut"] = [], []
        info.pop("smooth", None)
        fine = resampled
        field, level = None, 0.5
        if verbose:
            print("  finish  ! %s" % info["error"])

    info["final_mm3"] = round(float(fine.sum()) * fine_h ** 3, 1)
    return Finished(fine=fine, h=fine_h, origin=origin, info=info,
                    field=field, level=level)


def write(fine, fine_h, origin, outdir, base, info=None, verbose=True,
          field=None, level=0.5):
    """Write the finished shape. -> {tag: path}

    With `field` (the smoothed scalar field from `build_part`) the part is
    written as its isosurface -- `<base>_finish.stl` -- and the voxel version
    alongside as `<base>_finish_voxel.stl`. Without it, or without
    scikit-image, `<base>_finish.stl` is the voxel shape as before.
    """
    from .meshout import write_blocky_stl, write_smooth_stl

    os.makedirs(outdir, exist_ok=True)
    files = {}
    p = os.path.join(outdir, base + "_finish.stl")
    if field is not None:
        n = write_smooth_stl(p, field, fine_h, origin, level=level, smooth=0,
                             floor_mask=None, envelope=fine)
        if n:
            files["finish_stl"] = p
            if info is not None:
                info["finish_stl_triangles"] = int(n)
                info["finish_stl_kind"] = "isosurface"
            if verbose:
                print("  finish  -> %s (%d triangles, isosurface at %.3f on "
                      "a %.3f mm grid)" % (os.path.basename(p), n, level,
                                           fine_h))
            pv = os.path.join(outdir, base + "_finish_voxel.stl")
            nv = write_blocky_stl(pv, fine, fine_h, origin, merge=True)
            files["finish_voxel_stl"] = pv
            if info is not None:
                info["finish_voxel_stl_triangles"] = int(nv)
            return files
        if info is not None:
            info.setdefault("warnings", []).append(
                "scikit-image is not installed, so the finished part is "
                "written as voxels rather than as a smoothed surface")
    # Merged: the resampled result would otherwise ship one voxel face per fine
    # cell across surfaces that are a single plane at the analysis resolution.
    n = write_blocky_stl(p, fine, fine_h, origin, merge=True)
    files["finish_stl"] = p
    if info is not None:
        info["finish_stl_triangles"] = int(n)
        info["finish_stl_kind"] = "voxel"
    if verbose:
        print("  finish  -> %s (%d triangles at %.3f mm)"
              % (os.path.basename(p), n, fine_h))
    return files


def write_part(part, outdir, base, verbose=True):
    """`write` for a `Finished`. -> {tag: path}"""
    part.files = write(part.fine, part.h, part.origin, outdir, base,
                       info=part.info, verbose=verbose, field=part.field,
                       level=part.level)
    return part.files

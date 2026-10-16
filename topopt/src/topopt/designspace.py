"""Design space, preserve and obstacle geometry -- the Fusion Generative Design
setup model, on a voxel grid.

Four geometry types, and the rules that make them mean something:

| type                     | density | role |
|--------------------------|---------|------|
| `preserve`               | fixed 1 | interfaces you bolt, load and locate from |
| `obstacle`               | fixed 0 | holes, swept volumes, cable and tool access |
| `design_space`           | free    | where the optimiser is allowed to put material |
| `optional_starting_shape`| initial | a warm start; it constrains nothing |

The invariants this module exists to enforce:

- **Preserve is never eroded.** It is not smoothed, not filtered, not projected,
  not thresholded and not dropped by connectivity cleanup. Everything that could
  reduce it runs *before* the preserve value is re-imposed, never after.
- **Loads and supports live on preserve only.** Applying a load to free material
  means the optimiser can delete the thing you are pushing on. This is checked,
  not assumed.
- **Obstacles get a clearance offset** before they are burned into the grid, so
  the result does not touch the moving part / cable / lens cone it must avoid.
- **Every preserve body has to be reachable.** If two preserve bodies sit in
  different connected components of `design_space | preserve`, no amount of
  material can join them and the run is pointless. That is a setup error and is
  reported before the first solve.

Everything is evaluated on one shared grid: the design space fixes the grid, and
every other body is voxelised onto that same origin and shape. Bodies voxelised
on their own grids and then combined is the classic silent misalignment bug.
"""

import os
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import distance_transform_edt, label

from .loadcase import region_mask
from .voxel import Grid, load_stl, voxelize_stl

KINDS = ("design_space", "preserve", "obstacle", "starting_shape")

_ALIASES = {
    "optional_starting_shape": "starting_shape",
    "start_shape": "starting_shape",
    "keep_in": "preserve",
    "keep_out": "obstacle",
    "domain": "design_space",
}

# 6-connectivity: two voxels that only share an edge or a corner are not a
# printable connection, so they must not count as one here either.
_CONN = np.array([[[0, 0, 0], [0, 1, 0], [0, 0, 0]],
                  [[0, 1, 0], [1, 1, 1], [0, 1, 0]],
                  [[0, 0, 0], [0, 1, 0], [0, 0, 0]]], bool)


@dataclass
class Body:
    """One named piece of setup geometry."""
    name: str
    kind: str
    spec: dict
    clearance_mm: float = 0.0
    stl_path: str = None
    tris: np.ndarray = None
    mask: np.ndarray = None          # as declared, before clearance
    mask_offset: np.ndarray = None   # after the clearance offset (obstacles)
    # Preserve only: where the optimiser may *join* this body. None means
    # anywhere (the historical behaviour); a list of attach specs means the
    # rest of the body's surface gets a no-contact shell. See `attach_zone`.
    attach: list = None
    attach_mask: np.ndarray = None       # design-space cells it may join at
    no_attach_mask: np.ndarray = None    # the shell it may not

    @property
    def source(self):
        return ("stl:" + os.path.basename(self.stl_path)) if self.stl_path \
            else self.spec.get("type", "region")


@dataclass
class Setup:
    """A validated (or at least fully described) generative-design setup."""
    grid: Grid                       # .solid is the FE domain
    bodies: list
    design_space: np.ndarray
    preserve: np.ndarray
    obstacle: np.ndarray             # after clearance
    obstacle_raw: np.ndarray         # as declared
    starting_shape: np.ndarray = None    # optional warm start
    free: np.ndarray = None
    report: dict = field(default_factory=dict)
    symmetry: list = field(default_factory=list)
    obstacle_in_preserve: np.ndarray = None
    # The band of cells sitting on the plate, and which axis "down" is. A
    # support declared `"contact": "ground"` is clipped to this instead of to
    # preserve -- see `ground_mask`.
    ground: np.ndarray = None
    ground_axis: int = 2
    ground_mm: float = 0.0
    # Where material may join a preserve body that declared `attach`, and the
    # shell around such bodies where it may not. `no_attach` is already folded
    # into `obstacle`; it is kept apart so the report and the preview can say
    # which obstacle cells the user drew and which ones this rule added.
    attach: np.ndarray = None
    no_attach: np.ndarray = None
    attach_standoff_mm: float = 0.0

    def __post_init__(self):
        """Fill in what a hand-built Setup is allowed to leave out.

        `build_setup` supplies every field, but the whole point of this being a
        library is that a caller can assemble masks itself. `starting_shape`
        genuinely is optional, and `free` has exactly one correct value, so
        neither should have to be spelled out.
        """
        if self.starting_shape is None:
            self.starting_shape = np.zeros(self.grid.shape, bool)
        if self.free is None:
            # Intersect with grid.solid, which `build_setup` sets to
            # design_space | preserve. A caller assembling masks by hand can
            # pass a smaller domain, and a free cell outside it would be
            # counted by `volfrac` and then zeroed by `impose` -- a volume
            # constraint that quietly allocates material to nowhere.
            self.free = (self.design_space & ~self.preserve & ~self.obstacle
                         & self.grid.solid)
        if self.obstacle_in_preserve is None:
            self.obstacle_in_preserve = np.zeros(self.grid.shape, bool)
        if self.attach is None:
            self.attach = np.zeros(self.grid.shape, bool)
        if self.no_attach is None:
            self.no_attach = np.zeros(self.grid.shape, bool)
        if self.ground is None:
            self.ground = ground_mask(self.grid.solid, self.ground_axis,
                                      self.grid.h, self.grid.origin)[0]

    @property
    def h(self):
        return self.grid.h

    @property
    def origin(self):
        return self.grid.origin

    def initial_density(self, volfrac):
        """Starting design field: preserve solid, obstacles empty, and either
        the starting shape or a uniform `volfrac` in between."""
        x = np.zeros(self.grid.shape)
        if self.starting_shape.any():
            # Blend towards volfrac so the optimiser is not handed a fully
            # discrete field it cannot get gradients out of.
            x[self.free] = np.where(self.starting_shape[self.free],
                                    max(volfrac, 0.6), min(volfrac, 0.3))
        else:
            x[self.free] = volfrac
        x[self.preserve] = 1.0
        x[self.obstacle] = 0.0
        return x

    def impose(self, x):
        """Re-apply the hard density rules. Call this *after* every operation
        that could erode preserve -- filtering, projection, symmetry, smoothing.
        """
        x = np.clip(x, 0.0, 1.0)
        x[self.preserve] = 1.0
        x[self.obstacle] = 0.0
        return x * self.grid.solid


# ----------------------------------------------------------------- parsing

def _normalise_entry(entry, default_clearance):
    """-> (spec_or_path_dict, clearance_mm)."""
    clearance = default_clearance
    if isinstance(entry, str):
        return ({"stl": entry} if entry.lower().endswith((".stl", ".3mf"))
                else {"region": entry}), clearance
    if not isinstance(entry, dict):
        raise ValueError("geometry entry must be a string or an object, got %r"
                         % (entry,))
    e = dict(entry)
    if "clearance_mm" in e:
        clearance = float(e.pop("clearance_mm"))
    return e, clearance


def parse_geometry(geom, regions, base_dir, default_clearance=0.0):
    """`geometry` block -> a list of `Body` with masks still unset."""
    bodies = []
    for raw_kind, entries in geom.items():
        kind = _ALIASES.get(raw_kind, raw_kind)
        if kind not in KINDS:
            # `_`-prefixed keys are the study's own comments (JSON has none).
            if str(raw_kind).startswith("_") or raw_kind in (
                    "clearance_mm", "pad_voxels", "voxel_mm",
                    "target_elements", "notes", "attach_standoff_mm"):
                continue
            raise ValueError(
                "unknown geometry type %r; expected one of %s"
                % (raw_kind, ", ".join(KINDS)))

        if isinstance(entries, dict) and not (
                "type" in entries or "stl" in entries or "region" in entries):
            items = list(entries.items())                 # {name: entry}
        elif isinstance(entries, (list, tuple)):
            items = [("%s_%d" % (kind, i), e) for i, e in enumerate(entries)]
        else:
            items = [(kind, entries)]                     # a single entry

        for name, entry in items:
            # JSON has no comments, so `_`-prefixed keys are how studies carry
            # the reasoning for a body. They are notes, not geometry.
            if str(name).startswith("_"):
                continue
            spec, clearance = _normalise_entry(entry, default_clearance)
            if kind != "obstacle":
                clearance = 0.0
            # `attach` belongs to preserve: the faces the optimiser may join
            # the body at. Pulled out before the region lookup below replaces
            # the spec dict, and refused elsewhere because "this obstacle may
            # be attached at" means nothing.
            attach = None
            if "attach" in spec:
                attach = spec.pop("attach")
                if kind != "preserve":
                    raise ValueError(
                        "%s %r: 'attach' is only meaningful on a preserve "
                        "body" % (kind, name))
                attach = _normalise_attach(attach, kind, name)
            stl_path = None
            if "stl" in spec:
                p = spec["stl"]
                stl_path = p if os.path.isabs(p) else os.path.join(base_dir, p)
                if not os.path.exists(stl_path):
                    raise FileNotFoundError(
                        "%s %r: STL not found: %s" % (kind, name, stl_path))
            elif "region" in spec:
                rn = spec["region"]
                if rn not in (regions or {}):
                    raise KeyError("%s %r references unknown region %r"
                                   % (kind, name, rn))
                spec = dict(regions[rn])
            elif "type" not in spec:
                raise ValueError(
                    "%s %r: needs 'stl', 'region' or an inline region 'type'"
                    % (kind, name))
            bodies.append(Body(name=str(name), kind=kind, spec=spec,
                               clearance_mm=clearance, stl_path=stl_path,
                               attach=attach))
    return bodies


# ------------------------------------------------------------------ attach

_FACE_DIRS = {"+X": (0, +1), "-X": (0, -1), "+Y": (1, +1), "-Y": (1, -1),
              "+Z": (2, +1), "-Z": (2, -1)}


def _normalise_attach(attach, kind, name):
    """`attach` as written -> a list of specs, each a face string or a region.

    Accepted forms, all meaning "the optimiser may join this body here":

        "attach": "-Z"                         one face of the body
        "attach": ["-Z", "+X"]                 several faces
        "attach": [{"face": "-Z"}, "foot_pad", {"type": "box", ...}]
                                               faces, named regions, inline
                                               regions -- any mix
        "attach": []                           nowhere: an error at setup,
                                               because nothing could ever
                                               connect to the body

    A face is the body's own face, not a plane in space: `-Z` is every
    design-space cell reachable by stepping *down* out of the body -- so it
    follows an undercut or a stepped underside exactly, and it says nothing
    about cells beside the body.
    """
    if isinstance(attach, (str, dict)):
        attach = [attach]
    if not isinstance(attach, (list, tuple)):
        raise ValueError("%s %r: 'attach' must be a face (\"-Z\"), a region, "
                         "or a list of those" % (kind, name))
    out = []
    for a in attach:
        if isinstance(a, dict) and "face" in a:
            a = {"face": str(a["face"]).upper()}
            if a["face"] not in _FACE_DIRS:
                raise ValueError("%s %r: attach face %r is not one of %s"
                                 % (kind, name, a["face"],
                                    ", ".join(_FACE_DIRS)))
        elif isinstance(a, str) and a.upper() in _FACE_DIRS:
            a = {"face": a.upper()}
        elif isinstance(a, str):
            a = {"region": a}
        elif isinstance(a, dict) and "type" in a:
            a = dict(a)
        else:
            raise ValueError("%s %r: attach entry %r is neither a face "
                             "(\"-Z\"), a region name nor an inline region"
                             % (kind, name, a))
        out.append(a)
    return out


def _shift(mask, axis, steps):
    """`mask` moved `steps` cells along `axis`; nothing wraps around."""
    out = np.zeros_like(mask)
    n = mask.shape[axis]
    if abs(steps) >= n:
        return out
    src = [slice(None)] * 3
    dst = [slice(None)] * 3
    if steps >= 0:
        src[axis] = slice(0, n - steps)
        dst[axis] = slice(steps, n)
    else:
        src[axis] = slice(-steps, n)
        dst[axis] = slice(0, n + steps)
    out[tuple(dst)] = mask[tuple(src)]
    return out


def attach_zone(body_mask, specs, shell, grid, regions, standoff_cells):
    """Cells of `shell` (the body's no-contact band) that `specs` open up.

    A face spec walks out of the body along its normal, up to the shell's
    thickness, so that with a thicker standoff the attachable column still
    reaches the surface it names. A region spec is the region, clipped to the
    shell -- a region drawn generously simply loses the cells that are not
    next to the body, the same rule loads and supports follow.
    """
    zone = np.zeros_like(body_mask)
    for spec in specs:
        if "face" in spec:
            axis, sign = _FACE_DIRS[spec["face"]]
            for s in range(1, standoff_cells + 1):
                zone |= _shift(body_mask, axis, sign * s)
        elif "region" in spec:
            zone |= region_mask(grid, spec["region"], regions, clip=False)
        else:
            zone |= region_mask(grid, spec, regions, clip=False)
    return zone & shell


def _apply_attach(bodies, preserve, obstacle, design_space, grid, regions, h,
                  standoff_mm):
    """Fold the no-contact shells into `obstacle`. -> (obstacle, attach,
    no_attach, standoff_cells)

    Only bodies that declared `attach` get a shell; the rest keep the old
    rule, which is that material may join them anywhere. Between two bodies
    whose shells overlap, an attach zone wins over a shell -- the zone is the
    thing the user asked for by name.
    """
    shape = preserve.shape
    attach = np.zeros(shape, bool)
    shells = np.zeros(shape, bool)
    n_cells = max(1, int(np.ceil(float(standoff_mm) / h - 1e-9))) \
        if standoff_mm > 0 else 1
    for b in bodies:
        if b.kind != "preserve" or b.attach is None:
            continue
        # Only the design space can hold material, so only the design space
        # needs a shell -- a face that already looks out of the domain is
        # neither joinable nor worth counting.
        shell = (offset_mask(b.mask, max(standoff_mm, h), h) & ~preserve
                 & design_space)
        zone = attach_zone(b.mask, b.attach, shell, grid, regions, n_cells)
        b.attach_mask = zone
        b.no_attach_mask = shell & ~zone
        attach |= zone
        shells |= shell
    no_attach = shells & ~attach
    return obstacle | no_attach, attach, no_attach, n_cells


# ------------------------------------------------------------------- grid

def _spec_bbox(spec):
    """Bounds a region contributes to the grid, or None per axis where it is
    unbounded (an infinite cylinder, or a `face`/`all` region that only means
    something once the grid exists)."""
    kind = spec.get("type", "box").lower()
    lo = [None, None, None]
    hi = [None, None, None]
    if kind == "box":
        mn, mx = spec.get("min"), spec.get("max")
        for a in range(3):
            if mn is not None and mn[a] is not None:
                lo[a] = float(mn[a])
            if mx is not None and mx[a] is not None:
                hi[a] = float(mx[a])
    elif kind == "sphere":
        c, r = np.array(spec["centre"], float), float(spec["r"])
        for a in range(3):
            lo[a], hi[a] = c[a] - r, c[a] + r
    elif kind == "cylinder":
        axis = {"X": 0, "Y": 1, "Z": 2}[str(spec.get("axis", "Z")).upper()]
        c, r = np.array(spec["centre"], float), float(spec["r"])
        for a in range(3):
            if a != axis:
                lo[a], hi[a] = c[a] - r, c[a] + r
        rng = spec.get("range")
        if rng:
            lo[axis], hi[axis] = float(rng[0]), float(rng[1])
    return lo, hi


def _grid_extent(bodies, pad_mm):
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for b in bodies:
        if b.kind == "starting_shape":
            continue
        grow = b.clearance_mm
        if b.stl_path is not None:
            b.tris = load_stl(b.stl_path)
            v = b.tris.reshape(-1, 3)
            lo = np.minimum(lo, v.min(axis=0) - grow)
            hi = np.maximum(hi, v.max(axis=0) + grow)
        else:
            blo, bhi = _spec_bbox(b.spec)
            for a in range(3):
                if blo[a] is not None:
                    lo[a] = min(lo[a], blo[a] - grow)
                if bhi[a] is not None:
                    hi[a] = max(hi[a], bhi[a] + grow)
    if not np.isfinite(lo).all() or not np.isfinite(hi).all():
        raise ValueError(
            "could not bound the design space -- at least one body must have "
            "finite extent in every axis (an STL, a box, or a cylinder with a "
            "'range')")
    return lo - pad_mm, hi + pad_mm


def build_setup(study, geom=None, target_elements=None, voxel_mm=None,
                verbose=True, symmetry=None):
    """Resolve a study's `geometry` block into a `Setup` on one shared grid.

    `symmetry` (a list of "X"/"Y"/"Z") is needed *here*, not just in the
    optimiser: mirroring is done by flipping the array, which only equals a
    mirror about the geometry's mid-plane when the grid is centred on it. The
    grid is snapped so that it is.
    """
    geom = geom if geom is not None else dict(study.raw.get("geometry", {}))
    if not geom:
        raise ValueError("study has no 'geometry' block")
    if symmetry is None:
        symmetry = (study.raw.get("design", {}) or {}).get("symmetry") or []

    default_clearance = float(geom.get("clearance_mm", 0.0))
    bodies = parse_geometry(geom, study.regions, study.base_dir,
                            default_clearance)
    if not any(b.kind == "design_space" for b in bodies):
        raise ValueError("geometry needs at least one 'design_space' entry")

    pad_vox = int(geom.get("pad_voxels", 2))
    target = int(target_elements or geom.get("target_elements")
                 or study.target_elements)
    h = voxel_mm or geom.get("voxel_mm") or study.resolution_mm

    lo, hi = _grid_extent(bodies, 0.0)
    size = np.maximum(hi - lo, 1e-9)
    if h is None:
        h = float((size.prod() / max(target, 1)) ** (1.0 / 3.0))
    h = float(max(h, 1e-3))
    origin = lo - pad_vox * h
    shape = np.maximum(np.ceil((hi + pad_vox * h - origin) / h).astype(int), 1)

    # Snap the grid so a mirror axis' mid-plane sits exactly on the array
    # centre. Without this, np.flip mirrors about whatever plane the rounding
    # happened to leave, the mirrored partner of a free cell is usually not a
    # free cell, and the symmetry constraint silently does nothing at all.
    sym_axes = [{"X": 0, "Y": 1, "Z": 2}[str(a).upper()] for a in symmetry]
    for a in sym_axes:
        c = 0.5 * (lo[a] + hi[a])
        n_half = int(np.ceil((c - origin[a]) / h))
        n_half = max(n_half, int(np.ceil((hi[a] + pad_vox * h - c) / h)), 1)
        origin[a] = c - n_half * h
        shape[a] = 2 * n_half

    nx, ny, nz = (int(v) for v in shape)
    full = Grid(solid=np.ones((nx, ny, nz), bool), h=h, origin=origin)
    if verbose:
        print("grid %d x %d x %d at %.3f mm (origin %s)%s"
              % (nx, ny, nz, h, np.round(origin, 2).tolist(),
                 ", mirror plane on %s" % "+".join(str(s).upper() for s in symmetry)
                 if symmetry else ""))

    for b in bodies:
        if b.tris is not None:
            b.mask = voxelize_stl(b.tris, h=h, origin=origin, shape=shape).solid
        elif b.stl_path is not None:
            b.mask = voxelize_stl(b.stl_path, h=h, origin=origin,
                                  shape=shape).solid
        else:
            b.mask = region_mask(full, b.spec, study.regions, clip=False)
        if b.kind == "obstacle" and b.clearance_mm > 0:
            b.mask_offset = offset_mask(b.mask, b.clearance_mm, h)
        else:
            b.mask_offset = b.mask

    def union(kind, offset=False):
        m = np.zeros((nx, ny, nz), bool)
        for b in bodies:
            if b.kind == kind:
                m |= (b.mask_offset if offset else b.mask)
        return m

    design_space = union("design_space")
    preserve = union("preserve")
    obstacle_raw = union("obstacle")
    obstacle = union("obstacle", offset=True)
    starting = union("starting_shape")

    # An obstacle is often a *feature of* a preserve body -- the bolt bore through
    # a mounting boss, the slot a tab slides into. In CAD the two are exact
    # complements, but voxelising the two meshes separately makes them disagree
    # by up to one cell along the surface they share. Preserve wins there: that
    # cell was material in the CAD, and rule 2 does not bend for rounding.
    # (`impose` applies obstacle after preserve, so without this the shared
    # shell would be zeroed and the boss would lose its skin.)
    obstacle_in_preserve = obstacle & preserve
    obstacle = obstacle & ~preserve

    # Where the optimiser may join each preserve body. A body that declared
    # `attach` gets a no-contact shell everywhere else, and that shell is an
    # obstacle from here on: the optimiser cannot put material in it, the
    # threshold cannot keep material in it, and the finish stage cannot
    # smooth material into it. Without this the design space touches the
    # whole surface of every interface and the result grows onto faces that
    # have to stay clear -- the top of a camera mount, the flanks of a boss.
    standoff_mm = float(geom.get("attach_standoff_mm", 0.0) or 0.0)
    obstacle, attach, no_attach, n_standoff = _apply_attach(
        bodies, preserve, obstacle, design_space, full, study.regions, h,
        standoff_mm)

    # The FE domain is design space plus preserve. Obstacle cells stay *in* the
    # domain but are pinned to zero density: deleting them would carve holes in
    # the mesh and can strand islands of elements, and a void element at
    # e_min stiffness costs one element and no correctness.
    domain = (design_space | preserve)
    grid = Grid(solid=domain, h=h, origin=origin)

    free = domain & ~preserve & ~obstacle
    # "Down" is the build axis unless the study says otherwise: the part is
    # printed standing on that plane and, for anything that stands up, that
    # is also the plane it stands on. `ground_axis` decouples the two so the
    # build direction can be varied on a standing part (spec Phase 6).
    g_axis = int(getattr(study, "ground_axis", study.build_axis))
    ground, ground_mm = ground_mask(domain, g_axis, h, origin,
                                    geom.get("ground_depth_mm"))
    setup = Setup(grid=grid, bodies=bodies, design_space=design_space,
                  preserve=preserve, obstacle=obstacle,
                  obstacle_raw=obstacle_raw,
                  starting_shape=starting & domain, free=free,
                  ground=ground, ground_axis=g_axis, ground_mm=ground_mm,
                  attach=attach & domain, no_attach=no_attach & domain,
                  attach_standoff_mm=n_standoff * h)
    setup.symmetry = [str(a).upper() for a in symmetry]
    setup.obstacle_in_preserve = obstacle_in_preserve
    return setup


def ground_mask(domain, axis, h, origin, depth_mm=None):
    """The band of domain cells that sits on the plate. -> (mask, plane_mm)

    "The plate" is the lowest face of the domain along the build axis -- the
    plane the part is printed on and, for anything that stands up, the plane it
    stands on. `depth_mm` is how thick the contact band is; one voxel by
    default, because a support is a boundary condition on a surface and any
    more than that starts fixing structure rather than contact.

    This exists so that a support does not have to name *where* the feet are.
    The rule that loads and supports may only act on preserve is there because
    the optimiser can delete free material out from under them; a ground
    support keeps that protection differently -- it can only act on the contact
    plane, and if the optimiser puts nothing there, the part is unsupported and
    the compliance says so immediately.
    """
    m = np.zeros(np.asarray(domain).shape, bool)
    idx = np.argwhere(domain)
    if idx.size == 0:
        return m, float(origin[axis])
    k0 = int(idx[:, axis].min())
    n = 1 if not depth_mm else max(1, int(np.ceil(float(depth_mm) / h)))
    sl = [slice(None)] * 3
    sl[axis] = slice(k0, k0 + n)
    m[tuple(sl)] = np.asarray(domain, bool)[tuple(sl)]
    return m, float(origin[axis] + k0 * h)


def offset_mask(mask, clearance_mm, h):
    """Grow a mask by a Euclidean clearance, measured in mm on the grid.

    The offset is quantised to the voxel size -- a clearance finer than about
    half a voxel cannot be represented and would silently vanish, so the caller
    is expected to report `achieved_clearance_mm` alongside the request.
    """
    if clearance_mm <= 0 or not mask.any():
        return mask
    r = clearance_mm / h
    return distance_transform_edt(~mask) <= max(r, 1.0)


def achieved_clearance(clearance_mm, h):
    """What `offset_mask` will actually deliver for a request of this size."""
    if clearance_mm <= 0:
        return 0.0
    return max(clearance_mm, h)


# ------------------------------------------------------------- validation

def _bodies_of(setup, kind):
    return [b for b in setup.bodies if b.kind == kind]


def _mm3(mask, h):
    return float(mask.sum()) * h ** 3


def _components(mask):
    lab, n = label(mask, structure=_CONN)
    return lab, n


def bbox_mm(mask, origin, h):
    """Axis-aligned extent of a voxel mask in mm -> (lo, hi, size), or None.

    Measured from the mask, never from the grid shape: the grid carries
    `pad_voxels` of empty margin, and quoting that as the part size overstates
    every dimension by a fixed amount that looks like a real measurement.
    """
    mask = np.asarray(mask)
    if not mask.any():
        return None
    idx = np.argwhere(mask)
    lo = np.asarray(origin, float) + idx.min(axis=0) * h
    hi = np.asarray(origin, float) + (idx.max(axis=0) + 1) * h
    return lo, hi, hi - lo


def build_volume_report(setup, study, printer=None):
    """Will anything this study can produce come out of the machine?

    Answerable in the first second of a run, and until now it was not asked
    until after one. Two bounds, and they answer different questions:

    - **preserve** is in every candidate shape. If its bounding box does not
      fit, then no result of this study fits, whatever the optimiser does.
    - **the domain** contains every candidate shape. If it fits, every result
      fits, whatever the optimiser does.

    Between the two the honest answer is "it depends what the optimiser
    decides", and that is not an answer anyone can act on after the solve has
    already cost hours. So a domain larger than the machine is an error by
    default; `printer.allow_oversize_design_space` downgrades it for parts that
    are meant to be split, which is a decision worth writing down.

    Orientation is not a loophole. A part that fits only lying on its side has
    to be *printed* lying on its side, which puts the layers somewhere else
    than the declared `build_axis` -- so the orthotropic analysis this study is
    about to run would no longer describe the object. That is an error too, and
    the fix is to re-declare the build axis and re-run, not to rotate quietly.

    -> (info dict, errors, warnings)
    """
    from . import printers

    errors, warnings = [], []
    p = printer if printer is not None else study.printer
    h, grid = setup.h, setup.grid
    axis_name = "XYZ"[study.build_axis]

    dom = bbox_mm(grid.solid, setup.origin, h)
    pres = bbox_mm(setup.preserve, setup.origin, h)
    if dom is None:
        return {"printer": p.as_dict(), "note": "empty domain"}, errors, warnings

    info = {"printer": p.as_dict(), "build_axis": axis_name,
            "domain": printers.fit(dom[2], p, axis_name, "design space")}
    info["domain"]["lo_mm"] = [round(float(v), 2) for v in dom[0]]
    info["domain"]["hi_mm"] = [round(float(v), 2) for v in dom[1]]
    if pres is not None:
        info["preserve"] = printers.fit(pres[2], p, axis_name, "preserve")

    env = "%.0f x %.0f x %.0f mm" % tuple(p.envelope_mm)
    size = lambda b: "%.1f x %.1f x %.1f mm" % tuple(b[2])          # noqa: E731

    def over(fitrep):
        bad = {k: v for k, v in (fitrep.get("overflow_mm") or {}).items()
               if v > 1e-6}
        return ", ".join("%s by %.1f mm" % (k, v)
                         for k, v in sorted(bad.items())) or "-"

    # ---- preserve: the floor. If this does not fit, nothing does.
    if pres is not None and not info["preserve"]["fits_as_declared"]:
        alt = info["preserve"]["up_axes_that_fit"]
        if alt:
            errors.append(
                "the preserve geometry is %s and does not fit %s (%s) on %s "
                "with %s up, the declared build axis. It would fit with %s up "
                "-- but printing it that way moves the layer planes, and the "
                "material axes this study solves with are tied to build_axis "
                "%s. Re-declare `build_axis` as %s and re-run, or make the "
                "part smaller. Rotating it afterwards silently separates the "
                "analysis from the object."
                % (size(pres), env, p.name, over(info["preserve"]), axis_name,
                   " or ".join(alt), axis_name, alt[0]))
        else:
            errors.append(
                "the preserve geometry is %s and fits %s (%s) in no "
                "orientation at all (%s). Preserve is in every candidate "
                "shape, so no result of this study can be printed in one "
                "piece. Shrink the part, choose a bigger machine, or split it "
                "deliberately and analyse the joint."
                % (size(pres), env, p.name, over(info["preserve"])))

    # ---- the domain: the ceiling. Anything inside it is fair game for the
    # optimiser, so an oversize design space is a result you cannot predict.
    elif not info["domain"]["fits_as_declared"]:
        msg = ("the design space is %s and does not fit %s (%s) with %s up "
               "(%s). The optimiser may put material anywhere in the design "
               "space, so whether the result fits would only be known after "
               "the solve. Draw a design space the machine can print."
               % (size(dom), env, p.name, axis_name, over(info["domain"])))
        if p.allow_oversize_design_space:
            warnings.append(
                msg + " Allowed here by `printer.allow_oversize_design_space`: "
                      "this part is expected to be split, and the joint has to "
                      "be analysed separately.")
        else:
            errors.append(
                msg + " If this part is meant to be split, say so with "
                      '`"printer": {..., "allow_oversize_design_space": true}` '
                      "so the choice is on the record.")
    else:
        m = info["domain"].get("margin_to_envelope_mm")
        if m is not None and m < 5.0:
            warnings.append(
                "the design space clears the build volume by only %.1f mm. "
                "Brims, elephant-foot compensation and plate-edge adhesion all "
                "eat into that." % m)

    # ---- can the optimiser even be asked for a printable member?
    filt = float((study.raw.get("design") or {}).get("filter_mm", 0) or 0)
    if filt and filt < p.min_feature_mm:
        warnings.append(
            "filter_mm is %.2f mm but %s cannot make a wall below %.2f mm "
            "(two %.1f mm perimeters). The optimiser is free to produce "
            "members the printer will not reproduce."
            % (filt, p.name, p.min_feature_mm, p.nozzle_mm))
    if h > p.min_feature_mm:
        info["voxel_vs_min_feature"] = (
            "one voxel is %.2f mm and the smallest printable wall is %.2f mm, "
            "so the grid cannot represent the printer's limit; minimum-feature "
            "checks on this run are about the grid, not the machine."
            % (h, p.min_feature_mm))
    return info, errors, warnings


def freedom_report(setup, study):
    """How much of this result was already decided before the solver started?

    A topology optimisation only answers the question it is given room to
    answer. Three ways that room goes missing, all of them invisible in the
    output because the result still looks like an optimisation:

    - **the design space is too narrow.** If the free region is a thin skin
      around preserve, the outline decides the topology and the optimiser only
      rounds the corners.
    - **preserve is most of the part.** Then the shape is a CAD model with
      fillets, and reporting it as a generative result overstates what was
      optimised.
    - **the loads and supports are pinned.** Support footprints must sit on
      preserve, so their *positions* are chosen by whoever drew the CAD. The
      optimiser decides how to reach the feet, never where the feet go. That
      is a legitimate way to run a study; it is not legitimate to leave it
      unsaid, because a reader assumes an optimised layout was optimised.

    -> (info dict, warnings)
    """
    warnings = []
    h = setup.h
    free_v = int(setup.free.sum())
    dom_v = int(setup.grid.solid.sum())
    pres_mm3 = _mm3(setup.preserve, h)
    free_mm3 = _mm3(setup.free, h)
    volfrac = float((study.raw.get("design") or {}).get("volfrac", 0.3) or 0.3)

    expected = pres_mm3 + volfrac * free_mm3
    info = {
        "free_voxels": free_v, "domain_voxels": dom_v,
        "free_fraction_of_domain": round(free_v / max(dom_v, 1), 4),
        "volfrac": volfrac,
        "expected_final_mm3": round(expected, 1),
        "preserve_mm3": round(pres_mm3, 1),
        "preserve_fraction_of_expected_part":
            round(pres_mm3 / expected, 4) if expected > 0 else None,
    }

    frac = info["free_fraction_of_domain"]
    if frac < 0.2:
        warnings.append(
            "the optimiser may decide only %.0f %% of the domain; the rest is "
            "preserve or blocked by obstacles. Whatever comes out is mostly "
            "the CAD model, not the optimisation -- widen the design space if "
            "you want the layout to be an answer rather than an input."
            % (100 * frac))
    elif frac < 0.5:
        warnings.append(
            "only %.0f %% of the domain is free for the optimiser to decide."
            % (100 * frac))

    pf = info["preserve_fraction_of_expected_part"]
    if pf is not None and pf > 0.5:
        warnings.append(
            "preserve is %.0f %% of the expected final part (%.0f mm3 of "
            "%.0f mm3 at volfrac %.2f). Most of this shape is drawn, not "
            "optimised; say so wherever the result is presented."
            % (100 * pf, pres_mm3, expected, volfrac))

    # ---- is the free region thick enough to hold a decision?
    fm = min_member_size(setup.free, h) if setup.free.any() else None
    if fm:
        vox = fm["min_mm"] / h
        info["free_min_thickness_mm"] = fm["min_mm"]
        info["free_median_thickness_mm"] = fm["median_mm"]
        info["free_min_thickness_voxels"] = round(vox, 2)
        if vox < 3.0:
            warnings.append(
                "the free region is only %.1f voxels (%.2f mm) thick at its "
                "thinnest. Below about 3 voxels there is no room for a hole, "
                "so the topology there is set by the outline rather than "
                "chosen -- refine the grid or widen the design space."
                % (vox, fm["min_mm"]))
        filt = float((study.raw.get("design") or {}).get("filter_mm", 0) or 0)
        if filt and fm["median_mm"] < filt:
            warnings.append(
                "the free region's median thickness is %.2f mm, below the "
                "%.2f mm filter radius. The filter cannot form a member "
                "narrower than itself, so most of the free region will come "
                "out grey and the threshold, not the optimisation, will decide "
                "what is material." % (fm["median_mm"], filt))

    # ---- what the boundary conditions pinned down
    #
    # A ground-contact support is deliberately NOT in this list: it acts on
    # whatever the optimiser puts on the plate, so it does not pin a position.
    # It does pin a *zone*, though, and that is still an input -- reported
    # separately below rather than folded in here, because "the CAD chose this
    # point" and "the study chose this region to look in" are different claims.
    fixed, seen = [], set()
    ground_zones = []
    for case in study.cases:
        for tag, items in (("support", case.supports), ("load", case.loads)):
            for it in items:
                spec = it.get("region", it) if isinstance(it, dict) else it
                name = spec if isinstance(spec, str) else "<inline>"
                key = (tag, name)
                if tag == "support" and isinstance(it, dict) \
                        and str(it.get("contact", "")).lower() == "ground":
                    if key in seen:
                        continue
                    seen.add(key)
                    gm = region_mask(setup.grid, spec, study.regions,
                                     clip=True) & setup.ground
                    if not gm.any():
                        continue
                    idx = np.argwhere(gm)
                    centre = setup.origin + (idx.mean(axis=0) + 0.5) * h
                    ground_zones.append({
                        "region": name,
                        "centroid_mm": [round(float(v), 1) for v in centre],
                        "area_mm2": round(float(
                            gm.any(axis=setup.ground_axis).sum()) * h * h, 1)})
                    continue
                m = region_mask(setup.grid, spec, study.regions, clip=True)
                m = m & setup.preserve
                if not m.any():
                    continue
                if key in seen:
                    continue
                seen.add(key)
                idx = np.argwhere(m)
                centre = setup.origin + (idx.mean(axis=0) + 0.5) * h
                fixed.append({"kind": tag, "region": name,
                              "centroid_mm": [round(float(v), 1)
                                              for v in centre],
                              "mm3": round(_mm3(m, h), 1)})
    info["fixed_points"] = fixed
    info["ground_zones"] = ground_zones
    if ground_zones:
        info["note_ground_zones"] = (
            "%d support(s) act on the build plane rather than on preserve, so "
            "where the part touches down is a result. The zones themselves are "
            "still an input: this study says to look for contact inside them."
            % len(ground_zones))
        warnings.append(
            "%d support(s) use ground contact, so the feet are not placed by "
            "the CAD. What the study still chooses is the %d zone(s) to look "
            "in (%s). If a partial-support case appears at only one azimuth, "
            "check the result actually stands up -- a glued contact cannot "
            "tip, so nothing in the objective asks for a wide stance."
            % (len(ground_zones), len(ground_zones),
               "; ".join("%s at %s" % (z["region"], z["centroid_mm"])
                         for z in ground_zones)))
    if fixed:
        sup = [f for f in fixed if f["kind"] == "support"]
        info["note_fixed_points"] = (
            "%d load/support footprint(s) sit on preserve at positions the CAD "
            "model chose. The optimiser decides how to reach them, not where "
            "they are." % len(fixed))
        if sup:
            warnings.append(
                "the %d support point(s) are fixed by preserve at %s. Where "
                "the part is held is an input to this study, not a result of "
                "it -- if the layout of the supports is itself meant to be "
                "optimised, that decision is currently being made in CAD."
                % (len(sup), "; ".join("%s at %s" % (s["region"],
                                                     s["centroid_mm"])
                                       for s in sup)))
    return info, warnings


def validate(setup, study, strict_bc=False):
    """Run every setup check. Returns a report dict with `errors`/`warnings`.

    Nothing here raises: the caller decides whether to stop, and the report is
    written next to the preview so a failed setup leaves evidence behind.
    """
    errors, warnings, info = [], [], {}
    h = setup.h
    grid = setup.grid

    info["voxel_mm"] = h
    info["grid_shape"] = list(setup.grid.shape)
    info["origin_mm"] = [round(float(v), 3) for v in setup.origin]
    info["design_space_mm3"] = round(_mm3(setup.design_space, h), 1)
    info["preserve_mm3"] = round(_mm3(setup.preserve, h), 1)
    info["obstacle_mm3"] = round(_mm3(setup.obstacle, h), 1)
    info["obstacle_declared_mm3"] = round(_mm3(setup.obstacle_raw, h), 1)
    info["domain_mm3"] = round(_mm3(grid.solid, h), 1)
    info["free_mm3"] = round(_mm3(setup.free, h), 1)
    info["free_voxels"] = int(setup.free.sum())
    info["domain_voxels"] = int(grid.solid.sum())
    info["ground"] = {
        "axis": "XYZ"[setup.ground_axis],
        "plane_mm": round(setup.ground_mm, 3),
        "band_voxels": int(setup.ground.sum()),
        "band_area_mm2": round(float(setup.ground.any(axis=setup.ground_axis)
                                     .sum()) * h * h, 1)}

    if not grid.solid.any():
        errors.append("the domain (design_space | preserve) is empty -- check "
                      "the STL units and coordinates")
        return {"errors": errors, "warnings": warnings, "info": info,
                "bodies": [], "ok": False}
    if not setup.free.any():
        errors.append("no free cells: obstacles and preserve fill the whole "
                      "design space, so there is nothing to optimise")

    # ---- per-body detail
    body_rows = []
    for b in setup.bodies:
        m = b.mask_offset if b.kind == "obstacle" else b.mask
        row = {"name": b.name, "kind": b.kind, "source": b.source,
               "voxels": int(m.sum()), "mm3": round(_mm3(m, h), 1)}
        if m.any():
            idx = np.argwhere(m)
            row["bbox_mm"] = [
                [round(float(setup.origin[a] + idx[:, a].min() * h), 2)
                 for a in range(3)],
                [round(float(setup.origin[a] + (idx[:, a].max() + 1) * h), 2)
                 for a in range(3)]]
        if b.kind == "obstacle":
            row["clearance_requested_mm"] = b.clearance_mm
            row["clearance_achieved_mm"] = round(
                achieved_clearance(b.clearance_mm, h), 3)
            if 0 < b.clearance_mm < h:
                warnings.append(
                    "obstacle %r: clearance %.2f mm is below the voxel size "
                    "%.2f mm, so it was rounded up to one voxel (%.2f mm). "
                    "Refine the grid if that matters."
                    % (b.name, b.clearance_mm, h, h))
        if m.sum() == 0:
            warnings.append("%s %r voxelised to nothing -- wrong units, wrong "
                            "coordinates, or a non-watertight STL?"
                            % (b.kind, b.name))

        # A leaky input mesh does not fail -- ray-parity fill degrades to a few
        # stray voxels -- so it would otherwise surface much later as an
        # inexplicable speckle. Name the file while the reader still knows
        # which one they passed.
        if b.tris is not None and b.stl_path:
            from .geom import shell_report
            sh = shell_report(b.tris)
            row["mesh"] = sh
            # Only *open* edges matter here. A non-manifold edge is two solids
            # meeting along a line -- normal in CSG output, and ray parity
            # handles it exactly. Warning about it would put a line on every
            # osmo_tripod run and teach the reader to skip the warnings, which
            # costs more than it catches.
            if sh["open_edges"]:
                warnings.append(
                    "%s %r is not watertight: %s has %d open edge(s) of %d "
                    "triangles. Voxelisation fills by ray parity, so the result "
                    "will be approximately right with stray voxels near the "
                    "hole. Repair the mesh if this body matters dimensionally."
                    % (b.kind, b.name, os.path.basename(b.stl_path),
                       sh["open_edges"], sh["triangles"]))
        body_rows.append(row)

    # ---- attach: where each preserve body may be joined
    #
    # A body that declared `attach` is joinable only through its attach zone;
    # the rest of its surface got a no-contact shell (now obstacle). Two
    # things can go wrong and both are setup errors, not results: the zone
    # names a face that is outside the design space or under an obstacle, so
    # no material can ever reach the body; or it was written as an empty list.
    attach_rows = {}
    for pb in _bodies_of(setup, "preserve"):
        if pb.attach is None:
            continue
        zone = pb.attach_mask if pb.attach_mask is not None \
            else np.zeros(grid.shape, bool)
        shell = pb.no_attach_mask if pb.no_attach_mask is not None \
            else np.zeros(grid.shape, bool)
        usable = zone & setup.design_space & ~setup.obstacle
        row = {"specs": pb.attach,
               "attach_voxels": int(zone.sum()),
               "attach_usable_voxels": int(usable.sum()),
               "attach_area_mm2": round(float(usable.sum()) * h * h, 1),
               "no_attach_voxels": int(shell.sum()),
               "no_attach_mm3": round(_mm3(shell, h), 1),
               "standoff_mm": round(setup.attach_standoff_mm, 3)}
        attach_rows[pb.name] = row
        for r in body_rows:
            if r["kind"] == "preserve" and r["name"] == pb.name:
                r["attach"] = row
        if not pb.attach:
            errors.append(
                "preserve %r: 'attach' is empty, so no face of it may be "
                "joined and nothing the optimiser adds can ever reach it. "
                "Name a face (\"-Z\") or a region, or drop the key to allow "
                "joining anywhere." % pb.name)
        elif not usable.any():
            errors.append(
                "preserve %r: its attach zone %s selects no free design-space "
                "cell next to the body (%d cell(s) in the zone, all outside "
                "the design space or inside an obstacle). Nothing can ever "
                "connect to it -- check the face direction against the body, "
                "or extend the design space to that face."
                % (pb.name, pb.attach, int(zone.sum())))
    if attach_rows:
        info["attach"] = attach_rows
        info["no_attach_mm3"] = round(_mm3(setup.no_attach, h), 1)
        info["attach_standoff_mm"] = round(setup.attach_standoff_mm, 3)

    # ---- rule 5.1: preserve must not intersect an obstacle
    #
    # With one deliberate exception. A hole *belongs to* the body it is drilled
    # through, and voxelising the boss and the bore as separate meshes makes
    # them disagree by a cell along the surface they share. A shared surface
    # gives a one-cell shell, which erodes to nothing; a body genuinely driven
    # through another gives a solid core that does not. Only the second is a
    # setup mistake -- so erode before judging.
    from scipy.ndimage import binary_erosion
    for pb in _bodies_of(setup, "preserve"):
        for ob in _bodies_of(setup, "obstacle"):
            hit = pb.mask & ob.mask_offset
            if not hit.any():
                continue
            core = binary_erosion(hit, structure=_CONN)
            raw = pb.mask & ob.mask
            if core.any():
                errors.append(
                    "preserve %r intersects obstacle %r over %.1f mm3, %.1f mm3 "
                    "of it more than one cell thick (%.1f mm3 is the declared "
                    "body, the rest its %.2f mm clearance). Move one of them, "
                    "or reduce the clearance."
                    % (pb.name, ob.name, _mm3(hit, h), _mm3(core, h),
                       _mm3(raw, h), ob.clearance_mm))
            else:
                warnings.append(
                    "obstacle %r shares a one-cell boundary layer with preserve "
                    "%r (%.1f mm3) -- that is the voxelisation of a surface the "
                    "two meshes have in common, as when %r is a feature cut into "
                    "%r. Preserve keeps those cells."
                    % (ob.name, pb.name, _mm3(hit, h), ob.name, pb.name))

    # ---- rule 5.2: preserve outside the design space
    outside = setup.preserve & ~setup.design_space
    info["preserve_outside_design_space_mm3"] = round(_mm3(outside, h), 1)
    if outside.any():
        frac = 100.0 * outside.sum() / max(setup.preserve.sum(), 1)
        for pb in _bodies_of(setup, "preserve"):
            po = pb.mask & ~setup.design_space
            if not po.any():
                continue
            pf = 100.0 * po.sum() / max(pb.mask.sum(), 1)
            msg = ("preserve %r is %.0f %% outside the design space (%.1f mm3)"
                   % (pb.name, pf, _mm3(po, h)))
            # Not a defect. Preserve is "geometry the design cannot change",
            # not "part of the design space", so an interface whose shape is
            # decided by the mating part routinely sticks out. Say what happens
            # to it and leave the judgement to the reader, rather than phrasing
            # a normal setup as a mistake.
            if pf > 99.5:
                warnings.append(
                    msg + " -- entirely outside, which is fine in itself: it "
                    "becomes material either way. But the optimiser can only "
                    "connect to it where it touches the design space, so check "
                    "that it does.")
            else:
                warnings.append(
                    msg + " -- normal for an interface whose shape the mating "
                    "part dictates. The protruding part is kept as material; "
                    "no material can be added to it. Worth a look only if you "
                    "expected the design space to cover it.")
        info["preserve_outside_design_space_pct"] = round(frac, 1)

    # ---- rule 5.3: preserve bodies, and whether they can ever be joined
    plab, n_pres = _components(setup.preserve)
    info["preserve_bodies"] = int(n_pres)
    dlab, n_dom = _components(grid.solid)
    info["domain_components"] = int(n_dom)
    if n_pres:
        comps = {}
        for i in range(1, n_pres + 1):
            m = plab == i
            c = int(np.bincount(dlab[m]).argmax())
            comps.setdefault(c, []).append(i)
            names = sorted({b.name for b in _bodies_of(setup, "preserve")
                            if (b.mask & m).any()})
            body_rows.append({"name": "preserve body %d" % i,
                              "kind": "preserve_component",
                              "source": ", ".join(names) or "-",
                              "voxels": int(m.sum()),
                              "mm3": round(_mm3(m, h), 1),
                              "domain_component": c})
        info["preserve_bodies_by_domain_component"] = {
            str(k): v for k, v in comps.items()}
        if len(comps) > 1:
            errors.append(
                "the %d preserve bodies fall into %d disconnected parts of the "
                "domain (%s). No material the optimiser can add will ever join "
                "them -- extend the design space, or accept that this is more "
                "than one part."
                % (n_pres, len(comps),
                   "; ".join("component %s holds preserve %s" % (k, v)
                             for k, v in comps.items())))
        elif n_pres > 1:
            info["note_preserve_bodies"] = (
                "%d separate preserve bodies, all reachable through the design "
                "space -- that is the normal generative-design setup."% n_pres)

    # ---- how much of the design space the obstacles ate
    blocked = setup.design_space & setup.obstacle
    if blocked.any():
        pct = 100.0 * blocked.sum() / max(setup.design_space.sum(), 1)
        info["design_space_blocked_pct"] = round(pct, 1)
        if pct > 60:
            warnings.append("obstacles (with clearance) block %.0f %% of the "
                            "design space" % pct)

    # ---- rule 2/3: loads and supports may only touch preserve
    bc_rows = []
    for case in study.cases:
        for tag, items in (("support", case.supports), ("load", case.loads)):
            for it in items:
                spec = it.get("region", it) if isinstance(it, dict) else it
                m_all = region_mask(grid, spec, study.regions, clip=False)
                m_dom = m_all & grid.solid
                name = spec if isinstance(spec, str) else "<inline>"
                on_ground = (tag == "support" and isinstance(it, dict)
                             and str(it.get("contact", "")).lower() == "ground")
                row = {"case": case.name, "kind": tag, "region": name,
                       "voxels_in_domain": int(m_dom.sum()),
                       "voxels_on_preserve": int((m_dom & setup.preserve).sum())}
                if on_ground:
                    row["contact"] = "ground"
                    row["voxels_on_ground"] = int((m_dom & setup.ground).sum())
                bc_rows.append(row)
                if not m_dom.any():
                    errors.append(
                        "%s region %r in case %r selects no material in the "
                        "domain" % (tag, name, case.name))
                    continue

                # A ground support is measured against the contact band, not
                # against preserve. It is the one support that is *allowed* to
                # act on free material -- but only on the plate, and the
                # optimiser has to put something there or the part has no
                # support at all.
                if on_ground:
                    hit = m_dom & setup.ground
                    if not hit.any():
                        errors.append(
                            "support region %r in case %r asks for ground "
                            "contact but does not reach the contact plane at "
                            "%s = %.2f mm. The band is one voxel thick; draw "
                            "the region so it crosses it, or set "
                            "geometry.ground_depth_mm."
                            % (name, case.name, "XYZ"[setup.ground_axis],
                               setup.ground_mm))
                    continue

                on = m_dom & setup.preserve
                off = m_dom & ~setup.preserve
                row["voxels_on_preserve"] = int(on.sum())
                if not on.any():
                    errors.append(
                        "%s region %r in case %r does not touch any preserve "
                        "body. Loads and supports act on preserve geometry "
                        "only, so this one would act on nothing -- and a load "
                        "on free material lets the optimiser delete the thing "
                        "you are pushing on." % (tag, name, case.name))
                elif off.any():
                    pct = 100.0 * off.sum() / m_dom.sum()
                    msg = ("%s region %r in case %r reaches %.0f %% (%.1f mm3) "
                           "beyond preserve -- a hole it crosses, or free "
                           "material. That part is dropped; the %s acts on the "
                           "preserve %.0f %% only."
                           % (tag, name, case.name, pct, _mm3(off, h), tag,
                              100 - pct))
                    (errors if strict_bc else warnings).append(msg)
    info["boundary_conditions"] = bc_rows

    # ---- symmetry: a mirror constraint on an asymmetric setup produces a
    # result that satisfies neither the loads nor the symmetry.
    if getattr(setup, "symmetry", None):
        sym_info = {}
        for a in setup.symmetry:
            i = {"X": 0, "Y": 1, "Z": 2}[a]
            rows = {}
            for nm, m in (("design_space", setup.design_space),
                          ("preserve", setup.preserve),
                          ("obstacle", setup.obstacle)):
                if not m.any():
                    continue
                broken = int((m ^ np.flip(m, axis=i)).sum())
                pct = 100.0 * broken / max(2 * m.sum(), 1)
                rows[nm] = round(pct, 2)
                if pct > 2.0:
                    warnings.append(
                        "symmetry %s is requested but %s is %.0f %% asymmetric "
                        "about that plane -- the mirror-average will pull the "
                        "result away from the geometry you declared"
                        % (a, nm, pct))
            sym_info[a] = rows
        info["symmetry_mismatch_pct"] = sym_info

    # ---- starting shape
    ss = _bodies_of(setup, "starting_shape")
    if ss:
        out = setup.starting_shape & ~setup.free & ~setup.preserve
        if out.any():
            warnings.append(
                "the starting shape overlaps obstacles by %.1f mm3; that part "
                "of it is ignored" % _mm3(out, h))
        info["starting_shape_mm3"] = round(_mm3(setup.starting_shape, h), 1)

    # ---- can the machine print it, and did the setup leave anything to decide?
    # Both run last so that a broken domain reports as a broken domain rather
    # than as ten downstream complaints about a shape that does not exist.
    try:
        bv, bv_err, bv_warn = build_volume_report(setup, study)
        info["build_volume"] = bv
        errors.extend(bv_err)
        warnings.extend(bv_warn)
    except Exception as exc:                                      # noqa: BLE001
        # An unreadable `printer` block is the user's input, not a broken
        # setup: report it and let the rest of the checks still run.
        warnings.append("the build volume could not be checked: %s" % exc)
        info["build_volume"] = {"error": str(exc)}

    fr, fr_warn = freedom_report(setup, study)
    info["design_freedom"] = fr
    warnings.extend(fr_warn)

    report = {"schema": "topopt/setup/1",
              "ok": not errors, "errors": errors, "warnings": warnings,
              "info": info, "bodies": body_rows}
    setup.report = report
    return report


def promote_bc_to_preserve(setup, study):
    """Fold every load and support footprint into preserve.

    This is what the pre-`geometry` optimiser did implicitly, and it is what you
    do *not* want by default: it silently invents preserve geometry to match
    whatever region was drawn, which is exactly the mistake the setup checks
    exist to surface. Kept for callers reproducing legacy behaviour.
    """
    added = np.zeros(setup.grid.shape, bool)
    for case in study.cases:
        for it in list(case.supports) + list(case.loads):
            spec = it.get("region", it) if isinstance(it, dict) else it
            added |= region_mask(setup.grid, spec, study.regions, clip=True)
    added &= ~setup.obstacle
    setup.preserve = setup.preserve | added
    setup.free = setup.grid.solid & ~setup.preserve & ~setup.obstacle
    return int(added.sum())


# ------------------------------------------------------------ connectivity

def connectivity_report(mask, setup):
    """Does the finished geometry actually hold the preserve bodies together?

    Returns the report plus the mask trimmed to the components that carry
    preserve material -- floating islands are structurally useless and would
    print as loose debris.
    """
    out = {}
    if not mask.any():
        return {"connected": False, "note": "the result is empty"}, mask

    lab, n = _components(mask)
    out["components"] = int(n)
    sizes = np.bincount(lab.ravel())

    plab, n_pres = _components(setup.preserve)
    out["preserve_bodies"] = int(n_pres)

    carrying = set()
    bodies = []
    for i in range(1, n_pres + 1):
        pm = plab == i
        comp = int(np.bincount(lab[pm]).argmax()) if lab[pm].any() else 0
        kept = float((pm & mask).sum()) / max(pm.sum(), 1)
        carrying.add(comp)
        bodies.append({"body": i, "component": comp,
                       "preserve_voxels": int(pm.sum()),
                       "kept_fraction": round(kept, 4)})
        if kept < 0.999:
            out.setdefault("violations", []).append(
                "preserve body %d lost %.1f %% of its material in the final "
                "geometry" % (i, 100 * (1 - kept)))
    out["preserve_body_components"] = bodies
    out["connected"] = (len(carrying) <= 1) and 0 not in carrying

    wanted = sorted(c for c in carrying if c > 0)
    # With no preserve at all there is nothing to anchor to, so fall back to the
    # largest blob rather than throwing the whole result away.
    keep = np.isin(lab, wanted) if wanted else (lab == int(np.argmax(sizes[1:])) + 1)
    islands = mask & ~keep
    out["island_voxels"] = int(islands.sum())
    out["island_mm3"] = round(_mm3(islands, setup.h), 1)
    if islands.any():
        out.setdefault("violations", []).append(
            "%.1f mm3 of material is not connected to any preserve body; it "
            "was dropped from the final geometry" % out["island_mm3"])
    if not out["connected"]:
        out.setdefault("violations", []).append(
            "the preserve bodies are NOT all joined by the final geometry -- "
            "raise volfrac, lower filter_mm, or widen the design space")
    return out, keep


def min_member_size(mask, h, where=None):
    """Measured minimum member size, mm.

    Read the inscribed-sphere diameter on the ridge of the distance transform
    (the medial axis), not on every voxel: every surface voxel is one voxel from
    the void, so a plain distance transform just reports the voxel size back.

    `where` restricts *which* ridge points are reported, while the distance
    transform still runs over the whole mask so thicknesses stay correct at the
    boundary. Pass the free cells to measure the members the filter actually
    governed: preserve geometry is whatever the CAD says it is, and a 2.5 mm
    camera tab reads as a filter violation when it is nothing of the kind.
    """
    if not mask.any():
        return None
    from scipy.ndimage import maximum_filter
    edt = distance_transform_edt(mask) * h
    ridge = mask & (edt >= maximum_filter(edt, size=3) - 1e-9) & (edt > 0)
    if where is not None:
        ridge &= where
    vals = 2.0 * edt[ridge]
    if not vals.size:
        return None
    return {"min_mm": round(float(vals.min()), 3),
            "p5_mm": round(float(np.percentile(vals, 5)), 3),
            "median_mm": round(float(np.percentile(vals, 50)), 3)}

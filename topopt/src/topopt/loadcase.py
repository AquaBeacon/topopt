"""Load-case description: named regions, supports, loads, and body forces.

A study file is JSON:

{
  "part": "child_shell_aft.stl",
  "material": "PETG",
  "build_axis": "Z",
  "target_elements": 60000,          // or "resolution_mm": 1.5
  "safety_factor": 2.0,
  "max_deflection_mm": 1.0,

  "regions": {
    "boss":  {"type": "box", "min": [0, 0, 0], "max": [20, 40, 8]},
    "top":   {"type": "face", "axis": "Z", "side": "max", "depth": 2.0},
    "eye":   {"type": "cylinder", "axis": "Y", "centre": [30, 0, 90], "r": 6}
  },

  "cases": [
    {"name": "handling 5g",
     "supports": [{"region": "boss", "fix": "xyz"}],
     "loads":    [{"region": "eye", "force": [0, 0, -120]}],
     "gravity":  {"direction": [0, 0, -1], "g_factor": 5.0}}
  ]
}

Regions are evaluated on voxel centres; supports and loads are then transferred
to the *nodes* of those voxels. A load's `force` is the total in newtons and is
spread evenly over the region's nodes, so it never depends on mesh resolution.
"""

import json
import os

import numpy as np


LOAD_CLASSES = ("normal", "abnormal")


class LoadCase:
    def __init__(self, name, supports, loads, gravity=None,
                 load_class="normal"):
        self.name = name
        self.supports = supports
        self.loads = loads
        self.gravity = gravity or {}
        # "normal" or "abnormal" (spec 10.1 / 15.1). A drop, a one-foot-caught
        # lift or a transport shock is judged at 1.5, a working load at the
        # study's number. Undeclared is normal, because the stricter reading
        # is the one that cannot certify a weak part by omission.
        lc = str(load_class or "normal").lower()
        if lc not in LOAD_CLASSES:
            from .schema import InputError
            raise InputError("case %r: load_class must be one of %s, got %r"
                             % (name, "/".join(LOAD_CLASSES), load_class))
        self.load_class = lc


class Study:
    def __init__(self, data, base_dir="."):
        self.raw = data
        self.base_dir = base_dir
        self.problems = []
        self.part = data.get("part")
        self.material = data.get("material", "PETG")
        # `build_axis` decides which failure indices mean what: interlayer
        # tension and interlaminar shear are read along it. Defaulting it to Z
        # is a convenience for isotropic work; anisotropic analysis must not
        # accept the default silently (spec 9.2), which `requires_build_axis`
        # below is how a caller asks about.
        self.build_axis_declared = "build_axis" in data
        self.build_axis = {"X": 0, "Y": 1, "Z": 2}[str(data.get("build_axis", "Z")).upper()]
        # Which in-plane direction the raster runs along. Only matters once the
        # stiffness is orthotropic (Ex 3000 vs Ey 2400 for PLA); undeclared
        # means "the first axis that is not the build axis", which is what a
        # default slicer does for the first layer and is stated in the summary
        # rather than assumed silently.
        ra = data.get("raster_axis")
        self.raster_axis_declared = ra is not None
        self.raster_axis = (None if ra is None
                            else {"X": 0, "Y": 1, "Z": 2}[str(ra).upper()])
        # Which way is *down* for a ground-contact support. A part printed
        # standing up stands on its build plate, so the default is the build
        # axis; declaring it separately is what lets a Phase 6 comparison
        # re-print the same standing part with the layers along X or Y.
        ga = data.get("ground_axis")
        self.ground_axis_declared = ga is not None
        self.ground_axis = (self.build_axis if ga is None
                            else {"X": 0, "Y": 1, "Z": 2}[str(ga).upper()])
        if self.raster_axis is not None and self.raster_axis == self.build_axis:
            from .schema import InputError
            raise InputError(
                "raster_axis and build_axis are both %s; the raster runs "
                "within the layer plane, so they cannot be the same axis"
                % str(data.get("build_axis", "Z")).upper())
        # Which machine this part has to come out of. Undeclared resolves to
        # the default *and is labelled as defaulted* (printers.resolve), so a
        # report can never present an assumed envelope as a chosen one. The
        # spec is kept raw here and resolved on demand, so a bad printer name
        # is reported by whoever asks rather than at import time.
        self.printer_spec = data.get("printer")
        self.printer_declared = "printer" in data
        self.resolution_mm = data.get("resolution_mm", data.get("voxel_mm"))
        self.target_elements = data.get("target_elements", 60000)
        self.safety_factor = float(data.get("safety_factor", 2.0))
        self.max_deflection_mm = data.get("max_deflection_mm")
        self.regions = data.get("regions", {})
        self.infill = float(data.get("infill", 0.25))
        self.walls = int(data.get("walls", 4))

        cases = data.get("cases")
        if not cases:
            cases = [{"name": data.get("name", "case 1"),
                      "supports": data.get("supports", []),
                      "loads": data.get("loads", []),
                      "gravity": data.get("gravity")}]
        self.cases = [LoadCase(c.get("name", "case %d" % (i + 1)),
                               c.get("supports", []), c.get("loads", []),
                               c.get("gravity"),
                               c.get("load_class", "normal"))
                      for i, c in enumerate(cases)]

    def required_safety_factor(self, case):
        """The bar this case is judged at (spec 15.1).

        Normal: the study's `safety_factor`. Abnormal: the reference
        document's 1.5 -- but never above the study's own number, so a
        study that deliberately asks for less (a topology search at 2.0)
        is not held to more on its abnormal cases than on its normal ones.
        """
        from .materials import MIN_SAFETY_FACTOR
        if getattr(case, "load_class", "normal") == "abnormal":
            return float(min(self.safety_factor, MIN_SAFETY_FACTOR["abnormal"]))
        return float(self.safety_factor)

    def require_build_axis(self, what="anisotropic analysis"):
        """Refuse to proceed on a defaulted build axis (spec 9.2).

        For isotropic work the default is harmless. For anything that reads the
        material axes it is not: the answer would depend on an axis nobody
        chose, and it would look exactly like an answer somebody did choose.
        """
        if not self.build_axis_declared:
            from .schema import InputError
            raise InputError(
                "%s needs `build_axis` to be declared (X, Y or Z); the study "
                "does not set it. There is no safe default: the material axes "
                "decide which strength applies to which direction, so an "
                "assumed axis would silently change the answer." % what)
        return self.build_axis

    @property
    def printer(self):
        """The resolved build volume. Raises `InputError` on an unknown name so
        the CLI returns exit code 2 rather than a traceback."""
        from .printers import PrinterError, resolve
        from .schema import InputError
        try:
            return resolve(self.printer_spec)
        except PrinterError as exc:
            raise InputError(str(exc))

    @property
    def part_path(self):
        if not self.part:
            return None
        p = self.part
        return p if os.path.isabs(p) else os.path.join(self.base_dir, p)


def load_case_from_json(path, validate=True, strict_units=False) -> Study:
    """Read and check a study file. JSON, or YAML by extension.

    Anything unreadable or invalid raises `schema.InputError`, which the CLI
    front end turns into exit code 2 ("the input could not be read") rather
    than a traceback or a bare exit 1.

    Validation problems are attached to the returned Study as
    `study.problems`, so a command can print them; only *errors* stop the run.
    """
    from . import schema

    data, problems, _text = schema.load(path, strict_units=strict_units)
    errors = [p for p in problems if p.severity == "error"]
    if validate and errors:
        raise schema.InputError(
            "%s has %d problem(s):\n%s"
            % (path, len(errors), schema.format_problems(problems, path,
                                                         show_info=False)),
            problems)
    study = Study(data, base_dir=os.path.dirname(os.path.abspath(path)))
    study.problems = problems
    # Where it came from. A study with no `part` field still has to be named
    # in a report, and "PLA part" names nothing.
    study.source_path = os.path.abspath(path)
    return study


# ---------------------------------------------------------------- regions

def region_mask(grid, spec, regions=None, clip=True) -> np.ndarray:
    """Boolean (nx, ny, nz) mask of voxels selected by `spec`.

    `spec` is either a region name (looked up in `regions`) or an inline dict.

    `clip=True` (the default, and what every load case wants) intersects the
    result with the material. Pass `clip=False` when you need to know where the
    region is *regardless* of material -- setup validation has to be able to see
    a preserve body that sits outside the design space, and a clipped mask
    reports that case as simply empty.
    """
    if isinstance(spec, str):
        if not regions or spec not in regions:
            raise KeyError("unknown region %r" % spec)
        spec = regions[spec]

    kind = spec.get("type", "box").lower()
    c = grid.centres()
    lo, hi = grid.bbox()

    if kind == "all":
        m = np.ones(grid.shape, bool)

    elif kind == "box":
        mn = np.array(spec.get("min", lo), float)
        mx = np.array(spec.get("max", hi), float)
        # A null on either end means "open to the part's bounding box".
        mn = np.where(np.isnan(mn), lo, mn)
        mx = np.where(np.isnan(mx), hi, mx)
        m = np.ones(grid.shape, bool)
        for a in range(3):
            m &= (c[..., a] >= mn[a]) & (c[..., a] <= mx[a])

    elif kind == "face":
        axis = {"X": 0, "Y": 1, "Z": 2}[str(spec.get("axis", "Z")).upper()]
        depth = float(spec.get("depth", 2.0))
        side = str(spec.get("side", "min")).lower()
        # Measure from the extent of the *material*, not the grid, so padding
        # around the part cannot silently move the face.
        solid_coords = c[..., axis][grid.solid]
        if side.startswith("min"):
            edge = solid_coords.min()
            m = c[..., axis] <= edge + depth
        else:
            edge = solid_coords.max()
            m = c[..., axis] >= edge - depth

    elif kind == "cylinder":
        axis = {"X": 0, "Y": 1, "Z": 2}[str(spec.get("axis", "Z")).upper()]
        centre = np.array(spec["centre"], float)
        r = float(spec["r"])
        others = [a for a in range(3) if a != axis]
        d2 = sum((c[..., a] - centre[a]) ** 2 for a in others)
        m = d2 <= r * r
        rng = spec.get("range")
        if rng:
            m &= (c[..., axis] >= rng[0]) & (c[..., axis] <= rng[1])

    elif kind == "sphere":
        centre = np.array(spec["centre"], float)
        r = float(spec["r"])
        d2 = sum((c[..., a] - centre[a]) ** 2 for a in range(3))
        m = d2 <= r * r

    else:
        raise ValueError("unknown region type %r" % kind)

    if spec.get("invert"):
        m = ~m
    return (m & grid.solid) if clip else m


def region_mask_points(spec, points, regions=None) -> np.ndarray:
    """The same region semantics, evaluated at arbitrary points.

    `region_mask` needs a voxel grid; the CalculiX path has a tetrahedral mesh
    and needs to select nodes. Rather than approximate one with the other, the
    shape rules live here once and both callers use them, so a load region
    cannot mean one thing to the voxel solver and another to CalculiX.

    `points` is (n, 3) in mm. -> (n,) bool.
    """
    if isinstance(spec, str):
        if not regions or spec not in regions:
            raise KeyError("unknown region %r" % spec)
        spec = regions[spec]

    p = np.asarray(points, float).reshape(-1, 3)
    kind = spec.get("type", "box").lower()
    lo, hi = p.min(axis=0), p.max(axis=0)

    if kind == "all":
        m = np.ones(len(p), bool)

    elif kind == "box":
        mn = np.array(spec.get("min", lo), float)
        mx = np.array(spec.get("max", hi), float)
        mn = np.where(np.isnan(mn), lo, mn)
        mx = np.where(np.isnan(mx), hi, mx)
        m = np.all((p >= mn) & (p <= mx), axis=1)

    elif kind == "face":
        axis = {"X": 0, "Y": 1, "Z": 2}[str(spec.get("axis", "Z")).upper()]
        depth = float(spec.get("depth", 2.0))
        side = str(spec.get("side", "min")).lower()
        if side.startswith("min"):
            m = p[:, axis] <= lo[axis] + depth
        else:
            m = p[:, axis] >= hi[axis] - depth

    elif kind == "cylinder":
        axis = {"X": 0, "Y": 1, "Z": 2}[str(spec.get("axis", "Z")).upper()]
        centre = np.array(spec["centre"], float)
        r = float(spec["r"])
        others = [a for a in range(3) if a != axis]
        d2 = sum((p[:, a] - centre[a]) ** 2 for a in others)
        m = d2 <= r * r
        rng = spec.get("range")
        if rng:
            m &= (p[:, axis] >= rng[0]) & (p[:, axis] <= rng[1])

    elif kind == "sphere":
        centre = np.array(spec["centre"], float)
        r = float(spec["r"])
        m = np.sum((p - centre) ** 2, axis=1) <= r * r

    else:
        raise ValueError("unknown region type %r" % kind)

    return ~m if spec.get("invert") else m


def _nodes_of(grid, vox_mask) -> np.ndarray:
    """(nx+1, ny+1, nz+1) bool: nodes belonging to the selected voxels."""
    nx, ny, nz = grid.shape
    nodes = np.zeros((nx + 1, ny + 1, nz + 1), bool)
    idx = np.argwhere(vox_mask)
    for a in (0, 1):
        for b in (0, 1):
            for d in (0, 1):
                nodes[idx[:, 0] + a, idx[:, 1] + b, idx[:, 2] + d] = True
    return nodes


def _plane_nodes_of(grid, vox_mask, axis, plane_index) -> np.ndarray:
    """Only the nodes of `vox_mask` that lie on one node plane.

    A ground support has to fix the *contact face*, not the cell. `_nodes_of`
    marks all eight nodes of every selected voxel, including the four on top --
    which would clamp the plane one voxel above the plate just as rigidly, so
    the optimiser could hang a leg in mid-air and still be fully supported. The
    part would then be certified standing on nothing.
    """
    nx, ny, nz = grid.shape
    nodes = np.zeros((nx + 1, ny + 1, nz + 1), bool)
    idx = np.argwhere(vox_mask)
    if idx.size == 0:
        return nodes
    others = [a for a in range(3) if a != axis]
    for da in (0, 1):
        for db in (0, 1):
            i = idx.copy()
            i[:, others[0]] += da
            i[:, others[1]] += db
            i[:, axis] = plane_index
            nodes[i[:, 0], i[:, 1], i[:, 2]] = True
    return nodes


_FIXMAP = {"x": (0,), "y": (1,), "z": (2,), "xy": (0, 1), "yz": (1, 2),
           "xz": (0, 2), "zx": (0, 2), "xyz": (0, 1, 2), "all": (0, 1, 2)}


def build_bcs(grid, case: LoadCase, regions, material=None, infill=0.25,
              walls=4, restrict=None, ground=None, ground_axis=2):
    """-> (fixed_mask, force) arrays shaped (nx+1, ny+1, nz+1, 3).

    `restrict` is an optional mask every support and load region is intersected
    with. Pass the preserve mask: a load must act on material that is not up for
    optimisation, and clipping the region enforces that without demanding the
    region be drawn to voxel accuracy. A region that misses `restrict` entirely
    still raises -- that is a real mistake, not a rounding one.

    `ground` is the second kind of support: the contact band from
    `designspace.ground_mask`. A support that says `"contact": "ground"` is
    clipped to that band instead of to preserve, and fixes only the nodes on
    the plate itself. That is how a study asks "stand this on the table"
    without also having to say where the feet go -- which is otherwise decided
    by whoever drew the preserve pads, not by the optimiser.
    """
    nx, ny, nz = grid.shape
    sn = (nx + 1, ny + 1, nz + 1)
    fixed = np.zeros(sn + (3,), bool)
    force = np.zeros(sn + (3,))

    def _sel(spec, clip=None):
        m = region_mask(grid, spec, regions)
        return (m & clip) if clip is not None else m

    for s in case.supports:
        on_ground = str(s.get("contact", "")).lower() == "ground" \
            if isinstance(s, dict) else False
        if on_ground and ground is None:
            raise ValueError(
                "support %r asks for ground contact, but this caller did not "
                "supply the contact band" % (s.get("region", s),))
        vm = _sel(s.get("region", s), ground if on_ground else restrict)
        if not vm.any():
            raise ValueError(
                "support region %r selected no %s"
                % (s.get("region", s),
                   "material on the ground plane" if on_ground else "material"))
        if on_ground:
            k0 = int(np.argwhere(ground)[:, ground_axis].min())
            nodes = _plane_nodes_of(grid, vm, ground_axis, k0)
        else:
            nodes = _nodes_of(grid, vm)
        for comp in _FIXMAP[str(s.get("fix", "xyz")).lower()]:
            fixed[..., comp] |= nodes

    for ld in case.loads:
        # Loads are never ground-contact: something has to push on the part,
        # and the plate is what holds it, not what pushes it.
        vm = _sel(ld.get("region", ld), restrict)
        if not vm.any():
            raise ValueError("load region %r selected no material"
                             % (ld.get("region", ld),))
        nodes = _nodes_of(grid, vm)
        n = int(nodes.sum())

        if "force" in ld:
            vec = np.array(ld["force"], float)
        elif "pressure" in ld:
            # Pressure acts on the region's projected area in the given direction.
            direction = np.array(ld.get("direction", [0, 0, -1]), float)
            direction /= np.linalg.norm(direction)
            axis = int(np.argmax(np.abs(direction)))
            others = [a for a in range(3) if a != axis]
            area = vm.any(axis=axis).sum() * grid.h ** 2
            vec = direction * float(ld["pressure"]) * area
            del others
        elif "torque" in ld:
            raise NotImplementedError(
                "torque loads: apply as a force couple on two regions instead")
        else:
            raise ValueError("load needs 'force' or 'pressure': %r" % ld)

        force[nodes] += vec / n

    grav = case.gravity or {}
    if grav and material is not None:
        direction = np.array(grav.get("direction", [0, 0, -1]), float)
        direction /= np.linalg.norm(direction)
        gf = float(grav.get("g_factor", 1.0))
        fill = grav.get("fill_factor")
        if fill is None:
            fill = min(1.0, max(infill, 0.35 + 0.1 * walls))
        # density g/cm^3 -> kg/mm^3 is 1e-6; * 9.81 m/s^2 -> N per mm^3
        n_per_mm3 = material.density * fill * 1e-6 * 9.81
        w_elem = n_per_mm3 * grid.h ** 3 * gf
        idx = np.argwhere(grid.solid)
        for a in (0, 1):
            for b in (0, 1):
                for d in (0, 1):
                    np.add.at(force, (idx[:, 0] + a, idx[:, 1] + b, idx[:, 2] + d),
                              direction * w_elem / 8.0)

    return fixed, force


def total_load(force) -> np.ndarray:
    return force.reshape(-1, 3).sum(axis=0)

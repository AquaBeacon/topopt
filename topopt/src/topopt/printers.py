"""Build volumes: the one place that knows how big a machine can print.

Same rule as `materials.py`. A build volume written down in two places is a
build volume that disagrees with itself, and the way it disagrees is silent:
one module passes the part, another rejects it, and whichever ran last wins.

The reason this module exists at all is a measured failure. The tall tripod's
design space was drawn 300 mm across, the target machine prints 250 mm, and
nothing looked until after a 3.6-hour optimisation had produced an STL that
fits the printer in no orientation whatsoever. The envelope is a property of
the *setup*, knowable in the first second, and it is now checked there.

Two ideas worth separating:

- **usable** is not **nominal**. A 256 mm nominal bed does not print a 256 mm
  part: the purge/wipe area eats into it and a part on the very edge of the
  plate does not stay stuck. `usable` is the number to check against.
- **fitting in some orientation** is not the same as **fitting**. A part that
  only fits lying on its side has to be *printed* lying on its side, and that
  changes which way the layers run -- so the orthotropic analysis done with the
  declared build axis no longer describes it. Reporting "it fits (rotated)" as
  a pass is how an analysis gets separated from the object it analysed.

Custom machines are first-class: a study may name a built-in key or give its
own envelope. This toolkit is not a Bambu Lab accessory.
"""

from dataclasses import dataclass

import numpy as np


# Usable build volume in mm, as (x, y, height). The nominal X1C envelope is
# 256^3; the practical limit is smaller because of the purge/wipe area at the
# back left and because a part touching the very edge of the plate rarely
# adheres well.
@dataclass(frozen=True)
class Printer:
    key: str
    name: str
    nominal_mm: tuple
    usable_mm: tuple
    nozzle_mm: float
    note: str = ""
    source: str = "built-in"
    margin_mm: float = 0.0
    allow_oversize_design_space: bool = False

    @property
    def envelope_mm(self):
        """The usable volume less the requested margin -- what must contain
        the part. Kept separate from `usable_mm` so a report can show both and
        a reader can see how much of a rejection was the margin's doing."""
        m = float(self.margin_mm)
        return tuple(max(float(v) - 2 * m, 0.0) for v in self.usable_mm)

    @property
    def min_feature_mm(self):
        """Two perimeters. Anything thinner is gap-fill, which is not a wall."""
        return 2.0 * float(self.nozzle_mm)

    def as_dict(self):
        return {"key": self.key, "name": self.name,
                "nominal_mm": [float(v) for v in self.nominal_mm],
                "usable_mm": [float(v) for v in self.usable_mm],
                "margin_mm": float(self.margin_mm),
                "envelope_mm": [round(float(v), 2) for v in self.envelope_mm],
                "nozzle_mm": float(self.nozzle_mm),
                "min_feature_mm": round(self.min_feature_mm, 3),
                "note": self.note, "source": self.source,
                "allow_oversize_design_space":
                    bool(self.allow_oversize_design_space)}


def _p(key, name, nominal, usable, nozzle, note=""):
    return Printer(key=key, name=name, nominal_mm=tuple(nominal),
                   usable_mm=tuple(usable), nozzle_mm=nozzle, note=note)


PRINTERS = {
    "X1C": _p("X1C", "Bambu Lab X1-Carbon", (256, 256, 256), (250, 250, 250),
              0.4, "AMS-fed; the purge tower needs ~60x60 mm of plate on "
                   "multi-material prints."),
    "X1C-0.6": _p("X1C-0.6", "Bambu Lab X1-Carbon (0.6 mm nozzle)",
                  (256, 256, 256), (250, 250, 250), 0.6,
                  "Faster and stronger walls; min feature size grows to "
                  "~1.2 mm."),
    "A1": _p("A1", "Bambu Lab A1", (256, 256, 256), (250, 250, 250), 0.4),
    "P1S": _p("P1S", "Bambu Lab P1S", (256, 256, 256), (250, 250, 250), 0.4),
    "MK4": _p("MK4", "Prusa MK4", (250, 210, 220), (245, 205, 215), 0.4),
    "MINI": _p("MINI", "Prusa MINI+", (180, 180, 180), (175, 175, 175), 0.4),
    "ENDER3": _p("ENDER3", "Creality Ender-3", (220, 220, 250),
                 (215, 215, 245), 0.4),
    "GENERIC200": _p("GENERIC200", "Generic 200 mm-class FDM machine",
                     (200, 200, 200), (195, 195, 195), 0.4,
                     "A deliberately modest default for studies that have not "
                     "picked a machine yet."),
}

DEFAULT = "X1C"


def names():
    return sorted(PRINTERS)


class PrinterError(ValueError):
    """A printer spec that cannot be read. Raised so the CLI can turn it into
    exit code 2 (bad input) rather than letting it surface as a solver bug."""


def resolve(spec, default=DEFAULT):
    """A study's `printer` value -> a `Printer`.

    Accepts a built-in key (`"X1C"`), a full custom envelope, or None. None
    returns the default *and says so through `source`*, because "no printer was
    declared" and "this printer was chosen" must not read the same in a report.
    """
    if isinstance(spec, Printer):
        return spec
    if spec is None:
        p = PRINTERS[default]
        return Printer(**dict(p.__dict__, source="default (none declared)"))
    if isinstance(spec, str):
        key = spec.strip()
        if key not in PRINTERS:
            raise PrinterError(
                "unknown printer %r; known: %s. To use a machine that is not "
                "in the list, give the envelope instead: "
                '"printer": {"name": "...", "build_volume_mm": [x, y, z]}'
                % (spec, ", ".join(names())))
        return Printer(**dict(PRINTERS[key].__dict__, source="built-in"))
    if not isinstance(spec, dict):
        raise PrinterError("`printer` must be a name or an object, got %s"
                           % type(spec).__name__)

    base = spec.get("printer") or spec.get("based_on")
    p = resolve(base) if base else None

    vol = spec.get("build_volume_mm", spec.get("usable_mm"))
    if vol is None and p is None:
        raise PrinterError(
            "a custom printer needs `build_volume_mm: [x, y, z]` (the volume "
            "you can actually print into, not the sales figure)")
    if vol is None:
        usable = p.usable_mm
    else:
        try:
            usable = tuple(float(v) for v in vol)
        except (TypeError, ValueError):
            raise PrinterError("`build_volume_mm` must be three numbers in mm")
        if len(usable) != 3 or any(v <= 0 for v in usable):
            raise PrinterError("`build_volume_mm` must be three positive "
                               "numbers in mm, got %r" % (vol,))

    nominal = spec.get("nominal_mm", usable if vol is not None
                       else (p.nominal_mm if p else usable))
    nozzle = float(spec.get("nozzle_mm", p.nozzle_mm if p else 0.4))
    if nozzle <= 0:
        raise PrinterError("`nozzle_mm` must be positive")
    margin = float(spec.get("margin_mm", 0.0))
    if margin < 0:
        raise PrinterError("`margin_mm` must not be negative")

    return Printer(
        key=spec.get("key", "custom"),
        name=spec.get("name", p.name if p else "custom machine"),
        nominal_mm=tuple(float(v) for v in nominal), usable_mm=usable,
        nozzle_mm=nozzle, note=spec.get("note", ""),
        source="declared in the study", margin_mm=margin,
        allow_oversize_design_space=bool(
            spec.get("allow_oversize_design_space", False)))


# ------------------------------------------------------------------ fitting

AXES = "XYZ"


def fits_somehow(size, usable):
    """A box fits in some axis-aligned orientation iff its sorted extents fit
    the sorted build volume."""
    return bool(np.all(np.sort(np.asarray(size, float))
                       <= np.sort(np.asarray(usable, float)) + 1e-6))


def fit_with_up(size, usable, up):
    """Fit with model axis `up` (0/1/2) vertical. -> (fits, overflow per axis).

    The two remaining axes lie on the plate and may be swapped freely, so the
    longer one is checked against the longer bed dimension. Overflow is
    reported per *model* axis and in mm, positive meaning "this much too big",
    because that is the number someone edits in the CAD.
    """
    size = np.asarray(size, float)
    usable = np.asarray(usable, float)
    plane = [i for i in range(3) if i != up]
    bed = sorted(usable[:2], reverse=True)
    lay = sorted(((size[i], i) for i in plane), reverse=True)
    over = {}
    for (extent, axis), limit in zip(lay, bed):
        over[AXES[axis]] = round(float(extent - limit), 2)
    over[AXES[up]] = round(float(size[up] - usable[2]), 2)
    return all(v <= 1e-6 for v in over.values()), over


def fit(size_mm, printer, build_axis=None, what="part"):
    """Does a box of `size_mm` fit `printer`? -> a report dict.

    `build_axis` is the model axis that points up on the plate. When it is
    given, `fits_as_declared` answers the question that actually matters --
    does it fit *in the orientation the analysis assumed* -- and the list of
    other orientations is reported separately and never as a pass.
    """
    p = resolve(printer)
    size = [float(v) for v in size_mm]
    env = p.envelope_mm

    up_axes = []
    per_axis = {}
    for i in range(3):
        ok, over = fit_with_up(size, env, i)
        per_axis[AXES[i]] = {"fits": ok, "overflow_mm": over}
        if ok:
            up_axes.append(AXES[i])

    out = {"what": what, "printer": p.key, "printer_name": p.name,
           "printer_source": p.source,
           "size_mm": [round(v, 2) for v in size],
           "usable_mm": [round(float(v), 2) for v in p.usable_mm],
           "margin_mm": float(p.margin_mm),
           "envelope_mm": [round(float(v), 2) for v in env],
           "fits_any_orientation": fits_somehow(size, env),
           "up_axes_that_fit": up_axes,
           "by_up_axis": per_axis}

    if build_axis:
        b = str(build_axis).upper()[:1]
        if b in AXES:
            out["declared_build_axis"] = b
            out["fits_as_declared"] = per_axis[b]["fits"]
            out["overflow_mm"] = per_axis[b]["overflow_mm"]
            worst = max(per_axis[b]["overflow_mm"].values())
            out["margin_to_envelope_mm"] = round(-worst, 2)
    if not out["fits_any_orientation"]:
        out["split"] = suggest_split(size, env)
    return out


def suggest_split(model_size, usable, joint="dowel"):
    """Where to cut a part that does not fit, in *model* coordinates.

    Reported against the axes as modelled, because that is what gets edited in
    the .scad -- quoting a cut in some rotated printing frame is how a 300 mm
    Y dimension ends up being cut in X.
    """
    model_size = np.asarray(model_size, float)
    if fits_somehow(model_size, usable):
        return {"required": False}

    div = np.ones(3, int)
    for _ in range(24):
        cur = model_size / div
        if fits_somehow(cur, usable):
            break
        # Always subdivide whatever is longest right now.
        div[int(np.argmax(cur))] += 1
    else:
        return {"required": True, "joint": joint, "plans": [],
                "guidance": "part is far larger than the build volume; "
                            "reconsider the design rather than splitting it"}

    plans = []
    for axis in range(3):
        if div[axis] < 2:
            continue
        piece = float(model_size[axis] / div[axis])
        plans.append({
            "axis": AXES[axis],
            "model_extent_mm": round(float(model_size[axis]), 2),
            "pieces": int(div[axis]),
            "piece_length_mm": round(piece, 2),
            "cut_positions_mm": [round(piece * (i + 1), 2)
                                 for i in range(div[axis] - 1)],
        })
    return {"required": True, "joint": joint, "plans": plans,
            "resulting_piece_mm": [round(float(v), 1) for v in model_size / div],
            "guidance": (
                "Cut across the *smallest* stress in the part, never through a "
                "bearing surface or a bolt boss. A glued/bolted joint is only "
                "as strong as its bond area, so add overlap: dowel pins carry "
                "shear, a bolted flange carries tension, a dovetail carries "
                "both but only in one direction. Always re-run `topopt fea` on "
                "the split design with the joint modelled as the weak link.")}


def describe(p):
    """One line for a human: what machine, how big, whose number it is."""
    p = resolve(p)
    return "%s -- usable %.0f x %.0f x %.0f mm, %.1f mm nozzle (%s)" % (
        p.name, p.usable_mm[0], p.usable_mm[1], p.usable_mm[2],
        p.nozzle_mm, p.source)

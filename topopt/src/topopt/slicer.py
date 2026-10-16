"""Reading a slicer: what a real slice says, and what it cannot be asked.

Everything before this module is an estimate. `printcheck` counts voxels with
nothing under them; `printability` measures a bounding box. Neither of them
knows what a nozzle actually does, so neither can say how long the print takes,
how much filament it eats, or how much of that filament is scaffolding thrown
away afterwards. Only a slicer knows, because only a slicer produces the
toolpath.

The slicer used here is **Bambu Studio** (see `docs/spec.md` 24). Two facts
about its command line cost an afternoon to find and are worth writing down:

- **The CLI does not resolve `inherits`.** The built-in presets under
  `resources/profiles/BBL/` are a chain -- `0.20mm Standard @BBL X1C` inherits
  `fdm_process_single_0.20` inherits ... -- and handing the leaf file to
  `--load-settings` silently loads *only the leaf*. The X1C's 256 mm bed
  quietly became a 200x200x100 default and the run then segfaulted. So
  `resolve()` below flattens the chain itself before anything is written.
- **`"from": "system"` is required.** A flattened preset marked
  `"from": "User"` is rejected with "process not compatible with printer" even
  when its own `compatible_printers` names the machine exactly.

The other thing worth knowing is what the slicer *will not* tell you.
`Metadata/slice_info.config` gives total weight and total time; there is no
support total anywhere in the 3mf. It is recoverable, because the G-code marks
every extrusion block with `; FEATURE: Support` -- but only if the parse also
counts `G2`/`G3` arc moves, which Bambu Studio emits by default. Counting only
`G0`/`G1` loses 13 % of the filament and looks plausible while doing it. So
`filament_by_feature()` re-derives the slicer's own header total from the same
sum and the caller refuses to report a support number when the two disagree
(`cli/slice.py`). A number that cannot check itself is not worth printing.

Nothing here runs a subprocess or touches an output directory; that is
`cli/slice.py`. This module is the part that can be tested against a saved
fragment of a `slice_info.config` and a G-code header, which is what
`tests/test_slicer.py` does.
"""

import json
import os
import re

from topopt import materials


SCHEMA = "topopt/slice/1"

# Filament is round wire. Volume = length * pi d^2 / 4, and `d` comes from the
# slicer's own header rather than from here -- 1.75 mm is the usual value and
# is not a constant of nature.
def filament_area_mm2(diameter_mm):
    return 3.141592653589793 * float(diameter_mm) ** 2 / 4.0


# --------------------------------------------------------------- the presets

class PresetError(ValueError):
    """A preset that cannot be found or read. -> exit code 2: the machine or
    material asked for is a user choice, and 'there is no such profile' is an
    answer about that choice, not about the part."""


def profiles_root(exe_path):
    """`<install>/resources/profiles`, given the slicer executable, or None.

    Bambu Studio and OrcaSlicer both ship the vendor profiles beside the
    binary. `TOPOPT_SLICER_PROFILES` overrides, for an install that keeps them
    somewhere else.
    """
    env = os.environ.get("TOPOPT_SLICER_PROFILES")
    if env:
        return env if os.path.isdir(env) else None
    d = os.path.dirname(os.path.abspath(exe_path))
    for cand in (os.path.join(d, "resources", "profiles"),
                 os.path.join(d, "..", "resources", "profiles"),
                 os.path.join(d, "..", "Resources", "profiles")):
        cand = os.path.normpath(cand)
        if os.path.isdir(cand):
            return cand
    return None


class Presets(object):
    """The vendor preset tree, with `inherits` resolved.

    One vendor at a time (`BBL`). The vendor's index file lists every preset
    with its path, which is the only reliable way to follow `inherits`: the
    parent is named, not located, and the name does not have to be a filename.
    """

    def __init__(self, root, vendor="BBL"):
        self.root = root
        self.vendor = vendor
        index_file = os.path.join(root, vendor + ".json")
        if not os.path.exists(index_file):
            raise PresetError(
                "no %s preset index at %s -- the slicer is installed but its "
                "built-in profiles are not where they are expected."
                % (vendor, index_file))
        with open(index_file, encoding="utf-8") as f:
            index = json.load(f)
        self.paths = {}
        self.kind_of = {}
        for key, kind in (("machine_list", "machine"),
                          ("process_list", "process"),
                          ("filament_list", "filament")):
            for entry in index.get(key, []):
                p = os.path.join(root, vendor,
                                 entry["sub_path"].replace("/", os.sep))
                self.paths[entry["name"]] = p
                self.kind_of[entry["name"]] = kind
        self._cache = {}

    def names(self, kind):
        return sorted(n for n, k in self.kind_of.items() if k == kind)

    def _read(self, name):
        p = self.paths.get(name)
        if p is None or not os.path.exists(p):
            raise PresetError("no such preset: %r" % name)
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def resolve(self, name):
        """The preset with its whole `inherits` chain merged in, nearest wins.

        Marked `"from": "system"` on the way out: a flattened preset that says
        `"User"` is refused by the CLI as incompatible with the very printer
        its own `compatible_printers` names.
        """
        if name in self._cache:
            return dict(self._cache[name])
        chain = []
        seen = set()
        cur = name
        while cur is not None:
            if cur in seen:
                raise PresetError("circular `inherits` at preset %r" % cur)
            seen.add(cur)
            d = self._read(cur)
            chain.append(d)
            cur = d.get("inherits") or None
        merged = {}
        for d in reversed(chain):
            merged.update(d)
        merged.pop("inherits", None)
        merged["name"] = name
        merged["from"] = "system"
        self._cache[name] = merged
        return dict(merged)

    def compatible(self, preset_name, machine_name):
        """Does this process/filament preset accept that machine?

        An empty (or absent) `compatible_printers` means "no restriction",
        which is how the vendor's own generic profiles are written.
        """
        d = self.resolve(preset_name)
        want = d.get("compatible_printers")
        if not want:
            return True
        return machine_name in want


# printers.py key -> the vendor machine preset. Only the machines that have a
# Bambu profile appear: asking for a Prusa MK4 here is a mistake worth an
# error, not a silently substituted X1C.
MACHINE_PRESET = {
    "X1C": "Bambu Lab X1 Carbon 0.4 nozzle",
    "X1C-0.6": "Bambu Lab X1 Carbon 0.6 nozzle",
    "A1": "Bambu Lab A1 0.4 nozzle",
    "P1S": "Bambu Lab P1S 0.4 nozzle",
}

# `materials.py` name -> the vendor filament family. The material *values*
# still come from `materials.py`; this only says which spool the slicer should
# assume, and a mismatch between the two is reported rather than hidden.
FILAMENT_FAMILY = {
    "PLA": "Bambu PLA Basic",
    "PETG": "Bambu PETG Basic",
    "ABS": "Bambu ABS",
    "ASA": "Bambu ASA",
    "PC": "Bambu PC",
    "PA6-CF": "Bambu PA6-CF",
    "PLA-CF": "Bambu PLA-CF",
}

# --supports -> the two process keys that decide it. `auto` is the ordinary
# grid support; `tree` is the branching kind. They are separate options because
# the X1C process preset already defaults to `tree(auto)`, so "auto" and "tree"
# would otherwise be the same run under two names.
SUPPORT_MODES = {
    "none": {"enable_support": "0"},
    "auto": {"enable_support": "1", "support_type": "normal(auto)"},
    "tree": {"enable_support": "1", "support_type": "tree(auto)"},
}


def pick_machine(printer_key):
    name = MACHINE_PRESET.get(printer_key)
    if name is None:
        raise PresetError(
            "no built-in slicer profile for printer %r. The slicer ships "
            "profiles for: %s. (The printer is still valid for `topopt "
            "print`; it is only the slicer that has no profile for it.)"
            % (printer_key, ", ".join(sorted(MACHINE_PRESET))))
    return name


def pick_process(presets, machine_name, layer_mm):
    """A process preset for this machine at this layer height.

    Chosen by search rather than by a table, so a Bambu Studio update that
    renames a profile is a different answer instead of a crash. `Standard` is
    preferred where it exists because it is the vendor's own default; the tie
    is broken alphabetically so the choice is reproducible.
    """
    prefix = "%.2fmm " % float(layer_mm)
    cands = [n for n in presets.names("process") if n.startswith(prefix)]
    cands = [n for n in cands if presets.compatible(n, machine_name)]
    ok = []
    for n in cands:
        try:
            h = float(presets.resolve(n).get("layer_height", "nan"))
        except (TypeError, ValueError):
            continue
        if abs(h - float(layer_mm)) < 1e-9:
            ok.append(n)
    if not ok:
        heights = sorted({n.split("mm ")[0] for n in presets.names("process")
                          if presets.compatible(n, machine_name)
                          and "mm " in n})
        raise PresetError(
            "no %s process profile at layer height %s mm. This machine has "
            "profiles for: %s mm."
            % (machine_name, layer_mm, ", ".join(heights)))
    ok.sort(key=lambda n: (0 if " Standard " in n + " " else 1, n))
    return ok[0]


def pick_filament(presets, machine_name, material):
    """A filament preset for this machine and this `materials.py` material."""
    key = materials.get_material(material).name
    family = FILAMENT_FAMILY.get(key)
    if family is None:
        raise PresetError(
            "no slicer filament profile is mapped to material %r. Mapped "
            "materials: %s." % (material, ", ".join(sorted(FILAMENT_FAMILY))))
    cands = [n for n in presets.names("filament")
             if n == family or n.startswith(family + " @")]
    cands = [n for n in cands if presets.compatible(n, machine_name)]
    if not cands:
        raise PresetError(
            "the slicer has no %r profile for %s." % (family, machine_name))
    cands.sort()
    return cands[0]


# ------------------------------------------------------- reading the output

_META = re.compile(r'<metadata\s+key="([^"]+)"\s+value="([^"]*)"')
_FILAMENT = re.compile(r"<filament\s+([^>]*)/>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_RANGE = re.compile(r'layer_ranges="(\d+)\s+(\d+)"')


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_slice_info(text):
    """`Metadata/slice_info.config` -> the plate's own summary.

    This is the slicer's answer, not a re-derivation of it: `prediction` is
    the number the printer's display will show and `outside` is the slicer's
    own verdict on whether the object sits inside the build volume.
    """
    meta = dict(_META.findall(text))
    out = {
        "print_time_s": _num(meta.get("prediction")),
        "filament_g": _num(meta.get("weight")),
        # "outside the plate" is the slicer's arrangement result, so
        # `bed_fits` is its answer and not a bounding box of ours.
        "bed_fits": (meta["outside"].lower() == "false"
                     if "outside" in meta else None),
        "support_used": (meta["support_used"].lower() == "true"
                         if "support_used" in meta else None),
        "nozzle_mm": _num(meta.get("nozzle_diameters")),
        "filaments": [],
    }
    for blob in _FILAMENT.findall(text):
        a = dict(_ATTR.findall(blob))
        out["filaments"].append({
            "type": a.get("type"),
            "used_mm": (_num(a["used_m"]) * 1000.0) if a.get("used_m") else None,
            "used_g": _num(a.get("used_g")),
            "used_for_object": a.get("used_for_object") == "true",
            "used_for_support": a.get("used_for_support") == "true",
        })
    m = _RANGE.search(text)
    if m:
        # `layer_ranges="0 161"` is inclusive at both ends.
        out["layers"] = int(m.group(2)) - int(m.group(1)) + 1
    return out


_HEADER_KEYS = {
    "total layer number": ("layers", int),
    "total filament length [mm]": ("filament_mm", float),
    "total filament weight [g]": ("filament_g", float),
    "filament_density": ("filament_density_g_cm3", float),
    "filament_diameter": ("filament_diameter_mm", float),
    "max_z_height": ("max_z_mm", float),
}
_TIME = re.compile(r"model printing time:\s*([^;]+);\s*total estimated time:\s*(.+)")


def parse_gcode_header(text):
    """The `; HEADER_BLOCK_START` block of `Metadata/plate_1.gcode`.

    Note what is *not* trusted here: the header line
    `total filament volume [cm^3] : 2974.00` is mislabelled -- for a 3.75 g
    PLA print at 1.26 g/cm3 the true volume is 2.98 cm3, so the number is mm3
    wearing a cm3 label. It is ignored, and the volume is computed from the
    length and the wire diameter instead, both of which are unambiguous.
    """
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("; HEADER_BLOCK_END"):
            break
        if not s.startswith(";"):
            continue
        body = s[1:].strip()
        m = _TIME.search(body)
        if m:
            out["model_printing_time"] = m.group(1).strip()
            out["estimated_time"] = m.group(2).strip()
            continue
        if ":" not in body:
            continue
        key, _sep, val = body.partition(":")
        rule = _HEADER_KEYS.get(key.strip())
        if rule is None:
            continue
        name, cast = rule
        try:
            out[name] = cast(float(val.strip()))
        except (TypeError, ValueError):
            pass
    return out


_MOVE = re.compile(r"^G([0123])(?![0-9])")
_E_ARG = re.compile(r"(?:^|\s)E(-?\d*\.?\d+)")
_XY_ARG = re.compile(r"(?:^|\s)[XY]-?\d*\.?\d+")


def filament_by_feature(text):
    """-> {feature name: filament mm}. The slicer does not report this.

    Bambu Studio labels every extrusion block with `; FEATURE: <name>`, so
    support can be separated from the part by summing the extruder axis inside
    the blocks named `Support*`. Three details decide whether the number is
    right:

    - **`G2`/`G3` count.** Arc fitting is on in the stock profiles. Summing
      only `G0`/`G1` reproduces 86.6 % of the slicer's own total, which is
      wrong in a way that looks like a plausible answer.
    - **Only moves that also move in X or Y.** A bare `G1 E-0.8` is a
      retraction, and the matching un-retraction would otherwise be counted as
      material laid down.
    - **The file does not start in relative mode.** Bambu's machine start
      G-code draws its two prime lines in *absolute* E (`G92 E0`, then `E15`
      and `E30`) and only switches to relative with `M83` afterwards, so
      assuming relative throughout counts the prime as 45 mm instead of 30 --
      a constant 15 mm of phantom filament on every print, which is small
      enough to look like rounding and large enough to break the check below.
      The parse starts absolute (the G-code default) and follows `M82`/`M83`
      and `G92 E`.

    The caller compares `sum(result.values())` with the header's own
    `total filament length [mm]`; they match to the last digit when the parse
    is right, and that agreement is the only evidence anyone has that the
    support figure means anything.
    """
    per = {}
    feature = "(none)"
    # Absolute is the G-code default and is what the machine start block is
    # written in. `M83` arrives later, once the part itself starts printing.
    relative = False
    last_e = 0.0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s[0] == ";":
            if s.startswith("; FEATURE:"):
                feature = s.split(":", 1)[1].strip()
            continue
        if s.startswith("M83"):
            relative = True
            continue
        if s.startswith("M82"):
            relative = False
            continue
        if s.startswith("G92"):
            m = _E_ARG.search(s)
            if m:
                last_e = float(m.group(1))
            continue
        if not _MOVE.match(s):
            continue
        m = _E_ARG.search(s)
        if not m:
            continue
        e = float(m.group(1))
        if relative:
            delta = e
        else:
            delta = e - last_e
            last_e = e
        if delta > 0 and _XY_ARG.search(s):
            per[feature] = per.get(feature, 0.0) + delta
    return per


def support_mm(per_feature):
    """Filament laid down as scaffolding, in mm of wire.

    Every Bambu feature whose name begins with `Support` -- the body, the
    interface, and whatever a later version adds -- is scaffolding. Matching a
    prefix rather than a fixed list is deliberate: a feature this code has not
    heard of should be counted, not silently dropped.
    """
    return sum(v for k, v in per_feature.items()
               if k.strip().lower().startswith("support"))


# -------------------------------------------------------- spec 13.3 verdict

# spec 13.3 asks five questions, and all five are about *where* the support
# is. The slicer reports how much support there is and nothing at all about
# where, so all five come back UNKNOWN with the reason attached, and the
# geometric answers stay with `printcheck` (which works on the voxel grid and
# does know where things are). Recording them as UNKNOWN rather than omitting
# them is the point: AGENTS.md, "not checked is never rendered as fine".
SUPPORT_CONDITIONS = [
    ("no_support_in_sealed_cavity",
     "No support is generated inside a sealed cavity"),
    ("no_support_out_of_reach",
     "No support is generated where a tool cannot reach it"),
    ("no_support_on_mating_faces",
     "No support touches a critical (preserve) mating face"),
    ("removal_does_not_break_thin_members",
     "Removing the support does not break a thin member"),
    ("support_not_preferred_over_structure",
     "Support quantity was not preferred over structural performance"),
]

_UNKNOWN_WHY = ("the slicer reports how much support there is, never where "
                "it is; spec 13.3 is a question about location. "
                "`printcheck.json` answers the first and third of these on "
                "the voxel grid.")


def judge(info, error=None):
    """-> (pass, checks, note).

    `pass` is deliberately narrow, and its wording says so: the slice ran, the
    slicer put the object inside the build volume, and nothing about the
    quantities is missing. It is **not** a spec 13.3 verdict -- every 13.3
    condition is UNKNOWN here -- and a reader who takes it for one has been
    told otherwise in `slice.json` itself.
    """
    checks = [{"key": k, "condition": t, "state": "UNKNOWN",
               "detail": _UNKNOWN_WHY} for k, t in SUPPORT_CONDITIONS]
    if error:
        checks.insert(0, {"key": "sliced", "condition": "The slicer produced a "
                                                        "sliced plate",
                          "state": "FAIL", "detail": error})
        return False, checks, ("the slicer did not finish; this says nothing "
                               "about the structure, which keeps whatever "
                               "verdict the analysis stages gave it")
    checks.insert(0, {"key": "sliced",
                      "condition": "The slicer produced a sliced plate",
                      "state": "PASS", "detail": "3mf written and parsed"})
    fits = info.get("bed_fits")
    checks.insert(1, {"key": "bed_fits",
                      "condition": "The object sits inside the build volume "
                                   "in the orientation it was sliced in",
                      "state": "PASS" if fits else
                               ("FAIL" if fits is False else "UNKNOWN"),
                      "detail": "the slicer's own arrangement result"})
    ok = bool(fits) and info.get("print_time_s") is not None \
        and info.get("filament_g") is not None
    return ok, checks, ("`pass` means the slice ran and the object fits the "
                        "plate. It is not a spec 13.3 verdict: all five 13.3 "
                        "conditions are UNKNOWN here.")

"""Study-file loading and validation: JSON or YAML, checked before anything runs.

Three jobs, in the order they matter:

1. **Read the file at all.** A missing or malformed study is exit code 2 --
   "the input could not be read" -- not a traceback and not exit 1. Exit 1
   means "I ran it and it failed"; those are different findings and the
   distinction is the whole point of the exit-code contract.
2. **Reject a study that means something other than what it says.** Wrong
   types, out-of-range values, unit systems that are not mm/N/MPa.
3. **Point at the line.** A validator that says "bad value for volfrac" and
   makes you find it is only half a validator.

The strictness is deliberately asymmetric. A **known key with a bad value is an
error**; an **unknown key is a warning**. Study files carry `_`-prefixed
comments and have accumulated legacy keys, and a schema that rejects a study
the solver would have handled correctly is worse than no schema at all -- it
teaches people to skip validation.
"""

import io
import json
import os

UNITS = "mm_N_MPa"


class InputError(Exception):
    """The study could not be read or is not valid. Exit code 2."""
    exit_code = 2

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = problems or []


class Problem:
    """One validation finding, with where it is."""

    def __init__(self, path, message, line=None, severity="error"):
        self.path = path
        self.message = message
        self.line = line
        self.severity = severity

    def __str__(self):
        where = "line %d" % self.line if self.line else "?"
        return "%s: %s: %s (%s)" % (self.severity.upper(), self.path or "<root>",
                                    self.message, where)

    def as_dict(self):
        return {"severity": self.severity, "path": self.path,
                "message": self.message, "line": self.line}


# --------------------------------------------------------------- the schema
# type names: "str" "num" "int" "bool" "obj" "arr" "any"
# Only what is checked is listed. Anything absent from here is reported as an
# unknown key (warning) and passed through untouched.

_AXES = ("X", "Y", "Z")

TOP = {
    "part":              {"type": "str"},
    "material":          {"type": "str"},
    "build_axis":        {"type": "str", "enum": _AXES, "upper": True},
    "raster_axis":       {"type": "str", "enum": _AXES, "upper": True},
    # Which way is down for `"contact": "ground"`. Defaults to `build_axis`
    # (a part printed standing up stands on its build plate); declared
    # separately so the build direction can be compared X / Y / Z (spec
    # Phase 6) without the part falling over onto a different face.
    "ground_axis":       {"type": "str", "enum": _AXES, "upper": True},
    # `units` is checked separately (it also has to look at project.units, and
    # absent-vs-wrong are different findings), so it is only listed here to
    # keep it off the unknown-key warning list.
    "units":             {"type": "any"},
    "target_elements":   {"type": "int", "min": 1},
    "resolution_mm":     {"type": "num", "gt": 0},
    "voxel_mm":          {"type": "num", "gt": 0},
    "safety_factor":     {"type": "num", "gt": 0},
    "max_deflection_mm": {"type": "num", "gt": 0},
    "walls":             {"type": "int", "min": 1},
    "infill":            {"type": "num", "min": 0, "max": 1},
    "regions":           {"type": "obj"},
    "cases":             {"type": "arr"},
    "geometry":          {"type": "obj"},
    # How the chosen shape is turned back into a part: the preserve geometry is
    # re-added at its CAD dimensions and its features cut, on a grid fine
    # enough to hold them. See topopt/finish.py.
    "finish":            {"type": "obj"},
    "design":            {"type": "obj"},
    "name":              {"type": "str"},
    "supports":          {"type": "arr"},
    "loads":             {"type": "arr"},
    "gravity":           {"type": "obj"},
    "project":           {"type": "obj"},
    "required_cases":    {"type": "arr", "items": {"type": "str"}},
    # A machine name ("X1C") or a custom envelope object. Checked against the
    # design space at setup, before the solver runs -- a design space larger
    # than the printer can only ever produce a part that does not fit.
    "printer":           {"type": "any"},
}

PRINTER = {
    "key":              {"type": "str"},
    "name":             {"type": "str"},
    "printer":          {"type": "str"},
    "based_on":         {"type": "str"},
    "build_volume_mm":  {"type": "arr", "items": {"type": "num", "gt": 0}},
    "usable_mm":        {"type": "arr", "items": {"type": "num", "gt": 0}},
    "nominal_mm":       {"type": "arr", "items": {"type": "num", "gt": 0}},
    "nozzle_mm":        {"type": "num", "gt": 0},
    "margin_mm":        {"type": "num", "min": 0},
    "note":             {"type": "str"},
    "allow_oversize_design_space": {"type": "bool"},
}

DESIGN = {
    "volfrac":        {"type": "num", "gt": 0, "max": 1},
    "filter_mm":      {"type": "num", "gt": 0},
    "symmetry":       {"type": "arr", "items": {"type": "str", "enum": _AXES,
                                                "upper": True}},
    "penal":          {"type": "num", "gt": 0},
    "beta":           {"type": "num", "gt": 0},
    "projection_eta": {"type": "num", "min": 0, "max": 1},
    "move":           {"type": "num", "gt": 0},
    "tol_change":     {"type": "num", "gt": 0},
    "volume_exact":   {"type": "bool"},
    "strict_bc":      {"type": "bool"},
    "overhang":       {"type": "any"},          # bool, or {"up": "-Z", "p": 40}
    "stress":         {"type": "any"},          # bool, or {"safety_factor": 3, ...}
    "optimizer":      {"type": "str", "enum": ["oc", "mma"]},
    "keep_in":        {"type": "any"},          # legacy, pre-`geometry`
}

FINISH = {
    "voxel_mm":  {"type": "num", "gt": 0},
    "add":       {"type": "any"},
    "cut":       {"type": "any"},
    "max_cells": {"type": "int", "min": 1},
    # Gaussian sigma for the optimised material, in mm. "auto" (half the
    # analysis voxel), a number, or false. Never applied to the CAD bodies.
    "smooth_mm": {"type": "any"},
    # How finely each CAD body is sampled inside one finish cell, per axis,
    # to get the occupancy the isosurface follows. 1 is the old boolean
    # answer (the CAD surface quantised to the finish grid).
    "occupancy_samples": {"type": "int", "min": 1},
    # How far the finish stage may bridge the gap between the optimised body
    # and a CAD body where they join, in mm. Default: one analysis voxel.
    "bridge_mm": {"type": "any"},
}

GEOMETRY = {
    "clearance_mm":    {"type": "num", "min": 0},
    "pad_voxels":      {"type": "int", "min": 0},
    # How thick the ground-contact band is. One voxel by default: a support is
    # a condition on a surface, and more than that starts fixing structure.
    "ground_depth_mm": {"type": "num", "gt": 0},
    "voxel_mm":        {"type": "num", "gt": 0},
    "target_elements": {"type": "int", "min": 1},
    "design_space":    {"type": "any"},
    "preserve":        {"type": "any"},
    "obstacle":        {"type": "any"},
    "starting_shape":  {"type": "any"},
    "optional_starting_shape": {"type": "any"},
    "start_shape":     {"type": "any"},
    "keep_in":         {"type": "any"},
    "keep_out":        {"type": "any"},
    "domain":          {"type": "any"},
    "notes":           {"type": "any"},
}

CASE = {
    "name":     {"type": "str"},
    "supports": {"type": "arr"},
    "loads":    {"type": "arr"},
    "gravity":  {"type": "obj"},
    # Spec 15.1: a normal case is judged at the study's safety factor, an
    # abnormal one (drop, one-foot-caught, transport) at the lower bar the
    # reference document allows. Undeclared means normal -- the stricter one.
    "load_class": {"type": "str", "enum": ["normal", "abnormal"],
                   "lower": True},
}

REGION_TYPES = ("box", "cylinder", "sphere", "face", "all", "invert")


# ------------------------------------------------------------ line locator

def key_lines(text):
    """`{json path: 1-based line}` for every key in a JSON document.

    A hand-rolled scanner rather than a parser hook, because neither `json` nor
    `yaml` will hand back source positions. It only has to be right about where
    keys are, which is what an error message needs to point at.
    """
    lines = {}
    stack = []          # [{"kind": "obj"/"arr", "key": str/int}]
    i, n, line = 0, len(text), 1
    expect_key = False

    def path_of():
        """Every frame's current key, folded into `a.b[0].c`.

        A frame's `key` is where we are *inside* that container, so the stack
        read bottom-up already spells the path -- object frames contribute a
        name, array frames an index.
        """
        out = ""
        for fr in stack:
            k = fr.get("key")
            if k is None:
                continue
            if isinstance(k, int):
                out += "[%d]" % k
            else:
                out = k if not out else out + "." + k
        return out

    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if ch == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\":
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                if text[j] == "\n":
                    line += 1
                buf.append(text[j])
                j += 1
            s = "".join(buf)
            if stack and stack[-1]["kind"] == "obj" and expect_key:
                stack[-1]["key"] = s
                lines[path_of()] = line
                expect_key = False
            i = j + 1
            continue
        if ch == "{":
            stack.append({"kind": "obj", "key": None})
            expect_key = True
            i += 1
            continue
        if ch == "[":
            stack.append({"kind": "arr", "key": 0})
            i += 1
            continue
        if ch in "}]":
            if stack:
                stack.pop()
            # After a close, the next token is `,` or another close -- never a
            # key. Letting `,` be the only thing that arms `expect_key` keeps a
            # nested object's siblings from being mistaken for its own keys.
            expect_key = False
            i += 1
            continue
        if ch == ",":
            if stack and stack[-1]["kind"] == "obj":
                expect_key = True
            elif stack and stack[-1]["kind"] == "arr":
                stack[-1]["key"] = (stack[-1]["key"] or 0) + 1
            i += 1
            continue
        i += 1
    return lines


def _join(parent, key):
    if isinstance(key, int):
        return "%s[%d]" % (parent, key)
    return "%s.%s" % (parent, key) if parent else str(key)


# ---------------------------------------------------------------- checking

def _type_ok(value, want):
    if want == "any":
        return True
    if want == "str":
        return isinstance(value, str)
    if want == "bool":
        return isinstance(value, bool)
    if want == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "obj":
        return isinstance(value, dict)
    if want == "arr":
        return isinstance(value, list)
    return True


def _check_value(value, rule, path, add):
    want = rule.get("type", "any")
    if not _type_ok(value, want):
        add(path, "expected %s, got %s" % (want, type(value).__name__))
        return
    if "enum" in rule:
        v = value
        if isinstance(value, str):
            if rule.get("upper"):
                v = value.upper()
            elif rule.get("lower"):
                v = value.lower()
        if v not in rule["enum"]:
            add(path, "must be one of %s, got %r"
                % (", ".join(map(str, rule["enum"])), value))
    for op, sym in (("min", ">="), ("max", "<="), ("gt", ">"), ("lt", "<")):
        if op in rule and isinstance(value, (int, float)):
            lim = rule[op]
            ok = {"min": value >= lim, "max": value <= lim,
                  "gt": value > lim, "lt": value < lim}[op]
            if not ok:
                add(path, "must be %s %s, got %r" % (sym, lim, value))
    if "items" in rule and isinstance(value, list):
        for i, item in enumerate(value):
            _check_value(item, rule["items"], _join(path, i), add)


def _check_obj(data, table, path, add, what):
    for key, value in data.items():
        if str(key).startswith("_"):
            continue                    # a comment, by convention
        p = _join(path, key)
        rule = table.get(key)
        if rule is None:
            add(p, "unknown %s key; it will be ignored" % what,
                severity="warning")
            continue
        _check_value(value, rule, p, add)


def validate(data, text=None, strict_units=False):
    """-> [Problem]. Errors are things that would make the run mean something
    other than what the file says; warnings are things worth knowing."""
    problems = []
    lines = key_lines(text) if text else {}

    def line_for(path):
        """The line of `path`, or of the nearest ancestor that has one.

        An array element and a key that is *missing* have no line of their own.
        Pointing at the enclosing object is still far more useful than pointing
        at nothing, so walk up until something is known.
        """
        p = path
        while p:
            if p in lines:
                return lines[p]
            cut = max(p.rfind("."), p.rfind("["))
            if cut <= 0:
                break
            p = p[:cut]
        return lines.get(p)

    def add(path, message, severity="error"):
        problems.append(Problem(path, message, line_for(path), severity))

    if not isinstance(data, dict):
        return [Problem("", "the study must be an object, got %s"
                        % type(data).__name__)]

    _check_obj(data, TOP, "", add, "top-level")

    # units: absent is allowed (everything in this project is mm/N/MPa and
    # nothing else is implemented), but a *declared* unit system that is not
    # mm_N_MPa is an error, not a note -- a study that says it is in inches
    # would be silently solved as millimetres.
    declared = data.get("units", (data.get("project") or {}).get("units"))
    if declared is None:
        if strict_units:
            add("units", "no unit system declared; expected %r" % UNITS)
        else:
            add("units", "not declared; assumed %s (nothing else is "
                         "implemented)" % UNITS, severity="info")
    elif str(declared) != UNITS:
        add("units" if "units" in data else "project.units",
            "unit system %r is not supported; only %r" % (declared, UNITS))

    # build_axis decides which failure index means what: interlayer tension and
    # interlaminar shear are read along it. Defaulting silently to Z means the
    # reported safety factors quietly assume Z layers, so say so. Anisotropic
    # analysis must refuse outright -- see `Study.require_build_axis`.
    if "build_axis" not in data:
        add("build_axis",
            "not declared; assumed Z. The FDM failure indices (interlayer "
            "tension, interlaminar shear) are evaluated along this axis, so "
            "the safety factors reported will assume Z layers.",
            severity="warning")

    if isinstance(data.get("design"), dict):
        _check_obj(data["design"], DESIGN, "design", add, "design")
    if isinstance(data.get("geometry"), dict):
        _check_obj(data["geometry"], GEOMETRY, "geometry", add, "geometry")
    if isinstance(data.get("finish"), dict):
        _check_obj(data["finish"], FINISH, "finish", add, "finish")

    pr = data.get("printer")
    if isinstance(pr, dict):
        _check_obj(pr, PRINTER, "printer", add, "printer")
        for k in ("build_volume_mm", "usable_mm", "nominal_mm"):
            v = pr.get(k)
            if isinstance(v, list) and len(v) != 3:
                add(_join("printer", k),
                    "needs exactly three numbers (x, y, height in mm), got %d"
                    % len(v))
    elif pr is not None and not isinstance(pr, str):
        add("printer", "must be a machine name or an object with "
                       "`build_volume_mm`")
    elif pr is None:
        add("printer",
            "not declared, so the build volume is not checked against a real "
            "machine. A design space larger than the printer can only ever "
            "produce a part that does not fit, and that is knowable now rather "
            "than after the solve.", severity="warning")

    regions = data.get("regions")
    if isinstance(regions, dict):
        for name, spec in regions.items():
            if str(name).startswith("_"):
                continue
            p = _join("regions", name)
            if not isinstance(spec, dict):
                add(p, "a region must be an object")
                continue
            kind = str(spec.get("type", "box")).lower()
            if kind not in REGION_TYPES:
                add(_join(p, "type"), "unknown region type %r; expected one of %s"
                    % (spec.get("type"), ", ".join(REGION_TYPES)))

    cases = data.get("cases")
    if isinstance(cases, list):
        if not cases:
            add("cases", "no load cases; there is nothing to solve")
        for i, c in enumerate(cases):
            p = _join("cases", i)
            if not isinstance(c, dict):
                add(p, "a case must be an object")
                continue
            _check_obj(c, CASE, p, add, "case")
            if not c.get("supports"):
                add(_join(p, "supports"),
                    "no supports; the part is unconstrained and the solve "
                    "will be singular")
            if not c.get("loads") and not c.get("gravity"):
                add(_join(p, "loads"), "no loads and no gravity; nothing pushes")
    elif cases is None and not data.get("loads") and not data.get("geometry"):
        add("cases", "no `cases` and no top-level `loads`")

    # A study may name the cases it is not allowed to be run without. This is
    # how "the required load cases are present" becomes checkable at all: which
    # cases are mandatory is a property of the part being designed, not of the
    # tool, so the study has to say. Silence means no requirement is declared.
    required = data.get("required_cases")
    if isinstance(required, list) and required:
        have = set()
        for c in (cases or []):
            if isinstance(c, dict) and isinstance(c.get("name"), str):
                have.add(c["name"])
                have.add(c["name"].split()[0] if c["name"].split() else c["name"])
        for i, want in enumerate(required):
            if not isinstance(want, str):
                continue
            if want not in have and not any(n.startswith(want) for n in have):
                add(_join("required_cases", i),
                    "required load case %r is not among the cases defined (%s)"
                    % (want, ", ".join(sorted(have)) or "none"))

    return problems


# ----------------------------------------------------------------- loading

def _read_text(path):
    if not os.path.exists(path):
        raise InputError("no such study file: %s" % path)
    if os.path.isdir(path):
        raise InputError("%s is a directory, not a study file" % path)
    try:
        with io.open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError as exc:
        raise InputError("could not read %s: %s" % (path, exc))


def parse(text, path=""):
    """JSON, or YAML when the extension says so. -> dict."""
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            raise InputError(
                "%s is YAML, but PyYAML is not installed.\n"
                "  pip install pyyaml            # or: pip install -e \"topopt[yaml]\"\n"
                "PyYAML is an optional dependency: the voxel path itself needs "
                "only numpy and scipy, so YAML support is not forced on every "
                "install. Convert the study to JSON to avoid it entirely."
                % path)
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise InputError("%s is not valid YAML: %s" % (path or "input", exc))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError("%s is not valid JSON: %s at line %d column %d"
                         % (path or "input", exc.msg, exc.lineno, exc.colno))


def load(path, strict_units=False):
    """-> (data, problems, text). Raises InputError for anything unreadable.

    Validation problems are *returned*, not raised: the caller decides whether
    a warning is worth stopping for, and `setup` wants to print all of them
    rather than the first.
    """
    text = _read_text(path)
    data = parse(text, path)
    if data is None:
        raise InputError("%s is empty" % path)
    if not isinstance(data, dict):
        raise InputError("%s must contain an object at the top level, got %s"
                         % (path, type(data).__name__))
    return data, validate(data, text, strict_units=strict_units), text


def format_problems(problems, path="", show_info=True):
    """Human-readable block, worst first, with line numbers where known."""
    order = {"error": 0, "warning": 1, "info": 2}
    keep = [p for p in problems if show_info or p.severity != "info"]
    if not keep:
        return ""
    keep.sort(key=lambda p: (order.get(p.severity, 3), p.line or 0))
    out = []
    for p in keep:
        loc = "%s:%d" % (os.path.basename(path), p.line) if p.line else \
            (os.path.basename(path) if path else "")
        tag = {"error": "ERROR", "warning": "WARN ", "info": "note "}.get(
            p.severity, p.severity)
        out.append("  %s %s  %s: %s" % (tag, loc, p.path or "<root>", p.message))
    return "\n".join(out)

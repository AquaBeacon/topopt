"""`topopt slice` — hand the shape to a real slicer and read the answer back.

    topopt slice part.stl --out DIR [--printer X1C] [--layer-mm 0.2]
                                    [--material PLA] [--supports auto|none|tree]
                                    [--orient keep|auto]

Writes `slice.json` (`topopt/slice/1`) next to the sliced 3mf: print time,
filament used, how much of that filament is support, layer count, whether the
slicer could place the object on the plate, and the slicer's own warnings.

Why this exists at all: every printability number the toolkit had before this
was geometric. `printcheck` counts voxels with nothing under them and
`printability` measures a bounding box, and neither one has ever seen a
toolpath. "This part takes 4 h 38 m and 143 g, of which 22 g is scaffolding"
is not derivable from a mesh -- it is derivable from a slice, and from nothing
else.

**Exit codes** (spec 16.3, and one rule that outranks all of them):

    0  sliced, fits the plate
    1  the slicer failed, timed out, or the object does not fit
    2  the input or the requested profile could not be read
    3  no slicer installed on this machine

Spec 19.5: **a failed slice is never a structural pass, and never a structural
failure either.** This command reports on the slicer. It does not touch the
verdict the analysis stages reached, and `cli/report.py` renders the slicer as
its own section with its own UNKNOWN so that a machine without a slicer cannot
quietly downgrade -- or upgrade -- a part that was analysed perfectly well.

**What the slicer will not answer.** All five spec 13.3 support conditions are
about *where* support lands. The slicer reports quantity only. They come back
UNKNOWN, with the reason written into `slice.json`, and the geometric answers
stay with `printcheck`. `pass: true` here means "it sliced and it fits", and
says so in the file.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt import external, materials, printers, slicer            # noqa: E402
from topopt.schema import InputError                                # noqa: E402


WHAT = "reading real print time, filament and support"

# The slicer says a lot at --debug 2; these are the two sentences it uses when
# the object cannot be placed on the plate, which is a finding about the part
# and not a crash of the tool.
_TOO_BIG = ("no object is fully inside the print volume",
            "can not be arranged inside plate", "has no object fully inside it")

_LOGLINE = re.compile(r"\[(warning|error)\]\s*(.*)")


def _presets(exe):
    root = slicer.profiles_root(exe)
    if root is None:
        # The binary is there and its profile tree is not, so there is no
        # usable slicer here. Exit 3 rather than 2: nothing the user typed
        # caused it, and the message has to be about the installation.
        raise external.ToolMissing(
            "slicer", "%s, and the built-in printer/process/filament profiles "
                      "were not found next to %s (set TOPOPT_SLICER_PROFILES "
                      "to the `resources/profiles` directory)" % (WHAT, exe))
    return slicer.Presets(root), root


def _write_presets(presets, outdir, machine, process, filament, supports):
    """Flatten the three presets into `outdir` and return what was written.

    They go into the output directory, not a temporary one, because
    `slice.json` records the files that produced the numbers -- and a
    provenance record pointing at a deleted temp file is not one.
    """
    mode = slicer.SUPPORT_MODES[supports]
    files = {}
    for label, name, override in (("machine", machine, {}),
                                  ("process", process, mode),
                                  ("filament", filament, {})):
        d = presets.resolve(name)
        d.update(override)
        p = os.path.join(outdir, "preset_%s.json" % label)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
        files[label] = os.path.abspath(p)
    return files


def _run(exe, files, part, outdir, project, orient, timeout, bed_type=None):
    """-> (returncode, combined output, seconds). Never raises for a crash."""
    argv = [
        exe,
        "--load-settings", "%s;%s" % (files["machine"], files["process"]),
        "--load-filaments", files["filament"],
        "--arrange", "1",
        # `keep` is the default on purpose. printers.py: a part that only fits
        # rotated has to be *printed* rotated, and then the layers no longer
        # run the way the orthotropic analysis assumed they did. Letting the
        # slicer re-orient the object silently would break the link between
        # the analysis and the object analysed.
        "--orient", "1" if orient == "auto" else "0",
        "--slice", "0",
        "--export-3mf", project,
        "--outputdir", os.path.abspath(outdir),
        "--debug", "2",
        os.path.abspath(part),
    ]
    if bed_type:
        argv[1:1] = ["--curr-bed-type", bed_type]
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired:
        return None, "timed out after %g s" % timeout, time.time() - t0
    except OSError as exc:
        return None, "could not run the slicer: %s" % exc, time.time() - t0
    return r.returncode, (r.stdout or "") + (r.stderr or ""), time.time() - t0


def _warnings(text):
    """The slicer's own warnings and errors, de-noised of timestamps."""
    seen, out = set(), []
    for line in text.splitlines():
        m = _LOGLINE.search(line)
        if not m:
            continue
        msg = "%s: %s" % (m.group(1), m.group(2).strip())
        if msg not in seen:
            seen.add(msg)
            out.append(msg)
    return out[:40]


def _read_3mf(path):
    """-> (slice_info dict, gcode header dict, per-feature filament mm).

    Raises `InputError` when the file is not a sliced project: an exported 3mf
    with no `slice_info.config` is the shape the CLI leaves behind when it
    loaded the model and then refused to slice it, and reporting that as a
    successful slice with null numbers is exactly the confusion spec 19.5
    forbids.
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "Metadata/slice_info.config" not in names:
            raise InputError(
                "%s has no Metadata/slice_info.config -- the slicer wrote a "
                "project but never sliced it." % path)
        info = slicer.parse_slice_info(
            z.read("Metadata/slice_info.config").decode("utf-8", "replace"))
        gcodes = sorted(n for n in names
                        if n.startswith("Metadata/plate_")
                        and n.endswith(".gcode"))
        header, per_feature = {}, {}
        if gcodes:
            g = z.read(gcodes[0]).decode("utf-8", "replace")
            header = slicer.parse_gcode_header(g)
            per_feature = slicer.filament_by_feature(g)
    return info, header, per_feature


def _quantities(info, header, per_feature, material, layer_mm):
    """Everything the two files say, reconciled, with its provenance.

    The support figure is the only number here the slicer does not state
    itself, so it is the only one that has to prove itself: the per-feature
    sum must reproduce the header's own filament total. When it does not, the
    support fields are null with the discrepancy in the note -- an unchecked
    number is worse than a missing one, because it gets quoted.
    """
    out = {}
    diameter = header.get("filament_diameter_mm")
    length = header.get("filament_mm")
    out["print_time_s"] = info.get("print_time_s")
    out["print_time_text"] = header.get("estimated_time")
    out["model_print_time_text"] = header.get("model_printing_time")
    out["filament_g"] = (header.get("filament_g")
                         if header.get("filament_g") is not None
                         else info.get("filament_g"))
    out["filament_mm"] = length
    out["layers"] = header.get("layers", info.get("layers"))
    out["max_z_mm"] = header.get("max_z_mm")
    # The slicer's layer count is not height / layer height. The stock Bambu
    # profiles set `independent_support_layer_height`, so support prints on
    # layers of its own between the object's -- the tall tripod is 140 mm of
    # 0.20 mm layers (700) and the machine executes 1035 of them. `layers` is
    # what the machine does; `object_layers` is the part.
    out["object_layers"] = (int(round(out["max_z_mm"] / float(layer_mm)))
                            if out.get("max_z_mm") else None)
    out["layers_note"] = (
        "`layers` is every layer the machine executes. With "
        "`independent_support_layer_height` on (the stock profile) support "
        "adds layers of its own, so it exceeds `object_layers` "
        "(max_z / layer height).")
    out["bed_fits"] = info.get("bed_fits")
    out["support_used"] = info.get("support_used")

    # The material card is the toolkit's own source for density (AGENTS.md);
    # the slicer preset carries its own. They are close but not equal -- PLA is
    # 1.24 here and 1.26 in the Bambu profile -- so both are recorded and the
    # one actually used for each conversion is named.
    card = materials.get_material(material)
    out["material_density_g_cm3"] = float(card.density)
    slicer_rho = header.get("filament_density_g_cm3")
    out["slicer_filament_density_g_cm3"] = slicer_rho

    if length is not None and diameter:
        # Wire geometry: exact, and it checks against the weight the slicer
        # reports rather than depending on either density.
        out["filament_mm3"] = length * slicer.filament_area_mm2(diameter)
        out["filament_mm3_source"] = ("length x wire cross-section, both from "
                                      "the slicer's G-code header")
    elif out["filament_g"] is not None:
        out["filament_mm3"] = out["filament_g"] / float(card.density) * 1000.0
        out["filament_mm3_source"] = ("weight / density, density from "
                                      "materials.py (%s, %.3f g/cm3)"
                                      % (card.name, card.density))
    else:
        out["filament_mm3"] = None
        out["filament_mm3_source"] = None

    out["filament_by_feature_mm"] = {k: round(v, 3)
                                     for k, v in sorted(per_feature.items())}
    out["support_filament_g"] = None
    out["support_mm3"] = None
    out["support_mm"] = None
    out["support_fraction"] = None
    if not per_feature or length is None or not diameter:
        out["support_note"] = (
            "the slicer reports no support total anywhere in the 3mf, and "
            "this run had no G-code to re-derive one from.")
        return out
    total = sum(per_feature.values())
    rel = abs(total - length) / length if length else 1.0
    if rel > 5e-3:
        out["support_note"] = (
            "not reported: the per-feature filament sum (%.2f mm) does not "
            "reproduce the slicer's own total (%.2f mm, %.2f %% apart), so "
            "the split between part and support cannot be trusted."
            % (total, length, 100 * rel))
        return out
    smm = slicer.support_mm(per_feature)
    rho = slicer_rho if slicer_rho else float(card.density)
    out["support_mm"] = smm
    out["support_mm3"] = smm * slicer.filament_area_mm2(diameter)
    out["support_filament_g"] = out["support_mm3"] * rho / 1000.0
    out["support_fraction"] = smm / total if total else None
    out["support_note"] = (
        "summed over the G-code's `; FEATURE: Support*` blocks (the slicer "
        "states no support total); the same sum reproduces its own filament "
        "total to %.4f %%. Density %.3f g/cm3 from the %s. This is extruded "
        "1.75 mm wire, not the part-space volume `printcheck.json` reports."
        % (100 * rel, rho,
           "slicer preset" if slicer_rho else "materials.py card"))
    return out


def slice_part(part, outdir, printer_key="X1C", layer_mm=0.2, material="PLA",
               supports="auto", orient="keep", timeout=600.0, bed_type=None):
    """-> the `slice.json` blob. Raises ToolMissing / InputError; never for a
    slicer that merely failed -- that is a recorded finding, not an exception."""
    if not os.path.isfile(part):
        raise FileNotFoundError("no such file: %s" % part)
    if supports not in slicer.SUPPORT_MODES:
        raise InputError("--supports must be one of: %s"
                         % ", ".join(sorted(slicer.SUPPORT_MODES)))
    if orient not in ("keep", "auto"):
        raise InputError("--orient must be `keep` or `auto`")

    exe = external.require("slicer", WHAT)
    _path, version = external.version("slicer")
    presets, root = _presets(exe)

    try:
        machine = slicer.pick_machine(printer_key)
        process = slicer.pick_process(presets, machine, layer_mm)
        filament = slicer.pick_filament(presets, machine, material)
    except slicer.PresetError as exc:
        raise InputError(str(exc))

    os.makedirs(outdir, exist_ok=True)
    files = _write_presets(presets, outdir, machine, process, filament,
                           supports)
    stem = os.path.splitext(os.path.basename(part))[0]
    project = stem + ".3mf"
    project_path = os.path.join(os.path.abspath(outdir), project)
    if os.path.exists(project_path):
        os.remove(project_path)
    result_path = os.path.join(os.path.abspath(outdir), "result.json")
    if os.path.exists(result_path):
        os.remove(result_path)

    options = {"bed_type": bed_type} if bed_type else {}
    code, output, seconds = _run(exe, files, part, outdir, project, orient,
                                 timeout, **options)
    result_error = None
    # Recent Bambu CLI versions put the actual reason in result.json and
    # leave stdout empty (e.g. Windows return code -50 appears unsigned).
    if os.path.isfile(result_path):
        try:
            with open(result_path, encoding="utf-8") as f:
                result = json.load(f)
            if isinstance(result, dict) and result.get("error_string"):
                result_error = str(result["error_string"])
                output += "\n" + result_error
        except (OSError, ValueError):
            pass
    warns = _warnings(output)
    error = None
    info = header = {}
    per_feature = {}
    if not os.path.exists(project_path):
        low = output.lower()
        if any(s in low for s in _TOO_BIG):
            error = ("the slicer could not place the object inside the build "
                     "volume: %s" % printers.resolve(printer_key).name)
        elif code is None:
            error = output.strip().splitlines()[0] if output.strip() else \
                "the slicer did not run"
        elif result_error:
            error = result_error
        else:
            error = ("the slicer exited %s without writing a sliced project"
                     % code)
    else:
        try:
            info, header, per_feature = _read_3mf(project_path)
        except (InputError, zipfile.BadZipFile, KeyError) as exc:
            error = "could not read the sliced project: %s" % exc

    q = (_quantities(info, header, per_feature, material, layer_mm)
         if not error else {})
    if error and "build volume" in error:
        q["bed_fits"] = False
    ok, checks, note = slicer.judge(q, error)

    blob = {
        "schema": slicer.SCHEMA,
        "part": os.path.abspath(part),
        "tool": {"name": "Bambu Studio / OrcaSlicer CLI",
                 "path": exe, "version": version},
        "printer": printer_key,
        "printer_name": printers.resolve(printer_key).name,
        "material": material,
        "bed_type": bed_type or "slicer default",
        "layer_mm": float(layer_mm),
        "supports": supports,
        "support_type": slicer.SUPPORT_MODES[supports].get("support_type"),
        "orient": orient,
        "presets": {"root": root, "machine": machine, "process": process,
                    "filament": filament, "files": files},
        "project_3mf": project_path if os.path.exists(project_path) else None,
        # The slicer drops the plate G-code into `--outputdir` as a side
        # effect. Recorded rather than deleted: under `--model` this lands in
        # 04_print/ and it is literally the file the machine executes.
        "plate_gcode": (os.path.join(os.path.abspath(outdir), "plate_1.gcode")
                        if os.path.exists(os.path.join(outdir,
                                                       "plate_1.gcode"))
                        else None),
        "seconds": round(seconds, 2),
        "exit_code_slicer": code,
        "error": error,
        "warnings": warns,
        "checks": checks,
        "pass": bool(ok),
        "note": note,
    }
    blob.update(q)
    blob.setdefault("bed_fits", None)
    return blob


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt slice", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", help="the STL to slice")
    ap.add_argument("--out", default="slice_out",
                    help="directory for slice.json, the 3mf and the presets")
    ap.add_argument("--printer", default="X1C",
                    help="one of: %s" % ", ".join(sorted(slicer.MACHINE_PRESET)))
    ap.add_argument("--layer-mm", type=float, default=0.2)
    ap.add_argument("--material", default="PLA")
    ap.add_argument("--bed-type", help="installed plate name, e.g. 'Textured PEI Plate'; omitted uses slicer default")
    ap.add_argument("--supports", default="auto",
                    choices=sorted(slicer.SUPPORT_MODES))
    ap.add_argument("--orient", default="keep", choices=("keep", "auto"),
                    help="`keep` slices the part as modelled. `auto` lets the "
                         "slicer turn it over -- which changes which way the "
                         "layers run, so the orthotropic analysis no longer "
                         "describes the object being printed.")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    blob = slice_part(a.part, a.out, a.printer, a.layer_mm, a.material,
                      a.supports, a.orient, a.timeout, bed_type=a.bed_type)
    out = os.path.join(a.out, "slice.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2)

    if not a.quiet:
        print("wrote %s" % out)
        print("  slicer      %s" % (blob["tool"]["version"] or "?"))
        print("  presets     %s | %s | %s"
              % (blob["presets"]["machine"], blob["presets"]["process"],
                 blob["presets"]["filament"]))
        if blob["error"]:
            print("  ERROR       %s" % blob["error"])
        else:
            print("  print time  %s (%s s)"
                  % (blob.get("print_time_text"), blob.get("print_time_s")))
            print("  filament    %.2f g / %.0f mm3 over %s layers "
                  "(%s of object)"
                  % (blob.get("filament_g") or 0.0,
                     blob.get("filament_mm3") or 0.0, blob.get("layers"),
                     blob.get("object_layers")))
            if blob.get("support_filament_g") is not None:
                print("  support     %.2f g / %.0f mm3 (%.1f %% of filament)"
                      % (blob["support_filament_g"], blob["support_mm3"],
                         100 * (blob.get("support_fraction") or 0.0)))
            else:
                print("  support     not reported -- %s"
                      % blob.get("support_note"))
            print("  bed fits    %s" % blob.get("bed_fits"))
        for w in blob["warnings"][:8]:
            print("  slicer %s" % w)
        print("  spec 13.3   UNKNOWN (%d conditions) -- the slicer reports "
              "how much support, never where" % len(slicer.SUPPORT_CONDITIONS))
    return 0 if blob["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

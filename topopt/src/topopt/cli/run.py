"""`topopt run` — the whole pipeline in order, stopping where it should.

    setup -> opt -> extract -> ccx -> print -> report -> publish

Each stage runs only if the ones it depends on succeeded. A stage that needs a
tool this machine does not have is **skipped and recorded as skipped**, and the
report then lists its checks as UNKNOWN -- never as passing.

The distinction this command is built around:

- **stopped**  a required check failed. Nothing after it is trustworthy, so
               nothing after it runs.
- **skipped**  the stage could not run here (no CalculiX, no slicer). The
               stages before it are still valid, and the summary says exactly
               how far the results go.

Reporting those as the same thing is how an incomplete analysis gets filed as
a passing one.

With `--model DIR` the stages write the spec 17.5 layout -- `01_assumptions/`,
`02_setup/`, `03_optimization/`, `04_print/` -- instead of one flat directory,
and `publish` copies the chosen shape into `04_print/` with a page saying which
file to print and what is still wrong with it. Without it, `--out` behaves
exactly as before.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt.external import find                                  # noqa: E402
from topopt import backend as _backend                                   # noqa: E402


STAGES = ("setup", "opt", "extract", "ccx", "print", "slice", "report",
          "publish")
# Order matters and matches spec 16.2 exactly. `print` runs after
# `ccx` so a re-analysis that contradicts the voxel result is on the
# table before anyone starts judging printability. `slice` follows
# `print` -- geometry first, then the toolpath, and `report` after both
# so it can quote them. `publish` is last because it copies the chosen
# shape out and quotes the verdict, so it needs `report` to have
# produced one.

# spec 17.5: one folder per model, four stages inside it. `--model` writes
# this layout; `--out` still takes a single directory for a one-off run.
MODEL_DIRS = {"assumptions": "01_assumptions", "setup": "02_setup",
              "optimization": "03_optimization", "print": "04_print"}


def _call(module, argv):
    """Run one subcommand in-process. -> (exit code, error or None)."""
    import importlib
    mod = importlib.import_module("topopt.cli." + module)
    try:
        return int(mod.main(argv)), None
    except SystemExit as exc:                       # argparse
        return (int(exc.code or 0), None) if isinstance(exc.code, (int, type(None))) else (2, str(exc.code))
    except Exception as exc:                        # noqa: BLE001
        from topopt.schema import InputError
        from topopt.external import ToolMissing
        if isinstance(exc, ToolMissing):
            return 3, str(exc)
        if isinstance(exc, (InputError, FileNotFoundError, IsADirectoryError, UnicodeDecodeError)):
            return 2, str(exc)
        return None, "%s: %s" % (type(exc).__name__, exc)


QUICK_LEVELS = "0.5"
# `--quick` deliberately does NOT shorten the optimisation. The continuation
# schedule (penal, beta, the self-support ramp) is laid out over `--iters`,
# so fewer iterations is a *different* optimisation, not a faster one:
# child_jaw_gate at 40 iterations never recovered from the penal-3 spike
# and ended NOT CONNECTED, where the same study at 80 recovered by
# iteration 38 (measured 2026-09-08). Shorten with `--iters` on purpose.


def run(study, outdir, iters=60, elements=None, voxel=None, levels=None,
        isotropic=False, do_ccx=True, mesh_mm=None, converge=False,
        printer="X1C", quiet=False, only=None, stop_on_fail=True,
        model_dir=None, do_slice=True, quick=False, ccx_geometry="finished",
        supports="auto", layer_mm=0.2, bed_type=None):
    """`quick` is the exploration preset (try many variants fast, then spend
    the time on the one you keep): no CalculiX, no slicer, one threshold
    candidate, and `opt` / `extract` skip their slow exports. The
    verdict-deciding parts -- setup checks, the optimisation itself (at the
    full iteration count), the final FEA, printability -- all still run, and
    the summary records that the run was a quick one."""
    if quick:
        do_ccx = False
        do_slice = False
    levels = levels or (QUICK_LEVELS if quick else "0.4,0.5,0.6")
    # With `model_dir`, the stages write into the four numbered folders of
    # spec 17.5 instead of one flat directory. `outdir` stays the working
    # directory the analysis stages share, so nothing downstream has to learn
    # about the layout -- only where the two human-facing folders go.
    asm_dir = print_dir = None
    setup_dir = outdir
    if model_dir:
        outdir = os.path.join(model_dir, MODEL_DIRS["optimization"])
        asm_dir = os.path.join(model_dir, MODEL_DIRS["assumptions"])
        print_dir = os.path.join(model_dir, MODEL_DIRS["print"])
        # The setup previews go next to the geometry they describe. The report
        # still sees the validation results, because `opt` records them inside
        # its own summary -- so splitting the folders costs nothing downstream.
        setup_dir = os.path.join(model_dir, MODEL_DIRS["setup"])
        os.makedirs(setup_dir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(study))[0]
    log = []
    stopped_at = None
    t_start = time.time()
    t_last = [t_start]

    def record(name, code, err=None, skipped=None, note=None):
        now = time.time()
        entry = {"stage": name, "exit_code": code, "error": err,
                 "skipped": skipped, "note": note,
                 "seconds": round(now - t_last[0], 1)}
        t_last[0] = now
        log.append(entry)
        if not quiet:
            state = ("SKIPPED" if skipped else
                     "ERROR" if err else
                     "ok" if code == 0 else "exit %d" % code)
            # `err` wins over `note`. A note is written for the *expected*
            # non-zero exit of a stage, and printing it in place of a crash
            # reports a tool failure as a finding about the part: a run that
            # died with MemoryError announced "shape does not meet the
            # requirement", which is a sentence about a bracket, not about
            # the machine it was running on.
            print("[%-7s] %-8s %6.0f s  %s"
                  % (name, state, entry["seconds"], err or note or ""))
        return entry

    want = set(only) if only else set(STAGES)

    # ---- setup
    if "setup" in want:
        argv = [study, "--out", setup_dir, "--iters", str(iters)] + (
            ["--isotropic"] if isotropic else []) + (
            ["--elements", str(elements)] if elements else []) + (
            ["--voxel", str(voxel)] if voxel else []) + (
            ["--quiet"] if quiet else [])
        code, err = _call("setup", argv)
        record("setup", code, err)
        if err or (code != 0 and stop_on_fail):
            stopped_at = "setup"

    # ---- opt
    if stopped_at is None and "opt" in want:
        argv = [study, "--out", outdir, "--iters", str(iters)] + (
            ["--elements", str(elements)] if elements else []) + (
            ["--voxel", str(voxel)] if voxel else []) + (
            ["--isotropic"] if isotropic else []) + (
            ["--quick"] if quick else []) + (
            ["--quiet"] if quiet else [])
        code, err = _call("optimize", argv)
        # Exit 1 from `opt` means "the shape does not meet the safety factor",
        # which is a finding worth extracting and reporting on -- so it does
        # not stop the pipeline. A crash does.
        record("opt", code, err,
               note=None if code == 0 or err else
               "shape does not meet the requirement; continuing so the "
               "report says why")
        if err or code not in (0, 1):
            stopped_at = "opt"

    # ---- extract
    if stopped_at is None and "extract" in want:
        argv = [outdir, "--study", study, "--out", outdir,
                "--levels", levels] + (["--isotropic"] if isotropic else []) + (
            ["--quick"] if quick else []) + (
            ["--quiet"] if quiet else [])
        code, err = _call("extract", argv)
        record("extract", code, err)
        if err or code not in (0, 1):
            stopped_at = "extract"

    # ---- ccx (skipped, not failed, when the tools are absent)
    if "ccx" in want:
        missing = [t for t in ("gmsh", "calculix") if find(t) is None]
        if missing or not do_ccx:
            record("ccx", None, skipped=True,
                   note=("%s not installed on this machine -- the independent "
                         "re-analysis was not run, and the report lists its "
                         "checks as UNKNOWN" % " and ".join(missing))
                   if missing else
                   ("disabled by --quick" if quick else "disabled with --no-ccx"))
        elif stopped_at is None:
            stl = _chosen_stl(outdir, prefer_finished=ccx_geometry == "finished")
            if stl is None:
                record("ccx", None, skipped=True,
                       note="no extracted STL to re-analyse")
            else:
                # The voxel FEA of the same shape, when one was written
                # here: the re-analysis is only worth running against
                # something to disagree with, and `--voxel-fea` is what makes
                # the comparison case by case instead of one number.
                vfea = _voxel_fea(outdir) if ccx_geometry == "analysis" else None
                argv = [stl, "--study", study, "--out", outdir] + (
                    ["--mesh-mm", str(mesh_mm)] if mesh_mm else []) + (
                    ["--converge"] if converge else []) + (
                    ["--isotropic"] if isotropic else []) + (
                    ["--voxel-fea", vfea] if vfea else []) + (
                    ["--quiet"] if quiet else [])
                code, err = _call("ccx", argv)
                record("ccx", code, err)

    # ---- printability checks on the chosen shape
    if stopped_at is None and "print" in want:
        note = _printcheck(outdir, study, quiet)
        record("print", 0 if note.startswith("wrote") else None,
               None if note.startswith("wrote") else note, note=note)

    # ---- slice: real print time / filament / support (skipped, not failed,
    # when no slicer is installed -- spec 19.5, a slicer this machine does not
    # have must never be read as a structural verdict either way)
    if "slice" in want:
        if find("slicer") is None or not do_slice:
            record("slice", None, skipped=True,
                   note=("no slicer installed on this machine -- print time, "
                         "filament and support were not measured, and the "
                         "report lists them as UNKNOWN")
                   if do_slice else
                   ("disabled by --quick" if quick else "disabled with --no-slice"))
        elif stopped_at is not None:
            record("slice", None, skipped=True,
                   note="the pipeline stopped before there was a shape to slice")
        else:
            stl = _chosen_stl(outdir, prefer_finished=True)
            if stl is None:
                record("slice", None, skipped=True,
                       note="no extracted STL to slice")
            else:
                # Into 04_print/ under --model: a sliced 3mf is a thing you
                # print, which is what that folder is for (spec 17.5). The
                # report finds `slice.json` there through `collect`'s sibling
                # lookup, so there is only ever one copy of it.
                sdir = os.path.join(print_dir or outdir, "slice")
                from topopt.loadcase import load_case_from_json
                material = load_case_from_json(study).material
                argv = [stl, "--out", sdir, "--printer", printer,
                        "--material", material, "--supports", supports,
                        "--layer-mm", str(layer_mm), "--orient", "keep"] + (
                    ["--bed-type", bed_type] if bed_type else []) + (
                    ["--quiet"] if quiet else [])
                code, err = _call("slice", argv)
                # Exit 1 here is the slicer's finding, not the part's. It does
                # not stop the pipeline and it does not touch `valid_through`
                # -- the structural verdict stays whatever the analysis made
                # it (spec 19.5).
                record("slice", code, err,
                       note=None if code == 0 or err else
                       "the slicer did not produce a usable plate; the "
                       "report says so separately from the structural verdict")

    # Record this invocation before report reads the directory. In particular,
    # a skipped/failed stage must not borrow last week's successful output.
    def write_summary():
        summary = {"schema": "topopt/run/1", "study": study, "outdir": outdir,
                   "model_dir": model_dir, "quick": bool(quick),
                   "seconds": round(time.time() - t_start, 1),
                   "stages": log, "stopped_at": stopped_at,
                   "valid_through": _valid_through(log, stopped_at)}
        with open(os.path.join(outdir, "run_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        return summary

    write_summary()
    # ---- report always runs: a stopped pipeline still needs to say why
    if "report" in want:
        # Write the manifest *first*. The CLI wrapper writes one when the whole
        # command returns, which is one second after the report is rendered --
        # so every published report came out with an empty reproducibility
        # block and the commit hash sitting in the file next to it. The
        # wrapper's later write supersedes this one and carries the true exit
        # code; the provenance fields are identical either way.
        try:
            from topopt import manifest as _man
            _man.write(outdir, "run", [study, "--model", model_dir or outdir],
                       started=t_start, probe_external=True)
        except Exception as exc:                                  # noqa: BLE001
            record("manifest", None, "%s: %s" % (type(exc).__name__, exc))
        code, err = _call("report", [outdir, "--study", study]
                          + (["--quiet"] if quiet else []))
        record("report", code, err)

    # ---- the assumptions page. Written even when the run stopped early: it
    # describes the inputs, which exist regardless of whether anything solved,
    # and a failed run is exactly when someone needs to check them.
    if asm_dir and "report" in want:
        try:
            from topopt import assumptions as _asm
            from topopt.loadcase import load_case_from_json
            from topopt.cli.setup import prepare
            st = load_case_from_json(study)
            setup, rep = prepare(st, elements, voxel, verbose=False)
            _asm.write(asm_dir, st, setup, rep, isotropic=isotropic)
            record("assumptions", 0, note="wrote %s"
                   % os.path.join(asm_dir, "assumptions.md"))
        except Exception as exc:                                  # noqa: BLE001
            record("assumptions", None,
                   "%s: %s" % (type(exc).__name__, exc))

    # ---- 04_print: the chosen shape, on its own, with what is wrong with it
    if print_dir and "publish" in want and stopped_at is not None:
        record("publish", None, skipped=True, note="pipeline stopped; no new part published")
    elif print_dir and "publish" in want:
        try:
            from topopt import publish as _pub
            _pub.publish(outdir, print_dir, quiet=quiet,
                         printfit=_print_fit(outdir, printer),
                         printer_key=printer)
            record("publish", 0, note="wrote %s"
                   % os.path.join(print_dir, "PRINT.md"))
        except Exception as exc:                                  # noqa: BLE001
            record("publish", None, "%s: %s" % (type(exc).__name__, exc))

    return write_summary()


def _valid_through(log, stopped_at):
    """Which stages produced results a reader may rely on.

    Spec 19.6: when the pipeline stops early or a tool is missing, saying
    'how far the results go' is a required output, not a courtesy.

    A *skipped* stage does not truncate the list. `print` does not depend on
    `ccx`, so a missing CalculiX must not make a printability check that ran
    perfectly well disappear from the record -- that would under-report the
    work, which is the opposite error to over-reporting it but is still wrong.
    A stage that *errored* does truncate: anything after it ran on a state
    nobody has checked.
    """
    good = []
    for e in log:
        if e["stage"] == "report":
            continue
        if e["stage"] == "slice":
            continue  # slicer failure does not invalidate structural analysis
        if e["error"] or (e["exit_code"] not in (0, 1) and not e["skipped"]):
            break
        if e["stage"] == stopped_at:
            break
        if not e["skipped"]:
            good.append(e["stage"])
    return good


def _print_fit(outdir, printer):
    """Does the chosen STL fit the machine, and what is the best orientation?

    Read off the finished mesh rather than the design space, so PRINT.md
    quotes the object being printed. Returns None rather than raising: this is
    a courtesy for the print page, and the binding check already ran at setup.
    """
    stl = _chosen_stl(outdir, prefer_finished=True)
    if stl is None:
        return None
    try:
        from topopt.cli.printability import analyse
        r = analyse(stl, printer, min_features=False)
        return {"fits": bool(r.get("fits_as_modelled")),
                "fits_somehow": bool(r.get("fits_in_some_orientation")),
                "size_mm": r.get("bbox_as_modelled_mm"),
                "best_orientation":
                    (r.get("best_orientation") or {}).get("orientation")}
    except Exception:                                             # noqa: BLE001
        return None


def _voxel_fea(outdir):
    """A `*_fea.json` from `topopt fea` in this run's output directory, or None.

    The pipeline itself does not run `topopt fea` as a stage -- `extract`
    keeps its re-analysis inside the extract summary -- so this is only there
    when somebody ran it by hand. Absent is the normal case and not a problem;
    the comparison is simply not made.
    """
    import glob
    got = sorted(glob.glob(os.path.join(outdir, "*_fea.json")))
    return got[0] if got else None


def _chosen_stl(outdir, prefer_finished=False):
    """The STL for the threshold `extract` chose, if there is one.

    `prefer_finished` picks `finish_stl` when a finish stage ran. Use it for
    every step that is answering a question about **the object that comes off
    the printer**: how long it takes, what it weighs, whether it fits.

    MEASURED (tall tripod v7): without it, `slice` reported 6 h 26 m and
    188.89 g for a part whose camera mount -- 143 cm3 of the 424 cm3 shipped
    -- was not in the file it sliced, and the orientation figure said the part
    was 144 mm tall when the STL beside it is 196.5. The finished mesh is the
    one `04_print/PRINT.md` calls "the mesh to slice"; slicing anything else
    and printing the numbers next to it is how the wrong figure gets believed.

    CCX also prefers the finished body by default. Only an explicit
    `--ccx-geometry analysis` uses the coarse body for solver comparison.
    """
    import glob
    ex = glob.glob(os.path.join(outdir, "*_extract.json"))
    if not ex:
        return None
    with open(ex[0], encoding="utf-8") as f:
        blob = json.load(f)
    out = blob.get("outputs") or {}
    keys = ("finish_stl", "stl") if prefer_finished else ("stl",)
    for key in keys:
        p = out.get(key)
        if p:
            # A missing finished artifact is not permission to analyse or
            # slice the coarse body under its name.
            return p if os.path.exists(p) else None
    return None


def _printcheck(outdir, study, quiet):
    """Support-contact and enclosed-support checks on the chosen shape."""
    import glob
    import numpy as np
    from topopt import printcheck
    from topopt.cli.extract import load_density, recorded_grid
    from topopt.cli.setup import prepare
    from topopt.loadcase import load_case_from_json
    from topopt import generative as gen

    try:
        st = load_case_from_json(study)
        _base, x_phys, summary = load_density(outdir)
        h_rec, _o = recorded_grid(summary)
        setup, _rep = prepare(st, voxel=h_rec, verbose=False)
        level = 0.5
        for p in glob.glob(os.path.join(outdir, "*_extract.json")):
            with open(p, encoding="utf-8") as f:
                level = json.load(f).get("chosen_level", 0.5)
        mask, _conn = gen.final_geometry(x_phys, setup, level=level,
            self_support=(summary.get("settings") or {}).get("overhang"), study=st)
        rep = printcheck.report(mask, setup.preserve, setup.h, st.build_axis)
        rep["threshold"] = level
        out = os.path.join(outdir, "printcheck.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
        return "wrote printcheck.json (threshold %.2f)" % level
    except Exception as exc:                                      # noqa: BLE001
        return "printability checks could not run: %s: %s" % (
            type(exc).__name__, exc)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study")
    ap.add_argument("--out", default="run_out")
    ap.add_argument("--model", metavar="DIR",
                    help="write the spec 17.5 model layout under DIR: "
                         "01_assumptions/ 02_setup/ 03_optimization/ "
                         "04_print/. Overrides --out.")
    ap.add_argument("--iters", type=int, default=60,
                    help="optimiser iterations (default 60). --quick does not "
                         "change this: the continuation schedule is laid out "
                         "over the iteration count, so fewer iterations is a "
                         "different optimisation, not a faster one")
    ap.add_argument("--quick", action="store_true",
                    help="exploration preset for trying many variants: one "
                         "threshold (%s), no CalculiX, no slicer, no smooth "
                         "STL / STEP / finish grid. Setup checks, the "
                         "optimisation, the final FEA and the printability "
                         "checks all still run. Re-run without it for the "
                         "variant you keep." % QUICK_LEVELS)
    ap.add_argument("--elements", type=int)
    ap.add_argument("--voxel", type=float)
    ap.add_argument("--levels", default=None,
                    help="threshold candidates for extract (default "
                         "0.4,0.5,0.6, or %s with --quick)" % QUICK_LEVELS)
    ap.add_argument("--isotropic", action="store_true")
    ap.add_argument("--no-ccx", action="store_true",
                    help="skip the CalculiX re-analysis even if it is installed")
    ap.add_argument("--no-slice", action="store_true",
                    help="skip the slicer stage even if a slicer is installed")
    ap.add_argument("--mesh-mm", type=float)
    ap.add_argument("--converge", action="store_true",
                    help="three mesh densities in the re-analysis")
    ap.add_argument("--ccx-geometry", choices=("finished", "analysis"), default="finished",
                    help="re-analyse the shipped mesh (default), or the analysis-grid mesh for solver comparison")
    ap.add_argument("--supports", choices=("auto", "none", "tree"), default="auto")
    ap.add_argument("--layer-mm", type=float, default=0.2)
    ap.add_argument("--bed-type", help="plate name for slicing, e.g. 'Textured PEI Plate'")
    ap.add_argument("--only", help="comma-separated subset of: %s"
                                   % ", ".join(STAGES))
    ap.add_argument("--keep-going", action="store_true",
                    help="do not stop when setup fails (for diagnosis)")
    ap.add_argument("--printer", default="X1C",
                    help="machine the study is checked against when it does "
                         "not declare one itself")
    ap.add_argument("--quiet", action="store_true")
    _backend.add_cli_argument(ap)
    a = ap.parse_args(argv)
    _backend.apply_cli_choice(a)

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    if only:
        bad = [s for s in only if s not in STAGES]
        if bad:
            raise SystemExit("unknown stage(s): %s; known: %s"
                             % (", ".join(bad), ", ".join(STAGES)))

    t0 = time.time()
    s = run(a.study, a.out, a.iters, a.elements, a.voxel, a.levels,
            a.isotropic, not a.no_ccx, a.mesh_mm, a.converge,
            printer=a.printer, quiet=a.quiet, only=only,
            stop_on_fail=not a.keep_going, model_dir=a.model,
            do_slice=not a.no_slice, quick=a.quick,
            ccx_geometry=a.ccx_geometry, supports=a.supports, layer_mm=a.layer_mm,
            bed_type=a.bed_type)
    outdir = s["outdir"]

    if not a.quiet:
        print("\nvalid through: %s"
              % (" -> ".join(s["valid_through"]) or "nothing"))
        if s["stopped_at"]:
            print("STOPPED at '%s'. Stages after it were not run and their "
                  "checks are UNKNOWN, not passing." % s["stopped_at"])
        skipped = [e for e in s["stages"] if e["skipped"]]
        if skipped:
            print("skipped: %s" % ", ".join(e["stage"] for e in skipped))
        print("%s (%.0f s)" % (os.path.join(outdir, "report.md"),
                               time.time() - t0))
        if a.model:
            print("to print:      %s"
                  % os.path.join(a.model, MODEL_DIRS["print"], "PRINT.md"))

    for entry in s["stages"]:
        if entry["stage"] == "slice":
            continue
        if entry["exit_code"] in (2, 3):
            return entry["exit_code"]
        if entry["error"]:
            return 1
    if s["stopped_at"]:
        return 1
    rep = os.path.join(outdir, "report.json")
    if os.path.exists(rep):
        with open(rep, encoding="utf-8") as f:
            return 0 if json.load(f).get("verdict") == "PASS" else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

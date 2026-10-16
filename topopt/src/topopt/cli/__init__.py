"""`topopt <command>` — one entry point for the whole toolkit.

Subcommands are imported lazily so `topopt overhang` does not pay for scipy
and `topopt fea` does not pay for matplotlib.
"""

import importlib
import sys

# command -> (module under topopt.cli, one-line help)
COMMANDS = {
    "assumptions": ("assumptions", "the 前提条件 page: loads, material, machine, what is fixed"),
    "setup":     ("setup",     "validate a design-space setup and preview it (no solve)"),
    "opt":       ("optimize",  "run the topology optimisation"),
    "extract":   ("extract",   "density field -> shape candidates at several thresholds"),
    "ccx":       ("ccx",       "independent re-analysis with CalculiX on a tet mesh"),
    "report":    ("report",    "one report.md: verdict first, then why"),
    "run":       ("run",       "setup -> opt -> extract -> ccx -> print -> report"),
    "compare":   ("compare",   "does the parametric rebuild still carry the load?"),
    "fea":       ("fea",       "voxel FEA on an STL: deflection, stress, safety factor"),
    "print":     ("printability", "printer fit, best orientation, min feature, where to cut"),
    "slice":     ("slice",     "real print time, filament and support from a slicer CLI"),
    "overhang":  ("overhang",  "real support area in the modelled orientation"),
    "underwater-loads": ("underwater", "hydrostatic pressure, buoyancy and drag"),
    "sections":  ("sections",  "slice an STL and read the real dimensions off it"),
    "clearance": ("clearance", "swing a moving part about a hinge, check it clears"),
    "step2stl":  ("step2stl",  "planar-BREP STEP -> STL, no CAD kernel"),
    "validate":  ("validate",  "check a result JSON against its output schema"),
}

_USAGE = """usage: topopt <command> [options]

  %s

`topopt <command> --help` for the options of one command.
Typical order:

  topopt setup  study.json --out DIR        # seconds -- look at the preview
  topopt opt    study.json --out DIR        # minutes to hours
  topopt compare DIR rebuild.stl --study study.json
  topopt fea    rebuild_study.json --out DIR
"""


def _usage():
    width = max(len(c) for c in COMMANDS)
    lines = "\n  ".join("%-*s  %s" % (width, c, h) for c, (_m, h) in COMMANDS.items())
    return _USAGE % lines


def main(argv=None):
    # The docstrings that argparse prints as help carry em dashes and the
    # occasional Japanese word; a stock Windows console (cp932) raises
    # UnicodeEncodeError on them and `--help` dies before saying anything.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    if argv[0] in ("-V", "--version"):
        from topopt import __version__
        print("topopt %s" % __version__)
        return 0

    cmd = argv[0]
    if cmd not in COMMANDS:
        near = [c for c in COMMANDS if c.startswith(cmd)]
        hint = ("  did you mean: %s\n" % ", ".join(near)) if near else ""
        print("topopt: unknown command %r\n%s\n%s" % (cmd, hint, _usage()),
              file=sys.stderr)
        return 2

    mod = importlib.import_module("topopt.cli." + COMMANDS[cmd][0])
    rest = argv[1:]

    # Every run that produces an output directory gets a manifest.json beside
    # its results (spec 17.1). Written here, once, rather than in each
    # subcommand: a new subcommand cannot forget it and the fields cannot
    # drift. It is written even when the command failed -- the exit code is
    # part of what a reader needs to know, and "which stage was still valid"
    # is exactly the question a failed run leaves behind.
    import time
    from topopt import manifest as _manifest

    started = time.time()
    code = None
    try:
        try:
            code = mod.main(rest)
        except Exception as exc:                                # noqa: BLE001
            # Exit code 2 is "the input could not be read", and it is a
            # finding, not a crash: a study that does not exist, is not valid
            # JSON/YAML, or contradicts the schema is the user's answer, and a
            # traceback buries it. Everything else keeps propagating -- a bug
            # in the solver must not be dressed up as bad input.
            from topopt.schema import InputError
            from topopt.external import ToolMissing
            if isinstance(exc, ToolMissing):
                # Exit code 3, kept separate from 1 and 2 on purpose: a tool
                # this machine does not have is neither a failed part nor a
                # broken study, and conflating them sends someone off to
                # redesign a bracket that was fine.
                print("%s: %s" % (cmd, exc), file=sys.stderr)
                code = 3
            elif isinstance(exc, (InputError, FileNotFoundError,
                                  IsADirectoryError, UnicodeDecodeError)):
                print("%s: %s" % (cmd, exc), file=sys.stderr)
                code = getattr(exc, "exit_code", 2)
            else:
                raise
        return code
    finally:
        # `finally`, so a run that died with a traceback still leaves a record
        # saying which command was tried and against which inputs. There the
        # exit code is null: the run has no verdict, which is itself the point.
        try:
            _manifest.write(_manifest.out_dir_from_argv(rest), cmd, rest,
                            exit_code=code, seconds=time.time() - started,
                            started=started,
                            extra=None if code is not None else
                            {"crashed": True,
                             "crashed_note": "the command raised instead of "
                                             "returning an exit code; outputs "
                                             "in this directory may be partial"})
        except Exception as exc:                                # noqa: BLE001
            # A manifest is a record, not a result. Failing to write one must
            # not turn a passing run into a failing one, but must be visible.
            print("warning: could not write manifest.json (%s: %s)"
                  % (type(exc).__name__, exc), file=sys.stderr)

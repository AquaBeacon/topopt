#!/usr/bin/env python3
"""Self-check for the topopt install: does the whole stack still work?

    python topopt/tests/smoke.py

Builds a small cantilever from scratch (no external STL) and checks the things
that have actually gone wrong before, rather than the things that are easy to
assert:

  1  every module imports
  2  a hex8 cantilever's tip deflection matches Euler-Bernoulli
  3  preserve stays exactly 1 and obstacle exactly 0 after `Setup.impose`
  4  symmetry really mirrors the field -- this silently did nothing once,
     because np.flip mirrors about the *array* centre and grid rounding put
     that off the geometry mid-plane
  5  a blocky STL round-trips back to the same voxels
  6  the optimiser drives the objective down and still respects preserve
     and obstacle at the end

The checks themselves live in `checks.py`, so `pytest` runs exactly these and
not a second, drifting copy. This script stays runnable with nothing installed
but the package itself -- it is the thing you run when pytest is what you
suspect.

Exit 0 = all pass.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checks                                                   # noqa: E402


def main():
    print("topopt smoke test")
    failed = []
    ctx = None

    for name, fn, needs_ctx in checks.CHECKS:
        if needs_ctx and ctx is None:
            try:
                ctx = checks.Ctx()
            except Exception as exc:                            # noqa: BLE001
                print("  [FAIL] %-40s could not build the test bar: %s: %s"
                      % ("fixtures", type(exc).__name__, exc))
                failed.append("fixtures")
                break
        try:
            ok, detail = fn(ctx) if needs_ctx else fn()
        except Exception as exc:                                # noqa: BLE001
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        print("  [%s] %-40s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            failed.append(name)
        if name == "imports":
            if not ok:
                break                   # nothing below can mean anything
            import topopt
            print("  version %s" % topopt.__version__)

    print()
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

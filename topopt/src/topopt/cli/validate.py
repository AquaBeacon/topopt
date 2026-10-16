"""`topopt validate` -- check a result JSON against its own output schema.

    topopt validate out/ccx_summary.json
    topopt validate out/*.json
    topopt validate --print-doc > topopt/docs/output-schema.md

Every file `topopt` writes carries a `schema` key (`topopt/<kind>/<version>`,
with one legacy exception documented in `outschema.py`). This looks the value
up in the table for that kind and reports missing required keys, wrong types,
and unknown keys -- the output-side counterpart to `schema.py`'s study
validation.

Exit codes: 0 = every file given is valid (no errors; warnings are fine),
1 = at least one file has an error, 2 = a file could not be read at all
(missing, not JSON, not an object).
"""

import argparse
import sys

from topopt.outschema import load_and_validate, render_doc
from topopt.schema import format_problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt validate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="one or more result JSON files")
    ap.add_argument("--print-doc", action="store_true",
                    help="render topopt/docs/output-schema.md to stdout and "
                         "exit (no files needed)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.print_doc:
        sys.stdout.write(render_doc())
        return 0

    if not a.paths:
        ap.error("give at least one JSON file, or use --print-doc")

    worst = 0
    for path in a.paths:
        try:
            data, problems = load_and_validate(path)
        except (OSError, ValueError) as exc:
            print("%s: could not be read: %s" % (path, exc), file=sys.stderr)
            worst = max(worst, 2)
            continue
        errors = [p for p in problems if p.severity == "error"]
        if errors:
            worst = max(worst, 1)
        if not a.quiet:
            schema_value = data.get("schema") if isinstance(data, dict) else None
            state = "INVALID" if errors else "ok"
            print("%s: %s (schema %r, %d error(s), %d warning(s))"
                  % (path, state, schema_value, len(errors),
                     len(problems) - len(errors)))
            text = format_problems(problems, path)
            if text:
                print(text)
    return worst


if __name__ == "__main__":
    sys.exit(main())

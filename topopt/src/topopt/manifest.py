"""`manifest.json` -- everything needed to reproduce a run, written next to it.

One run of one command produces one manifest. It is written by the CLI front
end (`topopt.cli.main`) rather than by each subcommand, so a new subcommand
cannot forget to write one and the fields cannot drift apart between commands.

The rule the fields follow: **record what is actually known, and say so when
something is not.** A field that could not be determined is `null` with a
sibling `*_note` explaining why, never a plausible-looking guess. A manifest
that quietly invents a git commit is worse than one that admits there is no
repository.

What is deliberately not here: timing beyond wall-clock seconds, and memory.
Those belong to the run summary (spec 20 observability), not to reproducibility.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
import time

SCHEMA = "topopt/manifest/1"


class IncompleteManifest(Exception):
    """A manifest that would go to disk missing something spec 17.1 asks for.

    Spec 19.6's abnormal case: *stop* rather than write it. The rule the
    module is built on has two halves and only one of them is about honesty
    -- a value that could not be determined is `null` with a `*_note` -- and
    the other half is that the key is still there. A reader who opens a
    manifest and does not find `random_seed` cannot tell "this run had no
    seed" from "this tool forgot to record one", and the file's whole job is
    to make that distinction possible.
    """


# Spec 17.1's list, as the keys it becomes here. Kept in this module rather
# than read out of `outschema.py`: that table describes what a *reader* may
# rely on finding, and this one is the writer's own promise -- the two are
# meant to agree, and a test that checks they still do is worth more than one
# of them quietly deriving itself from the other.
REQUIRED = ("schema", "tool", "command", "run", "source", "environment",
            "inputs", "random_seed")


def check_complete(m):
    """Raise `IncompleteManifest` unless `m` is a manifest worth writing.

    Two rules, both from the module docstring: every key in `REQUIRED` is
    present (absent is not an option -- `null` is), and a `null` at the top
    level carries a sibling `<key>_note` saying why. Returns `m`, so it can
    wrap a value on the way past.
    """
    if not isinstance(m, dict):
        raise IncompleteManifest("a manifest must be an object, got %s"
                                 % type(m).__name__)
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        raise IncompleteManifest(
            "manifest is missing %s. Spec 17.1 asks for %s; a value that "
            "could not be determined is written as null with a `<key>_note` "
            "saying why, but the key itself is never left out -- an absent "
            "key reads as a tool that forgot, not as a fact about the run."
            % (", ".join(missing), ", ".join(REQUIRED)))
    unexplained = [k for k in REQUIRED
                   if m[k] is None and not m.get(k + "_note")]
    if unexplained:
        raise IncompleteManifest(
            "manifest has null %s with no `_note` saying why. A null without "
            "a reason is indistinguishable from a bug."
            % ", ".join(unexplained))
    return m


# Files worth hashing when they appear on a command line. Everything else on
# argv is recorded verbatim in `command` but not hashed -- hashing an output
# directory or a flag value is noise.
_HASHABLE = (".json", ".yaml", ".yml", ".stl", ".step", ".stp", ".3mf",
             ".scad", ".npy")

# External tools the pipeline may shell out to (spec 17.1). Absent is a fact,
# not an error: the voxel path runs on numpy and scipy alone.
_EXTERNAL = {
    "freecad":  ["freecad", "--version"],
    "calculix": ["ccx", "-v"],
    "gmsh":     ["gmsh", "--version"],
    "openscad": ["openscad", "--version"],
}


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _input_files(argv, limit_mb=512):
    """Hash the input files named on the command line.

    Only existing files with a known extension, and only the ones that were
    already there -- an `--out` path that the run is about to create is an
    output, and hashing it here would record the previous run's bytes.
    """
    out = []
    for a in argv:
        if not isinstance(a, str) or not a.lower().endswith(_HASHABLE):
            continue
        if not os.path.isfile(a):
            continue
        try:
            size = os.path.getsize(a)
            entry = {"path": os.path.abspath(a), "bytes": size}
            if size <= limit_mb * (1 << 20):
                entry["sha256"] = _sha256(a)
            else:
                entry["sha256"] = None
                entry["sha256_note"] = ("larger than %d MB, not hashed" % limit_mb)
            out.append(entry)
        except OSError as exc:
            out.append({"path": os.path.abspath(a), "sha256": None,
                        "sha256_note": "unreadable: %s" % exc})
    return out


def _git(cwd):
    """(commit, dirty, note). No repository is a normal answer, not a failure."""
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                             capture_output=True, text=True, timeout=10)
        if rev.returncode != 0:
            return None, None, "not a git repository (or git unavailable)"
        commit = rev.stdout.strip()
        st = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                            capture_output=True, text=True, timeout=10)
        dirty = bool(st.stdout.strip()) if st.returncode == 0 else None
        return commit, dirty, None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "git could not be run: %s" % exc


def _package_versions():
    vers = {}
    for name in ("numpy", "scipy", "matplotlib", "skimage", "pytest", "torch"):
        try:
            mod = __import__(name)
            vers[name] = getattr(mod, "__version__", None)
        except Exception:                                        # noqa: BLE001
            vers[name] = None
    return vers


def _external_versions(probe=True):
    """First line of each external tool's version output, or None if absent."""
    found = {}
    for name, cmd in _EXTERNAL.items():
        if not probe:
            found[name] = None
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            text = (r.stdout or "") + (r.stderr or "")
            line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            found[name] = line or None
        except (OSError, subprocess.SubprocessError):
            found[name] = None
    return found


def build(command, argv, exit_code=None, seconds=None, seed=None,
          extra=None, probe_external=True):
    """Assemble the manifest dict. Pure -- does not touch the filesystem
    except to stat and hash the inputs named on `argv`."""
    from topopt import __version__

    commit, dirty, git_note = _git(os.path.dirname(os.path.abspath(__file__)))
    m = {
        "schema": SCHEMA,
        "tool": {"name": "topopt", "version": __version__},
        "command": {
            "name": command,
            "argv": list(argv),
            "line": " ".join([command] + [str(a) for a in argv]),
            "cwd": os.getcwd(),
        },
        "run": {
            "started_utc": None,          # filled by write()
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "seconds": None if seconds is None else round(float(seconds), 3),
            "exit_code": exit_code,
            "exit_code_meaning": {
                0: "pass / valid", 1: "fail / invalid",
                2: "input could not be read", 3: "external tool missing",
            }.get(exit_code),
        },
        "source": {
            "git_commit": commit,
            "git_dirty": dirty,
        },
        "environment": {
            "python": sys.version.split()[0],
            "python_full": sys.version.replace("\n", " "),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "packages": _package_versions(),
            "external_tools": _external_versions(probe_external),
        },
        "inputs": _input_files(argv),
        "random_seed": seed,
    }
    if git_note:
        m["source"]["git_commit_note"] = git_note
    if seed is None:
        m["random_seed_note"] = (
            "no seed was declared by the study. The voxel path is "
            "deterministic (uniform initial density, OC update, CG solve), so "
            "a seed is not currently required for reproducibility.")
    if extra:
        m.update(extra)
    # Spec 19.6: never hand back something that is not a manifest. Checked
    # here rather than only in `write` so the caller that builds one for its
    # own use gets the same guarantee as the one that writes it out.
    return check_complete(m)


def write(out_dir, command, argv, exit_code=None, seconds=None, started=None,
          seed=None, extra=None, probe_external=True):
    """Write `out_dir/manifest.json`. Returns the path, or None if `out_dir`
    is not a directory that exists (a command that produced no output
    directory has nothing to describe)."""
    if not out_dir or not os.path.isdir(out_dir):
        return None
    m = build(command, argv, exit_code=exit_code, seconds=seconds, seed=seed,
              extra=extra, probe_external=probe_external)
    if started is not None:
        m["run"]["started_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime(started))
    check_complete(m)
    path = os.path.join(out_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def out_dir_from_argv(argv):
    """The output directory a subcommand was told to use, if any.

    Every subcommand that writes a directory spells it `--out DIR`; the ones
    that do not (`overhang`, `print`, `sections`, `clearance`, `step2stl`)
    write a single named file instead, and the manifest goes beside it.

    Returns None for a run that writes nothing but stdout -- `topopt overhang
    part.stl` with no `--json`. That run has no output for a manifest to
    describe, and dropping one into the working directory would be litter,
    so none is written. Ask for a file and you get a manifest with it.
    """
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--out="):
            return a.split("=", 1)[1]
    # `topopt run --model DIR` writes the spec 17.5 layout and takes no
    # `--out`. Without this the manifest was silently not written at all, and
    # the report's reproducibility section -- the one field a reviewer looks
    # for -- came out empty for exactly the runs meant to be published.
    for i, a in enumerate(argv):
        val = (argv[i + 1] if a == "--model" and i + 1 < len(argv)
               else a.split("=", 1)[1] if a.startswith("--model=") else None)
        if val:
            return os.path.join(val, "03_optimization")
    # a lone `--json FILE` / positional output file: use its directory
    for i, a in enumerate(argv):
        if a == "--json" and i + 1 < len(argv):
            return os.path.dirname(os.path.abspath(argv[i + 1])) or "."
    return None

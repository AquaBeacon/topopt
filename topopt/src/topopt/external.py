"""Detection of the optional external tools, and exit code 3 when one is absent.

The project rule is that numpy and scipy are the only hard dependencies: every
stage that needs FreeCAD, Gmsh, CalculiX or a slicer must detect it at run time
and say how to install it, rather than making the whole toolkit uninstallable
for the people who only wanted `topopt fea`.

Exit code 3 exists so that "the tool is not installed" is never confused with
"the part failed" (1) or "your study is broken" (2). A missing tool is a fact
about this machine, and reporting it as a failed part is the kind of error that
makes someone redesign a perfectly good bracket.
"""

import os
import shutil
import subprocess


class ToolMissing(Exception):
    """An external tool is needed and is not installed. -> exit code 3."""

    exit_code = 3

    def __init__(self, tool, what=None):
        self.tool = tool
        spec = TOOLS.get(tool, {})
        lines = ["%s is not installed, and %s needs it."
                 % (spec.get("label", tool), what or "this command")]
        if spec.get("why"):
            lines.append(spec["why"])
        hints = spec.get("install") or {}
        if hints:
            lines.append("Install it with one of:")
            for osname, cmd in hints.items():
                lines.append("  %-9s %s" % (osname, cmd))
        if spec.get("env"):
            lines.append("Already installed somewhere unusual? Point %s at the "
                         "executable." % spec["env"])
        super().__init__("\n".join(lines))


# name -> how to find it, how to ask its version, and how to install it.
# `env` is checked before PATH so a Windows install that never touched PATH
# (which is the normal case for FreeCAD and OpenSCAD) is still usable.
TOOLS = {
    "freecad": {
        "label": "FreeCAD",
        "exe": ["freecad", "FreeCAD", "freecadcmd", "FreeCADCmd"],
        "version": ["--version"],
        "env": "TOPOPT_FREECAD",
        "why": "STEP solids need a real CAD kernel; the built-in reader "
               "handles planar BREP only.",
        "install": {"Windows": "winget install FreeCAD.FreeCAD",
                    "macOS": "brew install --cask freecad",
                    "Linux": "sudo apt install freecad"},
        "extra_paths": [r"C:\Program Files\FreeCAD*\bin",
                        "/Applications/FreeCAD.app/Contents/Resources/bin"],
    },
    "gmsh": {
        "label": "Gmsh",
        "exe": ["gmsh"],
        "version": ["--version"],
        "env": "TOPOPT_GMSH",
        "why": "The tetrahedral mesh for the CalculiX re-analysis comes from "
               "Gmsh; the voxel grid is not a valid input for it.",
        "install": {"Windows": "download gmsh-<version>-Windows64.zip from "
                               "https://gmsh.info/ (or `winget install "
                               "gmsh.gmsh`), unzip it, and either put the "
                               "folder in C:\\Program Files or %USERPROFILE%"
                               "\\Tools, or set TOPOPT_GMSH to gmsh.exe",
                    "macOS": "brew install gmsh",
                    "Linux": "sudo apt install gmsh"},
        "extra_paths": [r"C:\Program Files\gmsh*",
                        os.path.expanduser(r"~\Tools\gmsh*")],
    },
    "calculix": {
        "label": "CalculiX (ccx)",
        "exe": ["ccx", "ccx_static", "CalculiX"],
        "version": ["-v"],
        "env": "TOPOPT_CCX",
        "why": "The re-analysis is only worth anything if it is a different "
               "solver on a different mesh; running the voxel solver twice "
               "reproduces its own assumptions.",
        "install": {"Windows": "download calculix_<version>_4win.zip from "
                               "https://www.dhondt.de/ (the author's own "
                               "Windows build; ccx_static.exe needs no DLLs), "
                               "unzip it into %USERPROFILE%\\Tools, or set "
                               "TOPOPT_CCX to ccx_static.exe",
                    "macOS": "brew install calculix-ccx",
                    "Linux": "sudo apt install calculix-ccx"},
        "extra_paths": [r"C:\Program Files\bConverged\CalculiX\bin",
                        os.path.expanduser(r"~\Tools\calculix*")],
    },
    "openscad": {
        "label": "OpenSCAD",
        "exe": ["openscad", "openscad.com"],
        "version": ["--version"],
        "env": "TOPOPT_OPENSCAD",
        "why": "Parametric parts in this repository are authored in OpenSCAD; "
               "rendering one to STL needs the binary.",
        "install": {"Windows": "winget install OpenSCAD.OpenSCAD",
                    "macOS": "brew install --cask openscad",
                    "Linux": "sudo apt install openscad"},
        "extra_paths": [r"C:\Program Files\OpenSCAD",
                        r"C:\Program Files (x86)\OpenSCAD",
                        "/Applications/OpenSCAD.app/Contents/MacOS"],
    },
    "slicer": {
        # Bambu Studio first: it is the X1 Carbon's own slicer, and OrcaSlicer
        # is a fork of it with the same command line, so `topopt slice` reads
        # either one without knowing which it got (spec 24). PrusaSlicer is
        # kept in the list because its `--export-gcode` produces a header the
        # same parser can read, but its preset system is not the one
        # `slicer.py` resolves.
        "label": "A slicer CLI (Bambu Studio, OrcaSlicer or PrusaSlicer)",
        "exe": ["bambu-studio", "bambu-studio-console", "BambuStudio",
                "orcaslicer", "orca-slicer", "OrcaSlicer",
                "prusa-slicer", "PrusaSlicer", "prusa-slicer-console"],
        "version": ["--help"],
        # `--help` opens with a boost log line ("Initializing
        # StaticPrintConfigs") before it says who it is, so taking the first
        # line of output records the logging framework instead of the version.
        "version_match": r"(BambuStudio|OrcaSlicer|PrusaSlicer)[- ]?"
                         r"(\d+\.[0-9.]+)",
        "env": "TOPOPT_SLICER",
        "why": "Real print time, real support volume and real material use "
               "come from slicing; everything else is an estimate.",
        "install": {"Windows": "download Bambu Studio from "
                               "https://bambulab.com/en/download/studio "
                               "(installs to C:\\Program Files\\Bambu Studio "
                               "and does not touch PATH), or "
                               "`winget install SoftFever.OrcaSlicer`",
                    "macOS": "brew install --cask orcaslicer",
                    "Linux": "download the AppImage from "
                             "https://github.com/SoftFever/OrcaSlicer/releases"},
        "extra_paths": [r"C:\Program Files\Bambu Studio",
                        r"C:\Program Files\OrcaSlicer",
                        r"C:\Program Files\Prusa3D\PrusaSlicer",
                        "/Applications/BambuStudio.app/Contents/MacOS",
                        "/Applications/OrcaSlicer.app/Contents/MacOS",
                        "/Applications/PrusaSlicer.app/Contents/MacOS"],
    },
}


def find(tool):
    """Path to the executable, or None. Checks $TOPOPT_<TOOL> first, then PATH,
    then the handful of places installers actually put things on Windows and
    macOS -- neither of which reliably adds itself to PATH."""
    spec = TOOLS.get(tool)
    if spec is None:
        raise KeyError("unknown external tool %r; known: %s"
                       % (tool, ", ".join(sorted(TOOLS))))
    env = spec.get("env")
    if env and os.environ.get(env):
        p = os.environ[env]
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
        # An env var that points at nothing is a mistake worth surfacing rather
        # than silently falling through to a different binary.
        raise ToolMissing(tool, "%s is set to %r, which is not an executable"
                          % (env, p))
    for name in spec["exe"]:
        p = shutil.which(name)
        if p:
            return p
    import glob
    for pattern in spec.get("extra_paths", []):
        for d in sorted(glob.glob(pattern), reverse=True):
            for name in spec["exe"]:
                for ext in ("", ".exe", ".com"):
                    p = os.path.join(d, name + ext)
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        return p
    return None


def require(tool, what=None):
    """Path to the executable, or raise `ToolMissing` (exit code 3)."""
    p = find(tool)
    if p is None:
        raise ToolMissing(tool, what)
    return p


def version(tool):
    """-> (path, version string or None). Never raises for a missing tool."""
    p = find(tool)
    if p is None:
        return None, None
    spec = TOOLS[tool]
    try:
        r = subprocess.run([p] + spec["version"], capture_output=True,
                           text=True, timeout=20)
        text = ((r.stdout or "") + (r.stderr or "")).strip()
        # Some tools open with a logging banner rather than their name, so the
        # first line records the log framework and the manifest then says the
        # run used "Initializing StaticPrintConfigs". `version_match` picks the
        # line that actually identifies the program.
        pattern = spec.get("version_match")
        if pattern:
            import re
            m = re.search(pattern, text)
            if m:
                return p, m.group(0)[:200]
        out = text.splitlines()
        return p, (out[0][:200] if out else None)
    except (OSError, subprocess.SubprocessError):
        return p, None


def survey():
    """Every tool, present or not -- for `manifest.json` and for reports.

    Recorded even when absent, because "this run had no CalculiX" is exactly
    the fact a reader needs to interpret a report with no re-analysis in it.
    """
    out = {}
    for name in sorted(TOOLS):
        p, v = version(name)
        out[name] = {"available": p is not None, "path": p, "version": v}
    return out

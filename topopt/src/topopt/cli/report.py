"""`topopt report` — one file that says whether the part is good, and why not.

What this is for
----------------
A run leaves a directory full of JSON. This turns it into one document a person
can read: what was analysed, under what assumptions, what passed, what failed,
and what is still unknown. The failures go **first**, before any of the
reassuring numbers, because a report whose FAILs are on page three gets skimmed
to page two.

The rule it enforces
--------------------
"Not checked" is never rendered as "fine". Every stop condition in spec 18 is
listed with one of three verdicts -- FAIL, PASS, or UNKNOWN -- and UNKNOWN
counts against the part, not for it. A missing CalculiX run is not evidence
that the shape is sound.

`--csv`
-------
`report.md` is for reading top to bottom; `--csv` writes the same numbers as
two flat tables for stacking results across parts or across runs of the same
part in a spreadsheet, next to `report.json`:

- `report_cases.csv` -- one row per load case: voxel FEA and CalculiX
  columns side by side (blank where that source did not run), the required
  safety factor, and the CalculiX/voxel displacement ratio.
- `report_checks.csv` -- one row per spec-18 stop condition, in the same
  order `report.md`'s three sections list them: name, pass (blank means
  "not checked", never "fine"), detail.

UTF-8, `\n` line endings, via the `csv` module.
"""

import argparse
import csv
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt.external import survey                                # noqa: E402


# Spec 18. Each entry: key, human wording, and the function that reads a
# verdict out of the collected results. Returning None means "not checked",
# which is reported as UNKNOWN rather than quietly omitted.
def _units(d):
    s = d.get("setup") or {}
    if not s:
        return None
    bad = [p for p in (s.get("errors") or []) if "unit" in str(p).lower()]
    return (False, "; ".join(bad)) if bad else (True, "mm / N / MPa")


def _material_model(d):
    """The material record, or None.

    Runs recorded before Phase 2 wrote `material` as a bare string ("PLA").
    Treating that as a dict is how a report crashes on the very archives it
    exists to summarise, so it is normalised here once.
    """
    m = (d.get("optimisation") or {}).get("material")
    if isinstance(m, str):
        return {"material_model": "isotropic_two_constant",
                "material": m,
                "note": "recorded before the orthotropic card existed"}
    if isinstance(m, dict) and m:
        return m
    fea = d.get("fea") or []
    mm = fea[0].get("material_model") if fea else None
    return mm if isinstance(mm, dict) and mm else None


def _material_nulls(d):
    m = _material_model(d)
    if not m:
        return None
    missing = [k for k, v in m.items() if v is None]
    if missing:
        return False, "null material values: %s" % ", ".join(missing)
    note = m.get("material_model", "?")
    if m.get("measured_for_specific_printer") is False:
        return True, ("%s, provisional card (no values measured on this "
                      "printer)" % note)
    return True, note


def _build_axis(d):
    """Declared, undeclared, or unknowable -- three answers, not two.

    A summary written before Phase 2 has no `build_axis` field at all, and
    reading that absence as "undeclared" puts a FAIL on studies that declare it
    perfectly well. A check that cries wolf on the archive gets switched off,
    so this returns UNKNOWN unless it has something that would have recorded
    the axis.
    """
    s = (d.get("optimisation") or {}).get("settings") or {}
    for src in (s, d.get("study_raw") or {},
                (d.get("fea") or [{}])[0].get("material_axes") or {}):
        a = src.get("build_axis")
        if a:
            return True, "build axis %s" % a
    if d.get("study_raw") is not None:
        return False, ("the study does not declare build_axis; the material "
                       "axes decide which strength applies to which direction")
    return None


def _setup_ok(d):
    s = d.get("setup") or {}
    if not s:
        return None
    return bool(s.get("ok")), ("%d error(s), %d warning(s)"
                               % (len(s.get("errors") or []),
                                  len(s.get("warnings") or [])))


def _connected(d):
    c = (d.get("optimisation") or {}).get("connectivity") or d.get("connectivity")
    if not c:
        return None
    detail = "%d component(s)" % c.get("components", 0)
    isl = c.get("island_mm3") or 0
    if isl:
        # Islands are dropped from the final geometry, so they do not fail
        # the part -- but a reader should know the optimiser put material
        # where nothing holds it, and how much.
        detail += "; %.0f mm3 of floating islands were dropped" % isl
    return bool(c.get("connected")), detail


def _self_supporting(d):
    u = d.get("unsupported")
    if u is None:
        u = ((d.get("optimisation") or {}).get("volumes") or {}).get("unsupported")
    if not u:
        return None
    area = float(u.get("area_mm2", 0.0))
    if u.get("voxels", 0) == 0:
        extra = ""
        if u.get("max_support_mm"):
            extra += ("; support columns up to %g mm were allowed"
                      % u["max_support_mm"])
        if u.get("allow_support"):
            extra += "; support allowed in %s" % ", ".join(u["allow_support"])
        c = d.get("connectivity") or {}
        if c.get("unsupported_pruned_voxels"):
            extra += ("; %d voxel(s) / %.0f mm3 that the smooth rule let through "
                      "were pruned from the thresholded shape before analysis"
                      % (c["unsupported_pruned_voxels"],
                         c.get("unsupported_pruned_mm3", 0)))
        if u.get("on_preserve_voxels"):
            extra = ("; %d voxel(s) / %.0f mm2 hang off a preserve body -- that "
                     "is the interface's own underside, chamfer or roof it in CAD"
                     % (u["on_preserve_voxels"], u.get("on_preserve_area_mm2", 0)))
        return True, ("every optimised voxel has material in the 45 degree "
                      "cone below it (build axis %s%s)%s"
                      % (u.get("up", "+"), u.get("axis", "?"), extra))
    return False, ("%d voxel(s), %.0f mm2 of material with nothing under it "
                   "within 45 degrees (build axis %s%s, at %s); it would print "
                   "on support or not at all. Enable design.overhang, or move "
                   "the build axis"
                   % (u.get("voxels", 0), area, u.get("up", "+"),
                      u.get("axis", "?"), u.get("bbox_mm", "?")))


def _converged(d):
    o = d.get("optimisation") or {}
    if "converged" not in o:
        return None
    # AGENTS.md: reaching the iteration cap is not convergence.
    return bool(o.get("converged")), "termination: %s" % o.get(
        "termination_reason", "unrecorded")


def _abnormal_bar(stress_settings):
    """" / SF x on abnormal case(s)" when spec 15.1 lowered the bar for some
    of them, empty when every case was held to the same number."""
    per = (stress_settings or {}).get("case_safety_factors") or []
    if not per or min(per) == max(per):
        return ""
    return " (abnormal case(s) at SF %.2f)" % min(per)


def _stress_constraint(d):
    """Spec 11.6: when the optimiser was run with a stress constraint, did it
    hold on the final density field? Not the final FEA's safety factor --
    that is `_safety` -- but whether the optimiser did what it was told."""
    o = d.get("optimisation")
    if not o:
        return None
    sc = o.get("stress_constraint")
    if not sc or not sc.get("enabled"):
        # Not an analysis that was skipped: the constraint is optional, and
        # without it strength is judged by the final FEA (`safety_factor`).
        return True, ("not requested (design.stress off); strength is judged "
                      "by the final FEA only")
    final = sc.get("final") or []
    worst = max(final, key=lambda c: c.get("max_ratio", 0)) if final else {}
    # Spec 15.1: the bar is per case, so quote the worst case's own figure
    # rather than the run's normal one -- an abnormal case is at 1.5 and
    # naming 3.0 next to its number would misreport what was required.
    need = worst.get("required_safety_factor", sc.get("safety_factor", 0))
    klass = worst.get("load_class")
    return (bool(sc.get("satisfied")),
            "SF %.2f%s x relaxed max-stress index %.3f in %r (%s), on the "
            "analysis grid at the final field"
            % (need, "" if not klass else " (%s)" % klass,
               worst.get("max_ratio", 0),
               worst.get("name", "?"), worst.get("worst_mode", "?")))


def _reanalysed(d):
    finished = ((d.get("extract") or {}).get("outputs") or {}).get("finish_stl")
    if finished:
        cc = d.get("ccx") or {}
        from topopt.provenance import sha256_file
        if not cc.get("stl_sha256") or not os.path.isfile(finished):
            return None
        if sha256_file(finished) != cc["stl_sha256"]:
            return None
        runs = cc.get("runs") or []
        if not runs or not runs[-1].get("cases"):
            return None
        return True, "finished STL content matches the independent re-analysis"
    f = d.get("fea")
    if not f:
        return None
    return True, "%d load case(s) re-solved on the thresholded solid" % len(f)


def _safety(d):
    f = d.get("fea") or []
    if not f:
        return None
    req = d.get("required_safety_factor")
    # Each case is judged at its own bar (spec 15.1): an abnormal case (a
    # drop, a one-foot-caught lift) carries `required_safety_factor` 1.5
    # from `topopt fea`; a case without one is held to the study's number.
    rows = [(s.get("safety_factor_p99_5"),
             s.get("required_safety_factor", req))
            for s in f if s.get("safety_factor_p99_5") is not None]
    rows = [(sf, need) for sf, need in rows if need is not None]
    if not rows:
        return None
    worst_sf, worst_need = min(rows, key=lambda r: r[0] / r[1])
    els = [s.get("min_safety_factor") for s in f
           if s.get("min_safety_factor") is not None]
    # Judged on the strength check alone. A case can fail overall on mesh
    # resolution while its safety factor is 20x what was asked for, and
    # folding that into this line would report a strength problem that does
    # not exist.
    if len(rows) != len(f) or len(els) != len(f):
        return None
    from topopt.cli.ccx import WORST_ELEMENT_MINIMUM
    return (all(sf >= need for sf, need in rows) and min(els) >= WORST_ELEMENT_MINIMUM,
            "p99.5 %.2f vs required %.2f (worst element %.2f)"
            % (worst_sf, worst_need, min(els) if els else float("nan")))


def _independent_analysis(d):
    runs = (d.get("ccx") or {}).get("runs") or []
    if not runs:
        return None
    cases = runs[-1].get("cases") or []
    if not cases or any(c.get("pass") is None for c in cases):
        return None
    expected = (d.get("study_raw") or {}).get("cases") or []
    if expected and {c["name"] for c in expected} != {c.get("case") for c in cases}:
        return None
    return (bool(runs[-1].get("pass")) and all(c["pass"] for c in cases),
            "finest CalculiX mesh: %d case(s), including worst-element strength and mesh quality" % len(cases))


def _equilibrium(d):
    runs = (d.get("ccx") or {}).get("runs") or []
    eqs = [r.get("equilibrium") for r in runs if r.get("equilibrium")]
    if not eqs:
        return None
    ok = all(e.get("pass") for e in eqs)
    errs = [e.get("relative_error") for e in eqs
            if e.get("relative_error") is not None]
    return ok, ("worst %.3f %%" % (100 * max(errs)) if errs
                else "no reaction totals recorded")


def _mesh_converged(d):
    for run in (d.get("ccx") or {}).get("runs") or []:
        if "--no-refine" in (run.get("surface_refinement") or {}).get("note", ""):
            return None
    m = (d.get("ccx") or {}).get("mesh_convergence")
    if m:
        return bool(m.get("pass")), m.get("note", "")
    # No CalculiX run, but the voxel FEA still reports whether the grid
    # resolves the thinnest section. That is a weaker statement than a
    # three-mesh study and is labelled as one -- but reporting UNKNOWN when
    # the run in hand says the mesh is too coarse throws away a real finding.
    for s in d.get("fea") or []:
        for chk in s.get("checks") or []:
            if chk.get("name") == "mesh resolution":
                if chk.get("pass"):
                    return None  # section resolution is not mesh convergence
                return False, (
                    "%s (voxel FEA only: %s vs required %s). A three-mesh "
                    "convergence study still has not been run."
                    % ("adequate" if chk.get("pass") else "TOO COARSE",
                       chk.get("got"), chk.get("required")))
    return None


def _watertight(d):
    """Is the shipped shape one closed body that does not pass through itself?

    Three findings, one verdict, and the detail says which one failed. A hole
    and an island are different mistakes and neither is visible in a volume
    total: a part that ships as two bodies has a perfectly reasonable mass, a
    plausible safety factor, and a crumb next to it on the plate.

    `collect` picks the finished isosurface when present, otherwise the
    blocky STL. Older merged exports with T-junctions need separate evidence;
    a watertight coarse body cannot certify a different delivered mesh.
    """
    s = d.get("shell")
    if not s:
        return None
    open_edges = s.get("open_edges", s.get("boundary_edges"))
    if open_edges is None:
        return None
    bodies = s.get("components")
    crossings = s.get("self_intersections")
    bad = []
    if open_edges:
        bad.append("%d open edge(s)" % open_edges)
    if bodies is not None and bodies > 1:
        bad.append("%d separate bodies" % bodies)
    if crossings:
        bad.append("%d self-intersecting face pair(s)" % crossings)
    if bad:
        return False, "; ".join(bad)
    detail = "0 open edges"
    if bodies is not None:
        detail += ", 1 body"
    # `None` is "not checked" (too many triangles to search) and must not read
    # as "none found" -- see `geom.self_intersections`.
    detail += (", no self-intersection" if crossings == 0
               else ", self-intersection not checked")
    return True, detail


def _min_wall(d):
    t = ((d.get("extract") or {}).get("chosen_thickness")
         or (d.get("thickness") or {}))
    opt = t.get("optimised_region") if t else None
    if not opt:
        return None
    req = d.get("min_wall_mm", 2.0)
    if opt.get("grid_limited"):
        return None                # a grid floor is not a wall measurement
    return opt["min_mm"] >= req, "%.2f mm vs required %.2f" % (opt["min_mm"], req)


def _support_on_faces(d):
    if ((d.get("extract") or {}).get("outputs") or {}).get("finish_stl"):
        return None  # current printcheck measures the analysis-grid body
    p = d.get("printcheck") or {}
    c = p.get("support_on_critical_faces")
    return (bool(c.get("pass")), c.get("note", "")) if c else None


def _enclosed_support(d):
    if ((d.get("extract") or {}).get("outputs") or {}).get("finish_stl"):
        return None
    p = d.get("printcheck") or {}
    c = p.get("enclosed_support")
    return (bool(c.get("pass")), c.get("note", "")) if c else None


CONDITIONS = [
    ("units", "Units are declared and are mm / N / MPa", _units),
    ("material_values", "Material properties have no unresolved nulls",
     _material_nulls),
    ("build_axis", "Material coordinate system (build_axis) is defined",
     _build_axis),
    ("setup_valid", "Setup validation passed (preserve/obstacle, loads on "
                    "preserve)", _setup_ok),
    ("connected", "All preserve regions are connected", _connected),
    ("converged", "The density field converged (not merely out of iterations)",
     _converged),
    ("stress_constraint", "The optimiser's stress constraint held on the "
                          "final density field", _stress_constraint),
    ("reanalysed", "The final shape was re-analysed", _reanalysed),
    ("independent_analysis", "Independent analysis passes every required case",
     _independent_analysis),
    ("safety_factor", "Safety factor meets the requirement on every case",
     _safety),
    ("equilibrium", "Load/reaction balance within 1 %", _equilibrium),
    ("mesh_convergence", "Mesh convergence confirmed", _mesh_converged),
    ("watertight", "Final shape is watertight", _watertight),
    ("min_wall", "Minimum wall thickness is met", _min_wall),
    ("self_supporting", "Self-supporting along the build axis (no voxel "
                        "without material in the 45 degree cone below it)",
     _self_supporting),
    ("support_faces", "No support lands on a mating (preserve) face",
     _support_on_faces),
    ("enclosed_support", "No support inside a sealed cavity", _enclosed_support),
]


def evaluate(data):
    """-> (fails, passes, unknowns) as lists of (key, wording, detail)."""
    fails, passes, unknown = [], [], []
    for key, text, fn in CONDITIONS:
        try:
            got = fn(data)
        except Exception as exc:                                  # noqa: BLE001
            unknown.append((key, text, "check raised: %s" % exc))
            continue
        if got is None:
            unknown.append((key, text, "not checked in this run"))
        else:
            ok, detail = got
            (passes if ok else fails).append((key, text, detail))
    return fails, passes, unknown


def collect(outdir, study_path=None):
    """Gather whatever the stages left behind. Missing stages stay missing.

    Files are read in *stage* order, not alphabetical order: a directory can
    hold both an `opt` summary and a later `extract` summary, and the later
    stage's results are the ones that describe the shape actually chosen.
    Sorting by name would let `gd_mount_study.json` win over
    `gd_mount_study_extract.json` purely because of where the underscore falls.
    """
    data = {}
    if study_path and os.path.exists(study_path):
        try:
            from topopt.schema import load as _load_study
            data["study_raw"] = _load_study(study_path)[0]
        except Exception:                                         # noqa: BLE001
            try:
                with open(study_path, encoding="utf-8") as f:
                    data["study_raw"] = json.load(f)
            except Exception:                                     # noqa: BLE001
                pass
        data["study_file"] = os.path.basename(study_path)

    def stage_key(p):
        b = os.path.basename(p)
        if b == "manifest.json":
            return 0
        if "setup" in b:
            return 1
        if b.endswith("_extract.json"):
            return 3
        if b == "ccx_summary.json":
            return 4
        return 2

    for path in sorted(glob.glob(os.path.join(outdir, "*.json")), key=stage_key):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, ValueError):
            continue
        if name == "manifest.json":
            data["manifest"] = blob
        elif name == "ccx_summary.json":
            data["ccx"] = blob
        elif name.endswith("_extract.json"):
            data["extract"] = blob
            ch = blob.get("chosen_level")
            for c in blob.get("candidates", []):
                if c.get("level") == ch:
                    data["fea"] = c.get("fea")
                    data["thickness"] = c.get("thickness")
                    data["connectivity"] = c.get("connectivity")
                    data["unsupported"] = (c.get("volumes") or {}).get("unsupported")
            data.setdefault("required_safety_factor",
                            blob.get("required_safety_factor"))
            # Modern finish_stl is the shipped isosurface. A good raw shell
            # says nothing about holes or extra bodies introduced by finish.
            shell = blob.get("shell") or {}
            outputs = blob.get("outputs") or {}
            key = "finish_stl" if outputs.get("finish_stl") else "stl"
            if shell.get(key):
                data["shell"] = shell[key]
            else:
                data.pop("shell", None)
        elif "setup" in name:
            data.setdefault("setup", blob)
        elif isinstance(blob, dict) and ("history" in blob or "settings" in blob):
            data["optimisation"] = blob
            data.setdefault("setup", blob.get("setup"))
            data.setdefault("fea", blob.get("final_fea"))
            data.setdefault("connectivity", blob.get("connectivity"))
    p = os.path.join(outdir, "printcheck.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            data["printcheck"] = json.load(f)
    # `slice.json` is the one result that does not live in the analysis
    # directory: under `--model` the sliced 3mf belongs in 04_print/, because
    # that folder holds the things you print (spec 17.5). Rather than keep two
    # copies -- which is how two different answers to the same question get
    # created -- the report looks in the places `topopt run` and `topopt
    # slice` actually write it.
    for cand in (os.path.join(outdir, "slice.json"),
                 os.path.join(outdir, "slice", "slice.json"),
                 os.path.join(outdir, os.pardir, "04_print", "slice",
                              "slice.json")):
        if os.path.exists(cand):
            with open(cand, encoding="utf-8") as f:
                data["slice"] = json.load(f)
            data["slice_file"] = os.path.abspath(cand)
            break
    # A report made during/after a failed or skipped invocation must not use
    # an older successful result that happens to remain in this directory.
    run_path = os.path.join(outdir, "run_summary.json")
    if os.path.isfile(run_path):
        with open(run_path, encoding="utf-8") as f:
            run = json.load(f)
        entries = {e["stage"]: e for e in run.get("stages", [])}
        for stage, keys in {
            "ccx": ("ccx",), "print": ("printcheck",), "slice": ("slice",),
            "opt": ("optimisation", "extract", "fea", "shell", "connectivity", "thickness", "ccx", "printcheck"),
            "extract": ("extract", "fea", "shell", "connectivity", "thickness", "ccx", "printcheck"),
        }.items():
            e = entries.get(stage)
            if e and (e.get("skipped") or e.get("error") or e.get("exit_code") not in (0, 1)):
                for key in keys:
                    data.pop(key, None)
        if run.get("stopped_at") == "setup":
            for key in ("optimisation", "extract", "fea", "shell", "connectivity", "thickness", "ccx", "printcheck"):
                data.pop(key, None)
    cc = data.get("ccx") or {}
    if cc.get("study_sha256") and study_path:
        from topopt.provenance import sha256_file
        if sha256_file(study_path) != cc["study_sha256"]:
            data.pop("ccx", None)
    return data


class _Sections(object):
    """Numbered headings, so the reader can say 'see section 5' and so the
    numbering cannot drift when a section is absent because its stage did not
    run."""

    def __init__(self):
        self.n = 0

    def __call__(self, title):
        self.n += 1
        return ["## %d. %s" % (self.n, title), ""]


def _footnote(text):
    """Explanatory prose, set apart from the numbers.

    Notes used to sit between the tables in the same weight as the results,
    which is how a reader skims past a table looking for the end of the
    paragraph and misses the row that mattered."""
    return ["> %s" % text, ""]


def render(data, title="topopt report"):
    fails, passes, unknown = evaluate(data)
    verdict = ("FAIL" if fails else ("INCOMPLETE" if unknown else "PASS"))
    sec = _Sections()

    part = ((data.get("assumptions") or {}).get("part")
            or (data.get("study_raw") or {}).get("part") or "")
    L = ["# %s%s" % (part + " — " if part else "", title), ""]

    # ---- the one line someone reads if they read nothing else
    headline = {
        "FAIL": "**FAIL** — %d stop condition(s) failed. This part is not "
                "signed off." % len(fails),
        "INCOMPLETE": "**INCOMPLETE** — nothing failed, but %d condition(s) "
                      "were never checked, so there is no pass either."
                      % len(unknown),
        "PASS": "**PASS** — every stop condition was checked and met.",
    }[verdict]
    L += ["## Verdict: **%s**" % verdict, "", headline, "",
          "| | conditions |", "|---|---|",
          "| failed | **%d** |" % len(fails),
          "| not checked | %d |" % len(unknown),
          "| passed | %d |" % len(passes), ""]

    L += ["_The inputs this rests on — loads, material, machine, and what was "
          "fixed before the optimiser ran — are in `assumptions.md`. This page "
          "reports only what was measured._", ""]

    # ---- failures first, always. A report whose FAILs are on page three
    # gets skimmed to page two.
    if fails:
        L += sec("What failed")
        L += ["These are errors, not warnings (spec 18). Each one names the "
              "fix.", ""]
        for _k, t, d in fails:
            L += ["- **%s**  " % t, "  %s" % d]
        L.append("")
    if unknown:
        L += sec("What was not checked")
        L += ["%d condition(s) have no answer in this run. Not checked is not "
              "the same as fine: an analysis that was never run is not "
              "evidence the part is sound, so these count against it and not "
              "for it." % len(unknown), ""]
        for _k, t, d in unknown:
            L += ["- %s — _%s_" % (t, d)]
        L.append("")
    if passes:
        L += sec("What passed")
        # `%d` had no operand: the list built here is joined later, so a
        # format spec in a literal element never gets substituted and the
        # reader sees "%d condition(s) passed".
        L += ["<details><summary>%d condition(s) passed</summary>" % len(passes),
              ""]
        for _k, t, d in passes:
            L.append("- %s (%s)" % (t, d))
        L += ["", "</details>", ""]

    # ---- which file to print. The question a reader most often arrives with.
    ex = data.get("extract") or {}
    if ex.get("candidates"):
        L += sec("Which shape this is")
        L += ["The optimiser produces a density field, not a shape. A "
              "threshold turns it into one, and different thresholds give "
              "different parts.", "",
              "| threshold | volume (mm³) | safety factor (p99.5) | "
              "thinnest member (mm) |",
              "|---|---|---|---|"]
        for c in ex["candidates"]:
            t = (c.get("thickness") or {}).get("optimised_region") or {}
            mark = " — grid-limited" if t.get("grid_limited") else ""
            L.append("| %.2f%s | %.0f | %s | %s%s |"
                     % (c["level"],
                        " **← chosen**" if c["level"] == ex.get("chosen_level")
                        else "",
                        c["volumes"]["final_mm3"],
                        _n(c.get("min_safety_factor_p99_5")),
                        _n(t.get("min_mm")), mark))
        L.append("")
        if ex.get("chosen_reason"):
            L += ["**Why this one:** %s" % ex["chosen_reason"], ""]
        L += _footnote(
            "A thinnest member marked *grid-limited* is exactly two voxels "
            "across, which is the finest the grid can represent. That number "
            "describes the mesh, not the structure.")

    # ---- how the optimiser was run
    o = data.get("optimisation") or {}
    if o:
        s = o.get("settings") or {}
        m = _material_model(data) or {}
        L += sec("How it was solved")
        L += ["| | |", "|---|---|",
              "| iterations | %s of %s requested |"
              % (s.get("iterations_run"), s.get("iterations_requested")),
              "| converged | **%s** |" % o.get("converged"),
              "| why it stopped | `%s` |" % o.get("termination_reason"),
              "| volume fraction (of free cells) | %s |"
              % s.get("volfrac_of_free_cells"),
              "| filter radius | %s mm |" % s.get("filter_mm"),
              "| build axis | %s |" % s.get("build_axis"),
              "| material model | `%s` |" % (m.get("material_model") or "?"),
              "| optimiser | %s |" % (s.get("optimizer") or "oc"),
              "| stress constraint | %s |"
              % (("SF %.2f%s x max-stress index <= 1 per case (p-norm %g, "
                  "rho^%g relaxation) -- **%s** on the final field"
                  % (s["stress"].get("safety_factor", 0),
                     _abnormal_bar(s["stress"]),
                     s["stress"].get("p_norm", 0), s["stress"].get("relax_q", 0),
                     "held" if (o.get("stress_constraint") or {}).get("satisfied")
                     else "VIOLATED"))
                 if s.get("stress") else "none (volume and stiffness only)"),
              "| wall-clock | %s s |" % _n(o.get("seconds"), "%.0f"), ""]
        if m.get("measured_for_specific_printer") is False:
            L += _footnote(
                "The material card is provisional — none of its values were "
                "measured on this printer. Use these results to compare "
                "options (build direction, layout, load case), not as an "
                "absolute strength.")

    # ---- results
    fea = data.get("fea") or []
    if fea:
        L += sec("Structural results")
        need = data.get("required_safety_factor")
        L += ["| load case | class | required | safety factor (p99.5) | "
              "safety factor (worst element) | max displacement (mm) |",
              "|---|---|---|---|---|---|"]
        for s in fea:
            case_need = s.get("required_safety_factor", need)
            L.append("| %s | %s | %s | %.2f | %.2f | %.3f |"
                     % (s.get("case", "?"), s.get("load_class", "normal"),
                        "%.2f" % case_need if case_need else "?",
                        s.get("safety_factor_p99_5", 0),
                        s.get("min_safety_factor", 0),
                        s.get("max_displacement_mm", 0)))
        L.append("")
        if need:
            L += ["Required: safety factor ≥ %.2f on every normal case; "
                  "abnormal cases (drop, one foot caught) are judged at "
                  "their own `required` column (spec 15.1)." % need, ""]
        L += _footnote(
            "The p99.5 value decides pass/fail; the worst element decides "
            "whether a person has to go and look, because a single "
            "stair-stepped voxel at a load point is an artefact of the grid "
            "rather than of the part.")

    # ---- can it be made
    bv = ((data.get("setup") or {}).get("info") or {}).get("build_volume") or {}
    dom = bv.get("domain") or {}
    pc = data.get("printcheck") or {}
    if dom.get("size_mm") or pc:
        L += sec("Can it be made")
    if dom.get("size_mm"):
        p = bv.get("printer") or {}
        L += ["| | |", "|---|---|",
              "| machine | %s |" % p.get("name"),
              "| usable build volume | %s mm |"
              % " × ".join("%g" % v for v in p.get("usable_mm", [])),
              "| design space | %s mm |"
              % " × ".join("%g" % v for v in dom["size_mm"]),
              "| fits with %s up | **%s** |"
              % (bv.get("build_axis"),
                 "yes" if dom.get("fits_as_declared") else "NO"), ""]
    if pc:
        L += ["| | |", "|---|---|",
              "| build axis | %s |" % pc.get("build_axis"),
              "| flat overhang area | %s mm² |"
              % pc.get("flat_overhang_area_mm2"),
              "| support volume | %s mm³ |" % pc.get("support_volume_mm3"),
              "| support on mating faces | %s |"
              % pc.get("support_on_critical_faces", {}).get("note"),
              "| enclosed support | %s |"
              % pc.get("enclosed_support", {}).get("note"), ""]
        if pc.get("note"):
            L += _footnote(pc["note"])

    # ---- what the slicer says. Its own section, never a condition in the
    # verdict above: spec 19.5 forbids letting a slicer that did not run --
    # or that failed -- move the structural answer in either direction. A
    # machine with no slicer must not turn a sound part into a doubtful one,
    # and a slicer that crashed must not be filed as a pass.
    L += sec("What the slicer says")
    L += _slicer_rows(data.get("slice"))

    # ---- how much of this was an answer at all
    fr = ((data.get("setup") or {}).get("info") or {}).get("design_freedom")
    if fr:
        L += sec("How much of the shape was optimised")
        L += ["| | |", "|---|---|",
              "| share of the domain free to decide | %s |"
              % ("%.0f %%" % (100 * fr["free_fraction_of_domain"])
                 if fr.get("free_fraction_of_domain") is not None else "?"),
              "| share of the expected part that is preserve | %s |"
              % ("%.0f %%" % (100 * fr["preserve_fraction_of_expected_part"])
                 if fr.get("preserve_fraction_of_expected_part") is not None
                 else "?"), ""]
        if fr.get("fixed_points"):
            L += ["Positions fixed by the CAD model, not chosen by the "
                  "optimiser:", "",
                  "| | region | position (mm) |", "|---|---|---|"]
            for f in fr["fixed_points"]:
                L.append("| %s | `%s` | %s |"
                         % (f["kind"], f["region"], f["centroid_mm"]))
            L.append("")
        L += _footnote(
            "Loads and supports may only act on preserve, so where the part "
            "is held is an input to this study. The optimiser decided how to "
            "reach those points, not where they are.")

    # ---- second opinion
    cc = data.get("ccx") or {}
    L += sec("Independent re-analysis (CalculiX)")
    if cc.get("runs"):
        L += ["| mesh (mm) | nodes | safety factor (p99.5) | "
              "max displacement (mm) |", "|---|---|---|---|"]
        for r in cc["runs"]:
            L.append("| %.2f | %s | %s | %s |"
                     % (r.get("mesh_target_mm", 0), r.get("nodes"),
                        _n(r.get("safety_factor_p99_5")),
                        _n(r.get("max_disp_mm"), "%.4f")))
        v = cc.get("vs_voxel_fea")
        if v:
            L += ["", "Voxel FEA vs CalculiX: **%.1f %%** difference. %s"
                  % (100 * v["relative_difference"], v["note"]), ""]
    else:
        L += ["**Not run.** The voxel solver has not been checked against a "
              "second solver on a second mesh, so its results carry only its "
              "own assumptions.", ""]

    # ---- provenance
    man = data.get("manifest") or {}
    L += sec("Reproducibility")
    if man:
        # `manifest.py` nests these under source/environment/run. Reading them
        # as flat keys silently produced "not recorded / None / None" on every
        # report ever written, which reads as "reproducibility was not
        # captured" when in fact it was captured and not looked at.
        src = man.get("source") or {}
        env = man.get("environment") or {}
        r = man.get("run") or {}
        pkgs = env.get("packages") or {}
        L += ["| | |", "|---|---|",
              "| commit | `%s`%s |"
              % (src.get("git_commit") or "not recorded",
                 " **(working tree dirty)**" if src.get("git_dirty") else ""),
              "| tool version | %s |" % (man.get("tool") or {}).get("version"),
              "| python | %s on %s |" % (env.get("python"), env.get("platform")),
              "| numpy / scipy | %s / %s |"
              % (pkgs.get("numpy"), pkgs.get("scipy")),
              "| run at | %s (%s s) |"
              % (r.get("started_utc"), _n(r.get("seconds"), "%.0f")), ""]
        if src.get("git_dirty"):
            L += _footnote(
                "The working tree had uncommitted changes when this ran, so "
                "the commit above does not fully describe the code that "
                "produced these numbers.")
    tools = survey()
    L += ["**External tools on this machine:**", ""]
    for k in sorted(tools):
        v = tools[k]
        L.append("- %s: %s" % (k, ("`%s`" % v["version"]) if v["available"]
                               else "**not installed**"))
    L += _footnote(
        "A stage that needs a tool this machine does not have was not run. "
        "Its conditions are reported as not checked above, never as passing.")
    return "\n".join(L)


def _slicer_rows(sl):
    """The slicer section: the real numbers, or a stated UNKNOWN.

    Absent is rendered, not skipped. Print time and filament are not
    estimated anywhere else in this toolkit -- they come from a slicer or they
    do not exist -- so a report with no slice has to say that rather than
    leave a reader to assume it was fine.
    """
    if not sl:
        return ["**UNKNOWN** -- no `slice.json` in this run. Print time, real "
                "filament use and real support quantity come from a slicer "
                "and from nothing else; they are not estimated here.", "",
                "    topopt slice PART.stl --out DIR", ""]
    L = ["| | |", "|---|---|",
         "| slicer | %s |" % ((sl.get("tool") or {}).get("version") or "?"),
         "| profiles | %s / %s / %s |"
         % ((sl.get("presets") or {}).get("machine"),
            (sl.get("presets") or {}).get("process"),
            (sl.get("presets") or {}).get("filament")),
         "| supports | %s |" % (sl.get("support_type") or sl.get("supports")),
         "| orientation | %s |" % sl.get("orient")]
    if sl.get("error"):
        L += ["| result | **the slicer did not finish** |",
              "| error | %s |" % sl["error"], ""]
        L += _footnote(
            "This is a statement about the slicer, not about the part. The "
            "structural verdict above is whatever the analysis stages made "
            "it, and a failed slice neither confirms nor withdraws it "
            "(spec 19.5).")
        return L
    L += ["| print time | %s (%s s) |"
          % (sl.get("print_time_text"), _n(sl.get("print_time_s"), "%.0f")),
          "| filament | %s g / %s mm3 |"
          % (_n(sl.get("filament_g")), _n(sl.get("filament_mm3"), "%.0f")),
          "| of which support | %s |"
          % ("%s g / %s mm3 (%.1f %% of filament)"
             % (_n(sl.get("support_filament_g")),
                _n(sl.get("support_mm3"), "%.0f"),
                100 * (sl.get("support_fraction") or 0.0))
             if sl.get("support_filament_g") is not None
             else "not reported by the slicer"),
          "| layers | %s (object %s) at %s mm |"
          % (sl.get("layers"), sl.get("object_layers"), sl.get("layer_mm")),
          "| fits the plate as sliced | **%s** |"
          % ("yes" if sl.get("bed_fits") else "NO"),
          "| spec 13.3 support conditions | **UNKNOWN** (%d) |"
          % len([c for c in sl.get("checks", [])
                 if c.get("state") == "UNKNOWN"]), ""]
    for w in (sl.get("warnings") or [])[:6]:
        L += ["- slicer %s" % w]
    if sl.get("warnings"):
        L += [""]
    L += _footnote(
        "Support here is extruded 1.75 mm filament -- the material thrown "
        "away -- and is not the same quantity as `printcheck.json`'s "
        "`support_volume_mm3`, which is part-space volume on the voxel grid. "
        "All five spec 13.3 conditions ask *where* the support is, and the "
        "slicer only ever says how much; `printcheck` answers the mating-face "
        "and sealed-cavity ones.")
    return L


def slicer_summary(data):
    """The `slicer` block of `report.json`: the numbers plus the honest
    UNKNOWN, kept out of `pass`/`fail`/`unknown` so no existing key moves."""
    sl = data.get("slice")
    if not sl:
        return {"state": "UNKNOWN",
                "detail": "no slice.json in this run -- print time, filament "
                          "and support were not measured",
                "spec_13_3": "UNKNOWN"}
    return {"state": "FAIL" if sl.get("error") else
                     ("PASS" if sl.get("pass") else "FAIL"),
            "detail": sl.get("error") or sl.get("note"),
            "tool": (sl.get("tool") or {}).get("version"),
            "print_time_s": sl.get("print_time_s"),
            "filament_g": sl.get("filament_g"),
            "filament_mm3": sl.get("filament_mm3"),
            "support_filament_g": sl.get("support_filament_g"),
            "support_mm3": sl.get("support_mm3"),
            "layers": sl.get("layers"),
            "layer_mm": sl.get("layer_mm"),
            "bed_fits": sl.get("bed_fits"),
            "spec_13_3": "UNKNOWN",
            "spec_13_3_detail": "the slicer reports how much support, never "
                                "where it lands; see printcheck",
            "file": data.get("slice_file")}


def _n(v, fmt="%.2f"):
    return "-" if v is None else (fmt % v)


# ------------------------------------------------------------------ CSV
# `--csv`: the same numbers as the "Structural results" and verdict sections
# of `report.md`, as two flat tables a spreadsheet can pivot -- the markdown
# is for a person reading top to bottom, this is for someone stacking results
# across parts or across runs of the same part.

CASE_CSV_FIELDS = [
    "case", "load_class", "required_safety_factor",
    "voxel_sf_p99_5", "voxel_sf_worst", "voxel_max_disp_mm",
    "ccx_sf_p99_5", "ccx_sf_worst", "ccx_max_disp_mm", "ccx_buckling_min",
    "disp_ratio_ccx_over_voxel", "pass",
]

CHECK_CSV_FIELDS = ["name", "pass", "detail"]


def _case_key(name):
    """First token of a case name, e.g. 'LC3x lateral 30 N' -> 'LC3x'.

    Same rule `ccx.compare_cases` uses to pair a voxel-FEA case with its
    CalculiX counterpart -- the two paths grow different long names for the
    same case, and pairing on the first token is what survives that."""
    parts = str(name or "").split()
    return parts[0] if parts else ""


def cases_csv_rows(data):
    """-> [{...}], one row per load case, columns per `CASE_CSV_FIELDS`.

    Voxel FEA (`data['fea']`) and the finest CalculiX run
    (`data['ccx']['runs'][-1]`) are read independently and paired by
    `_case_key`, so a case present on only one side still gets a row with the
    other side blank -- exactly the "which source ran" question spec 14.4
    exists to answer, not hidden by only ever emitting matched pairs.
    """
    voxel = data.get("fea") or []
    ccx_runs = (data.get("ccx") or {}).get("runs") or []
    ccx_cases = (ccx_runs[-1].get("cases") or []) if ccx_runs else []
    top_required = data.get("required_safety_factor")

    vmap = {_case_key(c.get("case")): c for c in voxel}
    cmap = {_case_key(c.get("case")): c for c in ccx_cases}

    order, seen = [], set()
    for c in list(voxel) + list(ccx_cases):
        k = _case_key(c.get("case"))
        if k not in seen:
            seen.add(k)
            order.append(k)

    rows = []
    for k in order:
        v, c = vmap.get(k) or {}, cmap.get(k) or {}
        required = v.get("required_safety_factor", c.get(
            "required_safety_factor", top_required))
        voxel_disp = v.get("max_displacement_mm")
        ccx_disp = c.get("max_disp_mm")
        ratio = (ccx_disp / voxel_disp
                 if voxel_disp not in (None, 0) and ccx_disp is not None
                 else None)
        if ((data.get("extract") or {}).get("outputs") or {}).get("finish_stl"):
            ratio = None  # finished-vs-coarse is not a solver agreement check
        # Overall pass is the AND of whichever sources actually ran; a case
        # checked by only one solver is judged on that one, not marked
        # unknown -- the same "not checked is not fine" rule cuts the other
        # way once *something* has checked it.
        seen_pass = [p for p in (v.get("pass"), c.get("pass")) if p is not None]
        rows.append({
            "case": v.get("case") or c.get("case") or k,
            "load_class": v.get("load_class") or c.get("load_class"),
            "required_safety_factor": required,
            "voxel_sf_p99_5": v.get("safety_factor_p99_5"),
            "voxel_sf_worst": v.get("min_safety_factor"),
            "voxel_max_disp_mm": voxel_disp,
            "ccx_sf_p99_5": c.get("safety_factor_p99_5"),
            "ccx_sf_worst": c.get("worst_element_safety_factor"),
            "ccx_max_disp_mm": ccx_disp,
            "ccx_buckling_min": c.get("buckling_factor_min"),
            "disp_ratio_ccx_over_voxel": ratio,
            "pass": all(seen_pass) if seen_pass else None,
        })
    return rows


def checks_csv_rows(fails, passes, unknown):
    """-> [{...}], one row per spec-18 stop condition, in `CONDITIONS` order --
    the same rows `report.md`'s "What failed/not checked/passed" sections
    list, just flattened to one table instead of three prose lists."""
    verdict = {}
    for k, t, d in fails:
        verdict[k] = (False, t, d)
    for k, t, d in passes:
        verdict[k] = (True, t, d)
    for k, t, d in unknown:
        verdict[k] = (None, t, d)          # not checked -- blank, not "fine"
    rows = []
    for key, text, _fn in CONDITIONS:
        ok, label, detail = verdict.get(key, (None, text, "not checked in this run"))
        rows.append({"name": label, "pass": ok, "detail": detail})
    return rows


def _write_csv(path, fieldnames, rows):
    # `newline=""` + an explicit `\n` terminator: the csv module defaults to
    # `\r\n`, and the task asks for UTF-8 with `\n` line endings specifically.
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_csv(outdir, data, fails, passes, unknown):
    """`report_cases.csv` and `report_checks.csv` next to `report.json`.
    -> (cases_path, checks_path)."""
    cp = os.path.join(outdir, "report_cases.csv")
    kp = os.path.join(outdir, "report_checks.csv")
    _write_csv(cp, CASE_CSV_FIELDS, cases_csv_rows(data))
    _write_csv(kp, CHECK_CSV_FIELDS, checks_csv_rows(fails, passes, unknown))
    return cp, kp


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt report", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir", help="a directory produced by opt/extract/ccx")
    ap.add_argument("--study", help="the study, so build_axis and the required "
                                    "safety factor can be checked directly")
    ap.add_argument("--title", default=None)
    ap.add_argument("--csv", action="store_true",
                    help="also write report_cases.csv (one row per load "
                         "case) and report_checks.csv (one row per spec-18 "
                         "stop condition)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if not os.path.isdir(a.outdir):
        raise FileNotFoundError("not a directory: %s" % a.outdir)
    data = collect(a.outdir, a.study)
    if not data:
        raise FileNotFoundError(
            "no results in %s -- nothing to report on. Run `topopt opt` "
            "first." % a.outdir)
    fails, passes, unknown = evaluate(data)
    # Name the report after the part, falling back to the study file. The
    # output directory is usually called "run" or "out", which identifies the
    # folder rather than the thing being reported on.
    stem = (data.get("study_file") or "").rsplit(".", 1)[0]
    title = a.title or ("topopt report -- %s"
                        % (stem or os.path.basename(os.path.abspath(a.outdir))))
    md = render(data, title)

    with open(os.path.join(a.outdir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    blob = {"schema": "topopt/report/1",
            "verdict": "FAIL" if fails else ("INCOMPLETE" if unknown else "PASS"),
            "fail": [{"key": k, "condition": t, "detail": d}
                     for k, t, d in fails],
            "unknown": [{"key": k, "condition": t, "detail": d}
                        for k, t, d in unknown],
            "pass": [{"key": k, "condition": t, "detail": d}
                     for k, t, d in passes],
            # A block of its own, deliberately outside fail/unknown/pass: a
            # slicer row must never change the structural verdict (spec 19.5).
            "slicer": slicer_summary(data),
            "external_tools": survey()}
    with open(os.path.join(a.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2)

    if a.csv:
        cp, kp = write_csv(a.outdir, data, fails, passes, unknown)

    if not a.quiet:
        print("wrote %s" % os.path.join(a.outdir, "report.md"))
        print("verdict: %s  (%d fail, %d unknown, %d pass)"
              % (blob["verdict"], len(fails), len(unknown), len(passes)))
        for _k, t, d in fails:
            print("  FAIL     %s -- %s" % (t, d))
        for _k, t, d in unknown:
            print("  UNKNOWN  %s" % t)
        if a.csv:
            print("wrote %s" % cp)
            print("wrote %s" % kp)
    # INCOMPLETE is not a pass: an unrun check is not evidence of soundness.
    return 0 if not fails and not unknown else 1


if __name__ == "__main__":
    sys.exit(main())

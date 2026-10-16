"""`04_print/` — the folder for the person who is going to print the thing.

A run leaves twenty-odd files in one directory: three thresholds' worth of
STLs, their smoothed variants, STEPs, density fields, setup previews, FEA
figures. Exactly one of those STLs is the one that was chosen, and in a flat
directory it sits between `_final.stl` (a *different* threshold) and
`_density_raw.npy`, indistinguishable from the working files around it. That is
how the wrong file gets printed.

So the chosen shape is copied out, renamed after the part rather than after the
threshold that produced it, and given a `PRINT.md` that says the three things
someone at the printer needs:

1. which file, and why that one,
2. what is still wrong with it,
3. what has to be rebuilt in CAD before it is a part at all.

Point 3 is not a formality. The raw output is quantised to the voxel grid, so
every mating dimension in it is wrong by up to a voxel -- a 3.50 mm slot came
out at 3.21 mm on a real run and the assembly would not go together. Nothing in
`04_print/` is a finished part unless the study says the interfaces were
resolved, and the page says so at the top rather than in a footnote.
"""

import glob
import json
import os
import shutil


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                             # noqa: BLE001
        return {}


def _find(outdir, pattern):
    hits = sorted(glob.glob(os.path.join(outdir, pattern)))
    return hits[0] if hits else None


def gather(outdir):
    """What the optimisation directory has to offer. -> dict."""
    ex = _read(_find(outdir, "*_extract.json") or "")
    rep = _read(os.path.join(outdir, "report.json"))
    pc = _read(os.path.join(outdir, "printcheck.json"))
    run = _read(os.path.join(outdir, "run_summary.json"))
    return {"extract": ex, "report": rep, "printcheck": pc, "run": run}


def _chosen_files(outdir, ex):
    """The STL/STEP for the threshold that was actually chosen.

    Paths inside `_extract.json` are as they were written, which may be
    relative to a different working directory or carry Windows separators from
    another machine. The basename is the reliable part, so it is re-resolved
    against the directory in hand.
    """
    out = {}
    for key in ("stl", "smooth_stl", "step", "finish_stl"):
        p = (ex.get("outputs") or {}).get(key)
        if not p:
            continue
        cand = os.path.join(outdir, os.path.basename(p.replace("\\", "/")))
        if os.path.exists(cand):
            out[key] = cand
    return out


def _governing_case(outdir):
    """(1-based index, case name) of the load case with the lowest safety
    factor, or None.

    The FEA figures are numbered by load case, and copying `_fea_1.png`
    blindly published the *first* case -- which on the tall tripod is the
    tipping limit at a safety factor of 1277, the least demanding of the four.
    A picture captioned "stress" showing the case that governs nothing is
    worse than no picture.
    """
    import glob as _g
    skip = ("_extract.json", "report.json", "manifest.json", "_setup.json",
            "run_summary.json", "printcheck.json")
    for path in sorted(_g.glob(os.path.join(outdir, "*.json"))):
        if any(path.endswith(x) for x in skip):
            continue
        fea = _read(path).get("final_fea")
        if not isinstance(fea, list) or not fea:
            continue
        rows = [(c.get("safety_factor_p99_5"), i + 1, c.get("case", "?"))
                for i, c in enumerate(fea)
                if c.get("safety_factor_p99_5") is not None]
        if rows:
            _sf, idx, name = min(rows)
            return idx, name
    return None


def _outstanding(g):
    """Everything a printer operator should know is still wrong. -> [str]"""
    rows = []
    rep = g["report"]
    for f in rep.get("fail") or []:
        rows.append("**%s** — %s" % (f.get("condition", f.get("key", "?")),
                                     f.get("detail", "")))
    # An unchecked condition is not a clean bill either, and the person at the
    # printer is exactly who should not be told it is.
    for f in rep.get("unknown") or []:
        rows.append("_not checked:_ %s" % f.get("condition", f.get("key", "?")))
    return rows


def render(part, g, files, printfit=None, gov=None, notes=None):
    """PRINT.md.

    `notes` are things the publish step itself found -- a figure it
    could not draw, a file it removed -- which belong in the folder's
    own page and nowhere else.
    """
    ex, rep, pc = g["extract"], g["report"], g["printcheck"]
    verdict = rep.get("verdict")
    fin = ex.get("finish") or {}
    slice_key = "finish_stl" if "finish_stl" in files else "stl"
    L = ["# Print this: `%s`" % os.path.basename(files.get(slice_key, "?")), ""]

    # ---- the warning that has to come before anything else
    if fin:
        # A finish stage ran, so the sentence above is no longer true of the
        # whole file -- and saying it anyway would teach the reader to ignore
        # it. Say precisely which half is exact.
        # Two tolerances, and quoting the wrong one is how a reader either
        # over-trusts the file or re-cuts something that was already right.
        # `cad_tolerance_mm` is present only when the smoothing ran, because
        # the sub-cell occupancy field goes with it.
        cad_tol = (fin.get("smooth") or {}).get("cad_tolerance_mm")
        L += ["> ## Half of this is a finished part. Know which half.",
              ">",
              "> The **interface** was re-cut from the source CAD on a %.2f mm "
              "grid, so a feature width is good to within **%.2f mm** "
              "instead of %s mm. Not exact — quote that tolerance, do not "
              "quote the drawing. The **optimised body** is still the analysis "
              "grid: resampling it added no detail it never had, and no mating "
              "dimension may be taken off it."
              % (fin.get("voxel_mm", 0.0),
                 fin.get("feature_tolerance_mm", fin.get("voxel_mm", 0.0)),
                 ex.get("voxel_mm", "?")),
              ">"]
        if cad_tol:
            L += ["> A **CAD face** in this mesh does better than that: it is "
                  "placed from the body's sub-cell occupancy rather than from "
                  "which cells it fills, so it lands within **%.2f mm** of "
                  "where CAD put it. That bound is for the faces of the added "
                  "and cut CAD bodies in `%s` only — the %.2f mm above still "
                  "applies to the optimised body and to the voxel file."
                  % (cad_tol, os.path.basename(files.get(slice_key, "?")),
                     fin.get("feature_tolerance_mm", fin.get("voxel_mm", 0.0))),
                  ">"]
        L += [
              "> The analysis did **not** see this shape. The FEA ran on the "
              "%s mm interface, so stresses in and around it are resolved no "
              "better than that grid allows." % (ex.get("voxel_mm", "?")), ""]
    else:
        L += ["> ## This is optimiser output, not a finished part.",
              ">",
              "> The shape is quantised to the %s mm voxel grid it was solved "
              "on, so **every mating dimension in it is wrong by up to one "
              "voxel**. On a real run a 3.50 mm slot came out at 3.21 mm and "
              "the assembly would not go together. Bolt holes, slots, bearing "
              "seats and mating faces must be rebuilt in CAD from the source "
              "model, and the result checked with `topopt compare`."
              % (ex.get("voxel_mm", "?")), ""]

    if verdict and verdict != "PASS":
        L += ["> ## The analysis verdict is %s." % verdict,
              ">",
              "> Printing this is fine for looking at, fitting, and checking "
              "the layout. It is not signed off to carry load. See section 3.",
              ""]

    # ---- 1. the files
    L += ["## 1. Files", "", "| file | what it is |", "|---|---|"]
    names = {
        "finish_stl": ("**the mesh to slice** — optimiser body%s with the "
                       "interface re-cut from CAD on a %.2f mm grid"
                       % (" (smoothed, sigma %.2f mm, volume held)"
                          % fin["smooth"]["sigma_mm"] if fin.get("smooth")
                          else "", fin.get("voxel_mm", 0.0))),
        "stl": ("what the optimiser produced, on the analysis grid"
                if fin else "**the mesh to slice**"),
        "preview_print.png": "**what the mesh above actually looks like** — "
                             "three shaded views of the surface you are "
                             "about to slice, flat-shaded so faceting shows",
        "smooth_stl": "the same shape, Taubin-smoothed — nicer surface, "
                      "slightly different dimensions",
        "step": "solid model for CAD, if you are rebuilding the "
                "interfaces (you are)",
        "preview_shape.png": "what the optimiser produced, in section",
        "preview_design_space.png": "what it was allowed to fill — compare "
                                    "against the shape",
        "preview_orientation.png": "how it sits on the plate, and what "
                                   "overhangs",
        "preview_stress.png": "safety factor on the **governing** load case%s"
                              % (" — %s" % gov[1].split()[0] if gov else ""),
    }
    for key, path in files.items():
        if key == "print_md":
            continue
        L.append("| `%s` | %s |" % (os.path.basename(path), names.get(key, "")))
    L.append("")
    for note in (notes or []):
        L += ["> %s" % note, ""]
    if ex.get("chosen_level") is not None:
        L += ["Threshold **%.2f** of the density field."
              % ex["chosen_level"], ""]
        if ex.get("chosen_reason"):
            L += ["_Why this one: %s_" % ex["chosen_reason"], ""]

    # ---- 2. how to print it
    L += ["## 2. How to print it", "", "| | |", "|---|---|"]
    axes = "XYZ"
    ba = pc.get("build_axis")
    if isinstance(ba, int):
        ba = axes[ba]
    L.append("| orientation the analysis assumed | model **%s** axis up |"
             % (ba or "?"))
    if printfit:
        p = printfit
        L.append("| fits the build volume | %s |"
                 % ("yes" if p.get("fits") else "**NO — see section 3**"))
        if p.get("size_mm"):
            L.append("| size as modelled | %s mm |"
                     % " × ".join("%.1f" % v for v in p["size_mm"]))
        if p.get("best_orientation"):
            L.append("| lowest-support orientation | %s |"
                     % p["best_orientation"])
    if pc.get("support_volume_mm3") is not None:
        L.append("| support volume in this orientation | %.0f mm³ |"
                 % pc["support_volume_mm3"])
    L.append("")
    L += ["**Do not reorient it to save support.** The material is anisotropic "
          "and the analysis was run with the axis above pointing up; laying "
          "the part on its side moves the layer planes and the safety factors "
          "no longer apply to it. If another orientation is wanted, change "
          "`build_axis` in the study and re-run.", ""]

    for key, label in (("support_on_critical_faces", "Support on mating faces"),
                       ("enclosed_support", "Support inside a sealed cavity")):
        c = pc.get(key)
        if c and not c.get("pass"):
            L += ["**%s.** %s" % (label, c.get("note", "")), ""]

    # ---- 3. what is still wrong
    out = _outstanding(g)
    L += ["## 3. What is still wrong with it", ""]
    if out:
        L += ["These failed the stop conditions (spec 18). They are listed in "
              "full, with the fix, in `../03_optimization/report.md`.", ""]
        for row in out:
            L.append("- %s" % row)
        L.append("")
    elif verdict == "PASS":
        L += ["Nothing: every stop condition was checked and met. The CAD "
              "rebuild in the box at the top of this page still applies.", ""]
    else:
        L += ["No report was found next to the results, so nothing here has "
              "been judged. That is not the same as nothing being wrong.", ""]
    L += ["_This list covers the stop conditions the report checks. **How much "
          "support the part needs is not one of them** -- that is judged by "
          "`topopt print <stl>`, which applies a 15 % overhang-area limit and "
          "is worth running before you slice._", ""]

    L += ["## 4. Where the rest of it is", "",
          "| | |", "|---|---|",
          "| what was assumed (loads, material, machine, what was fixed) | "
          "`../01_assumptions/assumptions.md` |",
          "| the design space and its validation | `../02_setup/` |",
          "| the full analysis and every number | "
          "`../03_optimization/report.md` |", ""]
    return "\n".join(L) + "\n"


def publish(outdir, dest, part=None, quiet=False, printfit=None,
            printer_key="X1C"):
    """Copy the chosen shape and its previews into `dest`, and write PRINT.md.

    -> dict of what was written. Missing inputs are reported, not raised: a run
    that failed still deserves whatever it did produce to be findable.
    """
    os.makedirs(dest, exist_ok=True)
    g = gather(outdir)
    part = part or (os.path.splitext(os.path.basename(
        g["extract"].get("study", "") or ""))[0] or "part")
    part = part[:-6] if part.endswith("_study") else part

    files = _chosen_files(outdir, g["extract"])
    written = {}
    notes = []
    # `_raw` for the analysis-grid mesh when a finish stage ran: the file named
    # after the part is the one to slice, and that is no longer the raw result.
    fin = bool((g["extract"].get("finish")))
    for key, ext in (("finish_stl", ".stl"),
                     ("stl", "_raw.stl" if fin else ".stl"),
                     ("smooth_stl", "_smooth.stl"),
                     ("step", ".step")):
        src = files.get(key)
        if not src:
            continue
        # `*_smooth.stl` is a Gaussian over the whole coarse density field,
        # preserve included, so it rounds the CAD interface into a blob -- the
        # tall tripod's camera fingers merge and its nut pocket closes. That is
        # harmless as a diagnostic of the optimised body (it is what `topopt
        # ccx` re-analyses) and actively dangerous in the folder marked "print
        # this", where someone reads "smooth" as "the nicer one". When a finish
        # stage ran, the finished mesh is smooth AND has the interface, so this
        # file has no reason to be here. It stays in 03_optimization.
        if key == "smooth_stl" and fin:
            continue
        dst = os.path.join(dest, part + ext)
        shutil.copy2(src, dst)
        written[key] = dst

    # Previews, named for what they show rather than for the stage that made
    # them. The setup preview is looked for in the sibling `02_setup/` as well,
    # because the model layout puts it there and a print folder with no picture
    # of the design space is missing the one image that shows what was asked
    # for versus what came back.
    here = [outdir, os.path.join(os.path.dirname(outdir.rstrip("/\\")),
                                 "02_setup")]
    gov = _governing_case(outdir)
    wanted = [("*_topo.png", "preview_shape.png"),
              ("*_setup.png", "preview_design_space.png")]
    if gov:
        wanted.append(("*_fea_%d.png" % gov[0], "preview_stress.png"))
    for pattern, name in wanted:
        for d in here:
            src = _find(d, pattern)
            if src:
                dst = os.path.join(dest, name)
                shutil.copy2(src, dst)
                written[name] = dst
                break

    # A picture of the surface that is going to be printed. Every other figure
    # in the folder draws a field on the analysis grid; none of them shows
    # whether the optimised material came out smooth or came out as a row of
    # beads, or whether the CAD interface is still crisp -- which is what a
    # person judges the moment they open the STL.
    ship = written.get("finish_stl") or written.get("stl")
    if ship:
        try:
            from topopt.geom import load_stl
            from topopt.meshout import write_mesh_preview
            png = write_mesh_preview(os.path.join(dest, "preview_print.png"),
                                     load_stl(ship),
                                     title=os.path.basename(ship))
            if png:
                written["preview_print.png"] = png
        except Exception:                                         # noqa: BLE001
            # A missing matplotlib must not cost anyone the STL.
            pass
        # A mesh over `write_mesh_preview`'s ceiling gets no picture -- fine,
        # a picture is not worth failing a run over. What is NOT fine is
        # leaving the LAST run's picture sitting in the folder: this is the
        # one figure the repo tells everyone to look at, and a stale one
        # shows a shape that is not the shape in the STL beside it.
        # MEASURED: the tall tripod's 1.0 mm finish is 226k triangles and
        # draws; its 0.5 mm finish is 905k, over the 400k ceiling, and the
        # 1.0 mm picture stayed behind pretending to be it.
        stale = os.path.join(dest, "preview_print.png")
        if "preview_print.png" not in written:
            gone = os.path.exists(stale)
            if gone:
                os.remove(stale)
            # Said on every run that has no picture, not only on the run that
            # deleted one: the second publish of the same shape would
            # otherwise go quiet and the folder would read as though the
            # figure had never been part of it.
            notes.append(
                "There is no `preview_print.png` for this run: the mesh is "
                "too big to draw (see `write_mesh_preview`)."
                + (" The previous run's picture has been REMOVED rather than "
                   "left here to be read as this one." if gone else "")
                + " Open the STL in the slicer instead.")

    # An orientation figure read off the finished mesh: how it sits on the
    # plate, what overhangs, whether it fits. This is the picture the person
    # printing it actually wants, and none of the analysis stages produce it.
    # Read off the FINISHED mesh when there is one. It used to read
    # `written["stl"]`, which after a finish stage is the analysis-grid body
    # under its `_raw` name -- so the figure answered "does the optimiser's
    # output fit the plate" while sitting next to the file you actually print.
    # MEASURED (tall tripod v7): it said 204 x 168 x 144 mm for a part that is
    # 201 x 166 x 196.5, because the camera mount is not in the raw mesh.
    orient = written.get("finish_stl") or written.get("stl")
    if orient:
        try:
            from topopt.cli.printability import analyse, write_png
            r = analyse(orient, printer_key, min_features=False)
            png = os.path.join(dest, "preview_orientation.png")
            write_png(png, orient, r)
            written["preview_orientation.png"] = png
        except Exception:                                         # noqa: BLE001
            # A missing matplotlib must not cost anyone the STL.
            pass

    md = os.path.join(dest, "PRINT.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(render(part, g, written, printfit, gov, notes))
    written["print_md"] = md

    if not quiet:
        print("04_print: %s" % ", ".join(sorted(os.path.basename(v)
                                                for v in written.values())))
        if not files.get("stl"):
            print("  WARNING: no chosen STL was found in %s -- there is "
                  "nothing to print yet" % outdir)
    return written

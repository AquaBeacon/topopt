"""Assumptions (前提条件): everything the result depends on, in one place.

A topology-optimisation result is a picture of its inputs. Change the load by
20 %, fix the feet somewhere else, print it the other way up, and a different
shape comes out -- all of them equally "optimal", none of them comparable. So
the shape on its own is not a result. The shape *plus the assumptions that
produced it* is.

This module writes those assumptions out as a standalone document, aimed at a
reader who was not in the room: a reviewer, a co-author, someone picking the
project up in a year. It answers, in order:

    what is this part for, what is it asked to survive, what is it made of,
    what machine has to make it, how much of the shape was decided in advance,
    what counts as a pass -- and what none of this establishes.

That last section is the point of the exercise. A document that lists only what
was checked reads as though everything was checked.

Works from a study file alone, and gets more specific when the setup has
actually been built (real volumes, real bounding boxes, real fixed points).
"""

import json
import os

import numpy as np

from . import materials, printers


G = 9.80665


# --------------------------------------------------------------- collection

def _clean(v):
    """Study files carry `_name` keys as inline commentary. Those are notes for
    a reader, not data, and they are pulled out deliberately below rather than
    dumped wholesale."""
    return {k: x for k, x in (v or {}).items() if not str(k).startswith("_")}


def _note(d, *keys):
    """The first `_note`-style annotation present, as prose."""
    for k in keys:
        val = (d or {}).get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _load_row(ld):
    """One load entry -> (description, force vector or None, magnitude N)."""
    region = ld.get("region", ld) if isinstance(ld, dict) else ld
    region = region if isinstance(region, str) else "<inline region>"
    if not isinstance(ld, dict):
        return region, None, None, "region only"
    if "force" in ld:
        vec = [float(v) for v in ld["force"]]
        return region, vec, float(np.linalg.norm(vec)), "force"
    if "pressure" in ld:
        # The area is not known without the grid, so the magnitude is left
        # blank rather than guessed. A guessed number here would be quoted.
        return region, None, None, "pressure %g MPa" % float(ld["pressure"])
    return region, None, None, "unrecognised"


def cases(study):
    """Every load case as a row: what it represents and what it applies."""
    raw = study.raw.get("cases") or []
    rows = []
    for i, case in enumerate(study.cases):
        src = raw[i] if i < len(raw) else {}
        loads, total = [], np.zeros(3)
        for ld in case.loads:
            region, vec, mag, kind = _load_row(ld)
            loads.append({"region": region, "force_N": vec,
                          "magnitude_N": None if mag is None else round(mag, 3),
                          "kind": kind})
            if vec is not None:
                total += np.asarray(vec, float)
        sup = []
        for s in case.supports:
            region = s.get("region", s) if isinstance(s, dict) else s
            sup.append({"region": region if isinstance(region, str)
                        else "<inline region>",
                        "fix": str((s or {}).get("fix", "xyz")).lower()
                        if isinstance(s, dict) else "xyz"})
        grav = _clean(case.gravity)
        rows.append({
            "name": case.name,
            "represents": _note(src, "_note", "_why", "_represents"),
            "supports": sup, "loads": loads,
            "gravity": grav or None,
            "resultant_N": [round(float(v), 3) for v in total],
            "resultant_magnitude_N": round(float(np.linalg.norm(total)), 3),
            "load_note": _note(src, "_load_note"),
        })
    return rows


def material_block(study, isotropic=False):
    """The card, its provenance, and what the numbers actually are."""
    mat, ortho, chosen = materials.material_model(
        study.material, isotropic=isotropic, infill=study.infill,
        walls=study.walls)
    out = {"name": mat.name, "model": chosen["material_model"],
           "chosen": chosen, "notes": mat.notes,
           "max_service_c": mat.max_service_c,
           "required_safety_factor":
               materials.required_safety_factor(mat)}
    if ortho is not None:
        out["card_id"] = ortho.card_id
        out["classification"] = ortho.classification
        out["measured_for_specific_printer"] = \
            ortho.measured_for_specific_printer
        out["source"] = ortho.source
        out["conditions"] = dict(ortho.conditions)
        out["stiffness_MPa"] = {"Ex": ortho.Ex, "Ey": ortho.Ey, "Ez": ortho.Ez,
                                "Gxy": ortho.Gxy, "Gyz": ortho.Gyz,
                                "Gzx": ortho.Gzx}
        out["poisson"] = {"nu_xy": ortho.nu_xy, "nu_yz": ortho.nu_yz,
                          "nu_zx": ortho.nu_zx}
        out["strength_MPa"] = {"Xt": ortho.Xt, "Xc": ortho.Xc, "Yt": ortho.Yt,
                               "Yc": ortho.Yc, "Zt": ortho.Zt, "Zc": ortho.Zc,
                               "Sxy": ortho.Sxy, "Syz": ortho.Syz,
                               "Szx": ortho.Szx}
    else:
        out["stiffness_MPa"] = {"E_xy": mat.E_xy, "E_z": mat.E_z}
        out["strength_MPa"] = {"sigma_xy": mat.sigma_xy,
                               "sigma_z": mat.sigma_z}
        out["poisson"] = {"nu": mat.nu}
    return out


def collect(study, setup=None, report=None, isotropic=False):
    """Everything a reader needs to interpret the result. -> dict."""
    raw = study.raw
    design = _clean(raw.get("design"))
    axes = "XYZ"

    src = getattr(study, "source_path", None)
    stem = os.path.splitext(os.path.basename(src))[0] if src else None
    data = {
        "schema": "topopt/assumptions/1",
        "part": raw.get("part") or raw.get("name") or stem
        or (study.material + " part"),
        "study_file": os.path.basename(src) if src else None,
        "what": _note(raw, "_what", "_purpose"),
        "why": _note(raw, "_why", "_rationale"),
        "caveat": _note(raw, "_not_a_part", "_caveat"),
        "units": "mm / N / MPa (lengths mm, forces N, stresses MPa)",
        "material": material_block(study, isotropic),
        "manufacturing": {
            "printer": study.printer.as_dict(),
            "printer_declared": study.printer_declared,
            "build_axis": axes[study.build_axis],
            "build_axis_declared": study.build_axis_declared,
            "build_axis_note": _note(raw, "_build_axis_note"),
            "raster_axis": (None if study.raster_axis is None
                            else axes[study.raster_axis]),
            "raster_axis_declared": study.raster_axis_declared,
            "walls": study.walls, "infill": study.infill,
        },
        "requirements": {
            "safety_factor": study.safety_factor,
            "safety_factor_note": _note(raw, "_safety_factor_note"),
            "max_deflection_mm": study.max_deflection_mm,
            "deflection_note": _note(raw, "_deflection_note"),
            "required_cases": raw.get("required_cases") or [],
        },
        "cases": cases(study),
        "design_settings": {
            "volfrac": design.get("volfrac"),
            "filter_mm": design.get("filter_mm"),
            "symmetry": design.get("symmetry") or [],
            "symmetry_note": _note(raw.get("design") or {}, "_symmetry_note"),
            "voxel_mm": raw.get("voxel_mm")
            or (raw.get("geometry") or {}).get("voxel_mm"),
            "voxel_note": _note(raw, "_voxel_note"),
            "target_elements": study.target_elements,
        },
        "geometry_sources": {
            k: v for k, v in _clean(raw.get("geometry")).items()
            if k in ("preserve", "obstacle", "design_space", "starting_shape")
        },
    }

    if setup is not None:
        from .designspace import bbox_mm
        h = setup.h
        dom = bbox_mm(setup.grid.solid, setup.origin, h)
        data["design_space"] = {
            "voxel_mm": h,
            "grid_shape": list(setup.grid.shape),
            "bbox_mm": None if dom is None
            else [round(float(v), 1) for v in dom[2]],
            "bbox_lo_mm": None if dom is None
            else [round(float(v), 1) for v in dom[0]],
        }
    if report is not None:
        info = report.get("info", {})
        data.setdefault("design_space", {}).update({
            "design_space_mm3": info.get("design_space_mm3"),
            "preserve_mm3": info.get("preserve_mm3"),
            "obstacle_mm3": info.get("obstacle_mm3"),
            "free_mm3": info.get("free_mm3"),
        })
        data["design_freedom"] = info.get("design_freedom")
        data["build_volume"] = info.get("build_volume")
        data["setup_valid"] = report.get("ok")
        data["setup_errors"] = report.get("errors") or []
        data["setup_warnings"] = report.get("warnings") or []
    return data


# ---------------------------------------------------------------- rendering

def _vec(v):
    return "(%g, %g, %g)" % tuple(v) if v else "-"


def _yn(b):
    return "yes" if b else "no"


def limitations(d):
    """What this study does not establish. Derived, never hand-maintained --
    a hand-maintained caveat list is the first thing to go stale, and a stale
    one is worse than none because it reads as current."""
    out = []
    m = d.get("material", {})
    if m.get("measured_for_specific_printer") is False:
        out.append(
            "**The material card is not measured.** Every stiffness and "
            "strength in section 3 is a literature/handbook value for %s, not "
            "a coupon test of this filament on this machine at these settings. "
            "Use the results to compare options (build direction, layout, "
            "load case), not as absolute strength." % m.get("name", "?"))
    if m.get("required_safety_factor") and d.get("requirements", {}).get(
            "safety_factor"):
        need = m["required_safety_factor"]
        have = d["requirements"]["safety_factor"]
        if have < need:
            out.append(
                "**The safety factor used (%.1f) is below the %.1f this "
                "material card calls for** while it is unmeasured. Results are "
                "reported against %.1f; read them with that in mind."
                % (have, need, have))
    man = d.get("manufacturing", {})
    if not man.get("printer_declared"):
        out.append(
            "**No printer was declared**, so the build volume was checked "
            "against a default (%s). If the real machine is smaller, the part "
            "may not fit." % man.get("printer", {}).get("name", "?"))
    if not man.get("build_axis_declared"):
        out.append(
            "**The build axis was not declared** and defaults to Z. The "
            "material axes follow it, so every across-layer strength in this "
            "document is assigned by an assumption rather than a decision.")
    if not man.get("raster_axis_declared"):
        out.append(
            "The raster (in-plane print) direction was not declared; the "
            "first non-build axis is assumed, which is what a default slicer "
            "does for the first layer but not what it does for every layer.")
    fr = d.get("design_freedom") or {}
    pf = fr.get("preserve_fraction_of_expected_part")
    if pf is not None and pf > 0.4:
        out.append(
            "**%.0f %% of the expected final part is preserve** -- drawn in "
            "CAD, not chosen by the optimiser." % (100 * pf))
    if fr.get("fixed_points"):
        sup = [f for f in fr["fixed_points"] if f["kind"] == "support"]
        if sup:
            out.append(
                "**Where the part is held is an input, not a result.** The %d "
                "support footprint(s) sit on preserve at positions the CAD "
                "model chose; the optimiser decided how to reach them, not "
                "where they go." % len(sup))
    if fr.get("ground_zones"):
        out.append(
            "**Stability was not analysed.** The %d ground-contact support(s) "
            "are glued to the plate, so the model cannot tip over and no load "
            "case in it rewards a wide stance. Whether this part stands up is "
            "a rigid-body question answered outside this analysis."
            % len(fr["ground_zones"]))
    if fr.get("free_min_thickness_voxels") is not None and \
            fr["free_min_thickness_voxels"] < 3:
        out.append(
            "The free region is only %.1f voxels thick at its thinnest, so in "
            "that region the topology follows the outline rather than the "
            "optimisation." % fr["free_min_thickness_voxels"])
    out.append(
        "Nothing here has been tested physically. No printed specimen, no "
        "static load test, no comparison of a measured stiffness against the "
        "predicted one.")
    return out


def render(d):
    """The assumptions as markdown, in the order a reader needs them."""
    L = []
    a = L.append
    a("# Assumptions (前提条件) — %s" % d.get("part", "part"))
    a("")
    a("_Everything on this page is an **input**. A topology-optimisation "
      "result is a picture of its assumptions: change any line below and a "
      "different shape is equally optimal. The shape is not the result — the "
      "shape together with this page is._")
    a("")
    if d.get("what"):
        a(d["what"])
        a("")
    if d.get("why"):
        a("**Why it is set up this way.** " + d["why"])
        a("")
    if d.get("caveat"):
        a("> %s" % d["caveat"])
        a("")
    a("**Units.** %s" % d["units"])
    a("")

    # --- 1. loading
    a("## 1. What it has to survive")
    a("")
    a("Every load case is a use case someone chose. They are not measured "
      "service loads unless a case says so.")
    a("")
    # Four narrow columns. The prose that explains each case goes *below* the
    # table, not inside a cell: a 350-character cell wraps into an unreadable
    # block in every markdown renderer there is, and makes the numbers -- the
    # reason the table exists -- impossible to scan down.
    a("| Case | Held at | Loaded at | Force (N) | \\|F\\| N |")
    a("|---|---|---|---|---|")
    for c in d.get("cases", []):
        held = ", ".join("%s (%s)" % (s["region"], s["fix"])
                         for s in c["supports"]) or "-"
        tag = c["name"].split()[0] if c["name"].split() else c["name"]
        for i, ld in enumerate(c["loads"]):
            a("| %s | %s | %s | %s | %s |" % (
                "**%s**" % tag if i == 0 else "",
                held if i == 0 else "",
                ld["region"], _vec(ld["force_N"]),
                "%.2f" % ld["magnitude_N"] if ld["magnitude_N"] is not None
                else ld["kind"]))
        if not c["loads"]:
            a("| **%s** | %s | - | - | - |" % (tag, held))
    a("")
    for c in d.get("cases", []):
        tag = c["name"].split()[0] if c["name"].split() else c["name"]
        rest = c["name"][len(tag):].strip()
        a("**%s**%s" % (tag, " — " + rest if rest else ""))
        a("")
        if c.get("represents"):
            a(c["represents"])
            a("")
        if c.get("load_note"):
            a("_%s_" % c["load_note"])
            a("")
    req = d.get("requirements", {})
    if req.get("required_cases"):
        a("Cases that must be present for the study to be considered complete: "
          "`%s`." % "`, `".join(req["required_cases"]))
        a("")

    # --- 2. pass/fail
    a("## 2. What counts as a pass")
    a("")
    a("| Requirement | Value |")
    a("|---|---|")
    a("| Safety factor (p99.5 of elements) | ≥ %s |" % req.get("safety_factor"))
    if req.get("max_deflection_mm"):
        a("| Maximum deflection | ≤ %s mm |" % req["max_deflection_mm"])
    mm = d.get("material", {})
    if mm.get("required_safety_factor"):
        a("| Safety factor this material card calls for | ≥ %s |"
          % mm["required_safety_factor"])
    a("")
    for k in ("safety_factor_note", "deflection_note"):
        if req.get(k):
            a("- %s" % req[k])
    a("")

    # --- 3. material
    a("## 3. What it is made of")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Material | %s |" % mm.get("name"))
    a("| Model | `%s` |" % mm.get("model"))
    if mm.get("card_id"):
        a("| Card | `%s` (%s) |" % (mm["card_id"], mm.get("classification")))
        a("| Measured on this printer | **%s** |"
          % _yn(mm.get("measured_for_specific_printer")))
        a("| Source | %s |" % mm.get("source"))
    a("| Walls / infill used for derating | %s / %s |"
      % (d.get("manufacturing", {}).get("walls"),
         d.get("manufacturing", {}).get("infill")))
    a("| Max service temperature | %s °C |" % mm.get("max_service_c"))
    a("")
    st = mm.get("stiffness_MPa") or {}
    sg = mm.get("strength_MPa") or {}
    if st:
        a("Stiffness (MPa): " + ", ".join("%s %g" % (k, v)
                                          for k, v in st.items()))
        a("")
    if sg:
        a("Strength (MPa): " + ", ".join("%s %g" % (k, v)
                                         for k, v in sg.items()))
        a("")
    if mm.get("notes"):
        a("> %s" % mm["notes"])
        a("")

    # --- 4. manufacturing
    man = d.get("manufacturing", {})
    p = man.get("printer", {})
    a("## 4. What has to make it")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Machine | %s |" % p.get("name"))
    a("| Usable build volume | %s mm |"
      % " × ".join("%g" % v for v in p.get("usable_mm", [])))
    if p.get("margin_mm"):
        a("| Margin held back | %g mm per side (envelope %s mm) |"
          % (p["margin_mm"], " × ".join("%g" % v
                                        for v in p.get("envelope_mm", []))))
    a("| Nozzle / smallest wall | %g mm / %g mm |"
      % (p.get("nozzle_mm", 0), p.get("min_feature_mm", 0)))
    a("| Printer chosen or assumed | %s |" % p.get("source"))
    a("| Build axis (up on the plate) | **%s**%s |"
      % (man.get("build_axis"),
         "" if man.get("build_axis_declared") else " _(assumed, not declared)_"))
    a("| Raster axis (in-plane) | %s%s |"
      % (man.get("raster_axis") or "first non-build axis",
         "" if man.get("raster_axis_declared") else " _(assumed)_"))
    a("")
    if man.get("build_axis_note"):
        a("- %s" % man["build_axis_note"])
        a("")

    bv = d.get("build_volume") or {}
    dom = bv.get("domain") or {}
    if dom.get("size_mm"):
        fits = dom.get("fits_as_declared")
        a("**Does it fit?** The design space measures %s mm; the machine takes "
          "%s mm. With %s up: **%s**."
          % (" × ".join("%g" % v for v in dom["size_mm"]),
             " × ".join("%g" % v for v in dom.get("envelope_mm", [])),
             man.get("build_axis"), "fits" if fits else "DOES NOT FIT"))
        a("")

    # --- 5. design space
    a("## 5. How much of the shape was decided in advance")
    a("")
    ds = d.get("ds") or d.get("design_space") or {}
    fr = d.get("design_freedom") or {}
    dset = d.get("design_settings", {})
    a("| | |")
    a("|---|---|")
    if ds.get("bbox_mm"):
        a("| Design-space bounding box | %s mm |"
          % " × ".join("%g" % v for v in ds["bbox_mm"]))
    a("| Voxel size | %s mm%s |"
      % (ds.get("voxel_mm") or dset.get("voxel_mm"),
         "" if ds.get("grid_shape") is None
         else ", grid %s" % "×".join(str(v) for v in ds["grid_shape"])))
    a("| Volume fraction target | %s of the free region |" % dset.get("volfrac"))
    a("| Filter radius | %s mm |" % dset.get("filter_mm"))
    a("| Symmetry imposed | %s |" % (", ".join(dset.get("symmetry") or [])
                                     or "none"))
    if ds.get("preserve_mm3") is not None:
        a("| Preserve (always kept) | %s mm³ |" % ds["preserve_mm3"])
        a("| Obstacle (never filled) | %s mm³ |" % ds.get("obstacle_mm3"))
        a("| Free for the optimiser | %s mm³ |" % ds.get("free_mm3"))
    if fr.get("free_fraction_of_domain") is not None:
        a("| Share of the domain the optimiser may decide | **%.0f %%** |"
          % (100 * fr["free_fraction_of_domain"]))
    if fr.get("preserve_fraction_of_expected_part") is not None:
        a("| Share of the expected final part that is preserve | **%.0f %%** |"
          % (100 * fr["preserve_fraction_of_expected_part"]))
    a("")
    if dset.get("symmetry_note"):
        a("- %s" % dset["symmetry_note"])
    if dset.get("voxel_note"):
        a("- %s" % dset["voxel_note"])
    a("")
    if fr.get("fixed_points"):
        a("**Fixed by the CAD model, not by the optimisation.** These act on "
          "preserve, so their positions are inputs:")
        a("")
        a("| | Region | Position (mm) | Volume mm³ |")
        a("|---|---|---|---|")
        for f in fr["fixed_points"]:
            a("| %s | `%s` | %s | %s |"
              % (f["kind"], f["region"], _vec(f["centroid_mm"]), f["mm3"]))
        a("")
    if fr.get("ground_zones"):
        a("**Where the part touches down is a result, not an input.** These "
          "supports act on the build plane, so the optimiser chose how many "
          "feet, how far out and what shape. What this study still chose is "
          "the zone to look for contact in:")
        a("")
        a("| Zone | Centre of the searched band (mm) | Band area mm² |")
        a("|---|---|---|")
        for z in fr["ground_zones"]:
            a("| `%s` | %s | %s |"
              % (z["region"], _vec(z["centroid_mm"]), z["area_mm2"]))
        a("")
        a("A ground contact is glued: the analysis cannot tip the part over, "
          "so no load case in it asks for a wide stance. If the zones above "
          "are the only thing producing one, they are doing more work than "
          "they look like they are.")
        a("")
    if d.get("geometry_sources"):
        a("Geometry read from:")
        a("")
        for k, v in d["geometry_sources"].items():
            if isinstance(v, dict):
                for name, src in v.items():
                    a("- **%s** `%s` → `%s`" % (k, name, src))
            else:
                a("- **%s** → `%s`" % (k, v))
        a("")

    # --- 6. limitations
    a("## 6. What this study does *not* establish")
    a("")
    for line in limitations(d):
        a("- %s" % line)
    a("")

    if d.get("setup_valid") is False:
        a("## 7. The setup did not validate")
        a("")
        a("These were reported before any solving, and nothing downstream is "
          "trustworthy until they are fixed:")
        a("")
        for e in d.get("setup_errors", []):
            a("- **%s**" % e)
        a("")
    return "\n".join(L).rstrip() + "\n"


def write(outdir, study, setup=None, report=None, isotropic=False):
    """-> {"md": path, "json": path}"""
    os.makedirs(outdir, exist_ok=True)
    d = collect(study, setup, report, isotropic)
    md = os.path.join(outdir, "assumptions.md")
    js = os.path.join(outdir, "assumptions.json")
    with open(md, "w", encoding="utf-8") as f:
        f.write(render(d))
    with open(js, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    return {"md": md, "json": js}

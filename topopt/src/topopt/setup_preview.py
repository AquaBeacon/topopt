"""The 3D setup preview: see the design space before spending an hour solving it.

Colour convention, matching Fusion Generative Design so the pictures are
readable by anyone who has used it:

    preserve      green
    obstacle      red
    design space  transparent grey
    start shape   blue

Three outputs, because each catches a different mistake:

- `*_setup.png`     three orthographic composites plus an isometric voxel view.
  Wrong units, a body in the wrong place and a preserve that is nowhere near
  the design space are all obvious here and nowhere else.
- `*_setup_*.stl`   the same bodies as meshes, so they can be dropped into any
  viewer or into Fusion next to the real CAD.
- `*_setup.scad`    an OpenSCAD file that imports those STLs *with colour*. STL
  carries no colour; this is how the 3D preview keeps it.
"""

import os

import numpy as np

from .meshout import merge_stls, write_blocky_stl

COLOURS = {
    "design_space": (0.62, 0.65, 0.70),
    "preserve": (0.13, 0.72, 0.33),
    "obstacle": (0.88, 0.19, 0.19),
    "starting_shape": (0.20, 0.45, 0.95),
    # Where a preserve body that declared `attach` may be joined. Drawn on
    # top of the design space so the reader sees the one place the optimiser
    # is allowed to touch the interface -- and, by omission, the shell it
    # may not.
    "attach": (0.96, 0.62, 0.08),
}

_SCAD_COLOUR = {
    "design_space": '[0.62,0.65,0.70,0.18]',
    "preserve": '[0.13,0.72,0.33,1.0]',
    "obstacle": '[0.88,0.19,0.19,0.55]',
    "starting_shape": '[0.20,0.45,0.95,0.35]',
    "attach": '[0.96,0.62,0.08,0.9]',
}


def _downsample(mask, cap):
    """Block-max downsample so the isometric view stays cheap to render."""
    f = max(1, int(np.ceil(max(mask.shape) / cap)))
    if f == 1:
        return mask, 1
    pad = [(0, (-s) % f) for s in mask.shape]
    m = np.pad(mask, pad)
    m = m.reshape(m.shape[0] // f, f, m.shape[1] // f, f, m.shape[2] // f, f)
    return m.any(axis=(1, 3, 5)), f


def _shell(mask):
    """Surface voxels only -- a transparent solid block renders as mush and
    takes forever; its shell reads as a box you can see through."""
    from scipy.ndimage import binary_erosion
    if not mask.any():
        return mask
    return mask & ~binary_erosion(mask)


def _build_axis_banner(build_axis, raster_axis=None, declared=True):
    """One line naming the layer normal and the in-plane axes.

    The reported safety factors depend on it -- interlayer tension is read
    along this axis -- so the picture should say which one was used, and say
    when nobody chose it.
    """
    names = "XYZ"
    b = names[build_axis]
    plane = "".join(n for i, n in enumerate(names) if i != build_axis)
    txt = ("build axis %s (layer normal)   layer plane %s"
           % (b, "-".join(plane)))
    if raster_axis is not None:
        txt += "   raster %s" % names[raster_axis]
    if not declared:
        txt += "   [NOT DECLARED -- assumed]"
    return txt


def write_setup_png(path, setup, title="", loads=None, build_axis=None,
                    raster_axis=None, build_axis_declared=True):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    g = setup.grid
    fig = plt.figure(figsize=(19, 5.0))
    planes = [(2, "XY (top)", 0, 1), (1, "XZ (front)", 0, 2), (0, "YZ (side)", 1, 2)]

    layers = [("design_space", setup.design_space, 0.30),
              ("obstacle", setup.obstacle, 0.85),
              ("starting_shape", setup.starting_shape, 0.55),
              ("attach", setup.attach, 0.9),
              ("preserve", setup.preserve, 1.0)]

    for col, (axis, label, ha, va) in enumerate(planes):
        ax = fig.add_subplot(1, 4, col + 1)
        h, w = g.shape[ha], g.shape[va]
        rgb = np.ones((h, w, 3))
        for name, mask, alpha in layers:
            if not mask.any():
                continue
            proj = mask.max(axis=axis)
            c = np.array(COLOURS[name])
            sel = proj.astype(bool)
            rgb[sel] = rgb[sel] * (1 - alpha) + c * alpha
        extent = [g.origin[ha], g.origin[ha] + g.shape[ha] * g.h,
                  g.origin[va], g.origin[va] + g.shape[va] * g.h]
        ax.imshow(np.transpose(rgb, (1, 0, 2)), origin="lower", extent=extent,
                  aspect="equal", interpolation="nearest")
        ax.set_title(label, fontsize=9)
        ax.set_xlabel("XYZ"[ha] + " (mm)", fontsize=8)
        ax.set_ylabel("XYZ"[va] + " (mm)", fontsize=8)
        ax.tick_params(labelsize=7)

        # Build direction, drawn in the two views that contain it. Layer lines
        # run perpendicular to it, so the hatching shows the layer plane
        # edge-on -- which is the thing a reader has to picture to judge
        # whether a load is pulling layers apart.
        if build_axis is not None and build_axis in (ha, va):
            ux = (extent[1] - extent[0]) * 0.10
            uy = (extent[3] - extent[2]) * 0.10
            x0 = extent[0] + 0.06 * (extent[1] - extent[0])
            y0 = extent[2] + 0.06 * (extent[3] - extent[2])
            dx, dy = (ux, 0.0) if build_axis == ha else (0.0, uy)
            ax.annotate("", xy=(x0 + dx, y0 + dy), xytext=(x0, y0),
                        arrowprops=dict(arrowstyle="-|>", color="#1f77b4",
                                        lw=1.6, shrinkA=0, shrinkB=0))
            ax.text(x0 + dx * 1.15, y0 + dy * 1.15,
                    "build %s" % "XYZ"[build_axis], color="#1f77b4",
                    fontsize=7, ha="left", va="bottom")
            # layer plane, edge-on: short ticks across the build direction
            for k in range(1, 4):
                if build_axis == va:
                    yy = y0 + dy * k / 3.0
                    ax.plot([x0 - ux * 0.25, x0 + ux * 0.25], [yy, yy],
                            color="#1f77b4", lw=0.7, alpha=0.55)
                else:
                    xx = x0 + dx * k / 3.0
                    ax.plot([xx, xx], [y0 - uy * 0.25, y0 + uy * 0.25],
                            color="#1f77b4", lw=0.7, alpha=0.55)
        for arrow in (loads or []):
            p, v = np.asarray(arrow["at"], float), np.asarray(arrow["dir"], float)
            n = np.linalg.norm(v[[ha, va]])
            if n < 1e-9:
                continue
            s = 0.18 * max(extent[1] - extent[0], extent[3] - extent[2]) / n
            ax.arrow(p[ha], p[va], v[ha] * s, v[va] * s, width=0.4,
                     color="k", length_includes_head=True, alpha=0.75)

    ax = fig.add_subplot(1, 4, 4, projection="3d")
    ds, f = _downsample(setup.design_space, 34)
    pr, _ = _downsample(setup.preserve, 34)
    ob, _ = _downsample(setup.obstacle, 34)
    at, _ = _downsample(setup.attach, 34)
    at = at & ~pr & ~ob
    ds = _shell(ds) & ~pr & ~ob & ~at
    filled = ds | pr | ob | at
    if filled.any():
        colours = np.zeros(filled.shape + (4,))
        colours[ds] = COLOURS["design_space"] + (0.16,)
        colours[ob] = COLOURS["obstacle"] + (0.65,)
        colours[at] = COLOURS["attach"] + (0.9,)
        colours[pr] = COLOURS["preserve"] + (1.0,)
        ax.voxels(filled, facecolors=colours, edgecolors=(0, 0, 0, 0.05),
                  linewidth=0.2, shade=True)
    ax.set_box_aspect(filled.shape)
    ax.set_title("3D setup (cell = %.2f mm)" % (g.h * f), fontsize=9)
    # ax.voxels indexes cells, so relabel the ticks in mm or the reader silently
    # compares the picture against the wrong numbers.
    for a, setter, lbl in ((0, ax.set_xticks, "X"), (1, ax.set_yticks, "Y"),
                           (2, ax.set_zticks, "Z")):
        n = filled.shape[a]
        ticks = np.linspace(0, n, min(5, n + 1))
        setter(ticks, ["%.0f" % (g.origin[a] + t * g.h * f) for t in ticks])
    ax.set_xlabel("X (mm)", fontsize=8)
    ax.set_ylabel("Y (mm)", fontsize=8)
    ax.set_zlabel("Z (mm)", fontsize=8)
    ax.tick_params(labelsize=6)
    ax.view_init(elev=24, azim=-58)

    fig.legend(handles=[Patch(facecolor=COLOURS[k], label=l) for k, l in (
        ("preserve", "preserve (density 1)"),
        ("obstacle", "obstacle + clearance (density 0)"),
        ("design_space", "design space (free)"),
        ("attach", "attach zone (only here may material join preserve)"),
        ("starting_shape", "starting shape"))],
        loc="lower center", ncol=5, fontsize=8, frameon=False)
    fig.suptitle(title or "generative design setup", fontsize=11)
    if build_axis is not None:
        fig.text(0.5, 0.925,
                 _build_axis_banner(build_axis, raster_axis,
                                    build_axis_declared),
                 ha="center", fontsize=8,
                 color="#1f77b4" if build_axis_declared else "#c0392b")
    # No tight_layout: a 3d axes' decorations make it give up with a warning.
    fig.subplots_adjust(left=0.04, right=0.99, top=0.88, bottom=0.14, wspace=0.28)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def write_setup_stls(outdir, base, setup):
    """Preserve / obstacle / design-space / combined meshes. -> {name: path}"""
    os.makedirs(outdir, exist_ok=True)
    out = {}
    parts = [("preserve", setup.preserve),
             ("obstacle", setup.obstacle),
             ("obstacle_declared", setup.obstacle_raw),
             ("design_space", setup.design_space & ~setup.preserve & ~setup.obstacle),
             ("attach", setup.attach),
             ("no_attach", setup.no_attach),
             ("starting_shape", setup.starting_shape)]
    for name, mask in parts:
        if not mask.any():
            continue
        p = os.path.join(outdir, "%s_%s.stl" % (base, name))
        write_blocky_stl(p, mask, setup.h, setup.origin)
        out[name] = p
    combined = os.path.join(outdir, "%s_setup.stl" % base)
    merge_stls(combined, [out[k] for k in
                          ("design_space", "obstacle", "preserve") if k in out])
    out["setup"] = combined

    scad = os.path.join(outdir, "%s_setup.scad" % base)
    with open(scad, "w", encoding="utf-8") as f:
        f.write("// Generative-design setup preview -- open in OpenSCAD (F5).\n"
                "// green = preserve, red = obstacle + clearance,\n"
                "// grey  = design space, blue = starting shape,\n"
                "// orange = attach zone (the only cells that may join a\n"
                "//          preserve body which declared `attach`).\n"
                "// STL carries no colour, which is why this file exists.\n\n")
        for key in ("design_space", "obstacle", "starting_shape", "attach",
                    "preserve"):
            if key in out:
                f.write('color(%s) import("%s");\n'
                        % (_SCAD_COLOUR[key], os.path.basename(out[key])))
    out["scad"] = scad
    return out


def export_setup(setup, study, outdir, base, png=True, title=None):
    """Everything a person needs to check a setup before paying for the solve.

    Written *before* the first solve on purpose: a wrong design space costs an
    hour if you find it in the result and ten seconds if you find it here.
    """
    import json

    os.makedirs(outdir, exist_ok=True)
    paths = write_setup_stls(outdir, base, setup)
    if png:
        p = os.path.join(outdir, "%s_setup.png" % base)
        raster = getattr(study, "raster_axis", None)
        if isinstance(raster, str):
            raster = {"X": 0, "Y": 1, "Z": 2}.get(raster.upper())
        write_setup_png(p, setup, title=title or ("setup -- %s" % base),
                        loads=load_markers(study, setup),
                        build_axis=getattr(study, "build_axis", None),
                        raster_axis=raster,
                        build_axis_declared=getattr(
                            study, "build_axis_declared", True))
        paths["png"] = p
    rp = os.path.join(outdir, "%s_setup.json" % base)
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(setup.report, f, indent=2, ensure_ascii=False)
    paths["json"] = rp
    return paths


def print_report(report):
    """Human-readable validation summary. Returns True when the setup is OK."""
    info = report.get("info", {})
    print("\n--- setup ---")
    print("  voxel               %.3f mm, grid %s"
          % (info.get("voxel_mm", 0), info.get("grid_shape")))
    print("  design space        %.0f mm3" % info.get("design_space_mm3", 0))
    print("  preserve            %.0f mm3 in %d bodies"
          % (info.get("preserve_mm3", 0), info.get("preserve_bodies", 0)))
    print("  obstacle            %.0f mm3 declared -> %.0f mm3 with clearance"
          % (info.get("obstacle_declared_mm3", 0), info.get("obstacle_mm3", 0)))
    print("  free (optimisable)  %.0f mm3 (%d cells)"
          % (info.get("free_mm3", 0), info.get("free_voxels", 0)))
    # Where each interface may be joined. The shell counts as obstacle above,
    # so say how much of that obstacle this rule added rather than the user.
    for name, a in (info.get("attach") or {}).items():
        print("  attach              %s may be joined at %d cell(s), %.0f mm2 "
              "(%s); %d cell(s) / %.0f mm3 of no-contact shell around the "
              "rest of it, %.2f mm thick"
              % (name, a.get("attach_usable_voxels", 0),
                 a.get("attach_area_mm2", 0),
                 ", ".join(str(list(sp.values())[0]) for sp in a.get("specs", [])),
                 a.get("no_attach_voxels", 0), a.get("no_attach_mm3", 0),
                 a.get("standoff_mm", 0)))

    # The two questions that decide whether the run is worth starting: can the
    # machine print what comes out, and is there anything left to decide.
    bv = info.get("build_volume") or {}
    dom = bv.get("domain") or {}
    if dom.get("size_mm"):
        fits = dom.get("fits_as_declared")
        print("  build volume        %s on %s -- design space %s, %s"
              % ("x".join("%.0f" % v for v in dom.get("envelope_mm", [])),
                 bv.get("printer", {}).get("name", "?"),
                 "x".join("%.0f" % v for v in dom["size_mm"]),
                 "fits" if fits else "DOES NOT FIT"))
    fr = info.get("design_freedom") or {}
    if fr:
        pf = fr.get("preserve_fraction_of_expected_part")
        print("  free to decide      %.0f %% of the domain; preserve is %s of "
              "the expected part"
              % (100 * fr.get("free_fraction_of_domain", 0),
                 "%.0f %%" % (100 * pf) if pf is not None else "?"))

    for b in report.get("bodies", []):
        if b["kind"] == "preserve_component":
            continue
        print("    %-10s %-22s %8.0f mm3  %s"
              % (b["kind"], b["name"], b["mm3"], b["source"]))
    for w in report.get("warnings", []):
        print("  WARN  %s" % w)
    for e in report.get("errors", []):
        print("  ERROR %s" % e)
    print("  => %s" % ("setup OK" if report.get("ok") else "SETUP INVALID"))
    return bool(report.get("ok"))


def load_markers(study, setup):
    """Arrow annotations for the preview: where each load pushes."""
    from .loadcase import region_mask
    marks = []
    for case in study.cases:
        for ld in case.loads:
            spec = ld.get("region", ld) if isinstance(ld, dict) else ld
            if "force" not in (ld if isinstance(ld, dict) else {}):
                continue
            m = region_mask(setup.grid, spec, study.regions, clip=True)
            if not m.any():
                continue
            idx = np.argwhere(m)
            centre = setup.origin + (idx.mean(axis=0) + 0.5) * setup.h
            marks.append({"at": centre, "dir": np.asarray(ld["force"], float),
                          "case": case.name})
    return marks

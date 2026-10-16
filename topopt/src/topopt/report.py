"""Diagnostics and PNG output for a voxel FEA run."""

import numpy as np

try:
    from scipy.ndimage import distance_transform_edt, maximum_filter
except Exception:                                    # pragma: no cover
    distance_transform_edt = maximum_filter = None


def local_thickness(grid):
    """Approximate wall/section thickness field, in mm, on the medial voxels.

    The distance transform alone will not do: every voxel touching a surface is
    exactly one voxel from the void, so its percentiles just report the voxel
    size back. Instead take the *ridge* of the distance transform (voxels that
    are a local maximum), which approximates the medial axis, and read the
    inscribed-sphere diameter there. That is the real section thickness.
    """
    if distance_transform_edt is None:
        return None
    edt = distance_transform_edt(grid.solid) * grid.h
    ridge = grid.solid & (edt >= maximum_filter(edt, size=3) - 1e-9) & (edt > 0)
    vals = 2.0 * edt[ridge]
    return vals if vals.size else None


def mesh_adequacy(grid):
    """How many elements sit across the thin sections of the part.

    Bending stiffness goes as thickness^3, so a section resolved by one or two
    elements is where a voxel model lies to you the most.
    """
    vals = local_thickness(grid)
    if vals is None:
        return None
    thin = float(np.percentile(vals, 5))
    typical = float(np.percentile(vals, 50))
    return {
        "thin_section_mm": thin,
        "typical_section_mm": typical,
        "elements_across_thin": thin / grid.h,
        "elements_across_typical": typical / grid.h,
    }


def hotspots(result, grid, values, n=5, min_sep_mm=None):
    """Top-n locations of `values`, thinned so they are not all one blob."""
    if min_sep_mm is None:
        min_sep_mm = 4 * grid.h
    order = np.argsort(values)[::-1]
    picked = []
    for i in order:
        p = grid.origin + (result.elements[i] + 0.5) * grid.h
        if all(np.linalg.norm(p - q["pos"]) > min_sep_mm for q in picked):
            picked.append({"pos": p, "value": float(values[i]),
                           "element": result.elements[i].tolist()})
        if len(picked) >= n:
            break
    return picked


def stress_concentration(values):
    """max / p99.5 ratio.

    A voxel model turns every curved or angled surface into stair steps, and a
    fully-clamped support into a mathematical singularity. Both inflate the
    single worst element without converging as the mesh refines. A large ratio
    means "the peak is a mesh artefact or a genuine sharp corner -- go look",
    not "the part is fine".
    """
    if values.size == 0:
        return float("nan")
    p = np.percentile(values, 99.5)
    return float(values.max() / p) if p > 0 else float("inf")


def summarise(result, grid, material, safety_factor, max_deflection_mm=None,
              build_axis=2):
    vm = result.von_mises
    fi = result.failure_index
    out = {
        "elements": int(result.elements.shape[0]),
        "voxel_mm": grid.h,
        "volume_mm3": grid.volume_mm3,
        "converged": result.converged,
        "cg_iterations": result.iterations,
        "compliance_Nmm": result.compliance,
        "max_displacement_mm": result.max_disp,
        "max_displacement_at": result.max_disp_node.tolist(),
        "von_mises_max_MPa": float(vm.max()),
        "von_mises_p99_5_MPa": float(np.percentile(vm, 99.5)),
        "stress_concentration_ratio": stress_concentration(vm),
        "failure_index_max": float(fi.max()),
        "failure_index_p99_5": float(np.percentile(fi, 99.5)),
        # A safety factor means nothing without the rule that produced it: the
        # two criteria use different strength numbers and disagree by design.
        "failure_criterion": getattr(result, "failure_criterion", "unknown"),
        "material_axes": dict(getattr(result, "material_axes", {}) or {}),
        "min_safety_factor": result.min_safety_factor,
        "safety_factor_p99_5": (float(1.0 / np.percentile(fi, 99.5))
                                if np.percentile(fi, 99.5) > 0 else float("inf")),
        "mesh": mesh_adequacy(grid),
        "warnings": list(result.warnings),
    }

    checks = []
    sf = out["min_safety_factor"]
    sf_p = out["safety_factor_p99_5"]
    checks.append({
        "name": "strength",
        "required": "safety factor >= %.2f" % safety_factor,
        "got": "%.2f (worst element) / %.2f (p99.5)" % (sf, sf_p),
        # The single worst element is often a stair-step artefact, so the p99.5
        # value decides pass/fail and the worst element decides whether a human
        # has to go and look.
        "pass": bool(sf_p >= safety_factor),
        "inspect": bool(sf < safety_factor),
    })
    if max_deflection_mm is not None:
        checks.append({
            "name": "stiffness",
            "required": "max deflection <= %.3f mm" % max_deflection_mm,
            "got": "%.3f mm" % result.max_disp,
            "pass": bool(result.max_disp <= max_deflection_mm),
            "inspect": False,
        })
    m = out["mesh"]
    if m:
        across = m["elements_across_thin"]
        chk = {
            "name": "mesh resolution",
            "required": ">= 3 elements across the thinnest section",
            "got": "%.1f (thin %.2f mm) / %.1f (typical %.2f mm)" % (
                across, m["thin_section_mm"],
                m["elements_across_typical"], m["typical_section_mm"]),
            "pass": bool(across >= 3.0),
            "inspect": False,
        }
        if not chk["pass"] and across > 0:
            # Quote a voxel size, not an element count: --elements targets the
            # bounding box while `elements` here counts solid voxels, and the
            # ratio between them depends on how hollow the part is.
            chk["fix"] = ("re-run with --voxel %.2f"
                          % (grid.h * across / 3.0))
        checks.append(chk)
    out["checks"] = checks
    out["pass"] = all(c["pass"] for c in checks)
    out["hotspots"] = [
        {"pos": [round(float(v), 2) for v in h["pos"]],
         "failure_index": round(h["value"], 3),
         "safety_factor": round(1.0 / h["value"], 2) if h["value"] > 0 else None}
        for h in hotspots(result, grid, fi, n=5)
    ]
    return out


def write_png(path, grid, result, title=""):
    """Three max-projections each of von Mises and displacement."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    nx, ny, nz = grid.shape
    e = result.elements
    vmfield = np.full(grid.shape, np.nan)
    vmfield[e[:, 0], e[:, 1], e[:, 2]] = result.von_mises

    nnode = (nx + 1) * (ny + 1) * (nz + 1)
    dmag = np.linalg.norm(result.u.reshape(nnode, 3), axis=1).reshape(
        nx + 1, ny + 1, nz + 1)
    # Element-centred displacement = mean of its 8 nodes.
    dfield = np.full(grid.shape, np.nan)
    dfield[e[:, 0], e[:, 1], e[:, 2]] = np.mean([
        dmag[e[:, 0] + a, e[:, 1] + b, e[:, 2] + c]
        for a in (0, 1) for b in (0, 1) for c in (0, 1)], axis=0)

    fig, axes = plt.subplots(2, 3, figsize=(13, 8))
    planes = [(2, "XY (top)", 0, 1), (1, "XZ (front)", 0, 2), (0, "YZ (side)", 1, 2)]
    for row, (field, name, unit) in enumerate((
            (vmfield, "von Mises", "MPa"), (dfield, "displacement", "mm"))):
        for col, (axis, label, ha, va) in enumerate(planes):
            ax = axes[row][col]
            # Columns that miss the part entirely are all-NaN; go through
            # -inf rather than nanmax so numpy does not warn on every one.
            filled = np.where(np.isnan(field), -np.inf, field)
            proj = filled.max(axis=axis)
            proj = np.where(np.isinf(proj), np.nan, proj)
            extent = [grid.origin[ha], grid.origin[ha] + grid.shape[ha] * grid.h,
                      grid.origin[va], grid.origin[va] + grid.shape[va] * grid.h]
            im = ax.imshow(proj.T, origin="lower", extent=extent,
                           cmap="inferno", aspect="equal")
            ax.set_title("%s -- %s" % (name, label), fontsize=9)
            ax.set_xlabel("XYZ"[ha] + " (mm)", fontsize=8)
            ax.set_ylabel("XYZ"[va] + " (mm)", fontsize=8)
            ax.tick_params(labelsize=7)
            cb = fig.colorbar(im, ax=ax, fraction=0.046)
            cb.set_label(unit, fontsize=8)
            cb.ax.tick_params(labelsize=7)
    fig.suptitle(title or "FEA result (max projection)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path

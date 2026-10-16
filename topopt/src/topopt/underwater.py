"""Underwater loads: hydrostatic pressure, buoyancy, and a crude drag model.

The three loads are different in kind and it matters which is which:

- **Hydrostatic pressure** acts on every wetted surface, normal to it, and
  grows with depth. On a sealed hollow it is a crushing load with nothing
  resisting from inside. On an *open* frame it very nearly cancels -- water
  pushes equally on both sides -- and applying it as if the frame were sealed
  is the classic way to "discover" that a perfectly good bracket needs to be
  three times thicker.

- **Buoyancy** acts on the displaced volume, upward, through the centroid of
  the displaced volume. For a printed part it is not small: PLA is 1.24 g/cm3
  against water's 1.00, so a submerged PLA part weighs about 19 % of its dry
  weight. Ignoring it makes gravity cases wrong in the safe direction, which
  is why it is easy to leave out and worth putting in.

- **Drag** is the one that is genuinely approximate. `F = 0.5 rho Cd A v^2`
  with a projected area and a table Cd is a screening number, not a CFD
  result, and this module says so in every output it produces rather than
  leaving the reader to remember.

Units, as everywhere here: mm, N, MPa. The interface takes kg/m^3 for water
and g/cm^3 for the polymer, because those are what people quote; every
conversion is written out at the point of use rather than hidden in a
constant, which is the project rule and is also how the 1000x error in the
first version of `pressure_MPa` was caught.
"""

import numpy as np

G = 9.80665                    # m/s^2
RHO_FRESH = 1000.0             # kg/m^3
RHO_SEA = 1025.0               # kg/m^3

# Drag coefficients for the shapes a bracket actually resembles. Deliberately
# short: a longer table invites picking a flattering number.
CD = {
    "flat_plate": 1.28,        # normal to the flow
    "cube": 1.05,
    "cylinder": 1.20,          # cross-flow, long
    "sphere": 0.47,
    "streamlined": 0.10,
    "open_frame": 1.50,        # bluff, with wake interaction between members
}


def pressure_MPa(depth_mm, rho_kg_m3=RHO_FRESH, surface_MPa=0.0):
    """Gauge pressure at a depth, MPa. `depth_mm` measured downward from the
    free surface; a negative depth (above water) is zero, not negative."""
    d = np.maximum(np.asarray(depth_mm, float), 0.0)
    # p[Pa] = rho[kg/m3] * g[m/s2] * h[m], and h[m] = h[mm]/1000, so
    # p[Pa] = rho*g*h_mm/1000 and p[MPa] = p[Pa]/1e6 = rho*g*h_mm * 1e-9.
    # Sanity: 1000 * 9.80665 * 10000 mm * 1e-9 = 0.0981 MPa at 10 m. Correct.
    return surface_MPa + rho_kg_m3 * G * d * 1e-9


def hydrostatic_nodal_forces(grid, mask, depth_of_surface_mm,
                             rho_kg_m3=RHO_FRESH, sealed=True,
                             build_axis_up=2, *, _nodal=True):
    """Pressure loads on the wetted outer surface. -> (nnode,3,...) force array.

    Each exposed voxel face carries `p * area` along its inward normal. The
    faces are found from the mask itself, so a hollow that the mask shows as
    solid is treated as solid -- which is the honest reading of the geometry
    handed in.

    `sealed=False` reports the loads but zeroes the net, modelling an open
    frame where water reaches both sides: the local pressure is real but the
    resultant is not, and treating an open frame as a pressure vessel is the
    classic way to triple a wall thickness for nothing.
    """
    from .hex8 import node_ids

    if sealed:
        from scipy.ndimage import binary_fill_holes
        mask = binary_fill_holes(mask)
    nx, ny, nz = grid.solid.shape
    h = grid.h
    force = np.zeros((nx + 1, ny + 1, nz + 1, 3)) if _nodal else None
    nid = node_ids(grid.solid.shape) if _nodal else None
    area = h * h

    wetted_faces = 0
    resultant = np.zeros(3)
    max_pressure = 0.0
    up = int(build_axis_up)
    for axis in range(3):
        for sgn in (-1, +1):
            nb = np.roll(mask, -sgn, axis=axis)
            # `np.roll(m, -1)` puts m[0] at the end, so the invalid entry is at
            # index -1; `np.roll(m, +1)` wraps at index 0. Zeroing the wrong end
            # swaps which face of the part is seen as exposed -- the resultant
            # then comes out with the wrong sign, which is what happened.
            sl = [slice(None)] * 3
            sl[axis] = -1 if sgn > 0 else 0
            nb[tuple(sl)] = False
            exposed = mask & ~nb
            idx = np.argwhere(exposed)
            if not len(idx):
                continue
            wetted_faces += len(idx)
            # Depth of the *face*, not of the voxel centre. For a face whose
            # normal is along the up-axis the two differ by h/2, and using the
            # centre understates the top-to-bottom pressure difference by
            # exactly one voxel -- a 10 % error on a 10-voxel cube that shrinks
            # with refinement and so looks like discretisation rather than a bug.
            face_z = (grid.origin[up] + (idx[:, up] + 0.5) * h
                      + (sgn * h / 2.0 if axis == up else 0.0))
            p = pressure_MPa(depth_of_surface_mm - face_z, rho_kg_m3)
            max_pressure = max(max_pressure, float(np.max(p)))
            resultant[axis] += float(np.sum(-sgn * p * area))
            if not _nodal:
                continue
            # Pressure pushes inward, i.e. against the outward normal.
            f = -sgn * p * area / 4.0            # split over the 4 face nodes
            others = [k for k in range(3) if k != axis]
            for a in (0, 1):
                for b in (0, 1):
                    off = np.zeros(3, int)
                    off[others[0]] = a
                    off[others[1]] = b
                    if sgn > 0:
                        off[axis] = 1
                    n = nid[idx[:, 0] + off[0], idx[:, 1] + off[1],
                            idx[:, 2] + off[2]]
                    np.add.at(force.reshape(-1, 3)[:, axis], n, f)

    info = {"wetted_faces": int(wetted_faces),
            "wetted_area_mm2": round(float(wetted_faces) * area, 1),
            "surface_depth_mm": depth_of_surface_mm,
            "max_pressure_MPa": round(max_pressure, 6),
            "sealed": bool(sealed),
            "resultant_N": [round(float(v), 4)
                            for v in resultant]}
    if not sealed:
        info["note"] = (
            "open frame: water reaches both sides of every member, so the "
            "local pressure is real but the net crushing load is not. The "
            "forces are reported and then zeroed. Treating an open frame as a "
            "pressure vessel is how a sound bracket gets three times thicker "
            "for nothing.")
        if force is not None:
            force[:] = 0.0
    else:
        info["note"] = ("sealed body: the pressure acts inward on every wetted "
                        "face with nothing pushing back from inside")
    return force, info


def buoyancy(grid, mask, material_density_g_cm3, rho_kg_m3=RHO_FRESH,
             submerged_fraction=1.0, up_axis=2, displaced_mask=None):
    """Displaced volume, buoyant force, and where it acts.

    Returns the *net* weight too, because that is the number a load case
    needs: a submerged PLA part weighs about 19 % of its dry weight, and a part
    less dense than water pulls upward instead.
    """
    if not mask.any():
        return {"error": "no material", "displaced_volume_mm3": 0.0}
    vox = grid.h ** 3
    idx = np.argwhere(mask if displaced_mask is None else displaced_mask)
    centres = grid.origin + (idx + 0.5) * grid.h

    frac = float(np.clip(submerged_fraction, 0.0, 1.0))
    if frac >= 1.0:
        wet = np.ones(len(idx), bool)
    else:
        # Submerge from the bottom up along the up-axis, which is what a part
        # sitting in water actually does.
        order = np.argsort(centres[:, up_axis])
        cut = int(round(frac * len(idx)))
        wet = np.zeros(len(idx), bool)
        wet[order[:cut]] = True

    vol_total = float(mask.sum()) * vox
    vol_disp = float(wet.sum()) * vox
    # 1 kg/m^3 = 1e-9 kg/mm^3, so F = rho * 1e-9 * V[mm^3] * g.
    f_buoy = rho_kg_m3 * 1e-9 * G * vol_disp
    # 1 g/cm^3 = 1e-6 kg/mm^3, so W = rho_g_cm3 * 1e-6 * V[mm^3] * g.
    weight = material_density_g_cm3 * 1e-6 * vol_total * G

    centre = (centres[wet].mean(axis=0) if wet.any()
              else centres.mean(axis=0))
    return {
        "volume_mm3": round(vol_total, 1),
        "displaced_volume_mm3": round(vol_disp, 1),
        "submerged_fraction": round(frac, 4),
        "water_density_kg_m3": rho_kg_m3,
        "buoyant_force_N": round(float(f_buoy), 4),
        "dry_weight_N": round(float(weight), 4),
        "net_weight_in_water_N": round(float(weight - f_buoy), 4),
        "weight_fraction_of_dry": (round(float((weight - f_buoy) / weight), 4)
                                   if weight > 0 else None),
        "centre_of_buoyancy_mm": [round(float(v), 3) for v in centre],
        "note": ("a part denser than water still has weight in it; a part "
                 "lighter than water pulls upward and the sign of every "
                 "gravity case flips"),
    }


def projected_area(mask, h, flow_axis=0):
    """Silhouette area normal to the flow, mm^2.

    The *silhouette*, not the sum of face areas: a member hiding behind
    another does not see the free stream twice.
    """
    others = [a for a in range(3) if a != flow_axis]
    shadow = mask.any(axis=flow_axis)
    return float(np.count_nonzero(shadow)) * h * h, shadow


def drag(grid, mask, velocity_m_s, cd="open_frame", rho_kg_m3=RHO_FRESH,
         flow_axis=0):
    """Screening drag load. NOT a CFD result, and every output says so.

    -> dict with the force and the per-slice distribution used to spread it.
    """
    cd_val = CD[cd] if isinstance(cd, str) else float(cd)
    area, shadow = projected_area(mask, grid.h, flow_axis)
    # 0.5 * rho[kg/m3] * v^2[m2/s2] * A[mm2] -> N needs A in m^2: 1e-6.
    f = 0.5 * rho_kg_m3 * velocity_m_s ** 2 * area * 1e-6
    return {
        "flow_axis": "XYZ"[flow_axis],
        "velocity_m_s": velocity_m_s,
        "cd": cd_val,
        "cd_source": cd if isinstance(cd, str) else "given",
        "projected_area_mm2": round(area, 1),
        "drag_force_N": round(float(f * cd_val), 4),
        "model": "simplified",
        "note": ("F = 0.5 rho Cd A v^2 with a silhouette area and a table Cd. "
                 "This is a screening number for picking between candidates, "
                 "not a flow solution: it has no wake, no interference between "
                 "members and no Reynolds dependence. Do not quote it as a "
                 "predicted force."),
    }


def worst_flow_direction(grid, mask, velocity_m_s, cd="open_frame",
                         rho_kg_m3=RHO_FRESH):
    """Drag along each axis, and which is worst. Cheap, and the answer is
    usually not the axis someone guessed."""
    out = [drag(grid, mask, velocity_m_s, cd, rho_kg_m3, a) for a in range(3)]
    worst = max(out, key=lambda d: d["drag_force_N"])
    return {"per_axis": out, "worst_axis": worst["flow_axis"],
            "worst_drag_N": worst["drag_force_N"]}


def drag_nodal_forces(grid, mask, total_force_N, flow_axis=0):
    """Spread a drag resultant over the upstream faces. -> (nnode,3) array.

    Distributed rather than applied at one point, because a point load on a
    voxel model produces a stress singularity that then dominates every
    percentile the report prints.
    """
    from .hex8 import node_ids

    nx, ny, nz = grid.solid.shape
    force = np.zeros((nx + 1, ny + 1, nz + 1, 3))
    nid = node_ids(grid.solid.shape)
    nb = np.roll(mask, 1, axis=flow_axis)
    sl = [slice(None)] * 3
    sl[flow_axis] = 0
    nb[tuple(sl)] = False
    up = mask & ~nb                      # upstream-facing voxels
    idx = np.argwhere(up)
    if not len(idx):
        return force
    per = total_force_N / (len(idx) * 4.0)
    others = [a for a in range(3) if a != flow_axis]
    for a in (0, 1):
        for b in (0, 1):
            off = np.zeros(3, int)
            off[others[0]] = a
            off[others[1]] = b
            n = nid[idx[:, 0] + off[0], idx[:, 1] + off[1], idx[:, 2] + off[2]]
            np.add.at(force.reshape(-1, 3)[:, flow_axis], n, per)
    return force


def report(grid, mask, material_density_g_cm3, depth_mm=0.0,
           velocity_m_s=0.0, cd="open_frame", sealed=False,
           seawater=False, submerged_fraction=1.0):
    """All three loads at once, with the assumptions attached."""
    rho = RHO_SEA if seawater else RHO_FRESH
    from scipy.ndimage import binary_fill_holes
    displaced = binary_fill_holes(mask) if sealed else mask
    out = {"water": "seawater" if seawater else "fresh water",
           "water_density_kg_m3": rho,
           "buoyancy": buoyancy(grid, mask, material_density_g_cm3, rho,
                                submerged_fraction, displaced_mask=displaced)}
    if depth_mm > 0:
        top = grid.origin[2] + (np.flatnonzero(mask.any(axis=(0, 1)))[-1] + 1) * grid.h
        _f, info = hydrostatic_nodal_forces(grid, mask, top + depth_mm, rho,
                                            sealed=sealed, _nodal=False)
        out["hydrostatic"] = info
    if velocity_m_s > 0:
        out["drag"] = worst_flow_direction(grid, mask, velocity_m_s, cd, rho)
    out["assumptions"] = [
        "the part is %s" % ("sealed (pressure acts inward with nothing "
                            "resisting)" if sealed else
                            "an open frame (pressure very nearly cancels)"),
        "drag uses a silhouette area and a table Cd; it is a screening number",
        "no wave loading, no added mass, no corrosion, no long-term water "
        "uptake in the polymer",
        "sealed cavities are excluded from wetted surfaces and included in displaced volume; dry weight uses material volume only",
        "pressure resultant already includes buoyancy: do not add buoyancy again when applying the complete pressure field",
    ]
    return out

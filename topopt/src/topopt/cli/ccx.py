"""`topopt ccx` -- re-analyse the extracted shape with CalculiX, on a tet mesh.

What this is for
----------------
Checking the optimiser with the optimiser proves nothing. This command hands
the finished shape to a *different* solver on a *different* mesh and reports
where the two disagree. If they agree, the number is worth something; if they
do not, that is the finding.

What it needs
-------------
Gmsh (to build the tetrahedral mesh) and CalculiX (to solve it). Neither is a
dependency of this toolkit, and neither is installed by `pip install topopt`.
Without them this command **exits with code 3** and prints how to install
them -- it does not fail the part, because a tool this machine does not have
says nothing about the bracket.

  topopt ccx part.stl --study study.json --out DIR
  topopt ccx part.stl --study study.json --out DIR --converge   # 3 mesh sizes
  topopt ccx part.stl --study study.json --out DIR --buckle --frequency
  topopt ccx --check                                            # what is installed

How regions become boundary conditions
--------------------------------------
A region selects **every node inside it** by default (`--map volume`) -- the
same boundary condition the voxel path applies, which is what spec 14.4 asks
for when the two solvers are compared. `--map surface` keeps only boundary
nodes. Neither was the cause of known-issue A6: that was the STL's own
triangles being used as the surface mesh, which is why the shell is refined
to the target size before meshing (`--no-refine` to see the old behaviour).

A support that says `{"contact": "ground"}` is cut down further, to the nodes
on the *lowest plane* of the mesh along the build axis (within `--ground-tol`).
This mirrors `loadcase._plane_nodes_of` and it is not a refinement: clamping
anything above the contact plane certifies a foot that is floating one layer
up as fully supported.

Honest limits
-------------
- **Loads and supports are mapped from the study's regions onto mesh nodes.**
  A region that lands on no node is an error, not a warning: a case with no
  load is not a case.
- **The load is split equally over the selected nodes.** The resultant is
  exact at any mesh density -- which is what makes the equilibrium check worth
  running -- but the distribution is not: a real pad presses hardest where it
  is stiffest. Read the peak stress under a load patch as approximate.
- **Gravity here uses the solid material's density, not the infill-derated one
  the voxel FEA uses.** The tet mesh has no infill to model, so the two paths
  genuinely disagree about the weight of a drop case, by the fill factor. Both
  numbers are recorded, so the difference is visible rather than surprising.
- **The buckling and frequency steps need a density.** They are skipped, with
  a note, for a material card that has none, rather than reported as zero.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from topopt import ccx as ccxlib                                  # noqa: E402
from topopt.external import ToolMissing, find, survey             # noqa: E402
from topopt.hex8 import max_stress_failure_index, von_mises       # noqa: E402
from topopt.loadcase import (_FIXMAP, load_case_from_json,        # noqa: E402
                             region_mask_points)
from topopt.materials import material_model                       # noqa: E402
from topopt.schema import InputError                              # noqa: E402


# Standard gravity in the deck's units. mm/s^2, because the whole model is
# mm/N/MPa: a body force written in m/s^2 is off by a factor of 1000 and still
# solves perfectly.
G_MM_S2 = 9810.0

# The bar no case clears, whatever its load class (spec 15.1). The p99.5
# safety factor may never carry a part whose worst element is under this --
# that element is where it breaks.
WORST_ELEMENT_MINIMUM = 1.5

GROUND_TOL_MM = 0.05

MAP_MODES = ("surface", "volume")


# ------------------------------------------------------------- mesh reading

def read_inp_mesh(path):
    """-> (node ids, node coords (n,3), element count).

    Kept for callers that predate `ccx.read_mesh`; everything in this module
    goes through `mesh_context` instead, which also needs the connectivity.
    """
    m = ccxlib.read_mesh(path)
    return m["ids"], m["xyz"], len(m["elements"])


def mesh_context(path):
    """Read a mesh once and keep what every later step needs.

    -> {"ids", "xyz", "elements", "types", "index", "surface", "is_surface",
        "faces"}

    `index` is an explicit {node id: row} map. Gmsh happens to number nodes
    1..n so `xyz[id - 1]` works, which is precisely why it is not relied on
    here: the contact-area and reaction-moment code both address nodes by id,
    and an off-by-one there produces a plausible area and a plausible moment.
    """
    m = ccxlib.read_mesh(path)
    ids = m["ids"]
    surface = ccxlib.surface_nodes(m["elements"])
    index = {int(v): i for i, v in enumerate(ids)}
    is_surface = np.zeros(len(ids), bool)
    for n in surface:
        j = index.get(int(n))
        if j is not None:
            is_surface[j] = True
    return {"ids": ids, "xyz": m["xyz"], "elements": m["elements"],
            "types": m["types"], "index": index, "surface": surface,
            "is_surface": is_surface,
            "faces": ccxlib.surface_faces(m["elements"])}


def region_nodes(mesh, spec, regions=None, mode="surface"):
    """Mesh node ids inside one study region. -> np.ndarray(int).

    `mode="surface"` keeps only nodes on the boundary of the mesh. The region
    itself is evaluated on *all* the points either way, so the shape rules in
    `region_mask_points` mean the same thing in both modes and only the
    filtering differs -- a region whose extent is read off the point cloud (a
    `face` region) must not change meaning when the caller changes the mapping.
    """
    if mode not in MAP_MODES:
        raise InputError("--map must be one of %s, got %r"
                         % ("/".join(MAP_MODES), mode))
    sel = region_mask_points(spec, mesh["xyz"], regions)
    if mode == "surface":
        sel = sel & mesh["is_surface"]
    return mesh["ids"][sel]


def nodes_in_regions(node_ids, xyz, regions, names):
    """Node ids falling inside any of the named regions (volume mode).

    Kept in the shape of the old helper; new code calls `region_nodes`, which
    can also say "surface only".
    """
    if not names:
        return np.array([], int)
    sel = np.zeros(len(node_ids), bool)
    for nm in names:
        sel |= region_mask_points(regions[nm], xyz)
    return np.asarray(node_ids)[sel]


def ground_nodes(mesh, ids, axis, tol=GROUND_TOL_MM):
    """The subset of `ids` sitting on the mesh's lowest plane. -> (ids, plane).

    The plane is the *mesh* minimum along the build axis, not the region's: a
    region drawn one millimetre proud of the table would otherwise define its
    own floor and clamp a part that is standing on nothing. Only nodes within
    `tol` of that plane survive, which is the tet-mesh reading of
    `loadcase._plane_nodes_of`: the contact face, never anything above it.
    Widening this to "the nodes of every element that touches the plane" turns
    a foot floating one layer up into a full clamp.
    """
    coord = mesh["xyz"][:, axis]
    plane = float(coord.min())
    ids = np.asarray(ids, int)
    if not len(ids):
        return ids, plane
    rows = np.array([mesh["index"][int(n)] for n in ids], int)
    return ids[coord[rows] <= plane + float(tol)], plane


def selected_area_mm2(mesh, ids):
    """Boundary area whose three corner nodes are all selected. -> mm^2.

    A support is otherwise reported by node count, and a node count says
    nothing about how much of the part is held: the same twelve nodes are a
    postage stamp on a fine mesh and half the part on a coarse one. A face
    only partly inside the selection is left out rather than counted in part;
    the number is deliberately the area that is definitely clamped.
    """
    faces = mesh["faces"]
    if len(faces) == 0 or len(ids) == 0:
        return 0.0
    top = int(max(int(mesh["ids"].max()), int(np.max(faces))))
    mark = np.zeros(top + 2, bool)
    mark[np.asarray(ids, int)] = True
    keep = mark[faces].all(axis=1)
    if not keep.any():
        return 0.0
    return float(ccxlib.face_areas(faces[keep], mesh["xyz"],
                                   ids=mesh["ids"]).sum())


def fix_dofs(fix):
    """A study's `"fix": "xyz"` -> CalculiX dof numbers (1-based).

    Same map as the voxel path (`loadcase._FIXMAP`), converted from that
    module's 0-based components. Passing it through to `write_inp` is what
    keeps a pin from being solved as a clamp -- the commonest way to make a
    part look stiffer than it is.
    """
    key = str(fix or "xyz").lower()
    if key not in _FIXMAP:
        raise InputError("unknown fix %r; expected one of %s"
                         % (fix, ", ".join(sorted(_FIXMAP))))
    return tuple(c + 1 for c in _FIXMAP[key])


# ------------------------------------------------------------------ gravity

def gravity_vector(spec):
    """`{"direction": [..], "g_factor": f}` -> [gx, gy, gz] mm/s^2, or None."""
    if not spec:
        return None
    d = np.asarray(spec.get("direction", [0, 0, -1]), float).reshape(3)
    n = float(np.linalg.norm(d))
    if n <= 0:
        raise InputError("gravity direction %r has no length" % (list(d),))
    return (d / n) * float(spec.get("g_factor", 1.0)) * G_MM_S2


def body_force_N(density_kg_m3, volume_mm3, gvec):
    """Weight of `volume_mm3` at `density_kg_m3` under `gvec`. -> (3,) N.

    kg/m^3 -> tonne/mm^3 is 1e-12, and a tonne*mm/s^2 is a newton, so the
    whole conversion is one factor. Checked against the real run of the
    20x10x5 PLA box: 1240 * 1e-12 * 1000 mm^3 * 9810 mm/s^2 = 0.012164 N,
    which is the weight CalculiX's own reaction accounts for.
    """
    return (np.asarray(gvec, float).reshape(3) * float(density_kg_m3)
            * 1e-12 * float(volume_mm3))


def bound_volume_mm3(mesh, fixed_ids):
    """Volume of every element with at least one fixed node. -> mm^3.

    This is the volume whose body force CalculiX nets out of the reaction it
    prints, so it is the size of the discrepancy a gravity case's equilibrium
    check has to allow for. It over-estimates that share (the fixed nodes
    carry only part of each such element), which is the safe direction for an
    allowance: too tight and every gravity case fails for being right.
    """
    el = mesh["elements"]
    if el.size == 0 or not len(fixed_ids):
        return 0.0
    top = int(max(int(mesh["ids"].max()), int(el.max())))
    mark = np.zeros(top + 2, bool)
    mark[np.asarray(list(fixed_ids), int)] = True
    touch = mark[el].any(axis=1)
    if not touch.any():
        return 0.0
    return float(ccxlib.element_volumes(mesh["xyz"], el[touch],
                                        ids=mesh["ids"]).sum())


def equilibrium_with_body_force(applied, reactions, netted_bound_N):
    """The load balance for a case that carries its own weight.

    CalculiX prints the *net* nodal force on a support set, so the share of
    the body force landing on the clamped nodes has already been subtracted
    from it: on the 20x10x5 box (weight 0.012160 N) the printed total was
    0.011046 N. Judging such a case at 1 % of the applied load therefore fails
    every gravity case for being right. The allowance is 1 % of the applied
    load **plus** that netted body force -- a quantity computed from the mesh,
    not a fudge factor -- and both numbers are recorded, so a reader can see
    which of the two carried the verdict.
    """
    eq = ccxlib.equilibrium_error(applied, reactions)
    if "residual_N" not in eq:
        return eq
    resid = float(np.linalg.norm(np.asarray(eq["residual_N"], float)))
    mag = float(np.linalg.norm(np.asarray(applied, float)))
    allow = 0.01 * mag + float(netted_bound_N)
    eq.update({"netted_bound_N": round(float(netted_bound_N), 6),
               "residual_norm_N": round(resid, 6),
               "allowance_N": round(allow, 6),
               "pass": bool(mag > 1e-12 and resid <= allow),
               "required": "<= 1 %% of the applied load + the body force on "
                           "elements touching a fixed node (%.6f N), which "
                           "CalculiX nets out of the printed total"
                           % netted_bound_N})
    return eq


# ---------------------------------------------------------------- judgement

def near_support_mask(points, fixed_points, distance_mm):
    """True where a point is within `distance_mm` of any fixed node."""
    pts = np.asarray(points, float).reshape(-1, 3)
    fx = np.asarray(fixed_points, float).reshape(-1, 3)
    if distance_mm <= 0 or not len(pts) or not len(fx):
        return np.zeros(len(pts), bool)
    from scipy.spatial import cKDTree
    d, _ = cKDTree(fx).query(pts)
    return d <= float(distance_mm)


def worst_element_exclusion(fi, points, fixed_points, distance_mm):
    """-> (safety factor to judge the worst element on, exclusion dict | None).

    A clamped node in a linear model is a mathematical singularity: the stress
    there grows without limit as the mesh is refined, so the worst element of
    a fine mesh sits at the support and never converges. `--exclude-mm` lets a
    caller say how far from the clamp the model stops describing the part --
    **and the result is always recorded**, before and after, because an
    exclusion zone is also the easiest way to make a genuinely overloaded
    corner disappear. Off by default: with no stated distance the worst
    element decides as it is.
    """
    fi = np.asarray(fi, float)
    if not len(fi):
        return None, None
    before = 1.0 / float(fi.max()) if fi.max() > 0 else None
    if distance_mm <= 0:
        return before, None
    near = near_support_mask(points, fixed_points, distance_mm)
    keep = ~near
    if not keep.any():
        return before, {
            "distance_mm": float(distance_mm),
            "excluded_nodes": int(len(fi)),
            "worst_before": None if before is None else round(before, 3),
            "worst_after": None,
            "reason": "every node is within %.3f mm of a support, so the "
                      "exclusion was not applied and the worst element stands"
                      % distance_mm}
    kept = fi[keep]
    after = 1.0 / float(kept.max()) if kept.max() > 0 else None
    return after, {
        "distance_mm": float(distance_mm),
        "excluded_nodes": int(near.sum()),
        "worst_before": None if before is None else round(before, 3),
        "worst_after": None if after is None else round(after, 3),
        "reason": "nodes within %.3f mm of a fixed node are excluded from the "
                  "worst-element check only: the stress at a clamp in a linear "
                  "model is a singularity that grows with the mesh, not a "
                  "property of the part. The p99.5 value is unchanged."
                  % distance_mm}


def judge_case(sf_p99_5, worst_element, required, equilibrium_pass,
               buckling_min=None):
    """Spec 15.1, one case. -> (pass, [notes]).

    Independent conditions, and a missing number fails rather than abstains.
    The p99.5 safety factor is never allowed to carry a case whose worst
    element is under 1.5 -- that is the spec's sentence, and it is the whole
    reason both numbers are reported.
    """
    notes, ok = [], True
    if sf_p99_5 is None:
        ok = False
        notes.append("no safety factor could be computed for this case")
    elif sf_p99_5 < required:
        ok = False
        notes.append("safety factor p99.5 %.3f is below the required %.2f"
                     % (sf_p99_5, required))
    if worst_element is None:
        ok = False
        notes.append("the worst element has no safety factor, so it cannot be "
                     "shown to clear the absolute minimum %.1f"
                     % WORST_ELEMENT_MINIMUM)
    elif worst_element < WORST_ELEMENT_MINIMUM:
        ok = False
        notes.append("the worst element is at %.3f, below the absolute "
                     "minimum %.1f -- the p99.5 value does not carry a case "
                     "whose worst element fails"
                     % (worst_element, WORST_ELEMENT_MINIMUM))
    if not equilibrium_pass:
        ok = False
        notes.append("the load/reaction balance did not close; the boundary "
                     "conditions in the deck are not the ones intended")
    if buckling_min is not None and buckling_min < required:
        ok = False
        notes.append("the lowest positive buckling factor %.3f is below the "
                     "required %.2f: the part buckles before it reaches its "
                     "design load" % (buckling_min, required))
    return ok, notes


# ------------------------------------------------------------ case building

LOAD_SPLITS = ("consistent", "equal")


def build_cases(study, mesh, map_mode="volume", ground_tol=GROUND_TOL_MM,
                density_kg_m3=None, volume_mm3=None, load_split="consistent"):
    """Study load cases -> the node sets, forces and body force CalculiX wants.

    Each returned case carries what `write_inp` reads (`name`, `supports`,
    `loads`, `gravity`) plus a `report` dict of what a reader needs in order
    to check that the mapping did what it looks like: which region, in which
    mode, how many nodes, and how much area a support actually holds.

    The total force of a region is spread over the nodes it contains with the
    quadratic element's consistent weights (`ccxlib.load_weights`), or
    equally with `load_split="equal"`; either way the resultant matches the
    study whatever the mesh density -- which is what makes the equilibrium
    check meaningful rather than circular.
    """
    if load_split not in LOAD_SPLITS:
        raise InputError("--load-split must be one of %s, got %r"
                         % ("/".join(LOAD_SPLITS), load_split))
    out = []
    for case in study.cases:
        supports, sup_report, fixed = [], [], []
        for sup in case.supports:
            nm = sup.get("region")
            got = region_nodes(mesh, nm, study.regions, map_mode)
            info = {"region": nm, "mode": map_mode,
                    "fix": str(sup.get("fix", "xyz")).lower()}
            if str(sup.get("contact", "")).lower() == "ground":
                got, plane = ground_nodes(mesh, got, study.build_axis,
                                          ground_tol)
                info.update({"contact": "ground",
                             "plane_mm": round(plane, 4),
                             "ground_tol_mm": float(ground_tol),
                             "axis": "XYZ"[study.build_axis]})
                if not len(got):
                    raise InputError(
                        "support region %r asks for ground contact, but no "
                        "node of it lies on the contact plane %s = %.3f mm "
                        "(within %.3f mm). The region is above the plane, so "
                        "what it describes is standing on nothing -- clamping "
                        "it anyway would certify a floating foot."
                        % (nm, "XYZ"[study.build_axis], plane, ground_tol))
            if not len(got):
                raise InputError(
                    "support region %r contains no %s mesh node. Either the "
                    "region is outside the part or the mesh is too coarse to "
                    "resolve it -- both make the support silently absent."
                    % (nm, map_mode))
            dofs = fix_dofs(sup.get("fix"))
            # `region` is carried through so the per-support reaction
            # rows can name the foot rather than number it.
            supports.append({"nodes": [int(v) for v in got],
                             "dofs": dofs, "region": nm})
            fixed.extend(int(v) for v in got)
            info.update({"nodes": int(len(got)), "dofs": list(dofs),
                         "area_mm2": round(selected_area_mm2(mesh, got), 4)})
            sup_report.append(info)

        loads, pts, load_report = [], [], []
        for ld in case.loads:
            nm = ld.get("region")
            got = region_nodes(mesh, nm, study.regions, map_mode)
            if not len(got):
                raise InputError(
                    "load region %r contains no %s mesh node; the case would "
                    "be solved with that load missing" % (nm, map_mode))
            total = np.asarray(ld.get("force", [0, 0, 0]), float)
            weights, method = ({}, "equal")
            if load_split == "consistent":
                weights, method = ccxlib.load_weights(
                    mesh["xyz"], mesh["elements"], got, map_mode,
                    ids=mesh["ids"])
            if not weights:
                weights = {int(n): 1.0 / len(got) for n in got}
                method = ("equal split over %d nodes" % len(got)
                          + ("" if load_split == "equal" else
                             " (no element lies fully inside the region, "
                             "so the consistent weights are empty)"))
            for n, wgt in weights.items():
                loads.append((int(n), tuple(total * wgt)))
                pts.append(mesh["xyz"][mesh["index"][int(n)]])
            load_report.append({"region": nm, "mode": map_mode,
                                "nodes": int(len(got)),
                                "loaded_nodes": int(len(weights)),
                                "split": method,
                                "force_N": [round(float(v), 4) for v in total]})

        grav = gravity_vector(case.gravity)
        grav_report = None
        if grav is not None:
            if not density_kg_m3:
                grav_report = {
                    "note": "the material card has no density, so the body "
                            "force was not applied and this case is solved "
                            "without its own weight"}
                grav = None
            else:
                w = body_force_N(density_kg_m3, volume_mm3 or 0.0, grav)
                grav_report = {
                    "vector_mm_s2": [round(float(v), 3) for v in grav],
                    "density_kg_m3": float(density_kg_m3),
                    "mass_g": round(float(density_kg_m3) * 1e-6
                                    * float(volume_mm3 or 0.0), 5),
                    "weight_N": [round(float(v), 6) for v in w],
                    "note": "the solid material's density. The voxel FEA "
                            "derates it by the infill fill factor and this "
                            "path does not, so the two disagree about the "
                            "weight of a gravity case by that factor -- a "
                            "real difference to expect when comparing drop "
                            "cases."}
        if not loads and grav is None:
            raise InputError("case %r has no loads" % case.name)

        out.append({"name": case.name, "supports": supports,
                    "fixed_nodes": sorted(set(fixed)), "loads": loads,
                    "load_points": pts,
                    "gravity": None if grav is None
                    else [float(v) for v in grav],
                    "load_class": case.load_class,
                    "required_safety_factor":
                        study.required_safety_factor(case),
                    "report": {"supports": sup_report, "loads": load_report,
                               "gravity": grav_report,
                               "load_distribution":
                                   "each region's total force is spread over "
                                   "its %s nodes (%s weights): the resultant "
                                   "is exact, the distribution is uniform "
                                   "over what the region selects"
                                   % (map_mode, load_split)}})
    return out


def support_key(case):
    """What makes two cases share a frequency deck: the same clamp.

    Modes are a property of mass and stiffness alone, so cases differing only
    in their loads have the same modes -- solving them twice is minutes of
    CalculiX for an identical answer.
    """
    return tuple(sorted((tuple(s["dofs"]), tuple(sorted(s["nodes"])))
                        for s in case["supports"]))


# ------------------------------------------------------------------ the run

def prepare_shell(stl, outdir, repair=False, quiet=False):
    """Look at the STL before Gmsh does. -> (stl to mesh, shell info dict).

    A shell that is not closed is refused (exit code 2) unless `repair`,
    in which case MeshFix rebuilds it into `outdir` and the repaired file is
    what gets meshed -- and the summary says so.
    """
    report = ccxlib.shell_check(stl)
    info = {"stl": stl, "shell": report, "repaired": False}
    if report.get("closed_shell"):
        return stl, info
    if not repair:
        raise InputError(
            "%s is not a closed shell (%d degenerate triangle(s), %d "
            "non-manifold edge(s), %d open edge(s)). Gmsh cannot build a "
            "volume mesh on it -- re-run with --repair to let MeshFix "
            "(pymeshfix) rebuild the shell, or export a clean STL."
            % (stl, report.get("degenerate", 0),
               report.get("non_manifold_edges", 0), report.get("open_edges", 0)))
    base = os.path.splitext(os.path.basename(stl))[0]
    dst = os.path.join(outdir, base + "_repaired.stl")
    fix = ccxlib.repair_stl(stl, dst)
    if not fix["after"].get("closed_shell"):
        raise InputError(
            "%s could not be repaired into a closed shell (after MeshFix: %s)"
            % (stl, fix["after"]))
    if not quiet:
        print("  shell repaired: %d -> %d triangles, volume %.1f -> %.1f mm3 "
              "(%s)" % (fix["before"]["triangles"], fix["after"]["triangles"],
                        fix["volume_before_mm3"], fix["volume_after_mm3"],
                        os.path.basename(dst)))
    info.update({"repaired": True, "repair": fix, "meshed_stl": dst})
    return dst, info


def _extra_deck(path, mesh_inp, block, case, kind):
    """Write, run and read one extra deck (buckling or frequency).

    -> dict with `buckling_factors` / `frequencies_hz`, or a `note` saying why
    there are none. A deck CalculiX refuses is reported as a note rather than
    ending the run: the static answers are already in hand, and losing them to
    a failed eigenvalue solve would report less than was actually measured.
    """
    try:
        ccxlib.write_inp(path, mesh_inp, block, [case], analyses=(kind,))
        out, base = ccxlib.run_ccx(path)
        ok, why = ccxlib.converged(out)
        dat = ccxlib.parse_dat(base + ".dat")
    except (RuntimeError, ValueError) as exc:
        return {"note": "the %s deck did not solve: %s" % (kind, exc),
                "inp": path}
    got = {"inp": path}
    if not ok:
        got["note"] = "the %s solve did not converge: %s" % (kind, why)
        return got
    if kind == "buckle":
        got["buckling_factors"] = [round(float(v), 5)
                                   for v in dat["buckling_factors"]]
        pos = [v for v in got["buckling_factors"] if v > 0]
        got["buckling_factor_min"] = round(min(pos), 5) if pos else None
        if not pos:
            # Not a failure and not a zero: under this load direction none of
            # the modes asked for buckles at all.
            got["note"] = ("no positive buckling factor among the modes "
                           "computed; the part does not buckle under this "
                           "load direction")
    else:
        got["frequencies_hz"] = [round(float(v), 4)
                                 for v in dat["frequencies_hz"]]
    return got


def analyse(stl, study, outdir, target_mm=None, isotropic=False,
            buckle=False, frequency=False, map_mode="volume",
            ground_tol=GROUND_TOL_MM, exclude_mm=0.0, quiet=False,
            shell_info=None, refine=True, load_split="consistent"):
    """One mesh density, one static CalculiX job, one summary dict.

    The static deck is one `*STEP` per case, so frd step k is case k and the
    dat's k-th reaction total is that case's. Buckling and frequency get
    **their own decks**: a deck carrying three analyses per case writes one
    frd step for the static solve, one for the buckling preload, one per
    buckling mode and one per natural mode, and no fixed stride maps those
    back onto the cases -- which is how the case index quietly became wrong.
    """
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(stl))[0]

    if isotropic:
        from topopt.materials import get_material
        mat, ortho = get_material(study.material), None
        mat_info = material_model(study.material, isotropic=True)[2]
        density_kg_m3 = mat.density * 1000.0
    else:
        study.require_build_axis("the CalculiX re-analysis")
        mat, ortho, mat_info = material_model(study.material)
        density_kg_m3 = ortho.density_kg_m3

    if target_mm is None:
        target_mm = float(study.resolution_mm or 2.0)
    tag = "%s_h%.2f" % (base, target_mm)
    geo = os.path.join(outdir, tag + ".geo")
    mesh_path = os.path.join(outdir, tag + "_mesh.inp")
    # Gmsh keeps the STL's triangles as the surface mesh, so the target size
    # has to be imposed on the shell first or the boundary -- where the
    # stress is read and the supports are mapped -- never gets finer than
    # the CAD export (see `ccxlib.write_geo`).
    refinement = None
    mesh_stl = stl
    if refine:
        mesh_stl = os.path.join(outdir, tag + "_shell.stl")
        refinement = ccxlib.refine_stl(stl, mesh_stl, target_mm)
        if not quiet:
            print("  shell refined to %.2f mm edges: %d -> %d triangles"
                  % (target_mm, refinement["triangles_before"],
                     refinement["triangles_after"]))
    ccxlib.write_geo(geo, mesh_stl, target_mm)
    ccxlib.run_gmsh(geo, mesh_path)

    mesh = mesh_context(mesh_path)
    nel = len(mesh["elements"])
    if not quiet:
        print("  mesh %s: %d nodes, %d elements at %.2f mm"
              % (os.path.basename(mesh_path), len(mesh["ids"]), nel, target_mm))

    quality = ccxlib.mesh_quality(mesh["xyz"], mesh["elements"],
                                  ids=mesh["ids"])
    mprops = ccxlib.mass_properties(mesh["xyz"], mesh["elements"],
                                    density_kg_m3, ids=mesh["ids"])

    cases = build_cases(study, mesh, map_mode=map_mode, ground_tol=ground_tol,
                        density_kg_m3=density_kg_m3,
                        volume_mm3=mprops["volume_mm3"],
                        load_split=load_split)
    block = ccxlib.material_block(
        study.material.upper(), ortho=ortho,
        E=None if ortho is not None else mat.E_effective(across_layers=False),
        nu=None if ortho is not None else mat.nu,
        density_kg_m3=None if ortho is not None else density_kg_m3,
        build_axis=study.build_axis, raster_axis=study.raster_axis)
    inp = os.path.join(outdir, tag + ".inp")
    ccxlib.write_inp(inp, mesh_path, block, cases, analyses=("static",))

    out, cbase = ccxlib.run_ccx(inp)
    ok, why = ccxlib.converged(out)
    res = ccxlib.parse_frd(cbase + ".frd")
    dat = ccxlib.parse_dat(cbase + ".dat")

    summary = {"mesh_target_mm": target_mm, "nodes": len(mesh["ids"]),
               "elements": nel, "converged": ok, "convergence_note": why,
               "material_model": mat_info,
               "build_axis": "XYZ"[study.build_axis],
               "map_mode": map_mode, "load_split": load_split,
               "ground_tol_mm": float(ground_tol),
               "exclude_mm": float(exclude_mm),
               "mesh_quality": quality, "mass_properties": mprops,
               "density_kg_m3": density_kg_m3,
               "inp": inp, "frd": cbase + ".frd",
               "stl_meshed": mesh_stl,
               "surface_refinement": refinement or {
                   "note": "disabled with --no-refine: the surface mesh is "
                           "the STL's own triangles and does not change "
                           "with the target size"}}
    if shell_info:
        summary["shell"] = shell_info
    if not ok:
        # Spec 19.4: a non-converged solve is never shown as a pass.
        summary["pass"] = False
        summary["error"] = why
        return summary

    # ---- the extra decks, one physical question each
    freq_sets = []
    if (buckle or frequency) and not density_kg_m3:
        note = ("skipped: the %s material card has no density, and a buckling "
                "factor or a natural frequency without mass is not zero, it "
                "is unanswerable" % study.material)
        if buckle:
            summary["buckling_note"] = note
        if frequency:
            summary["frequencies_note"] = note
    else:
        if buckle:
            for i, case in enumerate(cases, 1):
                p = os.path.join(outdir, "%s_buckle_%d.inp" % (tag, i))
                case["buckle"] = _extra_deck(p, mesh_path, block, case,
                                             "buckle")
        if frequency:
            seen = {}
            for case in cases:
                seen.setdefault(support_key(case), []).append(case)
            groups = sorted(seen.values(), key=lambda g: g[0]["name"])
            for j, group in enumerate(groups, 1):
                p = os.path.join(outdir, "%s_freq_%d.inp" % (tag, j))
                deck = {"name": "modes %d" % j,
                        "supports": group[0]["supports"], "loads": []}
                got = _extra_deck(p, mesh_path, block, deck, "frequency")
                got["cases"] = [c["name"] for c in group]
                freq_sets.append(got)
                for c in group:
                    c["frequency"] = got

    per_case = []
    for i, case in enumerate(cases):
        st = res["steps"][i] if i < len(res["steps"]) else None
        c = {"case": case["name"], "step": i + 1,
             "load_class": case["load_class"],
             "required_safety_factor": case["required_safety_factor"],
             "worst_element_minimum": WORST_ELEMENT_MINIMUM,
             "mapping": case["report"]}
        if st is None:
            c.update({"error": "no results for this step in the .frd",
                      "pass": False})
            per_case.append(c)
            continue
        _case_results(c, case, st, res, mesh, ortho, study, exclude_mm,
                      dat, i, density_kg_m3, mprops["volume_mm3"])
        per_case.append(c)
    summary["cases"] = per_case
    if freq_sets:
        summary["frequencies_hz"] = freq_sets

    # The part-level numbers are the governing case's, and say which. The
    # governing case is the one closest to *its own* bar, not the one with the
    # smallest number: an abnormal case judged at 1.5 and a normal one judged
    # at 3.0 are not comparable as raw safety factors (spec 15.1).
    judged = [c for c in per_case if c.get("safety_factor_p99_5") is not None]
    if judged:
        gov = min(judged, key=lambda c: c["safety_factor_p99_5"]
                  / max(c.get("required_safety_factor") or 1.0, 1e-9))
        summary["governing_case"] = gov["case"]
        for key in ("safety_factor_p99_5", "worst_element_safety_factor",
                    "hotspot", "failure_criterion", "von_mises_p99_5_MPa",
                    "required_safety_factor", "load_class"):
            if key in gov:
                summary[key] = gov[key]
        # What `mesh_convergence` judges the stress on: the percentile, not
        # the maximum, and the hotspot it has to see stop moving.
        summary["p99_5_stress_MPa"] = gov.get("von_mises_p99_5_MPa")
        summary["hotspot_xyz_mm"] = (gov.get("hotspot") or {}).get("xyz_mm")
    disps = [c["max_disp_mm"] for c in per_case if c.get("max_disp_mm")]
    summary["max_disp_mm"] = max(disps) if disps else None
    vms = [c["von_mises_max_MPa"] for c in per_case
           if c.get("von_mises_max_MPa")]
    if vms:
        summary["von_mises_max_MPa"] = max(vms)
        summary["max_stress_MPa"] = max(vms)
    eqs = [c["equilibrium"] for c in per_case if c.get("equilibrium")]
    if eqs:
        # The worst one, so a run-level reader cannot see a balance that
        # closed on one case and miss the one that did not.
        summary["equilibrium"] = max(
            eqs, key=lambda e: e.get("relative_error", float("inf"))
            if e.get("pass") else float("inf"))
    bmins = [c["buckling_factor_min"] for c in per_case
             if c.get("buckling_factor_min") is not None]
    if bmins:
        summary["buckling_factor_min"] = min(bmins)

    summary["pass"] = bool(ok and per_case and quality.get("pass")
                           and all(c["pass"] for c in per_case))
    if not quality.get("pass"):
        # Spec 19.4: a stress read off a mesh full of slivers is a property of
        # its worst elements, not of the part, so the run cannot pass on it.
        summary["error"] = quality.get("note")
    if not quiet:
        for c in per_case:
            print("  %-38s %-8s req %4.2f  p99.5 %7s  worst %7s  max %8s mm  %s"
                  % (c["case"][:38], c.get("load_class", "?"),
                     c.get("required_safety_factor", 0),
                     _fmt(c.get("safety_factor_p99_5")),
                     _fmt(c.get("worst_element_safety_factor")),
                     _fmt(c.get("max_disp_mm"), "%.4f"),
                     "PASS" if c.get("pass") else "FAIL"))
            for n in c.get("notes") or ():
                print("      - %s" % n)
    return summary


def _fmt(v, fmt="%.3f"):
    return "?" if v is None else fmt % v


def _reaction_by_support(case, st, res):
    """Force and moment carried by each support set of one case. -> list

    The sets are the ones the deck actually wrote (`supports`, else the
    single `fixed_nodes` clamp), so the rows line up with the `*NSET`s in the
    `.inp` and with the `*NODE PRINT` totals CalculiX echoes for them.
    """
    reaction = st.get("reaction") or {}
    sets = case.get("supports") or [{"name": "FIXED",
                                     "nodes": case.get("fixed_nodes") or []}]
    out = []
    for k, s in enumerate(sets, 1):
        ids = [n for n in (s.get("nodes") if isinstance(s, dict) else s)
               if n in reaction]
        pts = [res["nodes"][n] for n in ids if n in res["nodes"]]
        vals = [reaction[n] for n in ids if n in res["nodes"]]
        name = s.get("region") if isinstance(s, dict) else None
        row = {"name": str(name) if name else "FIXED%d" % k,
               "nodes": len(ids)}
        if vals:
            v = np.array(vals, float)
            row["force_N"] = [round(float(x), 5) for x in v.sum(axis=0)]
            row["moment_Nmm"] = [
                round(float(x), 4)
                for x in np.cross(np.array(pts, float), v).sum(axis=0)]
        else:
            row["note"] = "no reaction was written for this set's nodes"
        out.append(row)
    return out


def _case_results(c, case, st, res, mesh, ortho, study, exclude_mm, dat, i,
                  density_kg_m3, volume_mm3):
    """Fill one case's numbers (spec 14.3) into `c`."""
    ids_u = list(st["displacement"].keys())
    disp = (np.array([st["displacement"][n] for n in ids_u])
            if ids_u else np.zeros((0, 3)))
    c["max_disp_mm"] = None
    if len(disp):
        mag = np.linalg.norm(disp, axis=1)
        k = int(np.argmax(mag))
        c["max_disp_mm"] = round(float(mag[k]), 5)
        # The per-axis maxima are not the components of the largest vector:
        # a part can move furthest at one node and furthest sideways at
        # another, and a clearance check needs the envelope, not one node.
        c["max_disp_components_mm"] = [round(float(v), 5)
                                       for v in np.abs(disp).max(axis=0)]
        pos = res["nodes"].get(ids_u[k])
        c["max_disp_at_mm"] = (None if pos is None
                               else [round(float(v), 3) for v in pos])

    ids_s = list(st["stress"].keys())
    sig = (np.array([st["stress"][n] for n in ids_s]) if ids_s
           else np.zeros((0, 6)))
    if len(sig):
        vm = von_mises(sig)
        c["von_mises_max_MPa"] = round(float(vm.max()), 4)
        c["von_mises_p99_5_MPa"] = round(float(np.percentile(vm, 99.5)), 4)
        # Spec 14.3 asks for the six components themselves, not only the
        # invariants built from them. The extreme of each one, signed: a
        # +40 MPa sigma_z and a -40 MPa sigma_z are the same von Mises and
        # very different things to a printed part.
        c["stress_components_MPa"] = {
            name: [round(float(sig[:, k].min()), 4),
                   round(float(sig[:, k].max()), 4)]
            for k, name in enumerate(("sxx", "syy", "szz",
                                      "txy", "tyz", "tzx"))}
        pr = ccxlib.principal_stresses(sig)
        c["principal_max_MPa"] = round(float(pr[:, 0].max()), 4)
        c["principal_min_MPa"] = round(float(pr[:, 2].min()), 4)
        c["tresca_max_MPa"] = round(float(ccxlib.tresca(sig).max()), 4)
        both = [n for n in ids_s if n in st["strain"]]
        if both:
            eps = np.array([st["strain"][n] for n in both])
            u = ccxlib.strain_energy_density(
                np.array([st["stress"][n] for n in both]), eps)
            c["strain_energy_density_max_MPa"] = round(float(u.max()), 6)
            # Spec 14.3 lists principal *strains* as well as principal
            # stresses. For an orthotropic card they are not parallel to the
            # principal stresses, which is the reason to report both.
            pe = ccxlib.principal_strains(eps)
            c["principal_strain_max"] = float("%.6g" % pe[:, 0].max())
            c["principal_strain_min"] = float("%.6g" % pe[:, 2].min())
        if ortho is not None:
            fi, modes = max_stress_failure_index(
                sig, ortho, study.build_axis, study.raster_axis, per_mode=True)
            p = float(np.percentile(fi, 99.5))
            c["failure_criterion"] = "max_stress_material_axes"
            c["failure_index_max"] = round(float(fi.max()), 5)
            c["failure_index_p99_5"] = round(p, 5)
            c["safety_factor_p99_5"] = round(1.0 / p, 3) if p > 0 else None
            # Spec 14.3, material-coordinate row: the in-plane, layer-normal
            # and interlaminar stresses each against their own allowable,
            # each with its own safety factor. The aggregated index above
            # says how close the part is; these say *which layer mechanism*
            # is what it is close to, which is the number that decides
            # whether to turn the part or to add material.
            from topopt.hex8 import (material_rotation,
                                     rotate_stress_to_material)
            sm = rotate_stress_to_material(
                sig, material_rotation(study.build_axis, study.raster_axis))
            c["material_axis_stress_MPa"] = {
                name: [round(float(sm[:, k].min()), 4),
                       round(float(sm[:, k].max()), 4)]
                for k, name in enumerate(
                    ("raster", "in_plane_transverse", "interlayer_normal",
                     "in_plane_shear", "interlayer_shear_yz",
                     "interlayer_shear_zx"))}
            c["failure_modes"] = {
                name: {"failure_index_max": round(float(v.max()), 5),
                       "safety_factor": (round(1.0 / float(v.max()), 3)
                                         if v.max() > 0 else None)}
                for name, v in modes.items()}
            c["governing_failure_mode"] = max(
                modes, key=lambda k: float(modes[k].max()))
            pts = np.array([res["nodes"].get(n, (0.0, 0.0, 0.0))
                            for n in ids_s], float)
            fixed_pts = np.array(
                [mesh["xyz"][mesh["index"][n]] for n in case["fixed_nodes"]
                 if n in mesh["index"]], float)
            worst, excl = worst_element_exclusion(fi, pts, fixed_pts,
                                                  exclude_mm)
            c["worst_element_safety_factor"] = (None if worst is None
                                                else round(worst, 3))
            if excl:
                c["exclusion"] = excl
            # Where to go and look, not just how bad it is (spec 19.4).
            worst_id = ids_s[int(np.argmax(fi))]
            pos = res["nodes"].get(worst_id)
            c["hotspot"] = {"node": worst_id,
                            "xyz_mm": None if pos is None
                            else [round(float(v), 3) for v in pos],
                            "failure_index": round(float(fi.max()), 4)}
        else:
            # Match the legacy voxel criterion when --isotropic is used.
            # Previously stress existed but every safety factor was absent.
            from topopt.hex8 import layer_failure_index
            from topopt.materials import get_material
            mat = get_material(study.material)
            fi = layer_failure_index(sig, mat.sigma_xy, mat.sigma_z, study.build_axis)
            p = float(np.percentile(fi, 99.5))
            c["failure_criterion"] = "layer_two_constant"
            c["failure_index_max"] = float(fi.max())
            c["failure_index_p99_5"] = p
            c["safety_factor_p99_5"] = round(1 / p, 3) if p > 0 else None
            pts = np.array([res["nodes"].get(n, (0, 0, 0)) for n in ids_s], float)
            fixed_pts = np.array([mesh["xyz"][mesh["index"][n]]
                                  for n in case["fixed_nodes"] if n in mesh["index"]], float)
            worst, excl = worst_element_exclusion(fi, pts, fixed_pts, exclude_mm)
            c["worst_element_safety_factor"] = round(worst, 3) if worst is not None else None
            if excl:
                c["exclusion"] = excl
            k = int(np.argmax(fi))
            c["hotspot"] = {"node": ids_s[k], "xyz_mm": pts[k].round(3).tolist(),
                            "failure_index": round(float(fi[k]), 4)}

    # ---- what was applied, and what came back
    applied = np.zeros(3)
    for _n, f in case["loads"]:
        applied += np.asarray(f, float)
    grav = case.get("gravity")
    netted = 0.0
    if grav is not None:
        applied = applied + body_force_N(density_kg_m3, volume_mm3, grav)
        netted = (float(np.linalg.norm(grav)) * float(density_kg_m3) * 1e-12
                  * bound_volume_mm3(mesh, case["fixed_nodes"]))
    c["applied_N"] = [round(float(v), 5) for v in applied]

    fixed = set(case["fixed_nodes"])
    rpts, rvals = [], []
    for n, f in (st.get("reaction") or {}).items():
        # The frd FORC block holds the applied load at a loaded node and the
        # reaction at a fixed one, so the reaction is the part of it that sits
        # on this case's own support set.
        if n in fixed:
            p = res["nodes"].get(n)
            if p is not None:
                rpts.append(p)
                rvals.append(f)
    if rvals:
        rv = np.array(rvals, float)
        c["reaction_total_N"] = [round(float(v), 5) for v in rv.sum(axis=0)]
        mom = np.cross(np.array(rpts, float), rv).sum(axis=0)
        c["reaction_moment_Nmm"] = [round(float(v), 4) for v in mom]
        # Spec 14.3 wants the reaction and its moment, and a case can be held
        # by several support sets in different directions. One grand total
        # hides the case this is for: a two-foot stance where one foot carries
        # the whole load and the other is along for the ride reads exactly
        # like a balanced one.
        c["reaction_by_support"] = _reaction_by_support(case, st, res)
        me = ccxlib.moment_error(
            case["load_points"], [f for _n, f in case["loads"]], rpts, rvals)
        # A moment balance measured about a point the load passes through is a
        # percentage of nothing: osmo_tripod's LC2 pushes straight down through
        # the origin, so its applied moment is 2 N*mm and a residual of
        # 0.05 N*mm reads as 2.4 % "failed" while the force balance closes to
        # 0.3 %. The verdict is left alone and the reason is written down --
        # inventing a scale for it here would be a criterion nobody chose.
        arm = max([float(np.linalg.norm(p)) for p in list(rpts)
                   + list(case["load_points"])] or [0.0])
        ref = float(np.linalg.norm(applied)) * arm
        if ref > 0 and float(np.linalg.norm(me["applied_Nmm"])) < 0.01 * ref:
            me["note"] = (
                "the applied moment about the origin is %.3g N*mm, near zero "
                "because the loads pass through it, so `relative_error` is a "
                "ratio to nothing; read `residual_Nmm` (%s) against the "
                "%.3g N*mm this load makes at the far end of the part"
                % (float(np.linalg.norm(me["applied_Nmm"])),
                   me["residual_Nmm"], ref))
        c["moment_error"] = me

    comp = 0.0
    for n, f in case["loads"]:
        u = st["displacement"].get(n)
        if u is not None:
            comp += float(np.dot(np.asarray(f, float), np.asarray(u, float)))
    c["compliance_Nmm"] = round(comp, 6)

    if i >= len(dat["reaction_totals"]):
        c["equilibrium"] = {
            "error": "CalculiX wrote no reaction totals for this step; "
                     "the balance could not be checked", "pass": False}
    elif grav is not None:
        c["equilibrium"] = equilibrium_with_body_force(
            applied, dat["reaction_totals"][i], netted)
    else:
        c["equilibrium"] = ccxlib.equilibrium_error(
            applied, dat["reaction_totals"][i])

    bk = case.get("buckle")
    if bk:
        if "buckling_factors" in bk:
            c["buckling_factors"] = bk["buckling_factors"]
            c["buckling_factor_min"] = bk.get("buckling_factor_min")
        if bk.get("note"):
            c["buckling_note"] = bk["note"]
    fq = case.get("frequency")
    if fq and "frequencies_hz" in fq:
        c["frequencies_hz"] = fq["frequencies_hz"]

    ok, notes = judge_case(c.get("safety_factor_p99_5"),
                           c.get("worst_element_safety_factor"),
                           c["required_safety_factor"],
                           bool(c["equilibrium"].get("pass")),
                           c.get("buckling_factor_min"))
    c["pass"] = ok
    if notes:
        c["notes"] = notes
    return c


def main(argv=None):
    ap = argparse.ArgumentParser(prog="topopt ccx", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl", nargs="?", help="the shape to re-analyse")
    ap.add_argument("--study", help="study defining loads, supports, material")
    ap.add_argument("--out", default="ccx_out")
    ap.add_argument("--mesh-mm", type=float, help="target element size")
    ap.add_argument("--converge", action="store_true",
                    help="run three mesh densities and judge convergence")
    ap.add_argument("--buckle", action="store_true",
                    help="add a linear buckling deck per case")
    ap.add_argument("--frequency", action="store_true",
                    help="add an eigenvalue (natural frequency) deck per "
                         "distinct support set")
    ap.add_argument("--map", choices=MAP_MODES, default="volume",
                    dest="map_mode",
                    help="whether a region selects every node inside it "
                         "(default; the same boundary condition the voxel "
                         "FEA applies, spec 14.4) or only surface nodes")
    ap.add_argument("--load-split", choices=LOAD_SPLITS, default="consistent",
                    help="how a region's force is spread over its nodes: "
                         "the quadratic element's consistent weights "
                         "(default) or an equal share per node")
    ap.add_argument("--ground-tol", type=float, default=GROUND_TOL_MM,
                    help="how far above the lowest plane a node may sit and "
                         "still count as touching the ground (mm)")
    ap.add_argument("--exclude-mm", type=float, default=0.0,
                    help="exclude nodes within this distance of a fixed node "
                         "from the worst-element check only (0 = off); the "
                         "result is recorded before and after")
    ap.add_argument("--isotropic", action="store_true")
    ap.add_argument("--voxel-disp", type=float,
                    help="max displacement from the voxel FEA, to compare "
                         "against (spec 14.4)")
    ap.add_argument("--voxel-fea",
                    help="a *_fea.json from `topopt fea`, to compare the two "
                         "solvers case by case (spec 14.4)")
    ap.add_argument("--no-refine", action="store_true",
                    help="mesh the STL's own triangles as the surface "
                         "instead of splitting its edges down to the target "
                         "size first (the surface then ignores --mesh-mm)")
    ap.add_argument("--repair", action="store_true",
                    help="rebuild an STL that is not a closed shell with "
                         "MeshFix (needs the optional pymeshfix package) "
                         "before meshing; the summary records what changed")
    ap.add_argument("--check", action="store_true",
                    help="report which external tools are available and stop")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.check:
        s = survey()
        print("external tools:")
        for k in ("gmsh", "calculix", "freecad", "openscad", "slicer"):
            v = s[k]
            print("  %-9s %-9s %s" % (k, "found" if v["available"] else "MISSING",
                                      v["path"] or ""))
        missing = [k for k in ("gmsh", "calculix") if not s[k]["available"]]
        if missing:
            print("\n`topopt ccx` needs %s." % " and ".join(missing))
            print("Run `topopt ccx <stl> --study <study>` to see how to "
                  "install them.")
            return 3
        return 0

    if not a.stl or not a.study:
        ap.error("both the STL and --study are required (or use --check)")

    # Fail on a missing tool before meshing, voxelising or anything else that
    # takes minutes -- the answer is the same and it arrives in a second.
    for tool in ("gmsh", "calculix"):
        if find(tool) is None:
            raise ToolMissing(tool, "the CalculiX re-analysis")

    study = load_case_from_json(a.study)
    from topopt.provenance import sha256_file
    input_stl_hash = sha256_file(a.stl)
    input_study_hash = sha256_file(a.study)

    base = float(a.mesh_mm or study.resolution_mm or 2.0)
    sizes = [base * 2, base, base / 2] if a.converge else [base]
    os.makedirs(a.out, exist_ok=True)
    try:
        stl, shell_info = prepare_shell(a.stl, a.out, repair=a.repair,
                                        quiet=a.quiet)
    except ImportError as exc:
        # The repair tool is optional, like the external ones: exit code 3,
        # with the install line, not a traceback.
        print("ccx: %s" % exc, file=sys.stderr)
        return 3
    runs = []
    for h in sizes:
        if not a.quiet:
            print("\nmesh target %.2f mm" % h)
        runs.append(analyse(stl, study, a.out, h, a.isotropic,
                            buckle=a.buckle, frequency=a.frequency,
                            map_mode=a.map_mode, ground_tol=a.ground_tol,
                            exclude_mm=a.exclude_mm, quiet=a.quiet,
                            shell_info=shell_info, refine=not a.no_refine,
                            load_split=a.load_split))

    out = {"schema": "topopt/ccx/1", "stl": os.path.abspath(a.stl),
           "stl_sha256": input_stl_hash, "study_sha256": input_study_hash,
           "study": a.study,
           "map_mode": a.map_mode, "runs": runs}
    if a.converge:
        out["mesh_convergence"] = ccxlib.mesh_convergence(
            [{"target_mm": r["mesh_target_mm"], "nodes": r["nodes"],
              "max_disp_mm": r.get("max_disp_mm"),
              "p99_5_stress_MPa": r.get("p99_5_stress_MPa"),
              "hotspot_xyz_mm": r.get("hotspot_xyz_mm"),
              "max_stress_MPa": r.get("max_stress_MPa")} for r in runs])
    if a.voxel_disp is not None and runs[-1].get("max_disp_mm"):
        out["vs_voxel_fea"] = ccxlib.compare_with_voxel(
            a.voxel_disp, runs[-1]["max_disp_mm"])
    if a.voxel_fea:
        out["vs_voxel_fea_cases"] = _compare_voxel_fea(a.voxel_fea, runs[-1])

    p = os.path.join(a.out, "ccx_summary.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    if not a.quiet:
        _print_tail(out, runs, p)
    ok = all(r.get("pass") for r in runs)
    if a.converge:
        ok = ok and out["mesh_convergence"].get("pass")
    return 0 if ok else 1


def _compare_voxel_fea(path, run):
    """Spec 14.4 case by case, against a `*_fea.json` from `topopt fea`."""
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError) as exc:
        raise InputError("--voxel-fea %s could not be read: %s" % (path, exc))
    cases = blob.get("cases")
    if not isinstance(cases, list):
        raise InputError(
            "--voxel-fea %s has no `cases` list; it does not look like a "
            "*_fea.json written by `topopt fea`" % path)
    got = ccxlib.compare_cases(cases, run.get("cases") or [])
    got["voxel_fea"] = path
    return got


def _print_tail(out, runs, path):
    print("\nwrote %s" % path)
    for r in runs:
        print("  %.2f mm: %s  SF %s  max %s mm  mass %s g"
              % (r["mesh_target_mm"],
                 "PASS" if r.get("pass") else "FAIL",
                 r.get("safety_factor_p99_5"), r.get("max_disp_mm"),
                 (r.get("mass_properties") or {}).get("mass_g")))
        q = r.get("mesh_quality") or {}
        if q and not q.get("pass"):
            print("    mesh quality: %s" % q.get("note"))
        for fs in r.get("frequencies_hz") or ():
            hz = fs.get("frequencies_hz") or []
            print("    modes of {%s}: %s"
                  % (", ".join(c[:20] for c in fs.get("cases", [])),
                     ", ".join("%.1f Hz" % v for v in hz[:3])
                     or fs.get("note", "none")))
        if r.get("buckling_factor_min") is not None:
            print("    lowest buckling factor: %.3f"
                  % r["buckling_factor_min"])
        elif r.get("buckling_note"):
            print("    buckling: %s" % r["buckling_note"])
    if "vs_voxel_fea" in out:
        v = out["vs_voxel_fea"]
        print("  vs voxel FEA: %.1f %% difference -- %s"
              % (100 * v["relative_difference"], v["note"]))
    if "vs_voxel_fea_cases" in out:
        v = out["vs_voxel_fea_cases"]
        print("  vs voxel FEA, case by case: %d of %d within %.0f %%"
              % (v["within"], v["total"], 100 * v["tolerance"]))
        for row in v["cases"]:
            print("    %-38s voxel %8s  ccx %8s  %s"
                  % (str(row["case"])[:38],
                     _fmt(row.get("voxel_max_disp_mm"), "%.4f"),
                     _fmt(row.get("ccx_max_disp_mm"), "%.4f"),
                     "ok" if row.get("pass") else "DIFFERS"))


if __name__ == "__main__":
    sys.exit(main())

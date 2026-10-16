"""`topopt ccx` as a command: how regions become boundary conditions, how a
case is judged, and one real end-to-end run.

`test_ccx.py` covers the deck and the parsers as text and `test_ccx_mesh.py`
covers the mesh algebra. What is left -- and what actually decides whether a
part passes -- lives in the CLI: which nodes a region selects, whether a
ground support can grab something that is floating, what gravity weighs, and
the sentence in spec 15.1 that the p99.5 safety factor never carries a case
whose worst element is under 1.5.

Everything except the last test is pure python: the box mesh is built here
(Freudenthal subdivision, exact volume and exact surface area) so the
selection rules can be checked against numbers that are known rather than
measured.
"""

import itertools
import json
import struct

import numpy as np
import pytest

from topopt import ccx as ccxlib
from topopt.cli import ccx as cli
from topopt.external import find
from topopt.loadcase import Study
from topopt.schema import InputError


BOX = (20.0, 10.0, 5.0)


# ------------------------------------------------------------- the fixtures

def _box_mesh(lx=20.0, ly=10.0, lz=5.0, nx=4, ny=2, nz=1):
    """A C3D10 mesh of a box. -> (ids, xyz, elements).

    The same Freudenthal subdivision `test_ccx_mesh` uses: every cell is cut
    into the six tets over the permutations of the axes, which tessellates the
    box exactly, so the volume is lx*ly*lz and the surface area is the box's
    to the last digit.
    """
    ids, xyz = [], []

    def add(pt):
        ids.append(len(ids) + 1)
        xyz.append(tuple(float(v) for v in pt))
        return ids[-1]

    grid = {}
    for i in range(nx + 1):
        for j in range(ny + 1):
            for k in range(nz + 1):
                grid[(i, j, k)] = add((lx * i / nx, ly * j / ny, lz * k / nz))

    tets = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                for perm in itertools.permutations((0, 1, 2)):
                    cur = [i, j, k]
                    seq = [tuple(cur)]
                    for d in perm:
                        cur = list(cur)
                        cur[d] += 1
                        seq.append(tuple(cur))
                    tets.append([grid[s] for s in seq])

    mid, els = {}, []
    for t in tets:
        row = list(t)
        for a, b in ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)):
            key = (min(t[a], t[b]), max(t[a], t[b]))
            if key not in mid:
                p = 0.5 * (np.array(xyz[key[0] - 1]) + np.array(xyz[key[1] - 1]))
                mid[key] = add(p)
            row.append(mid[key])
        els.append(row)
    return np.array(ids, int), np.array(xyz, float), np.array(els, int)


def _write_inp(path, ids, xyz, els):
    """The mesh as Gmsh would export it, so `mesh_context` reads a real file."""
    lines = ["*Heading", " built by test_ccx_cli", "*NODE"]
    for n, p in zip(ids, xyz):
        lines.append("%d, %.10g, %.10g, %.10g" % (n, p[0], p[1], p[2]))
    lines.append("*ELEMENT, type=C3D10, ELSET=Volume1")
    for e, row in enumerate(els, 1):
        lines.append(", ".join([str(e)] + [str(int(v)) for v in row]))
    lines.append("*ELSET,ELSET=part")
    lines.append(", ".join(str(e) for e in range(1, len(els) + 1)) + ",")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return str(path)


@pytest.fixture
def mesh(tmp_path):
    ids, xyz, els = _box_mesh(*BOX)
    return cli.mesh_context(_write_inp(tmp_path / "box_mesh.inp", ids, xyz, els))


def _study(cases, regions=None, **kw):
    data = {"material": "PLA", "build_axis": "Z", "safety_factor": 3.0,
            "regions": regions or {}, "cases": cases}
    data.update(kw)
    return Study(data)


ALL_BOX = {"type": "box", "min": [-1, -1, -1], "max": [21, 11, 6]}
# Deep enough to hold two node planes (z = 0 and the midside plane
# at 2.5), so "only the lowest one" is a claim the test can check.
LOW_BOX = {"type": "box", "min": [-1, -1, -1], "max": [21, 11, 3.0]}
HIGH_BOX = {"type": "box", "min": [-1, -1, 2.0], "max": [21, 11, 6]}
TIP = {"type": "face", "axis": "X", "side": "max", "depth": 0.5}


# ------------------------------------------------------------ surface faces

def test_the_boundary_faces_add_up_to_the_area_of_the_box(mesh):
    """`surface_faces` is only worth having if the area it reports is the
    area of the part: a support's contact area is read off it."""
    area = ccxlib.face_areas(mesh["faces"], mesh["xyz"], ids=mesh["ids"]).sum()
    lx, ly, lz = BOX
    assert area == pytest.approx(2 * (lx * ly + lx * lz + ly * lz), rel=1e-9)


def test_the_clamped_area_is_the_face_not_the_node_count(mesh):
    """A whole side of the box, selected as nodes, must measure as that side."""
    face = {"type": "face", "axis": "X", "side": "min", "depth": 0.01}
    ids = cli.region_nodes(mesh, face, mode="surface")
    assert cli.selected_area_mm2(mesh, ids) == pytest.approx(BOX[1] * BOX[2],
                                                             rel=1e-9)


# --------------------------------------------------------- node selection

def test_surface_mapping_selects_fewer_nodes_than_volume(mesh):
    """Known-issue A6: the interior of a region grows faster than its surface
    when the mesh is refined, so a volume-mapped clamp stiffens with the mesh."""
    vol = cli.region_nodes(mesh, ALL_BOX, mode="volume")
    surf = cli.region_nodes(mesh, ALL_BOX, mode="surface")
    assert len(vol) == len(mesh["ids"])
    assert 0 < len(surf) < len(vol)
    assert set(surf.tolist()) == set(int(v) for v in mesh["surface"])


def test_an_unknown_map_mode_is_refused(mesh):
    with pytest.raises(InputError, match="--map"):
        cli.region_nodes(mesh, ALL_BOX, mode="sideways")


def test_ground_contact_keeps_only_the_lowest_plane(mesh):
    """Mirrors `loadcase._plane_nodes_of`: the contact face, never anything
    above it. A foot floating one layer up is not supported."""
    ids = cli.region_nodes(mesh, LOW_BOX, mode="surface")
    got, plane = cli.ground_nodes(mesh, ids, 2, tol=0.05)
    assert plane == pytest.approx(0.0)
    assert 0 < len(got) < len(ids)
    z = mesh["xyz"][:, 2]
    assert max(z[mesh["index"][int(n)]] for n in got) <= 0.05
    # And the ones it dropped were real nodes above the plane, not rounding.
    dropped = set(int(v) for v in ids) - set(int(v) for v in got)
    assert dropped and min(z[mesh["index"][n]] for n in dropped) > 0.05


def test_a_ground_region_above_the_plane_is_refused(mesh):
    """Exit code 2, not a silent clamp: a region drawn above the table
    describes a part standing on nothing."""
    study = _study([{"name": "float", "supports": [
        {"region": "up", "contact": "ground", "fix": "xyz"}],
        "loads": [{"region": "tip", "force": [0, 0, -10]}]}],
        regions={"up": HIGH_BOX, "tip": TIP})
    with pytest.raises(InputError, match="standing on nothing"):
        cli.build_cases(study, mesh, density_kg_m3=1240.0, volume_mm3=1000.0)


def test_fix_z_reaches_the_deck_as_one_dof(mesh):
    """A pin written as a clamp is the commonest way to make a part look
    stiffer than it is, so `fix` has to survive the trip to `write_inp`."""
    assert cli.fix_dofs("z") == (3,)
    assert cli.fix_dofs("xy") == (1, 2)
    study = _study([{"name": "slide", "supports": [
        {"region": "low", "fix": "z"}],
        "loads": [{"region": "tip", "force": [0, 0, -10]}]}],
        regions={"low": LOW_BOX, "tip": TIP})
    cases = cli.build_cases(study, mesh, density_kg_m3=1240.0,
                            volume_mm3=1000.0)
    assert cases[0]["supports"][0]["dofs"] == (3,)
    assert cases[0]["report"]["supports"][0]["area_mm2"] > 0


def test_a_region_that_lands_on_no_node_is_refused(mesh):
    study = _study([{"name": "nowhere", "supports": [{"region": "low"}],
                     "loads": [{"region": "gone", "force": [0, 0, -10]}]}],
                   regions={"low": LOW_BOX,
                            "gone": {"type": "sphere",
                                     "centre": [500, 0, 0], "r": 1}})
    with pytest.raises(InputError, match="load region"):
        cli.build_cases(study, mesh, density_kg_m3=1240.0, volume_mm3=1000.0)


# ------------------------------------------------------------------ gravity

def test_the_gravity_vector_is_mm_per_second_squared():
    g = cli.gravity_vector({"direction": [0, 0, -1], "g_factor": 100.0})
    assert list(g) == pytest.approx([0.0, 0.0, -981000.0])
    # An unnormalised direction is a direction, not a magnitude.
    g2 = cli.gravity_vector({"direction": [0, 0, -3], "g_factor": 1.0})
    assert list(g2) == pytest.approx([0.0, 0.0, -9810.0])


def test_the_weight_matches_the_measured_box():
    """The 20x10x5 PLA box weighed 0.012160 N in the real CalculiX run."""
    w = cli.body_force_N(1240.0, 1000.0, [0, 0, -cli.G_MM_S2])
    assert w[2] == pytest.approx(-0.012164, abs=1e-6)


def test_the_netted_body_force_is_the_volume_that_touches_the_clamp(mesh):
    root = {"type": "face", "axis": "X", "side": "min", "depth": 0.01}
    ids = cli.region_nodes(mesh, root, mode="surface")
    v = cli.bound_volume_mm3(mesh, [int(n) for n in ids])
    total = ccxlib.element_volumes(mesh["xyz"], mesh["elements"],
                                   ids=mesh["ids"]).sum()
    assert 0 < v < total
    assert cli.bound_volume_mm3(mesh, []) == 0.0


def test_a_gravity_case_is_balanced_against_the_netted_body_force():
    """CalculiX's printed total nets out the body force on the clamped nodes
    (box: weight 0.012160 N, printed 0.011046 N), so a gravity case judged at
    1 % of the applied load fails for being right."""
    applied = np.array([0.0, 0.0, -0.012160])
    printed = np.array([0.0, 0.0, 0.011046])
    tight = ccxlib.equilibrium_error(applied, printed)
    assert tight["pass"] is False               # what the strict rule says
    ok = cli.equilibrium_with_body_force(applied, printed, 0.001114)
    assert ok["pass"] is True
    assert ok["netted_bound_N"] == pytest.approx(0.001114)
    assert ok["residual_norm_N"] == pytest.approx(0.001114, abs=1e-6)
    # But it is an allowance, not an amnesty: ten times the residual still fails.
    bad = cli.equilibrium_with_body_force(applied, printed * 0.5, 0.001114)
    assert bad["pass"] is False


def test_a_plain_cload_case_keeps_the_strict_one_percent():
    eq = ccxlib.equilibrium_error([0, 0, -100.0], [0, 0, 98.0])
    assert eq["pass"] is False and eq["relative_error"] == pytest.approx(0.02)


# ---------------------------------------------------------------- judgement

def test_the_p99_5_never_carries_a_failing_worst_element():
    """Spec 15.1's sentence, and the reason both numbers are reported."""
    ok, notes = cli.judge_case(5.0, 1.2, 3.0, True)
    assert ok is False
    assert any("worst element" in n for n in notes)


def test_a_case_clears_when_both_numbers_do():
    ok, notes = cli.judge_case(5.0, 4.0, 3.0, True)
    assert ok is True and notes == []


def test_a_missing_worst_element_fails_rather_than_abstains():
    ok, notes = cli.judge_case(5.0, None, 3.0, True)
    assert ok is False and any("cannot be shown" in n for n in notes)


def test_an_unbalanced_case_fails_whatever_its_safety_factor():
    ok, notes = cli.judge_case(50.0, 40.0, 3.0, False)
    assert ok is False and any("balance" in n for n in notes)


def test_buckling_below_the_required_factor_fails_the_case():
    ok, notes = cli.judge_case(5.0, 4.0, 3.0, True, buckling_min=1.2)
    assert ok is False and any("buckl" in n for n in notes)


def test_the_exclusion_zone_is_recorded_before_and_after():
    """An exclusion zone is also the easiest way to make a genuinely
    overloaded corner disappear, so it never happens silently."""
    fi = np.array([1.0 / 1.2, 1.0 / 4.0, 1.0 / 6.0])
    pts = np.array([[0.2, 0, 0], [10.0, 0, 0], [20.0, 0, 0]])
    fixed = np.array([[0.0, 0.0, 0.0]])

    plain, none = cli.worst_element_exclusion(fi, pts, fixed, 0.0)
    assert plain == pytest.approx(1.2) and none is None
    assert cli.judge_case(5.0, plain, 3.0, True)[0] is False

    after, excl = cli.worst_element_exclusion(fi, pts, fixed, 1.0)
    assert after == pytest.approx(4.0)
    assert excl["excluded_nodes"] == 1
    assert excl["worst_before"] == pytest.approx(1.2)
    assert excl["worst_after"] == pytest.approx(4.0)
    assert "singularity" in excl["reason"]
    assert cli.judge_case(5.0, after, 3.0, True)[0] is True


def test_excluding_everything_leaves_the_worst_element_standing():
    fi = np.array([1.0 / 1.2, 1.0 / 4.0])
    pts = np.array([[0.0, 0, 0], [0.5, 0, 0]])
    after, excl = cli.worst_element_exclusion(fi, pts, pts, 5.0)
    assert after == pytest.approx(1.2)
    assert excl["worst_after"] is None and "not applied" in excl["reason"]


# ---------------------------------------------------------- the real run

def _write_box_stl(path, lx=20.0, ly=10.0, lz=5.0):
    """A closed binary STL of a box, with a *text* 80-byte header: Gmsh
    4.15.2 refuses to load one whose header is all NUL."""
    v = np.array([[0, 0, 0], [lx, 0, 0], [lx, ly, 0], [0, ly, 0],
                  [0, 0, lz], [lx, 0, lz], [lx, ly, lz], [0, ly, lz]], float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append(v[[a, b, c]])
        tris.append(v[[a, c, d]])
    with open(path, "wb") as f:
        f.write(b"box written by test_ccx_cli".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            n = n / np.linalg.norm(n)
            f.write(struct.pack("<12fH", *n, *t.ravel(), 0))
    return str(path)


STUDY = {
    "material": "PLA", "build_axis": "Z", "resolution_mm": 3.0,
    "safety_factor": 3.0, "max_deflection_mm": 1.0,
    "regions": {
        "root": {"type": "face", "axis": "X", "side": "min", "depth": 0.5},
        "tip": {"type": "face", "axis": "X", "side": "max", "depth": 0.5}},
    "cases": [
        {"name": "LC1 working push",
         "supports": [{"region": "root", "fix": "xyz"}],
         "loads": [{"region": "tip", "force": [-40.0, 0.0, -8.0]}]},
        {"name": "LC2 drop on the tip", "load_class": "abnormal",
         "supports": [{"region": "root", "fix": "xyz"}],
         "loads": [{"region": "tip", "force": [-120.0, 0.0, -24.0]}],
         "gravity": {"direction": [0, 0, -1], "g_factor": 50.0}}]}


@pytest.mark.skipif(find("gmsh") is None or find("calculix") is None,
                    reason="Gmsh and CalculiX are not both installed here")
def test_a_small_box_runs_end_to_end_with_buckling_and_frequency(tmp_path):
    """The whole command on a 20x10x5 box at 3 mm: two cases at two different
    bars, a buckling deck each and one frequency deck for the support set they
    share. What it proves is that the pieces above are wired to the real
    tools -- a summary that a report can read, with the load class and the
    required factor in it."""
    stl = _write_box_stl(tmp_path / "box.stl", *BOX)
    study = tmp_path / "box_study.json"
    study.write_text(json.dumps(STUDY), encoding="utf-8")
    out = tmp_path / "out"

    code = cli.main([stl, "--study", str(study), "--out", str(out),
                     "--mesh-mm", "3", "--buckle", "--frequency"])
    assert code in (0, 1), "the run itself failed, which is not a verdict"

    with open(out / "ccx_summary.json", encoding="utf-8") as f:
        blob = json.load(f)
    run = blob["runs"][0]
    assert run["map_mode"] == "volume"
    assert run["mesh_quality"]["elements"] > 0
    assert run["mass_properties"]["volume_mm3"] == pytest.approx(
        BOX[0] * BOX[1] * BOX[2], rel=0.02)
    assert run["mass_properties"]["mass_g"] > 0

    cases = {c["case"]: c for c in run["cases"]}
    assert len(cases) == 2
    normal = cases["LC1 working push"]
    abnormal = cases["LC2 drop on the tip"]
    assert normal["required_safety_factor"] == 3.0
    assert abnormal["required_safety_factor"] == 1.5
    assert abnormal["load_class"] == "abnormal"

    for c in run["cases"]:
        # Spec 14.3: the numbers a report quotes, on every case.
        for key in ("max_disp_components_mm", "max_disp_at_mm",
                    "reaction_total_N", "reaction_moment_Nmm",
                    "compliance_Nmm", "principal_max_MPa", "principal_min_MPa",
                    "tresca_max_MPa", "strain_energy_density_max_MPa",
                    "failure_index_p99_5", "failure_index_max",
                    "worst_element_safety_factor"):
            assert key in c, (c["case"], key)
        # The rest of spec 14.3's element and material-coordinate rows, added
        # 2026-09-08. Asserted on the real summary and not only on the
        # functions that build them: a number computed and not written is not
        # an output.
        for key in ("stress_components_MPa", "material_axis_stress_MPa",
                    "failure_modes", "governing_failure_mode",
                    "principal_strain_max", "principal_strain_min",
                    "reaction_by_support"):
            assert key in c, (c["case"], key)
        assert set(c["stress_components_MPa"]) == {
            "sxx", "syy", "szz", "txy", "tyz", "tzx"}
        assert c["governing_failure_mode"] in c["failure_modes"]
        worst = c["failure_modes"][c["governing_failure_mode"]]
        assert worst["failure_index_max"] == pytest.approx(
            c["failure_index_max"], rel=1e-4),             "the governing mode must be the one the aggregate index came from"
        assert c["principal_strain_max"] >= c["principal_strain_min"]
        assert c["reaction_by_support"][0]["nodes"] > 0
        assert c["equilibrium"]["pass"] is True, c["equilibrium"]
        assert c["buckling_factors"], c.get("buckling_note")
        assert c["mapping"]["supports"][0]["area_mm2"] > 0

    # Gravity is the solid material's, and says so.
    grav = abnormal["mapping"]["gravity"]
    assert grav["density_kg_m3"] == 1240
    assert grav["vector_mm_s2"][2] == pytest.approx(-50 * cli.G_MM_S2)
    assert "infill" in grav["note"]

    # One frequency deck, shared by both cases, in Hz and not rad/s.
    fsets = run["frequencies_hz"]
    assert len(fsets) == 1 and len(fsets[0]["cases"]) == 2
    assert any(10.0 < f < 1e6 for f in fsets[0]["frequencies_hz"])

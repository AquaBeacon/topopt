"""The tet mesh, the mass properties, the stress algebra and the real files.

`test_ccx.py` covers the deck and the parsers as text. This file covers the
parts that need a *mesh*: reading one, finding its boundary, judging its
element shapes, weighing it, and turning CalculiX's stress and strain into
numbers a report can quote.

Everything here except the last test is pure python and runs on a machine with
neither Gmsh nor CalculiX. The box mesh is built in this file rather than by
Gmsh for exactly that reason -- and because a hand-built Freudenthal
tessellation of a box has an exact volume and an exact inertia tensor to
compare against, which a Gmsh mesh of a curved part never does.

The one end-to-end test runs the real tools when they are installed. What it
is for is the thing no fixture can prove: that CalculiX accepts these decks
and that these parsers read what it actually wrote.
"""

import itertools
import struct

import numpy as np
import pytest

from topopt import ccx
from topopt.external import find
from topopt.materials import ORTHOTROPIC, material_model


# ------------------------------------------------------------- the fixtures

BOX = (20.0, 10.0, 5.0)


def _box_mesh(lx=20.0, ly=10.0, lz=5.0, nx=8, ny=4, nz=2):
    """A C3D10 mesh of a box. -> (ids, xyz, elements).

    Freudenthal subdivision: every grid cell is cut into the six tets
    (base, +e_a, +e_a+e_b, +e_a+e_b+e_c) over the permutations of the axes.
    It tessellates the box exactly and conforms across cells, so the volume
    and the inertia tensor have closed forms to check against; a mesher's
    output has neither. Midside nodes are placed on the edge midpoints in the
    Abaqus C3D10 order n5=(1,2) n6=(2,3) n7=(3,1) n8=(1,4) n9=(2,4) n10=(3,4).
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


def _write_box_stl(path, lx=20.0, ly=10.0, lz=5.0):
    """A closed binary STL of a box.

    The 80-byte header is **text**, not zeros. Gmsh 4.15.2 refuses to load a
    binary STL whose header is all NUL ("Error loading ...", then "Unknown
    surface 1 in GEO volume 1"), which is what `stepio.write_binary_stl`
    writes -- so a test that used that writer would fail for a reason that has
    nothing to do with what it is testing.
    """
    v = np.array([[0, 0, 0], [lx, 0, 0], [lx, ly, 0], [0, ly, 0],
                  [0, 0, lz], [lx, 0, lz], [lx, ly, lz], [0, ly, lz]], float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append(v[[a, b, c]])
        tris.append(v[[a, c, d]])
    with open(path, "wb") as f:
        f.write(b"box written by test_ccx_mesh".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            n = n / np.linalg.norm(n)
            f.write(struct.pack("<12fH", *n, *t.ravel(), 0))
    return str(path)


# A Gmsh 4.15.2 `.inp` export, shortened. Reproduced rather than described:
# `*Heading` is followed by a bare Windows path, the element block is
# introduced by a line of stars, the element set on the *ELEMENT card is
# `Volume1` and not the physical volume's name, and `part` exists only as a
# separate `*ELSET` whose lines end in a trailing comma.
GMSH_INP = """*Heading
 C:\\tmp\\box_mesh.inp
*NODE
1, 0, 0, 0
2, 20, 0, 0
3, 20, 10, 0
4, 0, 0, 5
5, 10, 0, 0
6, 20, 5, 0
7, 10, 5, 0
8, 0, 0, 2.5
9, 10, 0, 2.5
10, 10, 5, 2.5
******* E L E M E N T S *************
*ELEMENT, type=C3D10, ELSET=Volume1
1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
*ELSET,ELSET=part
1,
"""


# ----------------------------------------------------------------- read_mesh

def test_read_mesh_reads_the_layout_gmsh_actually_writes(tmp_path):
    p = tmp_path / "m.inp"
    p.write_text(GMSH_INP)
    m = ccx.read_mesh(str(p))
    assert m["types"] == ["C3D10"]
    assert len(m["ids"]) == 10
    assert m["ids"][0] == 1
    assert m["xyz"][1] == pytest.approx([20.0, 0.0, 0.0])
    assert m["elements"].shape == (1, 10)
    assert m["elements"][0].tolist() == list(range(1, 11))


def test_the_heading_path_is_not_read_as_a_node(tmp_path):
    """`*Heading` is followed by a Windows path with no commas in it. A reader
    that tries to float() it either crashes or, worse, invents a node."""
    p = tmp_path / "m.inp"
    p.write_text(GMSH_INP)
    m = ccx.read_mesh(str(p))
    assert len(m["ids"]) == 10                     # 10 nodes, not 11


def test_a_wrapped_element_line_is_read_as_one_element(tmp_path):
    """Abaqus lets an element continue on the next line. Gmsh writes C3D10 on
    one line today, but it does use trailing-comma continuation elsewhere in
    the same file, so the reader must not depend on that staying true."""
    p = tmp_path / "m.inp"
    p.write_text(GMSH_INP.replace(
        "1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10",
        "1, 1, 2, 3, 4, 5, 6,\n7, 8, 9, 10"))
    m = ccx.read_mesh(str(p))
    assert m["elements"].shape == (1, 10)
    assert m["elements"][0].tolist() == list(range(1, 11))


def test_linear_tets_are_read_as_four_node_elements(tmp_path):
    """The .geo always asks for order 2, so a C3D4 mesh means something went
    wrong upstream -- and it has to be *visible*, not a crash in the reader."""
    p = tmp_path / "m.inp"
    p.write_text(GMSH_INP.replace("type=C3D10", "type=C3D4")
                 .replace("1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10", "1, 1, 2, 3, 4"))
    m = ccx.read_mesh(str(p))
    assert m["types"] == ["C3D4"]
    assert m["elements"].shape == (1, 4)


def test_an_empty_mesh_is_an_error_not_an_empty_answer(tmp_path):
    p = tmp_path / "m.inp"
    p.write_text("*Heading\n C:\\tmp\\x.inp\n")
    with pytest.raises(RuntimeError, match="mesh is empty"):
        ccx.read_mesh(str(p))


# ------------------------------------------------------------- surface_nodes

def test_surface_nodes_leave_the_interior_out():
    ids, xyz, els = _box_mesh()
    surf = ccx.surface_nodes(els)
    assert 0 < len(surf) < len(ids)
    inside = set(ids.tolist()) - set(surf.tolist())
    assert inside, "an 8x4x2 box mesh must have interior nodes"
    # Every excluded node is strictly inside the box, in all three directions.
    for nid in inside:
        x, y, z = xyz[nid - 1]
        assert 1e-9 < x < BOX[0] - 1e-9
        assert 1e-9 < y < BOX[1] - 1e-9
        assert 1e-9 < z < BOX[2] - 1e-9


def test_every_node_on_a_box_face_is_a_surface_node():
    """The reason this matters: the CalculiX cases pick their clamped and
    loaded nodes off a plane. A boundary node missed here is a support that
    quietly is not there."""
    ids, xyz, els = _box_mesh()
    surf = set(ccx.surface_nodes(els).tolist())
    on_face = np.zeros(len(ids), bool)
    for axis, length in enumerate(BOX):
        on_face |= np.abs(xyz[:, axis]) < 1e-9
        on_face |= np.abs(xyz[:, axis] - length) < 1e-9
    for nid in ids[on_face]:
        assert int(nid) in surf, "node %d is on a box face" % nid


def test_a_boundary_face_brings_its_midside_nodes_with_it():
    """A second-order face held only at its corners still flaps: the midside
    node is free. So the midsides of a boundary face are boundary nodes."""
    ids, xyz, els = _box_mesh(nx=1, ny=1, nz=1)
    surf = set(ccx.surface_nodes(els).tolist())
    corners = set(els[:, :4].ravel().tolist())
    midsides = set(ids.tolist()) - corners
    assert midsides, "a C3D10 mesh has midside nodes"
    # Every midside sitting on a face of the box is a boundary node, and a
    # corner-only answer would have dropped all of them.
    centre = np.array(BOX) / 2.0
    for nid in midsides:
        pt = xyz[nid - 1]
        on_face = any(abs(pt[a]) < 1e-9 or abs(pt[a] - BOX[a]) < 1e-9
                      for a in range(3))
        assert (int(nid) in surf) == on_face, (nid, pt)
    # The single node that is genuinely inside -- the midpoint of the cell's
    # body diagonal, at the centre of the box -- is the one left out.
    inside = set(ids.tolist()) - surf
    assert len(inside) == 1
    assert xyz[inside.pop() - 1] == pytest.approx(centre)


def test_surface_nodes_of_an_empty_mesh_is_empty():
    assert ccx.surface_nodes(np.zeros((0, 10), int)).tolist() == []


# -------------------------------------------------------------- mesh_quality

REG_TET = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)


def test_a_regular_tet_scores_one():
    q = ccx.mesh_quality(REG_TET, np.array([[1, 2, 3, 4]]), ids=[1, 2, 3, 4])
    assert q["min"] == pytest.approx(1.0, rel=1e-6)
    assert q["median"] == pytest.approx(1.0, rel=1e-6)
    assert q["pass"] is True


def test_a_flattened_tet_scores_near_zero_and_fails():
    """The failure this catches: a mesh full of slivers does not error, it
    solves, and it reports a stress that belongs to its worst elements."""
    flat = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0.3, 0.3, 1e-3]], float)
    q = ccx.mesh_quality(flat, np.array([[1, 2, 3, 4]]), ids=[1, 2, 3, 4])
    assert q["min"] < 0.02
    assert q["poor_count"] == 1 and q["poor_fraction"] == 1.0
    assert q["pass"] is False
    assert "sliver" in q["note"]


def test_the_box_mesh_passes_the_quality_check():
    ids, xyz, els = _box_mesh()
    q = ccx.mesh_quality(xyz, els, ids=ids)
    assert q["elements"] == 8 * 4 * 2 * 6
    assert q["min"] > 0.1 and q["pass"] is True


def test_an_inverted_element_is_judged_on_its_shape_not_raised_on():
    """|V| is used on purpose: an inverted tet should show up in the count,
    not stop the report before anyone sees the count."""
    good = np.array([[1, 2, 3, 4]])
    flipped = np.array([[1, 3, 2, 4]])
    a = ccx.mesh_quality(REG_TET, good, ids=[1, 2, 3, 4])
    b = ccx.mesh_quality(REG_TET, flipped, ids=[1, 2, 3, 4])
    assert a["min"] == pytest.approx(b["min"], rel=1e-12)


def test_quality_refuses_to_guess_the_node_numbering():
    with pytest.raises(ValueError, match="pass ids="):
        ccx.mesh_quality(REG_TET, np.array([[101, 102, 103, 104]]))


# ----------------------------------------------------------- mass_properties

def test_box_mass_properties_match_the_closed_form():
    """The tessellation is exact for a box, so this is not a tolerance test on
    a mesh -- it is a test of the integration formula. The inertia is the part
    that would still look plausible if it were wrong."""
    ids, xyz, els = _box_mesh()
    rho = 1240.0                                   # PLA, kg/m^3
    mp = ccx.mass_properties(xyz, els, rho, ids=ids)
    a, b, c = BOX
    vol = a * b * c
    mass = vol * rho * 1e-6                        # kg/m^3 -> g/mm^3
    assert mp["volume_mm3"] == pytest.approx(vol, rel=1e-6)
    assert mp["mass_g"] == pytest.approx(mass, rel=1e-6)
    assert mp["centroid_mm"] == pytest.approx([a / 2, b / 2, c / 2], abs=1e-9)
    inertia = np.array(mp["inertia_tensor_g_mm2"])
    assert inertia[0, 0] == pytest.approx(mass * (b * b + c * c) / 12, rel=1e-3)
    assert inertia[1, 1] == pytest.approx(mass * (a * a + c * c) / 12, rel=1e-3)
    assert inertia[2, 2] == pytest.approx(mass * (a * a + b * b) / 12, rel=1e-3)
    # A box about its own axes has no products of inertia.
    off = inertia - np.diag(np.diag(inertia))
    assert np.abs(off).max() < 1e-6 * inertia[2, 2]


def test_inertia_is_integrated_not_lumped_at_the_tet_centroids():
    """The point-mass approximation is short by exactly the tets' own inertia
    -- always low, which is the direction that makes a part look easier to
    spin up and easier to shake than it is."""
    ids, xyz, els = _box_mesh(nx=2, ny=1, nz=1)
    rho = 1240.0
    mp = ccx.mass_properties(xyz, els, rho, ids=ids)
    p = xyz[els[:, :4] - 1]
    v = np.abs(np.einsum("ij,ij->i", p[:, 1] - p[:, 0],
                         np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 0]))) / 6.0
    cen = np.array(mp["centroid_mm"])
    d = p.mean(axis=1) - cen
    lumped = rho * 1e-6 * float(
        (v * (d[:, 0] ** 2 + d[:, 1] ** 2)).sum())            # Izz, lumped
    exact = np.array(mp["inertia_tensor_g_mm2"])[2, 2]
    a, b, _c = BOX
    assert exact == pytest.approx(mp["mass_g"] * (a * a + b * b) / 12, rel=1e-6)
    assert lumped < 0.98 * exact


def test_weighing_nothing_is_refused():
    with pytest.raises(ValueError, match="nothing to weigh"):
        ccx.mass_properties(REG_TET, np.zeros((0, 4), int), 1240.0)


# --------------------------------------------------- stress post-processing

def test_uniaxial_stress_has_one_principal_and_tresca_equal_to_it():
    s = np.array([[30.0, 0, 0, 0, 0, 0]])
    p = ccx.principal_stresses(s)
    assert p[0] == pytest.approx([30.0, 0.0, 0.0])
    assert ccx.tresca(s)[0] == pytest.approx(30.0)


def test_pure_shear_is_plus_tau_zero_minus_tau():
    """Tresca reads 2*tau where von Mises reads sqrt(3)*tau -- about 15 %
    apart. Both are reported because a part whose margin lives inside that
    gap does not have a margin."""
    from topopt.hex8 import von_mises
    tau = 7.0
    s = np.array([[0, 0, 0, tau, 0, 0]], float)
    p = ccx.principal_stresses(s)
    assert p[0] == pytest.approx([tau, 0.0, -tau], abs=1e-9)
    assert ccx.tresca(s)[0] == pytest.approx(2 * tau)
    assert von_mises(s)[0] == pytest.approx(np.sqrt(3) * tau)


def test_principal_stresses_come_back_largest_first():
    s = np.array([[-50.0, 10.0, 2.0, 0, 0, 0]])
    p = ccx.principal_stresses(s)[0]
    assert p[0] > p[1] > p[2]
    assert p == pytest.approx([10.0, 2.0, -50.0])


def test_strain_energy_uses_tensor_shear_not_engineering():
    """Measured on a real CalculiX 2.23 run: sxy/exy came back at 2*Gxy, so
    the frd's TOSTRAIN shears are tensor components. Halving them here (the
    engineering convention) would understate the energy in exactly the
    shear-dominated places where it decides anything."""
    tau, g = 7.0, 1000.0
    exy = tau / (2 * g)                       # tensor strain for that stress
    s = np.array([[0, 0, 0, tau, 0, 0]], float)
    e = np.array([[0, 0, 0, exy, 0, 0]], float)
    assert ccx.strain_energy_density(s, e)[0] == pytest.approx(tau * exy)
    # The uniaxial case has the familiar 1/2 sigma epsilon.
    su = np.array([[30.0, 0, 0, 0, 0, 0]])
    eu = np.array([[0.01, 0, 0, 0, 0, 0]])
    assert ccx.strain_energy_density(su, eu)[0] == pytest.approx(0.15)


def test_strain_energy_refuses_mismatched_arrays():
    with pytest.raises(ValueError, match=r"\(n,6\)"):
        ccx.strain_energy_density(np.zeros((2, 6)), np.zeros((3, 6)))


# ------------------------------------------------- spec 14.3: principal strain

def test_principal_strains_come_back_largest_first():
    """Spec 14.3 lists principal *strains* next to principal stresses, and
    they are not the same directions once the material is orthotropic."""
    e = np.array([[-5e-3, 1e-3, 2e-4, 0, 0, 0]])
    p = ccx.principal_strains(e)[0]
    assert p[0] > p[1] > p[2]
    assert p == pytest.approx([1e-3, 2e-4, -5e-3])


def test_principal_strains_read_the_shears_as_tensor_components():
    """Same measured fact `strain_energy_density` is built on: the frd's
    TOSTRAIN shears are tensor strains. Pure shear exy therefore gives
    principal strains +exy, 0, -exy -- halving them first (the engineering
    convention) would report half the real principal strain, which is
    invisible in a uniaxial check."""
    exy = 3.5e-3
    e = np.array([[0, 0, 0, exy, 0, 0]], float)
    assert ccx.principal_strains(e)[0] == pytest.approx([exy, 0.0, -exy],
                                                        abs=1e-12)


def test_a_reaction_is_split_per_support_set():
    """Spec 14.3's reaction and reaction moment, per support rather than as
    one grand total: two feet where one carries everything reads exactly like
    a balanced stance when the sets are summed together."""
    from topopt.cli.ccx import _reaction_by_support

    case = {"supports": [{"nodes": [1, 2], "region": "left"},
                         {"nodes": [3], "region": "right"}]}
    st = {"reaction": {1: (0.0, 0.0, 6.0), 2: (0.0, 0.0, 4.0),
                       3: (0.0, 0.0, 1.0)}}
    res = {"nodes": {1: (0.0, 0.0, 0.0), 2: (10.0, 0.0, 0.0),
                     3: (100.0, 0.0, 0.0)}}
    rows = _reaction_by_support(case, st, res)
    assert [r["name"] for r in rows] == ["left", "right"]
    assert rows[0]["force_N"] == [0.0, 0.0, 10.0]
    assert rows[1]["force_N"] == [0.0, 0.0, 1.0]
    # M = r x F about the origin: node 2 at x = 10 with Fz = 4 gives My = -40.
    assert rows[0]["moment_Nmm"][1] == pytest.approx(-40.0)
    assert rows[1]["moment_Nmm"][1] == pytest.approx(-100.0)


def test_a_support_set_with_no_reaction_says_so_rather_than_reading_zero():
    from topopt.cli.ccx import _reaction_by_support
    rows = _reaction_by_support({"fixed_nodes": [9]}, {"reaction": {}},
                                {"nodes": {}})
    assert rows[0]["nodes"] == 0 and "no reaction" in rows[0]["note"]
    assert "force_N" not in rows[0], "a missing reaction is not a zero one"


# ------------------------------------------------- parse_frd: strain, forces

FRD_FULL = """    1C
    2C                  2                                    1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E+01 0.00000E+00 0.00000E+00
 -3
  100CL  101 1.000000000  2  0  1  1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E-02 0.00000E+00-5.00000E-02
 -3
  100CL  101 1.000000000  2  0  1  1
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -1         1 1.00000E+01 2.00000E+00 3.00000E+00 4.00000E-01 5.00000E-01 6.00000E-01
 -1         2 2.00000E+01 4.00000E+00 6.00000E+00 8.00000E-01 1.00000E+00 1.20000E+00
 -3
  100CL  101 1.000000000  2  0  1  1
 -4  TOSTRAIN    6    1
 -5  EXX         1    4    1    1
 -1         1 1.00000E-03 2.00000E-04-3.00000E-04 4.00000E-05 5.00000E-05 6.00000E-05
 -1         2 2.00000E-03 4.00000E-04-6.00000E-04 8.00000E-05 1.00000E-04 1.20000E-04
 -3
  100CL  101 1.000000000  2  0  1  1
 -4  FORC        4    1
 -5  F1          1    2    1    0
 -1         1-1.00000E+02 0.00000E+00-2.00000E+01
 -1         2 1.00000E+02 0.00000E+00 2.00000E+01
 -3
"""


def test_frd_carries_strain_and_reaction_per_step(tmp_path):
    p = tmp_path / "j.frd"
    p.write_text(FRD_FULL)
    r = ccx.parse_frd(str(p))
    st = r["steps"][0]
    assert st["strain"][2] == pytest.approx(
        (2e-3, 4e-4, -6e-4, 8e-5, 1e-4, 1.2e-4))
    assert st["reaction"][1] == pytest.approx((-100.0, 0.0, -20.0))
    # ... and the existing keys are untouched.
    assert st["displacement"][2] == pytest.approx((0.01, 0.0, -0.05))
    assert st["stress"][2][0] == pytest.approx(20.0)


def test_frd_top_level_still_carries_the_last_step(tmp_path):
    p = tmp_path / "j.frd"
    p.write_text(FRD_FULL)
    r = ccx.parse_frd(str(p))
    assert set(["nodes", "displacement", "stress", "steps"]) <= set(r)
    assert r["strain"][1][0] == pytest.approx(1e-3)
    assert r["reaction"][2] == pytest.approx((100.0, 0.0, 20.0))


def test_the_reactions_in_the_frd_balance_the_load(tmp_path):
    """FORC is the nodal force: the applied load where there is one, the
    reaction where there is a support. Summed over the mesh it is zero, which
    is the check that costs nothing and catches a mis-numbered node set."""
    p = tmp_path / "j.frd"
    p.write_text(FRD_FULL)
    r = ccx.parse_frd(str(p))
    tot = np.array(list(r["reaction"].values())).sum(axis=0)
    assert tot == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)


def test_a_modal_step_number_is_not_a_bare_integer(tmp_path):
    """A frequency step writes "2    7MODAL      1" in the 100C header. Read
    as a whole token it is not an int, and every block in the mode then became
    a step of its own -- a six-mode run reported 36 steps instead of 12."""
    p = tmp_path / "j.frd"
    p.write_text(FRD_FULL.replace("2  0  1  1", "2  0  7MODAL  1"))
    r = ccx.parse_frd(str(p))
    assert [st["step"] for st in r["steps"]] == [7]


# ------------------------------------------------ write_inp: supports, GRAV

def _sup_case(name="c1"):
    return {"name": name,
            "supports": [{"nodes": [1, 2, 3], "dofs": (1, 2, 3)},
                         {"nodes": [4, 5], "dofs": (3,)}],
            "loads": [(10, (0.0, 0.0, -5.0))]}


ORTHO_PLA = ORTHOTROPIC["PLA"]


def test_each_support_gets_its_own_nset_and_dof_range(tmp_path):
    p = tmp_path / "j.inp"
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", ortho=ORTHO_PLA),
                  [_sup_case()])
    txt = p.read_text()
    assert "*NSET, NSET=FIXED1_1" in txt and "*NSET, NSET=FIXED1_2" in txt
    assert txt.count("*BOUNDARY, OP=NEW") == 1
    assert "FIXED1_1, 1, 3, 0.0" in txt
    assert "FIXED1_2, 3, 3, 0.0" in txt
    # A pin written as a full clamp is the commonest way to make a part look
    # stiffer than it is, so the second set must NOT hold 1 and 2.
    assert "FIXED1_2, 1, 3, 0.0" not in txt


def test_non_contiguous_dofs_are_written_one_line_per_run(tmp_path):
    """CalculiX reads `nset, first, last, value` as a range, so a support that
    holds 1 and 3 and not 2 cannot be one line: it would clamp the direction
    that was meant to be free."""
    p = tmp_path / "j.inp"
    case = {"name": "c", "supports": [{"nodes": [1], "dofs": (1, 3)}],
            "loads": [(9, (1.0, 0, 0))]}
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", E=3000, nu=0.35),
                  [case])
    txt = p.read_text()
    assert "FIXED1_1, 1, 1, 0.0" in txt and "FIXED1_1, 3, 3, 0.0" in txt
    assert "FIXED1_1, 1, 3, 0.0" not in txt


def test_supports_replace_fixed_nodes(tmp_path):
    p = tmp_path / "j.inp"
    case = dict(_sup_case(), fixed_nodes=[77, 78])
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", E=3000, nu=0.35),
                  [case])
    txt = p.read_text()
    assert "77" not in txt and "*NSET, NSET=FIXED1\n" not in txt


def test_the_legacy_deck_is_byte_for_byte_unchanged(tmp_path):
    """`supports` must not disturb a caller that never heard of it."""
    a, b = tmp_path / "a.inp", tmp_path / "b.inp"
    case = {"name": "c", "fixed_nodes": [1, 2, 3],
            "loads": [(10, (0.0, 0.0, -5.0))]}
    mat = ccx.material_block("PLA", E=3000, nu=0.35)
    ccx.write_inp(str(a), "m.inp", mat, [case])
    ccx.write_inp(str(b), "m.inp", mat, [dict(case)])
    txt = a.read_text()
    assert a.read_text() == b.read_text()
    assert "*NSET, NSET=FIXED1" in txt
    assert "FIXED1, 1, 3, 0.0" in txt
    assert "FIXED1_1" not in txt


def test_a_support_with_no_nodes_is_refused(tmp_path):
    case = {"name": "c", "supports": [{"nodes": [], "dofs": (1, 2, 3)}],
            "loads": [(1, (1.0, 0, 0))]}
    with pytest.raises(ValueError, match="float away"):
        ccx.write_inp(str(tmp_path / "j.inp"), "m.inp",
                      ccx.material_block("PLA", E=3000, nu=0.35), [case])


def test_a_dof_outside_one_to_three_is_refused(tmp_path):
    case = {"name": "c", "supports": [{"nodes": [1], "dofs": (1, 6)}],
            "loads": [(1, (1.0, 0, 0))]}
    with pytest.raises(ValueError, match="only 1, 2 and 3"):
        ccx.write_inp(str(tmp_path / "j.inp"), "m.inp",
                      ccx.material_block("PLA", E=3000, nu=0.35), [case])


def test_gravity_is_written_as_a_magnitude_and_a_direction(tmp_path):
    p = tmp_path / "j.inp"
    case = {"name": "g", "fixed_nodes": [1, 2], "loads": [],
            "gravity": [0.0, 0.0, -9806.65]}
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", ortho=ORTHO_PLA),
                  [case])
    txt = p.read_text()
    assert "*DLOAD, OP=NEW" in txt
    line = [ln for ln in txt.splitlines() if ln.startswith("part, GRAV")][0]
    v = [float(x) for x in line.split(",")[2:]]
    assert v[0] == pytest.approx(9806.65)
    assert v[1:] == pytest.approx([0.0, 0.0, -1.0])
    # ... and the deck it goes with has the density that makes it mean
    # anything (tonne/mm^3, because the deck is mm/N/MPa).
    assert "*DENSITY" in txt


def test_gravity_without_a_density_is_refused(tmp_path):
    """CalculiX would apply a body force of zero and report it as converged --
    a whole load case silently missing from a run that says it passed."""
    case = {"name": "g", "fixed_nodes": [1, 2],
            "loads": [(3, (1.0, 0, 0))], "gravity": [0, 0, -9806.65]}
    with pytest.raises(ValueError, match="no \\*DENSITY"):
        ccx.write_inp(str(tmp_path / "j.inp"), "m.inp",
                      ccx.material_block("PLA", E=3000, nu=0.35), [case])


def test_gravity_reaches_the_buckling_step_but_never_the_frequency_one(tmp_path):
    p = tmp_path / "j.inp"
    case = {"name": "g", "fixed_nodes": [1, 2],
            "loads": [(3, (0.0, 0.0, -5.0))], "gravity": [0, 0, -9806.65]}
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", ortho=ORTHO_PLA),
                  [case], analyses=("static", "buckle", "frequency"))
    txt = p.read_text()
    assert txt.count("*DLOAD, OP=NEW") == 2          # static + buckle
    freq = txt.split("*FREQUENCY")[1].split("*END STEP")[0]
    assert "*CLOAD" not in freq and "*DLOAD" not in freq


def test_a_case_with_neither_load_nor_gravity_is_still_refused(tmp_path):
    case = {"name": "c", "fixed_nodes": [1], "loads": []}
    with pytest.raises(ValueError, match="no loads"):
        ccx.write_inp(str(tmp_path / "j.inp"), "m.inp",
                      ccx.material_block("PLA", ortho=ORTHO_PLA), [case])


def test_every_support_set_gets_its_own_node_print(tmp_path):
    """`parse_dat` sums the per-set totals into one number per step; it can
    only do that if every set is actually printed."""
    p = tmp_path / "j.inp"
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", ortho=ORTHO_PLA),
                  [_sup_case()])
    txt = p.read_text()
    assert "*NODE PRINT, NSET=FIXED1_1, TOTALS=ONLY" in txt
    assert "*NODE PRINT, NSET=FIXED1_2, TOTALS=ONLY" in txt


# ------------------------------------------------------- parse_dat, for real

# Copied from a real CalculiX 2.23 .dat (box, static + buckle + frequency).
# The headers are spelled out letter by letter with spaces, and the values
# start three lines below the header, not one. Both of those defeat the
# obvious parser and were how the buckling factors and the frequencies came
# back empty before 2026-09-08.
DAT_REAL = """                        S T E P       1


                                INCREMENT     1


 total force (fx,fy,fz) for set FIXED1_1 and time  0.1000000E+01

        6.000000E+01  0.000000E+00  1.200000E+01

 total force (fx,fy,fz) for set FIXED1_2 and time  0.1000000E+01

        4.000000E+01  0.000000E+00  8.000000E+00

                        S T E P       2


 total force (fx,fy,fz) for set FIXED1_1 and time  0.1000000E+01

        6.000000E+01  0.000000E+00  1.200000E+01

 total force (fx,fy,fz) for set FIXED1_2 and time  0.1000000E+01

        4.000000E+01  0.000000E+00  8.000000E+00

     B U C K L I N G   F A C T O R   O U T P U T

 MODE NO       BUCKLING
                FACTOR

      1   0.2193971E+02
      2   0.7189457E+02

                    E I G E N V A L U E    N U M B E R     1


 total force (fx,fy,fz) for set FIXED1_1 and time  0.1000000E+01

       -3.158829E+00  1.899889E-01 -2.027598E+00

                        S T E P       3


     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.4581725E+09   0.2140496E+05   0.3406706E+04   0.0000000E+00
      2   0.1449182E+10   0.3806813E+05   0.6058731E+04   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT

      1  -0.3984138E-06   0.7828147E-06   0.9133587E-03
      2  -0.6341636E-05  -0.9141813E-03   0.4262835E-05

                    E I G E N V A L U E    N U M B E R     1


 total force (fx,fy,fz) for set FIXED1_1 and time  0.2000000E+01

        1.825423E+02 -3.586642E+02 -4.184758E+05
"""


def test_buckling_factors_survive_the_spaced_out_header(tmp_path):
    p = tmp_path / "j.dat"
    p.write_text(DAT_REAL)
    r = ccx.parse_dat(str(p))
    assert r["buckling_factors"] == pytest.approx([21.93971, 71.89457])


def test_frequencies_are_the_cycles_per_time_column(tmp_path):
    """CalculiX prints eigenvalue, rad/time, cycles/time and an imaginary
    part. Reporting rad/s as Hz overstates every natural frequency by 2*pi,
    and sqrt(4.5817e8)/2pi = 3406.7 Hz is the check."""
    p = tmp_path / "j.dat"
    p.write_text(DAT_REAL)
    r = ccx.parse_dat(str(p))
    assert r["frequencies_hz"] == pytest.approx([3406.706, 6058.731])
    assert r["frequencies_hz"][0] == pytest.approx(
        np.sqrt(0.4581725e9) / (2 * np.pi), rel=1e-4)
    # The participation factors that follow are not frequencies.
    assert len(r["frequencies_hz"]) == 2


def test_reaction_totals_are_one_per_step_summed_over_the_sets(tmp_path):
    """A buckling step prints a total force per mode and a frequency step one
    per mode, so counting blocks gives far more "steps" than the deck has.
    The caller indexes by step, so this has to be one entry per step."""
    p = tmp_path / "j.dat"
    p.write_text(DAT_REAL)
    r = ccx.parse_dat(str(p))
    assert len(r["reaction_totals"]) == 3
    assert r["reaction_totals"][0] == pytest.approx((100.0, 0.0, 20.0))
    assert r["reaction_totals"][1] == pytest.approx((100.0, 0.0, 20.0))
    # The frequency step has only modal totals; a modal reaction is not a load
    # balance, and a zero there would look like a part in equilibrium.
    assert r["reaction_totals"][2] is None
    # Every block is still available for anyone who has to debug the sum.
    assert len(r["reaction_blocks"]) == 6
    assert r["reaction_blocks"][0]["set"] == "FIXED1_1"


def test_a_missing_reaction_total_is_reported_as_unchecked_not_as_a_pass():
    e = ccx.equilibrium_error([0, 0, -10.0], None)
    assert e["pass"] is False and "could not be checked" in e["error"]


def test_the_old_single_block_dat_still_reads(tmp_path):
    """A .dat with no S T E P header at all -- the shape `test_ccx.py` pins."""
    p = tmp_path / "j.dat"
    p.write_text("\n total force (fx,fy,fz) for set FIXED1 and time  1.0\n\n"
                 "        -1.000000E+00  0.000000E+00  5.000000E+00\n")
    r = ccx.parse_dat(str(p))
    assert r["reaction_totals"] == [pytest.approx((-1.0, 0.0, 5.0))]


# -------------------------------------------------------- mesh_convergence

def _runs(disp, stress, hotspots=None):
    out = []
    for i, (t, d, s) in enumerate(zip((4.0, 2.0, 1.0), disp, stress)):
        r = {"target_mm": t, "max_disp_mm": d, "p99_5_stress_MPa": s}
        if hotspots:
            r["hotspot_xyz_mm"] = hotspots[i]
        out.append(r)
    return out


def test_convergence_is_judged_on_the_p99_5_stress():
    r = ccx.mesh_convergence(_runs([1.20, 1.02, 1.00], [20.0, 24.0, 24.5]))
    assert r["pass"] is True
    assert r["steps"][-1]["stress_change"] == pytest.approx(0.02041, abs=1e-4)
    assert "max_stress_MPa" not in r["note"]


def test_the_max_stress_fallback_says_it_is_a_fallback():
    """The maximum on a tet mesh sits on one re-entrant node and grows without
    limit under refinement, so a verdict written on it can never converge. It
    is accepted, and the note says the verdict is weaker than it looks."""
    runs = [{"target_mm": 4.0, "max_disp_mm": 1.20, "max_stress_MPa": 20.0},
            {"target_mm": 2.0, "max_disp_mm": 1.02, "max_stress_MPa": 24.0},
            {"target_mm": 1.0, "max_disp_mm": 1.00, "max_stress_MPa": 24.5}]
    r = ccx.mesh_convergence(runs)
    assert r["pass"] is True
    assert "max_stress_MPa was used" in r["note"]


def test_a_hotspot_that_moves_fails_even_when_the_numbers_settle():
    """A part can hold its displacement and its stress while the worst place
    walks from one fillet to another. Then the two meshes are not describing
    the same failure, whatever the numbers agree on."""
    hs = [[10.0, 0.0, 0.0], [10.2, 0.0, 0.0], [40.0, 0.0, 0.0]]
    r = ccx.mesh_convergence(_runs([1.20, 1.02, 1.00], [20.0, 24.0, 24.5], hs))
    assert r["pass"] is False
    assert r["steps"][-1]["hotspot_shift_mm"] == pytest.approx(29.8)
    assert "hotspot moved" in r["note"]
    assert "discretisation" in r["note"]


def test_a_hotspot_that_stays_put_passes():
    hs = [[10.0, 0.0, 0.0], [10.2, 0.0, 0.0], [10.4, 0.0, 0.0]]
    r = ccx.mesh_convergence(_runs([1.20, 1.02, 1.00], [20.0, 24.0, 24.5], hs))
    assert r["pass"] is True
    assert r["hotspot_tolerance_mm"] == pytest.approx(2.0)  # 2 x finest 1 mm


def test_the_hotspot_allowance_can_be_set_explicitly():
    hs = [[0, 0, 0], [0, 0, 0], [3.0, 0, 0]]
    runs = _runs([1.20, 1.02, 1.00], [20.0, 24.0, 24.5], hs)
    assert ccx.mesh_convergence(runs)["pass"] is False
    assert ccx.mesh_convergence(runs, hotspot_tol_mm=5.0)["pass"] is True


# ------------------------------------------------------------ compare_cases

VOXEL = [{"case": "LC1 vertical 200 N", "max_displacement_mm": 1.00,
          "safety_factor_p99_5": 2.0, "min_safety_factor": 1.4},
         {"case": "LC3x lateral 30 N", "max_displacement_mm": 2.00,
          "safety_factor_p99_5": 3.0, "min_safety_factor": 2.1}]
CCX = [{"case": "LC1", "max_disp_mm": 1.05, "safety_factor_p99_5": 1.9,
        "worst_element_safety_factor": 1.2},
       {"case": "LC3x", "max_disp_mm": 3.00, "safety_factor_p99_5": 2.2,
        "worst_element_safety_factor": 1.0}]


def test_cases_pair_on_the_first_token_of_the_name():
    r = ccx.compare_cases(VOXEL, CCX)
    assert r["total"] == 2 and r["within"] == 1
    by = {c["case"]: c for c in r["cases"]}
    assert by["LC1"]["voxel_case"] == "LC1 vertical 200 N"
    assert by["LC1"]["relative_difference"] == pytest.approx(0.04762, abs=1e-4)
    assert by["LC1"]["ratio_ccx_over_voxel"] == pytest.approx(1.05)
    assert by["LC1"]["pass"] is True


def test_every_case_is_reported_not_only_the_governing_one():
    """osmo_tripod agreed on the worst case and disagreed by 40 % on others
    (4 of 6 outside 15 %, known-issues A6). Reporting only the governing case
    would have hidden that."""
    r = ccx.compare_cases(VOXEL, CCX)
    bad = [c for c in r["cases"] if not c["pass"]]
    assert [c["case"] for c in bad] == ["LC3x"]
    assert bad[0]["relative_difference"] == pytest.approx(1.0 / 3.0, abs=1e-4)
    assert r["pass"] is False and "disagree" in r["note"]
    # Both safety factors from each side, so neither can be quoted alone.
    assert bad[0]["voxel_min_safety_factor"] == 2.1
    assert bad[0]["ccx_worst_element_safety_factor"] == 1.0


def test_an_unpaired_case_is_named_not_dropped():
    """A case missing from one side is not agreement."""
    r = ccx.compare_cases(VOXEL, CCX[:1] + [dict(CCX[1], case="LC9")])
    assert r["unmatched_voxel"] == ["LC3x"]
    assert r["unmatched_ccx"] == ["LC9"]
    assert r["pass"] is False and "unpaired" in r["note"]


def test_all_cases_agreeing_is_a_pass():
    ccx_ok = [dict(CCX[0]), dict(CCX[1], max_disp_mm=2.05)]
    r = ccx.compare_cases(VOXEL, ccx_ok)
    assert r["pass"] is True and r["within"] == r["total"] == 2


# ------------------------------------------------------------- the real run

@pytest.mark.skipif(find("gmsh") is None or find("calculix") is None,
                    reason="Gmsh and CalculiX are not both installed here")
def test_box_runs_static_buckle_and_frequency_for_real(tmp_path):
    """The one thing no fixture can prove: CalculiX accepts these decks and
    these parsers read what it actually wrote.

    A 20x10x5 box, clamped on the x=0 face and pushed back along -x with a
    transverse component, at ~3 mm elements. Every assertion below is one that
    would have caught a real failure: a deck CalculiX rejects, a buckling
    factor list that came back empty because the header is spelled
    "B U C K L I N G", a frequency read out of the rad/s column, or reactions
    that do not cancel the load because the node set is not where it looks.
    """
    stl = _write_box_stl(tmp_path / "box.stl", *BOX)
    geo = str(tmp_path / "box.geo")
    mesh = str(tmp_path / "box_mesh.inp")
    ccx.write_geo(geo, stl, 3.0)
    ccx.run_gmsh(geo, mesh)

    m = ccx.read_mesh(mesh)
    assert m["types"] == ["C3D10"] and m["elements"].shape[1] == 10

    # The midside numbering the whole module assumes, on a real Gmsh mesh.
    p = m["xyz"][m["elements"] - 1]
    for k, (a, b) in enumerate(((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))):
        assert np.abs(p[:, 4 + k] - 0.5 * (p[:, a] + p[:, b])).max() < 1e-6

    surf = ccx.surface_nodes(m["elements"])
    assert 0 < len(surf) < len(m["ids"])
    x = m["xyz"][:, 0]
    on_face = np.abs(x) < 1e-6
    for nid in m["ids"][on_face]:
        assert int(nid) in set(surf.tolist())

    quality = ccx.mesh_quality(m["xyz"], m["elements"], ids=m["ids"])
    assert quality["pass"] is True, quality

    mp = ccx.mass_properties(m["xyz"], m["elements"], 1240.0, ids=m["ids"])
    assert mp["volume_mm3"] == pytest.approx(BOX[0] * BOX[1] * BOX[2], rel=1e-6)

    fixed = [int(n) for n in surf if x[n - 1] < 1e-6]
    tip = [int(n) for n in surf if x[n - 1] > BOX[0] - 1e-6]
    assert fixed and tip
    total = np.array([-100.0, 0.0, -20.0])           # axial compression + side
    loads = [(n, tuple(total / len(tip))) for n in tip]

    _mat, ortho, _info = material_model("PLA")
    block = ccx.material_block("PLA", ortho=ortho, build_axis=2)
    inp = str(tmp_path / "job.inp")
    ccx.write_inp(inp, mesh, block,
                  [{"name": "axial", "fixed_nodes": fixed, "loads": loads}],
                  analyses=("static", "buckle", "frequency"))
    out, base = ccx.run_ccx(inp)
    ok, why = ccx.converged(out)
    assert ok, why

    res = ccx.parse_frd(base + ".frd")
    dat = ccx.parse_dat(base + ".dat")

    static = res["steps"][0]
    disp = np.array(list(static["displacement"].values()))
    assert np.linalg.norm(disp, axis=1).max() > 0.0

    # Strain and nodal forces are in the frd, and the energy is finite.
    ids = sorted(set(static["stress"]) & set(static["strain"]))
    assert ids
    sig = np.array([static["stress"][i] for i in ids])
    eps = np.array([static["strain"][i] for i in ids])
    u = ccx.strain_energy_density(sig, eps)
    assert np.isfinite(u).all() and u.max() > 0
    assert (ccx.tresca(sig) >= 0).all()

    # A compressed column has a positive buckling factor ...
    assert dat["buckling_factors"], "no buckling factors were parsed"
    assert max(dat["buckling_factors"]) > 0
    # ... and a 20 mm PLA box rings somewhere sane, in Hz not rad/s.
    assert dat["frequencies_hz"], "no frequencies were parsed"
    assert any(10.0 < f < 1e5 for f in dat["frequencies_hz"])

    # One reaction total per step, and it cancels the applied load.
    assert len(dat["reaction_totals"]) == 3
    bal = ccx.equilibrium_error(total, dat["reaction_totals"][0])
    assert bal["pass"] is True, bal
    assert bal["relative_error"] < 0.01

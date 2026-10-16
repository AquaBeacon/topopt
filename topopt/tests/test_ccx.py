"""CalculiX deck generation, result parsing, and the exit-code-3 path.

Neither Gmsh nor CalculiX is installed in this environment, so the two
`subprocess` calls cannot be exercised here. Everything either side of them
can be, and is: the deck is text, the results are text, and the checks are
arithmetic. Those are also the parts most likely to be wrong -- a solver that
runs is loudly wrong, a deck with the shear constants transposed is quietly
wrong.

What is NOT verified by this file, and is therefore not claimed anywhere:
that CalculiX accepts these decks, and that the .frd this parser expects is
byte-for-byte what a real run writes.
"""

import numpy as np
import pytest

from topopt import ccx
from topopt.external import ToolMissing, find
from topopt.loadcase import region_mask_points
from topopt.materials import ORTHOTROPIC, Orthotropic
from tests.test_orthotropic import ASYM


# ------------------------------------------------------------ exit code 3

@pytest.mark.parametrize("tool", ["gmsh", "calculix"])
def test_a_missing_tool_is_exit_code_3_with_install_instructions(tool):
    if find(tool) is not None:
        pytest.skip("%s is installed here" % tool)
    with pytest.raises(ToolMissing) as e:
        ccx.require(tool, "the re-analysis") if hasattr(ccx, "require") else \
            __import__("topopt.external", fromlist=["require"]).require(tool)
    assert e.value.exit_code == 3
    msg = str(e.value)
    assert "not installed" in msg
    # An error that does not say how to fix it just moves the problem.
    assert "Install it with one of:" in msg
    assert "Windows" in msg and "Linux" in msg


def test_the_cli_reports_missing_tools_without_running_anything():
    from topopt.cli.ccx import main
    assert main(["--check"]) in (0, 3)


# --------------------------------------------------------- material card

def test_calculix_ortho_order_is_not_this_codebase_voigt_order():
    """CalculiX wants D1111 D1122 D2222 D1133 D2233 D3333 D1212 D1313 D2323,
    i.e. shears xy, zx, yz. This module's Voigt slots are xy, yz, zx. Emitting
    slots 3,4,5 in order would swap Gyz with Gzx -- a deck that solves happily
    and is wrong about which direction is weak."""
    c = ccx.ortho_constants(ASYM, build_axis=2, raster_axis=0)
    assert len(c) == 9
    assert c[6] == pytest.approx(ASYM.Gxy, rel=1e-9)   # D1212
    assert c[7] == pytest.approx(ASYM.Gzx, rel=1e-9)   # D1313
    assert c[8] == pytest.approx(ASYM.Gyz, rel=1e-9)   # D2323
    # And they really are three different numbers, so the check has teeth.
    assert len({round(v) for v in c[6:]}) == 3


def test_the_normal_block_is_the_upper_triangle_by_columns():
    from topopt.hex8 import orthotropic_D
    D, _ = orthotropic_D(ASYM, 2, 0, normalise=False)
    c = ccx.ortho_constants(ASYM, 2, 0)
    assert c[:6] == pytest.approx([D[0, 0], D[0, 1], D[1, 1],
                                   D[0, 2], D[1, 2], D[2, 2]], rel=1e-9)


def test_build_axis_reaches_the_calculix_card():
    a = ccx.ortho_constants(ASYM, build_axis=2)
    b = ccx.ortho_constants(ASYM, build_axis=0)
    assert a != pytest.approx(b, rel=1e-9)


def test_density_is_written_in_tonne_per_mm3():
    """The deck is mm/N/MPa, so density must be tonne/mm^3. Getting this wrong
    scales every buckling factor and frequency by 1e9 while leaving the static
    results perfect -- so a static check would never catch it."""
    block = ccx.material_block("PLA", ortho=ORTHOTROPIC["PLA"])
    lines = block.splitlines()
    i = lines.index("*DENSITY")
    assert float(lines[i + 1]) == pytest.approx(1240 * 1e-12, rel=1e-9)


def test_isotropic_material_block_needs_both_constants():
    with pytest.raises(ValueError, match="both E and nu"):
        ccx.material_block("PLA", E=3000)


def test_material_block_is_orthotropic_when_given_a_card():
    assert "TYPE=ORTHO" in ccx.material_block("PLA", ortho=ORTHOTROPIC["PLA"])
    assert "TYPE=ISO" in ccx.material_block("PLA", E=3000, nu=0.35,
                                            density_kg_m3=1240)


# ---------------------------------------------------------------- the deck

def _case(name="c1", n=4):
    return {"name": name, "fixed_nodes": list(range(1, n + 1)),
            "loads": [(10, (0.0, 0.0, -5.0)), (11, (1.0, 0.0, 0.0))]}


def test_deck_has_a_step_a_boundary_and_a_load(tmp_path):
    p = tmp_path / "job.inp"
    ccx.write_inp(str(p), "mesh.inp",
                  ccx.material_block("PLA", ortho=ORTHOTROPIC["PLA"]),
                  [_case()])
    txt = p.read_text()
    assert "*INCLUDE, INPUT=mesh.inp" in txt
    assert "*STATIC" in txt and "*BOUNDARY" in txt and "*CLOAD" in txt
    assert "*SOLID SECTION" in txt
    # Reaction forces are requested so the equilibrium check has something to
    # check; without RF the balance is unverifiable and would silently pass.
    assert "U, RF" in txt
    assert "10, 3, -5" in txt


def test_zero_force_components_are_not_written(tmp_path):
    p = tmp_path / "job.inp"
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", E=3000, nu=0.35),
                  [_case()])
    txt = p.read_text()
    assert "10, 1," not in txt          # node 10 has no x component


def test_a_case_with_no_supports_is_refused(tmp_path):
    """A part with no supports is a singular solve, and CalculiX's message for
    that is much harder to read than this one."""
    c = _case()
    c["fixed_nodes"] = []
    with pytest.raises(ValueError, match="float away"):
        ccx.write_inp(str(tmp_path / "j.inp"), "m.inp",
                      ccx.material_block("PLA", E=3000, nu=0.35), [c])


def test_a_case_with_no_loads_is_refused(tmp_path):
    c = _case()
    c["loads"] = []
    with pytest.raises(ValueError, match="no loads"):
        ccx.write_inp(str(tmp_path / "j.inp"), "m.inp",
                      ccx.material_block("PLA", E=3000, nu=0.35), [c])


def test_buckling_and_frequency_steps_are_emitted(tmp_path):
    p = tmp_path / "j.inp"
    ccx.write_inp(str(p), "m.inp", ccx.material_block("PLA", E=3000, nu=0.35),
                  [_case()], analyses=("static", "buckle", "frequency"))
    txt = p.read_text()
    assert "*BUCKLE" in txt and "*FREQUENCY" in txt
    # A frequency step with a load applied is a modelling error; modes are a
    # property of mass and stiffness alone.
    freq = txt.split("*FREQUENCY")[1].split("*END STEP")[0]
    assert "*CLOAD" not in freq


def test_second_order_elements_are_requested_in_the_geo(tmp_path):
    """Linear tets shear-lock in bending and report a beam several times
    stiffer than it is -- the error direction that lets a weak part pass."""
    p = tmp_path / "m.geo"
    ccx.write_geo(str(p), str(tmp_path / "part.stl"), 2.0)
    txt = p.read_text()
    assert "Mesh.ElementOrder = 2" in txt
    assert "Mesh.SecondOrderIncomplete = 0" in txt


# ------------------------------------------------------------- the parsers

FRD = """    1C
    2C                  3                                    1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E+01 0.00000E+00 0.00000E+00
 -1         3 0.00000E+00 1.00000E+01 0.00000E+00
 -3
  -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E-02 0.00000E+00-5.00000E-02
 -1         3 0.00000E+00 1.00000E-02 0.00000E+00
 -3
  -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -1         1 1.00000E+01 2.00000E+00 3.00000E+00 4.00000E-01 5.00000E-01 6.00000E-01
 -1         2 2.00000E+01 4.00000E+00 6.00000E+00 8.00000E-01 1.00000E+00 1.20000E+00
 -3
"""


def test_frd_parser_reads_nodes_displacement_and_stress(tmp_path):
    p = tmp_path / "j.frd"
    p.write_text(FRD)
    r = ccx.parse_frd(str(p))
    assert len(r["nodes"]) == 3
    assert r["nodes"][2] == pytest.approx((10.0, 0.0, 0.0))
    assert r["displacement"][2] == pytest.approx((0.01, 0.0, -0.05))
    assert r["stress"][2] == pytest.approx((20.0, 4.0, 6.0, 0.8, 1.0, 1.2))


def test_frd_stress_order_is_the_one_this_codebase_uses(tmp_path):
    """CalculiX writes SXX SYY SZZ SXY SYZ SZX, which is this module's Voigt
    order -- so no permutation happens on the way in. Pinned, because a future
    'tidy-up' that reorders one side and not the other is invisible."""
    p = tmp_path / "j.frd"
    p.write_text(FRD)
    s = ccx.parse_frd(str(p))["stress"][1]
    from topopt.hex8 import von_mises
    assert von_mises(np.array([s]))[0] == pytest.approx(
        von_mises(np.array([[10.0, 2.0, 3.0, 0.4, 0.5, 0.6]]))[0])


DAT = """
     total force (fx,fy,fz) for set FIXED1 and time  0.1000000E+01

        -1.000000E+00  0.000000E+00  5.000000E+00
"""


def test_dat_parser_reads_reaction_totals(tmp_path):
    p = tmp_path / "j.dat"
    p.write_text(DAT)
    r = ccx.parse_dat(str(p))
    assert r["reaction_totals"][0] == pytest.approx((-1.0, 0.0, 5.0))


def test_a_missing_dat_is_empty_not_an_error(tmp_path):
    r = ccx.parse_dat(str(tmp_path / "nope.dat"))
    assert r["reaction_totals"] == []


# --------------------------------------------------------------- the checks

def test_a_diverged_run_is_not_converged():
    ok, why = ccx.converged("... *ERROR in nonlingeo ...")
    assert ok is False and "error" in why.lower()
    ok, _ = ccx.converged("the solve did not converge")
    assert ok is False
    ok, _ = ccx.converged("Job finished")
    assert ok is True


def test_equilibrium_error_passes_when_reactions_cancel_the_load():
    e = ccx.equilibrium_error([0, 0, -150.0], [0, 0, 150.0])
    assert e["relative_error"] == pytest.approx(0.0)
    assert e["pass"] is True


def test_equilibrium_error_fails_at_two_percent():
    e = ccx.equilibrium_error([0, 0, -150.0], [0, 0, 147.0])
    assert e["relative_error"] == pytest.approx(0.02, rel=1e-6)
    assert e["pass"] is False


def test_equilibrium_with_no_load_is_not_a_pass():
    """Zero applied load makes the relative error undefined; reporting that as
    a pass would let a case with no load through the check."""
    e = ccx.equilibrium_error([0, 0, 0], [0, 0, 0])
    assert e["pass"] is False
    assert e["relative_error"] is None


def test_moment_balance_catches_a_reaction_at_the_wrong_place():
    """The check that force balance cannot make. Both cases below have forces
    that cancel exactly; only the moment arm differs, so this is the one that
    catches a node set selected 10 mm from where it was meant to be."""
    a_pts, a_f = [(100.0, 0, 0)], [(0, 0, -10.0)]
    good = ccx.moment_error(a_pts, a_f, [(100.0, 0, 0)], [(0, 0, 10.0)])
    assert good["relative_error"] == pytest.approx(0.0)
    assert good["pass"] is True

    bad = ccx.moment_error(a_pts, a_f, [(90.0, 0, 0)], [(0, 0, 10.0)])
    assert bad["pass"] is False
    assert bad["relative_error"] == pytest.approx(0.1, rel=1e-9)
    # ... while the force balance is perfectly happy with both.
    assert ccx.equilibrium_error([0, 0, -10.0], [0, 0, 10.0])["pass"] is True


def test_voxel_comparison_reports_the_difference_either_way():
    ok = ccx.compare_with_voxel(1.00, 1.10)
    assert ok["relative_difference"] == pytest.approx(0.0909, abs=1e-4)
    assert ok["pass"] is True
    bad = ccx.compare_with_voxel(1.00, 2.00)
    assert bad["pass"] is False
    assert "disagree" in bad["note"]


def test_mesh_convergence_needs_three_runs():
    r = ccx.mesh_convergence([{"target_mm": 2, "max_disp_mm": 1.0}])
    assert r["pass"] is False and "three runs" in r["error"]


def test_mesh_convergence_passes_when_the_answer_stops_moving():
    runs = [{"target_mm": 4.0, "max_disp_mm": 1.20, "max_stress_MPa": 20.0},
            {"target_mm": 2.0, "max_disp_mm": 1.02, "max_stress_MPa": 24.0},
            {"target_mm": 1.0, "max_disp_mm": 1.00, "max_stress_MPa": 24.5}]
    r = ccx.mesh_convergence(runs)
    assert r["pass"] is True
    assert r["steps"][-1]["displacement_change"] < 0.05


def test_mesh_convergence_fails_when_it_is_still_moving():
    runs = [{"target_mm": 4.0, "max_disp_mm": 1.0, "max_stress_MPa": 20.0},
            {"target_mm": 2.0, "max_disp_mm": 1.5, "max_stress_MPa": 30.0},
            {"target_mm": 1.0, "max_disp_mm": 2.2, "max_stress_MPa": 46.0}]
    r = ccx.mesh_convergence(runs)
    assert r["pass"] is False
    assert "property of the discretisation" in r["note"]


# -------------------------------------------------------- point-wise regions

def test_point_regions_agree_with_the_voxel_ones():
    """Both solvers must read a load region the same way, or the two analyses
    are of different problems."""
    pts = np.array([[0, 0, 0], [5, 5, 5], [11, 0, 0]], float)
    box = {"type": "box", "min": [-1, -1, -1], "max": [10, 10, 10]}
    assert region_mask_points(box, pts).tolist() == [True, True, False]

    cyl = {"type": "cylinder", "axis": "Z", "centre": [0, 0, 0], "r": 6.0}
    assert region_mask_points(cyl, pts).tolist() == [True, False, False]

    sph = {"type": "sphere", "centre": [5, 5, 5], "r": 1.0}
    assert region_mask_points(sph, pts).tolist() == [False, True, False]

    assert region_mask_points({"type": "all"}, pts).all()
    assert region_mask_points(dict(box, invert=True), pts).tolist() == \
        [False, False, True]


def test_an_unknown_region_type_is_refused():
    with pytest.raises(ValueError, match="unknown region type"):
        region_mask_points({"type": "banana"}, np.zeros((1, 3)))


def test_the_deck_points_at_the_element_set_gmsh_writes(tmp_path):
    # Gmsh names the element set after the physical volume in the .geo; the
    # *SOLID SECTION must reference that name, not a set nobody writes
    # (the first real run stopped on "element set EALL has not yet been
    # defined").
    geo = tmp_path / "part.geo"
    ccx.write_geo(str(geo), str(tmp_path / "part.stl"), 2.0)
    assert 'Physical Volume("%s")' % ccx.MESH_ELSET in geo.read_text()
    p = tmp_path / "job.inp"
    ccx.write_inp(str(p), "mesh.inp", ccx.material_block("PLA", E=3000, nu=0.35),
                  [_case()])
    assert "*SOLID SECTION, ELSET=%s," % ccx.MESH_ELSET in p.read_text()
    assert "EALL" not in p.read_text()


def test_every_step_resets_its_boundary_and_load(tmp_path):
    # CalculiX carries *BOUNDARY and *CLOAD over between steps unless the
    # card says OP=NEW. Without it a six-case deck solves case 4 with the
    # clamps of cases 1-3 still on (first real run: every later case read
    # 0.02 mm and SF 11; the voxel FEA had 2.8 mm and SF 2.0).
    p = tmp_path / "job.inp"
    ccx.write_inp(str(p), "mesh.inp", ccx.material_block("PLA", E=3000, nu=0.35),
                  [_case("a"), _case("b", n=6)])
    txt = p.read_text()
    assert txt.count("*BOUNDARY, OP=NEW") == 2
    assert txt.count("*CLOAD, OP=NEW") == 2
    assert "*BOUNDARY\n" not in txt and "*CLOAD\n" not in txt


FRD_TWO_STEPS = FRD.replace("  -4  DISP", "  100CL  101 1.000000000  3  0  1  1\n  -4  DISP") \
    .replace("  -4  STRESS", "  100CL  101 1.000000000  3  0  1  1\n  -4  STRESS") + """  100CL  102 2.000000000  3  0  2  1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 2.00000E-02 0.00000E+00-1.00000E-01
 -1         3 0.00000E+00 2.00000E-02 0.00000E+00
 -3
  100CL  102 2.000000000  3  0  2  1
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -1         1 1.00000E+01 2.00000E+00 3.00000E+00 4.00000E-01 5.00000E-01 6.00000E-01
 -1         2 4.00000E+01 8.00000E+00 1.20000E+01 1.60000E+00 2.00000E+00 2.40000E+00
 -3
"""


def test_frd_parser_keeps_every_step_apart(tmp_path):
    p = tmp_path / "two.frd"
    p.write_text(FRD_TWO_STEPS)
    r = ccx.parse_frd(str(p))
    assert [st["step"] for st in r["steps"]] == [1, 2]
    assert r["steps"][0]["displacement"][2] == pytest.approx((0.01, 0.0, -0.05))
    assert r["steps"][1]["displacement"][2] == pytest.approx((0.02, 0.0, -0.10))
    assert r["steps"][1]["stress"][2][0] == pytest.approx(40.0)
    # The top level is the last step, for one-step callers.
    assert r["displacement"][2] == pytest.approx((0.02, 0.0, -0.10))


def test_a_single_step_file_without_headers_is_step_one(tmp_path):
    p = tmp_path / "one.frd"
    p.write_text(FRD)
    r = ccx.parse_frd(str(p))
    assert len(r["steps"]) == 1 and r["steps"][0]["step"] == 1


def _dirty_stl(path):
    """A closed tetrahedron plus one zero-area triangle."""
    import struct
    import numpy as np
    v = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]], float)
    faces = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)]
    tris = [v[list(f)] for f in faces] + [np.array([[0, 0, 0], [5, 0, 0], [10, 0, 0]], float)]
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(tris)))
        for t in tris:
            fh.write(struct.pack("<3f", 0, 0, 0))
            fh.write(struct.pack("<9f", *t.ravel()))
            fh.write(b"\0\0")


def test_a_dirty_shell_is_refused_before_meshing(tmp_path):
    from topopt.cli.ccx import prepare_shell
    from topopt.schema import InputError
    p = tmp_path / "dirty.stl"
    _dirty_stl(str(p))
    with pytest.raises(InputError, match="not a closed shell"):
        prepare_shell(str(p), str(tmp_path))


def test_a_dirty_shell_is_repaired_when_asked(tmp_path):
    pytest.importorskip("pymeshfix")
    from topopt.cli.ccx import prepare_shell
    p = tmp_path / "dirty.stl"
    _dirty_stl(str(p))
    stl, info = prepare_shell(str(p), str(tmp_path), repair=True, quiet=True)
    assert info["repaired"] and stl.endswith("_repaired.stl")
    assert info["repair"]["after"]["closed_shell"]
    assert abs(info["repair"]["volume_after_mm3"] - 1000.0 / 6.0) < 1.0

"""Underwater loads: pressure, buoyancy, drag (spec Phase 7).

Every physical quantity here is checked against the closed form, because unit
errors in this module do not look like errors. The first version of
`pressure_MPa` was wrong by a factor of 1000 -- it reported 98 MPa at 10 m
instead of 0.098 -- and nothing about that number is obviously absurd until
you write down the arithmetic.
"""

import numpy as np
import pytest

from topopt import underwater as uw
from topopt.voxel import Grid


G = 9.80665


def _cube(side_mm=100.0, h=4.0):
    n = int(round(side_mm / h))
    return Grid(solid=np.ones((n, n, n), bool), h=h, origin=np.zeros(3))


# ---------------------------------------------------------------- pressure

@pytest.mark.parametrize("depth_m", [1.0, 10.0, 100.0])
def test_pressure_matches_rho_g_h(depth_m):
    got = uw.pressure_MPa(depth_m * 1000.0)
    expect = 1000.0 * G * depth_m / 1e6
    assert got == pytest.approx(expect, rel=1e-12)


def test_ten_metres_of_water_is_about_one_atmosphere():
    """The sanity check that would have caught the 1000x error immediately."""
    assert uw.pressure_MPa(10_000.0) == pytest.approx(0.0981, abs=1e-4)


def test_seawater_is_denser_than_fresh():
    a = uw.pressure_MPa(10_000.0, uw.RHO_FRESH)
    b = uw.pressure_MPa(10_000.0, uw.RHO_SEA)
    assert b > a
    assert b / a == pytest.approx(1.025, rel=1e-9)


def test_above_the_surface_is_zero_not_negative():
    assert uw.pressure_MPa(-5000.0) == 0.0
    assert np.all(uw.pressure_MPa(np.array([-100.0, 0.0, 100.0])) >= 0)


def test_surface_pressure_offset_is_added():
    assert uw.pressure_MPa(0.0, surface_MPa=0.1) == pytest.approx(0.1)


# --------------------------------------------------------------- buoyancy

def test_buoyancy_of_a_known_cube():
    g = _cube(100.0)
    b = uw.buoyancy(g, g.solid, 1.24)
    # Tolerances are 5e-5 because the reported values are rounded to four
    # decimals for readability; they are not slack in the physics.
    assert b["volume_mm3"] == pytest.approx(1e6, rel=1e-9)
    # 1 litre of fresh water displaced
    assert b["buoyant_force_N"] == pytest.approx(1000 * 1e-3 * G, abs=5e-5)
    # PLA at 1.24 g/cm3
    assert b["dry_weight_N"] == pytest.approx(1.24e-6 * 1e6 * G, abs=5e-5)


def test_a_pla_part_weighs_about_a_fifth_as_much_in_water():
    """1 - 1.00/1.24 = 0.194. Easy to leave out, and it makes every gravity
    case wrong in the safe direction, which is why it is easy to leave out."""
    g = _cube(100.0)
    b = uw.buoyancy(g, g.solid, 1.24)
    assert b["weight_fraction_of_dry"] == pytest.approx(1 - 1.0 / 1.24,
                                                        abs=5e-5)


def test_a_part_lighter_than_water_pulls_upward():
    g = _cube(100.0)
    b = uw.buoyancy(g, g.solid, 0.90)          # a foam or a sealed float
    assert b["net_weight_in_water_N"] < 0
    assert "pulls upward" in b["note"]


def test_centre_of_buoyancy_of_a_cube_is_its_centre():
    g = _cube(100.0)
    b = uw.buoyancy(g, g.solid, 1.24)
    assert b["centre_of_buoyancy_mm"] == pytest.approx([50.0, 50.0, 50.0],
                                                       abs=1e-6)


def test_partial_submersion_displaces_less_and_from_the_bottom():
    g = _cube(100.0)
    half = uw.buoyancy(g, g.solid, 1.24, submerged_fraction=0.5)
    full = uw.buoyancy(g, g.solid, 1.24)
    # 1e-3 relative: the cube is 25 voxels a side, so "half" lands on a whole
    # number of cells and cannot be exact.
    assert half["displaced_volume_mm3"] == pytest.approx(
        0.5 * full["displaced_volume_mm3"], rel=1e-3)
    # A part sitting in water submerges from the bottom up.
    assert half["centre_of_buoyancy_mm"][2] < full["centre_of_buoyancy_mm"][2]


def test_seawater_lifts_more():
    g = _cube(100.0)
    a = uw.buoyancy(g, g.solid, 1.24, uw.RHO_FRESH)
    b = uw.buoyancy(g, g.solid, 1.24, uw.RHO_SEA)
    assert b["buoyant_force_N"] > a["buoyant_force_N"]
    assert b["net_weight_in_water_N"] < a["net_weight_in_water_N"]


def test_no_material_reports_an_error_rather_than_zero_buoyancy():
    g = _cube(100.0)
    b = uw.buoyancy(g, np.zeros_like(g.solid), 1.24)
    assert "error" in b


# ------------------------------------------------------------------- drag

def test_drag_matches_the_closed_form():
    g = _cube(100.0)
    d = uw.drag(g, g.solid, 1.0, "cube")
    # 0.5 * 1000 * 1^2 * 0.01 m2 * 1.05
    assert d["drag_force_N"] == pytest.approx(0.5 * 1000 * 1.0 * 0.01 * 1.05,
                                              rel=1e-9)


def test_drag_grows_with_the_square_of_speed():
    g = _cube(100.0)
    a = uw.drag(g, g.solid, 1.0, "cube")["drag_force_N"]
    b = uw.drag(g, g.solid, 2.0, "cube")["drag_force_N"]
    assert b / a == pytest.approx(4.0, rel=1e-9)


def test_projected_area_is_the_silhouette_not_the_face_count():
    """Two plates in line present one plate's area to the flow, not two: a
    member hiding behind another does not see the free stream twice."""
    g = Grid(solid=np.zeros((10, 6, 6), bool), h=1.0, origin=np.zeros(3))
    m = g.solid.copy()
    m[2, :, :] = True
    m[7, :, :] = True                       # directly behind the first
    area, _ = uw.projected_area(m, 1.0, flow_axis=0)
    assert area == pytest.approx(36.0)      # 6 x 6, once


def test_every_drag_output_says_it_is_a_screening_number():
    g = _cube(100.0)
    d = uw.drag(g, g.solid, 1.0)
    assert d["model"] == "simplified"
    assert "not a flow solution" in d["note"]
    assert "Do not quote it as a predicted force" in d["note"]


def test_worst_flow_direction_scans_all_three_axes():
    g = Grid(solid=np.zeros((20, 5, 5), bool), h=1.0, origin=np.zeros(3))
    m = g.solid.copy()
    m[:, :, :] = True                       # a 20 x 5 x 5 bar
    w = uw.worst_flow_direction(g, m, 1.0)
    assert len(w["per_axis"]) == 3
    # Broadside (flow along Y or Z) sees 20x5; end-on (along X) sees 5x5.
    assert w["worst_axis"] in ("Y", "Z")
    end_on = next(e for e in w["per_axis"] if e["flow_axis"] == "X")
    assert w["worst_drag_N"] == pytest.approx(4 * end_on["drag_force_N"],
                                              abs=1e-3)


def test_a_named_cd_and_a_numeric_cd_agree():
    g = _cube(100.0)
    a = uw.drag(g, g.solid, 1.0, "cube")["drag_force_N"]
    b = uw.drag(g, g.solid, 1.0, 1.05)["drag_force_N"]
    assert a == pytest.approx(b, rel=1e-12)


def test_drag_forces_are_spread_over_the_upstream_faces():
    """A drag resultant dumped on one node makes a stress singularity that
    then dominates every percentile the report prints."""
    g = _cube(40.0, h=4.0)
    f = uw.drag_nodal_forces(g, g.solid, 10.0, flow_axis=0)
    assert f.reshape(-1, 3).sum(axis=0)[0] == pytest.approx(10.0, rel=1e-9)
    loaded = np.count_nonzero(np.linalg.norm(f.reshape(-1, 3), axis=1))
    assert loaded > 50


# ----------------------------------------------------- open frame vs sealed

def test_an_open_frame_gets_no_net_crushing_load():
    """Water reaches both sides, so the resultant is not real. Treating an
    open frame as a pressure vessel is how a sound bracket gets three times
    thicker for nothing."""
    g = _cube(40.0, h=4.0)
    f, info = uw.hydrostatic_nodal_forces(g, g.solid, 10_000.0, sealed=False)
    assert np.abs(f).max() == 0.0
    assert info["sealed"] is False
    assert "three times thicker" in info["note"]
    # ... but the wetted area and the pressure are still reported.
    assert info["wetted_area_mm2"] > 0
    assert info["max_pressure_MPa"] > 0


def test_a_sealed_body_does_get_the_load():
    g = _cube(40.0, h=4.0)
    f, info = uw.hydrostatic_nodal_forces(g, g.solid, 10_000.0, sealed=True)
    assert np.abs(f).max() > 0
    assert info["sealed"] is True
    assert "nothing pushing back from inside" in info["note"]


def test_sealed_pressure_load_is_self_equilibrated_horizontally():
    """A cube deep under water is squeezed equally from left and right, so the
    horizontal resultant is zero; only the top-bottom difference survives, and
    that difference IS the buoyant force."""
    g = _cube(40.0, h=4.0)
    _f, info = uw.hydrostatic_nodal_forces(g, g.solid, 10_000.0, sealed=True)
    rx, ry, rz = info["resultant_N"]
    assert rx == pytest.approx(0.0, abs=1e-6)
    assert ry == pytest.approx(0.0, abs=1e-6)
    # And that vertical resultant IS the buoyant force: integrating pressure
    # over a closed surface reproduces Archimedes, which is an independent
    # check on the face-depth handling that no amount of staring at the sign
    # convention would give.
    expect = uw.RHO_FRESH * 1e-9 * G * (40.0 ** 3)
    assert rz == pytest.approx(expect, abs=5e-5)


def test_deeper_water_presses_harder():
    g = _cube(40.0, h=4.0)
    _a, ia = uw.hydrostatic_nodal_forces(g, g.solid, 5_000.0, sealed=True)
    _b, ib = uw.hydrostatic_nodal_forces(g, g.solid, 50_000.0, sealed=True)
    assert ib["max_pressure_MPa"] > 9 * ia["max_pressure_MPa"]


# ----------------------------------------------------------------- report

def test_report_states_its_assumptions():
    g = _cube(100.0)
    r = uw.report(g, g.solid, 1.24, depth_mm=10_000.0, velocity_m_s=1.0)
    assert "buoyancy" in r and "hydrostatic" in r and "drag" in r
    joined = " ".join(r["assumptions"])
    assert "open frame" in joined
    assert "screening number" in joined
    assert "no wave loading" in joined


def test_report_omits_what_was_not_asked_for():
    g = _cube(100.0)
    r = uw.report(g, g.solid, 1.24)
    assert "hydrostatic" not in r and "drag" not in r
    assert "buoyancy" in r


def test_the_cli_refuses_a_run_with_nothing_to_compute():
    from topopt.cli.underwater import main
    with pytest.raises(SystemExit, match="nothing to compute"):
        main(["nonexistent.stl"])


def test_sealed_hollow_displaces_envelope_but_weighs_only_the_walls():
    g = _cube(40.0, h=4.0)
    g.solid[1:-1, 1:-1, 1:-1] = False
    r = uw.report(g, g.solid, 1.24, depth_mm=10000, sealed=True)
    assert r["buoyancy"]["displaced_volume_mm3"] == 40 ** 3
    assert r["buoyancy"]["volume_mm3"] == 40 ** 3 - 32 ** 3
    assert r["hydrostatic"]["wetted_area_mm2"] == 6 * 40 ** 2
    assert r["hydrostatic"]["resultant_N"][2] == r["buoyancy"]["buoyant_force_N"]
    force, _ = uw.hydrostatic_nodal_forces(g, g.solid, 10040, sealed=True)
    assert np.all(force[2:-2, 2:-2, 2:-2] == 0)  # no exterior pressure inside


def test_open_cavity_does_not_gain_sealed_displacement():
    g = _cube(40.0, h=4.0)
    g.solid[1:-1, 1:-1, 1:] = False
    r = uw.report(g, g.solid, 1.24, depth_mm=10000, sealed=True)
    assert r["buoyancy"]["displaced_volume_mm3"] == g.n_solid * g.h ** 3


def test_report_depth_is_measured_from_part_not_padding():
    g = _cube(40.0, h=4.0)
    padded = Grid(solid=np.pad(g.solid, 3), h=4, origin=np.full(3, -12.0))
    a = uw.report(g, g.solid, 1.24, depth_mm=10000, sealed=True)
    b = uw.report(padded, padded.solid, 1.24, depth_mm=10000, sealed=True)
    assert a["hydrostatic"] == b["hydrostatic"]
    assert a["hydrostatic"]["max_pressure_MPa"] == pytest.approx(
        uw.pressure_MPa(10040), abs=5e-7)


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("axis", [0, 1, 2])
def test_summary_fast_path_matches_nodal_integration(sealed, axis):
    g = _cube(40.0, h=4.0)
    g.solid[2:5, 3:8, 4:9] = False
    force, reference = uw.hydrostatic_nodal_forces(g, g.solid, 10000,
                                                  sealed=sealed, build_axis_up=axis)
    skipped, fast = uw.hydrostatic_nodal_forces(g, g.solid, 10000,
                                               sealed=sealed, build_axis_up=axis, _nodal=False)
    assert skipped is None
    assert reference == fast
    if sealed:
        assert fast["resultant_N"] == pytest.approx(force.reshape(-1, 3).sum(axis=0), abs=5e-5)


@pytest.mark.parametrize("args", [["--depth", "nan"], ["--velocity", "-1"],
                                 ["--submerged", "1.2"], ["--voxel", "0"]])
def test_invalid_water_inputs_are_not_silently_clipped(args):
    from topopt.cli.underwater import main
    from topopt.schema import InputError
    with pytest.raises(InputError):
        main(["unused.stl", *args])

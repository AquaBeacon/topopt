"""`topopt extract`: threshold candidates, thickness floors, and the grid pin.

The failure this file is built around is silent incomparability. Extraction
rebuilds the design space from the study, and if the rebuilt grid differs from
the one the density field was solved on -- even in the fourth decimal -- every
candidate measurement is against a different discretisation than the run it
came from, and nothing says so (lessons-learned 30).
"""

import json
import os

import numpy as np
import pytest

from topopt.cli import extract
from topopt.designspace import Setup
from topopt.loadcase import Study
from topopt.voxel import Grid


# ----------------------------------------------------------- thickness floor

def _bar_setup(nx=12, ny=6, nz=6, h=1.6369635577254278):
    solid = np.ones((nx, ny, nz), bool)
    grid = Grid(solid=solid, h=h, origin=np.zeros(3))
    preserve = np.zeros_like(solid)
    preserve[0, :, :] = True
    return Setup(grid=grid, bodies=[], design_space=solid.copy(),
                 preserve=preserve, obstacle=np.zeros_like(solid),
                 obstacle_raw=np.zeros_like(solid))


def test_two_voxel_thickness_is_flagged_as_grid_limited():
    """AGENTS.md: a min_mm of exactly 2 x voxel is the grid talking. Reporting
    it as a structural measurement is how a part gets signed off on a number
    that only says how coarse the mesh was."""
    setup = _bar_setup()
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, 2:4, 2:4] = True          # a 2-voxel-square rib: exactly the floor
    rep = extract.thickness_report(mask, setup, setup.h)
    assert rep["grid_floor_mm"] == pytest.approx(2 * setup.h, abs=1e-3)
    assert rep["whole_part"]["grid_limited"] is True
    assert "not a measurement of the structure" in rep["whole_part"]["note"]


def test_a_thicker_member_is_not_flagged():
    setup = _bar_setup()
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, 1:5, 1:5] = True          # 4 voxels across: twice the floor
    rep = extract.thickness_report(mask, setup, setup.h)
    assert rep["whole_part"]["grid_limited"] is False
    assert rep["whole_part"]["min_mm"] > rep["grid_floor_mm"] + 1e-3


def test_the_rounding_in_min_member_size_does_not_hide_the_floor():
    """A regression on the tolerance itself. `min_member_size` rounds to three
    decimals, so at this voxel the floor is 3.27393 and the report says 3.274;
    an exact comparison silently never fires."""
    setup = _bar_setup()
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, 2:4, 2:4] = True
    rep = extract.thickness_report(mask, setup, setup.h)
    assert rep["whole_part"]["min_mm"] != pytest.approx(2 * setup.h, abs=1e-6)
    assert rep["whole_part"]["grid_limited"] is True


def test_whole_part_and_optimised_region_are_reported_separately():
    """Preserve geometry is whatever the CAD says it is; a 2.5 mm camera tab is
    not a filter violation. Only the free cells answer 'did the filter hold'."""
    setup = _bar_setup()
    mask = np.ones(setup.grid.shape, bool)
    rep = extract.thickness_report(mask, setup, setup.h)
    assert "whole_part" in rep and "optimised_region" in rep
    assert rep["whole_part"] is not rep["optimised_region"]


# ------------------------------------------------------------- downward area

def test_downward_face_area_ignores_the_build_plate():
    setup = _bar_setup(nx=4, ny=4, nz=4, h=2.0)
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, :, 0] = True              # a slab sitting on the plate
    assert extract.downward_face_area(mask, 2.0, build_axis=2) == 0.0


def test_downward_face_area_counts_a_floating_slab():
    setup = _bar_setup(nx=4, ny=4, nz=4, h=2.0)
    mask = np.zeros(setup.grid.shape, bool)
    mask[:, :, 2] = True              # nothing under it
    # 4 x 4 voxels of 2 mm side = 64 mm^2
    assert extract.downward_face_area(mask, 2.0, build_axis=2) == 64.0


# ------------------------------------------------------------ candidate rules

def _cand(level, connected=True, passing=None, sf=None, vol=100.0):
    c = {"level": level, "connected": connected,
         "volumes": {"final_mm3": vol},
         "thickness": {"optimised_region": None, "whole_part": None},
         "downward_face_area_mm2": 0.0}
    if sf is not None:
        c["min_safety_factor_p99_5"] = sf
    if passing is not None:
        c["pass"] = passing
    return c


def test_the_lightest_passing_candidate_wins():
    cands = [_cand(0.4, passing=True, sf=3.0, vol=120.0),
             _cand(0.5, passing=True, sf=2.5, vol=100.0),
             _cand(0.6, passing=False, sf=1.2, vol=80.0)]
    assert extract._choose(cands) == 0.5


def test_with_nothing_passing_the_strongest_is_chosen_and_says_it_failed():
    cands = [_cand(0.4, passing=False, sf=1.8, vol=120.0),
             _cand(0.5, passing=False, sf=1.2, vol=100.0)]
    assert extract._choose(cands) == 0.4
    reason = extract._choose_reason(cands, 0.4, forced=False)
    assert "NOT a pass" in reason


def test_the_chosen_reason_names_the_check_that_failed_too():
    """`_verdict` was fixed to stop blaming strength for a mesh failure;
    `_choose_reason` writes into the summary JSON, which is the file a reader
    actually opens, and had the same assumption."""
    c = _cand(0.4, passing=False, sf=61.22)
    c["fea"] = [{"checks": [
        {"name": "strength", "pass": True, "got": "61.21", "required": ">= 3"},
        {"name": "mesh resolution", "pass": False, "got": "2.0 elements",
         "required": ">= 3 across the thinnest section"}]}]
    reason = extract._choose_reason([c], 0.4, forced=False)
    assert "mesh resolution" in reason
    assert "safety factor" not in reason
    assert "NOT a pass" in reason


def test_a_disconnected_candidate_is_never_chosen_over_a_connected_one():
    cands = [_cand(0.4, connected=False, passing=False, vol=50.0),
             _cand(0.5, connected=True, passing=True, sf=3.0, vol=100.0)]
    assert extract._choose(cands) == 0.5


def test_pick_is_recorded_as_forced_not_as_a_comparison():
    """A threshold the user demanded must not be reported as if the tool
    weighed the candidates and preferred it."""
    cands = [_cand(0.4, passing=True, sf=3.0), _cand(0.5, passing=True, sf=2.0)]
    reason = extract._choose_reason(cands, 0.5, forced=True)
    assert "--pick" in reason and "no candidate comparison" in reason


def test_a_failing_candidate_names_the_check_that_actually_failed():
    """Real case from the tall tripod: safety factor 61 against a required 3,
    and the candidate still fails -- on mesh resolution. Reporting that as
    "FAIL, SF 61.22 < 3.00" is false, self-contradictory, and sends someone
    off to add material to a part with 20x the strength it needs."""
    c = _cand(0.5, passing=False, sf=61.22)
    c["fea"] = [{"checks": [
        {"name": "strength", "pass": True, "got": "61.21", "required": ">= 3"},
        {"name": "stiffness", "pass": True, "got": "0.36 mm", "required": "<= 3"},
        {"name": "mesh resolution", "pass": False, "got": "2.0 elements",
         "required": ">= 3 elements across the thinnest section"},
    ]}]
    v = extract._verdict(c, 3.0)
    assert "mesh resolution" in v
    assert "61" not in v
    assert "SF" not in v


def test_the_same_check_failing_on_every_case_is_reported_once():
    c = _cand(0.5, passing=False, sf=10.0)
    one = {"checks": [{"name": "mesh resolution", "pass": False,
                       "got": "2.0", "required": ">= 3"}]}
    c["fea"] = [dict(one) for _ in range(4)]
    assert extract._verdict(c, 3.0).count("mesh resolution") == 1


def test_a_genuine_strength_failure_still_names_strength():
    c = _cand(0.5, passing=False, sf=1.2)
    c["fea"] = [{"checks": [
        {"name": "strength", "pass": False, "got": "1.20",
         "required": "safety factor >= 3.00"}]}]
    v = extract._verdict(c, 3.0)
    assert "strength" in v and "1.20" in v


def test_unanalysed_candidates_do_not_read_as_passing():
    c = _cand(0.5)                                   # no 'pass', no FEA
    assert extract._verdict(c, 3.0) == "not analysed"
    c2 = _cand(0.5, sf=1.0, passing=False)
    assert extract._verdict(c2, 3.0).startswith("FAIL")
    c3 = dict(_cand(0.5), fea_error="singular")
    assert "not evaluated" in extract._verdict(c3, 3.0)


# ------------------------------------------------------------------ the grid

def test_recorded_grid_is_read_from_the_summary():
    summary = {"setup": {"info": {"voxel_mm": 1.6369635577254278,
                                  "origin_mm": [-55.657, -55.274, -3.274]}}}
    h, o = extract.recorded_grid(summary)
    assert h == pytest.approx(1.6369635577254278)
    assert o == pytest.approx([-55.657, -55.274, -3.274])


def test_a_summary_without_a_grid_reports_none_rather_than_guessing():
    h, o = extract.recorded_grid({})
    assert h is None and o is None


def test_missing_density_field_names_what_was_expected(tmp_path):
    with pytest.raises(FileNotFoundError, match="_density_phys.npy"):
        extract.load_density(str(tmp_path))


def test_not_a_directory_is_reported_as_such(tmp_path):
    p = tmp_path / "x.npy"
    p.write_bytes(b"")
    with pytest.raises(FileNotFoundError):
        extract.load_density(str(p))


@pytest.mark.slow
def test_recorded_gd_mount_reproduces_its_own_volume():
    """End to end against the recorded run: extracting at 0.50 must give back
    the volume that run reported. If the rebuilt grid drifted, this is where it
    shows up -- and it is the only place it would."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    topo = os.path.join(root, "osmo_tripod", "out", "gd_mount")
    study = os.path.join(root, "osmo_tripod", "gd_mount_study.json")
    if not os.path.isdir(topo):
        pytest.skip("recorded gd_mount run is not present")
    with open(os.path.join(topo, "gd_mount_study.json"), encoding="utf-8") as f:
        recorded = json.load(f)

    out = extract.run(topo, study, outdir=None, levels=(0.5,), do_fea=False,
                      quiet=True)
    assert out["voxel_mm"] == pytest.approx(
        recorded["setup"]["info"]["voxel_mm"], abs=1e-4)
    got = out["candidates"][0]["volumes"]["final_mm3"]
    assert got == pytest.approx(recorded["volumes"]["final_mm3"], rel=1e-6)
    assert out["candidates"][0]["connected"] is True

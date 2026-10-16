"""osmo_tripod as the regression baseline (spec 5.2, 21 Phase 0).

The reference model is the only thing in this repository that has been through
the whole pipeline and been printed. Its numbers are therefore the contract:
a change to the solver, the voxeliser or the design-space rules that moves them
has to be explained, not discovered later.

Three tiers, by what they cost:

* **fits** and **printability** -- milliseconds, read straight off the exported
  STL. These are the ones that would have caught the v2 assembly failure and
  the support-area flip, and they run on every `pytest`.
* **setup** -- a few seconds. Volumes of design space, preserve, obstacle and
  free cells for the mount study.
* **optimisation** and **fea** -- minutes. Compared only under `-m slow`,
  against the recorded run summaries rather than by re-solving.

Everything skips cleanly when `osmo_tripod/` is not next to the package, so the
suite still passes for someone who installed only the tool.
"""

import json
import os

import pytest

import probe

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OSMO = os.path.join(ROOT, "osmo_tripod")
STL = os.path.join(OSMO, "out", "osmo_tripod.stl")
STUDY = os.path.join(OSMO, "gd_mount_study.json")
BASELINE = os.path.join(HERE, "baselines", "osmo_tripod.json")
Z_HINGE = 23.0

pytestmark = pytest.mark.osmo

needs_model = pytest.mark.skipif(
    not (os.path.exists(STL) and os.path.exists(BASELINE)),
    reason="osmo_tripod reference model not present next to the package")


@pytest.fixture(scope="module")
def base():
    with open(BASELINE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def tris():
    from topopt.voxel import load_stl
    return load_stl(STL)


def close(got, want, tol, what):
    assert abs(got - want) <= tol, (
        "%s moved: %.4f -> %.4f (tolerance %.4f). If this is intended, say why "
        "and re-run tests/make_baseline.py." % (what, want, got, tol))


# ------------------------------------------------------------------ fits
# The case interface. These are exact CAD values, so the tolerance is float
# noise, not an engineering allowance -- any real change is a design change.

@needs_model
@pytest.mark.parametrize("key", [
    "head_finger_mm", "centre_finger_mm", "slot_head_mm", "slot_nut_mm",
    "slot_x0_mm", "slot_x1_mm",
])
def test_mating_dimensions(base, tris, key):
    from make_baseline import part_fits
    got = part_fits(STL)[key]
    close(got, base["fits"][key], 1e-3, key)


@needs_model
def test_the_case_prong_still_fits(base):
    """The check that was missing when v2 was printed.

    Not a comparison against the baseline: an absolute statement about whether
    the mating part can physically enter. The male prong is one rigid piece, so
    every face has to clear at once.
    """
    f = base["fits"]
    prong_t, prong_gap = 3.00, 3.00
    assert f["slot_head_mm"] - prong_t >= 0.7, "head slot too tight for the prong"
    assert f["slot_nut_mm"] - prong_t >= 0.7, "nut slot too tight for the prong"
    assert prong_gap - f["centre_finger_mm"] >= 0.5, \
        "centre finger too thick for the gap between the prongs"
    assert f["bore_width_mm"] >= 5.4, "M5 bore under ISO medium clearance"
    assert min(f["knob_clear_dia_head_mm"], f["knob_clear_dia_nut_mm"]) >= 22.5, \
        "a 22 mm thumb-screw knob does not clear"


# ---------------------------------------------------------- printability

@needs_model
def test_no_support_needed(base):
    """0.0 mm2 is a designed property of this part, not an accident. It flipped
    to 40.2 mm2 once from a bore roof sitting exactly on the 45 deg threshold."""
    from topopt.geom import load_stl, support_area
    got = support_area(load_stl(STL))
    assert got["support_mm2"] == 0.0, (
        "the part now needs %.1f mm2 of support, located at %s"
        % (got["support_mm2"], got.get("support_bbox")))


@needs_model
@pytest.mark.parametrize("key,tol", [("volume_mm3", 1.0), ("bed_mm2", 1.0)])
def test_printability_numbers(base, key, tol):
    from topopt.geom import load_stl, support_area
    got = support_area(load_stl(STL))[key]
    close(got, base["printability"][key], tol, key)


# ----------------------------------------------------------------- setup

@needs_model
@pytest.mark.parametrize("key,tol", [
    ("voxel_mm", 1e-6), ("design_space_mm3", 1.0), ("preserve_mm3", 1.0),
    ("obstacle_mm3", 1.0), ("obstacle_declared_mm3", 1.0), ("free_mm3", 1.0),
])
def test_setup_volumes(base, key, tol):
    from make_baseline import setup_volumes
    got = setup_volumes()
    close(got[key], base["setup_gd_mount"][key], tol, "setup " + key)


@needs_model
def test_setup_is_valid(base):
    """The mount study must stay solvable. Loads falling off preserve is the
    failure mode a coarser grid produces, and it is an error, not a warning."""
    from make_baseline import setup_volumes
    got = setup_volumes()
    assert got["ok"], "%d setup error(s)" % got["n_errors"]
    assert got["preserve_bodies"] == base["setup_gd_mount"]["preserve_bodies"]


# -------------------------------------------------------------- recorded
# Read from the stored run summaries. These do not re-solve; they assert that
# the summaries on disk still say what the baseline says they say, which is
# what stops a stale directory being quoted in the docs.

@needs_model
def test_optimisation_inputs_unchanged(base):
    """The geometry the recorded run was solved from is still the geometry on
    disk. Without this the recorded numbers are self-referential -- regenerating
    `out/` and the baseline together moves both sides and looks clean.

    Fast, so it runs every time: it is the check that would have said "your
    reference result belongs to a different part" during the v2 -> v3 change.
    """
    from make_baseline import input_hashes, GD_MOUNT_INPUTS
    want = base.get("gd_mount_inputs_sha256")
    if not want:
        pytest.skip("baseline predates input hashing; re-run make_baseline.py")
    got = input_hashes(GD_MOUNT_INPUTS)
    moved = [k for k in want if got.get(k) != want[k]]
    assert not moved, (
        "the recorded gd_mount result was solved from different inputs; these "
        "changed since the baseline: %s. Re-run `topopt opt` before trusting "
        "the recorded numbers, then regenerate the baseline." % ", ".join(moved))


@needs_model
@pytest.mark.slow
def test_recorded_optimisation_matches(base):
    p = os.path.join(OSMO, "out", "gd_mount", "gd_mount_study.json")
    if not os.path.exists(p):
        pytest.skip("no recorded gd_mount run")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    want = base["optimisation_gd_mount"]
    close(d["volumes"]["final_mm3"], want["volume_mm3"], 1.0, "opt volume")
    assert d["connectivity"]["components"] == want["components"]
    assert d["connectivity"]["island_mm3"] == want["island_mm3"]
    for c in d["final_fea"]:
        key = c["case"].split()[0]
        close(c["safety_factor_p99_5"], want["safety_factor_p99_5"][key],
              0.01, "opt SF " + key)


@needs_model
@pytest.mark.slow
def test_recorded_fea_matches(base):
    p = os.path.join(OSMO, "out", "fea_v3", "osmo_tripod_fea.json")
    if not os.path.exists(p):
        pytest.skip("no recorded fea_v3 run")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    want = base["fea_study_main"]
    assert d["cases"][0]["voxel_mm"] == want["voxel_mm"]
    for c in d["cases"]:
        key = c["case"].split()[0]
        close(c["safety_factor_p99_5"],
              want["cases"][key]["safety_factor_p99_5"], 0.01, "fea SF " + key)

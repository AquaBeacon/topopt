"""Build volumes, and the setup checks that read them.

The failure this file exists for is measured, not hypothetical: the tall
tripod's design space was drawn 300 mm across against a 250 mm machine, nothing
looked, and 3.6 hours of optimisation produced an STL that fits the printer in
no orientation at all. The envelope is a property of the *setup* and is
knowable in the first second. Every test here is about asking then.
"""

import numpy as np
import pytest

from topopt import printers
from topopt.designspace import (Setup, bbox_mm, build_volume_report,
                                freedom_report)
from topopt.loadcase import Study
from topopt.voxel import Grid


# ------------------------------------------------------------------ the data

def test_one_module_owns_the_build_volumes():
    """`materials.py` is the single source of material numbers; this is the
    same rule for envelopes. A second copy is a copy that disagrees silently."""
    from topopt.cli import printability
    assert printability.PRINTERS is printers.PRINTERS


def test_an_undeclared_printer_is_labelled_as_defaulted():
    """'no machine was chosen' and 'this machine was chosen' must never read
    the same in a report."""
    p = printers.resolve(None)
    assert p.key == printers.DEFAULT
    assert "default" in p.source and "none declared" in p.source
    assert printers.resolve("X1C").source == "built-in"


def test_a_custom_machine_is_a_first_class_citizen():
    p = printers.resolve({"name": "shop mill", "build_volume_mm": [400, 300, 450],
                          "nozzle_mm": 0.6})
    assert p.usable_mm == (400.0, 300.0, 450.0)
    assert p.min_feature_mm == pytest.approx(1.2)
    assert p.source == "declared in the study"


def test_an_unknown_printer_name_says_how_to_declare_one():
    with pytest.raises(printers.PrinterError, match="build_volume_mm"):
        printers.resolve("NoSuchPrinter")


def test_a_custom_machine_without_an_envelope_is_rejected():
    with pytest.raises(printers.PrinterError, match="build_volume_mm"):
        printers.resolve({"name": "mystery"})


def test_margin_shrinks_the_envelope_on_both_sides():
    p = printers.resolve({"build_volume_mm": [200, 200, 200], "margin_mm": 5})
    assert p.envelope_mm == (190.0, 190.0, 190.0)


# --------------------------------------------------------------- the fit test

def test_a_part_that_fits_lying_down_does_not_pass_standing_up():
    """The whole point of separating the two. A 240 x 100 x 100 part fits a
    250-cube whichever way up; a 100 x 100 x 400 does not fit at all."""
    r = printers.fit([100, 100, 400], "X1C", "Z")
    assert r["fits_as_declared"] is False
    assert r["fits_any_orientation"] is False
    assert r["split"]["required"] is True


# A wide, shallow machine: 250 x 250 mm of plate but only 100 mm of headroom.
# A cube-shaped envelope cannot demonstrate the orientation question at all --
# if a part is too big for a 250-cube one way up, it is too big every way up --
# so the distinction needs a machine whose axes actually differ.
FLAT = {"name": "wide flatbed", "build_volume_mm": [250, 250, 100]}


def test_the_orientation_that_fits_is_reported_separately_from_a_pass():
    """90 x 200 x 240 on a 250 x 250 x 100 machine fits only with X up. That
    is not the same as fitting, because printing it that way moves the layer
    planes out from under the analysis."""
    r = printers.fit([90, 200, 240], FLAT, "Z")
    assert r["fits_as_declared"] is False
    assert r["fits_any_orientation"] is True
    assert r["up_axes_that_fit"] == ["X"]


def test_overflow_is_reported_per_model_axis_in_mm():
    """The number someone edits in the CAD is a model dimension, not a
    dimension in some rotated printing frame."""
    r = printers.fit([300, 200, 100], "X1C", "Z")
    assert r["overflow_mm"]["X"] == pytest.approx(50.0)
    assert r["overflow_mm"]["Y"] < 0


# --------------------------------------------------- the check at setup time

def _setup(size_vox=(10, 10, 10), h=4.0, preserve_slice=None):
    solid = np.ones(size_vox, bool)
    grid = Grid(solid=solid, h=h, origin=np.zeros(3))
    preserve = np.zeros_like(solid)
    preserve[preserve_slice if preserve_slice else np.s_[0, :, :]] = True
    return Setup(grid=grid, bodies=[], design_space=solid.copy(),
                 preserve=preserve, obstacle=np.zeros_like(solid),
                 obstacle_raw=np.zeros_like(solid))


def _study(**kw):
    d = {"units": "mm_N_MPa", "material": "PLA", "build_axis": "Z",
         "regions": {"a": {"type": "box", "min": [-1, -1, -1],
                           "max": [999, 999, 999]}},
         "cases": [{"name": "c", "supports": [{"region": "a", "fix": "xyz"}],
                    "loads": [{"region": "a", "force": [0, 0, -1]}]}]}
    d.update(kw)
    return Study(d)


def test_a_design_space_larger_than_the_machine_stops_the_run():
    """The measured failure, in one test. 80 voxels of 4 mm is 320 mm across
    a 250 mm machine, and the error has to arrive before the solver does."""
    s = _setup(size_vox=(80, 40, 40))
    _info, errors, _w = build_volume_report(s, _study(printer="X1C"))
    assert errors and "design space" in errors[0]
    assert "320" in errors[0] and "250" in errors[0]


def test_a_preserve_larger_than_the_machine_is_the_stronger_finding():
    """Preserve is in every candidate shape, so this is not 'might not fit' --
    it is 'cannot fit', and the message has to say which."""
    s = _setup(size_vox=(80, 40, 40), preserve_slice=np.s_[:, :, :])
    _info, errors, _w = build_volume_report(s, _study(printer="X1C"))
    assert any("no result of this study can be printed" in e for e in errors)


def test_fitting_only_sideways_is_an_error_that_names_the_conflict():
    """A part that fits with X up but is declared build_axis Z would have to be
    printed in an orientation the material model does not describe. Reporting
    that as "fits (rotated)" is how an analysis gets separated from its
    object."""
    s = _setup(size_vox=(9, 20, 24), h=10.0, preserve_slice=np.s_[:, :, :])
    _info, errors, _w = build_volume_report(s, _study(printer=FLAT))
    assert errors
    assert "build_axis" in errors[0] and "material axes" in errors[0]
    assert "X up" in errors[0]


def test_a_part_that_fits_produces_no_error():
    s = _setup(size_vox=(20, 20, 20))          # 80 mm cube
    _info, errors, _w = build_volume_report(s, _study(printer="X1C"))
    assert errors == []


def test_an_oversize_design_space_can_be_allowed_but_only_on_the_record():
    """Split parts are legitimate. Silently oversize design spaces are not."""
    s = _setup(size_vox=(80, 40, 40))
    st = _study(printer={"printer": "X1C", "allow_oversize_design_space": True})
    _info, errors, warnings = build_volume_report(s, st)
    assert errors == []
    assert any("allow_oversize_design_space" in w for w in warnings)


def test_a_filter_finer_than_the_nozzle_is_flagged():
    s = _setup(size_vox=(20, 20, 20))
    st = _study(printer="X1C", design={"filter_mm": 0.5, "volfrac": 0.3})
    _info, _e, warnings = build_volume_report(s, st)
    assert any("cannot make a wall below" in w for w in warnings)


def test_the_bounding_box_is_measured_from_the_mask_not_the_grid():
    """The grid carries `pad_voxels` of empty margin. Quoting that as the part
    size overstates every dimension by a number that looks measured."""
    solid = np.zeros((10, 10, 10), bool)
    solid[2:5, 2:5, 2:5] = True
    lo, hi, size = bbox_mm(solid, np.zeros(3), 2.0)
    assert list(size) == [6.0, 6.0, 6.0]
    assert list(lo) == [4.0, 4.0, 4.0] and list(hi) == [10.0, 10.0, 10.0]
    assert bbox_mm(np.zeros((3, 3, 3), bool), np.zeros(3), 1.0) is None


# ------------------------------------------------------------ design freedom

def test_a_design_space_that_is_nearly_all_preserve_is_reported_as_such():
    """The result would be a CAD model with fillets. That is a fine thing to
    build and a bad thing to present as an optimised layout."""
    s = _setup(size_vox=(10, 10, 10), preserve_slice=np.s_[:9, :, :])
    info, warnings = freedom_report(s, _study(design={"volfrac": 0.3}))
    assert info["free_fraction_of_domain"] == pytest.approx(0.1)
    assert any("mostly the CAD model" in w for w in warnings)
    assert info["preserve_fraction_of_expected_part"] > 0.5
    assert any("drawn, not" in w or "not\noptimised" in w or
               "optimised" in w for w in warnings)


def test_a_free_region_under_three_voxels_thick_cannot_hold_a_decision():
    """The free slab is put *inside* the grid on purpose. scipy's distance
    transform measures distance to the nearest zero within the array, so a
    region touching the array edge reads as thicker than it is -- which is
    exactly why `build_setup` pads the grid."""
    s = _setup(size_vox=(12, 12, 12))
    s.preserve[:] = True
    s.preserve[2:10, 2:10, 5:7] = False        # an 8 x 8 x 2 free slab
    s.free = s.grid.solid & ~s.preserve & ~s.obstacle
    info, warnings = freedom_report(s, _study(design={"volfrac": 0.3}))
    assert info["free_min_thickness_voxels"] == pytest.approx(2.0)
    assert any("no room for a hole" in w for w in warnings)


def test_a_roomy_design_space_gets_no_freedom_warning():
    s = _setup(size_vox=(20, 20, 20), preserve_slice=np.s_[0, :, :])
    _info, warnings = freedom_report(s, _study(design={"volfrac": 0.3,
                                                       "filter_mm": 1.0}))
    assert not any("mostly the CAD model" in w for w in warnings)


def test_the_fixed_support_positions_are_named_not_merely_counted():
    """The user-facing point: preserve pins where the feet go, so the layout of
    the supports is an input. A report that does not say so lets a reader
    assume an optimised layout was optimised."""
    s = _setup(size_vox=(10, 10, 10))
    info, warnings = freedom_report(s, _study(design={"volfrac": 0.3}))
    pts = {f["region"]: f for f in info["fixed_points"]}
    assert "a" in pts
    assert any("support point(s) are fixed by preserve" in w for w in warnings)
    assert any("made in CAD" in w for w in warnings)


# ------------------------------------------------------- wired into `setup`

def test_the_checks_actually_run_inside_validate():
    """A check nobody calls is not a check."""
    from topopt.designspace import validate
    s = _setup(size_vox=(80, 40, 40))
    rep = validate(s, _study(printer="X1C"))
    assert rep["ok"] is False
    assert "build_volume" in rep["info"] and "design_freedom" in rep["info"]


def test_an_unreadable_printer_block_does_not_take_the_setup_down():
    """A bad `printer` value is the user's input. Reporting it as a broken
    setup would hide every other finding behind it."""
    from topopt.designspace import validate
    s = _setup(size_vox=(20, 20, 20))
    rep = validate(s, _study(printer="NoSuchPrinter"))
    assert any("build volume could not be checked" in w
               for w in rep["warnings"])
    assert rep["info"]["build_volume"]["error"]


# ------------------------------------ orientations that the machine excludes

def _osmo_stl():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "..", "..", "osmo_tripod", "out", "osmo_tripod.stl")
    if not os.path.exists(p):
        pytest.skip("reference model not present next to the package")
    return os.path.abspath(p)


def test_an_orientation_that_does_not_fit_is_not_a_candidate():
    """Spec 19.5. Scoring an unbuildable orientation with a +1e6 penalty and
    then listing it anyway put it at the top of the table whenever nothing
    fit, complete with an overhang percentage someone could read off it."""
    from topopt.cli.printability import analyse
    tiny = {"name": "thumbnail", "build_volume_mm": [20, 20, 20]}
    r = analyse(_osmo_stl(), tiny, min_features=False)
    assert r["orientations"] == []
    assert r["fits_in_some_orientation"] is False
    assert r["excluded_orientations"], "excluded is not the same as unmentioned"
    assert all(o["fits"] is False for o in r["excluded_orientations"])


def test_an_excluded_orientation_says_by_how_much_and_on_which_axis():
    """'does not fit' alone sends someone to measure the part again."""
    from topopt.cli.printability import analyse
    tiny = {"name": "thumbnail", "build_volume_mm": [20, 20, 20]}
    r = analyse(_osmo_stl(), tiny, min_features=False)
    note = r["excluded_orientations"][0]["note"]
    assert note.startswith("excluded:")
    assert "mm over on" in note and "bed" in note


def test_nothing_fitting_is_a_fail_and_not_a_quiet_pass():
    """The support check has no orientation to measure in. It must abstain
    loudly rather than contribute a 0 % overhang pass to the verdict."""
    from topopt.cli.printability import analyse
    tiny = {"name": "thumbnail", "build_volume_mm": [20, 20, 20]}
    r = analyse(_osmo_stl(), tiny, min_features=False)
    assert r["pass"] is False
    sup = next(c for c in r["checks"] if c["name"] == "supports")
    assert sup["pass"] is False and "not measured" in sup["got"]
    assert r["best_orientation"] == {}


def test_a_part_that_fits_keeps_every_candidate_it_had():
    """The counter-test: exclusion must not quietly shrink a normal run's
    table, and the best orientation is still the best fitting one."""
    from topopt.cli.printability import analyse
    r = analyse(_osmo_stl(), "X1C", min_features=False)
    assert r["excluded_orientations"] == []
    assert len(r["orientations"]) == 8
    assert all(o["fits"] for o in r["orientations"])
    assert r["best_orientation"] == r["orientations"][0]

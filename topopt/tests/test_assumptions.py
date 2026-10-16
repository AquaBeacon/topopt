"""The 前提条件 page: is every input a reader needs actually on it?

A topology-optimisation result is a picture of its assumptions, so a shape
published without them cannot be checked, reproduced or argued with. The tests
here are less about formatting than about omission: each one names a thing that
changes the answer and asserts it reaches the page.

The limitations section gets the most attention, because it is the part that
rots. A hand-maintained caveat list goes stale silently and then reads as
current -- so it is derived from the data every time, and these tests pin the
derivation rather than the wording of any one caveat.
"""

import json
import os

import numpy as np
import pytest

from topopt import assumptions as A
from topopt.designspace import Setup
from topopt.loadcase import Study
from topopt.voxel import Grid


def _study(**kw):
    d = {
        "_what": "A bracket that holds the thing up.",
        "_why": "Because the thing falls down otherwise.",
        "units": "mm_N_MPa", "material": "PLA",
        "build_axis": "Z", "raster_axis": "X",
        "safety_factor": 3.0, "max_deflection_mm": 2.0,
        "printer": "X1C",
        "design": {"volfrac": 0.3, "filter_mm": 4.0},
        "regions": {"foot": {"type": "box", "min": [-99, -99, -99],
                             "max": [99, 99, 99]}},
        "required_cases": ["drop"],
        "cases": [{
            "name": "drop",
            "_note": "Dropped on a hard floor from bench height.",
            "_load_note": "5 g screening factor.",
            "supports": [{"region": "foot", "fix": "xyz"}],
            "loads": [{"region": "foot", "force": [0, 0, -25.0]}],
        }],
    }
    d.update(kw)
    return Study(d)


def _setup(n=10, h=4.0):
    solid = np.ones((n, n, n), bool)
    grid = Grid(solid=solid, h=h, origin=np.zeros(3))
    pres = np.zeros_like(solid)
    pres[0, :, :] = True
    return Setup(grid=grid, bodies=[], design_space=solid.copy(),
                 preserve=pres, obstacle=np.zeros_like(solid),
                 obstacle_raw=np.zeros_like(solid))


# ------------------------------------------------------- the loads are there

def test_every_load_case_reaches_the_page_with_its_use_case():
    """A force with no story behind it cannot be argued with. The number and
    the reason it was chosen have to travel together."""
    md = A.render(A.collect(_study()))
    assert "drop" in md
    assert "Dropped on a hard floor" in md
    assert "(0, 0, -25)" in md
    assert "25.00" in md                       # the magnitude, spelled out


def test_the_prose_lives_outside_the_table():
    """The first version put "what this case represents" in a table cell,
    which made a 350-character cell that wraps into an unreadable block in
    every markdown renderer and buries the numbers the table exists for.
    Narrow columns; the story goes underneath."""
    md = A.render(A.collect(_study()))
    rows = [l for l in md.splitlines() if l.startswith("|")]
    # Counted in words, not characters: a 44-character card id is one token and
    # reads fine, while three sentences of prose is the thing that breaks the
    # layout. Length was the wrong proxy and flagged the identifier instead.
    worst = max(((len(c.split()), c.strip()) for l in rows
                 for c in l.split("|")), key=lambda t: t[0])
    assert worst[0] <= 15, "a %d-word table cell is prose: %r" % worst
    # ...and the prose is still on the page, just not in the grid.
    assert "Dropped on a hard floor" in md
    body = md[md.index("## 1."):md.index("## 2.")]
    for line in body.splitlines():
        if line.startswith("|"):
            assert "Dropped on a hard floor" not in line


def test_a_load_with_no_note_still_appears():
    st = _study(cases=[{"name": "bare", "supports": [{"region": "foot"}],
                        "loads": [{"region": "foot", "force": [1, 0, 0]}]}])
    md = A.render(A.collect(st))
    assert "bare" in md and "(1, 0, 0)" in md


def test_a_pressure_load_is_not_given_an_invented_magnitude():
    """The area is not known without the grid. A guessed newton figure here
    would be quoted as if it had been computed."""
    st = _study(cases=[{"name": "p", "supports": [{"region": "foot"}],
                        "loads": [{"region": "foot", "pressure": 0.1}]}])
    d = A.collect(st)
    ld = d["cases"][0]["loads"][0]
    assert ld["magnitude_N"] is None
    assert "pressure" in ld["kind"]


def test_the_resultant_of_a_case_is_computed():
    st = _study(cases=[{"name": "two", "supports": [{"region": "foot"}],
                        "loads": [{"region": "foot", "force": [3, 0, 0]},
                                  {"region": "foot", "force": [0, 4, 0]}]}])
    d = A.collect(st)
    assert d["cases"][0]["resultant_magnitude_N"] == pytest.approx(5.0)


# --------------------------------------------------------- the inputs are there

def test_the_pass_thresholds_are_stated():
    md = A.render(A.collect(_study()))
    assert "Safety factor" in md and "3.0" in md
    assert "2.0 mm" in md


def test_the_machine_and_the_build_axis_are_stated():
    md = A.render(A.collect(_study()))
    assert "Bambu Lab X1-Carbon" in md
    assert "250 × 250 × 250" in md
    assert "Build axis" in md


def test_an_assumed_build_axis_is_marked_as_assumed():
    """Defaulted and chosen must not read the same."""
    st = _study()
    del st.raw["build_axis"]
    st.build_axis_declared = False
    md = A.render(A.collect(st))
    assert "assumed, not declared" in md


def test_the_material_constants_are_printed_not_just_named():
    """'PLA' is not a material specification. The nine constants are."""
    md = A.render(A.collect(_study()))
    assert "Ez" in md and "Zt" in md and "Gxy" in md


# ------------------------------------------------------- the limitations bite

def test_an_unmeasured_card_is_called_out():
    lim = A.limitations(A.collect(_study()))
    assert any("not measured" in x for x in lim)


def test_a_safety_factor_below_what_the_card_asks_for_is_called_out():
    lim = A.limitations(A.collect(_study(safety_factor=3.0)))
    assert any("below the 4.0" in x for x in lim)


def test_an_undeclared_printer_is_called_out():
    st = _study()
    del st.raw["printer"]
    st.printer_declared = False
    st.printer_spec = None
    lim = A.limitations(A.collect(st))
    assert any("No printer was declared" in x for x in lim)


def test_the_fixed_supports_are_called_out_as_an_input():
    """The user-facing point: preserve decides where the feet go, so the
    support layout is an input. A reader assumes an optimised layout was
    optimised unless told otherwise."""
    from topopt.designspace import freedom_report
    s = _setup()
    st = _study()
    fr, _w = freedom_report(s, st)
    d = A.collect(st)
    d["design_freedom"] = fr
    lim = A.limitations(d)
    assert any("Where the part is held is an input" in x for x in lim)


def test_the_never_tested_line_is_always_present():
    """It is true of every run this toolkit can currently produce, so it is
    unconditional rather than something a study could forget to declare."""
    assert any("tested physically" in x
               for x in A.limitations(A.collect(_study())))


def test_a_measured_card_would_drop_the_provisional_caveat():
    """Guards the derivation, not the wording: if the card ever becomes
    measured, the caveat must disappear on its own."""
    d = A.collect(_study())
    d["material"]["measured_for_specific_printer"] = True
    d["material"]["required_safety_factor"] = 2.0
    assert not any("not measured" in x for x in A.limitations(d))


# --------------------------------------------------------------- with a setup

def test_the_setup_supplies_measured_volumes_not_restated_ones():
    from topopt.designspace import validate
    s, st = _setup(), _study()
    rep = validate(s, st)
    d = A.collect(st, s, rep)
    assert d["design_space"]["bbox_mm"] == [40.0, 40.0, 40.0]
    assert d["design_freedom"]["free_fraction_of_domain"] == pytest.approx(0.9)


def test_a_setup_that_fails_still_gets_its_assumptions_written():
    """More important then, not less: the errors belong on the page rather
    than being the reason the page does not exist."""
    from topopt.designspace import validate
    s = _setup(n=80, h=10.0)                   # 800 mm across a 250 mm machine
    st = _study()
    rep = validate(s, st)
    md = A.render(A.collect(st, s, rep))
    assert "setup did not validate" in md
    assert "DOES NOT FIT" in md


# --------------------------------------------------------------- the plumbing

def test_no_format_placeholder_survives_into_the_page():
    md = A.render(A.collect(_study()))
    for bad in ("%d", "%s", "%.2f", "{}"):
        assert bad not in md, "unsubstituted %r on the page" % bad


def test_an_unnamed_study_is_named_after_its_file(tmp_path):
    """'PLA part' names nothing. The file it came from does."""
    p = tmp_path / "wing_rib_study.json"
    p.write_text(json.dumps(_study().raw), encoding="utf-8")
    from topopt.loadcase import load_case_from_json
    d = A.collect(load_case_from_json(str(p)))
    assert d["part"] == "wing_rib_study"
    assert d["study_file"] == "wing_rib_study.json"


def test_write_produces_both_formats(tmp_path):
    paths = A.write(str(tmp_path), _study())
    assert os.path.exists(paths["md"]) and os.path.exists(paths["json"])
    blob = json.load(open(paths["json"], encoding="utf-8"))
    assert blob["schema"] == "topopt/assumptions/1"


def test_the_cli_runs_without_building_the_setup(tmp_path):
    """`--no-setup` has to work on a study whose geometry files are missing --
    that is the case where you most want to read the declared inputs."""
    from topopt.cli import assumptions as cli
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_study().raw), encoding="utf-8")
    assert cli.main([str(p), "--out", str(tmp_path), "--no-setup",
                     "--quiet"]) == 0
    assert os.path.exists(tmp_path / "assumptions.md")


# ------------------------------------------- studies with no design space

def test_a_study_with_no_geometry_block_still_gets_a_page(tmp_path):
    """A `topopt fea` study analyses an STL that already exists and declares no
    design space at all. Its loads, material and requirements are exactly what
    this page is for. Crashing on it made the page unavailable for three of the
    four studies in this repository, with a raw traceback."""
    from topopt.cli import assumptions as cli
    raw = _study().raw
    raw.pop("geometry", None)
    p = tmp_path / "fea_only.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main([str(p), "--out", str(tmp_path), "--quiet"]) == 0
    md = (tmp_path / "assumptions.md").read_text(encoding="utf-8")
    # The declared inputs are all there...
    assert "drop" in md and "(0, 0, -25)" in md and "PLA" in md
    # ...and the sections that need a design space simply do not claim one.
    assert "Share of the domain the optimiser may decide" not in md

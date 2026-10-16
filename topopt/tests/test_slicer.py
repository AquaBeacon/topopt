"""`topopt slice`: the parse, the exit codes, and one real slice.

Most of this file is about a single number. The slicer states its total print
time, its total filament and whether the object fits the plate; it states no
support total anywhere in the 3mf. Support has to be re-derived by summing the
G-code's `; FEATURE: Support*` blocks, and the way that goes wrong is quiet:
counting only `G0`/`G1` moves reproduces 86.6 % of the slicer's own filament
total, because Bambu Studio emits `G2`/`G3` arcs by default. 86.6 % of the
right answer looks exactly like the right answer. So the parse tests pin the
arc case, the retraction case and the relative/absolute case one at a time,
and the end-to-end test re-checks the sum against the slicer's own header --
which is the same check `cli/slice.py` runs before it agrees to report a
support figure at all.

The other thing being pinned here is what the slicer is *not allowed* to do to
the verdict. Spec 19.5: a slicer that is missing, that crashed, or that could
not fit the part on the plate must never be read as a structural result. The
report tests below check that the slicer lives in its own section with its own
UNKNOWN, and that `report.json`'s existing keys are untouched by it.
"""

import json
import os
import zipfile

import pytest

from topopt import cli, external, outschema, slicer
from topopt.cli import report as rep
from topopt.cli import run as run_cli
from topopt.cli import slice as slice_cli


HERE = os.path.dirname(os.path.abspath(__file__))

HAVE_SLICER = external.find("slicer") is not None
needs_slicer = pytest.mark.skipif(not HAVE_SLICER,
                                  reason="no slicer installed on this machine")


# --------------------------------------------------------------- fixtures

# Trimmed to the elements the parser reads. Taken from a real 3mf so the
# attribute spellings are the slicer's, not ours -- `used_m` really is metres
# while everything else in the file is millimetres.
SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Version" value="02.05.00.66"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="nozzle_diameters" value="0.4"/>
    <metadata key="prediction" value="2199"/>
    <metadata key="weight" value="18.25"/>
    <metadata key="outside" value="false"/>
    <metadata key="support_used" value="true"/>
    <object identify_id="15" name="osmo_tripod.stl" skipped="false" />
    <filament id="1" type="PLA" color="#00AE42" used_m="6.02" used_g="18.25" \
used_for_object="true" used_for_support="false" nozzle_diameter="0.40"/>
    <layer_filament_lists>
      <layer_filament_list filament_list="0" layer_ranges="0 161" />
    </layer_filament_lists>
  </plate>
</config>
"""

GCODE_HEADER = """; HEADER_BLOCK_START
; BambuStudio 02.05.00.66
; model printing time: 36m 20s; total estimated time: 36m 39s
; total layer number: 162
; total filament length [mm] : 6020.34
; total filament volume [cm^3] : 14480.61
; total filament weight [g] : 18.25
; filament_density: 1.26
; filament_diameter: 1.75
; max_z_height: 32.40
; filament: 1
; HEADER_BLOCK_END

; CONFIG_BLOCK_START
; total layer number = 999
; CONFIG_BLOCK_END
"""


# ------------------------------------------------------- slice_info.config

def test_slice_info_gives_the_slicers_own_time_weight_and_fit():
    d = slicer.parse_slice_info(SLICE_INFO)
    assert d["print_time_s"] == 2199.0
    assert d["filament_g"] == 18.25
    assert d["bed_fits"] is True          # `outside="false"`
    assert d["support_used"] is True
    # `layer_ranges="0 161"` is inclusive at both ends: 162 layers, not 161.
    assert d["layers"] == 162


def test_slice_info_used_m_is_read_as_metres():
    f = slicer.parse_slice_info(SLICE_INFO)["filaments"][0]
    assert f["used_mm"] == pytest.approx(6020.0)
    assert f["used_g"] == 18.25
    assert f["type"] == "PLA"


def test_outside_true_is_a_part_that_does_not_fit():
    d = slicer.parse_slice_info(SLICE_INFO.replace('"outside" value="false"',
                                                   '"outside" value="true"'))
    assert d["bed_fits"] is False


# --------------------------------------------------------- G-code header

def test_gcode_header_is_read_and_stops_at_the_header_block():
    d = slicer.parse_gcode_header(GCODE_HEADER)
    assert d["layers"] == 162, "the CONFIG_BLOCK's `= 999` line leaked in"
    assert d["filament_mm"] == pytest.approx(6020.34)
    assert d["filament_g"] == pytest.approx(18.25)
    assert d["filament_density_g_cm3"] == pytest.approx(1.26)
    assert d["filament_diameter_mm"] == pytest.approx(1.75)
    assert d["max_z_mm"] == pytest.approx(32.40)
    assert d["estimated_time"] == "36m 39s"
    assert d["model_printing_time"] == "36m 20s"


def test_the_headers_cm3_line_is_mislabelled_and_is_not_used():
    """`total filament volume [cm^3] : 14480.61` is millimetres cubed.

    18.25 g of PLA at 1.26 g/cm3 is 14.5 cm3, not 14,481. Believing the label
    would overstate the volume by a factor of a thousand, so the parser does
    not read the line at all and the volume comes from length x wire area.
    """
    d = slicer.parse_gcode_header(GCODE_HEADER)
    assert "filament_cm3" not in d and "filament_mm3" not in d
    from_wire = d["filament_mm"] * slicer.filament_area_mm2(
        d["filament_diameter_mm"])
    from_weight = d["filament_g"] / d["filament_density_g_cm3"] * 1000.0
    assert from_wire == pytest.approx(from_weight, rel=1e-3)
    assert from_wire == pytest.approx(14480.6, rel=1e-3)   # ... in mm3


# ------------------------------------------------ filament, feature by feature

def _gcode(*body):
    """A G-code fragment already switched to relative E, as the part body is.

    The machine start block is not: see
    `test_the_machine_start_block_is_absolute_e`.
    """
    return "\n".join(("; HEADER_BLOCK_START", "; HEADER_BLOCK_END", "M83") +
                     body)


def test_the_machine_start_block_is_absolute_e():
    """The 15 mm that hid in every print until the totals were cross-checked.

    Bambu's start G-code draws two prime lines with `G92 E0` and absolute
    `E15` / `E30` -- 30 mm of filament -- and only switches to relative with
    `M83` afterwards. Reading the file as relative throughout scores that as
    45 mm, on every print, whatever its size: small enough to look like
    rounding, large enough to break the cross-check the support figure needs.
    """
    per = slicer.filament_by_feature("\n".join([
        "; HEADER_BLOCK_START", "; HEADER_BLOCK_END",
        "; FEATURE: Custom",
        "G92 E0 ;Reset Extruder",
        "G1 X10.1 Y200.0 Z0.28 F1500.0 E15 ;Draw the first line",
        "G1 X10.4 Y200.0 Z0.28 F5000.0 ;Move to side a little",
        "G1 X10.4 Y20 Z0.28 F1500.0 E30 ;Draw the second line",
        "G92 E0 ;Reset Extruder",
        "M83",
        "; FEATURE: Outer wall",
        "G1 X20 Y20 E1.25",
    ]))
    assert per["Custom"] == pytest.approx(30.0)
    assert per["Outer wall"] == pytest.approx(1.25)


def test_arc_moves_count():
    """The failure this test exists for: `G2`/`G3` carry filament too.

    Arc fitting is on in the stock Bambu profiles. Dropping arcs loses real
    material and leaves a total that still looks like an answer.
    """
    per = slicer.filament_by_feature(_gcode(
        "; FEATURE: Outer wall",
        "G1 X10 Y0 E1.0",
        "G2 X20 Y0 I5 J0 E2.0",
        "G3 X30 Y0 I5 J0 E0.5",
    ))
    assert per["Outer wall"] == pytest.approx(3.5)


def test_retraction_and_its_unretraction_are_not_material():
    """A bare `G1 E-0.8` / `G1 E0.8` pair moves no filament onto the plate."""
    per = slicer.filament_by_feature(_gcode(
        "; FEATURE: Inner wall",
        "G1 X10 Y0 E1.0",
        "G1 E-0.8 F1800",
        "G1 X50 Y50 F12000",
        "G1 E0.8 F1800",
        "G1 X60 Y50 E1.0",
    ))
    assert per["Inner wall"] == pytest.approx(2.0)


def test_absolute_extrusion_and_g92_are_honoured():
    per = slicer.filament_by_feature(_gcode(
        "M82",
        "G92 E0",
        "; FEATURE: Sparse infill",
        "G1 X10 Y0 E1.5",
        "G1 X20 Y0 E4.0",
        "G92 E0",
        "G1 X30 Y0 E2.0",
    ))
    assert per["Sparse infill"] == pytest.approx(6.0)


def test_support_is_every_feature_whose_name_starts_with_support():
    """Bambu emits `Support`, `Support interface` and `Support transition`.

    Matched by prefix rather than by a fixed list: a scaffolding feature this
    code has not heard of must be counted, not silently dropped into the part.
    """
    per = slicer.filament_by_feature(_gcode(
        "; FEATURE: Outer wall", "G1 X1 Y0 E10",
        "; FEATURE: Support", "G1 X2 Y0 E3",
        "; FEATURE: Support interface", "G1 X3 Y0 E1",
        "; FEATURE: Support transition", "G1 X4 Y0 E0.5",
    ))
    assert slicer.support_mm(per) == pytest.approx(4.5)
    assert sum(per.values()) == pytest.approx(14.5)


# ------------------------------------------------------------ spec 13.3

def test_every_spec_13_3_condition_comes_back_unknown():
    """The slicer says how much support there is, never where it is.

    All five spec 13.3 conditions are about location, so all five are UNKNOWN
    and say why. Dropping them instead would read as 'nothing to report'.
    """
    ok, checks, note = slicer.judge({"bed_fits": True, "print_time_s": 1.0,
                                     "filament_g": 1.0})
    by_key = {c["key"]: c for c in checks}
    for key, _text in slicer.SUPPORT_CONDITIONS:
        assert by_key[key]["state"] == "UNKNOWN"
        assert "where" in by_key[key]["detail"]
    assert ok is True
    assert "not a spec 13.3 verdict" in note


def test_a_slicer_that_failed_is_not_a_pass():
    ok, checks, note = slicer.judge({}, error="the slicer exited 127")
    assert ok is False
    assert checks[0]["key"] == "sliced" and checks[0]["state"] == "FAIL"
    assert "says nothing about the structure" in note


def test_a_part_that_does_not_fit_the_plate_fails_that_check():
    ok, checks, _note = slicer.judge({"bed_fits": False, "print_time_s": 1.0,
                                      "filament_g": 1.0})
    assert ok is False
    assert {c["key"]: c["state"] for c in checks}["bed_fits"] == "FAIL"


# ------------------------------------------------------------ exit codes

def test_no_slicer_installed_is_exit_3(tmp_path, monkeypatch, capsys):
    """Spec 16.3: a tool this machine does not have is not a failed part."""
    monkeypatch.setattr(external, "find", lambda tool: None)
    # A file that exists, so the run gets past the input check and the exit
    # code is about the missing tool and nothing else.
    code = cli.main(["slice", __file__, "--out", str(tmp_path)])
    assert code == 3
    err = capsys.readouterr().err
    assert "not installed" in err
    assert "bambulab.com" in err, "exit 3 must say how to install it"


def test_a_missing_stl_is_exit_2(tmp_path):
    assert cli.main(["slice", str(tmp_path / "nope.stl"),
                     "--out", str(tmp_path)]) == 2


def test_a_printer_with_no_slicer_profile_is_exit_2_not_3(tmp_path, capsys):
    """The tool is installed; it is the *choice* that cannot be honoured.

    Reporting a Prusa MK4 as 'no slicer installed' would send someone to
    download software they already have.
    """
    if not HAVE_SLICER:
        pytest.skip("no slicer installed on this machine")
    box = _write_box(tmp_path / "box.stl")
    code = cli.main(["slice", box, "--printer", "MK4", "--out", str(tmp_path)])
    assert code == 2
    assert "MK4" in capsys.readouterr().err


def test_an_unknown_support_mode_is_exit_2(tmp_path):
    with pytest.raises(SystemExit):        # argparse rejects it up front
        cli.main(["slice", "x.stl", "--supports", "sideways"])


# ------------------------------------------------------- registration

def test_slice_is_registered_in_the_main_cli():
    assert "slice" in cli.COMMANDS


def test_slice_runs_after_print_and_before_report():
    """Spec 16.2 order: geometry, then the toolpath, then the report that
    quotes both."""
    s = run_cli.STAGES
    assert s.index("print") < s.index("slice") < s.index("report")


def test_the_slice_output_schema_is_registered():
    assert "topopt/slice/1" in dict((k[0], k) for k in outschema.kinds())


# ----------------------------------------------------------- the report

def _min_report_data():
    return {"study_raw": {"units": "mm_N_MPa", "build_axis": "z"}}


def test_the_report_says_unknown_when_there_is_no_slice():
    md = rep.render(_min_report_data())
    assert "## " in md and "What the slicer says" in md
    i = md.index("What the slicer says")
    assert "**UNKNOWN**" in md[i:i + 400]
    assert "topopt slice PART.stl" in md[i:i + 600]


def test_a_missing_slice_does_not_change_the_structural_verdict():
    """Spec 19.5. The slicer is a section, never a stop condition: adding one
    would turn every recorded run's verdict into INCOMPLETE."""
    keys = [k for k, _t, _fn in rep.CONDITIONS]
    assert not any("slice" in k or "slicer" in k for k in keys)
    s = rep.slicer_summary(_min_report_data())
    assert s["state"] == "UNKNOWN" and s["spec_13_3"] == "UNKNOWN"


def test_the_report_shows_the_row_when_there_is_a_slice():
    data = _min_report_data()
    data["slice"] = {
        "tool": {"version": "BambuStudio-02.05.00.66"},
        "presets": {"machine": "Bambu Lab X1 Carbon 0.4 nozzle",
                    "process": "0.20mm Standard @BBL X1C",
                    "filament": "Bambu PLA Basic @BBL X1C"},
        "supports": "tree", "support_type": "tree(auto)", "orient": "keep",
        "print_time_s": 16689.0, "print_time_text": "4h 38m 9s",
        "filament_g": 143.06, "filament_mm3": 113543.5,
        "support_filament_g": 22.11, "support_mm3": 17550.7,
        "support_fraction": 0.1546, "layers": 700, "object_layers": 700,
        "layer_mm": 0.2, "bed_fits": True, "error": None, "pass": True,
        "note": "n", "warnings": [],
        "checks": [{"key": k, "condition": t, "state": "UNKNOWN", "detail": "d"}
                   for k, t in slicer.SUPPORT_CONDITIONS],
    }
    md = rep.render(data)
    i = md.index("What the slicer says")
    block = md[i:i + 1400]
    assert "4h 38m 9s" in block
    assert "143.06 g" in block
    assert "22.11 g" in block and "15.5 %" in block
    assert "**UNKNOWN** (5)" in block, "spec 13.3 must stay UNKNOWN"
    s = rep.slicer_summary(data)
    assert s["state"] == "PASS" and s["spec_13_3"] == "UNKNOWN"


def test_a_failed_slice_reports_the_slicer_not_the_part():
    data = _min_report_data()
    data["slice"] = {"tool": {"version": "v"}, "presets": {},
                     "supports": "auto", "support_type": "normal(auto)",
                     "orient": "keep", "pass": False,
                     "error": "the slicer could not place the object inside "
                              "the build volume: Bambu Lab X1-Carbon",
                     "warnings": [], "checks": [], "note": "n"}
    md = rep.render(data)
    i = md.index("What the slicer says")
    assert "did not finish" in md[i:i + 900]
    assert "neither confirms nor withdraws it" in md[i:i + 1400]
    assert rep.slicer_summary(data)["state"] == "FAIL"


def test_report_json_keeps_every_key_it_had(tmp_path):
    """The slicer block is additive. Nothing that read `report.json` before
    may have to change."""
    (tmp_path / "x_setup.json").write_text(json.dumps(
        {"schema": "topopt/setup/1", "ok": True, "errors": [], "warnings": [],
         "info": {}, "bodies": []}), encoding="utf-8")
    assert cli.main(["report", str(tmp_path), "--quiet"]) in (0, 1)
    with open(tmp_path / "report.json", encoding="utf-8") as f:
        blob = json.load(f)
    for key in ("schema", "verdict", "fail", "unknown", "pass",
                "external_tools"):
        assert key in blob, "report.json lost the %r key" % key
    assert blob["slicer"]["state"] == "UNKNOWN"


# --------------------------------------------------------- the real thing

def _write_box(path, sx=12.0, sy=12.0, sz=6.0):
    """A watertight box, small enough that the slice is a couple of seconds."""
    v = [(0, 0, 0), (sx, 0, 0), (sx, sy, 0), (0, sy, 0),
         (0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz)]
    f = [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(str(path), "w", encoding="ascii") as h:
        h.write("solid box\n")
        for tri in f:
            h.write("facet normal 0 0 0\n outer loop\n")
            for i in tri:
                h.write("  vertex %g %g %g\n" % v[i])
            h.write(" endloop\nendfacet\n")
        h.write("endsolid box\n")
    return str(path)


@needs_slicer
def test_a_real_slice_of_a_small_box(tmp_path):
    box = _write_box(tmp_path / "box.stl")
    code = cli.main(["slice", box, "--out", str(tmp_path / "out"),
                     "--supports", "none", "--quiet"])
    assert code == 0
    with open(tmp_path / "out" / "slice.json", encoding="utf-8") as f:
        blob = json.load(f)

    assert blob["schema"] == "topopt/slice/1"
    assert not outschema.validate_output(blob), "slice.json is off its schema"
    assert blob["error"] is None and blob["pass"] is True
    assert blob["bed_fits"] is True
    assert blob["print_time_s"] > 0 and blob["filament_g"] > 0
    assert blob["filament_mm3"] > 0
    assert blob["layers"] and blob["layer_mm"] == 0.2
    assert blob["object_layers"] == pytest.approx(6.0 / 0.2, abs=1)
    assert blob["support_filament_g"] == pytest.approx(0.0), \
        "a box on the plate needs no support"

    # The presets are the provenance, and they must be real files: the CLI
    # does not resolve `inherits`, so the flattened copies -- not the names --
    # are what produced these numbers.
    for p in blob["presets"]["files"].values():
        assert os.path.exists(p)
    with open(blob["presets"]["files"]["machine"], encoding="utf-8") as f:
        machine = json.load(f)
    assert machine["from"] == "system", \
        '"User" is rejected by the CLI as incompatible with its own printer'
    assert machine["printable_area"][2] == "256x256", \
        "the inherits chain was not resolved -- the bed fell back to a default"


@needs_slicer
def test_petg_with_compatible_plate(tmp_path):
    box = _write_box(tmp_path / "box.stl")
    assert cli.main(["slice", box, "--out", str(tmp_path / "out"),
                     "--material", "PETG", "--supports", "tree",
                     "--bed-type", "Textured PEI Plate", "--quiet"]) == 0
    blob = json.loads((tmp_path / "out" / "slice.json").read_text(encoding="utf-8"))
    assert blob["pass"] is True
    assert blob["bed_type"] == "Textured PEI Plate"
    assert blob["material"] == "PETG"
    assert not outschema.validate_output(blob)
    with zipfile.ZipFile(blob["project_3mf"]) as z:
        gcode = z.read("Metadata/plate_1.gcode").decode("utf-8", "replace")
    assert "; curr_bed_type = Textured PEI Plate" in gcode


@needs_slicer
def test_the_feature_sum_reproduces_the_slicers_own_filament_total(tmp_path):
    """The one check that makes the support figure worth quoting.

    The slicer never states a support total, so the split between part and
    scaffolding is ours. Its only evidence is that summing the same features
    reproduces the number the slicer *does* state, exactly.
    """
    box = _write_box(tmp_path / "box.stl", 20, 20, 8)
    assert cli.main(["slice", box, "--out", str(tmp_path / "out"),
                     "--quiet"]) == 0
    with open(tmp_path / "out" / "slice.json", encoding="utf-8") as f:
        blob = json.load(f)
    with zipfile.ZipFile(blob["project_3mf"]) as z:
        g = z.read("Metadata/plate_1.gcode").decode("utf-8", "replace")
    per = slicer.filament_by_feature(g)
    header = slicer.parse_gcode_header(g)
    assert sum(per.values()) == pytest.approx(header["filament_mm"], rel=1e-5)
    assert "reproduces its own filament total" in blob["support_note"]


@needs_slicer
def test_a_part_too_big_for_the_plate_is_exit_1_with_the_reason(tmp_path):
    """Not a crash, and not a structural verdict: the slicer's own finding."""
    box = _write_box(tmp_path / "huge.stl", 400, 400, 50)
    code = cli.main(["slice", box, "--out", str(tmp_path / "out"), "--quiet"])
    assert code == 1
    with open(tmp_path / "out" / "slice.json", encoding="utf-8") as f:
        blob = json.load(f)
    assert blob["pass"] is False
    assert "build volume" in blob["error"]
    assert blob["bed_fits"] is False
    assert blob["project_3mf"] is None

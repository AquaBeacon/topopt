"""Study-file validation: does it catch the right things, and point at them?

Two failure modes are being guarded against, and they pull in opposite
directions:

* **Too loose** -- a study that means something other than it says gets solved
  anyway. Wrong units, an out-of-range volume fraction, a case with no
  supports.
* **Too strict** -- a study the solver would have handled correctly gets
  rejected, which teaches people to skip validation. Every study in
  `osmo_tripod/` must stay clean, comments and legacy keys included.

The line numbers are tested against a real file rather than a fixture, because
the whole value of a line number is that it is right about the file in front of
you.
"""

import io
import json
import os

import pytest

from topopt import schema

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OSMO = os.path.join(ROOT, "osmo_tripod")

BROKEN = """{
  "material": "PLA",
  "units": "inch_lbf_psi",
  "build_axis": "Q",
  "safety_factor": -3.0,
  "walls": 0,
  "infill": 1.7,
  "regions": {
    "foot": {"type": "trapezoid"}
  },
  "design": {
    "volfrac": 2.5,
    "filter_mm": 0,
    "symmetry": ["X", "W"],
    "typo_key": 1
  },
  "cases": [
    {"name": "no supports", "loads": [{"region": "foot", "force": [0, 0, -1]}]}
  ]
}
"""


def errors_of(problems):
    return [p for p in problems if p.severity == "error"]


# ------------------------------------------------------------ line locator

def test_key_lines_matches_a_real_file():
    p = os.path.join(OSMO, "gd_mount_study.json")
    text = io.open(p, encoding="utf-8-sig").read()
    lines = schema.key_lines(text)
    src = text.split("\n")

    # Every located key must actually appear on the line it claims.
    for path, line in lines.items():
        leaf = path.split(".")[-1].split("[")[0]
        assert '"%s"' % leaf in src[line - 1], (
            "%s -> line %d, but that line is %r" % (path, line, src[line - 1]))

    # and nesting has to be reflected, not flattened
    assert "geometry.preserve.mount" in lines
    assert "cases[0].name" in lines
    assert "cases[3].loads" in lines
    assert lines["geometry.clearance_mm"] != lines["geometry"]


def test_key_lines_survives_arrays_of_objects():
    text = '{\n "a": [\n  {"x": 1},\n  {"y": 2}\n ],\n "b": 3\n}\n'
    lines = schema.key_lines(text)
    assert lines["a"] == 2
    assert lines["a[0].x"] == 3
    assert lines["a[1].y"] == 4
    assert lines["b"] == 6, "a sibling after a nested array was misattributed"


# ------------------------------------------------------------- not too strict

@pytest.mark.parametrize("name", [
    "gd_mount_study.json", "gd_study.json", "study_main.json",
    "study_drop.json", "topo_study.json",
])
def test_existing_studies_validate_clean(name):
    p = os.path.join(OSMO, name)
    if not os.path.exists(p):
        pytest.skip("%s not present" % name)
    _data, problems, _text = schema.load(p)
    assert not errors_of(problems), schema.format_problems(problems, p)


def test_underscore_keys_are_comments_not_errors():
    data = {"_note": "why", "material": "PLA",
            "cases": [{"name": "c", "supports": [1], "loads": [1]}],
            "design": {"_why": "x", "volfrac": 0.3}}
    assert not errors_of(schema.validate(data))


def test_unknown_key_is_a_warning_not_an_error():
    data = {"material": "PLA", "totally_new_knob": 3,
            "cases": [{"name": "c", "supports": [1], "loads": [1]}]}
    problems = schema.validate(data)
    assert not errors_of(problems)
    assert any(p.severity == "warning" and "totally_new_knob" in p.path
               for p in problems)


# --------------------------------------------------------------- not too loose

def test_broken_study_is_caught_with_line_numbers(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(BROKEN, encoding="utf-8")
    _data, problems, _text = schema.load(str(p))
    errs = errors_of(problems)
    found = {e.path for e in errs}
    for expect in ("units", "build_axis", "safety_factor", "walls", "infill",
                   "regions.foot.type", "design.volfrac", "design.filter_mm",
                   "design.symmetry[1]", "cases[0].supports"):
        assert expect in found, "%s was not reported. got: %s" % (expect, found)
    assert all(e.line for e in errs), \
        "every error should carry a line: %s" % [str(e) for e in errs if not e.line]
    # the line has to be the right one, not merely present
    by_path = {e.path: e.line for e in errs}
    assert by_path["units"] == 3
    assert by_path["build_axis"] == 4
    assert by_path["design.volfrac"] == 12


def test_declared_units_other_than_mm_N_MPa_is_an_error():
    bad = schema.validate({"units": "inch_lbf_psi", "material": "PLA",
                           "cases": [{"supports": [1], "loads": [1]}]})
    assert any("not supported" in p.message for p in errors_of(bad))
    ok = schema.validate({"units": schema.UNITS, "material": "PLA",
                          "cases": [{"supports": [1], "loads": [1]}]})
    assert not errors_of(ok)


def test_missing_units_is_a_note_by_default_and_an_error_when_strict():
    data = {"material": "PLA", "cases": [{"supports": [1], "loads": [1]}]}
    assert not errors_of(schema.validate(data))
    assert errors_of(schema.validate(data, strict_units=True))


def test_undeclared_build_axis_warns_because_the_answer_depends_on_it():
    problems = schema.validate({"material": "PLA",
                                "cases": [{"supports": [1], "loads": [1]}]})
    assert any(p.path == "build_axis" and p.severity == "warning"
               for p in problems)


def test_anisotropic_analysis_refuses_a_defaulted_build_axis():
    """spec 9.2. Isotropic work can default the axis harmlessly; anything that
    reads the material axes cannot, because the answer would depend on an axis
    nobody chose and would look exactly like one somebody did."""
    from topopt.loadcase import Study

    undeclared = Study({"material": "PLA", "cases": [{"supports": [], "loads": []}]})
    assert undeclared.build_axis == 2, "the isotropic default is still Z"
    with pytest.raises(schema.InputError) as e:
        undeclared.require_build_axis()
    assert "build_axis" in str(e.value)

    declared = Study({"material": "PLA", "build_axis": "X",
                      "cases": [{"supports": [], "loads": []}]})
    assert declared.require_build_axis() == 0


def test_required_cases_must_be_present():
    data = {"material": "PLA", "required_cases": ["LC1", "LC9"],
            "cases": [{"name": "LC1 push", "supports": [1], "loads": [1]}]}
    errs = errors_of(schema.validate(data))
    assert len(errs) == 1 and "LC9" in errs[0].message


# --------------------------------------------------------------- reading

def test_missing_file_is_input_error_not_traceback(tmp_path):
    with pytest.raises(schema.InputError) as e:
        schema.load(str(tmp_path / "nope.json"))
    assert e.value.exit_code == 2
    assert "no such study file" in str(e.value)


def test_malformed_json_names_the_line(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{\n "material": "PLA",,\n}\n', encoding="utf-8")
    with pytest.raises(schema.InputError) as e:
        schema.load(str(p))
    assert "line 2" in str(e.value)


def test_yaml_and_json_give_the_same_internal_representation(tmp_path):
    yaml = pytest.importorskip("yaml", reason="YAML support is optional")
    src = os.path.join(OSMO, "gd_mount_study.json")
    if not os.path.exists(src):
        pytest.skip("reference study not present")
    data = json.load(io.open(src, encoding="utf-8-sig"))
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    from_yaml, _p, _t = schema.load(str(p))
    from_json, _p, _t = schema.load(src)
    assert from_yaml == from_json


def test_empty_file_is_rejected(tmp_path):
    p = tmp_path / "e.json"
    p.write_text("", encoding="utf-8")
    with pytest.raises(schema.InputError):
        schema.load(str(p))

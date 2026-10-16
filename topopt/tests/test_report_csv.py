"""`topopt report --csv`: `report_cases.csv` / `report_checks.csv`.

Same style as `test_report.py` -- hand-built `collect()`-shaped dicts rather
than a real run, so the case-matching and blank-when-missing rules are
checked directly instead of through whatever a particular study happens to
produce.
"""

import csv
import json
import os

from topopt.cli import report as rep


def _data_with_two_cases():
    return {
        "setup": {"ok": True, "errors": [], "warnings": []},
        "optimisation": {"converged": True, "termination_reason": "converged",
                         "settings": {"build_axis": "Z"},
                         "material": {"material_model": "orthotropic_"
                                                        "equivalent_solid"}},
        "required_safety_factor": 3.0,
        "fea": [
            {"case": "LC1 push", "load_class": "normal",
             "safety_factor_p99_5": 4.0, "min_safety_factor": 3.1,
             "max_displacement_mm": 0.20, "pass": True},
            {"case": "LC2 drop", "load_class": "abnormal",
             "required_safety_factor": 1.5,
             "safety_factor_p99_5": 1.8, "min_safety_factor": 1.6,
             "max_displacement_mm": 0.55, "pass": True},
        ],
        "ccx": {"runs": [{"cases": [
            {"case": "LC1 push 30N at the top", "load_class": "normal",
             "safety_factor_p99_5": 4.2, "worst_element_safety_factor": 2.9,
             "max_disp_mm": 0.21, "buckling_factor_min": 12.0, "pass": True},
        ]}]},
        "connectivity": {"connected": True, "components": 1},
    }


# ------------------------------------------------------------- cases rows

def test_one_row_per_case():
    rows = rep.cases_csv_rows(_data_with_two_cases())
    assert len(rows) == 2
    assert {r["case"] for r in rows} == {"LC1 push", "LC2 drop"}


def test_voxel_and_ccx_are_paired_by_the_first_token_of_the_case_name():
    """The two paths grow different long names for the same case ('LC1
    push' vs 'LC1 push 30N at the top') -- pairing must not require an exact
    match, the same rule `ccx.compare_cases` uses."""
    rows = rep.cases_csv_rows(_data_with_two_cases())
    lc1 = next(r for r in rows if r["case"] == "LC1 push")
    assert lc1["ccx_sf_p99_5"] == 4.2
    assert lc1["ccx_max_disp_mm"] == 0.21
    assert lc1["ccx_buckling_min"] == 12.0


def test_a_case_missing_from_one_source_is_blank_there_not_dropped():
    rows = rep.cases_csv_rows(_data_with_two_cases())
    lc2 = next(r for r in rows if r["case"] == "LC2 drop")
    assert lc2["ccx_sf_p99_5"] is None
    assert lc2["ccx_max_disp_mm"] is None
    assert lc2["disp_ratio_ccx_over_voxel"] is None
    # but the voxel side and the case's own required SF (abnormal -> 1.5,
    # not the study's 3.0) still come through
    assert lc2["voxel_sf_p99_5"] == 1.8
    assert lc2["required_safety_factor"] == 1.5


def test_the_displacement_ratio_is_ccx_over_voxel():
    rows = rep.cases_csv_rows(_data_with_two_cases())
    lc1 = next(r for r in rows if r["case"] == "LC1 push")
    assert lc1["disp_ratio_ccx_over_voxel"] == 0.21 / 0.20


def test_required_safety_factor_falls_back_to_the_study_value():
    rows = rep.cases_csv_rows(_data_with_two_cases())
    lc1 = next(r for r in rows if r["case"] == "LC1 push")
    assert lc1["required_safety_factor"] == 3.0    # no per-case value on LC1


def test_pass_is_the_and_of_whichever_sources_ran():
    d = _data_with_two_cases()
    d["fea"][0]["pass"] = False
    rows = rep.cases_csv_rows(d)
    lc1 = next(r for r in rows if r["case"] == "LC1 push")
    assert lc1["pass"] is False        # voxel failed even though ccx passed


def test_no_fea_and_no_ccx_is_an_empty_table():
    assert rep.cases_csv_rows({}) == []


# ------------------------------------------------------------ checks rows

def test_one_row_per_condition_in_spec_18_order():
    fails, passes, unknown = rep.evaluate(_data_with_two_cases())
    rows = rep.checks_csv_rows(fails, passes, unknown)
    assert len(rows) == len(rep.CONDITIONS)
    assert [r["name"] for r in rows] == [t for _k, t, _fn in rep.CONDITIONS]


def test_not_checked_is_blank_not_false():
    """AGENTS.md: 'not checked' must not read as failing -- only as blank,
    same as the JSON/markdown reports render it as UNKNOWN, not FAIL."""
    fails, passes, unknown = rep.evaluate(_data_with_two_cases())
    rows = rep.checks_csv_rows(fails, passes, unknown)
    unknown_keys = {k for k, _t, _d in unknown}
    assert unknown_keys, "fixture should leave something unchecked"
    for k, t, _d in unknown:
        row = next(r for r in rows if r["name"] == t)
        assert row["pass"] is None


def test_the_pass_column_matches_report_json():
    """The acceptance test: the CSV's per-check verdicts agree with the
    verdicts `report.json` records for the same run."""
    d = _data_with_two_cases()
    fails, passes, unknown = rep.evaluate(d)
    rows = rep.checks_csv_rows(fails, passes, unknown)
    by_name = {r["name"]: r["pass"] for r in rows}
    for _k, t, _d in fails:
        assert by_name[t] is False
    for _k, t, _d in passes:
        assert by_name[t] is True
    for _k, t, _d in unknown:
        assert by_name[t] is None


# ------------------------------------------------------------------ files

def test_the_cli_writes_both_csv_files_next_to_report_json(tmp_path):
    with open(tmp_path / "opt.json", "w", encoding="utf-8") as f:
        json.dump({"settings": {"build_axis": "Z"}, "history": [],
                   "converged": True, "termination_reason": "converged",
                   "material": {"material_model": "orthotropic_"
                                                  "equivalent_solid"},
                   "final_fea": [{"case": "LC1", "load_class": "normal",
                                 "safety_factor_p99_5": 4.0,
                                 "min_safety_factor": 3.1,
                                 "max_displacement_mm": 0.2, "pass": True}]},
                  f)
    code = rep.main([str(tmp_path), "--quiet", "--csv"])
    assert os.path.exists(tmp_path / "report.json")
    assert os.path.exists(tmp_path / "report_cases.csv")
    assert os.path.exists(tmp_path / "report_checks.csv")

    with open(tmp_path / "report.json", encoding="utf-8") as f:
        report_json = json.load(f)

    with open(tmp_path / "report_cases.csv", newline="", encoding="utf-8") as f:
        case_rows = list(csv.DictReader(f))
    assert len(case_rows) == 1
    assert case_rows[0]["case"] == "LC1"

    with open(tmp_path / "report_checks.csv", newline="", encoding="utf-8") as f:
        check_rows = list(csv.DictReader(f))
    passed_names = {r["condition"] for r in report_json["pass"]}
    failed_names = {r["condition"] for r in report_json["fail"]}
    for row in check_rows:
        if row["name"] in passed_names:
            assert row["pass"] == "True"
        elif row["name"] in failed_names:
            assert row["pass"] == "False"
        else:
            assert row["pass"] == ""
    assert code in (0, 1)


def test_without_csv_flag_no_csv_files_are_written(tmp_path):
    with open(tmp_path / "opt.json", "w", encoding="utf-8") as f:
        json.dump({"settings": {"build_axis": "Z"}, "history": [],
                   "converged": True, "termination_reason": "converged",
                   "material": {"material_model": "orthotropic_"
                                                  "equivalent_solid"}}, f)
    rep.main([str(tmp_path), "--quiet"])
    assert not os.path.exists(tmp_path / "report_cases.csv")
    assert not os.path.exists(tmp_path / "report_checks.csv")


def test_csv_uses_unix_line_endings(tmp_path):
    with open(tmp_path / "opt.json", "w", encoding="utf-8") as f:
        json.dump({"settings": {"build_axis": "Z"}, "history": [],
                   "converged": True, "termination_reason": "converged",
                   "material": {"material_model": "orthotropic_"
                                                  "equivalent_solid"}}, f)
    rep.main([str(tmp_path), "--quiet", "--csv"])
    raw = (tmp_path / "report_checks.csv").read_bytes()
    assert b"\r\n" not in raw
    assert b"\n" in raw

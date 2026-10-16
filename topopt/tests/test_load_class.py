"""`load_class`: a case is judged at its own bar (spec 10.1 / 15.1).

Normal cases at the study's safety factor, abnormal ones (drop, one foot
caught, transport) at 1.5 -- and undeclared means normal, so leaving the key
out can only make the judgement stricter, never looser.
"""

import pytest

from topopt.loadcase import Study
from topopt.schema import InputError, validate


def _study(**case_extra):
    return {"material": "PLA", "build_axis": "Z", "safety_factor": 3.0,
            "regions": {"a": {"type": "box", "min": [0, 0, 0],
                              "max": [1, 1, 1]}},
            "cases": [dict({"name": "LC1 push", "supports": [{"region": "a"}],
                            "loads": [{"region": "a", "force": [0, 0, -1]}]},
                           **case_extra)]}


def test_undeclared_is_normal_and_judged_at_the_study_number():
    st = Study(_study())
    assert st.cases[0].load_class == "normal"
    assert st.required_safety_factor(st.cases[0]) == 3.0


def test_abnormal_is_judged_at_one_point_five():
    st = Study(_study(load_class="abnormal"))
    assert st.cases[0].load_class == "abnormal"
    assert st.required_safety_factor(st.cases[0]) == 1.5


def test_abnormal_never_asks_for_more_than_the_study_does():
    d = _study(load_class="abnormal")
    d["safety_factor"] = 1.2
    st = Study(d)
    assert st.required_safety_factor(st.cases[0]) == 1.2


def test_case_is_not_sensitive_to_letter_case():
    st = Study(_study(load_class="Abnormal"))
    assert st.cases[0].load_class == "abnormal"


def test_an_unknown_class_is_an_input_error():
    with pytest.raises(InputError):
        Study(_study(load_class="weird"))
    bad = [p for p in validate(_study(load_class="weird"))
           if "load_class" in str(p.path)]
    assert bad and bad[0].severity == "error"


def test_the_schema_knows_the_key_so_it_is_not_an_unknown_key_warning():
    probs = validate(_study(load_class="abnormal"))
    assert not [p for p in probs if "load_class" in str(p.path)]


def test_the_final_fea_of_a_run_judges_each_case_at_its_own_bar(monkeypatch):
    """known-issue A7: `generative.final_fea` used the study's one safety
    factor for every case, so an abnormal drop case was printed as
    'normal / 3.00' while the stress constraint next to it said 1.5."""
    import inspect
    from topopt import generative
    src = inspect.getsource(generative.final_fea)
    assert "required_safety_factor(case)" in src
    assert '"load_class"' in src

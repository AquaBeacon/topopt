"""The `watertight` stop condition, and the block it reads (spec 18, 19.3).

Two things were wrong at once. `_watertight` read `data["shell"]`, which
`collect()` never set, so the condition silently reported "not checked in this
run" on every report ever produced. And it judged on open edges alone, which
is one of the three ways a shipped shape can be broken: a part that comes out
as two bodies is closed, has a reasonable mass and a plausible safety factor,
and arrives at the printer as a part plus a crumb.

`collect()` uses the shipped finish_stl isosurface when present, otherwise
the plain blocky STL. It never substitutes a good coarse shell for a broken
finished shape. Legacy merged exports may require separate validation.
"""

import json
import os

import pytest

from topopt.cli import report as rep


def _shell(open_edges=0, components=1, self_intersections=0):
    return {"triangles": 1408, "open_edges": open_edges,
            "non_manifold_edges": 0, "degenerate": 0,
            "closed_shell": open_edges == 0, "components": components,
            "edge_components": components,
            "self_intersections": self_intersections,
            "self_intersection_examples": [], "file": "part.stl"}


def _extract_blob(shell):
    return {"schema": "topopt/extract/1", "chosen_level": 0.5,
            "required_safety_factor": 3.0,
            "candidates": [{"level": 0.5, "connected": True,
                            "connectivity": {"connected": True,
                                             "components": 1},
                            "thickness": {}, "volumes": {}}],
            "outputs": {"stl": "part.stl"},
            "shell": shell}


def _verdict(data):
    fails, passes, unknown = rep.evaluate(data)
    for group, name in ((fails, "fail"), (passes, "pass"),
                        (unknown, "unknown")):
        for key, _text, detail in group:
            if key == "watertight":
                return name, detail
    raise AssertionError("the watertight condition did not appear at all")


# ------------------------------------------------------------ the condition

def test_a_clean_shell_passes():
    name, detail = _verdict({"shell": _shell()})
    assert name == "pass"
    assert "0 open edges" in detail and "1 body" in detail
    assert "no self-intersection" in detail


def test_a_shell_in_two_bodies_fails():
    """The finding this condition was blind to. Nothing else in the report
    would have caught it: the shape is closed and the volume is right."""
    name, detail = _verdict({"shell": _shell(components=2)})
    assert name == "fail"
    assert "2 separate bodies" in detail


def test_a_shell_with_a_hole_still_fails():
    name, detail = _verdict({"shell": _shell(open_edges=3)})
    assert name == "fail" and "3 open edge(s)" in detail


def test_surfaces_that_pass_through_each_other_fail():
    name, detail = _verdict({"shell": _shell(self_intersections=6)})
    assert name == "fail" and "6 self-intersecting face pair(s)" in detail


def test_an_unchecked_pair_search_is_not_reported_as_none_found():
    """`geom.self_intersections` returns None past its size ceiling. Reading
    that as "clean" is how a 200,000-triangle shell would get a pass it was
    never given."""
    name, detail = _verdict({"shell": _shell(self_intersections=None)})
    assert name == "pass", "no finding, so the part is not failed on it"
    assert "not checked" in detail


def test_a_run_with_no_shell_block_is_still_unknown():
    """An old run predates the block. UNKNOWN, never PASS (spec 18)."""
    name, _detail = _verdict({})
    assert name == "unknown"


# ------------------------------------------------------------- the wiring

def test_collect_takes_the_shell_block_off_the_extract_summary(tmp_path):
    """The half that was missing: the condition read a key nothing wrote."""
    p = tmp_path / "beam_extract.json"
    p.write_text(json.dumps(_extract_blob({"stl": _shell(components=2)})),
                 encoding="utf-8")
    data = rep.collect(str(tmp_path))
    assert data["shell"]["components"] == 2
    assert _verdict(data)[0] == "fail"


def test_the_shipped_finish_export_is_what_is_judged(tmp_path):
    """Modern finish_stl is the shipped isosurface. Raw mesh success cannot
    hide a broken final body; old merged exports need separate validation."""
    blob = _extract_blob({"stl": _shell(),
                          "finish_stl": _shell(open_edges=2502,
                                               components=1)})
    blob["shell"]["finish_stl"]["edge_components"] = 65
    blob["outputs"]["finish_stl"] = "part_finish.stl"
    p = tmp_path / "beam_extract.json"
    p.write_text(json.dumps(blob), encoding="utf-8")
    data = rep.collect(str(tmp_path))
    assert data["shell"]["open_edges"] == 2502
    assert _verdict(data)[0] == "fail"


def test_an_extract_summary_without_a_shell_block_sets_nothing(tmp_path):
    blob = _extract_blob({})
    del blob["shell"]
    p = tmp_path / "beam_extract.json"
    p.write_text(json.dumps(blob), encoding="utf-8")
    assert "shell" not in rep.collect(str(tmp_path))

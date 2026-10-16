#!/usr/bin/env python3
"""Regenerate `baselines/osmo_tripod.json` from the current osmo_tripod outputs.

    python topopt/tests/make_baseline.py            # write
    python topopt/tests/make_baseline.py --show     # print, change nothing

**Run this only when a number is supposed to have moved, and say why in the
commit.** The whole value of the baseline is that it does not change when you
did not mean it to; regenerating it to make a red test go green throws that
away and is the exact failure this file exists to prevent.

The fast numbers here are recomputed on every `pytest` run. The slow ones
(`optimisation`, `fea`) are copied from the recorded run summaries and are only
compared when `-m slow` is asked for.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                                              # noqa: E402

import probe                                                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OSMO = os.path.join(ROOT, "osmo_tripod")
Z_HINGE = 23.0


def setup_volumes():
    """`topopt setup` on the mount study, as a dict of volumes.

    `build_setup` only builds; `validate` is what fills `setup.report`, and it
    is also what decides the run is legal at all. Calling both is what the
    `setup` command does, so this measures the same thing the CLI reports.
    """
    from topopt.loadcase import load_case_from_json
    from topopt.designspace import build_setup, validate

    study = load_case_from_json(os.path.join(OSMO, "gd_mount_study.json"))
    s = build_setup(study)
    report = validate(s, study)
    info = report["info"]
    out = {k: info[k] for k in
           ("voxel_mm", "design_space_mm3", "preserve_mm3", "obstacle_mm3",
            "obstacle_declared_mm3", "free_mm3", "domain_mm3",
            "preserve_bodies") if k in info}
    out["ok"] = report["ok"]
    out["n_errors"] = len(report["errors"])
    return out


def part_fits(stl):
    """The mating dimensions a camera case actually has to fit into."""
    from topopt.voxel import load_stl
    tris = load_stl(stl)

    sp = probe.solid_spans(tris, 0.0, Z_HINGE + 6.0, axis=0)
    gp = probe.gaps(sp)
    _lo, _hi, bore_w = probe.open_extent(tris, 0, 2, Z_HINGE, 1, -4.0, 4.0)
    _lo, _hi, bore_h = probe.open_extent(tris, 0, 1, 0.0, 2,
                                         Z_HINGE - 5.0, Z_HINGE + 7.0)
    return {
        "head_finger_mm":   round(sp[0][1] - sp[0][0], 4),
        "centre_finger_mm": round(sp[1][1] - sp[1][0], 4),
        "slot_head_mm":     round(gp[0][1] - gp[0][0], 4),
        "slot_nut_mm":      round(gp[1][1] - gp[1][0], 4),
        "slot_x0_mm":       round(sp[1][1], 4),
        "slot_x1_mm":       round(sp[2][0], 4),
        "bore_width_mm":    round(bore_w, 3),
        "bore_height_mm":   round(bore_h, 3),
        "knob_clear_dia_head_mm": round(
            2 * probe.clear_radius(tris, 0, 0.0, Z_HINGE, -15.0, floor=0.3), 3),
        "knob_clear_dia_nut_mm": round(
            2 * probe.clear_radius(tris, 0, 0.0, Z_HINGE, 16.0, floor=0.3), 3),
    }


def printability(stl):
    from topopt.geom import load_stl, support_area
    r = support_area(load_stl(stl))
    return {k: round(r[k], 3) for k in
            ("volume_mm3", "area_mm2", "bed_mm2", "support_mm2")}


def recorded(path, picker):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return picker(json.load(f))


def input_hashes(rel_paths):
    """sha256 of the geometry and study a recorded run was solved from.

    Without this the recorded numbers are self-referential: `make_baseline.py`
    reads `out/gd_mount/gd_mount_study.json` and the test compares that same
    file back, so re-running `topopt opt` on *changed inputs* and regenerating
    the baseline moves both sides together and looks clean. Hashing the inputs
    makes that visible -- which is precisely the v2 -> v3 situation, where the
    preserve and obstacle meshes changed underneath a run of the same name.
    """
    from topopt.manifest import _sha256
    out = {}
    for rel in rel_paths:
        p = os.path.join(ROOT, rel)
        out[rel] = _sha256(p) if os.path.isfile(p) else None
    return out


GD_MOUNT_INPUTS = [
    "osmo_tripod/gd_mount_study.json",
    "osmo_tripod/out/mount_only.stl",
    "osmo_tripod/out/mount_cuts.stl",
    "osmo_tripod/out/design_space.stl",
]


def main(argv):
    stl = os.path.join(OSMO, "out", "osmo_tripod.stl")
    base = {
        "_what": "Regression baseline for the osmo_tripod reference model "
                 "(spec 5.2, 21 Phase 0). Recomputed by tests/make_baseline.py.",
        "_version": "v3 (2026-08-04) -- case fits widened after the printed v2 "
                    "would not assemble. See osmo_tripod/osmo_tripod.md 1.",
        "_rule": "A test going red here means a number moved. Find out why "
                 "before regenerating this file.",
        "part_stl": os.path.relpath(stl, ROOT).replace("\\", "/"),
        "fits": part_fits(stl),
        "printability": printability(stl),
        "setup_gd_mount": setup_volumes(),
        "gd_mount_inputs_sha256": input_hashes(GD_MOUNT_INPUTS),
        "optimisation_gd_mount": recorded(
            os.path.join(OSMO, "out", "gd_mount", "gd_mount_study.json"),
            lambda d: {
                "volume_mm3": round(d["volumes"]["final_mm3"], 1),
                "preserve_mm3": round(d["volumes"]["preserve_mm3"], 1),
                "iterations_run": d["settings"]["iterations_run"],
                "components": d["connectivity"]["components"],
                "island_mm3": d["connectivity"]["island_mm3"],
                "safety_factor_p99_5": {c["case"].split()[0]:
                                        round(c["safety_factor_p99_5"], 3)
                                        for c in d["final_fea"]},
            }),
        "fea_study_main": recorded(
            os.path.join(OSMO, "out", "fea_v3", "osmo_tripod_fea.json"),
            lambda d: {
                "voxel_mm": d["cases"][0]["voxel_mm"],
                "elements": d["cases"][0]["elements"],
                "cases": {c["case"].split()[0]: {
                    "safety_factor_p99_5": round(c["safety_factor_p99_5"], 3),
                    "max_displacement_mm": round(c["max_displacement_mm"], 4),
                } for c in d["cases"]},
            }),
    }

    text = json.dumps(base, indent=2, ensure_ascii=False) + "\n"
    if "--show" in argv:
        print(text)
        return 0
    out = os.path.join(HERE, "baselines", "osmo_tripod.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

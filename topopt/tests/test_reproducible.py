"""Same study, same version, same seed -> the same run (spec 19.6, 20).

Run on `cpu:jacobi`, not on whatever the machine has. The CUDA backend sums a
reduction in an order the driver chooses, so two GPU runs of the same solve
agree to solver tolerance and not to the last bit -- which is the correct
behaviour for a solver and the wrong thing to assert bit-for-bit against. The
claim being made here is about *this tool's* determinism, so it is made on the
backend whose arithmetic is deterministic.

**What consumes `random_seed`: nothing.** The only random number in the whole
package is `backend.randn` in `multigrid.estimate_lam_max`, and it is called
with a hard-coded `seed=0`, so the multigrid smoother's eigenvalue estimate is
the same array every time. Everything else -- the uniform initial density, the
OC bisection, the filter, the projection, CG -- is a deterministic function of
the inputs. That is exactly what `manifest.random_seed_note` says when a study
declares no seed, and this file is the check that the claim is still true
rather than a sentence somebody wrote once.

So: a seed in the study changes nothing, and two runs are identical anyway.
The day something here does draw a random number, `test_the_two_runs_agree_bit_for_bit`
is what will notice.
"""

import json
import os

import numpy as np
import pytest

pytestmark = pytest.mark.slow

from test_run_layout import STUDY                          # noqa: E402 -- reuse


def _run(tmp, tag, seed=None):
    from topopt.cli import optimize as opt_cli
    from topopt.loadcase import load_case_from_json

    study = dict(STUDY)
    if seed is not None:
        study["random_seed"] = seed
    path = os.path.join(tmp, "%s_study.json" % tag)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(study, f)
    out = os.path.join(tmp, tag)
    s = load_case_from_json(path)
    summary = opt_cli.run_generative(s, path, dict(study.get("design", {})),
                                     out, iters=3, quiet=True, do_fea=True)
    return out, summary


@pytest.fixture(scope="module")
def twice(tmp_path_factory, request):
    d = str(tmp_path_factory.mktemp("repro"))
    old = os.environ.get("TOPOPT_SOLVER")
    os.environ["TOPOPT_SOLVER"] = "cpu:jacobi"
    try:
        a = _run(d, "first", seed=1234)
        b = _run(d, "second", seed=1234)
    finally:
        if old is None:
            os.environ.pop("TOPOPT_SOLVER", None)
        else:
            os.environ["TOPOPT_SOLVER"] = old
    return a, b


def _arrays(outdir, tag):
    name = [f for f in os.listdir(outdir) if f.endswith("_%s.npy" % tag)][0]
    return np.load(os.path.join(outdir, name))


def test_the_two_runs_agree_bit_for_bit(twice):
    """Not `allclose`. A tolerance here would pass a run that had picked up a
    source of randomness and only pin down how much of it there was."""
    (dir_a, _sa), (dir_b, _sb) = twice
    for tag in ("density_raw", "density_phys", "binary"):
        a, b = _arrays(dir_a, tag), _arrays(dir_b, tag)
        assert a.shape == b.shape
        assert np.array_equal(a, b), "%s differs between runs" % tag


def test_the_solver_used_was_the_deterministic_one(twice):
    """If this ever reads `cuda`, the test above is asserting something the
    hardware does not promise and the failure would be a mystery."""
    (_a, sa), (_b, sb) = twice
    assert sa["solver"]["backend"] == "cpu"
    assert sa["solver"]["preconditioner"] == "jacobi"
    assert sb["solver"]["backend"] == "cpu"


def test_every_reported_number_is_the_same(twice):
    """Wall-clock is the only field allowed to differ, and it is dropped by
    name rather than by "numbers that happen not to match"."""
    (_a, sa), (_b, sb) = twice
    drop = {"study", "seconds", "solve_seconds", "outputs", "setup"}

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in drop}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    assert clean(sa) == clean(sb)


def test_the_convergence_history_is_compared_too(twice):
    """Dropping `history` and `cg_iterations` would leave the test asserting
    almost nothing: those are where a drifting solve shows up first."""
    (_a, sa), (_b, sb) = twice
    assert sa["history"] == sb["history"]
    assert sa["history"], "a run with no history proves nothing"
    assert sa["solver"]["cg_iterations"] == sb["solver"]["cg_iterations"]


def test_nothing_in_the_package_draws_an_unseeded_random_number():
    """The claim `manifest.random_seed_note` makes, checked. `backend.randn`
    is the only generator, and `multigrid` calls it with a fixed seed."""
    import re
    import topopt

    root = os.path.dirname(os.path.abspath(topopt.__file__))
    offenders = []
    for base, _dirs, files in os.walk(root):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    # `seed=` / `generator=` on the line is what makes a draw
                    # reproducible; anything else is a live generator.
                    if (re.search(r"\b(np\.random|random\.(?!seed)|randn\()",
                                  line)
                            and not re.search(r"seed|generator=", line)):
                        offenders.append("%s:%d %s" % (f, i, line.strip()))
    assert not offenders, "\n".join(offenders)


def test_a_declared_seed_is_recorded_even_though_nothing_reads_it():
    """It goes in the manifest so a future run that *does* consume it can be
    told apart from this one -- which is the whole point of recording a value
    the tool does not currently use."""
    from topopt import manifest
    m = manifest.build("opt", ["s.json", "--out", "D"], seed=1234,
                       probe_external=False)
    assert m["random_seed"] == 1234
    assert "random_seed_note" not in m
    m2 = manifest.build("opt", ["s.json", "--out", "D"], probe_external=False)
    assert m2["random_seed"] is None and m2["random_seed_note"]

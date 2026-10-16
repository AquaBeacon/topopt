"""The smoke checks as pytest cases -- one test per check, named as it prints.

The bodies live in `checks.py` and are shared with `smoke.py`; this file only
decides how they are reported. Adding a check there adds a test here for free,
which is the point: there is no second list to keep in step.
"""

import pytest

import checks


@pytest.fixture(scope="module")
def ctx():
    """The 100 x 4 x 4 mm bar, built once for the whole module.

    Module-scoped because `Ctx.opt` runs an 8-iteration optimisation and three
    separate checks read its result; per-test scope would solve it three times
    to assert three different things about the same run.
    """
    return checks.Ctx()


def test_imports():
    ok, detail = checks.check_imports()
    assert ok, detail


@pytest.mark.parametrize(
    "name,fn",
    [(n, f) for n, f, needs_ctx in checks.CHECKS if needs_ctx],
    ids=[n for n, _f, needs_ctx in checks.CHECKS if needs_ctx],
)
def test_check(ctx, name, fn):
    ok, detail = fn(ctx)
    assert ok, "%s: %s" % (name, detail)

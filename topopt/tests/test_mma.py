"""The MMA port must reproduce published optima.

`mma.py` is a transcription of Svanberg's reference code, so the honest test
is not "does it decrease the objective" but "does it land where the reference
lands". The five-variable cantilever from Svanberg's 2007 MMA notes has a
tabulated optimum; the small quadratic problems have optima that can be
written down by hand.
"""

import numpy as np

from topopt.mma import MMA


def test_svanberg_beam_reaches_the_published_optimum():
    # minimise 0.0624 (x1 + ... + x5)
    # s.t.     61/x1^3 + 37/x2^3 + 19/x3^3 + 7/x4^3 + 1/x5^3 <= 1,  1 <= x <= 10
    w = np.array([61.0, 37.0, 19.0, 7.0, 1.0])
    x = np.full(5, 5.0)
    opt = MMA(n=5, m=1, xmin=1.0, xmax=10.0, move=1.0)
    for _ in range(60):
        f0 = 0.0624 * x.sum()
        df0 = np.full(5, 0.0624)
        f = np.array([(w / x ** 3).sum() - 1.0])
        df = (-3.0 * w / x ** 4)[None, :]
        x = opt.step(x, f0, df0, f, df)
    expected = np.array([6.016, 5.309, 4.494, 3.502, 2.153])
    assert np.allclose(x, expected, atol=2e-3), x
    assert abs(0.0624 * x.sum() - 1.340) < 2e-3
    assert (w / x ** 3).sum() <= 1.0 + 1e-6


def test_two_active_constraints_are_both_honoured():
    # minimise (x1 - 1)^2 + (x2 - 2)^2
    # s.t. x1 + x2 <= 2,  x2 - x1 <= 0.5,  0 <= x <= 3   ->  (0.75, 1.25)
    x = np.array([0.1, 0.1])
    opt = MMA(n=2, m=2, xmin=0.0, xmax=3.0, move=0.5)
    for _ in range(80):
        f0 = (x[0] - 1) ** 2 + (x[1] - 2) ** 2
        df0 = np.array([2 * (x[0] - 1), 2 * (x[1] - 2)])
        f = np.array([x[0] + x[1] - 2.0, x[1] - x[0] - 0.5])
        df = np.array([[1.0, 1.0], [-1.0, 1.0]])
        x = opt.step(x, f0, df0, f, df)
    assert np.allclose(x, [0.75, 1.25], atol=2e-3), x


def test_infeasible_start_is_driven_feasible():
    # Start deep inside the forbidden region; the elastic slack (c = 1000)
    # has to pull the design back over the constraint, not leave it there.
    x = np.array([3.0, 3.0])
    opt = MMA(n=2, m=1, xmin=0.0, xmax=3.0, move=0.3)
    for _ in range(60):
        f0 = -(x[0] + 2 * x[1])
        df0 = np.array([-1.0, -2.0])
        f = np.array([x[0] ** 2 + x[1] ** 2 - 1.0])
        df = np.array([[2 * x[0], 2 * x[1]]])
        x = opt.step(x, f0, df0, f, df)
    assert x[0] ** 2 + x[1] ** 2 <= 1.0 + 1e-4
    # Optimum of the underlying problem: x = (1, 2) / sqrt(5).
    assert np.allclose(x, np.array([1.0, 2.0]) / np.sqrt(5.0), atol=3e-3), x


def test_zero_gradient_variables_stay_put():
    # A variable that neither objective nor constraint depends on must not
    # drift: the self-support filter produces whole regions like that.
    x = np.array([0.4, 0.7])
    opt = MMA(n=2, m=1, xmin=0.0, xmax=1.0, move=0.2)
    for _ in range(40):
        f0 = (x[0] - 0.2) ** 2
        df0 = np.array([2 * (x[0] - 0.2), 0.0])
        f = np.array([x[0] - 0.9])
        df = np.array([[1.0, 0.0]])
        x = opt.step(x, f0, df0, f, df)
    assert abs(x[1] - 0.7) < 1e-6, x
    # The driven variable settles to within MMA's usual asymptote wobble.
    assert abs(x[0] - 0.2) < 2e-2, x

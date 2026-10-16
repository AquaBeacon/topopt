"""The smoke checks themselves, with no opinion about how they are reported.

`smoke.py` prints them; `test_smoke.py` asserts them. Both call the same
functions, so the standalone self-check and the pytest suite can never drift
into testing different things -- which is the whole reason this file exists
rather than the checks living in one of them.

Every check returns `(ok, detail)`. `detail` is written to be read on failure
*and* on success: "4.1 %" tells you more than "passed", and several of these
numbers are the ones a reviewer actually wants to see.

What is checked here is not what is easy to assert -- it is what has actually
gone wrong in this project before. See the note on each function.
"""

import tempfile

import numpy as np

MODULES = ["voxel", "hex8", "solve", "materials", "loadcase", "report",
           "members", "designspace", "generative", "meshout", "setup_preview",
           "geom", "stepio", "cli"]


# --------------------------------------------------------------- fixtures

def build_case(grid, tip_force=-50.0):
    """Study for the bar: built in at the x=0 end, pushed down at x=max.

    Both regions are one voxel column deep. A thicker clamp shortens the beam
    (every node in it is fixed), which on its own made an earlier version of
    this test read 23 % stiff against Euler-Bernoulli -- a boundary-condition
    artefact, not an element error.
    """
    from topopt.loadcase import Study
    h, lo = grid.h, grid.origin
    hi = lo + np.array(grid.shape) * h
    pad = 0.1 * h
    return Study({
        "material": "PLA",
        # Declared, not defaulted: since Phase 2 the optimiser is orthotropic
        # by default and refuses an assumed build axis, so leaving this out
        # would make every check below exercise only the escape hatch.
        "build_axis": "Z",
        "regions": {
            "root": {"type": "box",
                     "min": [lo[0] - pad, lo[1] - pad, lo[2] - pad],
                     "max": [lo[0] + 0.5 * h + pad, hi[1] + pad, hi[2] + pad]},
            "tip":  {"type": "box",
                     "min": [hi[0] - 0.5 * h - pad, lo[1] - pad, lo[2] - pad],
                     "max": [hi[0] + pad, hi[1] + pad, hi[2] + pad]},
        },
        "cases": [{
            "name": "tip load",
            "supports": [{"region": "root", "fix": "xyz"}],
            "loads": [{"region": "tip", "force": [0, 0, tip_force]}],
        }],
    }, base_dir=".")


class Ctx:
    """The 100 x 4 x 4 mm bar every check below shares, built once.

    L/t = 25, slender enough that Euler-Bernoulli is the right model to
    compare against rather than a rough guide.
    """

    def __init__(self):
        from topopt.voxel import Grid
        from topopt.materials import get_material
        from topopt.designspace import Setup

        self.h = 1.0
        self.nx, self.ny, self.nz = 100, 4, 4
        self.grid = Grid(np.ones((self.nx, self.ny, self.nz), bool),
                         self.h, np.zeros(3))
        self.mat = get_material("PLA")
        self.study = build_case(self.grid, tip_force=-50.0)

        free = np.zeros(self.grid.shape, bool)
        free[6:94] = True
        self.preserve = np.zeros(self.grid.shape, bool)
        self.preserve[:6] = True
        self.preserve[94:] = True
        self.obstacle = np.zeros(self.grid.shape, bool)
        self.obstacle[40:50, :, :2] = True
        free &= ~self.obstacle
        # Built by hand, not via build_setup -- `free`, `starting_shape` and
        # `obstacle_in_preserve` are filled in by Setup.__post_init__.
        self.setup = Setup(grid=self.grid, bodies=[],
                           design_space=free | self.preserve | self.obstacle,
                           preserve=self.preserve, obstacle=self.obstacle,
                           obstacle_raw=self.obstacle)
        self._opt = None

    @property
    def opt(self):
        """The 8-iteration optimisation result, solved once and reused."""
        if self._opt is None:
            from topopt.generative import optimize
            # penal and beta are held constant: with continuation the
            # objective is re-measured on different physics every few
            # iterations and "it went down" means nothing. What this run
            # checks is descent, preserve survival and obstacle emptiness.
            self._opt = optimize(self.setup, self.study,
                                 {"volfrac": 0.4, "filter_mm": 3.0,
                                  "penal": 1.0, "beta": 1.0},
                                 iters=8, verbose=False)
        return self._opt


# ----------------------------------------------------------------- checks

def check_imports():
    """Every module imports. A broken optional import used to surface only
    when a specific command was run, hours into a session."""
    import importlib
    bad = []
    for m in MODULES:
        try:
            importlib.import_module("topopt." + m)
        except Exception as exc:                                # noqa: BLE001
            bad.append("%s (%s)" % (m, exc))
    return not bad, "%d modules%s" % (
        len(MODULES), "" if not bad else " -- " + "; ".join(bad))


def check_cantilever(ctx):
    """Tip deflection against Euler-Bernoulli. Guards the element formulation
    *and* the boundary-condition handling -- see `build_case`."""
    from topopt.solve import solve_linear_elastic
    from topopt.loadcase import build_bcs

    fixed, force = build_bcs(ctx.grid, ctx.study.cases[0], ctx.study.regions,
                             ctx.mat, ctx.study.infill, ctx.study.walls)
    res = solve_linear_elastic(ctx.grid, ctx.mat.E_xy, ctx.mat.nu, fixed, force,
                               sigma_xy=ctx.mat.sigma_xy,
                               sigma_z=ctx.mat.sigma_z, want_stress=False)
    tip_mm = float(res.max_disp)

    L, b, t = ctx.nx * ctx.h, ctx.ny * ctx.h, ctx.nz * ctx.h
    analytic = 50.0 * L ** 3 / (3 * ctx.mat.E_xy * (b * t ** 3 / 12))
    err = abs(tip_mm - analytic) / analytic
    return err < 0.10, (
        "FEA %.3f mm vs beam %.3f mm (%.1f %%; FEA is stiffer -- a clamped "
        "face blocks the warping beam theory allows)"
        % (tip_mm, analytic, 100 * err))


def check_preserve_stays_one(ctx):
    """`impose` must pin preserve to exactly 1. Rule 2 of the design-space
    model; breaking it is how mounting faces quietly rounded off once."""
    x = ctx.setup.impose(np.random.default_rng(0).random(ctx.grid.shape))
    return bool((x[ctx.preserve] == 1.0).all()), "%d cells" % ctx.preserve.sum()


def check_obstacle_stays_zero(ctx):
    x = ctx.setup.impose(np.random.default_rng(0).random(ctx.grid.shape))
    return bool((x[ctx.obstacle] == 0.0).all()), "%d cells" % ctx.obstacle.sum()


def check_symmetry(ctx):
    """Symmetry really mirrors. This silently did nothing once, because
    np.flip mirrors about the *array* centre and grid rounding put that off
    the geometry mid-plane."""
    from topopt.generative import apply_symmetry
    field = np.random.default_rng(1).random(ctx.grid.shape)
    everywhere = np.ones(ctx.grid.shape, bool)
    sym = apply_symmetry(field, ["Y"], everywhere)
    resid = float(np.abs(sym - np.flip(sym, axis=1)).max())
    return resid < 1e-12, "max residual %.2e" % resid


def check_stl_roundtrip(ctx):
    """A blocky STL voxelises back to the same cells. Guards both the writer
    and the ray-parity fill, and would catch an origin convention flip."""
    from topopt.voxel import voxelize_stl
    from topopt.meshout import write_blocky_stl

    shape = np.zeros(ctx.grid.shape, bool)
    shape[5:60, 1:3, 1:3] = True
    with tempfile.TemporaryDirectory() as tmp:
        path = tmp + "/rt.stl"
        write_blocky_stl(path, shape, ctx.h, ctx.grid.origin)
        back = voxelize_stl(path, h=ctx.h, pad=2)

    off = np.round((back.origin - ctx.grid.origin) / ctx.h).astype(int)
    rebuilt = np.zeros(ctx.grid.shape, bool)
    src, dst, ok = [], [], True
    for a in range(3):
        lo = max(0, off[a])
        hi = min(ctx.grid.shape[a], off[a] + back.shape[a])
        if hi <= lo:
            ok = False
            break
        dst.append(slice(lo, hi))
        src.append(slice(lo - off[a], hi - off[a]))
    if ok:
        rebuilt[tuple(dst)] = back.solid[tuple(src)]
    diff = int((rebuilt != shape).sum())
    return ok and diff == 0, "%d of %d voxels differ" % (diff, int(shape.sum()))


def check_objective_decreases(ctx):
    obj = [r["objective"] for r in ctx.opt["history"]]
    return obj[-1] < obj[0], ("%.4f -> %.4f over %d iters"
                              % (obj[0], obj[-1], len(obj)))


def check_preserve_survives_run(ctx):
    """Preserve is still 1 *after* filtering, projection, symmetry and
    thresholding have all run. Order-of-operations regression."""
    xp = ctx.opt["x_phys"]
    return bool((xp[ctx.preserve] == 1.0).all()), \
        "min density on preserve %.3f" % xp[ctx.preserve].min()


def check_obstacle_empty_after_run(ctx):
    xp = ctx.opt["x_phys"]
    return bool((xp[ctx.obstacle] == 0.0).all()), \
        "max density in obstacle %.3f" % xp[ctx.obstacle].max()


# The order `smoke.py` prints them in, and the ids pytest parametrises over.
# `imports` takes no context because it is what proves a context can be built.
CHECKS = [
    ("imports",                      check_imports,                  False),
    ("cantilever vs Euler-Bernoulli", check_cantilever,              True),
    ("preserve stays 1",             check_preserve_stays_one,       True),
    ("obstacle stays 0",             check_obstacle_stays_zero,      True),
    ("symmetry mirrors the field",   check_symmetry,                 True),
    ("STL round-trip",               check_stl_roundtrip,            True),
    ("objective decreases",          check_objective_decreases,      True),
    ("preserve survives the run",    check_preserve_survives_run,    True),
    ("obstacle stays empty",         check_obstacle_empty_after_run, True),
]

NAMES = [name for name, _fn, _needs_ctx in CHECKS]

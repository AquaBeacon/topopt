"""Array backends for the linear solver: numpy on the CPU, torch on a GPU.

The solver (`solve.py`, `multigrid.py`) is written once against the small
set of operations below and runs on whichever backend is selected. numpy is
always available and is the reference. torch is optional: when it is
installed *and* sees a CUDA device, the same gather / multiply / scatter
operations run there, which on a 100k-element grid is 25-30x faster per
conjugate-gradient iteration (measured 45 ms -> 1.5 ms on an RTX 4060 Ti).

Selection (`resolve`):
    "auto"  -> cuda if torch + CUDA are usable, else cpu      (default)
    "cpu"   -> numpy
    "cuda"  -> torch on the first CUDA device; error if unavailable
The environment variable TOPOPT_SOLVER sets the default for every command
(e.g. `TOPOPT_SOLVER=cpu` to force the reference path).

Everything is float64 on both backends. The GPU is used for its memory
bandwidth, not its float32 throughput, and keeping double precision means a
GPU result and a CPU result agree to solver tolerance rather than to three
digits. Nothing here depends on the grid or the physics.
"""

import os

import numpy as np

_DEFAULT_ENV = "TOPOPT_SOLVER"


class NumpyBackend:
    """The reference backend. Every other backend must agree with it."""
    name = "cpu"
    device = "cpu"

    def asarray(self, a, dtype=None):
        return np.asarray(a, dtype=dtype)

    def to_numpy(self, a):
        return np.asarray(a)

    def zeros(self, shape, dtype=float):
        return np.zeros(shape, dtype=dtype)

    def zeros_like(self, a):
        return np.zeros_like(a)

    float32 = np.float32
    float64 = np.float64

    def cast(self, a, dtype):
        return np.asarray(a, dtype=dtype)

    def dtype_of(self, a):
        return np.asarray(a).dtype

    def copy(self, a):
        return np.array(a, copy=True)

    def where(self, cond, a, b):
        return np.where(cond, a, b)

    def sqrt(self, a):
        return np.sqrt(a)

    def moveaxis(self, a, src, dst):
        return np.moveaxis(a, src, dst)

    def scatter_add(self, idx, vals, n):
        """Sum `vals` (N,) or (N, m) into `n` rows addressed by `idx` (N,)."""
        if vals.ndim == 1:
            return np.bincount(idx, weights=vals, minlength=n)
        out = np.empty((n, vals.shape[1]))
        for m in range(vals.shape[1]):
            out[:, m] = np.bincount(idx, weights=vals[:, m], minlength=n)
        return out

    def index_add(self, target, idx, vals):
        """target[idx] += vals, in place; `idx` may repeat."""
        np.add.at(target, idx, vals)
        return target

    def col_norm(self, v):
        return np.sqrt((v * v).sum(axis=0))

    def col_dot(self, a, b):
        return (a * b).sum(axis=0)

    def cholesky(self, A):
        from scipy.linalg import cho_factor
        return cho_factor(A, lower=True, check_finite=False)

    def cho_solve(self, fac, b):
        from scipy.linalg import cho_solve
        return cho_solve(fac, b, check_finite=False)

    def randn(self, shape, seed=0):
        return np.random.default_rng(seed).standard_normal(shape)

    def synchronize(self):
        pass

    def item(self, a):
        return float(a)


class TorchBackend:
    """torch on a CUDA device. Same contract as `NumpyBackend`, float64."""
    name = "cuda"

    def __init__(self, device="cuda"):
        import torch
        self.torch = torch
        self.device = torch.device(device)
        self.dtype = torch.float64
        self.float32 = torch.float32
        self.float64 = torch.float64
        self.device_name = torch.cuda.get_device_name(self.device)
        # Python scalars handed to `where` (0.0, 1.0, ...) used to become a
        # fresh device tensor on every call. Each of those is a host-to-device
        # copy, and a copy from pageable memory waits for everything already
        # queued on the stream -- so every `where` drained the GPU pipeline.
        # Measured on a 100k-element solve: 11,558 such copies in 4
        # optimiser iterations, 12.5 s of a 25 s run spent waiting in them.
        # The scalars are cached once per value instead.
        self._scalars = {}

    def asarray(self, a, dtype=None):
        t = self.torch
        if isinstance(a, t.Tensor):
            return a.to(self.device)
        a = np.asarray(a)
        if dtype is None:
            if a.dtype == np.bool_:
                td = t.bool
            elif np.issubdtype(a.dtype, np.integer):
                td = t.int64
            else:
                td = self.dtype
        else:
            td = {bool: t.bool, int: t.int64, float: self.dtype}[dtype]
        return t.as_tensor(np.ascontiguousarray(a), device=self.device, dtype=td)

    def to_numpy(self, a):
        return a.detach().cpu().numpy()

    def zeros(self, shape, dtype=float):
        td = {bool: self.torch.bool, int: self.torch.int64,
              float: self.dtype}.get(dtype, dtype)
        return self.torch.zeros(shape, dtype=td, device=self.device)

    def zeros_like(self, a):
        return self.torch.zeros_like(a)

    def copy(self, a):
        return a.clone()

    def _scalar(self, v, dtype):
        key = (float(v), dtype)
        s = self._scalars.get(key)
        if s is None:
            s = self.torch.tensor(float(v), dtype=dtype, device=self.device)
            self._scalars[key] = s
        return s

    def where(self, cond, a, b):
        """Scalars take the dtype of the tensor argument, so a float32
        operand never gets promoted to float64 by a 0.0 next to it."""
        t = self.torch
        a_t, b_t = isinstance(a, t.Tensor), isinstance(b, t.Tensor)
        dtype = a.dtype if a_t else (b.dtype if b_t else self.dtype)
        if dtype == t.bool:
            dtype = self.dtype
        if not a_t:
            a = self._scalar(a, dtype)
        if not b_t:
            b = self._scalar(b, dtype)
        return t.where(cond, a, b)

    def cast(self, a, dtype):
        return a.to(dtype)

    def dtype_of(self, a):
        return a.dtype

    def sqrt(self, a):
        return self.torch.sqrt(a)

    def moveaxis(self, a, src, dst):
        return self.torch.movedim(a, src, dst)

    def scatter_add(self, idx, vals, n):
        out = self.torch.zeros((n,) + tuple(vals.shape[1:]), dtype=vals.dtype,
                               device=self.device)
        return out.index_add_(0, idx, vals)

    def index_add(self, target, idx, vals):
        return target.index_add_(0, idx, vals)

    def col_norm(self, v):
        return self.torch.sqrt((v * v).sum(dim=0))

    def col_dot(self, a, b):
        return (a * b).sum(dim=0)

    def cholesky(self, A):
        return self.torch.linalg.cholesky(A)

    def cho_solve(self, fac, b):
        return self.torch.cholesky_solve(b, fac)

    def randn(self, shape, seed=0):
        g = self.torch.Generator(device=self.device).manual_seed(seed)
        return self.torch.randn(shape, generator=g, dtype=self.dtype,
                                device=self.device)

    def synchronize(self):
        self.torch.cuda.synchronize(self.device)

    def item(self, a):
        return float(a)


def cuda_available():
    """-> (bool, reason). Never raises: torch is optional."""
    try:
        import torch
    except Exception as exc:                               # noqa: BLE001
        return False, "torch not installed (%s)" % exc.__class__.__name__
    try:
        if not torch.cuda.is_available():
            return False, "torch %s sees no CUDA device" % torch.__version__
    except Exception as exc:                               # noqa: BLE001
        return False, "torch.cuda probe failed: %s" % exc
    return True, "torch %s, %s" % (torch.__version__, torch.cuda.get_device_name(0))


_cache = {}


def resolve(name=None):
    """Backend instance for `name` ("auto" | "cpu" | "cuda" | None).

    None reads TOPOPT_SOLVER and falls back to "auto". Instances are cached:
    a torch backend holds a CUDA context and there is no reason to make two.
    """
    key = (name or os.environ.get(_DEFAULT_ENV) or "auto").strip().lower()
    if key in ("auto", ""):
        ok, _why = cuda_available()
        key = "cuda" if ok else "cpu"
    if key not in ("cpu", "cuda"):
        raise ValueError("unknown solver backend %r (use auto, cpu or cuda)" % name)
    if key not in _cache:
        if key == "cuda":
            ok, why = cuda_available()
            if not ok:
                raise RuntimeError("solver backend 'cuda' requested but %s. Install "
                                   "a CUDA build of torch or use --solver cpu." % why)
            _cache[key] = TorchBackend()
        else:
            _cache[key] = NumpyBackend()
    return _cache[key]


def add_cli_argument(ap):
    """The `--solver` option every solving command shares."""
    ap.add_argument("--solver", metavar="SPEC",
                    help="how to solve: auto (default: GPU if torch sees CUDA, "
                         "else CPU), cpu, cuda; optionally ':mg' (multigrid, "
                         "default; on a GPU its V-cycle runs in float32 inside "
                         "a float64 CG), ':mg64' (multigrid, everything in "
                         "float64) or ':jacobi' (the old preconditioner), e.g. "
                         "cpu:jacobi. Same as the TOPOPT_SOLVER environment "
                         "variable.")


def apply_cli_choice(args):
    """Make `--solver` the process-wide default so every solve in this
    command (and every stage `topopt run` calls in-process) uses it."""
    spec = getattr(args, "solver", None)
    if spec:
        os.environ[_DEFAULT_ENV] = spec
    return spec


def describe(backend):
    """Reproducibility line for manifests and summaries. -> dict"""
    d = {"backend": backend.name}
    if backend.name == "cuda":
        d["device"] = backend.device_name
        d["torch"] = backend.torch.__version__
    else:
        d["numpy"] = np.__version__
    return d

"""Trilinear 8-node hexahedron on a regular grid.

Every voxel is the same cube, so the element stiffness matrix is computed once
and reused for all of them (scaled by the element's Young's modulus). That is
what makes a matrix-free solve practical at 10^5 elements.

Local node order: n = a + 2*b + 4*c for a, b, c in {0, 1}, i.e. natural
coordinates xi = 2a-1, eta = 2b-1, zeta = 2c-1.
Voigt order: [xx, yy, zz, xy, yz, zx] with engineering shear strains.
"""

import numpy as np

_G = 1.0 / np.sqrt(3.0)
_GAUSS = np.array([-_G, _G])

# Natural coordinates of the 8 local nodes, in local-node order.
_NAT = np.array([[2 * (n & 1) - 1, 2 * ((n >> 1) & 1) - 1, 2 * ((n >> 2) & 1) - 1]
                 for n in range(8)], float)


def constitutive(E: float, nu: float) -> np.ndarray:
    """Isotropic 6x6 D matrix (Voigt, engineering shear)."""
    lam = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[0, 0] = D[1, 1] = D[2, 2] = lam + 2 * mu
    D[3, 3] = D[4, 4] = D[5, 5] = mu
    return D


# Voigt index <-> tensor index, engineering shear on the strain side.
#
# The order is this module's own -- [xx, yy, zz, xy, yz, zx] -- not the
# (yz, zx, xy) convention most textbooks use. It has to match `b_matrices`,
# whose rows 3/4/5 are gamma_xy / gamma_yz / gamma_zx. Using the textbook order
# here is invisible for an isotropic D (all three shear diagonals are mu) and
# silently swaps Gxy with Gyz for an orthotropic one.
_VOIGT = [(0, 0), (1, 1), (2, 2), (0, 1), (1, 2), (2, 0)]


def voigt_to_tensor(D: np.ndarray) -> np.ndarray:
    """6x6 Voigt -> 3x3x3x3 stiffness tensor.

    The identity used is `C_ijkl = D[V(ij), V(kl)]` with no factors, which
    holds precisely because the tensor form sums over both (k,l) and (l,k) and
    so reproduces the engineering shear 2*eps_kl on its own.
    """
    C = np.zeros((3, 3, 3, 3))
    for a, (i, j) in enumerate(_VOIGT):
        for b, (k, l) in enumerate(_VOIGT):
            v = D[a, b]
            for ii, jj in ((i, j), (j, i)):
                for kk, ll in ((k, l), (l, k)):
                    C[ii, jj, kk, ll] = v
    return C


def tensor_to_voigt(C: np.ndarray) -> np.ndarray:
    D = np.zeros((6, 6))
    for a, (i, j) in enumerate(_VOIGT):
        for b, (k, l) in enumerate(_VOIGT):
            D[a, b] = C[i, j, k, l]
    return D


def rotate_stiffness(D: np.ndarray, R: np.ndarray) -> np.ndarray:
    """Rotate a 6x6 stiffness by the 3x3 rotation `R` (columns = new axes).

    Done through the fourth-order tensor rather than with a 6x6 Bond matrix:
    the Bond form is easy to get subtly wrong for engineering shear, and this
    is neither hot code nor called per element.
    """
    C = voigt_to_tensor(np.asarray(D, float))
    R = np.asarray(R, float)
    C2 = np.einsum("ip,jq,kr,ls,pqrs->ijkl", R, R, R, R, C)
    return tensor_to_voigt(C2)


def material_rotation(build_axis: int, raster_axis: int = None) -> np.ndarray:
    """3x3 rotation taking *material* axes to *global* axes.

    The card is written in material axes -- X along the raster, Y in-plane
    transverse, Z along the build direction. A part printed on its side has
    those axes somewhere else, and getting this mapping wrong swaps the
    strongest direction for the weakest, which is the single most damaging
    mistake available here.

    `raster_axis` must differ from `build_axis`; it defaults to the first
    global axis that is not the build axis.
    """
    b = int(build_axis)
    if b not in (0, 1, 2):
        raise ValueError("build_axis must be 0, 1 or 2, got %r" % (build_axis,))
    if raster_axis is None:
        r = 0 if b != 0 else 1
    else:
        r = int(raster_axis)
    if r == b:
        raise ValueError("raster_axis (%d) must differ from build_axis (%d) -- "
                         "the raster runs within the layer plane" % (r, b))
    other = ({0, 1, 2} - {b, r}).pop()
    R = np.zeros((3, 3))
    R[r, 0] = 1.0          # material X (raster)   -> global r
    R[other, 1] = 1.0      # material Y (in-plane) -> global `other`
    R[b, 2] = 1.0          # material Z (build)    -> global b
    if np.linalg.det(R) < 0:            # keep it a rotation, not a reflection
        R[:, 1] *= -1.0
    return R


def _shape_derivs(xi, eta, zeta):
    """dN/d(natural) -> (3, 8)."""
    n = _NAT
    d = np.empty((3, 8))
    d[0] = 0.125 * n[:, 0] * (1 + n[:, 1] * eta) * (1 + n[:, 2] * zeta)
    d[1] = 0.125 * (1 + n[:, 0] * xi) * n[:, 1] * (1 + n[:, 2] * zeta)
    d[2] = 0.125 * (1 + n[:, 0] * xi) * (1 + n[:, 1] * eta) * n[:, 2]
    return d


def b_matrices(h: float):
    """B matrices and Jacobian weight at the 8 Gauss points of a cube of side h.

    Returns (B, w) with B shape (8, 6, 24) and w the scalar integration weight
    (equal at every point for a cube).
    """
    # Natural coords span [-1, 1] over a physical length h, so dN/dx = dN/dxi * 2/h.
    scale = 2.0 / h
    detJ = (h / 2.0) ** 3
    Bs = []
    for zeta in _GAUSS:
        for eta in _GAUSS:
            for xi in _GAUSS:
                d = _shape_derivs(xi, eta, zeta) * scale
                B = np.zeros((6, 24))
                for n in range(8):
                    dx, dy, dz = d[:, n]
                    c = 3 * n
                    B[0, c + 0] = dx
                    B[1, c + 1] = dy
                    B[2, c + 2] = dz
                    B[3, c + 0] = dy
                    B[3, c + 1] = dx
                    B[4, c + 1] = dz
                    B[4, c + 2] = dy
                    B[5, c + 0] = dz
                    B[5, c + 2] = dx
                Bs.append(B)
    return np.array(Bs), detJ  # weight 1.0 per Gauss point for 2x2x2


def _g_matrices(h: float):
    """B-like matrices for the 3 incompatible bubble modes, at the Gauss points.

    Modes are m1 = 1-xi^2, m2 = 1-eta^2, m3 = 1-zeta^2, each carrying a full
    displacement vector -> 9 internal DOFs. Note every dm_i/dx vanishes at the
    element centroid, so centroid stress recovery is unaffected by them.
    """
    scale = 2.0 / h
    Gs = []
    for zeta in _GAUSS:
        for eta in _GAUSS:
            for xi in _GAUSS:
                grads = [(-2 * xi * scale, 0.0, 0.0),
                         (0.0, -2 * eta * scale, 0.0),
                         (0.0, 0.0, -2 * zeta * scale)]
                G = np.zeros((6, 9))
                for m, (gx, gy, gz) in enumerate(grads):
                    c = 3 * m
                    G[0, c + 0] = gx
                    G[1, c + 1] = gy
                    G[2, c + 2] = gz
                    G[3, c + 0] = gy
                    G[3, c + 1] = gx
                    G[4, c + 1] = gz
                    G[4, c + 2] = gy
                    G[5, c + 0] = gz
                    G[5, c + 2] = gx
                Gs.append(G)
    return np.array(Gs)


def hex8_ke(h: float, E: float, nu: float, incompatible: bool = True) -> np.ndarray:
    """24x24 element stiffness for a cube of side h.

    With `incompatible` (the default) the Taylor-Beresford-Wilson incompatible
    modes are added and statically condensed out. This matters a lot: a plain
    trilinear hex shear-locks in bending and can report a beam several times
    stiffer than it is, which is exactly the direction of error that lets a weak
    design pass. The Jacobian is constant on a cube, so the element still passes
    the patch test.
    """
    KE, _ = element_operators(h, nu, incompatible)
    return KE * E


def _b_at(h: float, xi: float, eta: float, zeta: float) -> np.ndarray:
    scale = 2.0 / h
    d = _shape_derivs(xi, eta, zeta) * scale
    B = np.zeros((6, 24))
    for n in range(8):
        dx, dy, dz = d[:, n]
        c = 3 * n
        B[0, c + 0] = dx
        B[1, c + 1] = dy
        B[2, c + 2] = dz
        B[3, c + 0] = dy
        B[3, c + 1] = dx
        B[4, c + 1] = dz
        B[4, c + 2] = dy
        B[5, c + 0] = dz
        B[5, c + 2] = dx
    return B


def b_centre(h: float) -> np.ndarray:
    """B matrix at the element centroid."""
    return _b_at(h, 0.0, 0.0, 0.0)


def _g_at(h: float, xi: float, eta: float, zeta: float) -> np.ndarray:
    scale = 2.0 / h
    grads = [(-2 * xi * scale, 0.0, 0.0),
             (0.0, -2 * eta * scale, 0.0),
             (0.0, 0.0, -2 * zeta * scale)]
    G = np.zeros((6, 9))
    for m, (gx, gy, gz) in enumerate(grads):
        c = 3 * m
        G[0, c + 0] = gx
        G[1, c + 1] = gy
        G[2, c + 2] = gz
        G[3, c + 0] = gy
        G[3, c + 1] = gx
        G[4, c + 1] = gz
        G[4, c + 2] = gy
        G[5, c + 0] = gz
        G[5, c + 2] = gx
    return G


def element_operators(h: float, nu: float = None, incompatible: bool = True,
                      D: np.ndarray = None):
    """Element stiffness plus stress-recovery operators, for unit stiffness.

    Returns (KE, S) where KE is 24x24 and S is (nsample, 6, 24) mapping element
    displacements straight to Voigt stress. Samples are the 8 corners *and* the
    centroid: sampling only at the centroid systematically under-reads the peak
    bending stress at a surface (a 100x10x10 cantilever reads ~16 % low at 8
    voxels through the section), and under-reading stress is the error direction
    that lets a weak part pass.

    Pass either `nu` (isotropic, D built for E = 1) or `D` directly (already in
    global axes). In both cases the caller multiplies by a per-element modulus,
    so `D` should be normalised by whatever reference modulus that will be --
    `orthotropic_operators` does this for you. SIMP is untouched by the change:
    it still scales one scalar per element, so the sensitivity chain rule is
    exactly the isotropic one.
    """
    if D is None:
        if nu is None:
            raise ValueError("element_operators needs either `nu` or `D`")
        D = constitutive(1.0, nu)
    D = np.asarray(D, float)
    Bs, detJ = b_matrices(h)
    Kaa = np.zeros((24, 24))
    for B in Bs:
        Kaa += B.T @ D @ B * detJ

    pts = [(a, b, c) for c in (-1.0, 1.0) for b in (-1.0, 1.0) for a in (-1.0, 1.0)]
    pts.append((0.0, 0.0, 0.0))

    if not incompatible:
        S = np.array([D @ _b_at(h, *p) for p in pts])
        return Kaa, S

    Gs = _g_matrices(h)
    Kag = np.zeros((24, 9))
    Kgg = np.zeros((9, 9))
    for B, G in zip(Bs, Gs):
        Kag += B.T @ D @ G * detJ
        Kgg += G.T @ D @ G * detJ
    # alpha = T @ u_e recovers the condensed internal DOFs, which do contribute
    # to stress away from the centroid.
    T = -np.linalg.solve(Kgg, Kag.T)
    KE = Kaa + Kag @ T
    S = np.array([D @ (_b_at(h, *p) + _g_at(h, *p) @ T) for p in pts])
    return KE, S


def orthotropic_D(ortho, build_axis: int, raster_axis: int = None,
                  normalise: bool = True):
    """-> (D_global, E_ref).

    `D_global` is the card's stiffness expressed in global axes and divided by
    `E_ref` when `normalise`, so the caller's per-element modulus multiplies it
    exactly the way `E` multiplies the isotropic KE. `E_ref` is Ex, the stiffest
    material direction, chosen so the SIMP scalar keeps the same meaning it had
    for the isotropic path.
    """
    D_mat = ortho.stiffness()
    R = material_rotation(build_axis, raster_axis)
    D_glob = rotate_stiffness(D_mat, R)
    E_ref = float(ortho.Ex)
    return (D_glob / E_ref if normalise else D_glob), E_ref


def node_ids(shape):
    """Node index array of shape (nx+1, ny+1, nz+1).

    Deliberately plain C-order over (i, j, k): that way an array shaped
    (nx+1, ny+1, nz+1, 3) of nodal quantities ravels straight into DOF order,
    so boundary conditions and results need no index gymnastics.
    """
    nx, ny, nz = shape
    return np.arange((nx + 1) * (ny + 1) * (nz + 1)).reshape(nx + 1, ny + 1, nz + 1)


def element_dofs(shape, elements=None):
    """(nel, 24) global DOF indices for the given elements.

    `elements` is an (nel, 3) integer array of voxel indices; when None every
    voxel in the grid is used.
    """
    nx, ny, nz = shape
    nid = node_ids(shape)
    if elements is None:
        ex, ey, ez = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz),
                                 indexing="ij")
        elements = np.stack([ex.ravel(), ey.ravel(), ez.ravel()], axis=1)
    elements = np.asarray(elements)
    edof = np.empty((elements.shape[0], 24), np.int64)
    for n in range(8):
        a, b, c = n & 1, (n >> 1) & 1, (n >> 2) & 1
        gn = nid[elements[:, 0] + a, elements[:, 1] + b, elements[:, 2] + c]
        edof[:, 3 * n + 0] = 3 * gn + 0
        edof[:, 3 * n + 1] = 3 * gn + 1
        edof[:, 3 * n + 2] = 3 * gn + 2
    return edof


def von_mises(sig: np.ndarray) -> np.ndarray:
    """sig: (n, 6) in Voigt order [xx, yy, zz, xy, yz, zx] -> (n,)."""
    sxx, syy, szz, sxy, syz, szx = (sig[:, i] for i in range(6))
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                   + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))


def layer_failure_index(sig: np.ndarray, sigma_xy: float, sigma_z: float,
                        build_axis: int = 2) -> np.ndarray:
    """Anisotropic failure index for an FDM part. FI >= 1 means predicted failure.

    Three mechanisms compete, and for a printed part the interlayer ones almost
    always win:
      1. tension normal to the layers   -> interlayer tensile strength sigma_z
      2. shear on the layer plane       -> interlayer shear, taken as 0.6*sigma_z
      3. general yielding in-plane      -> sigma_xy via von Mises

    Only *tensile* normal stress counts against mechanism 1: layers press
    together fine in compression, which is why a part can be far stronger one
    way up than the other.
    """
    normal = sig[:, build_axis]
    # Voigt shear slots: 3=xy, 4=yz, 5=zx. The two shears acting on the layer
    # plane are the ones carrying the build-axis index.
    shear_pairs = {0: (3, 5), 1: (3, 4), 2: (4, 5)}[build_axis]
    tau = np.sqrt(sig[:, shear_pairs[0]] ** 2 + sig[:, shear_pairs[1]] ** 2)

    fi_tension = np.maximum(normal, 0.0) / max(sigma_z, 1e-9)
    fi_shear = tau / max(0.6 * sigma_z, 1e-9)
    fi_vm = von_mises(sig) / max(sigma_xy, 1e-9)
    return np.maximum(np.maximum(fi_tension, fi_shear), fi_vm)


def rotate_stress_to_material(sig: np.ndarray, R: np.ndarray) -> np.ndarray:
    """Global Voigt stress (n, 6) -> material-axes Voigt stress (n, 6).

    `R` is what `material_rotation` returns, i.e. it maps material axes to
    global ones, so the stress tensor goes back the other way: s_mat = R^T s R.
    """
    sig = np.atleast_2d(np.asarray(sig, float))
    R = np.asarray(R, float)
    t = np.zeros((sig.shape[0], 3, 3))
    for a, (i, j) in enumerate(_VOIGT):
        t[:, i, j] = sig[:, a]
        t[:, j, i] = sig[:, a]
    t = np.einsum("pi,npq,qj->nij", R, t, R)
    out = np.empty_like(sig)
    for a, (i, j) in enumerate(_VOIGT):
        out[:, a] = t[:, i, j]
    return out


def max_stress_failure_index(sig: np.ndarray, ortho, build_axis: int = 2,
                             raster_axis: int = None,
                             per_mode: bool = False):
    """Max-stress failure index against the nine strengths. FI >= 1 fails.

    `sig` is in *global* axes, the strengths are in *material* axes, so the
    stress is rotated before anything is compared. Skipping that rotation is
    the failure mode this function exists to avoid: it yields safety factors
    that look entirely reasonable and are attributed to the wrong direction.

    Tension and compression are kept apart per axis (PLA: Xt 50 vs Xc 60,
    Zt 22 vs Zc 40). Collapsing them to `abs(sigma)/X` throws away the whole
    reason a printed part is directional -- layers pull apart in tension and
    press together fine in compression.

    With `per_mode` the six mode indices are returned as well, so a report can
    say *which* mechanism is governing rather than only how close it is.
    """
    R = material_rotation(build_axis, raster_axis)
    s = rotate_stress_to_material(sig, R)
    eps = 1e-9

    def norm(v, ft, fc):
        # One-sided: a tensile stress is measured against the tensile
        # allowable only, and vice versa.
        return np.maximum(np.maximum(v, 0.0) / max(ft, eps),
                          np.maximum(-v, 0.0) / max(fc, eps))

    modes = {
        "raster_tension_compression": norm(s[:, 0], ortho.Xt, ortho.Xc),
        "in_plane_transverse": norm(s[:, 1], ortho.Yt, ortho.Yc),
        "interlayer_normal": norm(s[:, 2], ortho.Zt, ortho.Zc),
        "in_plane_shear": np.abs(s[:, 3]) / max(ortho.Sxy, eps),
        "interlayer_shear_yz": np.abs(s[:, 4]) / max(ortho.Syz, eps),
        "interlayer_shear_zx": np.abs(s[:, 5]) / max(ortho.Szx, eps),
    }
    fi = np.max(np.stack(list(modes.values())), axis=0)
    return (fi, modes) if per_mode else fi

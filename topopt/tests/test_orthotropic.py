"""Phase 2: orthotropic stiffness, material axes, and the sensitivity chain rule.

These are the checks spec 22.1 lists under "solver correctness" and
"sensitivity correctness". The theme running through all of them is that an
orthotropic bug does not crash -- it returns a plausible number attributed to
the wrong direction -- so every test here pins a value that only comes out
right if the material axes are handled correctly.
"""

import numpy as np
import pytest

from topopt import hex8
from topopt.materials import (ORTHOTROPIC, Orthotropic, get_material,
                              material_model)
from topopt.solve import solve_linear_elastic
from topopt.voxel import Grid


# A card with three *different* shear moduli. The real PLA and PETG cards have
# Gyz == Gzx, so they cannot tell slot 4 from slot 5 -- this one can.
ASYM = Orthotropic(
    card_id="test_asymmetric", Ex=3000, Ey=2000, Ez=1000,
    Gxy=900, Gyz=600, Gzx=300,
    nu_xy=0.35, nu_yz=0.30, nu_zx=0.25,
    Xt=50, Xc=60, Yt=35, Yc=50, Zt=22, Zc=40, Sxy=25, Syz=15, Szx=10,
    density_kg_m3=1240, classification="estimated",
    measured_for_specific_printer=False, source="synthetic, for tests only")


# --------------------------------------------------------------- the cards

@pytest.mark.parametrize("name", sorted(ORTHOTROPIC))
def test_card_is_physically_consistent(name):
    errors, _ = ORTHOTROPIC[name].check()
    assert not errors, "%s: %s" % (name, "; ".join(errors))


@pytest.mark.parametrize("name", sorted(ORTHOTROPIC) + ["test_asymmetric"])
def test_compliance_is_symmetric_and_stiffness_positive_definite(name):
    o = ASYM if name == "test_asymmetric" else ORTHOTROPIC[name]
    S = o.compliance()
    assert np.allclose(S, S.T, atol=1e-14), "reciprocity violated"
    assert np.linalg.eigvalsh(o.stiffness()).min() > 0


def test_shear_slots_follow_hex8_not_the_textbook():
    """Voigt slots 3/4/5 are xy/yz/zx here, not the textbook yz/zx/xy.

    Using the textbook order is invisible for an isotropic D and swaps Gxy
    with Gyz for an orthotropic one. Only a card with three distinct shear
    moduli catches it, which is why ASYM exists.
    """
    D = ASYM.stiffness()
    assert D[3, 3] == pytest.approx(ASYM.Gxy, rel=1e-12)
    assert D[4, 4] == pytest.approx(ASYM.Gyz, rel=1e-12)
    assert D[5, 5] == pytest.approx(ASYM.Gzx, rel=1e-12)


def test_element_energy_matches_each_shear_modulus():
    """Card -> D -> element. A cube sheared by gamma stores 0.5*G*gamma^2*V."""
    h = 2.0
    D = ASYM.stiffness()
    KE, _ = hex8.element_operators(h, D=D)
    for G, (comp, along) in ((ASYM.Gxy, (0, 1)), (ASYM.Gyz, (1, 2)),
                             (ASYM.Gzx, (2, 0))):
        u = np.zeros(24)
        for n in range(8):
            p = np.array([n & 1, (n >> 1) & 1, (n >> 2) & 1], float) * h
            u[3 * n + comp] = p[along]
        assert 0.5 * u @ KE @ u == pytest.approx(0.5 * G * h ** 3, rel=1e-9)


# ----------------------------------------------------------- the rotation

def test_isotropic_stiffness_is_rotation_invariant():
    D = hex8.constitutive(3000.0, 0.35)
    rng = np.random.default_rng(0)
    Q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(Q) < 0:
        Q[:, 0] *= -1
    assert np.abs(hex8.rotate_stiffness(D, Q) - D).max() < 1e-9


def test_build_z_raster_x_is_the_identity():
    R = hex8.material_rotation(2, 0)
    assert np.allclose(R, np.eye(3))
    sig = np.array([[10., 20., 30., 4., 5., 6.]])
    assert np.allclose(hex8.rotate_stress_to_material(sig, R), sig, atol=1e-12)
    Dn, Eref = hex8.orthotropic_D(ASYM, 2, 0)
    assert np.allclose(Dn * Eref, ASYM.stiffness(), atol=1e-9)


def test_build_x_moves_the_soft_axis_onto_global_x():
    Dn, _ = hex8.orthotropic_D(ASYM, 0, 1, normalise=False)
    # Ez is the softest direction; printed along X it must show up at D[0,0].
    assert Dn[0, 0] < Dn[1, 1] and Dn[0, 0] < Dn[2, 2]
    # and the global xx stress is the one judged against Zt.
    sig = np.array([[10., 20., 30., 4., 5., 6.]])
    s = hex8.rotate_stress_to_material(sig, hex8.material_rotation(0, 1))
    assert s[0, 2] == pytest.approx(10.0)


@pytest.mark.parametrize("build,raster,expect", [
    # (build, raster) -> the global (xy, yz, zx) shear diagonal it must produce.
    # build=X raster=Y sends material X to global Y and material Y to global Z,
    # so the material xy-plane (Gxy) ends up as the global yz plane.
    (0, 1, (ASYM.Gzx, ASYM.Gxy, ASYM.Gyz)),
    (1, 0, (ASYM.Gzx, ASYM.Gyz, ASYM.Gxy)),
    (2, 0, (ASYM.Gxy, ASYM.Gyz, ASYM.Gzx)),
])
def test_rotated_shear_diagonal_lands_on_the_right_plane(build, raster, expect):
    """The one check that catches a mis-ordered Voigt map.

    `_VOIGT` is used only inside the rotation, so an unrotated card looks
    perfect with either ordering and `build_axis: Z` rotates by the identity.
    Only a rotated *shear* term can tell the two apart -- and getting it wrong
    means a part printed on its side is analysed with the wrong shear modulus
    in the plane that carries the load.
    """
    D, _ = hex8.orthotropic_D(ASYM, build, raster, normalise=False)
    got = (D[3, 3], D[4, 4], D[5, 5])
    assert got == pytest.approx(expect, rel=1e-9)


@pytest.mark.parametrize("build,raster", [(0, 1), (1, 0), (1, 2), (2, 0), (2, 1)])
def test_rotated_card_still_stores_the_right_shear_energy(build, raster):
    """The same statement made physically: shear a cube of the rotated
    material and the energy must equal the modulus the rotation put there."""
    h = 2.0
    D, _ = hex8.orthotropic_D(ASYM, build, raster, normalise=False)
    KE, _ = hex8.element_operators(h, D=D)
    for slot, (comp, along) in ((3, (0, 1)), (4, (1, 2)), (5, (2, 0))):
        u = np.zeros(24)
        for n in range(8):
            p = np.array([n & 1, (n >> 1) & 1, (n >> 2) & 1], float) * h
            u[3 * n + comp] = p[along]
        assert 0.5 * u @ KE @ u == pytest.approx(0.5 * D[slot, slot] * h ** 3,
                                                 rel=1e-9)
    # A 90-degree axis swap can only permute the three shear moduli. If the
    # rotated diagonal holds some value the card never had, the tensor
    # round-trip is mixing terms rather than relabelling them.
    assert sorted([D[3, 3], D[4, 4], D[5, 5]]) == pytest.approx(
        sorted([ASYM.Gxy, ASYM.Gyz, ASYM.Gzx]), rel=1e-9)


@pytest.mark.parametrize("build", [0, 1, 2])
def test_softest_normal_direction_is_always_the_build_axis(build):
    D, _ = hex8.orthotropic_D(ASYM, build, normalise=False)
    diag = [D[0, 0], D[1, 1], D[2, 2]]
    assert int(np.argmin(diag)) == build


def test_rotated_stiffness_stays_symmetric_and_positive_definite():
    for b in (0, 1, 2):
        D, _ = hex8.orthotropic_D(ASYM, b, normalise=False)
        assert np.abs(D - D.T).max() < 1e-9
        assert np.linalg.eigvalsh(D).min() > 0


def test_raster_axis_equal_to_build_axis_is_rejected():
    with pytest.raises(ValueError):
        hex8.material_rotation(2, 2)


# ------------------------------------------------------- failure criterion

def test_max_stress_separates_tension_from_compression():
    o = ORTHOTROPIC["PLA"]
    ten = np.array([[0., 0., o.Zt, 0., 0., 0.]])
    com = np.array([[0., 0., -o.Zc, 0., 0., 0.]])
    assert hex8.max_stress_failure_index(ten, o, 2)[0] == pytest.approx(1.0)
    assert hex8.max_stress_failure_index(com, o, 2)[0] == pytest.approx(1.0)
    # The same magnitude is far safer in compression: Zc 40 vs Zt 22.
    same = np.array([[0., 0., -o.Zt, 0., 0., 0.]])
    assert hex8.max_stress_failure_index(same, o, 2)[0] == pytest.approx(
        o.Zt / o.Zc)


def test_build_direction_changes_the_safety_factor_of_the_same_stress():
    """The whole point of Phase 2: same part, same load, different build."""
    o = ORTHOTROPIC["PLA"]
    pull_x = np.array([[30., 0., 0., 0., 0., 0.]])
    fi = {b: hex8.max_stress_failure_index(pull_x, o, b)[0] for b in (0, 1, 2)}
    # Printed with the layers normal to the pull, PLA is judged against Zt=22.
    assert fi[0] == pytest.approx(30.0 / o.Zt)
    # Printed either other way the pull runs along the raster, against Xt=50.
    assert fi[1] == pytest.approx(30.0 / o.Xt)
    assert fi[2] == pytest.approx(30.0 / o.Xt)
    assert fi[0] > 2 * fi[2]


def test_governing_mode_is_reported():
    o = ORTHOTROPIC["PLA"]
    sig = np.array([[0., 0., 20., 0., 0., 0.]])
    _, modes = hex8.max_stress_failure_index(sig, o, 2, per_mode=True)
    assert max(modes, key=lambda k: modes[k][0]) == "interlayer_normal"


# --------------------------------------------------- solver-level behaviour

def _bar(nx=10, ny=3, nz=3, h=1.0):
    return Grid(solid=np.ones((nx, ny, nz), bool), h=h, origin=np.zeros(3))


def _cantilever_bcs(grid):
    nx, ny, nz = grid.solid.shape
    fixed = np.zeros((nx + 1, ny + 1, nz + 1, 3), bool)
    fixed[0, :, :, :] = True
    force = np.zeros((nx + 1, ny + 1, nz + 1, 3))
    force[nx, :, :, 2] = -10.0 / ((ny + 1) * (nz + 1))
    return fixed, force


def test_orthotropic_cantilever_is_stiffest_printed_flat():
    """Bending about Y draws on the modulus along X, so the tip deflection
    must track Ex > Ey > Ez as the build axis is moved onto X."""
    grid = _bar()
    fixed, force = _cantilever_bcs(grid)
    disp = {}
    for b in (0, 1, 2):
        r = solve_linear_elastic(grid, 1.0, 0.35, fixed, force,
                                 ortho=ASYM, build_axis=b, want_stress=False)
        disp[b] = r.max_disp
    # build=X puts the soft Ez along the beam -> softest of the three.
    assert disp[0] > disp[1] and disp[0] > disp[2]
    # and it is soft by roughly Ex/Ez, not by a rounding error.
    assert disp[0] / disp[2] > 1.5


def test_isotropic_card_reproduces_the_isotropic_solver():
    """An orthotropic card whose constants are isotropic must give the same
    answer as the closed-form isotropic path -- otherwise the two code paths
    have drifted and nothing downstream can be compared."""
    E, nu = 2500.0, 0.35
    G = E / (2 * (1 + nu))
    iso = Orthotropic(
        card_id="iso", Ex=E, Ey=E, Ez=E, Gxy=G, Gyz=G, Gzx=G,
        nu_xy=nu, nu_yz=nu, nu_zx=nu,
        Xt=50, Xc=50, Yt=50, Yc=50, Zt=50, Zc=50, Sxy=30, Syz=30, Szx=30,
        density_kg_m3=1240, classification="estimated",
        measured_for_specific_printer=False, source="synthetic")
    grid = _bar()
    fixed, force = _cantilever_bcs(grid)
    a = solve_linear_elastic(grid, E, nu, fixed, force, want_stress=False)
    b = solve_linear_elastic(grid, 1.0, nu, fixed, force, ortho=iso,
                             build_axis=2, want_stress=False)
    assert b.max_disp == pytest.approx(a.max_disp, rel=1e-6)
    assert b.compliance == pytest.approx(a.compliance, rel=1e-6)


def test_result_records_which_material_model_produced_it():
    grid = _bar()
    fixed, force = _cantilever_bcs(grid)
    o = solve_linear_elastic(grid, 1.0, 0.35, fixed, force, ortho=ASYM,
                             build_axis=1, raster_axis=0)
    assert o.failure_criterion == "max_stress_material_axes"
    assert o.material_axes["stiffness"] == "orthotropic"
    assert o.material_axes["build_axis"] == "Y"
    assert o.material_axes["raster_axis"] == "X"

    i = solve_linear_elastic(grid, 2500.0, 0.35, fixed, force,
                             sigma_xy=50, sigma_z=26, build_axis=2)
    assert i.failure_criterion == "layer_two_constant"
    assert i.material_axes["stiffness"] == "isotropic"


# --------------------------------------------------------- material_model

def test_material_model_records_the_choice():
    _, ortho, info = material_model("PLA")
    assert ortho is not None
    assert info["material_model"] == "orthotropic_equivalent_solid"
    assert info["measured_for_specific_printer"] is False

    _, none, iso = material_model("PLA", isotropic=True)
    assert none is None
    assert iso["material_model"] == "isotropic_two_constant"


def test_material_without_a_card_refuses_rather_than_falling_back():
    """Silently reverting to the isotropic model would report numbers that
    look orthotropic. The run has to stop instead."""
    mat = get_material("ABS")
    assert mat.ortho is None
    with pytest.raises(KeyError, match="orthotropic card"):
        mat.require_ortho()
    with pytest.raises(KeyError):
        material_model("ABS")


def test_legacy_and_card_disagree_on_the_layer_normal():
    """Pinned on purpose. The legacy E_z (2400) is really the card's in-plane
    transverse Ey, so the old model was optimistic about the direction that
    breaks printed parts. If these ever agree, someone has quietly merged the
    two models and every recorded result moved with it.
    """
    mat = get_material("PLA")
    card = mat.ortho
    assert mat.E_z == pytest.approx(2400)
    assert card.Ez == pytest.approx(1500)
    assert card.Ey == pytest.approx(mat.E_z)
    assert card.Ez < mat.E_z

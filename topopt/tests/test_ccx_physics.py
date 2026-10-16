"""Are the CalculiX numbers *right*, not merely present (spec 19.4).

`test_ccx_mesh.py::test_box_runs_static_buckle_and_frequency_for_real` proves
CalculiX accepts these decks and the parsers read what it wrote. It does not
prove the answers mean anything: a density written in the wrong units scales
every buckling factor and every natural frequency by 10^9 and leaves the
statics perfect, and a `*BUCKLE` deck whose clamp is not where it looks
returns a factor for a different column. Spec 19.4 called the physical
validity unconfirmed for exactly that reason.

So one slender cantilever, three closed-form answers, and the same mesh for
all three:

    a 60 x 6 x 6 mm PLA bar, clamped on the x = 0 face, at ~2 mm C3D10

    tip deflection  d = F L^3 / (3 E I)          (Euler-Bernoulli)
    buckling        P_cr = pi^2 E I / (4 L^2)    (fixed-free, K = 2)
    first mode      f1 = (1.875^2 / 2 pi) sqrt(E I / (rho A L^4))

with I = 108 mm^4, A = 36 mm^2, E = 3000 MPa and rho = 1.24 g/cm^3, all read
off `materials.get_material("PLA")` rather than typed in here.

**Measured (CalculiX 2.23, Gmsh 4.15.2, 2 mm C3D10):** the numbers are
asserted to 10 % and printed by the test, so a drift shows its size. The
tolerance is not a knob -- a solid model of an L/h = 10 bar is genuinely a few
per cent softer than Euler-Bernoulli (the clamp deforms and the section
shears), and if any of these ever moves outside 10 % the answer is a finer
mesh or a longer bar, not a wider band.

The sequence mirrors `cli/ccx.py::analyse`: refine the shell first, because
Gmsh keeps an STL's own triangles as the surface mesh and a 12-triangle box
would be meshed as a 12-triangle box (lessons-learned 73).
"""

import math
import os
import struct

import numpy as np
import pytest

from topopt import ccx
from topopt.external import find
from topopt.materials import get_material

pytestmark = pytest.mark.slow

# The bar. Slender enough that Euler-Bernoulli is the right model rather than
# a rough guide, short enough that a 2 mm mesh of it is a test and not a job.
LX, LY, LZ = 60.0, 6.0, 6.0
MESH_MM = 2.0
TIP_FORCE_N = 10.0
TOL = 0.10


def _tools():
    for name in ("gmsh", "calculix"):
        try:
            find(name)
        except Exception:                                         # noqa: BLE001
            pytest.skip("%s is not installed on this machine" % name)


def _write_box_stl(path, lx, ly, lz):
    """A closed binary STL of a box, with a *text* 80-byte header: Gmsh
    4.15.2 refuses one that is all NUL (see `test_ccx_mesh._write_box_stl`)."""
    v = np.array([[0, 0, 0], [lx, 0, 0], [lx, ly, 0], [0, ly, 0],
                  [0, 0, lz], [lx, 0, lz], [lx, ly, lz], [0, ly, lz]], float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append(v[[a, b, c]])
        tris.append(v[[a, c, d]])
    with open(path, "wb") as f:
        f.write(b"cantilever written by test_ccx_physics".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            n = n / np.linalg.norm(n)
            f.write(struct.pack("<12fH", *n, *t.ravel(), 0))
    return str(path)


class Beam(object):
    """Mesh, material block and node sets, built once for all three checks."""

    def __init__(self, tmp):
        mat = get_material("PLA")
        self.E = float(mat.E_effective(across_layers=False))
        self.nu = float(mat.nu)
        self.rho_kg_m3 = float(mat.density) * 1000.0
        self.I = LY * LZ ** 3 / 12.0
        self.A = LY * LZ

        raw = _write_box_stl(os.path.join(tmp, "bar.stl"), LX, LY, LZ)
        shell = os.path.join(tmp, "bar_shell.stl")
        self.refinement = ccx.refine_stl(raw, shell, MESH_MM)
        geo = os.path.join(tmp, "bar.geo")
        self.mesh_path = os.path.join(tmp, "bar_mesh.inp")
        ccx.write_geo(geo, shell, MESH_MM)
        ccx.run_gmsh(geo, self.mesh_path)

        m = ccx.read_mesh(self.mesh_path)
        self.mesh = m
        surf = set(int(n) for n in ccx.surface_nodes(m["elements"]))
        x = m["xyz"][:, 0]
        self.root = [int(n) for n in m["ids"]
                     if int(n) in surf and x[int(n) - 1] < 1e-6]
        self.tip = [int(n) for n in m["ids"]
                    if int(n) in surf and x[int(n) - 1] > LX - 1e-6]
        assert self.root and self.tip
        self.block = ccx.material_block("PLA", E=self.E, nu=self.nu,
                                        density_kg_m3=self.rho_kg_m3)
        self.tmp = tmp

    def loads(self, total):
        total = np.asarray(total, float)
        return [(n, tuple(total / len(self.tip))) for n in self.tip]

    def solve(self, tag, total, analyses):
        case = {"name": tag, "fixed_nodes": self.root,
                "loads": self.loads(total)}
        inp = os.path.join(self.tmp, "%s.inp" % tag)
        ccx.write_inp(inp, self.mesh_path, self.block, [case],
                      analyses=analyses)
        out, base = ccx.run_ccx(inp)
        ok, why = ccx.converged(out)
        assert ok, why
        return base


@pytest.fixture(scope="module")
def beam(tmp_path_factory):
    _tools()
    return Beam(str(tmp_path_factory.mktemp("cantilever")))


def test_the_shell_was_refined_before_meshing(beam):
    """Not a physics check -- the precondition for all three. Gmsh keeps the
    STL's triangles as the surface mesh, so an unrefined 12-triangle box gives
    a bar whose surface is 12 triangles at any `--mesh-mm`."""
    assert beam.refinement["triangles_before"] == 12
    assert beam.refinement["triangles_after"] > 500
    assert beam.mesh["types"] == ["C3D10"]
    assert len(beam.mesh["elements"]) > 1000


def test_tip_deflection_matches_the_cantilever_formula(beam, capsys):
    """d = F L^3 / (3 E I). The static answer has to be right before a
    buckling factor or a frequency from the same mesh means anything."""
    base = beam.solve("static", [0.0, 0.0, -TIP_FORCE_N], ("static",))
    st = ccx.parse_frd(base + ".frd")["steps"][0]
    disp = np.array(list(st["displacement"].values()))
    got = float(np.abs(disp[:, 2]).max())
    want = TIP_FORCE_N * LX ** 3 / (3.0 * beam.E * beam.I)
    with capsys.disabled():
        print("\n  tip deflection: FEM %.4f mm vs F L^3/3EI %.4f mm (%+.1f %%)"
              % (got, want, 100 * (got / want - 1.0)))
    assert got == pytest.approx(want, rel=TOL)


def test_the_buckling_factor_matches_euler(beam, capsys):
    """P_cr = pi^2 E I / (4 L^2): fixed at one end, free at the other, so the
    effective length is 2 L. CalculiX reports a *factor* on the load that was
    applied, so the critical load is factor x the 10 N pushed here."""
    base = beam.solve("buckle", [-TIP_FORCE_N, 0.0, 0.0], ("buckle",))
    factors = [f for f in ccx.parse_dat(base + ".dat")["buckling_factors"]
               if f > 0]
    assert factors, "no positive buckling factor came back"
    got = min(factors) * TIP_FORCE_N
    want = math.pi ** 2 * beam.E * beam.I / (4.0 * LX ** 2)
    with capsys.disabled():
        print("  buckling:       FEM %.1f N vs Euler %.1f N (%+.1f %%), "
              "factor %.2f" % (got, want, 100 * (got / want - 1.0),
                               min(factors)))
    assert got == pytest.approx(want, rel=TOL)


def test_the_first_natural_frequency_matches_the_closed_form(beam, capsys):
    """f1 = (1.875^2 / 2 pi) sqrt(E I / (rho A L^4)).

    The unit chain is the point of this one. `material_block` writes the
    density in tonne/mm^3 because the deck is mm/N/MPa; the closed form here
    is evaluated in the same units, so a factor of 10^9 anywhere -- the error
    that leaves every static result perfect -- lands two orders of magnitude
    outside the 10 % band and cannot be missed.
    """
    base = beam.solve("freq", [0.0, 0.0, -TIP_FORCE_N], ("frequency",))
    freqs = [f for f in ccx.parse_dat(base + ".dat")["frequencies_hz"] if f > 0]
    assert freqs, "no frequencies came back"
    got = min(freqs)
    rho = beam.rho_kg_m3 * 1e-12                      # kg/m^3 -> tonne/mm^3
    want = (1.875 ** 2 / (2 * math.pi)) * math.sqrt(
        beam.E * beam.I / (rho * beam.A * LX ** 4))
    with capsys.disabled():
        print("  first mode:     FEM %.1f Hz vs closed form %.1f Hz (%+.1f %%)"
              % (got, want, 100 * (got / want - 1.0)))
    assert got == pytest.approx(want, rel=TOL)


def test_a_square_section_has_a_pair_of_first_bending_modes(beam):
    """The counter-test for the frequency check: a 6 x 6 section bends the
    same way about y and about z, so the two lowest modes must be a pair. One
    lonely low mode would mean the clamp is not doing what it looks like."""
    base = os.path.join(beam.tmp, "freq")
    freqs = sorted(f for f in ccx.parse_dat(base + ".dat")["frequencies_hz"]
                   if f > 0)
    assert len(freqs) >= 2
    assert freqs[1] == pytest.approx(freqs[0], rel=0.02)

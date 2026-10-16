"""FDM material properties, with the anisotropy that actually breaks parts.

**This module is the only place material numbers live.** Anything that needs a
modulus or a strength asks here; nothing writes its own.

Two models sit side by side, and the difference between them matters:

* The **legacy two-constant model** (`E_xy`/`E_z`, `sigma_xy`/`sigma_z`) --
  in-plane versus across-layer. This is what every recorded osmo_tripod result
  was solved with, so its numbers are frozen: changing them would silently
  invalidate the regression baselines rather than produce a new answer.
* The **orthotropic card** (`Orthotropic`, reached via `Material.ortho`) --
  nine elastic constants and nine strengths in material axes, from
  `docs/FFF_PLA_PETG_orthotropic_material_reference.md`.

They disagree, on purpose and visibly. For PLA the legacy model says the
across-layer modulus is 2400 MPa; the card says Ez = 1500. The legacy value was
really closer to the card's *in-plane transverse* Ey, so the old model was
optimistic about the layer normal -- exactly the direction that breaks parts.
Orthotropic runs are therefore expected to be more conservative than the
recorded isotropic ones. That is the point of the exercise, not a regression.

Sources: the orthotropic cards are conservative literature-based defaults, not
measurements of any specific printer. Every field carries its own provenance
and `Orthotropic.check()` refuses a card that does not. For anything
safety-critical, print a coupon and test it -- and see the reference document's
section 11: a provisional card is normally used with a *higher* required safety
factor than a measured one.
"""

from dataclasses import dataclass, field

import numpy as np

# How much a number can be trusted, worst last. Kept as data because it has to
# survive into the run summary and the report -- a value whose provenance is
# lost is a value that will eventually be quoted as if it were measured.
PROVENANCE = {
    "measured": "measured on this printer, this filament, these settings",
    "literature_anchor": "reported directly in a cited study",
    "conservative_default": "set from the trend across several studies, "
                            "chosen not to overestimate",
    "estimated": "engineering estimate; published data was insufficient",
    "typical_bulk_value": "bulk polymer value, not a printed-part measurement",
}


@dataclass(frozen=True)
class Orthotropic:
    """Nine elastic constants and nine strengths, in *material* axes.

    Material axes, per the reference document:

        X = primary raster direction (filament runs along it)
        Y = in-plane, transverse to the raster
        Z = build direction, normal to the layer interfaces

    The three Poisson ratios given are the major ones; the minors follow from
    reciprocity (`nu_ij / Ei = nu_ji / Ej`) and are never stored, so the
    compliance matrix is symmetric by construction rather than by luck.
    """
    card_id: str
    Ex: float
    Ey: float
    Ez: float
    Gxy: float
    Gyz: float
    Gzx: float
    nu_xy: float
    nu_yz: float
    nu_zx: float
    Xt: float
    Xc: float
    Yt: float
    Yc: float
    Zt: float
    Zc: float
    Sxy: float
    Syz: float
    Szx: float
    density_kg_m3: float
    classification: str
    measured_for_specific_printer: bool
    source: str
    conditions: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    # ---------------------------------------------------------- elasticity

    def minor_poissons(self):
        """(nu_yx, nu_zy, nu_xz) from reciprocity."""
        return (self.nu_xy * self.Ey / self.Ex,
                self.nu_yz * self.Ez / self.Ey,
                self.nu_zx * self.Ex / self.Ez)

    def compliance(self) -> np.ndarray:
        """6x6 compliance in Voigt order (xx, yy, zz, **xy, yz, zx**).

        That shear order is `hex8`'s, not the textbook (yz, zx, xy). It is
        chosen to match `hex8.b_matrices`, so this matrix drops straight into
        the element. Getting it wrong swaps Gxy with Gyz, which an isotropic
        check cannot see.
        """
        nyx, nzy, nxz = self.minor_poissons()
        S = np.zeros((6, 6))
        S[0, 0], S[1, 1], S[2, 2] = 1 / self.Ex, 1 / self.Ey, 1 / self.Ez
        S[0, 1] = S[1, 0] = -self.nu_xy / self.Ex
        S[1, 2] = S[2, 1] = -self.nu_yz / self.Ey
        S[0, 2] = S[2, 0] = -self.nu_zx / self.Ez
        S[3, 3], S[4, 4], S[5, 5] = 1 / self.Gxy, 1 / self.Gyz, 1 / self.Gzx
        return S

    def stiffness(self) -> np.ndarray:
        """6x6 D in material axes. Voigt with engineering shear, matching
        `hex8.constitutive`, so the two are interchangeable in the element."""
        return np.linalg.inv(self.compliance())

    def scaled(self, modulus_factor=1.0, strength_factor=1.0, card_id=None):
        """A derated copy -- equivalent-solid infill knock-down, or a wet
        scenario. Provenance is carried through and the id records the factor,
        so a derated card can never be mistaken for the card it came from."""
        def s(v, f):
            return v * f
        return Orthotropic(
            card_id=card_id or ("%s_x%.2fE_x%.2fS"
                                % (self.card_id, modulus_factor, strength_factor)),
            Ex=s(self.Ex, modulus_factor), Ey=s(self.Ey, modulus_factor),
            Ez=s(self.Ez, modulus_factor), Gxy=s(self.Gxy, modulus_factor),
            Gyz=s(self.Gyz, modulus_factor), Gzx=s(self.Gzx, modulus_factor),
            nu_xy=self.nu_xy, nu_yz=self.nu_yz, nu_zx=self.nu_zx,
            Xt=s(self.Xt, strength_factor), Xc=s(self.Xc, strength_factor),
            Yt=s(self.Yt, strength_factor), Yc=s(self.Yc, strength_factor),
            Zt=s(self.Zt, strength_factor), Zc=s(self.Zc, strength_factor),
            Sxy=s(self.Sxy, strength_factor), Syz=s(self.Syz, strength_factor),
            Szx=s(self.Szx, strength_factor),
            density_kg_m3=self.density_kg_m3,
            classification=self.classification,
            measured_for_specific_printer=self.measured_for_specific_printer,
            source=self.source, conditions=dict(self.conditions),
            provenance=dict(self.provenance))

    # ---------------------------------------------------------- validation

    def check(self):
        """-> (errors, warnings). Section 12 of the reference document.

        A card that fails these is not usable: an indefinite stiffness matrix
        does not merely give a wrong answer, it gives a *plausible* one.
        """
        errors, warnings = [], []
        for n in ("Ex", "Ey", "Ez", "Gxy", "Gyz", "Gzx"):
            if getattr(self, n) is None or getattr(self, n) <= 0:
                errors.append("%s must be > 0, got %r" % (n, getattr(self, n)))
        for n in ("Xt", "Xc", "Yt", "Yc", "Zt", "Zc", "Sxy", "Syz", "Szx"):
            v = getattr(self, n)
            if v is None:
                errors.append("%s is null; a safety factor cannot be computed "
                              "from it" % n)
            elif v <= 0:
                errors.append("%s must be > 0, got %r" % (n, v))
        for n in ("nu_xy", "nu_yz", "nu_zx"):
            v = getattr(self, n)
            if v is None or not (-1.0 < v < 0.5 + 1e-12):
                errors.append("%s outside the physical range (-1, 0.5], got %r"
                              % (n, v))
        if errors:
            return errors, warnings          # the matrix below would be junk

        S = self.compliance()
        if not np.allclose(S, S.T, atol=1e-12):
            errors.append("compliance matrix is not symmetric -- reciprocity "
                          "nu_ij/Ei = nu_ji/Ej does not hold")
        w = np.linalg.eigvalsh((S + S.T) / 2)
        if w.min() <= 0:
            errors.append("stiffness matrix is not positive definite "
                          "(smallest compliance eigenvalue %.3e); the constants "
                          "are not physically consistent" % w.min())
        if not self.classification:
            errors.append("no provenance classification; a card whose "
                          "trustworthiness is unrecorded will eventually be "
                          "quoted as if it were measured")
        missing = [n for n in _ORTHO_FIELDS if n not in self.provenance]
        if missing:
            warnings.append("no per-value provenance for: %s"
                            % ", ".join(missing))
        if not self.measured_for_specific_printer:
            warnings.append(
                "measured_for_specific_printer = false: this is a %s, not a "
                "measurement of your printer and filament. The reference "
                "document asks for a higher safety factor when using one "
                "(section 11) and lists the measurement order to replace it "
                "(section 13)." % self.classification)
        return errors, warnings

    def provenance_summary(self):
        """{classification: [field, ...]} -- what the report has to print."""
        out = {}
        for name in _ORTHO_FIELDS:
            out.setdefault(self.provenance.get(name, "unrecorded"), []).append(name)
        return out


_ORTHO_FIELDS = ("Ex", "Ey", "Ez", "Gxy", "Gyz", "Gzx", "nu_xy", "nu_yz",
                 "nu_zx", "Xt", "Xc", "Yt", "Yc", "Zt", "Zc", "Sxy", "Syz",
                 "Szx", "density_kg_m3")


@dataclass(frozen=True)
class Material:
    name: str
    E_xy: float        # MPa, in-plane Young's modulus
    E_z: float         # MPa, across-layer Young's modulus
    nu: float          # Poisson's ratio
    sigma_xy: float    # MPa, in-plane tensile strength
    sigma_z: float     # MPa, across-layer (interlayer) tensile strength
    density: float     # g/cm^3 (solid); multiply by fill for a real part
    max_service_c: float  # deg C, practical continuous service temperature
    notes: str = ""

    @property
    def anisotropy(self) -> float:
        """sigma_z / sigma_xy -- how much strength is lost across layers."""
        return self.sigma_z / self.sigma_xy

    def allowable(self, safety_factor: float, across_layers: bool = True) -> float:
        """Allowable stress. Defaults to the across-layer value on purpose:
        unless you know the principal stress direction lines up with the
        extrusion, the honest assumption is that it does not."""
        base = self.sigma_z if across_layers else self.sigma_xy
        return base / safety_factor

    def E_effective(self, across_layers: bool = True) -> float:
        return self.E_z if across_layers else self.E_xy

    @property
    def ortho(self):
        """The orthotropic card for this material, or None if there is none.

        Deliberately not merged into the legacy fields. The two models give
        different answers -- for PLA the card's Ez is 1500 MPa against the
        legacy 2400 -- and every recorded result was solved with the legacy
        numbers. Folding the card in would move those results without anyone
        asking for it; keeping them apart makes the isotropic path reproduce
        exactly and the orthotropic path a deliberate choice.
        """
        return ORTHOTROPIC.get(self.name.upper())

    def require_ortho(self, what="orthotropic analysis"):
        """The card, or a message saying which materials have one.

        Falling back to the isotropic model here would be the worst option:
        the run would carry on and report numbers that look orthotropic.
        """
        card = self.ortho
        if card is None:
            raise KeyError(
                "%s needs an orthotropic card for %r and there is none. "
                "Cards exist for: %s. Either pick one of those materials, or "
                "run with --isotropic (which reports the two-constant model "
                "and says so)." % (what, self.name, ", ".join(sorted(ORTHOTROPIC))))
        return card


def material_model(name, isotropic=False, infill=None, walls=None,
                   nozzle=0.4):
    """-> (Material, Orthotropic | None, dict describing the choice).

    One place decides which of the two material models a run uses, so `opt`,
    `fea` and the reports can never disagree about it. The returned dict is
    what goes into the summary: a result that does not record which model
    produced it cannot be compared with anything later.

    With `infill` / `walls` the card is derated by `effective_density_factor`
    -- the same knock-down the legacy path applies -- and the id records the
    factor so a derated card cannot be mistaken for the measured one.
    """
    mat = get_material(name)
    if isotropic:
        return mat, None, {
            "material_model": "isotropic_two_constant",
            "E_xy_MPa": mat.E_xy, "E_z_MPa": mat.E_z, "nu": mat.nu,
            "sigma_xy_MPa": mat.sigma_xy, "sigma_z_MPa": mat.sigma_z,
            "note": "legacy model; stiffness is isotropic and build_axis "
                    "affects only the failure index",
        }
    card = mat.require_ortho()
    factor = 1.0
    if infill is not None or walls is not None:
        factor = effective_density_factor(walls if walls is not None else 4,
                                          infill if infill is not None else 0.25,
                                          nozzle)
        # Stiffness scales roughly with solid fraction; strength is taken to
        # scale the same way, which is optimistic for sparse infill and is why
        # `required_safety_factor` is higher for a provisional card.
        card = card.scaled(modulus_factor=factor, strength_factor=factor)
    return mat, card, {
        "material_model": "orthotropic_equivalent_solid",
        "card_id": card.card_id,
        "classification": card.classification,
        "measured_for_specific_printer": card.measured_for_specific_printer,
        "solid_fraction_applied": round(factor, 4),
        "Ex_Ey_Ez_MPa": [round(card.Ex, 1), round(card.Ey, 1), round(card.Ez, 1)],
        "Gxy_Gyz_Gzx_MPa": [round(card.Gxy, 1), round(card.Gyz, 1),
                            round(card.Gzx, 1)],
        "strengths_MPa": {"Xt": round(card.Xt, 2), "Xc": round(card.Xc, 2),
                          "Yt": round(card.Yt, 2), "Yc": round(card.Yc, 2),
                          "Zt": round(card.Zt, 2), "Zc": round(card.Zc, 2),
                          "Sxy": round(card.Sxy, 2), "Syz": round(card.Syz, 2),
                          "Szx": round(card.Szx, 2)},
        "source": card.source,
    }


_REF = "docs/FFF_PLA_PETG_orthotropic_material_reference.md"

_FFF_CONDITIONS = {
    "moisture": "dry",
    "temperature_C": 23,
    "immersion_hours": 0,
    "nozzle_mm": "0.4-0.6",
    "layer_height_mm": "0.15-0.28",
    "model": "equivalent solid, high density, generous perimeters",
}

# Provenance per field, transcribed with the values. Kept beside them rather
# than derived, because the point of the classification is that it is a
# property of where each number came from, not of the card as a whole.
_PLA_PROV = {
    "Ex": "conservative_default", "Ey": "conservative_default",
    "Ez": "conservative_default",
    "Gxy": "estimated", "Gyz": "estimated", "Gzx": "estimated",
    "nu_xy": "estimated", "nu_yz": "estimated", "nu_zx": "estimated",
    "Xt": "conservative_default", "Yt": "conservative_default",
    "Zt": "conservative_default",
    "Xc": "estimated", "Yc": "estimated", "Zc": "estimated",
    "Sxy": "estimated", "Syz": "estimated", "Szx": "estimated",
    "density_kg_m3": "typical_bulk_value",
}
_PETG_PROV = dict(_PLA_PROV)

ORTHOTROPIC = {
    "PLA": Orthotropic(
        card_id="pla_generic_fff_orthotropic_v1",
        Ex=3000, Ey=2400, Ez=1500, Gxy=1000, Gyz=550, Gzx=550,
        nu_xy=0.35, nu_yz=0.30, nu_zx=0.30,
        Xt=50, Xc=60, Yt=35, Yc=50, Zt=22, Zc=40,
        Sxy=25, Syz=15, Szx=15,
        density_kg_m3=1240,
        classification="conservative_literature_based_default",
        measured_for_specific_printer=False,
        source=_REF + " section 5",
        conditions=dict(_FFF_CONDITIONS, polymer="unfilled PLA"),
        provenance=_PLA_PROV),
    "PETG": Orthotropic(
        card_id="petg_generic_fff_orthotropic_v1",
        Ex=2100, Ey=1750, Ez=1150, Gxy=700, Gyz=420, Gzx=420,
        nu_xy=0.38, nu_yz=0.34, nu_zx=0.34,
        Xt=42, Xc=50, Yt=33, Yc=42, Zt=25, Zc=35,
        Sxy=20, Syz=14, Szx=14,
        density_kg_m3=1270,
        classification="conservative_literature_based_default",
        measured_for_specific_printer=False,
        source=_REF + " section 6",
        conditions=dict(_FFF_CONDITIONS, polymer="unfilled PETG"),
        provenance=_PETG_PROV),
}

# Equivalent-solid knock-down for a real print (reference document section 8.1).
# Screening only: a wall-dominated thin part is under-read by it and a chunky
# core-dependent part over-read.
EQUIVALENT_SOLID = {
    "modulus_factor": 0.55,
    "tensile_strength_factor": 0.60,
    "compressive_strength_factor": 0.65,
    "shear_strength_factor": 0.55,
}

# Minimum safety factors the reference document asks for (section 11). A
# provisional card carries more uncertainty than a measured one, so it is held
# to a higher bar -- which is the opposite of the intuition that an unmeasured
# material should be judged loosely.
MIN_SAFETY_FACTOR = {
    "topology_search": 2.0,
    "provisional_card_normal": 4.0,
    "provisional_card_underwater_normal": 5.0,
    "measured_card_normal": 3.0,
    "abnormal": 1.5,
}


def required_safety_factor(material, underwater=False, phase="normal"):
    """What the reference document asks for, given how the card was obtained."""
    if phase == "topology_search":
        return MIN_SAFETY_FACTOR["topology_search"]
    if phase == "abnormal":
        return MIN_SAFETY_FACTOR["abnormal"]
    ortho = getattr(material, "ortho", None)
    measured = bool(ortho and ortho.measured_for_specific_printer)
    if measured:
        return MIN_SAFETY_FACTOR["measured_card_normal"]
    return MIN_SAFETY_FACTOR["provisional_card_underwater_normal" if underwater
                             else "provisional_card_normal"]


MATERIALS = {
    "PLA": Material(
        name="PLA", E_xy=3000, E_z=2400, nu=0.36,
        sigma_xy=46, sigma_z=26, density=1.24, max_service_c=50,
        notes="Stiffest common filament, but creeps under sustained load and "
              "softens in a hot car / direct sun. Fine for jigs and dry indoor "
              "parts; wrong for anything structural left loaded for days.",
    ),
    "PETG": Material(
        name="PETG", E_xy=1800, E_z=1400, nu=0.40,
        sigma_xy=38, sigma_z=24, density=1.27, max_service_c=70,
        notes="Tough, good layer adhesion, low warp, water resistant. The "
              "default for AquaBeacon wet-side parts. Softer than PLA, so "
              "deflection governs more often than strength.",
    ),
    "ABS": Material(
        name="ABS", E_xy=2000, E_z=1200, nu=0.35,
        sigma_xy=33, sigma_z=17, density=1.04, max_service_c=85,
        notes="Warps on large flat parts; needs an enclosure. Poor layer "
              "adhesion relative to PETG -- avoid loading across layers.",
    ),
    "ASA": Material(
        name="ASA", E_xy=2100, E_z=1300, nu=0.35,
        sigma_xy=35, sigma_z=18, density=1.07, max_service_c=90,
        notes="ABS with UV resistance. Right choice for parts that live "
              "outdoors in sunlight (parent unit / float / mast).",
    ),
    "PC": Material(
        name="PC", E_xy=2300, E_z=1500, nu=0.38,
        sigma_xy=60, sigma_z=30, density=1.20, max_service_c=110,
        notes="Strong and heat resistant, but hygroscopic and warp-prone; "
              "needs a hardened nozzle-free hot end at 270-300 C.",
    ),
    "PA6-CF": Material(
        name="PA6-CF", E_xy=6500, E_z=2600, nu=0.38,
        sigma_xy=90, sigma_z=32, density=1.19, max_service_c=120,
        notes="Very stiff in-plane, and very anisotropic (Z is ~35 % of XY). "
              "Needs a hardened nozzle and drying. Best where a thin, stiff "
              "member carries in-plane load.",
    ),
    "PLA-CF": Material(
        name="PLA-CF", E_xy=4000, E_z=2200, nu=0.36,
        sigma_xy=45, sigma_z=22, density=1.24, max_service_c=55,
        notes="Stiffer and less stringy than PLA but more brittle; the carbon "
              "fill does not help interlayer strength at all.",
    ),
}


def get_material(name: str) -> Material:
    key = name.strip().upper().replace(" ", "").replace("_", "-")
    aliases = {"PA-CF": "PA6-CF", "NYLON-CF": "PA6-CF", "PA12-CF": "PA6-CF",
               "PLACF": "PLA-CF", "PETG-CF": "PETG"}
    key = aliases.get(key, key)
    if key not in MATERIALS:
        raise KeyError(
            "unknown material %r; known: %s" % (name, ", ".join(sorted(MATERIALS)))
        )
    return MATERIALS[key]


def effective_density_factor(walls: int = 4, infill: float = 0.25,
                             nozzle: float = 0.4) -> float:
    """Rough solid-fraction of a printed part.

    Used to convert a solid-model volume into a mass estimate and to derate
    stiffness for sparse infill. A wall-dominated thin part is nearly solid; a
    chunky part with 25 % infill is not.
    """
    return min(1.0, max(infill, 0.35 + 0.1 * walls))

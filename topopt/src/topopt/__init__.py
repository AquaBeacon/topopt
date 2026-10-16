"""topopt — voxel FEA and SIMP topology optimisation for FDM-printed parts.

Everything works on a regular voxel grid so one engine serves both the
stiffness check (`topopt fea`) and the topology optimisation
(`topopt opt`). Units are mm / N / MPa throughout.

Layers, bottom up:

    voxel      STL -> occupancy grid
    hex8       incompatible-mode 8-node brick element, FDM failure index
    solve      matrix-free conjugate-gradient linear elasticity
    materials  FDM material data (E, nu, sigma_xy, sigma_z, rho)
    loadcase   study JSON -> boundary conditions on the grid
    report     stress summaries and result PNGs

    designspace  preserve / obstacle / design_space setup model + validation
    generative   SIMP optimiser that respects that model
    members      medial-axis rib extraction from a density field
    meshout      density field -> STL (blocky/smooth) and planar-BREP STEP
    setup_preview  colour-coded 3D preview of a setup before solving

    geom       STL/STEP geometry utilities (sections, overhang, clearance)

The heavy/optional modules (designspace, generative, meshout, setup_preview,
members, geom) are *not* imported here, so a plain FEA run does not pull in
matplotlib or scikit-image.
"""

__version__ = "1.0.0"

from .materials import MATERIALS, Material, get_material
from .voxel import voxelize_stl, load_stl, Grid
from .hex8 import hex8_ke, element_dofs
from .solve import solve_linear_elastic, solve_cases, SolverConfig, Result
from .loadcase import LoadCase, load_case_from_json, region_mask

__all__ = [
    "__version__",
    "MATERIALS", "Material", "get_material",
    "voxelize_stl", "load_stl", "Grid",
    "hex8_ke", "element_dofs",
    "solve_linear_elastic", "solve_cases", "SolverConfig", "Result",
    "LoadCase", "load_case_from_json", "region_mask",
]

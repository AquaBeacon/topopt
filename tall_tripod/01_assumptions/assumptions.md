# Assumptions (前提条件) — tall_tripod_study

_Everything on this page is an **input**. A topology-optimisation result is a picture of its assumptions: change any line below and a different shape is equally optimal. The shape is not the result — the shape together with this page is._

Topology-optimisation validation model: the tall tripod (spec 5.3).

**Why it is set up this way.** Raising the camera to about 200 mm puts a real three-dimensional load path between the camera mount and the floor, and how to build it -- one column, three legs, a braced tripod, something else -- is genuinely open.

> This is a validation model, not the shipped bracket. osmo_tripod/osmo_tripod.scad stays as it is.

**Units.** mm / N / MPa (lengths mm, forces N, stresses MPa)

## 1. What it has to survive

Every load case is a use case someone chose. They are not measured service loads unless a case says so.

| Case | Held at | Loaded at | Force (N) | \|F\| N |
|---|---|---|---|---|
| **LC1f** | ground_all (xyz) | cam_iface | (0, 1, 0) | 1.00 |
|  |  | cam_iface | (0, 0, -3.44) | 3.44 |
| **LC2** | ground_all (xyz) | cam_iface | (0, 0, -17.2) | 17.20 |
| **LC3n** | catch_n (xyz) | cam_iface | (0, 0, 12) | 12.00 |
|  |  | cam_iface | (4, 0, 0) | 4.00 |
| **LC3sw** | catch_sw (xyz) | cam_iface | (0, 0, 12) | 12.00 |
|  |  | cam_iface | (-3.46, -2, 0) | 4.00 |
| **LC3se** | catch_se (xyz) | cam_iface | (0, 0, 12) | 12.00 |
|  |  | cam_iface | (3.46, -2, 0) | 4.00 |
| **LC4t** | ground_all (xyz) | cam_plus_x | (0, 0.6, 0) | 0.60 |
|  |  | cam_minus_x | (0, -0.6, 0) | 0.60 |
|  |  | cam_iface | (0, 0, -3.44) | 3.44 |

**LC1f** — push 1.0 N horizontally at the camera mount

1.0 N is the tipping limit, and the tipping limit is what bounds the load on this model -- it is not a strength case, because past it the part is lying on the floor. BEST-CASE tipping (feet on the rim of the design space, camera mass only, camera top at z = 202.5 using the 54.04 mm case height measured from osmo-action-6.step rather than the mount's provisional 75 mm envelope): 0.35 kg x 9.81 x 60 mm / 202.5 mm = 1.02 N. Essentially unchanged from v7's 1.025 N -- the mount raises the camera by 1.5 mm, not by its own height. The actual value depends on where the optimiser puts the feet and is only known after the run -- read `ground_contact` in the summary.

_1.0 N forward at the mount plus the 0.35 kg camera weight (3.44 N down). The horizontal force is applied at the mount because that is where preserve is; the moment it stands in for is 1.0 N x about 47 mm of remaining lever from the region's centroid to the camera top._

**LC2** — camera weight plus 5x vertical shock

Setting the tripod down hard, or a knock straight down. 5 g is the usual screening factor for a hand-carried item. NOTE that the mount's own 143,505 mm3 -- about 180 g of PLA, half the camera again -- is NOT in this number. Self-weight is not applied anywhere in this study; it was not in v4..v7 either, and it matters more now than it did.

**LC3n** — lift by the head, north foot caught

Picking the tripod up by the camera while one leg is still hooked on something. This is the case that decided the mass of the short tripod, and the only kind of case here that asks for a wide base -- everything else is happiest with a single central post. Three of them, one per catch zone, because the hooked leg can be any of them and one case alone just moves the post over to that zone (measured in v4 -- see the catch-zone note in `regions`).

**LC3sw** — lift by the head, south-west foot caught

_The 4 N horizontal component points away from the caught foot, as in LC3n, rotated with the zone._

**LC3se** — lift by the head, south-east foot caught

**LC4t** — twist the head about the vertical axis

Tightening the screw, or panning the camera against friction. A hollow section is stiff in bending and can still be soft in torsion, so this case is what stops the optimiser producing an open C-section.

_A 0.60 N couple across the mount: +Y on the +X half, -Y on the -X half, about 52 mm apart, which is 31 N.mm -- the same moment v4..v7 applied as 3.0 N across 10 mm. Combined with the camera weight so the case is a real one rather than pure torsion._

Cases that must be present for the study to be considered complete: `LC1f`, `LC2`, `LC3n`, `LC3sw`, `LC3se`, `LC4t`.

## 2. What counts as a pass

| Requirement | Value |
|---|---|
| Safety factor (p99.5 of elements) | ≥ 3.0 |
| Maximum deflection | ≤ 3.0 mm |
| Safety factor this material card calls for | ≥ 4.0 |

- materials.required_safety_factor() asks for 4.0 while the PLA card is provisional (no value measured on this printer). 3.0 is kept here so the number stays comparable with the recorded runs; the report states the shortfall.
- At about 200 mm the camera is a long lever. 3 mm at the camera top is about 0.9 degrees of tilt, which is where video framing visibly drifts.

## 3. What it is made of

| | |
|---|---|
| Material | PLA |
| Model | `orthotropic_equivalent_solid` |
| Card | `pla_generic_fff_orthotropic_v1_x0.75E_x0.75S` (conservative_literature_based_default) |
| Measured on this printer | **no** |
| Source | docs/FFF_PLA_PETG_orthotropic_material_reference.md section 5 |
| Walls / infill used for derating | 4 / 0.25 |
| Max service temperature | 50 °C |

Stiffness (MPa): Ex 2250, Ey 1800, Ez 1125, Gxy 750, Gyz 412.5, Gzx 412.5

Strength (MPa): Xt 37.5, Xc 45, Yt 26.25, Yc 37.5, Zt 16.5, Zc 30, Sxy 18.75, Syz 11.25, Szx 11.25

> Stiffest common filament, but creeps under sustained load and softens in a hot car / direct sun. Fine for jigs and dry indoor parts; wrong for anything structural left loaded for days.

## 4. What has to make it

| | |
|---|---|
| Machine | Bambu Lab X1-Carbon |
| Usable build volume | 250 × 250 × 250 mm |
| Nozzle / smallest wall | 0.4 mm / 0.8 mm |
| Printer chosen or assumed | built-in |
| Build axis (up on the plate) | **Z** |
| Raster axis (in-plane) | X |

- Printed standing up, the way the model is drawn: the layer planes are horizontal, so bending the structure pulls the layers apart. That is the worst orientation for an FFF part and is exactly what this model is meant to expose. `build_axis` is also which way is *down* for ground contact.

**Does it fit?** The design space measures 240 × 240 × 196 mm; the machine takes 250 × 250 × 250 mm. With Z up: **fits**.

## 5. How much of the shape was decided in advance

| | |
|---|---|
| Design-space bounding box | 240 × 240 × 196 mm |
| Voxel size | 4.0 mm, grid 64×64×54 |
| Volume fraction target | 0.05 of the free region |
| Filter radius | 20.0 mm |
| Symmetry imposed | none |
| Preserve (always kept) | 136960.0 mm³ |
| Obstacle (never filled) | 0.0 mm³ |
| Free for the optimiser | 5364224.0 mm³ |
| Share of the domain the optimiser may decide | **98 %** |
| Share of the expected final part that is preserve | **34 %** |

- No mirror symmetry declared. The three LC3 cases each load one side and LC4t twists; mirror-averaging would pull the result away from what those cases actually ask for. It would also decide part of the stance, which is the thing this model exists to stop doing. NOTE that the head itself is NOT symmetric in X (the nut side is 6.00 mm thick, the knob side 3.00) nor in Y (the rear plate and its gussets are all on -Y), so a mirrored structure would be wrong for it anyway.
- 4.0 mm. v4..v7 recorded 10.0 here and were run at 3.0 from the command line, which meant the file never said what was actually solved. This is the size the v8 run was solved on. It is still an EXPLORATORY grid: 4 mm across a d240 x 120 design space is 60 cells wide and about 106,000 free cells -- enough to see which topology the loads ask for, not enough to size a member. `topopt setup` times one solve on whatever grid it builds and prints what the run would cost, so the trade is priced before it is paid.

**Fixed by the CAD model, not by the optimisation.** These act on preserve, so their positions are inputs:

| | Region | Position (mm) | Volume mm³ |
|---|---|---|---|
| load | `cam_iface` | (-0.2, -17.5, 150.4) | 71424.0 |
| load | `cam_plus_x` | (24.9, -19, 151.5) | 27328.0 |
| load | `cam_minus_x` | (-24.3, -18.2, 150.6) | 28480.0 |

**Where the part touches down is a result, not an input.** These supports act on the build plane, so the optimiser chose how many feet, how far out and what shape. What this study still chose is the zone to look for contact in:

| Zone | Centre of the searched band (mm) | Band area mm² |
|---|---|---|
| `ground_all` | (0, 0, 2) | 45248.0 |
| `catch_n` | (0, 79.8, 2) | 6880.0 |
| `catch_sw` | (-69.2, -40, 2) | 6944.0 |
| `catch_se` | (69.2, -40, 2) | 6944.0 |

A ground contact is glued: the analysis cannot tip the part over, so no load case in it asks for a wide stance. If the zones above are the only thing producing one, they are doing more work than they look like they are.

Geometry read from:

- **preserve** `camera_mount` → `{'stl': '02_setup/tall_camera_mount.stl'}`
- **design_space** → `02_setup/tall_v8_design_space.stl`

## 6. What this study does *not* establish

- **The material card is not measured.** Every stiffness and strength in section 3 is a literature/handbook value for PLA, not a coupon test of this filament on this machine at these settings. Use the results to compare options (build direction, layout, load case), not as absolute strength.
- **The safety factor used (3.0) is below the 4.0 this material card calls for** while it is unmeasured. Results are reported against 3.0; read them with that in mind.
- **Stability was not analysed.** The 4 ground-contact support(s) are glued to the plate, so the model cannot tip over and no load case in it rewards a wide stance. Whether this part stands up is a rigid-body question answered outside this analysis.
- The free region is only 2.0 voxels thick at its thinnest, so in that region the topology follows the outline rather than the optimisation.
- Nothing here has been tested physically. No printed specimen, no static load test, no comparison of a measured stiffness against the predicted one.

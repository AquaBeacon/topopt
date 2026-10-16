# Design space — preserve, obstacle, design space, starting shape

Resolution precedence is CLI `--voxel`, `geometry.voxel_mm`, root
`resolution_mm`, then root `voxel_mm`. The root `voxel_mm` alias was accepted
by validation but ignored until 2026-10-07. Check the actual setup grid before
comparing an older run. Pinch repair with self-support enabled may add only
supported cells; it cannot repair a contact by introducing a floating bridge.

The setup half of topology optimisation, in the same four body types Fusion
Generative Design uses. Getting these right decides whether the result is a
part you can build or a picture of one.

```
topopt setup study.json --out DIR     # validate + 3D preview, seconds
topopt opt   study.json --out DIR     # the same setup, then solve
```

Exit codes are findings, not crashes. `topopt setup`: 0 = valid, 1 = invalid,
2 = unreadable. `topopt opt`: 0 = the result is connected *and* every load case
passes its safety factor on the final solid geometry, 1 = it does not — usually
meaning `volfrac` is below what one case needs.

`topopt opt` runs the validation and writes the preview itself before the
first solve, and refuses to run an invalid setup. `topopt setup` exists so you
can iterate on the geometry in seconds instead of finding the mistake an hour
into a run.

## The four body types

| type | density | what belongs here |
|---|---|---|
| `preserve` | **fixed 1** | every face you bolt, load, locate or grip from |
| `obstacle` | **fixed 0** | holes, moving-part envelopes, cable runs, tool access, optical fields of view |
| `design_space` | free | the space the optimiser is allowed to fill |
| `optional_starting_shape` | initial guess only | a warm start; it constrains nothing |

Each entry is an STL path, a named region, or an inline region:

```jsonc
"geometry": {
  "clearance_mm": 1.0,                    // default obstacle offset
  "design_space": "envelope.stl",         // or a list of several

  "preserve": {
    "mount":  "mount_pad.stl",
    "foot_F": {"type": "cylinder", "axis": "Z", "centre": [0,45,0], "r": 6,
               "range": [0, 3]}
  },

  "obstacle": {
    "cable":     {"type": "box", "min": [10,-6,0], "max": [40,6,12]},
    "lens_cone": {"stl": "fov.stl", "clearance_mm": 2.5}   // per-body override
  },

  "optional_starting_shape": "current_part.stl"
}
```

Region types are the same as everywhere else in this skill (`box`, `cylinder`,
`sphere`, `face`, `all`, plus `invert`) — see `topopt/loadcase.py`. Any key
starting with `_` is a comment; JSON has none, and a setup file that does not
say *why* a body is there is a setup file nobody can review.

## The rules, and why each one exists

**Preserve is density 1 and is never eroded.** Not by the density filter, not by
the Heaviside projection, not by the symmetry mirror, not by thresholding, not
by the connectivity cleanup, and not by the marching-cubes smoothing that
produces `*_final_smooth.stl`. Everything that could reduce it runs first and
the preserve value is imposed afterwards, every iteration. The failure mode this
prevents is silent: the mounting face slowly rounds off over 40 iterations and
the result looks fine until it will not bolt to anything.

**Loads and supports act on preserve only.** A load applied to free material
tells the optimiser it may delete the thing you are pushing on — and it will,
because deleting it is cheap. So every support and load region is intersected
with preserve before the boundary conditions are built. That *enforces* the
rule rather than merely testing for it, and it means a region does not have to
be drawn to voxel accuracy: a load cylinder that clips a bolt bore or overhangs
a boss by half a millimetre simply loses those cells.

What is still an error is a region that touches **no** preserve at all — that is
aiming at the wrong place, not rounding. A partial miss is reported with its
percentage so it stays visible; set `"design": {"strict_bc": true}` to make a
partial miss an error too, when you want your regions exact.

**Ground contact is the one exception, and it costs you something to use.** A
support may say `"contact": "ground"`:

```json
{"region": "floor", "fix": "xyz", "contact": "ground"}
```

That support is clipped to the **contact band** — the one-voxel layer of the
domain at the low end of the ground axis, i.e. the plane the part is printed on
and the plane anything standing up stands on — instead of to preserve. It may
therefore act on free material.

The ground axis is `build_axis` unless the study declares `ground_axis`
separately (`"ground_axis": "Z"`). The two are decoupled so the build
direction can be compared X / Y / Z on a part that keeps standing on the same
floor (spec Phase 6): with `build_axis: X, ground_axis: Z` the layers run along
X, the ground band and the ground-contact supports stay on the Z floor, and the
self-support / pinch-healing rules still follow the build axis, because they
are about what the printer can build.

The protection the preserve rule was providing is kept, differently. A ground
support fixes only the nodes **on the plate itself**, never the four on top of
those cells. So if the optimiser puts nothing on the plate, the structure is
genuinely unsupported and the compliance says so on the next iteration. It
cannot get free fixity by hanging a leg one voxel off the ground — which is
exactly what happens if you widen this to `_nodes_of`, and the result is a part
certified as standing on nothing.

Loads are never ground contact: the plate holds the part, it does not push it.

Use it when *where the part touches down* should be a result rather than an
input. Without it, supports have to sit on preserve, so somebody draws the feet
in CAD and the report has to disclaim their position.

**What it does not do is model tipping.** The contact is bilateral — the part is
glued to the plate and cannot lift off. Compliance minimisation has no way to
express a rigid-body stability criterion, so no case with a whole-floor support
will ever ask for a wide stance. Measured on the tall tripod: freeing the feet
and keeping a single one-foot-caught case produced a slim post leaning towards
that one region, two contact pads, and a safety factor of 128. It falls over.
The workaround is to write the partial-support case at several azimuths so the
structure has to reach all of them; the number and rough position of those
zones is then the input, which is far less than the feet were, but it is not
nothing. Check the achieved stance in `volumes.ground_contact`, which reports
the contact plane, area and number of pads.

**Preserve keeps its own holes.** An obstacle is often a feature *of* a preserve
body — the bolt bore through a boss, the slot a tab slides into. Model it that
way: preserve is the real solid (holes already in it), the cuts are obstacles so
the optimiser cannot fill them back in. The two meshes share a surface, and
voxelising them separately makes them disagree by a cell along it; preserve wins
there and the shell is reported as a warning. A body driven *through* another
leaves a core more than one cell thick, and that is still an error.

**Obstacles get a clearance offset before they are voxelised.** `clearance_mm`
on the `geometry` block is the default; any obstacle can override it. The offset
is a true Euclidean grow, but it is quantised to the grid: a clearance below one
voxel is rounded *up* to one voxel and warned about, because rounding down would
silently produce a part that touches the thing it must clear. If a clearance
matters to the millimetre, refine the grid until the voxel is smaller than it.

**A clearance cannot widen a hole in a preserve body.** The same `preserve wins`
rule applies to the grown mask, not just the declared one: wherever the offset
runs into preserve, it is discarded. So raising `clearance_mm` on a feature-cut
obstacle does *not* open the bore or the slot up — the preserve mesh still
decides those. What it does do is push the optimiser back in the **free** design
space around the feature, which is where material creeps to within one voxel of
a pocket it is supposed to leave alone. Both are worth having, but they are
different fixes: to widen the fit itself, widen the CAD and re-export both the
preserve and the obstacle mesh.

**Only design-space cells are variables.** Cells outside the design space and
inside obstacles are fixed at 0, cells inside preserve are fixed at 1, and
`volfrac` is measured against the *free* cells only. The whole-part fraction is
always higher — the summary reports both, because this is the number people
misread. Obstacle cells stay in the finite-element mesh at void stiffness rather
than being deleted: carving them out of the mesh strands islands of elements and
buys nothing.

**Every preserve body must be reachable.** Separate preserve bodies are the
normal setup — joining them is the optimiser's job. But if two of them fall in
different connected components of `design_space | preserve`, no material the
optimiser can add will ever join them, and that is an error before the first
solve, not a disappointment after the last.

**A preserve body may say where it can be joined (`attach`).** By default the
design space touches every face of every interface, so the optimiser may grow
material onto any of them — and it does: on the tall tripod the result crept
onto the flanks and the top of the camera head, which is exactly where the
camera and the thumbscrew need the space, and the part looked like nothing
anyone would draw. A preserve body can declare its attachable faces:

```jsonc
"preserve": {
  "mount_head": {"stl": "02_setup/tall_preserve.stl",
                 "attach": ["-Z"]},                 // the underside only
  "boss":       {"type": "cylinder", ..., "attach": ["-Z", "+X"]},
  "pad":        {"stl": "pad.stl", "attach": ["pad_root"]}   // a region
},
"attach_standoff_mm": 0                              // shell thickness; 0 = one voxel
```

An `attach` entry is a face of the body (`"+X"` … `"-Z"`), a named region, an
inline region, or a list of any of those. A *face* is the body's own face, not
a plane in space: `"-Z"` selects the design-space cells reached by stepping
down out of the body, so it follows an undercut or a stepped underside exactly
and says nothing about cells beside the body. A *region* is clipped to the
cells next to the body — a region drawn generously simply loses the rest, the
same rule loads and supports follow.

Everything else on that body's surface gets a **no-contact shell** — one
voxel thick, or `attach_standoff_mm` — and that shell is an obstacle from
setup onwards: the optimiser cannot put material in it, the threshold cannot
keep material in it, and the finish stage cannot smooth material into it. A
body with no `attach` key keeps the old rule (joinable anywhere), so existing
studies are unchanged. The setup report lists, per body, the attachable area
and the shell volume (`info.attach`), and the preview draws the attach zone
in orange. Two mistakes are errors, not results: an attach zone that selects
no free design-space cell (the face looks out of the domain, or sits under an
obstacle), and an empty `attach` list.

## What the validation catches

`topopt setup` exits 1 and names the body for each of:

- an obstacle driven through a preserve body — more than a one-cell shared shell
  (reported separately for the declared body and for its clearance, so you know
  which to change)
- a load or support region that touches no preserve at all
- preserve bodies that cannot be connected through the design space
- a preserve body whose `attach` zone selects no free design-space cell, or
  whose `attach` list is empty — nothing could ever join it
- a region that selects no material
- no free cells left to optimise

and warns about:

- an obstacle sharing a one-cell boundary with preserve — the normal signature
  of a hole cut into a preserved body
- a load or support region that reaches past preserve, with the percentage that
  was dropped
- preserve sticking out of the design space (kept as material, but nothing can
  be added to it — usually a sign the body is oversized)
- a clearance finer than the voxel
- a body that voxelised to nothing (wrong units, wrong coordinates, or a leaky
  STL)
- obstacles blocking most of the design space
- a symmetry constraint on geometry that is not symmetric about that plane

## Will the machine print it — asked before the solve, not after

The optimiser only ever puts material inside the design space. So whether the
result fits the printer is decided when the design space is *drawn*, and there
is no reason to find out three hours later. Declare the machine and `topopt
setup` answers it in seconds:

```json
"printer": "X1C"
```

or, for anything not in the built-in list — this toolkit is not tied to one
vendor:

```json
"printer": {
  "name": "shop machine",
  "build_volume_mm": [400, 300, 450],
  "nozzle_mm": 0.6,
  "margin_mm": 5,
  "allow_oversize_design_space": false
}
```

`build_volume_mm` is the volume you can actually print into, not the sales
figure. `margin_mm` is held back on every side for brims, elephant-foot
compensation and plate-edge adhesion. Undeclared resolves to a default **and is
labelled as defaulted** everywhere it is reported, because "no machine was
chosen" and "this machine was chosen" must not read the same.

Two bounds, answering different questions:

| | contains | so if it does not fit |
|---|---|---|
| **preserve** | is in every candidate shape | **no** result of this study fits |
| **the domain** | contains every candidate shape | the result *may* not fit, and you would only know afterwards |

Errors:

- preserve outside the envelope in every orientation — nothing this study can
  produce is printable in one piece
- preserve fitting only with a different axis up — that orientation has to be
  *printed*, which moves the layer planes, so the orthotropic analysis about to
  run would no longer describe the object. **"Fits rotated" is not a pass.**
  Re-declare `build_axis` and re-run
- the domain outside the envelope — by default. `allow_oversize_design_space`
  permits it for parts meant to be split, which puts the decision on the record

Warnings: less than 5 mm of clearance, and a `filter_mm` finer than two nozzle
widths (the optimiser would be free to produce members the printer cannot make).

## How much was left to decide

A topology optimisation only answers the question it is given room to answer,
and the room can disappear in three ways that the *output* cannot show. So
`setup` measures them:

| reported | meaning | flagged when |
|---|---|---|
| `free_fraction_of_domain` | share the optimiser may decide | < 0.5, strongly < 0.2 |
| `preserve_fraction_of_expected_part` | share of the expected final part that is preserve | > 0.5 |
| `free_min_thickness_voxels` | thinnest part of the free region | < 3 — no room for a hole, so the outline sets the topology |
| `fixed_points` | where every load and support sits | always listed |

The last is the one that gets missed. Loads and supports may only act on
preserve, so **the positions of the feet were chosen by whoever drew the CAD** —
the optimiser decided how to reach them, never where they go. That is a
perfectly good way to run a study. Leaving it unsaid is not, because a reader
assumes an optimised layout was optimised, so it also lands in the "what this
does not establish" section of `assumptions.md`.

## The preview — look at it before you solve

`DIR/<base>_setup.png` — three orthographic composites plus an isometric voxel
view, **green preserve, red obstacle + clearance, transparent grey design
space, orange attach zone, blue starting shape**, with an arrow per load. Wrong units and a body in
the wrong place are obvious here and invisible everywhere else.

`DIR/<base>_setup.scad` — the same three bodies imported with colour, openable
in OpenSCAD (F5). STL carries no colour, which is why this file exists.

Also written: `_preserve.stl`, `_obstacle.stl` (offset) and
`_obstacle_declared.stl` (as drawn), `_design_space.stl`, `_attach.stl` and
`_no_attach.stl` (when a body declared `attach`), `_setup.stl` (combined), and
`_setup.json` (the full report).

## The optimiser

Set in the `design` block. Defaults in brackets.

| key | | |
|---|---|---|
| `volfrac` [0.3] | target material fraction **of the free cells** | |
| `filter_mm` [max(3, 3·voxel)] | density-filter radius ≈ minimum member size | set ≥ 3× the printable wall |
| `penal` [1 → 3] | SIMP penalisation continuation | a number, or `{"start":1,"end":3,"step":6,"increment":0.5}` — `step` defaults to `iters/8` |
| `beta` [1 → 16] | Heaviside projection continuation | a number, or `{"start":1,"max":16,"step":10}` — `step` defaults to `iters/5`, and beta doubles each step |
| `projection_eta` [0.5] | projection threshold | |
| `symmetry` [] | mirror planes, e.g. `["X"]` | the grid is snapped to the plane |
| `overhang` [true] | self-support along `build_axis` (45 degree cone, plate at the low end); `false`, or `{"up": "-Z"}` | see "Self-support along the build direction" |
| `move` [0.2] | OC move limit | |
| `volume_exact` [false] | exact projected-volume constraint | slower; see below |
| `strict_bc` [false] | make a *partial* load/support miss an error, not a warning | a total miss is always an error |
| `tol_change` [0.002] | stop when fewer than this fraction of cells change side | |
| `stress` [false] | per-load-case strength constraint (required safety factor × relaxed failure index ≤ 1; the factor is spec 15.1's, so an `abnormal` case is held to 1.5), or `{"safety_factor": ..., "p_norm": 8, "relax_q": 0.5, "samples": "all" / "centroid", "scale_damping": 0.5}` | see "Stress constraint" below |
| `optimizer` ["oc"] | `"oc"` (volume constraint only) or `"mma"` (any number of constraints); defaults to `"mma"` once `stress` is set | `stress` with `"oc"` is refused (`InputError`, exit code 2) |

Three things about reading the output:

- **The objective is normalised.** Each load case is divided by its own
  first-iteration compliance, so it starts at exactly *N cases* and a value of
  1.4 for 2 cases means "on average 30 % stiffer than the starting field". Raw
  per-case compliances are in the JSON history.
- **The objective rises when `penal` or `beta` steps.** That is continuation
  working, not divergence. The convergence plot draws both schedules on a second
  axis so you can see which step caused which jump.
- **Convergence is measured on the shape, not the design variable.** At high
  `beta` the design variable keeps bouncing off the move limit while the
  geometry is perfectly still; the criterion is the fraction of free cells that
  changed side of the 0.5 threshold.

Continuation defaults scale with `--iters`, so a 30-iteration run still reaches
`beta` 16. If you cut `--iters` very low, expect a grey result.

### Minimum member size — approximate, and check it

`filter_mm` plus the projection gives a length scale of roughly the filter
radius. It is a strong tendency, not a hard constraint, so the run *measures*
what it produced: the inscribed-sphere diameter on the medial axis of the
finished geometry, as min / p5 / median.

Two figures are reported and the distinction matters. `min_member_optimised`
covers the free cells — the only ones `filter_mm` ever governed. `min_member`
covers the whole part including preserve, which is whatever the CAD says it is.
On a run whose preserve carried 2.5 mm camera tabs the two read median 7.18 mm
and 3.21 mm against `filter_mm 4.0`: the whole-part figure looks like a filter
violation and is nothing of the kind.

Read either with one caveat: both are floored at two voxels. Every run measured
here reported `min_mm` at exactly 2·voxel, which means the grid — not the
structure — set that number. To confirm the filter's length scale, run at a
voxel below `filter_mm / 3` and read it again.

### The volume constraint

The constraint belongs on the *projected* field, but the optimiser moves the
design variable. Evaluating the projection inside the multiplier bisection costs
a convolution per bisection step — roughly 30 per iteration — so by default the
bisection runs against the design-variable volume and a feedback term corrects
the target from the measured projected volume each iteration. It settles within
a few iterations and the achieved fraction is reported; if you need it exact,
`"volume_exact": true`.

### What the material model does and does not know about printing

Worth being exact about, because the study file declares `build_axis`, `walls`
and `infill` and only one of them reaches the optimiser.

**Build direction: strength yes, and — since Phase 2 (2026-08-05) — stiffness
too, by default.** `generative.optimize` calls `material_model(study.material)`,
which returns the orthotropic stiffness card by default, and the hex8 element
uses that orthotropic D matrix rotated by `build_axis`/`raster_axis`. So the
*load path itself* now depends on the declared build direction
(`tests/test_orthotropic.py::test_isotropic_and_orthotropic_reach_different_designs`
checks that the design actually changes). `--isotropic` reverts to the single
modulus `E_xy` for comparison against older recorded runs. The **final FEA**
applies the anisotropic failure index either way: tension normal to the layers
against `sigma_z`, layer-plane shear against `0.6·sigma_z`, in-plane yielding
against `sigma_xy`, all keyed off `build_axis`. Every safety factor reported at
the end of a run therefore already assumes the stated build direction and
already penalises interlayer tension.

**Infill and walls: no effect on the topology, by construction.** They only
reach the gravity body-force fill factor, so with no `gravity` in the study they
do nothing at all. That costs less than it sounds: compliance scales as `1/E`,
so derating `E` by any *uniform* factor leaves the optimal shape exactly
unchanged — it only rescales the reported compliance. What it does change is
absolute deflection, and the FEA reports deflection for **solid** material. A
part printed at 5 walls / 25 % gyroid is roughly 85 % dense, so expect the real
one to deflect somewhat more than the number printed. Size the safety factor
with that in mind, and treat the deflection figure as the optimistic end.

Infill/wall density is uniform derating and cannot change the topology for
that reason; anisotropy (`E_z ≠ E_xy`, on by default since Phase 2) is what
does, and it is already in the loop.

### Self-support along the build direction (`overhang`)

On by default since 2026-09-05. Between the density filter and the projection
sits Langelaar's additive-manufacturing filter (`amfilter.py`): the printed
density of a cell is the smaller of its design density and the (smooth)
maximum over the five cells under it — the one directly below and its four
in-plane neighbours — which on a cubic grid is a 45 degree cone. Layer 0 is the
build plate, i.e. the lowest layer of the domain along `build_axis`; nothing
outside the domain supports anything. The filter is differentiable and its
adjoint is exact (checked against central differences in
`tests/test_amfilter.py`), so it is a rule the optimiser designs around, not a
clean-up that cuts the load path afterwards.

| `overhang` | meaning |
|---|---|
| absent / `true` | self-supporting, plate at the low end of `build_axis` |
| `false` | off — the old behaviour; the report's `self_supporting` condition then fails on any unsupported voxel |
| `{"up": "-Z", "p": 40, "ramp": 0.33}` | plate at the high end (part printed upside down relative to the model); `p` is the smooth-max exponent; `ramp` the fraction of the iterations over which the rule is blended in (0 = from the first iteration) |
| `{"max_support_mm": 20}` | **support is allowed where a support column would be at most 20 mm tall**: a cell is also supported by material up to that far down inside the widening cone, and by the plate when it is within that height of it. Short columns are cheap to remove; use this when the strict rule costs too much stiffness |
| `{"allow_support": ["seat_underside", ...]}` | **support is allowed inside these regions** (names from `regions`, any type): the rule is off there. For a boss or seat that has to be where it is. The region is reported with its voxel count; `printcheck` still refuses support that lands on a preserve face |

Three details that were each learned the hard way (all measured on
tall_tripod, 2026-09-05):

- **The plate is the first layer of the domain**, not the first layer of
  the array. The grid usually starts a few voxels under the design space;
  treating that empty layer as the plate makes the whole part "in the air"
  and the filter erases it.
- **The smooth max is neutral at 0.5**, the projection threshold, so nothing
  above an unprintable layer can survive thresholding. The price is that a
  uniform field below 0.5 decays layer by layer (14 % per layer at 0.04), so
  the rule is **blended in over the first third of the iterations**
  (`ramp`): the load path forms first, then is made self-supporting while
  beta is still low. With the rule on, the volume constraint is evaluated on
  the printed field (`volume_exact` defaults to true).
- Cells that move neither the objective nor the printed volume (material
  "in the air") are held in place by the update rather than walked down,
  otherwise the field drains from the top.

Both allowances are applied identically inside the optimiser, in the hard
check of the thresholded shape (`volumes.unsupported`), in the printable
pruning, and in the report's `self_supporting` condition, which then states
what was allowed. Measured on tall_tripod at 10 mm: strict rule objective
3.70, with 20 mm columns and one allowed zone 3.34, both with zero
unsupported voxels under their own rule.

What it does not do: it knows no bridges (a short flat span a printer can
cross is still treated as unsupported), and it does not touch preserve —
preserve is re-imposed after it, so the underside of a mounting boss is CAD's
problem and `finish` cuts it from CAD. The thresholded shape is checked with
the hard version of the same rule (`volumes.unsupported`, and the
`self_supporting` condition in `topopt report`): zero is the target, and
anything else is listed with its location.

### Stress constraint (`design.stress`)

Off by default — without it, the shape is decided by `volfrac` and stiffness
alone, which is fine until strength, not stiffness, is the thing that should be
shaping the part. `design.stress` adds one constraint per load case: required
safety factor × relaxed failure index ≤ 1, everywhere. The failure rule is the
same one the final FEA uses (max-stress against the nine material-axis
strengths, tension/compression one-sided, each shear against its own
strength), written as twelve one-sided modes and aggregated with a p-norm
(`p_norm`, default 8) over every element and sample point (`samples`: `"all"`
— 8 corners + centroid — or `"centroid"`). Element stress is relaxed with
`density^relax_q` (default 0.5, below `penal`) so intermediate density does not
fake a false positive. The gradient is an adjoint solve per load case per
iteration, reusing the primal solve's multigrid hierarchy.

Because the OC update only handles one constraint (volume), a stress
constraint needs `design.optimizer: "mma"` (the Method of Moving Asymptotes;
this is the default once `stress` is set). MMA sees the log of the objective,
so the objective is rescaled to 1 every iteration — without that the volume
constraint drifts (measured: 0.646 instead of a 0.400 target on a 100-element
test bar).

**What is and is not constrained.** The constraint acts on the relaxed,
aggregated index on the analysis grid at the density field — not on the final
FEA of the thresholded solid. The report's `safety_factor` condition still
judges that final re-analysis, and the two numbers can disagree (measured on a
real part: optimiser-side SF×failure-index 1.571 against a final-FEA p99.5 of
2.49). Mesh convergence applies to both.

**Refused, not silently degraded:** `stress` together with `--isotropic` is an
`InputError` (exit code 2) — the legacy two-constant criterion mixes von Mises
into the rule and has no material-axis strengths to check against. `stress`
together with `optimizer: "oc"` is an `InputError` (exit code 2).

**An infeasible requirement does not stop the run.** If the required safety
factor cannot be met at the given `volfrac`, MMA trades the other constraint
instead: the volume drifts upward while the stress constraint stays violated
(measured: `safety_factor` 6 on a bar that only reaches ~6.9 solid pushed
volume from 0.3 to 0.419, and still finished at SF×failure-index 1.60). Read
both the volume and the stress lines, not just whether the run finished.

**The required factor is per load case (spec 15.1).** A case declared
`load_class: "abnormal"` — a drop, a lift with one foot hooked — is constrained
at 1.5, or at `stress.safety_factor` when that is already lower; every other
case is constrained at `stress.safety_factor` (which itself defaults to the
study's `safety_factor`). This is the same rule `topopt fea` and `topopt ccx`
judge the finished shape by, so the loop is no longer stricter than the
verdict. A study with no `load_class` puts the same number on every case and is
bit-for-bit the run it was before this existed.

The run summary's `stress_constraint` block records it: `case_safety_factors`
is the list in study-case order, and each entry of `final` carries its own
`load_class` and `required_safety_factor`. Note that `max_ratio` is *that
case's* required factor × index, so comparing `max_ratio` between a normal and
an abnormal case does not say which is closer to failing —
`min_safety_factor_relaxed` (required factor ÷ `max_ratio`) does.

## Outputs

| file | what it is |
|---|---|
| `<base>_topo.png` | **look at this first**: material depth, mid-slices, convergence |
| `<base>_setup.png` / `.scad` | the setup, colour-coded |
| `<base>_density_raw.npy` | the design variable |
| `<base>_density_phys.npy` | filtered + projected physical density |
| `<base>_binary.npy` | the thresholded geometry |
| `<base>_final.stl` | that geometry as a mesh — exactly what was analysed |
| `<base>_final_smooth.stl` | marching cubes, preserve floored at 1 |
| `<base>_final.step` | planar-BREP solid, coplanar faces merged |
| `<base>_preserve/_obstacle/_design_space/_setup.stl` | the setup bodies |
| `<base>_fea_N.png` | final FEA per load case |
| `<base>.json` | settings, volumes, connectivity, FEA, member list |

**Material depth, not max projection.** A max projection of a 3D result reads
as solid wherever a single rib crosses the column. The top row of `_topo.png`
is millimetres of material along each ray and the bottom row is the mid-plane
slice through the finished geometry; between them you can actually read the
topology.

### Connectivity report

After thresholding, the result is split into 6-connected components. Every
preserve body must land in one component; material attached to no preserve body
is a floating island, is dropped from the final geometry, and its volume is
reported. `"connected": false` means the run did not produce one part — raise
`volfrac`, lower `filter_mm`, or widen the design space.

### Final FEA

The compliance printed during optimisation is a SIMP number that includes
intermediate density nobody can print. The final FEA re-solves every load case
on the thresholded geometry as fully solid material and reports deflection, von
Mises and the p99.5 safety factor. **That** is the number that belongs in the
part's `.md`. It is skipped, with a message, when the geometry is not connected
— a disconnected structure has a singular stiffness matrix.

### STEP export

There is no CAD kernel in this environment, so the STEP is a faceted solid — but
a reasonable one. Coplanar voxel faces are merged into maximal rectangles
(measured 2.6–5.7× fewer faces on real optimisation results; much more on
slab-like shapes), and the T-junctions that merging creates are
repaired by inserting the hanging vertices into the neighbouring face's loop as
collinear points.

Two checks back that, and both are reported rather than asserted:

- **Geometry.** Re-reading the STEP with a planar-BREP reader and re-voxelising
  it reproduces the original mask exactly — measured, zero differing voxels.
- **Topology.** Every write counts its own edge use. Measured on two real
  results: `open_edges` 0 and `misoriented_edges` 0, so the merge and the
  T-junction repair are exact; `non_manifold_edges` 4 and 10 respectively.
  Those are places where two voxels *of the result* touch only along an edge, so
  four faces meet there. That is a property of a voxel boundary, not of the
  writer — no rectangle bookkeeping removes it, and it is also a spot where the
  FEA is carrying load through two shared nodes, which is worth a look in the
  rebuild. Kernels sew such a shell with tolerance; some flag it on import.

This is a **reference body for Fusion, not the part**, and the reason is
dimensional, not aesthetic. Every feature in the export is quantised to the
voxel. Measured on a run at 1.607 mm whose preserve carried a camera interface:
a 3.50 mm slot came out 3.21 mm (so 0.21 mm of clearance around a 3.00 mm tab
instead of the designed 0.50), a Ø5.30 teardrop bore came out a 6.43 mm square
hole, and a 2.50 mm tab came out 3.21 mm. The topology is right and every
interface is wrong. Everything in `topology-optimization.md` stage 3 still
applies: rebuild from primitives, keep the preserve interfaces at their CAD
dimensions, and prove the rebuild with `topopt compare`.

## Cost

Roughly linear in elements × load cases × iterations. A 10,000-free-cell domain
with 4 load cases and 50 iterations is on the order of five minutes here. Start
at `--elements 8000 --iters 25` to check the topology is sensible, then refine.
`topopt setup` is seconds — use it as the iteration loop for the geometry.

`--elements` targets the *bounding box*, and the box spans every declared body,
obstacles included, so an obstacle that reaches well outside the design space
buys grid cells that hold nothing. It is worth trimming such an obstacle to the
region that can actually matter — the setup report prints `domain_voxels`
against `grid_shape`, which is where you see it.

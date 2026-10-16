# Topology optimisation → a part you can actually build

## Finished-shape verification (2026-10-07)

`run` now sends the finished STL to CalculiX by default. Use
`--ccx-geometry analysis` explicitly for comparison against the coarse voxel
body. Missing declared finish output never falls back to the coarse mesh.
CCX records input STL/study SHA-256; a finished-shape re-analysis must match
the delivered file's content. The report uses the finished shell inspection
and does not label a resolved voxel section as a mesh convergence study.

Self-support-aware pinch repair adds only cells supported by the same hard
rule used during extraction. Unrepairable contacts remain explicit failures.
The current printcheck still measures the analysis grid, so its support-face
and enclosed-support checks remain UNKNOWN for a changed finish body. Review
the actual sliced 3mf before accepting support contact and removal.

`run --supports auto|none|tree --layer-mm 0.2` uses the study material for
slicing. Support quantity comes from G-code, not the geometric column volume.

## What `topopt opt` is for

It answers one question well: **where does material need to be?** It does not
produce a part. Treating its output as a part is how projects end up with an
unprintable organic blob that nobody can edit, that has no parameters, and that
turns a clean 18-face STEP into a 40,000-facet mesh.

The workflow is therefore three stages, and the third is not optional:

```
 design domain + load cases  ->  density field (load-path map)
                             ->  parametric OpenSCAD rebuilt from primitives
                             ->  `topopt fea` confirms the rebuild kept the stiffness
```

## Stage 1 — set up the domain

The design domain is the space the frame is *allowed* to occupy. In an assembly
this is naturally "the envelope minus everything else", which is why the small
parts get designed first (see the detail-first rule in SKILL.md): every camera
mount, handle and connector boss you have already placed becomes an obstacle,
and the optimiser routes material around them instead of through them.

**Write it as geometry — `references/design-space.md` is the full reference.**
Four body types, the same ones Fusion Generative Design uses: `preserve`
(density 1, never eroded, and the only place loads and supports may land),
`obstacle` (density 0, with a clearance offset), `design_space` (the variables),
and `optional_starting_shape`.

```jsonc
{
  "material": "PETG",
  "build_axis": "Z",
  "target_elements": 40000,

  "geometry": {
    "clearance_mm": 1.0,
    "design_space": "frame_envelope.stl",
    "preserve": {
      "tube_saddle": {"type": "cylinder", "axis": "Z", "centre": [0,0,100], "r": 60},
      "eye_left":    {"type": "box", "min": [-90,-10,220], "max": [-70,10,240]}
    },
    "obstacle": {
      "camera_pod": {"type": "box", "min": [60,40,120], "max": [120,100,180]}
    }
  },

  "regions": {
    "tube_saddle": {"type": "cylinder", "axis": "Z", "centre": [0,0,100], "r": 60},
    "eye_left":    {"type": "box", "min": [-90,-10,220], "max": [-70,10,240]}
  },
  "cases": [
    {"name": "hanging 5g",
     "supports": [{"region": "eye_left",  "fix": "xyz"}],
     "loads":    [{"region": "tube_saddle", "force": [0,0,-250]}],
     "gravity":  {"direction": [0,0,-1], "g_factor": 5.0}},
    {"name": "side snatch",
     "supports": [{"region": "eye_left", "fix": "xyz"}],
     "loads":    [{"region": "tube_saddle", "force": [150,0,0]}]}
  ],
  "design": {"volfrac": 0.3, "filter_mm": 4.0, "symmetry": ["X"]}
}
```

Then, always, before solving:

```
topopt setup study.json --out DIR      # seconds; look at the PNG
```

Points that decide whether the result is usable:

- **Use every load case at once.** A structure optimised for one load is a
  mechanism for the others. Each case is normalised by its own first-iteration
  compliance and summed, so one numerically large case cannot own the design.
- **`filter_mm` sets the minimum member size.** Set it to at least 3 × the
  printable wall (so ≥ 2.4 mm on an X1C, realistically 4 mm) or you get hairline
  members that cannot be printed and cannot be rebuilt as primitives. The
  measured min/p5/median member size is reported at the end — check it.
- **Loads and supports must land on preserve.** Optimising away the thing you
  bolt to is a classic and silent failure; under the `geometry` schema it is a
  setup error rather than a silent auto-fix.
- **`symmetry`** halves the search and gives a result a human can read. Use it
  whenever the loads are symmetric — most frames are. The grid is snapped to the
  mirror plane so the constraint is exact.
- **`volfrac`** is a target, not a constraint on quality: start at 0.3, and if
  the result looks like a solid blob, lower it; if it looks like lace, raise it
  or raise `filter_mm`. It applies to the **free cells**, not the whole domain.
- **The result is self-supporting by default.** `design.overhang` (on unless
  set to `false`) makes every cell depend on the 45 degree cone under it along
  `build_axis`, layer by layer from the plate up, inside the optimisation. The
  thresholded shape is re-checked with the hard rule and the report has a
  `self_supporting` stop condition. Orientation still matters — pick
  `build_axis` for the layers, not for the support — and `topopt print` still
  decides fit and support on the *rebuilt* part.
- **`design.stress`** adds a per-load-case strength constraint (required safety
  factor × relaxed, aggregated failure index ≤ 1) inside the optimisation loop,
  off by default. The required factor is spec 15.1's, case by case: a case
  declared `load_class: "abnormal"` is held to 1.5 and the rest to the study's
  own number, so the loop asks for exactly what the final FEA judges by. It
  needs `design.optimizer: "mma"` (the default once `stress` is set — the OC
  update only handles one constraint, the volume one). What it constrains is
  the index on the analysis grid at the density field, not the final
  re-analysis of the thresholded solid — that FEA is still the verdict.
  See `design-space.md` for the knobs and what is refused.

**Legacy schema.** A study with no `geometry` block still works exactly as
before: `part` is the domain and `design.keep_in` / `keep_out` name regions,
including its own heuristic `overhang` (a hard cap, no sensitivity — the
`geometry` schema uses the differentiable filter instead). One correctness fix
applies to both: symmetry and the overhang filter run *before* `keep_in` is
imposed, not after, because both of them were pulling keep_in cells below 1.0.

## Stage 2 — read the result

`topopt opt` --out DIR` writes (`geometry` schema):

| File | What it is |
|---|---|
| `*_topo.png` | material depth + mid-slices + convergence — **look at this first** |
| `*_setup.png`, `*_setup.scad` | the setup, colour-coded |
| `*.json` | settings, volumes, connectivity, final FEA, member list |
| `*_final.stl` | exactly what was analysed, blocky |
| `*_final_smooth.stl` | marching-cubes surface, preserve floored at 1 |
| `*_final.step` | planar-BREP solid, for Fusion |
| `*_density_raw.npy`, `*_density_phys.npy`, `*_binary.npy` | the fields |

(The legacy schema writes `*_topo_voxel.stl`, `*_topo_smooth.stl`,
`*_topo_density.npy` and `*_topo.json` as it always did.)

Sanity checks before believing it:

- **Compliance must drop and flatten.** A curve still falling at the last
  iteration means it has not converged — raise `--iters`.
- **The topology should be recognisable.** Tip-loaded cantilevers produce top and
  bottom chords with diagonal webs; that is the correct answer, and seeing it is
  how you know the load case is set up right. If you get a blob or a shell, the
  supports or loads are probably in the wrong place.
- **`mass_saving_pct`** in the 50–75 % range is typical for a frame. Above ~85 %
  the result is usually too delicate to print.

The `ribs` block in the JSON reports, for each axis, slice by slice, the
material area, its extent, and how many separate members the slice cuts
through — "how many ribs and how thick", but not where they run.

For *where*, `extracted_members` in the same JSON (and `*_members.scad`) gives
each member's centreline endpoints in mm, its width, and the extrusion axis, by
skeletonising the kept material and simplifying each skeleton path to a
polyline. Read `*_members.scad` as a **sketch to start from**, not a part — see
the measured caveat in stage 4.

## Stage 2.5 — put the interface back at its CAD dimensions (`finish`)

The interface is preserve. The optimiser never had a say in it. So there is no
reason for it to come out of the analysis grid — it went in as CAD and it can
come out as CAD.

```json
"finish": {
  "voxel_mm": 0.5,
  "smooth_mm": "auto",
  "add": {"mount_head": "02_setup/mount_solid.stl"},
  "cut": {"mount_features": "02_setup/mount_cuts.stl"}
}
```

`topopt extract` then writes `<base>_t40_finish.stl` alongside the raw result,
built in this order (2026-09-08):

0. **Grow the grid** to hold every `add` and `cut` body. The finish grid used
   to be the analysis grid times the refinement, same origin — right for the
   optimised body, which cannot exist outside the domain it was solved in, and
   wrong for the CAD: nothing says the part that bolts to the interface fits
   inside the design space. A body that reached past the wall was clipped
   there in silence. `finish.cover_pad` pads by whole *analysis* voxels (a
   fractional pad would move the resampled result by a fraction of a cell) and
   `info.grid_grown_voxels` reports it — a non-zero value is also the
   statement that the shipped shape covers millimetres the FEA never saw.
   Measured on the tall tripod v7: +15 voxels in Z for an 84.5 mm camera
   mount on a domain that ended 40 mm above the interface.
1. **Resample** the optimised body onto the `voxel_mm` grid by whole-number
   replication — no interpolation, no shift, the staircase stays exactly where
   it was analysed.
2. **Replace** the analysed preserve with its CAD. An `add` entry named after
   a preserve body (or carrying `"replaces": "<body>"`) drops that body's
   analysis voxels, keeping only a one-voxel *bridge* where optimised material
   met it. Before this, the CAD head was unioned into its own 10 mm
   voxelisation and the shipped file was a 30 mm block with a 19 mm head lost
   inside it. The bridge is what closes the gap between a strut that reached
   the voxelised face and the CAD face up to a voxel away.
3. **Union** the `add` bodies at their CAD dimensions.
4. **Smooth the optimised body only.** A Gaussian of `smooth_mm` (default
   `"auto"` = one analysis voxel; `false` turns it off) runs on the fine
   grid over the optimised material — the resampled result including the
   bridge, and nothing else. Any preserve the CAD did not replace and the first
   layer on the plate are written back at exactly 1, and the obstacles —
   including the no-contact shells from `attach` — at 0. The isosurface level
   is then chosen so the smoothed body has the **same volume** as the staircase
   it came from: corners round, faces stay put, and a one-voxel strut does not
   lose a third of its width to the filter (which is what `*_smooth.stl`'s
   sigma-one Gaussian does to it on a coarse grid — it erased the tall tripod's
   legs outright at 10 mm).

   **The CAD bodies never enter the Gaussian.** They come back as *occupancy*:
   `finish.occupancy()` voxelises each body `occupancy_samples` times finer
   (default 3) and block-averages, so a cell the CAD half fills reads 0.5 —
   which is where marching cubes puts a surface. The two are combined as
   `max(blurred, occupancy + (level - 0.5))`; the shift means
   `occupancy + (level - 0.5) >= level` is exactly `occupancy >= 0.5`, so the
   isosurface lands on the CAD face at *whatever* threshold the volume match
   picks. Cuts get the same treatment in step 5, as
   `min(field, (1 - occupancy) + (level - 0.5))`, so a bore comes out at its
   diameter instead of rounded outward to the nearest whole cell.

   Until 2026-09-09 the Gaussian ran over a mask that already contained the CAD
   bodies and their cells were written back at 1 afterwards. **Holding a cell
   at 1 holds the cell, not the face.** It stops the filter eroding the
   interface and does nothing about it moving: the head blurred into the space
   outside itself and the isosurface came out proud of the CAD face with every
   convex edge rounded. Measured on the tall tripod (3 mm analysis, 1.0 mm
   finish, sigma 1.50 mm): the nut-side finger face 11.00 mm -> 11.34 mm,
   322 mm3 of the CAD head rounded away, and — because the nut pocket was cut
   to the nominal face and no further — a membrane across the mouth of the
   pocket, which made the part impossible to bolt to the camera. With
   occupancy the same faces come out within 0.06 mm of CAD.
   Three more things belong to this step, all of them reported by users
   looking at the shipped mesh rather than at any number:

   - **The plate is a cut, not the end of the material.** Pinning the first
     layer cell by cell keeps the foot down and pins the *analysis grid's
     footprint* with it, so the part ships as a smooth body standing on a
     one-cell skirt with a jagged outline. The material is reflected below the
     plate before the filter runs instead -- built by padding, because the
     grid can start exactly on the plate -- and thrown away afterwards.
   - **The joint blends towards a blur of both bodies.** Taking the CAD out of
     the Gaussian stops it moving the CAD face and starts the optimised
     material necking as it lands on one. Near the optimised material -- the
     weight is its own blurred value, 1 at its surface and nothing two sigma
     out -- the field blends towards a blur of the union, which fillets the
     corner. Away from it the CAD occupancy stands alone and the face is
     exact. The blend is *bounded* to cells within `bridge_mm` of BOTH bodies,
     which is the corner and nothing else: left to the weight alone it reaches
     two sigma in every direction and grows a collar right round the CAD body,
     wider than the interface and wider than what supports it -- a cushion
     with the part sitting on it rather than one piece.
   - **The `attach` stand-off is bridged** (`bridge_mm`, default one analysis
     voxel). `attach` holds the optimised material one voxel off every face it
     may not join, so a CAD body wider than what supports it overhangs that
     stand-off: measured on the tall tripod, a 2.00 mm gap all round a
     30 x 30 mm pedestal whose attach patch is 18 x 18 mm. The fillet may
     enter that shell and nothing else -- not a clearance the study declared,
     and not the neighbourhood of a `cut`, because a fillet is material added
     outside a CAD face and a pocket cut to the face would be capped by it.

5. **Cut** the `cut` bodies. Cuts go **last**, for the same reason
   `Setup.impose` re-applies preserve last: a bore cut before its boss is
   unioned in is a bore that gets filled straight back in, and a slot cut
   before the smoothing is a slot the Gaussian fills.

The file is the marching-cubes isosurface (watertight, one body — `extract`
checks and says so), with the voxel version alongside as
`<base>_t40_finish_voxel.stl`. Without scikit-image only the voxel version is
written, under the `_finish.stl` name.

What this buys is that the interface stops being quantised at the *analysis*
voxel and becomes quantised at the *finish* voxel. Measured on the tall tripod
(10 mm analysis grid, 0.5 mm finish):

| feature | CAD | finished | analysis grid alone |
|---|---|---|---|
| outer finger | 4.00 mm | 4.00 mm | not present |
| slot | 3.80 mm | 4.00 mm | not present |
| centre finger | 2.40 mm | 2.00 mm | not present |
| bolt bore | Ø5.60 mm | Ø5.60 mm | not present |

Re-measured after the occupancy change (2026-09-09; 3 mm analysis grid, 1.0 mm
finish, `occupancy_samples` 3, tall tripod v6 head), the same interface comes
out at its drawing size to the resolution of the measurement:

| feature | CAD | finished |
|---|---|---|
| knob-side finger | 4.00 mm | 4.00 mm |
| centre finger | 2.00 mm | 2.00 mm |
| nut-side finger | 7.00 mm | 7.00 mm |
| slot | 4.25 mm | 4.25 mm |
| nut-side face | x = 12.25 | x = 12.25 (mesh vertices 0.06 mm out) |

What it does **not** buy:

- **It is not exact.** Two different bounds apply and they are reported
  separately. `feature_tolerance_mm` is one whole finish voxel: that is the
  bound for the voxel file `*_finish_voxel.stl`, for the optimised body, and
  for any study with `smooth_mm: false` (no smoothing means no occupancy field
  either, so a CAD face is quantised to the finish grid). `smooth.
  cad_tolerance_mm` — the finish voxel over `occupancy_samples` — is the bound
  for a CAD face in the shipped isosurface. Quote whichever applies, not the
  drawing.
- **The analysis still saw the coarse interface.** Stresses in and around it are
  resolved no better than the analysis voxel. The shape is finished; the
  analysis is not re-done.
- **The optimised body is still quantised.** Resampling to 0.5 mm adds no
  information it never had, and smoothing adds none either — it removes the
  stair steps and nothing else. Do not take a mating dimension off it.
- **Smoothing keeps the volume, not every thickness.** The level is matched on
  the whole body; a single member can still be a little thinner or fatter than
  its staircase. The `smooth` block of the extract summary records sigma, the
  level and the volume change. `topopt ccx` on the finished file is the check.

`PRINT.md` swaps its top-of-page warning when a finish stage ran, because
"every mating dimension in this file is wrong by up to one voxel" stops being
true of half the file — and a warning that is half wrong is one people learn to
skip. The published pair is `part.stl` (finished, the one to slice) and
`part_raw.stl` (the analysis grid, for comparison).

Stage 3 below is still the answer for the optimised body.

## Stage 3 — rebuild it as parametric OpenSCAD

This is the stage that makes the result a part rather than a picture.

Why rebuild instead of printing the mesh directly:

- This repository's rule is that `.scad` uses `cube`/`cylinder`/`polygon` +
  booleans only, so the STEP keeps true planar and cylindrical faces and stays
  editable in Fusion (`test_bracket`: 18 faces as primitives, 426 with rounding).
  A marching-cubes surface is thousands of facets and edits like a mesh.
- A mesh has no parameters. When the tube diameter changes by 2 mm, a
  parametric frame is one variable; a mesh is a redo.
- The optimiser does not know about bolt access, cable routes, tool clearance or
  moulding-in inserts. A human-authored rebuild does.

How to do it:

1. Take the member layout from the `ribs` report and the density projections.
2. Replace each member with the simplest primitive that follows it — a `cube`
   for a rib, a `cylinder` for a strut, a `polygon` + `linear_extrude` for a
   tapered web.
3. Keep every dimension a named parameter; derive positions from the interfaces
   you locked with `part_index.py`, not from the optimiser's coordinates.
4. Add what the optimiser could not know: fillet call-outs, bolt bosses, cable
   pass-throughs, assembly clearance.
5. `assert()` the clearances between the new members and every existing feature.

## Stage 4 — prove the rebuild kept the stiffness

```
topopt compare TOPO_OUT_DIR rebuild.stl --study study.json --out DIR
```

This is not optional, and the numbers below say why. Once the rebuild passes
`compare`, `topopt ccx rebuild.stl --study study.json --repair` re-checks it
with an independent solver and mesh (CalculiX on a Gmsh tetrahedral mesh
rather than the voxel FEA); as of 2026-09-08 this does not yet agree with
`topopt fea` on the same part within the 15% tolerance on most load cases. A
3-level mesh-convergence run (`--converge`, 4/2/1 mm) does not settle it
either — CalculiX itself is not converged at 1 mm (the governing case's
displacement moves 4.5% then 8.9% against a 5% tolerance), so the 2 mm number
quoted in earlier comparisons was mid-series, not a converged answer. Treat
neither solver's number as final (`docs/known-issues.md` A6).

It reads both output layouts. For a `geometry`-schema run it compares against
`*_binary.npy` — the shape that was actually analysed and exported, not a fresh
threshold of the density — and takes the grid origin from the summary, so
`--origin` is not needed. A legacy summary never recorded the origin, so
`--origin` is **required** there and the tool now says so instead of quietly
defaulting to `0,0,0` and reporting 0 % overlap. Sanity check whenever a result
looks wrong: pass the optimum's own STL as the rebuild — it must come back at
100 % overlap and a compliance ratio of 1.00.

### Compare against the right baseline

**Do not compare against the compliance printed during optimisation.** That is a
SIMP number: it includes intermediate-density material that you cannot print.
`topopt compare` instead re-analyses the optimiser's own *thresholded* shape as
solid and uses that. On a measured example the two differed by 36 % (SIMP 30.6
vs thresholded-solid 22.6 N·mm) — enough to make a bad rebuild look acceptable.

### Expect the first rebuild to be much softer

A worked example, cantilever domain, same load case throughout:

| shape | mass | overlap with optimum | compliance | vs optimum |
|---|---|---|---|---|
| optimiser's thresholded shape | 47,491 mm³ | — | 22.6 N·mm | — |
| rebuild A (straight members) | 56,275 mm³ | 46 % | 39.2 N·mm | 1.7× softer |
| rebuild B (polyline members) | 56,275 mm³ | 81 % | 150.5 N·mm | **6.7× softer** |

Both rebuilds were *heavier* than the optimum and both were softer. Rebuild B
matched the optimum's shape far better and was nearly four times worse than
rebuild A. Two lessons follow, and they are the whole point of this stage:

1. **Geometric overlap does not predict stiffness.** Overlap does not care
   *where* the missing material was. Material near the outer surfaces carries
   bending with the square of its distance from the neutral axis; material in
   the middle carries almost nothing. A rebuild can match 81 % of the optimum's
   volume and still have given away the part that mattered.
2. **A skeleton is a lossy description of a structure.** Centrelines plus a
   single width per member lose the varying section, put chords on the medial
   axis rather than flush with the domain surface, and can leave a panel
   unbraced that the optimiser had braced. None of that is visible without the
   FEA.

### When the rebuild is too soft

In order of effect:

- **Move chords out to the design-domain surface.** The extraction reports
  centrelines; the optimiser's material usually runs right up to the boundary.
  Building a chord flush with the surface instead of centred on the skeleton is
  free stiffness.
- **Check for an unbraced panel.** Two parallel chords with nothing between them
  shear like a four-bar linkage. Find the longest run with no diagonal and add
  one.
- **Widen the members that the MISSING panel highlights**, not all of them.
- **Then re-run `topopt compare`.** Iterate until you are satisfied, and record
  where you stopped.

### Two fixes that were tried and did *not* work

Recorded so nobody spends the afternoon again:

- **Raising `filter_mm` from 4 mm to 9 mm.** The idea was that forcing chunky,
  straight members would make the result reproducible. It roughly doubled the
  optimised compliance (30.6 → 53.0 N·mm) and the rebuild was still 5.8× softer
  than its own optimum. Not a fix.
- **Adding gussets at every joint.** Two flat-ended members meeting at an angle
  overlap only in a lens near the inside of the corner, which looked like a
  hinge. Gusseting every node bought 13 % (6.7× → 5.8×). Worth doing, nowhere
  near sufficient.

### Honest status of the extraction

`topopt/members.py` reliably recovers the *topology* — how many members, which
way they run, roughly how thick. Every automatic rebuild attempted so far has
been 5–7× softer than its own optimum, so treat `*_members.scad` strictly as a
**starting sketch for a human**, never as a finished structure. The hand-built
rebuild (rebuild A above) was three times better than either automatic one.

This is exactly why stage 4 is mandatory rather than a formality: the failure is
invisible in the geometry and only the like-for-like FEA exposes it.

Record in the part's `.md`: the load cases, the thresholded-optimum compliance,
the rebuild's compliance and mass, and the safety factor. That is what makes the
claim "this frame is stiff" checkable by the next person.

## Cost

A 30,000-element domain with one load case takes roughly 5 minutes for 40
iterations on this machine. Multiple load cases multiply that. Start coarse
(`--elements 15000 --iters 25`) to check the setup produces a sensible topology,
then refine.

`design.stress` roughly doubles that: it needs one extra adjoint solve per load
case per iteration, plus the failure-mode evaluation. Measured on a 60×20×10 mm
cantilever, 40 iterations: 7.3 s without the constraint, 13.4 s with it.

# v1 — the run that could not be printed

**Kept as evidence, not as a result.** This is the first tall-tripod run
(2026-08-05, commit `8cafb06`). It completed, it reported four honest failures,
and it was superseded because of a fifth nobody was looking for:

> The design space was drawn 300 mm across. The target machine prints 250 mm.
> The optimised shape came out **268 × 232 × 184 mm** and fits a Bambu Lab
> X1-Carbon **in no orientation at all** — not one of the 24. Nothing checked
> until after 3.6 hours of solving, because the only build-volume check in the
> toolkit ran on a finished STL.

The optimiser did nothing wrong. It was asked to fill a space larger than the
printer, and it filled it. That is the whole lesson: **whether the result can
be printed is decided when the design space is drawn, not when the solve
finishes** — so the check now runs at `topopt setup`, in seconds, and this run
would never have started.

See `docs/lessons-learned.md` #42–#44 and `docs/spec.md` §16.4.1.

## Why this model could not simply be shrunk

Raising the camera makes the tripod tip more easily, which demands a wider
footprint, which is what did not fit. Holding the stability of the tripod
actually in daily use (`osmo_tripod`, which tips at 1.014 N) ties the three
together:

    r_foot >= (z_hinge + 65) / 1.72        and       2 * r_foot <= 240

A 100 mm mast needs a 250 mm footprint — exactly the whole plate, with nothing
left for a brim. The current model sits on that line at `r_foot 118`,
`z_hinge 136`, giving an 88 mm mast. The 100 mm figure in the old CAD assert
was not relaxed to make the build pass; the build volume closed the triangle.

## What is here

Only the small files worth citing — the reports, the setup summary, the study
as it was, and three figures. The density fields, STLs and STEPs were dropped:
they describe a shape that cannot be made, and git history has them if anyone
needs to look.

| file | what it shows |
|---|---|
| `report.md` / `report.json` | the verdict: 4 FAIL, 3 UNKNOWN, 7 PASS |
| `tall_tripod_study_extract.json` | threshold candidates; every one fails the mesh check at 2 voxels |
| `printcheck.json` | 7,488 mm³ of support sealed inside the hollow mast |
| `tall_tripod_study_setup.json` | the setup that passed — note it has no build-volume section |
| `tall_tripod_study.json` | the study as it was, at `voxel_mm` 4.0 and `filter_mm` 6.0 |
| `*_topo.png`, `*_setup.png`, `*_fea_3.png` | the shape, the setup, the governing load case |

The three failures that were *not* about size are real and were carried
forward into the current study: the run hit the iteration cap without
converging, every member was exactly 2 voxels thick (`filter_mm` 6.0 at a
4.0 mm voxel is 1.5 voxels — below the floor the grid imposes anyway, so the
filter governed nothing), and support was sealed inside the mast.

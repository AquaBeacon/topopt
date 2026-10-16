> **印刷するファイルは `tall_tripod_selfsupport_print.stl`**（2026-09-05 追記）。
> 平滑化した最適化本体に CAD のカメラ取付頭部（`tall_mount_solid.stl`）をブール結合し、
> スロット・ボア・ポケットを引いた 1 体のメッシュ（`tall_tripod_selfsupport_print.scad` で再生成できる）。
> 結合の前に、最適化本体に含まれる頭部の 4 mm ボクセル塊（CAD 頭部より外に膨らみ、スロット開口を
> 塞いでいた）を z 120 以上の箱で取り除き、study で宣言したネジアクセス空間（`tall_obstacle.stl`）も引いてある。
> z 110〜120 の台座部は脚が取り付くので残す。頭部の切り欠き（スロット 3 枚・ティアドロップのボア・
> ナット/ノブのポケット）が開いていることは `view_head_*.png` で確認した（2026-09-05）。
> メッシュ検査で開いたエッジが 26 本出るが、ブール結合の座標丸めによるもので、スライサーの自動修復で通る。
> 下の表にある `tall_tripod_selfsupport.stl` は finish 段の出力で、4 mm の階段が
> そのまま残るため段ごとに水平面ができ、スライサーが全段にサポートを付ける。**印刷には使わない。**
>
> 造形指示：向きはモデルのまま **+Z 上**（解析した積層方向。変えない）、材料 PLA、
> インフィル 25 %・壁 4、サポートは**頭部の下面だけ**（`topopt overhang` で要サポート 2.9 %＝1,435 mm²、
> 脚・支柱は 45° 以内で自己支持）。スライサーの「Z 距離」を標準にし、頭部下だけ手動でサポートを塗るか、
> 自動サポートの角度しきい値を 40° にする。
>
> 画像：`view_iso.png` / `view_front.png` / `view_side.png` / `view_top.png` / `view_below.png`（OpenSCAD レンダ）、
> `preview_*.png`（設計空間・形・応力・向き）。

# Print this: `tall_tripod_selfsupport.stl`

> ## Half of this is a finished part. Know which half.
>
> The **interface** was re-cut from the source CAD on a 0.50 mm grid, so a feature width is good to within **0.50 mm** instead of 4.0 mm. Not exact — quote that tolerance, do not quote the drawing. The **optimised body** is still the analysis grid: resampling it added no detail it never had, and no mating dimension may be taken off it.
>
> The analysis did **not** see this shape. The FEA ran on the 4.0 mm interface, so stresses in and around it are resolved no better than that grid allows.

> ## The analysis verdict is FAIL.
>
> Printing this is fine for looking at, fitting, and checking the layout. It is not signed off to carry load. See section 3.

## 1. Files

| file | what it is |
|---|---|
| `tall_tripod_selfsupport.stl` | **the mesh to slice** — optimiser body with the interface re-cut from CAD on a 0.50 mm grid |
| `tall_tripod_selfsupport_raw.stl` | what the optimiser produced, on the analysis grid |
| `tall_tripod_selfsupport_smooth.stl` | the same shape, Taubin-smoothed — nicer surface, slightly different dimensions |
| `tall_tripod_selfsupport.step` | solid model for CAD, if you are rebuilding the interfaces (you are) |
| `preview_shape.png` | what the optimiser produced, in section |
| `preview_design_space.png` | what it was allowed to fill — compare against the shape |
| `preview_stress.png` | safety factor on the **governing** load case — LC3sw |
| `preview_orientation.png` | how it sits on the plate, and what overhangs |

Threshold **0.40** of the density field.

_Why this one: lightest candidate that meets every check on every load case_

## 2. How to print it

| | |
|---|---|
| orientation the analysis assumed | model **Z** axis up |
| fits the build volume | yes |
| size as modelled | 200.0 × 168.0 × 144.0 mm |
| lowest-support orientation | +Z up |
| support volume in this orientation | 256192 mm³ |

**Do not reorient it to save support.** The material is anisotropic and the analysis was run with the axis above pointing up; laying the part on its side moves the layer planes and the safety factors no longer apply to it. If another orientation is wanted, change `build_axis` in the study and re-run.

## 3. What is still wrong with it

These failed the stop conditions (spec 18). They are listed in full, with the fix, in `../03_optimization/report.md`.

- **The density field converged (not merely out of iterations)** — termination: max_iterations
- **Mesh convergence confirmed** — TOO COARSE (voxel FEA only: 2.8 (thin 11.31 mm) / 4.0 (typical 16.00 mm) vs required >= 3 elements across the thinnest section). A three-mesh convergence study still has not been run.
- _not checked:_ Load/reaction balance within 1 %
- _not checked:_ Final shape is watertight

_This list covers the stop conditions the report checks. **How much support the part needs is not one of them** -- that is judged by `topopt print <stl>`, which applies a 15 % overhang-area limit and is worth running before you slice._

## 4. Where the rest of it is

| | |
|---|---|
| what was assumed (loads, material, machine, what was fixed) | `../01_assumptions/assumptions.md` |
| the design space and its validation | `../02_setup/` |
| the full analysis and every number | `../03_optimization/report.md` |


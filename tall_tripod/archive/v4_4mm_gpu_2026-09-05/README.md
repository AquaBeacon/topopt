# tall_tripod v4 を 4 mm で回した記録（2026-09-05、GPU マルチグリッドソルバ）

`03_optimization/` の記録（10 mm）と同じ study を、`--voxel 4 --iters 80` で `topopt run` したもの。
ソルバ高速化（spec Phase 10）の検証として回した。**印刷するものではない。**

- 101,808 要素・6 ケース・80 反復。最適化 342 s（480 解、CG 計 10,272 反復）、全段 395 s
- 判定は 10 mm と同じ FAIL 2 件：未収束（`max_iterations`、`dshape` 0.0136）と
  メッシュ判定（最薄部材 8 mm = 2 ボクセル、グリッド律速）。どちらも study 側の課題（known-issues G1 / G4 / G6）で、
  いまは 1 run が数分なので反復数や `filter_mm` を振って潰せる
- 出力先が `--out` だったので `01_..04_` の 4 段階構成ではない

# v5 探索 run（4 mm・80 反復・`--quick`）— 2026-09-08

`topopt run tall_tripod/tall_tripod_study.json --model tall_tripod --voxel 4 --iters 80 --quick`
の成果物一式。README 本文が引用している数字（最適化 406 秒、finish 154,036 三角形、
平滑化による体積変化 0.00 %、`min thk 8.00*` = 2 ボクセルで解像度検査 FAIL）はここのもの。

`--quick` なので **CalculiX とスライサーは走っていない**（`run_summary.json` の
`valid_through` は setup → opt → extract → print → assumptions → publish）。
`03_optimization/reanalysis/` は v4 時代に手で回した ccx/fea の残り。

この状態を残したまま、同じ study を 3 mm・`--quick` なし・`--converge` つきで
回し直したものがモデル直下の現行版。

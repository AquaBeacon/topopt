# archive — 上書きされた実行結果

消していない理由は一つ。`osmo_tripod.md` と `../../README.md` の主張の
**根拠がここにある**から。数字を「測った」と書いてある以上、測った出力を捨てると
主張が根拠なしの断言に変わる。

| ここにあるもの | 何の根拠か |
|---|---|
| `fea_v1/` （voxel 1.0 / 29,713要素・初版の細脚） | `osmo_tripod.md` §3 の「初版(細脚)比」列 |
| `fea_v2/`, `fea_conv/` （1.0 mm → 0.67 mm、同じ9ケース） | §3 の「メッシュ収束チェック PASS」。**収束は2メッシュの対でしか示せない** |
| `fea_w30/` （根元幅 30 mm 版・voxel 1.0） | `README.md` 知見5「19→30 mm で質量+13.5%・全ケース改善」 |
| `fea_drop/` （4ケース版） | §3 落下節の途中結果。最終値は `../fea_drop_final/` |
| `cmp_coarse/`, `cmp_coarse2/` | §4 再構築検証の初回。採用値は `../cmp_eq/` |
| `study_v1.json` (9ケース) | `fea_v2/` と `fea_conv/` を出した荷重定義 |
| `study_drop_4case.json` | `fea_drop/` を出した荷重定義 |
| `osmo_tripod_v1_narrow.stl` | 初版の形状そのもの |
| `*_w30_*.png`, `preview_w30.png`, `clearance_w30.png` | 30 mm 版の外観・干渉チェック |
| `fea_drop.log` | 落下解析の実行ログ |
| **`v2_tight_fits/`** | **すきまが狭くて実物にケースが入らなかった v2 一式**（2026-08-04 に v3 へ改訂）。`osmo_tripod.stl` / `.3mf` は**実際に印刷したファイルそのもの**、`mount_only.stl` / `mount_cuts.stl` / `design_space.stl` はその最適化入力、`gd_mount/` は §4.2 の v2 結果、`gd_mount_study_RESULT_baseline.json` はその要約（spec §21 Phase 0 の回帰基準値）。`osmo_tripod.md` §1「入らなかった話」と §3 の v2 列の根拠 |

アーカイブした JSON の `part` は `../../out/...` に書き換えてあるので、
ここから直接再実行できる:

```bash
topopt fea out/archive/study_v1.json --out /tmp/recheck --voxel 1.0
```

**現行の根拠は archive の外**にある: `../fea_v3/`（§3 本表・v3・131,674要素）、
`../fea_drop_final/`（§3 落下）、`../gd/`（§4.1）、`../gd_mount/`（§4.2・v3）、
`../gd_mount_v2grid/`（§4.2 の**voxel を揃えた対照実験** — v3 形状を v2 の 1.6065 mm で回したもの）、
`../topo_coarse/` `../topo_eq/`（§4 表）、`../cmp_eq/`（§4 再構築検証）。
`../fea_final/` は §3 の **v2 列**の根拠として残す（v3 との比較は同じ voxel 0.67 で取った）。

> `v2_tight_fits/gd_mount/` の `*_setup.*` だけは v3 のもの。v2 の結果ファイル
> （`*_final.stl` / `*.npy` / `gd_mount_study.json` / `*_topo.png` / `*_fea_*.png`）は
> v2 のまま。退避が v3 の `setup` 実行より後だったため。

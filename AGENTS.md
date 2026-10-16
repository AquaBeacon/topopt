# AGENTS.md

> このリポジトリでコードを書く**すべての AI エージェント（Claude Code / Codex / Cursor 等）共通の作業規約**。
> 作業前に読むこと。Claude Code は `CLAUDE.md` 経由で読み込む。

## ドキュメントの役割

- **README.md**（直下・人間向け）：概要・構成・セットアップ・使い方。AI 専用手順は書かない。
- **AGENTS.md**（このファイル）：作業規約・コマンド・検証ループ・ドキュメント同期規約。
- **CLAUDE.md**（直下）：Claude Code 固有の補足のみ（このファイルを読み込む薄いラッパ）。
- **docs/**：
  - [`docs/spec.md`](docs/spec.md) — 仕様と**受け入れ条件＝合格ライン**。実装の契約書。
  - [`docs/workflow.md`](docs/workflow.md) — 設計手順ノート。ツールを使って実際に部品を設計する手順。
  - [`docs/architecture.md`](docs/architecture.md) — 二つの解析経路、モジュール地図、移行方針。
  - [`docs/lessons-learned.md`](docs/lessons-learned.md) — 過去に実際に踏んだ失敗。規則の根拠。
  - [`docs/known-issues.md`](docs/known-issues.md) — 分かっているのに直っていないことの台帳。
  - [`docs/README.md`](docs/README.md) — 索引。
- **topopt/docs/**（英語・実装準拠のリファレンス）：
  - `design-space.md` — 設計空間モデルの全仕様・全ノブ。
  - `topology-optimization.md` — 最適化結果を部品にするまでの4段階。

作業前に：何を作るかは `docs/spec.md`、どこを触るかは `docs/architecture.md`、
その挙動の理由は `topopt/docs/` と `docs/lessons-learned.md`。

## Tech Stack

| レイヤー | 技術 | 状態 |
|---|---|---|
| 言語 | Python ≥ 3.10 | — |
| 必須依存 | numpy ≥ 1.24, scipy ≥ 1.10 | 導入済 |
| 任意依存 | matplotlib（`[plots]`）, scikit-image（`[mesh]`） | 導入済 |
| 任意依存 | torch（CUDA ビルド）: GPU ソルバ | 導入済（このマシン）。無ければ numpy で動く |
| 外部ツール | CalculiX 2.23（dhondt.de の Windows ビルド） | 導入済（2026-09-08）。`C:\Users\ryupc\Tools\calculix_2.23_4win\ccx_static.exe`（DLL 不要）。`TOPOPT_CCX` または `~\Tools` 検索で検出 |
| 外部ツール | Gmsh 4.15.2（公式 zip、実行ファイル） | 導入済（2026-09-08）。`C:\Users\ryupc\Tools\gmsh-4.15.2-Windows64\gmsh.exe`。`TOPOPT_GMSH` または `~\Tools` 検索で検出。**同日：`topopt ccx` は渡す前に既定で表面を分割し（`ccx.refine_stl`、Gmsh は渡された STL の三角形をそのまま境界メッシュにするため）、3D アルゴリズムは HXT（`Mesh.Algorithm3D = 10`。旧 Delaunay は4万枚の殻で12分待っても終わらなかった）** |
| 外部ツール | FreeCAD | **未導入**。`external.py` が検出し終了コード 3 |
| 外部ツール | スライサー：Bambu Studio 02.05.00.66 | 導入済（2026-09-08）。`C:\Program Files\Bambu Studio\bambu-studio.exe`。**PATH には無い**。`TOPOPT_SLICER` または `C:\Program Files\Bambu Studio` 検索で検出。内蔵プリセットは実行ファイル隣の `resources/profiles`（`TOPOPT_SLICER_PROFILES` で上書き）。**CLI は `inherits` を解決しないので `slicer.py` が継承鎖を畳んでから渡す**。OrcaSlicer は同系統の CLI なのでそのまま動く想定（未検証）、PrusaSlicer はプリセット体系が別（spec §24） |
| 外部ツール | OpenSCAD | 導入済（`C:\Program Files\OpenSCAD\openscad.exe`。**PATH には無い**） |
| テスト | pytest（`topopt/tests/`）。695件 | 導入済 |
| パッケージ | pip + setuptools（`topopt/pyproject.toml`） | 導入済 |

単位は内部一貫して **mm / N / MPa**。

## Commands

すべてリポジトリ直下（`topopt/`）から。

```bash
pip install -e "topopt[all]"          # 依存（numpy/scipy + matplotlib + scikit-image + pyyaml）
pip install -e "topopt[test]"         # pytest（回帰テスト用。実行時には要らない）
python -m topopt --version            # 導入確認
python -m topopt --help               # コマンド一覧

# 回帰テスト。既定で slow を含む全部が十数秒で終わる
cd topopt && python -m pytest         # 695件。約5分
cd topopt && python -m pytest -m slow # 記録済み run の要約と基準値の照合
python topopt/tests/smoke.py          # pytest 抜きの自己診断（pytest 自体を疑うとき）
python topopt/tests/make_baseline.py  # 基準値の再生成。動いた理由を説明できてから

# 代表的な実行（osmo_tripod が実例一式）
python -m topopt assumptions osmo_tripod/gd_mount_study.json --out /tmp/chk  # 前提条件ページ
python -m topopt setup   osmo_tripod/gd_mount_study.json --out /tmp/chk   # 数秒。設定検証＋プレビュー
python -m topopt opt     osmo_tripod/gd_mount_study.json --out /tmp/chk --iters 25 --elements 8000
python -m topopt fea     osmo_tripod/study_main.json --out /tmp/chk
python -m topopt fea     osmo_tripod/study_main.json --out /tmp/chk --converge  # [2H,H,H/2] 3段階でケースごとに収束判定
python -m topopt print   osmo_tripod/out/osmo_tripod.stl --printer X1C --load-axis 1,0,0
python -m topopt overhang osmo_tripod/out/osmo_tripod.stl

# 実スライス（Phase 5、2026-09-08）。造形時間・材料量・サポート量はここからしか出ない
python -m topopt slice osmo_tripod/out/osmo_tripod.stl --out osmo_tripod/out/slice
python -m topopt slice part.stl --out DIR --printer X1C --layer-mm 0.2 --material PLA \
    --supports auto|none|tree --orient keep|auto
# --orient keep が既定（そのままの向きでスライスする）。auto はスライサーに向きを変えさせる——
# 層の向きが変わるので、build_axis に紐づいた直交異方性解析がその物体を記述しなくなる
python -m topopt run study.json --model DIR --no-slice   # スライス段を飛ばす
python -m topopt compare /tmp/chk rebuild.stl --study osmo_tripod/gd_mount_study.json

# Phase 2-7 で足したもの
python -m topopt opt osmo_tripod/gd_mount_study.json --out /tmp/chk --isotropic  # 従来の等方モデル
python -m topopt extract /tmp/chk --study osmo_tripod/gd_mount_study.json --out /tmp/chk
python -m topopt report  /tmp/chk --study osmo_tripod/gd_mount_study.json
python -m topopt report  /tmp/chk --study osmo_tripod/gd_mount_study.json --csv  # report_cases.csv + report_checks.csv も書く
python -m topopt run     tall_tripod/tall_tripod_study.json --model tall_tripod   # 4段階フォルダ
python -m topopt validate ccx_summary.json report.json manifest.json   # 出力 JSON を outschema.py の表で検証。0=有効/1=無効/2=読めない
python -m topopt ccx --check                       # Gmsh / CalculiX の導入状況
python -m topopt ccx osmo_tripod/out/osmo_tripod.stl --study osmo_tripod/study_main.json \
    --out /tmp/ccx --repair                         # Gmsh でメッシュを切り CalculiX で再解析
python -m topopt ccx osmo_tripod/out/osmo_tripod.stl --study osmo_tripod/study_main.json \
    --out /tmp/ccx --repair --converge               # 上に3段階メッシュ収束を追加
# --repair は入力 STL が非閉曲面（OpenSCAD 2021 出力など）のとき使う。要 pip install pymeshfix

# 2026-09-08 の推奨形（表面分割・体積マッピング・整合荷重は既定。座屈・振動は別デッキ）
python -m topopt ccx osmo_tripod/out/osmo_tripod.stl --study osmo_tripod/study_main.json \
    --out out/ccx --repair --buckle --frequency \
    --voxel-fea out/fea_conv_auto/osmo_tripod_fea.json   # vs_voxel_fea_cases に突き合わせ結果
# 個別に効く旗：--map volume|surface（既定 volume）、--load-split consistent|equal（既定 consistent）、
# --ground-tol MM、--exclude-mm D（最悪要素判定から固定節点近傍を除外）、--no-refine（表面分割を止める。
# その数値を収束スタディとして引用しない）

# ボクセルサイズを変えるとき。**setup が実際に1回解いて 80 反復の所要を出す**
python -m topopt setup tall_tripod/tall_tripod_study.json --out /tmp/chk --voxel 5 --iters 80
python -m topopt setup tall_tripod/tall_tripod_study.json --out /tmp/chk --no-estimate  # 飛ばす
python -m topopt underwater-loads osmo_tripod/out/osmo_tripod.stl --depth 10000 --velocity 1.0

# ソルバの選択（既定は auto: GPU が見えれば GPU、無ければ CPU、いずれもマルチグリッド前処理。
# GPU では V サイクルを float32 で回す mixed32 が既定（2026-09-08）。float64 に戻すなら :mg64）
python -m topopt fea study.json --out DIR --solver cpu:jacobi   # 旧ソルバで突き合わせ
python -m topopt run ... --solver cuda
python -m topopt run ... --solver cuda:mg64                     # 前処理まで float64 で

# 探索は --quick（2026-09-08）。しきい値 0.5 のみ・ccx / slice なし・平滑 STL / STEP / finish なし。
# 設定検査・最適化・最終 FEA・造形性検査は走る。反復数は変えない（減らすと継続スケジュールが
# 圧縮されて別の最適化になる。child_jaw_gate は 40 反復で NOT CONNECTED、80 で回復）。
# 採用案は後段を実行する。study/グリッド/設定が同一なら保存密度を再利用してoptを繰り返さない。
# 元のrun_summary/manifestを保存し、--only extract,ccx,print,slice,report,publish（--quickなし）等で仕上げる。
# 条件を変えた場合は新しい出力先で再opt。完成形状と収束の確認は別途必要。
python -m topopt run study.json --model DIR --quick
python -m topopt run study.json --model DIR --quick --voxel 4     # 粗くして速く（取合いが 1 ボクセルに乗る範囲で）

# 自己支持拘束は既定 ON（design.overhang）。旧挙動で回すなら study の design に "overhang": false
# サポートを条件つきで許す: "overhang": {"max_support_mm": 20, "allow_support": ["領域名"]}

# 応力制約つき最適化（design.stress、既定は OFF。Phase 12）
# study の design に "stress": true（または {"safety_factor": 3.0, ...}）を書くと有効になる。
# OC 法では扱えないので optimizer は自動で mma になる。--isotropic と併用すると InputError（終了コード2）
python -m topopt opt osmo_tripod/gd_mount_study.json --out /tmp/chk --iters 25   # design.stress を書いた study で
python -m topopt opt osmo_tripod/gd_mount_study.json --out /tmp/chk --isotropic  # stress ありの study にこれを付けると拒否される

# OpenSCAD は PATH に無いので絶対パスで呼ぶ
"/c/Program Files/OpenSCAD/openscad.exe" -o tall_tripod/02_setup/tall_preserve.stl \
    -D 'part="preserve"' tall_tripod/tall_tripod.scad
```

> **モデルは1つにつき1フォルダ。共用しない**（spec §17.5）。中は実行順に
> `01_assumptions/` → `02_setup/` → `03_optimization/` → `04_print/`。
> `04_print/` には印刷するものしか置かない。`tall_tripod/` が実例。
> `osmo_tripod/` は移行前の構成のまま（`out/` の記録済み run が回帰の基準値）。

> **CalculiX 経路は 2026-09-08 に実行検証済（それまでは配線しか確認していなかった）。**
> `topopt ccx` はケースごとの結果（`cases`）に、最大変位・安全率（p99.5・最悪要素）・
> ホットスポット・釣合い誤差・反力モーメント・コンプライアンス・主応力/Tresca・
> ひずみエネルギー密度・破壊指標・座屈係数を出す。荷重区分（`load_class: normal|abnormal`、
> 要求安全率は通常＝study の値、異常時＝1.5）もケースごとに判定する。run 全体では
> 質量・重心・慣性（`mass_properties`）とメッシュ品質（`mesh_quality`）を出す。
> 全ステップに `*BOUNDARY, OP=NEW` / `*CLOAD, OP=NEW` を付けて拘束・荷重がステップ間で
> 持ち越らないようにしてあり、座屈・振動は静解析と別デッキ（1つに混ぜると1ケースあたり
> 12ステップの `.frd` になり対応が取れないため）。メッシュを切る前に入力 STL の
> シェルを検査し（`geom.shell_report`）、非閉曲面（OpenSCAD 2021 出力など）なら
> 終了コード 2 で拒否する——`--repair`（`pip install pymeshfix` が要る）で修復できる。
>
> **続報（同日、2回目のラウンド）：known-issues A6 の食い違いの原因はメッシュだった。**
> Gmsh は渡した STL の三角形をそのまま境界メッシュにするので、粗い入力 STL（2048枚）は
> 「4/2/1 mm 収束スタディ」を回しても境界面が2048枚のまま変わらず、ある脚支持面は
> STL 頂点9個だけしか掴んでいなかった。既定で表面を分割してから渡すようにし
> （`ccx.refine_stl`、`--no-refine` で無効化）、節点写像もボクセル経路と同じ体積マッピング
> （`--map volume`、既定）に揃えたところ、osmo_tripod・CalculiX 4 mm殻 vs ボクセル FEA 1 mm
> （`fea --converge` の最細）は**6ケース中6ケースが変位15%以内（最悪5.4%）**まで縮まった。
> **それでも `fea --converge` を回すと、この1 mmを含め全メッシュ段階が未収束**
> （p99.5応力が4/2/1 mmで13〜32%動く）——**一致したことと収束したことは別**で、
> 上の数字を「決着した答え」として引用しない。`--voxel-fea PATH` で突き合わせ結果を
> `vs_voxel_fea_cases` に残せる。
> **最悪要素の安全率が p99.5 の 1.5 倍を下回るケースは合格にしない**（spec §15.1。
> `--exclude-mm D` で固定節点近傍だけ除外できる、既定オフ）。
> **接地拘束（`{"contact":"ground"}`）も CalculiX 側に実装した。** tall_tripod を8 mmで
> 再解析すると6ケース全PASSだが、同じ形の10 mmボクセル FEA とは6ケース中1ケースしか
> 変位15%以内に収まらず、比は1.09〜1.65——**1〜3セルしかない部材には10 mmボクセル格子が
> 粗すぎて硬く出る。**

> **造形時間・材料量・サポート量は `topopt slice` からしか出ない（2026-09-08）。**
> メッシュからは導けない。`slice.json`（schema `topopt/slice/1`）に
> `print_time_s` / `filament_g` / `filament_mm3` / `support_filament_g` / `support_mm3` /
> `layers` / `bed_fits` が入る。実測（X1C・0.20mm Standard・Bambu PLA Basic・`--supports auto`）：
> `osmo_tripod.stl` 36分39秒・18.25 g・サポート 0 g・162層／
> `tall_tripod_study_t40.stl` 4時間7分42秒・169.53 g・**サポート 48.58 g（28.7%）**・1035層。
> **サポート量はスライサーが報告しない。** 3mf のどこにも無いので G-code の
> `; FEATURE: Support*` を積算して出しており、**その積算がスライサー自身の総フィラメント長を
> 再現したときだけ報告する**（実測 0.0000% 差。再現しなければ null ＋ 理由）。
> 罠が3つある：CLI は `inherits` を解決しない（葉だけ読んでベッドが 200×200×100 に落ちる）、
> 畳んだプリセットは `"from": "system"` でないと拒否される、押し出し量は `G2`/`G3` の
> 円弧にも乗る（数え落とすと総量の 86.6%）。機械開始 G-code は絶対 E（`M83` は後から出る）。
>
> **スライサーの失敗を構造合格・構造不合格のどちらにもしない**（spec §19.5）。
> `topopt report` はスライサーを停止条件に入れず独立の節に出す。`slice.json` が無ければ
> UNKNOWN と明記する。**`slice.json` の `pass` は「スライスが通ってベッドに載った」だけの意味で、
> §13.3 の合否ではない**——§13.3 の5条件は「サポートがどこに付くか」を問うており、
> スライサーは「どれだけ付くか」しか言わないので**全部 UNKNOWN**。位置の判定は `printcheck.json`。
> **`printcheck.json` の `support_volume_mm3`（部品空間の体積）と `slice.json` の
> `support_mm3`（押し出す φ1.75 mm フィラメントの体積）は別の量。** 並べて比較しない。

> **設計空間を描いたら、解く前に印刷範囲を確認する。** study に `printer` を宣言すると
> `topopt setup` が数秒で検査する。設計空間がプリンターより大きければ、
> 最適化が何を出しても印刷できない——**それは解いた後に分かることではない**。
> 飛ばして 3.6 時間を捨てた実例が `tall_tripod/archive/v1_unprintable/`。

> **ボクセルサイズは解く前に値段を聞ける。** `topopt setup` はグリッドを作ったあと
> **実際に1回 FEA を解いて**、`--iters` 反復ぶんの所要を出す。式で外挿しない——
> 記録済み run 6本はセルあたりコストが 2 倍以上ばらついていて、セル数では説明できない
> （`tall_tripod` 17,902 セルで 16.5 s/反復/ケース、`gd_mount` 32,744 セルで 9.3）。
> 校正の実測は `topopt/src/topopt/runtime.py` の表にある。
> **ただしこの 16.5 s / 9.3 s は旧ソルバ（Jacobi 前処理）の値。** マルチグリッド前処理の
> コストはこれより一桁以上低い。プローブで値段を聞くやり方自体は変わらないが、
> 見積もり行はいまどのバックエンドで測ったかを名前で出す。

> **ソルバの速さは反復数で決まっていた。** 4 mm の tall_tripod（0/1 の密度場）は
> 旧 Jacobi 前処理付き CG で1ケースあたり 12,044 反復かかっていた。
> 幾何マルチグリッド前処理に替えると同じ答え（コンプライアンス相対差 1e-6 以下）に
> 74 反復で届く。1回あたりの解き時間は 540 秒 → GPU で 2.2 秒・CPU でも 20 秒。
> **解くのが遅いと感じたら、まず出力の `cg_iterations` を見る。** 要素数を疑う前に。

> **最適化の結果は既定で自己支持になる（2026-09-05）。** 立方体は、直下＋面内4近傍
> （45° の円錐）に材料があるときしか材料を持てない。これを層ごとに、
> **プレート（＝設計空間の最下層。グリッドの端ではない）から積み上げるように**判定する。
> `report` の `self_supporting` 条件は 0 でなければならない。preserve の下面
> （`on_preserve_voxels`）は対象外——CAD の面取りの仕事。三つの失敗を踏んで直した：
> (1) プレートを配列の端に取ったら、tall_tripod はグリッドが設計空間の下 20 mm から始まるため
> 全域が「空中」になり、フィルタが30反復で体積を0にした。
> (2) 補正していない p-norm smooth max は層ごとに4%成長するので、5支持の一様場が
> プレートで0.45・8層上で0.6と読まれ、しきい値後に足が浮いた（16 mm・接地0 mm²）。
> 中立点を射影しきい値0.5に揃えて直したが、代償として0.5未満の場が層ごとに減衰する
> （0.04で14%/層）ので最初の1/3反復で混ぜ込む（`ramp`）。
> (3) smooth min は真の最小より最大0.005高いので、1〜2ボクセルが支持なしでしきい値を
> すり抜ける——だから射影後の形を印刷可能な部分集合へさらに射影する（`prune_unsupported`）。

> **取合いは解析グリッドから出さない。** study に `finish` ブロックを書くと、
> 出荷する形は「最適化の結果（解析グリッドのまま）＋ CAD の preserve を足し戻し＋
> CAD の穴を引く」で作られる。だから**ボクセルより細かい取合いが消えない**。
> **ただし「CAD の寸法どおり」ではない。** 量子化の刻みが解析ボクセルから
> finish ボクセルに変わるだけで、**特徴の幅は finish ボクセル1つぶんまで狂う**
> （両面が独立に丸まり、cut は外側に丸まるため）。実測（解析 10 mm / finish 0.5 mm）：
> 中央爪 2.40 → 2.00、溝 3.80 → 4.00、外側の爪 4.00 → 4.00、ボルト穴 Ø5.60 → Ø5.60。
> **`feature_tolerance_mm` を引用すること。図面値を引用しない。**
> **解析はその形を見ていない。** FEA が見たのは粗い取合いのままで、
> `PRINT.md` はそれを明示する。
> **2026-09-08：finish 段は preserve ボディと同名の `add` でそのボディの解析ボクセルを捨てて
> CAD に置き換え（触れていた 1 ボクセルは橋として残す）、最適化した材料だけを細グリッド上で
> 平滑化する**（`smooth_mm`、既定 auto＝**解析ボクセル1つ**。置き換えなかった preserve・
> 接地層は 1、obstacle は 0 に固定、しきい値は体積一致で選ぶ）。旧 `*_smooth.stl` のように
> preserve ごと丸めない（lessons-learned #90）。出荷は等値面 `_finish.stl`、ボクセル版は
> `_finish_voxel.stl`。**`--quick` は finish を飛ばす**ので、形を見たいときは
> `topopt extract DIR --study study.json --out DIR` を別に回す。
>
> **2026-09-09：CAD ボディはガウスに入れない。占有率（occupancy）として合流させる。**
> 「CAD のセルを 1 に固定」では**面が動くのを止められない**——CAD が自分をぼかすので、
> 等値面は CAD 面の外に出る。実測（tall_tripod v5 出荷 STL、解析 3 mm・finish 1.0 mm・σ 1.5 mm）
> ナット側の爪の外面が 11.00 → 11.34 mm、CAD 頭部 322 mm³ が丸め落ち、**公称面ちょうどで
> 終わっていたナットポケットに膜**が張って締結できなくなった（lessons-learned #91）。
> いまは `finish.occupancy()` が add / cut を細分割サンプリングして「セルをどれだけ埋めるか」を
> 出し、`smooth_field` はガウスを**最適化した材料だけ**にかけて `max(ガウス, 占有率 + (level-0.5))`
> で合流する。`+ (level-0.5)` があるので**どのしきい値でも等値面は CAD 面に乗る**。
> **精度は `info.smooth.cad_tolerance_mm`（= finish ボクセル / `occupancy_samples`、既定 3）で、
> 全体の `feature_tolerance_mm`（finish ボクセル 1 つ）とは別の量**——後者はボクセル版の
> `_finish_voxel.stl` と最適化した本体に、前者は等値面 `_finish.stl` の CAD 面に効く。
> `smooth_mm: false` の study には占有率の経路が無いので、CAD 面は finish ボクセルに量子化される。
>
> **2026-09-09（同日、2回目）：`auto` は解析ボクセルの半分ではなく1つ。** 半分では階段が
> 消えず、階段を丸めた**数珠つなぎの瘤**になる（解析セル1つにつき瘤1つ）。利用者の言葉では
> 「トポロジー最適化した部品がかくかくしている」。1ボクセルにすると瘤は消え、殻は水密・
> 1ボディのまま、体積も一致（±0.00 %）。1.5ボクセルはそれ以上滑らかにならず、
> 殻検査が open edge と呼ぶ継ぎ目が出る（lessons-learned #93）。
>
> **`04_print/` に `*_smooth.stl` を置かない（finish 段が走った run では）。** これは粗い密度場
> **全体**へのガウスで、**preserve ごと丸める**——tall_tripod では爪が団子になりナットポケットが
> 塞がる。03_optimization には残す（`topopt ccx` が再解析するのはこれ）。「print this」の
> フォルダに `smooth` と付いたファイルがあると、そちらが良い版だと読まれる。
>
> **出荷する形の絵を出す（`04_print/preview_print.png`）。** ほかの図は全部「解析グリッド上の
> 場」の絵で、**表面がどうなっているかは1枚も映していなかった**。フラットシェーディングで
> 3方向。面の粗さが見えることが目的なので、スムーズシェーディングにしない
> （`meshout.write_mesh_preview`）。
>
> **2026-09-09（3回目）：接地面・接合部・出力の丸め**（lessons-learned #94）。
> **接地面は「材料の終わり」ではなく「切断面」**なので、畳み込みの前にプレートの下へ材料を
> **鏡像**する（パディングで作る。グリッドがプレートちょうどから始まることがある）。
> セルごとに 1 で固定すると**解析グリッドの footprint が固定され**、滑らかなドームが
> 1 セル厚のギザギザのスカートの上に立つ。
> **接合部だけ**は「両方のボディを含むぼかし」に寄せる（重みは最適化材料自身のぼけ値）。
> CAD をぼかしから外した副作用で支柱が着地直前に細るのを、CAD 面を動かさずに直す唯一の形。
> **範囲は集合で書く**——「両方のボディから `bridge_mm` 以内」に限る（`blend_zone`）。
> 重みの減衰まかせにすると 2σ の全方向に効いて **CAD の周りにぐるりと襟**が育ち、
> 「部品が座布団の上に載っている」形になる（lessons-learned #95）。
> **`attach` の非接触殻は finish 段が埋めてよい**（`bridge_mm`、既定＝解析ボクセル1つ）。
> 取合いが土台より広いと差分は必ず宙に浮く（tall_tripod で全周 2.00 mm）。
> **ただし埋めてよいのは殻だけ**——利用者が宣言した obstacle と、`cut` の周り
> （公称面で終わる穴が膜で塞がるため）には入らない。
> **書き出す前に頂点をセルの 1e-4 に量子化する。** STL の角は float32 で、100 mm 付近の刻みは
> 約 8e-6 mm。同じ点が別の float32 に落ちて、閉じているはずの殻に穴が開く（実測 3 本）。
> **殻検査の公差を緩めて通さない。**

> **finish グリッドは解析ドメインではない（2026-09-10）。** `add` / `cut` は CAD なので、
> **解いた設計空間の外に出ることがある**。出たぶんは以前**黙って切られていた**
> （tall_tripod v7：高さ 84.5 mm の取合いが、取合いの 40 mm 上で終わるドメインで上半分を失った）。
> いまは `finish.cover_pad` が**解析ボクセル単位で**グリッドを広げ、
> `info.grid_grown_voxels` に量を出す。**0 でない run は、FEA が一度も見ていない mm を
> 出荷している**——報告にそう書く。
>
> **完成ソリッドを `add` に入れるなら、finish ボクセルはその穴の公差から決める。**
> `cut` は最後に引かれるので穴が**外側に**丸まる（大きめに出る）が、`add` の中の凹みは
> 占有率の等値面なので**内側に**丸まり、閉じた奥の隅にはセル1つぶんの内 R が付く。
> 実測（tall_tripod v7、M5 ナット座 二面幅 8.20）：finish 1.0 mm で口 8.12・
> 深さ 3.65 mm で **7.62**（ナットが入らない）。**0.5 mm にすると 3 深さすべて 8.12 で
> ソース STL と完全一致**。`cut` で切っていた頃の実績値をそのまま使わない（lessons-learned #97）。
>
> **`slice`・造形範囲の表・`preview_orientation.png` は出荷する形を測る（2026-09-10 に修正）。**
> それまでは `outputs["stl"]`＝**解析グリッドの本体**——publish が `*_raw.stl` と
> 名付けているファイル——を測っていた。実測（tall_tripod v7、同じ形・同じプリセット）：
> raw 6h25m / 188.89 g / サポート 59.20 g に対し、出荷 **7h14m / 303.25 g / サポート 139.13 g**
> （どちらも v7 の形。解き直した v8 は 6h53m / 294.76 g / サポート 130.51 g）。
> **`ccx` だけは raw のまま**（FEA が見た形を別ソルバで解き直すのが仕事なので）。
> なお AGENTS.md 上段に載っている `tall_tripod_study_t40.stl` 4h07m・169.53 g は
> **raw を測った値**で、出荷形状の値ではない（lessons-learned #98）。
>
> **`preview_print.png` が無い run がある。** 40 万三角形を超えると
> `write_mesh_preview` は描かない（90 万枚は 284 秒かかり、深度ソートも破綻する）。
> そのとき**前の run の絵は消される**——残すと「絵を見ろ」と言われた読者が別の形を見るため。
> PRINT.md に消したと書いてある（lessons-learned #99）。

> **preserve には「どの面で繋いでよいか」を言わせる（`attach`、2026-09-08）。**
> 既定は設計空間が取合いの全面に触れていて、最適化はどの面にも材料を生やす（tall_tripod v4 は
> 頭部の側面・上面まで材料が回った）。`"preserve": {"head": {"stl": ..., "attach": ["-Z"]}}` で
> それ以外の表面に 1 ボクセル（`attach_standoff_mm`）の非接触殻が付き、setup 以降 obstacle として
> 扱われる。面は「ボディ自身の面から一歩出たセル」、region は「ボディに接するセル」に切り詰め。
> 届かない面・空リストは setup エラー。プレビューでは橙。実物の部品を作る study では書く
> （lessons-learned #89）。

> **`topopt opt` の進捗行は5反復ごとにしか出ない。** 1行ごとだと思って速度を計算すると
> 5 倍遅く見える。実際にそれで約 3 時間の run を「14 時間」と誤判定して打ち切り、
> その判断を「実測」として docs に書いた（lessons-learned #47）。
> **速度を口にする前に出力間隔を確認する。**

> **既定は直交異方性になった（Phase 2）。** 記録済みの osmo_tripod 結果は全部
> 旧・等方2定数モデルで解かれているので、**回帰の比較には `--isotropic` を付ける**。
> 付けずに比べると、材料モデルの違いを実装の差だと読み違える。
> 実測：`--isotropic` は記録済み run を34反復すべてで完全一致（相対差 0.000e+00）。

> **pytest が拾えるのは「前と同じ数字か」だけ。** osmo_tripod の回帰テストは
> 取合い寸法・サポート面積・setup 体積を基準値と突き合わせるが、
> **その基準値が正しいことは保証しない**（v2 は全部 green のまま実物に嵌まらなかった）。
> 変更のたびに該当コマンドを実際に osmo_tripod の study で回し、結果を見ること。

> **応力制約（`design.stress`、Phase 12）は「体積率と剛性だけで形を決める」を外すノブ**で、
> 既定は OFF。有効にすると OC 法は使えず（体積制約1つしか扱えない）、`optimizer` は自動で
> MMA になる。**要求安全率は荷重ケースごと**（2026-09-08）——`load_class: "abnormal"` の
> ケースは 1.5、それ以外は study の `safety_factor`（spec §15.1 と同じ規則）。
> `load_class` を書いていない study は挙動不変。
> MMA は目的関数の対数を見るので、`generative.optimize` は毎反復目的関数を
> 1へ再スケールする——これを外すと体積制約が守られない（実測：0.646 vs 目標 0.400）。
> **進捗行と結果には応力の行と体積の行の両方が出るので、両方読む。** 実行不可能な要求は
> 例外にならず、体積を volfrac から漂わせて妥協する（SF 6 の実測：体積 0.3 → 0.419、
> それでも制約は破れたまま）。`topopt report` の `stress_constraint` 条件は
> **最適化グリッド上の緩和・集約された指数**を判定するもので、既存の `safety_factor` 条件
> （最終 FEA）とは別の量。両方を見ること。
> **tall_tripod で回した結果は「効かなかった」**（2026-09-08）：6ケース全て SATISFIED、
> 最悪でも SF×FI 0.024（要求の 1/40）で、応力制約だけ外した対照 run と体積 0.3%・
> 支配ケース SF 0.6% しか違わない（どちらも p99.5 の分解能未満）。増えたのは所要だけ
> （332 → 677 s）。**この study は荷重が転倒荷重で決まっていて強度が形を決めていない**
> （known-issues G3）ので、応力制約の検証モデルにならない。
> 比較表：`tall_tripod/archive/phase12_stress_2026-09-08/README.md`。
>
> **`report.md` の「Structural results」表は `load_class` を見ていない**（known-issues A7）。
> 同じ report の中で、応力制約の行が「required SF 1.50（abnormal）」と書く同じケースを
> 表は「normal / 3.00」と書く。**表のほうが間違っている。** 異常荷重を含む study の合否は
> `topopt fea` / `topopt ccx` のケース別出力で見ること。

## Verification Loop（検証ループ）

**機能を実装したら、完了宣言の前に必ず回す。** 最重要の規約。

1. 実装する
2. `cd topopt && python -m pytest` が全件 PASS することを確認する
   （pytest 自体を疑うときは `python topopt/tests/smoke.py`。同じチェックを別経路で回す）
   - **数値の基準値を書き換えて green にしない。** 動いた理由を説明できてから
     `python topopt/tests/make_baseline.py` を回し、何がなぜ動いたかを併せて記録する
3. 変更した段階に対応する CLI を **osmo_tripod の study で実際に回す**（上の Commands 参照）
   - 出力 JSON の該当フィールドと**終了コード**を確認する
   - プレビュー / 結果 PNG がある変更なら画像を実際に見る
4. `docs/spec.md` §19 の該当機能の**受け入れ条件**を1つずつ照合する
5. 1つでも失敗・未達なら原因を直して 2 に戻る
6. すべて green かつ全受け入れ条件 ○ で初めて「完了」

### この検証ループ固有の規則

- **数値が変わったら、変わった理由を説明できるまで完了にしない。**
  osmo_tripod の既存結果（`osmo_tripod/out/gd_mount/gd_mount_study.json` 等）が回帰の基準値。
- **`converged: true` を根拠にしない。** `termination_reason` を読む。
  最大反復到達は収束ではない。
- **安全率は p99.5 と最悪要素の両方を見る。** どちらか一方だけで合格と言わない。
- **`min_mm` が 2×voxel ちょうどならグリッド律速。** 構造の値として報告しない。
- 手動確認をしたら、何をどう確認したかを一言報告する。
- 仕様の不備で受け入れ条件を満たせないときは `docs/spec.md` §24「未決定事項」に追記して相談する。
- **`self_supporting` が 0 でない run を印刷可能と言わない。** preserve の下面ぶん
  （`on_preserve_voxels`）は別で、CAD 側で面取りする。

## ドキュメント同期規約

コードだけ進んで docs が古くなると土台が嘘になる。だから：

- **仕様や挙動を変えたら、対応するドキュメントを同じ変更（コミット）の中で更新する。**
  - 機能・受け入れ条件・タスク → `docs/spec.md`
  - モジュール構成・経路の分担・依存 → `docs/architecture.md`
  - 設計空間モデルのノブ・規則の詳細 → `topopt/docs/design-space.md`
  - 最適化結果の扱い方 → `topopt/docs/topology-optimization.md`
  - セットアップ・使い方 → `README.md` / `topopt/README.md`
  - 設計の進め方・実務上の注意 → `docs/workflow.md`
- **新しく踏んだ失敗は `docs/lessons-learned.md` に足す。** 直したバグでも、
  「なぜその規則があるか」が残らないと次の人が同じ緩め方をする。
- **docs を増減したら `docs/README.md` の索引も直す。** 図（Mermaid）を持つ docs は実装とズレたら図も直す。
- 今すぐ直せないズレは `docs/spec.md` §24 に残して放置しない。

## 人間向けの書き方（README・docs・作業報告）

読む相手は**このコードを書いていない人**。内部名と専門用語だけで説明すると、
「書いたが伝わっていない」状態になる。

**人間向けの .md（`README.md`、`docs/workflow.md`、部品ごとの `.md`）は、冒頭で次の2つに答える。**

- **これは何なのか** — 何のためのもので、何を入れると何が出てくるか。1〜3文。
- **どう動いているのか** — 仕組みを普通の言葉で数行。数式・クラス名・モジュール名を並べない。
  比喩は1つまで使ってよい。

そのうえで：

- 専門用語は初出で開く（例：「preserve（絶対に残す領域）」）。略語も同じ。
- 既に知っているものへ寄せる（例：「Fusion Generative Design と同じ考え方」）。
- **割り切りと限界を隠さない。** 「できないこと」は人間向けにも1行で書く。
- コマンドは**何のために打つのか**とセットで書く。
- 受け入れ条件・検証手順・設計判断は `docs/` と AGENTS.md 側に置き、README へ持ち込まない。

**作業結果を報告するときも同じ。** 変更点の列挙だけでは伝わらない。

- 「何をしたか（1〜2文）／なぜそうしたか／どう確かめたか／相手が次にすること」の順で短く。
- ファイル名・関数名だけの報告をしない。**その変更で何が変わるのか**を書く。
- 判断が分かれたところ、仮に決めたところは明示する（勝手に確定させない）。
- 数値が変わったら、変わった理由を一言添える。

## コーディング規約

- **単位は mm / N / MPa。** 変換を関数内に隠さない。入力で `units` が違えばエラー。
- **`materials.py` が材料値の唯一の出所。** 他所に数値を書かない。
- **`printers.py` が印刷範囲の唯一の出所。** 同じ規約。ベッド寸法を他所に書かない。
- **荷重・拘束は preserve の上にしか置けない。** この規則を「便利だから」で緩めない。
  **例外は1つだけ**、2026-08-06 に足した `{"contact": "ground"}` の support。
  これは preserve ではなく**設計空間の底面1ボクセル層**に切り詰められ、
  さらに**接地面の節点だけ**を拘束する（`_plane_nodes_of`）。
  理由は規則の目的が守られていること：自由セルに拘束を置くと最適化がその下を
  消してしまうのが問題なのに対し、接地拘束は接地面より上を掴めないので、
  最適化が床に何も置かなければコンプライアンスが即座に発散して自分に返ってくる。
  **「上の節点も拘束する」に緩めないこと。** そうすると1ボクセル浮いた脚が
  完全拘束になり、宙に浮いた部品を「支持されている」と判定する。
  荷重は対象外——押すのは床の仕事ではない。
- **preserve の書き戻しは、密度を下げうる全処理（フィルタ・射影・対称化・しきい値・
  連結成分掃除・スムージング）の後。** 順序を変えない。
- **密度処理の順序は フィルタ → 自己支持 → 射影 → 対称 → impose。** 自己支持フィルタが
  射影より前にあるのは、射影が印刷される密度場を鋭くする処理で、smooth max の中立点 0.5 が
  射影しきい値 0.5 と一致していることが「しきい値後に浮かない」保証そのものだから。
  この対を崩す順序変更をしない。`impose` は変わらず最後。
- **書き出す形は、解析した形と同じ平面に立っていること。** `write_blocky_stl` /
  `write_smooth_stl` / `write_step_faceted` はボクセル (i,j,k) を
  `origin + [i,j,k]*h` 〜 `origin + [i+1,j+1,k+1]*h` として書く。
  スムージングが動かしてよくないものは2つ、**取合いと接地面**。
  だから `floor_mask` には `preserve | (mask & ground)` を渡す。
  （実測：preserve だけだと脚が 4.11 mm 浮いた。`test_meshout_ground.py`）
- **終了コードは所見。** 0=合格/有効、1=不合格/無効、2=入力が読めない、3=外部ツール未導入。
  例外で落ちるのと判定が NG なのを混同しない。
  2 の判定は `cli/__init__.py` が `schema.InputError` を捕まえて一元的に返す。
  **捕捉範囲を広げてソルバのバグを 2 に化けさせない。** ツールの欠陥が
  「利用者の入力が悪い」として報告されるのが一番たちが悪い。
- **グリッド原点を出力サマリに必ず残す。** 残さないと `compare` が黙って誤った数字を出す。
- **「別の向きなら入る」を合格にしない。** 向きを変えれば入る部品はその向きで印刷することになり、
  層の向きが変わって、`build_axis` に紐づいた異方性解析がその物体を記述しなくなる。
- **最適化の自由度を黙って捨てない。** 荷重・拘束は preserve の上にしか置けないので、
  **脚の位置は必ず人間が決めている**。それ自体は正しいが、
  黙っていると読み手は最適化された配置だと思う。`assumptions.md` に必ず載せる。
- **ソルバの経路を変えても物理を変えない。** `solve.py` の前処理・バックエンドは
  `K u = f` の解き方だけを決める。新しい経路は `--solver cpu:jacobi` と同じ答え
  （コンプライアンス相対差 1e-6 以下）を `tests/test_solver.py` で示してから既定にする。
- 新しい外部依存は必須にしない。実行時に検出し、無ければ終了コード 3 で導入手順を出す。
- 既存の英語ドキュメント（`topopt/docs/`）は英語のまま維持する。`topopt/README.md` は日本語。
  日本語ドキュメントは直下と `docs/`。

## Do NOT

- **未収束を収束と表示しない。** 最大反復到達を `converged: true` にしない。
- **最終形状を再解析せずに合格と言わない。** メッシュ収束未確認の数値を使わない。
- **最適化の生 STL/STEP を部品として扱わない。** 取合い寸法はボクセルに量子化されている。
- **不完全な解析を合格表示しない。** 落ちた段階があるなら、どこまでが有効かを明示する。
- **CalculiX の再解析は表面を分割した殻で行う。`--no-refine` の数字を収束スタディとして引用しない。**
  Gmsh は渡した STL の三角形をそのまま境界メッシュにするので、分割しないまま
  `Mesh.CharacteristicLength` だけ細かくしても境界の分解能は変わらない（lessons-learned #73）。
- **MeshFix の修復後は体積を確認する。減っていたらその STL は使わない。** `--repair` は
  つまずいた場所（非多様体稜線）ごと削るので、直したつもりが構造ごと消えていることがある
  （実測：tall_tripod で体積の21%を喪失。lessons-learned #77）。
- **前提条件を書かずに形だけ出さない。** トポロジー最適化の結果はその前提の像でしかない。
  形と `assumptions.md` の組で初めて結果になる。
- numpy / scipy 以外を必須依存に足さない（`pyproject.toml` の `dependencies` を勝手に増やさない）。
- README.md に AI 向け手順を書かない（人間向けに保つ）。
- 仕様を変えたのに docs を直さず「完了」と言わない。
## MCP からの設計・解析

**2026-10-07の更新（上の過去runの記述より優先）：** `run`のCCXは完成STLを既定にした。
同じ解析形状のソルバ比較は`--ccx-geometry analysis`で指定する。内容照合はCCXの
`stl_sha256 / study_sha256`を使う。完成形状欠落時のrawへのフォールバックはしない。
最悪要素SFの絶対下限は**1.5**（p99.5の1.5倍ではない）。断面の要素数はメッシュ収束とは別。
`run --supports auto|none|tree --layer-mm ...`はstudyの材料でスライスする。
新規水中部品の荷重・初期モデル・支持の手順は`docs/underwater-design.md`と
`examples/underwater_bracket/`。例の仮定値を実機荷重にしない。

`topopt` MCP を登録済みの場合、`topopt_help` で CLI 引数を確認し、
`topopt_start` → `topopt_job` で実行・結果取得できる。詳細は [docs/mcp.md](docs/mcp.md)。
MCP 経由でも本書の setup・プレビュー・収束確認の規則は同じ。

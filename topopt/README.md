# topopt — 3Dプリント部品のためのボクセルFEA＋トポロジー最適化

別部品への展開は[水中部品の設計手順](../docs/underwater-design.md)と
[ブラケット例](../examples/underwater_bracket/README.md)を参照してください。
`run --supports tree --layer-mm 0.2`でstudyの材料を使って実スライスできます。
CCXは完成STLを優先します。従来の解析形状のソルバ比較は`--ccx-geometry analysis`です。

CAD カーネルなしで、STL から直接:

- **剛性・強度を測る** — ボクセル FEA（たわみ・von Mises・FDM 異方破壊指標・安全率）
- **形を探す** — SIMP トポロジー最適化（Fusion Generative Design と同じ設計空間モデル）
- **造形性を判定する** — プリンタ適合・向き探索・実サポート面積・最小フィーチャ
- **実寸を読む** — 断面図・STEP 読み書き・可動部の掃引干渉

依存は numpy と scipy だけ（PNG に matplotlib、平滑 STL に scikit-image が任意）。
FreeCAD も OCC も要らない。単位は全部 **mm / N / MPa**。

---

## インストール

```bash
pip install -e path/to/topopt/topopt          # 最小
pip install -e "path/to/topopt/topopt[all]"   # PNG と平滑STLも
```

以後どこからでも `python -m topopt <command>`。
`Scripts/` に PATH が通っていれば `topopt <command>` でも動く。

インストールせずに使うなら:

```bash
PYTHONPATH=path/to/topopt/topopt/src python -m topopt <command>
```

GPU は任意。CUDA ビルドの `torch` が入っていれば自動で使う（`--solver auto` が既定）。
無ければ numpy で動き、答えは同じ。強制するなら `--solver cpu` / `--solver cuda`。

`topopt ccx`（CalculiX による独立再解析）を使うなら、Gmsh と CalculiX も要る。
どちらもインストーラ不要・管理者権限不要：Gmsh は [gmsh.info](https://gmsh.info/) の zip 版、
CalculiX は [dhondt.de](https://www.dhondt.de/) の Windows ビルドを、自分のユーザーフォルダの下
（例：`Tools\`）に展開するだけでよい。`topopt` は環境変数 `TOPOPT_GMSH` / `TOPOPT_CCX`、
または `%USERPROFILE%\Tools\` 以下の自動検索のどちらでも見つける。
`python -m topopt ccx --check` で導入状況を確認できる。
入力 STL が閉曲面でない（OpenSCAD 2021.01 系でよく起きる）場合は `--repair` で修復できる
（別途 `pip install pymeshfix` が要る）。

---

## コマンド

| | |
|---|---|
| `topopt assumptions` | 前提条件を1枚に（荷重・材料・機械・何が事前に決まっていたか） |
| `topopt setup` | 設計空間の設定を検証して 3D プレビューを出す。**解く前**に数秒で。**印刷範囲に入るか**と、**その解像度で何分かかるか**もここ |
| `topopt opt` | トポロジー最適化を回す |
| `topopt extract` | 密度場 → しきい値ごとの形状候補。候補ごとに再解析して並べる |
| `topopt ccx` | CalculiX で独立に再解析（Gmsh + CalculiX 必要。無ければ終了コード 3。`--repair` で入力 STL の非閉曲面を修復。既定で表面を分割してから渡し（`--no-refine` で無効化）、境界条件はボクセル経路と同じ体積マッピング（`--map surface` で代替）、荷重は整合節点荷重（`--load-split equal` で旧方式）。`--buckle`/`--frequency`・`--voxel-fea PATH`・`--ground-tol`・`--exclude-mm` あり） |
| `topopt report` | 1つの `report.md` に判定をまとめる。**FAIL が冒頭**。`--csv` で `report_cases.csv`（荷重ケースごとの安全率・変位）と `report_checks.csv`（停止条件ごとの合否）も書く |
| `topopt run` | setup → opt → extract → ccx → print → slice → report → publish を一括。`--model DIR` で4段階フォルダに出力（スライス成果物は `04_print/slice/`）。`--no-slice` でスライス段を飛ばす |
| `topopt validate` | 出力 JSON（`report.json` / `ccx_summary.json` / `manifest.json` 等）が `outschema.py` の表と合っているかを検証。`--print-doc` で `topopt/docs/output-schema.md` を再生成 |
| `topopt underwater-loads` | 静水圧・浮力・簡易抗力 |
| `topopt compare` | 手で作り直した形は、まだ荷重を持つか（幾何差分＋同条件 FEA） |
| `topopt fea` | STL に対するボクセル FEA。`--converge` で [2H, H, H/2] の3段階を解いてケースごとに収束を判定 |
| `topopt print` | プリンタに載るか・最適な向き・最小フィーチャ・どこで割るか |
| `topopt overhang` | この向きで**本当に**サポートが要る面積 |
| `topopt slice` | スライサー（Bambu Studio / OrcaSlicer）を headless で回して、**実際の**造形時間・材料量・サポート量・層数を読む。無ければ終了コード 3。`--printer` `--layer-mm` `--material` `--supports auto\|none\|tree` `--orient keep\|auto` |
| `topopt sections` | STL を切って実寸を読む |
| `topopt clearance` | ヒンジまわりに相手を振って干渉を見る |
| `topopt step2stl` | 平面BREPの STEP → STL |

`topopt <command> --help` で各コマンドの引数。

解くコマンド（`fea` / `opt` / `run` / `setup` / `extract` / `compare`）は `--solver SPEC` を取る
（環境変数 `TOPOPT_SOLVER` でも同じ）。`SPEC` は `auto`（既定：CUDA があれば GPU、無ければ CPU）/
`cpu` / `cuda` に、任意で `:mg`（マルチグリッド前処理、既定）/ `:mg64` / `:jacobi`（旧前処理）を付ける。
`--solver cpu:jacobi` が記録済み run と同じ解き方。どの経路で解いたかは出力 JSON の `solver`
（`backend` / `preconditioner` / `precision`）と `cg_iterations` に残る。
実測（4 mm・101,808 要素・0/1 密度・1 ケース）：CPU Jacobi 約 540 s →
CPU マルチグリッド 約 20 s → GPU マルチグリッド 2.2 s。答えはどれも同じ（コンプライアンス相対差 1e-6 以下）。

**GPU では `mg` の V サイクルを float32 で回す**（2026-09-08、`precision: mixed32`）。外側の CG と
粗格子の直接解法は float64 のままなので、収束判定も答えも float64 のもの（float64 サイクルとの
コンプライアンス相対差 1e-10）。前処理まで float64 に戻すなら `:mg64`。同日、密度が厳密に 0 の要素
（obstacle・空いた自由セル）を作用素から外し、CG の GPU 同期を 1 反復 1 回にした。
child_jaw_gate（3 mm・99,450 要素のうち obstacle 49%・5 ケース）の最適化 4 反復で 24.6 s → 9.9 s。

**試行を重ねる段階は `topopt run ... --quick`。** しきい値 0.5 のみ・CalculiX とスライサーなし・
平滑 STL / STEP / finish なしの探索プリセットで、設定検査・最適化・最終 FEA・造形性検査は走る。
反復数は変えない（継続スケジュールが `--iters` の上に敷かれるので、減らすのは別の最適化になる。
短くするなら `--iters` で明示する）。要約に `"quick": true` が残る。採用する案は、study・グリッド・設定が同じなら保存密度から後段を `--quick` なしで実行でき、optの繰り返しは不要。元のrun_summary/manifestを保存して後段の記録と対応付ける。条件を変えた場合は再optする。

### 典型的な順序

```bash
topopt setup   study.json --out out/gd            # 数秒。ここで設定ミスを潰す
                                                  # out/gd/*_setup.png を必ず見る
topopt opt     study.json --out out/gd --iters 50 # 数分〜数時間
topopt extract out/gd --study study.json --out out/gd   # しきい値候補を比べる
topopt report  out/gd --study study.json          # 判定を1ファイルに
topopt compare out/gd rebuild.stl --study study.json
topopt fea     rebuild_study.json --out out/fea --voxel 0.67
topopt print   rebuild.stl --printer X1C --load-axis 1,0,0
topopt overhang rebuild.stl
```

上の順序をまとめて回すのが `topopt run study.json --out out/gd`。
**必須検査に失敗したら止まり**、外部ツールが無い段階は**スキップと記録**して
「どこまでの結果が有効か」を `run_summary.json` の `valid_through` に書く。
この2つを混同すると、不完全な解析が合格として綴じられる。

`topopt opt` は自前で検証とプレビュー出力をしてから解き始め、
無効な設定は解かずに落ちる。`topopt setup` を分けてあるのは、
1時間解いた後で設定ミスに気づく代わりに数秒で気づくため。

**ボクセルサイズは解く前に値段を聞ける。** `topopt setup` はグリッドを作ったあと
**実際に1回 FEA を解いて**、`--iters` 反復ぶんの所要を範囲つきで出す。

```bash
topopt setup study.json --out out/gd --voxel 5 --iters 80
# runtime  80 iterations x 6 case(s) ~ 24 min  (range 12 min - 36 min;
#          one cold solve of case 1 took 3.0 s on 52000 elements)
```

式で外挿しない。記録済み run はセルあたりコストが2倍以上ばらついていて、
**セル数では説明できない**（17,902 セルで 16.5 s/反復/ケース、32,744 セルで 9.3）。
支配するのは CG の反復数＝剛性行列の条件で、要素数ではないため。`--no-estimate` で省略。

**終了コードは「クラッシュ」ではなく「所見」**。
`setup`: 0=有効 / 1=無効 / 2=読めない。
`opt`: 0=連結していて全ケース安全率を満たす / 1=満たさない。
`fea`・`print`: 0=PASS / 1=FAIL。

---

## 設計空間モデル

Fusion Generative Design と同じ 4 種類のジオメトリで設定を書く。

| 種別 | 密度 | 何を入れるか |
|---|---|---|
| `preserve` | **1 固定** | ボルト・荷重・位置決め・把持に使う面すべて |
| `obstacle` | **0 固定** | 穴、可動部の掃引域、ケーブル、工具アクセス、光学視野 |
| `design_space` | 自由 | 最適化が埋めてよい空間 |
| `optional_starting_shape` | 初期値のみ | ウォームスタート。何も拘束しない |

ソルバを回す前にエラーになる主なもの:

- **荷重・拘束は preserve の上にしか置けない。** 自由セルに荷重を置くと、
  最適化は「押している当の相手」を消してよいと解釈し、実際に消す。
- **preserve は一切侵食されない。** フィルタ・射影・対称化・しきい値処理・
  連結成分の掃除・スムージングは全部 preserve を書き戻す**前**に走る。
- **preserve 同士が設計空間経由で繋がらない設定はエラー。**
- **穴あき部品は「実体を preserve・穴を obstacle」で書く。**
  穴あき STL を preserve に渡すだけだと穴は自由セルなので最適化が埋める。

### 接地だけは preserve でなくてよい

```json
{"region": "floor", "fix": "xyz", "contact": "ground"}
```

`"contact": "ground"` と書いた**拘束だけ**は、preserve ではなく
**設計空間の底面1ボクセル層**に切り詰められ、**接地面の節点だけ**を固定する。
最適化が床に何も置かなければ構造は本当に支えを失うので、規則の目的は保たれる。

**足を CAD で描かずに済む唯一の手段**（描くと「脚の位置は人間が決めました」と
レポートに書き続けることになる）。ただし**転倒は表現できない**——接地は両側拘束で、
FEA の中で部品は床から離れられない。片側支持のケースを1方位しか書かないと、
**倒れる形が安全率 128 で合格する**（実測）。詳細は
[`docs/design-space.md`](docs/design-space.md) と `docs/known-issues.md` G7。

### 部品に「どこで繋いでよいか」を言わせる（`attach`、2026-09-08）

```json
"preserve": {"mount_head": {"stl": "02_setup/tall_preserve.stl",
                            "attach": ["-Z"]}}
```

既定では設計空間が preserve の**全面**に触れているので、最適化はどの面にでも材料を生やす。
tall_tripod v4 では頭部の側面と上面にまで材料が回り込み、カメラとネジのための空間を
食っていた。`attach` に面（`"+X"`〜`"-Z"`、そのボディ自身の面から一歩出たセル）か
region（ボディに接するセルだけに切り詰められる）を書くと、**それ以外の表面には
1 ボクセル（`attach_standoff_mm`）の非接触殻**が付き、setup 以降ずっと obstacle として扱われる。
`attach` を書かないボディは従来どおり（どこでも繋げる）。setup のプレビューでは接続してよい
セルが橙で出る。届かない面・空リストはエラー（そのボディに何も繋がらないから）。

### 取合いは解析グリッドから出さなくてよい（`finish`）

```json
"finish": {"voxel_mm": 0.5,
           "smooth_mm": "auto",
           "add": {"mount_head": "mount_solid.stl"},
           "cut": {"features": "mount_cuts.stl"}}
```

出荷する形＝**最適化の結果（解析グリッドのまま整数倍で再サンプル）− 解析した preserve の
ボクセル ＋ CAD の preserve − CAD の穴**。だから 10 mm ボクセルでも 2.40 mm の爪が出荷ファイルに残る。
**引くのが最後**（先に引くと足し戻しで埋まる）。

**2026-09-08 から、`add` の名前が preserve ボディの名前と同じなら、そのボディの解析ボクセルは
捨てて CAD で置き換える**（別名なら `"replaces": "<ボディ名>"`）。置き換えないと、CAD の頭部が
自分自身の 10 mm ボクセル化の中に埋もれた「ブロック」が出荷される（v4 の tall_tripod がそれ）。
最適化した材料が触れていた 1 ボクセルだけは橋として残すので、ボクセル面と CAD 面のずれで
部品が二つに分かれることはない。

**同じ日に、平滑化もこの段に移した。** `smooth_mm`（既定 `"auto"` ＝解析ボクセル1つ、
`false` で無効）は**最適化した材料だけ**にかかる。CAD のボディ・置き換えなかった preserve・
接地層は 1 に、obstacle（`attach` の非接触殻を含む）は 0 に固定してから等値面を切り、
しきい値は**体積が解析した階段形と同じになる値**を選ぶ。角は丸まり、面は動かず、
1 ボクセル幅の部材が細らない。旧 `*_smooth.stl`（粗いグリッド上で σ=1 ボクセルのガウス）は
10 mm で脚を消し、頭部を団子にしていた——あれは preserve ごと平滑化していたから。

**ただし「CAD の寸法どおり」ではない。** 刻みが解析ボクセルから finish ボクセルに
変わるだけで、**幅は finish ボクセル1つぶんまで狂う**。実測（10 mm → 0.5 mm）：
中央爪 2.40 → 2.00、溝 3.80 → 4.00、ボルト穴 Ø5.60 → Ø5.60。
`feature_tolerance_mm` を引用する。**解析はこの形を見ていない**——`PRINT.md` が区別を書く。

全仕様・全ノブ・study JSON のスキーマは **[`docs/design-space.md`](docs/design-space.md)**。
旧 `keep_in` / `keep_out` 方式と再構築の手順は
[`docs/topology-optimization.md`](docs/topology-optimization.md)。

### study ファイルは解く前に検査される

**JSON でも YAML でも同じ**（`.yaml` / `.yml` は拡張子で判定。PyYAML は任意依存で、
未導入なら導入手順を出して終わる）。読み込み時に次を見て、**行番号つきで**指摘する。

```
$ topopt setup broken.json --out out
setup: broken.json has 4 problem(s):
  ERROR broken.json:3  units: unit system 'inch_lbf_psi' is not supported; only 'mm_N_MPa'
  ERROR broken.json:5  safety_factor: must be > 0, got -3.0
  ERROR broken.json:12  design.volfrac: must be <= 1, got 2.5
  ERROR broken.json:17  cases[0].supports: no supports; the part is unconstrained
```

- **既知キーの値の誤りはエラー、未知キーは警告。** study はコメント（`_` 始まりのキー）や
  旧キーを持つので、ソルバが正しく扱えたはずの study を弾くほうが害が大きい。
- **単位は宣言されていれば検査する。** `mm_N_MPa` 以外はエラー。無宣言は注記のみ
  （他の単位系は実装されていないので、拒んでも誤りを1つも防がない）。
- **`build_axis` 無宣言は警告。** 積層法線をどこに取るかで層間引張の安全率が変わるため。
  等方解析（`--isotropic`）は Z を既定にして進むが、**既定の直交異方性解析は宣言が無ければ拒否する**。
- **`raster_axis` は任意。** 層内でラスタが走る向き（PLA なら Ex 3000 と Ey 2400 の差）。
  無宣言なら「build 軸以外の最初の軸」を使い、要約にそう書く。

### 終了コードは所見

| | |
|---|---|
| 0 | 合格 / 有効 |
| 1 | 不合格 / 無効（**解いた上での判定**。異常終了ではない） |
| 2 | 入力が読めない（ファイルが無い・壊れている・スキーマ違反） |
| 3 | 外部ツール未導入 |

`topopt opt` が 1 を返すのは「安全率が要求に届かなかった」であって、失敗ではない。

---

## 中身

```
topopt/
├─ pyproject.toml
├─ docs/
│  ├─ design-space.md            設計空間モデルの全仕様（まずこれ）
│  └─ topology-optimization.md   領域設定 → プリミティブでの再構築
└─ src/topopt/
   ├─ voxel.py          STL → 占有グリッド（+Z レイパリティ充填）
   ├─ hex8.py           非適合モード8節点ブリック要素、FDM 破壊指標
   ├─ solve.py          行列を組まない PCG 線形弾性ソルバ（全ケースをブロックで解く）
   ├─ multigrid.py      幾何マルチグリッド前処理（反復数を数千から数十へ）
   ├─ backend.py        numpy / torch+CUDA の配列バックエンドと `--solver`
   ├─ materials.py      FDM 材料データ（E, nu, sigma_xy, sigma_z, rho）
   ├─ loadcase.py       study JSON → グリッド上の境界条件
   ├─ report.py         応力集計と結果 PNG
   ├─ schema.py         study の検証（自前。jsonschema は使わない）
   ├─ manifest.py       再現性情報
   ├─ external.py       外部ツール検出と終了コード 3
   ├─ ccx.py            CalculiX デッキ生成・結果パーサ・シェル検査/修復。
   │                    **実行検証済（2026-09-08）**。ケースごとの結果（`cases`）を出す
   ├─ printcheck.py     サポートの着地先（重要面接触・密閉空洞）
   ├─ underwater.py     静水圧・浮力・簡易抗力
   ├─ designspace.py    preserve/obstacle/design_space の設定モデルと検証
   ├─ generative.py     それを守る SIMP 最適化
   ├─ amfilter.py       自己支持（オーバーハング）フィルタ：積層方向に沿って 45° 円錐で支えられた材料だけを許す
   ├─ members.py        密度場から中心線＋幅のリブ抽出
   ├─ meshout.py        密度場 → STL（角/平滑）と平面BREP STEP
   ├─ setup_preview.py  解く前の色分け 3D プレビュー
   ├─ geom.py           三角形スープ：断面・サポート面積・掃引干渉
   ├─ stepio.py         平面BREP STEP の読み取り
   └─ cli/              各コマンド（`topopt.cli.main` が振り分け）
```

`materials.py` は**材料値の唯一の出所**。スキル側の `quick_strength.py` も
ここから読むので、閉形式チェックと FEA が別の数字を使うことはない。

---

## 知っておくべき限界

- **異方性は剛性に入っている（既定）が、材料値は実測ではない。**
  `opt` / `fea` は既定で直交異方性カード（PLA / PETG）で解き、
  `build_axis` は形の探索そのものに効く。`--isotropic` で従来の等方2定数モデルに戻せる。
  **ただしカードの19個の値に実測は1つも無い**（文献傾向からの保守的な既定値と推定値）。
  そのため要求安全率は 3.0 ではなく **4.0** として報告される。
  実測で置き換えるまで、絶対値ではなく**造形方向どうしの比較**に使うこと。
- **カードの無い材料は直交異方性で解けない。** ABS / ASA / PA6-CF / TPU / PLA-CF には
  まだカードが無く、`--isotropic` を付けない限り停止する。
  等方へ黙って落ちると、直交異方性に見える数字が出てしまうため。
- **インフィルは最適形状を変えない。** compliance は `1/E` に比例するので、
  `E` を一様に下げても最適密度分布は厳密に同一。効くのは絶対たわみだけ。
- **`topopt slice` は spec §13.3 のサポート合格条件を判定しない。**
  §13.3 の5条件は「サポートが**どこに**付くか」（密閉空洞の中か、工具が届くか、
  preserve の座面に載るか、外すときに細い部材を折るか）を問うているのに対し、
  スライサーが出せるのは「**どれだけ**付くか」だけである。5条件は `slice.json` の
  `checks` に**理由つきで全部 UNKNOWN として**残る（省略すると「問題なし」と読まれる）。
  位置の判定はボクセル格子上の `printcheck.json` が受け持つ。
  `slice.json` の `pass` は「スライスが通ってベッドに載った」の意味しかない。
- **サポートの「体積」は2種類あって別の量。** `printcheck.json` の `support_volume_mm3`
  は部品空間でサポートが占める体積、`slice.json` の `support_mm3` は実際に押し出す
  φ1.75 mm フィラメントの体積（＝捨てる材料）。並べて比較しない。
- **サポートのフィラメント量はスライサーが報告していない。** 3mf のどこにも無いので、
  G-code の `; FEATURE: Support*` ブロックを積算して出している。積算がスライサー自身の
  総フィラメント長を再現したときだけ報告し、しなければ null ＋ 理由を書く
  （検算できない数字は引用されると直せない）。
- **自己支持は既定で入る（2026-09-05〜）。** `design.overhang` が既定 true で、積層方向
  （`build_axis`、プレートは設計空間の下端）に沿って「下の 45° 円錐に材料が無いセルには
  材料を置けない」を最適化の中で課す。しきい値後の形も同じ規則で検査し、
  `report` の `self_supporting` 条件になる。ブリッジは知らない（短い水平スパンも
  非支持扱い）ので、少し保守的。preserve の下面は対象外（CAD の仕事）。
  サポートを許す条件は 2 つ：`{"max_support_mm": 20}`（高さ 20 mm 以下のサポート柱なら可）と
  `{"allow_support": ["領域名"]}`（その領域では可）。
- **最適化の生結果は部品ではない。** 取合い寸法がボクセルに量子化される
  （実測: 1.607 mm ボクセルで 3.50 mm の溝が 3.21 mm になり、
  クリアランスが 0.50→0.21 mm に落ちた）。**必ず作り直す。**
  そのための `topopt compare`。
- **`volfrac` は自由セルにのみ掛かる。** 全体体積率 = preserve + volfrac ×（自由セル）。
- **強度で形を決めたいときは `design.stress` がある（既定は使わない）。**
  有効にすると、最適化が回っている間ずっと「必要な安全率を下回っていないか」を
  計算グリッド上で見ながら形を探す。**「必要な安全率」は荷重ケースごと**で、
  study に `load_class: "abnormal"` と書いたケース（落下・片脚引っ掛け）は 1.5、
  それ以外は study の `safety_factor`——最終判定と同じ規則（spec §15.1）。
  **それでも判定するのは最適化中の見積もりだけ**で、
  最終的な合否は仕上がった形を再解析した安全率（p99.5・最悪要素）で決まる。
  詳細は [`docs/design-space.md`](docs/design-space.md) の「Stress constraint」。
- **STEP は完全な閉シェルとは限らない。** 頂点/稜線だけで接するボクセルが
  非多様体稜線を生む。実測値を毎回出すので、断言ではなく数を見ること。
- **ボクセル FEA と CalculiX（`topopt ccx`）は、2026-09-08 の2回目のラウンドで
  変位が15%以内（最悪5.4%）まで一致した——ただし原因はメッシュだった。** 最初の
  突き合わせは6ケース中4ケースで15%を超えて食い違ったが（22〜40%）、原因は
  Gmsh が渡した STL の三角形をそのまま境界メッシュにしていたこと（一部の脚支持面は
  STL 頂点9個しか掴んでいなかった）。既定で表面を分割してから渡し（`--no-refine` で
  無効化可）、境界条件をボクセル経路と同じ体積マッピングに揃えたら一致した。
  **ただし `fea --converge` を回すと同じ osmo_tripod は4/2/1 mmのどの段階でも
  未収束**（p99.5応力が13〜32%動く）。**一致したことと収束したことは別。**
  今回一致した数字も最終値として引用しないこと。詳細は `../docs/known-issues.md` A6。

---

## 動作確認

```bash
python -m topopt --version
python tests/smoke.py            # 数秒。ソルバ・設定モデル・メッシュ出力
```

実例一式は `../osmo_tripod/`（DJI Osmo Action 6 用3脚ホルダー）。
`gd_mount_study.json` が治具込みの設計空間モデルの完成形。

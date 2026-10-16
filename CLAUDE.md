# CLAUDE.md

> Claude Code はこのファイルを自動で読み込みます。このリポジトリの作業規約・コマンド・検証ループは
> [AGENTS.md](AGENTS.md) に**一元化**しています（全エージェント共通）。
> CLAUDE.md はそれを取り込む薄いラッパで、Claude Code 固有の補足だけを足します。

@AGENTS.md

## Claude Code 固有のメモ

- 関連する Skill：`3d-print-design`（OpenSCAD での部品・筐体設計）、`circuit-design`、`embedded-firmware`。
  ただし **FEM・トポロジー最適化のアルゴリズムを Skill 側に再実装しない**（`docs/spec.md` Phase 8）。
  Skill の役割は `topopt` CLI を正しく操作・検証すること。
- `3d-print-design` スキルの `quick_strength.py` は `topopt/src/topopt/materials.py` から材料値を読む。
  材料データを変更したら、閉形式チェックと FEA が同じ数字を使い続けているか確認する。
  **2026-08-05 に確認：`quick_strength.py` は旧2定数モデル（`Material`）を読んでおり、
  `topopt fea --isotropic` と完全に同じ数字（PLA: E_xy 3000 / E_z 2400 / σ_xy 46 / σ_z 26）。**
  ただし **Phase 2 以降 `topopt` の既定は直交異方性**なので、
  **閉形式チェックと既定の FEA は意図的に食い違う**（カードは Ez 1500 / Zt 22）。
  旧モデルは層間方向を楽観的に見ていたので、**閉形式のほうが甘い**。
  閉形式で通っても、既定の FEA で落ちることがある。それは矛盾ではない。
- **本番を回す前に値段を聞く。** `topopt setup --voxel N --iters 80` が
  実際に1回解いて所要を出す（`runtime.py`）。**「たぶん数時間」と言う前にこれを回す。**
  出た数字は範囲つきで、実測 2 点では 0.54 倍・1.35 倍だった。
  見積もり行はいまどのバックエンド・前処理で解いたかを `[cuda/mg]` `[cpu/mg]` のように出す。
  **0.54 倍・1.35 倍の校正はマルチグリッド前処理より前の実測**なので、そのままは使えない。
- 実行が長いコマンド（`topopt opt` の本番設定）は、まず `--elements 8000 --iters 25` で
  トポロジーが妥当か確認してから本番を回す。
  **`topopt opt` の進捗行は5反復ごと。** 1行ごとだと思って速度を計算すると 5 倍遅く見える。
  実際にそれで 3 時間の run を「14 時間」と誤判定して打ち切った（lessons-learned #47）。
  速度を口にする前に出力間隔を確認する。
- **見積もりが約1時間を超える run は、ツールのバックグラウンド実行に載せない。** セッション側の後始末で
  途中で殺される。`topopt opt` は最後にしか書き出さないので、31/45 反復まで進んでいても
  出力は何も残らない（実際に一度そうなった）。**完全に切り離して起動する**：

  ```powershell
  Start-Process -FilePath "python" -WindowStyle Hidden -PassThru `
    -ArgumentList "-m","topopt","run","<study>","--model","<dir>","--iters","45" `
    -WorkingDirectory "C:\Users\ryupc\WorkSpace\Mitou26\AquaBeacon-OSS\topopt" `
    -RedirectStandardOutput "<dir>\03_optimization\run.log" `
    -RedirectStandardError  "<dir>\03_optimization\run.err"
  ```

  そのうえで、**反復数を減らした保険 run を並行で回す**とよい（コアは 24 あって余っている）。
  本命が落ちても、完成した成果物が1つ残る。
  **ただし解像度を落とすと取合いが消える study がある。** `--elements 8000` は
  `gd_mount_study` では voxel 3.67 mm になり、2.40 mm の中央爪が丸ごと消えて
  荷重が preserve から外れ `SETUP INVALID`（終了コード 1）になる。設定ミスではない。
  **最小 preserve 部材が 1 ボクセルに乗る解像度**まで戻し、反復数だけ削って試す
  （この study なら `--iters 25` のみ）。
  **このマシンでは tall_tripod の 4 mm × 6 ケース × 80 反復 run が、いまは分単位で終わる**
  （マルチグリッド前処理＋GPU。実測は README を参照）。それでも見積もりが約1時間を超えるなら
  上のバックグラウンド起動に載せる。
- **モデルは1つにつき1フォルダ**（spec §17.5）。中は `01_assumptions/` `02_setup/`
  `03_optimization/` `04_print/`。`topopt run --model DIR` がこの構成で書き出す。
  `tall_tripod/` が実例。`osmo_tripod/` は移行前の構成のまま（記録済み run が回帰の基準値なので動かさない）。
- **設計空間を描いたら、解く前に印刷範囲を確認する。** study に `printer` を宣言すれば
  `topopt setup` が数秒で見る。宣言しなければ既定機で検査し、「既定だった」と出力に残る。
  これを飛ばして 3.6 時間を無駄にした実例が `tall_tripod/archive/v1_unprintable/`。
  **その assert が本当に設計空間を見ているかも確かめる。** `tall_tripod` v3 の assert は
  脚の半径を見ていて、設計空間と一致していたのは偶然だった（v4 で直した）。
- **拘束を preserve から外したら、その preserve が何を担保していたかを確かめる。**
  `{"contact": "ground"}` は足の位置を自由にするが、**転倒は表現できない**。
  片側支持のケースを1方位しか書かないと、倒れる形が安全率 128 で「合格」する（実測）。
  → `docs/known-issues.md` G7、lessons-learned #54。
- **GPU は torch が CUDA を見つければ自動で使われる。** 記録済みの osmo_tripod run と
  突き合わせるときは `--solver cpu:jacobi` を付ける。2台のマシンで6桁目が食い違ったら、
  まずサマリの `solver` フィールドを見比べる。
- **自己支持拘束（`design.overhang`）は既定 ON。** 利用者が「浮いている」「印刷できない形が出た」
  と報告してきたら、まずサマリの `volumes.unsupported` と `connectivity.unsupported_pruned_voxels` /
  `island_mm3` を読む。そのうえで `topopt overhang` を**平滑化した STL**に対して回す
  （`topopt print` はベッド接地面も数えるので 45° の階段状の段差を誤読する）。
  **`volfrac` が非常に低い（0.04 のような）study はこのフィルタに厳しい**——
  低密度の一様場は層ごとに減衰するので `ramp` に頼っている。
- **利用者が「強度で形を決めたい」と言ってきたら `design.stress` がある（既定 OFF、Phase 12）。**
  直交異方性モデルが要る（`--isotropic` と併用すると `InputError`）。実行不可能な要求は
  例外にならず、体積が `volfrac` から漂う形で現れる——エラーメッセージを探すのではなく、
  結果 JSON の体積と `stress_constraint` の両方を見て判断する。
- **Gmsh / CalculiX は `C:\Users\ryupc\Tools` に入っている**（2026-09-08）。
  `TOPOPT_GMSH` / `TOPOPT_CCX` の環境変数、または `external.py` が追加で検索する
  `~\Tools\gmsh*` / `calculix*` のどちらでも見つかる。`topopt ccx` を使う前に
  `python -m topopt ccx --check` で導入状況を確認する。**ボクセル FEA と CalculiX の
  食い違い（旧 A6）は原因判明・解決済（2026-09-08）**：Gmsh は STL の三角形をそのまま
  表面メッシュにするので `--mesh-mm` は内部にしか効いておらず、支持領域が STL の頂点
  9 個しか掴んでいなかった。いまは殻を目標寸法まで辺分割してから切る（既定。
  `--no-refine` の数字を収束スタディとして引用しない）。osmo_tripod で CalculiX 4 mm と
  voxel 1 mm は 6 ケース全て 5.4% 以内。**一致と収束は別**：`topopt fea --converge` は
  osmo_tripod で全ケース未収束（p99.5 応力が 13〜32% 動く）。CalculiX 側は分割後シェルの
  8/4/2 mm で `pass: true`（変位変化 1.6%）、その収束値がボクセル 1 mm と 2% 以内。
- **利用者が「結果が不自然」「preserve に材料が回り込む」「平滑化すると部品が壊れる」と
  言ってきたら、study に `attach`（preserve ごとに繋いでよい面）と `finish.smooth_mm` が
  あるか見る**（2026-09-08、lessons-learned #89・#90）。`attach` が無い study は取合いの全面が
  設計空間に触れていて、finish の `add` が preserve と同名でないと解析ボクセルの上に CAD が
  重なるだけになる。tall_tripod v5 が実例（`tall_tripod/tall_tripod_study.json`）。
- **「触るなと言った部品まで平滑化された」は、まず出荷 STL の面を測る**（2026-09-09、
  lessons-learned #91）。`add` を書いていても v5 までは CAD 面が外へ 0.3 mm 動いていた。
  確かめ方は「CAD の `mount_finished` と出荷 STL を同じグリッドでボクセル化して幅を測る」
  ではなく（測定グリッドの誤差に埋もれる）、**両方の三角形の頂点座標から面の位置を直接読む**。
  直っていれば差は `cad_tolerance_mm` 以内に入る。
- **「最適化した部品がかくかくしている」と言われたら `finish.smooth_mm`。** 既定 `auto` は
  2026-09-09 から**解析ボクセル1つ**（それ以前は半分で、階段が瘤に丸まるだけだった）。
  **絵で確かめる**：`04_print/preview_print.png` が出荷する形そのものの陰影図。
  無い run は publish が古いので回し直す。
- **「底面がスムーズでない」「接続が浮いている」は finish 段の別々のノブ**（lessons-learned #94）。
  底面は接地面の鏡像（自動）、接続は接合部ブレンド（自動）と `finish.bridge_mm`
  （既定＝解析ボクセル1つ、`attach` の非接触殻を埋める）。`info.smooth.bridge_mm3` が
  実際に埋めた量。埋まらないときは study に `attach` があるか（＝殻があるか）を見る。
- **利用者が「既存の部品を取合いに差し替えて」と言ってきたら、まず3つ測る**（2026-09-10、
  tall_tripod v7、lessons-learned #96〜#99）。(1) **その部品は解析ドメインに収まるか**——
  `add` は設計空間の外に出られるので、以前は壁で黙って切られていた（いまは
  `finish.cover_pad` が広げ、`info.grid_grown_voxels` に量が出る。0 でなければ
  「FEA が見ていない mm を出荷している」と書く）。(2) **穴を持つ完成ソリッドなら
  `finish.cut` は空になる**ので、凹形状は `cut` と逆向き（内側）に丸まる——
  ナット座は finish 1.0 mm で 8.20 → 深さ 3.65 mm で 7.62 まで痩せた。**0.5 mm にすれば
  ソースと一致する。** (3) **荷重領域・obstacle は旧取合いのまま**なので、
  どこが空振りになったかを名指しで書く。
- **取合いが「相手の完成部品」なら、設計空間をその部品の手前で止める**（2026-09-10、
  tall_tripod v8）。設計空間を取合いの**上端まで**伸ばすと、カメラの体積・工具の進入路・
  部品どうしの隙間を全部 `obstacle` で守る羽目になり、`attach` で「どの面に繋いでよいか」も
  書く必要が出ます。設計空間を**取合いの底面で止めれば、そのどちらも要りません**
  ——守りたい空間はそもそも自由セルにならないので。v8 は obstacle も attach も 0 件です。
- **`tall_tripod.scad` は v8 以降 study の入力ではありません。** 設計空間は
  `02_setup/make_v8_design_space.py` が取合いの STL から導出します。.scad の頭部と assert は
  もう三脚に載っていない部品の話なので、そこを編集しても run は変わりません。
- **造形時間・質量・「載るか」を報告する前に、どの STL を測ったか確かめる。**
  2026-09-10 まで `topopt run` の `slice` と造形範囲の表は `outputs["stl"]`
  （＝publish が `*_raw.stl` と名付けている解析グリッドの本体）を測っていた。
  実測：raw 6h25m / 188.89 g に対し出荷 7h14m / 303.25 g。`ccx` だけは raw のままが正しい。
- **`04_print/preview_print.png` が無い run がある**（出荷メッシュが 40 万三角形超）。
  その run は**前の絵を消してある**ので「publish が古い」ではない。PRINT.md に理由が書いてある。
- **`04_print/` に `*_smooth.stl` があったら、それは finish 段より前の run。** このファイルは
  preserve ごと丸めるので取合いが潰れている。利用者が「平滑化で部品が壊れた」と言うとき、
  これを開いていることがある。
- **「組み立てられない」と言われたら、逃がしが `finish.cut` に入っているかを最初に見る**
  （lessons-learned #92）。obstacle にしか書いていない逃がしは **preserve を退けないので
  袋穴になる**。判定は目視ではなく数値で：ポケットの口の各ラインに対して外向きに材料が
  残っていないかを数える。実測で tall_tripod v5 は 3,228 本中 758 本が塞がっていた。
- **`--repair`（MeshFix）の後は体積を確認する。** 稜線だけで接するボクセルの「つまみ」を
  MeshFix は切って捨てる（tall_tripod で 21% 喪失）。抽出段が `heal_pinches` で塞ぐので、
  今日以降の抽出結果には無いが、古い run の STL には残っている。

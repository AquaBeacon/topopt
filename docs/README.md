# docs — 索引

このリポジトリのドキュメントは、**日本語＝プロジェクトの方針**、**英語＝実装のリファレンス**で分かれている。

## プロジェクトの方針（日本語・このフォルダ）

| ファイル | 内容 | いつ読むか |
|---|---|---|
| [workflow.md](workflow.md) | 設計手順ノート。実測 → 荷重決め → 断面 → 造形性 → FEA → 最適化 → 再構築 | 実際に部品を設計するとき |
| [underwater-design.md](underwater-design.md) | 新規部品の荷重表・初期モデル・水中荷重・サポート選択 | 水中ブラケット等へ展開するとき |
| [spec.md](spec.md) | 仕様と受け入れ条件。実装の契約書 | 何を作るか決めるとき／完了判定のとき |
| [architecture.md](architecture.md) | 二つの解析経路（ボクセル / CalculiX）、モジュール地図、移行方針 | どこを触るか決めるとき |
| [lessons-learned.md](lessons-learned.md) | 過去に実際に踏んだ失敗と、そこから来た規律 | 規則を緩めたくなったとき |
| [known-issues.md](known-issues.md) | 分かっているのに直っていないことの台帳 | 変な挙動に当たったとき／次に何を直すか決めるとき |
| [FFF_PLA_PETG_orthotropic_material_reference.md](FFF_PLA_PETG_orthotropic_material_reference.md) | PLA / PETG の直交異方性材料カード（弾性9定数＋強度9個）と、その出所・検証規則 | 材料値を疑うとき／別の材料を足すとき |

## 実装のリファレンス（英語・`../topopt/docs/`）

| ファイル | 内容 |
|---|---|
| [../topopt/docs/design-space.md](../topopt/docs/design-space.md) | 設計空間モデル（preserve / obstacle / design_space / starting_shape）の全仕様・全ノブ・study スキーマ |
| [../topopt/docs/topology-optimization.md](../topopt/docs/topology-optimization.md) | 最適化結果を「印刷できる部品」にするまでの4段階と、その実測根拠 |
| [../topopt/docs/output-schema.md](../topopt/docs/output-schema.md) | 各コマンドが書く出力 JSON のキー表（型・required・意味・書く/読むコマンド）。`outschema.py` から生成、手で編集しない |

## 直下のファイル

| ファイル | 内容 |
|---|---|
| [../README.md](../README.md) | 人間向けの概要・構成・セットアップ・使い方 |
| [../AGENTS.md](../AGENTS.md) | 全 AI エージェント共通の作業規約・検証ループ・ドキュメント同期規約 |
| [../CLAUDE.md](../CLAUDE.md) | Claude Code 固有の補足（AGENTS.md の薄いラッパ） |
| [../topopt/README.md](../topopt/README.md) | ツール（CLI）の説明書 |
| [../osmo_tripod/osmo_tripod.md](../osmo_tripod/osmo_tripod.md) | 実物の3脚ホルダー：荷重ケース・検証結果・印刷設定 |
| [../tall_tripod/README.md](../tall_tripod/README.md) | **検証用**の背の高い3脚：なぜ背を高くしたか・限界・使い方 |

## モデルごとのフォルダ（spec §17.5）

**1モデル1フォルダ、共用しない。** 中は実行順の4段階。

| | 中身 | 誰が見るか |
|---|---|---|
| `01_assumptions/` | `assumptions.md` — 荷重・材料・機械・何が事前に決まっていたか | 査読者・共著者・1年後の担当 |
| `02_setup/` | 形状 STL と検証プレビュー | 設計空間を疑うとき |
| `03_optimization/` | 密度場・収束履歴・FEA 図・候補・`report.md` | 結果を疑うとき |
| `04_print/` | **印刷する STL** と `PRINT.md` | 印刷する人 |

`tall_tripod/` が実例。`osmo_tripod/` は移行前の構成のまま（`out/` の記録済み run が回帰の基準値）。
- [mcp.md](mcp.md) — 設計・解析 MCP の導入、ツール、ジョブ管理。
- [validation-20261007.md](validation-20261007.md) — 別部品への展開、実CLI、サポート生成、高速化の検証記録。
- [mcp-evaluation.xml](mcp-evaluation.xml) — MCP 利用エージェント用の読み取り評価問題（自動採点は未実施）。

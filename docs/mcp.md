# 設計・解析 MCP

`topopt_mcp` はローカル stdio サーバーです。既存の全 topopt コマンドを
MCP クライアントから利用できます。解析の計算式・判定・出力形式は CLI と共通です。

## 導入

リポジトリ直下で次を実行します。MCP は任意依存です。

```powershell
python -m venv --system-site-packages .venv-mcp
.\.venv-mcp\Scripts\python.exe -m pip install -e "topopt[all,mcp]"
```

Codex への登録例（パスは実際の設置先に合わせます）：

```powershell
codex mcp add topopt -- C:\Users\ryupc\WorkSpace\Mitou26\AquaBeacon-OSS\topopt\.venv-mcp\Scripts\python.exe -m topopt.mcp_server --workspace C:\Users\ryupc\WorkSpace\Mitou26\AquaBeacon-OSS\TO_printing
```

登録後、新しいタスクで接続します。ほかの stdio 対応クライアントでも、同じ
Python 実行ファイルと引数をサーバー設定に指定できます。
Codex 設定の仕様は [公式ドキュメント](https://developers.openai.com/codex/mcp/) を参照。
SDK は [公式 Python SDK v1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x) を使用し、
API 互換性のため依存を `<2` に固定しています。

## 操作

新規部品の入力例は`examples/underwater_bracket/study.json`、荷重を決める手順は
[underwater-design.md](underwater-design.md)。`topopt_start`の引数配列から
`run --supports tree --layer-mm 0.2 --bed-type "Textured PEI Plate"`を指定でき、材料はstudyから引き継ぐ。プレートは材料に適合するものを選ぶ。MCPジョブのCLI引数にも同じ旗を渡せる。
CCXは完成STLが既定。解析形状の比較を意図する場合は`--ccx-geometry analysis`を追加する。

| ツール | 用途 |
|---|---|
| `topopt_help` | コマンド一覧と、各コマンドの最新の引数 |
| `topopt_list` | モデル・成果物・保存済みジョブのページ付き一覧 |
| `topopt_read` | UTF-8の部分読み取り、またはJSONのトップレベル項目選択 |
| `topopt_write_study` | study JSON の作成・更新（既存ファイルは明示上書き） |
| `topopt_start` | 任意の topopt コマンドをバックグラウンド起動 |
| `topopt_job` | 終了コード、差分ログ、最大30秒の変化待ち |
| `topopt_preview` | PNG を MCP 画像として取得 |

例えば `topopt_start` に次を渡します。

```json
{"command":"setup","arguments":["osmo_tripod/gd_mount_study.json","--out",".topopt-mcp/check","--no-estimate"]}
```

返った `job_id` を `topopt_job` に渡して結果を確認します。実際の設計では
`--no-estimate` を外し、実測の費用見積もりとプレビューを確認してから最適化へ進みます。
`run`、`opt`、`fea`、`ccx`、`extract`、`slice` なども同じ形式です。
引数はトークンごとの配列で、シェルの引用符やワイルドカード展開は不要です。

## 必要な情報だけ取得する

最初のジョブ確認は既定で末尾2,000バイト（最大50,000バイト）。次回は返された
`log_offset`を渡すと新しいバイトだけが返ります。`wait_seconds: 30`を併用すると、
新しいログまたは完了記録を最大30秒待ち、無変化の短間隔ポーリングを減らせます。
`log_more: true`なら残りを同じ方法で読みます。`log_restarted: true`はログが短くなり
先頭に戻ったことを示します。`tail_bytes: 0`ならログ本文なしで状態を確認できます。

```json
{"job_id":"<返されたID>","log_offset":1234,"wait_seconds":30,"tail_bytes":2000}
```

JSONは`json_keys: []`でトップレベルキーを確認し、必要な項目だけ選べます。
値はそのまま返し、欠落キーは`missing_keys`へ記録します。容量を超える項目選択は
切り詰めずエラーにするため、項目数を減らすか部分読み取りを使います。

```json
{"path":"model/03_optimization/report.json","json_keys":["verdict","fail","unknown","pass"]}
```

最初に既存の`run_summary.json`と`report.json`を確認し、症状に関係する結果・仕様の節へ
進んでください。仕様書全体や最適化の`history`を毎回取り込む必要はありません。
数値の計算・合否判定はCLIのままで、MCPの項目選択は検査を追加しません。

同じ条件の探索結果を仕上げる際は、保存された密度場からextractと後段を実行できます。
`run --only extract,ccx,print,slice,report,publish`はoptを再実行しませんが実行要約とmanifestを
書き換えるため、元の記録を保存して由来を対応付けます。条件変更時は新しい出力先を使います。
元の解像度・材料モデル・ソルバ等の設定も照合し、必要な引数を後段へ渡してください。
`run --converge`はCCXの収束検査で、既定OFFです。現行版は完成STLを再解析します。
`--ccx-geometry analysis`や過去のrunは別形状を見ている場合があるため、STLの内容ハッシュを確認します。

## 実行と判定の境界

- 相対パスの基準は `--workspace`。モデルごとにフォルダを分け、study を出力先に置きません。
- ログと終了記録は `.topopt-mcp/jobs/<job_id>/` に残ります。サーバーを再接続しても取得できます。
- MCP 切断では起動済みジョブを停止しません。OS 終了・強制終了まで保証するものではありません。
  完了記録がない場合は `pending_or_running` とし、成功と推定しません。
- 自動的なキャンセル・同時実行数制限はありません。重い解析を重複起動しないようにしてください。
- 終了コード 0 はそのコマンドの成功、1 は否定的所見、2 は入力エラー、3 は外部ツール不足。
  部品の構造合格は report・安全率・メッシュ収束等を別途確認します。
- ファイル操作ツールは workspace 内に限定します。ただし CLI のパス引数と study 内の参照は
  CLI と同じ権限です。これはローカルの信頼できる入力向けで、サンドボックスではありません。
- CAD の作成・編集は既存の CAD ツールで行います。この MCP は topopt の設計条件・解析・出力を扱います。

## 検証

```powershell
.\.venv-mcp\Scripts\python.exe -m pytest topopt/tests/test_mcp_server.py
```

実 stdio 接続、ツール列挙、study 上書き防止、パス検査、ジョブの再接続と終了コードを確認します。

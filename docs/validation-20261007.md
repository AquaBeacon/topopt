# 別部品への展開に向けた検証（2026-10-07）

ソフトウェアの動作確認記録です。実部品の強度や耐水性の認証ではありません。
検証出力はリポジトリ内の`.topopt-mcp/completion-20261007/`に保存しました（Git管理外）。

最終回帰：**726 passed、4 skipped、10 warnings、297.94秒**。PETGと指定プレートの
実スライス、生成G-codeのプレート設定照合も含みます。指定先のtopopt-pipelineスキルは
quick_validateで妥当と判定され、Codexの既存topopt MCP登録も確認しました。

## 再現方法

リポジトリ直下から、セットアップ済みの`.venv-mcp/Scripts/python.exe`を使用しました。
pytestは`topopt/`を作業ディレクトリとして実行します。

```powershell
cd topopt
../.venv-mcp/Scripts/python.exe -m pytest --disable-warnings --junitxml=../.topopt-mcp/completion-20261007/pytest.xml
```

自己診断は直下から`python topopt/tests/smoke.py`。MCPのstdio接続・ツール呼出しは
`test_mcp_server.py`、非同期ジョブは実際のブラケット探索・抽出・osmoのCCXでも確認しています。

## 実行結果と限界

| 対象 | 結果 | 読み方 |
|---|---|---|
| ブラケット例、4 mm、CPU、80反復、quick | 約61秒で完走。粗い断面と反復上限のため不合格 | 荷重も仮定。印刷承認用の設計ではない |
| 同じ保存密度から修正後の抽出 | 未支持セル0、pinch0、閉じた1ボディ | 支持を壊す接続補修を修正。最適化は再実行していない |
| 上記のPETG／樹状／0.2 mm／Textured PEI Plate | 予測47分20秒、19.17 g、サポート1.955 g（10.2%）、120層 | 実スライサーを実行した予測値。プリンターでの実測ではない |
| osmo、CalculiX、4 mm殻、repair、isotropic | 6ケース中4 PASS・2 FAIL | 正しく不合格を検出。最悪のp99.5安全率1.147／最悪要素0.540。収束確認は別途必要 |
| 自己診断 | 全チェックPASS | 回帰一致だけで実部品の正しさは保証しない |

サポート量はG-codeのSupportとSupport interfaceを積算し、全featureの合計が
スライサーの総フィラメント長を再現することを確認しました（差約0.0001%）。
完成形状の支持除去性・穴や座面への接触は自動認証せず、3mfで確認する項目です。

## 高速化の測定

```powershell
python topopt/tests/benchmark_underwater.py
```

80³セルの静水圧要約で、節点力配列を作る既存経路と、面から直接集計する要約経路を比較。
ウォームアップ後5回の中央値は13.759 ms → 9.601 ms（**1.43倍**）、
tracemallocのピークは19,927,544 → 2,817,928 bytes（約86%減）でした。
返す物理量の一致をassertしています。これは静水圧要約だけの改善で、最適化全体の倍率ではありません。

小さなブラケットではCPUのsetupプローブがCUDA初回より速かったため、探索はCPUで実行しました。
CUDA初回は初期化を含むので、その見積もりを最適化の定常速度と比較しません。
汎用手順では`setup --solver ...`で測り、採用後に保存密度から後段を再開します。

## 残る実部品の確認

水中部品の実機CAD、推力・反トルク・テザー荷重、濡れた材料データ、メッシュ収束、
サポート除去と締結、実物試験は用途ごとに必要です。`underwater-loads`は概算であり、
耐圧容器の分布外圧をstudyへ自動投入する機能はありません。
設計手順は[underwater-design.md](underwater-design.md)、既知の範囲は[known-issues.md](known-issues.md)を参照してください。

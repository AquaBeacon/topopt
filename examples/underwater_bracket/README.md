# 水中部品へ展開するための例

フレームに固定するスラスターブラケットを題材に、設計空間・取合い・ケーブルの逃げと複数荷重ケースを設定する例です。箱で作った共通グリッドから材料を減らし、前進・後進・横荷重・反トルクを同時に評価します。

**寸法・荷重は手順検証用の仮定で、特定製品の仕様ではありません。** ボルト穴や実機取合いは未作成です。実部品には取合いCAD、使用電圧での推力、反トルク、水中重量とその作用点を入れ直してください。

リポジトリ直下から、まず配置と所要時間を確認します。

```powershell
python -m topopt setup examples/underwater_bracket/study.json --out examples/underwater_bracket/02_setup --iters 80
python -m topopt run examples/underwater_bracket/study.json --model examples/underwater_bracket --quick --iters 80
```

`02_setup/*_setup.png` と `03_optimization/report.md` を確認します。探索時は独立解析や実スライスを省くため、INCOMPLETEは完成部品の合格を意味しません。

採用候補の仕上げでは元の実行記録を保存し、同じstudyと条件で後段を実行します。

```powershell
python -m topopt run examples/underwater_bracket/study.json --model examples/underwater_bracket --only extract,ccx,print,slice,report,publish --levels 0.5 --converge --supports tree --bed-type "Textured PEI Plate"
```

実設計では[水中部品の設計手順](../../docs/underwater-design.md)に従い、完成形状の再解析、メッシュ収束、サポート除去性、濡れた材料の試験まで確認します。

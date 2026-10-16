# FFF造形PLA・PETG用 直交異方性材料データ（暫定リファレンス）

> **用途**：FreeCAD / CalculiX を中心とした3Dプリント構造解析・トポロジー最適化ツールの初期材料カード  
> **位置付け**：文献に報告された一般的傾向を基にした、保守的な初期値  
> **重要**：特定のフィラメント、プリンター、積層条件を保証する材料値ではない。最終設計では実測値へ置換すること。

---

## 1. 適用範囲

本データは、一般的な未充填PLAおよび未充填PETGをFFF/FDM方式で造形した部品について、次の用途に使用する。

- トポロジー最適化の初期解析
- 造形方向候補の比較
- 荷重経路のスクリーニング
- 等価ソリッドモデルによる予備FEA
- 材料試験前の暫定安全率評価
- PLA案とPETG案の相対比較

次の用途には、そのまま使用しない。

- 人命・安全に関係する最終設計
- 圧力容器
- 長期クリープが支配する部品
- 衝撃・落下を厳密に評価する部品
- 長期水中使用部品
- 低密度インフィルを均質ソリッドとして扱う最終解析
- 特定メーカーのフィラメント性能保証

---

## 2. 座標系

材料座標を次のように定義する。

| 軸 | 定義 |
|---|---|
| X | 主ラスタ方向。荷重方向とフィラメントが平行になる方向 |
| Y | 積層面内でXと直交する方向 |
| Z | 積層方向。層間界面の法線方向 |

この材料カードでは、通常 `X > Y > Z` の順に引張強度・剛性が低下するものとして扱う。

FreeCADまたはCalculiXへ入力する際は、部品の造形方向に合わせて材料座標系を回転させること。

---

## 3. 文献から確認できる傾向

### 3.1 PLA

FFF造形PLAでは、フィラメント方向と荷重方向が平行な試験片が最も強く、垂直方向または層間方向が最も弱い。

報告例：

- 印刷方向に平行な引張強度：57.7 MPa
- 印刷方向に垂直な引張強度：30.8 MPa
- 垂直方向／平行方向の比：約0.53

別の造形方向・ラスタ角研究では、最良条件のPLAで約77.6 MPaが報告され、最弱のUpright条件に対して約58%高い。これは材料銘柄、試験片、造形温度、ラスタ角によって値が大きく変わることを示す。

### 3.2 PETG

PETGも明確な異方性を示すが、PLAより延性が高く、90°および±45°方向でPLAを上回る例がある。0°方向ではPLAの方が高強度となる場合がある。

PLAとPETGを同一手法で比較した研究では、次の傾向が報告されている。

- 0°方向：PLAの最大引張強度がPETGより約21.1%高い
- 90°方向：PETGがPLAより約13.7%高い
- ±45°方向：PETGがPLAより約12.3%高い
- PLA：比較的脆性的
- PETG：比較的延性的

### 3.3 共通傾向

- 造形方向は機械特性を大きく左右する
- 0°方向は一般に最も強い
- Z方向は層間剥離により弱い
- ±45°方向は引張強度最大にはなりにくいが、せん断荷重に適する
- インフィル率、外周数、ノズル温度、積層高さ、印刷速度でも値が変化する
- 文献値だけで完全な直交異方性9定数と全強度値をそろえることは難しい

---

## 4. 暫定材料カードの考え方

以下の値は、公開研究で見られる範囲から大きく外れないようにしつつ、構造解析で過大評価しにくいよう保守的に設定した**合成初期値**である。

値の由来は次の3種類に分ける。

| 種類 | 意味 |
|---|---|
| literature_anchor | 文献で直接報告された代表的な値・比率 |
| conservative_default | 複数文献の傾向から設定した保守的な初期値 |
| estimated | 公開データ不足のため工学的に推定した値 |

解析結果には、必ず `provenance` としてこの区別を残す。

---

# 5. PLA 暫定直交異方性材料カード

## 5.1 推奨初期値

想定条件：

- 未充填PLA
- 室温
- 乾燥状態
- 比較的高密度な造形
- 外周を十分に確保
- 等価ソリッド解析
- 0.4～0.6 mmノズル
- 積層高さ0.15～0.28 mm程度

| 物性 | 記号 | 暫定値 | 単位 | 信頼区分 |
|---|---:|---:|---|---|
| X方向ヤング率 | Ex | 3000 | MPa | conservative_default |
| Y方向ヤング率 | Ey | 2400 | MPa | conservative_default |
| Z方向ヤング率 | Ez | 1500 | MPa | conservative_default |
| XYせん断弾性率 | Gxy | 1000 | MPa | estimated |
| YZせん断弾性率 | Gyz | 550 | MPa | estimated |
| ZXせん断弾性率 | Gzx | 550 | MPa | estimated |
| XYポアソン比 | νxy | 0.35 | - | estimated |
| YZポアソン比 | νyz | 0.30 | - | estimated |
| ZXポアソン比 | νzx | 0.30 | - | estimated |
| X方向引張強度 | Xt | 50 | MPa | conservative_default |
| Y方向引張強度 | Yt | 35 | MPa | conservative_default |
| Z方向引張強度 | Zt | 22 | MPa | conservative_default |
| X方向圧縮強度 | Xc | 60 | MPa | estimated |
| Y方向圧縮強度 | Yc | 50 | MPa | estimated |
| Z方向圧縮強度 | Zc | 40 | MPa | estimated |
| XYせん断強度 | Sxy | 25 | MPa | estimated |
| YZせん断強度 | Syz | 15 | MPa | estimated |
| ZXせん断強度 | Szx | 15 | MPa | estimated |
| 密度 | ρ | 1240 | kg/m³ | typical_bulk_value |

## 5.2 強度比

| 比率 | 値 |
|---|---:|
| Yt / Xt | 0.70 |
| Zt / Xt | 0.44 |
| Ey / Ex | 0.80 |
| Ez / Ex | 0.50 |

Z方向引張強度は、0°方向の約35～60%程度に変動し得る。初期値では44%としている。

## 5.3 YAML

```yaml
material:
  id: pla_generic_fff_orthotropic_v1
  name: Generic FFF PLA
  model: orthotropic_engineering_constants
  condition:
    moisture: dry
    temperature_C: 23
    immersion_hours: 0

  coordinate_system:
    X: primary_raster_direction
    Y: in_plane_transverse_direction
    Z: build_direction

  density_kg_m3: 1240

  elasticity:
    Ex_MPa: 3000
    Ey_MPa: 2400
    Ez_MPa: 1500
    Gxy_MPa: 1000
    Gyz_MPa: 550
    Gzx_MPa: 550
    nu_xy: 0.35
    nu_yz: 0.30
    nu_zx: 0.30

  strength:
    Xt_MPa: 50
    Xc_MPa: 60
    Yt_MPa: 35
    Yc_MPa: 50
    Zt_MPa: 22
    Zc_MPa: 40
    Sxy_MPa: 25
    Syz_MPa: 15
    Szx_MPa: 15

  provenance:
    classification: conservative_literature_based_default
    suitable_for:
      - preliminary_fea
      - topology_optimization
      - orientation_screening
    unsuitable_for:
      - final_certification
      - pressure_vessel
      - long_term_creep
      - impact_certification
    measured_for_specific_printer: false
```

---

# 6. PETG 暫定直交異方性材料カード

## 6.1 推奨初期値

想定条件：

- 未充填PETG
- 室温
- 乾燥状態
- 比較的高密度な造形
- 外周を十分に確保
- 等価ソリッド解析
- 0.4～0.6 mmノズル
- 積層高さ0.15～0.28 mm程度

| 物性 | 記号 | 暫定値 | 単位 | 信頼区分 |
|---|---:|---:|---|---|
| X方向ヤング率 | Ex | 2100 | MPa | conservative_default |
| Y方向ヤング率 | Ey | 1750 | MPa | conservative_default |
| Z方向ヤング率 | Ez | 1150 | MPa | conservative_default |
| XYせん断弾性率 | Gxy | 700 | MPa | estimated |
| YZせん断弾性率 | Gyz | 420 | MPa | estimated |
| ZXせん断弾性率 | Gzx | 420 | MPa | estimated |
| XYポアソン比 | νxy | 0.38 | - | estimated |
| YZポアソン比 | νyz | 0.34 | - | estimated |
| ZXポアソン比 | νzx | 0.34 | - | estimated |
| X方向引張強度 | Xt | 42 | MPa | conservative_default |
| Y方向引張強度 | Yt | 33 | MPa | conservative_default |
| Z方向引張強度 | Zt | 25 | MPa | conservative_default |
| X方向圧縮強度 | Xc | 50 | MPa | estimated |
| Y方向圧縮強度 | Yc | 42 | MPa | estimated |
| Z方向圧縮強度 | Zc | 35 | MPa | estimated |
| XYせん断強度 | Sxy | 20 | MPa | estimated |
| YZせん断強度 | Syz | 14 | MPa | estimated |
| ZXせん断強度 | Szx | 14 | MPa | estimated |
| 密度 | ρ | 1270 | kg/m³ | typical_bulk_value |

## 6.2 強度比

| 比率 | 値 |
|---|---:|
| Yt / Xt | 0.79 |
| Zt / Xt | 0.60 |
| Ey / Ex | 0.83 |
| Ez / Ex | 0.55 |

PETGはPLAよりX方向の剛性・強度が低いことが多い一方、層間方向や斜交方向で相対的に強く、延性も高い傾向がある。

## 6.3 YAML

```yaml
material:
  id: petg_generic_fff_orthotropic_v1
  name: Generic FFF PETG
  model: orthotropic_engineering_constants
  condition:
    moisture: dry
    temperature_C: 23
    immersion_hours: 0

  coordinate_system:
    X: primary_raster_direction
    Y: in_plane_transverse_direction
    Z: build_direction

  density_kg_m3: 1270

  elasticity:
    Ex_MPa: 2100
    Ey_MPa: 1750
    Ez_MPa: 1150
    Gxy_MPa: 700
    Gyz_MPa: 420
    Gzx_MPa: 420
    nu_xy: 0.38
    nu_yz: 0.34
    nu_zx: 0.34

  strength:
    Xt_MPa: 42
    Xc_MPa: 50
    Yt_MPa: 33
    Yc_MPa: 42
    Zt_MPa: 25
    Zc_MPa: 35
    Sxy_MPa: 20
    Syz_MPa: 14
    Szx_MPa: 14

  provenance:
    classification: conservative_literature_based_default
    suitable_for:
      - preliminary_fea
      - topology_optimization
      - orientation_screening
    unsuitable_for:
      - final_certification
      - pressure_vessel
      - long_term_creep
      - impact_certification
    measured_for_specific_printer: false
```

---

# 7. PLAとPETGの比較

| 項目 | PLA | PETG |
|---|---:|---:|
| X方向ヤング率 | 3000 MPa | 2100 MPa |
| Z方向ヤング率 | 1500 MPa | 1150 MPa |
| X方向引張強度 | 50 MPa | 42 MPa |
| Z方向引張強度 | 22 MPa | 25 MPa |
| X方向剛性 | 高い | 中程度 |
| 層間方向の相対強度 | 低い | PLAよりやや高い |
| 延性 | 低～中 | 中～高 |
| 衝撃への適性 | PETGより低い傾向 | PLAより高い傾向 |
| クリープ | 要注意 | より要注意 |
| 寸法安定性 | 比較的良い | 荷重・温度で変形しやすい |
| 水中候補 | 短期試験向き | 靭性面では有利だが吸湿・クリープ評価が必要 |

設計上の単純な使い分け：

- 剛性、寸法保持、軽荷重での変位抑制を優先：PLA
- 層間破壊、衝撃、割れにくさを優先：PETG
- 長期荷重：どちらもクリープ試験が必要
- 水中：乾燥状態の材料カードだけで最終判断しない

---

# 8. インフィル率補正

上記材料カードは、比較的高密度な等価ソリッドを想定する。低密度インフィルへ単純適用してはならない。

MVPでは、次の2方式を用いる。

## 8.1 Equivalent Solid Mode

実際の印刷条件を代表する低減係数を掛ける。

暫定式：

```yaml
equivalent_solid_reduction:
  modulus_factor: 0.55
  tensile_strength_factor: 0.60
  compressive_strength_factor: 0.65
  shear_strength_factor: 0.55
```

30%インフィルでこの係数を一律採用することは、あくまで初期スクリーニング用である。外周数が多い薄肉部品では過小評価、厚いコア依存部品では過大評価になる可能性がある。

## 8.2 Shell-Core Mode

推奨方式。

- 外周領域：上記の高密度材料カード
- インフィル領域：インフィル率・パターンに応じた等価材料
- 上下面：外周とは別領域として必要に応じてモデル化

---

# 9. 水中使用時の初期低減係数

PLA、PETGともに、水中使用時は乾燥状態と同一の材料カードを使わない。

試験データがない段階では、次の保守的なシナリオを別ケースとして計算する。

```yaml
environmental_scenarios:
  dry:
    modulus_factor: 1.00
    strength_factor: 1.00

  wet_short_term:
    description: 24_to_72_hours_immersion
    modulus_factor: 0.90
    strength_factor_xy: 0.90
    strength_factor_z: 0.80
    shear_strength_factor_interlayer: 0.80

  wet_conservative:
    description: unknown_or_longer_immersion
    modulus_factor: 0.80
    strength_factor_xy: 0.80
    strength_factor_z: 0.65
    shear_strength_factor_interlayer: 0.65
```

これらは一般文献の直接測定値ではなく、実測までの保守的な解析シナリオである。

---

# 10. CalculiX入力時の注意

## 10.1 弾性定数

CalculiXへ直交異方性材料として入力する場合、使用する定数系と入力順を実装側で検証する。

材料カード生成時に次を確認する。

- 剛性行列が正定値である
- ポアソン比の相反関係が成立する
- 単位がMPaに統一されている
- 材料座標系が造形方向と一致する
- X/Y/Zの入れ替えが出力に反映される

相反関係：

\[
\frac{\nu_{xy}}{E_x}=\frac{\nu_{yx}}{E_y}
\]

同様にYZ、ZXについても成立させる。

## 10.2 強度判定

CalculiXの応力出力から、材料座標系で次を判定する。

```text
Xt / Xc：X方向引張・圧縮
Yt / Yc：Y方向引張・圧縮
Zt / Zc：Z方向引張・圧縮
Sxy：XY面内せん断
Syz：YZ層間せん断
Szx：ZX層間せん断
```

MVPでは最大応力則を基本とし、後からTsai–Hill、Tsai–Wu、Hashin系判定を追加できる構成とする。

---

# 11. 推奨安全率

暫定材料カードには不確かさが大きいため、通常の実測済み材料より高い安全率を要求する。

| 条件 | 最小安全率 |
|---|---:|
| 初期トポロジー探索 | 2.0 |
| 暫定材料カードによる通常荷重 | 4.0 |
| 暫定材料カードによる水中通常荷重 | 5.0 |
| 実測材料カードによる通常荷重 | 3.0 |
| 異常荷重 | 1.5以上。ただし破壊許容の定義が必要 |
| 落下・衝撃 | 等価静荷重だけで合格判定しない |

---

# 12. ツールでの材料カード検証規則

```text
FAIL: Ex, Ey, Ezのいずれかが0以下
FAIL: Gxy, Gyz, Gzxのいずれかが0以下
FAIL: ポアソン比が物理的範囲外
FAIL: 剛性行列が正定値でない
FAIL: 材料座標系が未定義
FAIL: 強度値にnullがある状態で安全率を計算
FAIL: literature/default/measuredの区分がない
FAIL: 乾燥値を水中最終設計へ無警告で使用
FAIL: 低密度インフィルを100%ソリッドとして無警告で使用
```

警告：

```text
WARN: measured_for_specific_printer=false
WARN: immersion condition differs from material card
WARN: layer height differs from material card
WARN: perimeter count differs from material card
WARN: infill differs from material card
WARN: nozzle diameter differs from material card
```

---

# 13. 実測値への置換優先順位

すべてを一度に測定できない場合は、次の順に置換する。

1. Z方向引張強度 `Zt`
2. X方向引張強度 `Xt`
3. Z方向ヤング率 `Ez`
4. X方向ヤング率 `Ex`
5. 層間せん断強度 `Syz`, `Szx`
6. Y方向引張強度 `Yt`
7. XYせん断強度 `Sxy`
8. 圧縮強度
9. ポアソン比
10. 浸水後の各値

水中用フレームでは、`Zt`、`Syz`、`Szx`、浸水後低減を優先する。

---

# 14. 参考文献

1. *The impact of defects on tensile properties of 3D printed parts manufactured by fused filament fabrication*. Materials Today Communications, 18, 140–148, 2019. PLAの印刷方向平行57.7 MPa、垂直30.8 MPaを報告。

2. *Influence of layer orientation on the mechanical properties of fused deposition modelling using PLA and PETG*. 2025. PLA・PETGを0°、±45°、90°で直交異方性材料として比較し、弾性率、ポアソン比、引張強度および破壊形態を評価。

3. *Characterization of the non-isotropic tensile and fracture behavior of unidirectional polylactic acid parts manufactured by material extrusion*. Additive Manufacturing, 2022. PLAの0～90°方向について引張・破壊特性を評価。

4. *Effects of extruder temperatures and raster orientations on mechanical properties of the FFF-processed polylactic-acid material*. Rapid Prototyping Journal, 2021. 0°が最強、90°が最弱、0°/90°および±45°が中間となる傾向を報告。

5. *Material Anisotropy in Additively Manufactured Polymers and Polymer Composites: A Review*. Polymers, 13(19), 3368, 2021. FFF高分子の造形方向依存性を整理。

6. *The Influence of the Process Parameters on the Mechanical Properties of PLA Specimens Produced by Fused Filament Fabrication—A Review*. Polymers, 14(5), 886, 2022. PLAの積層方向、層厚、ラスタ等による物性変化を整理。

7. *Optimization of Manufacturing Parameters and Tensile Specimen Geometry for FDM 3D-Printed PETG*. Materials, 14(10), 2556, 2021. PETGの代表的等方性仮定値としてヤング率2150 MPa、ポアソン比0.35を使用。

---

# 15. 最終判断

この材料カードは、解析ツールのMVPとトポロジー最適化の初期検証には使用できる。

ただし、数値は次の順に信頼性が異なる。

```text
特定条件での実測値
> 同一材料・類似条件の文献値
> 複数文献を基にした保守的初期値
> 工学的推定値
```

本ファイルのPLA・PETGカードは主に「保守的初期値」と「工学的推定値」で構成される。解析レポートには必ず「暫定材料カード使用」と表示し、最終設計では実測値へ置換すること。

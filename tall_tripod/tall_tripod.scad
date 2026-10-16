// =====================================================================
// tall_tripod.scad — トポロジー最適化の検証用モデル（背の高い3脚）
//
// **v8 (2026-09-10) 以降、このファイルは study の入力ではありません。**
//   カメラ固定部は子機用の別部品（`dji_osmo_action6_mount.stl`）に差し替わり、
//   設計空間はその部品から導出されます（`02_setup/make_v8_design_space.py`）。
//   ここに書いてある頭部（3枚爪・ヒンジ・台座）と、それを前提にした assert
//   （`z_hinge + finger_r <= ds_top` など）は、**いま三脚に載っていない部品**の話です。
//   残してあるのは v3〜v7 の記録と、`tall_mount_solid.stl` / `tall_mount_cuts.stl`
//   が何だったかを説明するためだけ。**ここを編集しても v8 の run は変わりません。**
//   取合いを変えたいときに触るのは
//   `mechanical/parts/child/exterior/printed/dji_osmo_action6_mount/` のほうです。
//
// これは何か
//   実物の部品ではなく、**トポロジー最適化を試すための検証用モデル**。
//   osmo_tripod.scad（実際に印刷して使っている低重心3脚）はそのまま残す。
//   こちらはカメラ位置を約 15 cm 高くしてあり、その分だけ曲げモーメントが
//   一桁大きくなる。docs/spec.md §5.3 の記録に対応する。
//
// どう動いているのか
//   上端に「カメラを留める頭部」、その周り全部を「材料を置いてよい空間」、
//   下端の床を「接地してよい面」として渡す。**脚を何本どこに出すかは
//   書いていない**——それを決めるのが最適化の仕事。
//
// 座標系: Z 上、Z=0 が接地面 = 印刷1層目。カメラは +Y を向く。
//         ヒンジ軸は X 方向。osmo_tripod.scad と同じ。
//
// 出力（part= で選ぶ）
//   preserve      絶対に残す領域＝カメラ頭部だけ。**脚も接地パッドも無い**
//   design_space  最適化が材料を置いてよい空間＝単純な円柱1つ
//   obstacle      絶対に空けておく領域（カメラの首振り＋ネジを回す空間）
//   mount_solid   頭部の中実エンベロープ（finish 段で足し戻す形）
//   mount_cuts    頭部に開ける穴（finish 段で引く形）
//   reference     人が形を見るための参考形状（最適化の入力ではない）
// =====================================================================
/* [Build] */
part = "reference";  // [reference, preserve, design_space, obstacle, camera_swing, screw_access, mount_solid, mount_cuts, mount_finished]

/* [Printable volume — 最適化を回す前に効かせる制約] */
// **設計空間が印刷範囲より大きければ、最適化の結果は必ず印刷できない。**
// これは解いたあとに分かることではなく、モデルを描いた時点で分かること。
// だから .scad 側でも assert する（topopt setup 側でも同じ検査が走る）。
// 既定は Bambu Lab X1-Carbon の実用範囲 250^3。機械を変えるならここを変える。
bed_xy       = 250.0;
bed_z        = 250.0;
// ブリム・エレファントフット補正・プレート端の食いつきに食われる分。
print_margin = 5.0;

/* [Case interface — osmo-action-6.step の実測値。変更禁止] */
// **出典はリポジトリ直下の `osmo-action-6.step`**（DJI Osmo Action 6 のケース、
// MANIFOLD_SOLID_BREP 'Shaks case DJI osmo action 6'）。
// 以前は「実測値」とだけ書いてあり、どこから来た数字か追えなかった。
// 下はすべて STEP の解析エンティティと頂点座標から読んだ値で、
// カッコ内がその根拠。ケース原点は本体底面中央、ヒンジは z = -11。
//
//   爪の板 2枚          x = [-4.5, -1.5] と [1.5, 4.5]（頂点座標）
//     → 板厚 3.00、板間 3.00、外幅 9.00
//   ピボット穴          CYLINDRICAL_SURFACE r=2.50 軸 X @ (y=0, z=-11) → Ø5.00
//   爪先の丸み          CYLINDRICAL_SURFACE r=7.00、中心 y=±0.5 @ z=-11
//     → ヒンジ軸から見た**外接半径は 7.50**（0.5 + 7.0）。爪先は z=-18 まで。
//   ヒンジ落とし込み    ケース底面 z=0 からヒンジ軸 z=-11 → 11.00
//   ケース外形          x -39.953..40.000 / y -16.300..20.000 / z 0..54.041
case_prong_t    = 3.00;
case_prong_gap  = 3.00;
case_pivot_d    = 5.00;
case_hinge_drop = 11.00;
// ヒンジ軸まわりの**外接**半径。爪先の丸み R7.0 は y=±0.5 を中心にしているので
// 実効半径は 7.5。メス溝はこれを逃がさなければならない。
case_prong_r    = 7.50;
case_prong_tip  = 7.00;   // ヒンジ軸から爪先までの距離（z=-18）
case_front_y    = 20.00;
case_back_y     = 16.30;
case_h          = 54.041;
case_w          = 79.953;
tilt_max        = 30;

/* [Fits — **v6 (2026-09-09) で全部広げた**] */
// **なぜ広げたか。** v5 の実物は「ケースが付かない」と報告された。
// 直接の原因はナットポケットが塞がっていたこと（下の [Screw] 参照）だが、
// 取合いのすきま自体も FFF には薄い。ここは「手で嵌まること」が要求で、
// **がたつきはネジで締めれば消える**——締結が位置決めをするので、
// 溝のすきまは精度ではなく組立性の値でよい。
//
// 目標：噛み合う面の**合計あそびを 1.0 mm 以上**にする。
// FFF は外形が 0.1〜0.25 mm 太り、溝が同じだけ細るので、
// 図面 0.30 のすきまは実物で消えることがある。
//
//   中央爪 ↔ ケースの爪間 3.00     : 2 * fit_centre = 1.00 のあそび
//   ケースの爪 3.00 ↔ メス溝       : fit_centre + fit_outer = 1.20 のあそび
fit_centre = 0.50;
fit_outer  = 0.70;
lead_c     = 0.50;
// M5 ボルトの通し穴すきま。5.00 + 0.80 = Ø5.80。
// **ボルトは位置決めではなく締結**。穴をきつくすると、印刷の反りぶんだけ
// 3枚の爪の穴が食い違ったときに通らなくなる。
bolt_fit   = 0.80;
// メス溝の半径すきま。爪の外接半径 7.50 に対して 1.50 逃がす。
// 爪が溝の底に当たると、ピボット穴どうしが合う前に止まる。
slot_fit_r = 1.50;

/* [Screw — ネジ部とネジを回すための空間。ここは広めに取る] */
// **要求：「ネジ部＋ネジで固定するためのクリアランスを必ず広めに」。**
// 締結は M5 の蝶ネジ（ノブ）＋ナット。効くのは3つで、性格が違う。
//
//   1. ナットのポケット   ——「入るか」。二面幅の公差ぶんを見る
//   2. ノブの逃げ         ——「指が入って回せるか」。ここが一番忘れられる
//   3. 工具・指の進入路   ——「ナット側にレンチが届くか」
//
// 1 は部品の穴なので CAD の寸法どおりに出る（finish 段が切る）。
// **2 と 3 は部品の外にある空き空間なので、最適化に obstacle として渡さないと
// 埋められる。** v3 では設計空間が頭部より下で終わっていたので埋めようが
// なかったが、v4 は頭部の周りが全部設計空間になった。だから宣言する。
//
// **v6 (2026-09-09) で直した実物の欠陥。** 3 は obstacle には入っていたが
// `mount_cuts` に入っていなかった。obstacle は「最適化が材料を置かない場所」で、
// **preserve（台座）そのものは退けない**。台座は爪より X に広い
// （z=136 で ±11.30 に対し爪は ±11.20、z=132 では ±11.87）ので、
// ナットポケットの口は台座の中に埋まっていた——**深さ 4.2 mm の袋穴**で、
// ナットが入らず締結できない。これが「ケースが付けられない」の直接の原因。
// `osmo_tripod.scad` の `knob_clearance()` は最初から両側を切っている。
// **逃がしは obstacle と cut の両方に書く。** 片方だけでは足りない。
nut_af     = 9.00;   // M5 ナット二面幅 8.00 公称 (max 8.10) に 0.90 の逃げ
nut_depth  = 4.20;   // ナット厚 4.00 に 0.20
knob_d     = 22.0;
knob_fit   = 1.50;   // 片側 1.50 mm。v3 の 0.75 から拡大
knob_depth = 16.0;   // 指が掛かる深さ。v3 の 12.0 から拡大
// ナット側の工具・指の進入路。ノブ側と同じ考え方で、こちらは細くて深い。
tool_r     = 9.0;
tool_depth = 16.0;
// **公称面をまたぐ穴は、面のぶんだけ余分に伸ばす。** 0 で終わる穴は、
// ブーリアンの同一面や finish 段のボクセル化で「膜」を残すことがある。
// 実測：v5 の出荷 STL は平滑化で頭部の面が 0.25 mm 外へ動き、
// 面ちょうどで終わっていたナットポケットに膜が張っていた。
cut_over   = 1.0;

/* [Mount head — preserve はこれだけ] */
// 接地面からヒンジ軸までの高さ。**これが検証モデルの本題**。
// v3 から動かしていない。設計空間の作り方を変えた効果だけを見たいので、
// 高さを同時に動かすと何が効いたのか分からなくなる。
z_hinge      = 136.0;
finger_r     = 9.50;
fin_head_t   = 4.00;
// **ナット側の爪は「ポケットの床」が決める。** ナットは締めると床を押す。
// 床の厚み = fin_nut_t - nut_depth。v5 は 6.00 - 4.20 = 1.80 mm で、
// `osmo_tripod.scad` が「締めると割れる」として置いている下限 2.50 を
// 満たしていなかった（そちらには assert があり、こちらには無かった）。
// 7.00 - 4.20 = 2.80 mm。下の assert で固定する。
fin_nut_t    = 7.00;
// マウント頭部の台座。**preserve はここまで。** 支柱も脚も preserve ではない。
head_h       = 26.0;
// 台座の上面は爪を覆う（覆わないと爪の付け根が宙に浮く）。爪が X に伸びた
// ぶんだけ広げる：fin_nut_x1 = 12.20 なので 12.3。下面は変えない——
// **`attach` の面（台座の底 15 x 15）が変わると最適化の入力が変わる。**
ped_top_x    = 12.3;
ped_top_y    = 9.5;
ped_bot_x    = 15.0;
ped_bot_y    = 15.0;

/* [Design space — 単純に、広く] */
// **v4 で作り直した部分。**
// v3 の設計空間は「円板＋上に細る錐台」で、外形がほぼ答えを決めていた。
// 支柱の太さも脚の付け根の位置も、最適化ではなく CAD が決めていたことになる。
// v4 はプレートに載る**円柱ひとつ**。中をどう使うかだけが問題になる。
//
// 半径はプレートの限界そのもの。高さは頭部の頂点まで——ヒンジより上も
// 設計空間にしてあるので、最適化はカメラを覆う形も作れる（そのぶん
// camera_swing の obstacle が初めて効く）。
ds_r     = 120.0;
ds_top   = 146.0;

/* [Load] */
// 搭載質量（カメラ＋ケース）。荷重ケースの計算に使う。
mounted_kg = 0.35;
// 参考形状（reference）だけが使う脚の寸法。**最適化の入力ではない。**
ref_r_foot = 110.0;
ref_z_hub  = 22.0;
n_legs     = 3;

/* [Hidden] */
$fn = 72;
EPS = 0.01;
BIG = 400;

// ---------------------------------------------------------------- derived
bolt_d    = case_pivot_d + bolt_fit;
slot_w    = case_prong_t + fit_centre + fit_outer;
fin_c_t   = case_prong_gap - 2 * fit_centre;
slot_r    = case_prong_r + slot_fit_r;
knob_r    = knob_d / 2 + knob_fit;
slot_x0   = case_prong_gap / 2 - fit_centre;
slot_x1   = case_prong_gap / 2 + case_prong_t + fit_outer;
fin_head_x0 = -(slot_x1 + fin_head_t);
fin_nut_x1  =   slot_x1 + fin_nut_t;
nut_floor = fin_nut_t - nut_depth;
NUT_R    = (nut_af / 2) / cos(30);
NUT_APEX = 1.366 * NUT_R;
bore_apex_k   = 1.80;
bore_roof_deg = 90 - asin(1 / bore_apex_k);
// マウント頭部の下端 = 台座の底。
z_head_bot = z_hinge - head_h;
// カメラ上端（組み立て後）
cam_top_z = z_hinge + case_hinge_drop + case_h;
case_low_z = z_hinge + min(
    -case_front_y * sin(tilt_max) + case_hinge_drop * cos(tilt_max),
    -case_back_y  * sin(tilt_max) + case_hinge_drop * cos(tilt_max));

// 転倒の判定。**v4 では上限しか言えない。**
// 脚の位置を最適化に任せた以上、実際の接地半径は解いてみないと分からない。
// ここで計算できるのは「脚を設計空間の縁いっぱいに出したとき」＝**最良の場合**。
// これが 1.0 N を下回るなら、最適化が何をしても足りない——それは解く前に分かる。
// 実際に出た接地半径は run のサマリの `ground_contact` に出る。
tip_arm_best  = ds_r * cos(180 / n_legs);
cg_z          = z_hinge + case_hinge_drop + case_h / 2;
tip_force_best_N = mounted_kg * 9.81 * tip_arm_best / cam_top_z;

// ---------------------------------------------------------------- asserts
// ---- 取合い。**STEP から読んだ値と噛み合っているか。**
assert(case_prong_r >= case_prong_tip + 0.4,
       "case_prong_r must be the ENVELOPING radius about the hinge axis (0.5 offset + R7.0 = 7.5 in osmo-action-6.step), not the fillet radius");
assert(finger_r < case_hinge_drop - 1.0,
       "finger_r too large: the case bottom will hit the mount when tilted");
assert(slot_r > case_prong_r + 0.6,
       "slot_r too small: the prong bottoms out and the pivot holes miss each other");
assert(slot_r < finger_r, "slot_r >= finger_r: the fingers would be cut away");
// **下限は「何本の壁が乗るか」。** 0.4 mm ノズルで 2.00 mm = 5 パス。
// v5 まで 2.40（6 パス）だったが、そのぶんのすきまが FFF の太りで消えて
// 実物が嵌まらなかった。中央爪はボルトで挟まれて支圧を受けるだけで、
// 曲げを持つのは外側の 4.0 / 6.0 mm の爪のほう。
assert(fin_c_t >= 2.0 - 1e-9, "centre finger thinner than 5 walls at 0.4 mm");
assert(lead_c < fin_c_t / 2,
       "the lead-in chamfers meet in the middle of the centre finger - it is a wedge, not a finger");
// **合計あそび 1.0 mm 以上。** 実物が嵌まらなかったあとに引き上げた値
// （v5 は 0.5 / 0.7 だった）。FFF は外形が太り溝が細るので、
// 図面上 0.5 mm のあそびは実物で消えることがある。
assert(case_prong_gap - fin_c_t >= 1.0,
       "the centre finger has under 1.0 mm of total play in the male gap - it will not assemble");
assert(slot_w - case_prong_t >= 1.0,
       "the male prong has under 1.0 mm of total play in the slot - it will not assemble");
// STEP の爪は x = 1.5 .. 4.5（と鏡像）。溝はそれを両側から逃がして囲むこと。
assert(slot_x0 <= case_prong_gap / 2 + 1e-9,
       "the slot's inner wall is inside the case prong - the prong will not enter");
assert(slot_x1 >= case_prong_gap / 2 + case_prong_t - 1e-9,
       "the slot's outer wall is inside the case prong - the prong will not enter");

// ---- ネジまわり。**「広めに」を数字で落とす。**
assert(bolt_d - case_pivot_d >= 0.5,
       "under 0.5 mm of clearance on the M5 pivot - it will bind as the part warps");
assert(nut_af - 8.10 >= 0.5,
       "the nut pocket is under 0.5 mm bigger than an M5 nut at its max width");
assert(nut_depth - 4.00 >= 0.15,
       "the nut pocket is not deep enough to swallow an M5 nut");
// ナットが締めると押す壁。osmo_tripod.scad と同じ下限。**v5 には
// この assert が無く、床は 1.80 mm しかなかった。**
assert(nut_floor >= 2.5,
       str("the nut pocket floor is ", nut_floor,
           " mm - it will crack when the knob is tightened"));
assert(knob_r - knob_d / 2 >= 1.0,
       "under 1.0 mm around the knob - there is no room to get a finger on it");
assert(knob_depth >= 14.0,
       "the knob pocket is too shallow to turn the knob by hand");
assert(tool_r >= NUT_R + 1.0,
       "no room to get a socket or a finger onto the nut");
// **ナットが本当に入るか。** ポケットは爪の外側面 (fin_nut_x1) に開くが、
// 台座はそこより X に広い。逃がしが台座を貫いていなければ、ポケットは
// 袋穴でナットは入らない——v5 の実物がこれで組めなかった。
// 3つとも通って初めて「ナット側が開いている」と言える。
assert(tool_r >= NUT_APEX + 0.5,
       str("the nut-side access (r=", tool_r, ") is narrower than the nut pocket (apex ",
           NUT_APEX, ") - the nut cannot be pushed in through it"));
assert(fin_nut_x1 + tool_depth >= ped_bot_x + 1.0,
       str("the nut-side access reaches x=", fin_nut_x1 + tool_depth,
           " but the pedestal is ", ped_bot_x,
           " mm wide - the nut pocket would be a blind hole in the pedestal"));
assert(fin_head_x0 - knob_depth <= -ped_bot_x - 1.0,
       str("the knob clearance reaches x=", fin_head_x0 - knob_depth,
           " but the pedestal is ", ped_bot_x,
           " mm wide - there is no room to get a finger on the knob"));
// 台座の上面は爪を覆っていること（覆っていないと爪の付け根が宙に浮く）。
// 上の2つと対で読むこと: 覆うからこそ、逃がしで貫かねばならない。
assert(ped_top_x >= fin_nut_x1 - 0.01 && ped_top_x >= -fin_head_x0 - 0.01,
       str("the pedestal top (", ped_top_x, ") does not cover the fingers (",
           fin_nut_x1, " / ", -fin_head_x0, ")"));
assert(ped_top_y >= finger_r - 0.01, "pedestal top too narrow in Y");
assert(ped_bot_x >= ped_top_x && ped_bot_y >= ped_top_y,
       "the pedestal is undercut - it cannot be printed standing up");
assert(bore_roof_deg >= 50, "bolt bore roof is too shallow - it will need support");

// ---- この検証モデル固有の検査
assert(case_low_z > 2.0, "the case bottom corner hits the ground at full tilt");

// ---- 印刷範囲。**最適化を回す前に、ここで落とす。**
//
// v3 まではこの assert が `r_foot`（脚先端の半径）を見ていた。設計空間の外形が
// たまたま脚先端と同じだったので通っていただけで、**設計空間そのものは
// 一度も検査されていなかった**。v4 で脚が無くなるとその偶然も消えるので、
// 設計空間の外形を直接見る。最適化は設計空間の外に材料を置けないので、
// これが通れば「出てきた形が入らない」は原理的に起きない。
//（同じ検査が topopt setup 側にもある。そちらは実際にボクセル化した
//  bounding box を見るので、こちらは「描く前の見取り図」に相当する。）
env_xy = bed_xy - 2 * print_margin;
env_z  = bed_z  - 2 * print_margin;
assert(2 * ds_r <= env_xy,
       str("the design space is ", 2 * ds_r, " mm across but only ", env_xy,
           " mm fits the plate (", bed_xy, " less ", print_margin,
           " mm of margin each side) - nothing the optimiser produces could be printed"));
assert(ds_top <= env_z,
       str("the design space is ", ds_top, " mm tall but only ", env_z,
           " mm of headroom is usable"));

// preserve が設計空間からはみ出していないか。はみ出した部分には材料を
// 足せないので、取合いが宙に浮く。
assert(z_hinge + finger_r <= ds_top,
       str("the mount head reaches ", z_hinge + finger_r,
           " mm but the design space stops at ", ds_top));
assert(fin_nut_x1 <= ds_r && -fin_head_x0 <= ds_r,
       "the mount head is wider than the design space");
assert(z_head_bot > 0, "the mount head reaches the ground plane");

// ---- 設計空間に本当に自由度があるか。
// v3 はここで「支柱が 85 mm 以上あるか」を見ていた。v4 は支柱という概念が
// 無いので、頭部の下にどれだけ立体があるかで測る。
assert(z_head_bot >= 100,
       "under 100 mm between the ground and the mount head - this model is not testing anything the short tripod did not");
assert(ds_r >= 100, "the design space is too narrow to place feet in");

// ---- 転倒。**背を高くしたぶんの代償がここに出る。** 隠さずに数字で落とす。
// 基準は実際に使っている osmo_tripod（1.014 N で転ぶ）。
// **これは最良の場合の上限。** 実際の値は解いた後にしか分からない。
assert(tip_force_best_N > 1.0,
       str("even with its feet on the rim of the design space this tripod tips at ",
           tip_force_best_N,
           " N pushed at the camera top - widen ds_r or lower z_hinge"));

// ---------------------------------------------------------------- modules

// ---- マウント頭部（preserve）

module head_pedestal() {
    translate([0, 0, z_head_bot])
        linear_extrude(height = head_h,
                       scale = [ped_top_x / ped_bot_x, ped_top_y / ped_bot_y])
            square([2 * ped_bot_x, 2 * ped_bot_y], center = true);
}

module finger(x0, x1, c0 = 0, c1 = 0) {
    L = x1 - x0;
    translate([x0, 0, z_hinge]) rotate([0, 90, 0]) {
        if (c0 > 0) cylinder(r1 = finger_r - c0, r2 = finger_r, h = c0);
        translate([0, 0, c0]) cylinder(r = finger_r, h = L - c0 - c1);
        if (c1 > 0)
            translate([0, 0, L - c1]) cylinder(r1 = finger_r, r2 = finger_r - c1, h = c1);
    }
}

module fingers() {
    finger(fin_head_x0, -slot_x1, 0,      lead_c);
    finger(-slot_x0,     slot_x0, lead_c, lead_c);
    finger( slot_x1,     fin_nut_x1, lead_c, 0);
}

// ---- 頭部に開ける穴（finish 段が引く形）

module slot_cut(x0, x1) {
    translate([x0, 0, z_hinge]) rotate([0, 90, 0])
        union() {
            cylinder(r = slot_r, h = x1 - x0);
            translate([-slot_r, 0, 0])
                cube([2 * slot_r, BIG, x1 - x0]);
        }
}

module slots() {
    slot_cut(-slot_x1, -slot_x0);
    slot_cut( slot_x0,  slot_x1);
}

// ティアドロップ。頂点は「点」で作る——小さな円で作るとその下半分が
// 水平な天井として残る（lessons-learned #28）。
module bolt_teardrop_2d(d) {
    r  = d / 2;
    ay = bore_apex_k * r;
    hull() {
        circle(r = r, $fn = 48);
        polygon([[0, ay], [-0.01, ay - 0.1], [0.01, ay - 0.1]]);
    }
}

// **`rotate([90,0,0])` を落とさないこと。**
// `rotate([0,90,0])` だけだと押し出し軸は +X で正しいが、2D 図形の +Y が
// **グローバルの +Y**（＝横）に向く。ティアドロップの頂点もナットの尖り屋根も
// 横を向き、穴の天井は水平のままになる——`bore_roof_deg` の assert は
// 「頂点が上を向いていれば 56 度」と言っているだけなので、**通ったまま何も守らない**。
// v3 までこの合成が抜けていた（`osmo_tripod.scad` の同じモジュールには入っている）。
// [90,0,0] を足すと 2D の +Y が +Z に回り、頂点が上を向く。
module bolt_hole() {
    translate([fin_head_x0 - 1, 0, z_hinge]) rotate([90, 0, 0]) rotate([0, 90, 0])
        linear_extrude(height = fin_nut_x1 - fin_head_x0 + 2)
            bolt_teardrop_2d(bolt_d);
}

// ナットざぐり。**口は `cut_over` だけ外へ突き抜けさせる**——公称面ちょうどで
// 終わる穴は膜を残しうる（[Screw] の cut_over を参照）。
module nut_pocket() {
    translate([fin_nut_x1 - nut_depth, 0, z_hinge])
        rotate([90, 0, 0]) rotate([0, 90, 0]) {
            linear_extrude(height = nut_depth + cut_over)
                circle(r = NUT_R, $fn = 6);
            // 六角の上に尖り屋根を足してサポートを不要にする
            linear_extrude(height = nut_depth + cut_over)
                polygon([[-NUT_R, 0], [NUT_R, 0], [0, NUT_APEX]]);
        }
}

// ---- ネジを回すための空き空間（obstacle **かつ** mount_cuts）
//
// **部品の外の空間であると同時に、台座に開ける穴でもある。**
// obstacle は「最適化が材料を置かない場所」で、**preserve は退けない**。
// 台座（preserve）は爪より X に広いので、obstacle にしか書かないと
// 台座がそのまま逃がしを塞ぐ。v5 のナット側がそれで袋穴になった。
// だから両方に書く——`obstacle_all()` と `mount_cuts()` の両方に出てくる。

// **どちらも爪の外側面ちょうどで止める。** knob_r も tool_r も爪の半径より
// 大きいので、内側へ伸ばすと爪をそのぶん削る——ノブ側は座面が薄くなり、
// ナット側はポケットの周りの肉が減る。膜の心配があるのは袋穴のほうなので、
// 伸ばすのは `nut_pocket()` の側（`cut_over`）にして、そこで重ねる。
module knob_clearance() {
    translate([fin_head_x0 - knob_depth, 0, z_hinge]) rotate([0, 90, 0])
        cylinder(r = knob_r, h = knob_depth + EPS);
}

module tool_clearance() {
    translate([fin_nut_x1 - EPS, 0, z_hinge]) rotate([0, 90, 0])
        cylinder(r = tool_r, h = tool_depth + EPS);
}

module screw_access() {
    knob_clearance();
    tool_clearance();
}

// ---- カメラの首振り範囲（obstacle）
//
// **v3 では実測で0セルしか削っていなかった**——設計空間がヒンジで終わって
// いて、掃引域はそのほぼ全部がその上にあったから。
// v4 は設計空間が頭部の頂点まで伸びたので、ここは**初めて本当に効く**。
module camera_swing() {
    // ケースをヒンジ軸まわりに -tilt_max ... +tilt_max まで少しずつ回して重ねる。
    // 全周の円環にすると保守的すぎて、ヒンジより下の台座（preserve）まで飲み込む。
    intersection() {
        translate([0, 0, z_hinge])
            for (a = [-tilt_max : 5 : tilt_max])
                rotate([a, 0, 0])
                    translate([-case_w/2, -case_back_y, case_hinge_drop])
                        cube([case_w, case_back_y + case_front_y, case_h]);
        // 設計空間より上は禁止しても何も禁止していないのに、ボクセルグリッドを
        // 上へ伸ばして同じ要素数でマス目を粗くする。だから ds_top で切る。
        translate([-BIG/2, -BIG/2, 0]) cube([BIG, BIG, ds_top]);
    }
}

// ---------------------------------------------------------------- parts

// preserve: 絶対に残す領域＝**カメラ頭部だけ**。
//
// **v3 との違いはここ。** v3 は3つの接地パッドとハブ座も preserve だった。
// 拘束は preserve の上にしか置けないという規約があるので、そうすると
// **脚の位置を人間が決めたことになる**。v4 は接地を `"contact": "ground"`
// の support に変えたので、preserve は取合いだけで足りる。
//
// 穴を開けた形を preserve に渡してはいけない: 溝や穴が自由セルになり、
// 最適化がそこを埋めてカメラの爪が入らなくなる。中実のまま渡す。
module preserve_all() {
    head_pedestal();
    fingers();
}

// design_space: 最適化が材料を置いてよい空間＝**プレートに載る円柱ひとつ**。
module design_space() {
    cylinder(r = ds_r, h = ds_top);
}

// obstacle: 絶対に空けておく領域。
//
// **preserve を引いてから渡す。** ノブの逃げもカメラの掃引域も、頭部そのものと
// 重なる（ノブのポケットは頭部に彫る穴だし、掃引域は爪の上端をかすめる）。
// 重なったまま渡すと「preserve と obstacle が1セルを超えて重なる」という
// 設定エラーになる——10 mm では preserve が細いので今は通るが、グリッドを
// 細かくした瞬間に落ちる。頭部に彫る穴は obstacle ではなく mount_cuts の仕事。
// obstacle は**部品の外の空き空間**だけ、というのが正しい定義でもある。
module obstacle_all() {
    difference() {
        union() { camera_swing(); screw_access(); }
        preserve_all();
    }
}

// ---- finish 段（topopt/finish.py）に渡す形
//
// 最適化のマス目は 10 mm で、中央爪は 2.40 mm。**マス目より細かい。**
// でも頭部は preserve で、最適化は一度も触っていない。だから解析グリッドから
// 出す必要が無い——CAD の寸法のまま足し戻して、穴を切ればよい。
// それをやるのが `finish`。mount_solid を足し、mount_cuts を引く。
module mount_solid() {
    head_pedestal();
    fingers();
}

// **`screw_access()` がここに入っているのが v6 の修正点。** ノブ側だけを切って
// ナット側を切っていなかったので、ナットポケットは台座の中で終わる袋穴だった。
// 切る箱は X 方向に ±40 mm あるので、tool_depth = 16 の進入路
// （fin_nut_x1 + 16 = 27.2 mm）はこの中に収まる。
module mount_cuts() {
    cut_top = z_hinge + finger_r + 2;
    intersection() {
        union() { slots(); bolt_hole(); nut_pocket(); screw_access(); }
        translate([-40, -25, z_head_bot - 1])
            cube([80, 50, cut_top - z_head_bot + 1]);
    }
}

// 出来上がりの頭部。人が寸法を確かめるための形で、finish 段は
// mount_solid と mount_cuts を別々に受け取る（足す順・引く順が決まっているため）。
module mount_finished() {
    difference() { mount_solid(); mount_cuts(); }
}

// reference: 人が形を見るための参考形状。**最適化の入力ではない。**
// 「素直に3本脚と支柱を描いたらこうなる」という比較対象で、
// 最適化はこれより軽い解を出すことが期待される。
module reference() {
    difference() {
        union() {
            cylinder(r = 30, h = ref_z_hub);
            for (i = [0 : n_legs - 1])
                rotate([0, 0, 90 + i * 360 / n_legs])
                    hull() {
                        cylinder(r = 16, h = 12);
                        translate([ref_r_foot, 0, 0]) cylinder(r = 10, h = 12);
                    }
            translate([0, 0, ref_z_hub - EPS])
                cylinder(r1 = 24, r2 = 15, h = z_head_bot - ref_z_hub + EPS);
            mount_solid();
        }
        mount_cuts();
    }
}

if      (part == "reference")      reference();
else if (part == "preserve")       preserve_all();
else if (part == "design_space")   design_space();
else if (part == "obstacle")       obstacle_all();
else if (part == "camera_swing")   camera_swing();
else if (part == "screw_access")   screw_access();
else if (part == "mount_solid")    mount_solid();
else if (part == "mount_cuts")     mount_cuts();
else if (part == "mount_finished") mount_finished();

echo(str("z_hinge=", z_hinge, "  camera top=", cam_top_z,
         "  design space = cylinder d", 2 * ds_r, " x ", ds_top));
echo(str("design space volume=", 3.14159 * ds_r * ds_r * ds_top, " mm3"));
echo(str("BEST CASE tipping (feet on the rim, r=", ds_r, "): arm=", tip_arm_best,
         ", tips at ", tip_force_best_N,
         " N pushed at the camera top. The real value depends on where the ",
         "optimiser puts the feet - read ground_contact in the run summary."));

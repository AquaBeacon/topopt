// =====================================================================
// osmo_tripod.scad — DJI Osmo Action 6 用 3脚ホルダー (一体固定・低重心)
//
// 取合い相手: osmo-action-6.step ("Shaks case DJI osmo action 6")
//             下部の GoPro 互換 2枚爪 (2-prong) を、本体側の 3枚爪 (3-prong)
//             メスで受け、M5 蝶ネジ + 六角ナットで締める。
//
// 座標系: Z 上、Z=0 が接地面 = 印刷 1層目。カメラは +Y を向く。
//         ヒンジ軸は X 方向。
//
// 印刷方向: そのまま Z 上 (脚の裏が全面ベッド接地)。サポート不要。
// =====================================================================
include <util.scad>

/* [Build] */
// 何を出力するか
part = "tripod";   // [tripod, mount_only, base_only, design_space, keepout, mount_solid, mount_cuts]

/* [Case interface — osmo-action-6.step から実測。変更禁止] */
// オス爪1枚の厚さ
case_prong_t    = 3.00;
// オス爪2枚の隙間
case_prong_gap  = 3.00;
// オス爪のピボット穴径
case_pivot_d    = 5.00;
// ケース底面からヒンジ中心までの距離
case_hinge_drop = 11.00;
// ヒンジ中心まわりのオス爪外形半径
case_prong_r    = 7.50;
// ケース外形 (干渉チェック用)
case_front_y    = 20.00;
case_back_y     = 16.30;
case_h          = 54.04;
case_w          = 79.95;

/* [Requirement] */
// 必要なチルト範囲 (水平前向きから上下に振れる角度)
tilt_max   = 30;

/* [Fits] */
// すきまは「片側 0.25 で全部いける」ではない。実機で組めなかったので、
// 痩せる側と痩せない側を分けて持つ (2026-08-04 の実測。docs/lessons-learned.md 参照)。
//
// 中央爪側のすきま。fin_c_t = case_prong_gap - 2*fit_centre なので、
// **広げるほど中央爪が薄くなる**。6壁 (2.40 mm) が下限なので上限は 0.30。
// ここが取合い全体で一番きついはめあいで、それは幾何的に避けられない。
fit_centre = 0.30;
// 外爪側のすきま。こちらは広げても部材が痩せない (台座が外へ伸びるだけ) ので厚く取る。
fit_outer  = 0.50;
// 差し込みの食い付き面取り。オス爪が最初に当たるのは爪の外周なので、
// そこに 45 度を付けて案内する。すきまを増やさずに「入らない」を減らせる。
lead_c     = 0.50;
// M5 ボルト通し穴のすきま (ケース側の実測 case_pivot_d に対して)。
// ISO の M5 中クラス通し穴が 5.5。水平穴は上側が垂れるのでさらに余裕を持たせる。
bolt_fit   = 0.60;
// オス爪先端の振れ逃がし (ヒンジ軸まわり、半径方向)。
// ここが足りないとオス爪が溝の底で突き当たり、ピボット穴が合わない
// = 「ネジ穴の少し外がふさがっている」ように見える。
slot_fit_r = 1.00;
// M5 六角ナット 二面幅 (実測8.0 + はめあい)
nut_af     = 8.60;
// ナットざぐり深さ
nut_depth  = 3.20;
// 蝶ネジ(ローレットノブ)の外径。市販の GoPro 用は Ø17〜22。
knob_d     = 22.0;
// ノブ逃がしの半径すきま。Ø22 のノブに Ø22 のポケットでは入らない。
knob_fit   = 0.75;
// ノブ/ナットが座るために爪の外側面から確保する奥行き。
// ハブの縁 (r_hub) を通り越すまで伸ばさないと、縁が 1 mm 残ってノブに当たる。
knob_depth = 12.0;

/* [Mount] */
// 接地面からヒンジ軸までの高さ
z_hinge      = 23.0;
// メス爪の外形半径。case_hinge_drop より小さいことがケース非干渉の条件
finger_r     = 9.50;
// ボルト頭側の外爪厚
fin_head_t   = 4.00;
// ナット側の外爪厚
fin_nut_t    = 6.00;
// 台座 (フラスタム) 上面の X 半幅 / Y 半幅。
// X はナット側の外爪 (fin_nut_x1) を覆う必要がある。fit_outer を広げると爪が外へ動くので、
// ここも一緒に動かす。
ped_top_x    = 11.3;
ped_top_y    = 9.5;
// 台座 底面の X 半幅 / Y 半幅
ped_bot_x    = 13.0;
ped_bot_y    = 13.0;

/* [Base] */
// 脚先端の半径 (接地円 = この2倍)
r_foot     = 52.0;
// ハブ半径
r_hub      = 19.0;
// ハブ上面高さ
z_hub_top  = 17.0;
// 脚先端の高さ
z_leg_tip  = 7.0;
// 脚の根元幅 / 先端幅
// 根元を広げるのはトポロジー最適化の指示。最適形状は「細い脚3本」ではなく
// 「根元が大きく開いた三つ葉板」で、MISSING の主成分が脚の側面だった。
leg_w_root = 30.0;
leg_w_tip  = 13.0;
// 脚先端の面取り長さ (平面視)
leg_tip_c  = 4.0;
// 脚の本数と1本目の方位 (90 = +Y = レンズ側)
n_legs     = 3;
leg_az0    = 90;

/* [Hidden] */
$fn = 72;
EPS = 0.01;
BIG = 400;

// ---------------------------------------------------------------- derived
// ボルト通し穴 = ケース側ピボット穴 + すきま
bolt_d    = case_pivot_d + bolt_fit;          // 5.60
// メススロット幅 = オス爪 + 内外のすきま (非対称)
slot_w    = case_prong_t + fit_centre + fit_outer;   // 3.80
// メス中央爪厚 = オス隙間 - 両側すきま
fin_c_t   = case_prong_gap - 2 * fit_centre;  // 2.40
// オス爪が振れるときの逃がし半径
slot_r    = case_prong_r + slot_fit_r;        // 8.50
// ノブ逃がしポケットの半径
knob_r    = knob_d / 2 + knob_fit;            // 11.75
// 爪の X 位置 (オス爪は x=0 対称。外爪厚が違うので全体は非対称)
slot_x0   = case_prong_gap / 2 - fit_centre;               //  1.20
slot_x1   = case_prong_gap / 2 + case_prong_t + fit_outer; //  5.00
fin_head_x0 = -(slot_x1 + fin_head_t);        // -9.00
fin_nut_x1  =   slot_x1 + fin_nut_t;          // 11.00
// ナットざぐり床の残肉
nut_floor = fin_nut_t - nut_depth;
// ナットざぐりの六角外接円半径と、45度屋根の頂点高さ
NUT_R    = (nut_af / 2) / cos(30);
NUT_APEX = 1.366 * NUT_R;
// ボルト穴のティアドロップ屋根。util.scad の teardrop() は屋根がちょうど 45.0 度で、
// 造形性判定の閾値 (支持不要なのは水平から 45 度超) の真上に乗る。実測でも、
// 穴径を 5.30 -> 5.60 に変えただけで同じ形の判定が 0.0 mm2 と 40.2 mm2 に割れた
// (浮動小数の丸めがどちらに転ぶかだけの違い)。ここは閾値から離す。
// 頂点を穴半径の bore_apex_k 倍だけ上に置くと、円への接線が屋根になる。
bore_apex_k   = 1.80;
bore_roof_deg = 90 - asin(1 / bore_apex_k);   // 56.25 度
// カメラ上端の高さ (組み立て後)
cam_top_z = z_hinge + case_hinge_drop + case_h;   // 88.04
// チルトさせたときのケース最下点。ヒンジまわりの回転なので幾何で閉じる。
// 前傾(-tilt)ではレンズ側の底角が、後傾(+tilt)では背面側の底角が下がる。
case_low_z = z_hinge + min(
    -case_front_y * sin(tilt_max) + case_hinge_drop * cos(tilt_max),
    -case_back_y  * sin(tilt_max) + case_hinge_drop * cos(tilt_max));

// ---------------------------------------------------------------- asserts
// ケースはヒンジ軸まわりに回るので、軸からの距離は回転で変わらない。
// よって「爪半径 < ケース底面までの距離」が全チルト角での非干渉条件。
assert(finger_r < case_hinge_drop - 1.0,
       "finger_r too large: the case bottom will hit the mount when tilted");
assert(slot_r > case_prong_r + 0.6,
       "slot_r too small: the prong bottoms out and the pivot holes miss each other");
// チルトさせたときにケースが地面に当たらないか
assert(case_low_z > 2.0, "the case bottom corner hits the ground at full tilt");
// カメラが接地円からはみ出しすぎないか (はみ出すと押したとき一気に倒れる)
assert(case_w / 2 < r_foot, "the camera is wider than the footprint");
assert(slot_r < finger_r, "slot_r >= finger_r: the fingers would be cut away");
assert(abs(fin_c_t + 2*fit_centre - case_prong_gap) < 1e-9, "centre finger fit drifted");
assert(abs(slot_w - fit_centre - fit_outer - case_prong_t) < 1e-9, "slot fit drifted");
// 6壁 = 2.40 mm。fit_centre = 0.30 でちょうど下限に乗るので、比較は浮動小数の
// 誤差ぶんだけ緩める (規則を緩めているのではなく、2.4 を 2.4 と読ませるため)。
assert(fin_c_t >= 2.4 - 1e-9, "centre finger thinner than 6 walls at 0.4 mm");
// 組めるかどうかは「全部の面が同時に空いているか」で決まる。オス爪は剛体なので、
// 中央爪の両面と外爪の内面の4面が同時に当たらないことを要求する。
assert(case_prong_gap - fin_c_t >= 0.5,
       "the centre finger has under 0.5 mm of total play in the male gap - it will not assemble");
assert(slot_w - case_prong_t >= 0.7,
       "the male prong has under 0.7 mm of total play in the slot - it will not assemble");
// 食い付き面取りが部材を食い潰さないか
assert(lead_c > 0 && lead_c < fin_c_t / 4, "lead-in chamfer eats the centre finger");
assert(lead_c < min(fin_head_t, fin_nut_t) / 2, "lead-in chamfer eats an outer finger");
assert(nut_floor >= 2.5, "nut pocket floor too thin - it will crack when tightened");
assert(finger_r - bolt_d/2 >= 5.0, "not enough material around the bolt hole");
// ボルト穴の屋根が自己支持するか。45 度ちょうどは閾値の上なので余裕を要求する。
assert(bore_roof_deg >= 50, "bolt bore roof is too shallow - it will need support");
assert(bore_apex_k * bolt_d/2 + 1.5 <= finger_r,
       "the teardrop apex breaks out of the top of the finger");
assert(NUT_APEX + 1.5 <= finger_r, "nut pocket roof breaks out of the finger");
assert(NUT_R + 0.5 <= finger_r, "nut pocket is wider than the finger");
// 台座上面が爪の張り出しを覆っているか
assert(ped_top_x >= fin_nut_x1 - 0.01 && ped_top_x >= -fin_head_x0 - 0.01,
       "pedestal top too narrow in X: the outer fingers overhang");
assert(ped_top_y >= finger_r - 0.01, "pedestal top too narrow in Y");
// 台座が末広がりであること (逆テーパは印刷不可)
assert(ped_bot_x >= ped_top_x && ped_bot_y >= ped_top_y, "pedestal is undercut");
// 台座底面がハブ内に収まるか
assert(sqrt(ped_bot_x*ped_bot_x + ped_bot_y*ped_bot_y) <= r_hub,
       "pedestal footprint sticks out of the hub");
// 爪の一番下が台座/ハブ内にあるか (爪が宙に浮かない)
assert(z_hinge - finger_r < z_hub_top + 2, "fingers start above the hub - floating tabs");
// 脚どうしが干渉しないか。脚は r=0 から生えているので、ハブの外(r>=r_hub)で
// 隣の脚と重ならなければよい。ハブ内側の重なりは union なので無害。
leg_w_at_hub = leg_w_root + (leg_w_tip - leg_w_root) * r_hub / r_foot;
assert(leg_w_at_hub / 2 < r_hub * sin(180 / n_legs) - 0.5,
       "legs overlap outside the hub");
assert(leg_w_tip/2 < leg_w_root/2, "leg does not taper");
assert(z_leg_tip < z_hub_top, "leg does not slope down");
assert(leg_tip_c < leg_w_tip/2, "tip chamfer eats the whole tip");
// スロットの底が接地面より上にあるか
assert(z_hinge - slot_r > 0, "slot cut reaches the ground plane");
// ノブ逃がしが接地面を割らないか (割ると底面が分断される)
assert(z_hinge - knob_r > 1.0, "knob clearance cut reaches the ground plane");
// ノブ逃がしが爪本体を削らないか
assert(knob_r <= finger_r + 3.0, "knob clearance would eat into the fingers");
// ノブが実際に入るか。ポケットがノブ径ちょうどでは入らない。
assert(knob_r - knob_d/2 >= 0.5, "knob pocket is the knob diameter - it will not go in");
// ノブ逃がしがハブの縁を通り越しているか (通り越さないと縁が残って当たる)
assert(fin_head_x0 - knob_depth <= -r_hub - 0.5 &&
       fin_nut_x1 + knob_depth >= r_hub + 0.5,
       "knob clearance does not clear the hub rim");

// ---------------------------------------------------------------- modules

// 1本の脚。+X 方向に伸びる。底面は Z=0 で平ら (サポート不要・最大接地)。
module leg() {
    intersection() {
        // 平面視のテーパ (先端は面取り)
        linear_extrude(height = z_hub_top + EPS)
            polygon([[0,                  -leg_w_root/2],
                     [r_foot - leg_tip_c, -leg_w_tip/2],
                     [r_foot,             -leg_w_tip/2 + leg_tip_c],
                     [r_foot,              leg_w_tip/2 - leg_tip_c],
                     [r_foot - leg_tip_c,  leg_w_tip/2],
                     [0,                   leg_w_root/2]]);
        // 側面視のテーパ (X-Z 平面の台形を Y 方向に押し出す)
        translate([0, leg_w_root, 0]) rotate([90, 0, 0])
            linear_extrude(height = 2 * leg_w_root)
                polygon([[-EPS, 0], [r_foot, 0],
                         [r_foot, z_leg_tip], [-EPS, z_hub_top]]);
    }
}

// ハブ + 3本の脚
module base_solid() {
    cylinder(r = r_hub, h = z_hub_top);
    for (i = [0 : n_legs - 1])
        rotate([0, 0, leg_az0 + i * 360 / n_legs]) leg();
}

// 台座: 四角錐台。上に向かって細るので必ず印刷できる。
module pedestal() {
    linear_extrude(height = z_hinge,
                   scale = [ped_top_x / ped_bot_x, ped_top_y / ped_bot_y])
        square([2 * ped_bot_x, 2 * ped_bot_y], center = true);
}

// メス爪 1枚 (ヒンジ軸まわりの円板)。X 範囲ごとに置く。
// rotate([0,90,0]) は local +Z -> world +X なので、円柱の軸がそのままヒンジ軸になる。
// c0 / c1 は「溝に面する側」に付ける食い付き面取り (45度)。溝に面していない外側面
// (ノブの座面・ナットざぐりの面) には付けない — 座面を削ると着座面積が減る。
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
    finger(fin_head_x0, -slot_x1, 0,      lead_c);  // ボルト頭側 (内面だけ食い付き)
    finger(-slot_x0,     slot_x0, lead_c, lead_c);  // 中央 (両面が溝)
    finger( slot_x1,     fin_nut_x1, lead_c, 0);    // ナット側 (内面だけ食い付き)
}

// オス爪が入る U 溝 (円 + 上方への角穴)。X 範囲ごとに切る。
module slot_cut(x0, x1) {
    translate([x0, 0, 0]) rotate([0, 90, 0])
        linear_extrude(height = x1 - x0) {
            translate([-z_hinge, 0]) circle(r = slot_r);
            // ヒンジより上を全部開ける (爪を上から差し込むため)
            translate([-(z_hinge + BIG/2), -slot_r]) square([BIG/2, 2*slot_r]);
        }
}

module slots() {
    slot_cut(-slot_x1, -slot_x0);
    slot_cut( slot_x0,  slot_x1);
}

// M5 通し穴。水平穴なのでティアドロップにして頂点の垂れを防ぐ。
// 屋根は 45 度ちょうどではなく bore_roof_deg (= 56 度) にして、閾値から離す。
// 頂点は「面」ではなく「点」でなければならない。ここに小さな円を置くと、
// その下半分が幅 0.1 mm のほぼ水平な天井として残り、要サポートに数えられる
// (実測 0.7 mm2)。頂点1点だけが hull の最上部になるよう、残り2点は
// 接線の内側に落とす。
module bolt_teardrop_2d(d) {
    r  = d / 2;
    ay = bore_apex_k * r;
    hull() {
        circle(r = r, $fn = 48);
        polygon([[0, ay], [-0.01, ay - 0.1], [0.01, ay - 0.1]]);
    }
}

// 軸を +X、頂点を +Z に向ける (util.scad の teardrop_bore と同じ向きの合成)。
module bolt_hole() {
    translate([fin_head_x0 - 1, 0, z_hinge])
        rotate([90, 0, 0]) rotate([0, 90, 0])
            linear_extrude((fin_nut_x1 - fin_head_x0) + 2) bolt_teardrop_2d(bolt_d);
}

// ナットざぐり。ただの六角だと天面が水平から30度になりサポートが要るので、
// 上の頂点を持ち上げて 45 度の尖り屋根にする (自己支持)。
module nut_pocket() {
    translate([fin_nut_x1 - nut_depth, 0, 0]) rotate([0, 90, 0])
        linear_extrude(height = nut_depth + EPS)
            polygon([[-(z_hinge + NUT_APEX),   0],
                     [-(z_hinge + 0.5*NUT_R),  0.866*NUT_R],
                     [-(z_hinge - 0.5*NUT_R),  0.866*NUT_R],
                     [-(z_hinge - NUT_R),      0],
                     [-(z_hinge - 0.5*NUT_R), -0.866*NUT_R],
                     [-(z_hinge + 0.5*NUT_R), -0.866*NUT_R]]);
}

// 蝶ネジのノブと六角ナットが座るための逃がし。
// 爪の外側面より外は、ヒンジ軸まわり半径 knob_r (= ノブ半径 + knob_fit) を空けておく。
// これが無いと、台座の肩とハブ上面に当たって Ø5 のノブしか入らない
// (実測で確認済み。組立不能になる)。切り口は上向きの凹円筒なので印刷できる。
module knob_clearance() {
    // ボルト頭側
    translate([fin_head_x0 - knob_depth, 0, z_hinge]) rotate([0, 90, 0])
        cylinder(r = knob_r, h = knob_depth);
    // ナット側
    translate([fin_nut_x1, 0, z_hinge]) rotate([0, 90, 0])
        cylinder(r = knob_r, h = knob_depth);
}

// ---------------------------------------------------------------- part

module tripod() {
    difference() {
        union() {
            base_solid();
            pedestal();
            fingers();
        }
        slots();
        knob_clearance();
        bolt_hole();
        nut_pocket();
    }
}

// トポロジー最適化用の設計領域 (脚とハブが入る円板 + 台座)
module design_space() {
    cylinder(r = r_foot, h = z_hub_top);
    pedestal();
}

// 非設計領域 (残さなければならない部分): 台座 + 爪 + 接地パッド
module keepout() {
    pedestal();
    fingers();
    for (i = [0 : n_legs - 1])
        rotate([0, 0, leg_az0 + i * 360 / n_legs])
            translate([r_foot - 12, -leg_w_tip/2, 0])
                cube([12, leg_w_tip, z_leg_tip]);
}

// --- 設計空間モデル (preserve / obstacle) 用 ---------------------------------
// 生成設計では「残す塊」と「開ける穴」を分けて渡す。mount_only は両者を
// difference() 済みなので preserve には使えない: 溝や穴が設計空間の自由セルに
// なり、最適化がそこを埋めてカメラの爪が入らなくなる。
// preserve = 穴を開ける前の中実エンベロープ。
module mount_solid() {
    pedestal();
    fingers();
}
// obstacle = 開ける側 (爪の U 溝・M5 通し穴・ナットざぐり・ノブ逃がし)。
// slot_cut() はオス爪を上から差し込むため z=+BIG まで開いている。そのまま
// obstacle STL にするとボクセルグリッドが z=223 まで伸びて解像度が死ぬので、
// 部品が存在しうる範囲に切ってから書き出す (外はどうせ材料が無く無意味)。
module mount_cuts() {
    cut_clip_z = z_hinge + finger_r + 2;   // 爪の頂点より少し上まで
    intersection() {
        union() { slots(); bolt_hole(); nut_pocket(); knob_clearance(); }
        translate([-30, -20, 0]) cube([60, 40, cut_clip_z]);
    }
}

if      (part == "tripod")       tripod();
else if (part == "mount_only")   difference() { union() { pedestal(); fingers(); }
                                                slots(); knob_clearance(); bolt_hole(); nut_pocket(); }
else if (part == "base_only")    base_solid();
else if (part == "design_space") design_space();
else if (part == "keepout")      keepout();
else if (part == "mount_solid")  mount_solid();
else if (part == "mount_cuts")   mount_cuts();

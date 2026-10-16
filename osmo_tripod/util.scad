// =====================================================================
// util.scad — reusable, print-friendly OpenSCAD modules
// `include <util.scad>` (or copy the modules you need) into your design.
// Units are millimetres. Conventions: parts grow in +Z from the bed; the
// "bottom" face (z=0) is the first printed layer.
// =====================================================================
$fn = $fn ? $fn : 64;

// ---- rounded / chamfered boxes -------------------------------------

// Rounded-rectangle prism, lower-left corner at origin.
module rrect_prism(w, d, h, r=2) {
    linear_extrude(height=h)
        offset(r=r) offset(delta=-r) square([w, d]);
}

// Box with rounded vertical edges AND a bottom chamfer (kills "elephant foot"
// and makes the first layer release cleanly). chamfer applies to the base edge.
module box_rounded(w, d, h, r=2, chamfer=0.6) {
    hull() {
        // chamfered bottom slab
        translate([chamfer, chamfer, 0])
            rrect_prism(w-2*chamfer, d-2*chamfer, 0.01, max(0.1, r-chamfer));
        // full-size body starting just above the chamfer
        translate([0, 0, chamfer])
            rrect_prism(w, d, h-chamfer, r);
    }
}

// Open-top enclosure shell: outer box with rounded vertical edges AND a bottom
// chamfer (elephant-foot relief), minus an inner cavity with a floor. This is the
// print-friendly base for a two-part case. w/d/h are OUTER; 'wall' and 'floor_t'
// set the shell thickness; the top is left open for the lid/contents.
module box_shell(w, d, h, r=3, wall=2, floor_t=2, chamfer=0.6) {
    difference() {
        box_rounded(w, d, h, r, chamfer);
        translate([wall, wall, floor_t])
            rrect_prism(w - 2*wall, d - 2*wall, h, max(0.1, r - wall));
    }
}

// Fillet a concave inner corner is hard in CSG; for outer fillets use minkowski
// sparingly (slow). Prefer chamfers for speed and printability.

// ---- holes & fits ---------------------------------------------------
// FDM clearance guidance (printer-dependent, tune once and reuse):
//   loose/clearance fit hole  : nominal + 0.4 .. 0.5 mm
//   snug/press fit hole        : nominal + 0.1 .. 0.2 mm
//   self-tap screw pilot       : nominal - 0.4 .. 0.6 mm
//   heat-set insert (brass)    : per insert spec, typ. boss hole = insert_d
CLEAR_FIT  = 0.45;
PRESS_FIT  = 0.15;

// A vertical bore (subtract from a solid). Set 'through' true to over-run ends.
module bore(d, h, through=true) {
    e = through ? 0.2 : 0;
    translate([0, 0, -e/2]) cylinder(d=d, h=h+e);
}

// Counterbore for a socket-head cap screw (subtract).
module counterbore(shaft_d, head_d, head_h, length) {
    bore(shaft_d, length);
    translate([0, 0, length-head_h]) bore(head_d, head_h+0.2);
}

// Countersink for a flat-head screw (subtract).
module countersink(shaft_d, head_d, length, angle=90) {
    bore(shaft_d, length);
    cone_h = (head_d/2) / tan(angle/2);
    translate([0, 0, length-cone_h])
        cylinder(d1=shaft_d, d2=head_d, h=cone_h+0.01);
}

// ---- bosses / standoffs --------------------------------------------
// Screw boss with optional ribs. Place where you need to mount a PCB or lid.
module screw_boss(h, screw_d=2.5, mode="self_tap", insert_d=3.4, wall=2.0) {
    od = max(screw_d + 2*wall, 5);
    hole = (mode=="insert") ? insert_d
         : (mode=="clearance") ? screw_d + CLEAR_FIT
         : max(0.1, screw_d - 0.5);          // self_tap pilot
    difference() {
        cylinder(d=od, h=h);
        translate([0,0,(mode=="insert")? h-min(h,6) : -0.1])
            cylinder(d=hole, h=(mode=="insert")? min(h,6)+0.2 : h+0.2);
    }
}

// PCB / lid standoff: a cylindrical boss carrying a radius-compensated hole (a
// self-tap pilot, an insert seat, or a clearance hole). The hole is bored from
// the TOP down by 'hole_depth'. Using hole() instead of a bare cylinder() makes
// the bore print close to nominal on FDM (see the hole() notes below).
module standoff(h, od, hole_d, hole_depth, fn=8) {
    difference() {
        cylinder(d=od, h=h);
        if (hole_d > 0)
            translate([0, 0, h - hole_depth + 0.01])
                hole(hole_d, hole_depth + 0.02, fn=fn, through=false);
    }
}

// ---- snap-fit cantilever -------------------------------------------
// A simple cantilever hook to subtract/add for lid retention. Tune for material.
module snap_hook(length=8, thick=1.6, hook=1.2, width=4) {
    cube([thick, width, length]);
    translate([thick, 0, length-hook])
        rotate([0,90,0]) linear_extrude(hook)
            polygon([[0,0],[0,width],[hook,width],[0,0]]);
}

// ---- ventilation ----------------------------------------------------
// Grid of slots through a wall of thickness 'th' (subtract from the wall).
module vent_slots(area_w, area_h, slot_w=2, gap=2, th=2) {
    n = floor(area_w / (slot_w+gap));
    for (i=[0:n-1])
        translate([i*(slot_w+gap), 0, -0.1])
            cube([slot_w, area_h, th+0.2]);
}

// Hex (honeycomb) vent pattern (subtract). r = cell radius.
module vent_hex(area_w, area_h, r=3, gap=1.2, th=2) {
    dx = (r*1.5 + gap);
    dy = (r*sqrt(3) + gap);
    for (iy=[0:ceil(area_h/dy)])
        for (ix=[0:ceil(area_w/dx)]) {
            ox = ix*dx; oy = iy*dy + (ix%2)*dy/2;
            if (ox<=area_w && oy<=area_h)
                translate([ox, oy, -0.1])
                    cylinder(r=r, h=th+0.2, $fn=6);
        }
}

// ---- text label (emboss/deboss) ------------------------------------
module label(txt, size=5, depth=0.6, deboss=true) {
    // place this where you want it; combine with difference() for deboss
    linear_extrude(depth) text(txt, size=size, halign="center", valign="center");
}

// ---- print-accuracy & reliability tricks ---------------------------
// OpenSCAD circles are INSCRIBED polygons, so a hole printed from circle()/
// cylinder() comes out undersized (the flats sit inside the nominal diameter),
// and FDM over-extrusion shrinks holes further. Two fixes, both here:
//   * radius compensation: scale r by 1/cos(180/fn) so the polygon CIRCUMSCRIBES
//     the true circle (flats land on the nominal diameter).
//   * use a modest, defined facet count (6-12) so the facets are crisp and the
//     size is predictable, instead of a near-circle that's hard to slice cleanly.

// Accurate hole: radius-compensated, defined facet count. Subtract from a solid.
module hole(d, h, fn=8, through=true) {
    e = through ? 0.2 : 0;
    r = (d/2) / cos(180/fn);
    translate([0,0,-e/2]) cylinder(r=r, h=h+e, $fn=fn);
}

// Explicit polygon hole with a flat oriented to +X (predictable across a wall).
module poly_hole(d, h, sides=6, through=true) {
    e = through ? 0.2 : 0;
    r = (d/2) / cos(180/sides);
    translate([0,0,-e/2]) rotate([0,0,180/sides]) cylinder(r=r, h=h+e, $fn=sides);
}

// Teardrop hole for a HORIZONTAL bore: the pointed top self-supports (<=45deg) so
// the hole doesn't sag. Built in XY; rotate the part so the point faces up in Z.
module teardrop(d, h, fn=24) {
    r = d/2;
    linear_extrude(h)
        hull() {
            circle(r=r, $fn=fn);
            translate([0, r]) rotate(45) square(r*0.78, center=true);
        }
}

// Horizontal teardrop bore through a vertical wall (cable gland, side bolt, axle).
// A round horizontal hole sags at the top; the teardrop's apex self-supports so it
// prints clean. Built so the bore axis runs along +X and the pointed apex faces
// +Z (up), assuming the part is printed floor-down — rotate about Z to aim it at
// another wall (e.g. rotate([0,0,90]) for a +Y/-Y wall). Subtract from the wall.
module teardrop_bore(d, len, fn=24) {
    rotate([90, 0, 0]) rotate([0, 90, 0]) teardrop(d, len, fn=fn);
}

// Peg / locating pin with a lead-in chamfer so it self-aligns into its hole.
module pin(d, h, chamfer=0.6, fn=24) {
    r = d/2;
    union() {
        cylinder(r=r, h=h-chamfer, $fn=fn);
        translate([0,0,h-chamfer]) cylinder(r1=r, r2=max(0.1,r-chamfer), h=chamfer, $fn=fn);
    }
}

// Triangular gusset rib: stiffens a vertical feature off a base WITHOUT supports
// (the hypotenuse stays <=45deg). thickness 't' runs along Y.
module gusset(leg=8, height=8, t=2) {
    rotate([90,0,0]) linear_extrude(t)
        polygon([[0,0],[leg,0],[0,height]]);
}

// "Mouse ear" / corner disc: a thin pad fused to a part corner on the bed to stop
// warping/lifting on tall or sharp-cornered prints. Snap/cut off after printing.
module mouse_ear(r=6, th=0.3) {
    cylinder(r=r, h=th, $fn=48);
}

// Sacrificial bridge: a thin solid layer left over an opening so the slicer can
// bridge it flat instead of needing support; drill/pop it out after. Subtract the
// opening, then ADD this 1-layer cap back across it at the chosen Z.
module bridge_cap(w, d, z, layer=0.2) {
    translate([0,0,z]) cube([w, d, layer]);
}

// ---- cable management ----------------------------------------------
// Rules of thumb baked into defaults: a cable pass-through wants ~ cable_d + 1mm,
// a strain-relief/grommet seat a few mm wider, and a wire's minimum bend radius
// is roughly 4-6x its outer diameter (leave that much clear in front of a
// connector and along a routing channel).

// Plain cable pass-through hole (subtract from a wall of thickness 'th').
module cable_hole(cable_d=4, th=2, clearance=1.0) {
    translate([0,0,-0.1]) cylinder(d=cable_d+clearance, h=th+0.2);
}

// Grommet / strain-relief seat: a counterbored pass-through that accepts a
// rubber grommet (seat_d) and passes the cable (cable_d) through the inner web.
module cable_grommet(cable_d=4, seat_d=8, seat_depth=3, th=2) {
    translate([0,0,-0.1]) cylinder(d=cable_d+1, h=th+0.2);        // through-hole
    translate([0,0,th-seat_depth]) cylinder(d=seat_d, h=seat_depth+0.2); // seat
}

// Keyhole strain relief slot (cable snaps into the round part, tail held in slot).
module cable_keyhole(cable_d=4, slot_w=2.0, len=8, th=2) {
    translate([0,0,-0.1]) {
        cylinder(d=cable_d+1, h=th+0.2);
        translate([-slot_w/2, 0, 0]) cube([slot_w, len, th+0.2]);
    }
}

// Cable clip / tie-down post: a small hooked post to route a bundle along a wall
// (ADD to the model). Loop a zip-tie under the hook, or press the cable in.
module cable_clip(cable_d=4, h=8, t=1.6) {
    gap = cable_d + 0.5;
    translate([0,0,0]) cube([t, gap+2*t, h]);                 // back wall
    translate([0,0,h-t]) cube([gap+t, gap+2*t, t]);           // top hook
    translate([0,gap+t,0]) cube([t, t, h]);                   // front lip (partial)
}

// Routing channel: a U-shaped trough to guide a bundle (ADD as walls, or subtract
// from a solid). Here as two side ribs of height 'h' spaced for the bundle.
module cable_channel(length=30, cable_d=4, h=5, t=1.6) {
    w = cable_d + 1.5;
    translate([0,    0, 0]) cube([length, t, h]);
    translate([0, w+t, 0]) cube([length, t, h]);
}

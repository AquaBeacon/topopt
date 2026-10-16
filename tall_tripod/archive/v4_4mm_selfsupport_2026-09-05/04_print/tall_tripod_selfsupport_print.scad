// Print-ready model: the smoothed optimised body with the camera mount head
// put back from CAD (tall_tripod.scad) and its slots / bore / pockets cut.
// The optimised body carries the head as a 4 mm voxel blob that bulges past
// the CAD head and closed the slot mouths, so that blob is removed first
// (box around the head from z = 120 up, where the slots and pockets are; below
// that the head is a plain pedestal and the legs may join it) together with the
// screw-access volumes the study declared as obstacle (the CAD STL, which is
// closed -- the voxelised one is not and OpenSCAD silently skips it); then the
// CAD head goes in and its cuts come out.
// Rendered with: openscad -o tall_tripod_selfsupport_print.stl <this file>
head_clear = 4.5;      // one analysis voxel plus smoothing
difference() {
    union() {
        difference() {
            import("tall_tripod_selfsupport_smooth.stl", convexity = 10);
            translate([-15 - head_clear, -15 - head_clear, 120])
                cube([30 + 2 * head_clear, 30 + 2 * head_clear, 40]);
            import("../../../02_setup/tall_obstacle.stl", convexity = 10);
        }
        import("../../../02_setup/tall_mount_solid.stl", convexity = 10);
    }
    import("../../../02_setup/tall_mount_cuts.stl", convexity = 10);
    import("../../../02_setup/tall_obstacle.stl", convexity = 10);
}

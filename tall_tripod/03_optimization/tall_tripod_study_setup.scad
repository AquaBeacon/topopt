// Generative-design setup preview -- open in OpenSCAD (F5).
// green = preserve, red = obstacle + clearance,
// grey  = design space, blue = starting shape,
// orange = attach zone (the only cells that may join a
//          preserve body which declared `attach`).
// STL carries no colour, which is why this file exists.

color([0.62,0.65,0.70,0.18]) import("tall_tripod_study_design_space.stl");
color([0.13,0.72,0.33,1.0]) import("tall_tripod_study_preserve.stl");

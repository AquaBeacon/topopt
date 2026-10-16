// Generative-design setup preview -- open in OpenSCAD (F5).
// green = preserve, red = obstacle + clearance,
// grey  = design space, blue = starting shape.
// STL carries no colour, which is why this file exists.

color([0.62,0.65,0.70,0.18]) import("control_study_design_space.stl");
color([0.88,0.19,0.19,0.55]) import("control_study_obstacle.stl");
color([0.13,0.72,0.33,1.0]) import("control_study_preserve.stl");

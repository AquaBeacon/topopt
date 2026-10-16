"""Manual summary-path benchmark; no timing thresholds in the test suite.

Run: python topopt/tests/benchmark_underwater.py
Prints JSON and verifies that both paths return identical rounded quantities.
"""
import json
import statistics
import time
import tracemalloc

import numpy as np
from topopt import underwater
from topopt.voxel import Grid


def main():
    grid = Grid(solid=np.ones((80, 80, 80), bool), h=1.0, origin=np.zeros(3))
    results = {}
    outputs = []
    for nodal in (True, False):
        times = []
        for _ in range(6):
            start = time.perf_counter()
            force, info = underwater.hydrostatic_nodal_forces(
                grid, grid.solid, 10080, sealed=True, _nodal=nodal)
            times.append(time.perf_counter() - start)
            del force
        outputs.append(info)
        tracemalloc.start()
        force, _ = underwater.hydrostatic_nodal_forces(
            grid, grid.solid, 10080, sealed=True, _nodal=nodal)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del force
        results["nodal" if nodal else "summary"] = {
            "median_seconds": statistics.median(times[1:]),
            "peak_traced_bytes": peak, "measured_runs": 5}
    assert outputs[0] == outputs[1]
    results["equal_quantities"] = True
    results["speedup"] = results["nodal"]["median_seconds"] / results["summary"]["median_seconds"]
    results["scope"] = "hydrostatic summary only; 80^3 full grid, warm medians; not optimiser speed"
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

"""Support-contact and enclosed-support checks (spec 19.5).

Both failures share a shape: the support *total* looks perfectly reasonable
while the part is ruined. A few mm3 of support landing on a bolt seat is a fit
failure; support sealed inside a cavity never comes out at all. Neither shows
up in a volume number, which is why they are separate checks.
"""

import numpy as np
import pytest

from topopt import printcheck as pc


def _empty(shape=(6, 6, 6)):
    return np.zeros(shape, bool)


# ---------------------------------------------------------------- overhang

def test_a_slab_on_the_build_plate_is_not_an_overhang():
    """`np.roll` wraps, so without an explicit first-layer rule the bottom
    layer is compared against the top one and a block lying flat on the bed
    reports its whole footprint as unsupported."""
    m = _empty()
    m[:, :, 0] = True
    assert pc.overhang_cells(m).sum() == 0
    assert pc.support_columns(m).sum() == 0


def test_a_floating_slab_is_an_overhang():
    m = _empty()
    m[:, :, 3] = True
    assert pc.overhang_cells(m).sum() == 36


def test_support_fills_the_gap_below_a_floating_slab():
    m = _empty()
    m[:, :, 3] = True
    cols = pc.support_columns(m)
    assert cols[:, :, :3].all()          # three layers under the slab
    assert not cols[:, :, 3:].any()      # nothing above it


def test_support_stops_at_the_first_solid_below():
    m = _empty()
    m[:, :, 0] = True                    # base
    m[:, :, 4] = True                    # roof
    cols = pc.support_columns(m)
    assert cols[:, :, 1:4].all()
    assert not cols[:, :, 0].any()


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_the_build_axis_is_respected(axis):
    m = _empty()
    idx = [slice(None)] * 3
    idx[axis] = 3
    m[tuple(idx)] = True
    assert pc.overhang_cells(m, build_axis=axis).sum() == 36
    # Seen along any other axis the same plate is a wall, not an overhang.
    other = (axis + 1) % 3
    assert pc.overhang_cells(m, build_axis=other).sum() < 36


# --------------------------------------------------- support on mating faces

def test_support_landing_on_preserve_is_a_failure():
    m = _empty((5, 5, 6))
    m[:, :, 0] = True                    # a bolt seat, say
    m[:, :, 4] = True                    # something spanning over it
    preserve = _empty((5, 5, 6))
    preserve[:, :, 0] = True
    r = pc.support_on_critical_faces(m, preserve, h=1.0)
    assert r["pass"] is False
    assert r["support_contact_area_mm2"] == 25.0
    assert "rough and out of tolerance" in r["note"]
    assert r["locations_voxel"]


def test_support_landing_on_ordinary_material_is_fine():
    m = _empty((5, 5, 6))
    m[:, :, 0] = True
    m[:, :, 4] = True
    r = pc.support_on_critical_faces(m, _empty((5, 5, 6)), h=1.0)
    assert r["pass"] is True
    assert r["support_contact_area_mm2"] == 0.0
    # ... and it still reports that support landed somewhere.
    assert r["total_landing_area_mm2"] == 25.0


def test_area_scales_with_the_voxel_size():
    m = _empty((5, 5, 6))
    m[:, :, 0] = True
    m[:, :, 4] = True
    pres = _empty((5, 5, 6))
    pres[:, :, 0] = True
    a = pc.support_on_critical_faces(m, pres, h=1.0)["support_contact_area_mm2"]
    b = pc.support_on_critical_faces(m, pres, h=2.0)["support_contact_area_mm2"]
    assert b == pytest.approx(4 * a)


# ------------------------------------------------------- enclosed support

def test_support_in_a_sealed_box_cannot_be_removed():
    m = _empty((7, 7, 7))
    m[:, :, 0] = m[:, :, 6] = True
    m[0, :, :] = m[6, :, :] = m[:, 0, :] = m[:, 6, :] = True
    r = pc.enclosed_support(m, h=1.0)
    assert r["pass"] is False
    assert r["enclosed_support_mm3"] > 0
    assert r["enclosed_support_mm3"] == r["total_support_mm3"]
    assert "cannot be removed" in r["note"]


def test_support_under_an_open_table_is_reachable():
    m = _empty((7, 7, 6))
    for x in (0, 6):
        for y in (0, 6):
            m[x, y, :4] = True           # four legs
    m[:, :, 4] = True                    # open-sided top
    r = pc.enclosed_support(m, h=1.0)
    assert r["total_support_mm3"] > 0
    assert r["pass"] is True
    assert "reachable from outside" in r["note"]


def test_a_diagonal_gap_is_not_an_escape_route():
    """Void connectivity is 6-connected on purpose: two voxels meeting only at
    an edge are not a channel anyone can get a tool through, and 26-connectivity
    would call that cavity open."""
    m = np.ones((7, 7, 7), bool)
    m[2:5, 2:5, 2:5] = False             # a sealed cavity with a solid roof
    m[1, 1, 1] = False                   # a void touching it only at a corner
    from scipy import ndimage
    lab, _n = ndimage.label(~m, structure=ndimage.generate_binary_structure(3, 1))
    assert lab[3, 3, 3] != lab[1, 1, 1], "corner contact is not a channel"

    # The cavity has a roof, so it really does contain support -- and being
    # sealed, that support stays in the part.
    r = pc.enclosed_support(m, h=1.0)
    assert r["pass"] is False
    assert r["enclosed_support_mm3"] == r["total_support_mm3"]

    # Open one wall to the outside and the same support becomes removable.
    # The lone corner void is filled back in first: a sealed one-voxel pocket
    # with material over it is itself unremovable support, and leaving it in
    # would make this assertion pass or fail for the wrong reason.
    m2 = m.copy()
    m2[1, 1, 1] = True
    m2[2:5, 0:2, 2:5] = False            # a channel out through -Y
    assert pc.enclosed_support(m2, h=1.0)["pass"] is True


def test_enclosed_support_is_a_subset_of_all_support():
    m = _empty((8, 8, 8))
    m[:, :, 0] = True
    m[:, :, 5] = True                    # open shelf -> reachable support
    m[1:4, 1:4, 2] = True                # a floating island under it
    r = pc.enclosed_support(m, h=1.0)
    assert r["enclosed_support_mm3"] <= r["total_support_mm3"]


# ----------------------------------------------------------------- report

def test_report_carries_both_checks_and_says_what_it_measured():
    m = _empty((5, 5, 6))
    m[:, :, 0] = True
    m[:, :, 4] = True
    pres = _empty((5, 5, 6))
    pres[:, :, 0] = True
    r = pc.report(m, pres, h=1.6, build_axis=2)
    assert r["build_axis"] == "Z"
    assert r["support_on_critical_faces"]["pass"] is False
    assert r["enclosed_support"]["pass"] is True
    # The limitation is stated in the output, not only in the docstring:
    # a voxel model has no 60-degree faces to self-support.
    assert "0 or 90 degrees" in r["note"]

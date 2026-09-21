"""Coverage for palace/netgen_meshing.py's curved-element mesh-quality gate.

No test coverage existed for this at all before this file -- confirmed by
searching the repo -- despite the Gmsh-side equivalent (_check_mesh_quality
in palace/meshing.py) having its own tests. Mirrors those tests directly
(same synthetic-geometry technique, same assertions where the two backends
share behavior) so both backends are held to the same bar, plus a direct
test of _tet_volume_batch since it's new, pure-numeric logic with an easy
independent way to check it (a tetrahedron with a known, hand-computed
volume).

is_bad is volume-weighted here for the same reason as the Gmsh side: a
handful of below-threshold elements confined to a geometrically negligible
feature does not mean the mesh is actually risky. See
palace.meshing._check_mesh_quality's docstring and CLAUDE.md's mesh-quality
invariant for the full empirical case (every example board in this repo,
plus a real Palace solve on a mesh with confirmed inversions that completed
cleanly with physically sane results).
"""

import numpy as np
import pytest

from palace.netgen_meshing import _check_curved_mesh_quality, _tet_volume_batch


def _generate_curved_mesh(occ, shape, maxh):
    geo = occ.OCCGeometry(shape)
    mesh = geo.GenerateMesh(maxh=maxh)
    mesh.SecondOrder()
    mesh.Curve(2)
    return mesh


def test_tet_volume_batch_matches_hand_computed_volume():
    # A right tetrahedron with legs 2, 3, 4 along the axes has volume
    # (1/6)*2*3*4 = 4 exactly -- independent of the quality metric, this
    # only checks the volume formula itself. Padded with 6 duplicate
    # "midside" entries (unused by this function, only the first 4 corners
    # are read) to match the (N, 10, 3) shape _check_curved_mesh_quality
    # actually passes in.
    corners = [(0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 4)]
    coords = np.array([corners + corners[:1] * 6], dtype=float)
    vol = _tet_volume_batch(coords)
    assert vol.shape == (1,)
    assert vol[0] == pytest.approx(4.0)


def test_check_curved_mesh_quality_disabled_when_threshold_not_positive():
    messages = []
    result = _check_curved_mesh_quality(None, messages.append, 0.0)

    assert result["is_bad"] is False
    assert result["n_elements"] == 0
    assert messages == []


def test_check_curved_mesh_quality_passes_for_well_shaped_mesh():
    occ = pytest.importorskip("netgen.occ")
    box = occ.Box(occ.Pnt(0, 0, 0), occ.Pnt(10, 10, 10))
    mesh = _generate_curved_mesh(occ, box, maxh=2.0)

    messages = []
    result = _check_curved_mesh_quality(mesh, messages.append, 0.1)

    assert result["is_bad"] is False
    assert result["n_bad"] == 0
    assert result["n_elements"] > 0
    assert result["min_quality"] > 0.1
    assert result["bad_volume_fraction"] == 0.0
    assert result["inverted_volume_fraction"] == 0.0
    assert any("quality check passed" in m for m in messages)


def test_check_curved_mesh_quality_flags_degenerate_mesh():
    occ = pytest.importorskip("netgen.occ")
    # Same coarse-vs-thin-feature mismatch as the Gmsh-side degenerate test:
    # a thin plate forced to mesh 40x too coarse for its own thickness.
    plate = occ.Box(occ.Pnt(0, 0, 0), occ.Pnt(10, 10, 0.05))
    mesh = _generate_curved_mesh(occ, plate, maxh=2.0)

    messages = []
    result = _check_curved_mesh_quality(mesh, messages.append, 0.1)

    assert result["is_bad"] is True
    assert result["n_bad"] > 0
    assert result["min_quality"] < 0.1
    assert result["bad_volume_fraction"] > 0.5  # the whole plate is thin
    assert any("WARNING" in m and "quality" in m for m in messages)


def test_check_curved_mesh_quality_passes_when_bad_elements_are_a_negligible_sliver():
    occ = pytest.importorskip("netgen.occ")
    block = occ.Box(occ.Pnt(0, 0, 0), occ.Pnt(10, 10, 10))
    # A thin shim sitting on top of the block -- same mismatch as the
    # degenerate-mesh test above, but confined to a feature whose own
    # volume is a tiny fraction of the whole (mirrors the real pattern:
    # a small SMD pad or thin trace fused onto an otherwise healthy board).
    shim = occ.Box(occ.Pnt(0, 0, 10), occ.Pnt(1, 1, 10.01))
    combined = occ.Glue([block, shim])
    mesh = _generate_curved_mesh(occ, combined, maxh=2.0)

    messages = []
    result = _check_curved_mesh_quality(mesh, messages.append, 0.1)

    assert result["n_bad"] > 0  # the shim really does produce bad elements
    assert result["bad_volume_fraction"] < 0.01
    assert result["is_bad"] is False
    assert any("quality check passed" in m for m in messages)

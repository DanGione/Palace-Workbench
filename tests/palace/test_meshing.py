"""Coverage for palace/meshing.py's conductor-group fusing logic.

_fuse_group_solids() exists to absorb tiny overlaps/touches between solids
that share one conductor group (e.g. a hand-placed pin meeting a trace)
before their faces are exported to Gmsh -- an unresolved overlap there
otherwise leaves a razor-thin sliver face for Gmsh's fragment/mesh steps to
choke on, producing inverted (negative-Jacobian) tetrahedra right at the
seam and derailing the linear solver.
"""

from unittest import mock

import FreeCAD
import Part
import pytest

from palace.meshing import (
    _apply_refinement_fields,
    _check_mesh_quality,
    _fuse_group_solids,
    _import_conductor_solid_faces,
    _log_group_overlaps,
    format_quality_warning,
)


class _FakeSolid:
    """Duck-typed stand-in for a Part::Feature document object -- only
    .Shape and .Label are read by _fuse_group_solids()."""

    def __init__(self, shape, label):
        self.Shape = shape
        self.Label = label


class _FakeGroup:
    def __init__(self, label):
        self.Label = label


def _log(_msg):
    pass


def test_single_solid_group_returned_unfused():
    box = Part.makeBox(2, 2, 2)
    solid = _FakeSolid(box, "OnlySolid")
    grp = _FakeGroup("Copper")

    shape, label = _fuse_group_solids(grp, [solid], _log)

    assert shape is box
    assert label == "OnlySolid"


def test_overlapping_solids_are_fused_and_simplified():
    # Two boxes overlapping by 1 unit along X -- a stand-in for a pin
    # slightly interpenetrating a trace.
    b1 = Part.makeBox(2, 2, 2)
    b2 = Part.makeBox(2, 2, 2, FreeCAD.Vector(1, 0, 0))
    grp = _FakeGroup("Copper")

    shape, label = _fuse_group_solids(
        grp, [_FakeSolid(b1, "Pin"), _FakeSolid(b2, "Trace")], _log)

    assert label == "Copper (2 solids fused)"
    assert shape.isValid()
    # Union volume is less than the naive sum -- confirms an actual boolean
    # fuse happened, not just a concatenation of the two shapes.
    assert shape.Volume == pytest.approx(b1.Volume + b2.Volume - 1 * 2 * 2, rel=1e-6)
    # removeSplitter() collapses the two overlapping boxes' faces down to a
    # single combined box's worth of faces (6), not the naive sum (12) --
    # the redundant coincident faces at the former overlap are gone.
    assert len(shape.Faces) == 6


def test_disjoint_solids_are_preserved_not_merged():
    b1 = Part.makeBox(1, 1, 1)
    b2 = Part.makeBox(1, 1, 1, FreeCAD.Vector(10, 0, 0))
    grp = _FakeGroup("Copper")

    shape, label = _fuse_group_solids(
        grp, [_FakeSolid(b1, "A"), _FakeSolid(b2, "B")], _log)

    assert label == "Copper (2 solids fused)"
    # No geometry gained or lost for genuinely disjoint solids.
    assert len(shape.Solids) == 2
    assert shape.Volume == pytest.approx(b1.Volume + b2.Volume)
    assert len(shape.Faces) == len(b1.Faces) + len(b2.Faces)


def test_log_group_overlaps_reports_overlapping_pair():
    b1 = Part.makeBox(2, 2, 2)
    b2 = Part.makeBox(2, 2, 2, FreeCAD.Vector(1, 0, 0))   # overlaps b1 by volume 4
    grp = _FakeGroup("Copper")

    messages = []
    _log_group_overlaps(
        grp, [_FakeSolid(b1, "Connector"), _FakeSolid(b2, "GroundPlane")], messages.append)

    assert len(messages) == 1
    assert "Connector" in messages[0]
    assert "GroundPlane" in messages[0]
    assert "Copper" in messages[0]
    assert "4" in messages[0]   # overlap volume (2*2*1)


def test_log_group_overlaps_silent_for_disjoint_solids():
    b1 = Part.makeBox(1, 1, 1)
    b2 = Part.makeBox(1, 1, 1, FreeCAD.Vector(10, 0, 0))
    grp = _FakeGroup("Copper")

    messages = []
    _log_group_overlaps(grp, [_FakeSolid(b1, "A"), _FakeSolid(b2, "B")], messages.append)

    assert messages == []


def test_log_group_overlaps_silent_for_merely_touching_solids():
    # Share exactly one face -- touching, zero-volume intersection, not a
    # real overlap.
    b1 = Part.makeBox(1, 1, 1)
    b2 = Part.makeBox(1, 1, 1, FreeCAD.Vector(1, 0, 0))
    grp = _FakeGroup("Copper")

    messages = []
    _log_group_overlaps(grp, [_FakeSolid(b1, "A"), _FakeSolid(b2, "B")], messages.append)

    assert messages == []


def test_fuse_group_solids_still_fuses_correctly_when_overlap_logged():
    # The new diagnostic logging must not change _fuse_group_solids's own
    # output -- it's purely additive.
    b1 = Part.makeBox(2, 2, 2)
    b2 = Part.makeBox(2, 2, 2, FreeCAD.Vector(1, 0, 0))
    grp = _FakeGroup("Copper")

    messages = []
    shape, label = _fuse_group_solids(
        grp, [_FakeSolid(b1, "Connector"), _FakeSolid(b2, "GroundPlane")], messages.append)

    assert label == "Copper (2 solids fused)"
    assert shape.Volume == pytest.approx(b1.Volume + b2.Volume - 1 * 2 * 2, rel=1e-6)
    # The overlap warning fired on the way in, ahead of the fuse.
    assert any("overlap" in m for m in messages)


def test_fuse_failure_falls_back_to_unfused_compound():
    # Part.Shape is an immutable C-extension type, so neither the class nor
    # an instance can be monkeypatched to simulate a real OCC fuse failure.
    # Use a stub with a raising fuse() to force the except branch, and stub
    # Part.makeCompound (an ordinary patchable function) so the fallback
    # doesn't need a real Shape to complete.
    class _StubShape:
        def fuse(self, _others):
            raise RuntimeError("simulated OCC failure")

    bad_solid = _FakeSolid(_StubShape(), "Bad")
    other_solid = _FakeSolid(Part.makeBox(1, 1, 1), "Good")
    grp = _FakeGroup("Copper")

    messages = []
    with mock.patch("Part.makeCompound", return_value="STUB_COMPOUND") as make_compound:
        shape, label = _fuse_group_solids(grp, [bad_solid, other_solid], messages.append)

    assert label == "Copper"   # fallback label -- no "(N solids fused)" suffix
    assert shape == "STUB_COMPOUND"
    make_compound.assert_called_once_with([bad_solid.Shape, other_solid.Shape])
    assert any("could not fuse" in m for m in messages)


# ---------------------------------------------------------------------------
# Integration: _import_conductor_solid_faces with a live Gmsh session
# ---------------------------------------------------------------------------

def test_import_conductor_solid_faces_fuses_before_export():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_fuse_import")

        b1 = Part.makeBox(2, 2, 2)
        b2 = Part.makeBox(2, 2, 2, FreeCAD.Vector(1, 0, 0))
        grp = _FakeGroup("Copper")
        cond_bodies = [(grp, _FakeSolid(b1, "Pin")), (grp, _FakeSolid(b2, "Trace"))]

        result = _import_conductor_solid_faces(gmsh, cond_bodies, _log)

        # One entry for the one conductor group, regardless of how many
        # solids it contains.
        assert len(result) == 1
        result_grp, surf_tags = result[0]
        assert result_grp is grp
        # Fused+simplified box pair has 6 faces -- confirms the fuse ran
        # before export rather than each solid's 6 faces being imported
        # independently (which would give 12).
        assert len(surf_tags) == 6
    finally:
        gmsh.finalize()


# ---------------------------------------------------------------------------
# _check_mesh_quality
# ---------------------------------------------------------------------------

def test_check_mesh_quality_disabled_when_threshold_not_positive():
    # threshold <= 0 must short-circuit before touching gmsh at all.
    messages = []
    result = _check_mesh_quality(None, messages.append, 0.0)

    assert result["is_bad"] is False
    assert result["n_elements"] == 0
    assert messages == []


def test_check_mesh_quality_passes_for_well_shaped_mesh():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_quality_good")
        gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 2.0)
        gmsh.model.mesh.generate(3)

        messages = []
        result = _check_mesh_quality(gmsh, messages.append, 0.1)

        assert result["is_bad"] is False
        assert result["n_bad"] == 0
        assert result["n_elements"] > 0
        assert result["min_quality"] > 0.1
        assert any("quality check passed" in m for m in messages)
    finally:
        gmsh.finalize()


def test_check_mesh_quality_flags_degenerate_mesh():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_quality_bad")
        # A thin plate (0.05 units thick) forced to mesh with a target size 40x
        # its own thickness -- the same coarse-vs-thin-feature mismatch found in
        # the Coax_to_Microstrip_wComponents.FCStd conductor groups earlier this
        # session -- reliably produces very low quality tetrahedra.
        gmsh.model.occ.addBox(0, 0, 0, 10, 10, 0.05)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", 2.0)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 2.0)
        gmsh.model.mesh.generate(3)

        messages = []
        result = _check_mesh_quality(gmsh, messages.append, 0.1)

        assert result["is_bad"] is True
        assert result["n_bad"] > 0
        assert result["min_quality"] < 0.1
        assert any("WARNING" in m and "quality" in m for m in messages)
    finally:
        gmsh.finalize()


def test_format_quality_warning_includes_counts_and_worst_value():
    quality = {"n_bad": 3, "n_elements": 100, "min_quality": 0.0417}

    msg = format_quality_warning(quality)

    assert "3/100" in msg
    assert "0.0417" in msg


# ---------------------------------------------------------------------------
# _apply_refinement_fields
# ---------------------------------------------------------------------------
# generate_mesh() uses this return value to decide whether a subsequent
# meshing failure gets the "no MeshSize configured anywhere" hint -- so False
# must mean "gmsh sized the whole model on its own defaults" and True must
# mean "at least one Distance/Threshold field actually constrains sizing".

def test_apply_refinement_fields_returns_false_when_nothing_configured():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_refinement_none")
        gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
        gmsh.model.occ.synchronize()

        applied = _apply_refinement_fields(
            gmsh, cl_max=0.0,
            cond_data=[], port_surf_tags=[], integ_curve_tags=[], diel_data=[],
            global_cond_size=0.0, port_size=0.0, refine_dist=0.0, log_fn=_log,
        )

        assert applied is False
    finally:
        gmsh.finalize()


def test_apply_refinement_fields_returns_true_when_a_group_size_is_set():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_refinement_configured")
        gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
        gmsh.model.occ.synchronize()
        surf_tags = [t for _, t in gmsh.model.getEntities(2)]
        grp = _FakeGroup("Copper")

        applied = _apply_refinement_fields(
            gmsh, cl_max=0.0,
            cond_data=[(grp, surf_tags)], port_surf_tags=[], integ_curve_tags=[],
            diel_data=[], global_cond_size=0.2, port_size=0.0, refine_dist=0.0,
            log_fn=_log,
        )

        assert applied is True
    finally:
        gmsh.finalize()

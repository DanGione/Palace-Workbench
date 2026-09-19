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
    _assign_material_physical_groups,
    _check_mesh_quality,
    _fuse_group_solids,
    _import_conductor_solids,
    _log_group_overlaps,
    _update_vol_physical_groups_after_fragment,
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
# Integration: _import_conductor_solids with a live Gmsh session
# ---------------------------------------------------------------------------
# Conductors are imported as their own standalone Gmsh *volumes* (not
# decomposed into faces) so the combined port+conductor occ.fragment() pass
# can include them as objects and let OCC's boolean kernel actually carve
# each conductor's cavity out of the surrounding material. Importing only
# bare faces and relying on fragment() to infer an enclosed cavity from that
# loose set was tried first and found unreliable in production (see
# CLAUDE.md) -- roughly half of every conductor's own faces ended up either
# bounding zero volumes or absorbed into a coincident dielectric/dielectric
# interface, both of which crash Palace's MFEM solver on launch (STable3D
# abort from a boundary triangle with no adjacent tetrahedron).

def test_import_conductor_solids_fuses_before_import():
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_fuse_import")

        b1 = Part.makeBox(2, 2, 2)
        b2 = Part.makeBox(2, 2, 2, FreeCAD.Vector(1, 0, 0))
        grp = _FakeGroup("Copper")
        cond_bodies = [(grp, _FakeSolid(b1, "Pin")), (grp, _FakeSolid(b2, "Trace"))]

        result = _import_conductor_solids(gmsh, cond_bodies, _log)

        # One entry for the one conductor group, regardless of how many
        # solids it contains.
        assert len(result) == 1
        result_grp, vol_tags = result[0]
        assert result_grp is grp
        # The fused+simplified box pair imports as a single Gmsh volume --
        # confirms the fuse ran before import, and that a genuine 3-D solid
        # entered Gmsh rather than a bag of standalone 2-D faces.
        assert len(vol_tags) == 1
        assert gmsh.model.getEntities(3) == [(3, vol_tags[0])]
    finally:
        gmsh.finalize()


def test_import_conductor_solids_imports_one_volume_per_disjoint_solid():
    # A conductor group with several disjoint solids (e.g. many separate SMD
    # contacts) must import as multiple volumes, one per solid -- not fused
    # into a single entity, and not silently dropped.
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_disjoint_import")

        b1 = Part.makeBox(1, 1, 1)
        b2 = Part.makeBox(1, 1, 1, FreeCAD.Vector(10, 0, 0))
        grp = _FakeGroup("SmdContacts")
        cond_bodies = [(grp, _FakeSolid(b1, "A")), (grp, _FakeSolid(b2, "B"))]

        result = _import_conductor_solids(gmsh, cond_bodies, _log)

        assert len(result) == 1
        _, vol_tags = result[0]
        assert len(vol_tags) == 2
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


# _update_vol_physical_groups_after_fragment() rebuilds each material's Gmsh
# physical-volume group after occ.fragment() invalidates it. In production,
# on a document with six material groups, Gmsh's addPhysicalGroup() raised
# "Physical volume 1 already exists" immediately after removePhysicalGroups()
# had supposedly cleared that same tag -- a rare Gmsh OCC bookkeeping quirk,
# not reproducible with a handful of boxes in isolation. The rebuild must
# retry once instead of letting that exception abort the whole mesh.

class _FakeGmshModel:
    def __init__(self, fail_once_tags=()):
        self._fail_once = set(fail_once_tags)
        self.physical_groups = {}   # (dim, tag) -> [vols]
        self.names = {}             # (dim, tag) -> name

    def getPhysicalGroups(self, dim):
        return [(dim, tag) for (d, tag) in self.physical_groups if d == dim]

    def removePhysicalGroups(self, dim_tags):
        for dt in dim_tags:
            self.physical_groups.pop(dt, None)

    def addPhysicalGroup(self, dim, vols, tag):
        if tag in self._fail_once:
            self._fail_once.discard(tag)
            raise Exception(f"Physical volume {tag} already exists")
        self.physical_groups[(dim, tag)] = list(vols)
        return tag

    def setPhysicalName(self, dim, tag, name):
        self.names[(dim, tag)] = name


class _FakeGmsh:
    def __init__(self, fail_once_tags=()):
        self.model = _FakeGmshModel(fail_once_tags)


def test_update_vol_physical_groups_retries_once_on_stale_tag_collision():
    gmsh = _FakeGmsh(fail_once_tags={1})
    old_vol_tags = [2, 3]
    out_map = [[(3, 2)], [(3, 3)]]
    vol_pg_info = {
        1: ("Airbox_Background", frozenset({2})),
        2: ("Dielectric_2", frozenset({3})),
    }

    _update_vol_physical_groups_after_fragment(
        gmsh, old_vol_tags, out_map, vol_pg_info, log_fn=_log)

    assert gmsh.model.physical_groups[(3, 1)] == [2]
    assert gmsh.model.physical_groups[(3, 2)] == [3]
    assert gmsh.model.names[(3, 1)] == "Airbox_Background"
    assert gmsh.model.names[(3, 2)] == "Dielectric_2"


def test_update_vol_physical_groups_skips_group_that_still_collides_after_retry():
    gmsh = _FakeGmsh(fail_once_tags=set())
    gmsh.model.addPhysicalGroup = mock.Mock(
        side_effect=Exception("Physical volume 1 already exists"))
    old_vol_tags = [2, 3]
    out_map = [[(3, 2)], [(3, 3)]]
    vol_pg_info = {
        1: ("Airbox_Background", frozenset({2})),
        2: ("Dielectric_2", frozenset({3})),
    }

    # Must not raise -- a group that still collides after the retry is
    # skipped (with a warning), not allowed to abort the whole mesh.
    _update_vol_physical_groups_after_fragment(
        gmsh, old_vol_tags, out_map, vol_pg_info, log_fn=_log)

    assert (3, 1) not in gmsh.model.physical_groups
    assert (3, 2) not in gmsh.model.physical_groups


# _assign_material_physical_groups() fragments the airbox with all dielectric
# solids together. Two dielectric solids from *different* groups can overlap
# in the CAD model (e.g. a broad fill/substrate a smaller, more specific
# dielectric sits inside without a pre-cut cavity) -- fragment() then puts the
# shared sub-volume in both groups' output. A production document hit exactly
# this: a "PTFE" fill's claimed volumes fully swallowed a smaller "FR-4"
# board's, leaving Airbox_Background with zero volumes. Contested tags must
# resolve to exactly one group (the smaller solid), not both.

def test_assign_material_physical_groups_resolves_dielectric_overlap_by_size(tmp_path):
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_diel_overlap")

        airbox_shape = Part.makeBox(20, 20, 20, FreeCAD.Vector(-10, -10, -10))
        big_shape    = Part.makeBox(8, 8, 8, FreeCAD.Vector(0, 0, 0))     # PTFE-like fill
        small_shape  = Part.makeBox(2, 2, 2, FreeCAD.Vector(1, 1, 1))    # FR-4-like, inside big_shape

        step_path = str(tmp_path / "geom.step")
        Part.makeCompound([airbox_shape, big_shape, small_shape]).exportStep(step_path)

        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()
        all_vol_tags_before = [t for _, t in gmsh.model.getEntities(3)]
        assert len(all_vol_tags_before) == 3

        airbox_obj = _FakeSolid(airbox_shape, "Airbox")
        big_grp = _FakeGroup("PTFE")
        big_grp.MeshAttribute = 3
        small_grp = _FakeGroup("FR-4")
        small_grp.MeshAttribute = 2
        diel_bodies = [
            (big_grp, _FakeSolid(big_shape, "PTFE_solid")),
            (small_grp, _FakeSolid(small_shape, "FR4_solid")),
        ]

        warnings = []
        _assign_material_physical_groups(
            gmsh, warnings.append, airbox_obj, diel_bodies, all_vol_tags_before)

        vols_by_attr = {}
        for _, pg_tag in gmsh.model.getPhysicalGroups(3):
            vols_by_attr[pg_tag] = set(gmsh.model.getEntitiesForPhysicalGroup(3, pg_tag))

        assert not (vols_by_attr.get(2, set()) & vols_by_attr.get(3, set()))
        assert vols_by_attr.get(2)          # smaller solid (FR-4) keeps the contested region
        assert vols_by_attr.get(1)          # background survives -- not swallowed whole
        assert any("overlap" in w.lower() for w in warnings)
    finally:
        gmsh.finalize()


# A dielectric group can legitimately have several child solids that end up
# as separate-but-touching Gmsh volumes after the material fragment (either
# because the user put multiple adjacent bodies in one group, or because the
# overlap resolution above leaves a group's "remainder" split into pieces
# that happen to touch elsewhere). The boundary between two same-attribute
# fragments is physically meaningless but still a real Gmsh surface, and one
# production document crashed mesh.generate(3) ("Invalid boundary mesh") when
# such a boundary landed exactly tangent to a later-imported conductor face.
# _assign_material_physical_groups() must fuse same-attribute adjacent
# fragments into one volume so that spurious boundary never reaches mesh
# generation; fragments that don't actually touch must be left alone.

def test_assign_material_physical_groups_fuses_touching_same_group_fragments(tmp_path):
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_diel_fuse_touching")

        airbox_shape = Part.makeBox(20, 20, 20, FreeCAD.Vector(-10, -10, -10))
        # Two boxes sharing the face at X=0 -- touching, not overlapping.
        left_shape  = Part.makeBox(5, 5, 5, FreeCAD.Vector(-5, -2.5, -2.5))
        right_shape = Part.makeBox(5, 5, 5, FreeCAD.Vector(0, -2.5, -2.5))

        step_path = str(tmp_path / "geom.step")
        Part.makeCompound([airbox_shape, left_shape, right_shape]).exportStep(step_path)

        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()
        all_vol_tags_before = [t for _, t in gmsh.model.getEntities(3)]
        assert len(all_vol_tags_before) == 3

        airbox_obj = _FakeSolid(airbox_shape, "Airbox")
        grp = _FakeGroup("FR-4")
        grp.MeshAttribute = 2
        diel_bodies = [
            (grp, _FakeSolid(left_shape, "FR4_left")),
            (grp, _FakeSolid(right_shape, "FR4_right")),
        ]

        logged = []
        _assign_material_physical_groups(
            gmsh, logged.append, airbox_obj, diel_bodies, all_vol_tags_before)

        diel_vols = gmsh.model.getEntitiesForPhysicalGroup(3, 2)
        assert len(diel_vols) == 1, (
            "touching same-attribute fragments must be fused into one volume")
        assert any("Fused 2 same-material fragment" in msg for msg in logged)
    finally:
        gmsh.finalize()


def test_assign_material_physical_groups_leaves_disjoint_same_group_fragments(tmp_path):
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("test_diel_fuse_disjoint")

        airbox_shape = Part.makeBox(20, 20, 20, FreeCAD.Vector(-10, -10, -10))
        # Two boxes on opposite sides of the airbox -- never touch.
        left_shape  = Part.makeBox(2, 2, 2, FreeCAD.Vector(-8, -1, -1))
        right_shape = Part.makeBox(2, 2, 2, FreeCAD.Vector(6, -1, -1))

        step_path = str(tmp_path / "geom.step")
        Part.makeCompound([airbox_shape, left_shape, right_shape]).exportStep(step_path)

        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()
        all_vol_tags_before = [t for _, t in gmsh.model.getEntities(3)]
        assert len(all_vol_tags_before) == 3

        airbox_obj = _FakeSolid(airbox_shape, "Airbox")
        grp = _FakeGroup("SmdCeramic")
        grp.MeshAttribute = 4
        diel_bodies = [
            (grp, _FakeSolid(left_shape, "Smd_left")),
            (grp, _FakeSolid(right_shape, "Smd_right")),
        ]

        _assign_material_physical_groups(
            gmsh, _log, airbox_obj, diel_bodies, all_vol_tags_before)

        diel_vols = gmsh.model.getEntitiesForPhysicalGroup(3, 4)
        assert len(diel_vols) == 2, "disjoint fragments must not be merged"
    finally:
        gmsh.finalize()

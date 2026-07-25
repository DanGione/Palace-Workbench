"""Coverage for features/component.py: the shared Component base that both
SMDComponent and the Selection-derived "Create Component from Selection"
feature build on -- placement-propagation math, create/delete round trips,
and delete_component's empty-material-group cleanup.
"""

import FreeCAD
import Part
import pytest

from features import find_dielectric_groups, find_conductor_groups
from features.component import create_component_from_solids, delete_component, ViewProviderComponent
from features.smd_component import create_smd_component, delete_smd_component


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestComponent")
    yield d
    FreeCAD.closeDocument(d.Name)


def _make_solid(doc, name, box_dims=(1, 1, 1), position=(0, 0, 0)):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(*box_dims)
    obj.Placement = FreeCAD.Placement(FreeCAD.Vector(*position), FreeCAD.Rotation())
    return obj


def _placements_close(p1, p2, tol=1e-6):
    return (p1.Base - p2.Base).Length < tol and abs(p1.Rotation.Angle - p2.Rotation.Angle) < tol


# ---------------------------------------------------------------------------
# Placement propagation
# ---------------------------------------------------------------------------

def test_smd_placement_broadcast_equivalence(doc):
    """SMD's master/shadow solids are all baked at the SAME absolute Placement
    at creation time, so ChildBasePlacements' bases all reduce to Identity --
    moving the container must broadcast the new Placement onto every child
    verbatim, exactly like the original flat-broadcast implementation."""
    container = create_smd_component(doc, "Resistor", "0402", 100.0)

    new_placement = FreeCAD.Placement(FreeCAD.Vector(5, 2, 0), FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 30))
    container.Placement = new_placement
    doc.recompute()

    for child in list(container.Group) + list(container.ShadowSolids):
        assert _placements_close(child.Placement, new_placement)


def test_selection_component_preserves_relative_offsets(doc):
    """Two solids captured at different absolute placements must keep their
    relative offset when the component is moved -- not collapse onto the
    container's new Placement (the bug this design fixes)."""
    src_a = _make_solid(doc, "SrcA", position=(0, 0, 0))
    src_b = _make_solid(doc, "SrcB", position=(10, 0, 0))
    doc.recompute()

    container = create_component_from_solids(doc, [
        (src_a, "Conductor", "TestCopper"),
        (src_b, "Dielectric", "TestFR4"),
    ])
    assert len(container.ShadowSolids) == 2

    delta = FreeCAD.Placement(FreeCAD.Vector(1, 2, 3), FreeCAD.Rotation())
    container.Placement = delta
    doc.recompute()

    shadow_a, shadow_b = container.ShadowSolids
    assert _placements_close(shadow_a.Placement, delta.multiply(src_a.Placement))
    assert _placements_close(shadow_b.Placement, delta.multiply(src_b.Placement))
    # Relative offset between the two solids must be unchanged (10 along X).
    offset = shadow_b.Placement.Base - shadow_a.Placement.Base
    assert abs(offset.Length - 10.0) < 1e-6


# ---------------------------------------------------------------------------
# create_component_from_solids: material sharing
# ---------------------------------------------------------------------------

def test_create_component_from_solids_shares_existing_material_by_name(doc):
    src_a = _make_solid(doc, "SrcA")
    src_b = _make_solid(doc, "SrcB", position=(5, 0, 0))
    doc.recompute()

    create_component_from_solids(doc, [(src_a, "Conductor", "SharedCopper")])
    create_component_from_solids(doc, [(src_b, "Conductor", "SharedCopper")])

    groups = [g for g in find_conductor_groups(doc) if g.MaterialName == "SharedCopper"]
    assert len(groups) == 1
    assert len(groups[0].Group) == 2


# ---------------------------------------------------------------------------
# create_component_from_solids: name preservation
# ---------------------------------------------------------------------------

def test_shadow_label_derives_from_source_label_not_internal_name(doc):
    """Regression test: the source object keeps a generic internal Name
    (typical of a Body/Pad the user never renamed at the FreeCAD-Name level)
    but a meaningful, user-chosen Label. The shadow must derive its name from
    that Label, not the source's internal Name -- using .Name here would
    produce a shadow Label with no relation to what the user actually called
    the shape.

    The source stays live in the document (by design -- see features/
    component.py's module docstring), so FreeCAD's own Label-uniqueness
    enforcement still appends a numeric suffix to the shadow's Label (an
    exact, unsuffixed match is only possible if the source is renamed or
    removed, which this feature deliberately does not do) -- but the result
    must clearly derive from "Ground Plane", not from the source's unrelated
    internal Name.
    """
    src = doc.addObject("Part::Feature", "Body003")
    src.Label = "Ground Plane"
    src.Shape = Part.makeBox(1, 1, 1)
    doc.recompute()

    container = create_component_from_solids(
        doc, [(src, "Conductor", "Copper")], base_name="TestComp")

    shadow = container.ShadowSolids[0]
    assert shadow.Label.startswith("Ground Plane")
    assert "Body003" not in shadow.Label


def test_add_solid_pair_master_label_matches_source(doc):
    src = _make_solid(doc, "MicrostripTrace")
    doc.recompute()

    container = create_component_from_solids(
        doc, [(src, "Conductor", "Copper")], base_name="TestComp")

    master = container.Group[0]
    assert master.Label == "MicrostripTraceMaster"


# ---------------------------------------------------------------------------
# delete_component cleanup
# ---------------------------------------------------------------------------

def test_delete_component_removes_now_empty_material_group(doc):
    src = _make_solid(doc, "Src")
    doc.recompute()
    container = create_component_from_solids(doc, [(src, "Dielectric", "OnlyUser")])

    matching = [g for g in find_dielectric_groups(doc) if g.MaterialName == "OnlyUser"]
    assert len(matching) == 1

    container_name = container.Name
    delete_component(doc, container)

    assert container_name not in [o.Name for o in doc.Objects]
    assert not any(g.MaterialName == "OnlyUser" for g in find_dielectric_groups(doc))


def test_view_provider_on_delete_cascades_children(doc):
    """Exercises the exact hook FreeCAD's tree-view Delete command consults
    (ViewProvider.onDelete) -- confirmed empirically that a plain
    Document.removeObject() does NOT invoke any App-level Proxy.onDelete in
    this FreeCAD build, so without this hook a tree-view Delete only removed
    the container, orphaning its masters/shadows/material groups."""
    src = _make_solid(doc, "Src")
    doc.recompute()
    container = create_component_from_solids(doc, [(src, "Dielectric", "OnlyUserVP")])
    container_name = container.Name

    class _FakeVObj:
        pass
    fake_vobj = _FakeVObj()
    fake_vobj.Object = container

    # Instantiated via __new__ to skip __init__ (which needs a real, GUI-only
    # ViewObject) -- onDelete only ever touches its vobj argument, not self.
    view_provider = object.__new__(ViewProviderComponent)
    assert view_provider.onDelete(fake_vobj, []) is True

    # What FreeCAD itself does immediately after onDelete returns True.
    doc.removeObject(container_name)

    assert container_name not in [o.Name for o in doc.Objects]
    assert not any(g.MaterialName == "OnlyUserVP" for g in find_dielectric_groups(doc))


def test_delete_component_preserves_material_group_shared_by_another_component(doc):
    src_a = _make_solid(doc, "SrcA")
    src_b = _make_solid(doc, "SrcB", position=(5, 0, 0))
    doc.recompute()

    container_a = create_component_from_solids(doc, [(src_a, "Conductor", "SharedCopper")])
    container_b = create_component_from_solids(doc, [(src_b, "Conductor", "SharedCopper")])

    delete_component(doc, container_a)

    groups = [g for g in find_conductor_groups(doc) if g.MaterialName == "SharedCopper"]
    assert len(groups) == 1
    assert list(groups[0].Group) == list(container_b.ShadowSolids)


def test_delete_smd_component_removes_now_empty_material_groups(doc):
    """Same cleanup fix, exercised through the SMD path (a pre-existing gap
    this refactor closes -- see features/component.py's delete_component)."""
    container = create_smd_component(doc, "Resistor", "0402", 100.0)
    container_name = container.Name
    delete_smd_component(doc, container)

    assert container_name not in [o.Name for o in doc.Objects]
    assert not any(g.MaterialName == "SmdCeramic" for g in find_dielectric_groups(doc))
    assert not any(g.MaterialName == "SmdContacts" for g in find_conductor_groups(doc))

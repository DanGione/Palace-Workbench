import FreeCAD
import pytest

from features import smd_component as smdc


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestSMDComponent")
    yield d
    FreeCAD.closeDocument(d.Name)


def _create(doc, **overrides):
    kwargs = dict(component_type="Resistor", size_code="0402", value_si=100.0,
                  include_pads=True, include_solder_joint=True)
    kwargs.update(overrides)
    return smdc.create_smd_component(doc, **kwargs)


def test_create_smd_component_nests_all_master_children_under_one_container(doc):
    container = _create(doc)
    assert container.TypeId == "Part::FeaturePython"
    # body + coating + 4 contacts + 2 pads + 2 solder = 10 (hidden masters)
    assert len(container.Group) == 10
    assert len(container.ShadowSolids) == 10


def test_create_smd_component_no_pads_no_solder(doc):
    container = _create(doc, include_pads=False, include_solder_joint=False)
    # body + coating + 4 contacts = 6
    assert len(container.Group) == 6
    assert len(container.ShadowSolids) == 6
    assert not container.IncludePads
    assert not container.IncludeSolderJoint


# Note: master-hidden/shadow-visible behavior can't be asserted headlessly --
# obj.ViewObject is None under freecadcmd (no GUI, no view objects at all).
# _add_solid() already guards against that (`if obj.ViewObject is not None`),
# so nothing crashes headlessly, but actually confirming the Visibility
# values needs a live GUI session (see the plan's manual verification steps).


def test_shared_groups_have_real_members_immediately_after_creation(doc):
    container = _create(doc)
    from features import find_dielectric_groups, find_conductor_groups

    diel_members = set()
    for g in find_dielectric_groups(doc):
        diel_members.update(o.Name for o in g.Group)
    cond_members = set()
    for g in find_conductor_groups(doc):
        cond_members.update(o.Name for o in g.Group)

    shadow_names = {s.Name for s in container.ShadowSolids}
    # every shadow solid is a real member of some shared group right away --
    # no mesh-time step is needed for classification to take effect.
    assert shadow_names <= (diel_members | cond_members)
    assert len(diel_members | cond_members) == 10


def test_shared_groups_created_with_descriptive_labels(doc):
    _create(doc)
    from features.material_group import (
        find_dielectric_group_by_material, find_conductor_group_by_material,
    )
    ceramic = find_dielectric_group_by_material(doc, smdc.CERAMIC_MATERIAL)
    assert ceramic.Label == smdc.CERAMIC_MATERIAL
    contacts = find_conductor_group_by_material(doc, smdc.CONTACT_MATERIAL)
    assert contacts.Label == smdc.CONTACT_MATERIAL


def test_pad_conductor_group_override_applied_eagerly(doc):
    from features.material_group import create_conductor_group
    custom_group = create_conductor_group(doc, mesh_attr=500)
    custom_group.MaterialName = "Custom Lead-Free Pads"

    container = _create(doc, pad_conductor_group=custom_group)
    assert container.PadConductorGroupOverride is custom_group
    # the pads are already, physically, in the custom group -- no deferred step
    assert len(custom_group.Group) == 2

    # the default shared "SmdPads" group must never have been created
    from features.material_group import find_conductor_group_by_material
    assert find_conductor_group_by_material(doc, smdc.PAD_MATERIAL) is None


def test_placement_change_propagates_to_masters_and_shadows(doc):
    container = _create(doc)
    new_placement = FreeCAD.Placement(FreeCAD.Vector(5, 6, 7), FreeCAD.Rotation())
    container.Placement = new_placement
    for child in list(container.Group) + list(container.ShadowSolids):
        assert child.Placement.Base == new_placement.Base


def test_impedance_boundary_created_with_correct_value(doc):
    _create(doc, component_type="Capacitor", value_si=22e-12)
    from features import find_impedance_boundaries
    boundaries = find_impedance_boundaries(doc)
    assert len(boundaries) == 1
    assert boundaries[0].C == 22e-12
    assert boundaries[0].R == 0.0
    assert boundaries[0].L == 0.0


def test_impedance_boundary_direction_is_cartesian_not_radial(doc):
    # Regression test: Direction used to default to "+R" (radial, meant for
    # annular/coaxial geometry) for every edge-pair-created boundary, which
    # made _compute_ar() bail out to 0 and silently left Rs/Ls/Cs at 0 even
    # though R/L/C had a real value.
    _create(doc, component_type="Resistor", value_si=100.0)
    from features import find_impedance_boundaries
    boundary = find_impedance_boundaries(doc)[0]
    assert boundary.Direction not in ("+R", "-R")
    assert boundary.Direction in ("X", "-X", "Y", "-Y", "Z", "-Z")
    assert boundary.AspectRatio > 0.0
    assert boundary.Rs != 0.0


def test_contact_is_not_stray_relative_to_impedance_boundary(doc):
    # A shadow contact (referenced by the ImpedanceBoundary) is legitimately
    # inside a ConductorGroup, so features/port_group.py's existing,
    # unmodified _in_any_palace_group check already recognizes it -- no
    # SMD-specific code is needed for this to hold under the new design.
    _create(doc)
    from features.material_group import find_conductor_group_by_material
    from features.port_group import _in_any_palace_group
    contact_group = find_conductor_group_by_material(doc, smdc.CONTACT_MATERIAL)
    shadow_contact = contact_group.Group[0]
    assert _in_any_palace_group(doc, shadow_contact) is True


def test_delete_smd_component_removes_everything(doc):
    container = _create(doc)
    master_names = [o.Name for o in container.Group]
    shadow_names = [o.Name for o in container.ShadowSolids]
    boundary_name = container.ImpedanceBoundaryObj.Name
    container_name = container.Name

    smdc.delete_smd_component(doc, container)

    live_names = {o.Name for o in doc.Objects}
    assert container_name not in live_names
    assert boundary_name not in live_names
    for name in master_names + shadow_names:
        assert name not in live_names

    # The shared groups themselves are deleted too, since this component was
    # their only member (features.component.delete_component_children prunes
    # any material group left empty -- see features/component.py).
    from features import find_dielectric_groups, find_conductor_groups
    assert not any(g.MaterialName == smdc.CERAMIC_MATERIAL for g in find_dielectric_groups(doc))
    assert not any(g.MaterialName == smdc.COATING_MATERIAL for g in find_dielectric_groups(doc))
    assert not any(g.MaterialName == smdc.CONTACT_MATERIAL for g in find_conductor_groups(doc))
    assert not any(g.MaterialName == smdc.PAD_MATERIAL for g in find_conductor_groups(doc))
    assert not any(g.MaterialName == smdc.SOLDER_MATERIAL for g in find_conductor_groups(doc))


def test_impedance_boundary_position_defaults_to_flipped(doc):
    container = _create(doc)
    assert container.ImpedanceBoundaryPosition == "Flipped"


@pytest.mark.parametrize("position_mode", ["Flipped", "Conventional", "Middle"])
def test_impedance_boundary_position_persisted(doc, position_mode):
    container = _create(doc, position_mode=position_mode)
    assert container.ImpedanceBoundaryPosition == position_mode


def test_middle_mode_splits_body_into_two_masters_sharing_one_ceramic_group(doc):
    container = _create(doc, position_mode="Middle")
    # body_lower + body_upper + coating + 4 contacts + 2 pads + 2 solder = 11
    assert len(container.Group) == 11
    assert len(container.ShadowSolids) == 11

    from features.material_group import find_dielectric_group_by_material
    ceramic = find_dielectric_group_by_material(doc, smdc.CERAMIC_MATERIAL)
    coating = find_dielectric_group_by_material(doc, smdc.COATING_MATERIAL)
    assert len(ceramic.Group) == 2   # body_lower + body_upper share the SAME group
    assert len(coating.Group) == 1


@pytest.mark.parametrize("position_mode", ["Flipped", "Conventional", "Middle"])
def test_regenerate_on_edit_preserves_position_mode(doc, position_mode):
    container = _create(doc, position_mode=position_mode)
    smdc.delete_smd_component(doc, container)
    new_container = smdc.create_smd_component(
        doc, "Resistor", "0402", 100.0, position_mode=position_mode,
    )
    assert new_container.ImpedanceBoundaryPosition == position_mode


def test_regenerate_on_edit_preserves_port_index(doc):
    container = _create(doc, component_type="Resistor", value_si=100.0)
    old_port_index = container.ImpedanceBoundaryObj.PortIndex

    smdc.delete_smd_component(doc, container)
    new_container = smdc.create_smd_component(
        doc, "Capacitor", "0603", 10e-12,
        include_pads=True, include_solder_joint=False,
        port_index=old_port_index,
    )

    from features import find_impedance_boundaries
    boundaries = find_impedance_boundaries(doc)
    assert len(boundaries) == 1
    assert boundaries[0].PortIndex == old_port_index
    assert boundaries[0].C == 10e-12
    assert boundaries[0].R == 0.0
    assert new_container.ComponentType == "Capacitor"
    assert new_container.SizeCode == "0603"

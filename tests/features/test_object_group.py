"""Coverage for the auto-managed Ports/Impedance Boundaries/Components tree
groups (features/object_group.py).

Unlike DielectricGroup/ConductorGroup, these groups have no creation command
and no panel -- they spring into existence via get_or_create_*() the moment
a matching object is created (or a document with scattered matching objects
is restored), and get deleted again by cleanup_group_if_orphaned() once
their last member is removed. This file covers that lifecycle directly.
"""

import FreeCAD
import pytest

from features import (
    find_port_group, find_impedance_boundary_group, find_component_group,
    find_wave_ports,
)
from features.object_group import (
    get_or_create_port_group,
    get_or_create_component_group, cleanup_group_if_orphaned, sync_all_groups,
)
from features.wave_port import create_wave_port
from features.lumped_port import create_lumped_port
from features.impedance_boundary import create_impedance_boundary


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestObjectGroup")
    yield d
    FreeCAD.closeDocument(d.Name)


def _make_fake_component(doc, name):
    """Minimal stand-in satisfying find_components()'s duck-type check
    (ChildBasePlacements + ComponentKind) without going through the full
    Component/SMDComponent creation machinery -- keeps these tests focused
    on object_group.py's own logic, not component.py's.
    """
    obj = doc.addObject("App::FeaturePython", name)
    obj.addProperty("App::PropertyPlacementList", "ChildBasePlacements", "Palace", "")
    obj.addProperty("App::PropertyString", "ComponentKind", "Palace", "")
    return obj


# ---------------------------------------------------------------------------
# get_or_create_* : creation + idempotence + auto-adopt
# ---------------------------------------------------------------------------

def test_get_or_create_port_group_creates_once(doc):
    assert find_port_group(doc) is None
    grp = get_or_create_port_group(doc)
    assert grp is not None
    assert find_port_group(doc) is grp
    # Calling again returns the SAME group, doesn't create a second one.
    assert get_or_create_port_group(doc) is grp


def test_get_or_create_port_group_adopts_existing_ports(doc):
    p1 = create_wave_port(doc, index=1)
    p2 = create_lumped_port(doc, index=2)
    # create_wave_port/create_lumped_port already call get_or_create_port_group
    # internally -- confirm both landed in the SAME group, not two separate ones.
    grp = find_port_group(doc)
    assert grp is not None
    assert set(grp.Group) == {p1, p2}


def test_get_or_create_impedance_boundary_group_adopts_existing(doc):
    b1 = create_impedance_boundary(doc, index=1000)
    grp = find_impedance_boundary_group(doc)
    assert grp is not None
    assert list(grp.Group) == [b1]


def test_get_or_create_component_group_adopts_existing(doc):
    c1 = _make_fake_component(doc, "Comp1")
    c2 = _make_fake_component(doc, "Comp2")
    assert find_component_group(doc) is None
    grp = get_or_create_component_group(doc)
    assert set(grp.Group) == {c1, c2}


def test_new_port_joins_existing_group_not_a_second_one(doc):
    create_wave_port(doc, index=1)
    grp_first = find_port_group(doc)
    create_wave_port(doc, index=2)
    grp_second = find_port_group(doc)
    assert grp_first is grp_second
    assert len(grp_first.Group) == 2


# ---------------------------------------------------------------------------
# cleanup_group_if_orphaned
# ---------------------------------------------------------------------------

def test_cleanup_deletes_group_when_last_member_removed(doc):
    p1 = create_wave_port(doc, index=1)
    grp = find_port_group(doc)
    grp_name = grp.Name

    cleanup_group_if_orphaned(doc, find_port_group, p1)
    doc.removeObject(p1.Name)
    doc.recompute()

    assert find_port_group(doc) is None
    assert grp_name not in {o.Name for o in doc.Objects}


def test_cleanup_keeps_group_when_other_members_remain(doc):
    p1 = create_wave_port(doc, index=1)
    p2 = create_lumped_port(doc, index=2)
    grp = find_port_group(doc)

    cleanup_group_if_orphaned(doc, find_port_group, p1)
    doc.removeObject(p1.Name)
    doc.recompute()

    assert find_port_group(doc) is grp
    assert list(grp.Group) == [p2]


def test_cleanup_is_a_noop_when_no_group_exists(doc):
    # Should not raise even though there's nothing to clean up.
    fake = doc.addObject("App::FeaturePython", "Stray")
    cleanup_group_if_orphaned(doc, find_port_group, fake)
    assert find_port_group(doc) is None


def test_cleanup_is_a_noop_when_object_is_not_a_member(doc):
    create_wave_port(doc, index=1)
    grp = find_port_group(doc)
    outsider = doc.addObject("App::FeaturePython", "Outsider")

    cleanup_group_if_orphaned(doc, find_port_group, outsider)

    # Group untouched -- outsider was never a member.
    assert find_port_group(doc) is grp
    assert len(grp.Group) == 1


# ---------------------------------------------------------------------------
# sync_all_groups (document-restore path)
# ---------------------------------------------------------------------------

def test_sync_all_groups_only_creates_groups_that_are_needed(doc):
    # No ports/impedance-boundaries/components at all -- nothing should appear.
    sync_all_groups(doc)
    assert find_port_group(doc) is None
    assert find_impedance_boundary_group(doc) is None
    assert find_component_group(doc) is None


def test_sync_all_groups_creates_only_the_categories_with_members(doc):
    create_wave_port(doc, index=1)
    # Wipe out the group create_wave_port already made, to simulate a
    # document saved before this feature existed (port present, no group).
    grp = find_port_group(doc)
    doc.removeObject(grp.Name)
    doc.recompute()
    assert find_port_group(doc) is None
    assert len(find_wave_ports(doc)) == 1

    sync_all_groups(doc)

    assert find_port_group(doc) is not None
    assert list(find_port_group(doc).Group) == find_wave_ports(doc)
    # No impedance boundaries or components exist -- no groups for those.
    assert find_impedance_boundary_group(doc) is None
    assert find_component_group(doc) is None


# ---------------------------------------------------------------------------
# sync_all_groups : self-healing a drifted document
#
# All three cases below were found baked into a real board
# (projects/Coax_Directional_Bridge.FCStd): its populated Components and
# Impedance Boundaries groups had left PalaceSimulation.Group and rendered at
# the document's top level, and an empty duplicate "Components" group sat
# inside the Simulation in their place. Nothing healed any of it on restore,
# and the tree offers no drop target to put a group back by hand.
# ---------------------------------------------------------------------------

def _make_simulation(doc):
    from features.simulation import SimulationContainer
    sim = doc.addObject("App::FeaturePython", "PalaceSimulation")
    sim.addExtension("App::GroupExtensionPython")
    SimulationContainer(sim)
    return sim


def test_sync_reparents_a_group_that_left_the_simulation(doc):
    sim = _make_simulation(doc)
    create_wave_port(doc, index=1)
    grp = find_port_group(doc)
    assert grp in sim.Group

    sim.removeObject(grp)
    assert grp not in sim.Group

    sync_all_groups(doc)

    assert grp in sim.Group
    assert len(grp.Group) == 1        # membership untouched by the re-parent


def test_sync_merges_duplicate_groups_and_keeps_every_member(doc):
    sim = _make_simulation(doc)
    c1 = _make_fake_component(doc, "Comp1")
    survivor = get_or_create_component_group(doc)

    # A second group carrying the same marker -- unreachable via
    # find_component_group() once the first one exists, so nothing else can
    # ever clean it up or notice its members.
    dup = doc.addObject("App::FeaturePython", "PalaceComponentsDup")
    dup.addExtension("App::GroupExtensionPython")
    from features.object_group import ComponentGroup
    ComponentGroup(dup)
    c2 = _make_fake_component(doc, "Comp2")
    dup.addObject(c2)
    dup_name = dup.Name

    sync_all_groups(doc)

    assert dup_name not in {o.Name for o in doc.Objects}
    assert find_component_group(doc) is survivor
    assert set(survivor.Group) == {c1, c2}      # the duplicate's member survived
    assert survivor in sim.Group


def test_sync_removes_an_empty_leftover_group(doc):
    _make_simulation(doc)
    grp = get_or_create_component_group(doc)   # created with no members at all
    grp_name = grp.Name

    sync_all_groups(doc)

    assert grp_name not in {o.Name for o in doc.Objects}
    assert find_component_group(doc) is None


def test_sync_keeps_a_group_holding_only_unrecognized_members(doc):
    # A group is only pruned when it is genuinely empty -- a stray object a
    # user parked inside it is not something to silently delete around.
    _make_simulation(doc)
    grp = get_or_create_component_group(doc)
    stray = doc.addObject("App::FeaturePython", "StrayThing")
    grp.addObject(stray)

    sync_all_groups(doc)

    assert find_component_group(doc) is grp
    assert list(grp.Group) == [stray]


def test_sync_is_idempotent_on_a_healthy_document(doc):
    sim = _make_simulation(doc)
    p1 = create_wave_port(doc, index=1)
    b1 = create_impedance_boundary(doc, index=1000)
    c1 = _make_fake_component(doc, "Comp1")
    get_or_create_component_group(doc)

    before = [o.Name for o in doc.Objects]
    sync_all_groups(doc)
    sync_all_groups(doc)

    assert [o.Name for o in doc.Objects] == before
    assert list(find_port_group(doc).Group) == [p1]
    assert list(find_impedance_boundary_group(doc).Group) == [b1]
    assert list(find_component_group(doc).Group) == [c1]
    for finder in (find_port_group, find_impedance_boundary_group, find_component_group):
        assert finder(doc) in sim.Group

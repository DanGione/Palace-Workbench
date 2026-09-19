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

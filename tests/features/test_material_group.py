"""Coverage for features/material_group.py's find_group_label_collision() --
used by the Dielectric/Conductor group task panels to warn before renaming a
group's tree Label to one already used by a sibling group.
"""

import FreeCAD
import pytest

from features.material_group import (
    create_dielectric_group, create_conductor_group, find_group_label_collision,
)


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestMaterialGroup")
    yield d
    FreeCAD.closeDocument(d.Name)


def test_no_collision_when_name_is_unique(doc):
    grp = create_dielectric_group(doc)
    grp.Label = "Substrate"

    assert find_group_label_collision(doc, grp, "Copper") is None


def test_no_false_positive_against_self(doc):
    grp = create_dielectric_group(doc)
    grp.Label = "Substrate"

    # Re-accepting the group's own unchanged name must not flag a collision
    # against itself.
    assert find_group_label_collision(doc, grp, "Substrate") is None


def test_collision_detected_against_sibling_dielectric_group(doc):
    other = create_dielectric_group(doc)
    other.Label = "Substrate"
    grp = create_dielectric_group(doc, mesh_attr=3)
    grp.Label = "Coating"

    assert find_group_label_collision(doc, grp, "Substrate") == "Substrate"


def test_collision_detected_across_conductor_and_dielectric_groups(doc):
    diel = create_dielectric_group(doc)
    diel.Label = "Copper"
    cond = create_conductor_group(doc)
    cond.Label = "Gold"

    assert find_group_label_collision(doc, cond, "Copper") == "Copper"

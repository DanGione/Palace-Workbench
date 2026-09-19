"""Regression coverage for MeshAttribute staying in sync with PortIndex.

A port's Gmsh physical-surface attribute (MeshAttribute) is derived from
PortIndex (10 + PortIndex) but stored as its own property, set together only
once at creation. A production document hit "Physical surface N already
exists" during meshing because a duplicated port's PortIndex was corrected
without anyone knowing MeshAttribute needed the same correction -- onChanged()
now keeps them in sync going forward, and onDocumentRestored() self-heals any
value already saved out of sync (same pattern as the App::PropertyFileIncluded
migration in tests/features/test_embedding_migration.py).
"""

import FreeCAD
import pytest

from features.impedance_boundary import create_impedance_boundary
from features.lumped_port import create_lumped_port
from features.wave_port import create_wave_port

_FACTORIES = [create_lumped_port, create_wave_port, create_impedance_boundary]


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestPortMeshAttribute")
    yield d
    FreeCAD.closeDocument(d.Name)


@pytest.mark.parametrize("factory", _FACTORIES)
def test_mesh_attribute_tracks_port_index_on_change(doc, factory):
    obj = factory(doc, index=5)
    assert obj.MeshAttribute == 15

    obj.PortIndex = 8

    assert obj.MeshAttribute == 18


@pytest.mark.parametrize("factory", _FACTORIES)
def test_mesh_attribute_self_heals_on_document_restore(doc, factory):
    obj = factory(doc, index=5)
    obj.PortIndex = 8
    # Force MeshAttribute back out of sync, as if this were a document saved
    # before the desync was fixed (a raw property write bypasses onChanged's
    # own recomputation the same way loading a stale value from disk does).
    obj.MeshAttribute = 15

    obj.Proxy.onDocumentRestored(obj)

    assert obj.MeshAttribute == 18

"""Regression coverage for a port's Label staying in sync with PortIndex.

A port's Label (its display name in the tree) used to be set once at
creation (f"PalaceWavePort{index}"/f"PalaceLumpedPort{index}") and never
touched again -- on a real board, enough create/delete/edit cycles left a
port's internal Name, Label, and PortIndex all showing different numbers
(e.g. Name="PalaceWavePort009", Label="PalaceWavePort002", PortIndex=2).
Label now reads simply "Port {PortIndex}" -- deliberately identical for
WavePort and LumpedPort, since they share one numbering sequence -- and
onChanged() keeps it in sync going forward the same way it already does for
MeshAttribute (see test_port_mesh_attribute.py), with onDocumentRestored()
self-healing any value already saved out of sync.

ImpedanceBoundary is deliberately NOT covered here -- it has its own,
separate numbering range (see _IMPEDANCE_INDEX_BASE) and wasn't part of
this request.
"""

import FreeCAD
import pytest

from features.lumped_port import create_lumped_port
from features.wave_port import create_wave_port

_FACTORIES = [create_lumped_port, create_wave_port]


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestPortLabelSync")
    yield d
    FreeCAD.closeDocument(d.Name)


@pytest.mark.parametrize("factory", _FACTORIES)
def test_label_set_from_port_index_at_creation(doc, factory):
    obj = factory(doc, index=5)
    assert obj.Label == "Port 5"


@pytest.mark.parametrize("factory", _FACTORIES)
def test_label_tracks_port_index_on_change(doc, factory):
    obj = factory(doc, index=5)
    obj.PortIndex = 8
    assert obj.Label == "Port 8"


@pytest.mark.parametrize("factory", _FACTORIES)
def test_label_self_heals_on_document_restore(doc, factory):
    obj = factory(doc, index=5)
    obj.PortIndex = 8
    # Force Label back out of sync, as if this were a document saved before
    # the desync was fixed (a raw property write bypasses onChanged's own
    # recomputation the same way loading a stale value from disk does).
    obj.Label = "PalaceWavePort002"

    obj.Proxy.onDocumentRestored(obj)

    assert obj.Label == "Port 8"


def test_wave_and_lumped_ports_share_identical_label_format(doc):
    wave = create_wave_port(doc, index=1)
    lumped = create_lumped_port(doc, index=2)
    assert wave.Label == "Port 1"
    assert lumped.Label == "Port 2"

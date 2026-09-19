"""Regression coverage for keeping impedance-boundary numbering out of the
way of excitation-port numbering.

next_port_index() used to pool LumpedPort + WavePort + ImpedanceBoundary
together into one shared counter. Since ImpedanceBoundary isn't a real
excitation port (excluded from find_lumped_ports/find_wave_ports and from
get_available_s_params), creating one before any stimulus port pushed the
first LumpedPort/WavePort's PortIndex above 1. next_port_index and
next_impedance_index are now independent counters.
"""

import FreeCAD
import pytest

from features import next_impedance_index, next_port_index, _IMPEDANCE_INDEX_BASE
from features.impedance_boundary import create_impedance_boundary
from features.lumped_port import create_lumped_port
from features.wave_port import create_wave_port


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestPortIndexAllocation")
    yield d
    FreeCAD.closeDocument(d.Name)


def test_next_port_index_starts_at_one_on_empty_document(doc):
    assert next_port_index(doc) == 1


def test_next_impedance_index_starts_at_base_on_empty_document(doc):
    assert next_impedance_index(doc) == _IMPEDANCE_INDEX_BASE


def test_impedance_boundary_created_first_does_not_shift_port_numbering(doc):
    create_impedance_boundary(doc, index=next_impedance_index(doc))
    create_impedance_boundary(doc, index=next_impedance_index(doc))

    assert next_port_index(doc) == 1

    lumped = create_lumped_port(doc, index=next_port_index(doc))
    assert lumped.PortIndex == 1

    wave = create_wave_port(doc, index=next_port_index(doc))
    assert wave.PortIndex == 2


def test_stimulus_ports_do_not_shift_impedance_numbering(doc):
    create_lumped_port(doc, index=next_port_index(doc))
    create_wave_port(doc, index=next_port_index(doc))

    assert next_impedance_index(doc) == _IMPEDANCE_INDEX_BASE

    boundary = create_impedance_boundary(doc, index=next_impedance_index(doc))
    assert boundary.PortIndex == _IMPEDANCE_INDEX_BASE

    boundary2 = create_impedance_boundary(doc, index=next_impedance_index(doc))
    assert boundary2.PortIndex == _IMPEDANCE_INDEX_BASE + 1

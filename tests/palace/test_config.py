import FreeCAD
import Part
import pytest

from features.simulation import create_simulation
from features.wave_port import create_wave_port
from palace.config import _build_boundaries, _probe_points_along_edge, generate_config


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("WavePortConfigTest")
    yield d
    FreeCAD.closeDocument(d.Name)


def _add_edge(doc, name, p1, p2):
    """A Part::Feature edge object usable as WavePort.IntegrationEdge."""
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeLine(FreeCAD.Vector(*p1), FreeCAD.Vector(*p2))
    return obj


def test_voltage_path_emitted_for_port_with_integration_edge(doc):
    wp = create_wave_port(doc, index=1)
    edge_obj = _add_edge(doc, "SignalToGround", (0, 0, 0), (0, 0, 1))
    wp.IntegrationEdge = (edge_obj, ["Edge1"])
    doc.recompute()

    boundaries = _build_boundaries(None, [], [wp], [])

    entry = boundaries["WavePort"][0]
    assert "VoltagePath" in entry
    assert len(entry["VoltagePath"]) >= 2
    # Directed signal (first vertex) -> ground (last vertex), matching IntegrationEdge order.
    assert entry["VoltagePath"][0] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
    assert entry["VoltagePath"][-1] == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)


def test_voltage_path_absent_without_integration_edge(doc):
    wp = create_wave_port(doc, index=1)
    doc.recompute()

    boundaries = _build_boundaries(None, [], [wp], [])

    assert "VoltagePath" not in boundaries["WavePort"][0]


def test_probe_points_along_edge_empty_without_integration_edge(doc):
    wp = create_wave_port(doc, index=1)
    assert _probe_points_along_edge(wp) == []


@pytest.mark.parametrize("prop,json_key,value", [
    ("MaxIts", "MaxIts", 50),
    ("KSPTol", "KSPTol", 1e-8),
    ("EigenTol", "EigenTol", 1e-6),
    ("NSamples", "NSamples", 200),
    ("Offset", "Offset", 2.5),
])
def test_tuning_knob_emitted_when_nonzero(doc, prop, json_key, value):
    wp = create_wave_port(doc, index=1)
    setattr(wp, prop, value)

    entry = _build_boundaries(None, [], [wp], [])["WavePort"][0]

    assert entry[json_key] == pytest.approx(value)


def test_tuning_knobs_omitted_at_default(doc):
    wp = create_wave_port(doc, index=1)

    entry = _build_boundaries(None, [], [wp], [])["WavePort"][0]

    for key in ("MaxIts", "KSPTol", "EigenTol", "NSamples", "Offset"):
        assert key not in entry


def test_generate_config_skips_field_probes_for_voltage_path_port(doc):
    sim = create_simulation(doc)
    sim.MeshFile = "mesh.msh"

    wp1 = create_wave_port(doc, index=1)
    edge_obj = _add_edge(doc, "SignalToGround1", (0, 0, 0), (0, 0, 1))
    wp1.IntegrationEdge = (edge_obj, ["Edge1"])

    # Port 2 has no IntegrationEdge -- hollow-waveguide TE/TM fallback, still
    # needs the face-grid probes for compute_te_tm_impedance.
    face_box = doc.addObject("Part::Box", "PortFaceBox")
    face_box.Length, face_box.Width, face_box.Height = 10, 10, 0.001
    doc.recompute()
    create_wave_port(doc, index=2, faces=[(face_box, ["Face1"])])
    doc.recompute()

    cfg = generate_config(doc)

    probes = cfg["Domains"].get("Postprocessing", {}).get("Probe", [])
    probe_indices = {p["Index"] for p in probes}

    # No V-line/grid probes reserved for port 1 (VoltagePath handles it natively).
    assert not any(1000 + 1 * 10 <= i < 1000 + 1 * 10 + 8 for i in probe_indices)
    assert not any(2000 + (1 - 1) * 200 <= i < 2000 + (1 - 1) * 200 + 200 for i in probe_indices)

    # Port 2 (no IntegrationEdge) still gets its face-grid probes.
    assert any(2000 + (2 - 1) * 200 <= i < 2000 + (2 - 1) * 200 + 200 for i in probe_indices)

    # And port 1's WavePort entry carries VoltagePath.
    wp_entries = {e["Index"]: e for e in cfg["Boundaries"]["WavePort"]}
    assert "VoltagePath" in wp_entries[1]
    assert "VoltagePath" not in wp_entries[2]

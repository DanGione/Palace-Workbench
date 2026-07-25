import FreeCAD
import Part
import pytest

from palace.port_geometry import detect_axis_direction


def _edge_at(x, y, z):
    """A short edge centered at (x, y, z) -- only CenterOfMass matters here."""
    return Part.makeLine(FreeCAD.Vector(x - 0.01, y, z), FreeCAD.Vector(x + 0.01, y, z))


@pytest.mark.parametrize("p1,p2,expected", [
    ((0, 0, 0), (1, 0, 0), "X"),
    ((0, 0, 0), (-1, 0, 0), "-X"),
    ((0, 0, 0), (0, 1, 0), "Y"),
    ((0, 0, 0), (0, -1, 0), "-Y"),
    ((0, 0, 0), (0, 0, 1), "Z"),
    ((0, 0, 0), (0, 0, -1), "-Z"),
])
def test_detect_axis_direction_axis_aligned(p1, p2, expected):
    e1 = _edge_at(*p1)
    e2 = _edge_at(*p2)
    assert detect_axis_direction(e1, e2) == expected


def test_detect_axis_direction_picks_dominant_component():
    e1 = _edge_at(0, 0, 0)
    e2 = _edge_at(5, 1, -1)   # X dominates
    assert detect_axis_direction(e1, e2) == "X"


def test_detect_axis_direction_negative_dominant_component():
    e1 = _edge_at(0, 0, 0)
    e2 = _edge_at(-0.2, 3, 0.1)   # Y dominates, negative-adjacent axis unaffected
    assert detect_axis_direction(e1, e2) == "Y"


def test_detect_axis_direction_order_matters():
    e1 = _edge_at(0, 0, 0)
    e2 = _edge_at(2, 0, 0)
    assert detect_axis_direction(e1, e2) == "X"
    assert detect_axis_direction(e2, e1) == "-X"

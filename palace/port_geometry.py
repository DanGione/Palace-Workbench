"""Port face geometry construction from edge/curve pairs.

These helpers build a Part.Shape representing a lumped port face from two
selected edges.  No FreeCAD document objects are referenced — inputs and
outputs are raw Part shapes.
"""

import Part
import FreeCAD


def _bbox_encloses(outer, inner, tol=1e-6):
    """True if *outer*'s BoundBox fully contains *inner*'s BoundBox."""
    ob, ib = outer.BoundBox, inner.BoundBox
    return (ob.XMin <= ib.XMin + tol and ob.XMax >= ib.XMax - tol and
            ob.YMin <= ib.YMin + tol and ob.YMax >= ib.YMax - tol and
            ob.ZMin <= ib.ZMin + tol and ob.ZMax >= ib.ZMax - tol)


def classify_edge_pair(edge1, edge2):
    """Classify two edges as an annular or planar port pair.

    Returns a 3-tuple ``(kind, primary, secondary)`` where *kind* is
    ``'annular'`` or ``'planar'``.  For annular ports, *primary* is the outer
    edge and *secondary* is the inner edge.  For planar ports, *primary* is
    the shorter edge (sets the port width) and *secondary* is the longer edge.
    """
    if _bbox_encloses(edge1, edge2):
        return "annular", edge1, edge2
    if _bbox_encloses(edge2, edge1):
        return "annular", edge2, edge1
    # Planar: sort by length — shorter edge defines the port width
    if edge1.Length <= edge2.Length:
        return "planar", edge1, edge2
    return "planar", edge2, edge1


def build_annular_face(outer_edge, inner_edge):
    """Build an annular (ring-shaped) face from outer and inner closed edges.

    The outer wire forms the boundary; the inner wire becomes a hole.  OCC
    requires the hole wire to be oriented opposite to the outer wire, so we
    try both orientations and return whichever succeeds.
    """
    outer_wire = Part.Wire([outer_edge])
    inner_wire = Part.Wire([inner_edge])

    # Try with reversed inner wire first (standard OCC hole orientation)
    inner_rev = inner_wire.copy()
    inner_rev.reverse()
    try:
        face = Part.Face([outer_wire, inner_rev])
        if face.isValid():
            return face
    except Exception:
        pass

    # Fallback: try without reversing
    try:
        face = Part.Face([outer_wire, inner_wire])
        if face.isValid():
            return face
    except Exception:
        pass

    raise RuntimeError(
        "Could not build an annular face from the selected edges.  "
        "Ensure both edges are closed curves on the same plane."
    )


def build_planar_port_face(short_edge, long_edge):
    """Build a rectangular port face between two edges on the same plane.

    The port width equals the length of *short_edge*.  The two endpoints of
    *short_edge* are projected perpendicularly onto the infinite line defined
    by *long_edge*; the four resulting points form a planar quadrilateral.
    """
    p1 = short_edge.Vertexes[0].Point
    p2 = short_edge.Vertexes[1].Point
    q1 = long_edge.Vertexes[0].Point
    q2 = long_edge.Vertexes[1].Point

    long_vec = q2 - q1
    length_sq = long_vec.dot(long_vec)
    if length_sq < 1e-12:
        raise RuntimeError("The longer edge has zero length; cannot build planar port face.")
    long_dir = long_vec / long_vec.Length

    def project(point):
        t = (point - q1).dot(long_dir)
        return q1 + long_dir * t

    r1 = project(p1)
    r2 = project(p2)

    wire = Part.makePolygon([p1, p2, r2, r1, p1])
    face = Part.Face(wire)
    if not face.isValid():
        raise RuntimeError(
            "Could not build a planar port face from the selected edges.  "
            "Ensure both edges are approximately coplanar."
        )
    return face


def build_port_shape(edge1, edge2):
    """Build a port face shape from two selected edges.

    Returns ``(shape, kind)`` where *shape* is a ``Part.Shape`` and *kind* is
    ``'annular'`` or ``'planar'``.
    """
    kind, primary, secondary = classify_edge_pair(edge1, edge2)
    if kind == "annular":
        return build_annular_face(primary, secondary), "annular"
    return build_planar_port_face(primary, secondary), "planar"

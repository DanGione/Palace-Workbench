"""Coverage for palace/netgen_meshing.py's pre-mesh OCC face matching.

_match_occ_faces() matches a netgen.occ candidate face against a real
FreeCAD reference face (a wave port's own target plane, an impedance
boundary's box face, etc.). No test coverage existed for it at all before
this file -- confirmed by searching the repo.

The candidates here are lightweight duck-typed stand-ins (only .mass,
.center, .vertices[].p are read by the function), not real netgen.occ
geometry -- this lets the tests target the function's own *decision
logic* directly and deterministically, rather than depending on whatever
centroid convention netgen.occ's own Face.center happens to use for a
particular curved/annular shape on the version of netgen installed
wherever these tests run.
"""

import Part
import FreeCAD
import pytest

from palace.netgen_meshing import _match_occ_faces, _approx_face_normal


class _FakeVertex:
    def __init__(self, p):
        self.p = p


class _FakeFace:
    def __init__(self, mass, center, vertex_points):
        self.mass = mass
        self.center = center
        self.vertices = [_FakeVertex(p) for p in vertex_points]


def _target_rectangle():
    """A plain 10x4 rectangle at Z=0 -- corners (0,0,0),(0,4,0),(10,0,0),
    (10,4,0), area 40, true center (5,2,0)."""
    return Part.makePlane(10, 4)


def test_match_occ_faces_accepts_exact_vertices_despite_far_off_centroid():
    # The real-board regression (Coax_to_Microstrip_wComponents.FCStd): a
    # candidate whose area matches exactly and whose every vertex sits
    # exactly on the target face was rejected anyway, because the
    # candidate's own reported center was 0.635mm from the true face --
    # past even the function's scaled-tolerance cap (0.5mm) -- despite the
    # deviation being entirely in-plane (not off the target's own plane at
    # all). Vertex agreement must win over an unreliable centroid.
    target = _target_rectangle()
    corners = [tuple(v.Point) for v in target.Vertexes]
    bad_center = (10.6, 2.0, 0.0)  # 0.6mm past the rectangle's own x=10 edge
    candidate = _FakeFace(mass=target.Area, center=bad_center, vertex_points=corners)

    matches = _match_occ_faces([candidate], target)

    assert matches == [candidate]


def test_match_occ_faces_still_rejects_a_perpendicular_face():
    # Must-not-regress: a face merely straddling the target's plane at its
    # middle (a side wall) -- centroid sits on the plane, but its own
    # vertices don't -- is still rejected by the flatness check.
    target = _target_rectangle()
    # A vertical strip through the middle of the rectangle's plane: its
    # centroid lies on Z=0, but its own vertices span Z=-1..1, well past
    # min_tol (0.05) from the target's Z=0 plane.
    candidate = _FakeFace(
        mass=1.0,
        center=(5.0, 2.0, 0.0),
        vertex_points=[(5.0, 2.0, -1.0), (5.0, 2.0, 1.0), (5.0, 2.0, -1.0), (5.0, 2.0, 1.0)],
    )

    matches = _match_occ_faces([candidate], target)

    assert matches == []


def test_match_occ_faces_still_rejects_an_oversized_fragment():
    # Must-not-regress: a candidate larger than the target itself can never
    # be a genuine sub-fragment, regardless of where its centroid lands.
    target = _target_rectangle()
    candidate = _FakeFace(
        mass=target.Area * 2,  # bigger than the whole target
        center=(5.0, 2.0, 0.0),
        vertex_points=[(0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (10.0, 0.0, 0.0), (10.0, 4.0, 0.0)],
    )

    matches = _match_occ_faces([candidate], target)

    assert matches == []


def test_match_occ_faces_still_rejects_a_genuinely_distant_candidate():
    target = _target_rectangle()
    candidate = _FakeFace(
        mass=target.Area,
        center=(500.0, 500.0, 0.0),
        vertex_points=[(500.0, 498.0, 0.0), (500.0, 502.0, 0.0),
                       (510.0, 498.0, 0.0), (510.0, 502.0, 0.0)],
    )

    matches = _match_occ_faces([candidate], target)

    assert matches == []


def test_match_occ_faces_returns_every_matching_fragment():
    # A FreeCAD face can correspond to several occ.Face pieces after
    # Glue() fragmentation -- every genuine sub-fragment must come back,
    # not just the first.
    target = _target_rectangle()
    left = _FakeFace(mass=20.0, center=(2.5, 2.0, 0.0),
                      vertex_points=[(0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (5.0, 0.0, 0.0), (5.0, 4.0, 0.0)])
    right = _FakeFace(mass=20.0, center=(7.5, 2.0, 0.0),
                       vertex_points=[(5.0, 0.0, 0.0), (5.0, 4.0, 0.0), (10.0, 0.0, 0.0), (10.0, 4.0, 0.0)])
    unrelated = _FakeFace(mass=1.0, center=(500.0, 2.0, 0.0),
                           vertex_points=[(500.0, 0.0, 0.0), (501.0, 0.0, 0.0),
                                          (500.0, 1.0, 0.0), (501.0, 1.0, 0.0)])

    matches = _match_occ_faces([left, right, unrelated], target)

    assert set(matches) == {left, right}


def test_match_occ_faces_rejects_a_perpendicular_face_even_at_the_min_tol_boundary():
    # The real-board regression (Coax_Directional_Bridge.FCStd /
    # Directional Bridge.FCStd, both via SMD-component impedance
    # boundaries): a 0.05mm-tall contact pad's own side wall -- a
    # genuinely perpendicular face -- has vertices at both Z=0 and
    # Z=0.05mm, exactly min_tol's own default. A real BREP roundtrip left
    # one such vertex's computed plane distance at
    # 0.049999999999999996 instead of the mathematically exact 0.05,
    # narrowly slipping under the flatness check's `>= min_tol` rejection
    # by floating-point noise alone -- confirmed directly against the
    # real document. This produced 2 extra wrongly-matched faces per
    # affected impedance boundary and fed Palace a boundary attribute
    # spanning both an interior and an exterior face, which crashed the
    # solver (SIGBUS) during matrix assembly.
    #
    # Reproduced here with the same tiny floating-point shortfall,
    # decoupled from an actual BREP roundtrip: the flatness check alone
    # must not be trusted to catch this (it's the whole point of the
    # regression), so this candidate is deliberately built so the OLD
    # code would have accepted it -- the normal-alignment check added
    # alongside it is what must reject it now.
    target = _target_rectangle()  # Z=0 plane, normal (0,0,1)
    just_under_min_tol = 0.05 - 1e-17
    candidate = _FakeFace(
        mass=0.025,
        center=(5.0, 2.0, 0.025),  # centroid conveniently on-plane-ish too
        vertex_points=[
            (5.0, 1.8, 0.0), (5.0, 2.2, 0.0),
            (5.0, 1.8, just_under_min_tol), (5.0, 2.2, just_under_min_tol),
        ],
    )
    # Confirm the premise: this candidate's own vertices really do slip
    # under min_tol (0.05), same as the real board's BREP-derived values.
    nx, ny, nz = 0.0, 0.0, 1.0
    plane_d = 0.0
    for v in candidate.vertices:
        assert abs(nx * v.p[0] + ny * v.p[1] + nz * v.p[2] - plane_d) < 0.05

    matches = _match_occ_faces([candidate], target)

    assert matches == []


def test_approx_face_normal_matches_target_plane_for_a_genuine_coplanar_fragment():
    target = _target_rectangle()
    coplanar = [(0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (5.0, 0.0, 0.0), (5.0, 4.0, 0.0)]
    n = _approx_face_normal([_FakeVertex(p) for p in coplanar])
    assert n is not None
    assert abs(abs(n[2]) - 1.0) < 1e-9  # parallel to target's own (0,0,1) normal


def test_approx_face_normal_returns_none_for_collinear_points():
    n = _approx_face_normal([_FakeVertex(p) for p in
                              [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]])
    assert n is None

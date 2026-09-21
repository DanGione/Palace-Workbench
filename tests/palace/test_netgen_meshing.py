"""Coverage for palace/netgen_meshing.py's post-Glue() domain identity match.

_match_reference() re-identifies each solid Glue() produces against the
original FreeCAD material it came from. Confirmed on a real board
(Directional Bridge.FCStd), across three stages of hardening:

1. A coax cable's center pin and its surrounding annular PTFE dielectric
   spacer are coaxial and axially centered at the same point, so they share
   a center of mass -- nearest-COM alone can't tell them apart, and the
   whole dielectric spacer was silently misclassified as the conductor,
   vanishing from the 3-D mesh and getting PEC-tagged across both of its
   own end-caps.
2. A volume-floor fix (reject any reference entry too small to contain the
   candidate) was not enough: the coax's outer shield tube is *also*
   coaxial with the same axial center as the dielectric spacer, and its
   volume comfortably fit under the dielectric's own volume too, so the
   tube itself then started matching the dielectric instead of its own
   conductor reference. Even the closest-volume tiebreak that replaced it
   is still just a heuristic -- an asymmetrically fragmented layer could in
   principle have its reduced volume land closer to an unrelated entry's
   full volume than to its own.
3. The actual fix: when entries are tied on COM, ask the real geometric
   question instead of estimating from volume -- does the candidate solid
   really sit inside a tied reference's own original geometry? netgen.occ's
   own `*` (Boolean intersection) operator answers this cleanly: a
   candidate intersected with its true parent returns its own full volume;
   intersected with a merely-touching, different material (the exact
   adjacency concentric layers have with each other) returns exactly 0.0.
   Closest-volume is kept only as a fallback for when containment itself
   can't decide (a real OCC Boolean failure, or a genuinely inconclusive
   case).
"""

import FreeCAD
import Part

from palace.netgen_meshing import _match_reference


class _FakeCandidate:
    """Minimal duck-typed stand-in for a netgen.occ solid -- only .center
    and .mass are read when no COM tie is detected (the fast path, which
    never touches geometry at all)."""

    def __init__(self, center, mass):
        self.center = center
        self.mass = mass


def _occ_coaxial_layers():
    """The real board's actual coax cross-section as netgen.occ solids: a
    solid center pin, an annular dielectric spacer around it, and an
    annular shield tube around that -- all coaxial, all centered at the
    same axial point, exactly the ambiguity this module guards against."""
    import netgen.occ as occ

    pin = occ.Cylinder(occ.Pnt(-34, 0, 0), occ.X, r=0.265, h=28).solids[0]
    ptfe_outer = occ.Cylinder(occ.Pnt(-32.4, 0, 0), occ.X, r=0.825, h=24.8)
    ptfe_inner = occ.Cylinder(occ.Pnt(-32.4, 0, 0), occ.X, r=0.265, h=24.8)
    ptfe = (ptfe_outer - ptfe_inner).solids[0]
    tube_outer = occ.Cylinder(occ.Pnt(-32.4, 0, 0), occ.X, r=1.1, h=24.8)
    tube_inner = occ.Cylinder(occ.Pnt(-32.4, 0, 0), occ.X, r=0.825, h=24.8)
    tube = (tube_outer - tube_inner).solids[0]
    return pin, ptfe, tube


def _fc_pin_and_ptfe():
    """The same pin/spacer pair as FreeCAD Part.Shape objects -- what a
    background (dielectric/airbox) reference entry actually holds at its
    own construction site, before anything has gone through netgen.occ."""
    pin = Part.makeCylinder(0.265, 28, FreeCAD.Vector(-34, 0, 0), FreeCAD.Vector(1, 0, 0))
    ptfe_outer = Part.makeCylinder(0.825, 24.8, FreeCAD.Vector(-32.4, 0, 0), FreeCAD.Vector(1, 0, 0))
    ptfe_inner = Part.makeCylinder(0.265, 24.8, FreeCAD.Vector(-32.4, 0, 0), FreeCAD.Vector(1, 0, 0))
    ptfe = ptfe_outer.cut(ptfe_inner)
    return pin, ptfe


def test_match_reference_picks_the_dielectric_over_the_smaller_pin():
    pin, ptfe, _tube = _occ_coaxial_layers()
    reference = [
        ("cond:Copper", tuple(pin.center), "conductor", 100, pin.mass, pin),
        ("dielectric:Coax Diel", tuple(ptfe.center), "dielectric", 2, ptfe.mass, ptfe),
    ]
    label, com, kind, attr, volume, geom = _match_reference(ptfe, reference)
    assert label == "dielectric:Coax Diel"
    assert kind == "dielectric"


def test_match_reference_picks_the_pin_over_the_larger_dielectric():
    pin, ptfe, _tube = _occ_coaxial_layers()
    reference = [
        ("cond:Copper", tuple(pin.center), "conductor", 100, pin.mass, pin),
        ("dielectric:Coax Diel", tuple(ptfe.center), "dielectric", 2, ptfe.mass, ptfe),
    ]
    label, com, kind, attr, volume, geom = _match_reference(pin, reference)
    assert label == "cond:Copper"
    assert kind == "conductor"


def test_match_reference_three_way_coaxial_tie_picks_the_shield_tube():
    # The exact real-board regression: pin, dielectric spacer, and outer
    # shield tube are all coaxial and share a center of mass. A candidate
    # that IS the tube must resolve to the tube's own conductor entry, not
    # the dielectric's, even though the dielectric's volume is "big enough"
    # to superficially fit the tube's mass -- this is the scenario a
    # volume-only tiebreak got wrong; only real geometric containment
    # (zero overlap with the dielectric's actual material, full overlap
    # with the tube's own) resolves it correctly.
    pin, ptfe, tube = _occ_coaxial_layers()
    reference = [
        ("cond:Copper:pin", tuple(pin.center), "conductor", 100, pin.mass, pin),
        ("dielectric:Coax Diel", tuple(ptfe.center), "dielectric", 2, ptfe.mass, ptfe),
        ("cond:Copper:tube", tuple(tube.center), "conductor", 100, tube.mass, tube),
    ]
    label, com, kind, attr, volume, geom = _match_reference(tube, reference)
    assert label == "cond:Copper:tube"
    assert kind == "conductor"


def test_match_reference_converts_a_freecad_shape_reference_lazily():
    # Background (dielectric/airbox) reference entries only have a FreeCAD
    # Part.Shape on hand at their own construction site, not a netgen.occ
    # shape -- confirm the lazy BREP-roundtrip conversion (_as_occ_solid)
    # still resolves a real tie correctly, not just the already-netgen.occ
    # path the other tests exercise.
    pin_occ, ptfe_occ, _tube = _occ_coaxial_layers()
    fc_pin, fc_ptfe = _fc_pin_and_ptfe()
    reference = [
        ("cond:Copper", tuple(fc_pin.CenterOfGravity), "conductor", 100, fc_pin.Volume, fc_pin),
        ("dielectric:Coax Diel", tuple(fc_ptfe.CenterOfGravity), "dielectric", 2, fc_ptfe.Volume, fc_ptfe),
    ]
    # The candidate itself is always a real netgen.occ solid in production
    # (it comes from Glue()/a STEP re-import) -- only the reference side
    # is a bare FreeCAD shape here.
    label, com, kind, attr, volume, geom = _match_reference(ptfe_occ, reference)
    assert label == "dielectric:Coax Diel"


def test_match_reference_falls_back_to_volume_when_containment_is_inconclusive():
    # Two annuli sharing a COM at the origin (an annulus's own centroid
    # sits in its empty middle, not its material) but a candidate small
    # enough to sit entirely in that shared hole, touching neither one's
    # actual material -- containment can't decide (both overlaps are
    # genuinely 0.0), so this must fall back to closest-volume instead of
    # picking arbitrarily, and it must say so via log_fn.
    import netgen.occ as occ

    small_outer = occ.Cylinder(occ.Pnt(0, 0, 0), occ.X, r=3, h=10)
    small_inner = occ.Cylinder(occ.Pnt(0, 0, 0), occ.X, r=2, h=10)
    small_ring = (small_outer - small_inner).solids[0]
    big_outer = occ.Cylinder(occ.Pnt(0, 0, 0), occ.X, r=4, h=10)
    big_inner = occ.Cylinder(occ.Pnt(0, 0, 0), occ.X, r=2.5, h=10)
    big_ring = (big_outer - big_inner).solids[0]
    candidate = occ.Cylinder(occ.Pnt(0, 0, 0), occ.X, r=0.5, h=10).solids[0]

    reference = [
        ("ring:small", tuple(small_ring.center), "dielectric", 2, small_ring.mass, small_ring),
        ("ring:big", tuple(big_ring.center), "dielectric", 3, big_ring.mass, big_ring),
    ]
    warnings = []
    label, com, kind, attr, volume, geom = _match_reference(
        candidate, reference, log_fn=warnings.append
    )
    # closest-volume fallback: whichever ring's own volume is nearer the
    # tiny candidate's mass wins -- small_ring is smaller, so it's closer.
    assert label == "ring:small"
    assert any("containment" in w and "no real overlap" in w for w in warnings)


def test_match_reference_falls_back_to_volume_when_containment_raises():
    # A tied entry whose geom is neither a FreeCAD shape nor a netgen.occ
    # shape (a bookkeeping mistake, or a future refactor that breaks the
    # assumption) must not crash the whole match -- containment fails,
    # falls back to closest-volume, and logs why.
    candidate = _FakeCandidate((0.0, 0.0, 0.0), mass=5.0)
    reference = [
        ("cond:Copper", (0.0, 0.0, 0.0), "conductor", 100, 6.0, object()),
        ("dielectric:PTFE", (0.0, 0.0, 0.0), "dielectric", 2, 5.0, object()),
    ]
    warnings = []
    label, com, kind, attr, volume, geom = _match_reference(
        candidate, reference, log_fn=warnings.append
    )
    assert label == "dielectric:PTFE"  # volume 5.0 exactly matches mass 5.0
    assert any("containment" in w and "failed" in w for w in warnings)


def test_match_reference_does_not_tiebreak_across_genuinely_different_locations():
    # A small, distant reference entry must not win on volume-closeness
    # (or containment) when it isn't even close to tied on COM -- neither
    # mechanism should apply to entries that are truly at different
    # locations.
    candidate = _FakeCandidate((-9.175, -0.87, -0.611), mass=5.0)
    reference = [
        ("cond:Lead", (-9.175, -0.87, -0.611), "conductor", 101, 0.01, None),
        ("dielectric:PCB Diel", (-9.2, -0.9, -0.6), "dielectric", 3, 227.89, None),
    ]
    label, com, kind, attr, volume, geom = _match_reference(candidate, reference)
    assert label == "cond:Lead"


def test_match_reference_plain_nearest_com_when_no_tie():
    candidate = _FakeCandidate((0.5, 0.0, 0.0), mass=5.0)
    reference = [
        ("cond:Copper", (0.0, 0.0, 0.0), "conductor", 100, 1.0, None),
        ("dielectric:PTFE", (10.0, 0.0, 0.0), "dielectric", 2, 1.0, None),
    ]
    label, com, kind, attr, volume, geom = _match_reference(candidate, reference)
    assert label == "cond:Copper"

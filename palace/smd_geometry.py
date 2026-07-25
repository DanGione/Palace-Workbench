"""SMD chip component (resistor/capacitor/inductor) geometry construction.

These helpers build the solids/faces for a two-terminal SMD chip component in
"dead-bug" (upside-down) mounting orientation.  No FreeCAD document objects
are referenced here -- inputs are plain parameter dicts and outputs are raw
Part shapes, mirroring the style of ``palace/port_geometry.py``.

Coordinate convention
----------------------
X = component length axis, Y = width axis, Z = height.  Z=0 is the top
surface of the PCB/substrate the component is mounted on (i.e. the pad's
*bottom* face, when pads are generated) -- this is what FreeCAD's Attachment
editor attaches to, so the pad must never extend below it into the
substrate.  The component is built centered at X=0, Y=0; callers
translate/place the assembled shapes as needed.

Z-stack, bottom (PCB side) to top. The component always sits directly on the
pads, whether or not a solder joint is generated -- the solder joint is a
cosmetic fillet added *around the outside* of the contact, not a spacer.
``z_offset`` is ``pad_thickness`` when pads are generated (so the dielectric
stack/contacts sit on top of the pad, never overlapping it), or ``0`` when
``include_pads=False`` (no copper layer is being modeled, so the component
sits directly on the attached surface). The dielectric stack + contacts span
``[z_offset, z_offset + coating_thickness + body_height]``.

Impedance boundary position
----------------------------
The insulating "coating" (thin, represents the resistive/capacitive/
inductive film + its protective layer) and the bulk ceramic "body" can be
arranged three ways, chosen via ``position_mode`` (default ``"Flipped"``,
matching this module's original -- and only -- behavior):

- ``"Flipped"``: coating on the bottom (near the pad/ground plane), body on
  top. Short high-frequency current loop, low parasitic inductance -- the
  "flipped resistor trick".
- ``"Conventional"``: body on the bottom, coating on top (far from the
  ground plane). Longer current loop, higher parasitic inductance -- how a
  real two-terminal component sits "right side up".
- ``"Middle"``: the body splits into two equal halves with the boundary
  buried between them, deep in the bulk ceramic -- the coating stays a
  separate, untouched layer stacked on top, same role it plays in
  "Conventional". Models a capacitor's internal electrode plane, which sits
  inside the bulk dielectric rather than at its outer surface.

In every mode, the two end contacts are split into a "lower" segment and an
"upper" segment at whatever Z height the mode's boundary sits (see
``get_boundary_height()``), so a real edge exists there -- this is the edge
pair the ImpedanceBoundary feature's PortEdges mode attaches to, since the
boundary (the volumeless R/L/C element) sits exactly at that interface. The
overall dielectric stack height (``coating_thickness + body_height``) is the
same in every mode; only where the split falls within it changes.

Solder joint (optional): a meniscus fillet wrapping the *outside* face of
each contact, from the pad up to the top of the contact stack.  Built by
lofting a wedge that is as wide as the pad at Z=0 and narrows to exactly
the contact's own footprint at the top, then subtracting the contact solid
-- the remainder is a shell that is thick at the pad and tapers to nothing
at the top, only existing outside the contact (never in the ceramic gap).
"""

import FreeCAD
import Part

POSITION_MODES = ("Flipped", "Conventional", "Middle")


# Nominal EIA chip dimensions in mm.  Defaults to be refined after visual
# review -- see scripts/smd_prototype.py.
EIA_SIZE_TABLE = {
    "01005": dict(body_length=0.40, body_width=0.20, body_height=0.20,
                  contact_wrap=0.10, pad_length=0.25, pad_width=0.25, pad_gap=0.20),
    "0201":  dict(body_length=0.60, body_width=0.30, body_height=0.30,
                  contact_wrap=0.15, pad_length=0.30, pad_width=0.30, pad_gap=0.30),
    "0402":  dict(body_length=1.00, body_width=0.50, body_height=0.50,
                  contact_wrap=0.25, pad_length=0.60, pad_width=0.60, pad_gap=0.50),
    "0603":  dict(body_length=1.60, body_width=0.80, body_height=0.80,
                  contact_wrap=0.30, pad_length=0.90, pad_width=0.95, pad_gap=1.00),
    "0805":  dict(body_length=2.00, body_width=1.25, body_height=0.60,
                  contact_wrap=0.40, pad_length=1.00, pad_width=1.30, pad_gap=1.20),
}


def get_size_params(size_code, coating_thickness=None, pad_thickness=None):
    """Look up ``EIA_SIZE_TABLE[size_code]`` and fill in derived defaults.

    Returns a plain dict.  Raises KeyError (listing valid codes) if
    *size_code* is not recognized.  Raises ValueError if the contact wrap
    would consume the entire body length (no gap left for the element).
    """
    try:
        params = dict(EIA_SIZE_TABLE[size_code])
    except KeyError:
        raise KeyError(
            f"Unknown SMD size code {size_code!r}; valid codes: "
            f"{sorted(EIA_SIZE_TABLE)}"
        )

    gap_half = params["body_length"] / 2.0 - params["contact_wrap"]
    if gap_half <= 0:
        raise ValueError(
            f"Size {size_code!r}: contact_wrap*2 >= body_length, no gap left "
            f"for the element/coating."
        )
    if params["pad_gap"] / 2.0 < gap_half:
        raise ValueError(
            f"Size {size_code!r}: pad_gap ({params['pad_gap']}) is too small -- "
            f"the pad's inner edge would sit under the ceramic gap "
            f"(needs pad_gap >= {2 * gap_half})."
        )

    params["size_code"] = size_code
    params["coating_thickness"] = (
        coating_thickness if coating_thickness is not None
        else max(0.05, 0.1 * params["body_height"])
    )
    params["pad_thickness"] = pad_thickness if pad_thickness is not None else 0.035
    # Assumed by default (pads present); build_smd_component() sets this to 0
    # when include_pads=False, since then there's no copper layer to sit on.
    params["z_offset"] = params["pad_thickness"]
    return params


def _gap_half(p):
    return p["body_length"] / 2.0 - p["contact_wrap"]


def _gap_box(p, z_bottom, height):
    """A box spanning the gap footprint (X/Y) at the given Z range."""
    gap_half = _gap_half(p)
    return Part.makeBox(
        2 * gap_half, p["body_width"], height,
        FreeCAD.Vector(-gap_half, -p["body_width"] / 2.0, z_bottom),
    )


def build_ceramic_body(p):
    """The bulk ceramic/substrate solid, spanning only the gap between contacts."""
    z0 = p["z_offset"] + p["coating_thickness"]
    return _gap_box(p, z0, p["body_height"])


def build_coating(p):
    """The insulating (epoxy) coating slab, spanning only the gap between contacts."""
    return _gap_box(p, p["z_offset"], p["coating_thickness"])


def get_boundary_height(p, position_mode):
    """Z offset (above ``z_offset``) where the contact splits and the
    ImpedanceBoundary attaches, for *position_mode* -- see module docstring.
    """
    if position_mode == "Flipped":
        return p["coating_thickness"]
    elif position_mode == "Conventional":
        return p["body_height"]
    elif position_mode == "Middle":
        return p["body_height"] / 2.0
    raise ValueError(f"Unknown position_mode {position_mode!r}; valid: {POSITION_MODES}")


def build_dielectric_layers(p, position_mode="Flipped"):
    """Build the dielectric solids for *position_mode*, bottom-to-top.

    Returns a list of ``(name, material_key, solid)`` triples --
    *material_key* is ``"body"`` or ``"coating"`` (the existing
    CERAMIC_MATERIAL/COATING_MATERIAL groups in features/smd_component.py).
    Two layers for "Flipped"/"Conventional" (the coating and body simply
    swap vertical order); three for "Middle" (the body splits into two equal
    halves with the boundary buried between them -- see module docstring).
    """
    z_offset = p["z_offset"]
    coating_t = p["coating_thickness"]
    body_h = p["body_height"]

    if position_mode == "Flipped":
        return [
            ("coating", "coating", build_coating(p)),
            ("body", "body", build_ceramic_body(p)),
        ]
    elif position_mode == "Conventional":
        return [
            ("body", "body", _gap_box(p, z_offset, body_h)),
            ("coating", "coating", _gap_box(p, z_offset + body_h, coating_t)),
        ]
    elif position_mode == "Middle":
        half = body_h / 2.0
        return [
            ("body_lower", "body", _gap_box(p, z_offset, half)),
            ("body_upper", "body", _gap_box(p, z_offset + half, half)),
            ("coating", "coating", _gap_box(p, z_offset + body_h, coating_t)),
        ]
    raise ValueError(f"Unknown position_mode {position_mode!r}; valid: {POSITION_MODES}")


def build_contact_pair(p, boundary_height=None):
    """Build the two end-contact metallizations.

    Each contact is split into a "lower" segment and an "upper" segment at
    *boundary_height* (Z offset above ``z_offset``; defaults to
    ``p["coating_thickness"]`` -- the "Flipped" mode's interface, for
    backward-compatible standalone use) so a real edge exists there -- see
    module docstring. In "Middle" mode this means the "upper" segment spans
    both the body_upper and coating layers without an internal split of its
    own; that's fine geometrically (Palace's meshing fragments all
    dielectric volumes together before matching surfaces, see
    palace/meshing.py), it's just new territory -- every other mode's
    segments each span exactly one dielectric material.

    Returns a dict with keys ``left_lower``, ``left_upper``, ``right_lower``,
    ``right_upper`` (Part.Solid) and ``gap_left``, ``gap_right``
    (FreeCAD.Vector -- midpoints of the facing inner edges at the boundary
    interface, for use with find_edge_by_midpoint()).
    """
    W, H = p["body_width"], p["body_height"]
    wrap = p["contact_wrap"]
    coating_t = p["coating_thickness"]
    z_offset = p["z_offset"]
    total_height = coating_t + H
    if boundary_height is None:
        boundary_height = coating_t
    iface_z = z_offset + boundary_height
    gap_half = _gap_half(p)

    def _one_side(x0):
        lower = Part.makeBox(wrap, W, boundary_height, FreeCAD.Vector(x0, -W / 2.0, z_offset))
        upper = Part.makeBox(wrap, W, total_height - boundary_height, FreeCAD.Vector(x0, -W / 2.0, iface_z))
        return lower, upper

    left_lower, left_upper = _one_side(-p["body_length"] / 2.0)
    right_lower, right_upper = _one_side(gap_half)

    return {
        "left_lower": left_lower, "left_upper": left_upper,
        "right_lower": right_lower, "right_upper": right_upper,
        "gap_left": FreeCAD.Vector(-gap_half, 0.0, iface_z),
        "gap_right": FreeCAD.Vector(gap_half, 0.0, iface_z),
    }


def build_pad_pair(p):
    """Two rectangular PCB pads, centered under each contact, at Z in [0, pad_thickness]."""
    pad_l, pad_w, pad_gap = p["pad_length"], p["pad_width"], p["pad_gap"]
    t = p["pad_thickness"]
    x_inner = pad_gap / 2.0
    left = Part.makeBox(pad_l, pad_w, t, FreeCAD.Vector(-x_inner - pad_l, -pad_w / 2.0, 0.0))
    right = Part.makeBox(pad_l, pad_w, t, FreeCAD.Vector(x_inner, -pad_w / 2.0, 0.0))
    return left, right


def _rect_wire(xmin, xmax, ymin, ymax, z):
    pts = [
        FreeCAD.Vector(xmin, ymin, z), FreeCAD.Vector(xmax, ymin, z),
        FreeCAD.Vector(xmax, ymax, z), FreeCAD.Vector(xmin, ymax, z),
        FreeCAD.Vector(xmin, ymin, z),
    ]
    return Part.makePolygon(pts)


def _lerp(a, b, t):
    return a + (b - a) * t


def _meniscus_solid(pad_rect, contact_rect, z_bottom, z_top, taper_fraction, taper_progress):
    """One solder-fillet meniscus: a wedge loft with the contact subtracted out.

    ``pad_rect``/``contact_rect`` are ``(xmin, xmax, ymin, ymax)`` tuples.
    The wedge's base (``z_bottom``, the pad's own top face) is exactly the
    pad's own footprint -- not just pad-width, the full rectangle -- so the
    meniscus physically rests on the whole exposed pad, then narrows to
    exactly the contact's own footprint at ``z_top``.  A middle profile
    (``taper_progress`` of the way from the pad's rectangle to the
    contact's) biases the taper concave -- fast narrowing near the pad, a
    long thin sliver above -- approximating a real solder fillet's profile.
    Subtracting the contact solid leaves only the exterior shell: thick at
    the pad, feathering to nothing at ``z_top`` where the wedge and contact
    coincide exactly.
    """
    z_mid = z_bottom + (z_top - z_bottom) * taper_fraction

    def _lerp_rect(t):
        return tuple(
            _lerp(pad_v, contact_v, t) for pad_v, contact_v in zip(pad_rect, contact_rect)
        )

    w0 = _rect_wire(*pad_rect, z_bottom)
    w1 = _rect_wire(*_lerp_rect(taper_progress), z_mid)
    w2 = _rect_wire(*contact_rect, z_top)

    try:
        # ruled=True (straight facets between profiles) rather than a smooth
        # spline surface: with a profile edge held exactly fixed while its
        # neighbors taper (common when the pad and contact share an inner
        # edge), a smooth loft can overshoot slightly past that fixed edge,
        # which would let the meniscus creep into the ceramic gap.  Ruled
        # interpolation is a literal straight line between corresponding
        # profile vertices, so it can never overshoot a fixed boundary.
        wedge = Part.makeLoft([w0, w1, w2], True, True)
        if not wedge.isValid():
            raise RuntimeError("resulting loft wedge failed isValid()")
        return wedge
    except Exception as exc:
        raise RuntimeError(f"Could not build solder meniscus loft: {exc}") from exc


def build_solder_joint_pair(p, contacts, taper_fraction=0.25, taper_progress=0.85):
    """Two solder-fillet menisci wrapping the outside of each contact.

    *contacts* is the dict returned by build_contact_pair(p) -- passed in
    (rather than rebuilt) so the caller's already-built contact solids are
    reused for the boolean subtraction.
    """
    W = p["body_width"]
    L = p["body_length"]
    pad_l, pad_w, pad_gap = p["pad_length"], p["pad_width"], p["pad_gap"]
    x_inner = pad_gap / 2.0
    gap_half = _gap_half(p)
    z_bottom = p["z_offset"]   # the pad's own top face
    z_top = p["z_offset"] + p["coating_thickness"] + p["body_height"]

    contact_left = contacts["left_lower"].fuse(contacts["left_upper"])
    contact_right = contacts["right_lower"].fuse(contacts["right_upper"])

    pad_rect_left = (-(x_inner + pad_l), -x_inner, -pad_w / 2.0, pad_w / 2.0)
    contact_rect_left = (-L / 2.0, -gap_half, -W / 2.0, W / 2.0)
    pad_rect_right = (x_inner, x_inner + pad_l, -pad_w / 2.0, pad_w / 2.0)
    contact_rect_right = (gap_half, L / 2.0, -W / 2.0, W / 2.0)

    wedge_left = _meniscus_solid(pad_rect_left, contact_rect_left, z_bottom, z_top,
                                  taper_fraction, taper_progress)
    wedge_right = _meniscus_solid(pad_rect_right, contact_rect_right, z_bottom, z_top,
                                   taper_fraction, taper_progress)

    left = wedge_left.cut(contact_left)
    right = wedge_right.cut(contact_right)
    return left, right


def find_edge_by_midpoint(shape, target_point, tol=1e-6):
    """Return (sub_name, Part.Edge) for the edge whose midpoint matches *target_point*.

    ``sub_name`` is a 1-based ``"EdgeN"`` string suitable for use in an
    ``App::PropertyLinkSubList`` (e.g. ImpedanceBoundary.PortEdges).  Raises
    RuntimeError if no edge matches within *tol*.
    """
    for i, edge in enumerate(shape.Edges):
        if (edge.CenterOfMass - target_point).Length < tol:
            return f"Edge{i + 1}", edge
    raise RuntimeError(f"No edge found near {target_point} (tol={tol})")


def build_smd_component(size_code, include_pads=True, include_solder_joint=True,
                         position_mode="Flipped", **overrides):
    """Assemble a full SMD component.

    Returns a dict:
        dielectric_layers   : list of (name, material_key, Part.Solid) from
                              build_dielectric_layers() -- 2 entries for
                              "Flipped"/"Conventional", 3 for "Middle"
        contacts            : dict from build_contact_pair()
        pads                : (Part.Solid, Part.Solid) or None
        solder              : (Part.Solid, Part.Solid) or None
        params              : the resolved parameter dict
        position_mode       : the resolved position_mode

    The component always sits directly on the pads regardless of
    ``include_solder_joint`` -- the solder joint is a cosmetic fillet, not a
    spacer.  A solder joint implies pads exist; if ``include_pads`` is
    False, ``include_solder_joint`` is ignored and no solder joint is built.

    When ``include_pads`` is False, ``z_offset`` collapses to 0 -- there's no
    copper layer being modeled, so the component sits directly on Z=0 (the
    attached surface) instead of on top of a pad.
    """
    if not include_pads:
        include_solder_joint = False

    p = get_size_params(size_code, **overrides)
    if not include_pads:
        p["z_offset"] = 0.0

    boundary_height = get_boundary_height(p, position_mode)
    dielectric_layers = build_dielectric_layers(p, position_mode)
    contacts = build_contact_pair(p, boundary_height=boundary_height)
    pads = build_pad_pair(p) if include_pads else None
    solder = build_solder_joint_pair(p, contacts) if include_solder_joint else None

    return {
        "dielectric_layers": dielectric_layers,
        "contacts": contacts,
        "pads": pads,
        "solder": solder,
        "params": p,
        "position_mode": position_mode,
    }

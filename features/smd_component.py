"""SMD chip component (resistor/capacitor/inductor) assembly.

An SMDComponent is a "SMD"-kind Component (see features/component.py for the
shared container/placement/shadow-solid machinery) whose master solids
(dielectric layers, 4 contact segments, optional pads, optional solder
joints) are generated parametrically from an EIA size code rather than
captured from a selection. It uses Part::AttachExtension for its Placement,
exactly like Part/PartDesign primitives, so double-clicking an existing
SMDComponent reopens the same Attachment-editor-equipped panel used at
creation time (see commands/cmd_smd_component.py / panels/smd_component_panel.py).

Every master's visible shadow copy is classified into the shared
DielectricGroup/ConductorGroup objects eagerly, at creation time, exactly
like any other Component -- meshing and config generation need no
SMD-specific code at all. On top of the generic Component properties, an
SMDComponent additionally carries an ImpedanceBoundary (the two-terminal
R/L/C circuit element, attached to an edge pair whose height depends on
ImpedanceBoundaryPosition -- see palace/smd_geometry.py) and the EIA
size/value fields below.
"""

import FreeCAD

from palace.smd_geometry import build_smd_component, find_edge_by_midpoint, POSITION_MODES
from features.component import (
    init_component_properties, propagate_placement, restore_component_extensions,
    create_component_container, add_solid_pair, delete_component,
)
from features.material_group import get_or_create_dielectric_group, get_or_create_conductor_group
from features.impedance_boundary import create_impedance_boundary
from features import next_port_index

CERAMIC_MATERIAL = "SmdCeramic"
COATING_MATERIAL = "SmdCoating"
CONTACT_MATERIAL = "SmdContacts"
PAD_MATERIAL = "SmdPads"
SOLDER_MATERIAL = "SmdSolder"

CERAMIC_PERMITTIVITY = 9.0    # generic alumina-class default; refine per-kind later
COATING_PERMITTIVITY = 4.0    # generic epoxy default

CERAMIC_COLOR = (0.82, 0.70, 0.55)
COATING_COLOR = (0.12, 0.12, 0.12)
CONTACT_COLOR = (0.75, 0.75, 0.78)    # plated termination (e.g. Ni/Sn) — light gray
PAD_COLOR = (0.722, 0.451, 0.200)     # copper
SOLDER_COLOR = (0.60, 0.60, 0.65)     # leaded/tin solder — darker gray than contacts


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


class SMDComponent:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        init_component_properties(obj)
        _add(obj, "App::PropertyString", "ComponentType", "SMD",
             "Resistor | Capacitor | Inductor", "")
        _add(obj, "App::PropertyString", "SizeCode", "SMD",
             "EIA size code (e.g. 0402)", "")
        _add(obj, "App::PropertyFloat", "ValueSI", "SMD",
             "R (ohm) / C (farad) / L (henry) depending on ComponentType", 0.0)
        _add(obj, "App::PropertyBool", "IncludePads", "SMD",
             "Whether pad solids were generated", True)
        _add(obj, "App::PropertyBool", "IncludeSolderJoint", "SMD",
             "Whether solder-joint solids were generated", True)
        _add(obj, "App::PropertyFloat", "PadThickness", "SMD",
             "Resolved pad copper thickness (mm)", 0.035)
        _add(obj, "App::PropertyLink", "PadConductorGroupOverride", "SMD",
             "Advanced override: pads were added to this existing "
             "ConductorGroup instead of the default shared one")
        _add(obj, "App::PropertyLink", "ImpedanceBoundaryObj", "SMD",
             "The ImpedanceBoundary generated for this component")
        _add(obj, "App::PropertyEnumeration", "ImpedanceBoundaryPosition", "SMD",
             "Where the R/L/C element sits: Flipped (default) | Conventional | Middle")
        if hasattr(obj, "ImpedanceBoundaryPosition"):
            obj.ImpedanceBoundaryPosition = list(POSITION_MODES)

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        if prop == "Placement":
            propagate_placement(obj)

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        restore_component_extensions(obj)

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_smd_component(doc, component_type, size_code, value_si,
                          include_pads=True, include_solder_joint=True,
                          placement=None, pad_conductor_group=None, port_index=None,
                          pad_thickness=None, position_mode="Flipped"):
    """Build one full SMD component assembly and wire it into the simulation.

    component_type : "Resistor" | "Capacitor" | "Inductor"
    value_si       : R (ohm) / C (farad) / L (henry) depending on component_type
    pad_conductor_group : an existing ConductorGroup object to use for the pads
                          instead of the default shared group (Advanced
                          override — pads only). Applied immediately.
    pad_thickness  : override for the pad copper thickness (mm); None uses
                     palace.smd_geometry's default.
    position_mode  : "Flipped" (default) | "Conventional" | "Middle" -- see
                     palace/smd_geometry.py's module docstring.

    Builds master/shadow solid pairs via features.component.add_solid_pair --
    container.Placement is set to *placement* before any pair is added
    (required by add_solid_pair's calling convention: it computes each
    child's ChildBasePlacements entry relative to whatever the container's
    Placement already is), so every pair's base placement reduces to Identity
    and all solids move together as one rigid unit exactly like before.

    Returns the created SMDComponent object.
    """
    placement = placement if placement is not None else FreeCAD.Placement()
    parts = build_smd_component(size_code, include_pads, include_solder_joint,
                                 position_mode=position_mode, pad_thickness=pad_thickness)
    resolved_pad_thickness = parts["params"]["pad_thickness"]

    container = create_component_container(doc, kind="SMD", base_name="SMDComponent",
                                            proxy_cls=SMDComponent)
    container.Placement = placement   # set BEFORE adding children -- see docstring
    container.ComponentType = component_type
    container.SizeCode = size_code
    container.ValueSI = value_si
    container.IncludePads = bool(parts["pads"])
    container.IncludeSolderJoint = bool(parts["solder"])
    container.PadThickness = resolved_pad_thickness
    container.ImpedanceBoundaryPosition = position_mode

    c = parts["contacts"]

    # Aggregate by material_key before calling the group factories -- in
    # "Middle" mode, body_lower and body_upper both have material_key
    # "body" and must land in the SAME shared ceramic group, not two.
    dielectric_shadows = {}
    for name, material_key, solid in parts["dielectric_layers"]:
        base_name = "SMD" + name.title().replace("_", "")
        _master, shadow = add_solid_pair(doc, container, base_name, solid, placement)
        dielectric_shadows.setdefault(material_key, []).append(shadow)

    _master, shadow_contact_ll = add_solid_pair(doc, container, "SMDContactLeftLower", c["left_lower"], placement)
    _master, shadow_contact_lu = add_solid_pair(doc, container, "SMDContactLeftUpper", c["left_upper"], placement)
    _master, shadow_contact_rl = add_solid_pair(doc, container, "SMDContactRightLower", c["right_lower"], placement)
    _master, shadow_contact_ru = add_solid_pair(doc, container, "SMDContactRightUpper", c["right_upper"], placement)
    shadow_contacts = [shadow_contact_ll, shadow_contact_lu, shadow_contact_rl, shadow_contact_ru]

    shadow_pads = []
    if parts["pads"]:
        p1, p2 = parts["pads"]
        _master, shadow_p1 = add_solid_pair(doc, container, "SMDPadLeft", p1, placement)
        _master, shadow_p2 = add_solid_pair(doc, container, "SMDPadRight", p2, placement)
        shadow_pads = [shadow_p1, shadow_p2]

    shadow_solder = []
    if parts["solder"]:
        s1, s2 = parts["solder"]
        _master, shadow_s1 = add_solid_pair(doc, container, "SMDSolderLeft", s1, placement)
        _master, shadow_s2 = add_solid_pair(doc, container, "SMDSolderRight", s2, placement)
        shadow_solder = [shadow_s1, shadow_s2]

    get_or_create_dielectric_group(doc, CERAMIC_MATERIAL, CERAMIC_PERMITTIVITY, CERAMIC_COLOR,
                                    solids=dielectric_shadows.get("body", []))
    get_or_create_dielectric_group(doc, COATING_MATERIAL, COATING_PERMITTIVITY, COATING_COLOR,
                                    solids=dielectric_shadows.get("coating", []))
    get_or_create_conductor_group(doc, CONTACT_MATERIAL, CONTACT_COLOR, solids=shadow_contacts)
    if shadow_pads:
        if pad_conductor_group is not None:
            for o in shadow_pads:
                pad_conductor_group.addObject(o)
            doc.recompute()
            container.PadConductorGroupOverride = pad_conductor_group
        else:
            get_or_create_conductor_group(doc, PAD_MATERIAL, PAD_COLOR, solids=shadow_pads)
    if shadow_solder:
        get_or_create_conductor_group(doc, SOLDER_MATERIAL, SOLDER_COLOR, solids=shadow_solder)

    # --- Impedance boundary at the position_mode-dependent interface edge pair ---
    # Locate the edge against the RAW pre-placement solids (parts["contacts"]),
    # not the placed document objects: gap_left/gap_right are local-frame
    # coordinates (already resolved to the correct height for position_mode
    # by build_contact_pair/get_boundary_height), so comparing them against a
    # placement-transformed shape would fail to find the edge whenever
    # placement != identity. Edge *indexing* is placement-independent
    # (Placement never touches topology) and unaffected by .copy(), so the
    # resulting "EdgeN" string is valid for the shadow contact object too.
    name_left, _ = find_edge_by_midpoint(c["left_lower"], c["gap_left"])
    name_right, _ = find_edge_by_midpoint(c["right_lower"], c["gap_right"])

    idx = port_index if port_index is not None else next_port_index(doc)
    boundary = create_impedance_boundary(
        doc, index=idx,
        edge_refs=[(shadow_contacts[0], name_left), (shadow_contacts[2], name_right)],
    )
    # Only the relevant field should be nonzero -- ImpedanceBoundary.R defaults
    # to 50.0 (a sensible default for a port, not for an unused R/L/C field),
    # so explicitly zero the other two rather than relying on that default.
    boundary.R = value_si if component_type == "Resistor" else 0.0
    boundary.L = value_si if component_type == "Inductor" else 0.0
    boundary.C = value_si if component_type == "Capacitor" else 0.0
    doc.recompute()   # re-derive Rs/Ls/Cs from the R/L/C value just set

    container.ImpedanceBoundaryObj = boundary

    return container


def delete_smd_component(doc, container):
    """Remove an SMDComponent and everything it owns.

    Thin wrapper around features.component.delete_component -- kept as a
    distinct name since callers (panels/smd_component_panel.py's re-edit
    flow) already refer to it, and "delete the SMD component" reads more
    clearly at call sites than the generic name.
    """
    delete_component(doc, container)

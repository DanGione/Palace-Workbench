"""
Auto-managed tree groups for Ports (WavePort + LumpedPort), Impedance
Boundaries, and Components.

Unlike DielectricGroup/ConductorGroup (features/material_group.py), these
groups are pure invisible plumbing: there is no command to create or delete
them, no task panel, no "add selection" step. A group is created the moment
it's needed (via get_or_create_*) and deleted again once its last member is
removed (via cleanup_group_if_orphaned, called from each member's own
ViewProvider.onDelete). Each is a document singleton -- at most one
PortGroup/ImpedanceBoundaryGroup/ComponentGroup per document.

Structural pattern mirrors DielectricGroup/ConductorGroup exactly:
App::FeaturePython + App::GroupExtensionPython so the Python proxy pattern
is supported while still getting native FreeCAD group behaviour (Group
property, addObject(), tree expand/collapse). Identification is duck-typed
via a single marker property per kind (features/__init__.py's finders):
  PortGroup               -- hasattr(obj, "IsPortGroup")
  ImpedanceBoundaryGroup  -- hasattr(obj, "IsImpedanceBoundaryGroup")
  ComponentGroup          -- hasattr(obj, "IsComponentGroup")

Per this codebase's established "no proxy-class inheritance" convention
(see features/component.py's module docstring), these are three separate
small classes sharing only plain helper functions.
"""

import FreeCAD
import os

from features import (
    add_to_simulation, find_simulation,
    find_port_group, find_impedance_boundary_group, find_component_group,
    find_wave_ports, find_lumped_ports, find_impedance_boundaries, find_components,
)


def _add_member(doc, grp, obj):
    """Add obj to grp, first removing it from the Simulation container's own
    Group if it's a stale direct member there.

    FreeCAD's Group property does NOT enforce single-parent membership on
    its own -- an object listed as a member of two different Group-holding
    containers renders twice in the tree, once under each. Verified on a
    real board: four SMD-component-derived ImpedanceBoundary objects were
    stale direct members of PalaceSimulation.Group (left over from a
    document saved before ImpedanceBoundary had its own group, or from
    before _is_palace_child was trimmed to stop matching these types) --
    correctly joining the new PalaceImpedanceBoundaries group did not clear
    that old membership, so each one appeared once at the top level under
    Simulation AND once nested inside Impedance Boundaries.
    """
    sim = find_simulation(doc)
    if sim is not None and sim is not grp and obj in sim.Group:
        sim.removeObject(obj)
    if obj not in grp.Group:
        grp.addObject(obj)

_PORT_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "PortGroup.svg")
_IMP_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "ImpedanceBoundaryGroup.svg")
_COMP_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "ComponentGroup.svg")


# ---------------------------------------------------------------------------
# Port group
# ---------------------------------------------------------------------------

class PortGroup:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        if not hasattr(obj, "IsPortGroup"):
            obj.addProperty("App::PropertyBool", "IsPortGroup", "Palace",
                            "Marker: this is the document's auto-managed Ports group")
            obj.IsPortGroup = True

    def execute(self, obj):
        pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        if FreeCAD.GuiUp and obj.ViewObject is not None:
            try:
                obj.ViewObject.removeExtension("Gui::ViewProviderGroupExtensionPython", None)
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderPortGroup:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object
        from pivy import coin
        self.node = coin.SoSeparator()
        vobj.addDisplayMode(self.node, "Group")

    def claimChildren(self):
        return getattr(self.Object, "Group", [])

    def getDisplayModes(self, vobj):
        return ["Group"]

    def getDefaultDisplayMode(self):
        return "Group"

    def onChanged(self, vobj, prop):
        if prop == "Visibility":
            for child in getattr(self.Object, "Group", []):
                if child.ViewObject is not None:
                    child.ViewObject.Visibility = vobj.Visibility

    def getIcon(self):
        return _PORT_ICON

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ---------------------------------------------------------------------------
# Impedance boundary group
# ---------------------------------------------------------------------------

class ImpedanceBoundaryGroup:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        if not hasattr(obj, "IsImpedanceBoundaryGroup"):
            obj.addProperty("App::PropertyBool", "IsImpedanceBoundaryGroup", "Palace",
                            "Marker: this is the document's auto-managed Impedance Boundaries group")
            obj.IsImpedanceBoundaryGroup = True

    def execute(self, obj):
        pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        if FreeCAD.GuiUp and obj.ViewObject is not None:
            try:
                obj.ViewObject.removeExtension("Gui::ViewProviderGroupExtensionPython", None)
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderImpedanceBoundaryGroup:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object
        from pivy import coin
        self.node = coin.SoSeparator()
        vobj.addDisplayMode(self.node, "Group")

    def claimChildren(self):
        return getattr(self.Object, "Group", [])

    def getDisplayModes(self, vobj):
        return ["Group"]

    def getDefaultDisplayMode(self):
        return "Group"

    def onChanged(self, vobj, prop):
        if prop == "Visibility":
            for child in getattr(self.Object, "Group", []):
                if child.ViewObject is not None:
                    child.ViewObject.Visibility = vobj.Visibility

    def getIcon(self):
        return _IMP_ICON

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ---------------------------------------------------------------------------
# Component group
# ---------------------------------------------------------------------------

class ComponentGroup:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        if not hasattr(obj, "IsComponentGroup"):
            obj.addProperty("App::PropertyBool", "IsComponentGroup", "Palace",
                            "Marker: this is the document's auto-managed Components group")
            obj.IsComponentGroup = True

    def execute(self, obj):
        pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        if FreeCAD.GuiUp and obj.ViewObject is not None:
            try:
                obj.ViewObject.removeExtension("Gui::ViewProviderGroupExtensionPython", None)
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderComponentGroup:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object
        from pivy import coin
        self.node = coin.SoSeparator()
        vobj.addDisplayMode(self.node, "Group")

    def claimChildren(self):
        return getattr(self.Object, "Group", [])

    def getDisplayModes(self, vobj):
        return ["Group"]

    def getDefaultDisplayMode(self):
        return "Group"

    def onChanged(self, vobj, prop):
        if prop == "Visibility":
            for child in getattr(self.Object, "Group", []):
                if child.ViewObject is not None:
                    child.ViewObject.Visibility = vobj.Visibility

    def getIcon(self):
        return _COMP_ICON

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ---------------------------------------------------------------------------
# Find-or-create factories
# ---------------------------------------------------------------------------

def get_or_create_port_group(doc):
    """Return the document's PortGroup, creating it (and sweeping in every
    already-existing WavePort/LumpedPort) if one doesn't exist yet."""
    grp = find_port_group(doc)
    if grp is not None:
        return grp
    grp = doc.addObject("App::FeaturePython", "PalacePorts")
    grp.Label = "Ports"
    grp.addExtension("App::GroupExtensionPython")
    PortGroup(grp)
    if grp.ViewObject is not None:
        ViewProviderPortGroup(grp.ViewObject)
    for p in find_wave_ports(doc) + find_lumped_ports(doc):
        _add_member(doc, grp, p)
    add_to_simulation(doc, grp)
    doc.recompute()
    return grp


def add_to_port_group(doc, obj):
    """Add obj (a WavePort/LumpedPort) to the document's PortGroup,
    creating the group first if needed. The call site for new-object
    creation -- always use this rather than get_or_create_port_group(doc)
    .addObject(obj) directly, so stale Simulation.Group membership is
    cleaned up too (see _add_member)."""
    _add_member(doc, get_or_create_port_group(doc), obj)


def get_or_create_impedance_boundary_group(doc):
    """Return the document's ImpedanceBoundaryGroup, creating it (and
    sweeping in every already-existing ImpedanceBoundary) if needed."""
    grp = find_impedance_boundary_group(doc)
    if grp is not None:
        return grp
    grp = doc.addObject("App::FeaturePython", "PalaceImpedanceBoundaries")
    grp.Label = "Impedance Boundaries"
    grp.addExtension("App::GroupExtensionPython")
    ImpedanceBoundaryGroup(grp)
    if grp.ViewObject is not None:
        ViewProviderImpedanceBoundaryGroup(grp.ViewObject)
    for b in find_impedance_boundaries(doc):
        _add_member(doc, grp, b)
    add_to_simulation(doc, grp)
    doc.recompute()
    return grp


def add_to_impedance_boundary_group(doc, obj):
    """Add obj (an ImpedanceBoundary) to the document's ImpedanceBoundaryGroup,
    creating the group first if needed -- see add_to_port_group's docstring
    for why this, not get_or_create_impedance_boundary_group(doc).addObject(obj)
    directly, is the right call site."""
    _add_member(doc, get_or_create_impedance_boundary_group(doc), obj)


def get_or_create_component_group(doc):
    """Return the document's ComponentGroup, creating it (and sweeping in
    every already-existing Component) if needed."""
    grp = find_component_group(doc)
    if grp is not None:
        return grp
    grp = doc.addObject("App::FeaturePython", "PalaceComponents")
    grp.Label = "Components"
    grp.addExtension("App::GroupExtensionPython")
    ComponentGroup(grp)
    if grp.ViewObject is not None:
        ViewProviderComponentGroup(grp.ViewObject)
    for c in find_components(doc):
        _add_member(doc, grp, c)
    add_to_simulation(doc, grp)
    doc.recompute()
    return grp


def add_to_component_group(doc, obj):
    """Add obj (a Component) to the document's ComponentGroup, creating the
    group first if needed -- see add_to_port_group's docstring for why
    this, not get_or_create_component_group(doc).addObject(obj) directly,
    is the right call site."""
    _add_member(doc, get_or_create_component_group(doc), obj)


# ---------------------------------------------------------------------------
# Cleanup: delete a group once its last member is removed
# ---------------------------------------------------------------------------

def cleanup_group_if_orphaned(doc, group_finder, obj):
    """Delete group_finder(doc)'s group if obj is its last member.

    Called from a member's own ViewProvider.onDelete(), before FreeCAD
    actually removes the member -- so "last member" is computed by excluding
    obj from the group's current (pre-removal) membership.
    """
    grp = group_finder(doc)
    if grp is None or obj not in grp.Group:
        return
    if not [o for o in grp.Group if o is not obj]:
        doc.removeObject(grp.Name)


# ---------------------------------------------------------------------------
# Document-load sync: tidy up an already-scattered document on open
# ---------------------------------------------------------------------------

def sync_all_groups(doc):
    """Ensure a group exists for each category that already has members, and
    that none of those members is ALSO left as a stale direct member of
    PalaceSimulation.Group -- called on every document restore, not only
    when a group doesn't exist yet.

    The unconditional re-sweep (not just "create if missing") matters: a
    document can already have both the group AND stale direct
    Simulation.Group membership baked in (from before this feature existed,
    or from before ImpedanceBoundary/etc. was trimmed out of
    _is_palace_child) -- get_or_create_*_group's own sweep only runs on
    first creation, so re-opening such a document without this unconditional
    pass would keep leaving those objects doubled in the tree forever, never
    self-healing. Never creates a group speculatively -- each get_or_create_*
    call below is itself gated on there being at least one matching object.
    """
    if find_wave_ports(doc) or find_lumped_ports(doc):
        grp = get_or_create_port_group(doc)
        for p in find_wave_ports(doc) + find_lumped_ports(doc):
            _add_member(doc, grp, p)
    if find_impedance_boundaries(doc):
        grp = get_or_create_impedance_boundary_group(doc)
        for b in find_impedance_boundaries(doc):
            _add_member(doc, grp, b)
    if find_components(doc):
        grp = get_or_create_component_group(doc)
        for c in find_components(doc):
            _add_member(doc, grp, c)

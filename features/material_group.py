"""
Dielectric and Conductor material group feature objects.

Both use App::FeaturePython + App::GroupExtensionPython so that the Python
proxy pattern (obj.Proxy = self) is supported while still providing native
FreeCAD group behaviour (Group property, addObject(), tree expand/collapse).

Identification pattern (from features/__init__.py finders):
  DielectricGroup  — hasattr(obj, "Permittivity") and hasattr(obj, "MeshAttribute")
  ConductorGroup   — hasattr(obj, "ConductorType") and hasattr(obj, "MeshAttribute")

Color propagation:
  App::FeaturePython ViewObjects (our groups) have type
  Gui::ViewProviderDocumentObject and do NOT expose ShapeColor, so the
  FreeCAD Appearance menu cannot be used directly on the group.
  Instead, edit the GroupColor / GroupTransparency data properties in the
  Data tab.  The App proxy's onChanged fires reliably for user-defined
  properties and pushes the colour to every child solid, walking into
  PartDesign::Body.Group so the Tip feature (the visually active shape)
  always gets updated.
"""

import FreeCAD
import os
from features import add_to_simulation

_DIEL_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "DielectricGroup.svg")
_COND_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "ConductorGroup.svg")

# Default colours
_DIEL_COLOR = (0.204, 0.788, 0.204)   # medium green
_DIEL_TRANSP = 30
_COND_COLOR = (0.722, 0.451, 0.200)   # copper
_COND_TRANSP = 0


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


# ---------------------------------------------------------------------------
# Shared appearance helpers
# ---------------------------------------------------------------------------

def _apply_appearance_to(child_obj, color, transparency):
    """
    Set ShapeColor and Transparency on child_obj's ViewObject, then recurse
    into PartDesign::Body features so the visually active Tip always gets the
    colour regardless of whether FreeCAD's own Body ViewProvider propagates.

    color        : (r, g, b) floats 0-1
    transparency : int 0-100
    """
    def _cv(target):
        cv = getattr(target, "ViewObject", None)
        if cv is None:
            return
        try:
            cv.ShapeColor = color
        except Exception:
            pass
        try:
            cv.Transparency = transparency
        except Exception:
            pass

    _cv(child_obj)

    # Walk into PartDesign::Body — the visible shape is owned by the Tip
    # feature, not the Body itself.
    if "Body" in getattr(child_obj, "TypeId", ""):
        for feat in getattr(child_obj, "Group", []):
            _cv(feat)



def _propagate_from_data(obj):
    """Propagate colour from the group's data properties to all children."""
    if not FreeCAD.GuiUp:
        return
    try:
        color = tuple(obj.GroupColor)[:3]   # (r, g, b) floats 0-1
    except Exception:
        return
    try:
        transparency = int(obj.GroupTransparency)
    except Exception:
        transparency = 0
    for child in getattr(obj, "Group", []):
        _apply_appearance_to(child, color, transparency)


# ---------------------------------------------------------------------------
# Dielectric group
# ---------------------------------------------------------------------------

class DielectricGroup:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyString",  "MaterialName",  "Material",
             "Descriptive label for this dielectric material", "")
        _add(obj, "App::PropertyFloat",   "Permittivity",  "Material",
             "Relative permittivity εr", 1.0)
        _add(obj, "App::PropertyFloat",   "Permeability",  "Material",
             "Relative permeability μr", 1.0)
        _add(obj, "App::PropertyFloat",   "LossTangent",   "Material",
             "Loss tangent tanδ (0 = lossless)", 0.0)
        _add(obj, "App::PropertyInteger", "MeshAttribute", "Mesh",
             "Gmsh physical volume attribute assigned to this domain", 2)
        _add(obj, "App::PropertyFloat",   "MeshSize",           "Mesh",
             "Element size at this dielectric's boundary surfaces "
             "(0 = use global MeshConductorSize)", 0.0)
        _add(obj, "App::PropertyFloat",   "MeshRefineDistance", "Mesh",
             "Distance over which the mesh transitions from MeshSize to background size "
             "(0 = use global MeshRefineDistance)", 0.0)
        # Display properties — editing these in the Data tab propagates the
        # colour to all child solids (reliable fallback for the Appearance menu).
        if not hasattr(obj, "GroupColor"):
            obj.addProperty("App::PropertyColor", "GroupColor", "Display",
                            "Color propagated to all child solids")
        if not hasattr(obj, "GroupTransparency"):
            obj.addProperty("App::PropertyInteger", "GroupTransparency", "Display",
                            "Transparency 0-100 propagated to all child solids")

    def onChanged(self, obj, prop):
        if prop in ("GroupColor", "GroupTransparency"):
            _propagate_from_data(obj)

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


class ViewProviderDielectricGroup:
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

    def updateData(self, fp, prop):
        # Fired when a child is added or removed from the group — push the
        # current GroupColor/GroupTransparency to the newly added child.
        if prop == "Group":
            _propagate_from_data(fp)

    def getIcon(self):
        return _DIEL_ICON

    def setEdit(self, vobj, mode=0):
        from panels.material_group_panel import DielectricGroupPanel
        from panels import show_palace_panel
        show_palace_panel(DielectricGroupPanel(vobj.Object))
        return True

    def unsetEdit(self, vobj, mode=0):
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return False

    def doubleClicked(self, vobj):
        self.setEdit(vobj)
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ---------------------------------------------------------------------------
# Conductor group
# ---------------------------------------------------------------------------

class ConductorGroup:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyString",      "MaterialName",   "Material",
             "Descriptive label for this conductor material", "")
        _add(obj, "App::PropertyEnumeration", "ConductorType",  "Material",
             "Conductor boundary condition type applied to exposed surfaces")
        if hasattr(obj, "ConductorType"):
            obj.ConductorType = ["PEC", "Lossy Conductor"]
        _add(obj, "App::PropertyFloat",       "Conductivity",   "Material",
             "Electrical conductivity σ (S/m) — used for Lossy Conductor type",
             5.96e7)
        _add(obj, "App::PropertyFloat",       "Permeability",   "Material",
             "Relative permeability μr — used for Lossy Conductor type", 1.0)
        _add(obj, "App::PropertyFloat",       "Thickness",      "Material",
             "Surface layer thickness (m) for impedance skin effect (0 = not set)", 0.0)
        _add(obj, "App::PropertyInteger",     "MeshAttribute",  "Mesh",
             "Gmsh physical surface attribute assigned to conductor boundaries", 100)
        _add(obj, "App::PropertyFloat",       "MeshSize",           "Mesh",
             "Element size at this conductor's surfaces "
             "(0 = use global MeshConductorSize)", 0.0)
        _add(obj, "App::PropertyFloat",       "MeshRefineDistance", "Mesh",
             "Distance over which the mesh transitions from MeshSize to background size "
             "(0 = use global MeshRefineDistance)", 0.0)
        if not hasattr(obj, "GroupColor"):
            obj.addProperty("App::PropertyColor", "GroupColor", "Display",
                            "Color propagated to all child solids")
        if not hasattr(obj, "GroupTransparency"):
            obj.addProperty("App::PropertyInteger", "GroupTransparency", "Display",
                            "Transparency 0-100 propagated to all child solids")

    def onChanged(self, obj, prop):
        if prop in ("GroupColor", "GroupTransparency"):
            _propagate_from_data(obj)

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


class ViewProviderConductorGroup:
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

    def updateData(self, fp, prop):
        if prop == "Group":
            _propagate_from_data(fp)

    def getIcon(self):
        return _COND_ICON

    def setEdit(self, vobj, mode=0):
        from panels.material_group_panel import ConductorGroupPanel
        from panels import show_palace_panel
        show_palace_panel(ConductorGroupPanel(vobj.Object))
        return True

    def unsetEdit(self, vobj, mode=0):
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return False

    def doubleClicked(self, vobj):
        self.setEdit(vobj)
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def create_dielectric_group(doc, mesh_attr=2, solids=None):
    count = sum(1 for o in doc.Objects if hasattr(o, "Permittivity") and hasattr(o, "MeshAttribute"))
    name = f"PalaceDielectric{count + 1}"
    obj = doc.addObject("App::FeaturePython", name)
    obj.addExtension("App::GroupExtensionPython")
    DielectricGroup(obj)
    obj.MeshAttribute = mesh_attr
    if solids:
        for s in solids:
            obj.addObject(s)
    if obj.ViewObject is not None:
        ViewProviderDielectricGroup(obj.ViewObject)
    # Set data-property defaults after children are in the group so that
    # the App proxy's onChanged propagates the colour to them immediately.
    obj.GroupColor = _DIEL_COLOR
    obj.GroupTransparency = _DIEL_TRANSP
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj


def create_conductor_group(doc, mesh_attr=100, solids=None):
    count = sum(1 for o in doc.Objects if hasattr(o, "ConductorType") and hasattr(o, "MeshAttribute"))
    name = f"PalaceConductor{count + 1}"
    obj = doc.addObject("App::FeaturePython", name)
    obj.addExtension("App::GroupExtensionPython")
    ConductorGroup(obj)
    obj.MeshAttribute = mesh_attr
    if solids:
        for s in solids:
            obj.addObject(s)
    if obj.ViewObject is not None:
        ViewProviderConductorGroup(obj.ViewObject)
    obj.GroupColor = _COND_COLOR
    obj.GroupTransparency = _COND_TRANSP
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj

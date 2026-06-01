import FreeCAD
import os

from features.material_group import _propagate_from_data, _apply_appearance_to  # noqa: F401
from features import add_to_simulation

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Airbox.svg")

_AIRBOX_COLOR = (0.4, 0.6, 1.0)
_AIRBOX_TRANSP = 70


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


class Airbox:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyEnumeration", "OuterBoundaryType", "Airbox",
             "Default boundary condition for outer airbox faces not explicitly assigned")
        if hasattr(obj, "OuterBoundaryType"):
            obj.OuterBoundaryType = ["PEC", "PMC", "Absorbing"]
        _add(obj, "App::PropertyLinkSubList", "PECFaces", "Airbox",
             "Outer faces explicitly assigned PEC boundary")
        _add(obj, "App::PropertyLinkSubList", "PMCFaces", "Airbox",
             "Outer faces explicitly assigned PMC boundary")
        _add(obj, "App::PropertyLinkSubList", "AbsorbingFaces", "Airbox",
             "Outer faces explicitly assigned Absorbing boundary")
        _add(obj, "App::PropertyInteger", "MeshAttribute", "Mesh",
             "Gmsh physical volume attribute number", 1)
        _add(obj, "App::PropertyFloat",   "MeshSize",      "Mesh",
             "Background element size inside the airbox "
             "(0 = use MeshCharacteristicLengthMax or auto)", 0.0)
        if not hasattr(obj, "GroupColor"):
            obj.addProperty("App::PropertyColor", "GroupColor", "Display",
                            "Color propagated to the airbox solid")
        if not hasattr(obj, "GroupTransparency"):
            obj.addProperty("App::PropertyInteger", "GroupTransparency", "Display",
                            "Transparency 0-100 propagated to the airbox solid")

    def onChanged(self, obj, prop):
        if prop == "Group":
            if len(obj.Group) > 1:
                obj.Group = [obj.Group[0]]
            _propagate_from_data(obj)
        elif prop in ("GroupColor", "GroupTransparency"):
            _propagate_from_data(obj)

    def execute(self, obj):
        # Migrate old AirboxShape PropertyLink to group membership.
        # The extension may not be present on objects restored from an older FCStd,
        # so we add it here before attempting any group operation.
        if not hasattr(obj, "AirboxShape"):
            return
        shape = getattr(obj, "AirboxShape", None)
        if shape is None:
            try:
                obj.removeProperty("AirboxShape")
            except Exception:
                pass
            return
        if not hasattr(obj, "Group"):
            try:
                obj.addExtension("App::GroupExtensionPython")
            except Exception:
                return
        if not obj.Group:
            try:
                obj.addObject(shape)
            except Exception:
                return
        try:
            obj.removeProperty("AirboxShape")
        except Exception:
            pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        if not hasattr(obj, "Group"):
            try:
                obj.addExtension("App::GroupExtensionPython")
            except Exception:
                pass
        if FreeCAD.GuiUp and obj.ViewObject is not None:
            try:
                obj.ViewObject.removeExtension("Gui::ViewProviderGroupExtensionPython", None)
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderAirbox:
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
        return _ICON

    def setEdit(self, vobj, mode=0):
        from panels.airbox_panel import AirboxPanel
        from panels import show_palace_panel
        show_palace_panel(AirboxPanel(vobj.Object))
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


def create_airbox(doc, shape_obj=None):
    obj = doc.addObject("App::FeaturePython", "PalaceAirbox")
    obj.addExtension("App::GroupExtensionPython")
    Airbox(obj)
    if shape_obj:
        obj.addObject(shape_obj)
    if obj.ViewObject is not None:
        ViewProviderAirbox(obj.ViewObject)
    obj.GroupColor = _AIRBOX_COLOR
    obj.GroupTransparency = _AIRBOX_TRANSP
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj

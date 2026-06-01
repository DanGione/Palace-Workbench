import FreeCAD
import os
from features import add_to_simulation

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "LumpedPort.svg")


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


class LumpedPort:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyInteger", "PortIndex", "Port",
             "Palace port index (1-based)", 1)
        _add(obj, "App::PropertyLinkSubList", "PortFaces", "Port",
             "Faces on the geometry that define this port boundary (face-selection mode)")
        _add(obj, "App::PropertyLinkSubList", "PortEdges", "Port",
             "Two edges defining the port cross-section (edge-pair mode)")
        _add(obj, "App::PropertyString", "PortGeometryType", "Port",
             "Geometry type detected from edge pair: 'annular', 'planar', or ''", "")
        _add(obj, "App::PropertyEnumeration", "Direction", "Port",
             "Port excitation direction (+R/-R for coaxial radial ports)")
        if hasattr(obj, "Direction"):
            obj.Direction = ["+R", "-R", "X", "-X", "Y", "-Y", "Z", "-Z"]
        _add(obj, "App::PropertyFloat", "R", "Impedance",
             "Port resistance (Ohms)", 50.0)
        _add(obj, "App::PropertyFloat", "L", "Impedance",
             "Port inductance (H)", 0.0)
        _add(obj, "App::PropertyFloat", "C", "Impedance",
             "Port capacitance (F)", 0.0)
        _add(obj, "App::PropertyBool", "Excitation", "Port",
             "Whether this port drives the simulation", True)
        _add(obj, "App::PropertyInteger", "MeshAttribute", "Mesh",
             "Gmsh physical surface attribute number", 11)

    def execute(self, obj):
        if not (hasattr(obj, "PortEdges") and obj.PortEdges):
            return
        edges = []
        for link_obj, subs in obj.PortEdges:
            for sub in (subs or []):
                try:
                    edges.append(link_obj.Shape.getElement(sub))
                except Exception:
                    pass
        if len(edges) < 2:
            return
        try:
            from palace.port_geometry import build_port_shape
            shape, kind = build_port_shape(edges[0], edges[1])
            obj.Shape = shape
            obj.PortGeometryType = kind
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace: lumped port shape error: {exc}\n")

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


class ViewProviderLumpedPort:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object

    def claimChildren(self):
        return getattr(self.Object, "Group", [])

    def getIcon(self):
        return _ICON

    def getDisplayModes(self, vobj):
        return ["Flat Lines", "Wireframe"]

    def getDefaultDisplayMode(self):
        return "Flat Lines"

    def updateData(self, obj, prop):
        pass

    def setEdit(self, vobj, mode=0):
        from panels.lumped_port_panel import LumpedPortPanel
        from panels import show_palace_panel
        show_palace_panel(LumpedPortPanel(vobj.Object))
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


def create_lumped_port(doc, index=1, faces=None, edge_refs=None):
    """Create a lumped port feature object.

    Parameters
    ----------
    faces     : list of (obj, [sub_names]) for face-selection mode
    edge_refs : list of (obj, sub_name) pairs — exactly 2 edges for edge-pair mode
    """
    obj = doc.addObject("Part::FeaturePython", f"PalaceLumpedPort{index}")
    obj.addExtension("App::GroupExtensionPython")
    LumpedPort(obj)
    obj.PortIndex = index
    obj.MeshAttribute = 10 + index

    if edge_refs and len(edge_refs) >= 2:
        # Group by object identity for PropertyLinkSubList
        by_obj = {}
        for ref_obj, sub in edge_refs[:2]:
            key = id(ref_obj)
            if key not in by_obj:
                by_obj[key] = (ref_obj, [])
            by_obj[key][1].append(sub)
        obj.PortEdges = [(ref_obj, subs) for ref_obj, subs in by_obj.values()]
    elif faces:
        obj.PortFaces = faces

    if obj.ViewObject is not None:
        ViewProviderLumpedPort(obj.ViewObject)
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj

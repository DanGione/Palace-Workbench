import FreeCAD
import os
from features import add_to_simulation

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "WavePort.svg")

_PORT_COLOR = (0.502, 0.0, 0.125)   # Burgundy #800020
_PORT_TRANSP = 30


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


class WavePort:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyInteger", "PortIndex", "Port",
             "Palace port index (1-based)", 1)
        _add(obj, "App::PropertyLinkSubList", "PortFaces", "Port",
             "Faces on the geometry that define this port boundary")
        _add(obj, "App::PropertyInteger", "NumModes", "Port",
             "Number of modal modes to include", 1)
        _add(obj, "App::PropertyBool", "Excitation", "Port",
             "Whether this port drives the simulation", True)
        _add(obj, "App::PropertyInteger", "MeshAttribute", "Mesh",
             "Gmsh physical surface attribute number", 11)
        _add(obj, "App::PropertyLinkSub", "IntegrationEdge", "Impedance",
             "Edge on the port face from inner conductor (signal) to outer "
             "conductor (ground) — used as Palace's native VoltagePath for "
             "voltage/impedance (Z_PV) postprocessing")
        _add(obj, "App::PropertyFloat", "CharacteristicZ", "Impedance",
             "Characteristic impedance magnitude (Ω), mean of |Z| across "
             "frequency — Palace's native Z_PV when an Integration edge is "
             "set, otherwise the TE/TM Poynting-flux impedance — "
             "auto-updated after each simulation run", 50.0)
        _add(obj, "App::PropertyFloat", "CharacteristicZReal", "Impedance",
             "Characteristic impedance, real part (Ω) — mean of Re(Z) across "
             "frequency — auto-updated after each simulation run", 50.0)
        _add(obj, "App::PropertyFloat", "CharacteristicZImag", "Impedance",
             "Characteristic impedance, imaginary part (Ω) — mean of Im(Z) "
             "across frequency — auto-updated after each simulation run", 0.0)
        _add(obj, "App::PropertyFloat", "RenormZ", "Impedance",
             "Target S-parameter normalization impedance (Ω)", 50.0)
        _add(obj, "App::PropertyInteger", "MaxIts", "Solver",
             "Krylov solver max iterations for the wave port eigenmode/"
             "voltage-path solve — 0 = use Palace's default", 0)
        _add(obj, "App::PropertyFloat", "KSPTol", "Solver",
             "Krylov solver tolerance — 0 = use Palace's default", 0.0)
        _add(obj, "App::PropertyFloat", "EigenTol", "Solver",
             "Eigenmode solve tolerance — 0 = use Palace's default", 0.0)
        _add(obj, "App::PropertyInteger", "NSamples", "Solver",
             "VoltagePath internal resampling resolution — 0 = use Palace's "
             "default (100)", 0)
        _add(obj, "App::PropertyFloat", "Offset", "Solver",
             "Port mode profile distance offset, same units as the port "
             "geometry — 0 = use Palace's default", 0.0)
        _add(obj, "App::PropertyColor",   "GroupColor",        "Display",
             "Color propagated to all child shapes", _PORT_COLOR)
        _add(obj, "App::PropertyInteger", "GroupTransparency", "Display",
             "Transparency 0-100 propagated to all child shapes", _PORT_TRANSP)

    def onChanged(self, obj, prop):
        if prop in ("GroupColor", "GroupTransparency"):
            from features.material_group import _propagate_from_data
            _propagate_from_data(obj)

    def execute(self, obj):
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


class ViewProviderWavePort:
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
            from features.material_group import _propagate_from_data
            _propagate_from_data(fp)

    def getIcon(self):
        return _ICON

    def setEdit(self, vobj, mode=0):
        from panels.wave_port_panel import WavePortPanel
        from panels import show_palace_panel
        show_palace_panel(WavePortPanel(vobj.Object))
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


def create_wave_port(doc, index=1, faces=None):
    obj = doc.addObject("App::FeaturePython", f"PalaceWavePort{index}")
    obj.addExtension("App::GroupExtensionPython")
    WavePort(obj)
    obj.PortIndex = index
    obj.MeshAttribute = 10 + index
    if faces:
        obj.PortFaces = faces
    if obj.ViewObject is not None:
        ViewProviderWavePort(obj.ViewObject)
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj

import FreeCAD
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Simulation.svg")


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


def _is_palace_child(o):
    return (
        hasattr(o, "OuterBoundaryType") or
        (hasattr(o, "Permittivity") and hasattr(o, "MeshAttribute")) or
        (hasattr(o, "ConductorType") and hasattr(o, "MeshAttribute")) or
        (hasattr(o, "Rs") and hasattr(o, "MeshAttribute")) or          # ImpedanceBoundary
        (hasattr(o, "PortIndex") and hasattr(o, "R") and hasattr(o, "C")
         and not hasattr(o, "Rs")) or                                   # LumpedPort
        (hasattr(o, "PortIndex") and hasattr(o, "NumModes") and not hasattr(o, "R"))
    )


class SimulationContainer:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyEnumeration", "SimulationType", "Palace",
             "Type of Palace simulation")
        if hasattr(obj, "SimulationType"):
            obj.SimulationType = ["Driven", "Eigenmode", "Electrostatic"]

        _add(obj, "App::PropertyFloat", "L0", "Palace",
             "Length scale in meters (1e-3 = mm)", 1e-3)
        _add(obj, "App::PropertyInteger", "Order", "Solver",
             "Finite element polynomial order (1–4)", 2)
        _add(obj, "App::PropertyInteger", "NumProcesses", "Solver",
             "Number of MPI processes (Palace ranks). 1 = single-process, no mpirun.", 1)
        _add(obj, "App::PropertyInteger", "NumThreads", "Solver",
             "OpenMP threads per MPI rank (OMP_NUM_THREADS). 0 = use system default.", 0)
        _add(obj, "App::PropertyInteger", "Verbose", "Palace",
             "Palace verbosity level (0–2)", 1)
        _add(obj, "App::PropertyString", "OutputDir", "Palace",
             "Output directory for Palace results", "output/")
        _add(obj, "App::PropertyString", "PalaceBinary", "Palace",
             "Full path to the Palace executable", "/usr/local/bin/palace")
        _add(obj, "App::PropertyString", "MeshFile", "Palace",
             "Path to the generated Gmsh mesh file (set automatically)", "")
        _add(obj, "App::PropertyString", "SParamSelection", "Palace",
             "JSON list of [row,col] S-parameter keys selected in the viewer", "")
        _add(obj, "App::PropertyString", "AvailableSParams", "Palace",
             "JSON list of [[row,col],...] S-param pairs available given current port Excitation "
             "settings (auto-updated on recompute).", "[]")

        # Mesh sizing
        _add(obj, "App::PropertyFloat", "MeshCharacteristicLengthMax", "Mesh",
             "Global maximum element size in model units (0 = Gmsh default)", 0.0)
        _add(obj, "App::PropertyFloat", "MeshCharacteristicLengthMin", "Mesh",
             "Global minimum element size in model units (0 = Gmsh default)", 0.0)

        # Driven
        _add(obj, "App::PropertyFloat", "DrivenMinFreq", "Driven Solver",
             "Minimum frequency (Hz)", 1e9)
        _add(obj, "App::PropertyFloat", "DrivenMaxFreq", "Driven Solver",
             "Maximum frequency (Hz)", 5e9)
        _add(obj, "App::PropertyFloat", "DrivenFreqStep", "Driven Solver",
             "Frequency step (Hz) — uniform sweep step, or output grid step for adaptive", 0.1e9)

        # Driven sweep mode
        _add(obj, "App::PropertyString", "DrivenSweepMode", "Driven Solver",
             "Frequency sweep mode: 'Linear' (MinFreq/MaxFreq/FreqStep) or 'Samples' (custom list)", "Linear")
        _add(obj, "App::PropertyString", "DrivenSamplesJSON", "Driven Solver",
             "JSON array of sample objects for Samples sweep mode", "[]")

        # Adaptive frequency sweep (driven only)
        _add(obj, "App::PropertyFloat", "DrivenAdaptiveTol", "Driven Solver",
             "Adaptive sweep convergence tolerance (0 = disabled, use uniform step)", 0.0)
        _add(obj, "App::PropertyInteger", "DrivenAdaptiveMaxSamples", "Driven Solver",
             "Maximum number of adaptive frequency samples", 10)
        _add(obj, "App::PropertyInteger", "DrivenAdaptiveMaxCandidates", "Driven Solver",
             "Maximum candidate frequency points evaluated per adaptive iteration (0 = Palace default)", 0)
        _add(obj, "App::PropertyBool", "ParallelPasses", "Solver",
             "Run multi-port excitation passes simultaneously (parallel) rather than one at a time (serial)",
             True)

        # Eigenmode
        _add(obj, "App::PropertyInteger", "EigenNumModes", "Eigenmode Solver",
             "Number of eigenmodes to compute", 2)
        _add(obj, "App::PropertyFloat", "EigenFreqMin", "Eigenmode Solver",
             "Minimum frequency hint (Hz)", 0.0)

        # Electrostatic
        _add(obj, "App::PropertyInteger", "ElecMaxIter", "Electrostatic Solver",
             "Maximum solver iterations", 500)
        _add(obj, "App::PropertyFloat", "ElecTol", "Electrostatic Solver",
             "Solver tolerance", 1e-8)

    def execute(self, obj):
        import json
        try:
            from features import get_available_s_params
            pairs = get_available_s_params(obj.Document)
            obj.AvailableSParams = json.dumps([[r, c] for r, c in pairs])
        except Exception:
            pass

    def onChanged(self, obj, prop):
        pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        self._migrate_to_group(obj)
        if FreeCAD.GuiUp and obj.ViewObject is not None:
            try:
                obj.ViewObject.removeExtension("Gui::ViewProviderGroupExtensionPython", None)
            except Exception:
                pass

    def _migrate_to_group(self, obj):
        """Add GroupExtension if missing and pull in any un-grouped palace children."""
        if not hasattr(obj, "Group"):
            try:
                obj.addExtension("App::GroupExtensionPython")
            except Exception:
                return
        current_ids = {id(o) for o in obj.Group}
        for other in obj.Document.Objects:
            if other is obj or id(other) in current_ids:
                continue
            if _is_palace_child(other):
                try:
                    obj.addObject(other)
                    current_ids.add(id(other))
                except Exception:
                    pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderSimulation:
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
        return _ICON

    def setEdit(self, vobj, mode=0):
        from panels.simulation_panel import SimulationPanel
        from panels import show_palace_panel
        show_palace_panel(SimulationPanel(vobj.Object))
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


def _detect_palace_binary():
    import shutil
    hit = shutil.which("palace")
    if hit:
        return hit
    fallback = (
        "/opt/spack/opt/spack/linux-ubuntu22.04-icelake/gcc-11.4.0"
        "/palace-0.12.0-eqguemtopoeez24knhosweng35twldaa/bin/palace"
    )
    return fallback if os.path.isfile(fallback) else ""


def create_simulation(doc):
    obj = doc.addObject("App::FeaturePython", "PalaceSimulation")
    obj.addExtension("App::GroupExtensionPython")
    SimulationContainer(obj)
    if obj.ViewObject is not None:
        ViewProviderSimulation(obj.ViewObject)
    obj.PalaceBinary = _detect_palace_binary()
    doc.recompute()
    return obj

import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Results.svg")


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


class SweepContainer:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        # ---- Sweep parameters -------------------------------------------------
        _add(obj, "App::PropertyString", "SweepParams", "Sweep",
             'JSON list of sweep parameters: [{"varset":"VarSet","property":"name",'
             '"values":[...],"min":0,"max":1}, ...]', "[]")
        _add(obj, "App::PropertyEnumeration", "SweepMode", "Sweep",
             "Sweep: iterate over explicit values.  Optimize: find optimal values via scipy.",
             ["Sweep", "Optimize"])
        _add(obj, "App::PropertyEnumeration", "SweepType", "Sweep",
             "Grid: cartesian product of all value lists.  "
             "Sequential: zip value lists (all must be the same length).",
             ["Grid", "Sequential"])
        _add(obj, "App::PropertyFileIncluded", "SweepResultsFile", "Sweep",
             "Embedded sweep/optimization results NetCDF4 file (set automatically on run).", "")
        _add(obj, "App::PropertyString", "SweepStatus", "Sweep",
             "Current sweep status (read-only, updated during run).", "")

        # ---- Optimization settings --------------------------------------------
        _add(obj, "App::PropertyString", "ObjectiveList", "Optimize",
             "JSON list of weighted objectives: "
             '[{"row":1,"col":1,"type":"magnitude","freq_start":2.45,"freq_stop":2.45,"target":-20.0,"weight":1.0}]',
             "[]")
        _add(obj, "App::PropertyEnumeration", "ObjectiveGoal", "Optimize",
             "Whether to minimize or maximize the weighted objective sum.",
             ["Minimize", "Maximize"])
        _add(obj, "App::PropertyEnumeration", "OptAlgorithm", "Optimize",
             "Scipy optimization algorithm.",
             ["Nelder-Mead", "Differential Evolution", "COBYLA"])
        _add(obj, "App::PropertyInteger", "OptMaxIter", "Optimize",
             "Maximum number of optimizer iterations.", 50)
        # Per-algorithm tolerances
        _add(obj, "App::PropertyFloat", "NMXatol", "Optimize",
             "Nelder-Mead: simplex convergence tolerance as a fraction of each "
             "parameter's range (e.g. 0.01 = 1% of range).", 0.01)
        _add(obj, "App::PropertyFloat", "COBYLATol", "Optimize",
             "COBYLA: initial trust-region radius (rhobeg); controls final accuracy.", 0.01)
        _add(obj, "App::PropertyFloat", "DETol", "Optimize",
             "Differential Evolution: relative tolerance on population spread for convergence.", 0.01)

    def execute(self, obj):
        pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        from palace.embedded_files import migrate_legacy_string_property, legacy_sibling_path
        migrate_legacy_string_property(
            obj, "SweepResultsFile", "Sweep",
            "Embedded sweep/optimization results NetCDF4 file (set automatically on run).",
            legacy_hint=legacy_sibling_path(obj.Document, "sweep.nc"),
        )

    def __getstate__(self):
        return None

    def __setstate__(self, _state):
        return None


class ViewProviderSweep:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.vobj = vobj

    def getIcon(self):
        return _ICON

    def setEdit(self, vobj, mode=0):
        obj = vobj.Object
        try:
            from panels.sweep_panel import SweepPanel
            from panels import show_palace_panel
            show_palace_panel(SweepPanel(obj))
        except Exception as exc:
            import FreeCAD
            FreeCAD.Console.PrintError(f"Palace: could not open sweep panel: {exc}\n")
        return True

    def unsetEdit(self, vobj, mode=0):
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return True

    def doubleClicked(self, vobj):
        self.setEdit(vobj)

    def onChanged(self, vobj, prop):
        pass

    def updateData(self, obj, prop):
        pass

    def getDisplayModes(self, vobj):
        return ["Default"]

    def getDefaultDisplayMode(self):
        return "Default"

    def __getstate__(self):
        return None

    def __setstate__(self, _state):
        return None


def create_sweep(doc):
    """Create and return a PalaceSweep feature object."""
    obj = doc.addObject("App::FeaturePython", "PalaceSweep")
    SweepContainer(obj)
    if obj.ViewObject is not None:
        ViewProviderSweep(obj.ViewObject)
    doc.recompute()
    return obj

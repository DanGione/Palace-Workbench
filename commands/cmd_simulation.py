import FreeCAD
import FreeCADGui
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Simulation.svg")


class CmdSimulation:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "New Simulation",
            "ToolTip": "Create a Palace simulation container in the active document",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        # Only one simulation container per document
        return not any(hasattr(o, "SimulationType") and hasattr(o, "PalaceBinary")
                       for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            doc = FreeCAD.newDocument()
        from features.simulation import create_simulation
        from panels.simulation_panel import SimulationPanel
        from panels import show_palace_panel
        obj = create_simulation(doc)
        show_palace_panel(SimulationPanel(obj))

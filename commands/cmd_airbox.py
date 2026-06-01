import FreeCAD
import FreeCADGui
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Airbox.svg")


class CmdAirbox:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Set Airbox",
            "ToolTip": "Assign the selected solid as the Palace simulation domain (airbox)",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        has_sim = any(hasattr(o, "SimulationType") for o in doc.Objects)
        no_airbox = not any(hasattr(o, "OuterBoundaryType") for o in doc.Objects)
        return has_sim and no_airbox

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        sel = FreeCADGui.Selection.getSelection()
        shape_obj = sel[0] if sel else None
        from features.airbox import create_airbox
        from panels.airbox_panel import AirboxPanel
        from panels import show_palace_panel
        obj = create_airbox(doc, shape_obj)
        show_palace_panel(AirboxPanel(obj))

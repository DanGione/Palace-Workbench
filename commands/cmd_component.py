import FreeCAD
import os

from commands import selected_solids

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Component.svg")


class CmdCreateComponent:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Create Component from Selection",
            "ToolTip": (
                "Bundle selected solids into a single reusable Component: tag each "
                "one as a Conductor or Dielectric with a material, then move, copy, "
                "or export the whole assembly as one unit. Select solids first, or "
                "add them from the panel that opens."
            ),
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return bool(doc and any(hasattr(o, "OuterBoundaryType") for o in doc.Objects))

    def Activated(self):
        doc = FreeCAD.ActiveDocument

        from panels.component_panel import ComponentBuilderPanel
        from panels import show_palace_panel

        show_palace_panel(ComponentBuilderPanel.for_new(doc, initial_solids=selected_solids()))

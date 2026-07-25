import FreeCAD
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons",
                     "SMDComponent.svg")


class CmdSMDComponent:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Create SMD Component",
            "ToolTip": (
                "Generate a two-terminal SMD chip component (resistor, capacitor,\n"
                "or inductor) with body, contacts, optional pads and solder joints,\n"
                "and an ImpedanceBoundary. Select a face or plane first to attach\n"
                "the component to it (see the Attachment section in the task panel)."
            ),
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        return any(hasattr(o, "OuterBoundaryType") for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        ghost = doc.addObject("Part::Feature", "SMDComponentPreview")

        from panels.smd_component_panel import SMDComponentPanel
        from panels import show_palace_panel

        show_palace_panel(SMDComponentPanel(doc, ghost))

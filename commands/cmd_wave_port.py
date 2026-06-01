import FreeCAD
import FreeCADGui
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "WavePort.svg")


class CmdWavePort:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Add Wave Port",
            "ToolTip": "Add a full-wave modal port to the Palace simulation",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        return any(hasattr(o, "OuterBoundaryType") for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        faces = []
        for s in FreeCADGui.Selection.getSelectionEx():
            subs = [n for n in (s.SubElementNames or []) if n.startswith("Face")]
            if subs:
                faces.append((s.Object, subs))

        from features import next_port_index
        from features.wave_port import create_wave_port
        from panels.wave_port_panel import WavePortPanel
        from panels import show_palace_panel
        idx = next_port_index(doc)
        obj = create_wave_port(doc, index=idx, faces=faces if faces else None)
        show_palace_panel(WavePortPanel(obj))

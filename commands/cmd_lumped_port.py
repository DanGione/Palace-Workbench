import FreeCAD
import FreeCADGui
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "LumpedPort.svg")


class CmdLumpedPort:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Add Lumped Port",
            "ToolTip": (
                "Add a lumped R/L/C port to the Palace simulation.\n"
                "Select two edges (curves) for automatic port face construction,\n"
                "or select a face to use the existing face-selection workflow."
            ),
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        return any(hasattr(o, "OuterBoundaryType") for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.ActiveDocument

        # Prefer edge selection (two edges → build port face automatically)
        edge_refs = []
        face_refs = []
        for s in FreeCADGui.Selection.getSelectionEx():
            for sub in (s.SubElementNames or []):
                if sub.startswith("Edge") and len(edge_refs) < 2:
                    edge_refs.append((s.Object, sub))
                elif sub.startswith("Face"):
                    face_refs.append((s.Object, [sub]))

        from features import next_port_index
        from features.lumped_port import create_lumped_port
        from panels.lumped_port_panel import LumpedPortPanel
        from panels import show_palace_panel

        idx = next_port_index(doc)

        if len(edge_refs) == 2:
            obj = create_lumped_port(doc, index=idx, edge_refs=edge_refs)
        else:
            obj = create_lumped_port(doc, index=idx, faces=face_refs if face_refs else None)

        show_palace_panel(LumpedPortPanel(obj))

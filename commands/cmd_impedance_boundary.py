import FreeCAD
import FreeCADGui
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons",
                     "ImpedanceBoundary.svg")


class CmdImpedanceBoundary:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Add Impedance Boundary",
            "ToolTip": (
                "Add a surface impedance boundary (Rs/Ls/Cs) to the Palace simulation.\n"
                "Select two edges for automatic boundary face construction,\n"
                "or select a face to use the face-selection workflow."
            ),
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        return any(hasattr(o, "OuterBoundaryType") for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.ActiveDocument

        edge_refs = []
        face_refs = []
        for s in FreeCADGui.Selection.getSelectionEx():
            for sub in (s.SubElementNames or []):
                if sub.startswith("Edge") and len(edge_refs) < 2:
                    edge_refs.append((s.Object, sub))
                elif sub.startswith("Face"):
                    face_refs.append((s.Object, [sub]))

        from features import next_port_index
        from features.impedance_boundary import create_impedance_boundary
        from panels.impedance_boundary_panel import ImpedanceBoundaryPanel
        from panels import show_palace_panel

        idx = next_port_index(doc)

        if len(edge_refs) == 2:
            obj = create_impedance_boundary(doc, index=idx, edge_refs=edge_refs)
        else:
            obj = create_impedance_boundary(
                doc, index=idx, faces=face_refs if face_refs else None
            )

        show_palace_panel(ImpedanceBoundaryPanel(obj))

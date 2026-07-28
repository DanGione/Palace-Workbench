import FreeCAD
import os

from commands import selected_solids

_DIEL_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "DielectricGroup.svg")
_COND_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "ConductorGroup.svg")


class CmdDielectricGroup:
    def GetResources(self):
        return {
            "Pixmap":    _DIEL_ICON,
            "MenuText":  "Add Dielectric Group",
            "ToolTip":   "Create a material group for dielectric solid bodies (sets εr / μr per domain)",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return bool(doc and any(hasattr(o, "OuterBoundaryType") for o in doc.Objects))

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        solids = selected_solids()

        from features.material_group import create_dielectric_group
        from panels.material_group_panel import DielectricGroupPanel
        from panels import show_palace_panel

        # Mesh attribute: dielectric volumes start at 2 (1 = airbox background).
        existing = [o for o in doc.Objects
                    if hasattr(o, "Permittivity") and hasattr(o, "MeshAttribute")]
        mesh_attr = max((o.MeshAttribute for o in existing), default=1) + 1

        obj = create_dielectric_group(doc, mesh_attr=mesh_attr,
                                      solids=solids if solids else None)
        show_palace_panel(DielectricGroupPanel(obj))


class CmdConductorGroup:
    def GetResources(self):
        return {
            "Pixmap":    _COND_ICON,
            "MenuText":  "Add Conductor Group",
            "ToolTip":   "Create a material group for conductor solid bodies (PEC or lossy boundary)",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return bool(doc and any(hasattr(o, "OuterBoundaryType") for o in doc.Objects))

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        solids = selected_solids()

        from features.material_group import create_conductor_group
        from panels.material_group_panel import ConductorGroupPanel
        from panels import show_palace_panel

        # Conductor surface attributes start at 100.
        existing = [o for o in doc.Objects
                    if hasattr(o, "ConductorType") and hasattr(o, "MeshAttribute")]
        mesh_attr = max((o.MeshAttribute for o in existing), default=99) + 1

        obj = create_conductor_group(doc, mesh_attr=mesh_attr,
                                     solids=solids if solids else None)
        show_palace_panel(ConductorGroupPanel(obj))

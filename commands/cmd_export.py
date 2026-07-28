"""Export commands for embedded simulation artifacts (mesh, geometry, config).

Since results.nc/sweep.nc/mesh.msh/geometry.step/palace_config.json are now
embedded inside the .FCStd file (see palace/embedded_files.py) rather than
left in a sibling folder, these commands let the user pull a copy back out to
a chosen location on disk -- e.g. to inspect mesh.msh in the Gmsh GUI or
geometry.step in another CAD tool.
"""

import os

import FreeCAD

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

_MESH_ICON     = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Mesh.svg")
_GEOMETRY_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Airbox.svg")
_CONFIG_ICON   = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Simulation.svg")


def _default_export_dir(doc):
    if doc and doc.FileName:
        return os.path.dirname(doc.FileName)
    return os.path.expanduser("~")


def _is_exportable(doc, find_owner, prop_name):
    if not doc:
        return False
    from palace.embedded_files import resolve
    owner = find_owner(doc)
    return bool(owner and resolve(owner, prop_name))


def _export_embedded_file(doc, owner, prop_name, dialog_title, default_name, file_filter):
    from palace.embedded_files import resolve, copy_for_rewrite

    resolved = resolve(owner, prop_name) if owner else ""
    if not resolved or not os.path.isfile(resolved):
        QtWidgets.QMessageBox.warning(
            None, "Nothing to Export",
            f"No {default_name} has been generated yet."
        )
        return

    default_path = os.path.join(_default_export_dir(doc), default_name)
    path, _ = QtWidgets.QFileDialog.getSaveFileName(None, dialog_title, default_path, file_filter)
    if not path:
        return

    try:
        copy_for_rewrite(resolved, path)
        FreeCAD.Console.PrintMessage(f"Palace: Exported {default_name} → {path}\n")
    except Exception as exc:
        QtWidgets.QMessageBox.critical(None, "Export Failed", str(exc))


class CmdExportMesh:
    """Export the embedded Gmsh mesh file for inspection in the Gmsh GUI."""

    def GetResources(self):
        return {
            "Pixmap": _MESH_ICON,
            "MenuText": "Export Mesh…",
            "ToolTip": "Save a copy of the embedded mesh.msh file to disk.",
        }

    def IsActive(self):
        from features import find_palace_mesh
        return _is_exportable(FreeCAD.ActiveDocument, find_palace_mesh, "MeshFile")

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        from features import find_palace_mesh
        mesh_obj = find_palace_mesh(doc) if doc else None
        _export_embedded_file(
            doc, mesh_obj, "MeshFile",
            "Export Mesh", "mesh.msh", "Gmsh mesh (*.msh);;All files (*)",
        )


class CmdExportGeometry:
    """Export the embedded STEP geometry snapshot for use in another CAD tool."""

    def GetResources(self):
        return {
            "Pixmap": _GEOMETRY_ICON,
            "MenuText": "Export Geometry…",
            "ToolTip": "Save a copy of the embedded geometry.step file to disk.",
        }

    def IsActive(self):
        from features import find_palace_mesh
        return _is_exportable(FreeCAD.ActiveDocument, find_palace_mesh, "GeometryFile")

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        from features import find_palace_mesh
        mesh_obj = find_palace_mesh(doc) if doc else None
        _export_embedded_file(
            doc, mesh_obj, "GeometryFile",
            "Export Geometry", "geometry.step", "STEP files (*.step *.stp);;All files (*)",
        )


class CmdExportConfig:
    """Export the embedded Palace JSON config for manual inspection or a standalone run."""

    def GetResources(self):
        return {
            "Pixmap": _CONFIG_ICON,
            "MenuText": "Export Config…",
            "ToolTip": "Save a copy of the embedded Palace JSON config to disk.",
        }

    def IsActive(self):
        from features import find_simulation
        return _is_exportable(FreeCAD.ActiveDocument, find_simulation, "ConfigFile")

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        from features import find_simulation
        sim = find_simulation(doc) if doc else None
        _export_embedded_file(
            doc, sim, "ConfigFile",
            "Export Config", "palace_config.json", "JSON files (*.json);;All files (*)",
        )

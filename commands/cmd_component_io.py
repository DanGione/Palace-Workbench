import os
import shutil

import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

from palace.component_io import (
    export_component, import_component, read_manifest, detect_collisions, unique_material_name,
)

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Component.svg")


def _default_export_dir(doc):
    if doc and doc.FileName:
        return os.path.dirname(doc.FileName)
    return os.path.expanduser("~")


def _selected_component(doc):
    if not doc:
        return None
    for obj in FreeCADGui.Selection.getSelection():
        if hasattr(obj, "ChildBasePlacements") and hasattr(obj, "ComponentKind"):
            return obj
    return None


class CmdExportComponent:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Export Component…",
            "ToolTip": "Save the selected Component as a single bundle file for reuse in another project.",
        }

    def IsActive(self):
        return _selected_component(FreeCAD.ActiveDocument) is not None

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        container = _selected_component(doc)
        if container is None:
            QtWidgets.QMessageBox.information(
                None, "No Component Selected", "Select a Component in the tree or 3D view first.")
            return

        default_path = os.path.join(_default_export_dir(doc), f"{container.Label}.palcomp")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            None, "Export Component", default_path, "Palace Component (*.palcomp);;All files (*)")
        if not path:
            return

        try:
            export_component(container, path)
            FreeCAD.Console.PrintMessage(f"Palace: Exported component → {path}\n")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "Export Failed", str(exc))


def _resolve_collisions(doc, collisions):
    """Show a modal dialog listing material-name collisions.

    Returns (keep_existing_for, rename_to) -- see palace/component_io.py's
    import_component() -- or None if the user cancelled the import outright.
    """
    dlg = QtWidgets.QDialog(FreeCADGui.getMainWindow())
    dlg.setWindowTitle("Material Name Conflicts")
    vbox = QtWidgets.QVBoxLayout(dlg)
    vbox.addWidget(QtWidgets.QLabel(
        "These material names already exist in this document with different "
        "properties. Choose how to resolve each one before importing:"
    ))

    table = QtWidgets.QTableWidget(len(collisions), 3)
    table.setHorizontalHeaderLabels(["Material", "Role", "Resolution"])
    combos = []
    for row, c in enumerate(collisions):
        table.setItem(row, 0, QtWidgets.QTableWidgetItem(c.material_name))
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(c.role))
        combo = QtWidgets.QComboBox()
        combo.addItems(["Use existing", "Import as new"])
        table.setCellWidget(row, 2, combo)
        combos.append(combo)
    table.resizeColumnsToContents()
    vbox.addWidget(table)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    vbox.addWidget(buttons)

    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return None

    keep_existing_for = set()
    rename_to = {}
    for c, combo in zip(collisions, combos):
        key = (c.role, c.material_name)
        if combo.currentText() == "Use existing":
            keep_existing_for.add(key)
        else:
            rename_to[key] = unique_material_name(doc, c.role, c.material_name)
    return keep_existing_for, rename_to


class CmdImportComponent:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Import Component…",
            "ToolTip": "Load a Component bundle exported from this or another project.",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return bool(doc and any(hasattr(o, "OuterBoundaryType") for o in doc.Objects))

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Import Component", _default_export_dir(doc),
            "Palace Component (*.palcomp);;All files (*)")
        if not path:
            return

        scratch_dir = doc.getTempFileName("component_import_preview")
        try:
            os.makedirs(scratch_dir, exist_ok=True)
            manifest = read_manifest(path, scratch_dir)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "Import Failed", f"Could not read bundle: {exc}")
            return
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

        collisions = detect_collisions(doc, manifest)
        keep_existing_for, rename_to = set(), {}
        if collisions:
            resolved = _resolve_collisions(doc, collisions)
            if resolved is None:
                return   # user cancelled
            keep_existing_for, rename_to = resolved

        try:
            import_component(doc, path, keep_existing_for=keep_existing_for, rename_to=rename_to)
            FreeCAD.Console.PrintMessage(f"Palace: Imported component ← {path}\n")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(None, "Import Failed", str(exc))

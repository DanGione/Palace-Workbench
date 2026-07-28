"""Task panel for creating/editing a Selection-derived Component.

Lets the user tag each selected solid as a Conductor or Dielectric with a
material name, then bundles them into a single Component (see
features/component.py) via create_component_from_solids(). Numeric material
properties (permittivity, conductivity, etc.) are intentionally not exposed
in this table -- picking a material name that already exists in the document
shares that material; a brand-new name gets the same DielectricGroup/
ConductorGroup defaults a hand-created group gets, and its property panel
(panels/material_group_panel.py, already built and field-complete) opens
right after accept() so the user can set real numbers.
"""

import FreeCADGui

try:
    from PySide2 import QtWidgets, QtCore
except ImportError:
    from PySide6 import QtWidgets, QtCore

from commands import selected_solids
from features import find_dielectric_groups, find_conductor_groups
from features.component import create_component_from_solids, add_solid_pair

_ROLES = ["Conductor", "Dielectric"]

_COL_SOLID = 0
_COL_ROLE = 1
_COL_MATERIAL = 2


def _material_names(doc, role):
    groups = find_dielectric_groups(doc) if role == "Dielectric" else find_conductor_groups(doc)
    return sorted({g.MaterialName for g in groups if g.MaterialName})


def _find_role_and_material(doc, shadow):
    for g in find_dielectric_groups(doc):
        if shadow in g.Group:
            return "Dielectric", g.MaterialName
    for g in find_conductor_groups(doc):
        if shadow in g.Group:
            return "Conductor", g.MaterialName
    return None, None


class _ComponentTableWidget(QtWidgets.QTableWidget):
    """One row per tagged solid: Solid (read-only) | Role | Material.

    Each row remembers the FreeCAD object it refers to as Qt.UserRole data on
    the Solid column -- either an existing shadow solid already part of the
    component being edited, or a freshly selected source object not yet
    incorporated (see ComponentBuilderPanel.accept()/_apply_edits()).
    """

    def __init__(self, doc, parent=None):
        super().__init__(0, 3, parent)
        self._doc = doc
        self.setHorizontalHeaderLabels(["Solid", "Role", "Material"])
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(_COL_SOLID, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(_COL_ROLE, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_MATERIAL, QtWidgets.QHeaderView.Stretch)

    def add_row(self, label, role, material, ref):
        row = self.rowCount()
        self.insertRow(row)

        item = QtWidgets.QTableWidgetItem(label)
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        item.setData(QtCore.Qt.UserRole, ref)
        self.setItem(row, _COL_SOLID, item)

        role_combo = QtWidgets.QComboBox()
        role_combo.addItems(_ROLES)
        role_combo.setCurrentText(role)
        self.setCellWidget(row, _COL_ROLE, role_combo)

        mat_combo = QtWidgets.QComboBox()
        mat_combo.setEditable(True)
        mat_combo.addItems(_material_names(self._doc, role))
        mat_combo.setCurrentText(material)
        self.setCellWidget(row, _COL_MATERIAL, mat_combo)

        # Connected last, after setCurrentText() above -- same ordering
        # invariant as panels/sweep_panel.py's _ParamTableWidget.add_row:
        # refreshing the material list on role change must not fire while
        # this row's saved values are still being loaded.
        role_combo.currentTextChanged.connect(lambda text, r=row: self._on_role_changed(r, text))

    def _on_role_changed(self, row, role_text):
        mat_combo = self.cellWidget(row, _COL_MATERIAL)
        if mat_combo is None:
            return
        current = mat_combo.currentText()
        mat_combo.clear()
        mat_combo.addItems(_material_names(self._doc, role_text))
        mat_combo.setCurrentText(current)

    def remove_selected_rows(self):
        rows = sorted({i.row() for i in self.selectedItems()}, reverse=True)
        for r in rows:
            self.removeRow(r)

    def refs(self):
        return [self.item(r, _COL_SOLID).data(QtCore.Qt.UserRole) for r in range(self.rowCount())]

    def rows(self):
        """Return (ref, role, material_name) for every row."""
        result = []
        for r in range(self.rowCount()):
            ref = self.item(r, _COL_SOLID).data(QtCore.Qt.UserRole)
            role = self.cellWidget(r, _COL_ROLE).currentText()
            material = self.cellWidget(r, _COL_MATERIAL).currentText().strip()
            result.append((ref, role, material))
        return result


class ComponentBuilderPanel:
    def __init__(self, doc, container=None):
        self.doc = doc
        self.container = container   # existing Component being re-edited, or None when creating
        self.form = self._build()

    @classmethod
    def for_new(cls, doc, initial_solids=None):
        panel = cls(doc, container=None)
        for obj in (initial_solids or []):
            panel._add_row_for_source(obj)
        return panel

    @classmethod
    def for_existing(cls, container):
        panel = cls(container.Document, container=container)
        for shadow in getattr(container, "ShadowSolids", []):
            role, material = _find_role_and_material(container.Document, shadow)
            panel.table.add_row(shadow.Label, role or "Conductor", material or "", shadow)
        return panel

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Create Component" if self.container is None else "Edit Component")
        vbox = QtWidgets.QVBoxLayout(w)

        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Component name:"))
        self.edit_name = QtWidgets.QLineEdit()
        if self.container is not None:
            self.edit_name.setText(self.container.Label)
        name_row.addWidget(self.edit_name)
        vbox.addLayout(name_row)

        self.table = _ComponentTableWidget(self.doc)
        vbox.addWidget(self.table)

        btn_row = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("Add Selected")
        btn_add.clicked.connect(self._add_selection)
        btn_row.addWidget(btn_add)
        btn_remove = QtWidgets.QPushButton("Remove Row")
        btn_remove.clicked.connect(self.table.remove_selected_rows)
        btn_row.addWidget(btn_remove)
        vbox.addLayout(btn_row)

        hint = QtWidgets.QLabel(
            "Tag each solid as a Conductor or Dielectric and give it a material "
            "name. A name that already exists elsewhere in the document shares "
            "that material; a new name opens its property panel after you "
            "click OK."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-style: italic;")
        vbox.addWidget(hint)

        return w

    def _add_row_for_source(self, obj):
        if obj in self.table.refs() or not hasattr(obj, "Shape"):
            return
        self.table.add_row(obj.Label, "Conductor", "", obj)

    def _add_selection(self):
        before = self.table.rowCount()
        for obj in selected_solids():
            self._add_row_for_source(obj)
        if self.table.rowCount() == before:
            QtWidgets.QMessageBox.information(
                self.form, "No New Solids",
                "Select solid objects in the 3D view or model tree first."
            )

    # ------------------------------------------------------------------
    # Task dialog protocol
    # ------------------------------------------------------------------

    def accept(self):
        component_name = self.edit_name.text().strip()
        if not component_name:
            QtWidgets.QMessageBox.warning(
                self.form, "Missing Name", "Enter a component name first.")
            return False
        rows = self.table.rows()
        if not rows:
            QtWidgets.QMessageBox.warning(
                self.form, "Nothing to Build", "Add at least one solid first.")
            return False
        for _ref, _role, material in rows:
            if not material:
                QtWidgets.QMessageBox.warning(
                    self.form, "Missing Material", "Every row needs a material name.")
                return False

        # Snapshot which (role, material) pairs already existed *before* this
        # accept() so brand-new ones can get their property panel opened
        # afterward (see _open_new_material_panel below).
        existing_before = {
            (role, name)
            for role in ("Dielectric", "Conductor")
            for name in _material_names(self.doc, role)
        }

        if self.container is None:
            container = create_component_from_solids(self.doc, rows, base_name=component_name)
            container.Label = component_name
        else:
            self._apply_edits(rows)
            self.container.Label = component_name

        self._open_new_material_panel(rows, existing_before)

        FreeCADGui.Control.closeDialog()
        return True

    def _apply_edits(self, rows):
        """Reconcile an existing Component's children against the edited table.

        Rows whose ref is still one of the container's current shadows are
        "kept" (their role/material may have changed); any current shadow
        with no matching row is dropped (its master+shadow deleted, and its
        old material group pruned if that leaves it empty); any row whose
        ref is a plain source object (added via "Add Selected" during this
        edit) is a brand-new solid, incorporated via add_solid_pair.

        Dropped indices are removed from Group/ShadowSolids/
        ChildBasePlacements together, preserving the index-alignment
        invariant documented in features/component.py.
        """
        from features.material_group import get_or_create_dielectric_group, get_or_create_conductor_group

        doc = self.doc
        container = self.container

        old_shadows = list(getattr(container, "ShadowSolids", []))
        old_masters = list(getattr(container, "Group", []))
        old_bases = list(getattr(container, "ChildBasePlacements", []))

        kept_refs = {ref for ref, _role, _mat in rows if ref in old_shadows}
        keep_idx = [i for i, s in enumerate(old_shadows) if s in kept_refs]
        drop_idx = [i for i in range(len(old_shadows)) if i not in keep_idx]

        for i in drop_idx:
            doc.removeObject(old_masters[i].Name)
            doc.removeObject(old_shadows[i].Name)
        container.ShadowSolids = [old_shadows[i] for i in keep_idx]
        container.ChildBasePlacements = [old_bases[i] for i in keep_idx]

        for ref, role, material in rows:
            if ref in kept_refs:
                shadow = ref
                cur_role, cur_material = _find_role_and_material(doc, shadow)
                if (cur_role, cur_material) == (role, material):
                    continue
                for g in find_dielectric_groups(doc) + find_conductor_groups(doc):
                    if shadow in g.Group:
                        g.removeObject(shadow)
                        if len(g.Group) == 0:
                            doc.removeObject(g.Name)
                        break
            else:
                source_obj = ref
                _master, shadow = add_solid_pair(
                    doc, container, source_obj.Label, source_obj.Shape.copy(), source_obj.Placement,
                )
            if role == "Dielectric":
                get_or_create_dielectric_group(doc, material, solids=[shadow])
            else:
                get_or_create_conductor_group(doc, material, solids=[shadow])

        doc.recompute()

    def _open_new_material_panel(self, rows, existing_before):
        from panels.material_group_panel import DielectricGroupPanel, ConductorGroupPanel
        from features.material_group import find_dielectric_group_by_material, find_conductor_group_by_material
        from panels import show_palace_panel

        for _ref, role, material in rows:
            if (role, material) in existing_before:
                continue
            finder = find_dielectric_group_by_material if role == "Dielectric" else find_conductor_group_by_material
            grp = finder(self.doc, material)
            if grp is None:
                continue
            panel_cls = DielectricGroupPanel if role == "Dielectric" else ConductorGroupPanel
            show_palace_panel(panel_cls(grp))
            # Only chain the first brand-new material's panel automatically --
            # stacking several task panels back-to-back would bury the user in
            # dialogs; any others can still be edited afterward via the tree.
            return

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            return int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        except TypeError:
            return (QtWidgets.QDialogButtonBox.Ok.value
                    | QtWidgets.QDialogButtonBox.Cancel.value)

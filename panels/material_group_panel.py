import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets


class DielectricGroupPanel:
    def __init__(self, obj):
        self.obj = obj
        self.form = self._build()
        self._populate()

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Dielectric Group")
        fl = QtWidgets.QFormLayout(w)

        self.edit_name = QtWidgets.QLineEdit()
        self.edit_name.setToolTip("Optional label, e.g. 'Silicon substrate'")
        fl.addRow("Material name:", self.edit_name)

        self.lbl_objects = QtWidgets.QLabel("—")
        fl.addRow("Grouped solids:", self.lbl_objects)

        btn_add = QtWidgets.QPushButton("Add selected objects to group")
        btn_add.clicked.connect(self._add_selection)
        fl.addRow("", btn_add)

        self.edit_eps = QtWidgets.QLineEdit()
        self.edit_eps.setToolTip("Relative permittivity εr (dimensionless)")
        fl.addRow("Permittivity εr:", self.edit_eps)

        self.edit_mu = QtWidgets.QLineEdit()
        self.edit_mu.setToolTip("Relative permeability μr (dimensionless)")
        fl.addRow("Permeability μr:", self.edit_mu)

        self.edit_loss = QtWidgets.QLineEdit()
        self.edit_loss.setToolTip("Loss tangent tanδ (0 = lossless)")
        fl.addRow("Loss tangent:", self.edit_loss)

        self.lbl_attr = QtWidgets.QLabel()
        self.lbl_attr.setStyleSheet("color: gray; font-style: italic;")
        fl.addRow("Volume mesh attr:", self.lbl_attr)

        return w

    def _populate(self):
        o = self.obj
        self.edit_name.setText(o.MaterialName or "")
        self.edit_eps.setText(str(o.Permittivity))
        self.edit_mu.setText(str(o.Permeability))
        self.edit_loss.setText(str(o.LossTangent))
        self.lbl_attr.setText(str(o.MeshAttribute))
        self._refresh_objects_label()

    def _refresh_objects_label(self):
        children = list(self.obj.Group) if hasattr(self.obj, "Group") else []
        if children:
            names = ", ".join(c.Label for c in children[:4])
            suffix = f" (+{len(children)-4} more)" if len(children) > 4 else ""
            self.lbl_objects.setText(f"{len(children)} solid(s): {names}{suffix}")
        else:
            self.lbl_objects.setText("No solids — drag objects here in the tree")

    def _add_selection(self):
        added = 0
        existing = set(self.obj.Group) if hasattr(self.obj, "Group") else set()
        for sel in FreeCADGui.Selection.getSelection():
            if sel not in existing and hasattr(sel, "Shape"):
                self.obj.addObject(sel)
                added += 1
        if added:
            self._refresh_objects_label()
        else:
            QtWidgets.QMessageBox.information(
                self.form, "No New Solids",
                "Select solid objects in the 3D view or model tree first."
            )

    def _float(self, widget, fallback):
        try:
            return float(widget.text())
        except ValueError:
            return fallback

    def accept(self):
        o = self.obj
        o.MaterialName = self.edit_name.text().strip()
        o.Permittivity = self._float(self.edit_eps, o.Permittivity)
        o.Permeability = self._float(self.edit_mu, o.Permeability)
        o.LossTangent  = self._float(self.edit_loss, o.LossTangent)
        o.Document.recompute()
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            from PySide2.QtWidgets import QDialogButtonBox
            return int(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        except ImportError:
            from PySide6.QtWidgets import QDialogButtonBox
            return QDialogButtonBox.Ok.value | QDialogButtonBox.Cancel.value


class ConductorGroupPanel:
    def __init__(self, obj):
        self.obj = obj
        # Migrate any old object that predates the Permeability/Thickness properties.
        if hasattr(obj, "Proxy") and hasattr(obj.Proxy, "_init_properties"):
            obj.Proxy._init_properties(obj)
        self.form = self._build()
        self._populate()

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Conductor Group")
        fl = QtWidgets.QFormLayout(w)

        self.edit_name = QtWidgets.QLineEdit()
        self.edit_name.setToolTip("Optional label, e.g. 'Gold trace'")
        fl.addRow("Material name:", self.edit_name)

        self.lbl_objects = QtWidgets.QLabel("—")
        fl.addRow("Grouped solids:", self.lbl_objects)

        btn_add = QtWidgets.QPushButton("Add selected objects to group")
        btn_add.clicked.connect(self._add_selection)
        fl.addRow("", btn_add)

        self.combo_type = QtWidgets.QComboBox()
        self.combo_type.addItems(["PEC", "Lossy Conductor"])
        self.combo_type.currentIndexChanged.connect(self._type_changed)
        fl.addRow("Conductor type:", self.combo_type)

        self.edit_sigma = QtWidgets.QLineEdit()
        self.edit_sigma.setToolTip("Electrical conductivity σ in S/m (e.g. 5.96e7 for copper)")
        fl.addRow("Conductivity (S/m):", self.edit_sigma)

        self.lbl_mu = QtWidgets.QLabel("Permeability μr:")
        self.edit_mu = QtWidgets.QLineEdit()
        self.edit_mu.setToolTip("Relative permeability μr (dimensionless, default 1.0)")
        fl.addRow(self.lbl_mu, self.edit_mu)

        self.lbl_thick = QtWidgets.QLabel("Thickness (m):")
        self.edit_thick = QtWidgets.QLineEdit()
        self.edit_thick.setToolTip("Surface layer thickness in metres for skin-effect model (0 = not set)")
        fl.addRow(self.lbl_thick, self.edit_thick)

        self.lbl_attr = QtWidgets.QLabel()
        self.lbl_attr.setStyleSheet("color: gray; font-style: italic;")
        fl.addRow("Surface mesh attr:", self.lbl_attr)

        return w

    def _populate(self):
        o = self.obj
        self.edit_name.setText(o.MaterialName or "")
        idx = 0 if o.ConductorType == "PEC" else 1
        self.combo_type.setCurrentIndex(idx)
        self.edit_sigma.setText(str(o.Conductivity))
        self.edit_mu.setText(str(getattr(o, "Permeability", 1.0)))
        self.edit_thick.setText(str(getattr(o, "Thickness", 0.0)))
        self.lbl_attr.setText(str(o.MeshAttribute))
        self._refresh_objects_label()
        self._type_changed(idx)

    def _refresh_objects_label(self):
        children = list(self.obj.Group) if hasattr(self.obj, "Group") else []
        if children:
            names = ", ".join(c.Label for c in children[:4])
            suffix = f" (+{len(children)-4} more)" if len(children) > 4 else ""
            self.lbl_objects.setText(f"{len(children)} solid(s): {names}{suffix}")
        else:
            self.lbl_objects.setText("No solids — drag objects here in the tree")

    def _add_selection(self):
        added = 0
        existing = set(self.obj.Group) if hasattr(self.obj, "Group") else set()
        for sel in FreeCADGui.Selection.getSelection():
            if sel not in existing and hasattr(sel, "Shape"):
                self.obj.addObject(sel)
                added += 1
        if added:
            self._refresh_objects_label()
        else:
            QtWidgets.QMessageBox.information(
                self.form, "No New Solids",
                "Select solid objects in the 3D view or model tree first."
            )

    def _type_changed(self, idx):
        lossy = idx == 1
        self.edit_sigma.setEnabled(lossy)
        self.edit_mu.setEnabled(lossy)
        self.lbl_mu.setEnabled(lossy)
        self.edit_thick.setEnabled(lossy)
        self.lbl_thick.setEnabled(lossy)

    def _float(self, widget, fallback):
        try:
            return float(widget.text())
        except ValueError:
            return fallback

    def accept(self):
        o = self.obj
        o.MaterialName  = self.edit_name.text().strip()
        o.ConductorType = self.combo_type.currentText()
        o.Conductivity  = self._float(self.edit_sigma, o.Conductivity)
        o.Permeability  = self._float(self.edit_mu, getattr(o, "Permeability", 1.0))
        o.Thickness     = self._float(self.edit_thick, getattr(o, "Thickness", 0.0))
        o.Document.recompute()
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            from PySide2.QtWidgets import QDialogButtonBox
            return int(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        except ImportError:
            from PySide6.QtWidgets import QDialogButtonBox
            return QDialogButtonBox.Ok.value | QDialogButtonBox.Cancel.value

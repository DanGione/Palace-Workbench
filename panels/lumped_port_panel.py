import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

from panels.unit_widgets import (
    R_UNITS as _R_UNITS, L_UNITS as _L_UNITS, C_UNITS as _C_UNITS,
    set_unit_field as _set_unit_field, read_si_field as _read_si_field,
    rescale_on_unit_change as _rescale_on_unit_change, make_unit_row as _make_unit_row,
)

_ALL_DIRECTIONS = ["+R", "-R", "X", "-X", "Y", "-Y", "Z", "-Z"]


def _detect_face_geometry(faces):
    """Inspect selected faces to classify the port geometry.

    Returns 'annular', 'planar', or 'unknown'.
    """
    for shape_obj, subnames in faces:
        if isinstance(subnames, str):
            subnames = [subnames]
        for subname in subnames:
            try:
                face = shape_obj.Shape.getElement(subname)
                wires = face.Wires
                if len(wires) == 2:
                    if all(
                        all(
                            type(e.Curve).__name__ in ("Circle", "ArcOfCircle")
                            for e in w.Edges
                            if hasattr(e, "Curve")
                        )
                        for w in wires
                    ):
                        return "annular"
                    return "planar"
                elif len(wires) == 1:
                    return "planar"
            except Exception:
                pass
    return "unknown"


class LumpedPortPanel:
    def __init__(self, obj):
        self.obj = obj

        # Edge-pair mode state: flat list of (obj, sub_name) pairs
        self.edge_refs = []
        if hasattr(obj, "PortEdges") and obj.PortEdges:
            for link_obj, subs in obj.PortEdges:
                for sub in (subs or []):
                    self.edge_refs.append((link_obj, sub))

        # Face-selection mode state
        self.faces = list(obj.PortFaces) if obj.PortFaces else []

        self.form = self._build()
        self._populate()

        # Refresh geometry label without overriding the saved direction
        if self.edge_refs or self.faces:
            self._update_geom_label(auto_set_direction=False)

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Lumped Port Settings")
        fl = QtWidgets.QFormLayout(w)

        self.spin_index = QtWidgets.QSpinBox()
        self.spin_index.setRange(1, 99)
        fl.addRow("Port index:", self.spin_index)

        # --- Edge-pair mode ---
        self.lbl_edges = QtWidgets.QLabel("No edges selected")
        fl.addRow("Port edges:", self.lbl_edges)

        btn_edges = QtWidgets.QPushButton("Use current edge selection (2 edges)")
        btn_edges.clicked.connect(self._pick_edges)
        fl.addRow("", btn_edges)

        # --- Face mode (legacy / manual) ---
        self.lbl_faces = QtWidgets.QLabel("No faces selected")
        fl.addRow("Port faces:", self.lbl_faces)

        btn_faces = QtWidgets.QPushButton("Use current face selection")
        btn_faces.clicked.connect(self._pick_faces)
        fl.addRow("", btn_faces)

        # --- Detected geometry ---
        self.lbl_geom = QtWidgets.QLabel("—")
        self.lbl_geom.setStyleSheet("color: gray; font-style: italic;")
        fl.addRow("Detected geometry:", self.lbl_geom)

        self.combo_dir = QtWidgets.QComboBox()
        self.combo_dir.addItems(_ALL_DIRECTIONS)
        self.combo_dir.setToolTip(
            "+R / -R for coaxial (annular) ports; X / Y / Z for rectangular planar ports"
        )
        fl.addRow("Direction:", self.combo_dir)

        # --- R / L / C with unit selectors ---
        self.edit_r  = QtWidgets.QLineEdit()
        self.combo_r = QtWidgets.QComboBox()
        self.edit_r.setToolTip("Port resistance")
        fl.addRow("R:", _make_unit_row(self.edit_r, self.combo_r, _R_UNITS))

        self.edit_l  = QtWidgets.QLineEdit()
        self.combo_l = QtWidgets.QComboBox()
        self.edit_l.setToolTip("Port inductance (0 to omit)")
        fl.addRow("L:", _make_unit_row(self.edit_l, self.combo_l, _L_UNITS))

        self.edit_c  = QtWidgets.QLineEdit()
        self.combo_c = QtWidgets.QComboBox()
        self.edit_c.setToolTip("Port capacitance (0 to omit)")
        fl.addRow("C:", _make_unit_row(self.edit_c, self.combo_c, _C_UNITS))

        # Connect unit-change re-scaling
        self.combo_r.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_r, self.combo_r, _R_UNITS, new))
        self.combo_l.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_l, self.combo_l, _L_UNITS, new))
        self.combo_c.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_c, self.combo_c, _C_UNITS, new))

        self.chk_excite = QtWidgets.QCheckBox("Excite this port")
        fl.addRow("Excitation:", self.chk_excite)

        return w

    def _populate(self):
        o = self.obj
        self.spin_index.setValue(o.PortIndex)
        self._refresh_edge_label()
        self._refresh_face_label()
        cur = o.Direction if hasattr(o, "Direction") and o.Direction in _ALL_DIRECTIONS else "+R"
        self.combo_dir.setCurrentIndex(_ALL_DIRECTIONS.index(cur))
        _set_unit_field(self.edit_r, self.combo_r, o.R, _R_UNITS)
        _set_unit_field(self.edit_l, self.combo_l, o.L, _L_UNITS)
        _set_unit_field(self.edit_c, self.combo_c, o.C, _C_UNITS)
        self.chk_excite.setChecked(o.Excitation)

    def _refresh_edge_label(self):
        n = len(self.edge_refs)
        self.lbl_edges.setText(f"{n} edge(s) selected" if n else "No edges selected")

    def _refresh_face_label(self):
        n = sum(len(subs) if isinstance(subs, (list, tuple)) else 1
                for _, subs in self.faces)
        self.lbl_faces.setText(f"{n} face(s) selected" if n else "No faces selected")

    def _update_geom_label(self, auto_set_direction=True):
        geom = "unknown"

        if len(self.edge_refs) == 2:
            try:
                edges = [ref_obj.Shape.getElement(sub) for ref_obj, sub in self.edge_refs]
                from palace.port_geometry import classify_edge_pair
                geom, *_ = classify_edge_pair(edges[0], edges[1])
            except Exception:
                pass
        elif self.faces:
            geom = _detect_face_geometry(self.faces)

        if geom == "annular":
            self.lbl_geom.setText("Annular (coaxial) — use +R or -R direction")
            self.lbl_geom.setStyleSheet("color: #229944; font-style: italic;")
            if auto_set_direction and self.combo_dir.currentText() not in ("+R", "-R"):
                self.combo_dir.setCurrentIndex(_ALL_DIRECTIONS.index("+R"))
        elif geom == "planar":
            self.lbl_geom.setText("Planar — use X / Y / Z direction")
            self.lbl_geom.setStyleSheet("color: #2277aa; font-style: italic;")
            if auto_set_direction and self.combo_dir.currentText() in ("+R", "-R"):
                self.combo_dir.setCurrentIndex(_ALL_DIRECTIONS.index("X"))
        else:
            self.lbl_geom.setText("—")
            self.lbl_geom.setStyleSheet("color: gray; font-style: italic;")

    def _pick_edges(self):
        refs = []
        for s in FreeCADGui.Selection.getSelectionEx():
            for sub in (s.SubElementNames or []):
                if sub.startswith("Edge"):
                    refs.append((s.Object, sub))
        if len(refs) < 2:
            QtWidgets.QMessageBox.warning(
                self.form, "Need 2 Edges",
                "Select exactly 2 edges in the 3D view first.",
            )
            return
        self.edge_refs = refs[:2]
        self._refresh_edge_label()
        self._update_geom_label(auto_set_direction=True)

    def _pick_faces(self):
        sel = FreeCADGui.Selection.getSelectionEx()
        faces = []
        for s in sel:
            subs = [n for n in (s.SubElementNames or []) if n.startswith("Face")]
            if subs:
                faces.append((s.Object, subs))
        if not faces:
            QtWidgets.QMessageBox.warning(
                self.form, "No Face Selection",
                "Select one or more faces in the 3D view first.",
            )
            return
        self.faces = faces
        self._refresh_face_label()
        self._update_geom_label(auto_set_direction=True)

    def _float(self, widget, fallback):
        try:
            return float(widget.text())
        except ValueError:
            return fallback

    def accept(self):
        o = self.obj
        o.PortIndex = self.spin_index.value()
        o.MeshAttribute = 10 + o.PortIndex
        o.Direction = self.combo_dir.currentText()
        o.R = _read_si_field(self.edit_r, self.combo_r, _R_UNITS, o.R)
        o.L = _read_si_field(self.edit_l, self.combo_l, _L_UNITS, o.L)
        o.C = _read_si_field(self.edit_c, self.combo_c, _C_UNITS, o.C)
        o.Excitation = self.chk_excite.isChecked()

        if self.edge_refs:
            # Group by object for PropertyLinkSubList
            by_obj = {}
            for ref_obj, sub in self.edge_refs:
                key = id(ref_obj)
                if key not in by_obj:
                    by_obj[key] = (ref_obj, [])
                by_obj[key][1].append(sub)
            o.PortEdges = [(ref_obj, subs) for ref_obj, subs in by_obj.values()]
        else:
            o.PortFaces = self.faces

        from features.port_group import sync_port_group
        sync_port_group(o)
        o.Document.recompute()
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            return int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        except TypeError:
            return (QtWidgets.QDialogButtonBox.Ok.value
                    | QtWidgets.QDialogButtonBox.Cancel.value)

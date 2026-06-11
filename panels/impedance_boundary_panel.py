import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtWidgets, QtCore
except ImportError:
    from PySide6 import QtWidgets, QtCore

_ALL_DIRECTIONS = ["+R", "-R", "X", "-X", "Y", "-Y", "Z", "-Z"]

# ---------------------------------------------------------------------------
# Unit sets — (display_label, SI_scale)   SI_value = display_value × SI_scale
# ---------------------------------------------------------------------------
_R_UNITS  = [("Ω",     1.0),  ("mΩ",     1e-3), ("kΩ",     1e3)]
_L_UNITS  = [("H",     1.0),  ("mH",     1e-3), ("μH",     1e-6), ("nH",     1e-9), ("pH",     1e-12)]
_C_UNITS  = [("F",     1.0),  ("mF",     1e-3), ("μF",     1e-6), ("nF",     1e-9), ("pF",     1e-12)]
_RS_UNITS = [("Ω/sq",  1.0),  ("mΩ/sq",  1e-3), ("kΩ/sq",  1e3)]
_LS_UNITS = [("H/sq",  1.0),  ("mH/sq",  1e-3), ("μH/sq",  1e-6), ("nH/sq",  1e-9), ("pH/sq",  1e-12)]
_CS_UNITS = [("F/sq",  1.0),  ("mF/sq",  1e-3), ("μF/sq",  1e-6), ("nF/sq",  1e-9), ("pF/sq",  1e-12)]


def _best_unit_idx(si_value, units):
    """Return the index of the most readable unit for si_value."""
    if si_value == 0.0:
        return 0
    for i, (_, scale) in enumerate(units):
        if 0.1 <= abs(si_value) / scale < 1000:
            return i
    return 0


def _set_unit_field(edit, combo, si_value, units):
    """Populate edit+combo from a SI value, choosing the best unit."""
    idx = _best_unit_idx(si_value, units)
    combo.blockSignals(True)
    combo.setCurrentIndex(idx)
    combo.setProperty("_prev_idx", idx)
    combo.blockSignals(False)
    edit.setText(f"{si_value / units[idx][1]:.6g}")


def _read_si_field(edit, combo, units, fallback):
    """Read the SI value from edit+combo; return fallback on invalid input."""
    try:
        return float(edit.text()) * units[combo.currentIndex()][1]
    except ValueError:
        return fallback


def _rescale_on_unit_change(edit, combo, units, new_idx):
    """Re-scale the displayed value when the unit selection changes."""
    prev_idx = combo.property("_prev_idx")
    if prev_idx is None:
        prev_idx = 0
    try:
        si = float(edit.text()) * units[prev_idx][1]
    except (ValueError, TypeError):
        si = 0.0
    combo.setProperty("_prev_idx", new_idx)
    edit.setText(f"{si / units[new_idx][1]:.6g}")


def _make_unit_row(edit, combo, units):
    """Return a QWidget containing edit + unit combo side by side."""
    for name, _ in units:
        combo.addItem(name)
    combo.setFixedWidth(80)
    w = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(edit)
    lay.addWidget(combo)
    return w


def _detect_face_geometry(faces):
    """Inspect selected faces to classify port geometry ('annular', 'planar', 'unknown')."""
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


class ImpedanceBoundaryPanel:
    def __init__(self, obj):
        self.obj = obj

        # Edge-pair mode state
        self.edge_refs = []
        if hasattr(obj, "PortEdges") and obj.PortEdges:
            for link_obj, subs in obj.PortEdges:
                for sub in (subs or []):
                    self.edge_refs.append((link_obj, sub))

        # Face-selection mode state
        self.faces = list(obj.PortFaces) if obj.PortFaces else []

        self.form = self._build()
        self._populate()

        if self.edge_refs or self.faces:
            self._update_geom_label(auto_set_direction=False)

        self._refresh_mode()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Impedance Boundary Settings")
        fl = QtWidgets.QFormLayout(w)

        self.spin_index = QtWidgets.QSpinBox()
        self.spin_index.setRange(1, 99)
        fl.addRow("Boundary index:", self.spin_index)

        # --- Edge-pair mode ---
        self.lbl_edges = QtWidgets.QLabel("No edges selected")
        fl.addRow("Boundary edges:", self.lbl_edges)

        btn_edges = QtWidgets.QPushButton("Use current edge selection (2 edges)")
        btn_edges.clicked.connect(self._pick_edges)
        fl.addRow("", btn_edges)

        # --- Face mode ---
        self.lbl_faces = QtWidgets.QLabel("No faces selected")
        fl.addRow("Boundary faces:", self.lbl_faces)

        btn_faces = QtWidgets.QPushButton("Use current face selection")
        btn_faces.clicked.connect(self._pick_faces)
        fl.addRow("", btn_faces)

        # --- Detected geometry ---
        self.lbl_geom = QtWidgets.QLabel("—")
        self.lbl_geom.setStyleSheet("color: gray; font-style: italic;")
        fl.addRow("Detected geometry:", self.lbl_geom)

        self.combo_dir = QtWidgets.QComboBox()
        self.combo_dir.addItems(_ALL_DIRECTIONS)
        fl.addRow("Direction:", self.combo_dir)

        # --- Aspect ratio ---
        self.lbl_ar = QtWidgets.QLabel("—")
        self.lbl_ar.setStyleSheet("color: gray;")
        fl.addRow("Aspect ratio (length/width):", self.lbl_ar)

        # --- Mode toggle ---
        self.chk_surface_mode = QtWidgets.QCheckBox("Set surface impedance directly")
        self.chk_surface_mode.stateChanged.connect(self._refresh_mode)
        fl.addRow("Input mode:", self.chk_surface_mode)

        fl.addRow(QtWidgets.QFrame())   # spacer

        # --- Total values (R/L/C) with unit selectors ---
        self.edit_r  = QtWidgets.QLineEdit()
        self.combo_r = QtWidgets.QComboBox()
        self.edit_r.setToolTip("Total resistance across the boundary")
        fl.addRow("R:", _make_unit_row(self.edit_r, self.combo_r, _R_UNITS))

        self.edit_l  = QtWidgets.QLineEdit()
        self.combo_l = QtWidgets.QComboBox()
        self.edit_l.setToolTip("Total inductance across the boundary")
        fl.addRow("L:", _make_unit_row(self.edit_l, self.combo_l, _L_UNITS))

        self.edit_c  = QtWidgets.QLineEdit()
        self.combo_c = QtWidgets.QComboBox()
        self.edit_c.setToolTip("Total capacitance across the boundary")
        fl.addRow("C:", _make_unit_row(self.edit_c, self.combo_c, _C_UNITS))

        # --- Surface values (Rs/Ls/Cs) with unit selectors ---
        self.edit_rs  = QtWidgets.QLineEdit()
        self.combo_rs = QtWidgets.QComboBox()
        self.edit_rs.setToolTip("Surface resistance (per square)")
        fl.addRow("Rs:", _make_unit_row(self.edit_rs, self.combo_rs, _RS_UNITS))

        self.edit_ls  = QtWidgets.QLineEdit()
        self.combo_ls = QtWidgets.QComboBox()
        self.edit_ls.setToolTip("Surface inductance (per square)")
        fl.addRow("Ls:", _make_unit_row(self.edit_ls, self.combo_ls, _LS_UNITS))

        self.edit_cs  = QtWidgets.QLineEdit()
        self.combo_cs = QtWidgets.QComboBox()
        self.edit_cs.setToolTip("Surface capacitance (per square)")
        fl.addRow("Cs:", _make_unit_row(self.edit_cs, self.combo_cs, _CS_UNITS))

        # Connect unit-change re-scaling for all six fields
        self.combo_r.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_r, self.combo_r, _R_UNITS, new))
        self.combo_l.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_l, self.combo_l, _L_UNITS, new))
        self.combo_c.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_c, self.combo_c, _C_UNITS, new))
        self.combo_rs.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_rs, self.combo_rs, _RS_UNITS, new))
        self.combo_ls.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_ls, self.combo_ls, _LS_UNITS, new))
        self.combo_cs.currentIndexChanged.connect(
            lambda new: _rescale_on_unit_change(self.edit_cs, self.combo_cs, _CS_UNITS, new))

        return w

    # ------------------------------------------------------------------
    # Populate from object
    # ------------------------------------------------------------------

    def _populate(self):
        o = self.obj
        self.spin_index.setValue(o.PortIndex)
        self._refresh_edge_label()
        self._refresh_face_label()

        cur_dir = (o.Direction if hasattr(o, "Direction") and o.Direction in _ALL_DIRECTIONS
                   else "+R")
        self.combo_dir.setCurrentIndex(_ALL_DIRECTIONS.index(cur_dir))

        ar = getattr(o, "AspectRatio", 0.0)
        self.lbl_ar.setText(f"{ar:.4g}" if ar > 0 else "—")

        self.chk_surface_mode.setChecked(bool(getattr(o, "SurfaceMode", False)))

        _set_unit_field(self.edit_r,  self.combo_r,  getattr(o, "R",  50.0), _R_UNITS)
        _set_unit_field(self.edit_l,  self.combo_l,  getattr(o, "L",   0.0), _L_UNITS)
        _set_unit_field(self.edit_c,  self.combo_c,  getattr(o, "C",   0.0), _C_UNITS)
        _set_unit_field(self.edit_rs, self.combo_rs, getattr(o, "Rs",  0.0), _RS_UNITS)
        _set_unit_field(self.edit_ls, self.combo_ls, getattr(o, "Ls",  0.0), _LS_UNITS)
        _set_unit_field(self.edit_cs, self.combo_cs, getattr(o, "Cs",  0.0), _CS_UNITS)

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _refresh_mode(self):
        surface_mode = self.chk_surface_mode.isChecked()

        # Total inputs: enabled in Total mode, read-only in Surface mode
        for w in (self.edit_r, self.edit_l, self.edit_c):
            w.setEnabled(not surface_mode)
            w.setStyleSheet("" if not surface_mode else "color: gray;")
        for w in (self.combo_r, self.combo_l, self.combo_c):
            w.setEnabled(not surface_mode)

        # Surface inputs: enabled in Surface mode, read-only in Total mode
        for w in (self.edit_rs, self.edit_ls, self.edit_cs):
            w.setEnabled(surface_mode)
            w.setStyleSheet("" if surface_mode else "color: gray;")
        for w in (self.combo_rs, self.combo_ls, self.combo_cs):
            w.setEnabled(surface_mode)

    # ------------------------------------------------------------------
    # Geometry detection helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Selection pickers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Accept / reject
    # ------------------------------------------------------------------

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
        o.SurfaceMode = self.chk_surface_mode.isChecked()

        if o.SurfaceMode:
            o.Rs = _read_si_field(self.edit_rs, self.combo_rs, _RS_UNITS, o.Rs)
            o.Ls = _read_si_field(self.edit_ls, self.combo_ls, _LS_UNITS, o.Ls)
            o.Cs = _read_si_field(self.edit_cs, self.combo_cs, _CS_UNITS, o.Cs)
        else:
            o.R = _read_si_field(self.edit_r, self.combo_r, _R_UNITS, o.R)
            o.L = _read_si_field(self.edit_l, self.combo_l, _L_UNITS, o.L)
            o.C = _read_si_field(self.edit_c, self.combo_c, _C_UNITS, o.C)

        if self.edge_refs:
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

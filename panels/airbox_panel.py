import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

_BOUNDARY_TYPES = ["PEC", "PMC", "Absorbing"]

# Maps property name → BC type string
_FACE_PROPS = [
    ("PECFaces",       "PEC"),
    ("PMCFaces",       "PMC"),
    ("AbsorbingFaces", "Absorbing"),
]


class _FaceSelectionObserver:
    """Caches face sub-element selections so the Assign button can read them
    even if a button click causes FreeCAD to briefly clear the 3D selection.

    clearSelection sets a flag rather than clearing immediately; _assign_faces()
    resets the cache after reading it, so the button handler always sees the
    last-selected faces regardless of Qt event-loop ordering.

    FreeCAD may pass compound sub-element names like "PalaceAirbox.Box.Face1"
    when the solid lives inside a group container. We strip to the terminal
    component before storing so the rest of the code only sees plain "FaceN".
    """

    def __init__(self):
        self.faces = set()   # set of (obj_name, bare_subname) tuples
        self._pending_clear = False

    def addSelection(self, doc, obj, sub, pnt=None):
        # If a clearSelection fired since we last read the cache, discard the
        # stale entries before adding the new face.  This prevents faces from a
        # previous selection sequence from leaking into the next one.
        if self._pending_clear:
            self.faces.clear()
            self._pending_clear = False
        bare = sub.split(".")[-1]
        if bare.startswith("Face"):
            self.faces.add((obj, bare))

    def removeSelection(self, doc, obj, sub, pnt=None):
        bare = sub.split(".")[-1]
        self.faces.discard((obj, bare))

    def clearSelection(self, doc):
        # Mark the cache as stale.  We intentionally do NOT clear self.faces
        # here: if a button click triggered this clearSelection, the Assign
        # handler needs to read the cache one last time before it goes away.
        # The actual clear happens in _assign_faces() or at the next addSelection.
        self._pending_clear = True

    def consume(self):
        """Read and immediately invalidate the cache.  Called by _assign_faces()."""
        result = set(self.faces)
        self.faces.clear()
        self._pending_clear = False
        return result

    def setPreselection(self, doc, obj, sub, pnt=None):
        pass

    def removePreselection(self, doc, obj, sub, pnt=None):
        pass


class AirboxPanel:
    def __init__(self, obj):
        self.obj = obj
        # face_subname → BC type string
        self._overrides: dict = {}
        self.form = self._build()
        self._populate()

        # Register selection observer so Assign button can read faces even
        # if the button click clears the 3D-view selection before we query it.
        self._observer = _FaceSelectionObserver()
        FreeCADGui.Selection.addObserver(self._observer)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Airbox Settings")
        layout = QtWidgets.QVBoxLayout(w)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_perface_tab(), "Per-Face BCs")
        layout.addWidget(tabs)

        return w

    def _build_general_tab(self):
        tab = QtWidgets.QWidget()
        fl = QtWidgets.QFormLayout(tab)

        self.lbl_shape = QtWidgets.QLabel("(none)")
        fl.addRow("Simulation domain:", self.lbl_shape)

        btn_sel = QtWidgets.QPushButton("Use current 3D selection")
        btn_sel.clicked.connect(self._pick_shape)
        fl.addRow("", btn_sel)

        self.combo_boundary = QtWidgets.QComboBox()
        self.combo_boundary.addItems(_BOUNDARY_TYPES)
        self.combo_boundary.setToolTip(
            "Default boundary condition for outer airbox faces not explicitly assigned"
        )
        fl.addRow("Default outer BC:", self.combo_boundary)

        return tab

    def _build_perface_tab(self):
        tab = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(tab)

        lbl = QtWidgets.QLabel(
            "Select faces on the airbox solid in the 3D view, choose a BC type, "
            "then click Assign. Click a row to re-select that face in the 3D view."
        )
        lbl.setWordWrap(True)
        vl.addWidget(lbl)

        self.face_table = QtWidgets.QTableWidget(0, 2)
        self.face_table.setHorizontalHeaderLabels(["Face", "BC Type"])
        self.face_table.horizontalHeader().setStretchLastSection(True)
        self.face_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.face_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.face_table.itemClicked.connect(self._on_row_clicked)
        vl.addWidget(self.face_table)

        btn_row = QtWidgets.QHBoxLayout()

        btn_row.addWidget(QtWidgets.QLabel("BC type:"))

        self.combo_face_bc = QtWidgets.QComboBox()
        self.combo_face_bc.addItems(_BOUNDARY_TYPES)
        btn_row.addWidget(self.combo_face_bc)

        btn_assign = QtWidgets.QPushButton("Assign from 3D selection")
        btn_assign.clicked.connect(self._assign_faces)
        btn_row.addWidget(btn_assign)

        btn_remove = QtWidgets.QPushButton("Remove selected")
        btn_remove.clicked.connect(self._remove_face)
        btn_row.addWidget(btn_remove)

        btn_row.addStretch()
        vl.addLayout(btn_row)

        return tab

    # ------------------------------------------------------------------
    # Populate from object
    # ------------------------------------------------------------------

    def _populate(self):
        o = self.obj
        child = getattr(o, "Group", [None])[0] if getattr(o, "Group", None) else None
        if child:
            self.lbl_shape.setText(child.Label)

        btype = o.OuterBoundaryType
        if btype in _BOUNDARY_TYPES:
            self.combo_boundary.setCurrentIndex(_BOUNDARY_TYPES.index(btype))

        # Load per-face overrides from the three PropertyLinkSubList properties.
        self._overrides = {}
        for prop_name, bc_type in _FACE_PROPS:
            for _link_obj, subnames in (getattr(o, prop_name, None) or []):
                for sub in (subnames or []):
                    self._overrides[sub] = bc_type

        self._refresh_table()

    # ------------------------------------------------------------------
    # Per-face table helpers
    # ------------------------------------------------------------------

    def _refresh_table(self):
        self.face_table.setRowCount(0)
        for subname, bc_type in sorted(self._overrides.items()):
            row = self.face_table.rowCount()
            self.face_table.insertRow(row)
            self.face_table.setItem(row, 0, QtWidgets.QTableWidgetItem(subname))
            self.face_table.setItem(row, 1, QtWidgets.QTableWidgetItem(bc_type))

    def _assign_faces(self):
        solid = getattr(self.obj, "Group", [None])[0] if getattr(self.obj, "Group", None) else None
        if solid is None:
            QtWidgets.QMessageBox.warning(
                FreeCADGui.getMainWindow(), "No Airbox Solid",
                "Assign a simulation domain solid on the General tab first."
            )
            return

        bc_type = self.combo_face_bc.currentText()
        # Accept face selections on the solid itself OR on its container.  When a
        # solid lives inside App::GroupExtensionPython, FreeCAD may report the
        # selected object as the container ("PalaceAirbox") rather than the inner
        # solid ("Box"), so we accept both.
        valid_names = {solid.Name, self.obj.Name}
        added = 0

        sel = FreeCADGui.Selection.getSelectionEx()

        # Try live selection first.
        for sel_obj in sel:
            if sel_obj.Object.Name not in valid_names:
                continue
            for subname in (sel_obj.SubElementNames or []):
                # Strip compound paths like "PalaceAirbox.Box.Face1" → "Face1"
                bare = subname.split(".")[-1]
                if bare.startswith("Face"):
                    self._overrides[bare] = bc_type
                    added += 1

        # Fall back to the observer cache in case a button-click triggered a
        # selection clear before getSelectionEx() ran (Theory D).
        # consume() always clears the cache, preventing stale faces from
        # accumulating across multiple Assign operations.
        if added == 0:
            for _obj_name, bare in self._observer.consume():
                self._overrides[bare] = bc_type
                added += 1
        else:
            self._observer.consume()  # discard stale cache even when live sel worked

        if added == 0:
            QtWidgets.QMessageBox.information(
                FreeCADGui.getMainWindow(), "No Faces Selected",
                "Select one or more faces of the airbox solid in the 3D view first."
            )
            return

        self._refresh_table()

    def _remove_face(self):
        rows = self.face_table.selectionModel().selectedRows()
        for idx in sorted(rows, reverse=True):
            subname = self.face_table.item(idx.row(), 0).text()
            self._overrides.pop(subname, None)
        self._refresh_table()

    def _on_row_clicked(self, item):
        subname = self.face_table.item(item.row(), 0).text()
        solid = getattr(self.obj, "Group", [None])[0] if getattr(self.obj, "Group", None) else None
        if solid is None:
            return
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(solid, subname)

    # ------------------------------------------------------------------
    # General tab helpers
    # ------------------------------------------------------------------

    def _pick_shape(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            QtWidgets.QMessageBox.warning(
                FreeCADGui.getMainWindow(), "No Selection",
                "Select a solid in the 3D view first, then click this button."
            )
            return
        if getattr(self.obj, "Group", None):
            self.obj.Group = []
        self.obj.addObject(sel[0])
        self.lbl_shape.setText(sel[0].Label)

    # ------------------------------------------------------------------
    # Accept / Reject
    # ------------------------------------------------------------------

    def _cleanup_observer(self):
        try:
            FreeCADGui.Selection.removeObserver(self._observer)
        except Exception:
            pass

    def accept(self):
        self._cleanup_observer()
        o = self.obj

        # Tab 1 — default BC
        o.OuterBoundaryType = _BOUNDARY_TYPES[self.combo_boundary.currentIndex()]

        # Tab 2 — per-face overrides: group by BC type → PropertyLinkSubList format
        solid = getattr(o, "Group", [None])[0] if getattr(o, "Group", None) else None
        bc_to_faces: dict = {bt: [] for bt in _BOUNDARY_TYPES}
        for subname, bc_type in self._overrides.items():
            bc_to_faces[bc_type].append(subname)

        for prop_name, bc_type in _FACE_PROPS:
            faces = bc_to_faces[bc_type]
            if faces and solid is not None:
                setattr(o, prop_name, [(solid, faces)])
            else:
                setattr(o, prop_name, [])

        o.Document.recompute()
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        self._cleanup_observer()
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            return int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        except TypeError:
            return (QtWidgets.QDialogButtonBox.Ok.value
                    | QtWidgets.QDialogButtonBox.Cancel.value)

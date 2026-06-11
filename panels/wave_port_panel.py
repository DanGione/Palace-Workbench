import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets


class WavePortPanel:
    def __init__(self, obj):
        self.obj       = obj
        self.faces     = list(obj.PortFaces) if obj.PortFaces else []
        self.edge_ref  = None   # (link_obj, sub_name) or None
        self.form      = self._build()
        self._populate()

    def _build(self):
        w  = QtWidgets.QWidget()
        w.setWindowTitle("Palace Wave Port Settings")
        fl = QtWidgets.QFormLayout(w)

        self.spin_index = QtWidgets.QSpinBox()
        self.spin_index.setRange(1, 99)
        fl.addRow("Port index:", self.spin_index)

        self.lbl_faces = QtWidgets.QLabel("No faces selected")
        fl.addRow("Port faces:", self.lbl_faces)

        btn_faces = QtWidgets.QPushButton("Use current face selection")
        btn_faces.clicked.connect(self._pick_faces)
        fl.addRow("", btn_faces)

        self.lbl_edge = QtWidgets.QLabel("not set")
        fl.addRow("Integration edge:", self.lbl_edge)

        btn_edge = QtWidgets.QPushButton("Use current edge selection")
        btn_edge.clicked.connect(self._pick_edge)
        fl.addRow("", btn_edge)

        self.spin_modes = QtWidgets.QSpinBox()
        self.spin_modes.setRange(1, 50)
        self.spin_modes.setToolTip("Number of modal modes to include in the port expansion")
        fl.addRow("Number of modes:", self.spin_modes)

        self.chk_excite = QtWidgets.QCheckBox("Excite this port")
        fl.addRow("Excitation:", self.chk_excite)

        self.lbl_char_z = QtWidgets.QLabel("50.00 Ω")
        self.lbl_char_z.setToolTip(
            "Characteristic impedance extracted from the field solution — "
            "auto-updated after each simulation run"
        )
        fl.addRow("Characteristic Z:", self.lbl_char_z)

        self.spin_renorm_z = QtWidgets.QDoubleSpinBox()
        self.spin_renorm_z.setRange(0.01, 10000.0)
        self.spin_renorm_z.setSingleStep(1.0)
        self.spin_renorm_z.setDecimals(2)
        self.spin_renorm_z.setSuffix(" Ω")
        self.spin_renorm_z.setToolTip(
            "Target impedance for S-parameter renormalization — set by user"
        )
        fl.addRow("Renorm target Z:", self.spin_renorm_z)

        return w

    def _populate(self):
        o = self.obj
        self.spin_index.setValue(o.PortIndex)
        self._refresh_face_label()

        ie = getattr(o, "IntegrationEdge", None)
        if ie and ie[0] and ie[1]:
            self.edge_ref = (ie[0], ie[1][0])
            self.lbl_edge.setText(ie[1][0])
        else:
            self.edge_ref = None
            self.lbl_edge.setText("not set")

        self.spin_modes.setValue(o.NumModes)
        self.chk_excite.setChecked(o.Excitation)

        char_z = getattr(o, "CharacteristicZ", 50.0)
        self.lbl_char_z.setText(f"{char_z:.2f} Ω")

        renorm_z = getattr(o, "RenormZ", 50.0)
        self.spin_renorm_z.setValue(renorm_z)

    def _refresh_face_label(self):
        n = sum(len(subs) for _, subs in self.faces)
        self.lbl_faces.setText(f"{n} face(s) selected" if n else "No faces selected")

    def _pick_faces(self):
        sel   = FreeCADGui.Selection.getSelectionEx()
        faces = []
        for s in sel:
            subs = [n for n in (s.SubElementNames or []) if n.startswith("Face")]
            if subs:
                faces.append((s.Object, subs))
        if not faces:
            QtWidgets.QMessageBox.warning(
                self.form, "No Face Selection",
                "Select one or more faces in the 3D view first."
            )
            return
        self.faces = faces
        self._refresh_face_label()

    def _pick_edge(self):
        sel = FreeCADGui.Selection.getSelectionEx()
        edges = []
        for s in sel:
            for sub in (s.SubElementNames or []):
                if sub.startswith("Edge"):
                    edges.append((s.Object, sub))
        if len(edges) != 1:
            QtWidgets.QMessageBox.warning(
                self.form, "Select Exactly One Edge",
                "Select a single edge on the port face from the inner conductor "
                "to the outer conductor, then click this button."
            )
            return
        self.edge_ref = edges[0]
        self.lbl_edge.setText(edges[0][1])

    def accept(self):
        o            = self.obj
        o.PortIndex  = self.spin_index.value()
        o.MeshAttribute = 10 + o.PortIndex
        o.PortFaces  = self.faces
        o.NumModes   = self.spin_modes.value()
        o.Excitation = self.chk_excite.isChecked()
        o.RenormZ    = self.spin_renorm_z.value()
        if self.edge_ref is not None:
            link_obj, sub_name = self.edge_ref
            o.IntegrationEdge = (link_obj, [sub_name])
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

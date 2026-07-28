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
            "Characteristic impedance magnitude, mean of |Z| across frequency — "
            "Palace's native Z_PV (from VoltagePath) when an Integration edge "
            "is set, otherwise the TE/TM Poynting-flux impedance — "
            "auto-updated after each simulation run"
        )
        fl.addRow("Characteristic |Z|:", self.lbl_char_z)

        self.lbl_char_z_complex = QtWidgets.QLabel("50.00 + j0.00 Ω")
        self.lbl_char_z_complex.setToolTip(
            "Characteristic impedance as a complex number (mean of Re(Z)/Im(Z) "
            "across frequency) — auto-updated after each simulation run"
        )
        fl.addRow("Characteristic Z:", self.lbl_char_z_complex)

        self.spin_renorm_z = QtWidgets.QDoubleSpinBox()
        self.spin_renorm_z.setRange(0.01, 10000.0)
        self.spin_renorm_z.setSingleStep(1.0)
        self.spin_renorm_z.setDecimals(2)
        self.spin_renorm_z.setSuffix(" Ω")
        self.spin_renorm_z.setToolTip(
            "Target impedance for S-parameter renormalization — set by user"
        )
        fl.addRow("Renorm target Z:", self.spin_renorm_z)

        adv_label = QtWidgets.QLabel("<b>Advanced (Solver)</b>")
        fl.addRow(adv_label)

        self.spin_max_its = QtWidgets.QSpinBox()
        self.spin_max_its.setRange(0, 10000)
        self.spin_max_its.setSpecialValueText("Auto")
        self.spin_max_its.setToolTip(
            "Krylov solver max iterations for the wave port eigenmode/"
            "voltage-path solve — 0 = Palace default"
        )
        fl.addRow("Max iterations:", self.spin_max_its)

        self.spin_ksp_tol = QtWidgets.QDoubleSpinBox()
        self.spin_ksp_tol.setRange(0.0, 1.0)
        self.spin_ksp_tol.setDecimals(10)
        self.spin_ksp_tol.setSpecialValueText("Auto")
        self.spin_ksp_tol.setToolTip("Krylov solver tolerance — 0 = Palace default")
        fl.addRow("KSP tolerance:", self.spin_ksp_tol)

        self.spin_eigen_tol = QtWidgets.QDoubleSpinBox()
        self.spin_eigen_tol.setRange(0.0, 1.0)
        self.spin_eigen_tol.setDecimals(10)
        self.spin_eigen_tol.setSpecialValueText("Auto")
        self.spin_eigen_tol.setToolTip("Eigenmode solve tolerance — 0 = Palace default")
        fl.addRow("Eigen tolerance:", self.spin_eigen_tol)

        self.spin_nsamples = QtWidgets.QSpinBox()
        self.spin_nsamples.setRange(0, 100000)
        self.spin_nsamples.setSpecialValueText("Auto")
        self.spin_nsamples.setToolTip(
            "VoltagePath internal resampling resolution — 0 = Palace default (100)"
        )
        fl.addRow("Voltage path samples:", self.spin_nsamples)

        self.spin_offset = QtWidgets.QDoubleSpinBox()
        self.spin_offset.setRange(0.0, 100000.0)
        self.spin_offset.setDecimals(4)
        self.spin_offset.setSuffix(" mm")
        self.spin_offset.setSpecialValueText("Auto")
        self.spin_offset.setToolTip(
            "Port mode profile distance offset — 0 = Palace default"
        )
        fl.addRow("Mode offset:", self.spin_offset)

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

        char_z_re = getattr(o, "CharacteristicZReal", 50.0)
        char_z_im = getattr(o, "CharacteristicZImag", 0.0)
        sign = "+" if char_z_im >= 0 else "-"
        self.lbl_char_z_complex.setText(f"{char_z_re:.2f} {sign} j{abs(char_z_im):.2f} Ω")

        renorm_z = getattr(o, "RenormZ", 50.0)
        self.spin_renorm_z.setValue(renorm_z)

        self.spin_max_its.setValue(getattr(o, "MaxIts", 0))
        self.spin_ksp_tol.setValue(getattr(o, "KSPTol", 0.0))
        self.spin_eigen_tol.setValue(getattr(o, "EigenTol", 0.0))
        self.spin_nsamples.setValue(getattr(o, "NSamples", 0))
        self.spin_offset.setValue(getattr(o, "Offset", 0.0))

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
        o.MaxIts     = self.spin_max_its.value()
        o.KSPTol     = self.spin_ksp_tol.value()
        o.EigenTol   = self.spin_eigen_tol.value()
        o.NSamples   = self.spin_nsamples.value()
        o.Offset     = self.spin_offset.value()
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

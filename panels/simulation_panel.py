import json
import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets

_SIM_TYPES = ["Driven", "Eigenmode", "Electrostatic"]

# Column indices for the samples table
_COL_TYPE  = 0
_COL_FREQ1 = 1
_COL_FREQ2 = 2
_COL_STEP  = 3


class SimulationPanel:
    def __init__(self, obj):
        self.obj = obj
        self.form = self._build()
        self._populate()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Simulation Settings")
        root = QtWidgets.QVBoxLayout(w)

        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs)

        self.tabs.addTab(self._build_general_tab(),     "General")
        self.tabs.addTab(self._build_solver_tab(),      "Solver")
        self.tabs.addTab(self._build_frequencies_tab(), "Frequencies")

        self.combo_type.currentIndexChanged.connect(self._on_type_changed)
        return w

    # ---------- General tab -------------------------------------------

    def _build_general_tab(self):
        tab = QtWidgets.QWidget()
        vl  = QtWidgets.QVBoxLayout(tab)

        gen = QtWidgets.QGroupBox("General")
        fl  = QtWidgets.QFormLayout(gen)

        self.combo_type = QtWidgets.QComboBox()
        self.combo_type.addItems(_SIM_TYPES)
        fl.addRow("Simulation type:", self.combo_type)

        self.spin_order = QtWidgets.QSpinBox()
        self.spin_order.setRange(1, 4)
        fl.addRow("FE order:", self.spin_order)

        self.spin_verbose = QtWidgets.QSpinBox()
        self.spin_verbose.setRange(0, 2)
        fl.addRow("Verbose:", self.spin_verbose)

        self.spin_num_procs = QtWidgets.QSpinBox()
        self.spin_num_procs.setRange(1, 256)
        self.spin_num_procs.setToolTip(
            "Number of MPI processes (Palace ranks). 1 = single-process."
        )
        fl.addRow("MPI processes:", self.spin_num_procs)

        self.spin_num_threads = QtWidgets.QSpinBox()
        self.spin_num_threads.setRange(0, 256)
        self.spin_num_threads.setToolTip(
            "OpenMP threads per MPI rank (OMP_NUM_THREADS). 0 = use system default."
        )
        fl.addRow("OMP threads/rank:", self.spin_num_threads)

        self.edit_l0 = QtWidgets.QLineEdit()
        self.edit_l0.setToolTip("Length scale in meters, e.g. 1e-3 for mm")
        fl.addRow("Length scale L0 (m):", self.edit_l0)

        self.edit_output = QtWidgets.QLineEdit()
        fl.addRow("Output directory:", self.edit_output)

        bin_row = QtWidgets.QHBoxLayout()
        self.edit_binary = QtWidgets.QLineEdit()
        btn_browse = QtWidgets.QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_binary)
        bin_row.addWidget(self.edit_binary)
        bin_row.addWidget(btn_browse)
        fl.addRow("Palace binary:", bin_row)

        vl.addWidget(gen)

        mesh_grp = QtWidgets.QGroupBox("Mesh")
        mfl = QtWidgets.QFormLayout(mesh_grp)
        self.edit_cl_max = QtWidgets.QLineEdit()
        self.edit_cl_max.setToolTip(
            "Gmsh CharacteristicLengthMax in model units. 0 = let Gmsh decide."
        )
        self.edit_cl_min = QtWidgets.QLineEdit()
        self.edit_cl_min.setToolTip(
            "Gmsh CharacteristicLengthMin in model units. 0 = let Gmsh decide."
        )
        mfl.addRow("Max element size:", self.edit_cl_max)
        mfl.addRow("Min element size:", self.edit_cl_min)
        vl.addWidget(mesh_grp)
        vl.addStretch()
        return tab

    # ---------- Solver tab --------------------------------------------

    def _build_solver_tab(self):
        tab = QtWidgets.QWidget()
        vl  = QtWidgets.QVBoxLayout(tab)

        self.stack = QtWidgets.QStackedWidget()

        # Driven page (index 0) — frequency sweep moved to Frequencies tab;
        # only adaptive sweep stays here.
        driven = QtWidgets.QWidget()
        dvl = QtWidgets.QVBoxLayout(driven)
        dvl.setContentsMargins(0, 0, 0, 0)

        self.chk_parallel = QtWidgets.QCheckBox("Run passes in parallel")
        self.chk_parallel.setToolTip(
            "When checked, all per-port excitation passes are launched simultaneously.\n"
            "Uncheck to run them one at a time (useful on memory-constrained systems)."
        )
        self.chk_parallel.setChecked(True)
        dvl.addWidget(self.chk_parallel)

        self.adaptive_grp = QtWidgets.QGroupBox("Adaptive Frequency Sweep")
        self.adaptive_grp.setCheckable(True)
        self.adaptive_grp.setChecked(False)
        self.adaptive_grp.setToolTip(
            "When enabled, Palace uses rational-interpolation-based adaptive\n"
            "sampling instead of a uniform frequency step. Works with both\n"
            "Linear and Samples sweep modes."
        )
        afl = QtWidgets.QFormLayout(self.adaptive_grp)
        self.edit_adaptive_tol = QtWidgets.QLineEdit("0.01")
        self.edit_adaptive_tol.setToolTip(
            "Convergence tolerance for the adaptive sweep (e.g. 0.01 = 1%).\n"
            "Palace stops adding samples once the rational model change is below this."
        )
        self.spin_adaptive_max = QtWidgets.QSpinBox()
        self.spin_adaptive_max.setRange(2, 1000)
        self.spin_adaptive_max.setValue(10)
        self.spin_adaptive_max.setToolTip(
            "Maximum number of frequency samples Palace may take."
        )
        self.spin_adaptive_cand = QtWidgets.QSpinBox()
        self.spin_adaptive_cand.setRange(0, 1000)
        self.spin_adaptive_cand.setValue(0)
        self.spin_adaptive_cand.setToolTip(
            "Maximum candidate frequency points evaluated per adaptive iteration.\n"
            "0 = use Palace's internal default."
        )
        afl.addRow("Tolerance:",       self.edit_adaptive_tol)
        afl.addRow("Max samples:",     self.spin_adaptive_max)
        afl.addRow("Max candidates:",  self.spin_adaptive_cand)
        dvl.addWidget(self.adaptive_grp)
        dvl.addStretch()
        self.stack.addWidget(driven)

        # Eigenmode page (index 1)
        eigen = QtWidgets.QWidget()
        efl = QtWidgets.QFormLayout(eigen)
        self.spin_nmodes = QtWidgets.QSpinBox()
        self.spin_nmodes.setRange(1, 200)
        self.edit_fmin_eigen = QtWidgets.QLineEdit()
        efl.addRow("Number of modes:",   self.spin_nmodes)
        efl.addRow("Min freq hint (GHz):", self.edit_fmin_eigen)
        self.stack.addWidget(eigen)

        # Electrostatic page (index 2)
        elec = QtWidgets.QWidget()
        elfl = QtWidgets.QFormLayout(elec)
        self.spin_maxiter = QtWidgets.QSpinBox()
        self.spin_maxiter.setRange(1, 100000)
        self.edit_tol = QtWidgets.QLineEdit()
        elfl.addRow("Max iterations:", self.spin_maxiter)
        elfl.addRow("Tolerance:",      self.edit_tol)
        self.stack.addWidget(elec)

        solver_grp = QtWidgets.QGroupBox("Solver Settings")
        sgl = QtWidgets.QVBoxLayout(solver_grp)
        sgl.addWidget(self.stack)
        vl.addWidget(solver_grp)
        vl.addStretch()
        return tab

    # ---------- Frequencies tab ---------------------------------------

    def _build_frequencies_tab(self):
        tab = QtWidgets.QWidget()
        vl  = QtWidgets.QVBoxLayout(tab)

        # Radio buttons
        mode_grp = QtWidgets.QGroupBox("Sweep Mode")
        mode_hl  = QtWidgets.QHBoxLayout(mode_grp)
        self.radio_linear  = QtWidgets.QRadioButton("Linear sweep")
        self.radio_samples = QtWidgets.QRadioButton("Samples")
        self.radio_linear.setChecked(True)
        mode_hl.addWidget(self.radio_linear)
        mode_hl.addWidget(self.radio_samples)
        mode_hl.addStretch()
        vl.addWidget(mode_grp)

        # Stacked widget: page 0 = linear, page 1 = samples
        self.freq_stack = QtWidgets.QStackedWidget()

        # --- Page 0: Linear sweep ---
        linear_page = QtWidgets.QWidget()
        lp_vl = QtWidgets.QVBoxLayout(linear_page)
        lp_vl.setContentsMargins(0, 0, 0, 0)
        freq_grp = QtWidgets.QGroupBox("Frequency Sweep")
        dfl = QtWidgets.QFormLayout(freq_grp)
        self.edit_fmin  = QtWidgets.QLineEdit()
        self.edit_fmax  = QtWidgets.QLineEdit()
        self.edit_fstep = QtWidgets.QLineEdit()
        self.edit_fstep.setToolTip(
            "Step between output frequency points (GHz).\n"
            "For uniform sweep this also sets the sample spacing.\n"
            "For adaptive sweep this controls the output interpolation grid."
        )
        dfl.addRow("Min frequency (GHz):",  self.edit_fmin)
        dfl.addRow("Max frequency (GHz):",  self.edit_fmax)
        dfl.addRow("Frequency step (GHz):", self.edit_fstep)
        lp_vl.addWidget(freq_grp)
        lp_vl.addStretch()
        self.freq_stack.addWidget(linear_page)

        # --- Page 1: Samples ---
        samples_page = QtWidgets.QWidget()
        sp_vl = QtWidgets.QVBoxLayout(samples_page)
        sp_vl.setContentsMargins(0, 0, 0, 0)

        samp_grp = QtWidgets.QGroupBox("Frequency Samples")
        sg_vl = QtWidgets.QVBoxLayout(samp_grp)

        self.table_samples = QtWidgets.QTableWidget(0, 4)
        self.table_samples.setHorizontalHeaderLabels(
            ["Type", "Min / Freq (GHz)", "Max (GHz)", "Step / Count"]
        )
        self.table_samples.horizontalHeader().setStretchLastSection(True)
        self.table_samples.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.table_samples.setToolTip(
            "Each row defines one frequency sub-sweep.\n"
            "Linear: MinFreq, MaxFreq, FreqStep (all GHz).\n"
            "Log: MinFreq, MaxFreq, NumSamples (integer count).\n"
            "Point: single frequency in 'Min / Freq' column."
        )
        sg_vl.addWidget(self.table_samples)

        btn_row = QtWidgets.QHBoxLayout()
        btn_add_lin   = QtWidgets.QPushButton("Add Linear")
        btn_add_log   = QtWidgets.QPushButton("Add Log")
        btn_add_pt    = QtWidgets.QPushButton("Add Point")
        btn_remove    = QtWidgets.QPushButton("Remove Selected")
        btn_add_lin.clicked.connect(self._add_linear_row)
        btn_add_log.clicked.connect(self._add_log_row)
        btn_add_pt.clicked.connect(self._add_point_row)
        btn_remove.clicked.connect(self._remove_selected_rows)
        btn_row.addWidget(btn_add_lin)
        btn_row.addWidget(btn_add_log)
        btn_row.addWidget(btn_add_pt)
        btn_row.addStretch()
        btn_row.addWidget(btn_remove)
        sg_vl.addLayout(btn_row)

        note = QtWidgets.QLabel(
            "Linear: FreqStep (GHz) in last column.  "
            "Log: NumSamples (integer) in last column.  "
            "Point: only Min/Freq column is used."
        )
        note.setWordWrap(True)
        sg_vl.addWidget(note)

        sp_vl.addWidget(samp_grp)
        sp_vl.addStretch()
        self.freq_stack.addWidget(samples_page)

        vl.addWidget(self.freq_stack)
        vl.addStretch()

        # Wire radio buttons to stack
        self.radio_linear.toggled.connect(
            lambda checked: self.freq_stack.setCurrentIndex(0) if checked else None
        )
        self.radio_samples.toggled.connect(
            lambda checked: self.freq_stack.setCurrentIndex(1) if checked else None
        )

        return tab

    # ------------------------------------------------------------------
    # Helpers for the samples table
    # ------------------------------------------------------------------

    def _append_row(self, type_str, val1, val2, val3, val3_disabled=False):
        row = self.table_samples.rowCount()
        self.table_samples.insertRow(row)

        type_item = QtWidgets.QTableWidgetItem(type_str)
        type_item.setFlags(type_item.flags() & ~QtCore.Qt.ItemIsEditable)
        self.table_samples.setItem(row, _COL_TYPE,  type_item)
        self.table_samples.setItem(row, _COL_FREQ1, QtWidgets.QTableWidgetItem(str(val1)))
        self.table_samples.setItem(row, _COL_FREQ2, QtWidgets.QTableWidgetItem(str(val2)))

        step_item = QtWidgets.QTableWidgetItem(str(val3) if val3 is not None else "")
        if val3_disabled:
            step_item.setFlags(step_item.flags() & ~QtCore.Qt.ItemIsEditable)
            step_item.setForeground(
                QtWidgets.QApplication.palette().color(QtGui.QPalette.Disabled,
                                                        QtGui.QPalette.Text)
            )
        self.table_samples.setItem(row, _COL_STEP, step_item)

        # For Point type, Max cell is also non-editable
        if type_str == "Point":
            max_item = self.table_samples.item(row, _COL_FREQ2)
            max_item.setFlags(max_item.flags() & ~QtCore.Qt.ItemIsEditable)
            max_item.setForeground(
                QtWidgets.QApplication.palette().color(QtGui.QPalette.Disabled,
                                                        QtGui.QPalette.Text)
            )

    def _add_linear_row(self):
        self._append_row("Linear", 1.0, 5.0, 0.5)

    def _add_log_row(self):
        self._append_row("Log", 1.0, 5.0, 20)

    def _add_point_row(self):
        self._append_row("Point", 2.45, "", "", val3_disabled=True)

    def _remove_selected_rows(self):
        rows = sorted(
            {idx.row() for idx in self.table_samples.selectedIndexes()},
            reverse=True,
        )
        for r in rows:
            self.table_samples.removeRow(r)

    def _load_samples_table(self, json_str):
        self.table_samples.setRowCount(0)
        try:
            entries = json.loads(json_str or "[]")
        except Exception:
            return
        for e in entries:
            t = e.get("type", "linear")
            if t == "linear":
                self._append_row(
                    "Linear",
                    e.get("min_freq_ghz", 1.0),
                    e.get("max_freq_ghz", 5.0),
                    e.get("freq_step_ghz", 0.5),
                )
            elif t == "log":
                self._append_row(
                    "Log",
                    e.get("min_freq_ghz", 1.0),
                    e.get("max_freq_ghz", 5.0),
                    e.get("num_samples", 20),
                )
            elif t == "point":
                self._append_row("Point", e.get("freq_ghz", 1.0), "", "", val3_disabled=True)

    def _dump_samples_table(self):
        entries = []
        for row in range(self.table_samples.rowCount()):
            type_str = (self.table_samples.item(row, _COL_TYPE) or
                        QtWidgets.QTableWidgetItem("")).text().strip()
            v1 = (self.table_samples.item(row, _COL_FREQ1) or
                  QtWidgets.QTableWidgetItem("")).text().strip()
            v2 = (self.table_samples.item(row, _COL_FREQ2) or
                  QtWidgets.QTableWidgetItem("")).text().strip()
            v3 = (self.table_samples.item(row, _COL_STEP) or
                  QtWidgets.QTableWidgetItem("")).text().strip()
            try:
                if type_str == "Linear":
                    entries.append({
                        "type": "linear",
                        "min_freq_ghz":  float(v1),
                        "max_freq_ghz":  float(v2),
                        "freq_step_ghz": float(v3),
                    })
                elif type_str == "Log":
                    entries.append({
                        "type": "log",
                        "min_freq_ghz": float(v1),
                        "max_freq_ghz": float(v2),
                        "num_samples":  int(float(v3)),
                    })
                elif type_str == "Point":
                    entries.append({"type": "point", "freq_ghz": float(v1)})
            except (ValueError, TypeError):
                FreeCAD.Console.PrintWarning(
                    f"Palace: Skipping invalid samples row {row + 1} ({type_str}: "
                    f"'{v1}', '{v2}', '{v3}')\n"
                )
        return json.dumps(entries)

    # ------------------------------------------------------------------
    # Slot: simulation type changed
    # ------------------------------------------------------------------

    def _on_type_changed(self, idx):
        self.stack.setCurrentIndex(idx)
        is_driven = (_SIM_TYPES[idx] == "Driven")
        self.tabs.setTabEnabled(2, is_driven)

    # ------------------------------------------------------------------
    # Populate from object
    # ------------------------------------------------------------------

    def _populate(self):
        o = self.obj
        idx = _SIM_TYPES.index(o.SimulationType) if o.SimulationType in _SIM_TYPES else 0
        self.combo_type.setCurrentIndex(idx)
        self.stack.setCurrentIndex(idx)
        self.spin_order.setValue(o.Order)
        self.spin_verbose.setValue(o.Verbose)
        self.spin_num_procs.setValue(getattr(o, "NumProcesses", 1))
        self.spin_num_threads.setValue(getattr(o, "NumThreads", 0))
        self.edit_l0.setText(str(o.L0))
        self.edit_output.setText(o.OutputDir)
        self.edit_binary.setText(o.PalaceBinary)

        self.edit_fmin.setText(str(o.DrivenMinFreq / 1e9))
        self.edit_fmax.setText(str(o.DrivenMaxFreq / 1e9))
        self.edit_fstep.setText(str(o.DrivenFreqStep / 1e9))

        self.chk_parallel.setChecked(bool(getattr(o, "ParallelPasses", True)))

        adaptive_tol = getattr(o, "DrivenAdaptiveTol", 0.0)
        self.adaptive_grp.setChecked(adaptive_tol > 0.0)
        self.edit_adaptive_tol.setText(
            str(adaptive_tol) if adaptive_tol > 0.0 else "0.01"
        )
        self.spin_adaptive_max.setValue(getattr(o, "DrivenAdaptiveMaxSamples", 10))
        self.spin_adaptive_cand.setValue(getattr(o, "DrivenAdaptiveMaxCandidates", 0))

        sweep_mode = getattr(o, "DrivenSweepMode", "Linear")
        self.radio_linear.setChecked(sweep_mode != "Samples")
        self.radio_samples.setChecked(sweep_mode == "Samples")
        self.freq_stack.setCurrentIndex(1 if sweep_mode == "Samples" else 0)
        self._load_samples_table(getattr(o, "DrivenSamplesJSON", "[]"))

        self.spin_nmodes.setValue(o.EigenNumModes)
        self.edit_fmin_eigen.setText(str(o.EigenFreqMin / 1e9))

        self.spin_maxiter.setValue(o.ElecMaxIter)
        self.edit_tol.setText(str(o.ElecTol))

        self.edit_cl_max.setText(str(o.MeshCharacteristicLengthMax))
        self.edit_cl_min.setText(str(o.MeshCharacteristicLengthMin))

        # Apply tab enable state for initial solver type
        self.tabs.setTabEnabled(2, o.SimulationType == "Driven")

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _browse_binary(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.form, "Select Palace Binary", "", "Executables (*.exe);;All Files (*)"
        )
        if path:
            self.edit_binary.setText(path)

    def _float(self, widget, fallback):
        try:
            return float(widget.text())
        except ValueError:
            return fallback

    # ------------------------------------------------------------------
    # Accept / Reject
    # ------------------------------------------------------------------

    def accept(self):
        o = self.obj
        o.SimulationType = _SIM_TYPES[self.combo_type.currentIndex()]
        o.Order          = self.spin_order.value()
        o.Verbose        = self.spin_verbose.value()
        o.NumProcesses   = self.spin_num_procs.value()
        o.NumThreads     = self.spin_num_threads.value()
        o.L0             = self._float(self.edit_l0, o.L0)
        o.OutputDir      = self.edit_output.text()
        o.PalaceBinary   = self.edit_binary.text()

        o.DrivenMinFreq  = self._float(self.edit_fmin,  o.DrivenMinFreq  / 1e9) * 1e9
        o.DrivenMaxFreq  = self._float(self.edit_fmax,  o.DrivenMaxFreq  / 1e9) * 1e9
        o.DrivenFreqStep = self._float(self.edit_fstep, o.DrivenFreqStep / 1e9) * 1e9

        o.ParallelPasses = self.chk_parallel.isChecked()

        if self.adaptive_grp.isChecked():
            o.DrivenAdaptiveTol = max(self._float(self.edit_adaptive_tol, 0.01), 1e-12)
        else:
            o.DrivenAdaptiveTol = 0.0
        o.DrivenAdaptiveMaxSamples    = self.spin_adaptive_max.value()
        o.DrivenAdaptiveMaxCandidates = self.spin_adaptive_cand.value()

        o.DrivenSweepMode   = "Samples" if self.radio_samples.isChecked() else "Linear"
        o.DrivenSamplesJSON = self._dump_samples_table()

        o.EigenNumModes = self.spin_nmodes.value()
        o.EigenFreqMin  = self._float(self.edit_fmin_eigen, o.EigenFreqMin / 1e9) * 1e9

        o.ElecMaxIter = self.spin_maxiter.value()
        o.ElecTol     = self._float(self.edit_tol, o.ElecTol)

        o.MeshCharacteristicLengthMax = self._float(
            self.edit_cl_max, o.MeshCharacteristicLengthMax
        )
        o.MeshCharacteristicLengthMin = self._float(
            self.edit_cl_min, o.MeshCharacteristicLengthMin
        )

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

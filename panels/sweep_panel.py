"""Panel for editing PalaceSweep parameter sweep / optimization settings."""

import json

import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtCore import Qt
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt

# Table column indices — parameter table
_COL_VARSET = 0
_COL_PROP   = 1
_COL_VALUES = 2   # shown in Sweep mode
_COL_MIN    = 2   # shown in Optimize mode (same column, header changes)
_COL_MAX    = 3
_COL_INIT   = 4   # Optimize mode only — initial guess for optimizer

_SWEEP_COLS    = ["VarSet", "Property", "Values  (1,2,3  or  start:stop:N)"]
_OPTIMIZE_COLS = ["VarSet", "Property", "Min", "Max", "Initial"]

# Objective table columns
_OBJ_SPARAM = 0
_OBJ_TYPE   = 1
_OBJ_FSTART = 2
_OBJ_FSTOP  = 3
_OBJ_TARGET = 4
_OBJ_WEIGHT = 5
_OBJ_COLS   = ["S-param", "Type", "Freq start (GHz)", "Freq stop (GHz)", "Target", "Weight"]


def _sparam_label(row, col):
    """Return 'S11' for single-digit ports, 'S1,10' for ports >= 10."""
    if max(row, col) >= 10:
        return f"S{row},{col}"
    return f"S{row}{col}"


def _varset_labels(doc):
    """Return labels of VarSet objects in the document."""
    # Prefer strict App::VarSet type (FreeCAD 1.0+)
    labels = [obj.Label for obj in doc.Objects
              if getattr(obj, "TypeId", "") == "App::VarSet"]
    if labels:
        return labels
    # Fallback: any non-Palace object with properties
    palace_markers = {
        "SimulationType", "OuterBoundaryType", "PortIndex", "MeshFile",
        "Permittivity", "ConductorType", "Rs", "SweepParams",
    }
    return [obj.Label for obj in doc.Objects
            if not any(hasattr(obj, m) for m in palace_markers)
            and obj.PropertiesList]


def _numeric_props(doc, varset_label):
    """Return list of numeric property names on the named VarSet object.

    Detects plain int/float properties AND FreeCAD Quantity objects
    (App::PropertyLength, App::PropertyAngle, etc.), which carry a .Value
    attribute and are NOT subclasses of Python float.
    """
    objs = doc.getObjectsByLabel(varset_label)
    if not objs:
        return []
    obj = objs[0]
    result = []
    for p in obj.PropertiesList:
        try:
            val = getattr(obj, p)
        except Exception:
            continue
        if isinstance(val, bool):
            continue
        # Plain numeric types (App::PropertyFloat, App::PropertyInteger)
        if isinstance(val, (int, float)):
            result.append(p)
            continue
        # FreeCAD Quantity (App::PropertyLength, App::PropertyAngle, …)
        # Quantities expose a .Value attribute holding the raw float.
        if hasattr(val, 'Value') and isinstance(val.Value, (int, float)):
            result.append(p)
    return result


def _current_value(doc, varset_label, prop_name):
    """Return the current numeric value of a VarSet property, or None if unavailable."""
    objs = doc.getObjectsByLabel(varset_label)
    if not objs:
        return None
    obj = objs[0]
    try:
        val = getattr(obj, prop_name)
    except Exception:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if hasattr(val, "Value") and isinstance(val.Value, (int, float)):
        return float(val.Value)
    return None


def _parse_values(text):
    """Parse a values string into a list of floats.

    Accepts two formats:
      • Discrete:  "0.3, 0.5, 0.7"   — comma-separated values
      • Linspace:  "start:stop:N"     — N evenly-spaced points from start to stop
    """
    text = text.strip()
    if not text:
        return []
    if ":" in text:
        parts = [p.strip() for p in text.split(":")]
        if len(parts) == 3:
            try:
                start, stop, n = float(parts[0]), float(parts[1]), int(parts[2])
                if n < 1:
                    return [start]
                if n == 1:
                    return [start]
                step = (stop - start) / (n - 1)
                return [start + i * step for i in range(n)]
            except ValueError:
                pass
    try:
        return [float(v.strip()) for v in text.split(",") if v.strip()]
    except ValueError:
        return []


class _ParamTableWidget(QtWidgets.QTableWidget):
    """Table for entering sweep/optimize parameters."""

    def __init__(self, mode, parent=None):
        super().__init__(0, 3 if mode == "Sweep" else 5, parent)
        self._mode = mode
        self._doc = FreeCAD.ActiveDocument
        self._update_headers()

    def _update_headers(self):
        if self._mode == "Sweep":
            self.setColumnCount(3)
            self.setHorizontalHeaderLabels(_SWEEP_COLS)
        else:
            self.setColumnCount(5)
            self.setHorizontalHeaderLabels(_OPTIMIZE_COLS)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        if self._mode == "Optimize":
            hh.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
            hh.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)

    def set_mode(self, mode):
        if mode == self._mode:
            return
        data = self.get_params()
        self._mode = mode
        self._update_headers()
        self.setRowCount(0)
        self.load_params(data)

    def add_row(self, varset="", prop="", values="", pmin="0", pmax="1", pini=""):
        row = self.rowCount()
        self.insertRow(row)

        vs_combo = QtWidgets.QComboBox()
        if self._doc:
            vs_combo.addItems([""] + _varset_labels(self._doc))
        vs_combo.setEditable(True)
        vs_combo.setCurrentText(varset)
        vs_combo.currentTextChanged.connect(lambda text, r=row: self._on_varset_changed(r, text))
        self.setCellWidget(row, _COL_VARSET, vs_combo)

        prop_combo = QtWidgets.QComboBox()
        prop_combo.setEditable(True)
        prop_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        if varset and self._doc:
            prop_combo.addItems([""] + _numeric_props(self._doc, varset))
        prop_combo.setCurrentText(prop)
        self.setCellWidget(row, _COL_PROP, prop_combo)

        if self._mode == "Sweep":
            item = QtWidgets.QTableWidgetItem(values)
            self.setItem(row, _COL_VALUES, item)
        else:
            item_min = QtWidgets.QTableWidgetItem(pmin)
            item_max = QtWidgets.QTableWidgetItem(pmax)
            item_ini = QtWidgets.QTableWidgetItem(pini)
            item_ini.setToolTip("Starting value for optimizer. Leave blank to use midpoint (min+max)/2.")
            self.setItem(row, _COL_MIN,  item_min)
            self.setItem(row, _COL_MAX,  item_max)
            self.setItem(row, _COL_INIT, item_ini)
            # Connect after items exist and after setCurrentText(prop) above so
            # load_params never triggers auto-fill; only live user interaction does.
            prop_combo.currentTextChanged.connect(
                lambda text, r=row: self._on_property_changed(r, text)
            )

    def _on_varset_changed(self, row, varset_text):
        prop_combo = self.cellWidget(row, _COL_PROP)
        if prop_combo is None or not self._doc:
            return
        current = prop_combo.currentText()
        prop_combo.clear()
        prop_combo.addItems([""] + _numeric_props(self._doc, varset_text))
        prop_combo.setCurrentText(current)
        self.resizeColumnToContents(_COL_PROP)

    def _on_property_changed(self, row, prop_text):
        if self._mode != "Optimize" or not self._doc or not prop_text:
            return
        vs_w = self.cellWidget(row, _COL_VARSET)
        if vs_w is None:
            return
        varset = vs_w.currentText()
        if not varset:
            return
        val = _current_value(self._doc, varset, prop_text)
        if val is None:
            return
        half = 0.5 * abs(val) if val != 0.0 else 0.5
        for col, text in ((_COL_MIN,  f"{val - half:g}"),
                          (_COL_MAX,  f"{val + half:g}"),
                          (_COL_INIT, f"{val:g}")):
            item = self.item(row, col)
            if item is not None:
                item.setText(text)

    def remove_selected_rows(self):
        rows = sorted(set(i.row() for i in self.selectedItems()), reverse=True)
        for r in rows:
            self.removeRow(r)

    def get_params(self):
        result = []
        for row in range(self.rowCount()):
            vs_w = self.cellWidget(row, _COL_VARSET)
            pr_w = self.cellWidget(row, _COL_PROP)
            varset = vs_w.currentText() if vs_w else ""
            prop = pr_w.currentText() if pr_w else ""
            if not varset or not prop:
                continue
            entry = {"varset": varset, "property": prop}
            if self._mode == "Sweep":
                val_item = self.item(row, _COL_VALUES)
                raw = (val_item.text() if val_item else "").strip()
                vals = _parse_values(raw)
                entry["values"] = vals
                entry["min"] = vals[0] if vals else 0.0
                entry["max"] = vals[-1] if vals else 1.0
            else:
                min_item = self.item(row, _COL_MIN)
                max_item = self.item(row, _COL_MAX)
                ini_item = self.item(row, _COL_INIT)
                try:
                    pmin = float(min_item.text()) if min_item else 0.0
                    pmax = float(max_item.text()) if max_item else 1.0
                except ValueError:
                    pmin, pmax = 0.0, 1.0
                ini_text = (ini_item.text() if ini_item else "").strip()
                try:
                    initial = float(ini_text)
                except ValueError:
                    initial = 0.5 * (pmin + pmax)
                entry["min"]     = pmin
                entry["max"]     = pmax
                entry["initial"] = initial
                entry["values"]  = [pmin, pmax]
            result.append(entry)
        return result

    def load_params(self, params):
        self.setRowCount(0)
        for p in params:
            varset = p.get("varset", "")
            prop = p.get("property", "")
            if self._mode == "Sweep":
                vals = p.get("values", [])
                values_str = ", ".join(str(v) for v in vals)
                self.add_row(varset, prop, values_str)
            else:
                pmin = str(p.get("min", 0.0))
                pmax = str(p.get("max", 1.0))
                pini = str(p["initial"]) if "initial" in p else ""
                self.add_row(varset, prop, pmin=pmin, pmax=pmax, pini=pini)


class _ObjectiveTableWidget(QtWidgets.QTableWidget):
    """Table for entering weighted multi-objective optimization targets."""

    def __init__(self, parent=None):
        super().__init__(0, len(_OBJ_COLS), parent)
        self.setHorizontalHeaderLabels(_OBJ_COLS)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(_OBJ_SPARAM, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_OBJ_TYPE,   QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_OBJ_FSTART, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(_OBJ_FSTOP,  QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(_OBJ_TARGET, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_OBJ_WEIGHT, QtWidgets.QHeaderView.ResizeToContents)
        self.setToolTip(
            "Each objective contributes weight × (mean_value − target)² to the optimizer.\n"
            "Set freq_start = freq_stop for a single frequency point.\n"
            "Set freq_start < freq_stop to average over a frequency range."
        )

    @staticmethod
    def _get_pairs(doc):
        """Read AvailableSParams from simulation, or compute directly from ports."""
        if doc is None:
            return []
        try:
            from features import find_simulation, get_available_s_params
            sim = find_simulation(doc)
            if sim is not None:
                raw = json.loads(getattr(sim, "AvailableSParams", "[]") or "[]")
                if raw:
                    return [tuple(p) for p in raw]
            return get_available_s_params(doc)
        except Exception:
            return []

    def add_row(self, selected=(1, 1), type_="magnitude",
                freq_start=2.45, freq_stop=2.45, target=0.0, weight=1.0):
        row = self.rowCount()
        self.insertRow(row)

        # S-param dropdown populated from active ports
        sparam_cb = QtWidgets.QComboBox()
        pairs = self._get_pairs(FreeCAD.ActiveDocument)
        for r, c in pairs:
            sparam_cb.addItem(_sparam_label(r, c), (r, c))
        if not pairs:
            sparam_cb.addItem("(no ports defined)", (1, 1))

        # Pre-select the saved (row, col) value
        pair_list = list(pairs)
        if selected in pair_list:
            sparam_cb.setCurrentIndex(pair_list.index(selected))
        elif selected != (1, 1) or not pairs:
            # Saved value not currently available — show with warning
            label = _sparam_label(*selected) + "  ⚠ not available"
            sparam_cb.addItem(label, selected)
            sparam_cb.setCurrentIndex(sparam_cb.count() - 1)

        def _spin(lo, hi, dec, val, suffix=""):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setDecimals(dec)
            sb.setValue(val)
            if suffix:
                sb.setSuffix(suffix)
            return sb

        type_cb = QtWidgets.QComboBox()
        type_cb.addItems(["Magnitude (dB)", "Phase (°)"])
        type_cb.setCurrentIndex(0 if type_ == "magnitude" else 1)

        fs_sb  = _spin(0.0, 1000.0, 3, freq_start, " GHz")
        fe_sb  = _spin(0.0, 1000.0, 3, freq_stop,  " GHz")
        tgt_sb = _spin(-999.0, 999.0, 2, target)
        wt_sb  = _spin(0.0001, 10000.0, 4, weight)

        self.setCellWidget(row, _OBJ_SPARAM, sparam_cb)
        self.setCellWidget(row, _OBJ_TYPE,   type_cb)
        self.setCellWidget(row, _OBJ_FSTART, fs_sb)
        self.setCellWidget(row, _OBJ_FSTOP,  fe_sb)
        self.setCellWidget(row, _OBJ_TARGET, tgt_sb)
        self.setCellWidget(row, _OBJ_WEIGHT, wt_sb)

    def remove_selected_rows(self):
        rows = sorted(set(i.row() for i in self.selectedItems()), reverse=True)
        for r in rows:
            self.removeRow(r)

    def get_objectives(self):
        result = []
        for row in range(self.rowCount()):
            sp_w = self.cellWidget(row, _OBJ_SPARAM)
            t_w  = self.cellWidget(row, _OBJ_TYPE)
            fs_w = self.cellWidget(row, _OBJ_FSTART)
            fe_w = self.cellWidget(row, _OBJ_FSTOP)
            tg_w = self.cellWidget(row, _OBJ_TARGET)
            wt_w = self.cellWidget(row, _OBJ_WEIGHT)
            if not all([sp_w, t_w, fs_w, fe_w, tg_w, wt_w]):
                continue
            rc = sp_w.currentData()
            if rc is None:
                rc = (1, 1)
            row_val, col_val = rc
            result.append({
                "row":        row_val,
                "col":        col_val,
                "type":       "magnitude" if t_w.currentIndex() == 0 else "phase",
                "freq_start": fs_w.value(),
                "freq_stop":  fe_w.value(),
                "target":     tg_w.value(),
                "weight":     wt_w.value(),
            })
        return result

    def load_objectives(self, objectives):
        self.setRowCount(0)
        for obj in objectives:
            self.add_row(
                selected   = (int(obj.get("row", 1)), int(obj.get("col", 1))),
                type_      = obj.get("type",       "magnitude"),
                freq_start = float(obj.get("freq_start", obj.get("freq_ghz", 2.45))),
                freq_stop  = float(obj.get("freq_stop",  obj.get("freq_ghz", 2.45))),
                target     = float(obj.get("target",     0.0)),
                weight     = float(obj.get("weight",     1.0)),
            )


class SweepPanel:
    def __init__(self, obj):
        self.obj = obj
        self.form = self._build()
        self._populate()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Sweep / Optimize Settings")
        root = QtWidgets.QVBoxLayout(w)

        # --- Mode ---
        mode_box = QtWidgets.QGroupBox("Mode")
        mode_hl = QtWidgets.QHBoxLayout(mode_box)
        self.radio_sweep = QtWidgets.QRadioButton("Parameter Sweep")
        self.radio_opt   = QtWidgets.QRadioButton("Optimization")
        mode_hl.addWidget(self.radio_sweep)
        mode_hl.addWidget(self.radio_opt)
        root.addWidget(mode_box)

        # --- Parameter table ---
        param_box = QtWidgets.QGroupBox("Sweep Parameters")
        param_vl  = QtWidgets.QVBoxLayout(param_box)

        self.table = _ParamTableWidget("Sweep")
        param_vl.addWidget(self.table)

        btn_hl = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add Parameter")
        self.btn_rem = QtWidgets.QPushButton("Remove Selected")
        btn_hl.addWidget(self.btn_add)
        btn_hl.addWidget(self.btn_rem)
        btn_hl.addStretch()
        param_vl.addLayout(btn_hl)
        root.addWidget(param_box)

        # --- Sweep settings ---
        self.sweep_box = QtWidgets.QGroupBox("Sweep Settings")
        sweep_fl = QtWidgets.QFormLayout(self.sweep_box)
        self.combo_sweep_type = QtWidgets.QComboBox()
        self.combo_sweep_type.addItems(["Grid", "Sequential"])
        sweep_fl.addRow("Sweep type:", self.combo_sweep_type)
        self.lbl_run_count = QtWidgets.QLabel("Estimated runs: —")
        sweep_fl.addRow(self.lbl_run_count)
        root.addWidget(self.sweep_box)

        # --- Optimization settings ---
        self.opt_box = QtWidgets.QGroupBox("Optimization Settings")
        opt_vl = QtWidgets.QVBoxLayout(self.opt_box)

        # Algorithm + goal + max iter
        alg_fl = QtWidgets.QFormLayout()
        self.combo_goal = QtWidgets.QComboBox()
        self.combo_goal.addItems(["Minimize", "Maximize"])
        alg_fl.addRow("Goal:", self.combo_goal)

        self.combo_algorithm = QtWidgets.QComboBox()
        self.combo_algorithm.addItems(["Nelder-Mead", "Differential Evolution", "COBYLA"])
        alg_fl.addRow("Algorithm:", self.combo_algorithm)

        self.spin_max_iter = QtWidgets.QSpinBox()
        self.spin_max_iter.setRange(1, 1000)
        alg_fl.addRow("Max iterations:", self.spin_max_iter)
        opt_vl.addLayout(alg_fl)

        # Per-algorithm tolerance pages in a QStackedWidget
        self.tol_stack = QtWidgets.QStackedWidget()

        # Page 0: Nelder-Mead
        nm_page = QtWidgets.QWidget()
        nm_fl = QtWidgets.QFormLayout(nm_page)
        self.spin_nm_xatol = QtWidgets.QDoubleSpinBox()
        self.spin_nm_xatol.setRange(1e-10, 1.0)
        self.spin_nm_xatol.setDecimals(6)
        self.spin_nm_xatol.setSingleStep(1e-3)
        nm_fl.addRow("Simplex size tolerance (fraction of range):", self.spin_nm_xatol)
        self.tol_stack.addWidget(nm_page)

        # Page 1: Differential Evolution
        de_page = QtWidgets.QWidget()
        de_fl = QtWidgets.QFormLayout(de_page)
        self.spin_de_tol = QtWidgets.QDoubleSpinBox()
        self.spin_de_tol.setRange(1e-10, 1.0)
        self.spin_de_tol.setDecimals(6)
        self.spin_de_tol.setSingleStep(1e-3)
        de_fl.addRow("Population spread tol:", self.spin_de_tol)
        self.tol_stack.addWidget(de_page)

        # Page 2: COBYLA
        co_page = QtWidgets.QWidget()
        co_fl = QtWidgets.QFormLayout(co_page)
        self.spin_cobyla_tol = QtWidgets.QDoubleSpinBox()
        self.spin_cobyla_tol.setRange(1e-10, 1000.0)
        self.spin_cobyla_tol.setDecimals(6)
        self.spin_cobyla_tol.setSingleStep(1e-3)
        co_fl.addRow("Initial trust radius (rhobeg):", self.spin_cobyla_tol)
        self.tol_stack.addWidget(co_page)

        opt_vl.addWidget(self.tol_stack)

        # Objectives table
        obj_lbl = QtWidgets.QLabel(
            "<b>Objectives</b> — optimizer minimizes Σ weight·(mean_val − target)²"
        )
        obj_lbl.setWordWrap(True)
        opt_vl.addWidget(obj_lbl)

        self.obj_table = _ObjectiveTableWidget()
        opt_vl.addWidget(self.obj_table)

        obj_btn_hl = QtWidgets.QHBoxLayout()
        btn_add_obj = QtWidgets.QPushButton("+ Add Objective")
        btn_rem_obj = QtWidgets.QPushButton("− Remove Selected")
        btn_add_obj.clicked.connect(lambda: self.obj_table.add_row())
        btn_rem_obj.clicked.connect(self.obj_table.remove_selected_rows)
        obj_btn_hl.addWidget(btn_add_obj)
        obj_btn_hl.addWidget(btn_rem_obj)
        obj_btn_hl.addStretch()
        opt_vl.addLayout(obj_btn_hl)

        root.addWidget(self.opt_box)

        # --- Status ---
        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        root.addStretch()

        # Connections
        self.radio_sweep.toggled.connect(self._on_mode_changed)
        self.btn_add.clicked.connect(self._add_row)
        self.btn_rem.clicked.connect(self.table.remove_selected_rows)
        self.table.cellChanged.connect(self._update_run_count)
        self.combo_sweep_type.currentIndexChanged.connect(self._update_run_count)
        self.combo_algorithm.currentIndexChanged.connect(self._on_algorithm_changed)

        return w

    # ------------------------------------------------------------------
    # Populate
    # ------------------------------------------------------------------

    def _populate(self):
        o = self.obj
        mode = getattr(o, "SweepMode", "Sweep")
        is_sweep = (mode == "Sweep")
        self.radio_sweep.setChecked(is_sweep)
        self.radio_opt.setChecked(not is_sweep)
        self.table.set_mode(mode)

        params = json.loads(getattr(o, "SweepParams", "[]") or "[]")
        self.table.load_params(params)

        sweep_type = getattr(o, "SweepType", "Grid")
        idx = self.combo_sweep_type.findText(sweep_type)
        if idx >= 0:
            self.combo_sweep_type.setCurrentIndex(idx)

        goal = getattr(o, "ObjectiveGoal", "Minimize")
        idx = self.combo_goal.findText(goal)
        if idx >= 0:
            self.combo_goal.setCurrentIndex(idx)

        alg = getattr(o, "OptAlgorithm", "Nelder-Mead")
        idx = self.combo_algorithm.findText(alg)
        if idx >= 0:
            self.combo_algorithm.setCurrentIndex(idx)

        self.spin_max_iter.setValue(int(getattr(o, "OptMaxIter", 50)))
        self.spin_nm_xatol.setValue(float(getattr(o, "NMXatol", 0.01)))
        self.spin_de_tol.setValue(float(getattr(o, "DETol",       0.01)))
        self.spin_cobyla_tol.setValue(float(getattr(o, "COBYLATol", 0.01)))

        # Recompute so AvailableSParams is current before populating objective dropdowns
        try:
            o.Document.recompute()
        except Exception:
            pass

        objectives = json.loads(getattr(o, "ObjectiveList", "[]") or "[]")
        self.obj_table.load_objectives(objectives)

        status = getattr(o, "SweepStatus", "")
        self.lbl_status.setText(f"Status: {status}" if status else "")

        self._on_mode_changed(is_sweep)
        self._on_algorithm_changed(self.combo_algorithm.currentIndex())
        self._update_run_count()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_mode_changed(self, sweep_checked):
        mode = "Sweep" if sweep_checked else "Optimize"
        self.sweep_box.setVisible(sweep_checked)
        self.opt_box.setVisible(not sweep_checked)
        self.table.set_mode(mode)
        self._update_run_count()

    def _on_algorithm_changed(self, idx):
        # Stack pages: 0=Nelder-Mead, 1=Differential Evolution, 2=COBYLA
        alg_order = ["Nelder-Mead", "Differential Evolution", "COBYLA"]
        alg = self.combo_algorithm.currentText()
        try:
            page = alg_order.index(alg)
        except ValueError:
            page = 0
        self.tol_stack.setCurrentIndex(page)

    def _add_row(self):
        self.table.add_row()

    def _update_run_count(self):
        if not self.radio_sweep.isChecked():
            return
        params = self.table.get_params()
        if not params:
            self.lbl_run_count.setText("Estimated runs: 0")
            return
        sweep_type = self.combo_sweep_type.currentText()
        if sweep_type == "Sequential":
            n = min(len(p.get("values", [])) for p in params) if params else 0
        else:
            n = 1
            for p in params:
                n *= max(len(p.get("values", [])), 1)
        self.lbl_run_count.setText(f"Estimated runs: {n}")

    # ------------------------------------------------------------------
    # Accept / Reject
    # ------------------------------------------------------------------

    def accept(self):
        o = self.obj
        o.SweepMode = "Sweep" if self.radio_sweep.isChecked() else "Optimize"
        o.SweepType = self.combo_sweep_type.currentText()
        o.SweepParams = json.dumps(self.table.get_params())
        o.ObjectiveGoal = self.combo_goal.currentText()
        o.OptAlgorithm = self.combo_algorithm.currentText()
        o.OptMaxIter = self.spin_max_iter.value()
        o.NMXatol    = self.spin_nm_xatol.value()
        o.DETol      = self.spin_de_tol.value()
        o.COBYLATol  = self.spin_cobyla_tol.value()
        o.ObjectiveList = json.dumps(self.obj_table.get_objectives())
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

"""Dockable sweep results viewer for the Palace workbench.

Loads a sweep.nc file (produced by the PalaceSweep feature) and plots
S-parameters across sweep parameter values using an embedded matplotlib figure.
"""

import json
import os

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtCore import Qt
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt

try:
    import Plot as _PlotMod
    _MPL_OK = True
except Exception:
    _MPL_OK = False

_KNOWN_DIMS = {"freq", "port_i", "port_j", "probe", "component"}


def _detect_sweep_dim(ds):
    """Return (dim_name, coord_values) for the sweep dimension, or (None, [])."""
    for dim in ds.dims:
        if dim not in _KNOWN_DIMS:
            vals = ds.coords[dim].values.tolist() if dim in ds.coords else list(range(ds.dims[dim]))
            return dim, vals
    return None, []


def _load_sweep_nc(path):
    """Load sweep.nc and return structured sweep data.

    Returns
    -------
    sweep_dim   : str or None — name of the sweep coordinate
    sweep_vals  : list — coordinate values along the sweep dimension
    freq_ghz    : list[float]
    slices      : dict[sweep_val -> (mag_db, phase_deg)]
                  where mag_db / phase_deg are dict[(r,c) -> list[float]]
    """
    import numpy as np
    from palace.results_db import load_dataset

    ds = load_dataset(path)
    sweep_dim, sweep_vals = _detect_sweep_dim(ds)

    freq_ghz = ds.coords["freq"].values.tolist()
    port_i   = ds.coords["port_i"].values.tolist()
    port_j   = ds.coords["port_j"].values.tolist()

    slices = {}

    def _extract_slice(mag_arr_3d, phase_arr_3d):
        mag_db, phase_deg = {}, {}
        for ri, r in enumerate(port_i):
            for ci, c in enumerate(port_j):
                col_mag = mag_arr_3d[:, ri, ci]
                if not np.all(np.isnan(col_mag)):
                    mag_db[(int(r), int(c))]    = col_mag.tolist()
                    phase_deg[(int(r), int(c))] = phase_arr_3d[:, ri, ci].tolist()
        return mag_db, phase_deg

    if sweep_dim is None:
        # Single-run file — treat as one sweep point at index 0
        mag_arr   = ds["S_mag_db"].values   # (freq, port_i, port_j)
        phase_arr = ds["S_phase"].values
        mag_db, phase_deg = _extract_slice(mag_arr, phase_arr)
        slices[0] = (mag_db, phase_deg)
        sweep_vals = [0]
    else:
        n_sweep = len(sweep_vals)
        mag_full   = ds["S_mag_db"].values   # (sweep, freq, port_i, port_j)
        phase_full = ds["S_phase"].values
        for si in range(n_sweep):
            mag_db, phase_deg = _extract_slice(mag_full[si], phase_full[si])
            slices[sweep_vals[si]] = (mag_db, phase_deg)

    params_by_val = {}
    obj_by_val = {}
    for i, sv in enumerate(sweep_vals):
        attr = ds.attrs.get(f"sweep_run_{i:03d}_params")
        if attr:
            try:
                params_by_val[sv] = json.loads(attr)
            except Exception:
                params_by_val[sv] = None
        else:
            params_by_val[sv] = None

        obj_attr = ds.attrs.get(f"sweep_run_{i:03d}_objective")
        obj_by_val[sv] = float(obj_attr) if obj_attr is not None else None

    ds.close()
    return sweep_dim, sweep_vals, freq_ghz, slices, params_by_val, obj_by_val


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class SweepResultsPanel(QtWidgets.QDockWidget):
    """Dockable sweep results plot viewer (singleton)."""

    _instance = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(cls):
        import FreeCADGui
        mw = FreeCADGui.getMainWindow()

        if cls._instance is not None:
            try:
                cls._instance.show()
                cls._instance.raise_()
                return cls._instance
            except RuntimeError:
                cls._instance = None

        existing = mw.findChild(QtWidgets.QDockWidget, "PalaceSweepResultsPanel")
        if existing is not None and isinstance(existing, cls):
            cls._instance = existing
            existing.show()
            existing.raise_()
            return cls._instance

        cls._instance = cls(mw)
        return cls._instance

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, parent):
        super().__init__("Palace Sweep Results", parent)
        self.setObjectName("PalaceSweepResultsPanel")
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFloatable |
            QtWidgets.QDockWidget.DockWidgetClosable
        )

        self._sweep_dim   = None
        self._sweep_vals  = []
        self._freq_ghz    = []
        self._slices      = {}       # sweep_val -> (mag_db, phase_deg)
        self._run_params  = {}       # sweep_val -> {property: value} or None
        self._obj_by_val  = {}       # sweep_val -> float or None
        self._checks      = {}       # (r,c) -> QCheckBox
        self._nc_path     = None

        central = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(central)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(4)

        # --- File row ---
        file_row = QtWidgets.QHBoxLayout()
        self._file_label = QtWidgets.QLabel("(no results available)")
        self._file_label.setWordWrap(False)
        file_row.addWidget(self._file_label, 1)
        self._btn_reload = QtWidgets.QPushButton("Reload")
        self._btn_reload.setFixedWidth(70)
        self._btn_reload.setToolTip("Re-check the active document for updated results.")
        self._btn_reload.clicked.connect(self.refresh_from_active_document)
        file_row.addWidget(self._btn_reload)
        vl.addLayout(file_row)

        # --- Sweep selector row ---
        sweep_row = QtWidgets.QHBoxLayout()
        sweep_row.addWidget(QtWidgets.QLabel("Sweep point:"))
        self._combo_sweep = QtWidgets.QComboBox()
        self._combo_sweep.setMinimumWidth(120)
        self._combo_sweep.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self._combo_sweep.currentIndexChanged.connect(self._refresh_plot)
        sweep_row.addWidget(self._combo_sweep)
        self._chk_overlay = QtWidgets.QCheckBox("Overlay all")
        self._chk_overlay.setToolTip(
            "Plot all sweep values as separate traces on the same chart."
        )
        self._chk_overlay.toggled.connect(self._on_overlay_toggled)
        sweep_row.addWidget(self._chk_overlay)
        sweep_row.addStretch()
        vl.addLayout(sweep_row)

        # --- Parameter display label ---
        self._params_label = QtWidgets.QLabel("")
        self._params_label.setStyleSheet("color: #888; font-size: 9pt;")
        self._params_label.setWordWrap(True)
        vl.addWidget(self._params_label)

        # --- S-param checkboxes + plot mode radios ---
        ctrl_row = QtWidgets.QHBoxLayout()
        self._checks_widget = QtWidgets.QWidget()
        self._checks_layout = QtWidgets.QGridLayout(self._checks_widget)
        self._checks_layout.setContentsMargins(0, 0, 0, 0)
        self._checks_layout.setSpacing(4)
        ctrl_row.addWidget(self._checks_widget)

        self._radio_mag   = QtWidgets.QRadioButton("Magnitude (dB)")
        self._radio_phase = QtWidgets.QRadioButton("Phase (°)")
        self._radio_mag.setChecked(True)
        self._radio_mag.toggled.connect(self._refresh_plot)
        radio_col = QtWidgets.QVBoxLayout()
        radio_col.setContentsMargins(8, 0, 0, 0)
        radio_col.addWidget(self._radio_mag)
        radio_col.addWidget(self._radio_phase)
        radio_col.addStretch()
        ctrl_row.addLayout(radio_col)
        ctrl_row.addStretch()
        vl.addLayout(ctrl_row)

        # --- Plot area ---
        if _MPL_OK:
            self._plot_widget = _PlotMod.Plot("Sweep Results", parent=central)
            self._fig    = self._plot_widget.fig
            self._canvas = self._plot_widget.canvas
            self._ax     = self._plot_widget.axes
            self._fig.set_tight_layout(True)
            self._plot_widget.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )
            vl.addWidget(self._plot_widget, 1)
        else:
            placeholder = QtWidgets.QLabel(
                "FreeCAD Plot module is not available.\n\n"
                "The sweep results viewer requires FreeCAD's built-in Plot workbench."
            )
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #888; font-size: 11pt;")
            vl.addWidget(placeholder, 1)

        self.setWidget(central)
        parent.addDockWidget(Qt.RightDockWidgetArea, self)
        self.show()

        try:
            from commands.cmd_sweep import _iter_emitter
            _iter_emitter.iteration_done.connect(self._on_iteration_done)
        except Exception:
            pass

        try:
            from commands.doc_events import doc_emitter
            doc_emitter.active_document_changed.connect(
                lambda: QtCore.QTimer.singleShot(0, self.refresh_from_active_document)
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_from_active_document(self):
        """Resolve the embedded sweep results file from the active document and
        load it, or show the empty state if none is embedded."""
        if not _MPL_OK:
            return
        import FreeCAD
        from palace.embedded_files import resolve
        from features import find_sweep
        doc = FreeCAD.ActiveDocument
        sweep_obj = find_sweep(doc) if doc else None
        nc_path = resolve(sweep_obj, "SweepResultsFile") if sweep_obj else ""
        if nc_path and os.path.isfile(nc_path):
            self.load_nc(nc_path)
        else:
            self._show_no_data("No sweep results embedded in the active document.")

    def _show_no_data(self, message):
        """Clear the plot and show an explanatory message in place of data."""
        self._nc_path = None
        self._sweep_dim = None
        self._sweep_vals = []
        self._freq_ghz = []
        self._slices = {}
        self._run_params = {}
        self._obj_by_val = {}
        self._file_label.setText("(no results available)")
        self._file_label.setToolTip("")
        self._combo_sweep.blockSignals(True)
        self._combo_sweep.clear()
        self._combo_sweep.blockSignals(False)
        self._params_label.setText("")
        self._rebuild_checkboxes()
        if _MPL_OK:
            self._ax.cla()
            self._ax.text(0.5, 0.5, message, ha="center", va="center",
                           transform=self._ax.transAxes, wrap=True, color="gray")
            self._canvas.draw()

    def load_nc(self, path):
        """Load a Palace sweep.nc file and refresh the panel."""
        if not _MPL_OK:
            return
        try:
            self._sweep_dim, self._sweep_vals, self._freq_ghz, self._slices, \
                self._run_params, self._obj_by_val = _load_sweep_nc(path)
        except Exception as exc:
            import FreeCAD
            FreeCAD.Console.PrintError(
                f"Palace sweep viewer: failed to load {path}: {exc}\n"
            )
            self._show_no_data(f"Failed to load embedded results:\n{exc}")
            return

        self._nc_path = path
        self._file_label.setText(os.path.basename(path))
        self._file_label.setToolTip(path)

        # Rebuild sweep combo
        self._combo_sweep.blockSignals(True)
        self._combo_sweep.clear()
        dim_label = self._sweep_dim or "run_index"
        for v in self._sweep_vals:
            self._combo_sweep.addItem(f"{dim_label} = {v}", userData=v)

        # Mark the best iteration (minimum objective) in the combo list
        valid_objs = [(sv, ov) for sv, ov in self._obj_by_val.items() if ov is not None]
        if valid_objs:
            best_sv = min(valid_objs, key=lambda x: x[1])[0]
            for i in range(self._combo_sweep.count()):
                if self._combo_sweep.itemData(i) == best_sv:
                    self._combo_sweep.setItemText(i, self._combo_sweep.itemText(i) + "  ★ best")
                    break

        self._combo_sweep.blockSignals(False)

        self._rebuild_checkboxes()
        self._refresh_plot()

    def _on_iteration_done(self, sweep_nc):
        """Called after each sweep/optimization iteration writes to the database."""
        if not self._nc_path:
            # Panel has no file yet — load it and jump to the latest entry.
            self.load_nc(sweep_nc)
            if self._combo_sweep.count():
                self._combo_sweep.setCurrentIndex(self._combo_sweep.count() - 1)
            return
        if self._nc_path != sweep_nc:
            return  # a different file is open — don't disturb it
        # Smart reload: if the user is on the last entry, advance to the new one;
        # otherwise leave their selection intact.
        was_at_last = (self._combo_sweep.count() == 0 or
                       self._combo_sweep.currentIndex() == self._combo_sweep.count() - 1)
        prev_idx = self._combo_sweep.currentIndex()
        self.load_nc(sweep_nc)
        if was_at_last:
            self._combo_sweep.setCurrentIndex(self._combo_sweep.count() - 1)
        else:
            self._combo_sweep.setCurrentIndex(prev_idx)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_overlay_toggled(self, checked):
        self._combo_sweep.setEnabled(not checked)
        self._refresh_plot()

    def _rebuild_checkboxes(self):
        while self._checks_layout.count():
            item = self._checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checks.clear()

        if not self._slices:
            return

        # Collect all (r,c) keys that have data in any slice
        all_keys = set()
        for mag_db, _ in self._slices.values():
            all_keys.update(mag_db.keys())
        if not all_keys:
            return

        n_ports = max(max(r, c) for r, c in all_keys)

        if n_ports > 1:
            for j in range(1, n_ports + 1):
                lbl = QtWidgets.QLabel(str(j))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("color: gray; font-size: 9pt;")
                self._checks_layout.addWidget(lbl, 0, j)
            for i in range(1, n_ports + 1):
                lbl = QtWidgets.QLabel(str(i))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("color: gray; font-size: 9pt;")
                self._checks_layout.addWidget(lbl, i, 0)

        for r in range(1, n_ports + 1):
            for c in range(1, n_ports + 1):
                key = (r, c)
                cb = QtWidgets.QCheckBox(f"S{r}{c}")
                has_data = key in all_keys
                cb.setChecked(has_data)
                cb.setEnabled(has_data)
                if has_data:
                    cb.stateChanged.connect(lambda _s, _k=key: self._refresh_plot())
                self._checks[key] = cb
                self._checks_layout.addWidget(cb, r, c)

    def _refresh_plot(self):
        if not _MPL_OK or not self._slices:
            return

        self._ax.cla()
        self._ax.set_xlabel("Frequency (GHz)")
        self._ax.grid(True, linestyle="--", alpha=0.5)

        show_phase = self._radio_phase.isChecked()
        ylabel = "Phase (°)" if show_phase else "Magnitude (dB)"
        self._ax.set_ylabel(ylabel)

        overlay = self._chk_overlay.isChecked()
        checked_keys = {k for k, cb in self._checks.items() if cb.isChecked()}

        # Update parameter label for the currently selected sweep point
        if not overlay and self._sweep_vals:
            idx = self._combo_sweep.currentIndex()
            val = self._combo_sweep.itemData(idx) if idx >= 0 else None
            params = self._run_params.get(val) if val is not None else None
            if params:
                param_str = ",  ".join(f"{k} = {v:.4g}" for k, v in params.items())
                obj = self._obj_by_val.get(val)
                obj_str = f"  |  objective: {obj:.4f}" if obj is not None else ""
                self._params_label.setText(f"Parameters:  {param_str}{obj_str}")
            else:
                self._params_label.setText("")
        else:
            self._params_label.setText("")

        if overlay:
            sweep_items = list(self._slices.items())
        else:
            idx = self._combo_sweep.currentIndex()
            if idx < 0 or not self._sweep_vals:
                self._canvas.draw()
                return
            val = self._combo_sweep.itemData(idx)
            if val not in self._slices:
                self._canvas.draw()
                return
            sweep_items = [(val, self._slices[val])]

        dim_label = self._sweep_dim or "run"
        for sv, (mag_db, phase_deg) in sweep_items:
            data = phase_deg if show_phase else mag_db
            suffix = f" ({dim_label}={sv})" if overlay else ""
            for key in checked_keys:
                if key in data:
                    r, c = key
                    self._ax.plot(self._freq_ghz, data[key],
                                  label=f"S{r}{c}{suffix}")

        handles, labels = self._ax.get_legend_handles_labels()
        if handles:
            self._ax.legend(loc="best", fontsize=9)

        self._canvas.draw()

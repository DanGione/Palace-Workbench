"""Dockable S-parameter plot viewer for the Palace workbench.

Loads Palace results.nc (xarray/NetCDF4) and displays S-parameter magnitude
(dB) and phase (°) vs frequency using an embedded matplotlib figure with the
full navigation toolbar (zoom / pan / save-to-PNG).
"""

import os

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtCore import Qt
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt

# ---------------------------------------------------------------------------
# Plotting via FreeCAD's bundled Plot module (wraps matplotlib internally)
# ---------------------------------------------------------------------------
try:
    import Plot as _PlotMod
    _MPL_OK = True
except Exception:
    _MPL_OK = False


def _load_nc(path):
    """Load S-parameter data from a Palace results.nc file.

    Returns
    -------
    freq_ghz  : list[float]
    mag_db    : dict[(row, col) -> list[float]]
    phase_deg : dict[(row, col) -> list[float]]
    """
    import numpy as np
    from palace.results_db import load_dataset
    ds = load_dataset(path)

    freq_ghz  = ds.coords["freq"].values.tolist()
    port_i    = ds.coords["port_i"].values.tolist()
    port_j    = ds.coords["port_j"].values.tolist()
    mag_arr   = ds["S_mag_db"].values   # (freq, port_i, port_j)
    phase_arr = ds["S_phase"].values

    mag_db    = {}
    phase_deg = {}
    for ri, r in enumerate(port_i):
        for ci, c in enumerate(port_j):
            col_mag = mag_arr[:, ri, ci]
            if not np.all(np.isnan(col_mag)):
                mag_db[(int(r), int(c))]    = col_mag.tolist()
                phase_deg[(int(r), int(c))] = phase_arr[:, ri, ci].tolist()

    ds.close()
    return freq_ghz, mag_db, phase_deg


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class SParamPanel(QtWidgets.QDockWidget):
    """Dockable S-parameter plot viewer (singleton)."""

    _instance = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(cls):
        """Return the existing panel (creating it if necessary) and show it."""
        import FreeCADGui
        mw = FreeCADGui.getMainWindow()

        if cls._instance is not None:
            try:
                cls._instance.show()
                cls._instance.raise_()
                return cls._instance
            except RuntimeError:
                cls._instance = None

        existing = mw.findChild(QtWidgets.QDockWidget, "PalaceSParamPanel")
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
        super().__init__("Palace S-Parameters", parent)
        self.setObjectName("PalaceSParamPanel")
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFloatable |
            QtWidgets.QDockWidget.DockWidgetClosable
        )

        self._freq   = []
        self._mag    = {}   # (r,c) -> list[float]
        self._phase  = {}
        self._checks = {}   # (r,c) -> QCheckBox
        self._checked_keys: set = set()   # persists across reloads
        self._nc_path: str | None = None
        self._s_renorm_mag: dict = {}
        self._s_renorm_phase: dict = {}
        self._renorm_active = False

        central = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(central)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(4)

        # --- file row ---
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

        # --- control row: checkboxes + mode radios ---
        self._ctrl_row = QtWidgets.QHBoxLayout()
        self._checks_widget = QtWidgets.QWidget()
        self._checks_layout = QtWidgets.QGridLayout(self._checks_widget)
        self._checks_layout.setContentsMargins(0, 0, 0, 0)
        self._checks_layout.setSpacing(4)
        self._ctrl_row.addWidget(self._checks_widget)

        self._radio_mag   = QtWidgets.QRadioButton("Magnitude (dB)")
        self._radio_phase = QtWidgets.QRadioButton("Phase (°)")
        self._radio_mag.setChecked(True)
        self._radio_mag.toggled.connect(self._refresh_plot)
        self._radio_phase.toggled.connect(self._refresh_plot)
        radio_col = QtWidgets.QVBoxLayout()
        radio_col.setContentsMargins(8, 0, 0, 0)
        radio_col.addWidget(self._radio_mag)
        radio_col.addWidget(self._radio_phase)
        radio_col.addStretch()
        self._ctrl_row.addLayout(radio_col)
        self._ctrl_row.addStretch()
        vl.addLayout(self._ctrl_row)

        # --- action buttons row ---
        action_row = QtWidgets.QHBoxLayout()
        self._btn_renorm = QtWidgets.QPushButton()
        self._set_renorm_button_state(active=False)
        self._btn_renorm.setEnabled(False)
        self._btn_renorm.clicked.connect(self._toggle_renorm)
        action_row.addWidget(self._btn_renorm)

        self._btn_export_ts = QtWidgets.QPushButton("Export Touchstone…")
        self._btn_export_ts.setToolTip(
            "Export the current S-parameters as a Touchstone .sNp file."
        )
        self._btn_export_ts.setEnabled(False)
        self._btn_export_ts.clicked.connect(self._export_touchstone)
        action_row.addWidget(self._btn_export_ts)
        action_row.addStretch()
        vl.addLayout(action_row)

        # --- plot area ---
        if _MPL_OK:
            self._plot_widget = _PlotMod.Plot("S-Parameters", parent=central)
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
                "The S-parameter viewer requires FreeCAD's built-in Plot workbench.\n\n"
                "Ensure the Plot workbench is installed and restart FreeCAD."
            )
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #888; font-size: 11pt;")
            vl.addWidget(placeholder, 1)

        self.setWidget(central)
        parent.addDockWidget(Qt.RightDockWidgetArea, self)
        self.show()

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
        """Resolve the embedded results file from the active document and load
        it, or show the empty state if none is embedded."""
        if not _MPL_OK:
            return
        import FreeCAD
        from palace.embedded_files import resolve
        from features import find_simulation
        doc = FreeCAD.ActiveDocument
        sim = find_simulation(doc) if doc else None
        nc_path = resolve(sim, "ResultsFile") if sim else ""
        if nc_path and os.path.isfile(nc_path):
            self.load_nc(nc_path)
        else:
            self._show_no_data("No S-parameter results embedded in the active document.")

    def _show_no_data(self, message):
        """Clear the plot and show an explanatory message in place of data."""
        self._nc_path = None
        self._freq, self._mag, self._phase = [], {}, {}
        self._renorm_active = False
        self._s_renorm_mag, self._s_renorm_phase = {}, {}
        self._set_renorm_button_state(active=False)
        self._file_label.setText("(no results available)")
        self._file_label.setToolTip("")
        self._btn_renorm.setEnabled(False)
        self._btn_export_ts.setEnabled(False)
        self._rebuild_checkboxes()
        if _MPL_OK:
            self._ax.cla()
            self._ax.text(0.5, 0.5, message, ha="center", va="center",
                           transform=self._ax.transAxes, wrap=True, color="gray")
            self._canvas.draw()

    def load_nc(self, path):
        """Load a Palace results.nc file and refresh the plot."""
        if not _MPL_OK:
            return
        try:
            self._freq, self._mag, self._phase = _load_nc(path)
        except Exception as exc:
            import FreeCAD
            FreeCAD.Console.PrintError(
                f"Palace S-param viewer: failed to load {path}: {exc}\n"
            )
            self._show_no_data(f"Failed to load embedded results:\n{exc}")
            return

        self._nc_path = path
        self._renorm_active = False
        self._s_renorm_mag = {}
        self._s_renorm_phase = {}
        self._set_renorm_button_state(active=False)

        self._file_label.setText(os.path.basename(path))
        self._file_label.setToolTip(path)
        self._btn_renorm.setEnabled(True)
        self._btn_export_ts.setEnabled(True)

        self._load_selection_from_doc()
        self._rebuild_checkboxes()

        sim = self._active_simulation()
        if sim is not None and getattr(sim, "SParamRenormActive", False):
            self._apply_renorm()
        else:
            self._refresh_plot()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _port_reference_impedances(self):
        """Return (port_z_old, port_z_new) — {port_idx: float} dicts giving the
        reference impedance each port's S-parameters are (port_z_old) or would
        be after renormalization (port_z_new) expressed against.

        Wave ports use CharacteristicZ (native ref) / RenormZ (user's renorm
        target); lumped ports use R for both, since they have no separate
        native-vs-target distinction. Returns ({}, {}) if there's no active
        document — callers should treat that as "default to 50 Ω per port".
        """
        import FreeCAD
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return {}, {}
        from features import find_wave_ports, find_lumped_ports
        port_z_old, port_z_new = {}, {}
        for wp in find_wave_ports(doc):
            idx = wp.PortIndex
            port_z_old[idx] = getattr(wp, "CharacteristicZ", 50.0)
            port_z_new[idx] = getattr(wp, "RenormZ", 50.0)
        for lp in find_lumped_ports(doc):
            idx = lp.PortIndex
            r   = getattr(lp, "R", 50.0)
            port_z_old[idx] = r
            port_z_new[idx] = r
        return port_z_old, port_z_new

    def _active_simulation(self):
        """Return the Simulation object in the active document, or None."""
        try:
            import FreeCAD
            from features import find_simulation
            doc = FreeCAD.ActiveDocument
            return find_simulation(doc) if doc else None
        except Exception:
            return None

    def _persist_renorm_active(self, active):
        """Save the renorm toggle on the Simulation object so it survives
        document close/reopen and simulation re-runs."""
        sim = self._active_simulation()
        if sim is not None and hasattr(sim, "SParamRenormActive"):
            sim.SParamRenormActive = active

    def _set_renorm_button_state(self, active):
        """Sync the renorm button's label/tooltip to whether renorm is applied."""
        if active:
            self._btn_renorm.setText("Un-normalize Port Impedances")
            self._btn_renorm.setToolTip(
                "Reverts the S-matrix back to its original per-port reference\n"
                "impedances (CharacteristicZ for wave ports, R for lumped ports)."
            )
        else:
            self._btn_renorm.setText("Renormalize to Port Targets")
            self._btn_renorm.setToolTip(
                "Reads CharacteristicZ and RenormZ from each wave port in the active\n"
                "FreeCAD document and renormalizes the S-matrix accordingly.\n"
                "Wave ports use CharacteristicZ as the current reference impedance;\n"
                "lumped ports use their R value."
            )

    def _toggle_renorm(self):
        """Button handler: apply renormalization, or undo it if already applied."""
        if self._renorm_active:
            self._renorm_active = False
            self._s_renorm_mag, self._s_renorm_phase = {}, {}
            self._set_renorm_button_state(active=False)
            self._persist_renorm_active(False)
            self._refresh_plot()
            return
        self._apply_renorm()

    def _apply_renorm(self):
        """Renormalize to per-port targets read from the active FreeCAD document."""
        if not self._mag:
            return
        try:
            import FreeCAD
            if FreeCAD.ActiveDocument is None:
                QtWidgets.QMessageBox.warning(
                    self, "No Document",
                    "Open the FreeCAD document containing the simulation ports first."
                )
                return

            port_z_old, port_z_new = self._port_reference_impedances()

            from palace.post_process import renormalize_s_matrix_from_data
            _, mag, phase = renormalize_s_matrix_from_data(
                self._freq, self._mag, self._phase, port_z_old, port_z_new
            )
            self._s_renorm_mag   = mag
            self._s_renorm_phase = phase
            self._renorm_active  = True
            self._set_renorm_button_state(active=True)
            self._persist_renorm_active(True)
            self._refresh_plot()
        except Exception as exc:
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(f"Palace: S-param renormalization failed: {exc}\n")
            except Exception:
                pass

    def _export_touchstone(self):
        """Write S-parameters to a Touchstone .sNp file chosen by the user."""
        if not self._mag or not self._freq:
            return
        n_ports = max(max(r, c) for r, c in self._mag)
        default_name = f"port.s{n_ports}p"
        # Default to the project's own folder, not self._nc_path's directory --
        # since results.nc is now embedded in the .FCStd, that path resolves to
        # a private FreeCAD cache directory rather than anywhere user-visible.
        import FreeCAD
        doc = FreeCAD.ActiveDocument
        default_dir = (
            os.path.dirname(doc.FileName) if doc and doc.FileName
            else os.path.expanduser("~")
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Touchstone",
            os.path.join(default_dir, default_name),
            f"Touchstone (*.s{n_ports}p);;All files (*)",
        )
        if not path:
            return

        mag   = self._s_renorm_mag   if self._renorm_active else self._mag
        phase = self._s_renorm_phase if self._renorm_active else self._phase

        port_z_old, port_z_new = self._port_reference_impedances()
        port_z_eff = port_z_new if self._renorm_active else port_z_old
        z_per_port = [port_z_eff.get(p, 50.0) for p in range(1, n_ports + 1)]
        uniform = all(abs(z - z_per_port[0]) < 1e-6 * max(abs(z_per_port[0]), 1.0)
                      for z in z_per_port)

        try:
            import io, datetime
            buf = io.StringIO()
            buf.write("! Generated by Palace FreeCAD Workbench\n")
            buf.write(f"! {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if uniform:
                buf.write(f"# GHz S DB R {z_per_port[0]:g}\n")
            else:
                # Touchstone 1.0's single global R can't represent differing
                # per-port reference impedances (e.g. a wave port's computed
                # CharacteristicZ alongside a 50 Ω lumped port) -- upgrade to
                # Touchstone 2.0's [Reference] keyword, which lists one
                # impedance per port. The # line's R stays a nominal fallback
                # for tools that ignore [Reference].
                buf.write("[Version] 2.0\n")
                buf.write("# GHz S DB R 50\n")
                buf.write(f"[Number of Ports] {n_ports}\n")
                buf.write("[Reference] " + " ".join(f"{z:g}" for z in z_per_port) + "\n")
                buf.write("[Network Data]\n")
            ordered = [
                (row, col)
                for col in range(1, n_ports + 1)
                for row in range(1, n_ports + 1)
                if (row, col) in mag
            ]
            for fi, f in enumerate(self._freq):
                flat = []
                for (r, c) in ordered:
                    flat.append(mag[(r, c)][fi])
                    flat.append(phase.get((r, c), [0.0] * len(self._freq))[fi])
                VALUES_PER_LINE = 8
                first = True
                for i in range(0, len(flat), VALUES_PER_LINE):
                    chunk = flat[i: i + VALUES_PER_LINE]
                    nums = "  ".join(f"{v: .9e}" for v in chunk)
                    if first:
                        buf.write(f" {f:.9e}  {nums}\n")
                        first = False
                    else:
                        buf.write(f"             {nums}\n")

            if not uniform:
                buf.write("[End]\n")

            with open(path, "w", encoding="utf-8") as fh:
                fh.write(buf.getvalue())

            import FreeCAD
            FreeCAD.Console.PrintMessage(f"Palace: Touchstone exported → {path}\n")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Export Failed", f"Could not write Touchstone file:\n{exc}"
            )

    def _rebuild_checkboxes(self):
        """Recreate the S-param matrix of checkboxes from the current data keys."""
        while self._checks_layout.count():
            item = self._checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checks.clear()

        if not self._mag:
            return

        n_ports = max(max(r, c) for r, c in self._mag)

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
                has_data = key in self._mag
                cb.setChecked(has_data and key in self._checked_keys)
                cb.setEnabled(has_data)
                if has_data:
                    cb.stateChanged.connect(
                        lambda state, k=key: self._on_cb_toggled(k, state)
                    )
                self._checks[key] = cb
                self._checks_layout.addWidget(cb, r, c)

    def _on_cb_toggled(self, key, state):
        if state:
            self._checked_keys.add(key)
        else:
            self._checked_keys.discard(key)
        self._save_selection_to_doc()
        self._refresh_plot()

    def _load_selection_from_doc(self):
        try:
            import FreeCAD, json
            from features import find_simulation
            doc = FreeCAD.ActiveDocument
            if doc is None:
                return
            sim = find_simulation(doc)
            if sim is None or not hasattr(sim, "SParamSelection"):
                return
            raw = sim.SParamSelection
            if not raw:
                return
            self._checked_keys = {(r, c) for r, c in json.loads(raw)}
        except Exception:
            pass

    def _save_selection_to_doc(self):
        try:
            import FreeCAD, json
            from features import find_simulation
            doc = FreeCAD.ActiveDocument
            if doc is None:
                return
            sim = find_simulation(doc)
            if sim is None or not hasattr(sim, "SParamSelection"):
                return
            sim.SParamSelection = json.dumps(sorted(self._checked_keys))
        except Exception:
            pass

    def _refresh_plot(self):
        """Redraw the matplotlib axes from current checkbox + radio state."""
        if not _MPL_OK or not self._freq:
            return

        self._ax.cla()
        self._ax.set_xlabel("Frequency (GHz)")
        self._ax.grid(True, linestyle="--", alpha=0.5)

        show_phase = self._radio_phase.isChecked()
        if self._renorm_active and self._s_renorm_mag:
            data = self._s_renorm_phase if show_phase else self._s_renorm_mag
        else:
            data = self._phase if show_phase else self._mag
        ylabel = "Phase (°)" if show_phase else "Magnitude (dB)"
        self._ax.set_ylabel(ylabel)
        for key, cb in self._checks.items():
            if cb.isChecked() and key in data:
                r, c = key
                self._ax.plot(self._freq, data[key], label=f"S{r}{c}")

        handles, labels = self._ax.get_legend_handles_labels()
        if handles:
            self._ax.legend(loc="best", fontsize=9)

        self._canvas.draw()

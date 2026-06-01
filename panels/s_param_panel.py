"""Dockable S-parameter plot viewer for the Palace workbench.

Parses Palace port-S.csv output and displays magnitude (dB) and phase (°)
vs frequency using an embedded matplotlib figure with the full navigation
toolbar (zoom / pan / save-to-PNG).

matplotlib is an optional dependency.  If it is not installed the panel
shows installation instructions instead of crashing.
"""

import csv
import os
import re

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

# ---------------------------------------------------------------------------
# CSV parsing (regexes identical to palace/post_process.py)
# ---------------------------------------------------------------------------
_MAG_RE = re.compile(r"\|S\[(\d+)\]\[(\d+)\]\|")
_ANG_RE = re.compile(r"arg\(S\[(\d+)\]\[(\d+)\]\)")


def _parse_csv(path):
    """Parse a Palace port-S.csv file.

    Returns
    -------
    freq_ghz  : list[float]
    mag_db    : dict[(row, col) -> list[float]]
    phase_deg : dict[(row, col) -> list[float]]
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = [h.strip() for h in next(reader)]
        rows = [[float(v) for v in r] for r in reader if any(v.strip() for v in r)]

    mag_cols   = {}   # (row, col) -> column index
    phase_cols = {}

    for i, h in enumerate(headers):
        m = _MAG_RE.search(h)
        if m:
            mag_cols[(int(m.group(1)), int(m.group(2)))] = i
            continue
        m = _ANG_RE.search(h)
        if m:
            phase_cols[(int(m.group(1)), int(m.group(2)))] = i

    freq_ghz  = [r[0] for r in rows]
    mag_db    = {k: [r[v] for r in rows] for k, v in mag_cols.items()}
    phase_deg = {k: [r[v] for r in rows] for k, v in phase_cols.items()}
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
        self._checked_keys: set = set()   # persists across CSV reloads
        self._csv_path: str | None = None
        self._s_renorm_mag: dict = {}
        self._s_renorm_phase: dict = {}
        self._renorm_active = False

        central = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(central)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(4)

        # --- file row ---
        file_row = QtWidgets.QHBoxLayout()
        self._file_label = QtWidgets.QLabel("(no file loaded)")
        self._file_label.setWordWrap(False)
        file_row.addWidget(self._file_label, 1)
        btn_load = QtWidgets.QPushButton("Load CSV…")
        btn_load.setFixedWidth(90)
        btn_load.clicked.connect(self.browse_csv)
        file_row.addWidget(btn_load)
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

        # --- renormalization button ---
        renorm_row = QtWidgets.QHBoxLayout()
        self._btn_renorm = QtWidgets.QPushButton("Renormalize to Port Targets")
        self._btn_renorm.setToolTip(
            "Reads CharacteristicZ and RenormZ from each wave port in the active\n"
            "FreeCAD document and renormalizes the S-matrix accordingly.\n"
            "Wave ports use CharacteristicZ as the current reference impedance;\n"
            "lumped ports use their R value."
        )
        self._btn_renorm.setEnabled(False)
        self._btn_renorm.clicked.connect(self._apply_renorm)
        renorm_row.addWidget(self._btn_renorm)
        renorm_row.addStretch()
        vl.addLayout(renorm_row)

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
        parent.addDockWidget(Qt.BottomDockWidgetArea, self)
        self.show()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_csv(self, path):
        """Parse *path* and refresh the plot."""
        if not _MPL_OK:
            return
        try:
            self._freq, self._mag, self._phase = _parse_csv(path)
        except Exception as exc:
            import FreeCAD
            FreeCAD.Console.PrintError(f"Palace S-param viewer: failed to load {path}: {exc}\n")
            return

        self._csv_path = path
        self._renorm_active = False
        self._s_renorm_mag = {}
        self._s_renorm_phase = {}

        self._file_label.setText(os.path.basename(path))
        self._file_label.setToolTip(path)
        self._btn_renorm.setEnabled(True)

        self._load_selection_from_doc()
        self._rebuild_checkboxes()
        self._refresh_plot()

    def browse_csv(self):
        """Open a file picker, then load the chosen CSV."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Palace port-S.csv", "", "CSV files (*.csv);;All files (*)"
        )
        if path:
            self.load_csv(path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_renorm(self):
        """Renormalize to per-port targets read from the active FreeCAD document."""
        if not self._csv_path:
            return
        try:
            import FreeCAD
            from features import find_wave_ports, find_lumped_ports
            doc = FreeCAD.ActiveDocument
            if doc is None:
                QtWidgets.QMessageBox.warning(
                    self, "No Document",
                    "Open the FreeCAD document containing the simulation ports first."
                )
                return

            port_z_old, port_z_new = {}, {}
            for wp in find_wave_ports(doc):
                idx = wp.PortIndex
                port_z_old[idx] = getattr(wp, "CharacteristicZ", 50.0)
                port_z_new[idx] = getattr(wp, "RenormZ", 50.0)
            for lp in find_lumped_ports(doc):
                idx = lp.PortIndex
                r   = getattr(lp, "R", 50.0)
                port_z_old[idx] = r
                port_z_new[idx] = r   # lumped ports: renorm to same R (identity)

            from palace.post_process import renormalize_s_matrix
            _, mag, phase = renormalize_s_matrix(self._csv_path, port_z_old, port_z_new)
            self._s_renorm_mag   = mag
            self._s_renorm_phase = phase
            self._renorm_active  = True
            self._refresh_plot()
        except Exception as exc:
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(f"Palace: S-param renormalization failed: {exc}\n")
            except Exception:
                pass

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

"""Shared QLineEdit+QComboBox value/unit-conversion widgets.

Extracted from panels/impedance_boundary_panel.py and panels/lumped_port_panel.py
(previously duplicated verbatim in both) now that panels/smd_component_panel.py
is a third consumer. Pure extraction -- no behavior change.
"""

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

# ---------------------------------------------------------------------------
# Unit sets — (display_label, SI_scale)   SI_value = display_value × SI_scale
# ---------------------------------------------------------------------------
R_UNITS = [("Ω", 1.0), ("mΩ", 1e-3), ("kΩ", 1e3)]
L_UNITS = [("H", 1.0), ("mH", 1e-3), ("μH", 1e-6), ("nH", 1e-9), ("pH", 1e-12)]
C_UNITS = [("F", 1.0), ("mF", 1e-3), ("μF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)]


def best_unit_idx(si_value, units):
    """Return the index of the most readable unit for si_value."""
    if si_value == 0.0:
        return 0
    for i, (_, scale) in enumerate(units):
        if 0.1 <= abs(si_value) / scale < 1000:
            return i
    return 0


def set_unit_field(edit, combo, si_value, units):
    """Populate edit+combo from a SI value, choosing the best unit."""
    idx = best_unit_idx(si_value, units)
    combo.blockSignals(True)
    combo.setCurrentIndex(idx)
    combo.setProperty("_prev_idx", idx)
    combo.blockSignals(False)
    edit.setText(f"{si_value / units[idx][1]:.6g}")


def read_si_field(edit, combo, units, fallback):
    """Read the SI value from edit+combo; return fallback on invalid input."""
    try:
        return float(edit.text()) * units[combo.currentIndex()][1]
    except ValueError:
        return fallback


def rescale_on_unit_change(edit, combo, units, new_idx):
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


def make_unit_row(edit, combo, units):
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

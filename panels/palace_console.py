"""Dockable output console for the Palace workbench.

Shows Gmsh meshing and Palace solver output in real time.  Thread-safe:
write() and write_port() can be called from background threads.

Layout: a QTabWidget with:
  Tab 0 "Main" — meshing output + orchestration status (always present)
  Tabs 1..N     — per-Palace-pass stdout, added/removed dynamically
"""

try:
    from PySide2 import QtWidgets, QtGui, QtCore
    from PySide2.QtCore import Qt, Signal, Slot
except ImportError:
    from PySide6 import QtWidgets, QtGui, QtCore
    from PySide6.QtCore import Qt, Signal, Slot


def _make_text_edit():
    edit = QtWidgets.QPlainTextEdit()
    edit.setReadOnly(True)
    font = QtGui.QFont("Monospace")
    font.setStyleHint(QtGui.QFont.TypeWriter)
    font.setPointSize(9)
    edit.setFont(font)
    edit.setMaximumBlockCount(20_000)
    return edit


class PalaceConsole(QtWidgets.QDockWidget):
    """Dockable Palace output console with per-pass tabs."""

    # Emitted from any thread; port_index=-1 routes to the Main tab,
    # port_index>=0 routes to the matching port tab.
    _text_ready = Signal(int, str)

    _instance = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(cls):
        """Return the existing console (creating it if necessary) and show it."""
        import FreeCADGui
        mw = FreeCADGui.getMainWindow()

        if cls._instance is not None:
            try:
                cls._instance.show()
                cls._instance.raise_()
                return cls._instance
            except RuntimeError:
                cls._instance = None

        existing = mw.findChild(QtWidgets.QDockWidget, "PalaceOutputConsole")
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
        super().__init__("Palace Output", parent)
        self.setObjectName("PalaceOutputConsole")
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFloatable |
            QtWidgets.QDockWidget.DockWidgetClosable
        )

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- toolbar row ---
        row = QtWidgets.QHBoxLayout()
        self._status = QtWidgets.QLabel("Idle")
        self._status.setStyleSheet("font-weight: bold;")

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.setFixedWidth(55)
        clear_btn.clicked.connect(self.clear)

        row.addWidget(self._status)
        row.addStretch()
        row.addWidget(clear_btn)
        layout.addLayout(row)

        # --- tab widget ---
        self._tabs = QtWidgets.QTabWidget()
        self._main_edit = _make_text_edit()
        self._tabs.addTab(self._main_edit, "Main")
        # Maps pass_index (int ≥ 0) → QPlainTextEdit
        self._port_edits: dict = {}
        layout.addWidget(self._tabs)

        self.setWidget(central)

        self._text_ready.connect(self._append)

        parent.addDockWidget(Qt.BottomDockWidgetArea, self)
        self.show()

    # ------------------------------------------------------------------
    # Public API (thread-safe unless noted)
    # ------------------------------------------------------------------

    def write(self, text: str):
        """Append *text* to the Main tab.  Safe to call from any thread."""
        self._text_ready.emit(-1, text)

    def write_port(self, pass_index: int, text: str):
        """Append *text* to the tab for *pass_index*.  Safe to call from any thread."""
        self._text_ready.emit(pass_index, text)

    def add_port_tab(self, pass_index: int, label: str):
        """Create a new port tab.  Must be called from the main thread."""
        if pass_index in self._port_edits:
            return
        edit = _make_text_edit()
        self._port_edits[pass_index] = edit
        self._tabs.addTab(edit, label)

    def clear_port_tabs(self):
        """Remove all port tabs, keeping only the Main tab.  Main thread only."""
        while self._tabs.count() > 1:
            self._tabs.removeTab(self._tabs.count() - 1)
        self._port_edits.clear()

    def set_status(self, text: str):
        """Update the status label.  Call from the main thread only."""
        self._status.setText(text)

    def clear(self):
        """Clear the Main tab text and remove all port tabs.  Main thread only."""
        self._main_edit.clear()
        self.clear_port_tabs()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @Slot(int, str)
    def _append(self, pass_index: int, text: str):
        """Always runs on the main thread via the signal connection."""
        if pass_index < 0:
            edit = self._main_edit
        else:
            edit = self._port_edits.get(pass_index, self._main_edit)
        edit.moveCursor(QtGui.QTextCursor.End)
        edit.insertPlainText(text)
        sb = edit.verticalScrollBar()
        sb.setValue(sb.maximum())

def _ensure_combo_view_visible():
    """Make FreeCAD's Combo View dock visible before opening a task panel."""
    import FreeCADGui
    try:
        from PySide2 import QtWidgets
    except ImportError:
        from PySide6 import QtWidgets
    mw = FreeCADGui.getMainWindow()
    for dw in mw.findChildren(QtWidgets.QDockWidget):
        if dw.objectName() == "Combo View":
            if not dw.isVisible():
                dw.setVisible(True)
            dw.raise_()
            return


def _show_as_dialog(panel):
    """
    Show a Palace panel as a non-modal (non-blocking) QDialog.

    Using show() instead of exec_() keeps the FreeCAD main window — including
    the 3D viewport — fully interactive while the panel is open, which is
    required for workflows that need live 3D face selection (e.g. per-face BCs).
    A module-level reference prevents GC from collecting the dialog.
    """
    import FreeCADGui
    try:
        from PySide2 import QtWidgets, QtCore
    except ImportError:
        from PySide6 import QtWidgets, QtCore

    mw = FreeCADGui.getMainWindow()
    dlg = QtWidgets.QDialog(mw)
    dlg.setWindowTitle(panel.form.windowTitle())
    dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)

    vbox = QtWidgets.QVBoxLayout(dlg)
    vbox.addWidget(panel.form)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
    )
    buttons.accepted.connect(panel.accept)
    buttons.accepted.connect(dlg.close)
    buttons.rejected.connect(panel.reject)
    buttons.rejected.connect(dlg.close)
    vbox.addWidget(buttons)

    dlg.show()
    # Keep module-level references so Python GC does not collect the dialog or
    # the panel.  The panel holds all the button slot methods; if it is
    # collected, PySide drops the weak-ref connections and buttons go silent.
    show_palace_panel._active_dlg = dlg
    show_palace_panel._active_panel = panel


def show_palace_panel(panel):
    """
    Show a Palace task panel.

    Ensures the Combo View dock is visible, then attempts to open the panel
    in FreeCAD's embedded task view via a deferred QTimer call (avoids the
    toolbar-click re-entrancy that makes showTaskPanel throw immediately).
    Falls back to a non-modal QDialog if the task view is still unavailable,
    keeping the 3D viewport interactive throughout.
    """
    import FreeCAD
    import FreeCADGui
    try:
        from PySide2 import QtCore
    except ImportError:
        from PySide6 import QtCore

    FreeCADGui.Control.closeDialog()
    _ensure_combo_view_visible()

    # Hold a reference now so the panel is not GC'd while the timer is pending
    # or if showTaskPanel succeeds but FreeCAD only stores the C++-level QWidget.
    show_palace_panel._active_panel = panel

    def _try_task_panel():
        try:
            FreeCADGui.Control.showDialog(panel)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"Palace: Task tab unavailable ({exc}), using dialog.\n")
            _show_as_dialog(panel)

    QtCore.QTimer.singleShot(0, _try_task_panel)

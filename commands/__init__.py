import FreeCADGui


def selected_solids():
    """Return currently selected document objects that have a Shape."""
    return [sel for sel in FreeCADGui.Selection.getSelection() if hasattr(sel, "Shape")]

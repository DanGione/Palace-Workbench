import FreeCAD
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Results.svg")


def _default_csv(doc):
    if doc and doc.FileName:
        base = os.path.dirname(doc.FileName)
    else:
        import tempfile
        base = tempfile.gettempdir()
    return os.path.join(base, "palace_output", "output", "port-S.csv")


class CmdViewResults:
    def GetResources(self):
        return {
            "Pixmap":   _ICON,
            "MenuText": "View S-Parameters",
            "ToolTip":  (
                "Open the S-parameter plot viewer.  Loads the results from the "
                "last simulation automatically; use 'Load CSV…' inside the panel "
                "to open a different file."
            ),
        }

    def IsActive(self):
        return True   # viewer can load any CSV regardless of document state

    def Activated(self):
        from panels.s_param_panel import SParamPanel
        viewer = SParamPanel.get_or_create()
        csv_path = _default_csv(FreeCAD.ActiveDocument)
        if os.path.isfile(csv_path):
            viewer.load_csv(csv_path)
        else:
            viewer.browse_csv()

import FreeCAD
import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Results.svg")


def _default_nc(doc):
    if doc and doc.FileName:
        stem = os.path.splitext(os.path.basename(doc.FileName))[0]
        return os.path.join(os.path.dirname(doc.FileName), stem, "results.nc")
    import tempfile
    return os.path.join(tempfile.gettempdir(), "palace_output", "results.nc")


class CmdViewResults:
    def GetResources(self):
        return {
            "Pixmap":   _ICON,
            "MenuText": "View S-Parameters",
            "ToolTip":  (
                "Open the S-parameter plot viewer.  Loads the results from the "
                "last simulation automatically; use 'Load…' inside the panel "
                "to open a different results file."
            ),
        }

    def IsActive(self):
        return True

    def Activated(self):
        from panels.s_param_panel import SParamPanel
        viewer = SParamPanel.get_or_create()
        nc_path = _default_nc(FreeCAD.ActiveDocument)
        if os.path.isfile(nc_path):
            viewer.load_nc(nc_path)
        else:
            viewer.browse_nc()

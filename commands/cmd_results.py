import os

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Results.svg")


class CmdViewResults:
    def GetResources(self):
        return {
            "Pixmap":   _ICON,
            "MenuText": "View S-Parameters",
            "ToolTip":  (
                "Open the S-parameter plot viewer.  Always follows the active "
                "document's embedded results; use 'Reload' inside the panel to "
                "re-check for updates."
            ),
        }

    def IsActive(self):
        return True

    def Activated(self):
        from panels.s_param_panel import SParamPanel
        viewer = SParamPanel.get_or_create()
        viewer.refresh_from_active_document()

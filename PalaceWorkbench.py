import FreeCADGui
import os


# Lightweight packages auto-installed on first workbench activation.
# gmsh is intentionally excluded — it is ~100 MB and installed on demand by
# palace/meshing.py when the user first runs a mesh operation.
# Full dependency list: requirements.txt
_AUTO_INSTALL_PACKAGES = ["matplotlib", "numpy"]


def _ensure_dependencies():
    """Check for and pip-install any missing lightweight dependencies."""
    import importlib.util
    missing = [p for p in _AUTO_INSTALL_PACKAGES if importlib.util.find_spec(p) is None]
    if not missing:
        return

    import FreeCAD
    import subprocess
    import sys

    installed, failed = [], []
    for pkg in missing:
        FreeCAD.Console.PrintMessage(f"Palace: installing {pkg} via pip…\n")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg],
                capture_output=True, text=True, timeout=120, check=True,
            )
            installed.append(pkg)
            FreeCAD.Console.PrintMessage(f"Palace: {pkg} installed successfully.\n")
        except Exception as exc:
            failed.append(pkg)
            FreeCAD.Console.PrintWarning(f"Palace: failed to install {pkg}: {exc}\n")

    if failed:
        FreeCAD.Console.PrintWarning(
            "Palace: could not install " + " ".join(failed) + ". "
            "Run: pip install " + " ".join(failed) + "\n"
        )

    if installed:
        FreeCAD.Console.PrintMessage(
            "Palace: restart FreeCAD to activate newly installed packages.\n"
        )
        try:
            from PySide2.QtWidgets import QMessageBox
        except ImportError:
            from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            None,
            "Palace — restart required",
            f"Installed: {', '.join(installed)}\n\n"
            "Please restart FreeCAD to enable all Palace features.",
        )


class PalaceWorkbench(FreeCADGui.Workbench):
    MenuText = "Palace"
    ToolTip = "Palace 3D Electromagnetic FEM Solver"

    def __init__(self):
        icon = os.path.join(os.path.dirname(__file__), "resources", "icons", "Palace.svg")
        self.__class__.Icon = icon

    def Initialize(self):
        _ensure_dependencies()
        from commands.cmd_simulation import CmdSimulation
        from commands.cmd_airbox import CmdAirbox
        from commands.cmd_lumped_port import CmdLumpedPort
        from commands.cmd_wave_port import CmdWavePort
        from commands.cmd_impedance_boundary import CmdImpedanceBoundary
        from commands.cmd_material_group import CmdDielectricGroup, CmdConductorGroup
        from commands.cmd_mesh import CmdMesh
        from commands.cmd_run import CmdRun, CmdRunOnly, CmdStopRun
        from commands.cmd_results import CmdViewResults
        from commands.cmd_sweep import CmdSweep, CmdRunSweep, CmdStopSweep, CmdViewSweep

        FreeCADGui.addCommand("Palace_Simulation",      CmdSimulation())
        FreeCADGui.addCommand("Palace_Airbox",          CmdAirbox())
        FreeCADGui.addCommand("Palace_LumpedPort",          CmdLumpedPort())
        FreeCADGui.addCommand("Palace_WavePort",            CmdWavePort())
        FreeCADGui.addCommand("Palace_ImpedanceBoundary",   CmdImpedanceBoundary())
        FreeCADGui.addCommand("Palace_DielectricGroup", CmdDielectricGroup())
        FreeCADGui.addCommand("Palace_ConductorGroup",  CmdConductorGroup())
        FreeCADGui.addCommand("Palace_Mesh",            CmdMesh())
        FreeCADGui.addCommand("Palace_Run",             CmdRun())
        FreeCADGui.addCommand("Palace_RunOnly",         CmdRunOnly())
        FreeCADGui.addCommand("Palace_StopRun",         CmdStopRun())
        FreeCADGui.addCommand("Palace_ViewResults",     CmdViewResults())
        FreeCADGui.addCommand("Palace_Sweep",           CmdSweep())
        FreeCADGui.addCommand("Palace_RunSweep",        CmdRunSweep())
        FreeCADGui.addCommand("Palace_StopSweep",       CmdStopSweep())
        FreeCADGui.addCommand("Palace_ViewSweep",       CmdViewSweep())

        cmds = [
            "Palace_Simulation",
            "Palace_Airbox",
            "Palace_LumpedPort",
            "Palace_WavePort",
            "Palace_ImpedanceBoundary",
            "Palace_DielectricGroup",
            "Palace_ConductorGroup",
            "Separator",
            "Palace_Mesh",       # mesh only — inspect before solving
            "Palace_Run",        # generate mesh + run
            "Palace_RunOnly",    # run with existing mesh
            "Palace_StopRun",
            "Palace_ViewResults",
            "Separator",
            "Palace_Sweep",
            "Palace_RunSweep",
            "Palace_StopSweep",
            "Palace_ViewSweep",
        ]
        self.appendToolbar("Palace", cmds)
        self.appendMenu("Palace", cmds)

    def GetClassName(self):
        return "Gui::PythonWorkbench"

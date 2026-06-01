import FreeCAD
import FreeCADGui
import os
import time
import traceback

try:
    from PySide2 import QtWidgets
    from PySide2.QtCore import QThread, Signal
except ImportError:
    from PySide6 import QtWidgets
    from PySide6.QtCore import QThread, Signal

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Mesh.svg")


def _fmt_elapsed(secs):
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s"


class _MeshWorker(QThread):
    """Runs generate_mesh in a background thread."""

    log = Signal(str)         # progress messages (marshaled to main thread)
    done = Signal(bool, str)  # (success, mesh_path_or_traceback)

    def __init__(self, doc, output_dir, gmsh_instance, parent=None):
        super().__init__(parent)
        self._doc = doc
        self._output_dir = output_dir
        self._gmsh = gmsh_instance

    def run(self):
        from palace.meshing import generate_mesh
        try:
            path = generate_mesh(
                self._doc, self._output_dir,
                log_fn=self.log.emit,
                _gmsh_instance=self._gmsh,
            )
            self.done.emit(True, path)
        except Exception:
            self.done.emit(False, traceback.format_exc())


class CmdMesh:
    _worker = None  # class-level ref keeps the thread alive until it finishes

    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Generate Mesh",
            "ToolTip": (
                "Generate the Gmsh mesh and save it to disk.\n"
                "Inspect node/element counts in the Mesh object before\n"
                "running the Palace solver."
            ),
        }

    def IsActive(self):
        if CmdMesh._worker is not None and CmdMesh._worker.isRunning():
            return False
        from commands.cmd_run import CmdRun
        if CmdRun._coordinator is not None and CmdRun._coordinator.is_running():
            return False
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        from features import find_simulation, find_airbox
        return find_simulation(doc) is not None and find_airbox(doc) is not None

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        from commands.cmd_run import _output_dir
        out_dir = _output_dir(doc)
        os.makedirs(out_dir, exist_ok=True)

        # Ensure a PalaceMesh object exists to receive the result.
        from features.mesh import create_palace_mesh
        mesh_obj = create_palace_mesh(doc)

        # Open (or reuse) the debug console.
        console = None
        try:
            from panels.palace_console import PalaceConsole
            console = PalaceConsole.get_or_create()
            console.clear()
            console.set_status("Meshing…")
        except Exception:
            pass

        def _log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")
            if console is not None:
                console.write(msg)

        # gmsh.initialize() registers Python signal handlers — must run on the
        # main thread.  Everything after that is safe in a background thread.
        try:
            from palace.meshing import init_gmsh_main_thread
            gmsh_instance = init_gmsh_main_thread()
        except Exception as exc:
            _log(f"Palace: Failed to initialise Gmsh: {exc}\n")
            QtWidgets.QMessageBox.critical(None, "Mesh Error", str(exc))
            return

        _t0_mesh = time.time()

        def _on_done(ok, result):
            CmdMesh._worker = None
            if ok:
                mesh_obj.MeshFile = result
                try:
                    from features import find_simulation
                    sim = find_simulation(doc)
                    if sim:
                        sim.MeshFile = result  # backward compat
                except Exception:
                    pass
                # Call execute() directly — setting a PropertyString doesn't
                # reliably mark Mesh::FeaturePython dirty before doc.recompute().
                mesh_obj.Proxy.execute(mesh_obj)
                doc.recompute()
                elapsed = _fmt_elapsed(time.time() - _t0_mesh)
                _log(f"Palace: Mesh written to {result}\n")
                _log(f"Palace: Mesh generated in {elapsed}\n")
                if console is not None:
                    console.set_status(f"Mesh complete ({elapsed})")
            else:
                FreeCAD.Console.PrintError(f"Palace meshing failed:\n{result}\n")
                if console is not None:
                    console.write(f"ERROR:\n{result}\n")
                    console.set_status("Mesh error")
                QtWidgets.QMessageBox.critical(
                    None, "Mesh Error", result.splitlines()[-1]
                )

        _log("Palace: Generating mesh…\n")
        CmdMesh._worker = _MeshWorker(doc, out_dir, gmsh_instance)
        CmdMesh._worker.log.connect(_log)
        CmdMesh._worker.done.connect(_on_done)
        CmdMesh._worker.start()

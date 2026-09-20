import FreeCAD
import FreeCADGui
import os
import shutil
import time

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
    """Runs mesh generation in a freecadcmd subprocess, streaming its output.

    Gmsh's C++ core can't be interrupted mid-call from Python, and a crash
    inside it can take the whole FreeCAD process down -- running it as a
    real subprocess (see palace/mesh_runner.py) makes it killable via stop()
    and crash-isolated, the same reasons Palace itself runs as a subprocess.
    """

    log = Signal(str)            # progress messages (marshaled to main thread)
    done = Signal(bool, object)  # success: (mesh_path, geometry_path, quality); failure: error str

    def __init__(self, doc, output_dir, parent=None):
        super().__init__(parent)
        self._doc = doc
        self._output_dir = output_dir
        self._proc = None
        self._cancelled = False

    def stop(self):
        from palace.mesh_runner import kill_mesh_process
        self._cancelled = True
        kill_mesh_process(self._proc)

    def run(self):
        from palace.mesh_runner import generate_mesh_subprocess

        def _store_proc(p):
            self._proc = p

        try:
            mesh_path, geometry_path, quality = generate_mesh_subprocess(
                self._doc, self._output_dir,
                log_fn=self.log.emit,
                proc_callback=_store_proc,
            )
            self.done.emit(True, (mesh_path, geometry_path, quality))
        except Exception as exc:
            if self._cancelled:
                self.log.emit("Palace: *** MESHING ABORTED — process killed ***\n")
                self.done.emit(False, "Meshing cancelled by user")
                return
            # generate_mesh_subprocess() already raises with the subprocess's
            # full accumulated output (including its own nested traceback if
            # it failed) as the message -- str(exc) is that text directly,
            # without an extra layer of this-file's-own traceback framing.
            self.done.emit(False, str(exc))


class CmdMesh:
    _worker = None  # class-level ref keeps the thread alive until it finishes

    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Generate Mesh",
            "ToolTip": (
                "Generate the mesh (Gmsh or Netgen, per the Simulation "
                "object's Mesh Backend setting) and save it to disk.\n"
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
        from palace.embedded_files import new_scratch_dir
        mesh_dir = new_scratch_dir(doc, "mesh")

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

        _t0_mesh = time.time()

        def _on_done(ok, result):
            CmdMesh._worker = None
            if ok:
                mesh_path, geometry_path, quality = result
                if quality.get("is_bad"):
                    from palace.meshing import format_quality_warning
                    reply = QtWidgets.QMessageBox.question(
                        None, "Low-Quality Mesh",
                        f"{format_quality_warning(quality)}\n\nKeep this mesh?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.No,
                    )
                    if reply != QtWidgets.QMessageBox.Yes:
                        _log("Palace: Mesh discarded (quality check declined).\n")
                        shutil.rmtree(mesh_dir, ignore_errors=True)
                        if console is not None:
                            console.set_status("Mesh discarded (low quality)")
                        return
                from palace.embedded_files import embed
                embed(mesh_obj, "MeshFile", mesh_path, "mesh.msh")
                embed(mesh_obj, "GeometryFile", geometry_path, "geometry.step")
                from features.mesh import store_mesh_quality
                store_mesh_quality(mesh_obj, quality)
                # embed() copies rather than consumes a source living in a
                # subdirectory of the transient dir -- mesh_dir must be cleaned
                # up explicitly or it leaks on every "Generate Mesh" click.
                shutil.rmtree(mesh_dir, ignore_errors=True)
                try:
                    from features import find_simulation
                    sim = find_simulation(doc)
                    if sim:
                        # Resolved (embedded, permanent) path, not the scratch mesh_path
                        # that mesh_dir's rmtree above just deleted -- backward compat.
                        sim.MeshFile = mesh_obj.MeshFile
                except Exception:
                    pass
                # Call execute() directly — setting a PropertyFileIncluded doesn't
                # reliably mark Mesh::FeaturePython dirty before doc.recompute().
                mesh_obj.Proxy.execute(mesh_obj)
                doc.recompute()
                elapsed = _fmt_elapsed(time.time() - _t0_mesh)
                _log(f"Palace: Mesh written to {mesh_path}\n")
                _log(f"Palace: Mesh generated in {elapsed}\n")
                if console is not None:
                    console.set_status(f"Mesh complete ({elapsed})")
            else:
                shutil.rmtree(mesh_dir, ignore_errors=True)
                FreeCAD.Console.PrintError(f"Palace meshing failed:\n{result}\n")
                if console is not None:
                    console.write(f"ERROR:\n{result}\n")
                    console.set_status("Mesh error")
                QtWidgets.QMessageBox.critical(
                    None, "Mesh Error", result.splitlines()[-1]
                )

        _log("Palace: Generating mesh…\n")
        CmdMesh._worker = _MeshWorker(doc, mesh_dir)
        CmdMesh._worker.log.connect(_log)
        CmdMesh._worker.done.connect(_on_done)
        CmdMesh._worker.start()


_STOP_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Stop.svg")


class CmdStopMesh:
    def GetResources(self):
        return {
            "Pixmap": _STOP_ICON,
            "MenuText": "Stop Meshing",
            "ToolTip": "Terminate the mesh generation that is currently running.",
        }

    def IsActive(self):
        return CmdMesh._worker is not None and CmdMesh._worker.isRunning()

    def Activated(self):
        worker = CmdMesh._worker
        if worker is None or not worker.isRunning():
            return
        reply = QtWidgets.QMessageBox.question(
            None,
            "Stop Meshing",
            "Terminate the mesh generation currently running?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            worker.stop()
            FreeCAD.Console.PrintMessage(
                "Palace: Stop requested — waiting for mesh process to exit…\n"
            )

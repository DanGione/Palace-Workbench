"""Run Gmsh mesh generation in a subprocess and stream output back.

Mirrors palace/runner.py's run_palace(): a plain, thread-agnostic blocking
function with callbacks, so any caller (a QThread worker, or a coordinator
that just calls it synchronously) can use it the same way. Gmsh's C++ core
cannot be cleanly interrupted mid-call from Python, and a crash inside it
can take the whole process down -- running it in a real subprocess (killed
via SIGKILL, exactly like the Palace subprocess) gives every caller both a
reliable Stop and crash isolation, without changing palace/meshing.py at all.
"""
import json
import os
import signal
import subprocess

_WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "mesh_worker_main.py")
_RESULT_FILENAME = "_mesh_result.json"


def generate_mesh_subprocess(doc, output_dir, log_fn=None, proc_callback=None):
    """Generate a mesh for *doc* in a freecadcmd subprocess.

    Same contract as palace.meshing.generate_mesh(): blocking, returns
    (mesh_path, geometry_path, quality) on success, raises on failure --
    every existing call site's try/except keeps working unchanged.

    Parameters
    ----------
    doc          : FreeCAD document (its current in-memory state, including
                   unsaved changes, is what gets meshed -- see saveCopy()).
    output_dir   : directory generate_mesh() writes mesh.msh/geometry.step
                   into; also used to pass the document snapshot and result.
    log_fn       : callable(str), called with each line of subprocess output.
    proc_callback: callable(subprocess.Popen), called right after the
                   subprocess is started -- callers use this to stash a
                   reference for kill_mesh_process() to terminate it later.
    """
    snapshot_path = os.path.join(output_dir, "_doc_snapshot.FCStd")
    doc.saveCopy(snapshot_path)

    result_path = os.path.join(output_dir, _RESULT_FILENAME)
    if os.path.isfile(result_path):
        os.unlink(result_path)   # stale result from a previous attempt in this dir

    proc = subprocess.Popen(
        # --pass stops freecadcmd's own CLI from treating snapshot_path/
        # output_dir as files to auto-open as documents (verified empirically
        # -- without it, it silently re-imports the snapshot a second time
        # and fails trying to open output_dir as a document).
        ["freecadcmd", _WORKER_SCRIPT, "--pass", snapshot_path, output_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",   # match the worker's reconfigured stdout explicitly,
        errors="replace",  # rather than trust the parent locale's default
        start_new_session=True,   # own process group -> killpg kills it cleanly
    )

    if proc_callback is not None:
        proc_callback(proc)

    lines = []
    for line in proc.stdout:
        lines.append(line)
        if log_fn is not None:
            log_fn(line)

    proc.wait()

    try:
        os.unlink(snapshot_path)
    except OSError:
        pass

    if proc.returncode != 0:
        raise RuntimeError(
            "".join(lines) or f"mesh subprocess exited with code {proc.returncode}"
        )

    with open(result_path, "r", encoding="utf-8") as f:
        result = json.load(f)
    os.unlink(result_path)
    return result["mesh_path"], result["geometry_path"], result["quality"]


def kill_mesh_process(proc):
    """Kill a mesh subprocess (and its process group) started above.

    Shared by every caller's stop/cancel path -- _MeshWorker.stop(),
    _SweepCoordinator.cancel(), _OptimizationCoordinator.cancel() -- same
    approach as _PassWorker.stop() in commands/cmd_run.py.
    """
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

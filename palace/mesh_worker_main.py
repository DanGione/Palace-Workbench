"""Entry point run via `freecadcmd` by palace/mesh_runner.py, in a subprocess.

Opens the document snapshot handed to it and calls generate_mesh() -- fully
unchanged -- exactly as commands/cmd_mesh.py used to do in-process. Running
this in a separate OS process is what makes it possible to kill an
in-progress mesh (Gmsh's C++ core can't be interrupted mid-call from Python)
and keeps a Gmsh crash from taking the main FreeCAD session down with it.

Usage: freecadcmd mesh_worker_main.py --pass <snapshot.FCStd> <output_dir>
Writes <output_dir>/_mesh_result.json on success; prints a traceback and
exits non-zero on failure.

Three freecadcmd quirks this works around (verified empirically against the
actual freecadcmd binary, not assumed from docs):
  - sys.argv here is [freecadcmd, this_script.py, <our args>...] -- the
    script's own path occupies argv[1], unlike a plain `python3 script.py`.
  - Without --pass before our arguments, freecadcmd's own CLI treats every
    positional argument as a file to auto-open as a *document* (it silently
    imports our snapshot a second time and then fails to open the output
    directory as a document) -- palace/mesh_runner.py passes --pass.
  - __name__ is the script's own basename here, never "__main__" -- a plain
    `if __name__ == "__main__":` guard never fires, so main() must run
    unconditionally at module scope.
  - stdout is NOT flushed on sys.exit() the way a normal CPython process
    would -- every line must be flushed explicitly or it's lost, including
    the traceback on failure.
"""
import json
import os
import sys
import traceback

# generate_mesh()'s own log lines routinely include non-ASCII characters
# (e.g. "…", "→") and freecadcmd's stdout defaults to strict ASCII in this
# environment -- without this, printing any such line raises
# UnicodeEncodeError, masking whatever the real underlying error was.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _emit(msg):
    print(msg, end="" if msg.endswith("\n") else "\n", flush=True)


def main():
    # argv: [freecadcmd, this_script.py, --pass, <snapshot>, <output_dir>]
    snapshot_path, output_dir = sys.argv[-2], sys.argv[-1]

    import FreeCAD
    doc = FreeCAD.openDocument(snapshot_path)

    from palace.mesh_dispatch import generate_mesh
    mesh_path, geometry_path, quality = generate_mesh(doc, output_dir, log_fn=_emit)

    result_path = os.path.join(output_dir, "_mesh_result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(
            {"mesh_path": mesh_path, "geometry_path": geometry_path, "quality": quality},
            f,
        )


try:
    main()
except Exception:
    _emit(traceback.format_exc())
    sys.exit(1)

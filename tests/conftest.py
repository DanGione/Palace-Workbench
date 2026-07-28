"""Make FreeCAD's ``Part``/``FreeCAD`` modules importable from plain pytest.

FreeCAD's own interpreter (``freecadcmd``) adds its lib/Ext directories to
sys.path automatically; a plain ``python -m pytest`` run does not.  This adds
them before any test module imports ``Part``/``FreeCAD``.
"""

import glob
import os
import sys


def _add_freecad_to_path():
    candidates = (
        [os.environ.get("FREECAD_LIB_DIR")]
        + glob.glob("/opt/conda/lib")
        + glob.glob("/usr/lib/freecad*/lib")
        + glob.glob("/usr/lib/freecad/lib")
    )
    for lib_dir in filter(None, candidates):
        if not os.path.isdir(lib_dir):
            continue
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        ext_dir = os.path.join(os.path.dirname(lib_dir), "Ext")
        if os.path.isdir(ext_dir) and ext_dir not in sys.path:
            sys.path.insert(0, ext_dir)
        return


_add_freecad_to_path()

# FreeCAD's App kernel must be initialized before its "Part" extension module
# is imported (importing Part first segfaults) -- force FreeCAD to load here,
# before any test module gets a chance to `import Part` on its own.
import FreeCAD  # noqa: E402,F401

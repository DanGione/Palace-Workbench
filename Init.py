import FreeCAD
import os
import sys

# Ensure this workbench directory is on the Python path so that saved
# documents can restore Palace proxy objects without the GUI loaded.
_wb_dir = os.path.dirname(__file__) if "__file__" in dir() else ""
if _wb_dir not in sys.path:
    sys.path.insert(0, _wb_dir)

"""
Headless meshing test for Microstrip_test.FCStd.

Run with:
    freecadcmd /workspace/test_mesh_microstrip.py

Exercises generate_mesh() only (no Palace run) so we can capture Gmsh
errors/crashes without the GUI swallowing them.
"""

import sys
import os
sys.path.insert(0, "/workspace")

import FreeCAD

FCStd = "/workspace/Microstrip_test.FCStd"
output_dir = "/workspace/palace_output"

print(f"[test] Loading {FCStd}")
doc = FreeCAD.openDocument(FCStd)
doc.recompute()

print("[test] Document objects:")
for obj in doc.Objects:
    print(f"  {obj.Name} ({obj.TypeId})")
    for prop in ["SimulationType", "AirboxShape", "PortIndex", "PortGeometryType",
                 "Permittivity", "ConductorType", "MeshAttribute",
                 "MeshCharacteristicLengthMax"]:
        if hasattr(obj, prop):
            print(f"    .{prop} = {getattr(obj, prop)!r}")

print("\n[test] Running generate_mesh...")
from palace.meshing import generate_mesh

def log(msg):
    sys.stdout.write(msg)
    sys.stdout.flush()

try:
    mesh_path = generate_mesh(doc, output_dir, log_fn=log)
    print(f"\n[test] Mesh written: {mesh_path}")

    # Check element types in the mesh
    import re
    type_counts = {}
    in_elements = False
    with open(mesh_path) as f:
        for line in f:
            if line.strip() == "$Elements":
                in_elements = True
                continue
            if line.strip() == "$EndElements":
                break
            if in_elements:
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    t = parts[1]
                    type_counts[t] = type_counts.get(t, 0) + 1

    # type 9 = tri6 (surface), type 11 = tet10 (volume)
    print(f"[test] Element type counts: {type_counts}")
    tri6  = type_counts.get("9",  0)
    tet10 = type_counts.get("11", 0)
    print(f"[test]   tri6  (surface): {tri6}")
    print(f"[test]   tet10 (volume):  {tet10}")
    if tet10 == 0:
        print("[test] FAIL: no tet10 volume elements — Bug 1 still present")
        sys.exit(1)
    else:
        print("[test] PASS: volume elements present")

except Exception as exc:
    import traceback
    print(f"\n[test] EXCEPTION: {exc}")
    traceback.print_exc()
    sys.exit(1)

sys.exit(0)

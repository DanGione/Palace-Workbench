"""Regression coverage for PalaceMesh's "hide everything by default" behavior.

A freshly-generated mesh should default to nothing visible (see
features/mesh.py:execute()) -- expensive to render everything on a large
mesh by default. But execute() reruns on *any* recompute of the object, not
just when a new mesh was actually generated -- e.g. the Mesh Panel's
accept() (panels/mesh_panel.py) always calls doc.recompute() after applying
sizing settings, whether or not Generate Mesh was clicked. A production
document hit exactly this: checking boxes in the "Visible Attributes" list,
then clicking OK, silently wiped the selection back to all-hidden. execute()
must only reset visibility when the embedded mesh content actually changed,
not on every incidental recompute -- and it can't tell that apart by
MeshFile's resolved path, since App::PropertyFileIncluded reuses the same
resolved path across re-embeds under the same archive name (confirmed
empirically) even when the content is a genuinely new mesh.
"""
import time

import pytest

import FreeCAD

from features.mesh import create_palace_mesh
from palace.embedded_files import embed


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestMeshVisibilityDefaults")
    yield d
    FreeCAD.closeDocument(d.Name)


def _write_box_mesh(path, size, surf_attr, vol_attr):
    """Write a tiny real .msh file with one box, given physical-group attrs."""
    gmsh = pytest.importorskip("gmsh")
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add(f"box_{surf_attr}_{vol_attr}")
        gmsh.model.occ.addBox(0, 0, 0, size, size, size)
        gmsh.model.occ.synchronize()
        vols = [t for _, t in gmsh.model.getEntities(3)]
        surfs = [t for _, t in gmsh.model.getEntities(2)]
        gmsh.model.addPhysicalGroup(3, vols, vol_attr)
        gmsh.model.addPhysicalGroup(2, surfs, surf_attr)
        gmsh.model.mesh.generate(3)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)  # _parse_msh2 is v2.2-specific
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


def test_execute_hides_everything_on_first_generation(doc, tmp_path):
    mesh_obj = create_palace_mesh(doc)
    mesh_path = tmp_path / "a.msh"
    _write_box_mesh(mesh_path, 5, surf_attr=2, vol_attr=1)
    embed(mesh_obj, "MeshFile", mesh_path, "mesh.msh")

    mesh_obj.Proxy.execute(mesh_obj)

    assert list(mesh_obj.HiddenSurfAttributes) == [2]
    assert list(mesh_obj.HiddenVolAttributes) == [1]


def test_execute_preserves_manual_visibility_on_incidental_recompute(doc, tmp_path):
    mesh_obj = create_palace_mesh(doc)
    mesh_path = tmp_path / "a.msh"
    _write_box_mesh(mesh_path, 5, surf_attr=2, vol_attr=1)
    embed(mesh_obj, "MeshFile", mesh_path, "mesh.msh")
    mesh_obj.Proxy.execute(mesh_obj)

    # Simulate the user checking a box in "Visible Attributes" -- exactly
    # what panels/mesh_panel.py's _on_attr_visibility_changed does.
    mesh_obj.HiddenSurfAttributes = []
    mesh_obj.HiddenVolAttributes = []

    # Simulate clicking OK on the Mesh Panel: accept() calls doc.recompute()
    # unconditionally, which reruns execute() even though no new mesh was
    # generated.
    mesh_obj.Proxy.execute(mesh_obj)

    assert list(mesh_obj.HiddenSurfAttributes) == [], (
        "an incidental recompute must not silently discard the user's "
        "chosen Visible Attributes")
    assert list(mesh_obj.HiddenVolAttributes) == []


def test_execute_resets_visibility_when_a_new_mesh_is_embedded(doc, tmp_path):
    mesh_obj = create_palace_mesh(doc)
    mesh_path_a = tmp_path / "a.msh"
    _write_box_mesh(mesh_path_a, 5, surf_attr=2, vol_attr=1)
    embed(mesh_obj, "MeshFile", mesh_path_a, "mesh.msh")
    mesh_obj.Proxy.execute(mesh_obj)

    # User opts back into seeing everything from the first mesh.
    mesh_obj.HiddenSurfAttributes = []
    mesh_obj.HiddenVolAttributes = []
    mesh_obj.Proxy.execute(mesh_obj)  # incidental recompute -- stays visible

    # A genuinely new mesh is generated and re-embedded under the same
    # archive name (the real Generate Mesh flow) -- different content,
    # different attrs, but MeshFile's resolved path does not change. A real
    # mesh generation always takes far longer than a filesystem mtime tick;
    # the sleep here only guards this test's own back-to-back writes.
    time.sleep(0.05)
    mesh_path_b = tmp_path / "b.msh"
    _write_box_mesh(mesh_path_b, 8, surf_attr=5, vol_attr=4)
    resolved_before = mesh_obj.MeshFile
    embed(mesh_obj, "MeshFile", mesh_path_b, "mesh.msh")
    assert mesh_obj.MeshFile == resolved_before, (
        "test assumption: re-embedding under the same archive name reuses "
        "the same resolved path")

    mesh_obj.Proxy.execute(mesh_obj)

    assert list(mesh_obj.HiddenSurfAttributes) == [5], (
        "a genuinely new mesh must still default to hidden, even though "
        "MeshFile's resolved path didn't change")
    assert list(mesh_obj.HiddenVolAttributes) == [4]

import FreeCAD
import pytest

from commands.cmd_export import _default_export_dir, _is_exportable
from features.mesh import create_palace_mesh
from features.simulation import create_simulation
from palace.embedded_files import embed, new_scratch_file


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestCmdExport")
    yield d
    FreeCAD.closeDocument(d.Name)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_default_export_dir_uses_document_folder_when_saved(doc, tmp_path):
    fcstd = tmp_path / "MyProject.FCStd"
    doc.saveAs(str(fcstd))
    assert _default_export_dir(doc) == str(tmp_path)


def test_default_export_dir_falls_back_to_home_when_unsaved(doc):
    import os
    assert _default_export_dir(doc) == os.path.expanduser("~")


def test_default_export_dir_handles_none_document():
    import os
    assert _default_export_dir(None) == os.path.expanduser("~")


def test_is_exportable_false_when_nothing_generated(doc):
    create_palace_mesh(doc)
    from features import find_palace_mesh
    assert _is_exportable(doc, find_palace_mesh, "MeshFile") is False


def test_is_exportable_true_after_embedding(doc, tmp_path):
    mesh_obj = create_palace_mesh(doc)
    scratch = new_scratch_file(doc, "mesh")
    _write(scratch, "mesh contents")
    embed(mesh_obj, "MeshFile", scratch, "mesh.msh")

    from features import find_palace_mesh
    assert _is_exportable(doc, find_palace_mesh, "MeshFile") is True


def test_is_exportable_false_for_no_document():
    from features import find_palace_mesh
    assert _is_exportable(None, find_palace_mesh, "MeshFile") is False


def test_is_exportable_checks_config_file_via_simulation(doc, tmp_path):
    sim = create_simulation(doc)
    from features import find_simulation
    assert _is_exportable(doc, find_simulation, "ConfigFile") is False

    scratch = new_scratch_file(doc, "config")
    _write(scratch, '{"ok": true}')
    embed(sim, "ConfigFile", scratch, "palace_config.json")
    assert _is_exportable(doc, find_simulation, "ConfigFile") is True

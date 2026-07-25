"""End-to-end migration coverage for the App::PropertyFileIncluded refactor.

Each test simulates re-opening a document that was saved before this feature
existed: the target property is forced back down to a plain App::PropertyString
pointing at a real "legacy sibling-folder" file, then the object's real
onDocumentRestored() is invoked directly (exactly what FreeCAD calls when a
document is reopened) -- not just the generic helper in isolation.
"""

import FreeCAD
import pytest

from features.mesh import create_palace_mesh
from features.simulation import create_simulation
from features.sweep import create_sweep


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestEmbeddingMigration")
    yield d
    FreeCAD.closeDocument(d.Name)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _force_legacy_string(obj, name, group, tip, value):
    """Downgrade an already-PropertyFileIncluded property back to a plain
    PropertyString with *value*, mimicking a pre-refactor on-disk document."""
    if hasattr(obj, name):
        obj.removeProperty(name)
    obj.addProperty("App::PropertyString", name, group, tip)
    setattr(obj, name, value)


def test_palace_mesh_migrates_mesh_file_and_geometry_file(doc, tmp_path):
    mesh_obj = create_palace_mesh(doc)

    legacy_mesh = tmp_path / "mesh.msh"
    legacy_step = tmp_path / "geometry.step"
    _write(str(legacy_mesh), "legacy mesh contents")
    _write(str(legacy_step), "legacy step contents")
    _force_legacy_string(mesh_obj, "MeshFile", "Mesh", "tip", str(legacy_mesh))
    _force_legacy_string(mesh_obj, "GeometryFile", "Mesh", "tip", str(legacy_step))

    mesh_obj.Proxy.onDocumentRestored(mesh_obj)

    assert mesh_obj.getTypeIdOfProperty("MeshFile") == "App::PropertyFileIncluded"
    assert mesh_obj.getTypeIdOfProperty("GeometryFile") == "App::PropertyFileIncluded"
    with open(mesh_obj.MeshFile, encoding="utf-8") as fh:
        assert fh.read() == "legacy mesh contents"
    with open(mesh_obj.GeometryFile, encoding="utf-8") as fh:
        assert fh.read() == "legacy step contents"
    # non-destructive
    assert legacy_mesh.is_file()
    assert legacy_step.is_file()


def test_simulation_container_migrates_via_sibling_convention_hint(doc, tmp_path):
    fcstd = tmp_path / "MyProject.FCStd"
    doc.saveAs(str(fcstd))
    sim = create_simulation(doc)

    # ResultsFile/ConfigFile never existed as properties before this refactor
    # -- there is nothing to "downgrade"; recovery can only come from the
    # sibling-folder convention path derived from doc.FileName.
    project_dir = tmp_path / "MyProject"
    project_dir.mkdir()
    _write(str(project_dir / "results.nc"), "legacy results contents")
    _write(str(project_dir / "palace_config.json"), '{"legacy": true}')

    sim.Proxy.onDocumentRestored(sim)

    assert sim.getTypeIdOfProperty("ResultsFile") == "App::PropertyFileIncluded"
    assert sim.getTypeIdOfProperty("ConfigFile") == "App::PropertyFileIncluded"
    with open(sim.ResultsFile, encoding="utf-8") as fh:
        assert fh.read() == "legacy results contents"
    with open(sim.ConfigFile, encoding="utf-8") as fh:
        assert fh.read() == '{"legacy": true}'


def test_sweep_container_migrates_sweep_results_file(doc, tmp_path):
    sweep_obj = create_sweep(doc)

    legacy_sweep = tmp_path / "sweep.nc"
    _write(str(legacy_sweep), "legacy sweep contents")
    _force_legacy_string(sweep_obj, "SweepResultsFile", "Sweep", "tip", str(legacy_sweep))

    sweep_obj.Proxy.onDocumentRestored(sweep_obj)

    assert sweep_obj.getTypeIdOfProperty("SweepResultsFile") == "App::PropertyFileIncluded"
    with open(sweep_obj.SweepResultsFile, encoding="utf-8") as fh:
        assert fh.read() == "legacy sweep contents"
    assert legacy_sweep.is_file()


def test_migration_is_idempotent_across_repeated_restores(doc, tmp_path):
    sweep_obj = create_sweep(doc)
    legacy_sweep = tmp_path / "sweep.nc"
    _write(str(legacy_sweep), "legacy sweep contents")
    _force_legacy_string(sweep_obj, "SweepResultsFile", "Sweep", "tip", str(legacy_sweep))

    sweep_obj.Proxy.onDocumentRestored(sweep_obj)
    first = sweep_obj.SweepResultsFile

    # simulate the document being reopened again in a later session
    sweep_obj.Proxy.onDocumentRestored(sweep_obj)
    second = sweep_obj.SweepResultsFile

    assert first == second


def test_new_document_never_generated_anything_has_no_migration_errors(doc):
    # A brand-new document with no sibling folder and no legacy properties at
    # all must restore cleanly with everything empty -- no exceptions, no
    # spurious embedding of nonexistent files.
    mesh_obj = create_palace_mesh(doc)
    sim = create_simulation(doc)
    sweep_obj = create_sweep(doc)

    mesh_obj.Proxy.onDocumentRestored(mesh_obj)
    sim.Proxy.onDocumentRestored(sim)
    sweep_obj.Proxy.onDocumentRestored(sweep_obj)

    assert mesh_obj.MeshFile == ""
    assert mesh_obj.GeometryFile == ""
    assert sim.ResultsFile == ""
    assert sim.ConfigFile == ""
    assert sweep_obj.SweepResultsFile == ""

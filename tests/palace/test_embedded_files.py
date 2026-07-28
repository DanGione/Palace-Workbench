import os
import tempfile

import FreeCAD
import pytest

from palace import embedded_files as ef
from palace import results_db


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestEmbeddedFiles")
    yield d
    FreeCAD.closeDocument(d.Name)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_new_scratch_file_paths_are_unique(doc):
    a = ef.new_scratch_file(doc, "prefix")
    b = ef.new_scratch_file(doc, "prefix")
    assert a != b
    assert not os.path.exists(a)


def test_new_scratch_file_works_before_document_is_ever_saved(doc):
    assert doc.FileName == ""
    path = ef.new_scratch_file(doc, "unsaved")
    assert isinstance(path, str) and path


def test_embedding_a_flat_scratch_file_consumes_it(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    flat = ef.new_scratch_file(doc, "flat")
    _write(flat, "content")

    ef.embed(obj, "Blob", flat, "blob.txt")

    assert not os.path.isfile(flat)


def test_embedding_a_file_from_a_scratch_dir_does_not_remove_it(doc):
    # Regression / documented gotcha: unlike new_scratch_file(), a source path
    # nested inside a new_scratch_dir() directory is always COPIED by embed(),
    # never consumed. Callers (e.g. CmdRun.Activated, CmdMesh.Activated) MUST
    # shutil.rmtree() the scratch dir themselves after embedding, or every
    # mesh/geometry generation leaks a scratch directory for the life of the
    # document. This test exists so a future change to embedded_files.py (or
    # to FreeCAD itself) that alters this behavior gets caught immediately.
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "MeshFile", "Group", "tip")
    mesh_dir = ef.new_scratch_dir(doc, "mesh_run")
    mesh_path = os.path.join(mesh_dir, "mesh.msh")
    _write(mesh_path, "mesh contents")

    ef.embed(obj, "MeshFile", mesh_path, "mesh.msh")

    assert os.path.isfile(mesh_path)  # NOT consumed -- still sitting in mesh_dir
    assert ef.resolve(obj, "MeshFile") != mesh_path  # embed() made its own copy


def test_new_scratch_dir_is_created_and_empty(doc):
    d = ef.new_scratch_dir(doc, "meshdir")
    assert os.path.isdir(d)
    assert os.listdir(d) == []


def test_new_scratch_dir_unique_across_two_documents():
    doc_a = FreeCAD.newDocument("SharedScratchA")
    doc_b = FreeCAD.newDocument("SharedScratchB")
    try:
        a = ef.new_scratch_dir(doc_a, "x")
        b = ef.new_scratch_dir(doc_b, "x")
        assert a != b
    finally:
        FreeCAD.closeDocument(doc_a.Name)
        FreeCAD.closeDocument(doc_b.Name)


def test_embed_and_resolve_round_trip_before_any_save(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    scratch = ef.new_scratch_file(doc, "content")
    _write(scratch, "hello world")

    ef.embed(obj, "Blob", scratch, "blob.txt")
    resolved = ef.resolve(obj, "Blob")

    assert os.path.isfile(resolved)
    with open(resolved, encoding="utf-8") as fh:
        assert fh.read() == "hello world"


def test_resolve_returns_empty_string_when_unset(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    assert ef.resolve(obj, "Blob") == ""


def test_ensure_property_is_idempotent(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    ef.ensure_property(obj, "Blob", "Group", "tip")  # must not raise
    assert obj.getTypeIdOfProperty("Blob") == "App::PropertyFileIncluded"


def test_reembedding_under_same_archive_name_reuses_resolved_path(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")

    scratch1 = ef.new_scratch_file(doc, "v1")
    _write(scratch1, "version 1")
    ef.embed(obj, "Blob", scratch1, "blob.txt")
    path1 = ef.resolve(obj, "Blob")

    scratch2 = ef.new_scratch_file(doc, "v2")
    _write(scratch2, "version 2")
    ef.embed(obj, "Blob", scratch2, "blob.txt")
    path2 = ef.resolve(obj, "Blob")

    assert path1 == path2
    with open(path2, encoding="utf-8") as fh:
        assert fh.read() == "version 2"


def test_clear_resets_a_populated_property_to_empty(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    scratch = ef.new_scratch_file(doc, "content")
    _write(scratch, "hello world")
    ef.embed(obj, "Blob", scratch, "blob.txt")
    assert ef.resolve(obj, "Blob")

    ef.clear(obj, "Blob")

    assert ef.resolve(obj, "Blob") == ""
    assert obj.getTypeIdOfProperty("Blob") == "App::PropertyFileIncluded"


def test_clear_preserves_group_and_tooltip(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "MyGroup", "my tooltip")
    scratch = ef.new_scratch_file(doc, "content")
    _write(scratch, "hello")
    ef.embed(obj, "Blob", scratch, "blob.txt")

    ef.clear(obj, "Blob")

    assert obj.getGroupOfProperty("Blob") == "MyGroup"
    assert obj.getDocumentationOfProperty("Blob") == "my tooltip"


def test_clear_on_never_set_property_is_a_noop(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    ef.clear(obj, "Blob")  # must not raise
    assert ef.resolve(obj, "Blob") == ""


def test_clear_on_missing_property_is_a_noop(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.clear(obj, "NoSuchProp")  # must not raise


def test_save_close_reopen_round_trip_with_real_netcdf_dataset():
    # Manages its own document lifecycle (rather than the shared `doc` fixture)
    # since the test itself must close the document mid-test to reopen it --
    # the fixture's teardown would otherwise try to close it a second time.
    doc = FreeCAD.newDocument("RoundTripNetCDF")
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "ResultsFile", "Palace", "tip")

    ds = results_db.build_dataset(
        freq_ghz=[1.0, 2.0],
        s_mag_db={(1, 1): [-10.0, -12.0]},
        s_phase_deg={(1, 1): [0.0, 0.0]},
    )
    scratch = ef.new_scratch_file(doc, "results")
    results_db.save_dataset(ds, scratch)
    ef.embed(obj, "ResultsFile", scratch, "results.nc")

    fcstd_path = tempfile.mktemp(suffix=".FCStd")
    try:
        doc.saveAs(fcstd_path)
        FreeCAD.closeDocument(doc.Name)

        reopened = FreeCAD.openDocument(fcstd_path)
        try:
            obj2 = reopened.getObject("Obj")
            resolved = ef.resolve(obj2, "ResultsFile")
            assert os.path.isfile(resolved)
            loaded = results_db.load_dataset(resolved)
            assert loaded.attrs["freq_unit"] == "GHz"
            assert list(loaded["freq"].values) == [1.0, 2.0]
            loaded.close()
        finally:
            FreeCAD.closeDocument(reopened.Name)
    finally:
        if os.path.isfile(fcstd_path):
            os.remove(fcstd_path)


def test_copy_for_rewrite_restores_write_permission_on_resolved_content(doc, tmp_path):
    # Regression: resolve() returns a path FreeCAD marks read-only (0400) --
    # a plain shutil.copy2() would preserve that onto the copy and then fail
    # the moment a caller (e.g. xarray's to_netcdf) tries to write into it.
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "Blob", "Group", "tip")
    scratch = ef.new_scratch_file(doc, "content")
    _write(scratch, "hello world")
    ef.embed(obj, "Blob", scratch, "blob.txt")
    resolved = ef.resolve(obj, "Blob")
    assert not os.access(resolved, os.W_OK)  # confirms FreeCAD's read-only marking

    dest = str(tmp_path / "copy.txt")
    ef.copy_for_rewrite(resolved, dest)

    assert os.access(dest, os.W_OK)
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(" -- appended")  # must not raise
    with open(dest, encoding="utf-8") as fh:
        assert fh.read() == "hello world -- appended"


def test_legacy_sibling_path_none_for_unsaved_document(doc):
    assert ef.legacy_sibling_path(doc, "results.nc") is None


def test_legacy_sibling_path_matches_stem_convention(doc, tmp_path):
    fcstd = tmp_path / "MyProject.FCStd"
    doc.saveAs(str(fcstd))
    expected = str(tmp_path / "MyProject" / "results.nc")
    assert ef.legacy_sibling_path(doc, "results.nc") == expected


def test_migrate_legacy_string_property_converts_and_preserves_original(doc, tmp_path):
    legacy_file = tmp_path / "sweep.nc"
    _write(str(legacy_file), "legacy sweep contents")

    obj = doc.addObject("App::FeaturePython", "Obj")
    obj.addProperty("App::PropertyString", "SweepResultsFile", "Sweep", "tip")
    obj.SweepResultsFile = str(legacy_file)

    ef.migrate_legacy_string_property(obj, "SweepResultsFile", "Sweep", "tip")

    assert obj.getTypeIdOfProperty("SweepResultsFile") == "App::PropertyFileIncluded"
    resolved = ef.resolve(obj, "SweepResultsFile")
    with open(resolved, encoding="utf-8") as fh:
        assert fh.read() == "legacy sweep contents"
    # non-destructive: the original sibling-folder file must still exist
    assert legacy_file.is_file()


def test_migrate_legacy_string_property_uses_hint_when_value_is_empty(doc, tmp_path):
    hint_file = tmp_path / "results.nc"
    _write(str(hint_file), "hinted contents")

    obj = doc.addObject("App::FeaturePython", "Obj")
    obj.addProperty("App::PropertyString", "ResultsFile", "Palace", "tip")
    # value left empty -- nothing to migrate directly, must fall back to legacy_hint

    ef.migrate_legacy_string_property(
        obj, "ResultsFile", "Palace", "tip", legacy_hint=str(hint_file)
    )

    resolved = ef.resolve(obj, "ResultsFile")
    assert resolved
    with open(resolved, encoding="utf-8") as fh:
        assert fh.read() == "hinted contents"


def test_migrate_legacy_string_property_is_a_noop_second_time(doc, tmp_path):
    legacy_file = tmp_path / "sweep.nc"
    _write(str(legacy_file), "legacy sweep contents")

    obj = doc.addObject("App::FeaturePython", "Obj")
    obj.addProperty("App::PropertyString", "SweepResultsFile", "Sweep", "tip")
    obj.SweepResultsFile = str(legacy_file)

    ef.migrate_legacy_string_property(obj, "SweepResultsFile", "Sweep", "tip")
    resolved_first = ef.resolve(obj, "SweepResultsFile")

    # simulate a later session's onDocumentRestored re-running the same call
    ef.migrate_legacy_string_property(obj, "SweepResultsFile", "Sweep", "tip")
    resolved_second = ef.resolve(obj, "SweepResultsFile")

    assert resolved_first == resolved_second


def test_migrate_legacy_string_property_finds_hint_even_when_prop_pre_created_empty(doc, tmp_path):
    # Mirrors the real onDocumentRestored ordering: _init_properties() runs
    # first and always pre-creates brand-new properties (ResultsFile,
    # GeometryFile, ConfigFile) as an *empty* PropertyFileIncluded before
    # migration ever gets a chance to run -- migration must still recover
    # the legacy sibling-folder file in that case, not treat "already the
    # right type" as "already migrated".
    hint_file = tmp_path / "geometry.step"
    _write(str(hint_file), "step contents")

    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "GeometryFile", "Mesh", "tip")  # pre-created, empty

    ef.migrate_legacy_string_property(
        obj, "GeometryFile", "Mesh", "tip", legacy_hint=str(hint_file)
    )

    resolved = ef.resolve(obj, "GeometryFile")
    assert resolved
    with open(resolved, encoding="utf-8") as fh:
        assert fh.read() == "step contents"


def test_migrate_legacy_string_property_does_not_clobber_existing_embedded_content(doc, tmp_path):
    obj = doc.addObject("App::FeaturePython", "Obj")
    ef.ensure_property(obj, "ResultsFile", "Palace", "tip")
    scratch = ef.new_scratch_file(doc, "current")
    _write(scratch, "current session contents")
    ef.embed(obj, "ResultsFile", scratch, "results.nc")

    stale_hint = tmp_path / "results.nc"
    _write(str(stale_hint), "stale sibling-folder contents")

    ef.migrate_legacy_string_property(
        obj, "ResultsFile", "Palace", "tip", legacy_hint=str(stale_hint)
    )

    resolved = ef.resolve(obj, "ResultsFile")
    with open(resolved, encoding="utf-8") as fh:
        assert fh.read() == "current session contents"


def test_migrate_legacy_string_property_no_file_leaves_property_empty(doc):
    obj = doc.addObject("App::FeaturePython", "Obj")
    obj.addProperty("App::PropertyString", "ConfigFile", "Palace", "tip")
    obj.ConfigFile = "/nonexistent/path/palace_config.json"

    ef.migrate_legacy_string_property(obj, "ConfigFile", "Palace", "tip")

    assert obj.getTypeIdOfProperty("ConfigFile") == "App::PropertyFileIncluded"
    assert ef.resolve(obj, "ConfigFile") == ""

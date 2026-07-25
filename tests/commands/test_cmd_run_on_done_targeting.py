import os

import FreeCAD
import pytest

from commands.cmd_run import _on_done
from features.simulation import create_simulation
from palace.embedded_files import resolve


@pytest.fixture
def two_docs():
    doc_a = FreeCAD.newDocument("OnDoneTargetA")
    doc_b = FreeCAD.newDocument("OnDoneTargetB")  # becomes ActiveDocument
    yield doc_a, doc_b
    FreeCAD.closeDocument(doc_a.Name)
    FreeCAD.closeDocument(doc_b.Name)


def _write_port_s_csv(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("freq (GHz),|S[1][1]| (dB),arg(S[1][1]) (deg)\n")
        fh.write("1.0,-10.0,0.0\n")
        fh.write("2.0,-12.0,0.0\n")


def _write_probe_e_csv(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("freq (GHz),Re{E_x[1]},Im{E_x[1]}\n")
        fh.write("1.0,1.5,0.0\n")
        fh.write("2.0,2.5,0.0\n")


def test_on_done_embeds_into_the_launching_document_not_the_active_one(two_docs, tmp_path):
    doc_a, doc_b = two_docs
    assert FreeCAD.ActiveDocument.Name == doc_b.Name  # confirm doc_b is "active"

    sim_a = create_simulation(doc_a)

    tmp_dir = str(tmp_path)
    _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))

    _on_done(
        True, "ok",
        passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
        needs_merge=False,
        sim=sim_a,
        tmp_dir=tmp_dir,
        on_complete=lambda ok, nc_path: None,  # avoid the auto-open-viewer path
    )

    # Results were embedded on doc_a's Simulation object, regardless of doc_b
    # being the currently active document when the run completed.
    assert resolve(sim_a, "ResultsFile") != ""

    # doc_b must be completely untouched -- it has no Simulation object at all.
    from features import find_simulation
    assert find_simulation(doc_b) is None


def test_on_done_handles_deleted_sim_object_without_raising(tmp_path):
    doc = FreeCAD.newDocument("OnDoneDeletedSim")
    try:
        sim = create_simulation(doc)
        doc.removeObject(sim.Name)  # simulate user deleting it mid-run

        tmp_dir = str(tmp_path)
        _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))

        _on_done(
            True, "ok",
            passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
            needs_merge=False,
            sim=sim,
            tmp_dir=tmp_dir,
            on_complete=lambda ok, nc_path: None,
        )  # must not raise ReferenceError
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_on_done_handles_none_sim_without_raising(tmp_path):
    tmp_dir = str(tmp_path)
    _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))

    _on_done(
        True, "ok",
        passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
        needs_merge=False,
        sim=None,
        tmp_dir=tmp_dir,
        on_complete=lambda ok, nc_path: None,
    )  # must not raise


def test_sweep_iteration_skips_field_probe_parsing(tmp_path):
    doc = FreeCAD.newDocument("OnDoneSweepSkipsFields")
    try:
        sim = create_simulation(doc)
        tmp_dir = str(tmp_path)
        _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))
        _write_probe_e_csv(os.path.join(tmp_dir, "output", "probe-E.csv"))

        captured = {}
        _on_done(
            True, "ok",
            passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
            needs_merge=False,
            sim=sim,
            tmp_dir=tmp_dir,
            on_complete=lambda ok, nc_path: captured.update(nc_path=nc_path),
            is_sweep_iteration=True,
        )

        from palace.results_db import load_dataset
        ds = load_dataset(captured["nc_path"])
        import numpy as np
        assert np.isnan(ds["E_real"].values).all()  # real 1.5/2.5 fixture values never parsed
        ds.close()
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_non_sweep_run_still_parses_field_probes(tmp_path):
    doc = FreeCAD.newDocument("OnDoneRunParsesFields")
    try:
        sim = create_simulation(doc)
        tmp_dir = str(tmp_path)
        _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))
        _write_probe_e_csv(os.path.join(tmp_dir, "output", "probe-E.csv"))

        _on_done(
            True, "ok",
            passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
            needs_merge=False,
            sim=sim,
            tmp_dir=tmp_dir,
            on_complete=lambda ok, nc_path: None,
            # is_sweep_iteration defaults to False -- today's interactive-Run behavior
        )

        from palace.results_db import load_dataset
        ds = load_dataset(resolve(sim, "ResultsFile"))
        import numpy as np
        assert not np.isnan(ds["E_real"].values).all()  # real fixture values were parsed
        ds.close()
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_sweep_iteration_does_not_embed_results_file(tmp_path):
    doc = FreeCAD.newDocument("OnDoneSweepNoEmbed")
    try:
        sim = create_simulation(doc)
        tmp_dir = str(tmp_path)
        _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))

        captured = {}
        _on_done(
            True, "ok",
            passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
            needs_merge=False,
            sim=sim,
            tmp_dir=tmp_dir,
            on_complete=lambda ok, nc_path: captured.update(nc_path=nc_path),
            is_sweep_iteration=True,
        )

        # ResultsFile was never touched -- nothing reads it mid-sweep.
        assert resolve(sim, "ResultsFile") == ""
        # But the callback still received a real, loadable results path.
        assert captured["nc_path"] and os.path.isfile(captured["nc_path"])
        from palace.results_db import load_dataset
        ds = load_dataset(captured["nc_path"])
        ds.close()
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_non_sweep_run_still_embeds_results_file(tmp_path):
    doc = FreeCAD.newDocument("OnDoneRunEmbeds")
    try:
        sim = create_simulation(doc)
        tmp_dir = str(tmp_path)
        _write_port_s_csv(os.path.join(tmp_dir, "output", "port-S.csv"))

        _on_done(
            True, "ok",
            passes=[(os.path.join(tmp_dir, "palace_config.json"), os.path.join(tmp_dir, "output"), None)],
            needs_merge=False,
            sim=sim,
            tmp_dir=tmp_dir,
            on_complete=lambda ok, nc_path: None,
        )

        assert resolve(sim, "ResultsFile") != ""
    finally:
        FreeCAD.closeDocument(doc.Name)

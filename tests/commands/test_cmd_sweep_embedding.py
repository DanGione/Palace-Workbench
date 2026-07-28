import FreeCAD
import pytest

from commands.cmd_sweep import _embed_sweep_result
from features.sweep import create_sweep
from palace import results_db
from palace.embedded_files import clear, resolve


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestCmdSweepEmbedding")
    yield d
    FreeCAD.closeDocument(d.Name)


def _ds(mag):
    return results_db.build_dataset(
        freq_ghz=[1.0, 2.0],
        s_mag_db={(1, 1): [mag, mag]},
        s_phase_deg={(1, 1): [0.0, 0.0]},
    )


def test_first_fresh_run_creates_embedded_sweep_nc(doc):
    sweep_obj = create_sweep(doc)
    path = _embed_sweep_result(
        doc, sweep_obj, _ds(-10.0), "freq_offset", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "P", "value": 1.0}], fresh=True,
    )
    assert path == resolve(sweep_obj, "SweepResultsFile")
    ds = results_db.load_dataset(path)
    assert "freq_offset" in ds.dims
    assert list(ds.coords["freq_offset"].values) == [0]
    ds.close()


def test_subsequent_runs_append_along_sweep_coordinate(doc):
    sweep_obj = create_sweep(doc)
    _embed_sweep_result(
        doc, sweep_obj, _ds(-10.0), "freq_offset", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "P", "value": 1.0}], fresh=True,
    )
    path = _embed_sweep_result(
        doc, sweep_obj, _ds(-20.0), "freq_offset", 1, run_idx=1,
        param_set=[{"varset": "V", "property": "P", "value": 2.0}], fresh=False,
    )
    ds = results_db.load_dataset(path)
    assert list(ds.coords["freq_offset"].values) == [0, 1]
    ds.close()


def test_reembedding_reuses_same_resolved_path_across_iterations(doc):
    sweep_obj = create_sweep(doc)
    _embed_sweep_result(
        doc, sweep_obj, _ds(-10.0), "freq_offset", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "P", "value": 1.0}], fresh=True,
    )
    path1 = resolve(sweep_obj, "SweepResultsFile")
    _embed_sweep_result(
        doc, sweep_obj, _ds(-20.0), "freq_offset", 1, run_idx=1,
        param_set=[{"varset": "V", "property": "P", "value": 2.0}], fresh=False,
    )
    path2 = resolve(sweep_obj, "SweepResultsFile")
    assert path1 == path2


def test_fresh_true_discards_prior_run_even_if_embedded_content_exists(doc):
    sweep_obj = create_sweep(doc)
    _embed_sweep_result(
        doc, sweep_obj, _ds(-10.0), "freq_offset", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "P", "value": 1.0}], fresh=True,
    )
    # Starting a brand-new sweep (fresh=True) must not carry over the old run's
    # coordinate values, even though SweepResultsFile already has content.
    path = _embed_sweep_result(
        doc, sweep_obj, _ds(-30.0), "substrate_er", 4.4, run_idx=0,
        param_set=[{"varset": "V", "property": "Er", "value": 4.4}], fresh=True,
    )
    ds = results_db.load_dataset(path)
    assert "freq_offset" not in ds.dims
    assert list(ds.coords["substrate_er"].values) == [4.4]
    ds.close()


def test_annotation_attrs_are_present_on_embedded_file(doc):
    sweep_obj = create_sweep(doc)
    path = _embed_sweep_result(
        doc, sweep_obj, _ds(-10.0), "freq_offset", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "P", "value": 1.0}],
        objective=42.0, fresh=True,
    )
    ds = results_db.load_dataset(path)
    assert ds.attrs["sweep_run_000_objective"] == 42.0
    ds.close()


def test_embedding_a_sweep_result_does_not_leave_a_tmp_file_behind(doc):
    # Regression: _annotate_sweep_run used to do a second full read-modify-
    # rewrite pass (write to <path>.tmp, then os.replace) after
    # append_sweep_point's own rewrite. That's now merged into one write via
    # append_sweep_point's extra_attrs -- confirm no .tmp artifact survives.
    import os
    sweep_obj = create_sweep(doc)
    path = _embed_sweep_result(
        doc, sweep_obj, _ds(-10.0), "freq_offset", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "P", "value": 1.0}],
        objective=42.0, fresh=True,
    )
    assert not os.path.isfile(path + ".tmp")
    assert not any(f.endswith(".tmp") for f in os.listdir(os.path.dirname(path)))


def test_clearing_before_a_new_sweep_prevents_stale_data_merge_on_failed_first_run(doc):
    # Regression: a prior (unrelated) sweep left real embedded content on
    # SweepResultsFile. CmdRunSweep.Activated() must clear() it before
    # launching a new sweep, so that if the new sweep's first point fails to
    # embed (raises before reaching embed()), a later point (fresh=False)
    # can't silently resolve the stale prior sweep's data and merge onto it.
    sweep_obj = create_sweep(doc)
    _embed_sweep_result(
        doc, sweep_obj, _ds(-99.0), "old_param", 0, run_idx=0,
        param_set=[{"varset": "V", "property": "Old", "value": 0.0}], fresh=True,
    )
    assert resolve(sweep_obj, "SweepResultsFile")  # stale content from "prior sweep"

    # This is the fix under test: CmdRunSweep.Activated() calls clear() here.
    clear(sweep_obj, "SweepResultsFile")
    assert resolve(sweep_obj, "SweepResultsFile") == ""

    # Simulate run 0 of the new sweep failing before it ever calls embed()
    # (e.g. a malformed dataset) -- SweepResultsFile stays empty, not stale.
    assert resolve(sweep_obj, "SweepResultsFile") == ""

    # Run 1 (fresh=False) must NOT pick up the old sweep's data, since
    # SweepResultsFile correctly resolves empty rather than to the stale file.
    path = _embed_sweep_result(
        doc, sweep_obj, _ds(-5.0), "new_param", 1, run_idx=1,
        param_set=[{"varset": "V", "property": "New", "value": 1.0}], fresh=False,
    )
    ds = results_db.load_dataset(path)
    assert "old_param" not in ds.dims
    assert list(ds.coords["new_param"].values) == [1]
    ds.close()

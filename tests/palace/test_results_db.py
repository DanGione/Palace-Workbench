import os

from palace import results_db


def _ds(mag):
    return results_db.build_dataset(
        freq_ghz=[1.0, 2.0],
        s_mag_db={(1, 1): [mag, mag]},
        s_phase_deg={(1, 1): [0.0, 0.0]},
    )


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "results.nc"
    ds = _ds(-10.0)
    results_db.save_dataset(ds, path)

    loaded = results_db.load_dataset(path)
    assert list(loaded["freq"].values) == [1.0, 2.0]
    loaded.close()


def test_load_dataset_raises_for_missing_file(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        results_db.load_dataset(tmp_path / "nonexistent.nc")


def test_append_sweep_point_creates_file_when_absent(tmp_path):
    path = tmp_path / "sweep.nc"
    results_db.append_sweep_point(path, _ds(-10.0), "freq_offset", 0)

    ds = results_db.load_dataset(path)
    assert list(ds.coords["freq_offset"].values) == [0]
    ds.close()


def test_append_sweep_point_concatenates_along_coordinate(tmp_path):
    path = tmp_path / "sweep.nc"
    results_db.append_sweep_point(path, _ds(-10.0), "freq_offset", 0)
    results_db.append_sweep_point(path, _ds(-20.0), "freq_offset", 1)

    ds = results_db.load_dataset(path)
    assert list(ds.coords["freq_offset"].values) == [0, 1]
    ds.close()


def test_append_sweep_point_extra_attrs_merged_into_single_write(tmp_path):
    path = tmp_path / "sweep.nc"
    results_db.append_sweep_point(
        path, _ds(-10.0), "freq_offset", 0,
        extra_attrs={"sweep_run_000_params": '{"P": 1.0}', "sweep_run_000_objective": 42.0},
    )

    # Single write -- no leftover .tmp file from a second rewrite pass.
    assert not os.path.isfile(str(path) + ".tmp")

    ds = results_db.load_dataset(path)
    assert ds.attrs["sweep_run_000_params"] == '{"P": 1.0}'
    assert ds.attrs["sweep_run_000_objective"] == 42.0
    ds.close()


def test_append_sweep_point_extra_attrs_accumulate_across_calls(tmp_path):
    path = tmp_path / "sweep.nc"
    results_db.append_sweep_point(
        path, _ds(-10.0), "freq_offset", 0,
        extra_attrs={"sweep_run_000_objective": 10.0},
    )
    results_db.append_sweep_point(
        path, _ds(-20.0), "freq_offset", 1,
        extra_attrs={"sweep_run_001_objective": 20.0},
    )

    ds = results_db.load_dataset(path)
    assert ds.attrs["sweep_run_000_objective"] == 10.0
    assert ds.attrs["sweep_run_001_objective"] == 20.0
    ds.close()


def test_append_sweep_point_without_extra_attrs_is_unaffected(tmp_path):
    path = tmp_path / "sweep.nc"
    results_db.append_sweep_point(path, _ds(-10.0), "freq_offset", 0)  # no extra_attrs kwarg

    ds = results_db.load_dataset(path)
    assert "freq_unit" in ds.attrs  # normal build_dataset attrs still present
    ds.close()

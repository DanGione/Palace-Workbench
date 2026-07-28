import os

from palace.post_process import (
    compute_te_tm_impedance,
    parse_probe_csv,
    parse_wave_port_z_csv,
)


def _write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for row in rows:
            fh.write(",".join(str(v) for v in row) + "\n")


def test_parse_wave_port_z_csv_reads_zpv_columns(tmp_path):
    path = tmp_path / "port-Z.csv"
    _write_csv(path, [
        ["freq (GHz)", "Re{Z_PV[1]} (Ohm)", "Im{Z_PV[1]} (Ohm)"],
        [1.0, 48.5, 1.2],
        [2.0, 49.0, 0.5],
    ])

    freq_ghz, data = parse_wave_port_z_csv(path)

    assert freq_ghz == [1.0, 2.0]
    assert data[1] == [complex(48.5, 1.2), complex(49.0, 0.5)]


def test_parse_wave_port_z_csv_ignores_per_excitation_z_columns(tmp_path):
    """Re{Z[idx][ex]} is a different physical quantity (input impedance looking
    into the port) -- the parser must not cross-match it against Z_PV."""
    path = tmp_path / "port-Z.csv"
    _write_csv(path, [
        ["freq (GHz)", "Re{Z_PV[1]} (Ohm)", "Im{Z_PV[1]} (Ohm)",
         "Re{Z[1][1]} (Ohm)", "Im{Z[1][1]} (Ohm)"],
        [1.0, 48.5, 1.2, 999.0, -999.0],
    ])

    freq_ghz, data = parse_wave_port_z_csv(path)

    assert data[1] == [complex(48.5, 1.2)]


def test_parse_wave_port_z_csv_multiple_ports(tmp_path):
    path = tmp_path / "port-Z.csv"
    _write_csv(path, [
        ["freq (GHz)", "Re{Z_PV[1]} (Ohm)", "Im{Z_PV[1]} (Ohm)",
         "Re{Z_PV[2]} (Ohm)", "Im{Z_PV[2]} (Ohm)"],
        [1.0, 48.5, 1.2, 51.0, -0.5],
    ])

    _, data = parse_wave_port_z_csv(path)

    assert set(data.keys()) == {1, 2}
    assert data[2] == [complex(51.0, -0.5)]


def test_compute_te_tm_impedance_matches_analytic_te_mode():
    """A single TE mode gives Z_TE = ωμ0/β exactly -- construct a synthetic
    uniform E/B field pair on-axis (n = +z) and check the closed form."""
    import numpy as np

    mu0 = 4.0 * np.pi * 1e-7
    omega = 2 * np.pi * 5e9
    beta = 80.0
    z_te_expected = omega * mu0 / beta

    # For TE: E = (Ex, 0, 0), B = (0, By, 0) with By = (beta/omega) * Ex
    # gives E x B* . z_hat = Ex * By (real), and Z_TE = mu0*|Ex|^2 / (Ex*By).
    Ex = 1.0
    By = (beta / omega) * Ex

    e_data = {1: {"Ex": [complex(Ex, 0)], "Ey": [complex(0, 0)], "Ez": [complex(0, 0)]}}
    b_data = {1: {"Bx": [complex(0, 0)], "By": [complex(By, 0)], "Bz": [complex(0, 0)]}}

    z_c = compute_te_tm_impedance(e_data, b_data, [1], [(0.0, 0.0, 0.0)], port_normal=(0, 0, 1))

    assert len(z_c) == 1
    assert abs(z_c[0].real - z_te_expected) / z_te_expected < 1e-9


def test_compute_te_tm_impedance_returns_empty_for_missing_grid():
    assert compute_te_tm_impedance({}, {}, [], [], port_normal=(0, 0, 1)) == []


def test_parse_probe_csv_still_works_unchanged(tmp_path):
    """Regression guard: parse_probe_csv (still used by the TE/TM path) keeps
    its existing behavior after the VI-branch removal touched this module."""
    path = tmp_path / "probe-E.csv"
    _write_csv(path, [
        ["freq (GHz)", "Re{E_x[5]}", "Im{E_x[5]}"],
        [1.0, 2.0, 0.5],
    ])

    freq_ghz, data = parse_probe_csv(path)

    assert freq_ghz == [1.0]
    assert data[5]["Ex"] == [complex(2.0, 0.5)]

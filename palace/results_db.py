"""Serialize and load Palace simulation results as an xarray Dataset (NetCDF4)."""

import datetime
import json
from pathlib import Path

try:
    import numpy as np
    import xarray as xr
    _XARRAY_OK = True
except ImportError:
    _XARRAY_OK = False


def _require_xarray():
    if not _XARRAY_OK:
        raise ImportError(
            "xarray and numpy are required for results database support.\n"
            "Install with: pip install xarray netCDF4 numpy"
        )


def build_dataset(freq_ghz, s_mag_db, s_phase_deg,
                  e_data=None, b_data=None,
                  z0_per_port_per_freq=None,
                  config_dict=None, metadata=None):
    """Build an xr.Dataset from Palace simulation results.

    Parameters
    ----------
    freq_ghz             : list[float]
        Frequency axis in GHz.
    s_mag_db             : dict[(row, col) -> list[float]]
        S-parameter magnitudes in dB.
    s_phase_deg          : dict[(row, col) -> list[float]]
        S-parameter phases in degrees.
    e_data               : {probe_idx: {comp: list[complex]}} or None
        Electric field probe data from parse_probe_csv().
    b_data               : {probe_idx: {comp: list[complex]}} or None
        Magnetic field probe data from parse_probe_csv().
    z0_per_port_per_freq : {port_idx: list[complex]} or None
        Characteristic impedance vs frequency per port. Ports not present
        default to NaN.
    config_dict          : dict or None
        Palace JSON config; embedded as a string attribute.
    metadata             : dict or None
        Extra key/value pairs stored as dataset attributes.

    Returns
    -------
    xr.Dataset
    """
    _require_xarray()

    freq = np.array(freq_ghz, dtype=np.float64)
    n_freq = len(freq)

    # ---- S-parameters -------------------------------------------------------
    if s_mag_db:
        all_ports = sorted(
            set(r for r, c in s_mag_db) | set(c for r, c in s_mag_db)
        )
        n_ports = max(all_ports)
        port_indices = list(range(1, n_ports + 1))
    else:
        port_indices = [1]
        n_ports = 1

    shape = (n_freq, n_ports, n_ports)
    mag_arr   = np.full(shape, np.nan, dtype=np.float64)
    phase_arr = np.full(shape, np.nan, dtype=np.float64)
    s_real    = np.full(shape, np.nan, dtype=np.float64)
    s_imag    = np.full(shape, np.nan, dtype=np.float64)

    for (r, c), mags in (s_mag_db or {}).items():
        ri, ci = r - 1, c - 1
        phases = s_phase_deg.get((r, c), [0.0] * n_freq)
        mag_a  = np.array(mags, dtype=np.float64)
        ph_a   = np.array(phases, dtype=np.float64)
        s_c    = 10.0 ** (mag_a / 20.0) * np.exp(1j * np.deg2rad(ph_a))
        mag_arr[:, ri, ci]   = mag_a
        phase_arr[:, ri, ci] = ph_a
        s_real[:, ri, ci]    = s_c.real
        s_imag[:, ri, ci]    = s_c.imag

    # ---- Field probes --------------------------------------------------------
    def _probe_array(field_data, comp_prefix):
        if not field_data:
            return None, None
        probe_idxs = sorted(field_data)
        comps = [f"{comp_prefix}x", f"{comp_prefix}y", f"{comp_prefix}z"]
        arr = np.zeros((n_freq, len(probe_idxs), 3), dtype=np.complex128)
        for pi, idx in enumerate(probe_idxs):
            for ci, comp in enumerate(comps):
                vals = field_data[idx].get(comp, [complex(0)] * n_freq)
                arr[:, pi, ci] = vals
        return arr, probe_idxs

    e_arr, e_probe_idxs = _probe_array(e_data, "E")
    b_arr, b_probe_idxs = _probe_array(b_data, "B")

    probe_idxs = e_probe_idxs or b_probe_idxs or [0]
    n_probes = len(probe_idxs)
    if e_arr is None:
        e_arr = np.full((n_freq, n_probes, 3), np.nan, dtype=np.float64)
    if b_arr is None:
        b_arr = np.full((n_freq, n_probes, 3), np.nan, dtype=np.float64)

    # ---- Characteristic impedance Z0(freq, port) ----------------------------
    z0_arr = np.full((n_freq, n_ports), np.nan, dtype=np.float64)
    for port_idx, z_list in (z0_per_port_per_freq or {}).items():
        pi = port_idx - 1
        if 0 <= pi < n_ports and z_list:
            z0_arr[:, pi] = [abs(z) for z in z_list]

    # ---- Assemble dataset ---------------------------------------------------
    ds = xr.Dataset(
        {
            "S_mag_db": (["freq", "port_i", "port_j"], mag_arr),
            "S_phase":  (["freq", "port_i", "port_j"], phase_arr),
            "S_real":   (["freq", "port_i", "port_j"], s_real),
            "S_imag":   (["freq", "port_i", "port_j"], s_imag),
            "E_real":   (["freq", "probe", "component"], e_arr.real),
            "E_imag":   (["freq", "probe", "component"], e_arr.imag),
            "B_real":   (["freq", "probe", "component"], b_arr.real),
            "B_imag":   (["freq", "probe", "component"], b_arr.imag),
            "Z0":       (["freq", "port_i"], z0_arr),
        },
        coords={
            "freq":      freq,
            "port_i":    port_indices,
            "port_j":    port_indices,
            "probe":     probe_idxs,
            "component": ["x", "y", "z"],
        },
        attrs={
            "freq_unit":      "GHz",
            "length_unit":    "mm",
            "run_timestamp":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    )

    if config_dict is not None:
        ds.attrs["palace_config"] = json.dumps(config_dict)
    if metadata:
        for k, v in metadata.items():
            ds.attrs[str(k)] = str(v)

    return ds


def save_dataset(ds, path):
    """Write *ds* to a NetCDF4 file at *path*."""
    _require_xarray()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(str(path), format="NETCDF4")


def load_dataset(path):
    """Load a Palace results NetCDF4 file and return an xr.Dataset.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if required variables are missing.
    """
    _require_xarray()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")
    ds = xr.open_dataset(str(path))
    required = {"S_mag_db", "S_phase", "Z0"}
    missing = required - set(ds.data_vars)
    if missing:
        raise ValueError(f"Results file is missing variables: {missing}")
    return ds


def append_sweep_point(base_nc, ds_new, sweep_coord_name, sweep_coord_value):
    """Append a new simulation result along a named sweep dimension.

    If *base_nc* does not yet exist the new dataset is written directly.
    Otherwise the existing file is loaded, the new dataset is concatenated
    along *sweep_coord_name*, and the file is overwritten.

    Parameters
    ----------
    base_nc            : str or Path — path to the sweep results file
    ds_new             : xr.Dataset — result of a single simulation run
    sweep_coord_name   : str — dimension name, e.g. "substrate_er"
    sweep_coord_value  : scalar — coordinate value for this run
    """
    _require_xarray()
    base_nc = Path(base_nc)
    ds_new = ds_new.expand_dims({sweep_coord_name: [sweep_coord_value]})

    if base_nc.exists():
        ds_existing = xr.open_dataset(str(base_nc))
        if sweep_coord_name in ds_existing.dims:
            ds_combined = xr.concat([ds_existing, ds_new], dim=sweep_coord_name)
        else:
            # Existing file has a different sweep coordinate (e.g. from a prior run)
            # — start fresh rather than producing an incompatible concat.
            ds_combined = ds_new
        ds_existing.close()
    else:
        ds_combined = ds_new

    save_dataset(ds_combined, base_nc)

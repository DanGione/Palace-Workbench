"""Post-processing helpers for Palace simulation output."""
import csv
import datetime
import os
import re

try:
    import numpy as _np
    _NP_OK = True
except ImportError:
    _NP_OK = False

# Matches "|S[row][col]| (dB)" headers produced by Palace
_MAG_RE = re.compile(r"\|S\[(\d+)\]\[(\d+)\]\|")
# Matches "arg(S[row][col]) (deg.)" headers produced by Palace
_ANG_RE = re.compile(r"arg\(S\[(\d+)\]\[(\d+)\]\)")
# Matches "Re{E_x[N]}" / "Im{B_y[N]}" etc. in probe-E.csv / probe-B.csv
_RE_PROBE = re.compile(r"Re\{([EB])_([xyz])\[(\d+)\]\}")
_IM_PROBE = re.compile(r"Im\{([EB])_([xyz])\[(\d+)\]\}")


def parse_probe_csv(path):
    """Parse a Palace probe-E.csv or probe-B.csv file.

    Returns (freq_ghz, data) where:
    - data[probe_idx][component] = list[complex], one entry per frequency
    - component is 'Ex'/'Ey'/'Ez' for E-field files, 'Bx'/'By'/'Bz' for B-field files
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = [h.strip() for h in next(reader)]
        rows = [[float(v) for v in r] for r in reader if any(v.strip() for v in r)]

    re_cols = {}   # (probe_idx, component) -> col_index
    im_cols = {}
    for i, h in enumerate(headers):
        m = _RE_PROBE.search(h)
        if m:
            key = (int(m.group(3)), m.group(1) + m.group(2))
            re_cols[key] = i
            continue
        m = _IM_PROBE.search(h)
        if m:
            key = (int(m.group(3)), m.group(1) + m.group(2))
            im_cols[key] = i

    freq_ghz = [r[0] for r in rows]
    data = {}
    for (idx, comp), ri in re_cols.items():
        ii = im_cols.get((idx, comp))
        if ii is None:
            continue
        if idx not in data:
            data[idx] = {}
        data[idx][comp] = [complex(r[ri], r[ii]) for r in rows]

    return freq_ghz, data


def compute_wave_port_impedance(e_data, b_data, probe_indices, probe_positions_mm,
                                grid_probe_indices, grid_probe_positions_mm,
                                port_normal=(0.0, 0.0, 1.0)):
    """Compute characteristic impedance from E and B probe data.

    Two paths are selected automatically based on whether V-line probes are present:

    **Quasi-TEM** (len(probe_indices) >= 2 — IntegrationEdge was set):
        Z₀^{VI} = |V| / |I|  (voltage-current definition, engineering convention)
        V = trapezoidal ∫E·dl along the probe edge (inner → outer conductor).
        I = (Δu/μ₀) × Σ_cols [B_h(z_below) − B_h(z_above)]  via Ampere contour
        straddling the inner conductor height. B_h is the horizontal B component on
        the port face (the span axis that is neither the V-integration axis nor the
        port normal).

    **TE / TM** (len(probe_indices) == 0 — no IntegrationEdge, hollow waveguide):
        Z_w = μ₀ Σ|E_t|² / Σ Re(E×B*·n̂)  over 2-D grid probes.
        For a single TE mode this gives Z_TE = ωμ₀/β exactly; for TM, Z_TM = βη²/(ωμ₀).

    Parameters
    ----------
    e_data                  : {probe_idx: {comp: [complex]}} from parse_probe_csv(probe-E.csv)
    b_data                  : {probe_idx: {comp: [complex]}} from parse_probe_csv(probe-B.csv)
    probe_indices           : list[int] — V-line probe indices (inner→outer conductor); [] for TE/TM
    probe_positions_mm      : list[(x,y,z)] — V-line probe coordinates in mm; [] for TE/TM
    grid_probe_indices      : list[int] — 2-D grid probe indices covering the port face
    grid_probe_positions_mm : list[(x,y,z)] — grid probe coordinates in mm (same order)
    port_normal             : (x,y,z) unit vector normal to the port face (propagation direction)

    Returns list[float] of Z per frequency, or [] if data is unavailable.
    """
    if not _NP_OK:
        raise ImportError("numpy is required for characteristic impedance extraction")

    MU0 = 4.0 * _np.pi * 1e-7
    L0  = 1e-3   # mm → metres

    n_probes = len(probe_indices)
    use_vi = (n_probes >= 2)
    use_zw = (n_probes == 0 and bool(grid_probe_indices))
    if not use_vi and not use_zw:
        return []
    if not grid_probe_indices or not grid_probe_positions_mm:
        return []

    n_hat = _np.array(port_normal, dtype=float)
    n_hat /= (_np.linalg.norm(n_hat) or 1.0)
    nx, ny, nz = float(n_hat[0]), float(n_hat[1]), float(n_hat[2])

    _src   = probe_indices[0] if use_vi else grid_probe_indices[0]
    n_freq = len(next(iter(e_data[_src].values())))

    # ------------------------------------------------------------------ #
    # Quasi-TEM: Z₀^{VI} = |V| / |I|  via Ampere contour                #
    # ------------------------------------------------------------------ #
    if use_vi:
        for idx in probe_indices:
            if idx not in e_data:
                return []

        pts_arr     = _np.array(probe_positions_mm, dtype=float)
        axis_ranges = pts_arr.max(axis=0) - pts_arr.min(axis=0)
        vert_ax  = int(_np.argmax(axis_ranges))
        norm_ax  = int(_np.argmax(_np.abs(n_hat)))
        horiz_ax = [i for i in range(3) if i != vert_ax and i != norm_ax][0]
        bcomp    = ('Bx', 'By', 'Bz')[horiz_ax]

        grid_pos = _np.array(grid_probe_positions_mm, dtype=float)
        h_vals   = sorted(set(_np.round(grid_pos[:, horiz_ax], 6).tolist()))
        v_vals   = sorted(set(_np.round(grid_pos[:, vert_ax],  6).tolist()))
        if len(h_vals) < 2:
            return []
        du_m = (h_vals[1] - h_vals[0]) * L0

        z_inner = float(pts_arr[0, vert_ax])
        below_v = [v for v in v_vals if v < z_inner - 1e-6]
        above_v = [v for v in v_vals if v > z_inner + 1e-6]
        if not below_v or not above_v:
            return []
        z_below, z_above = max(below_v), min(above_v)

        idx_map = {(round(float(pos[horiz_ax]), 4), round(float(pos[vert_ax]), 4)): idx
                   for idx, pos in zip(grid_probe_indices, grid_probe_positions_mm)}
        below_ids = [idx_map[(round(h, 4), round(z_below, 4))] for h in h_vals
                     if (round(h, 4), round(z_below, 4)) in idx_map]
        above_ids = [idx_map[(round(h, 4), round(z_above, 4))] for h in h_vals
                     if (round(h, 4), round(z_above, 4)) in idx_map]
        if not below_ids or not above_ids:
            return []
        for idx in below_ids + above_ids:
            if idx not in b_data:
                return []

        pts = _np.array(probe_positions_mm, dtype=float)
        z_c = []
        for fi in range(n_freq):
            V = complex(0)
            for k in range(n_probes - 1):
                dl_m   = (pts[k + 1] - pts[k]) * L0
                idx_k  = probe_indices[k]
                idx_k1 = probe_indices[k + 1]
                E_k  = _np.array([e_data[idx_k].get( 'Ex', [0]*n_freq)[fi],
                                   e_data[idx_k].get( 'Ey', [0]*n_freq)[fi],
                                   e_data[idx_k].get( 'Ez', [0]*n_freq)[fi]])
                E_k1 = _np.array([e_data[idx_k1].get('Ex', [0]*n_freq)[fi],
                                   e_data[idx_k1].get('Ey', [0]*n_freq)[fi],
                                   e_data[idx_k1].get('Ez', [0]*n_freq)[fi]])
                V += _np.dot(0.5 * (E_k + E_k1), dl_m)

            I_val = (sum(b_data[i].get(bcomp, [0]*n_freq)[fi] for i in below_ids) -
                     sum(b_data[i].get(bcomp, [0]*n_freq)[fi] for i in above_ids))
            I_val = I_val * du_m / MU0
            if abs(I_val) < 1e-15:
                z_c.append(complex(0))
                continue
            z_c.append(abs(V) / abs(I_val))
        return z_c

    # ------------------------------------------------------------------ #
    # TE / TM: Z_w = μ₀ Σ|E_t|² / Σ Re(E×B*·n̂)                        #
    # ------------------------------------------------------------------ #
    for idx in grid_probe_indices:
        if idx not in e_data or idx not in b_data:
            return []

    z_c = []
    for fi in range(n_freq):
        E_t_sq_sum = 0.0
        cross_sum  = 0.0
        for idx_k in grid_probe_indices:
            Ex = e_data[idx_k].get('Ex', [0]*n_freq)[fi]
            Ey = e_data[idx_k].get('Ey', [0]*n_freq)[fi]
            Ez = e_data[idx_k].get('Ez', [0]*n_freq)[fi]
            Bx = b_data[idx_k].get('Bx', [0]*n_freq)[fi]
            By = b_data[idx_k].get('By', [0]*n_freq)[fi]
            Bz = b_data[idx_k].get('Bz', [0]*n_freq)[fi]
            E_dot_n    = nx*Ex + ny*Ey + nz*Ez
            E_t_sq     = abs(Ex)**2 + abs(Ey)**2 + abs(Ez)**2 - abs(E_dot_n)**2
            cross_n    = (nx*(Ey*Bz.conjugate() - Ez*By.conjugate()) +
                          ny*(Ez*Bx.conjugate() - Ex*Bz.conjugate()) +
                          nz*(Ex*By.conjugate() - Ey*Bx.conjugate()))
            E_t_sq_sum += E_t_sq
            cross_sum  += cross_n.real
        if cross_sum <= 0:
            z_c.append(complex(0))
            continue
        z_c.append(MU0 * E_t_sq_sum / cross_sum)
    return z_c


def _parse_s_csv(path):
    """Parse a Palace port-S.csv.

    Returns (freq_ghz, mag_db, phase_deg) where mag_db and phase_deg are
    dicts mapping (row, col) -> list[float].
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = [h.strip() for h in next(reader)]
        rows = [[float(v) for v in r] for r in reader if any(v.strip() for v in r)]

    mag_cols = {}
    phase_cols = {}
    for i, h in enumerate(headers):
        m = _MAG_RE.search(h)
        if m:
            mag_cols[(int(m.group(1)), int(m.group(2)))] = i
            continue
        m = _ANG_RE.search(h)
        if m:
            phase_cols[(int(m.group(1)), int(m.group(2)))] = i

    freq_ghz = [r[0] for r in rows]
    mag_db = {k: [r[v] for r in rows] for k, v in mag_cols.items()}
    phase_deg = {k: [r[v] for r in rows] for k, v in phase_cols.items()}
    return freq_ghz, mag_db, phase_deg


def _renormalize_data(freq_ghz, mag_db, phase_deg, port_z_old, port_z_new):
    """Inner renormalization logic (operates on in-memory arrays)."""
    keys      = sorted(mag_db)
    all_ports = sorted(set(r for r, c in keys) | set(c for r, c in keys))
    n_ports   = len(all_ports)
    p2i       = {p: i for i, p in enumerate(all_ports)}

    z_old_vec = _np.array([port_z_old.get(p, 50.0) for p in all_ports], dtype=float)
    z_new_vec = _np.array([port_z_new.get(p, 50.0) for p in all_ports], dtype=float)
    D_old     = _np.diag(_np.sqrt(z_old_vec))
    D_new_inv = _np.diag(1.0 / _np.sqrt(z_new_vec))
    D_new     = _np.diag(_np.sqrt(z_new_vec))
    T_new     = _np.diag(z_new_vec)

    s_new_mag   = {k: [] for k in keys}
    s_new_phase = {k: [] for k in keys}

    for fi in range(len(freq_ghz)):
        S = _np.zeros((n_ports, n_ports), dtype=complex)
        for (r, c) in keys:
            ri, ci = p2i[r], p2i[c]
            mag = mag_db[(r, c)][fi]
            phi = phase_deg.get((r, c), [0.0] * len(freq_ghz))[fi]
            S[ri, ci] = 10.0 ** (mag / 20.0) * _np.exp(1j * _np.deg2rad(phi))

        I_mat = _np.eye(n_ports, dtype=complex)
        Z_mat = D_old @ (I_mat + S) @ _np.linalg.inv(I_mat - S) @ D_old
        S_new = D_new_inv @ (Z_mat - T_new) @ _np.linalg.inv(Z_mat + T_new) @ D_new

        for (r, c) in keys:
            ri, ci = p2i[r], p2i[c]
            val = S_new[ri, ci]
            s_new_mag[  (r, c)].append(float(20.0 * _np.log10(max(abs(val), 1e-20))))
            s_new_phase[(r, c)].append(float(_np.rad2deg(_np.angle(val))))

    return freq_ghz, s_new_mag, s_new_phase


def renormalize_s_matrix(s_csv_path, port_z_old, port_z_new):
    """Renormalize S-matrix from per-port reference impedances to new target impedances.

    Applies the power-wave bilinear S→Z→S transform:
        Z_mat  = D_old @ (I + S) @ inv(I - S) @ D_old
        S_new  = D_new⁻¹ @ (Z_mat - T_new) @ inv(Z_mat + T_new) @ D_new
    where D_old = diag(sqrt(Z_old)), T_new = diag(Z_new).

    Parameters
    ----------
    s_csv_path : path to Palace port-S.csv
    port_z_old : {port_idx: float} — reference impedances S-params were computed at
    port_z_new : {port_idx: float} — target normalization impedances
    Ports absent from either dict default to 50 Ω.

    Returns (freq_ghz, renorm_mag_db, renorm_phase_deg) with the same dict-of-lists
    structure as _parse_s_csv, ready for the S-param viewer.
    """
    if not _NP_OK:
        raise ImportError("numpy is required for S-parameter renormalization")
    freq_ghz, mag_db, phase_deg = _parse_s_csv(s_csv_path)
    return _renormalize_data(freq_ghz, mag_db, phase_deg, port_z_old, port_z_new)


def renormalize_s_matrix_from_data(freq_ghz, mag_db, phase_deg, port_z_old, port_z_new):
    """Renormalize S-matrix from in-memory arrays (no CSV read).

    Same transform and return format as renormalize_s_matrix, but accepts
    already-parsed data directly (e.g. loaded from a results .nc file).
    """
    if not _NP_OK:
        raise ImportError("numpy is required for S-parameter renormalization")
    return _renormalize_data(freq_ghz, mag_db, phase_deg, port_z_old, port_z_new)


def merge_s_matrix(pass_output_dirs, output_path):
    """Merge per-excitation port-S.csv files into a single combined S-matrix CSV.

    Each directory in pass_output_dirs must contain a port-S.csv produced by
    Palace with a single driven port.  The files are expected to have identical
    frequency columns.  The non-frequency columns from each file are appended
    side-by-side so the result contains S[row][col] for every driven column.

    Raises FileNotFoundError if any expected port-S.csv is missing.
    """
    combined_headers = None
    combined_rows = None

    for d in pass_output_dirs:
        csv_path = os.path.join(d, "port-S.csv")
        if not os.path.isfile(csv_path):
            raise FileNotFoundError(f"port-S.csv not found in {d}")

        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            headers = next(reader)
            rows = [list(r) for r in reader]

        if combined_headers is None:
            combined_headers = list(headers)
            combined_rows = [list(r) for r in rows]
        else:
            if len(rows) != len(combined_rows):
                raise ValueError(
                    f"Frequency point count mismatch: expected {len(combined_rows)}, "
                    f"got {len(rows)} in {csv_path}"
                )
            combined_headers.extend(headers[1:])
            for i, row in enumerate(rows):
                combined_rows[i].extend(row[1:])

    if combined_headers is None:
        return

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(combined_headers)
        for row in combined_rows:
            writer.writerow(row)


def write_touchstone(csv_path, reference_impedance=50.0):
    """Write a Touchstone .sNp file from a Palace port-S.csv.

    Palace CSV headers use the form "|S[row][col]| (dB)" and
    "arg(S[row][col]) (deg.)".  This function parses those headers to
    determine the port count and S-parameter ordering, then writes a
    Touchstone 1.0 file in DB format (dB magnitude + angle in degrees)
    next to the source CSV.

    The Touchstone column ordering follows the standard: S-parameters are
    grouped by driven port (column-major), so all S[*][1] come first,
    then all S[*][2], etc.  Up to 4 S-parameters are placed on each data
    line; continuation lines for the same frequency are indented.

    Returns the path of the written Touchstone file.
    """
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = next(reader)
        data_rows = [[float(v) for v in r] for r in reader]

    # Map (row, col) → [mag_col_idx, ang_col_idx]
    s_pairs = {}
    for i, h in enumerate(headers):
        h = h.strip()
        m = _MAG_RE.search(h)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            s_pairs.setdefault(key, [None, None])[0] = i
            continue
        m = _ANG_RE.search(h)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            s_pairs.setdefault(key, [None, None])[1] = i

    if not s_pairs:
        raise ValueError(f"No S-parameter columns found in {csv_path}")

    n_ports = max(max(r, c) for r, c in s_pairs)

    # Touchstone column-major ordering: for col=1..N, row=1..N
    ordered = [
        (row, col)
        for col in range(1, n_ports + 1)
        for row in range(1, n_ports + 1)
        if (row, col) in s_pairs
    ]

    ts_path = os.path.splitext(csv_path)[0] + f".s{n_ports}p"

    with open(ts_path, "w", encoding="utf-8") as fh:
        fh.write("! Generated by Palace FreeCAD Workbench\n")
        fh.write(f"! {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"! Source: {os.path.basename(csv_path)}\n")
        fh.write(f"# GHz S DB R {reference_impedance:g}\n")

        for data_row in data_rows:
            freq = data_row[0]

            # Flatten S-params in Touchstone column-major order
            flat = []
            for (r, c) in ordered:
                mag_idx, ang_idx = s_pairs[(r, c)]
                flat.append(data_row[mag_idx])
                flat.append(data_row[ang_idx])

            # Touchstone 1.0: max 4 S-params (8 values) per line.
            # The frequency precedes the first group; continuation lines indent.
            VALUES_PER_LINE = 8  # 4 S-params × 2 values each
            first = True
            for i in range(0, len(flat), VALUES_PER_LINE):
                chunk = flat[i : i + VALUES_PER_LINE]
                nums = "  ".join(f"{v: .9e}" for v in chunk)
                if first:
                    fh.write(f" {freq:.9e}  {nums}\n")
                    first = False
                else:
                    fh.write(f"             {nums}\n")

    return ts_path

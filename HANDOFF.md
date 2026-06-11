# Handoff Notes

## Session: Results Database (xarray/NetCDF4)

### What was done

Simulation outputs are now packaged into a single `results.nc` file (xarray/NetCDF4) instead of scattered CSVs. The main goals were to reduce folder clutter and lay the groundwork for parameter sweep tracking.

---

### New file: `palace/results_db.py`

Self-contained serialization module. Key functions:

| Function | Purpose |
|---|---|
| `build_dataset(freq, s_mag_db, s_phase_deg, e_data, b_data, z0_per_port_per_freq, config_dict, metadata)` | Assembles an `xr.Dataset` from parsed Palace outputs |
| `save_dataset(ds, path)` | Writes NetCDF4 to disk |
| `load_dataset(path)` | Reads and validates a results file |
| `append_sweep_point(base_nc, ds_new, coord_name, coord_value)` | Concatenates a new run onto a sweep file along a named dimension |

**Dataset schema:**

| Variable | Dims | Description |
|---|---|---|
| `S_mag_db` | `(freq, port_i, port_j)` | S-parameter magnitude in dB |
| `S_phase` | `(freq, port_i, port_j)` | S-parameter phase in degrees |
| `S_real` / `S_imag` | `(freq, port_i, port_j)` | Complex S-parameter (NetCDF4 can't store complex natively) |
| `E_real` / `E_imag` | `(freq, probe, component)` | E-field at probe locations |
| `B_real` / `B_imag` | `(freq, probe, component)` | B-field at probe locations |
| `Z0` | `(freq, port_i)` | Characteristic impedance vs frequency per port (Ω) |

Attributes include `freq_unit`, `length_unit`, `run_timestamp`, and the full Palace JSON config as a string (`palace_config`).

---

### Changes to `palace/post_process.py`

- Refactored `renormalize_s_matrix` to use an inner `_renormalize_data` helper.
- Added `renormalize_s_matrix_from_data(freq_ghz, mag_db, phase_deg, port_z_old, port_z_new)` — same transform but takes in-memory arrays instead of a CSV path. Used by the panel to avoid needing a CSV on disk.

---

### Changes to `commands/cmd_run.py`

**Temp directory for Palace outputs:**

Palace CSVs (`port-S.csv`, `probe-E.csv`, `probe-B.csv`) are written to a `tempfile.mkdtemp(prefix="palace_")` directory, not the project folder. The temp dir is deleted after `results.nc` is saved. Only the config JSONs and mesh file remain in the permanent output folder.

**Output folder named after the FreeCAD file:**

`_output_dir(doc)` now uses the FCStd filename stem instead of the hardcoded name `palace_output`. A file called `MyModel.FCStd` produces a folder called `MyModel/` next to it.

**`results.nc` path:**

`_results_nc_path(doc)` returns `<stem>/results.nc` inside the named output folder (same directory as the config JSONs).

**`_update_wave_port_impedances` now returns data:**

Previously it only wrote `CharacteristicZ` back to the FreeCAD object. It now also returns `{port_idx: list[complex]}` (per-frequency Z0) so that data can be embedded in the dataset.

**`_build_passes` signature change:**

Now takes a `tmp_dir` argument and uses it for all Palace output paths via `output_override`.

**`_on_done` / `_launch_passes` signature change:**

Both accept a `tmp_dir` keyword argument that is threaded through and cleaned up after dataset building.

---

### Changes to `panels/s_param_panel.py`

- **`load_nc(path)`** replaces `load_csv(path)` — reads from `results.nc` via `_load_nc()`.
- **`browse_nc()`** replaces `browse_csv()` — file picker opens `.nc` files.
- **"Export Touchstone…" button** added — writes a `.sNp` file on demand to a user-chosen path, respecting the current renormalization state.
- **`_apply_renorm`** now calls `renormalize_s_matrix_from_data` (no CSV needed).

---

### Changes to `commands/cmd_results.py`

`CmdViewResults` updated to look for `<stem>/results.nc` and call `load_nc`/`browse_nc`.

---

### Dependencies

`xarray` and `netCDF4` are required. They are **not** in `_AUTO_INSTALL_PACKAGES` (adding them there blocks workbench loading — they pull in pandas and take minutes to install). Instead:

- **Docker**: pre-installed at image build time via the `pip install` line in both `Dockerfile` and `.devcontainer/Dockerfile`.
- **Native FreeCAD**: user must install manually once:
  ```
  "<freecad>/bin/python.exe" -m pip install xarray netCDF4
  ```
  The error message from `_require_xarray()` in `results_db.py` tells them this if they forget.

`requirements.txt` documents both packages for reference.

---

---

## Session: Optimization Robustness + Port Bug Fixes

### Nelder-Mead convergence overhaul (`commands/cmd_sweep.py`, `features/sweep.py`, `panels/sweep_panel.py`)

The previous NM convergence had three problems:

1. **Custom callback with premature-stop bug** — a per-iteration relative improvement check that fired if any single NM step failed to improve the global best by ≥1%, causing convergence after 6 iterations in some cases. Removed entirely.
2. **`fatol` left at scipy's default (1e-4)** — scipy's NM convergence requires *both* `xatol` AND `fatol` to be met simultaneously. With EM simulation objectives (weighted squared S-param deviations, magnitude ≫ 1e-4) the fatol gate was never opened and the optimizer would run until `maxfev`. Fixed by passing `"fatol": float("inf")`.
3. **`xatol` was unit-dependent** — a tolerance of 1e-4 means very different things for a 5 mm pad vs. a 2.45 GHz resonance. Fixed by normalizing all parameters to [0, 1] before passing to scipy, so `xatol` is always a fraction of each parameter's user-defined range. Default changed to 0.01 (1 % of range).

Additional fixes in the same session:
- **`maxiter` → `maxfev`**: scipy's `maxiter` counts NM algorithm steps; each step can call the objective (= one Palace simulation) multiple times. Changed to `maxfev` so "Max iterations" in the UI matches the number of simulations run.
- **Fixed `lo == hi` normalization bug**: when min = max for a parameter, the old guard `ranges = 1.0` allowed scipy to vary it over [lo, lo+1]. Now `bounds_norm = (0.0, 0.0)` pins it correctly.
- **Removed `NMFatol`** property from `features/sweep.py` and its spin box from the sweep panel. The `NMXatol` description and tooltip updated to reflect the normalized-space semantics.

---

### Sweep results panel additions (`panels/sweep_results_panel.py`, `commands/cmd_sweep.py`)

| Change | Detail |
|---|---|
| **Reload button** | "Reload" sits next to "Load…"; re-reads `self._nc_path` from disk without a file dialog. Disabled until a file is loaded. Useful for watching an optimization in progress. |
| **Objective persisted to sweep.nc** | `_annotate_sweep_run` now writes `sweep_run_NNN_objective = float` as a dataset attribute for each optimization iteration. Regular (non-optimization) sweeps are unaffected — the attribute is simply absent. |
| **Best iteration marked** | After loading, the combo box entry for the iteration with the lowest objective value gets `"  ★ best"` appended to its label. |
| **Objective in params label** | When a sweep point is selected, the parameter-display label at the bottom shows `| objective: X.XXXX` alongside the parameter values (optimization runs only). |

---

### Port group stray-object deletion bug (`features/port_group.py`)

`sync_port_group` unconditionally called `doc.removeObject(child.Name)` on every child of the port's Group. Two kinds of children can be in the Group:

- **`PartDesign::SubShapeBinder`** objects — created and owned by `sync_port_group`. Correct to delete.
- **Stray geometry objects** (user's plane, solid, etc.) — added whole via `port_obj.addObject(link_obj)` when the geometry isn't inside any Palace material group. Deleting these destroyed user geometry and nullified the `PortFaces` / `PortEdges` links, so the port's face reference was permanently lost.

**Fix:** type-discriminate in the cleanup loop — SubShapeBinders use `doc.removeObject`; stray objects use `port_obj.removeObject(child)` (detach from Group without deleting). Applies to WavePort, LumpedPort, and ImpedanceBoundary since all three call `sync_port_group`.

The bug was most visible when toggling the **Excitation** checkbox on a wave port panel and clicking Accept, but could affect any property change on any port type.

---

### Second recompute before meshing (`commands/cmd_sweep.py`)

`_apply_param_set` already calls `doc.recompute()` after changing VarSet properties, but FreeCAD's dependency propagation does not always reach every object in deep PartDesign chains (VarSet → Sketch constraint → Pad → Body) in a single pass. Added a second `doc.recompute()` as the first statement in the `try:` block of both `_SweepCoordinator._launch_run` and `_OptimizationCoordinator._launch_eval`, immediately before `generate_mesh`. A redundant recompute on an already-current document is a near-no-op.

---

---

## Session: Nelder-Mead Initial Simplex + Convergence Review

### Initial simplex fix (`commands/cmd_sweep.py`)

**Problem:** scipy's default simplex construction perturbs each dimension of `x0` by `(1 + 0.05) * x0[k]`. For a normalized starting point at the midpoint (x0_norm = 0.5), this is a perturbation of only 0.025 — **2.5% of the [0,1] parameter range** — regardless of how wide the actual bounds are. The optimizer was taking very small steps and exploring only a tiny fraction of the search space.

**Fix:** Construct an explicit `initial_simplex` before calling `minimize()`, where each non-fixed dimension is perturbed by 50% of the normalized range, clamped to [0, 1]:

```python
_step = 0.5
_sim[0] = x0_norm
for each dimension i:
    vertex[i] = x0_norm[i] + 0.5  (or − 0.5 if near the upper bound)
```

Also enabled `adaptive=True`, which scales the NM reflection/expansion/contraction/shrink coefficients with the number of parameters (per Gao & Han 2012). This is a free improvement for multi-parameter problems.

### Current convergence setup (confirmed, no changes made)

scipy's NM convergence requires **both** conditions simultaneously:
- `xatol`: max absolute difference between simplex vertices in parameter space ≤ threshold
- `fatol`: max absolute difference between objective values at simplex vertices ≤ threshold

Current settings:
- `xatol = NMXatol` (default **0.01**, i.e. 1% of the normalized [0,1] range) — **active**
- `fatol = float("inf")` — **disabled** (trivially always satisfied)

Effect: convergence is driven entirely by the simplex collapsing to < 1% of each parameter's range. `fatol` is disabled because EM objectives are squared dB deviations (magnitude 100–10,000), making any absolute `fatol` threshold either always-triggered or never-triggered depending on the problem. Unit-normalizing the objective to re-enable `fatol` is possible future work but was not implemented here.

---

---

## Session: Optimize UX — Auto-fill bounds + Live results panel

### Auto-fill Min / Max / Initial (`panels/sweep_panel.py`)

When the user adds a new row in Optimize mode and selects a property, the Min, Max, and Initial fields are now pre-populated from the property's current value in the FreeCAD document:

- **Initial** = current document value
- **Min** = Initial − 50% of |Initial| (falls back to −0.5 if value is zero)
- **Max** = Initial + 50% of |Initial| (falls back to +0.5 if value is zero)

**Implementation:**
- `_current_value(doc, varset_label, prop_name)` — new module-level helper (mirrors detection logic from `_numeric_props`; handles plain floats and FreeCAD Quantity objects via `.Value`)
- `_ParamTableWidget._on_property_changed(row, prop_text)` — new method that fetches the current value and writes to the Min/Max/Initial table items
- Signal connected inside the `else` (Optimize) block of `add_row()`, **after** `prop_combo.setCurrentText(prop)` and **after** the three table items are inserted. This ordering is critical: `load_params` calls `add_row` with pre-set values and sets the combo text before the signal is connected, so restoring saved parameters never triggers the auto-fill handler. Only live user interaction (selecting or changing a property) does.

Changing the property on an existing row also auto-fills, since the old bounds would be meaningless for the new variable.

---

### Live results panel updates (`commands/cmd_sweep.py`, `panels/sweep_results_panel.py`)

The Sweep Results panel now reloads automatically after each sweep/optimization iteration writes to the database, rather than waiting for the entire run to finish.

**Signal emitter (`commands/cmd_sweep.py`):**

A module-level singleton `_iter_emitter` emits `iteration_done(str)` (the sweep `.nc` path) immediately after `_annotate_sweep_run` completes in both coordinators:
- `_SweepCoordinator._launch_run → _on_run_complete` (line ~301)
- `_OptimizationCoordinator._launch_eval → _on_run_complete` (line ~609)

```python
class _IterationSignalEmitter(QObject):
    iteration_done = Signal(str)

_iter_emitter = _IterationSignalEmitter()
```

**Results panel (`panels/sweep_results_panel.py`):**

`SweepResultsPanel.__init__` connects to `_iter_emitter.iteration_done`. The handler `_on_iteration_done(sweep_nc)` does a smart reload:

| State | Behaviour |
|---|---|
| Panel has no file loaded | Loads the file and jumps to the latest entry |
| Panel has a different file open | No-op |
| Panel has same file, user is on last entry | Reloads and advances to new last entry |
| Panel has same file, user is on an earlier entry | Reloads but restores their position |

The "was at last" check (`currentIndex() == count() - 1`) is evaluated before calling `load_nc()` (which resets the combo to index 0), then the desired index is set after.

---

### What's not done / future work

- **Touchstone export reference impedance**: the Export Touchstone button hardcodes 50 Ω. It should read per-port impedances from the document (or let the user specify) before writing.
- **`xarray`/`netCDF4` lazy install**: a lazy installer inside `_require_xarray()` was briefly implemented but caused Docker issues and was removed. The current approach (clear error + manual install instruction) is intentional.

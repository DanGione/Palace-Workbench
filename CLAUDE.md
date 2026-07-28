# Palace Workbench — Developer Context

FreeCAD 1.0 Python workbench for EM simulation via the Palace solver (AWS Labs).
Docker-first: images published to GHCR. No test suite — verified by running the
container and exercising the GUI.

## File Layout

| Path | Purpose |
|---|---|
| `commands/` | FreeCAD `Gui::Command` subclasses (one per toolbar button) |
| `commands/cmd_run.py` | Orchestrates single simulations |
| `commands/cmd_sweep.py` | Orchestrates parameter sweeps and optimization |
| `features/` | FreeCAD `DocumentObject` definitions (App-layer) |
| `panels/` | Qt task panels shown in the FreeCAD sidebar |
| `palace/` | Palace helpers: config, meshing, result parsing, NetCDF4 serialization |
| `palace/results_db.py` | xarray/NetCDF4 read/write; `append_sweep_point` concatenates sweep runs |
| `palace/embedded_files.py` | Wraps `App::PropertyFileIncluded` so generated artifacts live inside the `.FCStd` |
| `docs/` | End-user markdown docs (rendered on GitHub) |

## Output File Layout

All generated artifacts are embedded directly inside the `.FCStd` archive via
`App::PropertyFileIncluded` (see `palace/embedded_files.py`) — nothing durable
is written to a sibling folder. Per-run scratch (Palace CSVs, per-iteration
mesh/config before embedding) lives in the document's transient directory and
is deleted automatically.

- Single simulation: `results.nc` on the Simulation object's `ResultsFile`
- Sweep/optimization: `sweep.nc` on the Sweep object's `SweepResultsFile` —
  runs concatenated via `results_db.append_sweep_point()` against a scratch
  copy, then re-embedded each iteration (see `commands/cmd_sweep.py:_embed_sweep_result`)
- Mesh/geometry/config from the last Run: `mesh.msh`/`geometry.step` on the
  Mesh object, `palace_config.json` on the Simulation object
- `commands/cmd_export.py` provides "Export Mesh/Geometry/Config…" commands
  to save a copy of any of these to a real file on disk
- Documents saved before this embedding existed are migrated automatically,
  once, the first time they're reopened (`onDocumentRestored` in each
  `features/*.py` calls `embedded_files.migrate_legacy_string_property()`);
  the original sibling folder is never deleted

## Non-obvious Invariants

### `add_row()` signal ordering — `panels/sweep_panel.py`
Connect `prop_combo.currentTextChanged` → `_on_property_changed` ONLY at the END
of the Optimize block in `add_row()`, after `prop_combo.setCurrentText(prop)` and
after all three `QTableWidgetItem`s (Min/Max/Initial) are inserted via `setItem()`.
If the signal is connected before `setCurrentText`, `load_params()` triggers the
auto-fill handler and overwrites saved values with current-document defaults.

### Nelder-Mead convergence — `commands/cmd_sweep.py`
Parameters are normalized to [0, 1] before scipy. Convergence uses `xatol` only
(default 0.01 = 1 % of each parameter's range). `fatol = float("inf")` disables
the objective-change criterion — EM objectives are squared dB deviations
(magnitude 100–10,000), making any absolute `fatol` arbitrary. Use `maxfev` (not
`maxiter`) so "Max iterations" in the UI equals Palace simulation count. Initial
simplex spans 50 % of normalized range to avoid scipy's default 5 % perturbation.

### `xarray`/`netCDF4` NOT in `_AUTO_INSTALL_PACKAGES` — `PalaceWorkbench.py`
These packages are intentionally absent from the auto-install list. Adding them
blocks workbench loading (they pull in pandas, takes minutes). In Docker they are
pre-installed in the `Dockerfile`. Native installs: `_require_xarray()` in
`results_db.py` prints the manual install command on error.

### Double `recompute()` before meshing — `commands/cmd_sweep.py`
`_apply_param_set` calls `doc.recompute()` after changing VarSet properties. A
second `doc.recompute()` is the first statement in both `_SweepCoordinator._launch_run`
and `_OptimizationCoordinator._launch_eval`, immediately before `generate_mesh`.
FreeCAD's dependency propagation doesn't always reach deep PartDesign chains
(VarSet → Sketch → Pad → Body) in a single pass. A redundant recompute on a
current document is near-instantaneous.

### `sync_port_group` child cleanup — `features/port_group.py`
Two types of children can be in a port's Group:
- `PartDesign::SubShapeBinder` — owned by `sync_port_group`; safe to
  `doc.removeObject(child.Name)`
- Stray geometry objects — user geometry added directly; must only call
  `port_obj.removeObject(child)` (detach without deletion)

Deleting stray objects destroys user geometry and breaks face references.
Applies to WavePort, LumpedPort, and ImpedanceBoundary.

### Cascade-delete must live on the ViewProvider's `onDelete`, not the DocumentObject's — `features/component.py`
FreeCAD's tree-view Delete command only consults `ViewProvider.onDelete(vobj,
subelements)` before removing an object — a plain `Document.removeObject()`
does **not** invoke any App-level `Proxy.onDelete` (confirmed empirically,
not just from docs). `ViewProviderComponent.onDelete()` calls
`delete_component_children()` to remove the master/shadow solids, the
`ImpedanceBoundary`, and any material group left empty as a result, then
returns `True` so FreeCAD removes the container itself right after. Without
this hook, deleting an SMDComponent (or any Component) via the tree only
removed the container, orphaning everything else. Because the cascade runs
inside FreeCAD's own Delete-command transaction (not a bare scripted
`removeObject`), a subsequent Undo restores every cascaded object too.

### Live results panel signal — `commands/cmd_sweep.py`, `panels/sweep_results_panel.py`
`_iter_emitter` is a module-level `QObject` singleton in `cmd_sweep.py` that emits
`iteration_done(str)` (sweep `.nc` path) after `_embed_sweep_result` in both
coordinators. The Sweep Results panel connects to it in `__init__`. This avoids
a circular import between `panels/` and `commands/`.

### `App::PropertyFileIncluded` resolved paths are read-only — `palace/embedded_files.py`
`resolve(obj, prop)` returns a real filesystem path, but FreeCAD marks it 0400
(read-only) — writing into it directly raises `PermissionError`, and the C++
docs explicitly forbid it (only property assignment may change the content).
To build on top of existing embedded content (e.g. `sweep.nc`'s per-iteration
append), copy it out with `copy_for_rewrite()` (not `shutil.copy2`, which
preserves the read-only bit onto the copy and just moves the failure) before
writing, then `embed()` the result back. Re-embedding under the same archive
name reuses the same resolved path, so `panels/sweep_results_panel.py`'s
`self._nc_path != sweep_nc` identity check across iterations still works.

### Legacy-document migration — `features/mesh.py`, `features/simulation.py`, `features/sweep.py`
Each `onDocumentRestored` calls `embedded_files.migrate_legacy_string_property()`
for its embedded fields, run unconditionally on every restore (not just once
ever) — it is idempotent: a property already holding real embedded content is
left alone, so it doesn't matter that `_init_properties()` (called first)
always pre-creates new fields like `ResultsFile`/`GeometryFile` as an empty
`PropertyFileIncluded` before migration gets a chance to run. Never deletes
the pre-refactor sibling-folder file it recovered from.

### Sweep/optimize iterations skip field-probe data and `ResultsFile` — `commands/cmd_run.py`, `commands/cmd_sweep.py`
`_on_done`'s `is_sweep_iteration` flag (set by both `_launch_passes` call sites
in `cmd_sweep.py`, left `False` for interactive Runs) skips parsing
`probe-E.csv`/`probe-B.csv` into the persisted dataset and skips re-embedding
`SimulationContainer.ResultsFile`. This exists because a real crash during an
Optimization run traced back to `E_real`/`E_imag`/`B_real`/`B_imag` field data
accounting for ~99% of `sweep.nc`'s size (confirmed nothing reads it back:
not `_compute_objective`, not the Sweep Results or S-Parameter panels) while
`sweep.nc` gets fully re-zipped into the `.FCStd` on every save (`PropertyFileIncluded`
has no diff/skip-if-unchanged logic) — a periodic autosave during a long
sweep repeatedly re-compressed an ever-larger file. `CharacteristicZ`/`Z0`
extraction is unaffected: `_update_wave_port_impedances` independently
re-parses the same probe CSVs itself. `append_sweep_point`'s `extra_attrs`
parameter (`palace/results_db.py`) merges per-run annotation into the same
write `append_sweep_point` already does — `_annotate_sweep_run` (a second
full read-modify-rewrite pass) no longer exists; use `_sweep_run_attrs()`
(pure, no I/O) instead.

### WavePort native `Z_PV` impedance vs. TE/TM fallback — `palace/config.py`, `palace/post_process.py`, `commands/cmd_run.py`
Palace ≥0.17.0 computes wave-port characteristic impedance natively (`Z_PV`) from
a `VoltagePath` (signal→ground line, sourced from `WavePort.IntegrationEdge` via
`_probe_points_along_edge`) written into each port's `WavePort` JSON entry —
`_build_boundaries()` emits it whenever `IntegrationEdge` is set, for every pass,
regardless of which port that pass excites. Ports *without* `IntegrationEdge`
(hollow single-conductor waveguide, no signal/ground split) have no `VoltagePath`
equivalent and fall back to the pre-0.17 Poynting-flux calculation
(`compute_te_tm_impedance` in `palace/post_process.py`) over a grid of field probes.

`_update_wave_port_impedances()` branches accordingly, and the two branches search
for their source data differently:
- **`Z_PV`** is excitation-independent (computed from the port's own 2-D
  boundary-mode eigenproblem, not the 3-D excited-state field) — Palace writes it
  into *every* pass's `port-Z.csv` for *every* `VoltagePath` port, so this branch
  searches all passes, not just the one where this port itself was excited.
- **TE/TM** uses actual field data from `probe-E.csv`/`probe-B.csv`, which is only
  physically clean at a port's own excited-state reference plane — this branch
  still requires a pass whose directory name contains `port{N}`.

`port-Z.csv` also contains a visually similar but physically different
per-excitation `Re{Z[idx][ex]}`/`Im{Z[idx][ex]}` column pair (input impedance
looking into the port, not the mode characteristic impedance). `parse_wave_port_z_csv()`
matches the literal `Z_PV` substring, not just `Z`, to avoid silently reading the
wrong quantity.

Single-excited-port and multi-pass runs now name their Palace output directory
the same way (`output_port{N}`) specifically so the "does this directory's name
contain `port{N}`" lookup above works identically regardless of pass count —
`_build_passes()` used to name the single-pass case bare `"output"`, which meant
`CharacteristicZ` silently never updated whenever only one port was excited (the
common case, since most driven runs excite a single port). `_on_done()`'s
`final_csv` path is derived from `passes[0][1]` for the same reason, rather than
assuming a hardcoded `"output"`.

## Dependencies

- **Runtime:** `matplotlib`, `numpy`, `xarray`, `netCDF4`, `gmsh` (lazy-loaded)
- **Environment:** FreeCAD 1.0, Palace binary, Python 3.11
- **Docker:** `ghcr.io/dangione/palace-workbench:latest` (main) / `:dev` (dev branch)

## Development Workflow

1. Push to `dev` branch → GitHub Actions builds and pushes `:dev` image
2. Test: `docker compose -f docker-compose.dev.yml up`
3. Open PR from `dev` → `main` → merge → `:latest` updates

## Code Quality

**Error checking:** After editing any Python file, run a syntax check:
```
python -m py_compile <file.py>
```
For a broader static analysis across all changed files use `pyflakes` (install once:
`pip install pyflakes`), then `python -m pyflakes <file.py>`.

**Code review:** Use the `/code-review` skill before committing non-trivial changes.
It launches a subagent that checks for correctness bugs, redundant logic, and
simplification opportunities.

**Test cases:** The FreeCAD GUI cannot be headlessly tested, but pure-Python modules
can and should have tests. New logic added to `palace/` (config generation,
results parsing, NetCDF4 serialization) and `features/` (property validation,
parameter normalization) should include `pytest` test cases in a `tests/` folder
mirroring the source layout. Run tests with:
```
python -m pytest tests/ -v
```


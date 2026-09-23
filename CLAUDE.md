# Palace Workbench — Developer Context

FreeCAD 1.0 Python workbench for EM simulation via the Palace solver (AWS Labs).
Docker-first: images published to GHCR. `tests/` has `pytest` coverage for the
pure-Python logic in `palace/` and `features/`; GUI-dependent behavior (panels,
tree rendering, ViewProvider hooks) has no automated coverage and is still
verified by running the container and exercising the GUI directly.

## File Layout

| Path | Purpose |
|---|---|
| `commands/` | FreeCAD `Gui::Command` subclasses (one per toolbar button) |
| `commands/cmd_run.py` | Orchestrates single simulations |
| `commands/cmd_sweep.py` | Orchestrates parameter sweeps and optimization |
| `features/` | FreeCAD `DocumentObject` definitions (App-layer) |
| `features/object_group.py` | Auto-managed tree groups for Ports/Impedance Boundaries/Components — no command or panel, see the invariant below |
| `panels/` | Qt task panels shown in the FreeCAD sidebar |
| `palace/` | Palace helpers: config, meshing, result parsing, NetCDF4 serialization |
| `palace/meshing.py` | Gmsh meshing backend |
| `palace/netgen_meshing.py` | Netgen meshing backend (alternative engine, see `MeshBackend` below) |
| `palace/mesh_dispatch.py` | The one place that picks Gmsh vs. Netgen per `Simulation.MeshBackend` — every call site imports `generate_mesh` from here, never directly from `palace.meshing`/`palace.netgen_meshing` |
| `palace/results_db.py` | xarray/NetCDF4 read/write; `append_sweep_point` concatenates sweep runs |
| `palace/embedded_files.py` | Wraps `App::PropertyFileIncluded` so generated artifacts live inside the `.FCStd` |
| `palace/shm_cleanup.py` | Pre-flight `/dev/shm` cleanup for orphaned Open MPI `vader_segment.*` files, called by `palace/runner.py` before every Palace launch — see the invariant below |
| `docs/` | End-user markdown docs (rendered on GitHub) |
| `tests/` | `pytest` coverage for `palace/`/`features/`, mirroring the source layout |

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

### Auto-managed tree groups for Ports/Impedance Boundaries/Components — `features/object_group.py`
Three document-singleton container objects (`PortGroup` holding both WavePort
and LumpedPort, `ImpedanceBoundaryGroup`, `ComponentGroup`) declutter the tree
once a document accumulates several ports/components, mirroring
`DielectricGroup`/`ConductorGroup`'s `App::FeaturePython` +
`App::GroupExtensionPython` + `ViewProvider.claimChildren()` pattern
(`features/material_group.py`). Unlike material groups, these have **no
command, no toolbar entry, and no task panel** — deliberately, per explicit
user direction: a group springs into existence via `get_or_create_*_group(doc)`
the moment a matching object is created (`features/wave_port.py`/
`lumped_port.py`/`impedance_boundary.py`/`component.py` all call
`add_to_port_group`/`add_to_impedance_boundary_group`/`add_to_component_group`
instead of the old `add_to_simulation`), and is deleted again by
`cleanup_group_if_orphaned()` once its last member is removed (called from
each member's own `ViewProvider.onDelete` — WavePort/LumpedPort/
ImpedanceBoundary gained an `onDelete` for exactly this; Component's existing
`delete_component_children()` gained one more call for the same purpose,
alongside its pre-existing material-group cleanup).

**FreeCAD's `Group` property does NOT enforce single-parent membership** — an
object can be listed in two different containers' `Group` lists at once, and
the tree renders it under both. Confirmed on a real board: four
SMD-component-derived `ImpedanceBoundary` objects were stale direct members of
`PalaceSimulation.Group` (left over from before `_is_palace_child` was trimmed
below) *in addition to* correctly joining the new group, so each rendered
twice. `_add_member()` strips stale `Simulation.Group` membership before
adding to the real group; `sync_all_groups()` (called from
`SimulationContainer.onDocumentRestored`, `features/simulation.py`) re-sweeps
this **unconditionally on every restore**, not just when a group doesn't
exist yet — a document can already have both the group and the stale
duplicate baked in, and `get_or_create_*_group`'s own sweep only runs on
first creation, so only the unconditional restore-time pass self-heals it.

**`sync_all_groups()` repairs three distinct kinds of drift, each found baked
into a real saved document (`projects/Coax_Directional_Bridge.FCStd`), not
added defensively.** Besides the stale double-membership above, it now also:

- **Re-parents a group that left `Simulation.Group`.** `add_to_simulation()`
  is otherwise called *only* by `get_or_create_*_group()`, which returns early
  when the group already exists — so a group that escaped stayed at the
  document's top level permanently. There is no drop target for it in the
  tree either (`ViewProviderSimulation` renders children via `claimChildren()`
  alone, and `onDocumentRestored` removes
  `Gui::ViewProviderGroupExtensionPython`), so a user cannot drag it back by
  hand. Confirmed on that board: its populated Components *and* Impedance
  Boundaries groups had both escaped.
- **Merges duplicate groups of the same kind.** The singular
  `find_*_group()` finders return the FIRST marker match and stop, which makes
  a second group permanently unreachable — `cleanup_group_if_orphaned()` looks
  its group up by finder, gets the other object, sees its member isn't in it,
  and returns, so the duplicate can never be garbage-collected. That board
  carried a populated `PalaceComponents` *and* an empty `PalaceComponents001`,
  the latter rendering as an empty "Components" group inside the Simulation.
  `_consolidate_duplicates()` keeps the first (the one the finders return, so
  every other code path agrees with the outcome) and moves the losers' members
  onto it before deleting them.
- **Prunes a group with zero members**, the restore-time catch-up for the
  same "delete once the last member is removed" rule
  `cleanup_group_if_orphaned()` applies during editing. A group holding only
  unrecognized stray objects is left alone — only genuinely empty ones go.

Relatedly, `delete_component_children()` (`features/component.py`) must call
`cleanup_group_if_orphaned()` for the component's `ImpedanceBoundaryObj`
itself: it removes that boundary with `doc.removeObject()`, which does **not**
fire the ViewProvider's `onDelete` where that cleanup otherwise lives (same
invariant as the cascade-delete note above). Without it, deleting the last
Component left an empty Impedance Boundaries group behind with no member left
to ever trigger its removal. `add_to_simulation()` (`features/__init__.py`)
logs a `Palace: WARNING —` instead of silently swallowing a failed re-parent —
it is the only path into `Simulation.Group`, so a silent failure strands the
object with nothing to put it back.

`features/simulation.py`'s `_is_palace_child()` (used by `_migrate_to_group`,
the pre-existing "sweep loose Palace objects into `Simulation.Group`"
migration) no longer matches LumpedPort/WavePort/ImpedanceBoundary — they have
their own group one level deeper now. Component was never in there to begin
with; `sync_all_groups` is its only grouping path.

### Port `Label` locked to `PortIndex` — `features/wave_port.py`, `features/lumped_port.py`
A port's `Label` used to be set once at creation
(`f"PalaceWavePort{index}"`/`f"PalaceLumpedPort{index}"`) and never touched
again. On a real board, enough create/delete/edit cycles left `Name`,
`Label`, and `PortIndex` all showing different numbers on the same object
(`Name="PalaceWavePort009"`, `Label="PalaceWavePort002"`, `PortIndex=2`).
`Label` is now always exactly `f"Port {PortIndex}"` — deliberately identical
between WavePort and LumpedPort, since they share one numbering sequence
(`next_port_index()` in `features/__init__.py` already combined both types
into a single counter; only the *display* was inconsistent, not the
underlying index). `onChanged()` re-derives `Label` whenever `PortIndex`
changes, mirroring the pre-existing `MeshAttribute = 10 + PortIndex` sync in
the same method; `onDocumentRestored()` re-applies it unconditionally too, so
an already-drifted document self-heals on next open. `ImpedanceBoundary` is
deliberately NOT included — it has its own separate numbering range
(`_IMPEDANCE_INDEX_BASE = 1000`) and "Port 1000" would be a nonsensical label.

### SMD component re-edit must preserve `Label` — `panels/smd_component_panel.py`
`SMDComponentPanel.accept()` re-edits an existing component by fully deleting
the old container (`delete_smd_component`) and building a brand-new one from
scratch (`create_smd_component`) rather than patching properties in place —
intentional, to avoid per-field diffing (see the code comment). But the new
container is a fresh `doc.addObject("SMDComponent")` every time, and
FreeCAD's internal Name-uniqueness counter for that base name is monotonic
and never reuses a suffix freed by deletion — so the visible name silently
climbed on every single re-edit (confirmed: `SMDComponent002` →
`SMDComponent005` after just one edit) if nothing restored the old `Label`
afterward. `accept()` now captures `self.existing.Label` before deleting and
reassigns it to `new_container.Label` after creating the replacement — same
technique already used there for `PortIndex` (captured from the old
`ImpedanceBoundaryObj` before delete, reused when calling
`create_smd_component`). The generic (non-SMD) `Component` panel
(`panels/component_panel.py`) does **not** have this problem — it patches the
existing container in place via `_apply_edits()`, never deletes/recreates it.

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

### Volume physical-group rebuild retries on stale Gmsh tag collisions — `palace/meshing.py`
`_update_vol_physical_groups_after_fragment()` re-registers each material's Gmsh
physical-volume group (attr 1 = airbox, `grp.MeshAttribute` per dielectric —
fixed numbers, since `palace/config.py`'s `Domains["Materials"]` block
references them by exact number) after the port/conductor `occ.fragment()`
pass, because `occ.synchronize()` silently invalidates physical groups whose
member entities were touched by a fragment. It removes and re-adds each group
individually (not one batch removal followed by a separate add loop) and
retries once if `addPhysicalGroup()` raises `"Physical volume N already
exists"` — a Gmsh OCC bookkeeping quirk where a group can still be reported as
present immediately after `removePhysicalGroups()` supposedly cleared it. Only
observed once a real document reached six material groups (16 fragment
volumes); not reproducible with a handful of boxes in an isolated gmsh script,
so treat it as an occasional Gmsh edge case rather than a deterministic bug to
fully root-cause. If a group still collides after the retry, it's skipped with
a `Palace: WARNING —` log line instead of aborting the whole mesh — one bad
material group shouldn't kill an otherwise-good mesh.

### Conductors are imported as volumes, then removed, not as bare faces — `palace/meshing.py`
`_import_conductor_solids()` imports each conductor group's fused shape into
Gmsh as a genuine OCC **volume** (one per disjoint solid), and
`generate_mesh()` includes it as an *object* — alongside the airbox/dielectric
volumes — in the combined port+conductor `occ.fragment()` pass, not as a 2-D
tool surface. This replaced an earlier approach
(`_import_conductor_solid_faces`, since removed) that imported only a
conductor's individual faces and relied on `occ.fragment()` to *infer* an
enclosed cavity from that loose, disconnected set. That inference was
confirmed unreliable on a real production document: checking Gmsh topology
adjacency directly (`getAdjacencies`) right after the fragment showed **100%**
of every conductor's own faces bounding either zero volumes (dangling) or two
*dielectric* volumes (absorbed into a coincident dielectric/dielectric
interface instead of becoming the conductor's own boundary) — both of which
leave boundary triangles with no adjacent tetrahedron, aborting Palace's MFEM
solver immediately (`STable3D` `operator()` assertion) despite meshing itself
completing without error. Importing the whole solid lets OCC's boolean kernel
actually carve the cavity; `getBoundary(combined=True)` then gives the true
outer skin, and the now-unlabeled interior volume is removed
(`gmsh.model.occ.remove(..., recursive=False)`) before mesh generation so it's
never meshed as a domain (Palace treats it as a PEC surface, not a region).

Two more things had to be handled once conductors became real volumes:
- **Conductor/conductor contact** (e.g. an SMD contact pad soldered onto a
  copper trace — two *different* conductor groups physically touching) leaves
  a face whose only two neighbors are conductor interiors, not one conductor
  plus real material. `getBoundary(combined=True)` cannot always fully cancel
  this out (same-group self-touching pieces from a conductor split by a
  heavily-fragmented dielectric region hit the identical symptom). Both are
  caught the same way: right before assigning a candidate boundary face to a
  group's PEC physical group, check its live `getAdjacencies()` — a genuine
  conductor boundary bounds exactly 2 volumes at that point (this conductor's
  own interior, not yet removed, plus real surrounding material), with
  neither side present in the *global* set of every conductor group's
  interior volumes. A face that fails is simply left untagged (no physical
  group → never written to the `.msh`, since `Mesh.SaveAll=0` by default) —
  dropping a handful of PEC surfaces at an already electrically-continuous
  junction is far cheaper than a hard Palace abort. **Do not** try to fix
  this by re-fusing a conductor group's own post-fragment volume pieces
  (`occ.fuse()` + `synchronize()`) before computing the boundary — tried once
  empirically and it made things dramatically worse (a previously-clean
  28-solid conductor group went from 0% to 72% orphaned), because the extra
  `occ.synchronize()` mid-loop silently renumbers/invalidates volume tags for
  conductor groups not yet processed in the same pass — the same class of
  surprise `occ.fragment()`+`synchronize()` already causes for physical
  groups (see the invariant above).
- A conductor face coincident with a port's own plane (needed to avoid a
  separate triple-edge MFEM crash — see `cond_skip_planes` in
  `generate_mesh()`) is now excluded by a plane-membership check *after* the
  fragment, not by skipping the face at import time — the whole conductor
  solid must stay intact (a single watertight shape) for the boolean fragment
  to correctly carve its cavity.

As an unexpected bonus, this fix also resolved a second, seemingly unrelated
symptom on the same document: the outer PEC boundary and one wave port had
**100%** of their own elements orphaned too, despite passing
`_assign_outer_boundary_groups()`'s own "bounds exactly 1 volume" filter —
that volume turned out to be one of the conductor's never-removed ghost
interior volumes. Once conductor interiors are properly removed before mesh
generation, this resolved to 0 orphans as a side effect, confirming it was a
downstream symptom of the same root cause rather than an independent bug.

**A regression this change introduced, only caught by testing other real
documents, not just the one being fixed**: planar/wave port physical-group
assignment (`planar_port_surf_list`/`wave_port_surf_list` in
`generate_mesh()`) builds a port's final surface tags straight from the
fragment's old→new map with no adjacency check at all — previously safe,
because conductors contributed no volume for a port face to be fragmented
against. Once conductors became fragment objects, a port face that
physically overlaps a conductor passing through it (the ordinary case for a
coaxial wave port wrapped around its own center conductor) gets a sliver
fragmented against the conductor's cavity instead of real background
material; that sliver was still being claimed by the port group, then
orphaned once the conductor's interior was removed. Confirmed as a true
regression (not pre-existing) by running the *original*, pre-fix
`_import_conductor_solid_faces`-based code — extracted via
`git show HEAD:palace/meshing.py` — against the same affected documents and
finding 0 orphans there. Fixed the same way as the conductor-side cases
above: `_touches_cond_interior()` rejects any candidate port surface
adjacent to the (still global, not-yet-removed) set of every conductor's
interior volume, applied in both the planar and wave port assignment loops;
resolving conductor volumes and building that global set now happens right
after `_update_vol_physical_groups_after_fragment()`, before port
assignment, instead of down in the conductor assignment block, since ports
are assigned first and need the set already built. This is also a
correctness improvement over the pre-fix behavior, not just a defect
avoidance: the old code silently meshed the port-face area actually
occupied by the conductor as if it were open aperture (vacuum/dielectric),
since conductors never had real volume to displace it — the new behavior
correctly excludes that area from the port instead.

Verified against every example `.FCStd` committed to this repo (filters,
splitters, microstrip, coax-to-microstrip) plus the two files this
investigation started from — 0 orphaned elements on every one that could
mesh at all (one file, `SM06FS012.FCStd`, fails for an unrelated,
pre-existing reason: no Airbox shape assigned).

### Netgen meshing backend — `palace/netgen_meshing.py`, `palace/mesh_dispatch.py`
A second meshing engine, selected per-document via `Simulation.MeshBackend`
(`"Gmsh"` default or `"Netgen"`) and dispatched through the single choke
point `palace/mesh_dispatch.py:generate_mesh()` — every call site
(`commands/cmd_run.py`, `palace/mesh_worker_main.py`) imports `generate_mesh`
from there, never from `palace.meshing`/`palace.netgen_meshing` directly.
`palace/meshing.py` (Gmsh) is untouched by this; the two backends share
nothing but the `(mesh_path, geometry_path, quality)` return contract, the
Gmsh MSH2 output format, and each source object's own `MeshAttribute`
numbering (`palace/config.py` needs zero backend-specific logic). Reused
directly from `palace/meshing.py`: `_collect_material_bodies`,
`_fuse_group_solids`, airbox-solid resolution. Domain identity uses COM+volume
matching (`_match_reference`), not `.name`/`.mat` propagation — `Glue()` does
not reliably preserve per-solid names through *nested* containment (airbox
containing dielectric containing conductor, this workbench's actual geometry
model). Boundary-condition tagging uses `.bc()` on the pre-mesh OCC faces
(ports tagged after conductors, so ports win on any overlap), read back after
meshing via `FaceDescriptor.bcname`/`.domin`/`.domout` — no post-mesh
`getAdjacencies()`-style querying needed the way Gmsh's `_touches_cond_interior`
requires, since Netgen exposes domain adjacency on the descriptor directly.
`netgen-mesher` runs inside the *same* `freecadcmd` subprocess Gmsh already
used (`palace/mesh_runner.py`/`mesh_worker_main.py`) — no separate venv
needed, confirmed by installing it alongside `gmsh` in the same `pip install`
line with no conflict.

Four real, production-confirmed bugs fixed here that must not regress:

- **Internal material-interface faces must never inherit the outer-boundary
  default.** A `FaceDescriptor` nobody explicitly `.bc()`-tagged keeps
  Netgen's own `"default"` bcname; the fallback logic used to apply the
  airbox's `OuterBoundaryType` (typically PEC) to *every* such face,
  including ones where `domout != 0` — i.e. a real kept domain on **both**
  sides, a genuine internal interface (e.g. dielectric/air), not an exterior
  wall. This PEC-shorted the tangential E-field across that entire interface
  board-wide, not locally — the direct cause of a real "S-parameters imply
  the board is a shorted mess" report. Fixed by checking
  `fd.domout != 0` and dropping those elements entirely from the exported
  mesh (`drop_surfnrs` in `_rewrite_physical_tags`) rather than tagging them
  — Palace applies domain continuity automatically there, matching
  `palace.meshing`'s own `_assign_outer_boundary_groups` exclusion of
  two-volume-adjacent surfaces.
- **`_match_occ_faces` needs both an area cap and a flatness check**, not
  just its documented relative plane-distance tolerance. A large, unrelated
  fragment (the rest of an airbox wall) can have its centroid coincidentally
  land inside a small target face's footprint by pure geometric symmetry —
  rejected by capping a candidate's own area at ~1.05× the target's (a true
  sub-fragment can never exceed the original face's area). Separately, a
  *perpendicular* face (e.g. a thin lumped-port box's vertical side wall) can
  have its centroid sit within the plane-distance tolerance purely because
  the box is short — rejected by requiring every one of the candidate's own
  vertices, not just its centroid, to lie within a small **fixed** (not
  area-scaled) tolerance of the target's plane. A third variant hits the
  containment check itself: a large, genuinely flat and coplanar fragment
  of the airbox's own wall can still have a centroid that geometrically
  falls inside a smaller target face's own footprint (not just "near" it —
  literally inside its 2-D boundary) while the fragment's own extent
  reaches well beyond the target's edge — confirmed on a real board,
  2.5mm past the target's own edge on both sides, at two different wave
  ports simultaneously. The area cap doesn't catch it (the fragment was
  *smaller* than the target), so every one of the candidate's own
  vertices, not just its centroid, must also lie within the target's own
  boundary via `distToShape`. Full reasoning for all three variants, plus
  the original annular-port centroid-mismatch case the scaled tolerance
  exists for, is in the function's own docstring — read it before touching
  this function again.

  A fourth board (`Coax_to_Microstrip_wComponents.FCStd`, forced onto the
  Netgen backend) showed the vertex-containment check above wasn't even
  being reached: a **centroid**-to-face containment check ran first (same
  scaled tolerance, capped at 0.5mm) and rejected a candidate whose area
  matched the target exactly and whose *every vertex* sat exactly on the
  target face (`distToShape` = 0.0 each) — about as strong a genuine-match
  signal as geometry gets — because Netgen's own `Face.center` for this
  particular shape lands 0.635mm from the true face, entirely in-plane
  (its out-of-plane component was exact). That centroid-to-face check has
  been removed outright: confirmed it was pure risk with no remaining
  benefit — re-running the fix against the board the vertex-containment
  check was originally added for produced byte-identical face-match counts
  and mesh-quality numbers to before, so the vertex check alone was
  already sufficient there too. The centroid-to-*plane* check earlier in
  the same function (a different, coarser pre-filter) is unaffected and
  stays.

  A fifth case (`Coax_Directional_Bridge.FCStd` and `projects/Directional
  Bridge.FCStd`, both via SMD-component impedance boundaries) found the
  *flatness* check has the same class of problem the centroid checks did —
  precision, not logic. It compares each vertex's distance from the
  target's plane against `min_tol`, a small **fixed** value (0.05mm by
  default) — and several SMD contact pads on these boards are themselves
  exactly 0.05mm tall, so a genuinely perpendicular side-wall face (straddling
  the target's plane from Z=0 to Z=0.05) had one vertex's computed distance
  land at `0.049999999999999996` instead of the mathematically exact `0.05`
  after a BREP roundtrip — just under the `>= min_tol` rejection by
  floating-point noise alone. This matched 4 faces where 2 was correct
  (confirmed the same mechanism explains a `10` vs. the correct `2` on
  `projects/Directional Bridge.FCStd`, noted as unexplained earlier in this
  same investigation), and fed Palace a boundary attribute spanning both an
  interior and an exterior face — logged by Palace itself as "Found
  boundary attribute with internal and external boundary elements" —
  which crashed the solver outright (SIGBUS) during matrix assembly.
  Fixed by adding `_approx_face_normal` (estimates a candidate's own
  normal from its vertices via cross product, not any parametric surface
  evaluation) and requiring it to be parallel, not perpendicular, to the
  target's — confirmed on the real board to cleanly separate every case,
  `|dot|` landing at `1.0` (to BREP precision) for every genuine match and
  `0.0` for every perpendicular side wall, regardless of how the box's own
  height happens to compare to `min_tol`. Skipped (not treated as a
  rejection) when a normal can't be estimated at all (fewer than 3 distinct
  vertices), so this can only reject an *additional* case beyond what the
  flatness check already catches, never let one through.
- **`_match_reference` needs a geometric-containment tiebreak for entries
  tied on center of mass**, not a volume-based heuristic. Solids that are
  coaxial and axially centered at the same point — a coax cable's center
  pin, its surrounding annular dielectric spacer, and its outer shield
  tube, when the spacer and shield happen to be the same length — all
  share a center of mass, and nearest-COM alone cannot rank entries that
  are exactly (or near-exactly) tied. Confirmed on a real board across
  three rounds of hardening: first the whole PTFE spacer (volume 47.6)
  matched the coax pin's own conductor reference (volume 6.2) purely
  because their COMs coincided, silently excluding the whole dielectric
  from the 3-D mesh (conductor volumes are removed, not meshed as a
  domain) and PEC-tagging every one of its faces — including both flat
  end-caps — as if they were the conductor's own. A volume-floor fix
  (reject any reference entry too small to contain the candidate) wasn't
  enough — confirmed by testing it against the same document: the shield
  tube (volume 41.2) is smaller than the spacer (47.6), so it still passed
  the floor, and the tube itself then started matching the spacer's
  reference instead of its own. A closest-volume tiebreak fixed that
  specific case but is still only a heuristic — an asymmetrically
  fragmented layer could in principle have its reduced volume land closer
  to some *other* tied entry's full volume than to its own.

  The actual fix: rank by COM distance first, same as always (cheap, and
  correct for the vast majority of solids with no such ambiguity at all).
  Only when two or more entries are tied to within `tie_tol` (~1 micron —
  BREP/COM roundoff, not a real physical separation) is the real,
  decisive question asked instead of estimated — does the candidate solid
  actually sit inside a tied reference's own original geometry? Verified
  empirically that `netgen.occ`'s own `*` (Boolean intersection) operator
  answers this cleanly, with no roundtrip needed between two shapes
  already in that kernel: a candidate intersected with its true parent
  returns its own full volume; intersected with a merely-*touching*
  different material — the exact adjacency concentric layers have with
  each other — returns exactly `0.0`. Reference entries now carry the
  actual geometry needed for this (a 6th tuple field, `geom`) — a netgen.occ
  solid where one is already in hand (conductor entries, and background
  entries once identified), or a FreeCAD `Part.Shape` where nothing else
  exists yet (the background/dielectric manifest built before anything
  has touched netgen.occ), converted to netgen.occ lazily
  (`_as_occ_solid`) only if a tie involving it is ever actually detected —
  so this costs nothing for the common, unambiguous case. Closest-volume
  is kept only as a fallback-of-a-fallback, for when containment itself
  can't decide (an OCC Boolean failure — an established, handled risk
  elsewhere in this file — or every tied entry's overlap coming back zero),
  logging a `Palace: WARNING —` either way so a real ambiguity never
  resolves silently on the weaker path. Read `_match_reference`'s own
  docstring — it walks through why neither a floor nor a volume tiebreak
  alone is enough — before touching this function again. Running
  containment for every candidate against every reference unconditionally
  (skipping the COM pre-filter entirely) was considered and rejected as
  needless work for the ~95% of solids that are already unambiguous, and
  it would scale worse on boards with many more solids than this one's 43.

  `generate_mesh_netgen()` calls this same function at **two** points —
  identifying each raw background (airbox/dielectric) solid straight off
  the STEP re-import, and again for every post-`Glue()` solid once
  conductors and ports are combined in — the first of these used to be a
  second, separate, un-hardened nearest-COM loop with none of this
  protection; a concentric dielectric shell (even with no conductor
  involved at all) could have hit the identical bug there. Both call sites
  now go through the one tested implementation.
- **`_wave_port_boundary_edges` (the Netgen equivalent of `palace.meshing`'s
  `PortBoundaryCurves`) must NOT exclude conductor-adjacent edges.** It once
  did, by analogy with Gmsh's `cond_skip_planes` — but that analogy was
  wrong: Gmsh excludes a *conductor's own face* from PEC tagging where it
  coincides with a port plane (avoiding a triple-edge MFEM conflict specific
  to its fragment-based topology), never the port's own boundary curve,
  which always gets the full outer-boundary treatment regardless of what it
  touches. Netgen's `.bc()` tagging makes ports win over conductors before
  meshing, so there's only ever one 2-D triangle at that location — the
  triple-edge conflict Gmsh avoids doesn't arise here, and excluding those
  edges just starved the wave-port eigenmode problem of a boundary condition
  it needed, producing a near-fully-evanescent (non-propagating) computed
  mode instead of the correct quasi-TEM one.

### TE/TM field-probe grid must be clipped to the port face's real shape — `palace/config.py`
`_probe_grid_over_port_face()` (used only for wave ports with no
`IntegrationEdge`, i.e. the TE/TM Poynting-flux fallback — see the `Z_PV`
invariant above) grids the port face's **bounding box**, not its actual
shape. For a non-rectangular face (e.g. an annular coax port), a fraction of
those points always land inside the unmeshed center-conductor hole or
outside the outer wall — reported by Palace at runtime as "Probe N could not
be found! Using default value 0.0." Purely cosmetic (the TE/TM impedance
formula is a ratio of two sums, so a zero-valued point contributes zero to
both and doesn't bias the ratio) but real log noise. Fixed by filtering the
grid to points that actually lie on the bounded face, via the same
`distToShape`-against-`Part.Vertex` containment check already established in
`palace/meshing.py`'s `_faces_within_freecad_face` and
`palace/netgen_meshing.py`'s `_match_occ_faces` — both of which document why
`Part.Face.isInside()` is avoided (unreliable on a naked, non-solid face).
Falls back to the unfiltered grid if filtering would remove every point, so
this can never produce fewer usable probes than before.

### Mesh-quality `is_bad` is volume-weighted, not a raw element count — `palace/meshing.py`, `palace/netgen_meshing.py`
Both backends classify tetrahedra as "bad" the same way — Gmsh's own
`minSICN`, Netgen's curved scaled-Jacobian (`_curved_tet_quality_batch`),
each below `MeshQualityWarnThreshold` (default 0.1) — but used to decide
`is_bad = n_bad > 0`: **one** bad element out of any number flagged the
*entire* mesh, feeding a blocking "Keep this mesh?"/"Run anyway?"
`QMessageBox` (`cmd_mesh.py`, `cmd_run.py`) that defaults to **No**.
Verified empirically to be a bad proxy for actual risk: mesh generation was
run, both backends forced (not just each document's own default), against
every example board in this repo plus a real in-progress board, capturing
full per-element quality *and volume* — every one of them, including
several with confirmed genuinely-inverted (negative-quality, not just
low-but-valid) elements, showed volume-weighted badness under 0.5% (usually
under 0.05%), while the old element-count fraction swung from under 0.01%
to over 23% *on the same document* depending only on which backend meshed
it — count doesn't just add noise, it doesn't even rank the two backends'
actual mesh quality consistently. A real Palace solve (not just a mesh
check) was run directly via `palace.runner.run_palace()` against a mesh
with 2 confirmed inversions (0.011% of its volume): it completed cleanly
and produced physically sane S-parameters, direct (not inferred)
confirmation that a small-volume inversion neither crashes the solver nor
corrupts the result.

`is_bad` is now `bad_volume_fraction > MeshQualityVolumeFraction (default
1%) or inverted_volume_fraction > MeshQualityInvertedVolumeFraction
(default 0.1%)` — both new `PalaceMesh` properties (`features/mesh.py`,
same "Refinement" group and `getattr(..., default)` fallback pattern as
`MeshQualityWarnThreshold`). Genuine inversions (quality < 0) get their own
stricter threshold since a folded/self-intersecting element is a
qualitatively worse defect than a merely-thin one, even at equal volume.
`n_bad`/`bad_fraction` (the old element-count stats) are kept in the
returned dict for diagnostic/log context but no longer decide `is_bad` on
their own. Confirmed the one synthetic case already proving the check can
catch a *real* problem (`test_check_mesh_quality_flags_degenerate_mesh`, a
deliberately 40x-too-coarse thin plate) is bad by volume too (100%, not
just by count) — this fix doesn't trade a false-positive problem for a
false-negative one. `commands/cmd_run.py`/`cmd_mesh.py`/`cmd_sweep.py`
needed no changes at all — they only ever read `quality["is_bad"]` and pass
the whole dict to `format_quality_warning()`, so only what causes the
dialog to fire changed, not the dialog/blocking behavior itself.

### GPU runs SIGFPE at FE order ≥ 2 — SLEPc's CUDA path, not memory — `palace/run_diagnosis.py`
A `Device=GPU` run of a real board (`projects/Coax_Directional_Bridge.FCStd`,
187,415 elements) dies with **signal 8 (SIGFPE)** immediately after Palace
prints `Assembling multigrid hierarchy`. GPU memory is full at that moment
(measured climbing 554 MiB → 7771 MiB during the run), which makes it look
like an out-of-memory failure. **It is not.** On x86-64, SIGFPE is an
*integer* divide-by-zero; floating-point operations produce inf/NaN instead
of trapping.

A gdb backtrace pins it exactly (registers at the fault: `rax = 1278924`,
the ND p=2 unknown count, divided by `rbx = 0`):

```
BVMultInPlace_BLAS_CUDA  <- SLEPc, CUDA basis-vector path
BVMultInPlace_Mat_CUDA
EPSSolve_KrylovSchur_Default / SVDSolve_Cross
palace::slepc::GetMaxSingularValue
palace::GetLambdaMax
palace::ChebyshevSmoother<ComplexOperator>::SetOperator
palace::DistRelaxationSmoother<ComplexOperator>::SetOperators
palace::GeometricMultigridSolver<ComplexOperator>::SetOperator
```

The Chebyshev smoother needs a largest-eigenvalue estimate, which Palace
gets through SLEPc; on GPU that runs SLEPc's CUDA BV routines, which divide
by zero here.

**It takes a Chebyshev-smoothed level AND a large one — not merely order ≥ 2.**
The smoother runs on the non-coarsest levels of the p-multigrid hierarchy,
which only exists at FE order ≥ 2, but size decides whether it actually
faults. Bisected against the same mesh, each varying exactly one thing:
- **Order 1, GPU — works.** Single level, 246,279 unknowns. Ran to iteration
  17/400 with sane S-parameters.
- **Order 2, GPU, adaptive sweep — SIGFPE.** Two levels, 1,278,924 on the
  smoothed level.
- **Order 2, GPU, uniform sweep — SIGFPE**, same point. The adaptive/PROM
  path is *not* implicated, despite appearing in the backtrace as the caller.
- **Order 2, GPU, a 50x smaller model (`examples/Microstrip_test_new.FCStd`,
  3,787 elements) — completes cleanly**, with the same two-level hierarchy
  shape (27,016 on the smoothed level). Do not restate this bug as
  "order ≥ 2 fails on GPU"; that was the first reading and testing a small
  order-2 model disproved it.
- `Solver.Linear.Type = "Jacobi"` — rejected by Palace itself before any
  solve ("Unsupported linear solver type for boundary mode solver"), so it
  is not an available route.
- `PETSC_OPTIONS="-bv_type vecs"` / `"-bv_type svec"` — **ignored**, still
  crashes. Palace sets the BV type internally, so the option never applies.
- **`Solver.Linear.MGMaxLevels = 1`, Order 2, GPU — does NOT crash.** Ran the
  full 420 s test window (exit 124 from `timeout`, not 136) with the
  hierarchy collapsed to a single `Level 0 (p = 2)`. This localizes the
  divide-by-zero to the presence of the **coarse p=1 level**, and is the
  strongest evidence for what the zero divisor actually is. It is *not*
  offered as a user workaround: a single-level hierarchy is a far weaker
  preconditioner, and this run produced no solver iteration output at all in
  420 s, versus order 1 reaching iteration 17/400 in ~440 s. The workbench
  writes no `Linear` block at all (`palace/config.py:_build_solver`), so
  reaching this setting needs a hand-edited config anyway.

Do **not** re-diagnose a GPU SIGFPE as an out-of-memory problem. This is now
a third distinct crash signature to keep apart from the two SIGBUS causes
documented below (`/dev/shm` exhaustion; malformed boundary attributes) —
all three look alike from outside, so `palace/run_diagnosis.py` matches on
the signal number reported in mpirun's own output line (mpirun substitutes
its own exit code for the child's, so `returncode` alone cannot tell them
apart).

**Shared GPU memory, measured** (WSL2, RTX 3060 Ti 8 GiB, driver 616.92,
via `libcudart` directly): `cudaMalloc` of 12 GiB **succeeds** — allocated,
written and synchronized, with `nvidia-smi` plateauing at ~7.8 GiB while the
remainder came from WDDM shared system memory. `cudaMallocManaged` of the
same size **fails** with `out of memory`. So plain device allocations spill
into shared memory and managed ones cannot; this build's hypre is
`HYPRE_USING_DEVICE_MEMORY` (unified memory `#undef`), so it is on the
spillable path.

**Exact root cause, confirmed against real SLEPc source (not inferred) —
and the decision not to patch it.** `git clone --branch v3.24.1
https://gitlab.com/slepc/slepc.git` (the exact tag Palace's superbuild
pins) and read `src/sys/classes/bv/impls/cuda/bvcuda.cu` directly.
`BVMultInPlace_BLAS_CUDA` checks `cudaMemGetInfo()`; when free GPU memory
can't hold the whole result matrix at once, it computes a fallback batch
size as `freemem/(m*sizeof(PetscScalar))` where `m` is the operator size
(`V->n`, our crashing level's ~1.28M unknowns) — dividing by `m`, the huge
dimension, rather than `n`, the small vector count actually being
multiplied (the same `d_work` buffer is sized `bs*n*sizeof(PetscScalar)`,
so the memory-correct divisor is `n`, not `m`) — collapses to 0 in integer
division once `m` is large relative to free memory, and two lines later
`PetscCall(PetscCuBLASIntCast(m % bs,&l))` — `m % bs` — modulo-by-zero,
SIGFPE. Exactly matches the gdb registers (`rax=1278924`=`m`,
`rbx=0`=`bs`). Confirmed still present, unchanged, at Palace v0.18.1 (see
the version-bump entry below) — this is a real upstream SLEPc bug, not
something introduced by this project or fixed by the version most recently
adopted here.

A defensive `bs` floor (e.g. clamp to ≥ 1) was considered and rejected: with
`m≈1.28M` and `bs=1`, the surrounding loop (`for (;l<m;l+=bs)`) would run
**1.28 million** individual tiny `cublasXgemm`/`cudaMemcpy2D` calls — avoids
the crash but turns a sub-second operation into something that could
plausibly take hours, the same "technically survives, practically unusable"
failure mode already documented above for `MGMaxLevels=1`. Fixing the
apparent `m`-vs-`n` divisor mismatch properly would avoid both problems, but
that's a real SLEPc source patch either way.

**Decision: don't patch Palace/PETSc/SLEPc source to work around this.**
Two routes were investigated and both carry real cost:
- A CPU-only PETSc/SLEPc build (MFEM/hypre/libCEED keep CUDA) was actually
  built and compiled successfully — two source patches applied
  (`extern/CMakeLists.txt`'s `include(ExternalSLEPc)` wrapped to
  save/force-OFF/restore `PALACE_WITH_CUDA` around just that include, since
  the file `include()`s every dependency unscoped and SLEPc's own include
  precedes hypre/libCEED/MFEM in file order; `palace/linalg/slepc.cpp`'s
  `PetscVecType()` patched to never select `VECCUDA`, since it reads MFEM's
  runtime device state rather than PETSc's own compile-time CUDA support) —
  but then hit a **second**, deliberate upstream guard:
  `palace/linalg/petsc.hpp:23-32` has an explicit
  `#error "Mismatch between MFEM and PETSc CUDA support!"` when
  `MFEM_USE_CUDA`/`PETSC_HAVE_CUDA` disagree, forbidding exactly this split
  configuration. Patching a defensive guard the Palace maintainers put there
  on purpose, on top of an already-two-patch workaround, felt like
  compounding risk rather than resolving it, so this was abandoned.
- Hand-deriving a fix for `GetLambdaMax`'s complex-operator branch (mirror
  PR #837's Hermitian-similarity-transform fix for the real-operator branch)
  was also considered and rejected — getting that math wrong produces
  silently-*incorrect* eigenvalues instead of a crash, a worse failure mode,
  and Palace's own maintainers are better positioned to write and validate
  that than we are.

**What we did instead: stay on GPU at order 1 (which never reaches this
code path — a single-level hierarchy has no Chebyshev smoother to set up),
and add cuDSS for the workload that actually dominates a real sweep.**
Order 1 GPU also has real memory headroom, not a near-miss: measured ~4.0
GiB peak on this same large board on the 8 GiB card, vs. order 2's ~7.8 GiB
right before the crash above — order 2 on a model this size is genuinely
memory-constrained on this hardware regardless of the SLEPc bug, since this
model's CPU-side memory usage (~12 GiB) already exceeds the card's 8 GiB
VRAM outright. See "NVIDIA cuDSS sparse direct solver" below.

### Palace version bumped v0.17.0 → v0.18.1 — both Dockerfiles
Bumped to chase the SIGFPE above (§4b of `PALACE_CUDA_ORDER2_HANDOFF.md`
hoped PR #837 fixed it outright) and to unlock cuDSS (below), which doesn't
exist as a build option before v0.18.0. The version bump did **not** fix
the SIGFPE (see above) but was kept anyway: 0.18.x's other changes are
independently worth it here — wave-port fixes matching this project's
geometry (0.18.0 PR 778 fixed 2-D mode eigensolver crashes with waveports +
non-zero conductivity, both present on these boards; 0.18.1 PR 921 improved
BoundaryMode convergence with lossy BCs), and performance work on the
wave-port-dominated bottleneck (0.18.0 PR 824/825 move boundary
postprocessing/field eval onto libCEED/GPU; 0.18.1 reduces repeated
complex-operator/PROM work and buffers adaptive-sweep CSV writes). The
`cmake/ExternalPalace.cmake` `sed` patch (`CMAKE_CUDA_HOST_COMPILER`
forwarding) needed no changes — confirmed byte-identical target line at
both tags. Known, not-yet-validated behavior changes from this bump, flagged
for whoever next touches wave-port results: per-port `MaxIts`/`KSPTol`
(`palace/config.py` ~166-169) go from silently-ignored to actually-effective
(0.18.1 PR 921); some passive lumped-port `port-S.csv`/`port-V.csv`/
`port-I.csv` values change from identically-zero to finite (0.18.0 PR 841);
a modal correction changes wave-port S-parameters for TM/hybrid modes only
(0.18.1 PR 886 — TEM/TE, i.e. coax, explicitly unaffected). None of these
have been diffed against a known-good 0.17.0 result yet.

### NVIDIA cuDSS sparse direct solver — `Dockerfile`, `.devcontainer/Dockerfile`
Added instead of chasing the SIGFPE above. `Solver.Linear.Type: "cuDSS"`
(Palace 0.18.0 PR 717) is a GPU-resident direct solver — confirmed via
config schema it applies only to `Solver.Linear.Type` (the main 3-D system
solve), not a separate wave-port-specific setting. **Does not avoid the
SIGFPE**: tested directly (before adding cuDSS) by setting
`Solver.Linear.Type = "SuperLU"` — already built into the image, a direct
solver in the same category as cuDSS — against the same order-2 crashing
config; it still crashed identically. `Solver.Linear.Type` only chooses the
*coarse-level* solver within Palace's multigrid hierarchy; the
intermediate-level Chebyshev smoother (where the actual bug lives) runs
regardless of that choice. cuDSS is valuable purely on performance
grounds: wave-port boundary modes are recomputed once per output frequency
and dominate a realistic sweep's wall clock (~89% of it, per
`PALACE_CUDA_ORDER2_HANDOFF.md`'s timing breakdown) — targeting that is
independent of, and unaffected by, the order-2 SIGFPE question.

**Verified working at runtime, not just at build time.** Ran a real,
full-scale order-1 GPU solve (`Coax_Directional_Bridge.FCStd`, all 400/400
sweep points, `Solver.Linear.Type: "cuDSS"`) to completion: exit 0,
physically sane S-parameters throughout, full Elapsed Time Report. This
also independently reconfirms the wave-port bottleneck at real scale, not
just the earlier 3-point sample: **Wave Ports timer read 4182.6s of
4960.3s total (~84%)**, in line with the ~89% figure `PALACE_CUDA_ORDER2_HANDOFF.md`
measured on a 3-point sweep. Not yet measured: a direct A/B comparison
against the same run with `Solver.Linear.Type` at its default — this run
confirms cuDSS is *functionally correct* on real hardware, not that it's
*faster* than the default solver for this workload. That comparison is a
real next step, not done here.

**Confirmed cuDSS is genuinely engaged for both the main solve and the
wave-port boundary-mode solve, not silently inert or falling back to
something else** — two independent lines of evidence, not just "it didn't
crash":
- **Source-level** (`palace/linalg/ksp.cpp`, `palace/models/modeeigensolver.cpp`,
  both read directly from a real v0.18.1 clone): `ModeEigenSolver::SetUpLinearSolver`
  does `LinearSolver pc_type = linear.type;` — our config's
  `Solver.Linear.Type` seeds `pc_type` directly. A fallback chain exists
  that would otherwise prefer SuperLU_DIST (built by default) over cuDSS,
  but it only fires `if (pc_type == LinearSolver::DEFAULT || ... AMS ||
  ... BOOMER_AMG)` — since our config sets it explicitly, that chain is
  skipped entirely and a real `CuDSSSolver` gets constructed, confirmed in
  both the main-solve dispatch and the wave-port module.
- **Empirical, contrastive**: ran the *identical* config/board against
  last night's non-cuDSS image (`palace-workbench:cuda-018`, same v0.18.1,
  built without `PALACE_WITH_CUDSS`). It failed immediately, before
  mesh/solve even started: `Verification failed: (solver.linear.type !=
  LinearSolver::CUDSS) is false: --> Linear solver cuDSS requested but
  Palace was not built with cuDSS support!`. If the cuDSS-enabled image's
  selection logic were silently falling back to something else, this
  cuDSS-specific up-front check wouldn't exist to fail on in the first
  place — the contrast between "hard fails immediately" (no cuDSS build)
  and "runs 400/400 points successfully" (cuDSS build) is direct proof the
  build, not just the config, is what's different.

Still genuinely open: whether cuDSS is *faster* than the default solver
here, and how much of the wave-port bottleneck is linear-solve time (where
cuDSS could help) versus per-frequency operator assembly/reprojection
(structurally outside what any direct solver can speed up, being assembly
work rather than a linear solve) — unmeasured, flagged as unresolved back
in `PALACE_CUDA_ORDER2_HANDOFF.md` §8 before cuDSS even existed as an
option here.

Not bundled in the CUDA Toolkit base image and not pip/conda-installable
for this purpose — Palace's own docs require `CUDSS_DIR` to point at a real
NVIDIA cuDSS *archive* install. Both Dockerfiles download it directly from
NVIDIA's redistributable manifest
(`developer.download.nvidia.com/compute/cudss/redist/redistrib_0.7.1.json`)
rather than a guessed URL: cuDSS **0.7.1.4**, the CUDA 12 Linux x86_64
build, extracted to `/opt/cudss` and passed as `-DCUDSS_DIR=/opt/cudss
-DPALACE_WITH_CUDSS=${PALACE_WITH_CUDSS}` (root `Dockerfile`; the
devcontainer hardcodes it on, matching its existing no-ARG pattern for
CUDA). Palace's own PR #826 (its cuDSS integration) notes cuDSS's MPI
communication plugin is compiled from source against whichever MPI Palace
itself uses, and NVIDIA only ships a prebuilt Open MPI binary — this
project's images already use Open MPI (`libopenmpi-dev`/`openmpi-bin`), so
this is a non-issue here, confirmed in a real build log (`cudss_commlayer_mpi`
built and linked clean).

**Pinned to 0.7.1, not the newest 0.8.0 release — a real build against
0.8.0.10 failed, root-caused rather than just avoided.** 15 compile errors
in `extern/mfem/linalg/cudss.cpp` ("too few arguments in function call" for
`cudssMatrixCreateCsr`/`cudssMatrixCreateDn`). Palace vendors cuDSS support
into MFEM itself via its own patch (`cmake/ExternalMFEM.cmake` downloads
`extern/patch/mfem/mfem_pr5124_cudss.diff` at build time, backporting
MFEM's own still-*unmerged* upstream PR #5124 — confirmed MFEM's pinned
commit, `d9d6526cc1749980a2ba1da16e2c1ca1e07d82ec` (the `mfem-4.9` release),
has zero files matching `*cudss*` anywhere in a real clone of it). That
vendored patch calls `cudssMatrixCreateCsr` with a single combined
index-type argument (`CUDA_R_32I`) — a 13-argument signature. cuDSS 0.8.0
changed this function to take two separate index-type arguments (14 args)
— confirmed by reading MFEM's own upstream `master` branch, which already
handles the split via `#if CUDSS_VERSION >= 800`, a check the older
vendored PR #5124 patch has no equivalent of. 0.7.1.4 is the newest cuDSS
release before that breaking change (confirmed against NVIDIA's own
redistributable manifest, which lists 0.7.1 immediately below 0.8.0). If
Palace ever updates its own vendored patch (or MFEM merges PR #5124
upstream with 0.8.0-compatible code), this pin should be revisited —
don't assume 0.7.1 stays correct indefinitely.

**Licensing is genuinely unresolved, and `PALACE_WITH_CUDSS` defaults OFF
in the root `Dockerfile` specifically because of it.** Unlike the CUDA
Toolkit math libraries already in this image (cuBLAS/cuSPARSE/cuSOLVER/etc.,
which have an explicit "Attachment A" redistributable-components list in
the CUDA EULA), cuDSS's own separate license agreement has no equivalent
list — it falls under general "distribute... binary files... as
incorporated into a software application" language, which plausibly covers
a real application like this one but is not a confirmed legal reading (not
reviewed by counsel or NVIDIA as of this writing). This project's own
license (MIT, permissive) doesn't trigger cuDSS's separate
can't-become-subject-to-an-open-source-license clause, which at least rules
out one specific concern. Do not flip `PALACE_WITH_CUDSS` on by default, or
publish a cuDSS-enabled image to GHCR, without treating that as its own
deliberate decision — local builds and testing only until resolved.

**Workbench-side gap, not yet closed:** `palace/config.py` writes no
`Solver.Linear` block at all (same `_build_solver` note as the
`MGMaxLevels` discussion above), so there is currently no way to select
`Solver.Linear.Type = "cuDSS"` from the GUI — only via a hand-edited config,
the same way the SIGFPE reproduction and the `SuperLU` test above were run.
Exposing it (a property + panel control + `config.py` write) is a real,
separate piece of work, not done as part of this entry.

### Umpire inside MFEM may be a real GPU adaptive-sweep bottleneck — researched, NOT applied
Surfaced from a real upstream report, not our own profiling:
[awslabs/palace#375](https://github.com/awslabs/palace/issues/375) ("Improve
GPU performance for adaptive sweep wave ports", open, unresolved) measured a
CPW board's adaptive wave-port sweep taking **11m24.7s on GPU vs. 1m35.1s
for the same board's non-adaptive case** — roughly 7x, and directly in the
same wave-port-re-solve hot path `PALACE_CUDA_ORDER2_HANDOFF.md`'s own §8
measured as ~89% of a real sweep's wall clock. A maintainer's follow-up
comment on that issue profiled it further: overhead traced to Umpire's
memory management inside `mfem::LinearForm::Assemble()`, and disabling
Umpire in MFEM measured **~6x faster** on the profiled hot function
(`MeasureAndPrintAll`), with GPU computation still correct. That comment
explicitly asks other contributors to verify the finding — it has not been
independently confirmed even upstream, and the issue remains open as of
Palace v0.18.1 (no linked PR).

Cross-referenced against MFEM's own issue
[mfem/mfem#5122](https://github.com/mfem/mfem/issues/5122) (closed, a
*different* symptom — ParaView output slowdown from Umpire host-allocation
overhead in `GridFunction::GetVectorValues`, not assembly) — same
underlying cause (Umpire's host-side allocation overhead), different call
site. MFEM maintainer v-dobrev's guidance there: **build hypre with Umpire,
MFEM without** — hypre benefits from Umpire's GPU memory pooling, MFEM's
own host-side allocations don't need it and pay for it anyway.

**Confirmed via Palace's own v0.18.1 CMake source that this split isn't
achievable with a build flag alone.** `PALACE_WITH_UMPIRE` isn't a
user-facing cache option — `CMakeLists.txt` hardcodes it: `if(PALACE_WITH_CUDA
OR PALACE_WITH_HIP) set(PALACE_WITH_UMPIRE ON) else() ... OFF endif()`, a
plain (non-cache) `set()` that overrides any `-D` passed on the command
line for as long as CUDA is on. `cmake/ExternalMFEM.cmake` then gates BOTH
Umpire being added to `MFEM_DEPENDENCIES` (build ordering — harmless either
way) AND the actual `-DMFEM_USE_UMPIRE=YES` MFEM build flag (the one that
matters) behind that same single `PALACE_WITH_UMPIRE` switch — there's no
independent toggle for "hypre gets Umpire, MFEM doesn't" as the code
stands. Achieving v-dobrev's recommended split would need a targeted sed
patch in the Dockerfile forcing `-DMFEM_USE_UMPIRE=NO` specifically in
`ExternalMFEM.cmake`'s options list, leaving `PALACE_WITH_UMPIRE`/hypre's
own Umpire usage untouched.

**Deliberately not applied tonight.** Different scope from the cuDSS work
above (a sweep-performance lever, not something blocking a working build),
unverified even by its own reporter, and — like the abandoned §5 CPU-only
PETSc/SLEPc attempt — would mean carrying yet another source patch, on the
same night source patches were specifically being avoided in favor of
cuDSS. Worth real testing before committing to it: measure the same
adaptive wave-port sweep with and without `-DMFEM_USE_UMPIRE=NO` on real
hardware first.

### Multi-rank CPU is broken in the CUDA-enabled build — devcontainer stays on the plain (non-CUDA) image for now
`mpirun -n 2` (and `-n 4`) hangs on `Device=CPU` in the CUDA-enabled image
(`palace-workbench:cuda-018-cudss`), confirmed via a live `gdb`/`cuda-gdb`
backtrace, not just a timeout:

```
PMPI_Iprobe → hypre_DataExchangeList → hypre_ParCSRCommPkgCreateApart
  → hypre_ParCSRMatrixExtractBExt → hypre_ParCSRMatrixRAPKTHost
  → WavePortData::Initialize → WavePortOperator::Initialize
```

This is the same `hypre_ParCSRCommPkgCreateApart` ("Assumed Partition")
family already documented in `PALACE_CUDA_ORDER2_HANDOFF.md` §8 for the
4-rank/main-system case, but reproduced here independently at **rank 2**,
and stuck in the much smaller **2-D wave-port eigensolve** setup rather
than the main 3-D system — both ranks pegged at ~99% CPU for 9+ minutes
(longer than an entire single-rank 3-point sweep) with zero forward log
progress. This rules out "AMG chokes once the matrix is large" as the
explanation: it's "any hypre distributed-matrix construction deadlocks the
instant more than one rank is involved," independent of problem size.

**Confirmed NOT a CUDA-transport issue**: `ompi_info -a | grep cuda` shows
`opal_built_with_cuda_support = false` — this Open MPI build has no CUDA
GPU-buffer support compiled in at all, so there's no GPUDirect/CUDA-IPC
negotiation to blame, and the hang reproduces on a plain `Device=CPU` run.
Previously ruled out in the same investigation: `/dev/shm` exhaustion,
forcing full (non-partial) matrix assembly.

**Confirmed specific to the CUDA-enabled build, not a pre-existing/generic
environment issue.** The exact same rank=2 CPU test (same host, same Open
MPI 4.1.2, same WSL2/Docker environment), run against `ghcr.io/dangione/
palace-workbench:dev` (a plain, non-CUDA image, still on Palace v0.17.0 —
built 2026-07-25, before any of this session's CUDA/v0.18.1 work) completed
cleanly: exit 0, 8.2s wall time, confirmed via log as genuinely `Running
with 2 MPI processes` / `Device configuration: cpu`, exercising the exact
same wave-port hypre assembly code path that deadlocked on the CUDA image.
The two images share host, Open MPI version, and general environment; the
only substantive difference is that the CUDA image's hypre is compiled with
`HYPRE_USING_CUDA`/`HYPRE_USING_DEVICE_MEMORY` (confirmed earlier in this
same file's SIGFPE investigation), which is plausibly why: those flags can
make hypre allocate its MPI communication buffers via pinned/page-locked
host memory (`cudaHostAlloc`) even on a `Device=CPU` run, since it's a
compile-time hypre setting, not a per-run switch — and Open MPI's `ob1` PML
may handle pinned-vs-plain host buffers differently in the exact
Iprobe-based rendezvous protocol `hypre_DataExchangeList` uses. Not fully
isolated from the v0.17.0→v0.18.1 version bump (the non-CUDA comparison
image is still on v0.17.0), but the bug lives entirely inside hypre's own
buffer construction, making the CUDA compile flags the much stronger
suspect of the two.

**Decision: shelve root-causing this, and don't make the CUDA-enabled
image the devcontainer default.** Two independent lines of reasoning:
- No matching upstream bug report was found (searched for
  `hypre_DataExchangeList`/`AssumedPartition` + Open MPI + Iprobe hangs);
  fixing it properly would mean bisecting Open MPI versions or hypre's own
  CUDA memory-allocator code with no confirmed root cause to aim at.
- The CUDA build's actual benefit is narrow enough that this isn't worth
  chasing right now: GPU only helps at FE order 1 (order ≥2 SIGFPEs — see
  above, deliberately unpatched), already forces `NumProcesses=1`
  regardless of this bug (existing workbench behavior), and the real
  sweep-time bottleneck (wave-port re-solve, ~84-89% of wall clock per the
  cuDSS entry above) isn't touched by GPU, cuDSS, or CPU rank count either
  way — it's algorithmically bound, pending upstream Palace PR #909.

The regular `:dev`/`:latest` devcontainer path (non-CUDA) is unaffected and
stays the default — multi-rank CPU has always worked there, confirmed
above. The CUDA-enabled image/devcontainer variant remains available for
order-1 GPU and cuDSS experimentation, but should be treated and documented
as single-rank-only in practice, not a general-purpose replacement.

### `/dev/shm` cleanup for orphaned Open MPI `vader` segments — `palace/runner.py`, `palace/shm_cleanup.py`
Open MPI's `vader` BTL (its shared-memory transport for intra-node inter-rank
communication) creates one file per rank in `/dev/shm`, named
`vader_segment.<hostname>.<uid>.<jobid>.<rank>`. These are removed on a
clean exit but are **left behind** whenever a Palace/`mpirun` job crashes or
is killed (`SIGKILL`, including via `CmdStopRun`'s `killpg`). Docker's
default `/dev/shm` here is only 64MB (`--shm-size` intentionally left at
that default, not changed, per explicit decision); on a real production
document, several days of crashed/killed runs had left 34 orphaned files
consuming 58MB/64MB (91% full, 6.3MB free), and Palace then crashed with
`SIGBUS` (signal 7) during matrix assembly on a document with no actual
mesh/config defect. Manually deleting all 34 files (after confirming via
`ps`/`pgrep` that no `mpirun`/`palace-x86_64.bin` process was still running)
brought usage to 0%, and the exact same simulation then ran to completion —
confirming the cause. This is a **different** root cause than the SIGBUS
documented under "Netgen meshing backend" above (malformed boundary
attributes from a face-matching bug) — both happen to produce the identical
`SIGBUS` signal during matrix assembly, so a future SIGBUS should not be
assumed to be either one without checking `/dev/shm` usage as well as
boundary-attribute sanity.

`run_palace()` now calls `palace.shm_cleanup.cleanup_stale_shm_segments()`
before every launch (once per pass, so a multi-port sweep is re-checked
before each pass too, at no extra plumbing cost). Liveness is checked
against real running processes — `/proc/<pid>/fd/*` **and**
`/proc/<pid>/maps`, both — never file mtime alone, which is a racy proxy
for "still in use." Both checks are required, not redundant: vader
`mmap()`s the segment and typically closes the raw fd afterward, so `maps`
is often the only signal that still shows the reference. **Must not**
regress to an mtime-only or fd-only check — a currently-running, unrelated
concurrent Palace simulation's own segments must never be deleted out from
under it. Every removed and every kept (still-in-use) file is logged
individually — **must not** go silent, since the failure mode this exists
for (crash-induced buildup unnoticed for days) is precisely what silent
cleanup would reproduce. After cleanup, `warn_if_shm_full()` (default
threshold 80% — a single confirmed data point at 91%-used/crashed and
0%-used/fixed, no finer-grained measurement exists, so treat this constant
as a conservative extrapolation, not a validated boundary) logs a real
`FreeCAD.Console.PrintWarning` if usage is still high even after removing
everything safely removable — that can only mean a genuinely live
concurrent job is using the space, so the message tells the user to wait
or stop another simulation.

`palace/shm_cleanup.py` has no FreeCAD/Qt dependency by design (pure
`os`/`glob`/`shutil`), so its liveness/removal logic is fully unit-testable
without mocking `/proc` — a test can `open()` (or `mmap()`) its own
`tmp_path` file and the *test process itself* then legitimately shows up
as a live referencer. `run_palace()` is the only place `mpirun`/the Palace
binary is actually launched in this repo, so hooking only this one function
is sufficient coverage, including for `commands/cmd_sweep.py`'s
sweep/optimize runs (which reuse `cmd_run.py`'s
`_build_passes`/`_launch_passes` → `_PassWorker` → `run_palace()`, the same
path).

## Dependencies

- **Runtime:** `matplotlib`, `numpy`, `xarray`, `netCDF4`, `gmsh`, `netgen-mesher`
  (both meshing backends lazy-loaded; see `palace/mesh_dispatch.py`)
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


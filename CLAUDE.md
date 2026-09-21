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


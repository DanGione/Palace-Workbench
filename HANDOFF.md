# Palace Workbench — Session Handoff

## What this is

A FreeCAD 1.0.0 workbench that integrates the [Palace](https://github.com/awslabs/palace)
3D electromagnetic FEM solver (from AWSLabs) into the FreeCAD GUI. Palace is an
open-source, parallel finite element code for full-wave 3D EM simulations built on MFEM.

The workbench lives at:
```
/workspace/   (symlinked into ~/.FreeCAD/Mod/PalaceWorkbench and ~/.local/share/FreeCAD/Mod/PalaceWorkbench)
```
FreeCAD loads it automatically on startup. Switch to it via the workbench selector dropdown.

---

## File map

```
PalaceWorkbench/
├── __init__.py              # empty package marker
├── Init.py                  # non-GUI init; adds workbench dir to sys.path so saved
│                            # documents can restore Palace proxy objects without GUI
├── InitGui.py               # registers PalaceWorkbench() with FreeCADGui
├── PalaceWorkbench.py       # Workbench class — toolbar, menu, command registration
│
├── features/                # App::FeaturePython document objects + ViewProviders
│   ├── __init__.py          # shared finders: find_simulation, find_airbox,
│   │                        #   find_lumped_ports, find_wave_ports, find_palace_mesh,
│   │                        #   find_impedance_boundaries, next_port_index, add_to_simulation
│   ├── simulation.py        # SimulationContainer — root Palace object; all solver settings
│   ├── airbox.py            # Airbox — links to a FreeCAD solid; outer boundary type
│   ├── lumped_port.py       # LumpedPort — R/L/C port with face selection + direction
│   ├── wave_port.py         # WavePort — modal port with face selection + NumModes
│   ├── impedance_boundary.py# ImpedanceBoundary — Rs/Ls/Cs surface impedance; NEW 2026-05-29
│   ├── port_group.py        # sync_port_group() — SubShapeBinder/stray-object Group mgmt; NEW 2026-05-29
│   └── mesh.py              # PalaceMesh — mesh settings + 3D display; NEW 2026-05-18
│
├── panels/                  # Qt task panels (open on double-click or command activation)
│   ├── __init__.py
│   ├── simulation_panel.py  # tabbed: General + Driven/Eigenmode/Electrostatic settings
│   ├── airbox_panel.py      # shape picker + outer boundary type + per-face BCs
│   ├── lumped_port_panel.py # index, face picker, direction, R/L/C, excitation
│   ├── wave_port_panel.py   # index, face picker, IntegrationEdge picker, NumModes, excitation,
│   │                        #   CharacteristicZ (read-only), RenormZ; UPDATED 2026-05-19
│   ├── impedance_boundary_panel.py  # index, face/edge picker, direction, R/L/C ↔ Rs/Ls/Cs toggle; NEW 2026-05-29
│   ├── mesh_panel.py        # element sizing + mesh stats; Generate Mesh button; NEW 2026-05-18
│   ├── palace_console.py    # dockable Palace output console (QDockWidget)
│   └── s_param_panel.py     # dockable S-parameter plot viewer (QDockWidget, matplotlib)
│
├── commands/                # FreeCAD commands (toolbar buttons / menu items)
│   ├── __init__.py
│   ├── cmd_simulation.py    # Palace_Simulation — create SimulationContainer
│   ├── cmd_airbox.py        # Palace_Airbox     — create Airbox from selection
│   ├── cmd_lumped_port.py   # Palace_LumpedPort — create LumpedPort
│   ├── cmd_wave_port.py     # Palace_WavePort   — create WavePort
│   ├── cmd_impedance_boundary.py  # Palace_ImpedanceBoundary — create ImpedanceBoundary; NEW 2026-05-29
│   ├── cmd_mesh.py          # Palace_Mesh — mesh only (no solve); NEW 2026-05-18
│   ├── cmd_run.py           # Palace_Run + Palace_RunOnly + Palace_StopRun
│   └── cmd_results.py       # Palace_ViewResults — open S-parameter viewer
│
├── palace/                  # Palace back-end logic (no FreeCAD GUI dependencies)
│   ├── __init__.py
│   ├── config.py            # generate_config(doc, only_excitation, output_override)
│   │                        # write_config(doc, path) → JSON
│   ├── meshing.py           # generate_mesh(doc, output_dir, log_fn) → .msh path via Gmsh
│   ├── runner.py            # run_palace(config_path, binary, num_procs, line_callback)
│   └── post_process.py      # merge_s_matrix(), write_touchstone(),
│                            # parse_probe_csv(), compute_wave_port_impedance(),
│                            # renormalize_s_matrix(s_csv, port_z_old, port_z_new)
│
├── test_headless.py         # end-to-end headless test via freecadcmd
├── Coax_test.FCStd          # FreeCAD file: 50 Ω coaxial through for GUI testing
│
└── resources/icons/         # SVG icons for each command (Run.svg, Stop.svg, Results.svg, …)
```

---

## Key design decisions

### Object identification
Palace objects are identified by checking for a characteristic combination of properties,
not by class name. The finders in `features/__init__.py` use this pattern:

```python
# SimulationContainer
hasattr(obj, "SimulationType") and hasattr(obj, "PalaceBinary")

# Airbox  (AirboxShape was removed 2026-05-16; Group[0] holds the solid now)
hasattr(obj, "OuterBoundaryType")

# DielectricGroup
hasattr(obj, "Permittivity") and hasattr(obj, "MeshAttribute")

# ConductorGroup
hasattr(obj, "ConductorType") and hasattr(obj, "MeshAttribute")

# LumpedPort  (has R, L, C but NOT Rs — ImpedanceBoundary also has R/L/C)
hasattr(obj, "PortIndex") and hasattr(obj, "R") and hasattr(obj, "C") and not hasattr(obj, "Rs")

# WavePort
hasattr(obj, "PortIndex") and hasattr(obj, "NumModes") and not hasattr(obj, "R")

# ImpedanceBoundary  (Rs is the unique discriminator)
hasattr(obj, "Rs") and hasattr(obj, "MeshAttribute")
```

### Mesh attribute numbering
| Entity | Gmsh physical attribute |
|---|---|
| Domain volume (airbox interior) | 1 |
| Outer boundary surfaces | 2 |
| Port i (lumped or wave) | 10 + i |

### Palace JSON frequency units — CRITICAL
Palace's driven solver expects frequencies in **GHz** in the JSON config, not Hz.
`config.py` stores `DrivenMinFreq`/`DrivenMaxFreq`/`DrivenFreqStep` as Hz in FreeCAD
properties and divides by 1e9 when writing JSON. The simulation panel displays and edits
in GHz. Eigenmode `Target` is likewise in GHz. Do not change this without updating both
`config.py` and `simulation_panel.py`.

### Port Excitation flag
In Palace JSON, `"Excitation": true` must be **explicitly present** on the port entry
for it to drive the simulation. Omitting it defaults to `false`. `config.py` only writes
the key when `p.Excitation is True`.

### Radial lumped port direction
Coaxial geometry requires `"Direction": "+R"` (or `"-R"`). This tells Palace to use
`CoaxialElementData` with excitation field `E = ê_r/r`. Without `+R`, Palace falls
through to `UniformElementData` (Cartesian), which requires a rectangular planar
port face — it will crash with "Planar port geometry is not a quadrilateral!" on a
circular face.

### Single vs multiple excitations — CRITICAL
When **only one port** has `Excitation: true`, Palace runs one frequency sweep and writes
`port-S.csv` with S[row][1] columns (S11, S21, …).

When **multiple ports** have `Excitation: true`, Palace attempts a multi-pass S-matrix
extraction. In practice (observed with Palace 0.12.0 and coaxial `+R` lumped ports) only
7 solves run (one pass) and **`port-S.csv` is never updated** — the stale file from any
previous single-excitation run remains. The cause is unclear (may be a Palace quirk with
coaxial port symmetry). Do NOT rely on multi-excitation within a single Palace config for
either lumped or wave ports.

**Correct approach for full N-port S-matrix:** run N separate Palace simulations, each
with exactly one port excited. `cmd_run.py` does this automatically for both lumped and
wave ports: when N ports (of any type) have `Excitation: true`, it generates N per-port
configs and runs them sequentially in the background thread, then merges the
per-excitation `port-S.csv` files.

### Face matching (meshing.py)
FreeCAD face references are matched to Gmsh surface entities by comparing bounding-box
centre-of-mass. FreeCAD internal units are mm; the exported STEP file is also mm; Gmsh
reads in mm — no unit conversion needed at the matching step.

### Qt version
Both PySide2 and PySide6 are tried (PySide2 first) in every panel file.

### FeaturePython proxy persistence
All state is stored in FreeCAD properties (not Python instance variables). Every proxy
and ViewProvider implements `__getstate__` / `__setstate__` returning `None`.

### Property initialisation guard
Every `_init_properties` uses `if not hasattr(obj, name)` before `addProperty`.

### Headless safety
All `create_*` factory functions guard `ViewProvider` construction with
`if obj.ViewObject is not None`. `Init.py` guards `__file__` with
`if "__file__" in dir()`. This allows `freecadcmd` headless runs.

---

## Supported simulation types

| Type | Palace solver block | Key settings |
|---|---|---|
| Driven | `Solver.Driven` | DrivenMinFreq/MaxFreq/FreqStep (stored Hz, written GHz) |
| Eigenmode | `Solver.Eigenmode` | EigenNumModes, EigenFreqMin (stored Hz, written GHz as "Target") |
| Electrostatic | `Solver.Electrostatic` | ElecMaxIter, ElecTol |

---

## User workflow (intended)

1. Open FreeCAD, switch to **Palace** workbench.
2. Model your geometry in Part/PartDesign. The simulation domain should be a single
   closed solid. Port regions are faces of that solid.
3. **New Simulation** → set type, frequencies/modes, FE order, optionally set Palace binary.
4. Select the airbox solid → **Set Airbox** → choose outer boundary type.
5. Select a face (or faces) → **Add Lumped Port** or **Add Wave Port** → set index,
   direction (use `+R` for coaxial), impedance, NumModes (wave only).
6. Repeat step 5 for each port. Set `Excitation: true` on all ports you want to
   characterise; the workbench runs one Palace pass per excited port automatically.
7. **Generate & Run** → Gmsh meshes the geometry (output streams to Palace Output console)
   → config(s) written → Palace runs in background (output streams to console).
8. On completion, `palace_output/output/port-S.csv` and `port-S.sNp` (Touchstone) are
   written. For multi-excitation runs, per-pass results land in
   `palace_output/output_portN/` and are merged into the combined `output/port-S.csv`.
9. All output also lands in `<document_dir>/palace_output/` (or a temp dir if unsaved).

---

## Output files

| File | Description |
|------|-------------|
| `palace_output/mesh.msh` | Gmsh mesh (MSH 2.2, order-2 curved elements) |
| `palace_output/geometry.step` | Exported geometry (for Gmsh import) |
| `palace_output/palace_config.json` | Config for single-excitation runs |
| `palace_output/palace_config_portN.json` | Per-port configs for multi-excitation runs |
| `palace_output/output/port-S.csv` | S-parameter results (Palace format, dB + degrees) |
| `palace_output/output/port-S.sNp` | Touchstone 1.0 export (DB format, R=50 Ω) |
| `palace_output/output_portN/port-S.csv` | Per-excitation results (multi-pass runs) |
| `palace_output/output_portN/probe-E.csv` | E-field at probe points (V/m) — written when `Domains.Postprocessing.Probe` present; used for Z_c extraction |
| `palace_output/output_portN/probe-B.csv` | B-field at probe points (Wb/m²) — same condition |

---

## Background simulation & multi-pass S-matrix (cmd_run.py)

`CmdRun.Activated()` does all FreeCAD document access on the **main thread**, then
hands off to a `_SimWorker(QThread)` for the blocking Palace process(es).

**Single excitation (1 port with `Excitation: true`):**
- Generates `palace_config.json` (standard)
- One Palace run → output to `output/`
- Writes `output/port-S.sNp`

**Multi-excitation (N ports with `Excitation: true`):**
- Generates `palace_config_portK.json` for each K, with `output_override` pointing to
  `output_portK/`
- `_SimWorker` runs each config sequentially
- On completion: `post_process.merge_s_matrix()` merges the N `port-S.csv` files column-
  by-column into `output/port-S.csv`, then `write_touchstone()` writes `output/port-S.sNp`

**Thread safety:** `console.write()` is a Qt signal emission (thread-safe). All FreeCAD
document reads happen before the thread starts. The worker receives only file paths.
`CmdRun._worker` is a class-level reference so GC cannot collect a running thread.

**Stopping a running simulation (`Palace_StopRun`):**
- `CmdStopRun.IsActive()` returns True only while `CmdRun._worker.isRunning()`.
- `CmdStopRun.Activated()` prompts for confirmation, then calls `_SimWorker.stop()`.
- `_SimWorker.stop()` sets `_cancelled = True` and calls `os.killpg(proc.pid, SIGKILL)`.
- Palace is launched with `start_new_session=True` (runner.py), which puts it and all
  MPI child processes (OpenMPI daemons, worker ranks) into a new process group whose PGID
  equals the top-level Palace PID. `os.killpg` kills the entire group atomically.
- **Why not `proc.terminate()` or `proc.kill()`?** `terminate()` (SIGTERM) is caught by
  Palace for graceful cleanup — the solver finishes its current linear step first (can take
  minutes). `proc.kill()` (SIGKILL on the parent) leaves MPI child processes alive because
  they are in the same process group as FreeCAD's Python process; orphaned children keep
  the stdout pipe open, so the read loop in `runner.py` never gets EOF and the worker
  thread never unblocks. `os.killpg` on the isolated session group is the only reliable approach.
- After kill: stdout EOF unblocks the `for line in proc.stdout:` loop immediately; `proc.wait()`
  returns at once; `_SimWorker.run()` sees `_cancelled=True` and emits an abort log entry
  (via the `log` signal, marshaled to main thread) plus the `done` signal. `_on_done()`
  prints a red "ABORTED" banner to the Report View via `PrintError` and does NOT show an
  error dialog (cancellation is intentional, not an error).

---

## S-matrix post-processing (palace/post_process.py)

### `merge_s_matrix(pass_output_dirs, output_path)`
Reads `port-S.csv` from each per-excitation directory and concatenates the non-frequency
columns side-by-side. Palace's header format (`|S[row][col]| (dB)`) is preserved verbatim.
Raises `FileNotFoundError` if any expected CSV is missing; `ValueError` on frequency
point count mismatch.

### `write_touchstone(csv_path, reference_impedance=50.0)`
Parses Palace-format headers to discover port count N and column-to-(row,col) mapping.
Writes a Touchstone 1.0 `.sNp` file in `DB` format (`# GHz S DB R 50`) with column-major
S-parameter ordering (S[*][1] first, then S[*][2], …). Lines wrap at 4 S-params each
(Touchstone 1.0 spec). Output is written next to the source CSV (`port-S.csv` →
`port-S.s2p` for a 2-port, `.s3p` for 3-port, etc.). Returns the output path.

**Note on partial Touchstone:** if only one port was excited (single-pass run), the CSV
only contains S[row][1] columns. `write_touchstone` will write a partial file with only
those columns — not a complete N-port `.sNp`. For a full S-matrix, run all N ports.

---

## Palace output debug console (panels/palace_console.py)

`PalaceConsole` is a `QDockWidget` that docks at the bottom of the FreeCAD window.
It shows Gmsh and Palace solver output in real time.

- `PalaceConsole.get_or_create()` — singleton factory; creates and docks the widget if
  it doesn't exist, or raises and focuses an existing one.
- `write(text)` — thread-safe; emits an internal Qt signal so text is always appended on
  the main thread regardless of the caller's thread.
- `set_status(text)` — updates the status label (`Meshing… / Running Palace… / Done`).
- `clear()` — clears the text area.
- `CmdRun.Activated()` calls `get_or_create()`, clears it, then passes `console.write`
  as `log_fn` to `generate_mesh()` and as `line_callback` to `_SimWorker`.
- All output is also written to `FreeCAD.Console.PrintMessage` (Report View), so nothing
  is lost if the panel is closed.

### Gmsh logger details
`gmsh.logger` in this version only has: `start`, `stop`, `get`, `write` — **no
`setCallback`**. `gmsh.logger.get()` returns the full accumulated log every time (it does
NOT consume messages); an offset is tracked in `_gmsh_log_offset` to emit only new lines.
`_flush_gmsh()` is called at four natural phase boundaries: after `occ.synchronize()`,
after `mesh.generate(3)`, after `mesh.setOrder(2)`, and after `gmsh.write()`.

---

## Known limitations / next steps

### Not yet implemented
- **Boundary condition objects** — no GUI for PEC/PMC/absorbing on *internal* faces (separate from the outer airbox per-face BCs).
- **Electrostatic terminals** — `TerminalPort` feature object needed for capacitance extraction.
- **Windows mpirun** — requires Microsoft MPI installed separately for multi-process runs.
- **Real-time Gmsh streaming** — Gmsh output is flushed in batches at phase boundaries,
  not line-by-line. During `mesh.generate(3)` the console is silent until it completes.

### Suggested next tasks (in priority order)
1. Add a `BoundaryCondition` feature object for internal conductor faces.
2. Add an `ElectrostaticTerminal` feature object for capacitance extraction.
3. Stream Gmsh output in real time (requires running Gmsh in a subprocess to capture
   stdout, or polling `gmsh.logger.get()` from a background thread — risky as Gmsh is
   not thread-safe).
4. Windows testing and packaging.

---

## Environment notes

- **FreeCAD version:** 1.0.0 (Python 3.13.9, conda-forge)
- **Gmsh:** installed via pip into FreeCAD's Python; `import gmsh` works inside `freecadcmd`
- **Palace binary:**
  `/opt/spack/opt/spack/linux-ubuntu22.04-icelake/gcc-11.4.0/palace-0.12.0-eqguemtopoeez24knhosweng35twldaa/bin/palace`
- **Palace version:** 0.12.0
- **MPI:** single-process (`num_procs=1`) — mpirun not needed for dev/test
- **Workbench symlinks:**
  - `~/.FreeCAD/Mod/PalaceWorkbench → /workspace`
  - `~/.local/share/FreeCAD/Mod/PalaceWorkbench → /workspace`

---

## Coaxial S-parameter Validation — RESOLVED (2026-05-15)

### Goal
Verify a 50 Ω coaxial cable produces S[1][1] ≈ −40 dB and S[2][1] ≈ 0 dB across
1–5 GHz, validating the LumpedPort `+R` radial direction implementation.

### Test file
`/workspace/Coax_test.FCStd` — FreeCAD document with:
- Geometry: R_INNER=1.52 mm, R_OUTER=3.50 mm, LENGTH=10 mm (Z₀=50 Ω exact)
- SimulationContainer, Airbox, LumpedPort×2 (ports 11, 12)
- Both ports: Direction=`+R`, R=50 Ω
- Port 1 Excitation=True; Port 2 Excitation=True (workbench runs two passes automatically)
- PalaceBinary set to the spack path above

Output lands in `/workspace/palace_output/`.

### Headless test
```bash
freecadcmd /workspace/test_headless.py
```
Runs the full pipeline (mesh → config → Palace) without the GUI. Results:
S21=−0.000 dB ✓, S11=−70.3 dB ✓

### Final validated results (GUI mesh, two-pass, 1–10 GHz)

```
f (GHz)   |S21| (dB)    |S12| (dB)    |S11| (dB)    |S22| (dB)
1.0       −0.000267     −0.000267     −83.0          −83.0
3.0       −0.000265     −0.000265     −74.4          −74.4
5.0       −0.000266     −0.000266     −70.3          −70.3
7.0       −0.000274     −0.000274     −67.8          −67.8
10.0      −0.000267     −0.000268     −65.7          −65.7
```

S21 ≈ S12 (reciprocity ✓), S11 ≈ S22 (symmetry ✓), S21 < −0.0003 dB (energy ✓).

### Root cause and fix — Port face area discretisation

**Root cause (confirmed by reading Palace source `lumpedelement.cpp:114`):**

Palace's `CoaxialElementData` computes the inner conductor radius as:
```cpp
ra = sqrt(bounding_ball.radius² - A_mesh / π)
```
where `A_mesh` is the numerically integrated area of the port face triangles.

With a coarse boundary circle, each arc is approximated by a chord: the triangulated
area differs from the exact annular area (π(R_OUTER² − R_INNER²) = 31.416 mm²).
The errors from inner and outer circles have **opposite signs**:

| Circle | Effect on A_mesh |
|--------|-----------------|
| Outer (chord < arc, triangle loses area) | A_mesh ← decrease |
| Inner (chord < arc, hole is smaller, annular area gains) | A_mesh ← increase |

The net area error per circle for N segments ≈ R² × (2π)³ / (12 × N²).
- Outer (R=3.5): ≈ 254 / N_outer² mm²  (removes area)
- Inner (R=1.5): ≈  46.5 / N_inner² mm²  (adds area)

With only N_OUTER=20 and no inner transfinite constraint (N_inner ≈ 24 from CL_MAX),
the outer deficit dominated: −0.638 + 0.082 = −0.556 mm² (−1.75% error).
This gave `ra ≈ 1.557 mm` and `Rs ≈ 388 Ω/sq` (4.9% high) → `S[2][1] ≈ +0.357 dB`.

Critically, reducing CL_MAX (finer volume mesh) made things **worse**, because finer
CL_MAX improved the inner circle approximation while the outer circle stayed at N=20.
This reduced the inner area contribution, increasing the net deficit.

**Fix in `meshing.py` (`_apply_port_transfinite`):**
Applies `setTransfiniteCurve` to both inner and outer boundary circles of each lumped
port face. N is computed from `circumference / (cl_max / 2)`, forced even (Palace's
bounding-ball check requires diametrically opposite nodes), minimum 8. The GUI mesh uses
order-2 curved elements (OCC geometry) so the area approximation is near-exact regardless
of N; the transfinite constraint is still required for the bounding-ball check.

### Full history of root causes fixed

| Session | Symptom | Root cause | Fix |
|---------|---------|------------|-----|
| Prior | S[1][1] = −1 | 1D PEC elements on lumped port boundary circles zeroed edge DOFs | `meshing.py`: 1D `PortBoundaryCurves` only for wave ports |
| Prior | Palace crash: bounding ball check | Order-2 mesh midside nodes at chord midpoints (r < R_OUTER by ~0.4%) | Transfinite outer circle with even N guarantees exact antipodal nodes |
| 2026-05-15 am | S[1][1] = −1, E/H = 10^11 | `MinFreq = 1e9` sent as-is → interpreted as 10^9 GHz = X-ray | `config.py` divides by 1e9; panel shows GHz |
| 2026-05-15 am | No port excited | `Excitation: true` never written | Fixed: write key only when True |
| 2026-05-15 am | Coaxial TEM not coupled | `"+R"` missing from `_DIR_MAP` | Added to both `_DIR_MAP` and Direction enum |
| 2026-05-15 am | S[2][1] = +0.357 dB | N_OUTER=20 → port face area 1.75% low → ra=1.557 mm → Rs=388 Ω/sq | `meshing.py`: `_apply_port_transfinite` on both boundary circles |
| 2026-05-15 pm | GUI config "Direction": "X" | FCStd had Direction="X" saved before fix; stale at time of run | FCStd re-saved with Direction="+R"; code was already correct |
| 2026-05-15 pm | S-param CSV stuck at 5 rows | Both ports Excitation=true → Palace multi-pass didn't write port-S.csv | Workbench now runs N separate single-excitation passes and merges |
| 2026-05-15 pm | No Gmsh output in console | `gmsh.logger.setCallback` doesn't exist; `get()` doesn't consume | Use offset-tracked `_flush_gmsh()` at phase boundaries |

---

## Tree View Group Nesting & Grayed-Eye Issue — RESOLVED (2026-05-16)

### What was intended

All Palace container objects (PalaceSimulation, PalaceAirbox, PalaceDielectric*, PalaceConductor*) should:
1. Appear as non-grayed, active-eyed items in the FreeCAD tree view
2. Show their child objects nested underneath them in the tree

### What was done

**AirBox refactored** from a `PropertyLink` pattern (`AirboxShape`) to `App::GroupExtensionPython`:
- `features/airbox.py` completely rewritten — AirBox now holds its solid in `Group[0]`
- Old `AirboxShape` property migrated automatically on document restore via `execute()`
- `palace/meshing.py`, `palace/config.py`, all command `IsActive` checks updated to use `hasattr(obj, "OuterBoundaryType")` instead of `hasattr(obj, "AirboxShape")`

**PalaceSimulation made into group container:**
- `create_simulation()` adds `App::GroupExtensionPython`
- `_migrate_to_group()` called in `onDocumentRestored()` pulls in all palace child objects from `doc.Objects`
- `features/__init__.py` `add_to_simulation(doc, obj)` helper added; called from all `create_*` factories

**Tree nesting (claimChildren):**
- All four group ViewProviders now implement `claimChildren()` returning `getattr(self.Object, "Group", [])`
- This gives tree nesting without the VP group extension

**Attempted fixes for grayed-out eyes:**

| Attempt | What was done | Result |
|---------|--------------|--------|
| 1 | Removed `Gui::ViewProviderGroupExtensionPython` from all VP factory setup calls | Still grayed |
| 2 | Patched `Microstrip_test_new.FCStd` to strip the serialized VP extension from `GuiDocument.xml` | Still grayed |
| 3 | Added `getDisplayModes(vobj)` → `["Group"]` and `getDefaultDisplayMode()` → `"Group"` to all four group VPs | Still grayed |
| 4 | Added `onDocumentRestored()` to all four proxy classes calling `vobj.removeExtension("Gui::ViewProviderGroupExtensionPython", None)` | Still grayed |
| 5 (Theory D) | Added `coin.SoSeparator()` node + `vobj.addDisplayMode(self.node, "Group")` in all four VP `attach()` methods | **Needs GUI verification** |

**Diagnostic findings:**
- `Microstrip_test_new.FCStd` `GuiDocument.xml` shows `Visibility=true` for all Palace objects — the grayed eye is NOT caused by `Visibility=False` stored in the file
- The `DisplayMode` property is absent from all Palace VP entries in `GuiDocument.xml` — FreeCAD derives it from the VP's `getDefaultDisplayMode()`
- The user reports the Display Mode field appears "unset" on Palace objects in the FreeCAD properties panel

### Current state of the files

All four group ViewProviders now have:
```python
def attach(self, vobj):
    self.ViewObject = vobj
    self.Object = vobj.Object
    from pivy import coin
    self.node = coin.SoSeparator()
    vobj.addDisplayMode(self.node, "Group")

def claimChildren(self): return getattr(self.Object, "Group", [])
def getDisplayModes(self, vobj): return ["Group"]
def getDefaultDisplayMode(self): return "Group"
```
No `Gui::ViewProviderGroupExtensionPython` is added anywhere in code. The `App::GroupExtensionPython` extension IS still added on the data (App) side of all four container objects.

### Resolution

Two fixes were required:

**Fix 1 — Theory D: register a scenegraph node in `attach()`** (resolves grayed eye):
```python
def attach(self, vobj):
    self.ViewObject = vobj
    self.Object = vobj.Object
    from pivy import coin
    self.node = coin.SoSeparator()
    vobj.addDisplayMode(self.node, "Group")
```
Without `addDisplayMode`, FreeCAD's display state machine never learned about the "Group"
mode and marked the object non-renderable (grayed eye).

**Fix 2 — `onChanged` for visibility propagation** (resolves child visibility not following parent):
```python
def onChanged(self, vobj, prop):
    if prop == "Visibility":
        for child in getattr(self.Object, "Group", []):
            if child.ViewObject is not None:
                child.ViewObject.Visibility = vobj.Visibility
```
Without this, toggling the eye icon on a container had no effect on children because there
is no `Gui::ViewProviderGroupExtensionPython` to do it automatically.

Both fixes are applied to all four container VPs in their respective feature files.
`getDisplayModes` → `["Group"]` and `getDefaultDisplayMode` → `"Group"` are also required
and must be kept (removing either broke the eye icon).

---

## Material Groups (DielectricGroup / ConductorGroup) — Added 2026-05-16

### What was added

Two new feature objects for assigning material properties and PEC boundaries to solid bodies inside the airbox.

**Files:**
- `features/material_group.py` — `DielectricGroup` and `ConductorGroup` feature objects + view providers
- `panels/material_group_panel.py` — Qt task panel for editing group properties
- `commands/cmd_material_group.py` — toolbar commands for adding groups

**Key design: `App::FeaturePython` + `App::GroupExtensionPython`**

`App::DocumentObjectGroup` (the native FreeCAD group) does NOT support the `Proxy` attribute — assigning `obj.Proxy = self` raises `AttributeError`. The workaround is:

```python
obj = doc.addObject("App::FeaturePython", name)
obj.addExtension("App::GroupExtensionPython")
DielectricGroup(obj)
if obj.ViewObject is not None:
    ViewProviderDielectricGroup(obj.ViewObject)
    # NOTE: Gui::ViewProviderGroupExtensionPython was removed from the factory —
    # it was causing grayed-out/crossed-eye appearance. claimChildren() is used instead.
```

This gives the object a Python proxy AND the group drag-and-drop behavior.

**Mesh attribute numbering** (added to table above):

| Entity | Gmsh physical attribute |
|---|---|
| Dielectric group i | configurable, default 2 for first |
| Conductor group | configurable, default 100 |

**Properties:**

`DielectricGroup`: `Permittivity`, `Permeability`, `LossTangent`, `MeshAttribute`
`ConductorGroup`: `ConductorType` (PEC / LossyConductor), `Conductivity`, `MeshAttribute`

**Meshing integration** (`palace/meshing.py`):

`_assign_material_physical_groups(gmsh, log_fn, airbox_solid, diel_bodies, cond_bodies, all_vol_tags_before_frag)`:
- Calls `occ.fragment([airbox, diel..., cond...], [])` to carve material regions
- Assigns Physical Volume 1 = airbox background, Physical Volume N = dielectrics
- Assigns Physical Surface M = conductor outer surfaces (no physical volume for conductors)
- **Critical bug fixed:** `out_map[0]` for the airbox includes ALL output volumes (OCC considers every child volume to "coincide" with its parent). Dielectric and conductor volumes must be explicitly subtracted when computing `bg_vol_tags`.

---

## Lumped Port from Two Edges — Added 2026-05-16

### What was added

Ports can now be created by selecting two curve edges rather than a pre-existing face. The workbench automatically constructs the port face geometry.

**Two cases:**
- **Annular (coaxial):** one edge's bounding box fully encloses the other → `Part.Face([outer_wire, inner_wire_reversed])` ring face; auto-suggests `+R` direction
- **Planar (microstrip):** two line edges on same plane → shorter edge width, endpoints projected onto longer edge's line → quadrilateral; auto-suggests `X`/`Y`/`Z`

**New file: `palace/port_geometry.py`**
Pure geometry helpers (no FreeCAD document objects):
- `classify_edge_pair(e1, e2)` → `('annular'|'planar', primary, secondary)`
- `build_annular_face(outer_edge, inner_edge)` → `Part.Shape`
- `build_planar_port_face(short_edge, long_edge)` → `Part.Shape`
- `build_port_shape(e1, e2)` → `(Part.Shape, 'annular'|'planar')`

**Modified: `features/lumped_port.py`**
- Changed from `App::FeaturePython` → `Part::FeaturePython` so the object owns a `Shape` property (auto-displayed in 3D view)
- Added properties: `PortEdges` (PropertyLinkSubList), `PortGeometryType` (PropertyString)
- `execute()` builds the port shape from `PortEdges` references
- `create_lumped_port(doc, index, faces=None, edge_refs=None)` accepts both modes

**Modified: `panels/lumped_port_panel.py`**
- `self.edge_refs`: flat list of `(obj, sub_name)` pairs
- "Use current edge selection (2 edges)" button → `_pick_edges()`
- `_update_geom_label()` uses `classify_edge_pair` for edges, face-wire inspection for faces
- `accept()` saves `PortEdges` when edge_refs present, else `PortFaces`

**Modified: `commands/cmd_lumped_port.py`**
- Detects edge sub-elements in selection first; falls back to face selection

### Port identification
Ports created from edges have `PortEdges` set and `PortGeometryType` = `'annular'` or `'planar'`.
Ports created from face selection have `PortFaces` set and `PortGeometryType` = `''`.

---

## Per-Face Boundary Conditions on AirBox — RESOLVED (2026-05-17)

A "Per-Face BCs" tab on the AirBox dialog allows per-face assignment of PEC, PMC, or Absorbing boundary conditions (overriding the global outer BC for specific faces).

**Architecture:**
- `features/airbox.py` — `PECFaces`, `PMCFaces`, `AbsorbingFaces` (`App::PropertyLinkSubList`)
- `panels/airbox_panel.py` — two-tab QTabWidget; `_FaceSelectionObserver` caches face selections; `_overrides: dict[subname → bc_type]` accumulated until `accept()`
- `palace/meshing.py` — `_assign_outer_boundary_groups()`: PEC→attr 2, PMC→attr 3, Absorbing→attr 4
- `palace/config.py` — `_build_boundaries()` uses per-attr BC scheme

**Three bugs that were fixed (all required together):**

1. **Compound subname stripping** — FreeCAD returns subnames like `"PalaceAirbox.Box.Face1"` when the solid is inside a container. Fixed by taking `sub.split(".")[-1]` in both `_FaceSelectionObserver.addSelection()` and `_assign_faces()`.

2. **Valid object name widening** — the filter `obj.Name == solid_name` rejected selections where the container object was returned instead of the inner solid. Fixed by accepting both `solid.Name` and `self.obj.Name` (the airbox container) in `valid_names`.

3. **Observer cache stale accumulation** — `clearSelection` was deferring `faces.clear()` via `QTimer.singleShot(0, ...)`. The timer fired before `_assign_faces()` ran (button click → focus change → clearSelection → timer → button handler), wiping the cache. Fixed with a `_pending_clear` flag: `addSelection` checks the flag and clears at the start of the next add (not on the timer). `consume()` always clears completely after every Assign click — critical to prevent removed faces from reappearing on the next assign.

**Final observer pattern:**
```python
def addSelection(self, doc, obj, sub, pnt=None):
    if self._pending_clear:
        self.faces.clear()
        self._pending_clear = False
    bare = sub.split(".")[-1]
    if bare.startswith("Face"):
        self.faces.add((obj, bare))

def clearSelection(self, doc):
    self._pending_clear = True

def consume(self):           # always called after Assign regardless of which path ran
    result = set(self.faces)
    self.faces.clear()
    self._pending_clear = False
    return result
```

---

## Adaptive Frequency Sweep — Added 2026-05-17

Driven solver runs can use Palace's adaptive frequency sampling instead of a fixed step.

**New properties on `SimulationContainer` (`features/simulation.py`):**
- `DrivenAdaptiveTol` (`App::PropertyFloat`, default 0.0) — tolerance; 0 = disabled
- `DrivenAdaptiveMaxSamples` (`App::PropertyInteger`, default 10)
- `DrivenAdaptiveMaxCandidates` (`App::PropertyInteger`, default 0 = Palace default)

**Simulation panel (`panels/simulation_panel.py`):**
- Driven page has a checkable `QGroupBox("Adaptive Frequency Sweep")` containing tolerance (QLineEdit), max samples (QSpinBox), and max candidates (QSpinBox)
- Checkbox unchecked → writes `DrivenAdaptiveTol = 0.0` (disabled)
- Checkbox checked → writes `max(tol, 1e-12)` to prevent accidental zero

**Config generation (`palace/config.py` `_build_solver()`):**
```python
adaptive_tol = getattr(sim, "DrivenAdaptiveTol", 0.0)
if adaptive_tol > 0.0:
    driven_block["AdaptiveTol"] = adaptive_tol
    driven_block["AdaptiveMaxSamples"] = getattr(sim, "DrivenAdaptiveMaxSamples", 10)
    cand = getattr(sim, "DrivenAdaptiveMaxCandidates", 0)
    if cand > 0:
        driven_block["AdaptiveMaxCandidates"] = cand
```

**Palace 0.12.0 schema note:** valid adaptive fields are `AdaptiveTol`, `AdaptiveMaxSamples`, `AdaptiveMaxCandidates`. There is NO `AdaptiveConverged` field — Palace crashes with "unsupported keyword" if it appears. The schema was verified from the installed JSON schema at the spack Palace path.

---

## S-Parameter Results Viewer — Added 2026-05-17

An in-FreeCAD S-parameter plot viewer implemented as a dockable `QDockWidget`.

**Files:**
- `panels/s_param_panel.py` — `SParamPanel(QDockWidget)` singleton
- `commands/cmd_results.py` — `CmdViewResults` toolbar command (`Palace_ViewResults`)
- `resources/icons/Results.svg` — line-chart icon

**matplotlib backend:** must use `matplotlib.use("QtAgg")` + `backend_qtagg` (Qt6/PySide6). The FEM workbench uses `Qt5Agg`/`backend_qt5agg` (PySide2) — these are incompatible with this workbench's PySide6 environment and must NOT be used.

**UI:** file label + "Load CSV…" button; per-S-param checkboxes (built dynamically from parsed headers); Magnitude (dB) / Phase (°) radio buttons; full `NavigationToolbar2QT` (zoom/pan/save-to-PNG); matplotlib canvas. If matplotlib is absent the canvas is replaced with a `QLabel` showing install instructions — the workbench still loads cleanly.

**CSV parsing:** `_MAG_RE = re.compile(r"\|S\[(\d+)\]\[(\d+)\]\|")` and `_ANG_RE` (same as `post_process.py`) parse Palace header format. Headers map to `(row, col)` keys; checkbox labels are `S{row}{col}`.

**Auto-open after simulation:** `_on_done()` in `cmd_run.py` calls `SParamPanel.get_or_create().load_csv(final_csv)` after every successful run (single or multi-pass).

**`CmdViewResults.Activated()`:** opens the viewer, auto-loads the default CSV path (`<doc_dir>/palace_output/output/port-S.csv`); falls back to `QFileDialog` if the file doesn't exist yet.

---

## Stop Simulation — Added 2026-05-17

See the "Stopping a running simulation" subsection under **Background simulation & multi-pass S-matrix** above for the full implementation rationale and process-group kill details.

**Key files:**
- `commands/cmd_run.py` — `CmdStopRun`, `_SimWorker.stop()`, `_SimWorker._proc` / `_cancelled`
- `palace/runner.py` — `start_new_session=True` in `Popen`, `proc_callback` parameter
- `resources/icons/Stop.svg` — red square icon

**Critical finding:** Palace (OpenMPI) spawns child processes even for single-rank runs. `proc.kill()` (SIGKILL to parent only) leaves orphaned children that keep the stdout pipe open, so the worker thread never unblocks — the simulation appears to finish normally rather than abort. The fix is `start_new_session=True` + `os.killpg(proc.pid, SIGKILL)` to kill the entire process group atomically.

---

## Microstrip Meshing — Two Bugs Remaining (2026-05-16)

### Test file
`Microstrip_test.FCStd` — geometry with dielectric substrate, PEC ground plane (conductor group), PEC strip (part of conductor group), and two planar lumped ports created from edge pairs.

### Architecture: conductor bodies → BRep surface import

**User requirement:** ConductorGroup objects continue to reference solid bodies in the GUI. During meshing, only the FACES of those solids are used — conductor volumes are never added to Gmsh.

**Implementation:**
- Conductor solid bodies are excluded from the STEP export (`export_objs` only contains airbox + dielectrics)
- Each face of each conductor solid is exported as a standalone `.brep` file and imported into Gmsh via `occ.importShapes()` + `synchronize()` → creates a 2D surface entity
- All conductor BRep surfaces + planar port surfaces are fragmented into the volumes in one combined `occ.fragment()` call
- Conductor surface tags (post-fragment) are assigned to physical surface group 100 (PEC)

This fixed the earlier `stable3d.cpp:112` MFEM crash (conductor volumes with no physical group caused MFEM to fail building element connectivity).

### Fragment pass structure

**Pass 1 — Dielectric fragmentation** (`_assign_material_physical_groups`):
- Fragments airbox + dielectrics only (no conductors)
- Assigns vol PG 1 (Airbox_Background) and vol PG 2..N (dielectrics)

**Pass 2 — Combined port + conductor fragmentation** (inline in `generate_mesh`):
- Imports planar port BRep surfaces, then conductor face BRep surfaces
- Snapshots `vol_pg_info_snap` (3D PGs) and `surf_pg_info` (2D PGs) BEFORE the fragment
- Fragments all volumes with all port + conductor surfaces in one call
- `_update_vol_physical_groups_after_fragment` rebuilds vol PGs from the pre-fragment snapshot + `out_map`
- Surface PGs (port 11/12, conductor 100, outer boundary 2) are assigned after the fragment

### Current status — RESOLVED (2026-05-16)

Both fixes are already applied in the current `palace/meshing.py`.

---

#### Bug 1: Missing volume (tet) elements in MSH2 file — FIXED

**Symptom:** `mesh.msh` contained only tri6 surface elements, zero tet10 volume elements.

**Root cause:** `gmsh.model.removePhysicalGroups([])` (empty list) removes ALL physical groups. When `surf_pg_info` was empty before the combined fragment, the guard-less call wiped the volume PGs rebuilt by `_update_vol_physical_groups_after_fragment`.

**Fix applied** (`palace/meshing.py` line ~644):
```python
if surf_pg_info:
    gmsh.model.removePhysicalGroups([(2, pg_tag) for pg_tag in surf_pg_info])
```

---

#### Bug 2: Triple-edge → MFEM `AddSegmentFaceElement` crash — FIXED

**Symptom:** `mfem::Mesh::AddSegmentFaceElement` assertion: interior edge found between 2D elements in PG 11 (port) and PG 100 (conductor).

**Root cause:** Conductor solid end faces (coplanar with port faces) were imported into Gmsh as separate BRep surfaces and assigned to PG 100, creating three boundary triangles sharing one edge with the port triangles.

**Fix applied** (`palace/meshing.py` lines ~230, ~555–579): `_import_conductor_solid_faces` accepts a `skip_normals` list; faces whose outward normal is parallel (`|dot| > 0.99`) to any port face normal are skipped. `generate_mesh` collects normals from all planar port faces and passes them as `skip_normals`.

Physically correct: the port excitation BC (PG 11) governs at conductor ends; no separate PEC is needed there.

### Verification criteria (for next run)

1. `mesh.msh` should contain both type 9 (tri6) AND type 11 (tet10) elements
2. Meshing log should show `Skipping conductor face N ... (end face parallel to a port)` for each end face
3. Palace should run past `AddSegmentFaceElement` and produce S-parameter output in `palace_output/`

---

## Wave Port Evanescent Mode Bug — FIXED (2026-05-17)

### Test file
`Waveport_test.FCStd` — AirBox (10×10×10 mm) with FR-4 substrate (10×10×0.5 mm),
ground plane (Body001, PEC, 10×10×0.1 mm), microstrip strip (Body002, PEC, 0.5×10×0.1 mm),
and one wave port on the Y=0 end face (`Plane.Face1`, 7×5 mm spanning Z=[-0.1, 4.9]).

### Symptom
Palace exits with:
```
Computed wave port mode is or is very close to being evanescent
```

### Root cause — single-surface wave port match misses fragmented sub-faces

After `_assign_material_physical_groups` carves the substrate out of the airbox, and after
the conductor BRep import + fragment pass, the Y=0 end face of the airbox is split into
multiple Gmsh sub-surfaces:

| Sub-surface | Region | Z range |
|---|---|---|
| Sub-A | Air below ground plane | −2 to −0.1 mm |
| Sub-B | Ground plane end face | −0.1 to 0 mm (conductor, attr 100) |
| Sub-C | Substrate end face | 0 to 0.5 mm |
| Sub-D | Air adjacent to strip | 0.5 to ~0.6 mm, split by strip X range |
| Sub-E | Strip end face | X=[4.5,5], Z=[0.5,0.6] (conductor, attr 100) |
| Sub-F | Air above substrate | 0.6 to 8 mm (large surface) |

The old code called `_closest_surface(centre_of_Plane.Face1, ...)`, which returned only the
single closest sub-surface — Sub-F (air above substrate, center at Z≈4 mm, closest to the
Plane center at Z=2.4 mm).

Palace's 2D eigenvalue problem on Sub-F alone sees an air-filled rectangular region
(7.5 mm tall × 10 mm wide) bounded by PEC walls. The TE10 cutoff of this region is
c / (2 × 10 mm) ≈ **15 GHz**, which is well above the 1–5 GHz simulation range → evanescent.

**Missing from the wave port:** Sub-C (substrate, with εr=4.5) and Sub-D (air at strip level)
and Sub-A (below-ground air). Without the dielectric sub-face and the full cross-section,
the microstrip quasi-TEM mode cannot be computed.

### Fix — `_coplanar_outer_surfaces` in `palace/meshing.py`

Two changes made:

**1. New helper `_coplanar_outer_surfaces`** — returns ALL single-volume-adjacent Gmsh
surfaces whose bbox centre lies on the same plane as the wave port face (within 0.01 mm).

**2. Wave port assignment** — `is_wave_port` branches to `_coplanar_outer_surfaces` instead
of `_closest_surface`. All coplanar outer-boundary sub-faces are collected into one physical
group. Conductor end faces (already in `used_surface_tags`) are automatically skipped.

**3. PortBoundaryCurves fix** — with multiple sub-faces in one wave port, curves shared by
two sub-faces are interior substrate/air interface edges; applying PEC there would incorrectly
constrain the field. Fixed by counting each curve's frequency across `wave_port_surface_tags`
and only including curves with count == 1 (true exterior edges).

### After fix

The wave port physical group (attr 11) covers all coplanar sub-faces at Y=0:
Sub-A (below-ground air), Sub-C (substrate, εr=4.5), Sub-D+F (air above substrate).
Conductor end faces (Sub-B ground plane, Sub-E strip) remain as attr 100 (PEC) and
act as internal PEC boundaries within Palace's 2D port eigenvalue problem.

Palace now sees the full microstrip cross-section and should compute a propagating
quasi-TEM mode at 1–5 GHz. The log will show:
```
Palace: Wave port 1 — N coplanar surface(s) collected (attr 11)
```

---

## Wave Port Multi-Pass S-Matrix — FIXED (2026-05-17)

### Symptom
With two wave ports both having `Excitation: true`, clicking **Generate & Run** started
only a single Palace driven simulation (and that simulation appeared to show "eigenmode"
output — actually the wave port 2D eigenvalue computation printed before the frequency
sweep). No full 2-port S-matrix was produced.

### Root cause — two bugs, both in the lumped-port-centric multi-pass logic

**Bug 1 — `commands/cmd_run.py` `_find_excited_lumped_ports`:**
The function tested `hasattr(o, "R") and hasattr(o, "C")` — wave ports have neither
attribute, so they were never counted. `len(excited_ports)` was always 0 for a
wave-port-only simulation, and `_build_passes` always took the single-pass branch.

**Bug 2 — `palace/config.py` `_build_boundaries` wave port loop:**
The wave port entries used `if p.Excitation:` unconditionally, ignoring the
`only_excitation` parameter entirely. Even if `_build_passes` had been fixed to
generate per-port configs, every config would have marked all wave ports as excited.

### Fix

**`cmd_run.py`:** Renamed `_find_excited_lumped_ports` → `_find_excited_ports`; now
accepts any port object that is either a lumped port (`hasattr R, C`) or a wave port
(`hasattr NumModes, not hasattr R`), as long as `Excitation` is True.

**`config.py`:** The wave port loop now mirrors the lumped port pattern:
```python
excited = p.Excitation if only_excitation is None else (p.PortIndex == only_excitation)
if excited:
    entry["Excitation"] = True
```

### Result
With N excited wave ports, `_build_passes` now generates N per-port configs
(`palace_config_port1.json`, `palace_config_port2.json`, …), runs Palace N times, and
merges the `port-S.csv` outputs into a full S-matrix — identical to the lumped port flow.

The "eigenmode" output the user saw is Palace's wave port 2D mode computation printed
before the frequency sweep; it is not the eigenmode solver and is expected even in a
correctly-running driven simulation.

---

## Session 2026-05-18 — Lossy Conductor Fix + Mesh/Run Separation + 3D Mesh View

### Summary of changes

#### 1. WavePortPEC fix for Lossy Conductor (palace/config.py)

**Symptom:** Switching a ConductorGroup from "PEC" to "Lossy Conductor" produced
near-zero S21 ("slightly lossy short-circuit") on `Waveport_test.FCStd` even though PEC
mode gave correct microstrip-through results.

**Root cause:** Palace's 2D wave port eigenvalue problem only applies PEC Dirichlet
boundary conditions from the `PEC` attribute list. `Conductivity` BC is treated as
natural (open) in the 2D eigenvalue. When conductor attr 100 was moved from `PEC` to
`Conductivity`, the ground plane and strip no longer appeared as conductors in the 2D
port mode computation — the computed mode was wrong (evanescent), causing a near-zero
S21 in the full 3D driven simulation.

**Fix:** Palace has a `WavePortPEC` JSON key (distinct from `PEC`) that specifies surface
attributes to treat as PEC **only during the 2D wave port eigenvalue computation**,
independently of the 3D driven BC. For Lossy Conductor groups, attr 100 now goes into
BOTH `WavePortPEC` (correct 2D mode) and `Conductivity` (3D surface impedance). These
two JSON keys are independent and do not conflict.

`_build_boundaries()` in `palace/config.py` now maintains a `wave_port_pec_attrs` list
alongside `pec_attrs` and `lossy_entries`. The JSON output for a mixed PEC+LossyConductor
model is:
```json
{
  "PEC": {"Attributes": [2]},
  "WavePortPEC": {"Attributes": [100]},
  "Conductivity": [{"Attributes": [100], "Conductivity": 5.96e7}]
}
```

#### 2. PalaceMesh feature object (features/mesh.py — new file)

New `App::FeaturePython` document object that holds mesh settings and the path to the
last-generated `.msh` file. Engineers can double-click it to inspect node/element counts,
file size, and refine element sizing before committing to a long simulation run.

**Properties:**
- `MeshFile` — `App::PropertyString` — absolute path to last `mesh.msh`
- `MeshCharacteristicLengthMax` — `App::PropertyFloat` — Gmsh global max element size (model units, 0 = auto)
- `MeshCharacteristicLengthMin` — `App::PropertyFloat` — Gmsh global min element size

**3D viewport display:** `ViewProviderPalaceMesh.attach()` builds a coin3D scene with
three display modes — **Wireframe** (default, lets engineers see through to internal
structure), **Shaded**, and **Flat Lines**. The mesh is parsed from the MSH v2.2 file
and only surface triangle elements (Gmsh types 2 and 9) are extracted; volume tetrahedra
are ignored. For second-order 6-node triangles (type 9, produced by `mesh.setOrder(2)`),
only the 3 corner nodes are used. A compact vertex array containing only nodes used by
surface triangles is built.

**Identification pattern** (used by `find_palace_mesh`):
```python
hasattr(obj, "MeshFile") and hasattr(obj, "MeshCharacteristicLengthMax") and not hasattr(obj, "SimulationType")
```

#### 3. MeshPanel task panel (panels/mesh_panel.py — new file)

Task panel opened by double-clicking the PalaceMesh object. Contains:
- **Element Sizing** group: Max/Min element size QLineEdit fields (blank = Gmsh auto)
- **Mesh Status** group: file path label, file size label, node/element count label
  (parsed from MSH v2.2 `$Nodes`/`$Elements` count lines — fast, doesn't read whole file)
- **Generate Mesh** button — calls `accept()` to save settings then `FreeCADGui.runCommand("Palace_Mesh")`

#### 4. Generate Mesh command (commands/cmd_mesh.py — new file)

`Palace_Mesh` toolbar command that runs meshing only (no Palace solver). Registered
alongside `Palace_Run` in `PalaceWorkbench.py`.

**Critical implementation note — Gmsh must run on the main thread:**
The original implementation used a `_MeshWorker(QThread)` background thread. This
produced `ValueError: signal only works in main thread of the main interpreter` because
`gmsh.initialize()` calls `signal.signal()` internally, which Python only permits from
the main interpreter thread. Fixed by running `generate_mesh()` synchronously in
`Activated()`, matching the pattern already used in `CmdRun.Activated()`.

`IsActive()` disables the button while `CmdRun._worker` is running (can't mesh during
a simulation).

#### 5. Run Simulation command (commands/cmd_run.py — CmdRunOnly)

New `Palace_RunOnly` command runs the Palace solver against an existing mesh file
without regenerating the mesh. `IsActive()` checks `_find_mesh_file(doc)` — true only
when a mesh file actually exists on disk. `Activated()` reads the mesh path, builds the
Palace config(s), and starts `_SimWorker` — identical to `CmdRun` but without the
`generate_mesh()` call.

**`_find_mesh_file(doc)` helper:** checks `PalaceMesh.MeshFile` first (canonical),
falls back to `SimulationContainer.MeshFile` (legacy backward compat).

**`CmdRun.Activated()` updated:** after `generate_mesh()`, writes `mesh_path` to both
`PalaceMesh.MeshFile` (via `create_palace_mesh(doc)`) and `sim.MeshFile` (backward compat).

#### 6. features/__init__.py — find_palace_mesh()

```python
def find_palace_mesh(doc):
    for obj in doc.Objects:
        if (hasattr(obj, "MeshFile") and
                hasattr(obj, "MeshCharacteristicLengthMax") and
                not hasattr(obj, "SimulationType")):
            return obj
    return None
```

#### 7. palace/meshing.py — mesh sizing from PalaceMesh

Characteristic length options now read from `PalaceMesh` first, falling back to
`SimulationContainer` for old documents. A diagnostic log line is emitted:
```
Palace: Element size limits — max=X.Xmm, min=auto (model units)
```

Gmsh option names updated to try 4.x canonical names first with fallback to 3.x aliases:
```python
_set_size_opt("Mesh.MeshSizeMax", "Mesh.CharacteristicLengthMax", cl_max)
_set_size_opt("Mesh.MeshSizeMin", "Mesh.CharacteristicLengthMin", cl_min)
```
(In Gmsh 4.9+ the `CharacteristicLength*` names were deprecated; the old aliases may
silently do nothing on newer installs.)

#### 8. Backward compatibility

All `SimulationContainer.MeshFile` / `MeshCharacteristicLength*` properties remain
untouched. Old documents open and simulate without a PalaceMesh object. `_find_mesh_file`
and `generate_mesh` fall back gracefully.

---

## OPEN BUG: PalaceMesh 3D display does not update after meshing in the same session (2026-05-18)

### Symptom

After clicking **Generate Mesh** (`Palace_Mesh`), the mesh file is generated and
`PalaceMesh.MeshFile` is updated — the Mesh Status labels in the panel show the correct
node/element count. But the 3D viewport does **not** display the mesh until the FCStd
file is saved and reopened.

On file reload, the mesh appears correctly in Wireframe display mode.

### What has been tried

Three mechanisms are in place, and none is reliably triggering the 3D update in-session:

**1. `ViewProviderPalaceMesh.updateData(obj, prop)`**
Standard FreeCAD ViewProvider callback, called (in theory) whenever a data property
changes. Our implementation calls `_reload_mesh(mesh_path)`. If `attach()` has
already been called and `_coords` exists, this should work. But it is apparently not
firing reliably — possibly due to a FreeCAD 1.0.0 quirk, or because `attach()` has
not yet completed when the property is first set (first-time mesh when PalaceMesh is
freshly created in the same session).

**2. `PalaceMesh.onChanged(obj, prop)` in the data proxy**
Data proxy `onChanged` fires synchronously and immediately when any property is set,
bypassing the deferred ViewProvider notification path. Calls
`obj.ViewObject.Proxy._reload_mesh(...)` directly. Added to work around any `updateData`
timing issues. Still not producing a visible update.

**3. `FreeCADGui.updateGui()` at the end of `_reload_mesh`**
coin3D marks scene graph nodes dirty when field values change, but FreeCAD may not
schedule a redraw until explicitly asked. `FreeCADGui.updateGui()` is called after
every `_reload_mesh` to force the viewport to repaint. Has not resolved the issue.

**4. mtime-based cache in `_reload_mesh`**
Uses `(path, mtime)` as a cache key to deduplicate calls when both `onChanged` and
`updateData` fire for the same file. Not a root cause fix, but prevents double-parsing
of large mesh files.

### Theories for the next session to investigate

**Theory A — `_coords` not initialised when `onChanged` fires (most likely)**

When `create_palace_mesh(doc)` creates a new PalaceMesh object in the same session:
```python
obj = doc.addObject("App::FeaturePython", "PalaceMesh")
PalaceMesh(obj)
ViewProviderPalaceMesh(obj.ViewObject)   # sets vobj.Proxy = self
add_to_simulation(doc, obj)
doc.recompute()
return obj
```
Setting `vobj.Proxy = self` does NOT immediately call `attach()`. FreeCAD defers
`attach()` until the 3D viewer processes the new scene node — which may happen during
the next Qt event-loop iteration, AFTER `CmdMesh.Activated()` has already set
`mesh_obj.MeshFile = mesh_path` and `onChanged` fired. When `onChanged` calls
`_reload_mesh`, `self._coords` doesn't exist yet (`attach()` hasn't run), and
`_reload_mesh` returns early.

Later, when `attach()` finally runs, it calls `_reload_mesh(mesh_file)` — but only if
`os.path.isfile(mesh_file)` is True at that point. Test whether commenting out the
`if not hasattr(self, "_coords"): return` guard and making `_coords` lazy-initialised
inside `_reload_mesh` itself fixes the problem.

**Theory B — `updateData` / `onChanged` firing correctly, but coin3D scene not connected to viewport**

When a freshly created `App::FeaturePython` object's VP is not yet attached to the
scene root, modifying coin3D nodes (even valid ones) produces no visible result because
the nodes are not in the scene graph yet. Verify by printing a log message inside
`_reload_mesh` after `coordIndex.setValues()` to confirm the data is being loaded
(check FreeCAD Report View). If the log appears but nothing is visible, the scene node
attachment is the issue.

**Theory C — Display mode not active on first attach**

If `attach()` is called but `setDisplayMode()` hasn't been called (FreeCAD hasn't
applied a display mode yet for this new object), the VP separators may not be connected
to the viewer's scene graph. Try calling `vobj.DisplayMode = "Wireframe"` explicitly
at the end of `create_palace_mesh()` or at the end of `attach()`.

**Theory D — `FreeCADGui.updateGui()` processes events including the deferred `attach()`**

If `attach()` is deferred to the Qt event loop, calling `FreeCADGui.updateGui()` at
the end of `CmdMesh.Activated()` (after `doc.recompute()`) should process the pending
attach AND then redraw. Try adding an explicit `FreeCADGui.updateGui()` call at the
very end of `CmdMesh.Activated()`, after `doc.recompute()`, as a final flush.

### Recommended next-session debugging steps

1. Add `FreeCAD.Console.PrintMessage(f"_reload_mesh called: path={path}, has_coords={hasattr(self, '_coords')}\n")` at the top of `_reload_mesh`. Run Generate Mesh and check the Report View. This immediately reveals whether `onChanged` is firing and whether `_coords` exists.

2. Add `FreeCAD.Console.PrintMessage("attach() called\n")` at the start of `attach()` to confirm when it runs relative to `Activated()`.

3. Try adding `vobj.DisplayMode = "Wireframe"` at the very end of `attach()` (after `_reload_mesh`). This forces FreeCAD to apply the display mode immediately.

4. Try calling `FreeCADGui.updateGui()` at the end of `CmdMesh.Activated()` itself (not just inside `_reload_mesh`) to flush the Qt event loop including any deferred `attach()` call.

5. If Theory A is confirmed, refactor `_reload_mesh` to lazy-initialise `_coords` and the coin3D nodes if they don't exist yet (instead of guarding with `return`). This makes `_reload_mesh` safe to call before or after `attach()`.

### File locations for the fix

- `features/mesh.py` — `ViewProviderPalaceMesh.attach()`, `_reload_mesh()`, `PalaceMesh.onChanged()`
- `commands/cmd_mesh.py` — `CmdMesh.Activated()` — add `FreeCADGui.updateGui()` after `doc.recompute()` (Theory D)

---

## Session 2026-05-19 — PalaceMesh Attribute Visibility + Face Collection Fixes

### Summary of changes

#### 1. PalaceMesh Visible Attributes checklist (`features/mesh.py`, `panels/mesh_panel.py`)

New UI control in the Mesh task panel that lets the user show/hide individual Gmsh
physical groups from the 3D viewport. Each attribute is represented as a checkable row
with a colour swatch; unchecking it removes those triangles from the coin3D scene and
calls `_update_colors()` immediately.

**New properties on PalaceMesh:**
- `HiddenSurfAttributes` — `App::PropertyIntegerList` — surface attrs hidden in viewport
- `HiddenVolAttributes` — `App::PropertyIntegerList` — volume attrs hidden in viewport

**New functions in `features/mesh.py`:**
- `_build_surf_attr_labels(doc)` → `{attr: label}` for surface physical groups only
- `_build_vol_attr_labels(doc)` → `{attr: label}` for volume physical groups only
- `_build_surf_color_map(doc)` → `{attr: (r,g,b)}` for surface attributes
- `_build_vol_color_map(doc)` → `{attr: (r,g,b)}` for volume attributes

These four functions are dimension-aware (surface and volume share the same integer
namespace in FreeCAD but are independent in Gmsh/Palace JSON — see bug below).

`execute()` now caches `_known_surf_attrs` and `_known_vol_attrs` on the VP proxy for
fast panel population without re-parsing the mesh file.

`_update_colors()` consults both maps and both hidden sets; uses
`setNum()` after `setValues()` to truncate stale array data when face count decreases.

**`panels/mesh_panel.py` — `_populate_attrs()`:**
Two-section `QListWidget` with italic separator items ("── Surface Boundaries ──",
"── Material Domains ──"). Each attribute is stored as `(dim, attr)` in `Qt.UserRole`
so the toggle handler knows which hidden list to update. Signals are blocked during
population to prevent spurious `_on_attr_visibility_changed` calls.

#### 2. Attr 2 dimension-namespace collision fix (`features/mesh.py`)

**Bug:** `DielectricGroup.MeshAttribute` defaults to 2; PEC outer boundary surface also
uses attr 2. A single flat colour/label dict caused the dielectric to overwrite the PEC
entry — the Visible Attributes panel displayed "Dielectric: PalaceDielectric1 (attr 2)"
for the PEC outer boundary.

**Root cause:** Gmsh physical groups are dimension-namespaced: surface attr 2 and volume
attr 2 are independent entities. The single dict conflated them.

**Fix:** Split into four dimension-aware functions (listed above). `_update_colors()`
uses `surf_map` for surface triangles and `vol_map` for volume faces. `HiddenAttributes`
split into `HiddenSurfAttributes` + `HiddenVolAttributes`.

#### 3. Absorbing face BC fix — `_assign_outer_boundary_groups` Step 1 (`palace/meshing.py`)

**Bug:** Per-face BC overrides (AbsorbingFaces / PECFaces / PMCFaces) only applied to
one Gmsh sub-surface per selected FreeCAD face. After `occ.fragment()`, one FreeCAD face
becomes several Gmsh sub-surfaces; the remaining siblings fell through to the default BC
(usually PEC), so Absorbing faces displayed as PEC.

**Root cause:** Step 1 called `_closest_surface` (single nearest match) instead of
collecting all coplanar sub-surfaces.

**Fix (first pass — 2026-05-19 morning):** Step 1 replaced `_closest_surface` with
`_coplanar_outer_surfaces` to collect all coplanar outer-boundary sub-surfaces.

**Fix (second pass — 2026-05-19 afternoon, same session):** Step 1 updated again to use
`_faces_within_freecad_face` (see §4 below), which is both correct for fragmented faces
AND bounded to the selected face's extent.

#### 4. Wave port face collection — `_faces_within_freecad_face` (`palace/meshing.py`)

**Bug:** `_coplanar_outer_surfaces` collected ALL outer-boundary Gmsh surfaces on an
entire plane, regardless of the user's selected PortFace size. Two wave ports on the
same plane would bleed together.

**New function `_faces_within_freecad_face(gmsh, fc_face, all_surfaces, used_surface_tags, tol=0.01)`:**
Returns single-volume-adjacent Gmsh surfaces whose bbox centre is:
1. Coplanar with `fc_face` (plane-equation check, within `tol` mm).
2. Within `fc_face.BoundBox` (within `tol` mm), confining the result to the selected face's 2D extent.

Both the wave port assignment (around line 898) and `_assign_outer_boundary_groups`
Step 1 now call `_faces_within_freecad_face` instead of `_coplanar_outer_surfaces`.
`_coplanar_outer_surfaces` is retained (used by nothing currently but kept for reference).

---

## Wave Port Over-Expansion — RESOLVED (2026-05-19)

### Symptom

After the `_faces_within_freecad_face` fix, the PEC face below (`-Z`) the wave port is
now correctly assigned. However the wave port physical group still expands beyond the
selected PortFace in the `+Z` direction and in both `+/-X` directions.

### What partial improvement was observed

The `-Z` face improvement is real: it was caused by the `_assign_outer_boundary_groups`
Step 1 fix (Absorbing/PEC overrides now correctly cover all fragmented sub-surfaces on
that face). The wave port expansion itself may be unchanged from before.

### Debug logging added (temporary — remove after diagnosis)

In `palace/meshing.py` around line 889, temporary logging prints:
- The PortFace subname and the `link_obj.Label` it came from
- The face normal and BoundBox
- The bbox centre of every surface that `_faces_within_freecad_face` matches

**Regenerate the mesh in Waveport_test.FCStd and paste the `WavePort N PortFace`,
`normal=`, `BBox`, and `→ surf` lines from the Palace console to diagnose.**

### Theories (in rough order of likelihood)

**Theory 1 — PortFace IS the full airbox end face (most likely)**

The user selected the entire end face of the airbox as the PortFace (e.g., all of
`Face4` of the `Part::Box`). Its `BoundBox` spans the complete end face (full X and Z
range). `_faces_within_freecad_face` would then match ALL coplanar sub-surfaces on that
plane — identical behaviour to `_coplanar_outer_surfaces`. No restriction occurs.

In this case the wave port physically SHOULD cover the full cross-section (including
air, substrate, conductor end faces) for Palace's 2D eigenvalue solve to see the correct
mode. The expansion is correct; the user's mental model of what the wave port should
cover may need adjustment. The visual anomaly could also be a display artefact: conductor
end faces at `used_surface_tags` are excluded from the wave port PG, but the remaining
air/substrate sub-surfaces cover a large area.

**Theory 2 — BoundBox larger than the visual face**

In FreeCAD, `fc_face.BoundBox` for a face of a `Part::Box` is tight. However, if the
solid has been Boolean-modified (fused, cut), the face's OCCT topology may report a
slightly inflated BoundBox that includes adjacent geometry. Check the debug output's
`BBox` line to confirm whether it matches the expected face dimensions.

**Theory 3 — `link_obj.Shape.getElement(subname)` returns wrong face**

FreeCAD has a topological naming problem: after any recompute, face numbers (`Face1`,
`Face2`, …) can be re-ordered. If the PortFace was assigned in a prior session and the
solid was recomputed since then, `subname` may now refer to a different face — possibly
one with a larger BoundBox. The debug output's `link_obj.Label` line confirms which
object is being queried; the `BBox` line then confirms whether the BoundBox matches the
expected PortFace.

**Theory 4 — Use `fc_face.isInside()` for a point-in-face test**

The BoundBox check passes for any point within the rectangular bounding box of the
face, which for a non-rectangular face (e.g., a face with a notch) would over-collect.
FreeCAD's `Part.Face.isInside(FreeCAD.Vector(cx, cy, cz), tol, True)` tests whether a
3D point projects inside the actual face boundary (OCCT's `BRep_Builder`). This is more
precise than BoundBox and handles non-rectangular faces correctly.

```python
# Candidate replacement for the BoundBox check in _faces_within_freecad_face:
import FreeCAD
pt = FreeCAD.Vector(cx, cy, cz)
if not fc_face.isInside(pt, tol, True):
    continue
```

The coplanarity check (step 1) can be kept or dropped since `isInside` implies it.

**Theory 5 — WavePortPanel stores `s.Object` as container, not solid**

When a face of the airbox solid (which lives inside `App::GroupExtensionPython`) is
selected, FreeCAD may report `s.Object` as the container (`PalaceAirbox`) rather than
the inner `Part::Box`. The container has no `Shape` property, so
`link_obj.Shape.getElement(subname)` would raise `AttributeError`, be caught silently,
and return no tags — the wave port gets zero surfaces.

However, if the wave port IS being collected (user sees it), this theory does not apply.
The debug output's `link_obj.Label` confirms which object is being queried.

### Resolution — BRep fragmentation of the wave port face

All three containment tests tried (BoundBox, `isInside`, `distToShape`) operated on Gmsh
sub-surface bbox CENTRES. After dielectric/conductor fragmentation the airbox end-face
sub-surfaces span the full airbox width — their centres all happen to fall inside the user's
narrow PortFace bounding box, so every containment test silently absorbed the entire end
face.

**Root cause:** Gmsh geometry was never cut at the wave port boundary edges. No containment
test on centres can fix this.

**Fix (`palace/meshing.py`):** Fragment the wave port face BRep into Gmsh geometry before
the combined fragment pass, exactly like planar lumped ports. Steps:

1. Before the combined fragment call, for each wave port's `PortFaces`:
   - Call `fc_face.exportBrep(brep_path)` on the `Part.Face` returned by
     `link_obj.Shape.getElement(subname)` — works directly on a naked face, no wrapping needed.
   - Import the BRep into Gmsh via `occ.importShapes(brep_path)` + `synchronize()`.
   - Record new surface tags in `wave_port_surf_list = [(port_obj, [tags]), ...]`.

2. Include all `wave_port_surf_list` surface tags as tools in the combined `occ.fragment()`.

3. After fragment, build `old_to_new_surf` from `out_map`. Assign wave port PGs by looking
   up each original wave port surface tag in `old_to_new_surf` — yielding only the
   sub-surfaces that actually fell within the PortFace boundaries.

4. Mark wave ports in `handled_port_indices` so the old coplanar-collection fallback loop
   skips them.

This is identical to the planar lumped port BRep pattern (`planar_port_surf_list`) already
in the code. See `palace/meshing.py` around lines 679 (`wave_port_surf_list` collection)
and 877 (wave port PG assignment after fragment).

**Note:** `_faces_within_freecad_face` and `distToShape` remain in the codebase and are
still called for the per-face BC fallback path (absorbing/PEC/PMC overrides on the airbox).
They are correct for that use case since absorbing faces are assigned to surfaces that are
already properly cut by the combined fragment.

---

## Session 2026-05-19 (continued) — Wave Port Z_c Extraction + S-Param Renormalization

### Background

Palace 0.12.0 does NOT expose wave port characteristic impedance in any output file.
The previous V/I CSV approach was broken (Palace only writes `port-V.csv`/`port-I.csv`
for Lumped Port excitations, never for wave ports). This session implements the correct
approach using field probes along a user-drawn integration edge.

### Approach — E·dl + Ampere's law

The user draws a single edge on the wave port face that runs from the inner conductor to
the outer conductor (e.g. a radial edge across a coaxial cross-section).

During config generation, Palace is given `Domains.Postprocessing.Probe` entries at 8
evenly-spaced points along that edge. After solving, Palace writes `probe-E.csv` (E-field
in V/m) and `probe-B.csv` (B-field in Wb/m²) in the pass output directory.

Post-processing computes:
- **V** = ∫E·dl (trapezoidal integration from probe 0 to probe 7 in metres)
- **I** = 2π r B_φ / μ₀ at each probe (Ampere's law, averaged over probes 1–7)
- **Z_c** = V / I (complex, averaged magnitude over all frequency points)

Where B_φ is the azimuthal B-field component: at probe position r = (x,y,z) relative to
the inner conductor end, φ_hat = normalize(port_normal × r_hat). The port face normal is
queried from FreeCAD geometry (`_port_face_normal`).

This formula is exact for TEM modes (coaxial) and accurate for quasi-TEM modes
(microstrip, stripline).

**Sanity check (5 radial probes, 50 Ω coaxial, R_in=1.52 mm, R_out=3.50 mm):**
- V ≈ 7.0 V (E_x ~5000 V/m at inner conductor, decreasing radially)
- I ≈ 0.136 A (from B_y × r product, approximately constant: r × B_y / μ₀ ≈ const)
- Z_c ≈ 51.5 Ω ✓ (theoretical 49.9 Ω; <3% error with 5 probes)

### Files changed

#### `features/wave_port.py`
Three new properties on `WavePort._init_properties`:
- `IntegrationEdge` — `App::PropertyLinkSub` — one edge from inner to outer conductor
- `CharacteristicZ` — `App::PropertyFloat` (default 50.0) — auto-updated after each run;
  read-only to the user (only the backend writes it)
- `RenormZ` — `App::PropertyFloat` (default 50.0) — user-settable target normalization Z

#### `panels/wave_port_panel.py`
New fields in the task panel (QFormLayout):
- **Integration edge** row: read-only label showing edge subname + "Use current edge
  selection" button (mirrors the face picker pattern)
- **Characteristic Z** row: read-only QLabel showing current Z_c value in Ω
- **Renorm target Z** row: QDoubleSpinBox (range 0.01–10000 Ω, step 1.0, suffix " Ω")

`accept()` writes `IntegrationEdge` and `RenormZ` to the document; does NOT write
`CharacteristicZ` (set by backend only).

#### `palace/config.py`
New `_probe_points_along_edge(port_obj, n_probes=8)` helper (before `generate_config`):
- Reads `port_obj.IntegrationEdge` → `(link_obj, [sub_name])`
- Samples 8 evenly-spaced points along the edge with `edge.valueAt(param)`
- Returns list of (x, y, z) tuples in model units (mm with L0=0.001)
- Returns [] if IntegrationEdge is not set

`generate_config()` now builds `Domains.Postprocessing.Probe` entries for each wave port
with IntegrationEdge set. Probe index = `1000 + port_idx * 10 + k` (k = 0..7) to avoid
conflicts with user-defined probes. The config block is:
```json
"Domains": {
  "Materials": [...],
  "Postprocessing": {
    "Probe": [
      {"Index": 1010, "X": 1.52, "Y": 0.0, "Z": 5.0},
      {"Index": 1011, "X": 2.01, "Y": 0.0, "Z": 5.0},
      ...
    ]
  }
}
```

#### `palace/post_process.py`
**Removed:** `_RE_VI_COL`, `_IM_VI_COL`, `_RE_Z_COL`, `_IM_Z_COL` regexes,
`_parse_vi_csv()`, `_parse_z_csv()`, `compute_port_impedances()`, old `renormalize_s_matrix()`.

**Added:**
- `parse_probe_csv(path)` — parses `probe-E.csv` or `probe-B.csv`. Returns
  `(freq_ghz, data)` where `data[probe_idx][component] = list[complex]`, components are
  `'Ex'`/`'Ey'`/`'Ez'` (E-file) or `'Bx'`/`'By'`/`'Bz'` (B-file).
  Header regex: `Re{E_x[N]}` → field='E', comp='x', probe=N.

- `compute_wave_port_impedance(e_data, b_data, probe_indices, probe_positions_mm, port_normal=(0,0,1))`
  — computes Z_c(f) per frequency. Trapezoidal V=∫E·dl for voltage; I = 2π·r·B_φ/μ₀
  averaged over probes 1–N for current. `port_normal` is used to define the azimuthal
  direction φ_hat = normalize(port_normal × r_hat) at each probe. Returns `list[complex]`.

- `renormalize_s_matrix(s_csv_path, port_z_old, port_z_new)` (new signature) — per-port
  bilinear S→Z→S renormalization. Uses power-wave convention:
  `Z_mat = D_old @ (I+S) @ inv(I-S) @ D_old`
  `S_new = D_new⁻¹ @ (Z_mat - T_new) @ inv(Z_mat + T_new) @ D_new`
  where `D_old = diag(sqrt(z_old))`, `D_new = diag(sqrt(z_new))`, `T_new = diag(z_new)`.
  Ports absent from the dicts default to 50 Ω.

#### `commands/cmd_run.py`
**Removed:** `compute_port_impedances` import + call block (lines 190–200 of old version).

**Added (before `_output_dir`):**
- `_port_face_normal(port_obj)` — queries the FreeCAD face geometry of the first PortFace
  to get the unit normal vector. Falls back to `(0,0,1)` if the face cannot be read.
- `_update_wave_port_impedances(doc, passes, out_dir)` — for each wave port with
  IntegrationEdge set: finds the correct pass directory, parses probe-E/B CSVs, calls
  `compute_wave_port_impedance`, stores the result in `port_obj.CharacteristicZ`, calls
  `doc.recompute()`.

`_on_done()` now calls `_update_wave_port_impedances(doc, passes, out_dir)` after the
S-matrix merge and before the Touchstone write.

#### `panels/s_param_panel.py`
**Removed:** `_z_re`, `_z_im`, `_z_csv_path` state vars; `_radio_z` "Port |Z| (Ω)" radio;
`_renorm_group` QGroupBox with Z_new QLineEdit + Apply button; `_load_z_csv()`; old
`_apply_renorm()`; Z-CSV auto-load in `load_csv()`; Z-mode branch in `_refresh_plot()`.

**Added:** "Renormalize to Port Targets" QPushButton (below the radio row).
- Enabled once a CSV is loaded; disabled until then.
- `_apply_renorm()` reads `CharacteristicZ` + `RenormZ` from each wave port in
  `FreeCAD.ActiveDocument`, reads `R` from each lumped port (Z_old = Z_new = R for lumped,
  i.e. identity), calls `renormalize_s_matrix(self._csv_path, port_z_old, port_z_new)`,
  stores result in `self._s_renorm_mag`/`self._s_renorm_phase`, sets `self._renorm_active`.

---

### Palace probe output format (verified 2026-05-19)

**`probe-E.csv` columns:**
```
f (GHz), Re{E_x[N]} (V/m), Im{E_x[N]} (V/m), Re{E_y[N]} (V/m), Im{E_y[N]} (V/m),
Re{E_z[N]} (V/m), Im{E_z[N]} (V/m)   [repeated for each probe N]
```
**`probe-B.csv` columns:**
```
f (GHz), Re{B_x[N]} (Wb/m²), Im{B_x[N]} (Wb/m²), Re{B_y[N]} (Wb/m²), Im{B_y[N]} (Wb/m²),
Re{B_z[N]} (Wb/m²), Im{B_z[N]} (Wb/m²)   [repeated for each probe N]
```
- Probe coordinates in the JSON are in mesh units (mm for L0=0.001).
- E field is in SI V/m; B field is in SI Wb/m².
- Multiple probe indices add additional 6-column blocks per index.
- `SurfaceFlux` is NOT a valid config key in Palace 0.12.0 JSON schema (`additionalProperties: false`
  enforced) — any attempt to add it causes Palace to abort. Do not use it.

---

### Output files added / changed

| File | Description |
|---|---|
| `palace_output/output_portN/probe-E.csv` | E-field (V/m) at probe points — written by Palace when `Domains.Postprocessing.Probe` entries are present |
| `palace_output/output_portN/probe-B.csv` | B-field (Wb/m²) at probe points — same condition |

The old `port-Z.csv`, `port-V.csv`, `port-I.csv` entries are no longer produced by the
workbench (those were Lumped Port only; the workbench no longer calls `compute_port_impedances`).

---

### Verification test (for next session)

1. Open `Coax_test.FCStd`.
2. Double-click `PalaceWavePort1` → in the dialog, select a radial edge on the port face
   (from inner circle to outer circle) → click "Use current edge selection" → OK.
3. Click **Generate & Run**.
4. After completion: double-click `PalaceWavePort1` — `CharacteristicZ` should show ≈ 50 Ω.
5. Open S-parameter viewer → click **Renormalize to Port Targets** with `RenormZ = 50 Ω`
   → plot should be identical to the unrenorlmalized result (identity transform).
6. Set `RenormZ = 75 Ω` → click **Renormalize to Port Targets**
   → S11 should increase (structure appears mismatched at 75 Ω reference).

---

### Known limitations

- **Coax test mesh** (`/workspace/palace_output/mesh.msh`) — the wave port test mesh
  fails with `SuperLUSolver: Found a singular matrix, U(272,272) is exactly zero!` during
  the 2D wave port eigenvalue solve. This is a pre-existing mesh issue (likely caused by
  the waveport mesh having been generated with settings incompatible with the wave port
  boundary conditions). Re-generating the mesh from a clean FCStd document should resolve
  it.
- **Z_c accuracy** — depends on probe count and placement. 8 probes give ~2–3% error on
  a 50 Ω coax. Probes must be placed between the inner and outer conductors (not inside
  PEC material where E=0).
- **Port face normal fallback** — `_port_face_normal` calls `face.normalAt(u_mid, v_mid)`.
  For non-planar faces this returns the normal at the parametric centre, which may differ
  from the face's "flat" normal. Planar port faces (the common case) are exact.

---

## Session 2026-05-20 — onDocumentRestored Fix + Integration Edge Mesh + Geometry-Aware Refinement

### 1. `onDocumentRestored` backfill fix (`features/wave_port.py`, `features/lumped_port.py`)

**Symptom:** Opening a document saved before the 2026-05-19 session caused
`AttributeError: 'FeaturePython' object has no attribute 'RenormZ'` when
the wave port panel's `accept()` tried to write the new Impedance properties.

**Root cause:** FreeCAD only calls `__init__` (and therefore `_init_properties`) when
an object is first created. On document restore it calls `__setstate__` only. Properties
added after an object was originally saved are absent from restored objects.

**Fix:** Added `onDocumentRestored(self, obj)` to both `WavePort` and `LumpedPort` proxies
that calls `_init_properties(obj)`. Since every property add is guarded by `if not hasattr`,
this safely backfills only missing properties without touching existing ones.

**Pattern to follow:** Every `App::FeaturePython` proxy that adds properties in
`_init_properties` must also implement `onDocumentRestored` calling `_init_properties`.
`simulation.py` and `airbox.py` already had this; `wave_port.py` and `lumped_port.py`
now do too.

---

### 2. Integration Edge Added to Gmsh Mesh (`palace/meshing.py`)

**Motivation:** The Z_c calculation places 8 probe points parametrically along the
`IntegrationEdge`. Palace interpolates field values at these arbitrary 3D coordinates.
If no mesh nodes lie near the probe path, interpolation error can be significant.

**Fix:** After the wave port BRep face import loop (around line 724), a new loop exports
each wave port's `IntegrationEdge` as a BRep and imports it into Gmsh as a 1D OCC curve.
These curves are appended as `(1, tag)` tools in the combined `occ.fragment()` call
(after all the 2D surface tools), which severs the wave port sub-surfaces along the
integration path and forces Gmsh to place mesh nodes exactly there.

**PortBoundaryCurves safety:** After fragment, the integration edge becomes a boundary
shared by the two halves of the wave port face. The count-based exclusion in
`PortBoundaryCurves` (count == 2 → excluded) correctly prevents PEC from being applied
to the interior integration line.

**`out_map` index arithmetic:** The surface `old_to_new_surf` loop iterates over
`all_tool_tags` (surface tools only). Integration edge curve tools are appended at
indices `n_vols + n_surf_tools + j` in `out_map_ports` and are handled in a separate
logging loop — the surface loop index arithmetic is unchanged.

**Key variable:** `wave_port_edge_list = [(port_obj, [gmsh_curve_tags]), ...]` — populated
immediately after `wave_port_surf_list`, used at the fragment call and at the
integration-edge transfinite constraint call.

---

### 3. Geometry-Aware Mesh Refinement (`features/mesh.py`, `panels/mesh_panel.py`, `palace/meshing.py`)

Three new properties on `PalaceMesh` (group "Refinement", all default 0.0):

| Property | Meaning |
|---|---|
| `MeshConductorSize` | Element size (mm) right at conductor + dielectric surfaces |
| `MeshPortSize` | Element size (mm) right at wave/lumped port faces and integration edges |
| `MeshRefineDistance` | Transition distance (mm); 0 = auto (30% of model extent) |

Both sizes are `SizeMin` in Gmsh's Threshold field. `SizeMax` = `cl_max` (or auto-estimated
from bounding box when `cl_max = 0`). Both fields default to 0 → **no behaviour change
for existing documents**.

**Gmsh field pipeline** (`_apply_refinement_fields` in `palace/meshing.py`):

```
for each active zone:
    Distance field  → measures distance from source entities
    Threshold field → maps distance to size: SizeMin at DistMin=0, SizeMax at DistMax

zones:
  1. Conductor + dielectric surfaces → SizeMin = MeshConductorSize
  2. Port faces (wave + lumped)       → SizeMin = MeshPortSize
  3. Integration edge curves          → SizeMin = MeshPortSize / 2,
                                        DistMax = MeshRefineDistance × 0.4

Min field over all Threshold IDs → setAsBackgroundMesh
```

Gmsh takes `min(background_field, cl_max)` at every point, so the global max is
always respected.

**Distance field API rename:** `SurfacesList` (Gmsh 4.9+) vs `FacesList` (older) — handled
with try/except fallback.

**Integration edge transfinite constraint:** Regardless of whether the refinement fields
are active, `setTransfiniteCurve` is applied to all integration edge curves with
`n = max(8, ceil(edge_length / seg_size))` nodes, where `seg_size = MeshPortSize / 2`
if set, else `cl_max / 6`. This guarantees adequate probe-path resolution independently
of the global mesh density.

**Dielectric surface collection:** Just before calling `_apply_refinement_fields`, all
surfaces bounding non-background (attr ≠ 1) volumes are collected into `_diel_surf_tags`,
excluding already-assigned port/BC surfaces (`used_surface_tags`). These are merged with
conductor surface tags in the conductor Distance field so thin substrates are automatically
refined.

**Call site:** After `_apply_port_transfinite` and `_set_size_opt`, before
`gmsh.model.mesh.generate(3)`.

**Mesh panel UI:** Three new `QLineEdit` fields ("Conductor size", "Port size",
"Refine distance") in the "Element Sizing" group. Blank = 0 = disabled.

---

## Session 2026-05-23 — Wave Port Z_c: Grid Probes, Z₀^{VI} Ampere Contour, TE/TM Mode Support

### Background

The original `compute_wave_port_impedance` used a coaxial Ampere formula
(B_φ ∝ 1/r, cylindrical symmetry) that gave ~70 Ω for a microstrip expected at ~45 Ω.
An intermediate fix used the Poynting power-voltage definition Z₀^{PV} = |V|²/(2P),
which gives ~37 Ω — Palace's internal reference but not the engineering convention.

This session implements the voltage-current definition Z₀^{VI} = |V|/|I| using an Ampere
contour over 2D grid probes, and extends the calculation to cover TE/TM hollow waveguide
modes.

### Three Z₀ definitions

For a quasi-TEM line all three are related by the identity Z₀^{VI}² = Z₀^{PV} × Z₀^{PI}:

| Definition | Formula | Microstrip value | Notes |
|---|---|---|---|
| Z₀^{PV} | \|V\|²/(2P) | ~37 Ω | Most stable; Palace's internal reference |
| Z₀^{VI} | \|V\|/\|I\| | ~47 Ω | Engineering convention; matches Hammerstad |
| Z₀^{PI} | 2P/\|I\|² | ~62 Ω | Least stable; inflated by stray Poynting flux |

Hammerstad closed-form reference for the test geometry (εr=4.5, h=0.5 mm, w=0.5 mm): ~45 Ω.

**Why PI is unreliable:** The full-face Poynting integral captures ~33% stray flux from
evanescent non-TEM fields near strip edges, so 2P/|V||I| ≈ 1.33 > 1 (impossible for a
single TEM mode). This inflates P and therefore Z₀^{PI}.

**Run-to-run variability:** Palace's adaptive PROM solver independently interpolates E
and B fields at output frequencies. V (from E-field integration) and I (from B-field Ampere
contour) scale differently between runs, causing Z₀^{VI} to vary ~40–47 Ω depending on
which frequencies the PROM sampled. Set `DrivenAdaptiveTol = 0` in the Simulation object
to disable the adaptive solver and obtain a fully stable, self-consistent result at the
exact requested frequencies.

---

### 1. 2D grid probes over port face (`palace/config.py`)

**New helper `_probe_grid_over_port_face(port_obj, m_cols=12, n_rows=8)`:**
Generates an m_cols × n_rows rectangular grid of probe points covering the wave port face.
Returns `(pts, face_area_mm2)` where `pts` is a flat list of `(x,y,z)` tuples in model
units (mm) in row-major order (col index varies fastest).

The face normal axis is inferred as the axis with smallest extent in the face BoundBox.
Grid points are placed at midpoints of equal-width cells, so they never land on face
boundaries. Returns `([], 0.0)` if `PortFaces` is not set.

**Grid probe indices in `generate_config`:**
```
index = 2000 + (port_idx - 1) * 200 + k   (k = 0 .. m_cols*n_rows - 1)
```
These are independent of the V-line probe range (`1000 + port_idx*10 + k`) and must not
conflict with user-defined probes. Up to 200 grid probes are reserved per port; a 12×8=96
grid uses indices 2000–2095 for port 1 and 2200–2295 for port 2.

The `Domains.Postprocessing.Probe` block in the config now contains both V-line probes
(quasi-TEM only — when `IntegrationEdge` is set) and grid probes (both paths).

---

### 2. `compute_wave_port_impedance` rewrite (`palace/post_process.py`)

**Signature change:**
```python
# Old
compute_wave_port_impedance(e_data, b_data, probe_indices, probe_positions_mm,
                             grid_probe_indices, face_area_mm2, port_normal)
# New
compute_wave_port_impedance(e_data, b_data, probe_indices, probe_positions_mm,
                             grid_probe_indices, grid_probe_positions_mm, port_normal)
```
`face_area_mm2: float` replaced by `grid_probe_positions_mm: list[(x,y,z)]`.

**Mode detection — automatic, no user input required:**

| Condition | Path | Formula |
|---|---|---|
| `len(probe_indices) >= 2` (IntegrationEdge set) | Quasi-TEM | Z₀^{VI} = \|V\|/\|I\| via Ampere contour |
| `len(probe_indices) == 0` (no IntegrationEdge) | TE/TM | Z_w = μ₀ Σ\|E_t\|² / Σ Re(E×B*·n̂) |
| `len(probe_indices) == 1` | Error | returns `[]` |

Both paths require `grid_probe_indices` to be non-empty (returns `[]` otherwise).

#### Quasi-TEM path: Z₀^{VI} = |V|/|I|

**Voltage** (unchanged from prior version): trapezoidal ∫E·dl along the V-line probe
chain from inner conductor to outer conductor.

**Current** (new — Ampere contour):
1. Infer geometry axes automatically from the V-line probe positions and port normal:
   - `vert_ax` = axis along which V is integrated (largest range in probe positions)
   - `norm_ax` = port propagation axis (`argmax(|n_hat|)`)
   - `horiz_ax` = remaining axis
   - `bcomp` = B component on the horiz axis (`Bx`, `By`, or `Bz`)
2. Find the two grid rows that straddle the inner conductor height `z_inner = pts[0][vert_ax]`:
   - `z_below` = highest grid row below `z_inner`
   - `z_above` = lowest grid row above `z_inner`
3. Apply the Ampere contour:
   ```
   I = (Δu / μ₀) × Σ_cols [B_h(z_below) - B_h(z_above)]
   ```
   where Δu is the horizontal spacing between adjacent grid columns.

This formula is equivalent to integrating the curl of B around a rectangular contour
that encloses the strip conductor; it is independent of geometry and works for any
conductor cross-section.

#### TE/TM path: Z_w = μ₀ Σ|E_t|² / Σ Re(E×B*·n̂)

For a single TE mode this ratio equals Z_TE = ωμ₀/β exactly; for a single TM mode it
equals Z_TM = βη²/(ωμ₀). The sum/sum cancels the field amplitude and probe spacing
factors, leaving only the impedance. No geometry knowledge is required.

**Implementation note:** `E_t_sq = |E|² − |E·n̂|²` (transverse magnitude squared);
`cross_n` = Poynting flux component in the normal direction (= Re(E×H*·n̂)/2 up to μ₀
factor, summed over all grid probes). Guard: `if cross_sum <= 0: append 0`.

---

### 3. `_update_wave_port_impedances` fixes (`commands/cmd_run.py`)

Three targeted changes to the call site:

**Fix A — removed early `if not pts: continue` exit:**
Hollow waveguide ports (no IntegrationEdge, `pts = []`) must still reach the TE/TM
computation path. The guard was preventing any Z_w extraction for these ports.

**Fix B — conditionalized V-line presence check:**
The `missing_vline` check (exits if V-line probe indices absent from e_data) now runs
only when `probe_indices` is non-empty. For TE/TM ports `probe_indices = []` so the
check is a no-op.

**Fix C — removed fallback pass_dir + changed `face_area_mm2` → `grid_pts` in call:**
```python
# Old (wrong): if pass_dir is None and passes: pass_dir = passes[0][1]
# New: pass_dir stays None when no portN-named directory found → port is skipped
```
Without the fallback, `pass_dir = None` for any port that was not excited in the current
multi-pass run; the `if not pass_dir: continue` guard cleanly skips unexcited ports.
This ensures `CharacteristicZ` is updated **only for the port that was actually excited**
in each pass, not overwritten with data from the wrong excitation.

The `compute_wave_port_impedance` call now passes `grid_pts` (list of positions) instead
of `face_area_mm2` (float), matching the new function signature.

---

### Verification — `Waveport_test.FCStd` microstrip (εr=4.5, tanδ=0.02, h=0.5 mm, σ=59.6 MS/m)

Latest run result (5 frequencies, 1–5 GHz):

| f (GHz) | \|V\| (V) | \|I\| (A) | P (W) | Z_PV (Ω) | Z_VI (Ω) | Z_PI (Ω) |
|---|---|---|---|---|---|---|
| 1.0 | 7.167 | 0.1459 | 0.695 | 37.0 | 49.1 | 65.3 |
| 2.0 | 6.938 | 0.1504 | 0.694 | 34.7 | 46.1 | 61.4 |
| 3.0 | 6.810 | 0.1529 | 0.694 | 33.4 | 44.5 | 59.3 |
| 4.0 | 6.929 | 0.1510 | 0.695 | 34.5 | 45.9 | 60.9 |
| 5.0 | 7.192 | 0.1462 | 0.697 | 37.1 | 49.2 | 65.2 |
| **Mean** | | | | **35.3** | **47.0** | **62.4** |

Z₀^{VI} = 47.0 Ω, within ~5% of Hammerstad (45 Ω). 2P/|V||I| ≈ 1.33 > 1 indicates
~33% stray Poynting flux; this makes Z₀^{PI} unreliable and inflated (62 Ω).

Z₀ only updates on the excited port; the unexcited port's `CharacteristicZ` is unchanged.

---

### Files changed in this session

| File | Change |
|---|---|
| `palace/config.py` | Added `_probe_grid_over_port_face()`; `generate_config()` emits grid probes at `2000+(port_idx-1)*200+k` |
| `palace/post_process.py` | `compute_wave_port_impedance` rewritten: `face_area_mm2` → `grid_probe_positions_mm`; quasi-TEM Ampere contour + TE/TM wave impedance |
| `commands/cmd_run.py` | `_update_wave_port_impedances`: removed early hollow-port exit; conditionalized V-line check; removed fallback pass_dir; passes `grid_pts` instead of `face_area_mm2` |

---

## Session 2026-05-24 — S-param Checkbox Matrix, CPU Controls, Per-Pass Frequency Check

### Summary of changes

#### 1. S-parameter checkbox matrix layout (`panels/s_param_panel.py`)

**Before:** S-parameter checkboxes were arranged in a single horizontal row (`QHBoxLayout`),
which became unwieldy for more than 4 ports.

**After:** Checkboxes are arranged in an N×N `QGridLayout`. Row and column header labels
(`1`, `2`, …) appear in gray 9pt text when N > 1. Cells for absent parameters (e.g.,
S12 when only port 1 was excited) are disabled and unchecked. All present parameters
default to **unchecked** — the user explicitly selects what to plot.

Key changes in `_rebuild_checkboxes()`:
- Layout changed from `QHBoxLayout` to `QGridLayout(spacing=4)`.
- For n_ports > 1, column-header labels are added at row 0, row-header labels at column 0.
- Each checkbox is placed at `(row, col)` matching its `S{row}{col}` label.
- `cb.setChecked(has_data and key in self._checked_keys)` — defaults to off; restores from saved selection.
- `cb.setEnabled(has_data)` — disabled (grayed) when the corresponding parameter is absent.
- State change connects to `_on_cb_toggled(key, state)` which updates `_checked_keys`, persists to doc, and refreshes the plot.

#### 2. Persistent S-parameter checkbox selection (`panels/s_param_panel.py`, `features/simulation.py`)

Selected checkboxes are persisted across FCStd file close/reopen via a new `SParamSelection`
property on the `SimulationContainer` document object.

**`features/simulation.py`** — new property:
```python
_add(obj, "App::PropertyString", "SParamSelection", "Palace",
     "JSON list of [row,col] S-parameter keys selected in the viewer", "")
```
Because `onDocumentRestored` calls `_init_properties`, old saved documents gain this property automatically on the next open.

**`panels/s_param_panel.py`** — persistence helpers:

- `_load_selection_from_doc()`: finds the `SimulationContainer` in `FreeCAD.ActiveDocument`,
  parses `sim.SParamSelection` as JSON (list of `[row, col]` pairs), populates `self._checked_keys`.
  Called at the start of `load_csv()` before `_rebuild_checkboxes()`.

- `_save_selection_to_doc()`: serializes `self._checked_keys` as a JSON list of `[row, col]`
  pairs and writes it to `sim.SParamSelection`. Called by `_on_cb_toggled` after every state change.
  If no simulation object exists, the save is silently skipped.

#### 3. Palace solver CPU/resource controls

**Motivation:** Palace can leverage multiple CPU cores via MPI (multi-process) and OpenMP
(multi-thread). Previously, the workbench always ran single-process with no OpenMP
configuration. Users with workstations or servers needed a way to pass these settings
from the FreeCAD UI.

**New properties on `SimulationContainer` (`features/simulation.py`):**
```python
_add(obj, "App::PropertyInteger", "NumProcesses", "Solver",
     "Number of MPI processes (Palace ranks). 1 = single-process, no mpirun.", 1)
_add(obj, "App::PropertyInteger", "NumThreads", "Solver",
     "OpenMP threads per MPI rank (OMP_NUM_THREADS). 0 = use system default.", 0)
```
Both use `if not hasattr` guard so old documents get them automatically on restore.

**Simulation panel (`panels/simulation_panel.py`):**
Two new spinboxes in the "General" group (after "Verbose:", before "Length scale L0"):
- `spin_num_procs` — range 1–256, tooltip explains MPI semantics
- `spin_num_threads` — range 0–256, tooltip explains OpenMP semantics; 0 = system default

Wired in `_populate()` via `getattr(o, "NumProcesses", 1)` / `getattr(o, "NumThreads", 0)`
(safe fallback for pre-existing documents), and in `accept()` writing `o.NumProcesses` / `o.NumThreads`.

**`palace/runner.py` — `run_palace()` signature change:**
```python
def run_palace(config_path, binary_path, num_procs=1, num_threads=0,
               line_callback=None, proc_callback=None):
```
- **WSL path:** `env_prefix = ["env", f"OMP_NUM_THREADS={num_threads}"] if num_threads > 0 else []`
  prepended to the inner WSL command (before `mpirun` or `binary_path`), so Palace inside
  WSL sees the variable.
- **Native path:** `env = {**os.environ, "OMP_NUM_THREADS": str(num_threads)} if (num_threads > 0 and not use_wsl) else None`
  passed to `Popen`.
- `mpirun -np N` wrapper applied when `num_procs > 1`, on both WSL and native paths.

**`commands/cmd_run.py` — `_SimWorker` signature change:**
`__init__` now accepts `num_procs=1, num_threads=0`; stores them as `self._num_procs` and
`self._num_threads`; passes both to `run_palace()`.

Both `CmdRun.Activated()` and `CmdRunOnly.Activated()` now read:
```python
num_procs   = int(getattr(sim, "NumProcesses", 1)) if sim else 1
num_threads = int(getattr(sim, "NumThreads",   0)) if sim else 0
_SimWorker(..., num_procs=num_procs, num_threads=num_threads, ...)
```

#### 4. Per-pass frequency check and auto-retry (`commands/cmd_run.py`)

**Background:** Palace's adaptive frequency sweep (PROM rational interpolation) sometimes
produces a slightly different number of output frequency rows between passes (e.g., 26
rows in pass 1 but 25 rows in pass 2). When `merge_s_matrix` attempts to combine the
per-pass CSVs it raises:
```
ValueError: Frequency point count mismatch: expected 26, got 25 in .../output_port2/port-S.csv
```
This discards the entire simulation result. The fix pre-empts the merge failure by
checking each pass immediately after it completes and retrying once before giving up.

**New module-level helper `_read_freq_count(csv_path)`** (added `import csv` at top):
Counts data rows (non-empty lines after the header) in a `port-S.csv` file.
Returns `None` if the file is missing or unreadable.

**New module-level helper `_expected_freq_count(config_path)`**:
Reads the Palace JSON config and computes the expected output row count from
`Solver.Driven.MinFreq`, `MaxFreq`, and `FreqStep`:
```
count = round((MaxFreq - MinFreq) / FreqStep) + 1
```
Returns `None` (skip count check) for:
- Non-Driven configs (Eigenmode, Electrostatic) — `"Driven"` key absent.
- Configs using a `"Samples"` array (`"Samples" in driven`) — the `Samples` field is an
  array of objects (each being a `Linear`, `Log`, or `Point` sub-sweep definition) whose
  total unique-frequency count cannot be reliably reproduced here.
- Any parse error or missing/invalid field values.

`json` is already imported in `cmd_run.py`, so no extra import beyond `csv` is needed.

**Rewritten `_SimWorker.run()` — per-pass retry loop:**

Each pass is now wrapped in a `for attempt in range(2)` inner loop:

```python
for i, (config_path, pass_dir) in enumerate(self._passes):
    ...
    expected = _expected_freq_count(config_path)   # None for non-Driven

    for attempt in range(2):
        rc = run_palace(config_path, self._binary,
                        num_procs=self._num_procs,
                        num_threads=self._num_threads,
                        line_callback=self._line_callback,
                        proc_callback=_store_proc)
        self._proc = None

        if self._cancelled: ... return
        if rc != 0: ... return

        if expected is None:
            break   # non-Driven or Samples sweep — skip row count check

        out_csv = os.path.join(pass_dir, "port-S.csv")
        n_rows = _read_freq_count(out_csv)

        if n_rows is None:
            msg = f"pass {i+1}: port-S.csv not found or unreadable"
            if attempt == 0:
                self.log.emit(f"Palace: Warning: {msg} — retrying…\n")
                self.status.emit(f"Retrying pass {i+1}…")
                continue
            self.done.emit(False, f"Frequency check failed: {msg} (after retry)")
            return

        if n_rows != expected:
            msg = f"pass {i+1}: got {n_rows} frequency points, expected {expected}"
            if attempt == 0:
                self.log.emit(
                    f"Palace: Warning: frequency count mismatch — {msg}. Retrying…\n"
                )
                self.status.emit(f"Retrying pass {i+1}…")
                continue
            self.done.emit(False, f"Frequency count mismatch: {msg} (after retry)")
            return

        break   # row count matched — proceed to next pass
```

The inner loop always either `break`s (success) or `return`s (failure), so the outer
pass loop is never re-entered after a failure.

**Design choices:**
- Expected count comes from the Palace JSON config, not from pass 1's actual output —
  avoids silently accepting a systematically wrong count if pass 1 also got it wrong.
- `Samples`-based sweeps (log sweep, explicit point lists) return `None` from
  `_expected_freq_count`; the count check is silently skipped and any eventual mismatch
  falls back to `merge_s_matrix`'s existing `ValueError` handling.
- Only one retry is attempted. A second consecutive mismatch indicates a systematic
  problem (e.g., Palace adaptive solver consistently undersampling at the edge of the
  frequency range) that should be surfaced as an error rather than silently retried again.

---

### Files changed in this session

| File | Change |
|---|---|
| `panels/s_param_panel.py` | Checkbox layout: `QHBoxLayout` → `QGridLayout`; N×N grid with header labels; default off; `_checked_keys` state; `_on_cb_toggled`, `_load_selection_from_doc`, `_save_selection_to_doc` |
| `features/simulation.py` | New `SParamSelection` (PropertyString), `NumProcesses` (PropertyInteger), `NumThreads` (PropertyInteger) properties; all guarded with `if not hasattr` |
| `panels/simulation_panel.py` | New `spin_num_procs` and `spin_num_threads` spinboxes in _build(); wired in _populate() and accept() |
| `palace/runner.py` | Added `num_threads` parameter; WSL `env_prefix` and native `env` dict for OMP_NUM_THREADS; `num_procs`/`num_threads` wired to mpirun and env |
| `commands/cmd_run.py` | `import csv`; `_read_freq_count()`; `_expected_freq_count()`; `_SimWorker.__init__` accepts `num_procs`/`num_threads`; `_SimWorker.run()` rewritten with per-pass 2-attempt retry loop; both `Activated()` call sites read `NumProcesses`/`NumThreads` from sim |

---

## Session 2026-05-24 (continued) — MPI Multi-Process Fix

### Root cause — nested mpirun

The `palace` executable at the spack path is a **bash wrapper script**, not the actual
binary. It internally calls `mpirun -n $NUM_PROCS palace-x86_64.bin config.json`. The
original `runner.py` implementation wrapped this with a second outer `mpirun -np N`,
creating nested mpirun invocations which OpenMPI explicitly rejects:

```
mpirun does not support recursive calls
[segfault in libhwloc → hwloc_shmem_topology_write → mca_rtc_hwloc]
```

### Fix — `palace/runner.py`

Removed the outer `mpirun`/`env_prefix`/`env` dict. The palace wrapper already supports
`--np N` and `--nt T` flags for MPI process count and OpenMP threads respectively —
pass those as arguments to the wrapper directly:

```python
palace_args = []
if num_procs > 1:
    palace_args += ["--np", str(num_procs)]
if num_threads > 0:
    palace_args += ["--nt", str(num_threads)]

# Native: cmd = [binary_path] + palace_args + [config_path]
# WSL:    inner = [binary_path] + palace_args + [wsl_config]
```

The palace wrapper echoes the full command it runs, e.g.:
```
>> /usr/bin/mpirun -n 2 .../palace-x86_64.bin config.json
```
This echo in the Palace console confirms MPI is active and shows the actual process count.

---

### HYPRE matrix mismatch with 3+ MPI processes

Even after fixing the nested mpirun issue, running with `NumProcesses = 3` on the
microstrip test model produced a different crash during system matrix assembly:

```
Verification failed: (internal::hypre_ParCSRMatrixSum(A, beta, B.A) == 0) is false:
 --> error in hypre_ParCSRMatrixSum
 ... in function: mfem::HypreParMatrix::Add
```

**Root cause:** METIS partitions the mesh into N subdomains by element count, ignoring
geometry. For this model (thin conductor traces, ~450:1 aspect ratio in one direction),
the 3-way METIS cut places the boundary between partitions across a thin feature. The
resulting surface matrix (Robin absorbing BC or wave port mode matrix) ends up with a
different parallel column distribution than the main 3D stiffness matrix. HYPRE's `Add`
enforces matching column distributions and aborts.

**Key finding from testing:**
- `NumProcesses = 1`: works (single process, no partitioning)
- `NumProcesses = 2`: works (2-way METIS cut lands in a compatible region)
- `NumProcesses = 3`: fails with `hypre_ParCSRMatrixSum` error
- Switching conductors from Lossy → PEC does **not** fix the crash — the absorbing
  outer boundary BC (also Robin-type) remains and is sufficient to trigger it
- This is a METIS + MFEM behavior specific to this geometry; different geometries
  may tolerate more processes

**This is not fixable in the workbench** — it is Palace/MFEM/METIS behavior.

---

### Recommended parallelism settings

| Scenario | NumProcesses | NumThreads | Notes |
|---|---|---|---|
| Safe default (any model) | 1 | 0 | Single process, system default threads |
| Tested maximum (microstrip) | 2 | 4 | 2 ranks × 4 threads = 8 cores total |
| General formula | 2 | N_cores / 2 | `N_cores` = total CPU cores on the machine |

For the dev machine: `nproc` = **8 cores** → `NumProcesses = 2`, `NumThreads = 4`.

`NumThreads` is per MPI rank. Total core utilization = `NumProcesses × NumThreads`.
Set `NumThreads = 0` to let the palace wrapper use its own default (usually 1).

If a model crashes with `NumProcesses > 1`, try reducing to 2 before falling back to 1.
The crash is geometry-dependent; larger/simpler meshes often tolerate more ranks.

---

### Files changed in this session

| File | Change |
|---|---|
| `palace/runner.py` | Removed outer mpirun/env_prefix/env dict; now passes `--np N` and `--nt T` as args to the palace wrapper script |

---

## Session 2026-05-24 (continued) — Frequencies Tab, Samples Sweep, Dockerfile Rebuild

### Summary of changes

#### 1. Generate & Run mesh update fix (`commands/cmd_run.py`)

**Symptom:** After meshing via **Generate & Run**, the PalaceMesh GUI object (node/element
counts, 3D display) was not updated until the FCStd file was saved and reopened, or the
MeshPanel was opened and OK'd.

**Root cause:** `CmdRun.Activated()` called `create_palace_mesh(doc)` and set
`mesh_obj.MeshFile = mesh_path` but never called `mesh_obj.Proxy.execute(mesh_obj)` or
`doc.recompute()`. The ViewProvider's `updateData` callback therefore never fired.

**Fix:** Added two lines after setting `MeshFile`:
```python
mesh_obj.Proxy.execute(mesh_obj)
doc.recompute()
```
This mirrors the pattern already used in `CmdMesh._on_done()` (the standalone
Generate Mesh command).

---

#### 2. Frequencies tab for SimulationContainer

##### New properties (`features/simulation.py`)

Two properties added to `SimulationContainer._init_properties` (both guarded with
`if not hasattr`, so old saved documents gain them automatically on next open):

| Property | Type | Default | Description |
|---|---|---|---|
| `DrivenSweepMode` | `App::PropertyString` | `"Linear"` | `"Linear"` or `"Samples"` |
| `DrivenSamplesJSON` | `App::PropertyString` | `"[]"` | JSON array of sample objects |

Internal JSON schema per entry in `DrivenSamplesJSON`:
```json
{"type": "linear", "min_freq_ghz": 1.0, "max_freq_ghz": 5.0, "freq_step_ghz": 0.1}
{"type": "log",    "min_freq_ghz": 1.0, "max_freq_ghz": 10.0, "num_samples": 50}
{"type": "point",  "freq_ghz": 2.45}
```

##### Panel restructure (`panels/simulation_panel.py`)

The flat `QWidget` root is replaced by a `QTabWidget` with three tabs:

**Tab 0 — "General":** all existing General + Mesh groups (unchanged content).

**Tab 1 — "Solver":** the existing `QStackedWidget` (Driven / Eigenmode / Electrostatic
stack). The Driven page now only contains the "Adaptive Frequency Sweep" checkable group
box — the "Frequency Sweep" group was moved to the Frequencies tab.

**Tab 2 — "Frequencies":** disabled (greyed) when `SimulationType != "Driven"`.
Enabled/disabled in `_on_type_changed()` whenever the solver type combo changes.

Content of the Frequencies tab:
- **Radio buttons:** "Linear sweep" / "Samples" (exclusive, in a "Sweep Mode" group box)
- **`self.freq_stack` (QStackedWidget):**
  - Page 0 (Linear): existing MinFreq / MaxFreq / FreqStep fields
  - Page 1 (Samples): `QTableWidget` with 4 columns (Type | Min/Freq (GHz) | Max (GHz) |
    Step/Count), plus [Add Linear] [Add Log] [Add Point] [Remove Selected] buttons and an
    explanatory note label.

**Table interaction:**
- Type column is read-only; values are set by the Add buttons.
- Point rows: Max and Step/Count cells are also non-editable (greyed).
- `_load_samples_table(json_str)`: called by `_populate()`; rebuilds the table from
  `DrivenSamplesJSON`.
- `_dump_samples_table()`: called by `accept()`; reads all rows, validates, returns JSON
  string; invalid rows are skipped with a `PrintWarning`.

**PySide6 import fix:** `QPalette` lives in `QtGui`, not `QtWidgets`, in PySide6. Added
`QtGui` to the PySide2/PySide6 import block; replaced both `QtWidgets.QPalette`
references with `QtGui.QPalette`.

##### Config generation (`palace/config.py` — `_build_solver`)

Branches on `DrivenSweepMode`:

**`"Linear"` mode (unchanged behavior):**
```json
"Driven": {"MinFreq": 1.0, "MaxFreq": 5.0, "FreqStep": 0.1}
```

**`"Samples"` mode — correct Palace schema:**
```json
"Driven": {
  "Samples": [
    {"Type": "Linear", "MinFreq": 1.0, "MaxFreq": 5.0, "FreqStep": 0.1},
    {"Type": "Log",    "MinFreq": 5.0, "MaxFreq": 10.0, "NumSamples": 20},
    {"Type": "Point",  "Freq": 2.45}
  ]
}
```

Key Palace schema requirements (confirmed from Palace docs and user testing):
- Every entry in `Samples` must have a `"Type"` key (`"Linear"`, `"Log"`, or `"Point"`).
- `"Point"` entries use only `"Freq"` — NOT `"MinFreq"` / `"MaxFreq"` / `"NumSamples"`.
- `"Log"` entries do NOT need `"LogSampling": true` (type name implies it).
- Adaptive sweep fields (`AdaptiveTol`, `AdaptiveMaxSamples`, etc.) are written into the
  same `Driven` block regardless of sweep mode — the adaptive PROM operates over the
  collected frequency points in both cases.

Raises `RuntimeError` if Samples mode is selected but `DrivenSamplesJSON` parses to an
empty list.

---

#### 3. Frequency count check updates (`commands/cmd_run.py` — `_expected_freq_count`)

Two changes to the per-pass frequency count checker:

**Samples mode support:** When `"Samples"` is present in the driven block, the function
now computes the expected count by summing per-entry contributions keyed on `"Type"`:
- `"Point"` → 1
- `"Log"` → `NumSamples`
- `"Linear"` → `round((MaxFreq - MinFreq) / FreqStep) + 1`

Returns `None` (skip check) for any unrecognised `Type` or missing required field.

**Adaptive sweep clarification:** The earlier implementation returned `None` for any
config with `AdaptiveTol > 0`, reasoning that adaptive output count was "non-deterministic".
This was wrong: Palace's adaptive PROM chooses *where to solve* for the reduced-order
model, but always writes output at every specified frequency point (the defined sweep grid).
The `AdaptiveTol` early-return guard was removed; the count check now runs for all Driven
configs regardless of adaptive settings.

Updated docstring explains the correct behavior.

---

#### 4. Dockerfile — Palace from GitHub source (`.devcontainer/Dockerfile`)

**Removed:** The entire Spack toolchain (Spack was only present to install Palace):
- `ENV SPACK_ROOT=/opt/spack`
- `git clone spack` layer
- `spack mirror add` + `buildcache keys` layer
- `spack install palace` layer (1–4 hour build)
- `spack load palace` + symlink layer
- `printf '... spack load palace ...' >> /etc/bash.bashrc`

**Added:** `zlib1g-dev` to the apt-get package list (needed by Palace superbuild deps).

**Added:** Single `RUN` block that clones Palace from GitHub and builds it using its own
CMake superbuild:
```dockerfile
RUN git clone --depth=1 https://github.com/awslabs/palace.git /tmp/palace-src \
    && cmake -S /tmp/palace-src -B /tmp/palace-src/build \
             -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local \
    && cmake --build /tmp/palace-src/build -j$(nproc) \
    && cmake --install /tmp/palace-src/build \
    && rm -rf /tmp/palace-src
```

The Palace CMake superbuild automatically downloads and compiles all of its own
dependencies (MFEM, HYPRE, libCEED, SuperLU_DIST, SUNDIALS, nlohmann/json, fmt, …).
Only system-level MPI (`libopenmpi-dev`), BLAS/LAPACK, and zlib are consumed from the OS.

**Result:** Palace binary is at `/usr/local/bin/palace` (same path as the old Spack
symlink). Update the `PalaceBinary` field in the Simulation settings after rebuilding
the container.

**Build time:** First `docker build` takes 30–60 minutes (superbuild compiles ~10
dependencies). Subsequent builds are cached at the `git clone` layer. Using `--depth=1`
always fetches the latest commit on the default branch (`main`).

**Note on `--np`/`--nt` wrapper flags:** The old Spack-installed Palace was a bash
wrapper that accepted `--np` and `--nt` arguments to control MPI rank count and OpenMP
threads. The GitHub-built Palace installs the actual binary directly. The `runner.py`
MPI/thread flags were already changed to use `mpirun -np N` externally (see previous
session), so no runner.py changes are needed for the new binary.

---

### Files changed in this session

| File | Change |
|---|---|
| `commands/cmd_run.py` | `CmdRun.Activated()`: call `mesh_obj.Proxy.execute()` + `doc.recompute()` after setting `MeshFile`; `_expected_freq_count`: add Samples type-keyed counting, remove incorrect adaptive early-return |
| `features/simulation.py` | New `DrivenSweepMode` and `DrivenSamplesJSON` properties |
| `panels/simulation_panel.py` | Full restructure to QTabWidget (General / Solver / Frequencies tabs); Frequencies tab with radio + freq_stack; samples table with add/remove helpers; `QtGui` import for `QPalette` fix |
| `palace/config.py` | `_build_solver`: branch on `DrivenSweepMode`; Samples entries use correct Palace schema (`"Type"` key; Point uses `"Freq"` only) |
| `.devcontainer/Dockerfile` | Removed Spack; added `zlib1g-dev`; Palace built from `--depth=1` GitHub clone via CMake superbuild into `/usr/local` |

---

## Session 2026-05-29 — Port Group / SubShapeBinder + ImpedanceBoundary

### Summary of changes

#### 1. Port Group / SubShapeBinder tree organization

Wave Ports and Lumped Ports now each own a `Group` (via `App::GroupExtensionPython`) that
is automatically populated with child objects every time the panel is accepted, so the
model tree shows the selected geometry nested under the port.

**`features/port_group.py` (new file):**

Contains `sync_port_group(port_obj)` — called from both port panels' `accept()` methods.
It clears all children from the port's Group and repopulates based on the current face/edge
references:

- **In-group source** (face/edge comes from an object already in a Palace Group — Airbox,
  ConductorGroup, or DielectricGroup): create a `PartDesign::SubShapeBinder` referencing
  only the specific sub-element (face or edge), since the source object cannot be moved.
  Note: the correct type string is `"PartDesign::SubShapeBinder"` — `"Part::SubShapeBinder"`
  is not registered in this FreeCAD build.
- **Stray source** (object not in any Palace Group): add the whole object directly to the
  port's Group, matching the pattern of material groups. Deduplicated when multiple
  faces/edges share the same source object.

Helper `_in_any_palace_group(doc, obj)` checks membership in Airbox.Group,
ConductorGroup.Group, and DielectricGroup.Group.

**`features/wave_port.py`:**
- `create_wave_port()` now calls `obj.addExtension("App::GroupExtensionPython")`.
- `WavePort.onDocumentRestored()` migrates old documents (adds extension if absent) and
  calls `obj.ViewObject.removeExtension("Gui::ViewProviderGroupExtensionPython", None)` —
  the same defensive cleanup used by all other group-container objects (see Session 2026-05-16).
- `WavePort._init_properties()` adds `GroupColor` (burgundy `#800020 = (0.502, 0, 0.125)`)
  and `GroupTransparency` (30) data properties. `WavePort.onChanged()` propagates them via
  `_propagate_from_data()` from `material_group.py`.
- `ViewProviderWavePort` gets the full Group display treatment required to avoid grayed-out
  eye icons: `attach()` registers a `coin.SoSeparator` + `addDisplayMode("Group")`;
  `getDisplayModes()` / `getDefaultDisplayMode()` return `"Group"`; `onChanged()` propagates
  Visibility to children; `updateData()` re-propagates color when children change;
  `claimChildren()` returns `obj.Group`.

**`features/lumped_port.py`:**
- Same GroupExtension setup as WavePort (factory + migration + `claimChildren()`).
- No Group display mode added (LumpedPort is `Part::FeaturePython` with its own shape display).
- Defensive `removeExtension("Gui::ViewProviderGroupExtensionPython")` added to restore.

**Panels** (`panels/wave_port_panel.py`, `panels/lumped_port_panel.py`):
Both `accept()` methods call `sync_port_group(o)` before `o.Document.recompute()`.

---

#### 2. ImpedanceBoundary feature object

A new Palace feature object for applying a distributed surface impedance (Rs, Ls, Cs in
Ω/sq, H/sq, F/sq) to a meshed boundary face. This corresponds to Palace's `"Impedance"`
JSON boundary key and is distinct from `LumpedPort` (discrete element between terminals).

**Typical use case:** SMD resistor or inductor placed across a port gap — the total R/L/C
of the component is converted to surface (per-square) values using the port face's aspect
ratio.

**`features/impedance_boundary.py` (new file):**

`Part::FeaturePython` + `App::GroupExtensionPython` (same pattern as LumpedPort). Identifier
signature: `hasattr(obj, "Rs") and hasattr(obj, "MeshAttribute")` — `Rs` is the unique
discriminator vs. LumpedPort.

Properties:
- Port group: `PortIndex`, `PortFaces`, `PortEdges`, `PortGeometryType`, `Direction`
- Impedance group: `SurfaceMode` (bool), `R/L/C` (total values, Ω/H/F), `Rs/Ls/Cs` (Ω/sq, H/sq, F/sq), `AspectRatio` (float)
- Mesh group: `MeshAttribute`
- No `Excitation` (impedance boundaries are passive)

`execute()` builds the Shape from edge pairs (same as LumpedPort), then:
1. Computes `AspectRatio` from the face BoundBox and Direction axis. For directions `+R`/`-R`
   (radial), AR = 0 and the conversion is skipped.
2. If `SurfaceMode == False` (Total mode): `Rs = R/AR`, `Ls = L/AR`, `Cs = C×AR`; marks
   Rs/Ls/Cs/AspectRatio as `ReadOnly` via `setPropertyStatus`.
3. If `SurfaceMode == True` (Surface mode): `R = Rs×AR`, `L = Ls×AR`, `C = Cs/AR`; marks
   R/L/C as `ReadOnly`.

**Surface impedance math:**
```
AR = length / width    (length = BBox extent in Direction axis; width = other in-plane axis)

Total → Surface:   Rs = R / AR   Ls = L / AR   Cs = C × AR
Surface → Total:   R = Rs × AR   L = Ls × AR   C = Cs / AR
```

**`panels/impedance_boundary_panel.py` (new file):**
Same face/edge selection UI as LumpedPort. Key difference: a `QCheckBox` labelled
"Set surface impedance directly" maps to `SurfaceMode`. When unchecked (Total mode),
R/L/C inputs are active and Rs/Ls/Cs are shown as read-only labels. When checked
(Surface mode), Rs/Ls/Cs inputs are active and R/L/C are shown read-only.

**`commands/cmd_impedance_boundary.py` (new file):** `Palace_ImpedanceBoundary` command.

**Updated infrastructure files:**

| File | Change |
|---|---|
| `features/__init__.py` | Added `find_impedance_boundaries()`; `find_lumped_ports()` now excludes `Rs` (adds `and not hasattr(obj, "Rs")`); `next_port_index()` includes impedance boundaries |
| `features/simulation.py` | `_is_palace_child()` recognises `Rs+MeshAttribute`; LumpedPort arm also excludes `Rs` |
| `palace/config.py` | Added `_find_impedance_boundaries()`; local `_find_lumped_ports()` also excludes `Rs`; `_build_boundaries()` outputs `"Impedance"` JSON block with Rs/Ls/Cs |
| `palace/meshing.py` | ImpedanceBoundary detected before LumpedPort (uses `Rs+MeshAttribute` check); included in planar edge-pair BRep import, port face normals collection, annular COM match, and `all_ports` physical group assignment loop |
| `PalaceWorkbench.py` | Imports, registers, and adds `Palace_ImpedanceBoundary` to toolbar/menu |
| `resources/icons/ImpedanceBoundary.svg` | Placeholder copy of LumpedPort.svg — needs a proper icon design |

**Status:** Fully implemented, not yet tested against a real simulation geometry.

---

### Files changed in this session

| File | Change |
|---|---|
| `features/port_group.py` | **New** — `sync_port_group()`, `_in_any_palace_group()` |
| `features/wave_port.py` | GroupExtension + full Group VP (coin separator, display modes, visibility, color propagation, `GroupColor`/`GroupTransparency` props) |
| `features/lumped_port.py` | GroupExtension + `claimChildren()` + defensive `removeExtension` in restore |
| `panels/wave_port_panel.py` | `accept()` calls `sync_port_group()` |
| `panels/lumped_port_panel.py` | `accept()` calls `sync_port_group()` |
| `features/impedance_boundary.py` | **New** — full feature class, VP, factory |
| `panels/impedance_boundary_panel.py` | **New** — panel with mode toggle |
| `commands/cmd_impedance_boundary.py` | **New** — command |
| `features/__init__.py` | `find_impedance_boundaries()`; fixed `find_lumped_ports()`; updated `next_port_index()` |
| `features/simulation.py` | `_is_palace_child()` updated |
| `palace/config.py` | `_find_impedance_boundaries()`; `"Impedance"` JSON block; fixed local finder |
| `palace/meshing.py` | ImpedanceBoundary detection + processing |
| `PalaceWorkbench.py` | `Palace_ImpedanceBoundary` registered and in toolbar |
| `resources/icons/ImpedanceBoundary.svg` | Placeholder icon |

---

## Session 2026-05-30 — ImpedanceBoundary Fixes + Parallel Palace Runs + Tabbed Console

### Summary of changes

---

#### 1. SubShapeBinder ancestry fix (`features/port_group.py`)

**Bug:** When edges were selected from a conductor whose geometry was organized as
`ConductorGroup → Part → Body → Pad`, `sync_port_group()` added the Pad object directly
to the port's Group instead of creating a `PartDesign::SubShapeBinder`. The Pad is
not a direct member of `ConductorGroup.Group`, so the old direct-membership check
`obj in conductor_group.Group` returned False.

**Fix:** `_in_any_palace_group(doc, obj)` now builds a `parent_map` by scanning
`candidate.Group` for every object in the document, then BFS-walks upward from `obj`
through the parent map. If any ancestor is a direct member of a Palace container Group,
it returns True → a SubShapeBinder is created. The `parent_map` uses only `.Group`
(container relationships), not `.InList`, to avoid false positives from binder/link
references.

---

#### 2. ImpedanceBoundary color (`features/impedance_boundary.py`)

Added purple group color matching the WavePort pattern:

- `_BOUNDARY_COLOR = (0.502, 0.0, 0.502)` (purple), `_BOUNDARY_TRANSP = 30`
- `GroupColor` and `GroupTransparency` properties on `ImpedanceBoundary._init_properties()`
- `ImpedanceBoundary.onChanged()` calls `_propagate_from_data()` when either changes
- `ViewProviderImpedanceBoundary` updated to full Group VP treatment: `attach()` registers
  `coin.SoSeparator` + `addDisplayMode("Group")`; `getDisplayModes()` / `getDefaultDisplayMode()`
  return `"Group"`; `onChanged()` propagates Visibility to children; `updateData()` calls
  `_propagate_from_data()` when Group changes; `claimChildren()` returns `obj.Group`.

---

#### 3. Parallel Palace runs + tabbed console

Major redesign of the runner and output console to support simultaneous multi-pass
Palace simulations.

##### `panels/palace_console.py` — tabbed widget

Replaced the single `QPlainTextEdit` with a `QTabWidget`:
- **Tab 0 — "Main"**: meshing output + orchestration status lines (always present)
- **Tabs 1..N — "Port N" / "Palace"**: one `QPlainTextEdit` per Palace subprocess (added/removed dynamically before each run)

Signal changed from `Signal(str)` to `Signal(int, str)` — `int = -1` routes to Main tab, `int ≥ 0` routes to the port tab for that pass index.

New public API (main thread only):
```python
add_port_tab(pass_index: int, label: str)   # create tab
clear_port_tabs()                            # remove all non-Main tabs
write_port(pass_index: int, text: str)       # thread-safe append to port tab
```
`write(text)` unchanged (routes to Main tab). `clear()` now also calls `clear_port_tabs()`.
`FreeCAD.Console.PrintMessage` removed from `_append` — Report View no longer receives Palace subprocess output line-by-line.

##### `palace/runner.py`

Removed the per-line `FreeCAD.Console.PrintMessage(f"Palace: {line}")` from the stdout
loop. All subprocess output now flows exclusively through `line_callback`. One-time
start/finish messages are preserved.

##### `features/simulation.py`

New property:
```python
_add(obj, "App::PropertyBool", "ParallelPasses", "Solver",
     "Run multi-port excitation passes simultaneously (True) or one at a time (False)", True)
```

##### `panels/simulation_panel.py`

Added `self.chk_parallel = QCheckBox("Run passes in parallel")` at the top of the Driven
page in the Solver tab. Wired in `_populate()` (`getattr(o, "ParallelPasses", True)`) and
`accept()` (`o.ParallelPasses = self.chk_parallel.isChecked()`).

##### `commands/cmd_run.py` — complete redesign

Replaced the monolithic `_SimWorker(QThread)` sequential loop with:

**`_PassWorker(QThread)`** — runs a single Palace pass:
- Signals: `log(str)` (each stdout line), `pass_done(int pass_index, bool ok, str msg)`
- Preserves the 2-attempt frequency-count retry logic from the old `_SimWorker`
- `stop()` uses `os.killpg` on the process group (same as before)

**`_SimCoordinator(QObject)`** — lives on the main thread, manages N `_PassWorker`s:
- `parallel=True`: calls `worker.start()` for all workers simultaneously
- `parallel=False`: chains workers — starts the next only after the previous emits `pass_done`
- Writes `[Port N] Starting…` / `[Port N] Completed (Xs)` / `[Port N] FAILED` to the Main tab
- Emits `all_done(bool, str)` when all pending workers finish; honours cancellation
- `is_running()` → True if any worker `isRunning()`; `stop()` kills all running workers

**`_launch_passes(...)` helper** — shared by `CmdRun` and `CmdRunOnly`:
- Builds tab labels (`"Palace"` for single-pass, `"Port N"` for multi-pass)
- Calls `console.clear_port_tabs()` + `console.add_port_tab(i, label)` per pass
- Creates `_PassWorker` per pass, connects `log → console.write_port(i, ...)`
- Creates `_SimCoordinator`, connects `all_done → _on_done(...)`, calls `start_all()`
- Assigns `CmdRun._coordinator`

**`_build_passes`** now returns 3-tuples `(config_path, pass_output_dir, port_index_or_None)`. All callers updated.

**`_on_done`** — unchanged logic (merge, Z_c, Touchstone, viewer); resets `CmdRun._coordinator = None`.

**`CmdRun._worker` → `CmdRun._coordinator`** (class-level). `CmdStopRun` and `CmdRunOnly.IsActive()` updated accordingly.

**Orchestration status lines in Main tab:**
```
[Palace] Starting 3 passes (parallel)…
[Port 1] Starting…
[Port 2] Starting…
[Port 3] Starting…
[Port 2] Completed (8.5s)
[Port 1] Completed (9.1s)
[Port 3] FAILED: …
[Post] Merging S-matrix…
[Post] Writing Touchstone…
[Done] Output: palace_output/output/port-S.s3p
```

---

#### 4. ImpedanceBoundary skip_normals bug (`palace/meshing.py`)

**Bug:** `port_face_normals` was collected by iterating `lumped_ports + impedance_boundaries`.
These normals were then passed as `skip_normals` to `_import_conductor_solid_faces()`, which
skips conductor faces whose outward normal has `|dot| > 0.99` with any normal in the list.
With an X-facing impedance boundary, all conductor faces normal to Y and Z were incorrectly
skipped, making the conductor physical group incomplete.

**Root cause:** The `skip_normals` mechanism exists solely to prevent the MFEM
`AddSegmentFaceElement` triple-edge assertion between a conductor end-face (attr 100),
a port face (attr 11), and a `PortBoundaryCurve` (attr 2). Impedance boundaries don't
create `PortBoundaryCurves` and don't use attr 11, so their normals must never enter
`skip_normals`.

**Fix (one line):**
```python
# Before (buggy):
for port in lumped_ports + impedance_boundaries:
# After:
for port in lumped_ports:
```

---

#### 5. `cmd_mesh.py` stale `_worker` reference

`CmdMesh.IsActive()` checked `CmdRun._worker` which no longer exists after the rename to
`CmdRun._coordinator`. This caused the Mesh button to appear permanently disabled.

**Fix:** Updated line in `commands/cmd_mesh.py`:
```python
# Before:
if CmdRun._worker is not None and CmdRun._worker.isRunning():
# After:
if CmdRun._coordinator is not None and CmdRun._coordinator.is_running():
```

---

#### 6. ImpedanceBoundary per-object mesh size control

**Problem:** Impedance boundary surfaces were included in `lumped_port_surface_tags` and
therefore received the aggressive `port_size` mesh refinement intended for excitation ports.
Impedance boundaries are passive BCs and don't need port-level refinement.

**`features/impedance_boundary.py`** — two new properties:
```python
_add(obj, "App::PropertyFloat", "MeshSize", "Mesh",
     "Element size at this impedance boundary surface (0 = global conductor size)", 0.0)
_add(obj, "App::PropertyFloat", "MeshRefineDistance", "Mesh",
     "Distance over which mesh transitions from MeshSize to background (0 = auto)", 0.0)
```
Edited via the **Data** properties panel in FreeCAD.

**`palace/meshing.py`** — three targeted edits:

1. **`_apply_refinement_fields` signature**: added `imp_data=()` parameter. New loop after
   the dielectric group loop applies per-object sizing:
   ```python
   for grp, tags in imp_data:
       size     = (getattr(grp, "MeshSize",          0.0) or 0.0) or global_cond_size
       grp_dist = (getattr(grp, "MeshRefineDistance", 0.0) or 0.0) or dist_max
       _add_distance_threshold(tags, [], size, grp_dist)
   ```
   Falls back to `global_cond_size` (MeshConductorSize). If both are 0, no field is
   created and Gmsh uses the background `cl_max`.

2. **Planar-port assignment loop**: accumulates `imp_data = [(imp_obj, [surf_tags])]`
   for each planar impedance boundary by checking `hasattr(port, "Rs")`.

3. **Call site**: computes `_imp_surface_tags` from `imp_data` and subtracts them from
   `port_surf_tags` before the call, so impedance boundaries no longer receive `port_size`.

**Scope note:** Only planar edge-pair impedance boundaries populate `imp_data`. Annular
edge-pair and face-selection mode impedance boundaries remain in `lumped_port_surface_tags`
and still receive `port_size` — a known limitation for a future session.

---

### Files changed in this session

| File | Change |
|---|---|
| `features/port_group.py` | `_in_any_palace_group`: direct membership → BFS through Group-parent graph |
| `features/impedance_boundary.py` | Purple `GroupColor`/`GroupTransparency`; VP coin separator + Group display modes; `MeshSize`/`MeshRefineDistance` properties |
| `panels/palace_console.py` | Full redesign: `QTabWidget` with Main + per-pass port tabs; `write_port()`, `add_port_tab()`, `clear_port_tabs()`; no Report View spam |
| `palace/runner.py` | Removed per-line `FreeCAD.Console.PrintMessage` from stdout loop |
| `features/simulation.py` | New `ParallelPasses` bool property |
| `panels/simulation_panel.py` | "Run passes in parallel" checkbox in Solver/Driven tab |
| `commands/cmd_run.py` | `_PassWorker`, `_SimCoordinator`, `_launch_passes`; `_build_passes` returns 3-tuples; `CmdRun._coordinator`; updated `CmdStopRun`/`CmdRunOnly`/`_on_done` |
| `commands/cmd_mesh.py` | `IsActive()`: `CmdRun._worker` → `CmdRun._coordinator.is_running()` |
| `palace/meshing.py` | `port_face_normals` loop excludes impedance boundaries; `imp_data` tracking; `_apply_refinement_fields` handles `imp_data`; call site excludes imp surface tags from port zone |

---

## Session 2026-05-30 (continued) — PalaceMesh Display Fix, CSV Permission Fix, ImpedanceBoundary Meshing, Unit Selectors

### Summary of changes

---

#### 1. PalaceMesh 3D display update on first Generate & Run (`features/mesh.py`)

**Bug (from 2026-05-18 OPEN BUG):** After clicking **Generate & Run** for the first time
in a session (i.e., no pre-existing PalaceMesh object), the 3D viewport showed no mesh
until after the simulation finished (or the document was saved and reopened).

**Root cause (Theory A confirmed):** `CmdRun.Activated()` runs `generate_mesh()`
synchronously on the main thread, blocking the Qt event loop. After meshing,
`create_palace_mesh(doc)` creates a new `Mesh::FeaturePython` object and immediately
calls `execute()`. The VP's `__init__` only does `vobj.Proxy = self`; FreeCAD defers
the actual `attach()` call to the next Qt event-loop iteration. When `execute()` calls
`_update_colors()` via `updateData("Mesh")`, `_coords` doesn't exist yet (set in
`attach()`), so `_update_colors()` returns early. The coin3D nodes are never populated.
After `CmdRun.Activated()` returns, `attach()` is eventually called but finds `_coords`
empty — there is no trigger to re-run `_update_colors()` at that point.

**Fix:** At the end of `ViewProviderPalaceMesh.attach()`, added:
```python
# If execute() ran before attach() (fresh object created while generate_mesh
# blocked the Qt event loop), _parsed is already set — paint the mesh now.
if getattr(self, "_parsed", None):
    self._update_colors()
```
`_parsed` is set by `execute()` on the VP proxy before `obj.Mesh` is written. If it's
already present when `attach()` fires, `_update_colors()` runs immediately with `_coords`
now initialized. This is safe for document restore (`_parsed` is `None` then — not
persisted) and for subsequent runs (existing object; `attach()` was called long ago).

**Note:** `CmdMesh` (standalone mesh command) never had this bug because it runs meshing
in a background thread, allowing the Qt event loop to call `attach()` before `_on_done`
fires.

---

#### 2. Pre-delete stale CSV files before Palace runs (`commands/cmd_run.py`)

**Bug:** Running Palace with 2+ MPI processes could crash intermittently with:
```
terminate called after throwing an instance of 'std::filesystem::__cxx11::filesystem_error'
  what():  filesystem error: cannot remove: Permission denied
           [/workspace/palace_output/output_port1/probe-E.csv]
```
Palace's C++ `std::filesystem::remove()` fails with `EACCES` on stale CSV files from a
previous run, under WSL2 with OpenMPI.

**Fix:** Added `_clear_stale_csv(directory)` helper before `_build_passes()`:
```python
def _clear_stale_csv(directory):
    import glob
    for path in glob.glob(os.path.join(directory, "*.csv")):
        try:
            os.remove(path)
        except OSError:
            pass
```
Called immediately after `os.makedirs(pass_out_dir, exist_ok=True)` in both branches of
`_build_passes()` (single-pass `output/` and multi-pass `output_portN/`). Python runs as
the file owner and can reliably delete these files; Palace then starts with a clean
directory and never needs to call `remove()` itself.

---

#### 3. ImpedanceBoundary meshing fixes (`palace/meshing.py`)

Two bugs caused ImpedanceBoundary `MeshSize` to have no effect and produced mesh far
finer than expected near the boundary.

**Bug A — `imp_data` not populated for annular and face-selection ImpedanceBoundaries:**

`imp_data` — the list of `(imp_obj, [gmsh_surf_tags])` pairs passed to
`_apply_refinement_fields()` for per-object Distance+Threshold Gmsh fields — was only
populated for **planar edge-pair** impedance boundaries (inside the
`if planar_port_surf_list or …:` block). Two other matching paths never wrote to it:

- **Annular edge-pair** path (outside the `if` block): added tags to
  `lumped_port_surface_tags` only.
- **Face-selection fallback** (outside the `if` block): same omission.

As a result, the `_imp_surface_tags` filter at the call site didn't remove these surfaces
from `port_surf_tags`, and they received `port_size` refinement instead of their own
per-object `MeshSize` field.

**Fix:**
1. Added `imp_data = []` before the `if` block (robustness — ensures it's defined even
   when the block is skipped).
2. In the annular loop, after `lumped_port_surface_tags.add(tag)`:
   ```python
   if hasattr(port, "Rs"):   # ImpedanceBoundary
       imp_data.append((port, [tag]))
   ```
3. In the face-selection fallback, after the physical group is registered for `port_tags`:
   ```python
   if port_tags and hasattr(port, "Rs"):   # ImpedanceBoundary
       imp_data.append((port, list(port_tags)))
   ```

**Bug B — `_apply_port_transfinite` applied to ImpedanceBoundary faces (primary cause of over-fine mesh):**

`_apply_port_transfinite` forces Gmsh transfinite constraints (`setTransfiniteCurve`)
on every boundary curve of every surface in `lumped_port_surface_tags`, with
`N = max(8, ceil(circumference / (cl_max / 2)))`. This is required for coaxial lumped
ports (Palace's bounding-ball check and area accuracy) but must **not** be applied to
ImpedanceBoundary faces. For a rectangular boundary face with short straight edges,
forcing ≥ 8 nodes per edge creates a mesh far finer than the specified `MeshSize` — or
even than the global `cl_max`.

**Fix:** Compute `_imp_surf_set` from the fully-populated `imp_data` and filter it out
before the transfinite call:
```python
_imp_surf_set = {t for _, tags in imp_data for t in tags}
_lumped_only  = lumped_port_surface_tags - _imp_surf_set
if _lumped_only:
    cl_max_for_transfinite = cl_max if cl_max > 0.0 else 1.0
    _apply_port_transfinite(gmsh, _lumped_only, cl_max_for_transfinite)
```
At this point all three port-matching paths have run, so `imp_data` is fully populated.

---

#### 4. Unit-selection dropdowns for R, L, C fields (`panels/lumped_port_panel.py`, `panels/impedance_boundary_panel.py`)

R, L, C values were previously entered as bare SI numbers (Ω, H, F), requiring
`1e-9` for 1 nH. Each field now has a `QComboBox` unit selector alongside the
`QLineEdit`. Values are **always stored in SI** in the feature objects — the unit
selector is a panel-level display convenience only.

**Unit sets:**

| Field | Units |
|---|---|
| R | Ω, mΩ, kΩ |
| L | H, mH, μH, nH, pH |
| C | F, mF, μF, nF, pF |
| Rs (ImpedanceBoundary) | Ω/sq, mΩ/sq, kΩ/sq |
| Ls (ImpedanceBoundary) | H/sq, mH/sq, μH/sq, nH/sq, pH/sq |
| Cs (ImpedanceBoundary) | F/sq, mF/sq, μF/sq, nF/sq, pF/sq |

**Module-level helpers** (identical in both panel files):

```python
_R_UNITS  = [("Ω", 1.0), ("mΩ", 1e-3), ("kΩ", 1e3)]
_L_UNITS  = [("H", 1.0), ("mH", 1e-3), ("μH", 1e-6), ("nH", 1e-9), ("pH", 1e-12)]
_C_UNITS  = [("F", 1.0), ("mF", 1e-3), ("μF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)]

def _best_unit_idx(si_value, units):
    """Pick the unit that puts abs(si_value)/scale in [0.1, 1000)."""

def _set_unit_field(edit, combo, si_value, units):
    """Populate edit+combo from SI value; sets _prev_idx property on combo."""

def _read_si_field(edit, combo, units, fallback):
    """Return float(edit.text()) × units[combo.currentIndex()][1]."""

def _rescale_on_unit_change(edit, combo, units, new_idx):
    """Re-scale displayed number when user changes unit (reads _prev_idx from combo)."""

def _make_unit_row(edit, combo, units):
    """Return QWidget with QHBoxLayout(edit, combo); populates combo items."""
```

**Behaviour:**
- On open: `_set_unit_field` auto-picks the best unit (e.g., 1e-9 → "1 nH") and sets
  `_prev_idx` on the combo for re-scaling.
- Unit change: `_rescale_on_unit_change` converts via the old SI value so "1000 nH"
  becomes "1 μH" without loss.
- On accept: `_read_si_field` multiplies the displayed number by the unit scale to get SI.

**ImpedanceBoundary additions:** `_RS_UNITS`, `_LS_UNITS`, `_CS_UNITS` defined with the
same scales and "/sq" suffix labels. `_refresh_mode()` now also enables/disables the six
unit combos (alongside the six edits) when the Surface-mode toggle is flipped.

No changes to feature files or config/meshing code — values are consumed in SI throughout.

---

### Files changed in this session

| File | Change |
|---|---|
| `features/mesh.py` | `ViewProviderPalaceMesh.attach()`: call `_update_colors()` if `_parsed` already set |
| `commands/cmd_run.py` | Added `_clear_stale_csv(directory)`; called in both branches of `_build_passes()` |
| `palace/meshing.py` | `imp_data = []` pre-initialized before `if` block; annular + face-selection paths append to `imp_data`; `_apply_port_transfinite` filters out imp surfaces via `_imp_surf_set` |
| `panels/lumped_port_panel.py` | Unit-selector helpers + combos for R, L, C |
| `panels/impedance_boundary_panel.py` | Unit-selector helpers + combos for R, L, C, Rs, Ls, Cs; `_refresh_mode` toggles combos |

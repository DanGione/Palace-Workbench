# Results and Export

After a simulation or sweep completes, the Palace Workbench provides two panels for exploring results: the **S-Parameter Viewer** (single runs) and the **Sweep Results** panel (multi-point sweeps and optimizations).

---

## S-Parameter Viewer

The S-Parameter Viewer opens automatically after each **Generate & Run**. You can also open it manually from **Palace → View Results**.

### Loading results

Results are stored as `results.nc` (a NetCDF4 file) embedded directly inside your `.FCStd` file — no separate output folder is needed. The viewer loads this file automatically after a successful run. To load a different file manually:

- Click **Load…** and navigate to the `results.nc` you want to open.
- Click **Browse…** (if shown) for a file picker.

### Selecting S-parameters

The checkboxes on the left list every computed S-parameter (S11, S21, etc.). Check or uncheck to show/hide individual traces.

- **Magnitude (dB)** — plots `20·log₁₀|S|`. This is the most common view for filter and matching network analysis.
- **Phase (°)** — plots the phase angle of the complex S-parameter.

### Renormalization

S-parameters are extracted relative to each port's characteristic impedance (computed by Palace for Wave Ports, or set directly for Lumped Ports). If you want to view them at a different reference impedance (e.g., renormalize everything to 50 Ω), use the **Renorm target Z** field on the Wave Port panel before running, or use the **Renorm Z** control in the viewer panel (if present) after the run.

### Export Touchstone

Click **Export Touchstone…** to save the S-parameters as an `.sNp` file (Touchstone format), which can be imported into Keysight ADS, AWR Microwave Office, or any other RF EDA tool.

> **Known limitation:** The Export button currently uses 50 Ω as the reference impedance for all ports, regardless of the actual port impedances. If your ports are not 50 Ω, renormalize before exporting.

---

## Sweep Results panel

The Sweep Results panel is used to explore the output of **parameter sweeps** and **geometry optimizations** — simulations where Palace runs multiple times at different parameter values. It opens automatically when a sweep or optimization completes.

Open it manually from **Palace → View Sweep Results**.

### Loading a sweep file

Sweep results are stored in `sweep.nc`, embedded directly inside your `.FCStd` file. Click **Load…** to open a different (e.g. externally-exported) file instead. Once a file is loaded, click **Reload** to re-read it (useful for monitoring an in-progress sweep).

The panel also updates automatically during a running sweep: after each iteration, it reloads the file and advances the view to the latest result.

### Navigating sweep points

The **Sweep point** dropdown lists every completed iteration. Each entry shows the parameter value (e.g., `freq = 2.45` for a single-parameter sweep, or `eval_index = 3` for an optimization).

The best iteration (lowest objective value for optimizations) is marked with **★ best** in the dropdown.

The label below the dropdown shows the exact parameter values for the selected point, and for optimizations, the objective value at that iteration.

**Auto-advance behaviour:**

- If you are viewing the last entry when a new iteration arrives, the view automatically advances to the new result.
- If you have manually selected an earlier entry, your selection is preserved while new iterations are added in the background.

### Overlay mode

Check **Overlay all** to plot all sweep points on the same chart as separate traces. This is useful for visualising how S-parameters change across a parameter range. The sweep point dropdown is disabled in overlay mode.

### S-parameter selection and plot mode

The checkboxes and Magnitude/Phase radio buttons work the same as in the single-run S-Parameter Viewer.

---

## Output files

Every generated file is embedded directly inside the `.FCStd` archive — a Palace
project is always a single, self-contained file. Nothing is written next to
the `.FCStd` on disk (aside from short-lived scratch files during a run,
which are cleaned up automatically).

| File | Embedded on | Contents |
|---|---|---|
| `results.nc` | the Simulation object | Single-run S-params, fields, port impedances |
| `sweep.nc` | the Sweep object | All sweep/optimization iterations |
| `palace_config.json` | the Simulation object | Palace configuration JSON that generated the last single Run |
| `mesh.msh` | the Mesh object | Gmsh mesh file from the last Run/Sweep iteration |
| `geometry.step` | the Mesh object | STEP snapshot of the meshed geometry from the last Run/Sweep iteration |

Use **Palace → Export Mesh…**, **Export Geometry…**, or **Export Config…** to
save a copy of any of these to a regular file on disk — e.g. to inspect
`mesh.msh` in the Gmsh GUI, `geometry.step` in another CAD tool, or
`palace_config.json` to run Palace manually outside the workbench.

The `.nc` files use the NetCDF4 format and, once exported, can be opened with
Python (`xarray`, `netCDF4`), MATLAB, or any compatible tool for custom
post-processing.

Opening a project saved by an older version of the workbench (with a sibling
output folder) automatically migrates its `results.nc`/`sweep.nc`/`mesh.msh`/
`geometry.step`/`palace_config.json` into the `.FCStd` the first time it's
reopened — the original folder is left untouched.

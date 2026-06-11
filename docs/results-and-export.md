# Results and Export

After a simulation or sweep completes, the Palace Workbench provides two panels for exploring results: the **S-Parameter Viewer** (single runs) and the **Sweep Results** panel (multi-point sweeps and optimizations).

---

## S-Parameter Viewer

The S-Parameter Viewer opens automatically after each **Generate & Run**. You can also open it manually from **Palace → View Results**.

### Loading results

Results are stored as `results.nc` (a NetCDF4 file) in the output folder next to your `.FCStd` file. The viewer loads this file automatically after a successful run. To load a different file manually:

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

Sweep results are stored in `sweep.nc` in the output folder. Click **Load…** to open one. Once a file is loaded, click **Reload** to re-read it from disk (useful for monitoring an in-progress sweep).

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

| File | Location | Contents |
|---|---|---|
| `results.nc` | `<model_name>/results.nc` | Single-run S-params, fields, port impedances |
| `sweep.nc` | `<model_name>/sweep.nc` | All sweep/optimization iterations |
| `<model_name>.json` | `<model_name>/` | Palace configuration JSON (for inspection or manual re-runs) |
| `mesh.msh` | `<model_name>/` | Gmsh mesh file |

The `.nc` files use the NetCDF4 format and can be opened with Python (`xarray`, `netCDF4`), MATLAB, or any compatible tool for custom post-processing.

# Palace Workbench for FreeCAD

A FreeCAD 1.0 workbench for 3D electromagnetic FEM simulation using the
[Palace](https://github.com/awslabs/palace) solver (AWS Labs).

Supports driven S-parameter sweeps, eigenmode analysis, and electrostatic
simulations — all configured and launched from the FreeCAD GUI.

## Quick Start (Docker)

**Requirements:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)

1. Download `docker-compose.yml` from this repository.

2. In a terminal, navigate to the folder containing the file and run:
   ```
   docker compose up
   ```
   The first run downloads a ~3 GB image (FreeCAD + Palace pre-compiled).
   Subsequent starts take a few seconds.

3. Open your browser to:
   ```
   http://localhost:6080/vnc.html?autoconnect=1&resize=scale
   ```
   FreeCAD starts automatically. Switch to the **Palace** workbench from
   the workbench dropdown in the toolbar.

4. Your project files go in a `projects/` folder next to `docker-compose.yml`.
   They persist between container restarts.

To stop: `Ctrl+C` in the terminal, or `docker compose down`.

## Workflow

1. Model your geometry in FreeCAD's Part or PartDesign workbench.
2. Switch to the **Palace** workbench.
3. Add a **Simulation** object and configure solver settings (frequency range, etc.).
4. Add an **Airbox** around your geometry to define the simulation domain.
5. Add **Lumped Ports** or **Wave Ports** for your RF connections.
6. Add **Dielectric** or **Conductor** groups for material regions as needed.
7. Click **Generate & Run** to mesh and solve.
8. Results open automatically in the built-in S-parameter viewer.

## Example Files

Three worked examples are included in the repository under the `projects/` folder:
- `Microstrip_test_new.FCStd` — microstrip transmission line, S-parameter extraction
- `Filter_2p4G.FCStd` — 2.4 GHz bandpass filter
- `Wilkinson_Splitter.FCStd` — Wilkinson power divider

Copy these into your local `projects/` directory to run them inside the container.

## Troubleshooting

**Port 6080 already in use:** Edit `docker-compose.yml`, change `"6080:6080"` to
`"6081:6080"`, and navigate to `http://localhost:6081/vnc.html?autoconnect=1&resize=scale`.

**FreeCAD doesn't start after an update:** Docker caches images locally, so `docker compose up`
may still run an older version even after a new release. Force a refresh with:
```
docker compose pull
docker compose down
docker compose up
```

**FreeCAD window appears zoomed in / only part of the window is visible:** This is a noVNC
scaling issue on HiDPI (Retina) displays. Make sure you are using the `resize=scale` URL
parameter (included in the link above). If you connected with a different URL previously,
the VNC session may have already resized itself incorrectly — restart it with:
```
docker compose down
docker compose up
```
Then open the browser link printed in the terminal output.

**FreeCAD doesn't start (first install):** Run `docker compose logs` to see error output.

**Palace workbench missing:** Go to **Edit → Preferences → Workbenches** and enable "Palace".

## Alternative: Native Install (FreeCAD Addon Manager)

If you already have FreeCAD 1.0 and Palace installed natively, install the workbench via
**Tools → Addon Manager → Install from URL** using this repository's URL.
Set the Palace binary path in the Simulation object properties after installation.

# Troubleshooting

---

## Installation and startup

**Port 6080 already in use**

Edit `docker-compose.yml`, change `"6080:6080"` to `"6081:6080"`, and navigate to `http://localhost:6081/vnc.html?autoconnect=1&resize=scale`.

**FreeCAD doesn't start after an update**

Docker caches images locally, so `docker compose up` may still run an older version. Force a refresh:
```
docker compose pull
docker compose down
docker compose up
```

**Devcontainer fails with `postStartCommand ... not found` (Windows)**

If "Reopen in Container" builds successfully but then fails with something like:
```
Running the postStartCommand from devcontainer.json...
/bin/sh: 1: ./.devcontainer/start-gui.sh: not found
```
this usually isn't a missing file — the container itself is fine, only this one startup step failed. Ubuntu's `/bin/sh` (`dash`) reports the same generic "not found" whether a script is truly missing *or* its shebang can't be resolved, e.g. a `#!/bin/bash` line saved with Windows CRLF line endings instead of LF. This is common on a fresh Windows machine, since Git for Windows' default `core.autocrlf=true` converts every text file to CRLF on checkout, including this repo's shell scripts.

To confirm and fix, open a terminal *inside* the running container (VS Code lets you do this even though the lifecycle command failed) and run:
```
file /workspace/.devcontainer/start-gui.sh
```
If it reports "with CRLF line terminators", strip them — this edits the real file on your host through the bind mount:
```
sed -i 's/\r$//' /workspace/.devcontainer/start-gui.sh
```
Then Rebuild Container. To stop this recurring on the same machine, also run `git config core.autocrlf input` (or `false`) in the repo and re-checkout so future pulls don't reintroduce CRLF.

**FreeCAD window appears zoomed in / only part of the window is visible**

This is a noVNC scaling issue on HiDPI (Retina) displays. Make sure you are using the `resize=scale` URL parameter:
```
http://localhost:6080/vnc.html?autoconnect=1&resize=scale
```
If you connected with a different URL previously, restart the container and use the link above.

**Palace workbench missing from the dropdown**

Go to **Edit → Preferences → Workbenches** and enable "Palace", then restart FreeCAD.

**Palace binary not found (native install)**

Double-click PalaceSimulation and set the **Palace binary** field to the full path of your compiled `palace` executable (e.g., `/home/user/palace/build/bin/palace`). Click OK, then try running again.

---

## Mesh generation

**Mesh generation fails immediately with no output**

Check the FreeCAD Report View (View → Panels → Report View) for Gmsh error messages. Common causes:

- The Airbox solid is not assigned (open the Airbox panel and assign a solid).
- A material group references a deleted or renamed body.
- The model has zero-volume bodies or non-manifold geometry. Run **Part → Check Geometry** to find issues.

**Mesh is very coarse or very fine**

Adjust **Max element size** in the Mesh panel. If you leave it blank, Gmsh uses its own heuristic which can be either too coarse or too fine depending on model scale. A good starting point is `λ_max / 10` at your highest simulation frequency, in model units.

For a 5 GHz simulation in mm: λ = 300/5 = 60 mm in free space; substrate-loaded, roughly 30 mm; λ/10 ≈ 3 mm max element size.

**Mesh generation is very slow**

The mesh is too fine. Check your element size settings. A fine **Conductor size** (e.g., 0.01 mm) with no **Refine distance** set will refine the entire model near every conductor face. Either increase Conductor size or set a Refine distance to limit how far the refinement reaches.

**Palace warns about low mesh quality / results look wrong even though meshing "succeeded"**

Palace checks tetrahedron quality after every mesh generation and warns (Report View + Palace console, and a Yes/No prompt on a single Run or the first pass of a sweep — see [Simulation Reference → Mesh quality check](simulation-reference.md#mesh-quality-check)) when it finds degenerate or sliver elements. In practice this almost always comes from **one Conductor or Dielectric group mixing a very thin feature with a much larger body** — e.g. a 25–50 µm trace or ground-plane layer in the same group as a millimeter-scale connector shell or via barrel. **Conductor size** applies to every surface in that group, so:

- Too coarse (or left blank) for the thin feature → Gmsh can't resolve it and produces near-zero-quality (sometimes inverted) tetrahedra right at the seam.
- Fine enough for the thin feature but applied to the whole group → the large body gets meshed at that same fine resolution too, which can multiply the total element count far more than the thin feature alone would need.

Set **Conductor size** to roughly the thinnest feature's own thickness in that group, and set a **Refine distance** so the fine region doesn't have to reach across the whole model — then re-check the warning is gone. Note that exact-touching vs. slightly-overlapping geometry is *not* the deciding factor here; a real interference fit between the two solids doesn't materially change element quality if the size setting is still too coarse for the thinner one. See also [Geometry Guide → Common pitfalls](geometry-guide.md#common-pitfalls).

---

## Simulation

**Simulation fails with "Palace process exited with error"**

Check the FreeCAD Report View and the Palace Console panel for the specific error. Common causes:

- **No excited port:** At least one port must have **Excite this port** checked.
- **Mismatched port indices:** Two ports with the same index. Re-number them so all indices are unique.
- **Mesh not generated:** Click the mesh status in the Mesh panel; if it says "Not generated", generate the mesh first.
- **Out of memory:** Reduce the mesh density or the FE order (try order 1).

**Simulation produces no output (results.nc missing)**

Palace may have run but produced no valid output. Check the FreeCAD console for error lines from Palace. If L0 is wrong (e.g., set to 1.0 when your model is in mm), the frequency range will be effectively outside the mesh resolution and Palace may produce NaN outputs.

**S11 is 0 dB across all frequencies (signal not going anywhere)**

- The excited port's impedance (R field) is set to 0. Set it to 50 Ω.
- A conductor body is not assigned to any material group and is being treated as vacuum — the port is terminating in air.
- The port direction is wrong, causing zero coupling.

**S21 is −300 dB or similar**

- The receive port is not in the mesh (its assigned body is missing from the geometry tree or not found by Gmsh).
- The port index on the receive port doesn't match the one Palace solved for.

---

## Results

**S-parameter viewer shows nothing after the run**

- `results.nc` is embedded inside the `.FCStd` file, not written next to it — check the Simulation object's **ResultsFile** property is set (Report View also logs a "Results database →" message after a successful run).
- Use **Palace → Export Config…**/**Export Mesh…** to confirm a run actually completed and produced embedded output.
- Click **Load…** in the S-Parameter Viewer to open a different (e.g. externally-exported) `results.nc` file.
- If the file exists but the viewer is blank, the simulation may have solved only one port and produced a 1×1 S-matrix with no cross-terms. Check that S11 is visible in the checkboxes.

**"xarray is required" error when loading results**

`xarray` and `netCDF4` are not installed. In the Docker image they are pre-installed; on a native install run:
```
"<freecad>/bin/python" -m pip install xarray netCDF4
```

---

## Sweeps and optimization

**Optimization converges immediately (stops after 1–2 iterations)**

The **Simplex size tolerance** is too loose. Open the Sweep panel, go to the Nelder-Mead settings, and reduce `xatol` (e.g., from `0.1` to `0.01`). Also check that your parameter ranges (Min, Max) are wide enough — if Min ≈ Max the optimizer has nowhere to go.

**Optimization is stuck in a local optimum**

Try switching from Nelder-Mead to **Differential Evolution**, which is a global optimizer. It requires more simulations (set Max iterations to at least 20× the number of parameters) but is much less prone to local minima.

**Port faces lost after a geometry edit**

If you edit Sketch constraints or change a VarSet property manually, FreeCAD may recompute the body and assign new internal face names. Port face assignments are stored by face name and can become stale. Re-open the affected port panel and click **Use current face selection** again to reassign the faces.

This is most likely to happen with PartDesign bodies after a topology change (adding or removing a Pad, Fillet, etc.). It does not happen with pure dimension changes.

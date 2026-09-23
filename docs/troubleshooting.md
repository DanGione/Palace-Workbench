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

Check the FreeCAD Report View (View → Panels → Report View) for mesher error messages. Common causes:

- The Airbox solid is not assigned (open the Airbox panel and assign a solid).
- A material group references a deleted or renamed body.
- The model has zero-volume bodies or non-manifold geometry. Run **Part → Check Geometry** to find issues.

**Mesh is very coarse or very fine (Gmsh backend)**

Adjust **Max element size** in the Mesh panel. If you leave it blank, Gmsh uses its own heuristic which can be either too coarse or too fine depending on model scale. A good starting point is `λ_max / 10` at your highest simulation frequency, in model units.

For a 5 GHz simulation in mm: λ = 300/5 = 60 mm in free space; substrate-loaded, roughly 30 mm; λ/10 ≈ 3 mm max element size.

If you're on the **Netgen** backend instead, there is no equivalent manual
knob — it sizes elements automatically from the geometry. If the automatic
result is consistently too coarse or too fine for your model, that's a sign
to switch to the Gmsh backend for this board rather than look for a hidden
Netgen sizing setting; see [Simulation Reference → Choosing a mesh
backend](simulation-reference.md#choosing-a-mesh-backend).

**Mesh generation is very slow (Gmsh backend)**

The mesh is too fine. Check your element size settings. A fine **Conductor size** (e.g., 0.01 mm) with no **Refine distance** set will refine the entire model near every conductor face. Either increase Conductor size or set a Refine distance to limit how far the refinement reaches — but don't swing too far the other way, see the floor described just below and in [Simulation Reference → Mesh panel](simulation-reference.md#mesh-panel). A manually-set **Conductor size**/**Port size** left over from Gmsh tuning has no effect on the Netgen backend (it's ignored, not slow) — if a mesh that used to be fast under Gmsh is slow after switching to Netgen, the cause is elsewhere; check the model's actual feature sizes instead.

**Palace warns about low mesh quality / results look wrong even though meshing "succeeded"**

The workbench checks tetrahedron quality after every mesh generation and warns (Report View + Palace console, and a Yes/No prompt on a single Run or the first pass of a sweep — see [Simulation Reference → Mesh quality check](simulation-reference.md#mesh-quality-check)) when it finds degenerate or sliver elements. On the **Gmsh** backend, two distinct causes produce the same symptom (neither applies to Netgen, which doesn't use these sizing settings — if Netgen itself reports low quality, try the Gmsh backend on the same model instead of looking for an equivalent sizing fix):

1. **One Conductor or Dielectric group mixing a very thin feature with a much larger body** — e.g. a 25–50 µm trace or ground-plane layer in the same group as a millimeter-scale connector shell or via barrel. **Conductor size** applies to every surface in that group, so:

   - Too coarse (or left blank) for the thin feature → Gmsh can't resolve it and produces near-zero-quality (sometimes inverted) tetrahedra right at the seam.
   - Fine enough for the thin feature but applied to the whole group → the large body gets meshed at that same fine resolution too, which can multiply the total element count far more than the thin feature alone would need.

   Set **Conductor size** to roughly the thinnest feature's own thickness in that group, and set a **Refine distance** so the fine region doesn't have to reach across the whole model — then re-check the warning is gone. Note that exact-touching vs. slightly-overlapping geometry is *not* the deciding factor here; a real interference fit between the two solids doesn't materially change element quality if the size setting is still too coarse for the thinner one.

2. **Refine distance set too tight for a single, already-well-scaled feature.** Even when **Conductor size**/**Port size** is a good match for the feature itself, an explicit **Refine distance** that's too short forces Gmsh to collapse a large size jump (refined size → global max) into a short span, which alone can produce inverted elements at the transition — no group-mixing needed. Tell-tale sign: the warning names a group whose own Conductor/Port size already looks reasonable for its geometry. Fix: lengthen **Refine distance** (or clear it back to blank/auto) rather than touching the feature's own size. On one real case (a 0.1 mm coax conductor transitioning to a 3 mm background), widening Refine distance from 0.2 mm to auto (~12 mm) took the worst element quality from clearly inverted to essentially zero — at the cost of a 3× larger, roughly 2× slower mesh. The relationship isn't perfectly linear (an intermediate value can occasionally be worse than either end), so increase it in steps and re-check the quality result rather than jumping straight to the largest value, especially if mesh cost matters (e.g. for repeated sweep/optimization runs).

See also [Geometry Guide → Common pitfalls](geometry-guide.md#common-pitfalls).

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

- The receive port is not in the mesh (its assigned body is missing from the geometry tree or not found by the mesher).
- The port index on the receive port doesn't match the one Palace solved for.

**"Probe N could not be found! Using default value 0.0!" warnings at startup**

Expected and harmless for a Wave Port with no **Integration edge** set (see
[Ports Reference → Wave Port → Impedance calculation](ports-reference.md#impedance-calculation)) —
Palace falls back to sampling a grid of field probes over the port face to
compute impedance, and a few of those points can fall just outside the
port's real (e.g. annular) cross-section, which is deliberately unmeshed
conductor material. This doesn't affect the computed impedance or
S-parameters (those probes simply contribute nothing) — it's log noise, not
a sign of a meshing problem. If you'd rather not see it at all, set an
**Integration edge** on the port (switches to Palace's native, more accurate
impedance calculation and skips the probe grid entirely).

**"This simulation requests Device=GPU, but this Palace installation has no CUDA marker"**

**Device** is set to `GPU` on the Simulation panel's General tab, but the
running Palace binary wasn't built with CUDA support. Most likely you're
running the default `:latest`/`:dev` image (CPU-only) — switch to the
`:cuda` image variant and launch it with `docker-compose.cuda.yml` instead.
If you built Palace yourself outside Docker with `-DPALACE_WITH_CUDA=ON`,
this check doesn't know about it yet; the error names the exact marker file
path to create to confirm CUDA support and silence the check.

**GPU run fails to start, or `nvidia-smi` isn't found inside the container**

The `:cuda` image needs the host to have the [NVIDIA Container
Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
installed so Docker can pass the GPU through — without it, the GPU
reservation in `docker-compose.cuda.yml` silently has nothing to attach to.
Verify with `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04
nvidia-smi` on the host before troubleshooting further inside this
workbench's container.

**GPU run dies with "Floating point exception" (signal 8) after "Assembling multigrid hierarchy"**

This is **not** an out-of-memory failure in the usual sense, even though GPU
memory is typically near-full when it happens (more on that below). Signal 8
on x86-64 is an integer divide (or modulo) by zero. A gdb backtrace places it
in SLEPc's CUDA basis-vector routine (`BVMultInPlace_BLAS_CUDA`), reached
from the Chebyshev smoother's largest-eigenvalue estimate while Palace sets
up its p-multigrid hierarchy.

**Root cause, confirmed against SLEPc's own source** (v3.24.1, the exact
version Palace pins — cloned directly and read, not inferred): that function
checks `cudaMemGetInfo()` for free GPU memory before computing a large
matrix product. If there isn't enough to do the whole thing at once, it
falls back to computing the work in batches, sized as
`freemem/(m*sizeof(PetscScalar))` where `m` is the operator size (our large
model's smoothed multigrid level, ~1.28M unknowns) — this divides by `m`,
the huge dimension, rather than `n`, the small number of vectors actually
being multiplied, which looks like the real bug: for a large `m` this
collapses to 0 well before free memory is actually exhausted. Two lines
later, that batch size is used as a modulo divisor (`m % bs`) with no check
that it's nonzero. This is a genuine upstream SLEPc bug, not anything
particular to this workbench or Docker image, and is present unchanged in
Palace v0.18.1 as well as v0.17.0 (confirmed: PR #837, credited in the 0.18.0
changelog as "fixed smoother spectral estimates," only touches a different,
already-safe branch — the crashing complex-operator branch is untouched by
that fix).

It needs two things together: a multi-level hierarchy (which only exists at
**FE order 2 and above**), and a **large** problem. Measured on this build:

| run | hierarchy | result |
|---|---|---|
| `Coax_Directional_Bridge`, order 1 | single level, 246,279 unknowns | runs |
| `Coax_Directional_Bridge`, order 2 | two levels, 1,278,924 on the smoothed level | **crashes** |
| `Microstrip_test_new`, order 2 | two levels, 27,016 on the smoothed level | runs |

So small models are fine on GPU at any order — this bites large ones. Both
the adaptive and the uniform frequency sweep crash identically, so the
sweep type is not involved.

If you hit it, drop to **FE order 1** (verified to run the large model to
completion on GPU) or switch to **Device = CPU** for order 2 and above. The
run panel names this cause itself when the crash happens.

We deliberately did not patch this ourselves — it would mean carrying a
source patch against Palace/SLEPc indefinitely for a bug that isn't ours,
and a CPU-only-PETSc/SLEPc workaround was tried and abandoned when it hit a
*second*, deliberate upstream guard (`palace/linalg/petsc.hpp`'s
`#error "Mismatch between MFEM and PETSc CUDA support!"`) that would have
needed patching too. Order 1 GPU never reaches this code path at all (a
single-level hierarchy has no Chebyshev smoother to set up) and stays
comfortably within memory budget regardless of model size — see the memory
table below. For a model that genuinely needs order 2, GPU's actual biggest
win on real sweeps is wave-port re-solving (recomputed once per output
frequency, ~89% of a realistic sweep's wall clock) rather than the main 3-D
solve this bug lives in — see cuDSS below for that path.

**How much GPU memory does a run actually need?**

Measured on the same real board (`Coax_Directional_Bridge`, 187,415
elements) on an 8 GiB RTX 3060 Ti:

| config | peak GPU memory |
|---|---|
| Order 1, GPU | **~4.0 GiB** — comfortable margin, roughly half the card |
| Order 1, CPU | ~4.7 GiB (host RAM) |
| Order 2, GPU | **~7.8 GiB / 8 GiB** — right before the SIGFPE above |

Order 1 GPU has real headroom on an 8 GiB card and isn't "just barely"
fitting — order 2's much larger multigrid hierarchy (roughly 5× the
unknowns on the smoothed level for this same mesh) is what pushes memory to
the edge, and that's directly why it's the one that trips the SLEPc bug
above: that bug only manifests when free GPU memory is actually low at the
moment it's checked. If your model's CPU memory usage is well over ~6-7
GiB, expect order 2 on an 8 GiB card to be tight regardless of whether the
SIGFPE specifically hits.

**Does a GPU run use Windows' "Shared GPU Memory"?**

Partly, and not in the way the Task Manager display suggests. Measured on
WSL2 (RTX 3060 Ti, 8 GiB, driver 616.92) by calling CUDA directly:

| allocation type | 12 GiB requested on the 8 GiB card |
|---|---|
| `cudaMalloc` (plain device memory) | **succeeded** — allocated, written and synchronized, with `nvidia-smi` plateauing at ~7.8 GiB while the rest came from shared system memory |
| `cudaMallocManaged` (unified memory) | **failed** — `out of memory` |

So plain device allocations *do* spill into shared GPU memory, which is why
a run can keep going past the point where dedicated memory looks exhausted.
CUDA managed allocations cannot spill — that is a documented WSL2
limitation — and fail hard at the physical limit. Spilled memory is backed
by host RAM and is far slower than real VRAM, so a run that relies on it
heavily may appear to hang. Not every allocation in Palace's GPU pipeline
has been individually confirmed to use the spillable path — hypre (the bulk
of the algebraic multigrid work) is confirmed on it, but this hasn't been
checked for every library in the chain, so treat "12 GiB worked once" as
encouraging, not a guarantee that a specific large model will fit.

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

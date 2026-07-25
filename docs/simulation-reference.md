# Simulation Reference

This page documents every setting in the Simulation, Airbox, and Mesh panels.

---

## Simulation panel

Double-click the **PalaceSimulation** object to open this panel. It has three tabs.

### General tab

| Field | Description |
|---|---|
| **Simulation type** | `Driven` — frequency-domain S-parameter solve. `Eigenmode` — find resonant frequencies and Q-factors. `Electrostatic` — DC capacitance extraction. |
| **FE order** | Finite element polynomial order (1–4). Higher order gives more accurate results per element but increases memory and solve time. Order 2 is a good default; order 1 for very large models. |
| **Verbose** | Palace console verbosity: 0 = minimal, 1 = normal, 2 = debug. |
| **MPI processes** | Number of parallel MPI ranks. On a 4-core machine, 2–4 is typical. More ranks reduces wall-clock time but increases memory. |
| **OMP threads/rank** | OpenMP threads per MPI rank. 0 = let the system decide. If MPI processes × OMP threads exceeds your core count, you'll see diminishing returns. |
| **Length scale L0 (m)** | Converts model units to metres. Use `1e-3` if your model is drawn in millimetres (the typical FreeCAD default). All frequency-independent lengths (Airbox size, port geometry) are multiplied by L0 internally. |
| **Output directory** | Legacy field, no longer used — Palace's raw CSV/field output is written to a temporary scratch location and discarded once the results database is built and embedded in the `.FCStd`. Safe to leave at its default. |
| **Palace binary** | Full path to the `palace` executable. Pre-configured in the Docker image. For native installs, point this to your compiled binary. |

**Mesh group (also on General tab):**

| Field | Description |
|---|---|
| **Max element size** | Global maximum Gmsh element size in model units. 0 = Gmsh chooses automatically. |
| **Min element size** | Global minimum element size. 0 = Gmsh chooses. |

These duplicate the settings in the Mesh panel (see below). The Simulation panel copy is provided for convenience; both control the same properties.

---

### Solver tab

The solver tab changes content based on the simulation type selected on General.

#### Driven (S-parameter) settings

| Field | Description |
|---|---|
| **Run passes in parallel** | When checked, all per-port excitation passes launch simultaneously. Uncheck on memory-constrained machines to run them one at a time. |
| **Adaptive Frequency Sweep** (checkbox group) | Enable to use Palace's rational-interpolation adaptive sampling instead of a uniform frequency step. Palace adds frequency samples automatically where the response changes rapidly. |
| **— Tolerance** | Convergence criterion for the adaptive sampler (e.g. `0.01` = 1%). Palace stops adding samples when the rational model changes by less than this between iterations. |
| **— Max samples** | Hard limit on the number of frequency samples the adaptive sampler may take. |
| **— Max candidates** | Maximum candidate frequencies evaluated per adaptive iteration. `0` = Palace's internal default. |

#### Eigenmode settings

| Field | Description |
|---|---|
| **Number of modes** | How many resonant modes to compute. |
| **Min freq hint (GHz)** | Approximate lower bound for the frequency search. Palace uses this as a starting point, not a hard filter. |

#### Electrostatic settings

| Field | Description |
|---|---|
| **Max iterations** | Maximum iterations for the linear solver. |
| **Tolerance** | Linear solver convergence tolerance. |

---

### Frequencies tab

The Frequencies tab is only enabled for Driven simulations.

**Sweep mode — Linear:**

| Field | Description |
|---|---|
| **Min frequency (GHz)** | First frequency point. |
| **Max frequency (GHz)** | Last frequency point. |
| **Frequency step (GHz)** | Spacing between output points. Also controls the sample density for adaptive sweeps. |

**Sweep mode — Samples:**

Allows defining multiple frequency sub-sweeps with different types and resolutions in the same simulation. Each row in the table defines one sub-sweep:

| Row type | Columns used | Description |
|---|---|---|
| **Linear** | Min, Max, Step | Uniformly spaced from Min to Max in steps of Step GHz. |
| **Log** | Min, Max, Count | Logarithmically spaced with Count samples between Min and Max. |
| **Point** | Freq only | A single frequency point. |

Use Add Linear / Add Log / Add Point buttons to append rows. Remove Selected removes highlighted rows.

---

## Airbox panel

Double-click the **PalaceAirbox** object.

### General tab

| Field | Description |
|---|---|
| **Simulation domain** | Shows the solid currently used as the Airbox. Click **Use current 3D selection** to assign or change it. Select the solid in the 3D view first. |
| **Default outer BC** | Boundary condition applied to all outer Airbox faces that don't have a per-face override. `PEC` — perfectly reflecting (zero tangential E). `PMC` — perfectly reflecting (zero tangential H). `Absorbing` — first-order absorbing BC, minimises reflections from outgoing waves. |

Use **PEC** when the outer wall should be a ground plane or a metallic enclosure. Use **Absorbing** (with sufficient clearance) for open-space radiation or when port faces don't fill the entire Airbox face.

### Per-Face BCs tab

Override the default BC on individual Airbox faces. This is useful when most faces should absorb but specific faces should be PEC (e.g., a ground plane on one side of a microstrip model).

To assign an override:
1. Select one or more Airbox faces in the 3D view.
2. Choose the BC type from the **BC type** dropdown.
3. Click **Assign from 3D selection**.

The table updates to show the assigned override. Click a row to re-select that face in the 3D view. Use **Remove selected** to delete an override and revert the face to the default BC.

---

## Mesh panel

Double-click the **PalaceMesh** object, or click the **Mesh Settings** toolbar button.

### Element Sizing group

All sizes are in model units (mm if L0 = 1e-3).

| Field | Description |
|---|---|
| **Max element size** | Global maximum element edge length. Leave blank to let Gmsh choose based on geometry. A good starting point is roughly λ/10 at the highest simulation frequency, expressed in model units. |
| **Min element size** | Global minimum element edge length. Leave blank unless elements are collapsing on very small features. |
| **Conductor size** | Element size right at conductor and dielectric surfaces. The mesh grows smoothly from this value back to the global max. Leave blank to use the global max near conductors. Use this to resolve skin-depth effects or narrow coupling gaps. |
| **Port size** | Element size right at port faces and integration edges. Also controls the node density on edge integration curves. Leave blank to use the global max. |
| **Refine distance** | Distance over which the mesh transitions from the refined size back to the global max. Leave blank for auto (30% of the model bounding-box extent). |

**Typical starting values for a 2.4 GHz PCB filter in mm:**

| Field | Value |
|---|---|
| Max element size | 2.0 |
| Conductor size | 0.2 |
| Port size | 0.3 |

### Mesh quality check

After every mesh generation, Palace checks the quality of the generated tetrahedra
(Gmsh's `minSICN` measure — 1.0 is equilateral, 0 is degenerate, negative is inverted)
and logs a pass/warn line to the FreeCAD Report View and the Palace debug console. If
any elements fall below the threshold:

- **Single "Generate Mesh" or "Generate && Run":** you're asked whether to proceed
  before the mesh is kept/the simulation launches.
- **Sweeps and optimizations:** only the **first** meshing pass asks; if the mesh
  degrades on a later iteration it's just logged, and the run's "Sweep Complete"/"Sweep
  Failed" summary notes how many iterations were affected.

The threshold is **Quality warn threshold** (`MeshQualityWarnThreshold`, default
`0.1`), a property on the **PalaceMesh** object — there's no dedicated field for it in
this panel yet, so set it from the **Data** tab in the Property Editor (group
"Refinement", alongside Conductor size/Port size/Refine distance). `0` disables the
check entirely.

See [Troubleshooting → Mesh generation](troubleshooting.md#mesh-generation) for what
usually causes a low-quality mesh and how to fix it.

### Mesh Status group

Read-only information about the last generated mesh: file path, file size, and node/element counts. Use this to gauge mesh density before running Palace.

### Visible Attributes

A checklist of all mesh attribute groups (surface boundaries and volume domains). Unchecking a group hides its elements in the 3D view — useful for inspecting internal mesh structure without hiding the whole mesh.

### Generate Mesh button

Equivalent to clicking the **Generate Mesh** toolbar button. Saves the current settings and runs Gmsh. Progress is reported in the FreeCAD Report View.

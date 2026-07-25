# Geometry Guide

This guide explains how to structure your FreeCAD model so Palace can simulate it correctly.

---

## How Palace sees geometry

Palace needs three things from your model:

1. **A simulation domain (Airbox)** — a solid that encloses everything. Its outer faces carry boundary conditions (absorbing, PEC, PMC). Everything outside this solid is ignored.
2. **Material regions** — solids assigned to Conductor or Dielectric groups define the physical materials. Anything inside the Airbox but *not* in a material group is treated as vacuum.
3. **Ports** — faces or edges where energy enters and exits the simulation.

The key rule: **every solid that matters must live inside the Airbox, and every conductor/dielectric must be assigned to a material group.** Solids that are missing from all material groups will be meshed as vacuum.

---

## Recommended geometry workflow

### Use PartDesign for parametric models

PartDesign Bodies (Pad, Pocket, Revolution, etc.) work well with Palace. Each feature references a Sketch, and Sketch dimensions can be driven by a **VarSet** for parametric sweeps.

Part workbench primitives (Part Box, Part Cylinder) also work, but they're harder to link to VarSets.

### Keep bodies non-overlapping

Palace uses the mesh to define material interfaces. If two solids overlap (e.g., a trace body that penetrates the substrate), Gmsh may produce degenerate elements or incorrect interfaces. Either model solids that share faces exactly, or use Boolean operations (Part → Boolean → Cut) to remove the overlap.

### Airbox size

The Airbox should clear all your geometry on every side. A good starting rule is to add at least λ/4 of clearance at the highest simulation frequency on the sides where you want to absorb radiation. For transmission line models where ports terminate at the Airbox faces, the clearance on the port sides can be zero (the port face coincides with the Airbox face).

Absorbing boundary conditions in Palace are first-order, so they are only accurate if the fields are close to a plane wave at the boundary. For highly reactive structures (resonators, spirals), increase the clearance or use eigenmode analysis instead.

---

## Material groups

Material groups are Palace-specific objects that link your geometry to solver material properties.

### Conductor groups

A **Conductor** group assigns a perfect electric conductor (PEC) or a resistive surface to one or more solid bodies. Conductors are typically traces, ground planes, vias, and metal enclosures.

To create one: **Palace toolbar → Add Conductor Group**. In the panel that opens:

- **Conductor type:** `PEC` (lossless) or `Lossy` (surface resistance Rs).
- **Rs (Ω/sq):** Surface resistance per square, used when type is Lossy. Compute Rs = √(πfμ/σ) or use standard tables (copper at 2.4 GHz ≈ 0.026 Ω/sq).
- **Bodies:** Click each body in the 3D view, then click **Assign from 3D selection**.

### Dielectric groups

A **Dielectric** group assigns permittivity and loss tangent to a solid.

- **Permittivity:** Relative permittivity εr.
- **Loss tangent:** tan δ (0 = lossless). Typical PCB substrates: Rogers 4003C tan δ ≈ 0.0027.
- **Bodies:** same assignment workflow as conductor groups.

---

## VarSets and parametric geometry

A **VarSet** (App::VarSet) is a FreeCAD object that holds named numeric properties — essentially a spreadsheet row. Sketch constraints can reference VarSet properties using the formula syntax `=VarSetName.PropertyName`, making the geometry fully parametric.

VarSets are the entry point for the **Sweep & Optimization** feature. Palace can vary any VarSet property between simulations without you touching the geometry manually.

### Creating a VarSet

1. In the **Part** or **PartDesign** workbench, go to **Part → Create a VarSet** (or equivalent in your FreeCAD version).
2. In the VarSet dialog, add properties with names like `trace_width`, `substrate_height`, `gap`.
3. In your Sketch constraints, replace numeric values with `=VarSetName.trace_width` etc.

### Good practices

- Give properties descriptive names — they appear in the Sweep panel as-is.
- Use consistent units. If L0 = 1e-3 (mm), all VarSet lengths should also be in mm.
- Group related dimensions into one VarSet rather than scattering them across multiple objects.

---

## Common pitfalls

**Gap between conductor and dielectric:** If there is even a tiny gap between a trace and the substrate it sits on, Gmsh inserts vacuum elements in between. The result is wrong S-parameters and unrealistic fields. Ensure faces are co-planar or use Boolean fusion.

**Degenerate/sliver mesh elements from mixed-scale features in one group:** A Conductor or Dielectric group that mixes a very thin feature (a 25–50 µm trace or ground-plane layer) with a much larger body in the *same* group (a connector shell or via barrel several mm across) forces one **Conductor size** setting to serve both scales — too coarse and Gmsh can't resolve the thin layer at all, producing near-zero-quality (occasionally inverted) tetrahedra right at the seam between the two; fine enough for the thin layer and the whole group, including the large body, gets meshed at that resolution, which can be far more expensive than necessary. This is a mesh-*sizing* problem, not primarily a geometry one — fusing the solids or giving them a deliberate interference fit doesn't fix it on its own, since the elements degrade from an inappropriate target size, not from how the surfaces touch. Palace checks for this automatically after meshing (see [Simulation Reference → Mesh quality check](simulation-reference.md#mesh-quality-check)) and warns if it finds it; the fix is to set **Conductor size** to roughly the thinnest feature's own thickness and a **Refine distance** that keeps the fine region local, per group.

**Airbox too tight:** If the Airbox faces cut through a material region, Palace will reject the mesh or produce incorrect BCs on those faces. Always verify there is clearance on every side.

**Non-manifold geometry:** Solids with edges shared by more than two faces, zero-thickness sheets, or self-intersecting bodies can confuse Gmsh. Use **Part → Check Geometry** to detect issues before meshing.

**Forgetting to assign a body to a material group:** A copper trace not in any Conductor group is treated as vacuum. The simulation will run but transmission loss will be wrong.

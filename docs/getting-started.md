# Getting Started

This guide walks you through opening a working example, running your first simulation, and reading the results. It uses the included `Microstrip_test_new.FCStd` file so you can follow along immediately without building any geometry.

**Prerequisites:** A running Palace Workbench container. If you haven't set that up yet, follow the [Quick Start](../README.md#quick-start-docker) in the README first.

---

## 1. Open the example file

Inside the FreeCAD window:

1. Go to **File → Open**.
2. Navigate to **File System → /root/projects/** (or wherever you mapped your `projects/` folder).
3. Open **`Microstrip_test_new.FCStd`**.

The model tree on the left will show several objects: a Body containing the microstrip geometry, material groups, ports, and simulation settings.

---

## 2. Switch to the Palace workbench

From the workbench dropdown in the toolbar (it may show "Part" or "PartDesign"), select **Palace**. The Palace toolbar appears along the top.

If the Palace workbench is not listed, go to **Edit → Preferences → Workbenches** and enable it, then restart FreeCAD.

---

## 3. Inspect the model tree

The model tree shows:

| Object | What it is |
|---|---|
| **Body** / **Sketch** | The geometry (substrate, trace, ground plane) |
| **PalaceSimulation** | Solver settings — frequency range, type, binary path |
| **PalaceAirbox** | The simulation domain (a box surrounding the structure) |
| **PalaceLumpedPort_1 / _2** | Port definitions at each end of the transmission line |
| **PalaceConductor_...** | Metal conductor material assignments |
| **PalaceDielectric_...** | Substrate material assignment |
| **PalaceMesh** | Mesh settings |

Double-clicking any Palace object opens its settings panel.

---

## 4. Check the simulation settings

Double-click **PalaceSimulation**. Under the **General** tab, confirm:

- **Palace binary** points to `/usr/local/bin/palace` (pre-set in the Docker image). If you are running natively you must set this path yourself.
- **Length scale L0** is `0.001` (millimetres).

Under the **Frequencies** tab:

- **Min frequency:** 1.0 GHz
- **Max frequency:** 5.0 GHz
- **Frequency step:** 0.1 GHz

Click **Cancel** — no changes needed.

---

## 5. Run the simulation

Click the **Generate & Run** button in the Palace toolbar (or go to **Palace → Generate & Run**).

The Palace Console panel opens at the bottom of the window and shows progress:

```
Meshing…
Running Palace (pass 1/2)…
Running Palace (pass 2/2)…
Done.
```

Meshing takes a few seconds; Palace solve time depends on your hardware. On a modern laptop the microstrip example takes under a minute.

---

## 6. Read the results

When the run finishes, the **S-Parameter Viewer** panel opens automatically and plots S11 and S21 vs. frequency.

- **S11** (return loss) should be low across the band — the microstrip is well-matched.
- **S21** (insertion loss) should be close to 0 dB, indicating low-loss transmission.

Use the checkboxes on the left to show/hide individual S-parameters. The **Magnitude (dB)** / **Phase (°)** radio buttons switch the plot type.

To export the results as a Touchstone file, click **Export Touchstone…** and choose a save location.

---

## 7. What's next?

Now that you've run a simulation, the natural next steps are:

- **Build your own geometry** — see the [Geometry Guide](geometry-guide.md) for tips on how to structure parts, assign materials, and use VarSets for parametric models.
- **Understand every setting** — see [Ports Reference](ports-reference.md) and [Simulation Reference](simulation-reference.md) for a full explanation of each field.
- **Run a parameter sweep or optimization** — see [Sweep & Optimization](sweep-and-optimization.md).
- **Something not working?** — see [Troubleshooting](troubleshooting.md).

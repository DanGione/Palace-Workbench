# Ports Reference

Ports are where RF energy enters and exits the simulation. Palace supports three port types: Lumped Port, Wave Port, and Impedance Boundary. Each is added from the Palace toolbar and configured with its own panel.

Every port has a **Port index** (1-based integer). Indices must be unique across all ports in the model. The index determines the row/column in the S-parameter matrix: Port 1 corresponds to S11, S21, etc.

---

## Lumped Port

A lumped port represents a discrete circuit element — a resistor, inductor, or capacitor connected across a gap — and is the most common port type for planar RF structures (microstrips, CPW, patch antennas, filters). Palace solves for the voltage and current at the port to extract S-parameters.

**When to use:** Transmission lines, filters, antennas, and any structure where you can identify a clearly defined voltage gap. Works well for structures with characteristic impedances close to 50 Ω.

**When to avoid:** Waveguide transitions, or any structure where the field distribution at the port cannot be described by a simple impedance.

### Panel fields

| Field | Description |
|---|---|
| **Port index** | Unique integer identifying this port (1–99). Determines position in the S-matrix. |
| **Port edges** | Two edges that define the + and − terminals of the port. Select them in the 3D view, then click **Use current edge selection (2 edges)**. The edges should span the gap between the conductor and the ground reference. |
| **Port faces** | Alternative to edge mode: one or more faces that define the port cross-section. Less common for planar structures. |
| **Detected geometry** | Auto-detected as `planar` (rectangular) or `annular` (coaxial) based on the selected face shape. Informational only. |
| **Direction** | Current flow direction through the port: `X`, `Y`, `Z`, `-X`, `-Y`, `-Z` for planar ports; `+R`, `-R` for coaxial (annular) ports. Auto-suggested based on geometry; adjust if incorrect. |
| **R** | Port resistance in Ω. For a 50 Ω system, set to 50 Ω. Must be non-zero. |
| **L** | Port series inductance. Set to 0 to omit. |
| **C** | Port shunt capacitance. Set to 0 to omit. |
| **Excite this port** | Check to use this port as the source in a driven simulation. At least one port must be excited. Uncheck to make a port a passive load (termination only). |

---

## Wave Port

A wave port solves for the modal field distribution at a cross-section and uses it to define S-parameters rigorously. It is more accurate than a lumped port for waveguide-like structures because it doesn't assume a direction or impedance — Palace computes the characteristic impedance from the eigenmode.

**When to use:** Coaxial connectors, rectangular/circular waveguide, microstrip cross-sections at the model boundary, or any structure where the mode shape matters.

**When to avoid:** Structures where the port cross-section does not have a well-defined transverse mode (e.g., the middle of a resonator). Wave ports must be placed at the boundary of the simulation domain (Airbox face).

### Panel fields

| Field | Description |
|---|---|
| **Port index** | Unique integer identifying this port. |
| **Port faces** | The face(s) at the Airbox boundary that define the port cross-section. Select in the 3D view, then click **Use current face selection**. |
| **Integration edge** | An edge on the port face used to define the reference direction for impedance calculation (from ground to signal conductor). Select a single edge, then click **Use current edge selection**. |
| **Number of modes** | How many modal modes to include. 1 is sufficient for single-mode structures (microstrip, coax). Increase for overmoded waveguide. |
| **Excitation** | Check to excite this port as the source. |
| **Characteristic Z** | Read-only. Displays the characteristic impedance computed by Palace after the last simulation run. Updated automatically. |
| **Renorm target Z** | The impedance to renormalize S-parameters to (e.g., 50 Ω). This is independent of Characteristic Z. |

---

## Impedance Boundary

An impedance boundary assigns a surface impedance (R, L, C per square) to a face without treating it as a port for S-parameter extraction. It is used to model:

- Lossy conductors (finite conductivity) without solving the volume
- Absorbing surfaces at arbitrary internal boundaries
- Resistive sheets

**When to use:** Adding conductor loss to a metallic wall without using a full Conductor material group, or defining an internal absorbing surface.

**When to avoid:** Don't use as a substitute for proper ports — Impedance Boundaries do not produce S-parameters.

### Panel fields

| Field | Description |
|---|---|
| **Boundary index** | Unique integer. |
| **Faces** | Select faces in the 3D view, then click **Assign from 3D selection**. |
| **Rs (Ω/sq)** | Surface resistance per square. For copper at 2.4 GHz, Rs ≈ 0.026 Ω/sq. |
| **Ls (H/sq)** | Surface inductance per square (0 to omit). |
| **Cs (F/sq)** | Surface capacitance per square (0 to omit). |

---

## Port numbering and the S-matrix

Palace produces an N×N S-parameter matrix where N is the number of ports. The port index you assign in each panel determines the matrix position:

- Port 1, Port 2 → S11, S12, S21, S22
- Port 3 → S31, S32, S13, S23, S33, …

Port indices don't have to be consecutive, but gaps will leave unused matrix entries. Keeping them 1, 2, 3, … is simplest.

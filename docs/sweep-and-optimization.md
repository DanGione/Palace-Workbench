# Parameter Sweep and Optimization

The Palace Workbench can automatically run multiple simulations while varying geometry parameters — either as a **sweep** (evaluating a fixed set of values) or as an **optimization** (using a scipy minimizer to find parameter values that best meet a target).

Open the sweep panel from **Palace → Configure Sweep / Optimize**.

---

## Core concepts

**Parameters** are geometry dimensions driven by a **VarSet** (see [Geometry Guide](geometry-guide.md#varsets-and-parametric-geometry)). Each parameter links to one property on a VarSet object. Before you can sweep or optimize a dimension, it must be a named VarSet property and the Sketch constraint must reference it.

**Objectives** define what you are trying to achieve — for example, "S11 at 2.4 GHz should be −30 dB". Objectives are only used in Optimize mode; sweeps run unconditionally.

---

## Sweep mode

A sweep runs Palace at every combination of parameter values you specify, then stores all results in `sweep.nc` for comparison.

### Configuring a sweep

1. Click **Add** to add a parameter row.
2. In the **VarSet** column, select the VarSet object that contains your dimension (e.g., `GeoVars`).
3. In the **Property** column, select the specific property (e.g., `trace_width`).
4. In the **Values** column, enter either:
   - Comma-separated values: `0.3, 0.5, 0.7, 1.0`
   - Linspace notation: `0.3:1.0:8` (8 evenly-spaced points from 0.3 to 1.0)

Repeat for as many parameters as you want. Palace evaluates all combinations (Cartesian product), so 3 values × 4 values = 12 simulations.

5. Click **Run Sweep / Optimize** in the toolbar.

The Sweep Results panel updates after each simulation so you can monitor progress.

---

## Optimize mode

Optimization uses a scipy minimizer to search the parameter space for values that minimise a weighted objective function.

### Configuring parameters

1. Click **Add** to add a parameter row. When you select a VarSet and Property, the **Min**, **Max**, and **Initial** columns are auto-filled from the property's current value:
   - **Initial** = current document value
   - **Min** = Initial − 50% of |Initial|
   - **Max** = Initial + 50% of |Initial|

   Adjust these to set the bounds the optimizer is allowed to explore. The optimizer will not go outside [Min, Max].

2. Repeat for each parameter you want to optimize.

### Configuring objectives

Each objective row defines one target for the optimizer to drive toward. The optimizer minimizes the sum: **Σ weight × (mean_S_param − target)²** over all objectives.

| Column | Description |
|---|---|
| **S-param** | Which S-parameter to target (e.g., S11, S21). |
| **Type** | `magnitude` (dB) or `phase` (°). |
| **Freq start / stop** | Frequency range (GHz) over which to average the S-parameter value. Use the same value for both to target a single frequency. |
| **Target** | The value you want to achieve (e.g., −30 for −30 dB). |
| **Weight** | Relative importance of this objective. Higher weight pulls the optimizer harder toward this target. Useful for balancing multiple objectives (e.g., S11 and S21 simultaneously). |

**Minimize vs. Maximize goal:** The **Goal** dropdown at the top switches the optimizer between minimizing the objective sum (default — drive toward target values) and maximizing it.

### Algorithm settings

| Field | Description |
|---|---|
| **Algorithm** | The scipy optimization algorithm to use. See below. |
| **Max iterations** | Maximum number of Palace simulations the optimizer may run. Each evaluation is one full simulation. |

#### Nelder-Mead (default)

A derivative-free simplex method. Robust and works well for 1–5 parameters with smooth objectives.

- **Simplex size tolerance (fraction of range):** Convergence criterion. The optimizer stops when the simplex has shrunk to less than this fraction of each parameter's range. Default `0.01` = 1%. Tighten (e.g., `0.001`) for more precise convergence; loosen (e.g., `0.05`) to stop earlier.

Nelder-Mead does not require gradient information and handles noisy objectives well. The initial simplex spans 50% of each parameter's range, so it explores widely even with tight bounds.

#### Differential Evolution

A population-based global optimizer. Much better than Nelder-Mead for finding global optima in rugged or multi-modal landscapes, but requires many more evaluations (typically 10× more simulations).

- **Population spread tolerance:** Convergence criterion based on how spread out the population is. Default `0.01`.

Use Differential Evolution when Nelder-Mead consistently gets stuck in local optima, or when you have more than 5 parameters.

#### COBYLA

A gradient-free constrained optimizer. Similar robustness to Nelder-Mead with slightly different behaviour on constrained problems.

- **Initial trust radius (rhobeg):** Controls the initial step size and final accuracy. Default `0.01` (1% of normalized range). Larger values explore more; smaller values converge more tightly.

### Running and monitoring

Click **Run Sweep / Optimize**. The Sweep Results panel updates after each evaluation. The **★ best** marker in the dropdown shows the iteration with the lowest objective value so far.

When optimization completes, Palace automatically applies the best parameter values to the document geometry, updating the model to the optimal dimensions.

### Reading results

- The parameter label at the bottom of the Sweep Results panel shows the exact parameter values and objective value for each evaluated point.
- The **★ best** entry in the dropdown marks the globally best result found.
- The final geometry in the FreeCAD document is set to the best parameter values.

---

## Tips

**Start with a coarse sweep to understand the landscape.** Before optimizing, run a sweep over a broad range to see how the objective changes with each parameter. This helps you set tighter initial bounds for the optimizer.

**Normalize weights by expected range.** If S11 typically ranges from 0 to −40 dB and S21 from 0 to −3 dB, a weight of 1 on both gives S11 much more influence (40²=1600 vs 3²=9). Scale weights to balance contributions: e.g., weight S21 by 100 and S11 by 1.

**Nelder-Mead can get stuck in local optima.** If results are unsatisfying, try re-running from a different initial point, or switch to Differential Evolution.

**Max iterations is the simulation count, not the algorithm step count.** Each iteration is one Palace simulation (possibly several seconds to several minutes). Budget accordingly.

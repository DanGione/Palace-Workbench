"""Commands for creating and running parameter sweeps and optimizations."""

import itertools
import json
import os
import queue
import shutil
import tempfile
import threading
import time

import FreeCAD
import FreeCADGui

try:
    from PySide2 import QtCore, QtWidgets
    from PySide2.QtCore import QObject, QTimer, Signal
except ImportError:
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtCore import QObject, QTimer, Signal

_SWEEP_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Results.svg")


class _IterationSignalEmitter(QObject):
    """Module-level emitter so the results panel can react to each completed iteration."""
    iteration_done = Signal(str)   # sweep_nc path

_iter_emitter = _IterationSignalEmitter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_out_dir(doc):
    """Return the base output directory for the active document."""
    from commands.cmd_run import _output_dir
    return _output_dir(doc)


def _sweep_nc_path(doc):
    return os.path.join(_base_out_dir(doc), "sweep.nc")


def _run_dir(doc, run_index):
    return os.path.join(_base_out_dir(doc), f"sweep_run_{run_index:03d}")


def _build_param_sets(sweep_obj):
    """Return list of {varset: str, property: str, value: float} dicts for each run.

    Returns (param_sets, coord_name, coord_values) where:
      param_sets   : list of list-of-dict (one inner list per run)
      coord_name   : str — sweep dimension name for append_sweep_point
      coord_values : list of scalar — one per run
    """
    params = json.loads(sweep_obj.SweepParams or "[]")
    if not params:
        return [], "run_index", []

    sweep_type = getattr(sweep_obj, "SweepType", "Grid")

    if sweep_type == "Sequential":
        # Zip: all lists must be the same length; iterate in lockstep
        length = len(params[0].get("values", []))
        sets = []
        for i in range(length):
            run = []
            for p in params:
                vals = p.get("values", [])
                run.append({"varset": p["varset"], "property": p["property"],
                             "value": vals[i] if i < len(vals) else vals[-1]})
            sets.append(run)
    else:
        # Grid: cartesian product
        value_lists = [p.get("values", []) for p in params]
        sets = []
        for combo in itertools.product(*value_lists):
            run = [{"varset": p["varset"], "property": p["property"], "value": v}
                   for p, v in zip(params, combo)]
            sets.append(run)

    # Naming: single param → use property name; multi → run_index
    if len(params) == 1:
        coord_name = params[0]["property"]
        coord_values = [run[0]["value"] for run in sets]
    else:
        coord_name = "run_index"
        coord_values = list(range(len(sets)))

    return sets, coord_name, coord_values


def _apply_param_set(doc, param_set):
    """Set VarSet properties for one run and recompute."""
    for p in param_set:
        varset_obj = doc.getObjectsByLabel(p["varset"])
        if not varset_obj:
            raise ValueError(f"VarSet object '{p['varset']}' not found in document.")
        varset_obj = varset_obj[0]
        setattr(varset_obj, p["property"], float(p["value"]))
    doc.recompute()


def _compute_objective(nc_path, objectives):
    """Return sum(weight * (mean_over_freq_range(val) - target)^2) for all objectives.

    Each objective dict: {row, col, type ("magnitude"|"phase"),
                          freq_start, freq_stop, target, weight}
    The result is dimensionless (squared weighted deviations), suitable as a
    scalar for any scipy minimizer regardless of mixed S-param units.
    """
    import numpy as np
    from palace.results_db import load_dataset
    ds = load_dataset(nc_path)
    freq = ds.coords["freq"].values
    total = 0.0
    for obj in objectives:
        f0 = float(obj.get("freq_start", obj.get("freq_ghz", 2.45)))
        f1 = float(obj.get("freq_stop",  obj.get("freq_ghz", f0)))
        mask = (freq >= f0) & (freq <= f1)
        if not np.any(mask):
            mask = np.zeros(len(freq), dtype=bool)
            mask[int(np.argmin(np.abs(freq - f0)))] = True
        ri, ci = int(obj["row"]) - 1, int(obj["col"]) - 1
        if obj.get("type", "magnitude") == "phase":
            vals = ds["S_phase"].values[mask, ri, ci]
        else:
            vals = ds["S_mag_db"].values[mask, ri, ci]
        mean_val = float(np.mean(vals))
        target   = float(obj.get("target", 0.0))
        weight   = float(obj.get("weight", 1.0))
        total += weight * (mean_val - target) ** 2
    ds.close()
    return total


# ---------------------------------------------------------------------------
# Sweep Coordinator
# ---------------------------------------------------------------------------

class _SweepCoordinator:
    """Runs a series of Palace simulations serially, appending results to sweep.nc."""

    def __init__(self, doc, sweep_obj, param_sets, coord_name, coord_values,
                 console=None, on_finished=None):
        self._doc = doc
        self._sweep_obj = sweep_obj
        self._param_sets = param_sets
        self._coord_name = coord_name
        self._coord_values = coord_values
        self._console = console
        self._on_finished = on_finished
        self._run_index = 0
        self._sweep_nc = _sweep_nc_path(doc)
        self._cancelled = False
        # Store original param values to restore on cancel
        self._originals = self._save_originals()

    def _save_originals(self):
        originals = []
        for p in (json.loads(self._sweep_obj.SweepParams or "[]")):
            objs = self._doc.getObjectsByLabel(p["varset"])
            if objs:
                originals.append({
                    "varset": p["varset"],
                    "property": p["property"],
                    "value": getattr(objs[0], p["property"]),
                })
        return originals

    def _restore_originals(self):
        for p in self._originals:
            objs = self._doc.getObjectsByLabel(p["varset"])
            if objs:
                setattr(objs[0], p["property"], p["value"])
        self._doc.recompute()

    def start(self):
        self._run_next()

    def cancel(self):
        self._cancelled = True
        # Stop any running coordinator
        from commands.cmd_run import CmdRun
        coord = CmdRun._coordinator
        if coord and coord.is_running():
            coord.stop()

    def _run_next(self):
        if self._cancelled:
            self._restore_originals()
            self._update_status("Cancelled")
            if self._console:
                self._console.set_status("Sweep cancelled")
            if self._on_finished:
                self._on_finished(False, None)
            return

        if self._run_index >= len(self._param_sets):
            self._restore_originals()
            self._update_status(f"Done — {self._run_index} runs complete")
            if self._console:
                self._console.set_status(f"Sweep done ({self._run_index}/{len(self._param_sets)})")
            if self._on_finished:
                self._on_finished(True, self._sweep_nc)
            return

        param_set = self._param_sets[self._run_index]
        coord_value = self._coord_values[self._run_index]
        n_total = len(self._param_sets)
        param_str = ", ".join(f"{p['property']}={p['value']}" for p in param_set)
        self._update_status(f"Run {self._run_index + 1}/{n_total}: {param_str}")
        if self._console:
            self._console.set_status(f"Sweep {self._run_index + 1}/{n_total} | {param_str}")

        try:
            _apply_param_set(self._doc, param_set)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace sweep: failed to apply params: {exc}\n")
            self._update_status(f"Error: {exc}")
            if self._on_finished:
                self._on_finished(False, None)
            return

        out_dir = _run_dir(self._doc, self._run_index)
        os.makedirs(out_dir, exist_ok=True)

        # Re-mesh and launch this run
        try:
            self._launch_run(out_dir, coord_value)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace sweep: launch failed: {exc}\n")
            self._update_status(f"Error: {exc}")
            if self._on_finished:
                self._on_finished(False, None)

    def _launch_run(self, out_dir, coord_value):
        from commands.cmd_run import _build_passes, _launch_passes, _fmt_elapsed
        from palace.meshing import generate_mesh

        doc = self._doc
        sim = next((o for o in doc.Objects if hasattr(o, "SimulationType")), None)

        def _log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")
            if self._console is not None:
                self._console.write(msg)

        def _mesh_log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")

        n_total = len(self._param_sets)
        param_str = ", ".join(f"{p['property']}={p['value']}" for p in self._param_sets[self._run_index])
        _log(f"[Sweep {self._run_index + 1}/{n_total}] {param_str}\n")

        tmp_dir = tempfile.mkdtemp(prefix="palace_sweep_")
        try:
            doc.recompute()
            t0_mesh = time.time()
            mesh_path = generate_mesh(doc, out_dir, log_fn=_mesh_log)
            mesh_elapsed = _fmt_elapsed(time.time() - t0_mesh)
            if sim:
                sim.MeshFile = mesh_path
            try:
                from commands.cmd_run import _update_mesh_object
                _update_mesh_object(doc, mesh_path)
            except Exception as exc:
                FreeCAD.Console.PrintWarning(f"Palace sweep: mesh display update failed: {exc}\n")

            passes, needs_merge = _build_passes(doc, out_dir, tmp_dir)

            binary = sim.PalaceBinary if sim else ""
            if not binary or not os.path.isfile(binary):
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise RuntimeError("Palace binary not configured — cannot run sweep.")

            num_procs = int(getattr(sim, "NumProcesses", 1)) if sim else 1
            num_threads = int(getattr(sim, "NumThreads", 0)) if sim else 0
            t0_sim = time.time()

            run_idx = self._run_index
            coord_name = self._coord_name

            def _on_run_complete(success, nc_path):
                if not success or not nc_path:
                    shutil.rmtree(out_dir, ignore_errors=True)
                    self._update_status(f"Run {run_idx + 1} failed")
                    if not self._cancelled and self._on_finished:
                        self._on_finished(False, None)
                    return
                try:
                    from palace.results_db import load_dataset, append_sweep_point
                    ds = load_dataset(nc_path)
                    append_sweep_point(self._sweep_nc, ds, coord_name, coord_value)
                    ds.close()
                    FreeCAD.Console.PrintMessage(
                        f"Palace sweep: appended run {run_idx + 1} → {self._sweep_nc}\n"
                    )
                    _annotate_sweep_run(self._sweep_nc, run_idx, self._param_sets[run_idx])
                    _iter_emitter.iteration_done.emit(str(self._sweep_nc))
                    shutil.rmtree(out_dir, ignore_errors=True)
                except Exception as exc:
                    FreeCAD.Console.PrintError(
                        f"Palace sweep: failed to append results: {exc}\n"
                    )

                self._run_index += 1
                QTimer.singleShot(0, self._run_next)

            _launch_passes(passes, needs_merge, out_dir, self._console, sim,
                           binary, num_procs, num_threads, t0_sim, mesh_elapsed,
                           tmp_dir=tmp_dir, on_complete=_on_run_complete,
                           initial_status=f"Sweep {self._run_index + 1}/{n_total} | {param_str}")

        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    def _update_status(self, msg):
        try:
            self._sweep_obj.SweepStatus = msg
        except Exception:
            pass
        FreeCAD.Console.PrintMessage(f"Palace sweep: {msg}\n")


def _annotate_sweep_run(sweep_nc, run_index, param_set, objective=None):
    """Store per-run parameter values (and optional objective) as dataset attributes."""
    try:
        import xarray as xr
        ds = xr.open_dataset(str(sweep_nc))
        ds.attrs[f"sweep_run_{run_index:03d}_params"] = json.dumps(
            {p["property"]: p["value"] for p in param_set}
        )
        if objective is not None:
            ds.attrs[f"sweep_run_{run_index:03d}_objective"] = float(objective)
        ds.to_netcdf(str(sweep_nc) + ".tmp", format="NETCDF4")
        ds.close()
        os.replace(str(sweep_nc) + ".tmp", str(sweep_nc))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Optimization coordinator
# ---------------------------------------------------------------------------

class _OptimizationCoordinator(QObject):
    """Runs scipy optimizer; each evaluation triggers a Palace simulation."""

    # Thread-safe signals: emitting from the optimizer thread queues the slot
    # on the main Qt event loop, replacing the unreliable QTimer.singleShot-from-thread pattern.
    _eval_requested = Signal()
    _done_signal    = Signal(bool)   # True = success, False = cancelled/failed

    def __init__(self, doc, sweep_obj, params, console=None, on_finished=None):
        super().__init__()
        self._doc = doc
        self._sweep_obj = sweep_obj
        self._params = params          # list of param dicts with min/max
        self._console = console
        self._on_finished = on_finished
        self._sweep_nc = _sweep_nc_path(doc)
        self._eval_index = 0
        self._last_val = None
        self._best_val = float('inf')
        self._best_params = None
        self._cancelled = False
        self._result_queue = queue.Queue()
        self._eval_event = threading.Event()
        self._pending_params = None
        self._pending_coord = None
        self._originals = self._save_originals()
        self._eval_requested.connect(self._trigger_eval, QtCore.Qt.QueuedConnection)
        self._done_signal.connect(self._finish, QtCore.Qt.QueuedConnection)

    def _save_originals(self):
        originals = []
        for p in self._params:
            objs = self._doc.getObjectsByLabel(p["varset"])
            if objs:
                originals.append({
                    "varset": p["varset"],
                    "property": p["property"],
                    "value": getattr(objs[0], p["property"]),
                })
        return originals

    def _restore_originals(self):
        for p in self._originals:
            objs = self._doc.getObjectsByLabel(p["varset"])
            if objs:
                setattr(objs[0], p["property"], p["value"])
        self._doc.recompute()

    def start(self):
        """Start the scipy optimizer in a background thread."""
        t = threading.Thread(target=self._optimizer_thread, daemon=True)
        t.start()

    def cancel(self):
        self._cancelled = True
        self._eval_event.set()   # Unblock any waiting evaluation
        from commands.cmd_run import CmdRun
        coord = CmdRun._coordinator
        if coord and coord.is_running():
            coord.stop()

    def _optimizer_thread(self):
        try:
            import scipy.optimize as opt
            import numpy as np
        except ImportError:
            FreeCAD.Console.PrintError(
                "Palace optimize: scipy is required. Install with: pip install scipy\n"
            )
            self._done_signal.emit(False)
            return

        lo_arr = [p.get("min", 0.0) for p in self._params]
        hi_arr = [p.get("max", 1.0) for p in self._params]
        ranges = [hi - lo for lo, hi in zip(lo_arr, hi_arr)]

        # Normalize parameters to [0, 1] so xatol is a universal fraction of each
        # parameter's range, independent of units (mm, GHz, Ω, etc.).
        # Fixed parameters (lo == hi, r == 0) are pinned to bounds (0, 0) so scipy
        # never varies them; denormalization lo + xn*0 = lo handles them correctly.
        x0_norm = [(p.get("initial", 0.5 * (lo + hi)) - lo) / r if r != 0 else 0.0
                   for p, lo, hi, r in zip(self._params, lo_arr, hi_arr, ranges)]
        bounds_norm = [(0.0, 1.0) if r != 0 else (0.0, 0.0) for r in ranges]

        maximize = getattr(self._sweep_obj, "ObjectiveGoal", "Minimize") == "Maximize"
        sign = -1.0 if maximize else 1.0
        algorithm = getattr(self._sweep_obj, "OptAlgorithm", "Nelder-Mead")
        max_iter   = int(getattr(self._sweep_obj, "OptMaxIter",   50))
        nm_xatol   = float(getattr(self._sweep_obj, "NMXatol",    1e-4))
        cobyla_tol = float(getattr(self._sweep_obj, "COBYLATol",  0.01))
        de_tol     = float(getattr(self._sweep_obj, "DETol",      0.01))

        def objective(x_norm):
            if self._cancelled:
                raise StopIteration("Cancelled")
            x_actual = [lo + xn * r for xn, lo, r in zip(x_norm, lo_arr, ranges)]
            param_set = [{"varset": p["varset"], "property": p["property"], "value": float(v)}
                         for p, v in zip(self._params, x_actual)]
            # Request a simulation on the main thread via a queued signal.
            # QTimer.singleShot from a plain Python thread is unreliable; a Signal
            # emitted from any thread is guaranteed to queue into the main event loop.
            self._pending_params = param_set
            self._pending_coord = self._eval_index
            self._eval_event.clear()
            self._eval_requested.emit()
            self._eval_event.wait()   # Block until simulation completes
            result = self._result_queue.get()
            if result is None:
                raise RuntimeError("Simulation failed during optimization")
            return sign * result

        try:
            if algorithm == "Differential Evolution":
                opt.differential_evolution(
                    objective, bounds_norm,
                    maxiter=max_iter, tol=de_tol, seed=42,
                )
            elif algorithm == "COBYLA":
                opt.minimize(objective, x0_norm, method="COBYLA",
                             bounds=bounds_norm,
                             options={"maxiter": max_iter, "rhobeg": cobyla_tol})
            else:  # Nelder-Mead
                # Build initial simplex spanning 50% of each parameter's normalized range.
                # scipy's default (nonzdelt=0.05) perturbs x0 by only ~5% of each coordinate
                # value, giving a tiny initial simplex regardless of how wide the bounds are.
                _step = 0.5
                _n = len(x0_norm)
                _np_x0 = np.array(x0_norm, dtype=float)
                _sim = np.empty((_n + 1, _n), dtype=float)
                _sim[0] = _np_x0
                for _i in range(_n):
                    _v = _np_x0.copy()
                    if bounds_norm[_i][1] > bounds_norm[_i][0]:  # non-fixed param
                        _v[_i] = (_v[_i] + _step) if (_v[_i] + _step <= 1.0) else (_v[_i] - _step)
                    _sim[_i + 1] = _v
                opt.minimize(objective, x0_norm, method="Nelder-Mead",
                             bounds=bounds_norm,
                             options={"maxfev": max_iter, "xatol": nm_xatol, "fatol": float("inf"),
                                      "adaptive": True, "initial_simplex": _sim})
        except StopIteration:
            self._done_signal.emit(False)  # cancellation only
            return
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace optimize: {exc}\n")

        self._done_signal.emit(True)

    def _trigger_eval(self):
        """Called on the main thread to run one Palace simulation."""
        if self._cancelled:
            self._result_queue.put(None)
            self._eval_event.set()
            return

        param_set = self._pending_params
        eval_idx = self._eval_index
        self._eval_index += 1

        param_str = ", ".join(f"{p['property']}={p['value']:.4g}" for p in param_set)
        if self._last_val is not None:
            header = f"Iter {eval_idx + 1} | {param_str} (prev optim: {self._last_val:.4f})"
            prev_line = f"  Previous optim: {self._last_val:.4f}\n"
        else:
            header = f"Iter {eval_idx + 1} | {param_str}"
            prev_line = ""
        self._update_status(f"Iter {eval_idx + 1}: {param_str}")
        if self._console:
            self._console.set_status(header)
            self._console.write(f"[Optimize Iter {eval_idx + 1}] {param_str}\n")
            if prev_line:
                self._console.write(prev_line)

        try:
            _apply_param_set(self._doc, param_set)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace optimize: param apply failed: {exc}\n")
            self._result_queue.put(None)
            self._eval_event.set()
            return

        out_dir = _run_dir(self._doc, eval_idx)
        os.makedirs(out_dir, exist_ok=True)

        try:
            self._launch_eval(out_dir, eval_idx, param_set)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace optimize: launch failed: {exc}\n")
            self._result_queue.put(None)
            self._eval_event.set()

    def _launch_eval(self, out_dir, eval_idx, param_set):
        from commands.cmd_run import _build_passes, _launch_passes, _fmt_elapsed
        from palace.meshing import generate_mesh

        doc = self._doc
        sim = next((o for o in doc.Objects if hasattr(o, "SimulationType")), None)

        def _log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")
            if self._console is not None:
                self._console.write(msg)

        def _mesh_log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")

        param_str = ", ".join(f"{p['property']}={p['value']:.4g}" for p in param_set)
        if self._last_val is not None:
            initial_status = f"Iter {eval_idx + 1} | {param_str} (prev optim: {self._last_val:.4f})"
        else:
            initial_status = f"Iter {eval_idx + 1} | {param_str}"

        tmp_dir = tempfile.mkdtemp(prefix="palace_opt_")
        try:
            doc.recompute()
            t0_mesh = time.time()
            mesh_path = generate_mesh(doc, out_dir, log_fn=_mesh_log)
            mesh_elapsed = _fmt_elapsed(time.time() - t0_mesh)
            if sim:
                sim.MeshFile = mesh_path
            try:
                from commands.cmd_run import _update_mesh_object
                _update_mesh_object(doc, mesh_path)
            except Exception as exc:
                FreeCAD.Console.PrintWarning(f"Palace optimize: mesh display update failed: {exc}\n")

            passes, needs_merge = _build_passes(doc, out_dir, tmp_dir)
            binary = sim.PalaceBinary if sim else ""
            if not binary or not os.path.isfile(binary):
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise RuntimeError("Palace binary not configured.")

            num_procs = int(getattr(sim, "NumProcesses", 1)) if sim else 1
            num_threads = int(getattr(sim, "NumThreads", 0)) if sim else 0
            t0_sim = time.time()
            sweep_nc = self._sweep_nc
            objectives = json.loads(getattr(self._sweep_obj, "ObjectiveList", "[]") or "[]")
            if not objectives:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                FreeCAD.Console.PrintError("Palace optimize: no objectives configured.\n")
                result_queue = self._result_queue
                eval_event = self._eval_event
                result_queue.put(None)
                eval_event.set()
                return
            result_queue = self._result_queue
            eval_event = self._eval_event

            def _on_run_complete(success, nc_path):
                if not success or not nc_path:
                    shutil.rmtree(out_dir, ignore_errors=True)
                    result_queue.put(None)
                    eval_event.set()
                    return
                try:
                    val = _compute_objective(nc_path, objectives)
                    from palace.results_db import load_dataset, append_sweep_point
                    ds = load_dataset(nc_path)
                    append_sweep_point(sweep_nc, ds, "eval_index", eval_idx)
                    ds.close()
                    _annotate_sweep_run(sweep_nc, eval_idx, param_set, val)
                    _iter_emitter.iteration_done.emit(str(sweep_nc))
                    shutil.rmtree(out_dir, ignore_errors=True)
                    self._last_val = val
                    if val < self._best_val:
                        self._best_val = val
                        self._best_params = list(param_set)
                    if self._console:
                        self._console.set_status(
                            f"Iter {eval_idx + 1} | {param_str} → optim: {val:.4f}"
                        )
                        self._console.write(
                            f"  Optim (weighted Σ(w·Δ²)): {val:.4f}\n"
                        )
                    result_queue.put(val)
                except Exception as exc:
                    FreeCAD.Console.PrintError(f"Palace optimize: result extraction failed: {exc}\n")
                    result_queue.put(None)
                finally:
                    eval_event.set()

            _launch_passes(passes, needs_merge, out_dir, self._console, sim,
                           binary, num_procs, num_threads, t0_sim, mesh_elapsed,
                           tmp_dir=tmp_dir, on_complete=_on_run_complete,
                           initial_status=initial_status)

        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    def _finish(self, success):
        if success and self._best_params is not None:
            _apply_param_set(self._doc, self._best_params)
            best_str = ", ".join(
                f"{p['property']}={p['value']:.4g}" for p in self._best_params
            )
            self._update_status("Optimization complete")
            if self._console:
                self._console.set_status(f"Optimization complete | best: {best_str}")
                self._console.write(
                    f"[Optimization complete] Best optim value: {self._best_val:.4f}\n"
                    f"  Best parameters: {best_str}\n"
                    f"  Geometry updated to optimal values.\n"
                )
        else:
            self._restore_originals()
            status = "Optimization cancelled" if not success else "Optimization complete"
            self._update_status(status)
            if self._console:
                self._console.set_status(status)
        if self._on_finished:
            self._on_finished(success, self._sweep_nc if success else None)

    def _update_status(self, msg):
        try:
            self._sweep_obj.SweepStatus = msg
        except Exception:
            pass
        FreeCAD.Console.PrintMessage(f"Palace optimize: {msg}\n")


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_active_coordinator = None   # _SweepCoordinator or _OptimizationCoordinator


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class CmdSweep:
    """Create a PalaceSweep object and open its editor panel."""

    def GetResources(self):
        return {
            "Pixmap": _SWEEP_ICON,
            "MenuText": "New Parameter Sweep",
            "ToolTip": "Create a parameter sweep / optimization object.",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        return any(hasattr(o, "SimulationType") for o in doc.Objects)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return
        from features import find_sweep
        from features.sweep import create_sweep
        from panels.sweep_panel import SweepPanel
        from panels import show_palace_panel

        sweep_obj = find_sweep(doc)
        if sweep_obj is None:
            sweep_obj = create_sweep(doc)
        show_palace_panel(SweepPanel(sweep_obj))


class CmdRunSweep:
    """Execute the parameter sweep or optimization defined in the PalaceSweep object."""

    def GetResources(self):
        return {
            "Pixmap": _SWEEP_ICON,
            "MenuText": "Run Sweep / Optimize",
            "ToolTip": "Run the configured parameter sweep or geometry optimization.",
        }

    def IsActive(self):
        global _active_coordinator
        if _active_coordinator is not None:
            return False
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        from features import find_sweep, find_simulation
        return find_sweep(doc) is not None and find_simulation(doc) is not None

    def Activated(self):
        global _active_coordinator
        doc = FreeCAD.ActiveDocument
        if not doc:
            return

        from features import find_sweep
        sweep_obj = find_sweep(doc)
        if sweep_obj is None:
            return

        console = None
        try:
            from panels.palace_console import PalaceConsole
            console = PalaceConsole.get_or_create()
            console.clear()
            console.set_status("Starting sweep…")
        except Exception:
            pass

        mode = getattr(sweep_obj, "SweepMode", "Sweep")

        def _on_finished(success, sweep_nc):
            global _active_coordinator
            _active_coordinator = None
            if success and sweep_nc and os.path.isfile(sweep_nc):
                try:
                    from panels.sweep_results_panel import SweepResultsPanel
                    viewer = SweepResultsPanel.get_or_create()
                    viewer.load_nc(sweep_nc)
                except Exception as exc:
                    FreeCAD.Console.PrintError(
                        f"Palace sweep: could not open results viewer: {exc}\n"
                    )
                QtWidgets.QMessageBox.information(
                    None, "Sweep Complete",
                    f"Sweep finished.\nResults: {sweep_nc}"
                )
            elif not success:
                QtWidgets.QMessageBox.warning(
                    None, "Sweep Failed",
                    "The sweep did not complete successfully.\n"
                    "Check the FreeCAD console for details."
                )

        sweep_nc_path = _sweep_nc_path(doc)
        sweep_obj.SweepResultsFile = sweep_nc_path
        # Remove any stale sweep.nc from a previous run so results don't accumulate
        # across separate runs or carry over incompatible sweep coordinates.
        try:
            if os.path.isfile(sweep_nc_path):
                os.remove(sweep_nc_path)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"Palace sweep: could not clear old sweep.nc: {exc}\n")

        if mode == "Optimize":
            params = json.loads(sweep_obj.SweepParams or "[]")
            if not params:
                QtWidgets.QMessageBox.warning(
                    None, "No Parameters",
                    "No sweep parameters configured.\nOpen the sweep editor to add parameters."
                )
                return
            coord = _OptimizationCoordinator(
                doc, sweep_obj, params, console=console, on_finished=_on_finished
            )
            _active_coordinator = coord
            coord.start()
        else:
            param_sets, coord_name, coord_values = _build_param_sets(sweep_obj)
            if not param_sets:
                QtWidgets.QMessageBox.warning(
                    None, "No Runs",
                    "No parameter values configured.\nOpen the sweep editor to add values."
                )
                return
            coord = _SweepCoordinator(
                doc, sweep_obj, param_sets, coord_name, coord_values,
                console=console, on_finished=_on_finished,
            )
            _active_coordinator = coord
            coord.start()


class CmdStopSweep:
    """Cancel the running sweep or optimization."""

    def GetResources(self):
        return {
            "Pixmap": os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Stop.svg"),
            "MenuText": "Stop Sweep",
            "ToolTip": "Cancel the running parameter sweep or optimization.",
        }

    def IsActive(self):
        return _active_coordinator is not None

    def Activated(self):
        global _active_coordinator
        if _active_coordinator is not None:
            _active_coordinator.cancel()
            _active_coordinator = None


class CmdViewSweep:
    """Open the sweep results viewer."""

    def GetResources(self):
        return {
            "Pixmap": _SWEEP_ICON,
            "MenuText": "View Sweep Results",
            "ToolTip": "Open the sweep results viewer to plot S-parameters across sweep points.",
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        from panels.sweep_results_panel import SweepResultsPanel
        viewer = SweepResultsPanel.get_or_create()

        # Auto-load from sweep object or default path
        from features import find_sweep
        sweep_obj = find_sweep(doc) if doc else None
        nc_path = None
        if sweep_obj:
            nc_path = getattr(sweep_obj, "SweepResultsFile", "") or _sweep_nc_path(doc)
        elif doc:
            nc_path = _sweep_nc_path(doc)

        if nc_path and os.path.isfile(nc_path):
            viewer.load_nc(nc_path)

import csv
import FreeCAD
import FreeCADGui
import json
import os
import time

try:
    from PySide2 import QtWidgets
    from PySide2.QtCore import QObject, QThread, Signal
except ImportError:
    from PySide6 import QtWidgets
    from PySide6.QtCore import QObject, QThread, Signal

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Run.svg")


def _fmt_elapsed(secs):
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s"


def _read_freq_count(csv_path):
    """Return the number of data rows in a port-S.csv, or None if unreadable."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader)  # skip header
            return sum(1 for row in reader if any(v.strip() for v in row))
    except Exception:
        return None


def _expected_freq_count(config_path):
    """Compute expected output row count from the Palace JSON config.

    Returns None (skip count check) for non-Driven configs or any parse error.
    Handles both the standard MinFreq/MaxFreq/FreqStep sweep and the Samples array.
    Adaptive sweep (AdaptiveTol) does not affect the output point count — Palace
    always writes results at every specified frequency point regardless.

    For Samples, each entry contributes:
      - log / explicit-count entry (NumSamples present): NumSamples points
      - linear entry (FreqStep present): round((MaxFreq-MinFreq)/FreqStep) + 1 points
      - point entry (MinFreq==MaxFreq, NumSamples=1): 1 point (covered by NumSamples branch)
    """
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        driven = cfg.get("Solver", {}).get("Driven", {})
        if not driven:
            return None
        if "Samples" in driven:
            total = 0
            for s in driven["Samples"]:
                t = s.get("Type", "").lower()
                if t == "point":
                    freq = s.get("Freq", [])
                    total += len(freq) if isinstance(freq, list) else 1
                elif t == "log":
                    n_samp = s.get("NSample")
                    if n_samp is None:
                        return None
                    total += int(n_samp)
                elif t == "linear":
                    f_min  = s.get("MinFreq")
                    f_max  = s.get("MaxFreq")
                    f_step = s.get("FreqStep")
                    if None in (f_min, f_max, f_step) or f_step <= 0:
                        return None
                    total += round((f_max - f_min) / f_step) + 1
                else:
                    return None  # unrecognised Type
            return total if total > 0 else None
        # Standard linear sweep
        f_min  = driven.get("MinFreq")
        f_max  = driven.get("MaxFreq")
        f_step = driven.get("FreqStep")
        if None in (f_min, f_max, f_step) or f_step <= 0:
            return None
        return round((f_max - f_min) / f_step) + 1
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-pass worker — one QThread per Palace excitation pass
# ---------------------------------------------------------------------------

class _PassWorker(QThread):
    """Runs a single Palace pass in a background thread.

    Signals
    -------
    log(str)
        Each line of Palace stdout output.
    pass_done(int pass_index, bool success, str message)
        Emitted when the pass completes (success or failure).
    """

    log       = Signal(str)
    pass_done = Signal(int, bool, str)

    def __init__(self, pass_index, config_path, pass_dir, binary,
                 num_procs=1, num_threads=0, parent=None):
        super().__init__(parent)
        self._pass_index  = pass_index
        self._config_path = config_path
        self._pass_dir    = pass_dir
        self._binary      = binary
        self._num_procs   = num_procs
        self._num_threads = num_threads
        self._proc        = None
        self._cancelled   = False

    def stop(self):
        """Kill the Palace process group (all MPI children too)."""
        self._cancelled = True
        proc = self._proc
        if proc is None:
            return
        try:
            import signal as _signal
            os.killpg(proc.pid, _signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        from palace.runner import run_palace

        def _store_proc(p):
            self._proc = p

        expected = _expected_freq_count(self._config_path)

        for attempt in range(2):
            rc = run_palace(
                self._config_path, self._binary,
                num_procs=self._num_procs,
                num_threads=self._num_threads,
                line_callback=self.log.emit,
                proc_callback=_store_proc,
            )
            self._proc = None

            if self._cancelled:
                self.log.emit("Palace: *** SIMULATION ABORTED — process killed ***\n")
                self.pass_done.emit(self._pass_index, False, "Simulation cancelled by user")
                return

            if rc != 0:
                self.pass_done.emit(
                    self._pass_index, False, f"Palace exited with code {rc}"
                )
                return

            if expected is None:
                break  # non-Driven or complex sweep — skip frequency check

            out_csv = os.path.join(self._pass_dir, "port-S.csv")
            n_rows  = _read_freq_count(out_csv)

            if n_rows is None:
                msg = "port-S.csv not found or unreadable"
                if attempt == 0:
                    self.log.emit(f"Palace: Warning: {msg} — retrying…\n")
                    continue
                self.pass_done.emit(
                    self._pass_index, False,
                    f"Frequency check failed: {msg} (after retry)"
                )
                return

            if n_rows != expected:
                msg = f"got {n_rows} frequency points, expected {expected}"
                if attempt == 0:
                    self.log.emit(
                        f"Palace: Warning: frequency count mismatch — {msg}. Retrying…\n"
                    )
                    continue
                self.pass_done.emit(
                    self._pass_index, False,
                    f"Frequency count mismatch: {msg} (after retry)"
                )
                return

            break  # all checks passed

        self.pass_done.emit(self._pass_index, True, "OK")


# ---------------------------------------------------------------------------
# Coordinator — lives on the main thread, manages N PassWorkers
# ---------------------------------------------------------------------------

class _SimCoordinator(QObject):
    """Manages N _PassWorkers and emits all_done when every pass has finished.

    Parameters
    ----------
    workers  : list[_PassWorker]    — one per pass, same order as passes
    parallel : bool                 — True = start all at once; False = chain serially
    passes   : list of (cfg, dir, port_idx)  — matching workers list
    labels   : dict[int, str]       — pass_index → display label ("Palace" / "Port N")
    console  : PalaceConsole | None — for writing orchestration messages to Main tab
    """

    all_done = Signal(bool, str)   # (overall_success, summary_message)

    def __init__(self, workers, parallel, passes, labels, console, parent=None):
        super().__init__(parent)
        self._workers       = workers
        self._parallel      = parallel
        self._passes        = passes
        self._labels        = labels
        self._console       = console
        self._pending       = len(workers)
        self._any_failed    = False
        self._any_cancelled = False
        self._next_idx      = 0          # index of next worker to start (serial mode)
        self._start_times   = {}         # pass_index → float (epoch seconds)

    def start_all(self):
        for w in self._workers:
            w.pass_done.connect(self._on_pass_done)

        if self._parallel:
            for i, w in enumerate(self._workers):
                self._start_times[i] = time.time()
                self._write_main(f"[{self._labels[i]}] Starting…\n")
                w.start()
        else:
            # Serial: start only the first worker; chain from _on_pass_done
            if self._workers:
                self._next_idx = 1
                self._start_times[0] = time.time()
                self._write_main(f"[{self._labels[0]}] Starting…\n")
                self._workers[0].start()

    def is_running(self):
        return any(w.isRunning() for w in self._workers)

    def stop(self):
        for w in self._workers:
            if w.isRunning():
                w.stop()

    def _write_main(self, text):
        if self._console is not None:
            self._console.write(text)

    def _on_pass_done(self, pass_index, ok, msg):
        label   = self._labels.get(pass_index, f"Pass {pass_index + 1}")
        elapsed = _fmt_elapsed(
            time.time() - self._start_times.get(pass_index, time.time())
        )

        if "cancelled" in msg.lower():
            self._any_cancelled = True
            self._write_main(f"[{label}] CANCELLED\n")
        elif ok:
            self._write_main(f"[{label}] Completed ({elapsed})\n")
        else:
            self._any_failed = True
            self._write_main(f"[{label}] FAILED: {msg}\n")

        # In serial mode, start the next queued worker (unless cancelled)
        if (not self._parallel
                and not self._any_cancelled
                and self._next_idx < len(self._workers)):
            next_w = self._workers[self._next_idx]
            next_label = self._labels.get(self._next_idx, f"Pass {self._next_idx + 1}")
            self._start_times[self._next_idx] = time.time()
            self._write_main(f"[{next_label}] Starting…\n")
            self._next_idx += 1
            next_w.start()

        self._pending -= 1
        if self._pending == 0:
            n = len(self._workers)
            if self._any_cancelled:
                self.all_done.emit(False, "Simulation cancelled by user")
            elif self._any_failed:
                self.all_done.emit(
                    False, "Simulation failed — check individual port tabs for details"
                )
            else:
                self.all_done.emit(
                    True, f"Simulation complete ({n} pass{'es' if n > 1 else ''})"
                )


# ---------------------------------------------------------------------------
# Helpers shared by CmdRun and CmdRunOnly
# ---------------------------------------------------------------------------

def _port_face_normal(port_obj):
    """Return the unit normal of the first port face as a (x,y,z) tuple.

    Falls back to (0,0,1) if the face cannot be read (e.g. no Part module).
    """
    try:
        faces = getattr(port_obj, "PortFaces", None)
        if not faces:
            return (0.0, 0.0, 1.0)
        link_obj, subs = faces[0]
        if not subs:
            return (0.0, 0.0, 1.0)
        face = link_obj.Shape.getElement(subs[0])
        pr   = face.ParameterRange
        u    = (pr[0] + pr[1]) / 2.0
        v    = (pr[2] + pr[3]) / 2.0
        n    = face.normalAt(u, v)
        mag  = (n.x**2 + n.y**2 + n.z**2) ** 0.5
        if mag < 1e-10:
            return (0.0, 0.0, 1.0)
        return (n.x / mag, n.y / mag, n.z / mag)
    except Exception:
        return (0.0, 0.0, 1.0)


def _update_wave_port_impedances(doc, passes, out_dir):
    """Extract Z_c from probe fields and store on each WavePort.CharacteristicZ."""
    from features import find_wave_ports
    from palace.post_process import parse_probe_csv, compute_wave_port_impedance
    from palace.config import _probe_points_along_edge, _probe_grid_over_port_face

    wave_ports = find_wave_ports(doc)
    updated = 0

    for wp in wave_ports:
        pts           = _probe_points_along_edge(wp)
        n_probes      = len(pts)
        probe_indices = [1000 + wp.PortIndex * 10 + k for k in range(n_probes)]

        grid_pts, _  = _probe_grid_over_port_face(wp)
        grid_base    = 2000 + (wp.PortIndex - 1) * 200
        grid_indices = [grid_base + k for k in range(len(grid_pts))]

        if not grid_pts:
            continue

        # Only compute Z for the pass where this port was excited
        pass_dir = None
        for _, d, _ in passes:
            if f"port{wp.PortIndex}" in os.path.basename(d):
                pass_dir = d
                break
        if not pass_dir:
            continue

        e_path = os.path.join(pass_dir, "probe-E.csv")
        b_path = os.path.join(pass_dir, "probe-B.csv")
        if not os.path.isfile(e_path) or not os.path.isfile(b_path):
            FreeCAD.Console.PrintWarning(
                f"Palace: probe files not found for port {wp.PortIndex} in {pass_dir}\n"
            )
            continue

        try:
            _, e_data = parse_probe_csv(e_path)
            _, b_data = parse_probe_csv(b_path)
        except Exception as exc:
            FreeCAD.Console.PrintError(
                f"Palace: failed to parse probe CSVs for port {wp.PortIndex}: {exc}\n"
            )
            continue

        if probe_indices:
            missing_vline = [i for i in probe_indices if i not in e_data]
            if missing_vline:
                FreeCAD.Console.PrintWarning(
                    f"Palace: V-line probe indices {missing_vline} not found for port "
                    f"{wp.PortIndex}\n"
                )
                continue

        missing_grid = [i for i in grid_indices if i not in e_data or i not in b_data]
        if missing_grid:
            FreeCAD.Console.PrintWarning(
                f"Palace: Port {wp.PortIndex}: grid probes not found in output — "
                f"re-run simulation to extract CharacteristicZ\n"
            )
            continue

        port_normal = _port_face_normal(wp)
        z_c = compute_wave_port_impedance(
            e_data, b_data, probe_indices, pts, grid_indices, grid_pts, port_normal
        )
        if not z_c:
            continue

        import math
        z_c_mean = float(sum(abs(z) for z in z_c) / len(z_c))
        if math.isfinite(z_c_mean) and z_c_mean > 0:
            wp.CharacteristicZ = z_c_mean
            FreeCAD.Console.PrintMessage(
                f"Palace: Port {wp.PortIndex} CharacteristicZ = {z_c_mean:.2f} Ω\n"
            )
            updated += 1

    if updated:
        doc.recompute()


def _output_dir(doc):
    if doc.FileName:
        return os.path.join(os.path.dirname(doc.FileName), "palace_output")
    import tempfile
    return os.path.join(tempfile.gettempdir(), "palace_output")


def _find_excited_ports(doc):
    """Return all excited ports (lumped and wave), sorted by PortIndex."""
    ports = [
        o for o in doc.Objects
        if hasattr(o, "PortIndex") and hasattr(o, "Excitation") and o.Excitation
        and (
            (hasattr(o, "R") and hasattr(o, "C"))                     # lumped port
            or (hasattr(o, "NumModes") and not hasattr(o, "R"))        # wave port
        )
    ]
    return sorted(ports, key=lambda o: o.PortIndex)


def _clear_stale_csv(directory):
    """Delete *.csv files left by a previous Palace run.

    Palace's C++ std::filesystem::remove() can fail with EACCES on WSL2 when
    files were written by a previous multi-process MPI run.  Pre-deleting them
    from Python (as the file owner) avoids the crash.
    """
    import glob
    for path in glob.glob(os.path.join(directory, "*.csv")):
        try:
            os.remove(path)
        except OSError:
            pass


def _build_passes(doc, out_dir):
    """Generate Palace config file(s) and return (passes, needs_merge).

    passes      : list of (config_path, pass_output_dir, port_index_or_None)
    needs_merge : True when more than one pass was built (S-matrix merge required)

    All FreeCAD document access happens here, on the main thread, so the
    background workers receive only plain file paths.
    """
    from palace.config import generate_config

    excited_ports = _find_excited_ports(doc)

    if len(excited_ports) <= 1:
        pass_out_dir = os.path.join(out_dir, "output")
        os.makedirs(pass_out_dir, exist_ok=True)
        _clear_stale_csv(pass_out_dir)
        config_path = os.path.join(out_dir, "palace_config.json")
        cfg = generate_config(doc)
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        return [(config_path, pass_out_dir, None)], False

    # Multi-pass: one Palace run per excited port.
    passes = []
    for p in excited_ports:
        pass_out_dir = os.path.join(out_dir, f"output_port{p.PortIndex}")
        os.makedirs(pass_out_dir, exist_ok=True)
        _clear_stale_csv(pass_out_dir)
        config_path = os.path.join(out_dir, f"palace_config_port{p.PortIndex}.json")
        cfg = generate_config(
            doc,
            only_excitation=p.PortIndex,
            output_override=pass_out_dir + "/",
        )
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        passes.append((config_path, pass_out_dir, p.PortIndex))

    return passes, True


def _on_done(success, message, out_dir, passes, needs_merge,
             console=None, t0=None, mesh_elapsed=None):
    """Called on the main thread when all passes have completed."""
    CmdRun._coordinator = None  # allow a new run to start

    if not success:
        cancelled = "cancelled" in message.lower()
        if cancelled:
            FreeCAD.Console.PrintError(
                "Palace: *** SIMULATION ABORTED BY USER — Palace process was killed ***\n"
            )
        else:
            FreeCAD.Console.PrintError(f"Palace: {message}\n")
        if console is not None:
            console.set_status("Cancelled" if cancelled else f"Failed: {message}")
        if not cancelled:
            QtWidgets.QMessageBox.critical(None, "Palace Error", message)
        return

    FreeCAD.Console.PrintMessage(f"Palace: {message}\n")

    sim_elapsed = _fmt_elapsed(time.time() - t0) if t0 is not None else None
    if mesh_elapsed is not None:
        FreeCAD.Console.PrintMessage(f"Palace: Mesh time:       {mesh_elapsed}\n")
    if sim_elapsed is not None:
        FreeCAD.Console.PrintMessage(f"Palace: Simulation time: {sim_elapsed}\n")

    final_csv = os.path.join(out_dir, "output", "port-S.csv")

    if needs_merge:
        if console is not None:
            console.write("[Post] Merging S-matrix…\n")
        try:
            from palace.post_process import merge_s_matrix
            pass_dirs = [d for _, d, _ in passes]
            os.makedirs(os.path.dirname(final_csv), exist_ok=True)
            merge_s_matrix(pass_dirs, final_csv)
            FreeCAD.Console.PrintMessage(f"Palace: Combined S-matrix → {final_csv}\n")
            if console is not None:
                console.write(f"[Post] Combined S-matrix → {final_csv}\n")
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace: S-matrix merge failed: {exc}\n")
            QtWidgets.QMessageBox.warning(
                None,
                "Palace Warning",
                f"Simulation finished but S-matrix merge failed:\n{exc}",
            )
            return

    # Extract characteristic impedance from field probes (wave ports with IntegrationEdge)
    try:
        import FreeCAD as _FC
        doc = _FC.ActiveDocument
        if doc is not None:
            _update_wave_port_impedances(doc, passes, out_dir)
    except Exception as exc:
        FreeCAD.Console.PrintError(f"Palace: Z_c extraction failed: {exc}\n")

    # Write Touchstone alongside the final CSV (single or multi-pass)
    ts_path = None
    if os.path.isfile(final_csv):
        if console is not None:
            console.write("[Post] Writing Touchstone…\n")
        try:
            from palace.post_process import write_touchstone
            ts_path = write_touchstone(final_csv)
            FreeCAD.Console.PrintMessage(f"Palace: Touchstone file  → {ts_path}\n")
            if console is not None:
                console.write(f"[Done] Output: {ts_path}\n")
        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace: Touchstone write failed: {exc}\n")

    if console is not None:
        console.set_status(f"Done ({sim_elapsed})" if sim_elapsed else "Done")

    # Auto-open the S-parameter viewer
    try:
        from panels.s_param_panel import SParamPanel
        viewer = SParamPanel.get_or_create()
        viewer.load_csv(final_csv)
    except Exception:
        pass

    detail = f"S-matrix CSV:   {final_csv}"
    if ts_path:
        detail += f"\nTouchstone:     {ts_path}"
    if mesh_elapsed:
        detail += f"\nMesh time:       {mesh_elapsed}"
    if sim_elapsed:
        detail += f"\nSimulation time: {sim_elapsed}"
    QtWidgets.QMessageBox.information(None, "Palace Complete", f"{message}\n\n{detail}")


def _find_mesh_file(doc):
    """Return path to an existing mesh file, or None.

    Checks PalaceMesh first (canonical), then SimulationContainer (legacy).
    """
    from features import find_palace_mesh, find_simulation
    mesh_obj = find_palace_mesh(doc)
    if mesh_obj and getattr(mesh_obj, "MeshFile", "") and os.path.isfile(mesh_obj.MeshFile):
        return mesh_obj.MeshFile
    sim = find_simulation(doc)
    if sim and getattr(sim, "MeshFile", "") and os.path.isfile(sim.MeshFile):
        return sim.MeshFile
    return None


def _launch_passes(passes, needs_merge, out_dir, console, sim, binary,
                   num_procs, num_threads, t0_sim, mesh_elapsed=None):
    """Create _PassWorker objects, set up console tabs, and start the coordinator.

    Must be called on the main thread.  Sets CmdRun._coordinator.
    """
    parallel = bool(getattr(sim, "ParallelPasses", True)) if sim else True
    n = len(passes)

    # Build per-pass tab labels
    if n == 1:
        labels = {0: "Palace"}
    else:
        labels = {}
        for i, (_, _, pidx) in enumerate(passes):
            labels[i] = f"Port {pidx}" if pidx is not None else f"Pass {i + 1}"

    # Set up console tabs
    if console is not None:
        console.clear_port_tabs()
        for i, label in labels.items():
            console.add_port_tab(i, label)
        mode_str = "parallel" if parallel else "serial"
        console.write(
            f"[Palace] Starting {n} pass{'es' if n > 1 else ''} ({mode_str})…\n"
        )
        console.set_status("Running Palace…")

    # Create workers
    workers = []
    for i, (config_path, pass_dir, _) in enumerate(passes):
        w = _PassWorker(i, config_path, pass_dir, binary, num_procs, num_threads)
        if console is not None:
            # Capture i in default arg to avoid late-binding closure
            w.log.connect(lambda line, idx=i: console.write_port(idx, line))
        workers.append(w)

    # Create coordinator and start
    coordinator = _SimCoordinator(workers, parallel, passes, labels, console)
    coordinator.all_done.connect(
        lambda ok, msg: _on_done(
            ok, msg, out_dir, passes, needs_merge, console, t0_sim, mesh_elapsed
        )
    )
    coordinator.start_all()
    CmdRun._coordinator = coordinator


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

_STOP_ICON    = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Stop.svg")
_RUN_ONLY_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Run.svg")


class CmdStopRun:
    def GetResources(self):
        return {
            "Pixmap": _STOP_ICON,
            "MenuText": "Stop Simulation",
            "ToolTip": "Terminate the Palace simulation that is currently running.",
        }

    def IsActive(self):
        return CmdRun._coordinator is not None and CmdRun._coordinator.is_running()

    def Activated(self):
        coord = CmdRun._coordinator
        if coord is None or not coord.is_running():
            return
        reply = QtWidgets.QMessageBox.question(
            None,
            "Stop Simulation",
            "Terminate the running Palace simulation?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            coord.stop()
            FreeCAD.Console.PrintMessage(
                "Palace: Stop requested — waiting for process(es) to exit…\n"
            )


class CmdRunOnly:
    """Run the Palace solver using an existing mesh file — no remeshing."""

    def GetResources(self):
        return {
            "Pixmap": _RUN_ONLY_ICON,
            "MenuText": "Run Simulation",
            "ToolTip": (
                "Run Palace using the existing mesh file.\n"
                "Generate Mesh (or Generate && Run) must have been run first."
            ),
        }

    def IsActive(self):
        if CmdRun._coordinator is not None and CmdRun._coordinator.is_running():
            return False
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        return bool(_find_mesh_file(doc))

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        mesh_path = _find_mesh_file(doc)
        if not mesh_path:
            QtWidgets.QMessageBox.warning(
                None,
                "No Mesh Found",
                "No mesh file found.\n\n"
                "Use 'Generate Mesh' or 'Generate && Run' first.",
            )
            return

        out_dir = _output_dir(doc)
        os.makedirs(out_dir, exist_ok=True)
        sim = next((o for o in doc.Objects if hasattr(o, "SimulationType")), None)

        if sim:
            sim.MeshFile = mesh_path

        console = None
        try:
            from panels.palace_console import PalaceConsole
            console = PalaceConsole.get_or_create()
            console.clear()
            console.set_status("Running Palace…")
        except Exception:
            pass

        def _log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")
            if console is not None:
                console.write(msg)

        try:
            _log(f"Palace: Using existing mesh: {mesh_path}\n")
            passes, needs_merge = _build_passes(doc, out_dir)
            n = len(passes)
            _log(f"Palace: {n} excitation pass{'es' if n > 1 else ''} prepared.\n")

            binary = sim.PalaceBinary if sim else ""
            if not binary or not os.path.isfile(binary):
                if console is not None:
                    console.set_status("No binary configured")
                QtWidgets.QMessageBox.information(
                    None,
                    "Files Generated",
                    f"{'Configs' if n > 1 else 'Config'} written to:\n{out_dir}\n\n"
                    "Set the Palace binary path in the Simulation settings to run.",
                )
                return

            if CmdRun._coordinator is not None and CmdRun._coordinator.is_running():
                QtWidgets.QMessageBox.warning(
                    None, "Palace Running",
                    "A simulation is already running.\n"
                    "Please wait for it to finish before starting another.",
                )
                return

            num_procs   = int(getattr(sim, "NumProcesses", 1)) if sim else 1
            num_threads = int(getattr(sim, "NumThreads",   0)) if sim else 0
            t0_sim = time.time()
            _launch_passes(passes, needs_merge, out_dir, console, sim,
                           binary, num_procs, num_threads, t0_sim)

        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace: {exc}\n")
            if console is not None:
                console.write(f"ERROR: {exc}\n")
                console.set_status("Error")
            QtWidgets.QMessageBox.critical(None, "Palace Error", str(exc))


class CmdRun:
    _coordinator = None  # class-level ref keeps coordinator alive until all passes finish

    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Generate && Run",
            "ToolTip": (
                "Generate the Gmsh mesh and Palace config(s), then run Palace "
                "in the background.  If multiple ports have Excitation enabled, "
                "one pass is run per port and the results are combined into a "
                "full S-parameter matrix."
            ),
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return False
        has_sim    = any(hasattr(o, "SimulationType") for o in doc.Objects)
        has_airbox = any(hasattr(o, "OuterBoundaryType") for o in doc.Objects)
        return has_sim and has_airbox

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        out_dir = _output_dir(doc)
        os.makedirs(out_dir, exist_ok=True)
        sim = next((o for o in doc.Objects if hasattr(o, "SimulationType")), None)

        console = None
        try:
            from panels.palace_console import PalaceConsole
            console = PalaceConsole.get_or_create()
            console.clear()
            console.set_status("Meshing…")
        except Exception:
            pass

        def _log(msg):
            FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")
            if console is not None:
                console.write(msg)

        try:
            # Step 1 — mesh (synchronous)
            _log("Palace: Generating mesh…\n")
            from palace.meshing import generate_mesh
            t0_mesh = time.time()
            mesh_path = generate_mesh(doc, out_dir, log_fn=_log)
            mesh_elapsed = _fmt_elapsed(time.time() - t0_mesh)
            sim.MeshFile = mesh_path
            try:
                from features.mesh import create_palace_mesh
                mesh_obj = create_palace_mesh(doc)
                mesh_obj.MeshFile = mesh_path
                mesh_obj.Proxy.execute(mesh_obj)
                doc.recompute()
            except Exception:
                pass
            _log(f"Palace: Mesh written to {mesh_path}\n")
            _log(f"Palace: Mesh generated in {mesh_elapsed}\n")

            # Step 2 — build config(s) on the main thread
            _log("Palace: Writing config(s)…\n")
            passes, needs_merge = _build_passes(doc, out_dir)
            n = len(passes)
            _log(f"Palace: {n} excitation pass{'es' if n > 1 else ''} prepared.\n")

            # Step 3 — launch Palace (or inform user if binary not set)
            binary = sim.PalaceBinary if sim else ""
            if not binary or not os.path.isfile(binary):
                if console is not None:
                    console.set_status("No binary configured")
                QtWidgets.QMessageBox.information(
                    None,
                    "Files Generated",
                    f"{'Configs' if n > 1 else 'Config'} written to:\n{out_dir}\n\n"
                    "Set the Palace binary path in the Simulation settings to run.",
                )
                return

            if CmdRun._coordinator is not None and CmdRun._coordinator.is_running():
                QtWidgets.QMessageBox.warning(
                    None,
                    "Palace Running",
                    "A simulation is already running in the background.\n"
                    "Please wait for it to finish before starting another.",
                )
                return

            num_procs   = int(getattr(sim, "NumProcesses", 1)) if sim else 1
            num_threads = int(getattr(sim, "NumThreads",   0)) if sim else 0
            t0_sim = time.time()
            _launch_passes(passes, needs_merge, out_dir, console, sim,
                           binary, num_procs, num_threads, t0_sim, mesh_elapsed)

        except Exception as exc:
            FreeCAD.Console.PrintError(f"Palace: {exc}\n")
            if console is not None:
                console.write(f"ERROR: {exc}\n")
                console.set_status("Error")
            QtWidgets.QMessageBox.critical(None, "Palace Error", str(exc))

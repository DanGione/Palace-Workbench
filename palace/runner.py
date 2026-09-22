"""Launch Palace as a subprocess and stream output to FreeCAD's Report View.

Binary path conventions
-----------------------
Native binary (Linux / macOS):
    /home/user/spack/opt/.../bin/palace

WSL binary (Windows → WSL2):
    Set binary_path to the Linux path inside WSL, e.g.:
        /home/danfl/spack/opt/spack/linux-.../palace
    The runner detects that it is on Windows and automatically wraps the
    command with  wsl --  so FreeCAD does not need to know the difference.

    The Windows output directory is translated to a WSL path automatically
    via  wsl wslpath  so Palace can write results back to the Windows filesystem.
"""
import json
import os
import subprocess
import sys
import FreeCAD

from palace.gpu_memory import log_gpu_memory
from palace.shm_cleanup import cleanup_stale_shm_segments, warn_if_shm_full

_CUDA_SENTINEL_NAME = "palace-cuda-enabled"
# Fallback prefix when binary_path has no usable directory component (e.g. a
# bare "palace" resolved via PATH) -- matches this project's Docker images,
# which always install to CMAKE_INSTALL_PREFIX=/usr/local (see Dockerfile's
# `touch /usr/local/share/palace-cuda-enabled`). Without this fallback,
# os.path.dirname(os.path.dirname("palace")) is "", producing a bogus
# CWD-relative sentinel path that can never be created correctly.
_DEFAULT_INSTALL_PREFIX = "/usr/local"


def _requested_device(config_path):
    """Return the Solver.Device value ("CPU"/"GPU") from a Palace config JSON.

    Defensively defaults to "CPU" on *any* read/parse problem -- including a
    well-formed but unexpectedly-shaped JSON document (e.g. "Solver" not being
    an object) -- since this is a best-effort read, not a validator.
    """
    try:
        with open(config_path) as f:
            data = json.load(f)
        solver = data.get("Solver", {})
        if not isinstance(solver, dict):
            return "CPU"
        return solver.get("Device", "CPU")
    except (OSError, ValueError):
        return "CPU"


def _cuda_sentinel_path(binary_path):
    """Path to the marker Dockerfile:PALACE_WITH_CUDA=ON touches at install time.

    Derived relative to the binary's own install prefix (bin/palace -> ../share/...)
    rather than hardcoded to /usr/local, so this still works if the image's
    CMAKE_INSTALL_PREFIX ever changes -- falling back to _DEFAULT_INSTALL_PREFIX
    when binary_path has no directory component to derive a prefix from.
    """
    prefix = os.path.dirname(os.path.dirname(binary_path))
    if not prefix:
        prefix = _DEFAULT_INSTALL_PREFIX
    return os.path.join(prefix, "share", _CUDA_SENTINEL_NAME)


def _check_gpu_requested(config_path, binary_path, path_exists=os.path.isfile):
    """Fail fast with a clear error if Device=GPU is requested but this Palace
    build has no known CUDA support, instead of letting Palace itself fail deep
    inside matrix assembly with a confusing, hard-to-diagnose error.

    path_exists is injectable so the WSL launch path can check the sentinel
    inside the WSL filesystem (via `wsl test -f`) instead of the default
    os.path.isfile, which can't see into WSL from a native Windows process.
    """
    if _requested_device(config_path) != "GPU":
        return
    sentinel = _cuda_sentinel_path(binary_path)
    if path_exists(sentinel):
        return
    raise RuntimeError(
        "This simulation requests Device=GPU, but this Palace installation has "
        f"no CUDA marker at {sentinel}.\n"
        "If you're using this project's Docker images, switch to the :cuda "
        "image variant (see docker-compose.cuda.yml).\n"
        "If you built Palace yourself with -DPALACE_WITH_CUDA=ON outside "
        f"Docker, create an empty file at {sentinel} to confirm CUDA support "
        "and silence this check."
    )


def _gpu_parallelism_warnings(device, num_procs, num_threads):
    """Advisory messages for a GPU run configured like a CPU run.

    MPI ranks are how Palace scales *across* GPUs, not how it extracts more
    speed from one: ranks sharing a single device time-slice rather than
    overlap (no CUDA MPS here), each rank duplicates device-resident operators
    plus its own CUDA context, and this project's CUDA image builds hypre
    without GPU-aware MPI (HYPRE_USING_GPU_AWARE_MPI undefined), so every halo
    exchange per solver iteration round-trips device->host->device.

    Pure (no I/O, no FreeCAD) so it is unit-testable the same way
    _check_gpu_requested is -- run_palace() reads Solver.Device itself and
    passes the result in.
    """
    if device != "GPU":
        return []
    msgs = []
    if num_procs > 1:
        msgs.append(
            f"Palace: WARNING — Device=GPU with {num_procs} MPI processes. On a "
            "GPU build, use one rank per GPU, not one per CPU core: ranks "
            "sharing a single device time-slice it rather than running "
            "concurrently, each rank duplicates GPU memory, and this build has "
            "no GPU-aware MPI (every halo exchange copies through host memory). "
            "Set MPI processes to 1 unless you have one GPU per rank."
        )
    if num_threads > 0:
        msgs.append(
            f"Palace: WARNING — Device=GPU with {num_threads} OpenMP threads per "
            "rank. Compute runs on the device, so OMP threads/rank have little "
            "to no effect on a GPU run; 0 is the appropriate setting."
        )
    return msgs


def _wsl_path_exists(path):
    """Check for a file inside the WSL filesystem from native Windows Python.

    Mirrors the existing _win_to_wsl() pattern below (shelling into `wsl`) --
    a plain os.path.isfile() on a WSL-style "/..." path can't see it.
    """
    result = subprocess.run(["wsl", "test", "-f", path], capture_output=True)
    return result.returncode == 0


def _is_wsl_path(path):
    """Return True if *path* looks like a Linux/WSL path (starts with /)."""
    return path.startswith("/")


def _win_to_wsl(windows_path):
    """Convert a Windows path to its WSL mount point path using wslpath."""
    result = subprocess.run(
        ["wsl", "wslpath", "-u", windows_path],
        capture_output=True, text=True
    )
    wsl_path = result.stdout.strip()
    if not wsl_path:
        raise RuntimeError(
            f"wslpath could not convert '{windows_path}' — is WSL2 installed?"
        )
    return wsl_path


def run_palace(config_path, binary_path, num_procs=1, num_threads=0,
               line_callback=None, proc_callback=None):
    """
    Run Palace with the given config file.

    Parameters
    ----------
    config_path   : str       — absolute path to the Palace JSON config
    binary_path   : str       — path to the Palace executable (the palace wrapper script)
    num_procs     : int       — MPI process count, passed as --np to the palace wrapper
    num_threads   : int       — OpenMP threads per rank, passed as --nt to the palace wrapper
                                (0 = palace wrapper uses OMP_NUM_THREADS or its own default)
    line_callback : callable  — called with each stdout line in addition to
                                FreeCAD.Console (used by the debug panel)
    proc_callback : callable  — called immediately after Popen with the
                                subprocess.Popen object (used to store a
                                reference for external termination)

    Returns
    -------
    int : Palace process return code
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    def _log(msg):
        text = msg if msg.endswith("\n") else msg + "\n"
        FreeCAD.Console.PrintMessage(text)
        if line_callback is not None:
            line_callback(text)

    def _warn(msg):
        text = msg if msg.endswith("\n") else msg + "\n"
        FreeCAD.Console.PrintWarning(text)
        if line_callback is not None:
            line_callback(text)

    # A crashed/killed MPI job leaves orphaned shared-memory segments behind
    # in /dev/shm -- clean those up before every launch so they can't
    # silently accumulate and starve a later run of shared memory (a real
    # incident: see CLAUDE.md's "/dev/shm cleanup for orphaned Open MPI
    # vader segments" invariant). No-ops harmlessly where /dev/shm doesn't
    # exist (e.g. this process running natively on Windows for the WSL path).
    cleanup_stale_shm_segments(log_fn=_log)
    warn_if_shm_full(log_fn=_warn)

    # A GPU run that later dies raises the obvious question of whether it
    # simply ran out of device memory -- record the answer up front, while
    # the process that would hold those allocations still exists. CPU runs
    # skip this entirely rather than shelling out to nvidia-smi for nothing.
    if _requested_device(config_path) == "GPU":
        log_gpu_memory(_log, warn_fn=_warn)

    on_windows = sys.platform == "win32"
    use_wsl = on_windows and _is_wsl_path(binary_path)

    # Build args for the palace wrapper script.  The wrapper handles mpirun and
    # OMP_NUM_THREADS internally — do NOT wrap with an outer mpirun call, as that
    # would create nested mpirun invocations which OpenMPI rejects.
    palace_args = []
    if num_procs > 1:
        palace_args += ["--np", str(num_procs)]
    if num_threads > 0:
        palace_args += ["--nt", str(num_threads)]

    if use_wsl:
        # Check the sentinel inside the WSL filesystem (not via os.path.isfile,
        # which can't see it from this native Windows process) before doing
        # anything else on this path.
        _check_gpu_requested(config_path, binary_path, path_exists=_wsl_path_exists)

        # Translate the Windows output directory to a WSL-accessible path
        wsl_config = _win_to_wsl(config_path)
        wsl_cwd    = _win_to_wsl(os.path.dirname(config_path))
        inner = [binary_path] + palace_args + [wsl_config]
        cmd = ["wsl", "--cd", wsl_cwd, "--"] + inner
        cwd = None

    else:
        # Native binary (Linux / macOS, or a Windows .exe)
        if on_windows and not os.path.isfile(binary_path):
            raise FileNotFoundError(
                f"Palace binary not found: {binary_path}\n\n"
                "Palace has no native Windows build. Options:\n"
                "  1. Install Palace in WSL2 and set the binary path to its\n"
                "     Linux path, e.g. /home/user/spack/opt/.../bin/palace\n"
                "  2. Use Docker (run Palace manually after files are generated)."
            )
        # Checked after the "no native Windows build" check above, not before:
        # on Windows without WSL, that's the real problem, and a GPU-specific
        # error here would be confusing/wrong for what's actually a "Palace
        # doesn't run natively on Windows" situation.
        _check_gpu_requested(config_path, binary_path)
        cmd = [binary_path] + palace_args + [config_path]
        cwd = os.path.dirname(config_path)

    # Emitted only after the branch above, so _check_gpu_requested() has already
    # run on whichever launch path was taken -- performance advice must never
    # precede the hard "this build has no CUDA support" failure.
    for msg in _gpu_parallelism_warnings(
        _requested_device(config_path), num_procs, num_threads
    ):
        _warn(msg)

    FreeCAD.Console.PrintMessage(f"Palace: Running: {' '.join(cmd)}\n")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        start_new_session=True,   # own process group → killpg kills all MPI children
    )

    if proc_callback is not None:
        proc_callback(proc)

    for line in proc.stdout:
        if line_callback is not None:
            line_callback(line)

    proc.wait()

    if proc.returncode == 0:
        FreeCAD.Console.PrintMessage("Palace: Simulation finished successfully.\n")
    else:
        FreeCAD.Console.PrintError(
            f"Palace: Simulation exited with code {proc.returncode}.\n"
        )

    return proc.returncode

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
import os
import subprocess
import sys
import FreeCAD


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
        cmd = [binary_path] + palace_args + [config_path]
        cwd = os.path.dirname(config_path)

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

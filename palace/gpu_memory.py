"""Pre-flight GPU memory report for Device=GPU runs.

A GPU run that dies partway through leaves the obvious question of whether
it simply ran out of device memory. `nvidia-smi` answers that in one call,
but only if someone asks before the run starts -- afterwards the process is
gone and its allocations with it. `run_palace()` logs this for every GPU
launch so the answer is already in the log next time.

No FreeCAD/Qt dependency by design -- the `nvidia-smi` call is injectable --
so this is unit-testable without a GPU present.
"""
import subprocess

_QUERY = "memory.total,memory.used,memory.free"
_SMI_ARGS = ["nvidia-smi", f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"]

# Below this much free device memory, say so explicitly rather than just
# reporting the numbers. Not a validated threshold -- a round number chosen
# so a nearly-full card is called out, since that is the case a user is
# most likely to want flagged before waiting through a long solve.
_LOW_FREE_MIB = 1024


def _run_nvidia_smi():
    """Return nvidia-smi's raw stdout, or None if it can't be run.

    Absent on the CPU-only images, on macOS, and on native Windows -- all of
    which are ordinary situations, not errors.
    """
    try:
        result = subprocess.run(_SMI_ARGS, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def parse_gpu_memory(raw):
    """Parse nvidia-smi CSV output into a list of per-GPU (total, used, free) MiB.

    Tolerates junk: a line that doesn't hold three integers is skipped
    rather than raising, since this is diagnostic output and must never be
    the reason a run fails to start.
    """
    gpus = []
    for line in (raw or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            gpus.append(tuple(int(p) for p in parts))
        except ValueError:
            continue
    return gpus


def log_gpu_memory(log_fn, warn_fn=None, run_smi=_run_nvidia_smi):
    """Log each GPU's total/used/free memory before a run. Never raises.

    Returns the parsed list so callers can assert on it; an empty list means
    nothing could be read, which is logged as such rather than silently.
    """
    gpus = parse_gpu_memory(run_smi())
    if not gpus:
        log_fn("Palace: GPU memory unavailable (nvidia-smi not present or "
               "returned nothing) — skipping pre-flight GPU memory report")
        return []
    for index, (total, used, free) in enumerate(gpus):
        log_fn(f"Palace: GPU {index} memory before run — "
               f"{used} MiB used / {total} MiB total, {free} MiB free")
        if free < _LOW_FREE_MIB and warn_fn is not None:
            warn_fn(f"Palace: WARNING — GPU {index} has only {free} MiB free "
                    f"of {total} MiB. Another process may be holding device "
                    "memory; a large model may fail to allocate.")
    return gpus

"""Turn a failed Palace run into an explanation of what to do about it.

`commands/cmd_run.py` used to report nothing but `Palace exited with code N`,
which is close to useless: Palace is launched through `mpirun`, so a process
killed by a signal surfaces as mpirun's own exit code plus a line of mpirun
text, and the two failures that look most alike from the outside -- a GPU
run dying in solver setup and a run killed for exhausting memory -- call for
completely different responses.

No FreeCAD/Qt dependency by design -- pure `re`/stdlib, same as
`palace/shm_cleanup.py` -- so every signature below is unit-testable against
a captured log without a running FreeCAD.
"""
import re

# mpirun's own report, e.g.
#   mpirun noticed that process rank 0 with PID 0 on node 1527e7afe2fa
#   exited on signal 8 (Floating point exception).
# This is the only place the signal number appears when Palace runs under
# mpirun (the palace wrapper always uses it unless --serial), since mpirun
# substitutes its own exit code for the child's.
_MPIRUN_SIGNAL_RE = re.compile(
    r"exited on signal (\d+)(?:\s*\(([^)]*)\))?", re.IGNORECASE
)

# Signals also reach us directly as 128+N when Palace is run without mpirun.
_SIGNAL_EXIT_BASE = 128

_CUDA_OOM_MARKERS = (
    "cudaerrormemoryallocation",
    "out of memory",
    "cuda_error_out_of_memory",
)


def _signal_from(output_lines, returncode):
    """Return (signal_number, signal_name_or_None), or (None, None).

    Prefers mpirun's text over the exit code: mpirun reports the *child's*
    signal there, while its own exit code may be anything it chooses.
    """
    for line in reversed(output_lines):
        m = _MPIRUN_SIGNAL_RE.search(line)
        if m:
            name = m.group(2).strip() if m.group(2) else None
            return int(m.group(1)), (name or None)
    if returncode is not None and returncode > _SIGNAL_EXIT_BASE:
        return returncode - _SIGNAL_EXIT_BASE, None
    return None, None


def _mentions_cuda_oom(output_lines):
    tail = "\n".join(output_lines[-80:]).lower()
    return any(marker in tail for marker in _CUDA_OOM_MARKERS)


def _gpu_run(output_lines):
    """True when Palace's own banner says it configured a GPU device.

    Read from Palace's output rather than the config so this stays a pure
    function of what the run actually did -- Palace prints e.g.
    "Device configuration: cuda,cpu" and "Detected 1 CUDA device".
    """
    head = "\n".join(output_lines[:40]).lower()
    return "cuda" in head or "hip" in head


_SIGFPE_GPU = (
    "Palace died with SIGFPE (signal 8): a modulo-by-zero inside SLEPc's "
    "CUDA basis-vector routine (BVMultInPlace_BLAS_CUDA), confirmed by "
    "reading SLEPc v3.24.1's own source. That function sizes a memory-limited "
    "batch as freemem/(m*sizeof(PetscScalar)) -- appears to divide by the "
    "wrong dimension (m, the large operator size, instead of n, the small "
    "vector count) -- which can floor to 0 for a large operator, then faults "
    "on an unguarded `m % bs` two lines later. This is a genuine upstream "
    "SLEPc bug, present unchanged in Palace v0.17.0 and v0.18.1 alike (the "
    "0.18.0 changelog's \"fixed smoother spectral estimates\" entry, PR #837, "
    "does not touch this code path).\n"
    "It faults during the Chebyshev smoother's largest-eigenvalue estimate, "
    "which runs on the non-coarsest levels of Palace's p-multigrid "
    "hierarchy -- levels that exist at FE order 2 and above -- and it takes a "
    "large one to trigger: measured here, a smoothed level of 1,278,924 "
    "unknowns faults while one of 27,016 completes.\n"
    "What to do: drop to FE order 1 (verified to run this same large model "
    "to completion on GPU, and stays well under GPU memory limits -- ~4 GiB "
    "peak measured here on an 8 GiB card, vs. order 2's ~7.8 GiB), or use "
    "Device=CPU at order 2 and above. Smaller models run as they are."
)

_SIGFPE_CPU = (
    "Palace died with SIGFPE (signal 8): an integer divide-by-zero. On CPU "
    "the usual source is degenerate geometry reaching the solver — check for "
    "an empty material group or a zero-area port face."
)

_SIGBUS = (
    "Palace died with SIGBUS (signal 7), typically during matrix assembly. "
    "This build has two known causes, so check both:\n"
    "  1. /dev/shm exhausted by orphaned Open MPI segments from earlier "
    "crashed runs. The pre-flight cleanup logs its findings above — if it "
    "reported usage still high, a concurrent simulation is holding that "
    "space.\n"
    "  2. A malformed boundary attribute from mesh face matching (Palace "
    "logs \"boundary attribute with internal and external boundary "
    "elements\" when this is the cause). Re-mesh, or try the other mesh "
    "backend."
)

_OOM_KILLED = (
    "Palace was killed by the operating system (SIGKILL / exit 137), which "
    "means the machine ran out of host RAM. GPU memory spilled into Windows' "
    "shared GPU memory is backed by host RAM and counts toward this. Reduce "
    "the mesh size or FE order, or give the container more memory."
)

_SEGFAULT = (
    "Palace died with SIGSEGV (signal 11): a memory fault inside the solver. "
    "Capture the full log and the mesh before retrying — this usually "
    "reproduces."
)

_CUDA_OOM = (
    "Palace ran out of GPU memory. On this WSL2 setup plain device "
    "allocations spill into Windows' shared GPU memory (measured: 12 GiB "
    "allocated and written on an 8 GiB card), while CUDA managed "
    "allocations stop at the physical limit. Reduce the mesh size or FE "
    "order, or run with Device=CPU."
)

_SIGNAL_MESSAGES = {
    7: lambda _gpu: _SIGBUS,
    8: lambda gpu: _SIGFPE_GPU if gpu else _SIGFPE_CPU,
    9: lambda _gpu: _OOM_KILLED,
    11: lambda _gpu: _SEGFAULT,
}


def diagnose_failure(output_lines, returncode):
    """Explain a failed Palace run, or return None if there's nothing to add.

    Returns None for a successful run and for a failure whose signature
    isn't recognized -- an unrecognized failure gets the plain exit code
    rather than a guess, since a confidently wrong diagnosis is worse than
    none at all.
    """
    if returncode == 0:
        return None
    lines = list(output_lines or [])

    if _mentions_cuda_oom(lines):
        return _CUDA_OOM

    signal_number, _signal_name = _signal_from(lines, returncode)
    if signal_number in _SIGNAL_MESSAGES:
        return _SIGNAL_MESSAGES[signal_number](_gpu_run(lines))
    if returncode == 137:
        return _OOM_KILLED
    return None

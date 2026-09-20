"""Pre-flight `/dev/shm` cleanup for orphaned Open MPI `vader` segments.

Open MPI's `vader` BTL (shared-memory transport for intra-node inter-rank
communication) creates one file per rank in `/dev/shm`, named
`vader_segment.<hostname>.<uid>.<jobid>.<rank>`. These are removed on a
clean exit but are left behind whenever a Palace/`mpirun` job crashes or is
killed -- see the "/dev/shm cleanup for orphaned Open MPI vader segments"
invariant in CLAUDE.md for the real incident this exists to prevent.

No FreeCAD/Qt dependency by design -- pure `os`/`glob`/`shutil` -- so this
is fully unit-testable without mocking `/proc`.
"""
import glob
import os
import shutil

_VADER_PATTERN = "vader_segment.*"
# Single confirmed data point: 91% used correlated with a real SIGBUS crash,
# 0% used (after cleanup) fixed it. No finer-grained measurement exists, so
# this is a conservative extrapolation, not a validated boundary -- a named
# constant so it's easy to retune later without touching call sites.
_WARN_THRESHOLD_FRACTION = 0.80


def _live_referenced_paths():
    """Return the set of every absolute path any live process currently has
    open (fd) or memory-mapped (maps).

    Both are checked, not just fd: Open MPI's vader BTL commonly
    shm_open()s a segment, mmap()s it, then closes the raw fd -- leaving
    `maps` as the only remaining signal that a process still references it.
    Tolerates a process exiting mid-scan (its /proc/<pid> directory
    disappearing) and permission errors reading another process's state --
    skip that process rather than raising.
    """
    paths = set()
    for pid_dir in glob.glob("/proc/[0-9]*"):
        fd_dir = os.path.join(pid_dir, "fd")
        try:
            fd_names = os.listdir(fd_dir)
        except OSError:
            fd_names = []
        for fd_name in fd_names:
            try:
                target = os.readlink(os.path.join(fd_dir, fd_name))
            except OSError:
                continue
            paths.add(target)

        maps_path = os.path.join(pid_dir, "maps")
        try:
            with open(maps_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    fields = line.split()
                    if not fields:
                        continue
                    last = fields[-1]
                    if last.startswith("/"):
                        paths.add(last)
        except OSError:
            continue
    return paths


def _path_in_use(path):
    """Return True if any live process has *path* open or memory-mapped."""
    return os.path.realpath(path) in _live_referenced_paths()


def cleanup_stale_shm_segments(shm_dir="/dev/shm", pattern=_VADER_PATTERN, log_fn=None):
    """Remove orphaned Open MPI vader segments from *shm_dir*.

    Returns (removed, kept) -- lists of absolute paths as globbed. No-ops to
    ([], []), with no log_fn calls, if shm_dir doesn't exist (e.g. native
    Windows, where /dev/shm is never a real path -- the actual Palace binary
    there runs inside WSL's own Linux filesystem, out of reach of this
    Python process).

    log_fn(str), if given, is called once to announce the check, once per
    removed file, once per kept (still in use, left alone) file, then a
    one-line summary. Everything logged here is informational -- severity
    is entirely warn_if_shm_full()'s job, not this function's. A failed
    os.remove() (e.g. the file vanished between glob() and removal) is
    tolerated silently; it only matters if /dev/shm is still full
    afterward, which warn_if_shm_full() independently checks next.
    """
    real_dir = os.path.realpath(shm_dir)
    if not os.path.isdir(real_dir):
        return [], []

    candidates = sorted(glob.glob(os.path.join(real_dir, pattern)))
    if not candidates:
        return [], []

    if log_fn is not None:
        log_fn(
            f"Palace: Checking {len(candidates)} leftover shared-memory "
            f"segment(s) in {shm_dir} for orphans from a previous crashed "
            "run..."
        )

    live_paths = _live_referenced_paths()
    removed = []
    kept = []
    for path in candidates:
        if os.path.realpath(path) in live_paths:
            kept.append(path)
            if log_fn is not None:
                log_fn(f"Palace:   kept (still in use): {os.path.basename(path)}")
            continue
        try:
            os.remove(path)
        except OSError:
            kept.append(path)
            if log_fn is not None:
                log_fn(f"Palace:   kept (could not remove): {os.path.basename(path)}")
            continue
        removed.append(path)
        if log_fn is not None:
            log_fn(f"Palace:   removed (orphaned): {os.path.basename(path)}")

    if log_fn is not None:
        log_fn(
            f"Palace: Shared-memory cleanup: removed {len(removed)}, "
            f"kept {len(kept)} (still in use or could not be removed)."
        )

    return removed, kept


def warn_if_shm_full(shm_dir="/dev/shm", threshold=_WARN_THRESHOLD_FRACTION, log_fn=None):
    """Warn via *log_fn* if *shm_dir* is still dangerously full.

    Uses shutil.disk_usage(). Returns True if the warning fired, else
    False. No-op (False, no log_fn call) if shm_dir doesn't exist. Firing
    after cleanup_stale_shm_segments() has already removed everything
    safely removable means the remaining usage belongs to a genuinely live
    process -- the message tells the user to wait or stop another
    simulation.
    """
    if not os.path.isdir(shm_dir):
        return False

    usage = shutil.disk_usage(shm_dir)
    if usage.total <= 0:
        return False

    used_fraction = 1.0 - (usage.free / usage.total)
    if used_fraction < threshold:
        return False

    if log_fn is not None:
        free_mb = usage.free / (1024 * 1024)
        total_mb = usage.total / (1024 * 1024)
        log_fn(
            f"Palace: WARNING — {shm_dir} is {used_fraction:.0%} full "
            f"({free_mb:.1f}MB free of {total_mb:.1f}MB) even after removing "
            "every orphaned shared-memory segment we could find. This "
            "usually means another simulation is still running and using "
            "shared memory -- wait for it to finish, or stop it, before "
            "starting a new run, to avoid a SIGBUS crash during matrix "
            "assembly."
        )
    return True

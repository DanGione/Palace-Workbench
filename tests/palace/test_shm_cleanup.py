"""Coverage for palace/shm_cleanup.py's orphaned /dev/shm segment cleanup.

cleanup_stale_shm_segments()/warn_if_shm_full() exist to fix a real
production SIGBUS crash: a crashed/killed Palace/mpirun job leaves Open
MPI's vader-BTL shared-memory segments behind in /dev/shm, and repeated
crashes over several days silently filled a 64MB /dev/shm to 91%, which
then caused a *later*, otherwise-healthy run to crash too. See CLAUDE.md's
"/dev/shm cleanup for orphaned Open MPI vader segments" invariant.

All tests use tmp_path as a fake shm_dir -- never touches the real
/dev/shm. Liveness detection is exercised against real, unmocked /proc
state: a file this *test process itself* has open or mmap'd genuinely
shows up under /proc/self, so no /proc mocking is needed at all.
"""
import mmap
import os

from palace.shm_cleanup import (
    _path_in_use,
    cleanup_stale_shm_segments,
    warn_if_shm_full,
)


def _touch(path):
    with open(path, "wb") as fh:
        fh.write(b"x" * 16)


def test_cleanup_removes_file_with_no_live_reference(tmp_path):
    target = tmp_path / "vader_segment.host.1000.abc.0"
    _touch(target)

    removed, kept = cleanup_stale_shm_segments(shm_dir=str(tmp_path))

    assert removed == [str(target)]
    assert kept == []
    assert not target.exists()


def test_cleanup_keeps_file_held_open_by_a_live_process(tmp_path):
    target = tmp_path / "vader_segment.host.1000.abc.1"
    _touch(target)

    fh = open(target, "rb")
    try:
        removed, kept = cleanup_stale_shm_segments(shm_dir=str(tmp_path))
    finally:
        fh.close()

    assert removed == []
    assert kept == [str(target)]
    assert target.exists()


def test_cleanup_keeps_file_that_is_only_mmapped_not_fd_open(tmp_path):
    target = tmp_path / "vader_segment.host.1000.abc.2"
    with open(target, "wb") as fh:
        fh.write(b"\x00" * mmap.ALLOCATIONGRANULARITY)

    fd = os.open(target, os.O_RDWR)
    mapping = mmap.mmap(fd, mmap.ALLOCATIONGRANULARITY)
    os.close(fd)  # mirror vader's real behavior: mapping outlives the fd

    try:
        removed, kept = cleanup_stale_shm_segments(shm_dir=str(tmp_path))
    finally:
        mapping.close()

    assert removed == []
    assert kept == [str(target)]
    assert target.exists()


def test_path_in_use_true_for_open_file_false_after_close(tmp_path):
    target = tmp_path / "some_file"
    _touch(target)

    fh = open(target, "rb")
    try:
        assert _path_in_use(str(target)) is True
    finally:
        fh.close()

    assert _path_in_use(str(target)) is False


def test_cleanup_ignores_non_matching_filenames(tmp_path):
    other = tmp_path / "not_a_vader_segment.txt"
    _touch(other)

    removed, kept = cleanup_stale_shm_segments(shm_dir=str(tmp_path))

    assert removed == []
    assert kept == []
    assert other.exists()


def test_cleanup_noops_on_missing_shm_dir(tmp_path):
    missing = tmp_path / "does_not_exist"
    calls = []

    removed, kept = cleanup_stale_shm_segments(shm_dir=str(missing), log_fn=calls.append)

    assert removed == []
    assert kept == []
    assert calls == []


def test_cleanup_logs_check_and_each_removed_and_kept_file(tmp_path):
    orphan = tmp_path / "vader_segment.host.1000.abc.3"
    _touch(orphan)
    held = tmp_path / "vader_segment.host.1000.abc.4"
    _touch(held)

    calls = []
    fh = open(held, "rb")
    try:
        cleanup_stale_shm_segments(shm_dir=str(tmp_path), log_fn=calls.append)
    finally:
        fh.close()

    joined = "\n".join(calls)
    assert "Checking 2" in joined
    assert orphan.name in joined
    assert held.name in joined
    assert any("removed" in c for c in calls)
    assert any("kept" in c for c in calls)


def test_cleanup_tolerates_file_removed_mid_scan(tmp_path, monkeypatch):
    target = tmp_path / "vader_segment.host.1000.abc.5"
    _touch(target)

    real_remove = os.remove

    def _flaky_remove(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(os, "remove", _flaky_remove)

    removed, kept = cleanup_stale_shm_segments(shm_dir=str(tmp_path))

    monkeypatch.setattr(os, "remove", real_remove)

    assert removed == []
    assert kept == [str(target)]


def test_warn_if_shm_full_fires_above_threshold(tmp_path, monkeypatch):
    import shutil as shutil_mod

    usage = shutil_mod.disk_usage(str(tmp_path))._make(
        (100 * 1024 * 1024, 92 * 1024 * 1024, 8 * 1024 * 1024)
    )
    monkeypatch.setattr(shutil_mod, "disk_usage", lambda path: usage)

    calls = []
    fired = warn_if_shm_full(shm_dir=str(tmp_path), log_fn=calls.append)

    assert fired is True
    assert len(calls) == 1
    assert "WARNING" in calls[0]


def test_warn_if_shm_full_silent_below_threshold(tmp_path, monkeypatch):
    import shutil as shutil_mod

    usage = shutil_mod.disk_usage(str(tmp_path))._make(
        (100 * 1024 * 1024, 50 * 1024 * 1024, 50 * 1024 * 1024)
    )
    monkeypatch.setattr(shutil_mod, "disk_usage", lambda path: usage)

    calls = []
    fired = warn_if_shm_full(shm_dir=str(tmp_path), log_fn=calls.append)

    assert fired is False
    assert calls == []


def test_warn_if_shm_full_noops_on_missing_shm_dir(tmp_path):
    missing = tmp_path / "does_not_exist"
    calls = []

    fired = warn_if_shm_full(shm_dir=str(missing), log_fn=calls.append)

    assert fired is False
    assert calls == []

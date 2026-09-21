"""Coverage for _PassWorker.run()'s exception handling.

run_palace() can raise synchronously before Palace even launches -- e.g. a
missing config file, or palace/runner.py's GPU-capability RuntimeError when
Device=GPU is requested without CUDA support. Before this fix, run() had no
try/except around that call: an uncaught exception inside QThread.run() means
pass_done is never emitted, _SimCoordinator never reaches all_done, and the
UI hangs on "Running Palace..." forever with the Stop button greyed out.
"""
from unittest import mock

from commands.cmd_run import _PassWorker


def _make_worker():
    return _PassWorker(
        pass_index=0,
        config_path="/fake/palace_config.json",
        pass_dir="/fake/pass_dir",
        binary="/fake/bin/palace",
    )


def test_run_emits_pass_done_failure_when_run_palace_raises():
    worker = _make_worker()
    captured = []
    worker.pass_done.connect(lambda idx, ok, msg: captured.append((idx, ok, msg)))

    with mock.patch(
        "palace.runner.run_palace",
        side_effect=RuntimeError("This simulation requests Device=GPU, but ..."),
    ):
        worker.run()  # must not raise -- the whole point of the fix

    assert len(captured) == 1
    pass_index, success, message = captured[0]
    assert pass_index == 0
    assert success is False
    assert "Device=GPU" in message


def test_run_emits_pass_done_success_when_run_palace_succeeds():
    worker = _make_worker()
    captured = []
    worker.pass_done.connect(lambda idx, ok, msg: captured.append((idx, ok, msg)))

    with mock.patch("commands.cmd_run._expected_freq_count", return_value=None), \
         mock.patch("palace.runner.run_palace", return_value=0):
        worker.run()

    assert captured == [(0, True, "OK")]

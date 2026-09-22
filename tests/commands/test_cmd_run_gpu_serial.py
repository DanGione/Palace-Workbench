"""Coverage for commands/cmd_run.py's GPU serial-pass override.

Parallel excitation passes are separate Palace processes with no per-pass GPU
assignment, so on Device=GPU they contend for the same device -- often slower
than serial, and a GPU out-of-memory risk. _passes_run_parallel() forces serial
in that case without touching the document's own ParallelPasses setting.
"""
from types import SimpleNamespace

from commands.cmd_run import _passes_run_parallel


def _sim(device="CPU", parallel=True):
    return SimpleNamespace(Device=device, ParallelPasses=parallel)


def test_gpu_multi_pass_is_forced_serial_with_a_reason():
    parallel, reason = _passes_run_parallel(_sim(device="GPU"), 4)

    assert parallel is False
    assert reason and "4 excitation passes" in reason


def test_gpu_single_pass_is_not_overridden():
    # With one pass there is no parallel/serial distinction to make -- warning
    # about it would be pure noise.
    parallel, reason = _passes_run_parallel(_sim(device="GPU"), 1)

    assert parallel is True
    assert reason is None


def test_gpu_override_does_not_write_back_to_the_document():
    # The saved setting is still correct for this same document's CPU runs.
    sim = _sim(device="GPU")

    _passes_run_parallel(sim, 4)

    assert sim.ParallelPasses is True


def test_cpu_multi_pass_stays_parallel():
    parallel, reason = _passes_run_parallel(_sim(device="CPU"), 4)

    assert parallel is True
    assert reason is None


def test_cpu_respects_an_explicit_serial_setting():
    parallel, reason = _passes_run_parallel(_sim(device="CPU", parallel=False), 4)

    assert parallel is False
    assert reason is None


def test_gpu_already_serial_needs_no_override():
    parallel, reason = _passes_run_parallel(_sim(device="GPU", parallel=False), 4)

    assert parallel is False
    assert reason is None


def test_missing_sim_keeps_the_parallel_default():
    parallel, reason = _passes_run_parallel(None, 4)

    assert parallel is True
    assert reason is None


def test_sim_without_device_property_is_treated_as_cpu():
    # A document saved before the Device property existed has no such
    # attribute; it must behave exactly as it did before (parallel).
    parallel, reason = _passes_run_parallel(SimpleNamespace(ParallelPasses=True), 4)

    assert parallel is True
    assert reason is None

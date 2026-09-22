"""Coverage for palace/runner.py's GPU parallelism advisories.

MPI ranks and OpenMP threads scale a CPU run; on a GPU build they don't help
(ranks are one-per-GPU, not one-per-core) and can hurt. These are advisory
warnings only -- unlike _check_gpu_requested's fast-fail, nothing here blocks
a run. See CLAUDE.md and docs/simulation-reference.md's GPU section.
"""
from palace.runner import _gpu_parallelism_warnings


def test_no_warnings_for_cpu_regardless_of_parallelism():
    # A CPU run is exactly what many ranks/threads are for -- must stay silent.
    assert _gpu_parallelism_warnings("CPU", 8, 4) == []


def test_no_warnings_for_gpu_with_single_rank_and_no_threads():
    assert _gpu_parallelism_warnings("GPU", 1, 0) == []


def test_warns_for_gpu_with_multiple_ranks():
    msgs = _gpu_parallelism_warnings("GPU", 4, 0)

    assert len(msgs) == 1
    assert "4 MPI processes" in msgs[0]
    assert "one rank per GPU" in msgs[0]


def test_warns_for_gpu_with_openmp_threads():
    msgs = _gpu_parallelism_warnings("GPU", 1, 4)

    assert len(msgs) == 1
    assert "OpenMP threads" in msgs[0]
    # Phrased as "little to no effect", never "no-op": whether OMP does
    # anything at all depends on PALACE_WITH_OPENMP, which this pure helper
    # cannot see.
    assert "little to no effect" in msgs[0]


def test_warns_about_both_ranks_and_threads_together():
    msgs = _gpu_parallelism_warnings("GPU", 4, 4)

    assert len(msgs) == 2
    assert all(m.startswith("Palace: WARNING — ") for m in msgs)

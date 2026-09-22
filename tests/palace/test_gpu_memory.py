"""Coverage for palace/gpu_memory.py's pre-flight GPU memory report.

Runs before every Device=GPU launch so that "did it just run out of device
memory?" is already answered in the log the next time a GPU run dies --
after the fact the process and its allocations are gone.
"""
from palace.gpu_memory import log_gpu_memory, parse_gpu_memory


def test_parse_reads_nvidia_smi_csv():
    assert parse_gpu_memory("8192, 732, 7293\n") == [(8192, 732, 7293)]


def test_parse_handles_multiple_gpus():
    assert parse_gpu_memory("8192, 732, 7293\n24576, 1024, 23552\n") == [
        (8192, 732, 7293), (24576, 1024, 23552),
    ]


def test_parse_skips_junk_without_raising():
    # Diagnostic output must never be the reason a run fails to start.
    raw = "8192, 732, 7293\nnot,really,numbers\ntoo,few\n\n"
    assert parse_gpu_memory(raw) == [(8192, 732, 7293)]


def test_parse_of_nothing_is_empty():
    assert parse_gpu_memory(None) == []
    assert parse_gpu_memory("") == []


def test_log_reports_each_gpu():
    logged = []
    gpus = log_gpu_memory(logged.append, run_smi=lambda: "8192, 732, 7293\n")

    assert gpus == [(8192, 732, 7293)]
    assert len(logged) == 1
    assert "732 MiB used" in logged[0] and "7293 MiB free" in logged[0]


def test_log_warns_when_free_memory_is_low():
    logged, warned = [], []
    log_gpu_memory(logged.append, warn_fn=warned.append,
                   run_smi=lambda: "8192, 7900, 292\n")

    assert warned and "only 292 MiB free" in warned[0]


def test_log_does_not_warn_with_healthy_headroom():
    warned = []
    log_gpu_memory(lambda _m: None, warn_fn=warned.append,
                   run_smi=lambda: "8192, 732, 7293\n")

    assert warned == []


def test_missing_nvidia_smi_is_logged_not_silent():
    # Absent on CPU-only images, macOS and native Windows -- ordinary
    # situations, but the log should still say why there are no numbers.
    logged = []
    gpus = log_gpu_memory(logged.append, run_smi=lambda: None)

    assert gpus == []
    assert len(logged) == 1 and "unavailable" in logged[0]

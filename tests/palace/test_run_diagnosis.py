"""Coverage for palace/run_diagnosis.py.

The SIGFPE case below is the real thing: a CUDA run of
projects/Coax_Directional_Bridge.FCStd died with signal 8, and a gdb
backtrace put the divide-by-zero in SLEPc's CUDA basis-vector routine
during the Chebyshev smoother's eigenvalue estimate. It looked like an
out-of-memory failure from the outside -- GPU memory was full at the time --
which is exactly why the diagnosis has to be specific rather than guessed.
"""
from palace.run_diagnosis import diagnose_failure

# Verbatim from the failing run (trimmed to the lines the parser reads).
_GPU_HEADER = [
    ">> /usr/bin/mpirun -n 1 /usr/local/bin/palace-x86_64.bin config.json\n",
    "Running with 1 MPI process\n",
    "Detected 1 CUDA device\n",
    "Device configuration: cuda,cpu\n",
    "Memory configuration: host-umpire,cuda-umpire\n",
    "Use GPU-aware MPI:    no\n",
    "libCEED backend: /gpu/cuda/magma\n",
]
_MPIRUN_SIGFPE = (
    "mpirun noticed that process rank 0 with PID 0 on node 1527e7afe2fa "
    "exited on signal 8 (Floating point exception).\n"
)


def _gpu_log(*tail):
    return _GPU_HEADER + ["Assembling multigrid hierarchy:\n"] + list(tail)


def test_successful_run_is_never_diagnosed():
    assert diagnose_failure(_gpu_log(_MPIRUN_SIGFPE), 0) is None


def test_sigfpe_on_gpu_names_the_confirmed_cause():
    msg = diagnose_failure(_gpu_log(_MPIRUN_SIGFPE), 1)

    assert msg is not None
    assert "SIGFPE" in msg
    # States what the failure is -- the mechanism and the fix -- rather than
    # listing what it is not.
    assert "integer divide-by-zero" in msg
    assert "BVMultInPlace_BLAS_CUDA" in msg
    assert "order 1" in msg.lower()


def test_sigfpe_message_does_not_overclaim_that_all_order_2_gpu_runs_fail():
    # The first reading of this bug was "FE order >= 2 fails on GPU". A
    # 3,787-element order-2 model runs to completion on GPU with the same
    # two-level hierarchy, so the message must stay size-qualified -- users
    # with small models should not be told to abandon the GPU.
    msg = diagnose_failure(_gpu_log(_MPIRUN_SIGFPE), 1)

    assert "27,016" in msg and "1,278,924" in msg
    assert "Smaller models run as they are" in msg


def test_sigfpe_signal_is_read_from_mpirun_not_the_exit_code():
    # mpirun substitutes its own exit code for the child's, so a bare
    # returncode cannot identify the signal -- only mpirun's text can.
    msg = diagnose_failure(_gpu_log(_MPIRUN_SIGFPE), 1)
    assert msg is not None and "SIGFPE" in msg


def test_sigfpe_direct_launch_uses_the_128_plus_n_exit_code():
    # Without mpirun (palace --serial, or the binary run directly) the
    # signal arrives as 128+N instead.
    msg = diagnose_failure(_gpu_log(), 136)
    assert msg is not None and "SIGFPE" in msg


def test_sigfpe_on_cpu_does_not_blame_the_gpu_path():
    cpu_log = ["Device configuration: cpu\n", "Assembling multigrid hierarchy:\n"]
    msg = diagnose_failure(cpu_log + [_MPIRUN_SIGFPE], 1)

    assert msg is not None
    assert "SLEPc" not in msg
    assert "degenerate geometry" in msg


def test_sigbus_points_at_both_known_causes():
    line = "mpirun noticed that process rank 0 ... exited on signal 7 (Bus error).\n"
    msg = diagnose_failure(_gpu_log(line), 1)

    assert msg is not None
    assert "/dev/shm" in msg
    assert "boundary attribute" in msg


def test_oom_killer_is_called_out_as_host_ram():
    line = "mpirun noticed that process rank 0 ... exited on signal 9 (Killed).\n"
    msg = diagnose_failure(_gpu_log(line), 1)

    assert msg is not None
    assert "host RAM" in msg


def test_exit_137_without_an_mpirun_line_is_still_the_oom_killer():
    msg = diagnose_failure(["some output\n"], 137)
    assert msg is not None and "host RAM" in msg


def test_cuda_oom_text_wins_over_the_signal():
    # A CUDA allocation failure may also end in a signal; the allocation
    # message is the more specific and more actionable of the two.
    msg = diagnose_failure(_gpu_log("cudaErrorMemoryAllocation: out of memory\n",
                                    _MPIRUN_SIGFPE), 1)

    assert msg is not None
    assert "GPU memory" in msg
    assert "shared GPU memory" in msg


def test_unrecognized_failure_returns_none_rather_than_guessing():
    # A confidently wrong diagnosis is worse than none -- the caller falls
    # back to reporting the plain exit code.
    assert diagnose_failure(["Palace: some unexpected error\n"], 1) is None


def test_empty_output_is_handled():
    assert diagnose_failure([], 1) is None
    assert diagnose_failure(None, 1) is None


def test_device_is_read_from_the_banner_even_far_from_the_failure():
    # commands/cmd_run.py keeps the first 40 lines separately from the
    # 200-line tail precisely so this holds: a crash late in a long run must
    # still be attributed to the GPU. Simulate the head + a distant tail.
    context = _GPU_HEADER + ["It %d/400: solving\n" % i for i in range(150)] + [
        "Assembling multigrid hierarchy:\n", _MPIRUN_SIGFPE,
    ]
    msg = diagnose_failure(context, 1)

    assert msg is not None
    assert "SLEPc" in msg          # the GPU-flavoured message, not the CPU one

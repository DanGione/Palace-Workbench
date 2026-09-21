"""Coverage for palace/runner.py's GPU-capability fast-fail check.

Device=GPU with a Palace build that has no CUDA support would otherwise fail
deep inside Palace's own matrix assembly with a confusing error -- these
functions catch that up front by checking for the palace-cuda-enabled
sentinel file the Dockerfile touches when PALACE_WITH_CUDA=ON (see
Dockerfile and CLAUDE.md).
"""
import json
import os
from unittest import mock

import pytest

from palace.runner import (
    _check_gpu_requested,
    _cuda_sentinel_path,
    _DEFAULT_INSTALL_PREFIX,
    _requested_device,
    _wsl_path_exists,
)


def _write_config(path, device=None):
    solver = {"Order": 2}
    if device is not None:
        solver["Device"] = device
    with open(path, "w") as f:
        json.dump({"Solver": solver}, f)


def test_requested_device_defaults_to_cpu_when_absent(tmp_path):
    config_path = tmp_path / "config.json"
    _write_config(config_path)

    assert _requested_device(str(config_path)) == "CPU"


def test_requested_device_reads_gpu(tmp_path):
    config_path = tmp_path / "config.json"
    _write_config(config_path, device="GPU")

    assert _requested_device(str(config_path)) == "GPU"


def test_requested_device_defaults_to_cpu_on_malformed_json(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("not json")

    assert _requested_device(str(config_path)) == "CPU"


def test_requested_device_defaults_to_cpu_when_solver_is_not_an_object(tmp_path):
    # A hand-edited or corrupted config could have "Solver" as any JSON type;
    # this must degrade to the same "CPU" default as any other read problem,
    # not raise AttributeError from calling .get() on a non-dict.
    config_path = tmp_path / "config.json"
    with open(config_path, "w") as f:
        json.dump({"Solver": "not-an-object"}, f)

    assert _requested_device(str(config_path)) == "CPU"


def test_cuda_sentinel_path_relative_to_install_prefix(tmp_path):
    binary_path = str(tmp_path / "usr" / "local" / "bin" / "palace")

    sentinel = _cuda_sentinel_path(binary_path)

    assert sentinel == str(tmp_path / "usr" / "local" / "share" / "palace-cuda-enabled")


def test_cuda_sentinel_path_falls_back_for_bare_binary_name():
    # binary_path with no directory component (e.g. "palace" resolved via
    # PATH) must not silently produce a bogus CWD-relative sentinel path.
    sentinel = _cuda_sentinel_path("palace")

    assert sentinel == os.path.join(_DEFAULT_INSTALL_PREFIX, "share", "palace-cuda-enabled")


def test_check_gpu_requested_noop_for_cpu(tmp_path):
    config_path = tmp_path / "config.json"
    _write_config(config_path, device="CPU")
    binary_path = str(tmp_path / "bin" / "palace")

    _check_gpu_requested(str(config_path), binary_path)  # must not raise


def test_check_gpu_requested_passes_when_sentinel_present(tmp_path):
    config_path = tmp_path / "config.json"
    _write_config(config_path, device="GPU")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    share_dir = tmp_path / "share"
    share_dir.mkdir()
    (share_dir / "palace-cuda-enabled").touch()
    binary_path = str(bin_dir / "palace")

    _check_gpu_requested(str(config_path), binary_path)  # must not raise


def test_check_gpu_requested_raises_when_sentinel_missing(tmp_path):
    config_path = tmp_path / "config.json"
    _write_config(config_path, device="GPU")
    binary_path = str(tmp_path / "bin" / "palace")

    with pytest.raises(RuntimeError, match="CUDA"):
        _check_gpu_requested(str(config_path), binary_path)


def test_check_gpu_requested_uses_injected_path_exists(tmp_path):
    # The WSL launch path can't os.path.isfile() into the WSL filesystem from
    # native Windows Python -- it injects its own path_exists (backed by
    # `wsl test -f`) instead. Simulate that here with a fake that reports the
    # sentinel present despite no real file existing at that path.
    config_path = tmp_path / "config.json"
    _write_config(config_path, device="GPU")
    binary_path = "/home/user/spack/opt/.../bin/palace"  # WSL-side path

    _check_gpu_requested(str(config_path), binary_path, path_exists=lambda p: True)  # must not raise

    with pytest.raises(RuntimeError, match="CUDA"):
        _check_gpu_requested(str(config_path), binary_path, path_exists=lambda p: False)


def test_wsl_path_exists_true_when_wsl_test_succeeds():
    with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        assert _wsl_path_exists("/home/user/share/palace-cuda-enabled") is True
    run.assert_called_once_with(
        ["wsl", "test", "-f", "/home/user/share/palace-cuda-enabled"],
        capture_output=True,
    )


def test_wsl_path_exists_false_when_wsl_test_fails():
    with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
        assert _wsl_path_exists("/home/user/share/palace-cuda-enabled") is False

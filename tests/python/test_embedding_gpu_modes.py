"""Synthetic device-policy checks; no runtimes, weights or downloads."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_mode import embedding_jobs, local_embeddings as embeddings


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(embeddings, "preferences", lambda: {})
    monkeypatch.setattr(embeddings, "media_parts", lambda text, path: [{"type": "text", "text": text}])
    monkeypatch.setattr(embeddings, "assets", lambda: (Path("fake-server"), Path("fake-model"), None))
    monkeypatch.setattr(embeddings, "_probe", lambda executable: SimpleNamespace(stdout="Vulkan0: fake\nVulkan1: fake", stderr=""))
    item = embeddings.LocalEmbedding()
    item._start = Mock(side_effect=lambda executable, model, projector, device: setattr(item, "device", device))
    item._request = Mock(return_value={"dimensions": 2, "device": "Vulkan0", "vector": [1, 0]})
    yield item
    item.close()
    item.session.close()


def test_vulkan_startup_retries_only_discovered_gpu_devices(runner):
    runner._start.side_effect = [RuntimeError("Vulkan0 unavailable"), None]
    runner.embed("input", "", "local")
    assert [call.args[-1] for call in runner._start.call_args_list] == ["Vulkan0", "Vulkan1"]
    runner._request.assert_called_once()


def test_all_gpu_startups_fail_without_cpu_fallback(runner):
    runner._start.side_effect = RuntimeError("specific startup failure")
    with pytest.raises(RuntimeError, match="specific startup failure"):
        runner.embed("input", "", "local")
    assert [call.args[-1] for call in runner._start.call_args_list] == ["Vulkan0", "Vulkan1"]
    runner._request.assert_not_called()
    assert runner.mode == ""


def test_no_vulkan_devices_requires_explicit_cpu(runner, monkeypatch):
    monkeypatch.setattr(embeddings, "_probe", lambda executable: SimpleNamespace(stdout="", stderr=""))
    with pytest.raises(RuntimeError, match="Select CPU explicitly"):
        runner.embed("input", "", "local")
    runner._start.assert_not_called()


def test_gpu_probe_error_preserves_detail_without_starting_cpu(runner, monkeypatch):
    monkeypatch.setattr(embeddings, "_probe", Mock(side_effect=OSError("specific probe failure")))
    with pytest.raises(RuntimeError, match="specific probe failure"):
        runner.embed("input", "", "local")
    runner._start.assert_not_called()


@pytest.mark.parametrize("reused", [False, True])
def test_gpu_inference_failure_closes_without_replaying_input(runner, reused):
    process = Mock()
    process.poll.return_value = None
    if reused:
        runner.process = process
        runner.mode = "local"
        runner.device = "Vulkan0"
        runner.active_identity = "embeddinggemma-2:Q8_0"
    runner._request.side_effect = RuntimeError("specific inference failure")
    with pytest.raises(RuntimeError, match="specific inference failure"):
        runner.embed("input", "", "local")
    runner._request.assert_called_once()
    assert runner._start.call_count == (0 if reused else 1)
    assert runner.mode == ""
    if reused:
        process.terminate.assert_called_once()
        process.wait.assert_called_once()


def test_cpu_mode_never_probes_gpu(runner, monkeypatch):
    probe = Mock(side_effect=AssertionError("GPU probe in CPU mode"))
    monkeypatch.setattr(embeddings, "_probe", probe)
    runner.embed("input", "", "cpu")
    assert runner._start.call_args.args[-1] == "none"
    assert runner.mode == "cpu"
    probe.assert_not_called()


def test_explicit_cpu_start_disables_all_offload(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "CACHE", tmp_path)
    process = Mock()
    process.poll.return_value = None
    spawn = Mock(return_value=process)
    monkeypatch.setattr(embeddings.subprocess, "Popen", spawn)
    item = embeddings.LocalEmbedding()
    monkeypatch.setattr(item.session, "get", Mock(return_value=SimpleNamespace(status_code=200)))
    try:
        item._start(Path("fake-server"), Path("fake-model"), None, "none")
        args = spawn.call_args.args[0]
        assert args[args.index("--device") + 1] == "none"
        assert args[args.index("--n-gpu-layers") + 1] == "0"
        assert all(flag in args for flag in ["--no-mmproj-offload", "--no-op-offload", "--no-kv-offload"])
    finally:
        item.close()
        item.session.close()


def test_cancel_during_gpu_startup_never_retries(runner):
    def cancel(*args):
        embedding_jobs.event().set()
        raise RuntimeError("startup interrupted")
    runner._start.side_effect = cancel
    with embedding_jobs.operation(), pytest.raises(embedding_jobs.Cancelled):
        runner.embed("input", "", "local")
    runner._start.assert_called_once()
    runner._request.assert_not_called()

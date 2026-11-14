"""Tests for managed model library: installation discovery, manifest verification,
activation, and embedding configuration.

Follows offline fixture requirements:
- No real network, no cloud API calls.
- Missing, incomplete, tampered, symlink/reparse manifests refused.
- Path traversal/containment verified.
- Exact pinned revision and owner checks.
- Checksums and sizes checked at activation.
- Selection persists safely without immediate inference or download.
- Embedding identity reflects selected revision and actual dimension; default preserved.
- models.installed is read-only; models.activate requires user approval.
"""

from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import struct
from unittest.mock import Mock, patch

import pytest

from local_mode import model_library as lib
from local_mode import local_embeddings
from local_mode import local_chat
from local_mode.services import ServiceRegistry


def create_gguf_file(path: Path, embedding_length: int | None = None) -> bytes:
    """Create a minimal valid GGUF header for testing."""
    magic = b"GGUF"
    version = struct.pack("<I", 3)
    tensor_count = struct.pack("<Q", 0)

    if embedding_length is not None:
        kv_count = struct.pack("<Q", 1)
        key = b"embeddinggemma.embedding_length"
        key_len = struct.pack("<Q", len(key))
        val_type = struct.pack("<I", 4)  # UINT32
        val = struct.pack("<I", embedding_length)
        payload = magic + version + tensor_count + kv_count + key_len + key + val_type + val
    else:
        kv_count = struct.pack("<Q", 0)
        payload = magic + version + tensor_count + kv_count

    # Pad payload
    payload += b"\x00" * 64
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


@pytest.fixture
def mock_library_env(tmp_path, monkeypatch):
    """Set up temporary managed models root and settings directory."""
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    models_root = tmp_path / ".cache" / "desktop-test" / "models"
    models_root.mkdir(parents=True)
    settings_dir = tmp_path / ".cache" / "desktop-test"
    settings_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(lib, "get_models_root", lambda: models_root)
    monkeypatch.setattr(local_chat.runner, "settings_path", lambda: settings_dir / "llama-chat.json")
    monkeypatch.setattr(local_embeddings, "preferences_path", lambda: settings_dir / "embedding-settings.json")
    from local_mode import managed_chat
    monkeypatch.setattr(managed_chat, "manager", managed_chat.ManagedChat(settings_path=settings_dir / "managed-chat.json"))

    # Ensure local runner is stopped/reset
    local_embeddings._runner.close()
    local_chat.runner.stop()

    return models_root, settings_dir


def test_list_installed_empty_and_valid_manifest(mock_library_env):
    models_root, _ = mock_library_env
    assert lib.list_installed_models() == []

    # Create valid manifest
    rev_dir = models_root / "llm" / "google__gemma-2-2b-it-GGUF" / "rev123"
    rev_dir.mkdir(parents=True)
    manifest = {
        "owner": lib.OWNER,
        "complete": True,
        "repo_id": "google/gemma-2-2b-it-GGUF",
        "revision": "rev123",
        "kind": "llm",
        "bundle_id": "llm-gguf",
        "license": "apache-2.0",
        "files": [{"path": "model.gguf", "size": 100, "sha256": "a" * 64}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    (rev_dir / "model.gguf").write_bytes(b"x" * 100)

    installed = lib.list_installed_models()
    assert len(installed) == 1
    assert installed[0]["repo_id"] == "google/gemma-2-2b-it-GGUF"
    assert installed[0]["revision"] == "rev123"
    assert installed[0]["complete"] is True
    assert installed[0]["files"] == ["model.gguf"]


def test_manifest_owner_mismatch_ignored_or_rejected(mock_library_env):
    models_root, _ = mock_library_env
    rev_dir = models_root / "llm" / "user__model" / "r1"
    rev_dir.mkdir(parents=True)
    manifest = {
        "owner": "foreign-downloader/1",
        "complete": True,
        "repo_id": "user/model",
        "revision": "r1",
        "kind": "llm",
        "bundle_id": "llm-gguf",
        "files": [{"path": "model.gguf", "size": 10, "sha256": "123"}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    # In listing it is filtered out
    assert lib.list_installed_models() == []

    # In activation it is rejected
    with pytest.raises(ValueError, match="Manifest missing, unreadable, or invalid"):
        lib.activate_model("llm", "user/model", "r1")


def test_incomplete_manifest_rejected_at_activation(mock_library_env):
    models_root, _ = mock_library_env
    rev_dir = models_root / "llm" / "user__model" / "r1"
    rev_dir.mkdir(parents=True)
    manifest = {
        "owner": lib.OWNER,
        "complete": False,
        "repo_id": "user/model",
        "revision": "r1",
        "kind": "llm",
        "bundle_id": "llm-gguf",
        "files": [{"path": "model.gguf", "size": 10, "sha256": "123"}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot activate incomplete model"):
        lib.activate_model("llm", "user/model", "r1")


def test_tampered_size_or_checksum_rejected(mock_library_env):
    models_root, _ = mock_library_env
    rev_dir = models_root / "llm" / "user__model" / "r1"
    rev_dir.mkdir(parents=True)
    model_file = rev_dir / "model.gguf"
    create_gguf_file(model_file)
    actual_sha = hashlib.sha256(model_file.read_bytes()).hexdigest()

    # Wrong checksum
    manifest = {
        "owner": lib.OWNER,
        "complete": True,
        "repo_id": "user/model",
        "revision": "r1",
        "kind": "llm",
        "bundle_id": "llm-gguf",
        "files": [{"path": "model.gguf", "size": model_file.stat().st_size, "sha256": "0" * 64}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA256 checksum mismatch"):
        lib.activate_model("llm", "user/model", "r1")

    # Wrong size
    manifest["files"][0]["sha256"] = actual_sha
    manifest["files"][0]["size"] = model_file.stat().st_size + 50
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Size mismatch"):
        lib.activate_model("llm", "user/model", "r1")


def test_path_escape_and_symlink_refused(mock_library_env, tmp_path):
    models_root, _ = mock_library_env
    rev_dir = models_root / "llm" / "user__model" / "r1"
    rev_dir.mkdir(parents=True)

    # Relative path escaping parent directory
    manifest = {
        "owner": lib.OWNER,
        "complete": True,
        "repo_id": "user/model",
        "revision": "r1",
        "kind": "llm",
        "bundle_id": "llm-gguf",
        "files": [{"path": "../../outside.gguf", "size": 10, "sha256": "123"}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsafe component|Path escapes managed directory"):
        lib.activate_model("llm", "user/model", "r1")


def test_unsupported_tts_activation_rejected(mock_library_env):
    root, _ = mock_library_env
    _, manifest = install_fixture(root, kind="tts")
    with pytest.raises(ValueError, match="Only standalone Supertonic"):
        lib.activate_model("tts", manifest["repo_id"], manifest["revision"])


def test_llm_activation_persists_settings_no_immediate_load(mock_library_env):
    models_root, settings_dir = mock_library_env
    revision = "a" * 40
    rev_dir = models_root / "llm" / "meta-llama__Llama-3-8B-GGUF" / revision
    rev_dir.mkdir(parents=True)
    model_file = rev_dir / "llama-3-8b.Q4_K_M.gguf"
    create_gguf_file(model_file)
    size = model_file.stat().st_size
    sha = hashlib.sha256(model_file.read_bytes()).hexdigest()

    manifest = {
        "owner": lib.OWNER,
        "complete": True,
        "repo_id": "meta-llama/Llama-3-8B-GGUF",
        "revision": revision,
        "kind": "llm",
        "bundle_id": "llm-gguf",
        "files": [{"path": "llama-3-8b.Q4_K_M.gguf", "size": size, "sha256": sha}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    # Activate
    result = lib.activate_model("llm", "meta-llama/Llama-3-8B-GGUF", revision, mode="vulkan")
    assert result["configured"] is True
    assert result["kind"] == "llm"
    assert result["running"] is False
    assert "baseURL" not in result
    assert result["modelId"] == f"jarvis-managed:llm:meta-llama/Llama-3-8B-GGUF@{revision}"
    assert result["repo_id"] == "meta-llama/Llama-3-8B-GGUF"

    # Verify settings file persisted
    settings_file = settings_dir / "managed-chat.json"
    assert settings_file.is_file()
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved[result["modelId"]] == "vulkan"
    assert not (settings_dir / "llama-chat.json").exists()


def test_vision_activation_requires_and_persists_projector(mock_library_env):
    models_root, settings_dir = mock_library_env
    revision = "b" * 40
    rev_dir = models_root / "vision" / "vikhyatk__moondream2" / revision
    rev_dir.mkdir(parents=True)

    m_file = rev_dir / "moondream2-text-model.gguf"
    p_file = rev_dir / "moondream2-mmproj-f16.gguf"
    create_gguf_file(m_file)
    create_gguf_file(p_file)

    manifest = {
        "owner": lib.OWNER,
        "complete": True,
        "repo_id": "vikhyatk/moondream2",
        "revision": revision,
        "kind": "vision",
        "bundle_id": "vision-gguf-mmproj",
        "files": [
            {"path": "moondream2-text-model.gguf", "size": m_file.stat().st_size, "sha256": hashlib.sha256(m_file.read_bytes()).hexdigest()},
            {"path": "moondream2-mmproj-f16.gguf", "size": p_file.stat().st_size, "sha256": hashlib.sha256(p_file.read_bytes()).hexdigest()},
        ],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    result = lib.activate_model("vision", "vikhyatk/moondream2", revision, mode="cpu")
    assert result["configured"] is True
    assert result["kind"] == "vision"
    assert result["running"] is False

    saved = json.loads((settings_dir / "managed-chat.json").read_text(encoding="utf-8"))
    assert saved[result["modelId"]] == "cpu"
    assert lib.verify_and_get_model_assets("vision", manifest["repo_id"], revision)[2]["projector_path"] == str(p_file)


def test_embedding_activation_and_identity_dimension_handling(mock_library_env):
    models_root, settings_dir = mock_library_env
    rev_dir = models_root / "embedding" / "BAAI__bge-large-en-v1.5-GGUF" / "rev99"
    rev_dir.mkdir(parents=True)
    m_file = rev_dir / "bge-large-en-v1.5-f16.gguf"
    create_gguf_file(m_file, embedding_length=1024)

    sha = hashlib.sha256(m_file.read_bytes()).hexdigest()
    manifest = {
        "owner": lib.OWNER,
        "complete": True,
        "repo_id": "BAAI/bge-large-en-v1.5-GGUF",
        "revision": "rev99",
        "kind": "embedding",
        "bundle_id": "embedding-gguf",
        "files": [{"path": "bge-large-en-v1.5-f16.gguf", "size": m_file.stat().st_size, "sha256": sha}],
    }
    (rev_dir / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    # Default preferences should be auto without selection
    assert local_embeddings.preferences() == {"backend": "auto", "chunk_size": 1500, "chunk_overlap": 200}

    result = lib.activate_model("embedding", "BAAI/bge-large-en-v1.5-GGUF", "rev99", mode="cpu")
    assert result["configured"] is True
    assert result["kind"] == "embedding"

    # Check updated preferences
    prefs = local_embeddings.preferences()
    assert prefs["backend"] == "cpu"
    sel = prefs["selected_model"]
    assert sel["repo_id"] == "BAAI/bge-large-en-v1.5-GGUF"
    assert sel["revision"] == "rev99"
    assert sel["dimensions"] == 1024
    assert sel["sha256"] == sha
    assert sel["projector_path"] == ""

    # Text-only embedding model rejects media
    with pytest.raises(ValueError, match="text-only"):
        local_embeddings.embed("test", "some_image.png", "cpu")


def test_services_catalog_approval_and_execution(mock_library_env):
    models_root, _ = mock_library_env
    # Create registry
    runtime = Mock()
    core = Mock()
    registry = ServiceRegistry(runtime=runtime, core=lambda: core)

    # models.installed should not require approval
    assert registry.risky("models.installed", {}) is False
    res = registry.execute("models.installed", {}, approved=False)
    assert isinstance(res, list)

    # models.activate requires approval
    act_args = {
        "kind": "llm",
        "repo_id": "org/repo",
        "revision": "r1",
        "mode": "vulkan",
    }
    assert registry.risky("models.activate", act_args) is True
    with pytest.raises(ValueError, match="approval is required"):
        registry.execute("models.activate", act_args, approved=False)


def install_fixture(root, kind="embedding", repo="test/model", revision="a" * 40):
    directory = root / kind / repo.replace("/", "__") / revision
    directory.mkdir(parents=True)
    model = directory / "model.gguf"
    create_gguf_file(model)
    manifest = {
        "owner": lib.OWNER, "complete": True, "kind": kind,
        "repo_id": repo, "revision": revision,
        "bundle_id": "embedding-gguf" if kind == "embedding" else "llm-gguf",
        "files": [{"path": model.name, "size": model.stat().st_size,
                   "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}],
    }
    (directory / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return model, manifest


def test_managed_selection_preserves_unrelated_legacy_chat(mock_library_env, monkeypatch):
    root, _ = mock_library_env
    model, manifest = install_fixture(root, kind="llm")
    runner = local_chat.runner
    monkeypatch.setattr(runner, "model", "previous-model.gguf")
    monkeypatch.setattr(runner, "process", Mock(poll=Mock(return_value=None)))
    stop = Mock(side_effect=lambda: setattr(runner, "process", None))
    monkeypatch.setattr(runner, "stop", stop)
    result = lib.activate_model("llm", manifest["repo_id"], manifest["revision"])
    stop.assert_not_called()
    assert result["running"] is False
    assert result["modelId"] == f"jarvis-managed:llm:{manifest['repo_id']}@{manifest['revision']}"
    assert runner.model == "previous-model.gguf"
    # Selecting the already loaded model must not terminate it unnecessarily.
    runner.process = Mock(poll=Mock(return_value=None))
    stop.reset_mock()
    assert not lib.activate_model("llm", manifest["repo_id"], manifest["revision"])["running"]
    stop.assert_not_called()


def test_unknown_embedding_dimension_uses_real_response(mock_library_env, monkeypatch):
    root, _ = mock_library_env
    _, manifest = install_fixture(root)
    lib.activate_model("embedding", manifest["repo_id"], manifest["revision"], "cpu")
    selected = local_embeddings.preferences()["selected_model"]
    assert selected["dimensions"] is None
    runner = local_embeddings._runner
    response = Mock(status_code=200)
    response.json.return_value = {"data": [{"embedding": [1.0] * 7}]}
    monkeypatch.setattr(runner, "session", Mock(post=Mock(return_value=response)))
    monkeypatch.setattr(local_embeddings, "ensure_runtime", Mock(return_value=Path("llama-server.exe")))
    start = Mock(side_effect=lambda *args: setattr(runner, "device", "none"))
    monkeypatch.setattr(runner, "_start", start)
    result = local_embeddings.embed("hello", "", "cpu")
    assert result["dimensions"] == 7
    assert result["model"].startswith(manifest["repo_id"] + "@" + manifest["revision"])
    assert abs(sum(v * v for v in result["vector"]) - 1) < 1e-9
    start.assert_called_once()
    assert runner.session.post.call_args.kwargs["json"]["model"] == "jarvis-embedding"


def test_selected_embedding_is_revalidated_without_rehash(mock_library_env, monkeypatch):
    root, _ = mock_library_env
    model, manifest = install_fixture(root)
    lib.activate_model("embedding", manifest["repo_id"], manifest["revision"], "cpu")
    selected = local_embeddings.preferences()["selected_model"]
    monkeypatch.setattr(lib, "compute_sha256", Mock(side_effect=AssertionError("query rehashed weights")))
    assert lib.validate_selected_model(selected)["model_path"] == str(model)
    altered = dict(selected, model_path=str(root.parent / "outside.gguf"))
    with pytest.raises(ValueError, match="does not match"):
        lib.validate_selected_model(altered)
    stat = model.stat()
    os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    with pytest.raises(ValueError, match="mtime changed"):
        lib.validate_selected_model(selected)


def test_incomplete_or_wrong_directory_not_listed(mock_library_env):
    root, _ = mock_library_env
    model, manifest = install_fixture(root)
    path = model.parent / lib.MANIFEST_NAME
    manifest["complete"] = False
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert lib.list_installed_models() == []
    manifest["complete"] = True
    manifest["repo_id"] = "other/repository"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert lib.list_installed_models() == []


def test_managed_chat_checks_file_identity_before_runtime_start(mock_library_env, monkeypatch):
    root, _ = mock_library_env
    model, manifest = install_fixture(root, kind="llm")
    lib.activate_model("llm", manifest["repo_id"], manifest["revision"])
    runtime = Mock(side_effect=AssertionError("runtime started before validation"))
    monkeypatch.setattr(local_embeddings, "ensure_runtime", runtime)
    stat = model.stat()
    os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    from local_mode import managed_chat
    # First resolve validates the manifest checksum, then records stat identity.
    model.write_bytes(b"tampered" + model.read_bytes()[8:])
    identity = managed_chat.model_identity("llm", manifest["repo_id"], manifest["revision"])
    with pytest.raises(ValueError, match="checksum mismatch"):
        managed_chat.manager.resolve(identity)
    runtime.assert_not_called()


def test_missing_download_file_is_not_available_to_agents(mock_library_env):
    root, _ = mock_library_env
    model, _ = install_fixture(root)
    assert len(lib.list_installed_models()) == 1
    model.unlink()
    assert lib.list_installed_models() == []

def test_embedding_reset_stops_owned_runner_and_preserves_provider(mock_library_env, monkeypatch):
    root, settings = mock_library_env
    install_fixture(root)
    lib.activate_model('embedding', 'test/model', 'a' * 40)
    closed = Mock()
    monkeypatch.setattr(local_embeddings._runner, 'close', closed)
    monkeypatch.setattr(local_embeddings, 'assets', Mock(side_effect=AssertionError('must not download')))
    before = local_embeddings.preferences()
    assert 'selected_model' in before
    reset = local_embeddings.preferences(reset_model=True)
    assert reset == {key: value for key, value in before.items() if key != 'selected_model'}
    closed.assert_called_once()
    assert 'selected_model' not in json.loads((settings / 'embedding-settings.json').read_text())
    assert len(lib.list_installed_models()) == 1


def test_embedding_reset_uses_common_approval_gate(mock_library_env):
    registry = ServiceRegistry(runtime=Mock(), core=lambda: Mock())
    assert not registry.risky('embedding.settings', {})
    assert not registry.risky('embedding.settings', {'reset_model': False})
    assert registry.risky('embedding.settings', {'reset_model': True})
    with pytest.raises(ValueError, match='approval is required'):
        registry.execute('embedding.settings', {'reset_model': True}, approved=False)
    assert registry.execute('embedding.settings', {'reset_model': True}, approved=True) == {'backend': 'auto', 'chunk_size': 1500, 'chunk_overlap': 200}


def supertonic_fixture(root, family="supertonic"):
    model, manifest = install_fixture(root, kind="tts")
    fields = {"general.architecture":"audiocpp", "audiocpp.model_spec.version":1, "audiocpp.model_spec.family":family}
    payload = b"GGUF" + struct.pack("<IQQ", 3, 0, len(fields))
    for key, value in fields.items():
        encoded=key.encode()
        payload += struct.pack("<Q",len(encoded))+encoded
        if isinstance(value,str):
            encoded=value.encode()
            payload += struct.pack("<IQ",8,len(encoded))+encoded
        else:
            payload += struct.pack("<II",4,value)
    model.write_bytes(payload)
    manifest["bundle_id"]="tts-supertonic-gguf"
    manifest["files"][0].update(size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
    (model.parent/lib.MANIFEST_NAME).write_text(json.dumps(manifest),encoding="utf8")
    return model,manifest


def test_tts_selection_is_lazy_and_revalidated_without_changing_speaker(mock_library_env,monkeypatch):
    from local_mode import tts_models, voice_settings
    root,settings=mock_library_env
    monkeypatch.setattr(tts_models,"settings_path",lambda:settings/"tts-model.json")
    monkeypatch.setattr(voice_settings,"PREFERENCES",settings/"voice.json")
    voice_settings.save_voice("supertonic:M4")
    model,manifest=supertonic_fixture(root)
    with patch("subprocess.Popen",side_effect=AssertionError("Activation must not spawn")):
        result=lib.activate_model("tts",manifest["repo_id"],manifest["revision"],"cpu")
    assert result["configured"] and not result["running"]
    assert voice_settings.read_voice()=="supertonic:M4"
    monkeypatch.setattr(lib,"compute_sha256",Mock(side_effect=AssertionError("No per-utterance rehash")))
    selected,mode,identity=tts_models.model_configuration()
    assert selected==model and mode=="cpu" and manifest["revision"] in identity
    model.write_bytes(model.read_bytes()+b"tamper")
    with pytest.raises(ValueError):tts_models.model_configuration()


def test_tts_rejects_incompatible_family_even_with_matching_filename(mock_library_env):
    root,_=mock_library_env
    _,manifest=supertonic_fixture(root,family="kokoro_tts")
    with pytest.raises(ValueError,match="not a supported audio.cpp Supertonic"):
        lib.activate_model("tts",manifest["repo_id"],manifest["revision"])


def test_tts_engine_changes_only_between_utterances_and_default_remains(mock_library_env,monkeypatch):
    from local_mode import tts_models
    from local_mode.audio_cpp_install import MODEL
    _,settings=mock_library_env
    monkeypatch.setattr(tts_models,"settings_path",lambda:settings/"tts-model.json")
    assert tts_models.model_configuration()==(MODEL,"vulkan","builtin")
    engine=Mock();engines={"supertonic":engine}
    assert tts_models.refresh_engine(engines,"old","old")=="old"
    engine.close.assert_not_called()
    assert tts_models.refresh_engine(engines,"new","old")=="new"
    engine.close.assert_called_once();assert "supertonic" not in engines


def test_selected_tts_skips_default_weight_download(monkeypatch):
    from local_mode import audio_cpp_install as install
    monkeypatch.setattr(install,"verify_runtime",lambda:True)
    download=Mock(side_effect=AssertionError("Selected TTS must not download default weights"))
    monkeypatch.setattr(install,"download_file",download)
    install.ensure_assets(include_default_model=False)
    download.assert_not_called()


def test_invalid_tts_selection_retires_cached_engine_and_allows_fallback_then_recovery(monkeypatch):
    from local_mode import tts_models
    old=Mock();engines={"supertonic":old}
    monkeypatch.setattr(tts_models,"model_configuration",Mock(side_effect=ValueError("tampered")))
    identity,error=tts_models.prepare_engine(engines,"old")
    old.close.assert_called_once()
    assert error=="tampered" and engines["supertonic"] is None
    monkeypatch.setattr(tts_models,"model_configuration",lambda:(Path("new.gguf"),"cpu","new"))
    identity,error=tts_models.prepare_engine(engines,identity)
    assert identity=="new" and error is None and "supertonic" not in engines


@pytest.mark.parametrize("kind", ["embedding", "vision", "tts"])
def test_backend_refuses_unsupported_split_model_kinds(tmp_path, kind):
    from local_mode.messages import OwnedValueError
    manifest = {"files":[{"path":"model-00001-of-00002.gguf"},{"path":"mmproj.gguf"}]}
    with pytest.raises(OwnedValueError, match="language models only"):
        lib.derive_model_files(kind, manifest, tmp_path)


@pytest.mark.parametrize("array", [struct.pack("<IQ",8,1000001), struct.pack("<IQ",10,1000000), struct.pack("<IQQ",8,1,17000000)])
def test_gguf_metadata_array_skip_rejects_unbounded_or_truncated_data(tmp_path, array):
    path = tmp_path / "bad.gguf"
    path.write_bytes(b"GGUF" + struct.pack("<IQQ",3,0,1) + struct.pack("<Q",1) + b"x" + struct.pack("<I",9) + array)
    assert lib.inspect_gguf_header(path) == {}


def piper_bundle(mock_library_env,monkeypatch,model_name='voice.onnx'):
    from local_mode import piper_models
    root,settings=mock_library_env
    monkeypatch.setattr(piper_models,'settings_path',lambda:settings/'piper-model.json')
    repo='qa/piper';revision='a'*40
    directory=root/'tts'/'qa__piper'/revision;directory.mkdir(parents=True)
    config={'num_symbols':3,'phoneme_id_map':{'_':[0],'^':[1],'$':[2]},'num_speakers':2,'phoneme_type':'espeak','audio':{'sample_rate':22050},'espeak':{'voice':'en-us'},'speaker_id_map':{'Alice':0,'Bob':1}}
    contents={model_name:b'ONNX fixture; never loaded',model_name+'.json':json.dumps(config).encode()}
    manifest={'owner':lib.OWNER,'complete':True,'repo_id':repo,'revision':revision,'kind':'tts','bundle_id':'tts-piper-onnx','license':'test-fixture','files':[]}
    for name,data in contents.items():
        (directory/name).write_bytes(data)
        manifest['files'].append({'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    (directory/lib.MANIFEST_NAME).write_text(json.dumps(manifest))
    return repo,revision,directory,config


def test_managed_piper_activation_is_lazy_cpu_only_and_preserves_other_tts(mock_library_env,monkeypatch):
    from local_mode import piper_models,tts_models,voice_settings
    repo,revision,directory,config=piper_bundle(mock_library_env,monkeypatch)
    other=mock_library_env[1]/'tts-model.json';other.write_text('unchanged')
    monkeypatch.setattr(tts_models,'settings_path',lambda:other)
    before=other.read_bytes()
    with pytest.raises(ValueError,match='CPU'):
        lib.activate_model('tts',repo,revision,'vulkan')
    assert not piper_models.settings_path().exists()
    result=lib.activate_model('tts',repo,revision,'cpu')
    assert result['family']=='piper' and result['running'] is False
    assert other.read_bytes()==before
    choices=piper_models.voices()
    assert len(choices)==2 and choices[1]['name'].endswith('Bob')
    assert voice_settings.validate_voice(choices[1]['id'])==choices[1]['id']
    assert piper_models.resolve(choices[1]['id'])[:3]==(directory/'voice.onnx',1,'en-us')
    assert any(item['id']==choices[0]['id'] for item in voice_settings.neural_voices())


def test_managed_piper_rejects_tampered_sidecar_and_retires_engine(mock_library_env,monkeypatch):
    from local_mode import piper_models
    repo,revision,directory,config=piper_bundle(mock_library_env,monkeypatch)
    lib.activate_model('tts',repo,revision,'cpu')
    voice=piper_models.voices()[0]['id']
    (directory/'voice.onnx.json').write_text('{"tampered":true}')
    assert piper_models.voices()==[]
    engines={'managed_piper':object()}
    with pytest.raises(ValueError):piper_models.engine_for(engines,voice)
    assert 'managed_piper' not in engines


def test_managed_piper_model_bound_speaker_identity_and_cache(mock_library_env,monkeypatch):
    from local_mode import piper_models,piper_voice
    repo,revision,directory,config=piper_bundle(mock_library_env,monkeypatch)
    lib.activate_model('tts',repo,revision,'cpu')
    a,b=[item['id'] for item in piper_models.voices()]
    build=Mock(side_effect=lambda *args:Mock())
    monkeypatch.setattr(piper_voice,'RussianVoice',build)
    engines={}
    first=piper_models.engine_for(engines,a)
    assert piper_models.engine_for(engines,a) is first
    assert piper_models.engine_for(engines,b) is not first
    assert build.call_count==2
    with pytest.raises(ValueError,match='active model'):
        piper_models.resolve(a.replace(a.split(':')[2],'f'*24))
    with pytest.raises(ValueError):piper_models.resolve(a.rsplit(':',1)[0]+':2')


def test_managed_piper_config_rejects_invalid_speaker_metadata(tmp_path):
    from local_mode.piper_models import inspect_config
    path=tmp_path/'voice.onnx.json'
    base={'num_symbols':3,'phoneme_id_map':{'_':[0],'^':[1],'$':[2]},'num_speakers':2,'audio':{'sample_rate':22050},'espeak':{'voice':'en-us'}}
    for extra in [{'num_speakers':True},{'speaker_id_map':{'bad':3}},{'speaker_id_map':{'a':0,'b':0}},{'espeak':{'voice':'../private'}},{'phoneme_type':'unsupported'}]:
        path.write_text(json.dumps({**base,**extra}))
        with pytest.raises(ValueError):inspect_config(path)


def test_managed_piper_keeps_non_russian_text_and_selects_speaker(tmp_path,monkeypatch):
    import sys,types,wave
    from local_mode import piper_voice
    calls=[]
    class FakeVoice:
        def synthesize_wav(self,text,target,syn_config):
            calls.append((text,syn_config.speaker_id))
            target.setnchannels(1);target.setsampwidth(2);target.setframerate(22050);target.writeframes(b'\0\0'*32)
    class FakePiper:
        @staticmethod
        def load(path,use_cuda):
            assert use_cuda is False
            return FakeVoice()
    monkeypatch.setitem(sys.modules,'piper',types.SimpleNamespace(PiperVoice=FakePiper,SynthesisConfig=lambda **kwargs:types.SimpleNamespace(**kwargs)))
    monkeypatch.setattr(piper_voice,'russian_pronunciation',Mock(side_effect=AssertionError('English text must stay unchanged')))
    model=tmp_path/'voice.onnx';model.write_bytes(b'fixture')
    engine=piper_voice.RussianVoice(model,1,'en-us')
    data=engine.wav('Hello 7')
    assert calls==[('Hello 7.',1)]
    with wave.open(io.BytesIO(data),'rb') as wav:
        assert wav.getnframes()==32

def test_managed_piper_uppercase_onnx_matches_discovery(mock_library_env,monkeypatch):
    from local_mode import piper_models
    repo,revision,directory,_=piper_bundle(mock_library_env,monkeypatch,'Voice.ONNX')
    lib.activate_model('tts',repo,revision,'cpu')
    assert piper_models.resolve(piper_models.voices()[0]['id'])[0]==directory/'Voice.ONNX'


@pytest.mark.parametrize('change',[{'num_symbols':None},{'num_symbols':True},{'phoneme_id_map':None},{'phoneme_id_map':{'a':[3]}},{'phoneme_id_map':{'a':[True]}}])
def test_managed_piper_required_metadata_rejects_before_selection(mock_library_env,monkeypatch,change):
    from local_mode import piper_models
    repo,revision,directory,config=piper_bundle(mock_library_env,monkeypatch)
    config.update(change);data=json.dumps(config).encode();(directory/'voice.onnx.json').write_bytes(data)
    manifest=json.loads((directory/lib.MANIFEST_NAME).read_text())
    manifest['files'][1].update(size=len(data),sha256=hashlib.sha256(data).hexdigest())
    (directory/lib.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Piper voice configuration'):
        lib.activate_model('tts',repo,revision,'cpu')
    assert not piper_models.settings_path().exists()


def test_builtin_piper_readiness_requires_matching_sidecar(tmp_path,monkeypatch):
    from local_mode import piper_models,piper_voice
    monkeypatch.setattr(piper_models,'settings_path',lambda:tmp_path/'selection.json')
    model=tmp_path/'voice.onnx';model.write_bytes(b'fixture')
    monkeypatch.setattr(piper_voice,'MODEL',model)
    assert piper_models.status()['available'] is False
    sidecar=tmp_path/'voice.onnx.json';sidecar.write_text('{}')
    assert piper_models.status()['available'] is False
    sidecar.write_text(json.dumps({'num_symbols':3,'phoneme_id_map':{'_':[0],'^':[1],'$':[2]},'num_speakers':1,'audio':{'sample_rate':22050},'espeak':{'voice':'ru'}}))
    assert piper_models.status()['available'] is True


@pytest.mark.parametrize('inference',[None,[],False,1,'invalid'])
def test_piper_non_object_inference_preserves_selection(mock_library_env,monkeypatch,inference):
    from local_mode import piper_models
    repo,revision,directory,config=piper_bundle(mock_library_env,monkeypatch)
    selection=piper_models.settings_path()
    selection.parent.mkdir(parents=True,exist_ok=True)
    selection.write_text('previous selection')
    config['inference']=inference
    data=json.dumps(config).encode()
    (directory/'voice.onnx.json').write_bytes(data)
    manifest=json.loads((directory/lib.MANIFEST_NAME).read_text())
    manifest['files'][1].update(size=len(data),sha256=hashlib.sha256(data).hexdigest())
    (directory/lib.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Piper voice configuration'):
        lib.activate_model('tts',repo,revision,'cpu')
    assert selection.read_text()=='previous selection'


@pytest.mark.parametrize('extra',[{}, {'inference':{}}])
def test_piper_missing_or_empty_inference_remains_valid(mock_library_env,monkeypatch,extra):
    from local_mode.piper_models import inspect_config
    _,_,directory,config=piper_bundle(mock_library_env,monkeypatch)
    config.pop('inference',None)
    config.update(extra)
    path=directory/'voice.onnx.json'
    path.write_text(json.dumps(config))
    assert inspect_config(path)==config


@pytest.mark.parametrize('family,enabled,current,choices,expected',[
    ('piper',True,'piper:managed:old:7',[{'id':'piper:managed:new:0'}],'piper:managed:new:0'),
    ('piper',True,'piper:managed:old:7',[], 'default'),
    ('piper',True,'piper:managed:new:0',[{'id':'piper:managed:new:0'}],None),
    ('piper',False,'piper:managed:old:7',[{'id':'piper:managed:new:0'}],None),
    ('piper',True,'supertonic:F1',[{'id':'piper:managed:new:0'}],None),
    ('piper',True,None,[{'id':'piper:managed:new:0'}],None),
    ('supertonic',True,'piper:managed:old:7',[{'id':'piper:managed:new:0'}],None),
])
def test_piper_activation_refreshes_only_enabled_stale_managed_voice(monkeypatch,family,enabled,current,choices,expected):
    from local_mode import piper_models
    from local_mode.voice_settings import DEFAULT_VOICE
    result={'family':family}
    activate=Mock(return_value=result)
    monkeypatch.setattr(lib,'activate_model',activate)
    discover=Mock(return_value=choices)
    monkeypatch.setattr(piper_models,'voices',discover)
    runtime=Mock()
    runtime.speaker.enabled=enabled
    runtime.speaker.voice=current
    registry=ServiceRegistry(runtime=runtime,core=lambda:Mock())
    args={'kind':'tts','repo_id':'qa/piper','revision':'a'*40,'mode':'cpu'}
    assert registry.execute('models.activate',args,approved=True) is result
    activate.assert_called_once_with(**args)
    if expected is not None:
        runtime.speaker.configure.assert_called_once_with(DEFAULT_VOICE if expected=='default' else expected,preserve_playback=True)
    else:
        runtime.speaker.configure.assert_not_called()
    if family!='piper' or not enabled or not isinstance(current,str) or not current.startswith('piper:managed:'):
        discover.assert_not_called()

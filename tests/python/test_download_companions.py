"""Companion format validation is separate from publisher checksum integrity."""
import hashlib
import threading
from unittest.mock import Mock

import pytest

from local_mode.downloads import download_file


def response(monkeypatch, data):
    reply = Mock()
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    reply.iter_content.return_value = [data]
    get = Mock(return_value=reply)
    monkeypatch.setattr("local_mode.downloads.requests.get", get)
    return get


@pytest.mark.parametrize("cached", [b"", b"broken", b"[]", b'"wrong"', b"\xff"])
def test_invalid_cached_json_is_replaced(monkeypatch, tmp_path, cached):
    target = tmp_path / "config.json"
    target.write_bytes(cached)
    get = response(monkeypatch, b'{"audio": {}}')
    download_file("https://fixture/config", target, max_bytes=100, content_format="json-object")
    assert get.call_count == 1
    assert target.read_bytes() == b'{"audio": {}}'


def test_valid_cached_json_does_not_download(monkeypatch, tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"audio": {}}', encoding="utf-8")
    get = response(monkeypatch, b"unused")
    download_file("https://fixture/config", target, max_bytes=100, content_format="json-object")
    get.assert_not_called()


@pytest.mark.parametrize("data", [b"", b"not-json", b"[]", b"\xff", b"\x00"])
def test_invalid_transfer_preserves_destination(monkeypatch, tmp_path, data):
    target = tmp_path / "config.json"
    target.write_bytes(b"old-invalid")
    response(monkeypatch, data)
    with pytest.raises((ValueError, UnicodeError)):
        download_file("https://fixture/config", target, max_bytes=100, content_format="json-object")
    assert target.read_bytes() == b"old-invalid"
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize("cached", [b"", b"  ", b"\xff", b"bad\x00text"])
def test_invalid_cached_text_is_replaced(monkeypatch, tmp_path, cached):
    target = tmp_path / "vocabulary.txt"
    target.write_bytes(cached)
    response(monkeypatch, b"hello\nworld\n")
    download_file("https://fixture/vocab", target, max_bytes=100, content_format="utf8-text")
    assert target.read_bytes() == b"hello\nworld\n"


def test_hashed_binary_remains_supported(monkeypatch, tmp_path):
    target = tmp_path / "model.bin"
    data = b"\x00\xffmodel"
    get = response(monkeypatch, data)
    digest = hashlib.sha256(data).hexdigest()
    download_file("https://fixture/model", target, max_bytes=100, sha256=digest)
    download_file("https://fixture/model", target, max_bytes=100, sha256=digest)
    assert get.call_count == 1
    assert target.read_bytes() == data


def test_cancelled_repair_preserves_destination(monkeypatch, tmp_path):
    target = tmp_path / "config.json"
    target.write_bytes(b"invalid")
    get = response(monkeypatch, b"{}")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        download_file("https://fixture/config", target, max_bytes=100, cancel_event=cancel,
                      content_format="json-object")
    assert target.read_bytes() == b"invalid"
    get.assert_not_called()


def test_cancellation_before_publication_preserves_destination(monkeypatch, tmp_path):
    target = tmp_path / "config.json"
    target.write_bytes(b"invalid")
    cancel = threading.Event()
    get = response(monkeypatch, b"{}")
    reply = get.return_value
    reply.raw = None

    def chunks(*args):
        yield b"{}"
        cancel.set()

    reply.iter_content.side_effect = chunks
    with pytest.raises(RuntimeError, match="cancelled"):
        download_file("https://fixture/config", target, max_bytes=100, cancel_event=cancel,
                      content_format="json-object")
    assert target.read_bytes() == b"invalid"
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize("engine", ["piper", "whisper"])
def test_voice_install_refuses_malformed_companion(monkeypatch, tmp_path, engine):
    from local_mode import piper_voice, whisper_voice

    module = piper_voice if engine == "piper" else whisper_voice
    model = tmp_path / ("voice.onnx" if engine == "piper" else "whisper")
    model_file = model if engine == "piper" else model / "model.bin"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_bytes(b"fixture-weight")
    monkeypatch.setattr(module, "MODEL", model)
    monkeypatch.setattr(module, "MODEL_SHA256", hashlib.sha256(b"fixture-weight").hexdigest())
    response(monkeypatch, b"not-json")
    with pytest.raises(ValueError):
        module.ensure_model()
    assert model_file.read_bytes() == b"fixture-weight"
    assert not (model.with_name(model.name + ".json") if engine == "piper" else model / "config.json").exists()

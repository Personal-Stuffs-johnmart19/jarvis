"""Qwen fixed identity, bounded long-form work and bootstrap cache ownership."""
from contextlib import nullcontext
import hashlib
import io
import json
from threading import Event
from unittest.mock import Mock
import wave
import httpx
import pytest
from local_mode import qwen_voice as q


def pcm(seconds=3):
    result = io.BytesIO()
    with wave.open(result, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\x01\x00' * int(24000 * seconds))
    return result.getvalue()


@pytest.fixture
def reference(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'DIRECTORY', tmp_path)
    monkeypatch.setattr(q, 'REFERENCE_DIRECTORY', tmp_path / 'qwen-reference')
    q.REFERENCE_DIRECTORY.mkdir()
    data = pcm()
    path = q.REFERENCE_DIRECTORY / 'reference.wav'
    path.write_bytes(data)
    manifest = {**q._reference_identity(), 'sha256': hashlib.sha256(data).hexdigest()}
    (q.REFERENCE_DIRECTORY / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    return path


def test_valid_owned_reference_skips_design_download_and_gpu_bootstrap(reference, monkeypatch):
    download = Mock(side_effect=AssertionError('reference already exists'))
    designer = Mock(side_effect=AssertionError('reference already exists'))
    monkeypatch.setattr(q, 'ensure_model', download)
    monkeypatch.setattr(q, '_DesignVoice', designer)
    assert q.ensure_reference(Event()) == reference
    download.assert_not_called(); designer.assert_not_called()


def test_oversized_reference_manifest_is_rejected_before_decoding(reference):
    reference.with_name('manifest.json').write_bytes(b'x' * 16_385)
    assert q._cached_reference() is None


def test_base_and_verified_reference_stay_available_without_design_weights(reference, monkeypatch):
    monkeypatch.setattr(q, 'MODEL', reference.parent / 'missing-design.gguf')
    base = reference.parent / 'base.gguf'
    base.write_bytes(b'installed')
    monkeypatch.setattr(q, 'BASE_MODEL', base)
    assert q.available()
    reference.unlink()
    assert not q.available()


def test_qwen_accepts_the_full_chat_speech_contract_before_chunking(monkeypatch):
    from local_mode.voice import LocalSpeaker
    monkeypatch.setattr('local_mode.voice.read_voice', lambda: q.VOICE_ID)
    speaker = LocalSpeaker()
    speaker._thread = Mock(is_alive=Mock(return_value=True))
    text = 'A long monologue. ' * 600
    try:
        future = speaker.say(text)
        queued = speaker._queue.get_nowait()
        speaker._queue.task_done()
        assert future is queued[3] and queued[0] == text and len(text) > 4000
    finally:
        speaker._thread = None
        speaker.close()


@pytest.mark.parametrize('corruption', ['checksum', 'identity', 'invalid-wav', 'manifest-type'])
def test_corrupted_reference_is_not_accepted(reference, corruption):
    manifest_path = reference.with_name('manifest.json')
    if corruption == 'checksum': reference.write_bytes(pcm(4))
    elif corruption == 'invalid-wav': reference.write_bytes(b'not-a-wav')
    elif corruption == 'manifest-type': manifest_path.write_text('[]')
    else:
        manifest = json.loads(manifest_path.read_text())
        manifest['instruction'] = 'different voice'
        manifest_path.write_text(json.dumps(manifest))
    assert q._cached_reference() is None


def test_reference_bootstrap_publishes_checksum_then_reuses_without_inference(reference, monkeypatch):
    reference.unlink()
    design = Mock()
    design.wav.return_value = pcm()
    monkeypatch.setattr(q, 'ensure_model', Mock())
    factory = Mock(return_value=design)
    monkeypatch.setattr(q, '_DesignVoice', factory)
    assert q.ensure_reference() == reference
    assert q.ensure_reference() == reference
    factory.assert_called_once_with(None)
    design.wav.assert_called_once_with(q.REFERENCE_TEXT)
    design.close.assert_called_once()
    assert q._cached_reference() == reference


def test_cancelled_reference_generation_closes_designer_without_publication(reference, monkeypatch):
    reference.unlink()
    cancel = Event()
    design = Mock()
    def generate(text):
        cancel.set()
        return pcm()
    design.wav.side_effect = generate
    monkeypatch.setattr(q, 'ensure_model', Mock())
    monkeypatch.setattr(q, '_DesignVoice', Mock(return_value=design))
    with pytest.raises(RuntimeError, match='cancelled'): q.ensure_reference(cancel)
    design.close.assert_called_once()
    assert not reference.exists()


def test_paragraph_coherence_preserves_line_and_paragraph_boundaries():
    text = 'A complete sentence. ' * 20
    parts = q.qwen_parts(text + '\nA line.\n\nLast paragraph.')
    assert len(parts) == 3 and len(parts[0].text) > 240
    assert [p.pause_ms for p in parts] == [320, 500, 0]
    assert all(len(p.text) <= q.FRAGMENT_LIMIT for p in parts)
    assert ' '.join(p.text for p in q.qwen_parts('word ' * 1200)) == ('word ' * 1200).strip()


def test_base_requests_reuse_exact_reference_and_language_without_voice_redesign(reference):
    engine = q.QwenVoice.__new__(q.QwenVoice)
    engine.reference = reference
    engine.process = Mock(); engine.process.poll.return_value = None
    engine.generating = Event(); engine.base = 'http://127.0.0.1:1'; engine.model_id = 'test'
    engine.client = Mock()
    def response(*args, **kwargs):
        return nullcontext(httpx.Response(200, content=pcm(), request=httpx.Request('POST', engine.base)))
    engine.client.stream.side_effect = response
    for text in ['Hello friend.', 'Привет, друг.']:
        engine.wav(text)
    payloads = [c.kwargs['json'] for c in engine.client.stream.call_args_list]
    assert [p['language'] for p in payloads] == ['en', 'ru']
    for payload in payloads:
        assert payload['voice_ref'] == {'type': 'path', 'path': str(reference)}
        assert payload['reference_text'] == q.REFERENCE_TEXT
        assert 'instruct' not in payload['options']
        assert payload['options']['x_vector_only_mode'] is False


def test_audio_at_token_ceiling_is_rejected_instead_of_silently_cut(reference):
    engine = q.QwenVoice.__new__(q.QwenVoice)
    engine.reference = reference
    engine.process = Mock(); engine.process.poll.return_value = None
    engine.generating = Event(); engine.base = 'http://127.0.0.1:1'; engine.model_id = 'test'
    engine.client = Mock()
    engine.client.stream.return_value = nullcontext(httpx.Response(200, content=pcm(41), request=httpx.Request('POST', engine.base)))
    with pytest.raises(RuntimeError, match='generation limit'): engine.wav('Hello!')
    assert not engine.generating.is_set()


def test_play_preserves_paragraph_pauses_and_propagates_gpu_error(monkeypatch):
    engine = q.QwenVoice.__new__(q.QwenVoice)
    engine.wav = Mock(return_value=b'synthetic')
    engine.cancel = Mock()
    monkeypatch.setattr(q, 'trim_wav_edges', lambda data: data)
    playback = Mock()
    monkeypatch.setattr(q, 'play_wav', playback)
    engine.play('One paragraph.\n\nAnother paragraph.', Event())
    assert [c.args[2] for c in playback.call_args_list] == [500, 0]
    engine.wav.side_effect = RuntimeError('GPU failed')
    with pytest.raises(RuntimeError, match='GPU failed'): engine.play('Hello!', Event())


def test_cancelled_play_skips_all_gpu_requests():
    engine = q.QwenVoice.__new__(q.QwenVoice)
    engine.wav = Mock()
    stop = Event(); stop.set()
    engine.play('Hello!', stop)
    engine.wav.assert_not_called()


def test_base_download_is_revision_and_checksum_pinned(monkeypatch):
    download = Mock()
    monkeypatch.setattr(q, 'download_file', download)
    cancel = Event()
    q.ensure_base_model(cancel)
    call = download.call_args
    assert '/resolve/874fcb41ececda20bcbc0782d6b4ea7f7d069ea8/' in call.args[0]
    assert call.args[1] == q.BASE_MODEL
    assert call.kwargs == {'max_bytes': 2695186240,
        'sha256': '92641a8022d521efd79a5db45e952589b3f2b781680f9abfcf26f075d37b86c6',
        'cancel_event': cancel}


def test_reference_cancel_watcher_interrupts_active_bootstrap(reference, monkeypatch):
    reference.unlink()
    cancel = Event(); cancelled_inference = Event()
    design = Mock()
    def generate(text):
        cancel.set()
        assert cancelled_inference.wait(2)
        return pcm()
    design.wav.side_effect = generate
    design.cancel.side_effect = cancelled_inference.set
    monkeypatch.setattr(q, 'ensure_model', Mock())
    monkeypatch.setattr(q, '_DesignVoice', Mock(return_value=design))
    with pytest.raises(RuntimeError, match='cancelled'): q.ensure_reference(cancel)
    design.cancel.assert_called_once()
    design.close.assert_called_once()
    assert not reference.exists()


def test_terminated_bootstrap_request_reports_cancellation(reference, monkeypatch):
    reference.unlink()
    cancel = Event()
    design = Mock()
    def generate(text):
        cancel.set()
        raise httpx.ReadError('owned server terminated')
    design.wav.side_effect = generate
    monkeypatch.setattr(q, 'ensure_model', Mock())
    monkeypatch.setattr(q, '_DesignVoice', Mock(return_value=design))
    with pytest.raises(RuntimeError, match='cancelled'): q.ensure_reference(cancel)
    design.close.assert_called_once()

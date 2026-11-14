"""Локальные embeddings: маршрутизация, приватность, медиа и CPU fallback."""
import base64
import math
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from local_mode import local_embeddings as module


def test_preferences_survive_reload_and_invalid_file(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    monkeypatch.setattr(module, 'preferences_path', lambda: path)
    assert module.preferences() == {'backend': 'auto', 'chunk_size': 1500, 'chunk_overlap': 200}
    module.preferences('local')
    assert module.preferences() == {'backend': 'local', 'chunk_size': 1500, 'chunk_overlap': 200}
    path.write_text('[]')
    assert module.preferences() == {'backend': 'auto', 'chunk_size': 1500, 'chunk_overlap': 200}
    with pytest.raises(ValueError): module.preferences('remote')


def test_image_uses_nested_content_not_ignored_image_data(tmp_path):
    path = tmp_path / 'red.png'; Image.new('RGB', (4,4), 'red').save(path)
    parts = module.media_parts('description', str(path))
    assert parts[0] == {'type':'text','text':'description'}
    assert parts[1]['type'] == 'image_url'
    assert base64.b64decode(parts[1]['image_url']['url'].split(',')[1]) == path.read_bytes()


def test_request_validates_and_normalizes_without_google():
    runner = module.LocalEmbedding(); runner.url='http://127.0.0.1:1234'; runner.device='Vulkan0'
    response=Mock(status_code=200)
    response.json.return_value={'data':[{'embedding':[2.0]*768}], 'usage':{'prompt_tokens':3}}
    runner.session.post=Mock(return_value=response)
    result=runner._request([{'type':'text','text':'sample'}])
    assert abs(math.hypot(*result['vector'])-1)<1e-9
    body=runner.session.post.call_args.kwargs['json']
    assert body['input'] == [{'content':[{'type':'text','text':'sample'}]}]
    assert runner.session.trust_env is False
    response.json.return_value['data'][0]['embedding']=[float('nan')]*768
    with pytest.raises(ValueError): runner._request([])


def test_gpu_inference_failure_requires_explicit_cpu_selection():
    runner=module.LocalEmbedding(); starts=[]
    def start(executable, model, projector, device):
        runner.device=device; starts.append(device)
    def request(parts, *, model_name, expected_dim):
        assert model_name == "embeddinggemma-2:Q8_0" and expected_dim == 768
        if runner.device != 'none': raise RuntimeError('GPU unavailable')
        return {'device':'none', 'dimensions':768}
    with patch.object(module,'assets',return_value=('exe','model','mmproj')), patch.object(module,'run',return_value=Mock(stdout='  Vulkan0: AMD Radeon\n',stderr='')), patch.object(runner,'_start',side_effect=start), patch.object(runner,'_request',side_effect=request):
        with pytest.raises(RuntimeError, match='GPU unavailable'):
            runner.embed('hello','','local')
        assert starts == ['Vulkan0']
        assert runner.embed('hello','','cpu') == {'device':'none', 'dimensions':768}
    assert starts == ['Vulkan0','none']


def test_cpu_never_probes_gpu_and_failure_never_calls_cloud():
    runner=module.LocalEmbedding()
    with patch.object(module,'assets',return_value=('exe','model','mmproj')), patch.object(module,'run') as probe, patch.object(runner,'_start',side_effect=RuntimeError('CPU error')) as start:
        with pytest.raises(RuntimeError): runner.embed('hello','','cpu')
        probe.assert_not_called()
        assert start.call_args.args[-1] == 'none'


def test_empty_or_unknown_backend_does_not_download():
    runner=module.LocalEmbedding()
    with patch.object(module,'assets') as download:
        with pytest.raises(ValueError): runner.embed('','','local')
        with pytest.raises(ValueError): runner.embed('x','','invalid')
        download.assert_not_called()


def test_close_only_terminates_owned_process():
    runner=module.LocalEmbedding(); process=Mock(); process.poll.return_value=None
    runner.process=process
    runner.close(); runner.close()
    process.terminate.assert_called_once(); process.wait.assert_called_once()


def test_local_audio_is_wav_and_does_not_require_ffmpeg(tmp_path):
    import wave
    path=tmp_path/'voice.wav'
    with wave.open(str(path),'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b'\x00\x00'*16000)
    part=module.media_parts('',str(path))[0]
    assert part['type']=='input_audio'
    assert base64.b64decode(part['input_audio']['data']).startswith(b'RIFF')

"""Cloud worker tests use synthetic subprocesses or mocked SDKs, never Google."""
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from google.genai import types
from google.genai.errors import APIError
from local_mode import embedding_jobs as jobs, embedding_cloud as cloud, embedding_cloud_worker as worker


def test_worker_uses_existing_sync_client_routing_and_roundtrips_binary(monkeypatch):
    from contextlib import nullcontext
    from local_mode import gemini
    client = SimpleNamespace(models=SimpleNamespace(count_tokens=Mock(return_value=types.CountTokensResponse(total_tokens=3))))
    create = Mock(return_value=nullcontext(client))
    monkeypatch.setattr(gemini, 'create_client', create)
    binary = b'\xff\x00\xfd\x81'
    contents = types.Content(parts=[types.Part.from_bytes(data=binary, mime_type='image/png')])
    payload = {'method':'count_tokens','params':{'model':'test','contents':contents.model_dump(mode='json'),'config':{}}}
    assert worker.execute(payload)['total_tokens'] == 3
    create.assert_called_once_with(retry_attempts=0)
    assert client.models.count_tokens.call_args.kwargs['contents'].parts[0].inline_data.data == binary


def test_parent_preserves_sdk_error_types_for_existing_retry_and_fallback(monkeypatch):
    real = subprocess.Popen
    def spawn(_args, **kwargs):
        reply = {'error':{'kind':'api','code':404,'details':{'error':{'code':404,'message':'not found','status':'NOT_FOUND'}},'message':'not found'}}
        return real([sys.executable,'-c','import sys;sys.stdin.buffer.read();sys.stdout.write('+repr(json.dumps(reply))+')'], **kwargs)
    monkeypatch.setattr(cloud.subprocess, 'Popen', spawn)
    with pytest.raises(APIError) as caught:
        cloud.call('count_tokens', {'model':'test','contents':{},'config':{}})
    assert caught.value.code == 404


def test_cancel_kills_and_reaps_owned_cloud_worker_without_background_waiters(monkeypatch):
    real = subprocess.Popen
    launched = threading.Event()
    processes, errors = [], []
    def spawn(_args, **kwargs):
        process = real([sys.executable,'-c','import sys,time;sys.stdin.buffer.read();time.sleep(60)'], **kwargs)
        processes.append(process)
        launched.set()
        return process
    monkeypatch.setattr(cloud.subprocess, 'Popen', spawn)
    key = uuid4().hex
    def request():
        try:
            with jobs.operation(key):
                cloud.call('count_tokens', {'model':'test','contents':{},'config':{}})
        except jobs.Cancelled:
            errors.append('cancelled')
    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert launched.wait(2)
        jobs.cancel(key)
        thread.join(3)
        assert not thread.is_alive()
        assert errors == ['cancelled']
        assert processes[0].poll() is not None
    finally:
        jobs.cancel(key)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=2)
        thread.join(3)


def test_owned_error_payload_keeps_dynamic_values_separate_from_translation_key():
    from local_mode.messages import OwnedValueError, OwnedRuntimeError
    error = OwnedValueError('Select {0} with a known duration up to {1} seconds.', 'audio', 180)
    assert isinstance(error, ValueError)
    assert str(error) == 'Select audio with a known duration up to 180 seconds.'
    assert error.payload() == {'source':'jarvis','message':'Select {0} with a known duration up to {1} seconds.','values':['audio',180]}
    assert isinstance(OwnedRuntimeError('Failure'), RuntimeError)

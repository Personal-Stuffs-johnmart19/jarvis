"""Document failures keep locale provenance across the isolated OCR worker."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest
from local_mode import documents, document_worker, worker_rpc
from local_mode.messages import OwnedError, OwnedValueError, OwnedRuntimeError, message


def test_document_validation_has_english_source_and_russian_catalog():
    with pytest.raises(ValueError) as caught:
        documents.document_bytes(None)
    assert isinstance(caught.value, OwnedValueError)
    payload = caught.value.payload()
    assert payload == {'source': 'jarvis', 'message': 'Invalid document path.', 'values': []}
    assert message(payload['message'], locale='en') == 'Invalid document path.'
    assert message(payload['message'], locale='ru') == 'Некорректный путь документа.'


def test_empty_pdf_rejects_before_starting_worker(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid PDF must not start a worker')
    monkeypatch.setattr(worker_rpc, 'request', forbidden)
    with pytest.raises(OwnedValueError, match='Invalid PDF size'):
        documents.pdf_text(b'')


@pytest.mark.parametrize('kind,error_type', [('value', OwnedValueError), ('runtime', OwnedRuntimeError)])
def test_document_worker_roundtrip_preserves_error_type_and_raw_detail(monkeypatch, kind, error_type):
    raw = 'Provider text /private/папка/model.gguf {0}'
    template = 'Scanned PDFs require local OCR: install Windows ru/en languages or Tesseract with rus+eng. {0}'
    def fail(data):
        raise error_type(template, raw)
    monkeypatch.setattr(documents, '_pdf_text', fail)
    output = io.BytesIO()
    monkeypatch.setattr(document_worker, 'sys', SimpleNamespace(
        stdin=SimpleNamespace(buffer=io.BytesIO(b'synthetic PDF')),
        stdout=SimpleNamespace(buffer=output)))
    document_worker.main()
    data = json.loads(output.getvalue())
    assert data['error_kind'] == kind
    assert data['error']['values'] == [raw]
    monkeypatch.setattr(worker_rpc, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=output.getvalue()))
    with pytest.raises(error_type) as caught:
        worker_rpc.request('local_mode.document_worker', b'synthetic PDF', 1)
    assert caught.value.payload() == data['error']
    assert message(template, *caught.value.values, locale='en').endswith(raw)
    assert message(template, *caught.value.values, locale='ru').startswith('Для PDF-скана')
    assert message(template, *caught.value.values, locale='ru').endswith(raw)


def test_external_worker_failure_remains_raw(monkeypatch):
    raw = 'External decoder /папка/file.pdf {0}'
    output = json.dumps({'error': raw}).encode()
    monkeypatch.setattr(worker_rpc, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(ValueError) as caught:
        worker_rpc.request('local_mode.document_worker', b'fixture', 1)
    assert not isinstance(caught.value, OwnedError)
    assert str(caught.value) == raw


@pytest.mark.parametrize('values', [[{'nested': 'data'}], ['x' * 4097], [float('nan')]])
def test_malformed_owned_worker_payload_is_not_interpreted(monkeypatch, values):
    error = {'source': 'jarvis', 'message': 'Invalid PDF size.', 'values': values}
    output = json.dumps({'error': error}).encode()
    monkeypatch.setattr(worker_rpc, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(ValueError) as caught:
        worker_rpc.request('local_mode.document_worker', b'fixture', 1)
    assert not isinstance(caught.value, OwnedError)


@pytest.mark.parametrize('output', [b'', b'not JSON', b'\xff', b'[]', b'null', b'[' * 2000, b'9' * 5000])
def test_invalid_worker_protocol_has_localizable_error_without_raw_output(monkeypatch, output):
    monkeypatch.setattr(worker_rpc, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(OwnedRuntimeError) as caught:
        worker_rpc.request('local_mode.document_worker', b'fixture', 1)
    assert caught.value.payload() == {
        'source': 'jarvis', 'message': 'Invalid local worker response.', 'values': []}
    assert message(caught.value.template, locale='ru') == 'Некорректный ответ локального процесса.'


def test_worker_crash_preserves_module_and_exit_code_as_values(monkeypatch):
    monkeypatch.setattr(worker_rpc, 'run', lambda *a, **k: SimpleNamespace(
        returncode=42, stdout=b'private provider output'))
    with pytest.raises(OwnedRuntimeError) as caught:
        worker_rpc.request('local_mode.document_worker', b'fixture', 1)
    assert caught.value.values == ('document_worker', 42)
    assert str(caught.value).startswith('Local worker document_worker exited with code 42.')
    assert message(caught.value.template, *caught.value.values, locale='ru').startswith(
        'Локальный процесс document_worker завершился с кодом 42.')


def test_worker_timeout_has_owned_error_and_preserves_cause(monkeypatch):
    failure = subprocess.TimeoutExpired(['private arguments'], 2, output=b'private output')
    def timeout(*args, **kwargs):
        raise failure
    monkeypatch.setattr(worker_rpc, 'run', timeout)
    with pytest.raises(OwnedRuntimeError) as caught:
        worker_rpc.request('local_mode.document_worker', b'fixture', 2)
    assert caught.value.__cause__ is failure
    assert caught.value.values == ('document_worker', 2)
    assert str(caught.value) == 'Local worker document_worker timed out after 2 seconds.'
    assert '2' in message(caught.value.template, *caught.value.values, locale='ru')


def test_oversized_worker_response_rejects_before_json_decode(monkeypatch):
    monkeypatch.setattr(worker_rpc, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b'x' * 300001))
    with pytest.raises(OwnedRuntimeError, match='exceeds the size limit'):
        worker_rpc.request('local_mode.document_worker', b'fixture', 1)


def test_unrelated_worker_interrupt_is_not_reclassified(monkeypatch):
    def cancelled(*args, **kwargs):
        raise InterruptedError('cancelled')
    monkeypatch.setattr(worker_rpc, 'run', cancelled)
    with pytest.raises(InterruptedError, match='cancelled'):
        worker_rpc.request('local_mode.document_worker', b'fixture', 1)

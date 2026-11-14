"""Owned pre-inference failures use templates; paths and transport failures stay raw."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from local_mode import document_store as store, model_library as lib
from local_mode.messages import OwnedError, OwnedValueError, message


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    directory = root / 'llm' / 'fixture__model' / 'r1'
    directory.mkdir(parents=True)
    path = directory / 'literal {0} модель.gguf'
    data = b'synthetic weights never loaded'
    path.write_bytes(data)
    manifest = {'owner': lib.OWNER, 'complete': True, 'repo_id': 'fixture/model',
                'revision': 'r1', 'kind': 'llm', 'bundle_id': 'llm-gguf',
                'files': [{'path': path.name, 'size': len(data),
                           'sha256': hashlib.sha256(data).hexdigest()}]}
    (directory / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(lib, 'get_models_root', lambda: root)
    return path, manifest


def localized(error):
    payload = error.payload()
    en = message(payload['message'], *payload['values'], locale='en')
    ru = message(payload['message'], *payload['values'], locale='ru')
    assert en == str(error)
    assert ru != en
    return ru


def test_deleted_installed_model_rejects_at_actual_safe_path_before_configuration(installed, monkeypatch):
    path, _ = installed
    assert lib.list_installed_models()[0]['repo_id'] == 'fixture/model'
    path.unlink()
    derive = Mock(side_effect=AssertionError('must fail before deriving/loading'))
    monkeypatch.setattr(lib, 'derive_model_files', derive)
    with pytest.raises(OwnedValueError) as caught:
        lib.activate_model('llm', 'fixture/model', 'r1')
    assert caught.value.template == 'Path does not exist or cannot be resolved: {0}'
    assert caught.value.values == (str(path),)
    assert str(path) in localized(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    derive.assert_not_called()


@pytest.mark.parametrize('change,template', [
    ('incomplete', 'Cannot activate incomplete model download'),
    ('size', 'Size mismatch for {0}: expected {1}, got {2}'),
    ('checksum', 'SHA256 checksum mismatch for {0}'),
])
def test_activation_validation_keeps_owned_provenance(installed, change, template):
    path, manifest = installed
    if change == 'incomplete':
        manifest['complete'] = False
        (path.parent / lib.MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')
    elif change == 'size':
        path.write_bytes(b'changed')
    else:
        path.write_bytes(b'x' * path.stat().st_size)
    with pytest.raises(OwnedValueError) as caught:
        lib.activate_model('llm', 'fixture/model', 'r1')
    assert caught.value.template == template
    ru = localized(caught.value)
    if change != 'incomplete':
        assert path.name in ru


def test_raw_os_failure_is_not_reclassified_by_text_collision(installed, monkeypatch):
    raw = 'Cannot activate incomplete model download'
    failure = OSError(raw)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(lib, 'compute_sha256', fail)
    with pytest.raises(OSError) as caught:
        lib.activate_model('llm', 'fixture/model', 'r1')
    assert caught.value is failure
    assert not isinstance(caught.value, OwnedError)
    assert str(caught.value) == raw


def test_changed_uploaded_file_rejects_before_any_embedding(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setenv('JARVIS_DESKTOP_TEST', '1')
    folder = tmp_path / '.cache/attachments'
    folder.mkdir(parents=True)
    path = folder / 'attachment-literal {0}.txt'
    path.write_text('synthetic uploaded text', encoding='utf-8')
    item = store.ingest(str(path), 'User title {0}')
    identifier = item['id']
    from local_mode import embeddings, local_embeddings
    monkeypatch.setattr(local_embeddings, 'preferences', lambda: {'chunk_size': 100, 'chunk_overlap': 10})
    single = Mock(side_effect=AssertionError('must fail before inference'))
    batch = Mock(side_effect=AssertionError('must fail before inference'))
    monkeypatch.setattr(embeddings, 'selected', single)
    monkeypatch.setattr(embeddings, 'batch_selected', batch)
    path.write_text('changed uploaded text', encoding='utf-8')
    with pytest.raises(OwnedValueError) as caught:
        store.embed(identifier)
    assert caught.value.template == 'Uploaded file changed; upload it again before indexing'
    assert localized(caught.value) == 'Загруженный файл изменился; загрузите его ещё раз перед индексированием'
    single.assert_not_called()
    batch.assert_not_called()
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 0
        row = db.execute('SELECT name, text, embedding FROM documents WHERE id=?', (identifier,)).fetchone()
        assert tuple(row) == ('User title {0}', 'synthetic uploaded text', None)

@pytest.mark.parametrize('kind', ['unrecognized-kind', ['untrusted', 'kind']])
def test_invalid_model_kind_is_rejected_before_filesystem_and_has_scalar_payload(monkeypatch, kind):
    def forbidden():
        raise AssertionError('invalid kind must not inspect models or start inference')
    monkeypatch.setattr(lib, 'get_models_root', forbidden)
    with pytest.raises(OwnedValueError) as caught:
        lib.activate_model(kind, 'fixture/model', 'r1')
    assert caught.value.payload()['values'] == [str(kind)]
    assert str(kind) in localized(caught.value)

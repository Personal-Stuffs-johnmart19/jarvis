"""Opt-in canonical semantic-index proof; preflight and guard checks make no inference.

Run from the repository with its Python environment. Retains only owned fixtures,
safe transport metadata and provider usage; never records credentials or vectors.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DOCUMENT = "The observatory keeps its telescope under a sliding roof. Stars are watched after sunset."
QUERY = "Where is the instrument used to observe the night sky kept?"


class Guard:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []
        self.device = None

    def start(self, device):
        if self.provider != 'local' or not device.startswith('Vulkan') or self.device is not None:
            raise RuntimeError('CPU startup, fallback and repeated runtime startup are refused')
        self.device = device

    def admit(self, method, text):
        expected = ([('embed_content', DOCUMENT), ('count_tokens', QUERY), ('embed_content', QUERY)]
                    if self.provider == 'google' else [('embeddings', DOCUMENT), ('embeddings', QUERY)])
        if len(self.calls) >= len(expected) or (method, text) != expected[len(self.calls)]:
            raise RuntimeError('Embedding conformance transport refused unexpected input, retry or extra call')
        row = {'method': method, 'input_characters': len(text), 'ok': False}
        self.calls.append(row)
        return row


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def preflight(provider):
    from local_mode import local_embeddings as local, embeddings
    settings = local.preferences()
    result = {'provider': provider, 'inference_calls': 0, 'downloads': 0,
              'configured_backend': settings['backend'], 'explicit_backend': 'local' if provider == 'local' else 'cloud',
              'embedding_limit': 2, 'sdk_limit': 3 if provider == 'google' else 0}
    assets = None
    if provider == 'local':
        runtime = next((local.CACHE / name for name in (local.BUILD, f'{local.BUILD}-verified-{local.RUNTIME_SHA[:12]}')
                        if local._runtime_ready(local.CACHE / name)), None)
        if runtime is None:
            raise RuntimeError('Pinned local runtime is not installed; downloads are refused')
        selected = settings.get('selected_model')
        if selected:
            from local_mode.model_library import validate_selected_model
            validate_selected_model(selected)
            model = Path(selected['model_path'])
            projector = Path(selected['projector_path']) if selected.get('projector_path') else None
            if digest(model) != selected['sha256']:
                raise RuntimeError('Selected model checksum mismatch')
            result['identity'] = {'model': model.name, 'sha256': selected['sha256'], 'managed_selection': True}
        else:
            model, projector = local.CACHE / local.MODEL, local.CACHE / local.PROJECTOR
            for name, (size, checksum) in local.ASSETS.items():
                path = local.CACHE / name
                if not path.is_file() or path.stat().st_size != size or digest(path) != checksum:
                    raise RuntimeError('Pinned local model assets are missing or invalid; downloads are refused')
            result['identity'] = {'model': 'embeddinggemma-2:Q8_0', 'dimensions': 768, 'managed_selection': False}
        assets = (runtime / 'llama-server.exe', model, projector)
        result['runtime'] = local.BUILD
        result['gpu_readiness'] = 'not probed: live startup must select Vulkan and refuses CPU'
    else:
        result['identity'] = {'model': embeddings.MODEL, 'dimensions': 768}
    return result, settings.get('selected_model'), assets


def self_test():
    for provider in ('local', 'google'):
        guard = Guard(provider)
        attempts = [('embed_content', DOCUMENT), ('count_tokens', QUERY), ('embed_content', QUERY)] if provider == 'google' else [('embeddings', DOCUMENT), ('embeddings', QUERY)]
        for method, text in attempts:
            guard.admit(method, text)
        for method, text in (attempts[-1], (attempts[0][0], 'private unexpected text')):
            try:
                guard.admit(method, text)
            except RuntimeError:
                pass
            else:
                raise AssertionError('Guard accepted extra inference')
        assert len(guard.calls) == len(attempts)
        fresh = Guard(provider)
        try:
            fresh.admit(attempts[-1][0], QUERY)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Guard accepted reordered request')
        assert not fresh.calls
        for device in ('none', 'CPU'):
            try:
                fresh.start(device)
            except RuntimeError:
                pass
            else:
                raise AssertionError('CPU startup admitted')
    local = Guard('local')
    local.start('Vulkan0')
    try:
        local.start('Vulkan1')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Fallback/repeated startup admitted')
    from local_mode import document_store as store, local_embeddings, embeddings, embedding_jobs as jobs
    from local_mode.services import ServiceRegistry
    from local_mode.shared_agent import SharedAgent
    with tempfile.TemporaryDirectory(prefix='jarvis-embedding-guard-') as folder, ExitStack() as stack:
        owned = Path(folder)
        stack.enter_context(patch.object(store, 'ROOT', owned))
        stack.enter_context(patch.object(local_embeddings, 'preferences_path', lambda: owned/'settings.json'))
        provider_calls = []
        def denied(*_args, **_kwargs):
            provider_calls.append(True)
            raise AssertionError('Cancelled dispatch reached provider')
        stack.enter_context(patch.object(embeddings, 'batch_selected', denied))
        registry = ServiceRegistry(SimpleNamespace(event=lambda *_: None, suggestions_path=owned/'suggestions.json'), lambda: None)
        bridge = SharedAgent(lambda *_: None)
        workspace = owned/'workspace'
        workspace.mkdir()
        source = workspace/'test.txt'
        source.write_text(DOCUMENT, encoding='utf-8')
        receipt = bridge.prepare(registry, 'documents.import_workspace', {'path': str(source), 'workspace': str(workspace)})
        document = bridge.execute(registry, receipt['token'])
        key = uuid.uuid4().hex
        jobs.cancel(key)
        receipt = bridge.prepare(registry, 'documents.embed', {'identifier': document['id'], 'request_id': key})
        try:
            bridge.execute(registry, receipt['token'])
        except jobs.Cancelled:
            pass
        else:
            raise AssertionError('Canonical pre-cancel did not reject')
        assert not provider_calls
        with store.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 0
    return {'ok': True, 'inference_calls': 0, 'guards': ['exact input/order', 'extra/retry refusal', 'provider method identity', 'CPU/fallback startup refusal', 'canonical pre-cancel before provider']}


def run(provider, output):
    from local_mode import local_embeddings as local, document_store as store, embeddings, embedding_jobs as jobs, api_usage
    from local_mode.services import ServiceRegistry
    from local_mode.shared_agent import SharedAgent
    details, selection, assets = preflight(provider)
    owned = Path(tempfile.mkdtemp(prefix='embedding-', dir=output.parent)).resolve()
    report = {'ok': False, 'preflight': details, 'fixtures': str(owned), 'calls': [], 'operations': [],
              'scope': 'actual canonical handlers and selected provider; owned synthetic document',
              'cancellation_scope': 'pre-cancelled dispatch; not an inflight transport cancellation proof'}
    guard = Guard(provider)
    report['calls'] = guard.calls
    runner = local.LocalEmbedding()
    identifiers = [uuid.uuid4().hex for _ in range(3)]
    timer = threading.Timer(90, lambda: [jobs.cancel(key) for key in identifiers])
    timer.daemon = True
    start = time.monotonic()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(store, 'ROOT', owned))
            stack.enter_context(patch.object(local, 'preferences_path', lambda: owned / 'embedding-settings.json'))
            stack.enter_context(patch.object(api_usage, '_default', api_usage.UsageLedger(owned / 'api-usage.json')))
            stack.callback(lambda: report.update(usage=api_usage._default.snapshot()))
            stack.enter_context(patch.object(local, '_runner', runner))
            # Keep runtime logs and every writable store inside this run; assets remain read-only.
            stack.enter_context(patch.object(local, 'CACHE', owned / 'runtime-logs'))
            def forbidden(*_args, **_kwargs):
                raise RuntimeError('Embedding conformance refuses downloads and provider fallback')
            stack.enter_context(patch.object(local, 'download_file', forbidden))
            backend = 'local' if provider == 'local' else 'cloud'
            prefs = {'backend': backend, 'chunk_size': 1500, 'chunk_overlap': 200}
            if selection:
                prefs['selected_model'] = selection
            (owned / 'embedding-settings.json').write_text(json.dumps(prefs), encoding='utf-8')
            if provider == 'local':
                stack.enter_context(patch.object(embeddings, 'embed', forbidden))
                stack.enter_context(patch.object(embeddings, 'batch_embed', forbidden))
                stack.enter_context(patch.object(local, 'assets', lambda: assets))
                stack.enter_context(patch.object(local, 'ensure_runtime', lambda **_kw: assets[0]))
                original_start = runner._start
                def gpu_start(executable, model, projector, device):
                    guard.start(device)
                    report['device'] = device
                    return original_start(executable, model, projector, device)
                stack.enter_context(patch.object(runner, '_start', gpu_start))
                import httpx
                original_send = httpx.AsyncClient.send
                async def send(client, request, **kwargs):
                    if str(request.url) != runner.url + '/v1/embeddings' or request.method != 'POST':
                        raise RuntimeError('Unexpected local embedding transport')
                    payload = json.loads(request.content)
                    parts = payload['input'][0]['content']
                    if payload.get('model') != 'jarvis-embedding' or len(parts) != 1 or parts[0].get('type') != 'text':
                        raise RuntimeError('Unexpected local embedding payload')
                    row = guard.admit('embeddings', parts[0]['text'])
                    row['request_bytes'] = len(request.content)
                    begin = time.monotonic()
                    response = await original_send(client, request, **kwargs)
                    row.update(ok=response.status_code == 200, milliseconds=round((time.monotonic()-begin)*1000, 3))
                    if response.status_code == 200:
                        usage = response.json().get('usage', {})
                        row['usage'] = {key: value for key, value in usage.items()
                                        if key in {'prompt_tokens', 'total_tokens'} and type(value) is int}
                    return response
                stack.enter_context(patch.object(httpx.AsyncClient, 'send', send))
            else:
                stack.enter_context(patch.object(local, 'embed', forbidden))
                # Remove only the application retry decorator, preserving normal usage tracking.
                for name in ('count_input', 'embed_input'):
                    stack.enter_context(patch.object(embeddings, name, getattr(embeddings, name).__wrapped__))
                original_cloud = embeddings._cloud_call
                def cloud(client, method, **params):
                    if params.get('model') != embeddings.MODEL:
                        raise RuntimeError('Unexpected cloud embedding model')
                    content = params['contents']
                    content = content if isinstance(content, list) else [content]
                    if len(content) != 1 or len(content[0].parts) != 1:
                        raise RuntimeError('Unexpected cloud embedding content')
                    row = guard.admit(method, content[0].parts[0].text)
                    begin = time.monotonic()
                    response = original_cloud(client, method, **params)
                    row.update(ok=True, milliseconds=round((time.monotonic()-begin)*1000, 3), usage=api_usage.token_usage(response))
                    if method == 'count_tokens':
                        row['counted_tokens'] = response.total_tokens
                    return response
                stack.enter_context(patch.object(embeddings, '_cloud_call', cloud))
            registry = ServiceRegistry(SimpleNamespace(event=lambda *_: None, suggestions_path=owned/'suggestions.json'), lambda: None)
            bridge = SharedAgent(lambda *_: None)
            def call(name, args):
                row = {'handler': name, 'category': 'actual_handler', 'ok': False}
                report['operations'].append(row)
                receipt = bridge.prepare(registry, name, args)
                if receipt['requires_approval']:
                    try:
                        registry.execute(name, args, approved=False)
                    except ValueError as error:
                        assert 'approval' in str(error).lower()
                        row['unapproved_rejected'] = True
                    else:
                        raise AssertionError('Unapproved inference reached handler')
                value = bridge.execute(registry, receipt['token'])
                try:
                    bridge.execute(registry, receipt['token'])
                except ValueError:
                    row['receipt_consumed_once'] = True
                else:
                    raise AssertionError('Approval receipt replay accepted')
                row['ok'] = True
                return value
            configured = call('embedding.settings', {})
            assert configured['backend'] == backend
            workspace = owned / 'workspace'
            workspace.mkdir()
            source = workspace / 'observatory.txt'
            source.write_bytes(DOCUMENT.encode('utf-8'))
            imported = call('documents.import_workspace', {'path': str(source), 'workspace': str(workspace)})
            timer.start()
            indexed = call('documents.embed', {'identifier': imported['id'], 'request_id': identifiers[0]})
            assert indexed['chunks'] == 1 and not indexed.get('fallback_reason')
            report['index'] = indexed
            with jobs.operation(identifiers[1]):
                found = call('documents.search', {'query': QUERY, 'semantic': True, 'limit': 1})
            hit = found['items'][0]
            assert hit['id'] == imported['id'] and not found.get('index_mismatch')
            citation = hit['citation']
            assert hit['excerpt'] == DOCUMENT[citation['start']:citation['end']]
            report['search'] = {'document_id': hit['id'], 'score': hit['score'], 'citation': citation,
                                'fresh_semantic_query': True, 'ranking_quality': 'one-document retrieval only'}
            def snapshot():
                with store.connection() as db:
                    return [tuple(row) for row in db.execute('SELECT * FROM chunks ORDER BY document_id,idx')]
            before, count = snapshot(), len(guard.calls)
            again = call('documents.import_workspace', {'path': str(source), 'workspace': str(workspace)})
            assert again['id'] == imported['id'] and snapshot() == before and len(guard.calls) == count
            report['unchanged_reimport'] = {'identity_and_vectors_preserved': True, 'additional_calls': 0}
            jobs.cancel(identifiers[2])
            try:
                call('documents.embed', {'identifier': imported['id'], 'request_id': identifiers[2]})
            except jobs.Cancelled:
                report['operations'][-1].update(ok=True, expected_cancellation=True)
            else:
                raise AssertionError('Pre-cancelled handler reached inference')
            assert snapshot() == before and len(guard.calls) == count
            report['pre_cancel'] = {'index_preserved': True, 'additional_calls': 0}
            assert len(guard.calls) == (3 if provider == 'google' else 2) and all(row['ok'] for row in guard.calls)
            report['usage'] = api_usage._default.snapshot()
            report['ok'] = True
    except Exception as error:
        report['failure_type'] = type(error).__name__
        # Errors can contain endpoint credentials; deliberately retain only exception type.
    finally:
        timer.cancel()
        runner.close()
        runner.session.close()
        report['milliseconds'] = round((time.monotonic()-start)*1000, 3)
        report['owned_source_unchanged'] = (owned/'workspace/observatory.txt').read_bytes() == DOCUMENT.encode('utf-8') if (owned/'workspace/observatory.txt').exists() else False
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('local', 'google'))
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return 0
    if not args.provider or not (args.preflight or args.live):
        parser.error('Select --provider and --preflight or explicit --live; no default inference')
    if args.preflight:
        print(json.dumps(preflight(args.provider)[0], indent=2))
        return 0
    if args.output is None:
        parser.error('--live requires a new --output report path')
    output = args.output.resolve()
    allowed = ROOT / '.cache' / 'verification'
    if not output.is_relative_to(allowed.resolve()) or output.exists():
        parser.error('Report must be a new file inside .cache/verification')
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run(args.provider, output)
    print(json.dumps({'ok': result['ok'], 'report': str(output), 'calls': len(result['calls'])}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

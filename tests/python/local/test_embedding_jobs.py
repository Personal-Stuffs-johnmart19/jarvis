import asyncio
import json
import threading
from uuid import uuid4
import pytest
from local_mode import embedding_jobs as jobs


def test_early_cancel_duplicate_ids_and_stale_cancel():
    key = uuid4().hex
    jobs.cancel(key)
    with pytest.raises(jobs.Cancelled):
        with jobs.operation(key):
            pytest.fail('cancelled request ran')
    assert jobs.cancel(key)['finished']
    with pytest.raises(ValueError, match='already used'):
        with jobs.operation(key):
            pass
    with jobs.operation(uuid4().hex):
        jobs.check()


def test_async_cancellation_awaits_transport_cleanup_and_leaves_other_jobs_usable():
    key = uuid4().hex
    started, closed = threading.Event(), threading.Event()
    errors = []
    async def transport():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            closed.set()
    def worker():
        try:
            with jobs.operation(key):
                jobs.run_async(transport)
        except jobs.Cancelled:
            errors.append('cancelled')
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert started.wait(2)
        jobs.cancel(key)
        thread.join(2)
        assert not thread.is_alive()
        assert closed.is_set() and errors == ['cancelled']
        with jobs.operation(uuid4().hex):
            assert jobs.run_async(lambda: asyncio.sleep(0, result='retry')) == 'retry'
    finally:
        jobs.cancel(key)
        thread.join(2)


def test_cancelled_rebuild_keeps_current_document_index_and_retry_succeeds(tmp_path, monkeypatch):
    from local_mode import document_store as store, embeddings
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    folder = tmp_path / '.cache/attachments'
    folder.mkdir(parents=True)
    path = folder / 'attachment-test.txt'
    path.write_text('A short document', encoding='utf-8')
    document = store.ingest(str(path))
    def indexed_model():
        with store.connection() as db:
            return json.loads(db.execute("SELECT embedding FROM documents WHERE id=?", (document['id'],)).fetchone()['embedding'])['model']
    old = {'model':'old','dimensions':2,'vector':[1.,0.], 'input_tokens':1}
    monkeypatch.setattr(embeddings, 'batch_selected', lambda texts, backend=None: [old for _ in texts])
    store.embed(document['id'], 'cpu')
    key = uuid4().hex
    def cancelled_provider(texts, backend=None):
        jobs.cancel(key)
        return [{**old, 'model':'new'} for _ in texts]
    monkeypatch.setattr(embeddings, 'batch_selected', cancelled_provider)
    with jobs.operation(key):
        result = store.rebuild('cpu')
    assert result == {'rebuilt':[], 'failed':[], 'cancelled':True}
    assert indexed_model() == 'old'
    monkeypatch.setattr(embeddings, 'batch_selected', lambda texts, backend=None: [{**old,'model':'new'} for _ in texts])
    with jobs.operation(uuid4().hex):
        assert len(store.rebuild('cpu')['rebuilt']) == 1
    assert indexed_model() == 'new'

def test_local_http_cancel_closes_active_connection_without_stopping_shared_runner():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from local_mode.local_embeddings import LocalEmbedding
    started, disconnected = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            started.set()
            self.connection.settimeout(3)
            if self.connection.recv(1) == b'':
                disconnected.set()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    runner = LocalEmbedding()
    runner.url = f'http://127.0.0.1:{server.server_port}'
    key = uuid4().hex
    errors = []
    def request():
        try:
            with jobs.operation(key):
                runner._request([])
        except jobs.Cancelled:
            errors.append('cancelled')
    worker = threading.Thread(target=request)
    worker.start()
    try:
        assert started.wait(3)
        jobs.cancel(key)
        worker.join(3)
        assert not worker.is_alive()
        assert disconnected.wait(2)
        assert errors == ['cancelled']
    finally:
        jobs.cancel(key)
        worker.join(3)
        server.shutdown()
        server.server_close()
        serving.join(2)
        runner.session.close()


def test_cancel_inside_sql_write_rolls_back_the_entire_document(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from local_mode import document_store as store, embeddings
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    folder = tmp_path / '.cache/attachments'
    folder.mkdir(parents=True)
    path = folder / 'attachment-atomic.txt'
    path.write_text('Atomic index update', encoding='utf-8')
    document = store.ingest(str(path))
    vector = {'model':'old', 'dimensions':2, 'vector':[1.,0.]}
    monkeypatch.setattr(embeddings, 'batch_selected', lambda texts, backend=None: [vector for _ in texts])
    store.embed(document['id'], 'cpu')
    original = store.connection
    key = uuid4().hex
    class Connection:
        def __init__(self, db): self.db = db
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, params=()):
            result = self.db.execute(sql, params)
            if sql.startswith('UPDATE documents SET embedding='):
                jobs.cancel(key)
            return result
    @contextmanager
    def cancellable_connection():
        with original() as db:
            yield Connection(db)
    monkeypatch.setattr(store, 'connection', cancellable_connection)
    vector['model'] = 'new'
    with pytest.raises(jobs.Cancelled):
        with jobs.operation(key):
            store.embed(document['id'], 'cpu')
    with original() as db:
        assert json.loads(db.execute('SELECT embedding FROM documents WHERE id=?', (document['id'],)).fetchone()['embedding'])['model'] == 'old'
        assert db.execute('SELECT model FROM chunks WHERE document_id=?', (document['id'],)).fetchone()['model'] == 'old'


def test_stop_cancels_accepted_queued_jobs_before_they_can_call_a_provider():
    from concurrent.futures import ThreadPoolExecutor
    release, occupied = threading.Event(), threading.Event()
    calls = []
    def block():
        occupied.set()
        release.wait(3)
    def queued(generation):
        with jobs.accepted(generation), jobs.operation(uuid4().hex):
            calls.append('provider')
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocker = pool.submit(block)
        assert occupied.wait(1)
        future = pool.submit(queued, jobs.generation())
        jobs.cancel_all()
        release.set()
        blocker.result(timeout=2)
        with pytest.raises(jobs.Cancelled):
            future.result(timeout=2)
        assert calls == []
        pool.submit(queued, jobs.generation()).result(timeout=2)
        assert calls == ['provider']

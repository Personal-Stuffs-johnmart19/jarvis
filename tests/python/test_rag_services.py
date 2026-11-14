from unittest.mock import Mock, patch
import pytest
from local_mode.services import ServiceRegistry

@pytest.mark.parametrize("name,args",[
    ("documents.import_workspace",{"path":"example.txt","workspace":"D:/workspace"}),
    ("documents.import_chat",{"session_id":"actual-session","turn_id":"actual-turn"}),
])
def test_new_rag_sources_share_approval_and_do_not_accept_caller_text(name,args):
    registry=ServiceRegistry(Mock(),lambda:Mock())
    assert registry.risky(name,args)
    with patch("local_mode.rag_sources.ingest_workspace") as workspace,patch("local_mode.rag_sources.ingest_chat") as chat:
        with pytest.raises(ValueError,match="approval"):
            registry.execute(name,args,approved=False)
        workspace.assert_not_called();chat.assert_not_called()
        with pytest.raises(Exception):
            registry.validate(name,dict(args,text="invented source content"))
        registry.execute(name,args,approved=True)
        (workspace if name.endswith("workspace") else chat).assert_called_once_with(**args)


def test_terminal_import_schema_approval_and_saved_id_routing():
    registry = ServiceRegistry(Mock(), lambda: Mock())
    args = {"session_id": "saved", "task_id": "actual-tool-call", "stream": "stdout"}
    with patch("local_mode.terminal_sources.ingest_terminal") as importer:
        with pytest.raises(ValueError, match="approval"):
            registry.execute("documents.import_terminal", args)
        importer.assert_not_called()
        with pytest.raises(Exception):
            registry.validate("documents.import_terminal", {**args, "output": "invented"})
        registry.execute("documents.import_terminal", args, approved=True)
        importer.assert_called_once_with(**args)


def test_terminal_import_requires_explicit_stream_before_dispatch():
    from jsonschema.exceptions import ValidationError
    registry = ServiceRegistry(Mock(), lambda: Mock())
    with patch("local_mode.terminal_sources.ingest_terminal") as importer:
        with pytest.raises(ValidationError, match="stream"):
            registry.execute("documents.import_terminal", {"session_id": "saved", "task_id": "actual-tool-call"}, approved=True)
        importer.assert_not_called()


@pytest.mark.parametrize("backend", ["cloud", "local", "cpu", "auto"])
def test_rebuild_requires_approval_before_embedding_for_every_backend(backend):
    registry = ServiceRegistry(Mock(), lambda: Mock())
    args = {"backend": backend}
    with patch("local_mode.document_store.rebuild", return_value={"rebuilt": [], "failed": []}) as rebuild:
        with pytest.raises(ValueError, match="approval"):
            registry.execute("documents.rebuild", args, approved=False)
        rebuild.assert_not_called()
        registry.execute("documents.rebuild", args, approved=True)
        rebuild.assert_called_once_with(**args)


def test_search_cannot_request_implicit_rebuild_or_call_embedding_lexically():
    from jsonschema.exceptions import ValidationError
    registry = ServiceRegistry(Mock(), lambda: Mock())
    with patch("local_mode.document_store.rebuild") as rebuild, patch("local_mode.document_store.search", return_value={"items": []}) as search:
        with pytest.raises(ValidationError, match="auto_rebuild"):
            registry.execute("documents.search", {"query": "alpha", "auto_rebuild": True})
        search.assert_not_called()
        assert not registry.risky("documents.search", {"query": "alpha"})
        registry.execute("documents.search", {"query": "alpha"})
        search.assert_called_once_with(query="alpha")
        rebuild.assert_not_called()

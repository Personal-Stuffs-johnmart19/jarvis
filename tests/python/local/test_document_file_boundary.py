"""Document handlers must parse the validated byte snapshot, never reopen it."""
import hashlib
import io
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from local_mode import documents, document_store, embeddings, translation
from actions import document_qa, pdf_reader


@pytest.mark.parametrize("reader", [documents.document_bytes, embeddings.attachment,
                                    document_qa._extract_text, pdf_reader._extract_text])
def test_shared_credential_directory_is_blocked_before_parsing(tmp_path, reader):
    path = tmp_path / ".aws" / "notes.pdf"
    path.parent.mkdir()
    path.write_bytes(b"synthetic protected content")
    with pytest.raises(ValueError, match="keys|tokens"):
        reader(str(path))


def test_ingest_hash_and_pdf_parser_receive_identical_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "ROOT", tmp_path)
    path = tmp_path / ".cache/attachments/attachment-report.pdf"
    path.parent.mkdir(parents=True)
    snapshot = b"owned original bytes"
    path.write_bytes(snapshot)

    def parse(source, data):
        path.write_bytes(b"changed after the validated read")
        assert source == path and data == snapshot
        return "parsed original"

    monkeypatch.setattr(documents, "read_document_data", parse)
    item = document_store.ingest(str(path))
    assert item["id"] == hashlib.sha256(snapshot).hexdigest()
    assert document_store.read(item["id"])["text"] == "parsed original"


def test_translation_cloud_fallback_reuses_validated_pdf(tmp_path, monkeypatch):
    path = tmp_path / "original.pdf"
    snapshot = b"validated PDF snapshot"
    path.write_bytes(snapshot)
    monkeypatch.setattr(translation, "document_bytes", lambda _: (path, snapshot))
    validated = Mock()
    monkeypatch.setattr(embeddings, "attachment_data", validated)

    def cloud(*args):
        assert args[3] == snapshot
        path.write_bytes(b"replacement path contents")
        raise translation.TranslationLimit("fixture")

    parse = Mock(return_value="original text")
    monkeypatch.setattr(translation, "cloud_translate", cloud)
    monkeypatch.setattr(translation, "read_document_data", parse)
    monkeypatch.setattr(translation, "local_translate", lambda *args: (args[0], "en"))
    result = translation.translate(path=str(path), target="ru", backend="auto")
    assert result["text"] == "original text"
    validated.assert_called_once_with(path, snapshot)
    parse.assert_called_once_with(path, snapshot)


def test_legacy_pdf_page_count_and_text_share_one_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"PDF snapshot")
    calls = []

    def parse(stream):
        assert isinstance(stream, io.BytesIO)
        assert stream.getvalue() == b"PDF snapshot"
        calls.append(stream)
        path.write_bytes(b"replacement")
        return NS(pages=[NS(extract_text=lambda: "original text")])

    monkeypatch.setattr(pdf_reader, "PdfReader", parse)
    monkeypatch.setattr(pdf_reader, "_PYPdf", True)
    result = pdf_reader.read_pdf({"path": str(path)})
    assert "1 pages" in result and "original text" in result
    assert len(calls) == 1


@pytest.mark.parametrize("suffix", [".txt", ".html", ".docx", ".pptx", ".xlsx"])
def test_legacy_local_read_uses_shared_handle_guard(tmp_path, monkeypatch, suffix):
    from local_mode import file_policy
    path = tmp_path / ("report" + suffix)
    path.write_bytes(b"owned fixture")
    guard = Mock(side_effect=ValueError("opened identity rejected"))
    monkeypatch.setattr(file_policy, "checked_open", guard)
    with pytest.raises(ValueError, match="identity"):
        document_qa._extract_text(str(path))
    guard.assert_called_once_with(path)


def test_media_index_embeds_the_bytes_associated_with_saved_hash(tmp_path, monkeypatch):
    from PIL import Image
    from local_mode import local_embeddings
    monkeypatch.setattr(document_store, "ROOT", tmp_path)
    monkeypatch.setattr(local_embeddings, "preferences", lambda: {"chunk_size": 1500, "chunk_overlap": 200})
    path = tmp_path / ".cache/attachments/attachment-image.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), "red").save(path)
    snapshot = path.read_bytes()
    item = document_store.ingest(str(path))

    def selected(**args):
        path.write_bytes(b"changed after hash validation")
        assert args["file_part"].inline_data.data == snapshot
        return {"model": "fixture", "vector": [1.0, 0.0]}

    monkeypatch.setattr(embeddings, "selected", selected)
    result = document_store.embed(item["id"], "local")
    assert result["document_id"] == hashlib.sha256(snapshot).hexdigest()


def test_embedding_cloud_fallback_keeps_original_file_part(tmp_path, monkeypatch):
    from PIL import Image
    from local_mode import local_embeddings
    path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "red").save(path)
    snapshot = path.read_bytes()

    def cloud(text, selected_path, **args):
        assert args["file_part"].inline_data.data == snapshot
        path.write_bytes(b"replacement bytes")
        raise RuntimeError("fixture quota")

    def local(text, selected_path, backend, **args):
        assert args["file_part"].inline_data.data == snapshot
        return {"model": "fixture"}

    monkeypatch.setattr(embeddings, "embed", cloud)
    monkeypatch.setattr(embeddings.gemini, "local_fallback_allowed", lambda _: True)
    monkeypatch.setattr(local_embeddings, "embed", local)
    assert embeddings.selected(path=str(path), backend="auto")["model"] == "fixture"


def test_private_redirect_is_rejected_before_second_request(monkeypatch):
    response = Mock(status_code=302, headers={"Location": "http://127.0.0.1/private"})
    get = Mock(return_value=response)
    monkeypatch.setattr(document_qa.requests, "get", get)
    monkeypatch.setattr(document_qa, "_is_private_target", lambda url: "127.0.0.1" in url)
    with pytest.raises(RuntimeError, match="private"):
        document_qa._extract_url("https://public.example/document")
    get.assert_called_once()
    assert get.call_args.kwargs["allow_redirects"] is False
    response.close.assert_called_once()


def test_public_relative_redirect_is_checked_and_responses_close(monkeypatch):
    first = Mock(status_code=302, headers={"Location": "/next"})
    final = Mock(status_code=200, encoding="utf-8")
    final.iter_content.return_value = [b"<p>owned response</p>"]
    get = Mock(side_effect=[first, final])
    check = Mock(return_value=False)
    monkeypatch.setattr(document_qa.requests, "get", get)
    monkeypatch.setattr(document_qa, "_is_private_target", check)
    assert "owned response" in document_qa._extract_url("https://public.example/start")
    assert [args.args[0] for args in check.call_args_list] == [
        "https://public.example/start", "https://public.example/next"]
    first.close.assert_called_once()
    first.iter_content.assert_not_called()
    final.close.assert_called_once()


def test_redirect_limit_closes_every_response(monkeypatch):
    responses = [Mock(status_code=302, headers={"Location": "/loop"}) for _ in range(6)]
    get = Mock(side_effect=responses)
    monkeypatch.setattr(document_qa.requests, "get", get)
    monkeypatch.setattr(document_qa, "_is_private_target", lambda _: False)
    with pytest.raises(RuntimeError, match="too many redirects"):
        document_qa._extract_url("https://public.example/start")
    assert get.call_count == 6
    for response in responses:
        response.close.assert_called_once()
        response.iter_content.assert_not_called()


def test_mixed_public_private_dns_answers_are_rejected(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
    ])
    assert document_qa._is_private_target("https://public.example/")


def test_url_body_limit_rejects_stream_before_decoding(monkeypatch):
    monkeypatch.setattr(documents, "MAX_BYTES", 5)
    response = Mock(status_code=200, encoding="utf-8")
    response.iter_content.return_value = iter([b"123", b"456", b"not consumed"])
    get = Mock(return_value=response)
    monkeypatch.setattr(document_qa.requests, "get", get)
    monkeypatch.setattr(document_qa, "_is_private_target", lambda _: False)
    with pytest.raises(RuntimeError, match="exceeds"):
        document_qa._extract_url("https://public.example/document")
    assert get.call_args.kwargs["stream"] is True
    assert next(response.iter_content.return_value) == b"not consumed"
    response.close.assert_called_once()


@pytest.mark.parametrize("encoding,body,expected", [
    ("iso-8859-1", b"caf\xe9", "café"),
    (None, "Привет".encode("utf-8"), "Привет"),
])
def test_url_bounded_bytes_preserve_requests_text_decoding(monkeypatch, encoding, body, expected):
    response = Mock(status_code=200, encoding=encoding)
    response.iter_content.return_value = [body[:2], body[2:]]
    monkeypatch.setattr(document_qa.requests, "get", Mock(return_value=response))
    monkeypatch.setattr(document_qa, "_is_private_target", lambda _: False)
    assert document_qa._extract_url("https://public.example/document") == expected
    response.close.assert_called_once()


@pytest.mark.parametrize("module,handler,resolve,extract,parameters", [
    (document_qa, "read_document", "_resolve_source", "_extract_text", {"path": "owned.pdf"}),
    (document_qa, "document_qa", "_resolve_source", "_extract_text", {"path": "owned.pdf", "question": "fixture"}),
    (pdf_reader, "read_pdf", "_resolve_pdf", "_extract_pdf", {"path": "owned.pdf"}),
    (pdf_reader, "pdf_qa", "_resolve_pdf", "_extract_text", {"path": "owned.pdf", "question": "fixture"}),
])
def test_action_owned_guard_preserves_provenance_for_ru(monkeypatch, module, handler, resolve, extract, parameters):
    from pathlib import Path
    from local_mode.messages import OwnedValueError, message
    error = OwnedValueError("Document must contain 1 byte to 12 MiB.")
    monkeypatch.setattr(module, resolve, lambda _: Path("owned.pdf"))
    monkeypatch.setattr(module, extract, Mock(side_effect=error))
    with pytest.raises(OwnedValueError) as caught:
        getattr(module, handler)(parameters)
    assert caught.value is error
    assert caught.value.payload() == {
        "source": "jarvis", "message": error.template, "values": []}
    assert message(error.template, locale="ru") != error.template


def test_external_action_error_remains_literal(monkeypatch):
    raw = "External parser /папка/document.pdf {0}"
    monkeypatch.setattr(document_qa, "_resolve_source", lambda _: "owned.pdf")
    monkeypatch.setattr(document_qa, "_extract_text", Mock(side_effect=ValueError(raw)))
    assert document_qa.read_document({"path": "owned.pdf"}) == "Could not read that source: " + raw

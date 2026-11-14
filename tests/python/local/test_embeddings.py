"""Embedding: недоверенные файлы, лимит контекста и отсутствие фоновой загрузки."""

from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest
from PIL import Image
from pypdf import PdfWriter
from local_mode import embeddings as module


def test_text_and_file_share_one_content_and_vector_is_normalized(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(path)
    client = Mock()
    client.models.count_tokens.return_value = NS(total_tokens=260)
    client.models.embed_content.return_value = NS(embeddings=[NS(values=[1.0] * 768)])
    with patch.object(module.gemini, "create_client", return_value=nullcontext(client)):
        result = module.embed("описание", str(path))
    assert (
        result["dimensions"] == 768
        and abs(sum(v * v for v in result["vector"]) - 1) < 1e-9
    )
    content = client.models.embed_content.call_args.kwargs["contents"]
    assert (
        len(content.parts) == 2
        and content.parts[1].inline_data.mime_type == "image/png"
    )
    assert (
        client.models.embed_content.call_args.kwargs["config"].output_dimensionality
        == 768
    )


def test_oversized_context_never_reaches_embed():
    client = Mock()
    client.models.count_tokens.return_value = NS(total_tokens=8193)
    with patch.object(module.gemini, "create_client", return_value=nullcontext(client)):
        with pytest.raises(ValueError, match="8192"):
            module.embed("слишком большой материал")
    client.models.embed_content.assert_not_called()


def test_rejects_secret_paths_and_unsupported_files_before_network(tmp_path):
    for name in ["credentials/secret.png", "config/key.png", "file.exe"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private")
        with patch.object(module.gemini, "create_client") as create:
            with pytest.raises((ValueError, OSError)):
                module.embed("", str(path))
            create.assert_not_called()


def test_rejects_large_file_and_mismatched_image(tmp_path, monkeypatch):
    path = tmp_path / "image.jpg"
    Image.new("RGB", (4, 4)).save(path, format="PNG")
    with pytest.raises(ValueError):
        module.attachment(str(path))
    path = tmp_path / "big.wav"
    path.write_bytes(b"x" * 20)
    monkeypatch.setattr(module, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        module.attachment(str(path))


def test_pdf_page_limit_is_local(tmp_path):
    path = tmp_path / "document.pdf"
    writer = PdfWriter()
    for _ in range(7):
        writer.add_blank_page(width=100, height=100)
    with path.open("wb") as f:
        writer.write(f)
    with pytest.raises(ValueError, match="6"):
        module.attachment(str(path))


def test_invalid_output_vector_is_not_reported_as_success():
    client = Mock()
    client.models.count_tokens.return_value = NS(total_tokens=3)
    client.models.embed_content.return_value = NS(
        embeddings=[NS(values=[float("nan")] * 768)]
    )
    with patch.object(module.gemini, "create_client", return_value=nullcontext(client)):
        with pytest.raises(ValueError):
            module.embed("пример")


def test_valid_image_in_actual_config_directory_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    path = tmp_path / "config" / "keys.png"
    path.parent.mkdir()
    Image.new("RGB", (4, 4)).save(path)
    with patch.object(module.gemini, "create_client") as create:
        with pytest.raises(ValueError, match="Credential"):
            module.embed("", str(path))
    create.assert_not_called()

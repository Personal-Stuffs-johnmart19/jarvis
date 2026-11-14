"""Exercise the installed PDF parser through byte snapshots and guarded readers."""
import io

from pypdf import PdfReader, PdfWriter

from actions import pdf_reader
from local_mode import documents, embeddings


def text_pdf():
    content = b"BT /F1 12 Tf 10 100 Td (Owned PDF text) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


def test_reader_writer_roundtrip_preserves_text_and_unicode_metadata():
    reader = PdfReader(io.BytesIO(text_pdf()), strict=True)
    writer = PdfWriter()
    writer.append(reader)
    writer.add_metadata({"/Title": "Owned PDF α"})
    output = io.BytesIO()
    writer.write(output)
    actual = PdfReader(io.BytesIO(output.getvalue()), strict=True)
    assert len(actual.pages) == 1
    assert actual.pages[0].extract_text().strip() == "Owned PDF text"
    assert actual.metadata.title == "Owned PDF α"


def test_document_text_extraction_uses_real_parser_without_ocr():
    assert documents._pdf_text(text_pdf()) == "Owned PDF text"


def test_legacy_reader_parses_guarded_owned_file(tmp_path):
    path = tmp_path / "owned.pdf"
    path.write_bytes(text_pdf())
    text, pages = pdf_reader._extract_pdf(path)
    assert text.strip() == "Owned PDF text" and pages == 1


def test_attachment_pdf_validation_preserves_original_bytes(tmp_path):
    data = text_pdf()
    part = embeddings.attachment_data(tmp_path / "owned.pdf", data)
    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == data

"""Чтение явных документов; PDF-сканы распознаются локально, постранично."""
from __future__ import annotations

import io
from pathlib import Path
import threading
from local_mode.config import ROOT
from local_mode.messages import OwnedValueError, OwnedRuntimeError
from local_mode.file_policy import checked_open, opened_path

_PDF_LOCK = threading.RLock()  # PDFium не допускает одновременных вызовов.
MAX_BYTES = 12 * 1024 * 1024
MAX_CHARS = 24000


def document_bytes(path: str) -> tuple[Path, bytes]:
    if not isinstance(path, str) or len(path) > 2048:
        raise OwnedValueError("Invalid document path.")
    with checked_open(path) as stream:
        source = opened_path(stream)
        if (any(part.casefold() in {'.git', 'credentials'} for part in source.parts)
                or (ROOT / 'config').resolve() in source.parents):
            raise OwnedValueError("Key and internal settings files cannot be processed.")
        if source.suffix.lower() not in {'.txt', '.md', '.pdf', '.docx'}:
            raise OwnedValueError("Choose a TXT, Markdown, PDF or DOCX file.")
        data = stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise OwnedValueError("Document must contain 1 byte to 12 MiB.")
    return source, data


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 6:
        raise OwnedValueError("An unencrypted PDF with 1 to 6 pages is required.")
    pages: list[str] = []
    scanned = [index for index, page in enumerate(reader.pages) if not (page.extract_text() or '').strip()]
    renderer = None
    engine = None
    with _PDF_LOCK:
        try:
            if scanned:
                import pypdfium2
                from vision.ocr_engine import OCREngine, OCRConfig
                renderer = pypdfium2.PdfDocument(data)
                engine = OCREngine(OCRConfig(scale=1.0))
                if not engine.load():
                    raise OwnedRuntimeError("Scanned PDFs require local OCR: install Windows ru/en languages or Tesseract with rus+eng. {0}", str(engine.error))
            for index, page in enumerate(reader.pages):
                content = (page.extract_text() or '').strip()
                if index in scanned:
                    import numpy as np
                    rendered = renderer[index]
                    try:
                        width, height = rendered.get_size()
                        if not 0 < width < 100000 or not 0 < height < 100000:
                            raise OwnedValueError("Invalid PDF page size.")
                        bitmap = rendered.render(scale=min(2.5, 2200 / max(width, height)))
                        try:
                            image = bitmap.to_pil().convert('RGB')
                            frame = np.asarray(image)[:, :, ::-1].copy()
                            words = engine.extract(frame)
                            content = ' '.join(word.label for word in words).strip()
                        finally:
                            bitmap.close()
                        if not content:
                            raise OwnedValueError("OCR found no text on page {0}. Empty or unreadable pages are not silently skipped.", index + 1)
                    finally:
                        rendered.close()
                pages.append(content)
                if sum(map(len, pages)) > MAX_CHARS:
                    raise OwnedValueError("Document exceeds 24000 characters; split it into smaller files.")
        finally:
            if renderer is not None:
                renderer.close()
    return '\n\n'.join(pages)


def pdf_text(data: bytes) -> str:
    from local_mode.worker_rpc import request
    if not 0 < len(data) <= MAX_BYTES:
        raise OwnedValueError("Invalid PDF size.")
    result=request('local_mode.document_worker',data,120)
    text=result.get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>MAX_CHARS:
        raise OwnedValueError("OCR did not return the complete document text.")
    return text


def read_document(path: str) -> str:
    source, data = document_bytes(path)
    return read_document_data(source, data)


def read_document_data(source: Path, data: bytes) -> str:
    """Parse the already validated snapshot without reopening its pathname."""
    if not data or len(data) > MAX_BYTES:
        raise OwnedValueError("Document must contain 1 byte to 12 MiB.")
    suffix = source.suffix.lower()
    if suffix == '.pdf':
        text = pdf_text(data)
    elif suffix == '.docx':
        import zipfile
        from docx import Document
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 40 * 1024 * 1024:
                raise OwnedValueError("The uncompressed DOCX is too large.")
        document = Document(io.BytesIO(data))
        text = '\n'.join([*(p.text for p in document.paragraphs), *('\t'.join(cell.text for cell in row.cells) for table in document.tables for row in table.rows)])
    else:
        text = data.decode('utf-8-sig')
    if not text.strip() or len(text) > MAX_CHARS:
        raise OwnedValueError("A nonempty document with up to 24000 characters is required.")
    return text

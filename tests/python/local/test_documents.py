"""Критические границы OCR/перевода: приватность, полнота страниц и резерв при квоте."""
import io
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
from PIL import Image
from pypdf import PdfWriter
from local_mode import documents


def scanned_pdf() -> bytes:
    stream=io.BytesIO(); Image.new('RGB',(100,80),'white').save(stream,format='PDF'); return stream.getvalue()


def test_scanned_pdf_is_rendered_and_ocr_failure_is_not_silently_skipped():
    from vision.ocr_engine import OCREngine
    with patch.object(OCREngine,'load',return_value=True), patch.object(OCREngine,'extract',return_value=[NS(label='OCR'),NS(label='123')]) as extract:
        assert documents._pdf_text(scanned_pdf())=='OCR 123'
        assert extract.call_args.args[0].shape[2]==3
    with patch.object(OCREngine,'load',return_value=True), patch.object(OCREngine,'extract',return_value=[]):
        with pytest.raises(ValueError,match='OCR'): documents._pdf_text(scanned_pdf())


def test_documents_reject_oversize_pdf_and_service_paths(tmp_path):
    writer=PdfWriter()
    for _ in range(7): writer.add_blank_page(width=100,height=100)
    output=io.BytesIO(); writer.write(output)
    with pytest.raises(ValueError): documents._pdf_text(output.getvalue())
    directory=tmp_path/'credentials'; directory.mkdir(); (directory/'key.txt').write_text('secret')
    with pytest.raises(ValueError): documents.read_document(str(directory/'key.txt'))



def test_native_worker_protocol_is_binary_and_does_not_inherit_api_key(monkeypatch):
    from local_mode import worker_rpc
    monkeypatch.setenv('GEMINI_API_KEY','synthetic-secret')
    with patch.object(worker_rpc,'run',return_value=NS(returncode=0,stdout=b'{"text":"ok"}')) as child:
        assert worker_rpc.request('local_mode.document_worker',b'%PDF',10)['text']=='ok'
    arguments=child.call_args.kwargs
    assert arguments['input']==b'%PDF' and 'GEMINI_API_KEY' not in arguments['env']
    assert not arguments.get('text',False)
    with patch.object(worker_rpc,'run',return_value=NS(returncode=-1,stdout=b'')):
        with pytest.raises(RuntimeError): worker_rpc.request('local_mode.document_worker',b'%PDF',10)

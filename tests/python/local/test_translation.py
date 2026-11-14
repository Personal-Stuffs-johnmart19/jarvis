"""Критические границы OCR/перевода: приватность, полнота страниц и резерв при квоте."""
import io
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest
from PIL import Image
from pypdf import PdfWriter
from google.genai.errors import APIError
from local_mode import documents, translation, embeddings


def scanned_pdf() -> bytes:
    stream=io.BytesIO(); Image.new('RGB',(100,80),'white').save(stream,format='PDF'); return stream.getvalue()


def test_quota_uses_local_but_explicit_cloud_and_bad_requests_do_not():
    quota=APIError(429,{'error':{'message':'quota'}})
    with patch.object(translation,'cloud_translate',side_effect=quota), patch.object(translation,'local_translate',return_value=('Привет','en')) as local:
        result=translation.translate('Hello','en','ru',backend='auto')
        assert result['backend']=='local' and result['fallback_reason']
        with pytest.raises(APIError): translation.translate('Hello','en','ru',backend='cloud')
        assert local.call_count==1
    with patch.object(translation,'cloud_translate',side_effect=APIError(400,{'error':{'message':'bad input'}})), patch.object(translation,'local_translate') as local:
        with pytest.raises(APIError): translation.translate('Hello','en','ru',backend='auto')
        local.assert_not_called()


def test_explicit_local_never_calls_cloud_and_unsupported_language_is_honest():
    with patch.object(translation,'cloud_translate') as cloud, patch.object(translation,'local_translate',return_value=('Hi','ru')):
        assert translation.translate('Привет','ru','en',backend='local')['backend']=='local'
        cloud.assert_not_called()
    with pytest.raises(ValueError): translation._local_translate('Bonjour','fr','ru')


def test_long_local_paragraph_is_chunked_without_engine_truncation():
    tokenizer=Mock(); tokenizer.encode.side_effect=lambda text,out_type: list(text); tokenizer.decode.side_effect=lambda tokens: ''.join(tokens)
    engine=Mock(); engine.translate_batch.side_effect=lambda chunks,**options: [NS(hypotheses=[tokens]) for tokens in chunks]
    with patch.object(translation,'local_model',return_value=(tokenizer,engine)):
        result,_=translation._local_translate('a'*600,'en','ru')
    assert result.replace(' ','')=='a'*600
    chunks=engine.translate_batch.call_args.args[0]
    assert max(map(len,chunks))<=180 and engine.translate_batch.call_args.kwargs['max_input_length']==0


def test_cloud_pdf_is_explicit_native_document_not_local_ocr(tmp_path):
    path=tmp_path/'scan.pdf'; path.write_bytes(scanned_pdf())
    with patch.object(translation,'cloud_translate',return_value=('Translated','ru')) as cloud, patch.object(translation,'read_document_data') as local:
        result=translation.translate(path=str(path),target='en',backend='cloud')
        assert result['backend']=='cloud' and cloud.call_args.args[3].startswith(b'%PDF')
        local.assert_not_called()


def test_embedding_auto_falls_back_on_quota_but_local_never_uploads():
    with patch.object(embeddings,'embed',side_effect=APIError(429,{'error':{'message':'quota'}})) as cloud, patch('local_mode.local_embeddings.embed',return_value={'model':'local'}) as local:
        assert embeddings.selected('hello',backend='auto')['fallback_reason']
        assert embeddings.selected('hello',backend='local')['model']=='local'
        assert cloud.call_count==1 and local.call_count==2


def test_translation_settings_are_persistent_and_isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(translation,'ROOT',tmp_path)
    assert translation.preferences()['backend']=='auto'
    translation.preferences('local')
    assert translation.preferences()['backend']=='local'
    with pytest.raises(ValueError): translation.preferences('arbitrary')

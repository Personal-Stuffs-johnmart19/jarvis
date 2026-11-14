"""Изолированный PDFium/Windows OCR: вход PDF, выход JSON, ни одного сетевого API."""
import json
import sys
from local_mode.messages import OwnedError, OwnedValueError


def main() -> None:
    try:
        from local_mode.documents import _pdf_text, MAX_BYTES
        data=sys.stdin.buffer.read(MAX_BYTES+1)
        if not 0<len(data)<=MAX_BYTES: raise OwnedValueError('Invalid PDF size.')
        reply={'text':_pdf_text(data)}
    except Exception as error:
        reply={'error': error.payload(), 'error_kind': 'runtime' if isinstance(error, RuntimeError) else 'value'} if isinstance(error, OwnedError) else {'error':str(error)[:1500]}
    sys.stdout.buffer.write(json.dumps(reply,ensure_ascii=False).encode('utf8'))
    sys.stdout.buffer.flush()


if __name__=='__main__': main()

"""Изолированный CPU translator: stdin/stdout JSON, без Gemini и PDFium вызовов."""
import contextlib
import json
import sys


def main() -> None:
    try:
        request=json.loads(sys.stdin.read(128000))
        text=request['text']
        if not isinstance(text,str) or not 0 < len(text) <= 24000:
            raise ValueError('Локальный перевод: нужен текст до 24000 символов.')
        with contextlib.redirect_stdout(sys.stderr):
            from local_mode.translation import _local_translate, language
            translated,source=_local_translate(text,language(request['source'],auto=True),language(request['target']))
        reply={'text':translated,'source':source}
    except Exception as error:
        from local_mode.gemini import safe_error
        reply={'error':safe_error(error)}
    sys.stdout.write(json.dumps(reply,ensure_ascii=False))
    sys.stdout.flush()


if __name__=='__main__':
    main()

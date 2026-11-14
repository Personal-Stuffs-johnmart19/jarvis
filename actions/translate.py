"""Инструмент агента использует тот же переводчик и предпочтения, что GUI."""
from local_mode.translation import translate


def translate_action(parameters: dict, response=None, player=None, session_memory=None) -> str | dict:
    params=parameters or {}
    try:
        result=translate(text=params.get('text',''),source=params.get('from','auto'),target=params.get('to',params.get('target','en')) or 'en')
        return result['text']
    except Exception as error:
        from local_mode.gemini import safe_error
        return {"ok": False, "error": safe_error(error)}

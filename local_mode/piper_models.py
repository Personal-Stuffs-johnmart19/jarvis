"""Verified managed Piper bundles and stable, model-bound speaker choices."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
from local_mode.config import ROOT
from local_mode.messages import OwnedValueError


def settings_path() -> Path:
    return ROOT / ('.cache/desktop-test' if os.getenv('JARVIS_DESKTOP_TEST') == '1' else 'data') / 'piper-model.json'


def inspect_config(path: Path) -> dict:
    if path.stat().st_size > 1_048_576:
        raise OwnedValueError('Piper configuration is too large.')
    config=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config,dict):
        raise OwnedValueError('Invalid Piper voice configuration.')
    if not isinstance(config.get('inference',{}),dict):
        raise OwnedValueError('Invalid Piper voice configuration.')
    symbols=config.get('num_symbols');phonemes=config.get('phoneme_id_map')
    if (type(symbols) is not int or not 1<=symbols<=65536 or not isinstance(phonemes,dict) or not 1<=len(phonemes)<=65536
        or any(not isinstance(key,str) or len(key)>128 or not isinstance(ids,list) or len(ids)>64
               or any(type(identifier) is not int or not 0<=identifier<symbols for identifier in ids) for key,ids in phonemes.items())):
        raise OwnedValueError('Invalid Piper voice configuration.')
    count=config.get('num_speakers')
    audio=config.get('audio');espeak=config.get('espeak')
    if (type(count) is not int or not 1<=count<=1024 or config.get('phoneme_type','espeak')!='espeak'
        or not isinstance(audio,dict) or type(audio.get('sample_rate')) is not int or not 8000<=audio['sample_rate']<=96000
        or not isinstance(espeak,dict) or not isinstance(espeak.get('voice'),str)
        or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',espeak['voice'])):
        raise OwnedValueError('Invalid Piper voice configuration.')
    speakers=config.get('speaker_id_map',{})
    if (not isinstance(speakers,dict) or len(speakers)>count
        or any(not isinstance(name,str) or not 1<=len(name)<=128 or any(ord(char)<32 for char in name)
               or type(identifier) is not int or not 0<=identifier<count for name,identifier in speakers.items())
        or len(set(speakers.values()))!=len(speakers)):
        raise OwnedValueError('Invalid Piper speaker map.')
    return config


def configuration() -> tuple[dict,dict,str]:
    path=settings_path()
    if path.stat().st_size>16384:
        raise OwnedValueError('Invalid managed Piper selection.')
    selection=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(selection,dict) or selection.get('family')!='piper' or selection.get('mode')!='cpu':
        raise OwnedValueError('Invalid managed Piper selection.')
    from local_mode.model_library import validate_selected_model
    derived=validate_selected_model(selection,'tts')
    if derived.get('family')!='piper':
        raise OwnedValueError('Invalid managed Piper selection.')
    config=inspect_config(Path(derived['config_path']))
    identity=hashlib.sha256(json.dumps(selection,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]
    return {**derived,"repo_id":selection["repo_id"],"revision":selection["revision"]},config,identity


def voices() -> list[dict[str,str]]:
    try:
        _,config,identity=configuration()
    except (OSError,ValueError,TypeError,KeyError):
        return []
    names={identifier:name for name,identifier in config.get('speaker_id_map',{}).items()}
    language=config['espeak']['voice']
    return [{'id':f'piper:managed:{identity}:{speaker}',
             'name':f'Piper — {language} / {names.get(speaker,str(speaker))}',
             'language':language} for speaker in range(config['num_speakers'])]


def resolve(voice: str) -> tuple[Path,int,str,str]:
    derived,config,identity=configuration()
    match=re.fullmatch(r'piper:managed:([a-f0-9]{24}):(0|[1-9][0-9]{0,3})',voice)
    if not match or match[1]!=identity or int(match[2])>=config['num_speakers']:
        raise OwnedValueError('The selected Piper speaker does not belong to the active model.')
    return Path(derived['model_path']),int(match[2]),config['espeak']['voice'],identity


def engine_for(engines: dict,voice: str):
    """Keep one managed voice; invalidate it before reloading changed or missing assets."""
    try:
        model_path,speaker,language,identity=resolve(voice)
    except (OSError,ValueError,TypeError,KeyError):
        engines.pop('managed_piper',None)
        raise
    key=(identity,speaker)
    current=engines.get('managed_piper')
    if current is None or current.managed_identity!=key:
        engines.pop('managed_piper',None)
        current=None
        from local_mode.piper_voice import RussianVoice
        current=RussianVoice(model_path,speaker,language)
        current.managed_identity=key
        engines['managed_piper']=current
    return current


def status() -> dict:
    if not settings_path().exists():
        from local_mode.piper_voice import MODEL
        available=False
        try:
            if MODEL.is_file():
                inspect_config(Path(str(MODEL)+".json"))
                available=True
        except (OSError,ValueError,TypeError):
            pass
        return {"managed":False,"available":available,"repo_id":"Built-in Piper Denis","revision":"","mode":"cpu"}
    try:
        derived,config,_=configuration()
        return {"managed":True,"available":True,"repo_id":derived["repo_id"],"revision":derived["revision"],"mode":"cpu","speakers":config["num_speakers"]}
    except (OSError,ValueError,TypeError,KeyError):
        return {"managed":True,"available":False,"repo_id":"","revision":"","mode":"cpu"}


def preview_text(voice: str, explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit
    from local_mode.messages import speech_preview
    language=resolve(voice)[2] if voice.startswith('piper:managed:') else 'ru'
    language=re.split(r'[-_]',language.lower())[0]
    if language not in {'en','ru'}:
        raise OwnedValueError('Provide preview text in the selected speech language')
    return speech_preview(language)

"""Загрузка только официальной маленькой русской модели Vosk."""
from pathlib import Path
import hashlib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
URL = 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip'


def main() -> None:
    destination = ROOT / 'models'
    model = destination / 'vosk-model-small-ru-0.22'
    if (model / 'am' / 'final.mdl').is_file():
        print('Русская модель Vosk уже установлена.')
        return
    archive = ROOT / '.cache' / 'vosk-model-small-ru-0.22.zip'
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        print('Загрузка официальной модели Vosk (~45 MB)…', flush=True)
        with urllib.request.urlopen(URL, timeout=60) as source, archive.open('wb') as output:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError('Неожиданно большой архив модели.')
                output.write(chunk)
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if sum(entry.file_size for entry in bundle.infolist()) > 200 * 1024 * 1024:
            raise ValueError('Неожиданный размер распакованной модели.')
        for entry in bundle.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(model.resolve()):
                raise ValueError('Недопустимый путь в архиве модели.')
        bundle.extractall(destination)
    if not (model / 'am' / 'final.mdl').is_file():
        raise RuntimeError('Архив не содержит ожидаемую русскую модель.')
    print('Модель Vosk установлена. SHA256 архива:', hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()

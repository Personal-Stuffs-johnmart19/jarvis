"""Сохранение подсказок между сеансами нового интерфейса."""
from unittest.mock import patch
import pytest
from local_mode import suggestions_store
from local_mode.suggestion_defaults import SUGGESTIONS


def test_crud_reorder_and_restart(tmp_path):
    path=tmp_path/'hints.json'
    items=list(SUGGESTIONS)+[('Своя','Моя команда')]
    suggestions_store.save(path,items)
    items[-1]=('Изменённая','Другая команда')
    moved=items.pop();items.insert(0,moved)
    suggestions_store.save(path,items)
    assert suggestions_store.load(path,SUGGESTIONS)==items
    items.pop(0);suggestions_store.save(path,items)
    assert suggestions_store.load(path,SUGGESTIONS)==list(SUGGESTIONS)
    suggestions_store.save(path,[])
    assert suggestions_store.load(path,SUGGESTIONS)==[]


def test_invalid_or_failed_save_keeps_existing_data(tmp_path):
    path=tmp_path/'hints.json';suggestions_store.save(path,[('Команда','Текст')]);before=path.read_bytes()
    for invalid in [[('', 'текст')],[('a'*61,'текст')],[('Имя','')],[('Имя','x'*8001)]]:
        with pytest.raises(ValueError):suggestions_store.save(path,invalid)
        assert path.read_bytes()==before
    with patch('local_mode.suggestions_store.write_json',side_effect=OSError('Нет доступа')):
        with pytest.raises(OSError):suggestions_store.save(path,[('Новая','Текст')])
    assert path.read_bytes()==before

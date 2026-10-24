"""The words of an add request stay out of the task's name (#280, #281)."""
import datetime as dt
import json
from unittest.mock import patch

import db
import llm
from support import Sandbox


def route(title, due=''):
    return json.dumps({'intent': 'add', 'focus': '', 'actions': [
        {'type': 'add_task', 'task_id': 0, 'title': title, 'project': '', 'due': due, 'notes': ''}]}, ensure_ascii=False)


def test_unclassified_is_fine_words_are_not_in_the_name():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, 'chat', return_value=route('ES提出', tomorrow)):
            r = sb.chat('明日までにES 提出を未分類でいいから追加して')
        tasks = db.list_tasks()
        assert [(t['title'], t['due'], t['project']) for t in tasks] == [('ES 提出', tomorrow, None)], (tasks, r)


def test_the_models_added_word_is_dropped_instead_of_refusing():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, 'chat', return_value=route('動作確認メモの追加', tomorrow)):
            r = sb.chat('明日までに動作確認メモを未分類でいいから追加して')
        tasks = db.list_tasks()
        assert [(t['title'], t['due']) for t in tasks] == [('動作確認メモ', tomorrow)], (tasks, r)


def test_an_add_that_cannot_be_checked_asks_for_the_name():
    with Sandbox(copy_real=False) as sb:
        with patch.object(llm, 'chat', return_value=route('履歴書の作成')):
            r = sb.chat('あれを追加しておいて')
        assert db.list_tasks() == []
        assert '変えたい項目' not in r['reply'] and '名前' in r['reply'], r['reply']

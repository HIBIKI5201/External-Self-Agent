"""Project names match exactly first; a part of a name only when it clearly means one project (#197)."""
import db
import mcp_server
from support import Sandbox


def _setup():
    for name in ('Warframe', 'EsAgent', '就活', '全般', 'Sinfonia Studio'):
        db.ensure_project(name)


def _find(name):
    with db.connect() as con:
        p = db.find_project(con, name)
    return p['name'] if p else None


def test_a_name_containing_an_existing_project_is_a_new_project():
    with Sandbox(copy_real=False):
        _setup()
        assert db.ensure_project('Warframeウィークリー') is True
        assert _find('Warframeウィークリー') == 'Warframe' + 'ウィークリー'
        assert _find('Warframe') == 'Warframe'
        t = db.add_task('次元アルキメディア', project='Warframeウィークリー')
        assert t['project'] == 'Warframeウィークリー'


def test_ensure_project_reuses_the_project_a_short_name_means():
    """Review on #221: brain calls ensure_project before add_task, so it must match like find_project."""
    with Sandbox(copy_real=False):
        _setup()
        before = [p['name'] for p in db.list_projects()]
        assert db.ensure_project('Sinfonia') is False
        assert db.ensure_project('sinfonia studio') is False
        assert [p['name'] for p in db.list_projects()] == before
        assert db.add_task('譜面の確認', project='Sinfonia')['project'] == 'Sinfonia Studio'


def test_exact_match_ignores_case_spaces_width_and_category_words():
    with Sandbox(copy_real=False):
        _setup()
        assert _find('esagent') == 'EsAgent'
        assert _find('ＥｓＡｇｅｎｔ') == 'EsAgent'
        assert _find('sinfonia studio') == 'Sinfonia Studio'
        assert _find('SinfoniaStudio') == 'Sinfonia Studio'
        assert _find('EsAgentカテゴリー') == 'EsAgent'
        assert db.ensure_project('esagent') is False and db.ensure_project('EsAgentカテゴリー') is False


def test_short_or_loose_parts_do_not_pick_a_project():
    with Sandbox(copy_real=False):
        _setup()
        for name in ('Es', 'S', '就', '全般的なこと', 'Warframeのウィークリー'):
            assert _find(name) is None, name
        assert _find('Sinfonia') == 'Sinfonia Studio'  # most of one project's name
        db.ensure_project('Sinfonia Lab')
        assert _find('Sinfonia') is None  # two candidates: do not guess


def test_add_command_stops_on_a_one_letter_project():
    with Sandbox(copy_real=False) as sb:
        _setup()
        before = db.list_tasks(include_done=True)
        r = sb.skill('/add 牛乳 #S')
        assert '見つからない' in r['reply'], r['reply']
        assert db.list_tasks(include_done=True) == before


def test_mcp_add_task_reports_an_unknown_project():
    with Sandbox(copy_real=False):
        _setup()
        before = db.list_tasks(include_done=True)
        r = mcp_server.call_tool('add_task', {'title': 'フォローアップ', 'project': 'Unknown'})
        assert 'error' in r and 'Unknown' in r['error'] and 'EsAgent' in r['error'], r
        assert db.list_tasks(include_done=True) == before
        r = mcp_server.call_tool('add_task', {'title': 'フォローアップ', 'project': 'esagent'})
        assert r['project'] == 'EsAgent'

"""Reading Es's lines aloud with VOICEVOX (#44, AS-19), with a fake engine and no sound."""
import json
import os
import subprocess
import sys
from urllib.parse import unquote_plus

from markers import needs_voicevox
from support import Sandbox

import db  # noqa: E402
import voice  # noqa: E402  (support puts esagent/ on the path)

SPEAKERS = [{"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 3}]},
            {"name": "冥鳴ひまり", "styles": [{"name": "ノーマル", "id": 14}]}]


class FakeEngine:
    """Stands in for VOICEVOX and the speaker: records what was synthesised and played."""

    def __init__(self, alive=True, fail=None):
        self.alive, self.fail = alive, fail
        self.queries, self.played = [], []

    def __enter__(self):
        self.saved = (voice.THREADED, voice._http, voice._alive, voice._play, voice._stop_sound, dict(voice._state))
        voice.THREADED = False
        voice._http, voice._alive, voice._play = self.http, lambda url: self.alive, self.play
        voice._stop_sound = lambda: None
        voice._style.clear()
        voice._seen.clear()
        voice._queue.clear()
        voice._recent.clear()
        voice._state.update(engine="off", error="", speaking="", last_spoken=0.0)
        return self

    def __exit__(self, *exc):
        voice.stop()
        (voice.THREADED, voice._http, voice._alive, voice._play, voice._stop_sound, state) = self.saved
        voice._state.update(state)

    def http(self, url, path, data=None, timeout=5):
        if self.fail:
            raise self.fail
        if path == "/speakers":
            return json.dumps(SPEAKERS).encode()
        if path.startswith("/audio_query"):
            self.queries.append(path)
            return json.dumps({"volumeScale": 1.0, "accent_phrases": []}).encode()
        if path.startswith("/synthesis"):
            q = json.loads(data)
            return f"RIFF{path}|{q['volumeScale']}".encode()
        raise AssertionError(path)

    def play(self, wav, gen):
        self.played.append(wav.decode())


def _cfg(sb, **extra):
    return {**sb.cfg, "voicevox_engine_path": os.path.join(sb.dir, "missing-run.exe"), **extra}


def test_speakable_drops_markdown_code_and_urls():
    text = "## 今日のタスク\n- **報告書** を書く\n詳細は https://example.com/a?b=1 を見てね\n```py\nprint(1)\n```\n`git push` した"
    out = voice.speakable(text)
    assert "#" not in out and "*" not in out and "http" not in out and "print" not in out, out
    assert "報告書 を書く。" in out and "リンク" in out and "git push した" in out, out


def test_speakable_cuts_long_text_at_a_sentence_end():
    out = voice.speakable("あいうえお。" * 60, max_chars=50)
    assert out.endswith("続きは画面で見てね。") and len(out) < 70, out
    assert out.replace("続きは画面で見てね。", "").endswith("。"), out


def test_off_by_default_and_nothing_is_spoken():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        assert voice.settings()["enabled"] is False
        assert voice.say(_cfg(sb), "こんにちは") is False
        assert voice.reply(_cfg(sb), {"reply": "了解。"}) is False
        voice.process_pending(_cfg(sb))
        assert fe.played == []


def test_reads_with_himari_once_per_key_and_volume():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True, volume=0.5)
        assert voice.say(cfg, "休憩おわり。次、いく？", key="event:7")
        assert not voice.say(cfg, "休憩おわり。次、いく？", key="event:7")  # the chat window and the cat
        assert not voice.say(cfg, "休憩おわり。次、いく？")                # same line again right away
        voice.process_pending(cfg)
        assert fe.played == ["RIFF/synthesis?speaker=14|0.5"], fe.played
        assert voice.status(cfg)["credit"] == "VOICEVOX:冥鳴ひまり"


def test_pending_reply_is_not_read_but_the_late_answer_is():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        assert not voice.reply(cfg, {"reply": "", "pending": True, "note": "あとで返事するね"})
        assert voice.reply(cfg, {"reply": "追加したよ。", "events": ["＋ 洗濯"]}, key="answered:5")
        voice.process_pending(cfg)
        assert len(fe.played) == 1 and len(fe.queries) == 1


def test_stop_drops_the_queue():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        for i in range(3):
            voice.say(cfg, f"{i}番目の話", key=f"k{i}")
        assert voice.status(cfg)["queued"] == 3
        voice.stop()
        assert voice.status(cfg)["queued"] == 0
        voice.process_pending(cfg)
        assert fe.played == []


def test_turning_off_stops_and_does_not_start_the_engine_later():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        voice.say(cfg, "まだ読まれていない")
        voice.update(cfg, enabled=False)
        voice.process_pending(cfg)
        assert fe.played == [] and voice.say(cfg, "オフのあと") is False


def test_missing_engine_is_a_status_not_an_error():
    with Sandbox(copy_real=False) as sb, FakeEngine(alive=False) as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        assert voice.say(cfg, "聞こえる？")
        voice.process_pending(cfg)  # must not raise
        st = voice.status(cfg)
        assert fe.played == [] and st["engine"] == "missing" and "VOICEVOX" in st["error"], st


def test_engine_failure_is_explained():
    import urllib.error
    with Sandbox(copy_real=False) as sb, FakeEngine(fail=urllib.error.URLError("refused")) as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        voice.say(cfg, "聞こえる？")
        voice.process_pending(cfg)
        assert fe.played == [] and voice.status(cfg)["error"] == "VOICEVOX に接続できませんでした。"


def test_unknown_speaker_is_reported():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb, voicevox_speaker="存在しない声")
        voice.update(cfg, enabled=True)
        voice.say(cfg, "テスト")
        voice.process_pending(cfg)
        assert fe.played == [] and "存在しない声" in voice.status(cfg)["error"]


def test_trial_plays_while_off():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        assert voice.say(cfg, voice.SAMPLE, force=True)
        voice.process_pending(cfg)
        assert len(fe.played) == 1


def test_timer_notices_are_read_by_the_server_once():
    import pomodoro
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        saved = pomodoro._bgm
        pomodoro._bgm = lambda action: None
        try:
            pomodoro.start(cfg)
        finally:
            pomodoro._bgm = saved
        voice.process_pending(cfg)
        pomodoro.reset(cfg)
        assert len(fe.played) == 1 and "集中タイム" in unquote_plus(fe.queries[0]), fe.queries


def test_engine_started_by_es_is_stopped():
    """The engine Es launches is hers to stop; a stand-in process plays the engine."""
    with FakeEngine():
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        voice._proc = proc
        voice.stop_engine()
        assert proc.poll() is not None and voice._proc is None


class FakeProc:
    def __init__(self, args, exits):
        self.args, self.exits = args, exits

    def poll(self):
        return 1 if self.exits else None


def test_engine_starts_on_the_gpu_and_falls_back_to_the_cpu():
    """GPU synthesis is ~30x faster here; a PC without a usable GPU still gets the CPU engine."""
    with Sandbox(copy_real=False) as sb, FakeEngine(alive=False) as fe:
        engine = os.path.join(sb.dir, "run.exe")
        open(engine, "w").close()
        cfg = _cfg(sb, voicevox_engine_path=engine)
        launched = []

        def popen(args, **kw):
            gpu = "--use_gpu" in args
            launched.append("gpu" if gpu else "cpu")
            if not gpu:
                fe.alive = True  # the CPU engine comes up
            return FakeProc(args, exits=gpu)
        saved = (voice.subprocess.Popen, voice._bind_to_us)
        voice.subprocess.Popen, voice._bind_to_us = popen, lambda proc: None
        try:
            voice._alive = lambda url: fe.alive
            assert voice._engine(cfg) == voice.DEFAULT_URL
            assert launched == ["gpu", "cpu"] and voice._state["device"] == "cpu", launched
            voice._proc = None
            launched.clear()
            fe.alive = False
            voice.subprocess.Popen = lambda args, **kw: (launched.append(args), setattr(fe, "alive", True),
                                                         FakeProc(args, exits=False))[2]
            voice._engine(_cfg(sb, voicevox_engine_path=engine, voicevox_use_gpu=False))
            assert "--use_gpu" not in launched[0]
        finally:
            voice.subprocess.Popen, voice._bind_to_us = saved
            voice._proc = None


def test_recent_lines_are_kept_for_the_echo_check():
    """会話モード (#89) drops what the mic hears of Es's own voice; it needs her recent lines."""
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        voice.say(cfg, "休憩おわり。次、いく？")
        voice.process_pending(cfg)
        assert voice.status(cfg)["recent"] == ["休憩おわり。次、いく？"]
        voice._recent[0][1] -= voice.ECHO_SECS + 1
        assert voice.status(cfg)["recent"] == []


def test_reading_fixes_keep_the_chat_text():
    """#117: 「～！次は」 was read ジ, 「E.S. Agent」 イイ、エス、エエジェント. Only the voice text changes."""
    assert voice.speakable("言葉がいいよ～！次は？") == "言葉がいいよー！次は？"
    assert voice.speakable("言葉がいいよ〜！次は？") == "言葉がいいよー！次は？"
    for name in ("E.S. Agent", "E.S.Agent", "ES Agent", "e.s. agent", "E.S.エージェント"):
        assert voice.speakable(f"{name}だよ") == "エスエージェントだよ。", name
    assert voice.speakable("10～12時に集合") == "10から12時に集合。"
    assert voice.speakable("月〜金は忙しい") == "月から金は忙しい。"
    assert voice.speakable("Esだよ。次回もよろしく") == "Esだよ。次回もよろしく。"


@needs_voicevox
def test_voicevox_reads_the_fixed_text():
    import json
    import urllib.parse
    import urllib.request
    cfg = db.load_config()
    url = cfg.get("voicevox_url", "http://127.0.0.1:50021").rstrip("/")
    speakers = json.load(urllib.request.urlopen(url + "/speakers", timeout=30))
    sid = speakers[0]["styles"][0]["id"]

    def kana(text):
        req = urllib.request.Request(url + "/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": sid}),
                                     method="POST")
        return json.load(urllib.request.urlopen(req, timeout=60))["kana"]
    assert "ツギ" in kana(voice.speakable("言葉がいいよ～！次は？"))
    assert "イイ、エス" not in kana(voice.speakable("E.S. Agent"))
    assert kana(voice.speakable("E.S. Agent")).startswith("エスエ")
def test_listen_corrections_come_from_defaults_and_config():
    """#118: the window gets the table of known words and their mishearings."""
    table = voice.listen_corrections({"listen_corrections": {"Sinfonia": ["シンフォニア"]}})
    assert "ウォーフレーム" in table["Warframe"] and table["Sinfonia"] == ["シンフォニア"]
    assert voice.listen_corrections({"listen_corrections": {"Warframe": ["ワーフレ"]}})["Warframe"] == ["ワーフレ"]
    assert voice.listen_corrections({"listen_corrections": {"x": "not a list"}}).get("x") is None


def test_a_line_that_may_not_wake_the_engine_is_dropped_when_it_is_stopped():
    # #284: every caring line restarted the engine that had stopped after voicevox_idle_minutes.
    with Sandbox(copy_real=False) as sb, FakeEngine(alive=False) as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        launched = []
        with_launch = voice._launch
        voice._launch = lambda *a: launched.append(a)
        try:
            assert voice.say(cfg, "お水飲んでる？", wake=False)
            voice.process_pending(cfg)
        finally:
            voice._launch = with_launch
        st = voice.status(cfg)
        assert fe.played == [] and launched == [] and st["error"] == "", st


def test_a_line_that_may_not_wake_the_engine_is_read_while_it_runs():
    with Sandbox(copy_real=False) as sb, FakeEngine() as fe:
        cfg = _cfg(sb)
        voice.update(cfg, enabled=True)
        assert voice.say(cfg, "お水飲んでる？", wake=False)
        voice.process_pending(cfg)
        assert len(fe.played) == 1

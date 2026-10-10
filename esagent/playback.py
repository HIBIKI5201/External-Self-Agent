"""Ephemeral app-only playback facts. No database, model, or media controls (#101)."""
import re
import threading
import time
import uuid


class Playback:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.token = ""
        self.sequence = -1
        self.value = None
        self.updated = None

    def open(self):
        with self.lock:
            self.token = uuid.uuid4().hex
            self.sequence = -1
            self.value = None
            self.updated = None
            return {"token": self.token}

    def update(self, body):
        with self.lock:
            seq = body.get("sequence")
            if body.get("token") != self.token or not self.token or type(seq) is not int or seq <= self.sequence:
                return {"accepted": False}
            state = body.get("state")
            if state not in ("none", "selected", "playing", "paused", "ended", "buffering", "error", "external"):
                return {"accepted": False}
            def clean(key, size):
                value = body.get(key, "")
                return "".join(c for c in value if c.isprintable())[:size] if isinstance(value, str) else ""
            self.value = {"state": state, "title": clean("title", 200), "channel": clean("channel", 120),
                          "video_id": clean("video_id", 64), "source": "app-player" if state not in ("selected", "external", "none") else "app-selection"}
            self.sequence = seq
            self.updated = self.clock()
            return {"accepted": True}

    def snapshot(self):
        with self.lock:
            if self.value is None or self.updated is None or self.clock() - self.updated > 15:
                return {"state": "unknown", "source": "unavailable"}
            return {**self.value, "age_seconds": max(0, round(self.clock() - self.updated, 1))}


current = Playback()


def answer(text):
    # A question about media is read-only. Never pass external titles into a prompt.
    if not re.fullmatch(r"\s*(?:今(?:は)?何(?:が|を)(?:流れて(?:いる|る)|流し(?:ている|てる)|再生(?:している|してる))|(?:今の|現在の|今流れている|今流れてる|再生中の)?(?:BGM|曲|音楽|動画|YouTube)(?:は何|はなに|の名前(?:は)?|のタイトル(?:は)?|(?:が|は)?(?:流れて(?:いる|る)|再生中)(?:なの)?|の内容(?:は)?|(?:を)?(?:聞ける|聴ける|見れる|見える)))(?:の|か|？|\?|。|！|!|\s)*", text, re.I):
        return None
    value = current.snapshot()
    state = value["state"]
    if state == "unknown":
        return "アプリ内の最新の再生状態は取得できていないよ。YouTube・BGMの画面を開いて確認してね。音や映像の内容は取得していないよ。"
    if state == "none":
        return "アプリ内では動画が選ばれていないよ。"
    title = value.get("title") or "タイトル不明の動画"
    labels = {"selected": "選択中", "playing": "再生中", "paused": "一時停止中", "ended": "再生終了",
              "buffering": "読み込み中", "error": "再生エラー", "external": "外部再生用に選択中"}
    reply = f"アプリ内の情報では「{title}」が{labels[state]}だよ。"
    if value.get("channel"):
        reply += f" チャンネル: {value['channel']}。"
    if state == "external":
        reply += " 外部ウィンドウの現在の再生状態は共有していないよ。"
    return reply + " タイトルと状態だけを参照していて、音や映像の内容は取得していないよ。"

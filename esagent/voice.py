"""Es's voice: reads her lines aloud with VOICEVOX on this PC (#44, AS-19).

One player for the whole app, on the server: the chat window, the desktop cat and the timer all
hand their lines here, so a line is spoken once even with several windows open, and Es keeps
talking with the window closed. Reading aloud never blocks the chat or task operations: lines go
to a queue that one worker thread synthesises and plays, and every failure ends up as a status
line instead of an exception.

Es starts the VOICEVOX engine (run.exe, no GUI) when she needs it and is responsible for it: the
engine she started is stopped when reading aloud is turned off, after a while without speech, and
with the server (a Windows job object kills it even when the server is killed). An engine that
was already running (the VOICEVOX app) is used as is and never stopped.

Audio stays on this PC and is not kept: each line is written to a temp file only while it plays.
"""
import atexit
import collections
import ctypes
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import db

log = logging.getLogger("esagent")

DEFAULT_URL = "http://127.0.0.1:50021"
DEFAULT_ENGINE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VOICEVOX", "vv-engine", "run.exe")
DEFAULT_SPEAKER, DEFAULT_STYLE = "冥鳴ひまり", "ノーマル"
SETTINGS = {"enabled": False, "volume": 0.8}  # off until the user turns it on
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
QUEUE_MAX = 8           # older lines are dropped rather than read minutes late
DEDUPE_SECS = 3         # the same line from two windows within this time is spoken once
START_WAIT = 90         # seconds to wait for a freshly started engine

SAMPLE = "Es だよ。こんな声で読み上げるね。"
THREADED = True  # tests turn this off and call process_pending()

_cond = threading.Condition()
_queue = collections.deque()
_seen = collections.OrderedDict()  # key or text -> when it was queued
_gen = 0                           # stop() bumps this; older lines are dropped
_worker = None
_proc = None                       # the engine we started
_job = None                        # kills that engine with this process
_style = {}                        # (url, speaker, style) -> style id
_state = {"engine": "off", "error": "", "speaking": "", "last_spoken": 0.0}


# ---- settings ---------------------------------------------------------------------------

def settings():
    try:
        saved = json.loads(db.get_setting("voice") or "{}")
    except json.JSONDecodeError:
        saved = {}
    return {**SETTINGS, **{k: saved[k] for k in SETTINGS if k in saved}}


def update(cfg, enabled=None, volume=None):
    s = settings()
    if enabled is not None:
        s["enabled"] = bool(enabled)
    if volume is not None:
        s["volume"] = max(0.0, min(1.0, float(volume)))
    db.set_setting("voice", json.dumps(s))
    if enabled is False:
        stop()
        stop_engine()
        _state.update(engine="off", error="")
    elif enabled:
        _kick(cfg)  # start the engine now, so the first line is not late
    return status(cfg)


def credit(cfg):
    return f"VOICEVOX:{cfg.get('voicevox_speaker') or DEFAULT_SPEAKER}"


def status(cfg):
    with _cond:
        queued = len(_queue)
    s = settings()
    return {**s, "engine": _state["engine"],
            "error": _state["error"], "speaking": bool(_state["speaking"]), "queued": queued,
            "credit": credit(cfg), "started_by_es": _proc is not None and _proc.poll() is None}


# ---- what is read aloud -------------------------------------------------------------------

_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_URL = re.compile(r"https?://\S+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_LEAD = re.compile(r"^\s*(#{1,6}\s+|[-*・>]\s+|[✔＋→✖×※]\s*)", re.M)


def speakable(text, max_chars=200):
    """The words of a line as they should be read: no Markdown marks, code or long URLs.
    Long text is cut at a sentence end; nothing else about the meaning changes."""
    t = _CODE_BLOCK.sub("（コードは画面で）", str(text or ""))
    t = _MD_LINK.sub(r"\1", t)
    t = _URL.sub("リンク", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"(\*\*|__|~~)", "", t)
    t = _LEAD.sub("", t)
    t = re.sub(r"\s*\|\s*", "、", t)          # table cells
    t = re.sub(r"^[、\s]+|[、\s]+$", "", t, flags=re.M)
    t = re.sub(r"^[-=:、 ]{3,}$", "", t, flags=re.M)  # table rules
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    t = "".join(l if re.search(r"[。．！？!?、」』）)]$", l) else l + "。" for l in lines)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_chars:
        cut = t[:max_chars]
        end = max(cut.rfind(c) for c in "。！？!?")
        t = (cut[:end + 1] if end >= max_chars // 3 else cut + "…") + "続きは画面で見てね。"
    return t


def say(cfg, text, key=None, force=False):
    """Queue a line. Returns False when reading aloud is off or the line was already queued.
    `key` names the thing being said (an event, a reply) so two windows queue it once."""
    if not (force or settings()["enabled"]):
        return False
    words = speakable(text, int(cfg.get("voice_max_chars", 200)))
    if not words:
        return False
    now = time.time()
    with _cond:
        for k, at in list(_seen.items()):
            if now - at > 600:
                del _seen[k]
        marks = [m for m in (key and f"key:{key}", f"text:{words}") if m]
        if (key and f"key:{key}" in _seen) or now - _seen.get(f"text:{words}", 0) < DEDUPE_SECS:
            return False
        for m in marks:
            _seen[m] = now
        _queue.append({"text": words, "gen": _gen, "at": now})
        while len(_queue) > QUEUE_MAX:
            _queue.popleft()
        _cond.notify_all()
    _ensure_worker(cfg)
    return True


def reply(cfg, result, key=None):
    """Read a finished chat reply aloud (not one still waiting for the model)."""
    if isinstance(result, dict) and not result.get("pending") and result.get("reply"):
        return say(cfg, result["reply"], key=key)
    return False


def stop():
    """Stop the line being played and drop everything queued."""
    global _gen
    with _cond:
        _gen += 1
        _queue.clear()
        _cond.notify_all()
    _stop_sound()
    _state["speaking"] = ""
    return True


# ---- the worker ---------------------------------------------------------------------------

def _ensure_worker(cfg):
    global _worker
    if not THREADED:
        return
    with _cond:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, args=(cfg,), name="voice", daemon=True)
        _worker.start()


def _kick(cfg):
    if THREADED:
        threading.Thread(target=lambda: _safe(lambda: _engine(cfg)), name="voice-start", daemon=True).start()
    _ensure_worker(cfg)


def _safe(fn):
    try:
        return fn()
    except Exception as e:  # shown on the screen; the chat goes on without the voice
        _state["error"] = str(e)
        return None


def _loop(cfg):
    while True:
        with _cond:
            idle = float(cfg.get("voicevox_idle_minutes", 30)) * 60
            if not _queue:
                _cond.wait(timeout=60)
            item = _queue.popleft() if _queue else None
        if item:
            _process(cfg, item)
        elif _proc and time.time() - _state["last_spoken"] > idle:
            log.info("voice: stopping the idle VOICEVOX engine")
            stop_engine()


def process_pending(cfg):
    """Speak everything queued, in this thread (tests)."""
    while True:
        with _cond:
            item = _queue.popleft() if _queue else None
        if not item:
            return
        _process(cfg, item)


def _process(cfg, item):
    if item["gen"] != _gen:
        return
    try:
        url = _engine(cfg)
        sid = _style_id(cfg, url)
        query = _http(url, "/audio_query?" + urllib.parse.urlencode({"text": item["text"], "speaker": sid}),
                      data=b"", timeout=30)
        q = json.loads(query)
        q["volumeScale"] = settings()["volume"]
        wav = _http(url, f"/synthesis?speaker={sid}", data=json.dumps(q).encode("utf-8"), timeout=60)
        if item["gen"] != _gen:
            return  # stopped while synthesising
        _state.update(error="", speaking=item["text"])
        _play(wav, item["gen"])
        _state["last_spoken"] = time.time()
    except Exception as e:
        _state["error"] = _explain(e)
        log.warning("voice: %s", _state["error"])
    finally:
        _state["speaking"] = ""


def _explain(e):
    if isinstance(e, VoiceError):
        return str(e)
    if isinstance(e, TimeoutError) or "timed out" in str(e):
        return "VOICEVOX の音声合成が時間内に終わりませんでした。"
    if isinstance(e, (urllib.error.URLError, ConnectionError)):
        return "VOICEVOX に接続できませんでした。"
    return f"読み上げに失敗しました（{type(e).__name__}）。"


class VoiceError(Exception):
    pass


# ---- VOICEVOX engine ------------------------------------------------------------------------

def _http(url, path, data=None, timeout=5):
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _alive(url):
    try:
        _http(url, "/version", timeout=1.5)
        return True
    except Exception:
        return False


def _engine(cfg):
    """The engine's URL, starting the engine when nothing answers there."""
    global _proc
    url = cfg.get("voicevox_url") or DEFAULT_URL
    if _alive(url):
        _state["engine"] = "ready"
        return url
    path = cfg.get("voicevox_engine_path") or DEFAULT_ENGINE
    if not os.path.isfile(path):
        _state["engine"] = "missing"
        raise VoiceError(f"VOICEVOX のエンジンが見つかりません（{path}）。config.json の voicevox_engine_path を確認してください。")
    with _cond:
        if _proc is None or _proc.poll() is not None:
            u = urllib.parse.urlparse(url)
            log.info("voice: starting VOICEVOX engine %s", path)
            _proc = subprocess.Popen([path, "--host", u.hostname or "127.0.0.1", "--port", str(u.port or 50021),
                                      "--disable_mutable_api"],
                                     cwd=os.path.dirname(path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            _bind_to_us(_proc)
            _state["last_spoken"] = time.time()  # the idle clock starts now
    _state["engine"] = "starting"
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if _proc.poll() is not None:
            _state["engine"] = "error"
            raise VoiceError("VOICEVOX のエンジンが起動直後に終了しました。")
        if _alive(url):
            _state["engine"] = "ready"
            return url
        time.sleep(0.5)
    _state["engine"] = "error"
    raise VoiceError("VOICEVOX のエンジンが起動しませんでした。")


def _style_id(cfg, url):
    speaker = cfg.get("voicevox_speaker") or DEFAULT_SPEAKER
    style = cfg.get("voicevox_style") or DEFAULT_STYLE
    key = (url, speaker, style)
    if key not in _style:
        for sp in json.loads(_http(url, "/speakers", timeout=10)):
            if sp.get("name") == speaker:
                for st in sp.get("styles", []):
                    if st.get("name") == style:
                        _style[key] = st["id"]
        if key not in _style:
            raise VoiceError(f"VOICEVOX に「{speaker}（{style}）」の声が見つかりません。")
    return _style[key]


def stop_engine():
    """Stop the engine Es started (never one the user started)."""
    global _proc
    with _cond:
        proc, _proc = _proc, None
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if proc:
        _state["engine"] = "off"


def _bind_to_us(proc):
    """Put the engine in a job that closes with this process, so it never outlives the server."""
    global _job
    try:
        k32 = ctypes.windll.kernel32
        if _job is None:
            class BASIC(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", ctypes.c_uint32),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", ctypes.c_uint32),
                            ("f", ctypes.c_size_t), ("g", ctypes.c_uint32), ("h", ctypes.c_uint32)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("basic", BASIC), ("io", ctypes.c_uint64 * 6), ("p", ctypes.c_size_t * 4)]
            k32.CreateJobObjectW.restype = ctypes.c_void_p
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not k32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(info),
                                                          ctypes.sizeof(info)):
                raise OSError("job object")
            _job = job
        if not k32.AssignProcessToJobObject(ctypes.c_void_p(_job), ctypes.c_void_p(int(proc._handle))):
            raise OSError("assign")
    except Exception:
        log.warning("voice: could not tie the engine to the server; it is stopped on exit instead")


atexit.register(stop_engine)


# ---- playback (Windows) ---------------------------------------------------------------------

def _wav_seconds(wav):
    try:
        rate = int.from_bytes(wav[28:32], "little")  # bytes per second
        return max(0.1, (len(wav) - 44) / rate)
    except (ZeroDivisionError, ValueError):
        return 1.0


def _play(wav, gen):
    import winsound
    fd, path = tempfile.mkstemp(prefix="es-voice-", suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        end = time.time() + _wav_seconds(wav) + 0.2
        with _cond:
            while gen == _gen and time.time() < end:
                _cond.wait(timeout=max(0.05, end - time.time()))
        if gen != _gen:
            _stop_sound()
    finally:
        for _ in range(10):  # the player may still hold the file for a moment
            try:
                os.remove(path)
                break
            except OSError:
                time.sleep(0.1)


def _stop_sound():
    try:
        import winsound
        winsound.PlaySound(None, 0)  # stops the sound playing asynchronously
    except Exception:
        pass

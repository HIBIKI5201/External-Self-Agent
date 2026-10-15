"""BGM in YouTube's own window: open it in a small Edge app window and play/pause it.

Playing YouTube's own site (rather than an embed) lets the user's Premium login apply
and works for videos that disallow embedding, such as Lofi Girl's live streams.
"""
import json
import os
import subprocess
import threading
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "media.ps1")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
# One PowerShell at a time: two cold WinRT loads in parallel can each take over 20 s.
_lock = threading.Lock()


YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be")


def youtube_url(url):
    """The URL if it is a YouTube page, else ValueError. Whatever passes is handed to the OS or to Edge,
    so a path, file: or another app's protocol must never get through (#234). Same hosts as the window's."""
    url = url.strip() if isinstance(url, str) else ""
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        u = port = None
    if not u or u.scheme not in ("https", "http") or (u.hostname or "") not in YOUTUBE_HOSTS \
            or u.username or u.password or port is not None or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("YouTube の URL だけ開けます。")
    return url


def open_window(cfg, url):
    url = youtube_url(url)
    edge = cfg.get("edge_path")
    if not (edge and os.path.exists(edge)):
        os.startfile(url)
        return
    subprocess.Popen([edge, f"--app={url}", "--window-size=560,360"], creationflags=NO_WINDOW)


def control(action, match="youtube"):
    """action: status | play | pause on media sessions whose app id matches `match`
    (Edge app windows opened on YouTube are "MSEdge.www.youtube.com_/" or similar).
    Returns those sessions after the action."""
    with _lock:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Action", action, "-Match", match],
            capture_output=True, text=True, encoding="utf-8", timeout=45, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:300])
    out = proc.stdout.strip()
    return json.loads(out) if out else []

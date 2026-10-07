"""Opens the EsAgent chat window, starting the server first if it is not running."""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db  # noqa: E402
import winfocus  # noqa: E402

CFG = db.load_config()
URL = f"http://127.0.0.1:{CFG['port']}/"


def alive():
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if not alive():
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")],
                         cwd=db.ROOT, creationflags=flags, close_fds=True)
        for _ in range(40):
            time.sleep(0.25)
            if alive():
                break
    if winfocus.focus_chat_window():
        return
    edge = CFG.get("edge_path")
    if edge and os.path.exists(edge):
        subprocess.Popen([edge, f"--app={URL}", "--window-size=1180,780"])
    else:
        webbrowser.open(URL)


if __name__ == "__main__":
    main()

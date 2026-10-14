"""Es on the desktop: the black cat, always on top in a transparent window.

Sprites: Pet Cats Pack by LuizMelo (CC0), static/cat/pack. It follows the server's focus timer
and event feed, talks in a small bubble, opens the chat on click and has a right-click menu.
Started by server.py; only one instance runs.
"""
import ctypes
import json
import os
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db  # noqa: E402
import winfocus  # noqa: E402

CFG = db.load_config()
BASE = f"http://127.0.0.1:{CFG['port']}"
SPRITES = os.path.join(HERE, "static", "cat", "pack")
STATE_FILE = os.path.join(db.DATA_DIR, "pet.json")

CAT = CFG.get("pet_cat", 2)              # 2 = black cat
SCALE = CFG.get("pet_scale", 3)          # cat is ~25px wide in a frame -> ~75px on screen
CROP = (8, 13, 40, 34)                   # the cat spans x11-36, y16-32 of each 50x50 frame
KEY = "#ff00fe"                          # this colour is made transparent
FX_H = 20                                # room above the cat for "z", "…", "♪"

ANIMS = {  # frames per strip, ms per frame
    "Idle": (10, 120), "Sleeping1": (1, 1000), "Sleeping2": (1, 1000), "Laying": (8, 150),
    "Stretching": (13, 120), "Meow": (4, 150), "Sitting": (1, 1000), "Licking 1": (5, 180), "Itch": (2, 170),
}
MOOD_ANIM = {"idle": "Idle", "focus": "Sleeping1", "break": "Laying", "stretch": "Stretching",
             "thinking": "Sitting", "happy": "Meow", "talk": "Meow", "worried": "Itch", "sleep": "Sleeping2",
             "lick": "Licking 1", "itch": "Itch", "sit": "Sitting"}
MOOD_FX = {"focus": "z", "sleep": "💤", "thinking": "…", "happy": "♪", "talk": "♪", "worried": "!?"}
FIDGETS = ("lick", "itch", "sit", "lick")


def api(path, body=None, timeout=1.5):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "X-EsAgent": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def work_area():
    """(left, top, right, bottom) of the desktop without the taskbar."""
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0)
    return rc.l, rc.t, rc.r, rc.b


def start_label(pomo):
    """The menu item that starts a focus session, with its length (#248): while idle, the server's
    remaining time is the configured focus_minutes."""
    secs = (pomo or {}).get("remaining") or 25 * 60
    return f"集中を始める（{max(1, round(secs / 60))}分）"


class Pet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.config(bg=KEY)
        self.w = (CROP[2] - CROP[0]) * SCALE
        self.h = (CROP[3] - CROP[1]) * SCALE + FX_H
        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h, bg=KEY, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.img = self.canvas.create_image(0, FX_H, anchor="nw")
        self.fx = self.canvas.create_text(self.w - 14, FX_H // 2 + 2, text="", fill="#9b83ff",
                                          font=("Segoe UI", 11, "bold"))
        self.cache, self.anim, self.frame = {}, "", 0
        self.mood, self.override, self.override_until = "idle", None, 0
        self.pomo, self.last_event, self.online, self.warned_day = {}, None, True, ""
        self.bubble = None
        self.voice = {}
        self.drag = None
        self._hidden = winfocus.chat_window_visible()
        self._place()
        self._bind()
        if not self._hidden:
            self.root.deiconify()
        self.root.after(500, self._visibility_tick)
        self.root.after(50, self._animate)
        self.root.after(200, self._poll)
        self.root.after(12000, self._fidget)
        self.root.after(1500, lambda: self.say(self._greeting(), kind="notice", source="clock"))

    # ---- placement ------------------------------------------------------------------
    def _visibility_tick(self):
        hidden = winfocus.chat_window_visible()
        if hidden != self._hidden:
            self._hidden = hidden
            if hidden:
                self._close_bubble()
                self.root.withdraw()
            else:
                self.root.deiconify()
        self.root.after(500, self._visibility_tick)

    def _place(self):
        try:
            pos = json.load(open(STATE_FILE, encoding="utf-8"))
            x, y = int(pos["x"]), int(pos["y"])
        except (OSError, ValueError, KeyError):
            l, t, r, b = work_area()
            x, y = r - self.w - 60, b - self.h
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _save_pos(self):
        os.makedirs(db.DATA_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"x": self.root.winfo_x(), "y": self.root.winfo_y()}, f)

    # ---- sprites --------------------------------------------------------------------
    def _restyle(self, cat, scale):
        """Switch the cat or its size (admin page), keeping the bottom-right corner in place."""
        global CAT, SCALE
        right, bottom = self.root.winfo_x() + self.w, self.root.winfo_y() + self.h
        CAT, SCALE = cat, scale
        self.cache, self.anim = {}, ""
        self.w = (CROP[2] - CROP[0]) * SCALE
        self.h = (CROP[3] - CROP[1]) * SCALE + FX_H
        self.canvas.config(width=self.w, height=self.h)
        self.canvas.coords(self.fx, self.w - 14, FX_H // 2 + 2)
        self.root.geometry(f"{self.w}x{self.h}+{right - self.w}+{bottom - self.h}")
        self._save_pos()

    def _frames(self, name):
        if name not in self.cache:
            strip = tk.PhotoImage(file=os.path.join(SPRITES, f"Cat-{CAT}", f"Cat-{CAT}-{name}.png"))
            frames = []
            for i in range(strip.width() // 50):
                f = tk.PhotoImage()
                f.tk.call(f, "copy", strip, "-from", i * 50 + CROP[0], CROP[1], i * 50 + CROP[2], CROP[3],
                          "-zoom", SCALE, SCALE)
                frames.append(f)
            self.cache[name] = frames
        return self.cache[name]

    def _current(self):
        if self.override and time.time() < self.override_until:
            if not (self.override in FIDGETS and self.mood != "idle"):
                return self.override
        return self.mood

    def _animate(self):
        mood = self._current()
        name = MOOD_ANIM.get(mood, "Idle")
        if name != self.anim:
            self.anim, self.frame = name, 0
        frames = self._frames(name)
        self.canvas.itemconfigure(self.img, image=frames[self.frame % len(frames)])
        self.canvas.itemconfigure(self.fx, text=MOOD_FX.get(mood, ""))
        self.frame += 1
        self.root.after(ANIMS[name][1], self._animate)

    def flash(self, mood, secs=3.0):
        self.override, self.override_until = mood, time.time() + secs

    def _fidget(self):
        if self.mood == "idle" and not (self.override and time.time() < self.override_until):
            self.flash(random.choice(FIDGETS), 2.4)
        self.root.after(random.randint(9000, 18000), self._fidget)

    # ---- speech bubble ------------------------------------------------------------------
    def say(self, text, buttons=None, secs=None, voice=True, key=None, kind="feedback", source="pet", ukey=None):
        if self._hidden:
            return  # the chat window owns companion speech while the desktop cat is hidden
        # Everything the cat says goes to the one utterance log (#139); an event's line carries its key
        # so the server, other windows and the cat record it only once.
        body = {"text": text, "kind": kind, "source": source, **({"key": ukey} if ukey else {})}
        threading.Thread(target=lambda: self._quiet(lambda: api("/api/es/said", body)), daemon=True).start()
        if voice and self.voice.get("enabled"):
            self._speak(text, key)
        self._close_bubble()
        b = tk.Toplevel(self.root)
        b.overrideredirect(True)
        b.attributes("-topmost", True)
        frame = tk.Frame(b, bg="#fffdf8", highlightbackground="#d9d2c3", highlightthickness=1)
        frame.pack()
        tk.Label(frame, text=text, bg="#fffdf8", fg="#2a2433", font=("Yu Gothic UI", 10), wraplength=230,
                 justify="left", padx=10, pady=6).pack(anchor="w")
        if buttons:
            row = tk.Frame(frame, bg="#fffdf8")
            row.pack(anchor="w", padx=8, pady=(0, 6))
            for label, fn in buttons:
                tk.Button(row, text=label, font=("Yu Gothic UI", 9), relief="flat", bg="#ece8ff", fg="#4a3a8a",
                          activebackground="#ddd5ff", padx=8,
                          command=lambda fn=fn: (self._close_bubble(), fn and fn())).pack(side="left", padx=2)
        b.update_idletasks()
        bw, bh = b.winfo_width(), b.winfo_height()
        l, t, r, _ = work_area()
        x = min(max(l, self.root.winfo_x() + self.w // 2 - bw // 2), r - bw)
        y = max(t, self.root.winfo_y() - bh - 2)
        b.geometry(f"+{x}+{y}")
        b.bind("<Button-1>", lambda e: self._close_bubble())
        self.bubble = b
        if not buttons:
            self.root.after(int((secs or max(4, len(text) * 0.16)) * 1000), lambda bb=b: self._close_bubble(bb))

    def _speak(self, text, key=None):
        """Hand the line to the server's single voice player (#44); the cat never plays audio itself."""
        body = {"text": text, **({"key": key} if key else {})}
        threading.Thread(target=lambda: self._quiet(lambda: api("/api/voice/say", body)), daemon=True).start()

    @staticmethod
    def _quiet(fn):
        try:
            fn()
        except Exception:
            pass

    def _close_bubble(self, only=None):
        if self.bubble and (only is None or only is self.bubble):
            self.bubble.destroy()
            self.bubble = None

    # ---- server polling ----------------------------------------------------------------
    def _poll(self):
        try:
            st = api("/api/pet")
            if not st.get("enabled", True):  # switched off in the chat window
                self.root.destroy()
                return
            if (st.get("cat", CAT), st.get("scale", SCALE)) != (CAT, SCALE):
                self._restyle(st.get("cat", CAT), st.get("scale", SCALE))
            self.online = True
            self.pomo = st["pomo"]
            self.voice = st.get("voice") or {}
            self.mood = st["mood"]
            if self.last_event is None:
                self.last_event = st["last_event"]  # don't replay what happened before we started
            for ev in api(f"/api/events?after={self.last_event}")["events"]:
                self.last_event = ev["id"]
                self._on_event(ev)
            today = time.strftime("%Y-%m-%d")
            if st["overdue"] and self.warned_day != today and self.mood == "idle":
                self.warned_day = today
                self.flash("worried", 6)
                self.say(f"「{st['overdue']}」の締切、過ぎちゃってるよ…", kind="notice", source="task")
        except Exception:
            self.online, self.mood = False, "sleep"
        self.root.after(1000, self._poll)

    def _on_event(self, ev):
        kind = ev["kind"]
        if kind == "focus_start":
            task = ev.get("task")
            self.say(f"「{task}」、いこう。となりで丸くなってるね。" if task else "集中タイム。となりで丸くなってるね。", secs=3.5,
                     kind="notice", source="timer", ukey=ev.get("key"),
                     voice=False)  # timer notices are read aloud by the server
        elif kind == "focus_end":
            self.flash("stretch", 1.7)
            if ev.get("leveled_up"):
                self.say(f"なかよし度が上がった！ Lv{ev['level']}「{ev['level_name']}」", secs=7, voice=False,
                         kind="notice", source="timer", ukey=ev.get("key"))
            elif ev.get("task_id"):
                tid = ev["task_id"]
                self.say(f"おつかれさま！{'長めに休もう。' if ev.get('long') else 'ひと休みしよ。'}\n「{ev['task']}」、終わった？",
                         buttons=[("終わった", lambda: self._done(tid)), ("まだ", lambda: self.say("了解。次の集中で進めよ。"))],
                         kind="question", source="timer", ukey=ev.get("key"),
                         voice=False)
            else:
                self.say("おつかれさま！ちょっと休憩しよ。", voice=False, kind="notice", source="timer", ukey=ev.get("key"))
        elif kind == "break_end":
            self.say("休憩おわり。次、いく？", buttons=[("集中する", self._start), ("あとで", None)], voice=False,
                     kind="question", source="timer", ukey=ev.get("key"))
        elif kind == "chat_events":
            lines, key = ev.get("lines") or [], f"event:{ev['id']}"
            # The server chose and recorded the line (ev["text"]); the cat shows that same line.
            line, opts = ev.get("text") or "", {"key": key, "kind": "murmur", "source": "task", "ukey": ev.get("key")}
            if any(l.startswith("✔") for l in lines):
                self.flash("happy"); self.say(line or random.choice(["やったね！", "えらい！", "ひとつ片づいたね。"]), secs=3, **opts)
            elif any(l.startswith("→") for l in lines):
                self.flash("happy"); self.say(line or "Claude に頼んだよ。", secs=3, **opts)
            elif any(l.startswith("＋") for l in lines):
                self.flash("talk", 2); self.say(line or "メモしたよ。", secs=2.5, **opts)

    # ---- actions -------------------------------------------------------------------------
    def _safe(self, fn):
        try:
            return fn()
        except Exception:
            self.say("E.S. Agent のサーバーに届かなかった…", secs=3, voice=False)

    def _done(self, task_id):
        try:
            api(f"/api/tasks/{task_id}", {"status": "done"})
        except urllib.error.HTTPError as e:  # e.g. a read-only task (#182): say why instead of cheering
            try:
                reason = json.load(e)["error"]
            except Exception:
                reason = "完了にできなかった…"
            self.say(reason, secs=6, voice=False)
            return
        except Exception:
            self.say("E.S. Agent のサーバーに届かなかった…", secs=3, voice=False)
            return
        self.flash("happy"); self.say("やったね！えらい。", secs=3)

    def _start(self):
        self._safe(lambda: api("/api/pomo", {"action": "start"}))

    def _open_chat(self):
        """Bring the chat window forward if it is open; otherwise open it."""
        if winfocus.focus_chat_window():
            return
        subprocess.Popen([sys.executable, os.path.join(HERE, "launch.py")], cwd=db.ROOT)

    def _bgm_toggle(self):
        # Media control can take several seconds; keep the cat responsive.
        def run():
            try:
                sessions = api("/api/media", {"action": "status"}, timeout=50)["sessions"]
                if not sessions:
                    msg = "YouTube のウィンドウが見つからないよ。チャットの BGM 欄から開いてね。"
                else:
                    playing = any(s["status"] == "Playing" for s in sessions)
                    api("/api/media", {"action": "pause" if playing else "play"}, timeout=50)
                    msg = "BGM、止めたよ。" if playing else "BGM、流すね。"
            except Exception:
                msg = "BGM を操作できなかった…"
            self.root.after(0, lambda: self.say(msg, secs=3))
        threading.Thread(target=run, daemon=True).start()

    def _disable(self):
        """Same as turning the switch off in the chat window."""
        try:
            api("/api/pet/enabled", {"enabled": False})
        except Exception:
            pass
        self.root.destroy()

    def _hide(self, minutes=30):
        self._close_bubble()
        self.root.withdraw()
        self.root.after(minutes * 60 * 1000, self.root.deiconify)

    def _menu(self, event):
        m = tk.Menu(self.root, tearoff=0, font=("Yu Gothic UI", 10))
        mode = self.pomo.get("mode", "idle")
        if mode == "idle":
            m.add_command(label=start_label(self.pomo), command=self._start)
        else:
            m.add_command(label="再開" if self.pomo.get("paused") else "一時停止",
                          command=lambda: self._safe(lambda: api("/api/pomo", {"action": "toggle"})))
            m.add_command(label="タイマーをリセット", command=lambda: self._safe(lambda: api("/api/pomo", {"action": "reset"})))
        m.add_separator()
        m.add_command(label="チャットを開く（ダブルクリックでも）", command=self._open_chat)
        m.add_command(label="BGM 再生 / 一時停止（YouTube ウィンドウ）", command=self._bgm_toggle)
        if self.voice.get("enabled"):
            m.add_command(label="読み上げを止める", command=lambda: self._safe(lambda: api("/api/voice/stop", {})))
        m.add_separator()
        m.add_command(label="30分かくれる", command=self._hide)
        m.add_command(label="デスクトップから消す（チャット画面の切り替えで戻せます）", command=self._disable)
        m.tk_popup(event.x_root, event.y_root)

    # ---- mouse -----------------------------------------------------------------------------
    def _bind(self):
        c = self.canvas
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Double-Button-1>", self._double)
        c.bind("<Button-3>", self._menu)
        c.bind("<Enter>", self._hover)

    def _press(self, e):
        self.drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), False)

    def _move(self, e):
        if not self.drag:
            return
        x0, y0, wx, wy, _ = self.drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 3:
            self.drag = (x0, y0, wx, wy, True)
            self._close_bubble()
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def _release(self, e):
        moved = self.drag and self.drag[4]
        self.drag = None
        if moved:
            self._save_pos()
        else:  # a single click is just a hello; double-click opens the chat
            self.flash("talk", 1.8)
            self.say(random.choice(["にゃ？", "なになに？", "にゃー"]), secs=1.5, kind="murmur")

    def _double(self, e):
        self.drag = None
        self._close_bubble()
        self._open_chat()

    def _hover(self, e):
        if self.bubble or not self.pomo or self.pomo.get("mode") == "idle":
            return
        mins = int(self.pomo.get("remaining", 0) // 60) + 1
        what = "集中" if self.pomo["mode"] == "focus" else "休憩"
        task = f"（{self.pomo['task']}）" if self.pomo.get("task") else ""
        self.say(f"{what} あと{mins}分{task}", secs=2.5, voice=False)

    @staticmethod
    def _greeting():
        h = time.localtime().tm_hour
        if 5 <= h < 11:
            return "おはよ。今日もとなりにいるね。"
        if 11 <= h < 17:
            return "ここにいるよ。何かあったら呼んでね。"
        if 17 <= h < 23:
            return "おつかれさま。あと少し、一緒にやる？"
        return "夜ふかし？ 無理しないでね。"

    def run(self):
        self.root.mainloop()


def main():
    # One cat only.
    ctypes.windll.kernel32.CreateMutexW(None, False, "EsAgentDesktopCat")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    Pet().run()


if __name__ == "__main__":
    main()

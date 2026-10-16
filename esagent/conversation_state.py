"""The one conversation running now: its lock and its progress (#237).

Es answers one message at a time. While she does, the chat window shows what she is doing
(`snapshot()`), and a question about the wait is answered from the same record (status_question).
brain, skills and actions use only the functions here; nobody reads or writes the record directly.
"""
import contextlib
import threading
import time

import conversation_jobs as jobs
import db


class ConversationState:
    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = {}  # the server's config, for asking whether the model is reachable
        self._progress = {"stage": None, "since": None, "detail": "", "partial": "", "text": "", "message_id": None}

    def configure(self, cfg):
        self.cfg = cfg

    def stage(self, name):
        """Name the current step; None ends the run and clears what was shown."""
        p = self._progress
        token = jobs.CURRENT.get()
        p["requestId"] = token["request_id"] if token and name else None
        p["revision"] = token["revision"] if token and name else None
        p["stage"] = name
        if name is None:
            p.update(since=None, detail="", partial="")
        elif p["since"] is None:
            p["since"] = time.time()

    @contextlib.contextmanager
    def running(self, name):
        """Hold the conversation lock for one run that shows `name` while it lasts."""
        with self.lock:
            self.stage(name)
            try:
                yield
            finally:
                self.stage(None)

    def set_detail(self, text):
        self._progress["detail"] = text

    def set_partial(self, text):
        self._progress["partial"] = text

    def partial(self):
        return self._progress["partial"]

    def begin_message(self, text, message_id):
        self._progress.update(text=text, message_id=message_id)

    def end_message(self):
        self._progress.update(text="", message_id=None)

    def set_message_id(self, message_id):
        self._progress["message_id"] = message_id

    def message_id(self):
        return self._progress.get("message_id")

    def running_since(self):
        """(text, stage, seconds) of the run in progress, or None when idle."""
        p = self._progress
        if p["since"] is None:
            return None
        return p.get("text") or "", p["stage"], time.time() - p["since"]

    def snapshot(self):
        """What Es is doing right now, for the UI's live thinking display."""
        p = self._progress
        since = p["since"]
        current = jobs.valid({"key": (db.DB_PATH, p.get("requestId")), "revision": p.get("revision")})
        return {"busy": since is not None, "stage": p["stage"], "detail": p["detail"],
                "partial": p["partial"] if current or not p.get("requestId") else "",
                "requestId": p.get("requestId"), "revision": p.get("revision"),
                "elapsed": round(time.time() - since, 1) if since else 0}


STATE = ConversationState()

lock = STATE.lock
configure = STATE.configure
stage = STATE.stage
running = STATE.running
set_detail = STATE.set_detail
set_partial = STATE.set_partial
partial = STATE.partial
begin_message = STATE.begin_message
end_message = STATE.end_message
set_message_id = STATE.set_message_id
message_id = STATE.message_id
running_since = STATE.running_since
snapshot = STATE.snapshot

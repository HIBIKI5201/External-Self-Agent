"""Test sandbox for E.S. Agent.

- Works on a *copy* of the real database (or an empty one when there is none, e.g. in CI),
  never on data/esagent.db itself, because that one syncs with Google.
- Google APIs can be replaced with in-memory fakes; any real Google HTTP call raises.
- Every request sent to Ollama is recorded so tests can check what the model actually saw.
"""
import itertools
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "esagent"))

import db  # noqa: E402

REAL_DB = db.DB_PATH


def ollama_ready(cfg=None):
    cfg = cfg or db.load_config()
    try:
        with urllib.request.urlopen(cfg["ollama_url"].rstrip("/") + "/api/tags", timeout=2) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return False
    return any(n == cfg["model"] or n.split(":")[0] == cfg["model"] for n in names)


class Sandbox:
    """with Sandbox() as sb: sb.chat("..."), sb.skill("/tasks"), sb.llm_calls ..."""

    def __init__(self, copy_real=True, mode="work"):
        self.copy_real, self.mode = copy_real, mode
        self.llm_calls = []

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="esagent-test-")
        path = os.path.join(self.dir, "esagent.db")
        if self.copy_real and os.path.exists(REAL_DB):
            src, dst = sqlite3.connect(REAL_DB), sqlite3.connect(path)
            src.backup(dst)
            src.close()
            dst.close()
        self._saved = (db.DB_PATH, db.DATA_DIR)
        db.DB_PATH, db.DATA_DIR = path, self.dir
        db.init()
        with db.connect() as con:  # start every test from a clean conversation
            con.execute("DELETE FROM messages")
            con.execute("DELETE FROM memories")
            con.execute("DELETE FROM settings WHERE key LIKE 'pending%' OR key LIKE 'snooze:%' "
                        "OR key IN ('last_advice', 'chat_mode')")
            con.execute("INSERT INTO settings(key, value) VALUES('chat_mode', ?)", (self.mode,))
        self.cfg = db.load_config()
        db.sync_projects(self.cfg)

        import gapi
        import gsync
        import handoff
        import llm
        handoff.HANDOFF_DIR = os.path.join(self.dir, "handoffs")
        self._patches = [(gsync, "run_once", gsync.run_once), (gapi, "request", gapi.request),
                         (llm, "_post", llm._post), (gsync, "_req", gsync._req), (gsync, "_paged", gsync._paged)]
        self.sync_once = gsync.run_once      # the real sync, for tests that use FakeTasks
        gsync.run_once = lambda: "online"    # chat code must not sync during tests

        def no_real_google(*a, **k):
            raise AssertionError("a test tried to call the real Google API")
        gapi.request = no_real_google

        orig_post = llm._post

        def record(cfg, body, timeout, on_text=None):
            out = orig_post(cfg, body, timeout, on_text)
            self.llm_calls.append({"stage": "router" if "format" in body else "reply",
                                   "messages": body["messages"], "out": out})
            return out
        llm._post = record
        return self

    def __exit__(self, *exc):
        for mod, name, value in self._patches:
            setattr(mod, name, value)
        db.DB_PATH, db.DATA_DIR = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def chat(self, text):
        import brain
        return brain.chat(text, self.cfg)

    def skill(self, text):
        import skills
        return skills.run(text, self.cfg)

    def sent_contains(self, needle, stage=None):
        """Did `needle` appear in what was sent to the model (optionally for one stage)?"""
        return any(needle in m["content"] for c in self.llm_calls
                   if stage in (None, c["stage"]) for m in c["messages"])


class FakeTasks:
    """In-memory Google Tasks API, wired in place of gsync._req / gsync._paged."""

    def __init__(self):
        self.n = itertools.count(1)
        self.lists = {"L0": {"id": "L0", "title": "マイタスク"}}
        self.tasks = {"L0": {}}
        self.offline = False

    def stamp(self):
        import datetime as dt
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def req(self, method, path, params=None, body=None):
        if self.offline:
            import gauth
            raise gauth.OfflineError("fake network down")
        p = path.strip("/").split("/")
        if path == "/users/@me/lists" and method == "GET":
            return {"items": list(self.lists.values())}
        if path == "/users/@me/lists/@default":
            return self.lists["L0"]
        if path == "/users/@me/lists" and method == "POST":
            i = f"L{next(self.n)}"
            self.lists[i] = {"id": i, "title": body["title"]}
            self.tasks[i] = {}
            return self.lists[i]
        lid = p[1]
        if len(p) == 3 and method == "GET":
            return {"items": list(self.tasks[lid].values())}
        if len(p) == 3 and method == "POST":
            i = f"T{next(self.n)}"
            t = {"id": i, **{k: v for k, v in body.items() if v is not None}, "updated": self.stamp()}
            self.tasks[lid][i] = t
            return t
        tid = p[3]
        if len(p) == 5 and p[4] == "move":
            t = self.tasks[lid].pop(tid)
            self.tasks[params["destinationTasklist"]][tid] = t
            return t
        if method == "PATCH":
            t = self.tasks[lid][tid]
            for k, v in body.items():
                if v is None:
                    t.pop(k, None)
                else:
                    t[k] = v
            t["updated"] = self.stamp()
            return t
        if method == "DELETE":
            self.tasks[lid].pop(tid)
            return {}
        raise AssertionError(f"unhandled {method} {path}")

    def install(self):
        import gsync
        gsync._req = self.req
        gsync._paged = lambda path, params: iter(self.req("GET", path, params)["items"])
        return self


class FakeCalendar:
    """In-memory Google Calendar API, wired in place of gcal._req / gcal._paged."""

    def __init__(self):
        self.n = itertools.count(1)
        self.cals = {}

    def req(self, method, path, params=None, body=None):
        import gapi
        p = [urllib.parse.unquote(x) for x in path.strip("/").split("/")]
        if path == "/calendars" and method == "POST":
            cid = f"c{next(self.n)}@group.calendar.google.com"
            self.cals[cid] = {}
            return {"id": cid, **body}
        cid = p[1]
        if cid not in self.cals:
            raise gapi.SyncError("404", code=404)
        evs = self.cals[cid]
        if len(p) == 2:
            return {"id": cid}
        if len(p) == 3 and method == "GET":
            return {"items": list(evs.values())}
        if len(p) == 3 and method == "POST":
            i = f"e{next(self.n)}"
            evs[i] = {"id": i, **body}
            return evs[i]
        eid = p[3]
        if method == "PUT":
            evs[eid] = {"id": eid, **body}
            return evs[eid]
        if method == "DELETE":
            if eid not in evs:
                raise gapi.SyncError("410", code=410)
            del evs[eid]
            return {}
        raise AssertionError(f"unhandled {method} {path}")

    def install(self):
        import gcal
        gcal._req = self.req
        gcal._paged = lambda path, params: iter(self.req("GET", path)["items"])
        return self

    def events(self):
        cid = db.get_setting("gcal_calendar_id")
        return sorted((e["start"]["date"], e["summary"]) for e in self.cals.get(cid, {}).values())

"""Chat delivery loss must not repeat committed operations."""
import io
import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from support import Sandbox
import chat_requests
import db

ID = "a" * 32


def test_replay_after_delivery_loss_and_restart():
    with Sandbox(copy_real=False):
        import server
        handler = object.__new__(server.Handler)
        handler.path = "/api/chat"
        sent_codes = []
        handler.send_response = sent_codes.append
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None

        class Broken:
            def write(self, data):
                raise ConnectionAbortedError(10053, "test disconnect")

        handler.wfile = Broken()
        calls = []

        def route(path, body):
            calls.append(body["text"])
            db.add_message("user", body["text"])
            db.add_task("synthetic task")
            return {"reply": "saved", "events": []}

        def post():
            handler.headers = {"Host": "127.0.0.1", "X-EsAgent": "1"}
            import json
            raw = json.dumps({"requestId": ID, "text": "synthetic message"}).encode()
            handler.headers["Content-Length"] = str(len(raw))
            handler.rfile = io.BytesIO(raw)
            handler.do_POST()

        with patch.object(handler, "_route", route), patch.object(server.gsync, "request"), \
                patch.object(server.log, "exception") as errors:
            post()
            assert handler.close_connection
            assert not errors.called, "delivery loss is not a processing failure"
            # No in-memory result survives a process restart; replay reads the DB.
            importlib.reload(chat_requests)
            handler.wfile = io.BytesIO()
            post()
            assert b"saved" in handler.wfile.getvalue()
        assert calls == ["synthetic message"]
        assert sent_codes == [200, 200], "never resend a 500 to the broken connection"
        assert len(db.recent_messages(10)) == 1
        assert len(db.list_tasks()) == 1


def test_concurrent_duplicate_only_executes_once():
    with Sandbox(copy_real=False):
        started, release = threading.Event(), threading.Event()
        calls = []

        def operation():
            calls.append(1)
            started.set()
            assert release.wait(5)
            return 200, {"reply": "done"}

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(chat_requests.execute, ID, "same", operation)
            assert started.wait(5)
            assert chat_requests.execute("c" * 32, "status", lambda: (200, {"reply": "status"}))[0] == 200
            second = pool.submit(chat_requests.execute, ID, "same", operation)
            release.set()
            assert first.result(5) == second.result(5) == (200, {"reply": "done"})
        assert calls == [1]


def test_unknown_interrupted_result_never_reexecutes():
    with Sandbox(copy_real=False):
        def crash():
            db.add_task("already committed")
            raise RuntimeError("simulated process interruption")

        try:
            chat_requests.execute(ID, "same", crash)
        except RuntimeError:
            pass
        else:
            assert False
        result = chat_requests.execute(ID, "same", lambda: (_ for _ in ()).throw(AssertionError("repeated")))
        assert result[0] == 409
        assert len(db.list_tasks()) == 1


def test_mismatch_invalid_ids_and_independent_requests():
    with Sandbox(copy_real=False):
        calls = []

        def op():
            calls.append(1)
            return 200, {"reply": "done"}

        assert chat_requests.execute(None, "one", op)[0] == 400
        assert chat_requests.execute("bad", "one", op)[0] == 400
        assert chat_requests.execute(ID, "one", op)[0] == 200
        assert chat_requests.execute(ID, "two", op)[0] == 409
        assert chat_requests.execute("b" * 32, "one", op)[0] == 200
        assert len(calls) == 2


def test_pending_and_failure_results_are_replayed():
    with Sandbox(copy_real=False):
        for i, expected in enumerate([(200, {"pending": True}), (400, {"error": "bad input"}),
                                      (500, {"error": "unknown result"})]):
            rid = str(i) * 32
            assert chat_requests.execute(rid, "same", lambda: expected) == expected
            assert chat_requests.execute(rid, "same", lambda: (_ for _ in ()).throw(AssertionError("repeat"))) == expected

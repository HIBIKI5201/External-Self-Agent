"""Ratings and rewrites of Es's replies are kept locally and exported for tuning (#144, AS-15)."""
import json
import os
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import actions
import db
import feedback
import llm
from support import Sandbox

ROUTE = json.dumps({"intent": "chat", "focus": "", "actions": []}, ensure_ascii=False)


def talk(sb, text, reply):
    with patch.object(llm, "chat", side_effect=lambda cfg, m, schema=None, **k: ROUTE if schema else reply):
        sb.chat(text)
    return db.recent_messages(1, roles=("assistant",))[-1]


def test_rating_rewrite_list_delete_and_export():
    with Sandbox(copy_real=False) as sb:
        chat = talk(sb, "今日はカレーを作ったよ", "いいね！カレーは甘口派？")
        with patch.object(llm, "chat", side_effect=AssertionError("no model")):
            sb.chat("記憶の仕組みはどうなってるの？")  # a fixed reply built by code
        template = db.recent_messages(1, roles=("assistant",))[-1]
        assert db.recent_messages(5, roles=("assistant",))[-2]["intent"] == "chat"

        row = feedback.rate(chat["id"], rating=-1, cfg=sb.cfg)
        assert row["kind"] == "chat" and row["rating"] == -1 and row["model"] == sb.cfg["model"]
        assert json.loads(row["context"])[-1] == {"role": "user", "content": "今日はカレーを作ったよ"}
        feedback.rate(chat["id"], rewrite="カレーいいね！スパイスから作ったの？", cfg=sb.cfg)
        feedback.rate(template["id"], rating=1, cfg=sb.cfg)
        assert feedback.rate(1, rating=1) is None or db.recent_messages(1)[0]["role"] != "user"
        assert feedback.ratings([chat["id"], template["id"]]) == {
            chat["id"]: {"rating": -1, "rewritten": True}, template["id"]: {"rating": 1, "rewritten": False}}

        out = feedback.export()
        sft = [json.loads(l) for l in open(out["sft"][0], encoding="utf-8")]
        dpo = [json.loads(l) for l in open(out["dpo"][0], encoding="utf-8")]
        assert out["sft"][0].startswith(os.path.join(db.DATA_DIR, "feedback"))
        assert [s["messages"][-1]["content"] for s in sft] == ["カレーいいね！スパイスから作ったの？"]
        assert dpo == [{**dpo[0], "chosen": "カレーいいね！スパイスから作ったの？", "rejected": "いいね！カレーは甘口派？"}]
        assert feedback.export(include_templates=True)["sft"][1] == 2  # the 👍 fixed reply too

        # The chat can be cleared; the ratings stay until deleted.
        db.clear_messages()
        listed = actions.run_action("feedback", "", sb.cfg)[0]
        assert "👎 ✎" in listed and "👍" in listed, listed
        first = feedback.listing()[-1]["id"]
        assert "消しました" in actions.run_action("feedback", f"delete {first}", sb.cfg)[0]
        assert len(feedback.listing()) == 1


def test_http_rates_only_replies():
    import server
    with Sandbox(copy_real=False) as sb, patch.object(server, "CFG", sb.cfg):
        reply = talk(sb, "おはよう", "おはよう！")
        user = db.recent_messages(5, roles=("user",))[-1]
        app = server.Server(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()

        def send(body):
            req = urllib.request.Request(f"http://127.0.0.1:{app.server_port}/api/feedback",
                                         data=json.dumps(body).encode(), headers={"X-EsAgent": "1"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        try:
            assert send({"messageId": reply["id"], "rating": 1})["rating"] == 1
            assert server.state()["feedback"][reply["id"]]["rating"] == 1
            try:
                send({"messageId": user["id"], "rating": 1})
                raise AssertionError("a user message was rated")
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            app.shutdown(); app.server_close(); thread.join(timeout=2)

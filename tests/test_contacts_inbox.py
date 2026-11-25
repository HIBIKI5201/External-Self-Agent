"""Google Contacts (read-only) and Inbox quick-memo tests (#321)."""
import datetime as dt
from unittest.mock import patch

import contacts_inbox
import gauth
from support import Sandbox


def test_contacts_sync_search_and_birthdays():
    def fake_req(method, path, params=None, body=None):
        assert method == "GET" and path == "/people/me/connections"
        return {"connections": [
            {
                "resourceName": "people/1",
                "names": [{"displayName": "山田 太郎"}],
                "emailAddresses": [{"value": "taro@example.com"}],
                "birthdays": [{"date": {"month": 11, "day": 28}}],
            },
            {
                "resourceName": "people/2",
                "names": [{"displayName": "佐藤 花子"}],
                "emailAddresses": [{"value": "hanako@example.com"}],
                "birthdays": [{"date": {"month": 5, "day": 1}}],
            },
        ]}

    with Sandbox(copy_real=False) as sb, \
            patch.object(gauth, "status", return_value={"connected": True, "contacts": True}), \
            patch.object(contacts_inbox, "_req", fake_req):
        n = contacts_inbox.sync_contacts()
        assert n == 2
        found = contacts_inbox.search_contacts("山田")
        assert len(found) == 1 and found[0]["email"] == "taro@example.com"
        bdays = contacts_inbox.upcoming_birthdays(today=dt.date(2026, 11, 26), days=7)
        assert [b["name"] for b in bdays] == ["山田 太郎"]
        out = sb.skill("/contacts 山田")
        assert "taro@example.com" in out["reply"]


def test_inbox_quick_memo_and_readonly_contacts_scope():
    assert gauth.CONTACTS_SCOPES == ["https://www.googleapis.com/auth/contacts.readonly"]
    with Sandbox(copy_real=False) as sb:
        r1 = sb.skill("/inbox 買いたい本: 達人プログラマー")
        assert "達人プログラマー" in r1["reply"]
        r2 = sb.skill("/inbox")
        assert "達人プログラマー" in r2["reply"]
        assert len(contacts_inbox.list_inbox()) == 1

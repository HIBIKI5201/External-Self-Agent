"""Hand a task to Claude Code or another configured AI provider via ai_hub (#322)."""
import os

import ai_hub
import db

HANDOFF_DIR = os.path.join(db.DATA_DIR, "handoffs")
MAX_ENCODED_URL = ai_hub.MAX_ENCODED_URL


def _render(handoff_id, task, convo, full_path=None):
    return ai_hub.render_packet(handoff_id, task, convo, provider="claude", full_path=full_path)


def _url(prompt, folder):
    return ai_hub.claude_url(prompt, folder)


def prepare(task, convo, folder, provider="claude"):
    return ai_hub.prepare(task, convo, folder, provider=provider)


def open_in_claude(handoff_id, provider=None):
    return ai_hub.open_handoff(handoff_id, provider=provider)

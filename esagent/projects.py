"""What each project (category) is about, for the model (AS-04, #7).

"就活" or "Sinfonia Studio" alone tell an 8B model little. A project can carry a short description
and a related folder; the description goes into the prompts for advice, /morning and the router.
A draft description can be made from the folder's README / AGENTS.md; the user edits it on /admin.
"""
import os

import db
import llm

DOC_NAMES = ("README.md", "AGENTS.md", "CLAUDE.md", "README.txt")
DOC_CHARS = 700        # per file
DRAFT_CHARS = 5000     # all files together
MAX_SUBDIRS = 10


def described(names=None):
    """Projects that have a description, optionally only those named."""
    want = {n for n in names or [] if n}
    return [p for p in db.list_projects()
            if (p.get("description") or "").strip() and (not want or p["name"] in want)]


def context_lines(names=None, width=200):
    """'- 就活: …' lines for prompts; empty string when nothing is described."""
    return "\n".join(f"- {p['name']}: {p['description'].strip()[:width]}" for p in described(names))


def _read_head(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(DOC_CHARS)
    except OSError:
        return ""


def folder_digest(folder):
    """The folder's own docs, then its sub-folders' (one level), clipped."""
    if not folder or not os.path.isdir(folder):
        return "", []
    subdirs = sorted(d for d in os.listdir(folder)
                     if os.path.isdir(os.path.join(folder, d)) and not d.startswith((".", "_")))[:MAX_SUBDIRS]
    parts = []
    for where in [folder] + [os.path.join(folder, d) for d in subdirs]:
        for name in DOC_NAMES:
            text = _read_head(os.path.join(where, name)).strip()
            if text:
                parts.append(f"## {os.path.relpath(os.path.join(where, name), folder)}\n{text}")
                break
    return "\n\n".join(parts)[:DRAFT_CHARS], subdirs


def draft_description(cfg, project):
    """A 1-3 sentence Japanese description from the related folder, for the user to edit."""
    digest, subdirs = folder_digest(project.get("folder"))
    if not digest:
        return f"{project['folder']} 配下: {', '.join(subdirs)}" if subdirs else ""
    system = ("Below are excerpts of the README files in a folder the user works in. "
              "Write what this work area is about in 1-3 short Japanese sentences (under 150 characters), "
              "naming the main sub-projects. Only use facts from the excerpts. Output only the description.")
    user = f"Project name: {project['name']}\nFolder: {project['folder']}\nSub-folders: {', '.join(subdirs)}\n\n{digest}"
    text = llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    options={"temperature": 0.2, "num_predict": 200}).strip()
    return text[:400]


def update(project_id, description=None, folder=None):
    if folder:
        folder = folder.strip().strip('"')
        if not os.path.isdir(folder):
            raise ValueError(f"フォルダが見つかりません: {folder}")
    return db.update_project(project_id, description=description, folder=folder)

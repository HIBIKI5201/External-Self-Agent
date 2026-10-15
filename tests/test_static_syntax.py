"""Every script of the screens parses (#252): esagent/static/*.js and the inline <script> of *.html.
A syntax error stops a whole pane (the calendar, the admin page) while CI would still pass."""
import glob
import os
import re
import subprocess
import tempfile

from support import ROOT

STATIC = os.path.join(ROOT, "esagent", "static")
INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def _check(path, label):
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, f"{label}: {r.stderr.strip()}"


def inline_scripts(html):
    return [s for s in INLINE.findall(html) if s.strip()]


def test_static_javascript_files_parse():
    files = sorted(glob.glob(os.path.join(STATIC, "*.js")))
    assert any(f.endswith("calendar.js") for f in files)
    for f in files:
        _check(f, os.path.basename(f))


def test_inline_scripts_of_the_pages_parse():
    checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        for page in sorted(glob.glob(os.path.join(STATIC, "*.html"))):
            with open(page, encoding="utf-8") as f:
                scripts = inline_scripts(f.read())
            for i, code in enumerate(scripts):
                path = os.path.join(tmp, f"{os.path.basename(page)}.{i}.js")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code)
                _check(path, f"{os.path.basename(page)} の {i + 1} 番目の <script>")
                checked += 1
    assert checked >= 2  # index.html and admin.html


def test_a_broken_script_is_caught():
    assert inline_scripts('<script src="/static/a.js"></script><script>let x = 1;</script>') == ["let x = 1;"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "broken.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write("function (")
        try:
            _check(path, "broken")
        except AssertionError:
            return
    raise AssertionError("node --check accepted a broken script")

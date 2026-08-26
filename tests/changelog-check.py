#!/usr/bin/env python3
"""Checks for the changelog the panel reads out of its own checkout.

The button beside *Check for updates* opens whatever `/api/changelog` returns,
so what matters here is that the route reads the file that is actually on disk,
that it says so plainly when there is not one — a tarball, or a copy with the
file removed, where the button hides itself rather than opening an empty dialog
— and that a file nobody meant to be enormous cannot be sent whole.

The server is real; only ROOT moves, which is how the missing-file case is
arranged without touching the checkout this is running in.

    python3 tests/changelog-check.py

A failure prints the case and exits 1.
"""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from watchtower import config, http as panel_http, update  # noqa: E402
from watchtower.config import ROOT  # noqa: E402
from watchtower.http import Handler  # noqa: E402

FAILED = 0


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


# ---------------------------------------------------------------- the file
path = ROOT / "CHANGELOG.md"
check("the checkout has a changelog", path.exists(), str(path))
text = path.read_text() if path.exists() else ""
check("it opens with a heading", text.startswith("# "), text.splitlines()[0] if text else "")
check("it has somewhere to put what is not released yet", "## Unreleased" in text)
# Every version heading has to be a version the updater would recognise as a
# release, or the changelog and the tags drift apart silently.
headings = re.findall(r"^## (.+)$", text, re.M)
versions = [h for h in headings if h != "Unreleased"]
check("and a heading per release, newest first",
      bool(versions) and all(re.match(r"^v?\d+\.\d+\.\d+( — \d{4}-\d{2}-\d{2})?$", v) for v in versions),
      ", ".join(versions))
check("each of which names a version the updater counts as a release",
      all(update.release_of(v.split(" — ")[0]) for v in versions))
# Long lines on purpose: the panel's markdown renderer turns a single newline
# into a line break, so a hard-wrapped paragraph reads as ragged in the dialog.
wrapped = [line for line in text.splitlines()
           if line.strip() and not line.startswith("#") and len(line) < 60]
check("its paragraphs are not hard-wrapped, which the renderer would break",
      not wrapped, f"{len(wrapped)} short lines, first: {wrapped[0][:40] if wrapped else ''}")


# ---------------------------------------------------------------- the route
def serve() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def ask(server, path_: str = "/api/changelog"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("GET", path_)
        answer = conn.getresponse()
        return answer.status, answer.read()
    finally:
        conn.close()


config.ACCESS_KEY = None
server = serve()
try:
    code, body = ask(server)
    found = json.loads(body)
    check("the route answers with the file", code == 200 and found["ok"] is True, str(code))
    check("and it is the file, not a copy of it made at import time",
          found["text"] == text[:panel_http.CHANGELOG_MAX])
    check("with when it was last written, for the dialog to say",
          isinstance(found.get("at"), float) and found["at"] > 0)

    # A checkout with no changelog in it. ROOT is what the handler reads, so
    # moving it is the whole of the arrangement.
    with tempfile.TemporaryDirectory() as empty:
        panel_http.ROOT = Path(empty)
        code, body = ask(server)
        check("a copy without one says so rather than opening empty",
              code == 404 and json.loads(body)["ok"] is False, str(code))
        check("and says it in a sentence somebody can read",
              "changelog" in json.loads(body).get("message", "").lower())

        # And one that is far longer than anyone meant it to be.
        (Path(empty) / "CHANGELOG.md").write_text("x" * (panel_http.CHANGELOG_MAX + 5000))
        code, body = ask(server)
        sent = json.loads(body)["text"]
        check("an enormous one is cut rather than sent whole",
              code == 200 and len(sent) == panel_http.CHANGELOG_MAX, str(len(sent)))
finally:
    panel_http.ROOT = ROOT
    server.shutdown()

print()
print("all ok" if not FAILED else f"{FAILED} failed")
sys.exit(1 if FAILED else 0)

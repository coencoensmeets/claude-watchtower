#!/usr/bin/env python3
"""Checks for a picture pasted into the box — no browser, no Claude.

A message is text on every transport the panel has, so a pasted picture goes to
disk and the message names the file. That makes the write the whole feature, and
a write into somebody's checkout is worth checking by itself: where the file
lands, what kinds are allowed, what happens to a name that came off a request,
and what a body far too big for a screenshot does to the server.

The helper is driven directly, and the endpoint over a real socket with a fake
session standing in for a running one.

    python3 tests/paste-check.py

A failure prints the case and exits 1.
"""

import base64
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server as S  # noqa: E402

FAILED = 0

# The smallest real PNG there is: one transparent pixel.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF/8LzJAAAAAElFTkSuQmCC"
)


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


# --------------------------------------------------------------- the write

work = tempfile.mkdtemp(prefix="watchtower-paste-")

ok, path, message = S.save_pasted_image(work, "image/png", base64.b64encode(PIXEL).decode())
check("a pasted picture is written", ok, message)
check("into the session's own folder, under .claude",
      path.startswith(str(Path(work) / ".claude" / "watchtower-images")), path)
check("with the bytes that came off the clipboard",
      ok and Path(path).read_bytes() == PIXEL)
check("and the extension its kind implies", path.endswith(".png"), path)

# A data URL header, which is how the browser hands the type over.
ok2, path2, _ = S.save_pasted_image(work, "image/jpeg; charset=binary",
                                    base64.b64encode(PIXEL).decode())
check("a type with parameters on it is still read", ok2 and path2.endswith(".jpg"), path2)
check("and two pastes in the same second do not collide", path2 != path)

bad, _, why = S.save_pasted_image(work, "text/html", base64.b64encode(b"<b>no</b>").decode())
check("what is not a picture is refused", not bad, why)
bad, _, why = S.save_pasted_image(work, "application/x-sh", base64.b64encode(b"rm -rf /").decode())
check("and so is anything that would land as a script", not bad, why)
bad, _, why = S.save_pasted_image(work, "image/png", "not base64 at all!!")
check("a picture that did not arrive in one piece is refused", not bad, why)
bad, _, why = S.save_pasted_image(work, "image/png", "")
check("an empty one too", not bad, why)
big = base64.b64encode(b"\0" * (S.PASTE_MAX_BYTES + 1)).decode()
bad, _, why = S.save_pasted_image(work, "image/png", big)
check("one larger than the ceiling is refused rather than written", not bad, why)

# What the sweep is for: the folder must not grow for the life of the machine.
stale = Path(work) / ".claude" / "watchtower-images" / "paste-old.png"
stale.write_bytes(PIXEL)
os.utime(stale, (time.time() - S.PASTE_KEEP_SECONDS - 60,) * 2)
S.save_pasted_image(work, "image/png", base64.b64encode(PIXEL).decode())
check("a picture older than the keep window is swept by the next paste", not stale.exists())
check("and the fresh ones are left alone", Path(path).exists())


# ------------------------------------------------------------ the endpoint

# One fake session, in the folder made above. Nothing is running; the endpoint
# only ever wants the folder, and it must come from the panel rather than the
# request — which is what standing in here checks.
S.SAY_ENABLED = True
S.Handler._session_by_id = lambda self, session_id: (  # type: ignore[assignment]
    {"sessionId": "s1", "cwd": work} if session_id == "s1" else None)

httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def post(body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(f"{base}/api/paste-image", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


code, data = post({"sessionId": "s1", "mime": "image/png",
                   "data": base64.b64encode(PIXEL).decode()})
check("the endpoint saves and answers with the path", code == 200 and bool(data.get("path")), str(data))
check("and the path it answers with is a file that is there",
      bool(data.get("path")) and Path(data["path"]).exists())

code, data = post({"sessionId": "nobody", "mime": "image/png",
                   "data": base64.b64encode(PIXEL).decode()})
check("a session the panel does not know has nowhere to save to", code == 404, str(data))

code, data = post({"sessionId": "s1", "mime": "text/html", "data": "aGk="})
check("the endpoint refuses what is not a picture", code == 400, str(data))

# A body past the ceiling is read and thrown away rather than held in memory,
# and the answer says which limit it met.
code, data = post({"sessionId": "s1", "mime": "image/png", "data": "A" * (S.POST_MAX_BYTES + 1024)})
check("a body too big for any screenshot is turned away", code == 413, str(data))

# The gate is the same as sending's, because saving a picture is part of sending.
S.SAY_ENABLED = False
code, data = post({"sessionId": "s1", "mime": "image/png",
                   "data": base64.b64encode(PIXEL).decode()})
check("with sending off, nothing is written either", code == 403, str(data))

httpd.shutdown()
print("\nall ok" if not FAILED else f"\n{FAILED} failed")
sys.exit(1 if FAILED else 0)

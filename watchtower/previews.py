"""Things a session made that you can look at: a page, a styleguide, a site.

A session that builds something viewable hands it to the panel with the
`show_preview` MCP tool (see watchtower/mcp.py), and the panel lists it under
that session's **Preview** tab and opens it — on this computer or a phone.

Two kinds:

  - a file or folder on disk. The panel serves the folder the page is in, so
    the page's own stylesheets, scripts and pictures load beside it, at
    /preview/<token>/<path>.
  - a URL, for a dev server the session started. The panel cannot serve that
    itself; it hands out the address, and on a phone swaps `localhost` for the
    panel's own host name so a server listening on every interface is reached.

The token is the whole of the access check for /preview/. It is 128 random
bits, the same order as the panel's key, and it only reaches files under the
one folder it was made for. It has to work without the key's cookie: the page
is served sandboxed (see http.py), which gives it an origin of its own, and a
browser does not send a Lax cookie with the requests such a page makes for its
own stylesheets.

Written down in ~/.config/claude-watchtower/previews.json so a restarted panel
still has them; capped, because session ids never repeat.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from watchtower.config import HOME

PREVIEW_FILE = HOME / ".config" / "claude-watchtower" / "previews.json"
# Starred previews: kept apart from the sessions that showed them, so they
# outlive the session, its row being forgotten and the per-session cap.
STAR_FILE = HOME / ".config" / "claude-watchtower" / "starred.json"
MAX_STARRED = 60
MAX_PER_SESSION = 12
MAX_SESSIONS = 100
MAX_TITLE = 80
INDEX_NAMES = ("index.html", "index.htm")
PAGE_SUFFIXES = {".html", ".htm", ".svg", ".pdf", ".md", ".txt", ".png", ".jpg", ".jpeg",
                 ".gif", ".webp"}

_LOCK = threading.Lock()


def _load() -> dict:
    try:
        data = json.loads(PREVIEW_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    PREVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    spare = PREVIEW_FILE.with_suffix(".tmp")
    spare.write_text(json.dumps(data, indent=1))
    spare.replace(PREVIEW_FILE)


def read_previews() -> dict[str, list[dict]]:
    """Session id -> its previews, newest first. What the feed carries."""
    with _LOCK:
        data = _load()
    return {sid: sorted(rows, key=lambda r: -r.get("at", 0)) for sid, rows in data.items()
            if isinstance(rows, list) and rows}


def find(token: str) -> dict | None:
    """The preview a /preview/<token>/ request is for — a session's, or a
    starred one whose session has since let it go."""
    if not token:
        return None
    rows = [r for rs in read_previews().values() for r in rs] + read_starred()
    for row in rows:
        if row.get("kind") == "file" and secrets.compare_digest(str(row.get("token", "")), token):
            return row
    return None


# --- starred: the sites listed under the sessions, with no session attached

def _load_starred() -> list[dict]:
    try:
        data = json.loads(STAR_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []


def _save_starred(rows: list[dict]) -> None:
    STAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    spare = STAR_FILE.with_suffix(".tmp")
    spare.write_text(json.dumps(rows, indent=1))
    spare.replace(STAR_FILE)


def read_starred() -> list[dict]:
    """The starred sites, in the order they were starred, oldest first."""
    with _LOCK:
        return _load_starred()


def _key(row: dict) -> str:
    return str(row.get("path") or row.get("url") or "")


def star(row: dict, session_id: str, cwd: str) -> dict:
    """Star a session's preview. Copies what is needed to serve and open it —
    the token included, so a link already handed out keeps working — and the
    folder a chat about it would start in. Starring the same page twice gives
    back the star it already has."""
    keep = ("kind", "title", "path", "root", "entry", "url", "token")
    with _LOCK:
        rows = _load_starred()
        old = next((r for r in rows if _key(r) == _key(row)), None)
        if old:
            return old
        made = {k: row[k] for k in keep if row.get(k)}
        made.update({"id": row.get("id") or secrets.token_hex(4), "at": time.time(),
                     "from": session_id, "cwd": cwd or row.get("root") or ""})
        if any(r["id"] == made["id"] for r in rows):
            made["id"] = secrets.token_hex(4)
        rows.append(made)
        _save_starred(rows[-MAX_STARRED:])
    return made


def unstar(star_id: str) -> bool:
    with _LOCK:
        rows = _load_starred()
        kept = [r for r in rows if r.get("id") != star_id]
        if len(kept) == len(rows):
            return False
        _save_starred(kept)
    return True


def rename_star(star_id: str, title: str) -> dict | None:
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    if not title:
        return None
    with _LOCK:
        rows = _load_starred()
        row = next((r for r in rows if r.get("id") == star_id), None)
        if row is None:
            return None
        row["title"] = title
        _save_starred(rows)
    return row


def _title_for(spot: Path) -> str:
    name = spot.parent.name if spot.name.lower() in INDEX_NAMES else spot.stem
    return name.replace("-", " ").replace("_", " ").strip().capitalize() or spot.name


def make_entry(target: str, spot: Path | None, title: str) -> tuple[dict | None, str]:
    """A preview from what a session asked to show, or None and why not.

    `spot` is the target already resolved and fenced by the caller when it is
    a path; None means it is a URL."""
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    if spot is None:
        url = urlparse(target)
        if url.scheme not in ("http", "https") or not url.hostname:
            return None, "A preview is a path to a page or folder, or an http(s) address"
        return {"kind": "url", "url": target, "title": title or url.netloc}, ""
    if spot.is_dir():
        page = next((spot / n for n in INDEX_NAMES if (spot / n).is_file()), None)
        if page is None:
            return None, f"There is no index.html in {spot}; name the page itself"
        spot = page
    if not spot.is_file():
        return None, f"That is not a file: {spot}"
    if spot.suffix.lower() not in PAGE_SUFFIXES:
        return None, f"The panel shows pages and pictures, not {spot.suffix or 'files without a suffix'}"
    return {"kind": "file", "path": str(spot), "root": str(spot.parent), "entry": spot.name,
            "title": title or _title_for(spot)}, ""


def add_preview(session_id: str, entry: dict) -> dict:
    """Keep it under the session. The same page shown again moves to the top
    and keeps its token, so an address already open stays good."""
    key = entry.get("path") or entry.get("url")
    with _LOCK:
        data = _load()
        rows = [r for r in data.get(session_id) or [] if isinstance(r, dict)]
        old = next((r for r in rows if (r.get("path") or r.get("url")) == key), None)
        rows = [r for r in rows if r is not old]
        row = {**entry, "id": (old or {}).get("id") or secrets.token_hex(4),
               "at": time.time(), "shown": ((old or {}).get("shown") or 0) + 1}
        if entry["kind"] == "file":
            row["token"] = (old or {}).get("token") or secrets.token_urlsafe(16)
        rows.insert(0, row)
        data[session_id] = rows[:MAX_PER_SESSION]
        if len(data) > MAX_SESSIONS:
            newest = sorted(data, key=lambda s: max((r.get("at", 0) for r in data[s]), default=0))
            for stale in newest[:len(data) - MAX_SESSIONS]:
                data.pop(stale, None)
        _save(data)
    return row


def remove_preview(session_id: str, preview_id: str) -> bool:
    with _LOCK:
        data = _load()
        rows = data.get(session_id) or []
        kept = [r for r in rows if r.get("id") != preview_id]
        if len(kept) == len(rows):
            return False
        if kept:
            data[session_id] = kept
        else:
            data.pop(session_id, None)
        _save(data)
    return True


def preview_path(row: dict) -> str:
    """Where the panel serves a file preview, relative to the panel's address."""
    return f"/preview/{row['token']}/{row['entry']}"


def resolve_file(row: dict, rest: str) -> Path | None:
    """A file under a preview's folder, or None: never anything outside it."""
    try:
        root = Path(row["root"]).resolve(strict=True)
        spot = (root / rest.lstrip("/")).resolve(strict=True)
    except (OSError, RuntimeError, KeyError):
        return None
    if spot != root and root not in spot.parents:
        return None
    if spot.is_dir():
        spot = next((spot / n for n in INDEX_NAMES if (spot / n).is_file()), None)
    return spot if spot and spot.is_file() else None

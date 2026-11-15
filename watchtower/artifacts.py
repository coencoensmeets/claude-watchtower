"""Artifacts a session published, shown in its Preview tab.

Claude Code's `Artifact` tool takes a page the session wrote to disk and
publishes it as a private page on claude.ai. The call carries the local path
(`file_path`) and, on an update, the address it is replacing (`url`); the result
carries the address it was published at. Both halves are in the transcript.

So an artifact is a preview that already happened: the panel files it under the
session's Preview tab the moment it sees the publish, without the session having
to call `show_preview` as well. The local file is what the frame shows — a
claude.ai page will not load inside another site's frame, and would need you
signed in if it did — and the claude.ai address rides along on the preview, for
an **Open on claude.ai** button beside the panel's own **Open**.

Read forward and incrementally, like shells.py, because a publish is one line far
back in a long session. Which publishes have been filed is written down, so a
restarted panel does not file them all again, and a preview you removed stays
removed.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from watchtower import previews
from watchtower.config import HOME
from watchtower.transcript import transcript_paths

SEEN_FILE = HOME / ".config" / "claude-watchtower" / "artifacts-seen.json"
SEEN_MAX = 2000
STALE_SECONDS = 2 * 24 * 3600
ARTIFACT_URL = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{4,80}")
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
# The actions that put a page up. The rest read, list, pin or delete.
PUBLISHING = (None, "", "publish")


def iso_seconds(stamp: object) -> float | None:
    """A transcript timestamp as epoch seconds."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _seen_load() -> list[str]:
    try:
        data = json.loads(SEEN_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _seen_save(items: list[str]) -> None:
    try:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        SEEN_FILE.write_text(json.dumps(items[-SEEN_MAX:]))
    except OSError:
        pass


class _Scan:
    """The publishes one transcript has made so far, in order."""

    def __init__(self) -> None:
        self.offset = 0
        self.inode: int | None = None
        self.calls: dict[str, dict] = {}       # tool_use_id -> the publish call
        self.published: list[dict] = []

    def feed(self, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            self.__init__()
            self.inode = stat.st_ino
        if stat.st_size == self.offset:
            return
        try:
            with path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(stat.st_size - self.offset)
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for raw in chunk[:end].split(b"\n"):
            self._line(raw)

    def _line(self, raw: bytes) -> None:
        call = b'"name":"Artifact"' in raw
        result = bool(self.calls) and b'"tool_result"' in raw
        if not (call or result):
            return
        try:
            entry = json.loads(raw)
        except ValueError:
            return
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Artifact":
                args = block.get("input") or {}
                if args.get("action") not in PUBLISHING or args.get("asset"):
                    continue
                if not isinstance(args.get("file_path"), str):
                    continue
                self.calls[str(block.get("id"))] = {
                    "path": args["file_path"],
                    "url": args.get("url") if isinstance(args.get("url"), str) else None,
                    "description": " ".join(str(args.get("description") or "").split())[:200],
                }
            elif block.get("type") == "tool_result":
                tool_id = str(block.get("tool_use_id"))
                made = self.calls.pop(tool_id, None)
                if made is None or block.get("is_error"):
                    continue
                # The address it went up at. Read off the result as text rather
                # than out of a field, so a change in the result's shape costs
                # nothing as long as the link is in it.
                found = ARTIFACT_URL.search(json.dumps(block.get("content"))
                                            + json.dumps(entry.get("toolUseResult")))
                url = found.group(0) if found else made["url"]
                if not url:
                    continue
                self.published.append({**made, "url": url, "toolUseId": tool_id,
                                       "at": entry.get("timestamp")})


_scans: dict[str, _Scan] = {}
_lock = threading.Lock()


def published(session_id: str, cwd: str) -> list[dict]:
    """Every page this session has published as an artifact, oldest first."""
    paths = transcript_paths(session_id, cwd)
    if not paths:
        return []
    with _lock:
        scan = _scans.setdefault(session_id, _Scan())
        scan.feed(paths[0])
        return list(scan.published)


def _servable(path: str, cwd: str) -> Path | None:
    """The page, if the panel may serve the folder it is in.

    The same fence as a session's own show_preview: inside your home folder,
    the session's folder or the temporary folder, and never one of those
    folders itself, since the whole folder becomes reachable."""
    try:
        spot = Path(path if Path(path).is_absolute() else Path(cwd) / path).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    roots = [Path(HOME).resolve(), Path(tempfile.gettempdir()).resolve()]
    if cwd:
        roots.append(Path(cwd).resolve())
    if not any(spot == root or root in spot.parents for root in roots):
        return None
    broad = {Path(HOME).resolve(), Path(tempfile.gettempdir()).resolve(), Path("/").resolve()}
    if spot.parent in broad:
        return None
    return spot


def _page_title(spot: Path) -> str:
    try:
        with spot.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        return ""
    found = TITLE.search(head)
    return " ".join(found.group(1).decode("utf-8", "replace").split()) if found else ""


def file_new(session_id: str, cwd: str) -> int:
    """File any publish not filed before as a preview. How many were filed."""
    fresh = published(session_id, cwd)
    if not fresh:
        return 0
    seen = _seen_load()
    known = set(seen)
    # A publish from long ago is history, not news: on a first run over old
    # sessions it would file a page per publish and light every Preview tab.
    cutoff = time.time() - STALE_SECONDS
    todo = [item for item in fresh if item["toolUseId"] not in known
            and (iso_seconds(item["at"]) or time.time()) >= cutoff]
    if not todo:
        return 0
    for item in todo:
        spot = _servable(item["path"], cwd)
        made = None
        if spot is not None:
            made, _ = previews.make_entry(item["path"], spot, _page_title(spot))
        if made is None:
            # Nothing on disk the panel may serve: the address alone, which
            # Open takes to claude.ai.
            made = {"kind": "url", "url": item["url"], "title": "Artifact"}
        made["artifactUrl"] = item["url"]
        if item["description"]:
            made["description"] = item["description"]
        previews.add_preview(session_id, made)
        seen.append(item["toolUseId"])
    _seen_save(seen)
    return len(todo)


def forget(session_id: str) -> None:
    with _lock:
        _scans.pop(session_id, None)

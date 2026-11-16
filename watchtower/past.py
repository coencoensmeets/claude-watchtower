"""The other conversations that were had in a session's folder — the panel's /resume.

Claude Code keeps every conversation as a transcript in a folder named after the
working directory, so "what else was said here" is a directory listing away. The
list is what `/resume` offers in a terminal: newest first, each one named by the
title Claude gave it or else by what it was first asked.

Reading a transcript to say that much means reading all of it — the first real
prompt can be anywhere in the first megabyte, and the count of prompts is the
whole file — so each one is read once and remembered against its size and
modification time. A conversation still being written to changes both and is
read again; the forty that finished last month are read once per panel.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

from watchtower import transcript
from watchtower.transcript import unwrap_sent

# The newest this many are listed. A folder with years of sessions behind it has
# hundreds, and the one you are looking for is nearly always recent.
PAST_MAX = 60

# What it is called in the list, at most. The row is one line.
TITLE_CHARS = 140

# The panel's own headless errands that predate `--no-session-persistence`, and
# so left a transcript behind in the repository they ran in. They are a prompt
# and an answer the panel had, not a conversation you had, so they are left out.
ERRAND_PROMPTS = ("Write a git commit message for the change below.",)

# The entry types worth parsing, recognised on the raw line first. Claude Code
# writes compact JSON, and the optional space keeps a hand-written one readable.
_ASSISTANT = re.compile(r'"type": ?"assistant"')
_WANTED = re.compile(r'"type": ?"(?:user|ai-title)"')
_TOOL_RESULT = re.compile(r'"type": ?"tool_result"')

_CACHE_LOCK = threading.Lock()
# path -> ((size, mtime_ns), summary or None). None is remembered too: a
# transcript that is not a conversation stays not one until it changes.
_CACHE: dict[str, tuple[tuple[int, int], dict | None]] = {}


def project_dir(cwd: str) -> Path:
    """The folder Claude Code keeps this working directory's transcripts in."""
    return transcript.PROJECT_DIR / ("-" + re.sub(r"[^A-Za-z0-9]+", "-", cwd.lstrip("/")))


def _prompt_text(entry: dict) -> str | None:
    """What was typed, when this user entry is something somebody typed."""
    if entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = " ".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return None
    text = content.strip()
    # A message the panel or another session sent arrives wrapped, and is what
    # was said all the same.
    sent = unwrap_sent(text)
    if sent:
        text = str(sent.get("text") or "").strip()
    # `/clear`, the caveat lines around a local command, a hook's report: all
    # plumbing written as a user turn, and all of it starts with a tag.
    if not text or text.startswith("<"):
        return None
    # A comment sent from the panel quotes the passage it is about first. What
    # the row should show is what was said about it, so the quote is dropped
    # when there is anything under it.
    if text.startswith(">"):
        said = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">")).strip()
        text = said or text
    # What Claude Code writes in your name when you press Escape mid-turn. You
    # did not type it, and as the last thing said it reads as a non-answer.
    if text.startswith("[Request interrupted by user"):
        return None
    return text


def _epoch(stamp: object) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _clip(text: str, size: int = TITLE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def summarise(path: Path) -> dict | None:
    """One transcript as a row of the list, or None when it is not a conversation."""
    title = first = last = branch = None
    prompts = replies = 0
    started = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # Most of a transcript is assistant turns and tool results. They
                # are counted or skipped on sight rather than parsed.
                if _ASSISTANT.search(line):
                    replies += 1
                    continue
                if not _WANTED.search(line) or _TOOL_RESULT.search(line):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") == "ai-title":
                    title = entry.get("aiTitle") or title
                    continue
                if entry.get("type") != "user":
                    continue
                started = started or entry.get("timestamp")
                branch = entry.get("gitBranch") or branch
                text = _prompt_text(entry)
                if text is None:
                    continue
                prompts += 1
                first = first or text
                last = text
    except OSError:
        return None
    # Nothing answered is nothing to go back to: a `/clear` that was the last
    # thing a session did leaves exactly that behind.
    if not prompts or not replies:
        return None
    if any(first.startswith(errand) for errand in ERRAND_PROMPTS):
        return None
    return {
        "sessionId": path.stem,
        "title": _clip(title.strip()) if isinstance(title, str) and title.strip() else None,
        "first": _clip(first),
        "last": _clip(last) if last != first else None,
        "prompts": prompts,
        "branch": branch if branch and branch != "HEAD" else None,
        "startedAt": _epoch(started) if started else None,
    }


def _cached(path: Path, stat) -> dict | None:
    key = (stat.st_size, stat.st_mtime_ns)
    with _CACHE_LOCK:
        hit = _CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    found = summarise(path)
    with _CACHE_LOCK:
        _CACHE[str(path)] = (key, found)
    return found


def past_sessions(cwd: str, limit: int = PAST_MAX) -> tuple[list[dict], int]:
    """The conversations had in this folder, newest first, and how many more there are.

    Only the transcripts at the top of the folder: a subagent's live in a folder
    named after the session that sent it, and are part of that conversation.
    """
    if not cwd:
        return [], 0
    folder = project_dir(cwd)
    try:
        files = [(path, path.stat()) for path in folder.glob("*.jsonl") if path.is_file()]
    except OSError:
        return [], 0
    files.sort(key=lambda pair: pair[1].st_mtime, reverse=True)
    out: list[dict] = []
    seen = 0
    for path, stat in files:
        found = _cached(path, stat)
        if not found:
            continue
        seen += 1
        if len(out) < limit:
            out.append({**found, "lastAt": stat.st_mtime})
    return out, seen - len(out)


def past_session(cwd: str, session_id: str) -> dict | None:
    """One of them by id — and only one that really is in this folder."""
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id or ""):
        return None
    path = project_dir(cwd) / f"{session_id}.jsonl"
    try:
        stat = path.stat()
    except OSError:
        return None
    found = _cached(path, stat)
    return {**found, "lastAt": stat.st_mtime} if found else None

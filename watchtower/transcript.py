"""Reading a session's transcript: what was said, and what it is doing now.

Claude Code appends one JSON line per event to a file per session. Everything
the panel knows beyond the session file is read from here — the last thing that
happened, the permission mode, the question a session is standing at, the title,
and the conversation itself.

Transcripts get long, so nothing here reads one whole: see reverse_lines, which
walks backwards from the end in blocks.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from watchtower.config import PROJECT_DIR


def tail_bytes(path: Path, size: int = 96_000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            start = max(0, handle.tell() - size)
            handle.seek(start)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def summarise_block(block: dict) -> str | None:
    kind = block.get("type")
    if kind == "text":
        text = (block.get("text") or "").strip()
        return " ".join(text.split())[:160] or None
    if kind == "tool_use":
        name = block.get("name") or "tool"
        # The same reading the conversation view takes, cut to the width of a
        # one-line summary — including the question a session is asking, which
        # names no file and runs no command.
        detail = tool_detail(block.get("input"))[:110]
        return f"{name}: {detail}" if detail else str(name)
    if kind == "thinking":
        return "thinking"
    return None


# How Claude Code writes down an answer to AskUserQuestion: as that tool's
# result, one quoted pair per question. There is no other record of what was
# picked, so this is the only way to show it back.
ANSWERED_PREFIX = "Your questions have been answered:"



ANSWERED_PAIR = re.compile(r'"([^"]+)"="([^"]*)"')



def answers_in(content: object) -> list[str]:
    """The question-and-answer pairs in a tool result, as lines to show."""
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                out += answers_in(part.get("text"))
        return out
    if not isinstance(content, str) or ANSWERED_PREFIX not in content:
        return []
    pairs = ANSWERED_PAIR.findall(content)
    # Several picks for one question come back joined, and read better as a list.
    return [f"{question} — {answer.replace(', ', ' · ')}" for question, answer in pairs if answer]


def transcript_paths(session_id: str, cwd: str) -> list[Path]:
    """Where Claude Code keeps this session's transcript."""
    slug = "-" + re.sub(r"[^A-Za-z0-9]+", "-", cwd.lstrip("/"))
    direct = PROJECT_DIR / slug / f"{session_id}.jsonl"
    if direct.exists():
        return [direct]
    try:
        return list(PROJECT_DIR.glob(f"*/{session_id}.jsonl"))
    except OSError:
        return []


def has_conversation(session_id: str, cwd: str) -> bool:
    """Whether anything has been said in this session yet.

    A transcript that does not exist, or exists and is empty, is a session that
    has never taken a turn — and `--resume` refuses one of those, so it decides
    whether a session is resumed or started under its own id.
    """
    return any(path.exists() and path.stat().st_size
               for path in transcript_paths(session_id, cwd))


def last_activity(session_id: str, cwd: str) -> dict | None:
    """Best-effort read of the newest interesting line in the transcript."""
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        lines = tail_bytes(path).splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            role = entry.get("type")
            if role not in ("assistant", "user"):
                continue
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            summary = None
            if isinstance(content, str):
                summary = " ".join(content.split())[:160] or None
            elif isinstance(content, list):
                for block in reversed(content):
                    if isinstance(block, dict):
                        summary = summarise_block(block)
                        if summary:
                            break
            if summary:
                return {"role": role, "text": summary, "mtime": path.stat().st_mtime}
    return None


# Every mode a reading can come back as. `dontAsk` is not one Shift+Tab reaches;
# the rest are its ring, in the order it walks them.
PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions", "auto", "dontAsk")


def read_permission_mode(session_id: str, cwd: str) -> str | None:
    """The permission mode this session last wrote down.

    Claude Code does not record the mode when it changes. The mode rides along in
    the block of session metadata the transcript writer re-appends now and then —
    on a resume, at exit, once the transcript has grown past a threshold — so a
    session that is working says where it is within seconds, and one sitting at
    its prompt holds whatever it last said, however long ago that was.

    Which is why this is a reading and not a setting. Nothing else on disk says
    more: the session file does not carry the mode, and the setter inside Claude
    Code only stages the value in memory. Anything that acted on this number —
    counting Shift+Tab presses from it, say — would be starting from a figure
    that is right until someone touches the keyboard and has no way to notice
    that they did.
    """
    for path in transcript_paths(session_id, cwd):
        for line in reversed(tail_bytes(path).splitlines()):
            if '"permission-mode"' not in line:
                continue
            try:
                entry = json.loads(line.strip())
            except ValueError:
                continue
            if entry.get("type") != "permission-mode":
                continue
            mode = entry.get("permissionMode")
            if isinstance(mode, str) and mode in PERMISSION_MODES:
                return mode
    return None


# How far back to look for an unanswered question. A pending one is close to the
# tail by construction — nothing follows a question but its own answer — so this
# only has to clear whatever else lands after it: a message queued at the prompt
# while it stands, a snapshot, a re-written title. The walk is deliberately short
# because the lines it reads are tool results, and one of those can be a whole
# file.
QUESTION_PATIENCE = 200


MAX_QUESTION_OPTIONS = 12


def question_asked(block: dict) -> dict | None:
    """The questions in an AskUserQuestion call, trimmed to what a card shows."""
    args = block.get("input")
    if not isinstance(args, dict):
        return None
    raw = args.get("questions")
    if not isinstance(raw, list) or not raw:
        return None
    questions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        options = []
        for option in item.get("options") or []:
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            if not label:
                continue
            options.append({
                "label": label[:200],
                "description": str(option.get("description") or "").strip()[:400],
            })
            if len(options) >= MAX_QUESTION_OPTIONS:
                break
        if not text and not options:
            continue
        questions.append({
            "question": text[:400],
            "header": str(item.get("header") or "").strip()[:40],
            "multiSelect": bool(item.get("multiSelect")),
            "options": options,
        })
    if not questions:
        return None
    return {"toolUseId": str(block.get("id") or ""), "questions": questions}


def read_pending_question(session_id: str, cwd: str) -> dict | None:
    """The AskUserQuestion this session is sitting on, if it is sitting on one.

    Claude Code shows the options in the terminal and blocks there; nothing in the
    session file says so. What the transcript has is the call itself — an
    `AskUserQuestion` tool_use — and, once it has been answered, a tool_result
    carrying the same id. Walking back from the newest line, a call whose result
    has not been seen yet is a question still on screen.

    Read like the mode and the title, from the tail, so the cost does not grow
    with the transcript. The walk stops at the first AskUserQuestion either way:
    an older, answered one is not what is being asked now.
    """
    for path in transcript_paths(session_id, cwd):
        answered: set[str] = set()
        seen = 0
        for line in reverse_lines(path):
            seen += 1
            if seen > QUESTION_PATIENCE:
                return None
            line = line.strip()
            if not line.startswith("{"):
                continue
            if "AskUserQuestion" not in line and "tool_result" not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("isSidechain"):
                continue  # a subagent's question is not one you can answer
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    used = block.get("tool_use_id")
                    if isinstance(used, str):
                        answered.add(used)
                    continue
                if block.get("type") != "tool_use" or block.get("name") != "AskUserQuestion":
                    continue
                asked = question_asked(block)
                if asked is None or asked["toolUseId"] in answered:
                    return None
                asked["at"] = entry.get("timestamp")
                return asked
    return None


def read_ai_title(session_id: str, cwd: str) -> str | None:
    """The line Claude wrote about what this session is doing.

    Claude Code names the conversation itself a few turns in and re-writes the
    name as the subject moves, in an `ai-title` entry of its own. It is the one
    thing on disk that says what a session is *for* — the name in the list says
    what it is called — so the index shows both.

    Read from the tail like the permission mode, and for the same reason: the
    entry recurs often enough that the end of the transcript almost always has
    one, and a session too young to have been named yet reads as nothing, which
    is the truth.
    """
    for path in transcript_paths(session_id, cwd):
        for line in reversed(tail_bytes(path).splitlines()):
            if '"ai-title"' not in line:
                continue
            try:
                entry = json.loads(line.strip())
            except ValueError:
                continue
            if entry.get("type") != "ai-title":
                continue
            title = entry.get("aiTitle")
            if isinstance(title, str) and title.strip():
                return title.strip()[:120]
    return None


TOOL_DETAIL_KEYS = ("description", "command", "file_path", "pattern", "path", "prompt", "url", "query")


# Entry types that are plumbing rather than conversation.
SKIP_ENTRY_TYPES = {
    "attachment", "file-history-snapshot", "queue-operation", "last-prompt",
    "ai-title", "mode", "permission-mode", "system", "summary",
}


# What a change costs the conversation to carry. The transcript is re-read on
# every poll while the chat is open, so what rides along with it is a preview and
# a count; the whole patch is a click and its own request away.
CHANGE_PREVIEW = 8



CHANGE_LINE = 200



# And what the whole one costs when asked for. Past this the file is not a change
# to read, it is a file — a generated one, or a wholesale rewrite.
CHANGE_MAX = 4000



def patch_lines(result: object) -> list[str]:
    """The patch Claude Code wrote down for an edit, as unified-diff lines.

    Not reconstructed from the tool's arguments — recorded. Claude Code writes a
    `structuredPatch` beside every Edit and Write result: real hunks against the
    real file, with the line numbers the file actually has. Rebuilding a diff
    from `old_string` and `new_string` would have neither, and would be a guess
    about a file that has already been written.
    """
    if not isinstance(result, dict):
        return []
    out: list[str] = []
    patch = result.get("structuredPatch")
    if isinstance(patch, list) and patch:
        for hunk in patch:
            if not isinstance(hunk, dict) or not isinstance(hunk.get("lines"), list):
                continue
            out.append(f"@@ -{hunk.get('oldStart', 0)},{hunk.get('oldLines', 0)}"
                       f" +{hunk.get('newStart', 0)},{hunk.get('newLines', 0)} @@")
            out += [str(line) for line in hunk["lines"]]
        return out
    # A file written where there was none has nothing to diff against, and is
    # written down as its own content. Shown the way the Git tab shows an
    # untracked file: all added.
    content = result.get("content")
    if isinstance(content, str) and content:
        body = content.splitlines()
        return [f"@@ -0,0 +1,{len(body)} @@", *(f"+{line}" for line in body)]
    return []



def change_of(result: object) -> dict | None:
    """One file change, as the conversation should carry it: a preview and a size.

    The preview starts at the first line that actually changes rather than at the
    top of the patch. A hunk opens with its context, and a preview of the context
    is a preview of the part you did not want to see — three unchanged lines and
    a promise that something happens further down.
    """
    if not isinstance(result, dict):
        return None
    path = result.get("filePath")
    if not isinstance(path, str) or not path:
        return None
    lines = patch_lines(result)
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))
    if not added and not removed:
        return None
    head = [lines[0]] if lines and lines[0].startswith("@@") else []
    rest = lines[len(head):]
    first = next((i for i, line in enumerate(rest) if line[:1] in "+-"), 0)
    # One line of context above it, where there is one: a change with nothing
    # around it reads as having come from nowhere.
    start = max(0, first - 1)
    preview = head + rest[start:start + CHANGE_PREVIEW]
    return {"path": path, "added": added, "removed": removed, "lines": len(lines),
            "preview": [line[:CHANGE_LINE] for line in preview]}



def tool_result_id(message: object) -> str:
    """Which tool call a result belongs to."""
    if not isinstance(message, dict):
        return ""
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            found = block.get("tool_use_id")
            if isinstance(found, str):
                return found
    return ""


def tool_detail(args: object) -> str:
    if not isinstance(args, dict):
        return ""
    for key in TOOL_DETAIL_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    # A question carries none of the keys above: what it is about is the question
    # itself, which is a list of them a level down. Without this the busiest line
    # in the transcript — the one you are being asked to answer — reads as a bare
    # tool name.
    asked = args.get("questions")
    if isinstance(asked, list):
        for item in asked:
            if isinstance(item, dict) and str(item.get("question") or "").strip():
                return " ".join(str(item["question"]).split())[:200]
    return ""


# A message that arrives over a session's socket — from this panel's composer or
# from another session — may be wrapped in an envelope naming its sender. Another
# session writes one; this panel sends the text bare, and neither has to.
CROSS_SESSION = re.compile(
    r"^<cross-session-message(?P<attrs>[^>]*)>\n(?P<body>.*)\n</cross-session-message>$",
    re.DOTALL,
)


FROM_NAME = re.compile(r'from-name="([^"]*)"')


# How the same message reads once it has been handed to the model: the body with
# a preamble in front and a paragraph of standing instructions behind it.
PEER_DELIVERY = re.compile(
    r"^Another Claude session sent a message:\n(?P<body>.*?)"
    r"\n\nThis came from another Claude session",
    re.DOTALL,
)


def unwrap_sent(text: str) -> dict | None:
    """The message inside a socket delivery, and who put it there.

    Claude Code never writes such a message down as a plain turn: it records it
    on the queue and hands it to the model wrapped in a preamble, on a turn
    marked as meta. Left alone, everything typed into this panel's composer would
    therefore be missing from the conversation it was typed into — which is
    precisely the half of the conversation the panel is responsible for.

    Both wrappings are peeled here, and both are optional: a peer names itself
    with an envelope, this panel sends the text bare, and the queue records
    whichever arrived.
    """
    text = text.strip()
    delivered = PEER_DELIVERY.match(text)
    if delivered:
        text = delivered.group("body").strip()
    found = CROSS_SESSION.match(text)
    if found:
        who = FROM_NAME.search(found.group("attrs") or "")
        return {"text": found.group("body").strip(),
                "from": (who.group(1) if who else "") or None}
    if delivered:
        return {"text": text, "from": None}
    return None


def reverse_lines(path: Path, cap: int = 3_000_000, block: int = 262_144):
    """The file's lines, newest first, reading back only as far as it is asked to.

    A transcript is mostly tool results, and a working session writes megabytes of
    them between two things you actually said. A fixed tail therefore drops your
    own messages first — on a 5 MB transcript the last 400 KB held ten of Claude's
    turns and none of mine. Walking backwards spends its reading where the
    conversation is instead.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            floor = max(0, pos - cap)
            held = b""
            while pos > floor:
                start = max(floor, pos - block)
                handle.seek(start)
                chunk = handle.read(pos - start) + held
                pos = start
                parts = chunk.split(b"\n")
                # The first piece is only half a line until the chunk before it
                # arrives, so it waits.
                held = parts.pop(0)
                for raw in reversed(parts):
                    if raw.strip():
                        yield raw.decode("utf-8", "replace")
            if held.strip():
                yield held.decode("utf-8", "replace")
    except OSError:
        return


# How far past a full page of messages to keep looking for the session's title
# before giving up on it: it rides in a metadata block, which recurs often.
TITLE_PATIENCE = 4000


# As far back as the panel will go when asked for more. Past this the reading is
# no longer cheap — and reverse_lines has its own byte cap under it anyway.
TRANSCRIPT_LIMIT_MAX = 500


def read_change(session_id: str, cwd: str, tool_use_id: str) -> dict:
    """The whole of one change, for the preview in the chat that was clicked.

    Its own read rather than something carried along with the conversation: a
    patch is unbounded, the transcript is re-read on every poll while the chat is
    open, and the reader who wants all of one change wants it once.

    Read newest-first and stopped at the one asked for, so the cost is the walk
    back to it rather than the size of the transcript.
    """
    out = {"ok": False, "id": tool_use_id, "path": "", "text": "",
           "added": 0, "removed": 0, "clipped": False}
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        for line in reverse_lines(path, cap=8_000_000):
            line = line.strip()
            if not line.startswith("{") or tool_use_id not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if tool_result_id(entry.get("message")) != tool_use_id:
                continue
            result = entry.get("toolUseResult")
            lines = patch_lines(result)
            if not lines:
                return {**out, "message": "That change is not written down line by line"}
            clipped = len(lines) > CHANGE_MAX
            return {**out, "ok": True,
                    "path": str((result or {}).get("filePath") or ""),
                    "text": "\n".join(lines[:CHANGE_MAX]),
                    "added": sum(1 for one in lines if one.startswith("+")),
                    "removed": sum(1 for one in lines if one.startswith("-")),
                    "clipped": clipped}
        return {**out, "message": "That change is no longer in the transcript this panel reads"}
    return {**out, "message": "There is no transcript to read it from"}


def read_transcript(session_id: str, cwd: str, limit: int = 60) -> dict:
    """The recent conversation: what you said, what Claude said, what it ran.

    Tool results are left out — they are the mechanics of a turn, not the
    conversation — but each tool call is kept so the run reads honestly. Read
    newest-first and stopped as soon as there is a page of it, so a long session
    costs no more than a short one.
    """
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        found = parse_transcript(path, limit)
        found["sessionId"] = session_id
        return found
    return {"sessionId": session_id, "title": None, "messages": [],
            "truncated": False, "path": None}


def parse_transcript(path: Path, limit: int = 60, sidechain: bool = False,
                     agents: dict[str, dict] | None = None) -> dict:
    """One transcript file, read as a conversation.

    `sidechain` says which kind of file this is. A session's own transcript holds
    no sidechain entries any more — Claude Code gives each subagent a file of its
    own — but the ones it does hold are another conversation's, and skipping them
    is what keeps a session's transcript the session's. Read a subagent's file
    with the flag set and the same skip would empty it, since every line in it is
    a sidechain.
    """
    title = None
    entries: list[dict] = []
    sent: dict[str, dict] = {}
    # toolUseId -> the change that tool made. The walk is newest-first, so a
    # result is always read before the call that caused it, which is what
    # makes this a plain lookup rather than a second pass.
    changes: dict[str, dict] = {}
    more = False
    seen = 0

    def keep(came: dict, at: object) -> None:
        """Show a socket message once, under the name of whoever sent it.

        The same message is written down twice — going on the queue and
        coming off it — and only one of the two still carries the sender's
        name, which is not always the one read first.
        """
        already = sent.get(came["text"])
        if already is not None:
            if came["from"] and not already["from"]:
                already["from"] = came["from"]
            return
        shown = {"role": "user", "at": at, "text": came["text"][:4000],
                 "tools": [], "from": came["from"] or ""}
        sent[came["text"]] = shown
        entries.append(shown)
    # A bigger ask reads further back: the byte cap is what usually ends the
    # walk on a long transcript, not the message count.
    for line in reverse_lines(path, cap=max(3_000_000, limit * 50_000)):
        line = line.strip()
        if not line.startswith("{"):
            continue
        seen += 1
        # A page of conversation is enough; the title is worth a little more
        # reading, since it rides in a block of its own.
        if len(entries) >= limit and (title is not None or seen > TITLE_PATIENCE):
            more = True
            break
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind == "ai-title" and entry.get("aiTitle"):
            if title is None:
                title = str(entry["aiTitle"])[:120]
            continue
        # Where a message sent over the socket is written down: on the queue,
        # once going on and once coming off. The one going on is the message.
        if kind == "queue-operation":
            if entry.get("operation") != "enqueue":
                continue
            queued = str(entry.get("content") or "").strip()
            # A peer names itself with an envelope; this panel sends the text
            # bare, and bare is then indistinguishable from what it is — a
            # message. Only the tagged plumbing (task notifications and the
            # like) is left out.
            came = unwrap_sent(queued)
            if came is None and queued and not queued.startswith("<"):
                came = {"text": queued, "from": None}
            if came and came["text"]:
                keep(came, entry.get("timestamp"))
            continue
        if kind in SKIP_ENTRY_TYPES or kind not in ("user", "assistant"):
            continue
        if entry.get("isSidechain") and not sidechain:
            continue
        # What a tool did to a file, written down on the result. Read before
        # the tool_result turn is skipped as mechanics, because this is the
        # one part of it that is not mechanics: it is the change itself.
        if isinstance(entry.get("toolUseResult"), dict):
            made = change_of(entry["toolUseResult"])
            said = tool_result_id(entry.get("message"))
            if made and said:
                changes[said] = made
        # A message that came in over the socket is written down as meta —
        # it was not typed at this terminal. It is still the conversation.
        origin = entry.get("origin")
        from_peer = isinstance(origin, dict) and origin.get("kind") == "peer"
        if entry.get("isMeta") and not from_peer:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        at = entry.get("timestamp")

        if isinstance(content, str):
            text = content.strip()
            # A message delivered rather than queued arrives wrapped; the
            # same message must not be shown twice.
            came = unwrap_sent(text)
            if came is None and from_peer and text:
                came = {"text": text, "from": None}
            if came:
                if came["text"]:
                    keep(came, at)
            elif text and not text.startswith("<"):
                shown = {"role": kind, "at": at, "text": text[:4000], "tools": []}
                # A turn the panel ran is written down twice — once as the
                # prompt going on the queue, once as the message itself —
                # so a message typed here is registered against the queue
                # the same way a delivered one is, and whichever is read
                # second is dropped rather than shown again.
                if kind == "user":
                    sent.setdefault(text, shown)
                entries.append(shown)
            continue
        if not isinstance(content, list):
            continue

        texts, tools, only_results, answered = [], [], True, []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_kind = block.get("type")
            if block_kind == "text":
                only_results = False
                text = (block.get("text") or "").strip()
                if text:
                    texts.append(text)
            elif block_kind == "tool_use":
                only_results = False
                made = changes.get(block.get("id") or "")
                tools.append({"name": block.get("name") or "tool",
                              "detail": tool_detail(block.get("input")),
                              # Only where there is one: every other tool call
                              # would otherwise carry a null through every poll.
                              **({"change": {**made, "id": block["id"]}} if made else {})})
            elif block_kind == "thinking":
                only_results = False
            elif block_kind == "tool_result":
                # An answer to a question is written down here and nowhere
                # else: the question is a tool call, so what you picked is
                # its result. It is the one tool_result worth showing —
                # without it the conversation reads as Claude asking
                # something and then carrying on for no visible reason.
                answered += answers_in(block.get("content"))
        if answered:
            entries.append({"role": "user", "at": at, "tools": [],
                            "text": "\n".join(answered)[:4000], "from": "answered here"})
            continue
        if only_results:
            continue  # a pure tool_result turn
        if texts or tools:
            shown = {
                "role": kind, "at": at,
                "text": "\n\n".join(texts)[:4000],
                "tools": tools[:12],
            }
            # Same as above, for a user turn whose content arrived as blocks.
            if kind == "user" and texts and not tools:
                sent.setdefault("\n\n".join(texts), shown)
            entries.append(shown)

    # Read newest-first, so put it back the way it was said.
    entries.reverse()
    return {
        "sessionId": "",
        "title": title,
        "messages": entries[-max(1, min(limit, TRANSCRIPT_LIMIT_MAX)):],
        "truncated": more or len(entries) > limit,
        "path": str(path),
    }

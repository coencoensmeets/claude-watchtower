"""The shell commands a session left running in the background.

A session can have work going that its own status does not show. A command
started with `run_in_background`, or a foreground command Claude Code moved to
the background when it ran past its timeout, keeps running after the turn ends —
so the session reads as waiting for you while a build, a download or a watcher
is still going. Claude Code records each of them in two places:

    the transcript   the tool result carries `toolUseResult.backgroundTaskId`,
                     and a `<task-notification>` arrives when it ends
    the temp folder  <tmp>/claude-<uid>/<project>/<session-id>/tasks/<id>.output,
                     which the command's stdout and stderr are pointed at

Neither is enough alone. The notification is only written while the session is
alive to write it, so a session that was closed under a running command would
show it running forever; and the output file says nothing about what the
command is. So the transcript names the tasks, and the process table says which
are still going: a background command is running exactly as long as some
process still holds its output file as stdout or stderr. Where there is no
/proc to ask, the notification is the fallback.

The transcript is read forward and incrementally — only the bytes added since
the last read — because a task's id appears once, at the moment it was
backgrounded, which on a long session is far behind any tail the panel reads.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from watchtower.transcript import transcript_paths

TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A notification's id and outcome. The status is one of completed, failed,
# stopped or killed; anything but running means the task is over.
NOTIFICATION = re.compile(
    r"<task-id>([A-Za-z0-9_-]{1,64})</task-id>.*?<status>([a-z_]+)</status>", re.S)
OUTPUT_PATH = re.compile(r"/claude-\d+/[^/]+/([^/]+)/tasks/([A-Za-z0-9_-]{1,64})\.output$")

# How much of a command and its description travel to the panel. A heredoc of a
# Python script is a command too, and the chip only has room for a line.
TEXT_MAX = 200
# How many Bash calls are remembered while waiting to see whether their result
# says they went to the background. A result follows its call within a few
# lines, so this only has to outlast one turn's worth of parallel calls.
PENDING_MAX = 64


def _clip(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 1] + "…"


class _Scan:
    """What one transcript has said about background tasks so far."""

    def __init__(self) -> None:
        self.offset = 0
        self.inode: int | None = None
        self.calls: dict[str, dict] = {}       # tool_use_id -> the call's input
        self.tasks: dict[str, dict] = {}       # task id -> what it is
        self.ended: dict[str, str] = {}        # task id -> how it ended

    def feed(self, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        # Rewritten rather than appended to — start over rather than read from
        # the middle of a line.
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
        # Only whole lines; a half-written last line waits for the next read.
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for raw in chunk[:end].split(b"\n"):
            self._line(raw)

    def _line(self, raw: bytes) -> None:
        # Most lines are none of these, and parsing every line of a ten-megabyte
        # transcript on first sight is the cost worth avoiding.
        calls = b'"name":"Bash"' in raw
        result = b'"backgroundTaskId"' in raw
        notice = b"<task-notification>" in raw
        if not (calls or result or notice):
            return
        if notice:
            text = raw.decode("utf-8", "replace").replace("\\n", "\n")
            for task_id, status in NOTIFICATION.findall(text):
                if status != "running":
                    self.ended[task_id] = status
        if not (calls or result):
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
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                args = block.get("input") or {}
                self.calls[str(block.get("id"))] = {
                    "description": _clip(args.get("description")),
                    "command": _clip(args.get("command")),
                }
                while len(self.calls) > PENDING_MAX:
                    self.calls.pop(next(iter(self.calls)))
            elif block.get("type") == "tool_result" and result:
                outcome = entry.get("toolUseResult")
                task_id = outcome.get("backgroundTaskId") if isinstance(outcome, dict) else None
                if not isinstance(task_id, str) or not TASK_ID.match(task_id):
                    continue
                call = self.calls.pop(str(block.get("tool_use_id")), None) or {}
                self.tasks[task_id] = {
                    "taskId": task_id,
                    "description": call.get("description") or "",
                    "command": call.get("command") or "",
                    # Moved there by the timeout rather than asked for: worth
                    # saying, since nobody chose to leave it running.
                    "timedOut": bool(outcome.get("timedOutAfterMs")),
                    "startedAt": entry.get("timestamp"),
                }


_scans: dict[str, _Scan] = {}
_lock = threading.Lock()


def background_tasks(session_id: str, cwd: str) -> list[dict]:
    """Every command this session has sent to the background, oldest first."""
    paths = transcript_paths(session_id, cwd)
    if not paths:
        return []
    with _lock:
        scan = _scans.setdefault(session_id, _Scan())
        scan.feed(paths[0])
        return [{**task, "ended": scan.ended.get(task_id)}
                for task_id, task in scan.tasks.items()]


def forget(session_id: str) -> None:
    with _lock:
        _scans.pop(session_id, None)


def held_outputs() -> dict[str, dict[str, list[int]]] | None:
    """Which task output files some process is writing to, by session.

    One pass over /proc, reading only each process's stdout and stderr — which
    is where a backgrounded command's output is pointed. None where there is no
    /proc to read, so callers can tell "nothing running" from "cannot tell".
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    uid = os.getuid() if hasattr(os, "getuid") else None
    held: dict[str, dict[str, list[int]]] = {}
    for entry in entries:
        if not entry.isdigit():
            continue
        for fd in ("1", "2"):
            try:
                target = os.readlink(f"/proc/{entry}/fd/{fd}")
            except OSError:
                break
            if "/tasks/" not in target:
                continue
            found = OUTPUT_PATH.search(target)
            if not found or (uid is not None and f"/claude-{uid}/" not in target):
                continue
            pids = held.setdefault(found.group(1), {}).setdefault(found.group(2), [])
            if int(entry) not in pids:
                pids.append(int(entry))
    return held


def running_shells(session_id: str, cwd: str, held: dict | None) -> list[dict]:
    """The background commands of this session that are still going.

    `held` is one held_outputs() sweep, shared by every session in a poll.
    """
    tasks = background_tasks(session_id, cwd)
    if not tasks:
        return []
    if held is None:
        return [task for task in tasks if not task["ended"]]
    mine = held.get(session_id) or {}
    return [{**task, "pids": sorted(mine[task["taskId"]])}
            for task in tasks if task["taskId"] in mine]


def output_path(session_id: str, task_id: str) -> Path | None:
    """Where a background command's output is being written, if it exists."""
    if not TASK_ID.match(task_id or "") or not re.match(r"^[A-Za-z0-9_-]{1,64}$", session_id or ""):
        return None
    roots = {os.environ.get("CLAUDE_CODE_TMPDIR"), os.environ.get("TMPDIR"), "/tmp"}
    uid = os.getuid() if hasattr(os, "getuid") else None
    for root in filter(None, roots):
        base = Path(root) / (f"claude-{uid}" if uid is not None else "claude")
        try:
            for found in base.glob(f"*/{session_id}/tasks/{task_id}.output"):
                return found
        except OSError:
            continue
    return None


def read_output(session_id: str, task_id: str, size: int = 16_000) -> dict:
    """The tail of a background command's output."""
    path = output_path(session_id, task_id)
    if path is None:
        return {"ok": False, "message": "That command's output is no longer there"}
    try:
        total = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, total - size))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return {"ok": False, "message": "That command's output could not be read"}
    if total > size:
        text = text.split("\n", 1)[-1]
    return {"ok": True, "taskId": task_id, "output": text, "size": total, "clipped": total > size}

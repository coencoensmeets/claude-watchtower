"""Scheduled tasks: something to do in a project folder, later or on repeat.

A task belongs to a folder — the project — and is one of two kinds:

  - claude   a prompt, sent as a turn. Either to a fresh session the panel
             starts for every run, or to the same session every time (the first
             run starts it; later runs continue that conversation).
  - command  a shell command, run with `bash -lc` in the folder. Its exit code
             and the tail of its output are kept with the run.

And runs either once, at a moment, or every so many seconds from a first
moment. Set from the panel's **Schedule** tab, or by a session through the
`schedule_task` MCP tool (see watchtower/mcp.py).

The panel is the clock. A task only runs while the panel does; one whose
moment passed while it was down runs once when it comes back, and a repeating
one then carries on from the next moment still ahead — it does not run once
for every interval it missed. A repeating task still running when its next
moment comes skips that moment rather than running twice over itself.

Nothing runs while sending is off (`config.SAY_ENABLED`): a scheduled task is
a turn or a command started from the panel, and gets the same gate as one
started by hand.

Written down in ~/.config/claude-watchtower/schedules.json. This process is
the only writer, so the list is held in memory and the file is its copy.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from watchtower import config
from watchtower.config import HOME

SCHEDULE_FILE = HOME / ".config" / "claude-watchtower" / "schedules.json"
KINDS = ("claude", "command")
SESSION_CHOICES = ("new", "same")
MODES = ("default", "auto", "plan", "acceptEdits")
MAX_TASKS = 200
MAX_TITLE = 80
MAX_TEXT = 20_000
MAX_HISTORY = 20
OUTPUT_TAIL = 4000
MIN_EVERY = 60                      # seconds; a minute is the finest the clock ticks
MAX_EVERY = 366 * 86400
DEFAULT_TIMEOUT = 30 * 60           # a command, unless the task says otherwise
MAX_TIMEOUT = 24 * 3600
TICK = 10.0                          # how often the clock looks

_LOCK = threading.Lock()
_TASKS: list[dict] | None = None
# Task ids with a run in flight, so a slow one is not started over itself.
_RUNNING: set[str] = set()


# ------------------------------------------------------------------ storage

def _load() -> list[dict]:
    global _TASKS
    if _TASKS is None:
        try:
            data = json.loads(SCHEDULE_FILE.read_text())
        except (OSError, ValueError):
            data = []
        _TASKS = [t for t in data if isinstance(t, dict) and t.get("id")] if isinstance(data, list) else []
    return _TASKS


def _save() -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    spare = SCHEDULE_FILE.with_suffix(".tmp")
    spare.write_text(json.dumps(_TASKS or [], indent=1))
    spare.replace(SCHEDULE_FILE)


def reset_for_tests() -> None:
    """Forget the in-memory copy, so the next read comes off SCHEDULE_FILE."""
    global _TASKS
    with _LOCK:
        _TASKS = None
        _RUNNING.clear()


def _public(task: dict) -> dict:
    return {**task, "running": task["id"] in _RUNNING}


def read_schedules(cwd: str | None = None) -> list[dict]:
    """Every task, or only the ones in one folder; soonest first, paused last."""
    with _LOCK:
        rows = [_public(t) for t in _load() if cwd is None or t.get("cwd") == cwd]
    return sorted(rows, key=lambda t: (not t.get("enabled"), t.get("next") or float("inf"), t.get("created", 0)))


def get(task_id: str) -> dict | None:
    with _LOCK:
        hit = next((t for t in _load() if t["id"] == task_id), None)
        return _public(hit) if hit else None


# --------------------------------------------------------------- the timing

def next_after(task: dict, now: float) -> float | None:
    """The first moment of this task still ahead of `now`, or None if it has none.

    A repeating task's moments are its start plus whole intervals, so it keeps
    to its own rhythm — every hour on the quarter stays on the quarter — however
    late the clock looked."""
    at = float(task.get("at") or 0)
    every = task.get("every")
    if not every:
        return at if at > now else None
    if at > now:
        return at
    missed = int((now - at) // every) + 1
    return at + missed * every


# ---------------------------------------------------------------- the edits

def _clean(fields: dict, old: dict | None) -> tuple[dict | None, str]:
    """A task's fields checked and tidied, over the task it replaces if any."""
    merged = {**(old or {}), **{k: v for k, v in fields.items() if v is not None}}
    kind = merged.get("kind") or "claude"
    if kind not in KINDS:
        return None, f"A task is one of {', '.join(KINDS)}, not {kind!r}"
    text = str(merged.get("text") or "").strip()
    if not text:
        return None, "Say what to do: the prompt for Claude, or the command to run"
    if len(text) > MAX_TEXT:
        return None, f"That is {len(text)} characters; keep it under {MAX_TEXT}"
    title = " ".join(str(merged.get("title") or "").split())[:MAX_TITLE]
    if not title:
        title = " ".join(text.split())[:48] + ("…" if len(" ".join(text.split())) > 48 else "")
    try:
        at = float(merged.get("at") or time.time())
        every = int(merged["every"]) if merged.get("every") else None
    except (TypeError, ValueError):
        return None, "`at` is a moment in seconds since the epoch, and `every` a number of seconds"
    if every is not None and not MIN_EVERY <= every <= MAX_EVERY:
        return None, f"Repeat at most once a minute and at least once a year ({MIN_EVERY}–{MAX_EVERY} seconds)"
    mode = merged.get("mode") or "auto"
    if mode not in MODES:
        return None, f"Mode is one of {', '.join(MODES)}"
    session = merged.get("session") or "new"
    if session not in SESSION_CHOICES:
        return None, "`session` is 'new' (a fresh session each run) or 'same' (one conversation, continued)"
    try:
        timeout = int(merged.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        return None, "`timeout` is a number of seconds"
    timeout = max(10, min(MAX_TIMEOUT, timeout))
    task = {
        "id": merged.get("id") or secrets.token_hex(4),
        "cwd": merged["cwd"], "title": title, "kind": kind, "text": text,
        "at": at, "every": every, "enabled": merged.get("enabled", True) is not False,
        "created": merged.get("created") or time.time(),
        "createdBy": merged.get("createdBy") or "you",
        "runs": merged.get("runs") or 0, "history": merged.get("history") or [],
        # Listed under the sessions, so it can be seen and worked with when no
        # session in its folder is on the list.
        "starred": merged.get("starred") is True,
    }
    if kind == "claude":
        task.update({"mode": mode, "session": session})
        # Pointing a 'same' task at a session that exists already is allowed;
        # otherwise its first run starts one and remembers it.
        if session == "same" and merged.get("sessionId"):
            task["sessionId"] = str(merged["sessionId"])
    else:
        task["timeout"] = timeout
    if merged.get("last"):
        task["last"] = merged["last"]
    task["next"] = next_after(task, time.time() - 1) if task["enabled"] else None
    if task["enabled"] and task["next"] is None:
        # Once, at a moment already gone: that is "now", not "never".
        task["next"] = time.time()
    return task, ""


def save_task(fields: dict) -> tuple[dict | None, str]:
    """Add a task, or change one when `fields` carries the id of one. `cwd` is
    the folder, already checked by the caller."""
    with _LOCK:
        tasks = _load()
        old = None
        if fields.get("id"):
            old = next((t for t in tasks if t["id"] == fields["id"]), None)
            if old is None:
                return None, "No scheduled task with that id"
            if fields.get("cwd") and fields["cwd"] != old["cwd"]:
                return None, "That task belongs to another folder"
            fields = {**fields, "cwd": old["cwd"]}
        elif len(tasks) >= MAX_TASKS:
            return None, f"There are {MAX_TASKS} scheduled tasks already; remove some first"
        task, why = _clean(fields, old)
        if task is None:
            return None, why
        if old is not None:
            tasks[tasks.index(old)] = task
        else:
            tasks.append(task)
        _save()
        return _public(task), ""


def remove(task_id: str) -> bool:
    with _LOCK:
        tasks = _load()
        kept = [t for t in tasks if t["id"] != task_id]
        if len(kept) == len(tasks):
            return False
        tasks[:] = kept
        _save()
    return True


def set_enabled(task_id: str, enabled: bool) -> dict | None:
    with _LOCK:
        task = next((t for t in _load() if t["id"] == task_id), None)
        if task is None:
            return None
        task["enabled"] = bool(enabled)
        now = time.time()
        # A one-off whose moment has gone, switched back on, runs now.
        task["next"] = (next_after(task, now) or now) if enabled else None
        _save()
        return _public(task)


def set_starred(task_id: str, starred: bool) -> dict | None:
    with _LOCK:
        task = next((t for t in _load() if t["id"] == task_id), None)
        if task is None:
            return None
        task["starred"] = bool(starred)
        _save()
        return _public(task)


# ---------------------------------------------------------------- the runs

def _record(task_id: str, run: dict) -> None:
    with _LOCK:
        task = next((t for t in _load() if t["id"] == task_id), None)
        if task is None:
            return
        task["last"] = run
        task["history"] = [run, *(task.get("history") or [])][:MAX_HISTORY]
        if run.get("sessionId") and task.get("session") == "same":
            task["sessionId"] = run["sessionId"]
        _save()


def run_command(task: dict) -> dict:
    """Run a command task to the end, or to its timeout, and say how it went."""
    from watchtower.control import top_level_env
    from watchtower.errands import own_errand

    started = time.time()
    try:
        proc = subprocess.Popen(
            ["bash", "-lc", task["text"]], cwd=task["cwd"], env=top_level_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return {"at": started, "ok": False, "message": f"Could not start it: {exc}"}
    own_errand(proc.pid, True)
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=task.get("timeout") or DEFAULT_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            out, _ = proc.communicate()
    finally:
        own_errand(proc.pid, False)
    text = (out or b"").decode(errors="replace")
    tail = text[-OUTPUT_TAIL:]
    if len(text) > OUTPUT_TAIL:
        tail = "…" + tail
    code = proc.returncode
    took = time.time() - started
    if timed_out:
        message = f"Stopped after {int(task.get('timeout') or DEFAULT_TIMEOUT)}s"
    elif code == 0:
        message = f"Exited 0 in {took:.1f}s"
    else:
        message = f"Exited {code} in {took:.1f}s"
    return {"at": started, "ok": code == 0 and not timed_out, "exit": code,
            "seconds": round(took, 1), "message": message, "output": tail}


def run_claude(task: dict) -> dict:
    """Send a claude task's prompt: into a fresh session, or the same one again."""
    from watchtower.input import deliver_later, say_to_session, session_alive, session_listening
    from watchtower.owned import load_owned, owned_new, owned_say, save_owned
    from watchtower.rows import pin_row
    from watchtower.store import STORE

    started = time.time()
    cwd, mode, text = task["cwd"], task.get("mode") or "auto", task["text"]
    if not Path(cwd).is_dir():
        return {"at": started, "ok": False, "message": f"Its folder is gone: {cwd}"}
    sid = task.get("sessionId") if task.get("session") == "same" else None
    if sid:
        data = STORE.raw(sid)
        if session_alive(data):
            # Open in a terminal: down its socket, or held until it listens.
            ok, said = say_to_session(data, text) if session_listening(data) else (False, "")
            if not ok:
                ok, said = deliver_later(sid, text)
        else:
            owned = load_owned()
            owned[sid] = {**(owned.get(sid) or {}), "mode": (owned.get(sid) or {}).get("mode") or mode, "here": True}
            save_owned(owned)
            ok, said = owned_say(sid, cwd, text, owned[sid]["mode"])
        return {"at": started, "ok": ok, "sessionId": sid,
                "message": "Sent to its session" if ok else f"Could not send it: {said}"}
    sid, said = owned_new(cwd, mode)
    if not sid:
        return {"at": started, "ok": False, "message": f"Could not start a session: {said}"}
    # Pinned and named after the task, so the run is on the list to read.
    pin_row(sid, {"sessionId": sid, "name": task["title"], "cwd": cwd, "startedAt": started,
                  "lastSeen": started, "version": None, "kind": "interactive"})
    owned = load_owned()
    owned[sid] = {"mode": mode, "here": True}
    save_owned(owned)
    ok, said = owned_say(sid, cwd, text, mode)
    return {"at": started, "ok": ok, "sessionId": sid,
            "message": "Started a session for it" if ok else f"Started a session, but could not send: {said}"}


RUNNERS: dict[str, Callable[[dict], dict]] = {"command": run_command, "claude": run_claude}


def _run(task: dict) -> None:
    try:
        run = RUNNERS[task["kind"]](task)
    except Exception as exc:  # a broken run must not take the clock down
        run = {"at": time.time(), "ok": False, "message": f"Failed: {exc}"}
    run["kind"] = task["kind"]
    try:
        _record(task["id"], run)
    finally:
        with _LOCK:
            _RUNNING.discard(task["id"])


def start_run(task_id: str, wait: bool = False) -> tuple[bool, str]:
    """Run one task now, whatever its schedule says. Moves nothing on the clock."""
    with _LOCK:
        task = next((t for t in _load() if t["id"] == task_id), None)
        if task is None:
            return False, "No scheduled task with that id"
        if task_id in _RUNNING:
            return False, "It is running already"
        _RUNNING.add(task_id)
        task["runs"] = (task.get("runs") or 0) + 1
        snapshot = dict(task)
        _save()
    if wait:
        _run(snapshot)
    else:
        threading.Thread(target=_run, args=(snapshot,), daemon=True).start()
    return True, "Started"


def tick(now: float | None = None, wait: bool = False) -> list[str]:
    """Start every task that is due, and move each one's clock on. Answers with
    the ids started; `wait` runs them here instead of in threads (for the tests)."""
    now = time.time() if now is None else now
    due = []
    with _LOCK:
        changed = False
        for task in _load():
            if not task.get("enabled") or task.get("next") is None or task["next"] > now:
                continue
            changed = True
            task["next"] = next_after(task, now) if task.get("every") else None
            if not task.get("every"):
                task["enabled"] = False
            if task["id"] in _RUNNING:
                # Still on the last run: this moment is skipped, not stacked.
                continue
            if not config.SAY_ENABLED:
                skipped = {"at": now, "ok": False, "kind": task["kind"],
                           "message": "Skipped: sending is off on this panel"}
                task["last"] = skipped
                task["history"] = [skipped, *(task.get("history") or [])][:MAX_HISTORY]
                continue
            _RUNNING.add(task["id"])
            task["runs"] = (task.get("runs") or 0) + 1
            due.append(dict(task))
        if changed:
            _save()
    for task in due:
        if wait:
            _run(task)
        else:
            threading.Thread(target=_run, args=(task,), daemon=True).start()
    return [t["id"] for t in due]


def run_forever() -> None:
    while True:
        try:
            tick()
        except Exception:  # the clock keeps going whatever one tick did
            pass
        time.sleep(TICK)


def start() -> None:
    threading.Thread(target=run_forever, daemon=True, name="schedules").start()

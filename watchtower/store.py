"""The panel's picture of what is running, kept current by one polling thread.

Everything the web layer serves comes from here. The store reads the session
files once a second, works out each session's real state — see effective_status
and _alive, which is where the guessing lives — and keeps a short trace of how
each one has moved, so the panel can draw where a session has been rather than
only where it is.

Reads that cost something are cached with their own lifetimes: a working folder
does not move under a running session, a branch changes whenever it checks out.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from watchtower import config
from watchtower.agents import list_subagents, subagent_dir
from watchtower.config import (
    ACTIVE_STATUSES, AGENT_DOCK_MAX, CPU_WINDOW, HISTORY_SECONDS, LIVENESS_GRACE,
    SAMPLE_INTERVAL, SESSION_DIR, STATUS_TTL, TRANSCRIPT_WINDOW, WORKING_CPU,
)
from watchtower.control import can_pick_folder
from watchtower.errands import is_own_errand
from watchtower.git.read import git_branch, git_root
from watchtower.owned import OWNED_ASK, OWNED_BUSY, owned_running, owned_moved, owned_state
from watchtower.proc import ancestors, cpu_seconds, proc_gone, proc_name, proc_starttime, session_tty
from watchtower.rows import kept_rows, refresh_row
from watchtower.input import session_alive, session_listening
from watchtower.sessions import PEER_PROTOCOL, child_sessions, peer_protocols
from watchtower.transcript import (
    has_conversation, last_activity, read_ai_title, read_pending_question, read_permission_mode,
    transcript_paths,
)
from watchtower.usage import context_window, scan_usage
from watchtower.windows import WINDOWS, load_names, load_pairs, save_pairs, window_exists, window_title


def status_age(data: dict, now: float) -> float | None:
    written = data.get("statusUpdatedAt") or data.get("updatedAt")
    written = written / 1000 if isinstance(written, (int, float)) else data.get("fileMtime")
    return now - written if isinstance(written, (int, float)) else None


def effective_status(data: dict, now: float, live: bool) -> str:
    """The session's status, dropped to idle once nothing backs it up.

    Claude Code writes `busy` or `shell` and then goes quiet: it records a status
    when the status changes, not on a timer. So an eight-minute-old `busy` may
    mean eight minutes of hard thinking, or it may mean the session wrote one
    line at startup — while sourcing its shell snapshot — and has sat at the
    prompt ever since. Age alone cannot tell those apart, and guessing from age
    alone reports every long turn as ready.

    `live` carries the second opinion: is the process burning CPU, is its
    transcript still growing. Only when the reading is old *and* nothing else
    says the session is working does the panel stop believing it.

    Only the active states expire. `waiting` means blocked on you and stays put
    for as long as you take, which is not the same as going stale.

    A session that writes no status at all is a separate case — see
    inferred_status.
    """
    if not data.get("status"):
        return inferred_status(live)
    status = data["status"]
    if status not in ACTIVE_STATUSES or live:
        return status
    age = status_age(data, now)
    return "idle" if age is not None and age > STATUS_TTL else status


def inferred_status(live: bool) -> str:
    """The state of a session that never reports one.

    Not every entry point keeps the status field current. The VS Code extension
    (`entrypoint: claude-vscode`) writes its session file once at startup and
    never adds a status to it, so taking the absent field at face value pins
    such a session to `idle` — which the panel shows as "Waiting" — for its
    whole life, working turns included.

    The liveness signals are the only reading left, and they separate the two
    states that matter here: CPU burning or a transcript still growing means the
    turn is running, and nothing means the turn is over. `waiting` cannot be
    reached this way, so a permission prompt in such a session reads as done
    rather than as blocked on you; that is the same as before this inference.
    """
    return "busy" if live else "idle"


def is_headless(data: dict) -> bool:
    """Is this session file a headless run rather than a session to watch."""
    return str(data.get("entrypoint") or "").startswith("sdk-")


class SessionStore:
    """Polls the session files and keeps a state trace for each session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._history: dict[str, list[list]] = {}
        self._branch_cache: dict[str, tuple[float, str | None]] = {}
        self._root_cache: dict[str, tuple[float, str | None]] = {}
        self._activity_cache: dict[str, tuple[float, dict | None]] = {}
        self._mode_cache: dict[str, tuple[float, str | None]] = {}
        self._title_cache: dict[str, tuple[float, str | None]] = {}
        # session id -> (read at, transcript mtime then, the question or None)
        self._question_cache: dict[str, tuple[float, float | None, dict | None]] = {}
        self._first_seen: dict[str, float] = {}
        # pid -> (sampled at, cpu seconds then, was it working)
        self._cpu: dict[int, tuple[float, float, bool]] = {}
        # session id -> when its liveness signals last said it was working
        self._alive_at: dict[str, float] = {}
        self._transcript_cache: dict[str, tuple[float, float | None]] = {}
        # session id -> (read at, has it said anything). True is kept for good:
        # a conversation does not become unsaid.
        self._spoken_cache: dict[str, tuple[float, bool]] = {}
        # The set of subagents, cached on the folder's mtime, and the verdict on
        # each one, cached by id. They are separate because they go stale for
        # different reasons — see _agents.
        self._agents_dir_cache: dict[str, tuple[float, float | None]] = {}
        self._agents_cache: dict[str, tuple[float, dict | None]] = {}
        self._agents_done: dict[str, str] = {}

    # --- reading

    def _read_files(self) -> list[dict]:
        live: list[dict] = []
        try:
            files = sorted(SESSION_DIR.glob("*.json"))
        except OSError:
            files = []  # nested sessions are found without it — read on
        for path in files:
            try:
                data = json.loads(path.read_text())
                # The last resort for judging how fresh the status is.
                data["fileMtime"] = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            pid = data.get("pid")
            if not isinstance(pid, int):
                continue
            if is_own_errand(pid) or is_headless(data):
                continue  # a headless run, not a session to watch
            actual = proc_starttime(pid)
            recorded = data.get("procStart")
            if actual is None or proc_gone(pid):
                continue  # process is gone, or has ended and not been reaped
            if recorded not in (None, "", actual):
                continue  # this pid belongs to something else now
            live.append(data)
        # Sessions started from inside another one write no file of their own, so
        # they are read off /proc and appended here, as ordinary sessions.
        known = {data["pid"] for data in live}
        live.extend(child_sessions(known, peer_protocols(live)))
        return live

    def _branch(self, cwd: str) -> str | None:
        now = time.time()
        hit = self._branch_cache.get(cwd)
        if hit and now - hit[0] < 15:
            return hit[1]
        value = git_branch(cwd)
        self._branch_cache[cwd] = (now, value)
        return value

    def _repo_root(self, cwd: str) -> str | None:
        # A working folder does not move under a running session, so this is
        # cached longer than the branch, which changes whenever it checks out.
        now = time.time()
        hit = self._root_cache.get(cwd)
        if hit and now - hit[0] < 60:
            return hit[1]
        value = git_root(cwd)
        self._root_cache[cwd] = (now, value)
        return value

    def _activity(self, session_id: str, cwd: str) -> dict | None:
        now = time.time()
        hit = self._activity_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        value = last_activity(session_id, cwd)
        self._activity_cache[session_id] = (now, value)
        return value

    def _mode(self, session_id: str, cwd: str) -> str | None:
        # Read less often than the poll: it changes only when the transcript's
        # metadata block is re-appended, and finding it means walking back
        # through the tail of the transcript.
        now = time.time()
        hit = self._mode_cache.get(session_id)
        if hit and now - hit[0] < 10:
            return hit[1]
        value = read_permission_mode(session_id, cwd)
        self._mode_cache[session_id] = (now, value)
        return value

    def _title(self, session_id: str, cwd: str) -> str | None:
        # Rarer still than the mode: Claude renames a conversation when its
        # subject moves, which is minutes apart at best, and the reading costs
        # the same walk back through the tail.
        now = time.time()
        hit = self._title_cache.get(session_id)
        if hit and now - hit[0] < 20:
            return hit[1]
        value = read_ai_title(session_id, cwd)
        self._title_cache[session_id] = (now, value)
        return value

    def _context(self, session_id: str, cwd: str) -> dict | None:
        """How much of the model's window this conversation is carrying.

        No cache of its own, unlike everything else here: `scan_usage` is already
        incremental — it remembers where it stopped in each transcript and reads
        only what has been appended since — so a second call costs a `stat` and
        nothing else. Measured on this machine: 33–76 ms to read a 2.6–7.4 MB
        transcript the first time, and 0.03–0.1 ms every time after. A cache on
        top of that would only add a second staleness window to reason about.

        The figure is the last request's total input — fresh tokens, cache reads
        and cache writes together, which is everything the model was carrying
        when it last answered. It is what `/context` reports and what compaction
        reduces, and it drops on its own after a compaction because the next
        assistant turn carries less.
        """
        if not cwd:
            return None
        for path in transcript_paths(session_id, cwd):
            if not path.exists():
                continue
            held = scan_usage(path)
            if not held or not held.get("context"):
                break
            window = context_window(held.get("contextModel") or "")
            return {
                "tokens": held["context"],
                "window": window,
                "share": min(1.0, held["context"] / window) if window else 0.0,
                "model": held.get("contextModel"),
            }
        return None

    def _question(self, session_id: str, cwd: str, now: float) -> dict | None:
        """The question this session is blocked on, re-read only when it could
        have changed.

        Unlike the mode and the title, this one is polled for its own sake — the
        panel wants to know the moment a question goes up — so it cannot sit
        behind a twenty-second cache. The transcript's mtime is what makes that
        affordable: a session that has written nothing since the last read cannot
        have asked or answered anything, and the walk is skipped.
        """
        touched = self._transcript_touched(session_id, cwd, now)
        hit = self._question_cache.get(session_id)
        if hit and hit[1] == touched and now - hit[0] < 30:
            return hit[2]
        value = read_pending_question(session_id, cwd)
        self._question_cache[session_id] = (now, touched, value)
        return value

    def _agents_touched(self, session_id: str, cwd: str, now: float) -> float | None:
        """When a subagent was last spawned or cleared away."""
        hit = self._agents_dir_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        folder = subagent_dir(session_id, cwd)
        try:
            at = folder.stat().st_mtime if folder else None
        except OSError:
            at = None
        self._agents_dir_cache[session_id] = (now, at)
        return at

    def _agents(self, session_id: str, cwd: str, now: float) -> dict | None:
        """How many subagents this session is running, for its row.

        Two things go stale here at different rates, and caching them together
        would be wrong in a way that is hard to see. A folder's mtime moves when
        an entry is created, so it says exactly when a subagent was spawned — but
        an agent *finishing* only appends to its own file, and leaves the folder
        untouched. A count held against the folder's mtime would freeze at
        whatever it read when the last agent started.

        So the folder's mtime gates nothing but the short cache below, and the
        verdicts are re-read on the same four-second beat as the activity line.
        What makes that affordable is that `done` is final: a finished agent is
        remembered as finished and never opened again, so the cost is one tail
        read per agent still going.
        """
        touched = self._agents_touched(session_id, cwd, now)
        if touched is None:
            return None
        hit = self._agents_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        found = []
        for item in list_subagents(session_id, cwd):
            settled = self._agents_done.get(item["agentId"])
            if settled:
                item = {**item, "state": settled}
            elif item["state"] == "done":
                self._agents_done[item["agentId"]] = "done"
            found.append(item)
        value = None
        if found:
            running = [item for item in found if item["state"] == "running"]
            first = (running or found)[0]
            named = ": ".join(part for part in
                              (first["agentType"], first["description"]) if part)
            value = {"running": len(running), "total": len(found), "newest": named}
            # Enough to draw the strip over the composer and open what a chip
            # stands for. Only the running ones: a finished agent is reachable
            # from the tool row that started it, and a session that has fanned
            # out twenty would otherwise bury the box you are typing in. Absent
            # rather than empty, so it does not travel on every poll.
            if running:
                value["live"] = [{"agentId": item["agentId"],
                                  "agentType": item["agentType"],
                                  "description": item["description"]}
                                 for item in running[:AGENT_DOCK_MAX]]
        self._agents_cache[session_id] = (now, value)
        return value

    def _burning_cpu(self, pid: int, now: float) -> bool:
        """Is this process actually doing something, judged over a few seconds?"""
        current = cpu_seconds(pid)
        if current is None:
            return False
        at, before, verdict = self._cpu.get(pid, (0.0, current, True))
        if now - at >= CPU_WINDOW:
            # A session first seen gets the benefit of the doubt until there are
            # two readings to compare.
            verdict = (current - before) / (now - at) > WORKING_CPU if at else True
            self._cpu[pid] = (now, current, verdict)
        return verdict

    def _transcript_touched(self, session_id: str, cwd: str, now: float) -> float | None:
        """When the transcript last grew — a working session appends constantly."""
        hit = self._transcript_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        newest = None
        for path in transcript_paths(session_id, cwd):
            try:
                newest = max(newest or 0.0, path.stat().st_mtime)
            except OSError:
                continue
        self._transcript_cache[session_id] = (now, newest)
        return newest

    def _spoken(self, session_id: str, cwd: str, now: float) -> bool:
        """Has this session said anything yet — cached like the rest.

        The rows care because taking a session over reads differently for a
        session with a conversation behind it than for one without.
        """
        hit = self._spoken_cache.get(session_id)
        if hit and (hit[1] or now - hit[0] < 4):
            return hit[1]
        said = has_conversation(session_id, cwd)
        self._spoken_cache[session_id] = (now, said)
        return said

    def _looks_alive(self, data: dict, now: float) -> bool:
        pid = data.get("pid")
        if isinstance(pid, int) and self._burning_cpu(pid, now):
            return True
        session_id = data.get("sessionId") or str(pid)
        touched = self._transcript_touched(session_id, data.get("cwd") or "", now)
        return touched is not None and now - touched < TRANSCRIPT_WINDOW

    def _alive(self, data: dict, session_id: str, now: float) -> bool:
        """Liveness, held across the quiet gaps inside a working turn.

        Both signals _looks_alive reads are bursty, and neither is absent only at
        the end of a turn — see LIVENESS_GRACE. Remembering the last time they
        agreed is what keeps a working session from blinking to "Waiting" and back
        while it sits on an API call.
        """
        if self._looks_alive(data, now):
            self._alive_at[session_id] = now
            return True
        last = self._alive_at.get(session_id)
        return last is not None and now - last < LIVENESS_GRACE

    # --- sampling loop

    def sample(self) -> None:
        now = time.time()
        seen: set[str] = set()
        for data in self._read_files():
            session_id = data.get("sessionId") or str(data.get("pid"))
            seen.add(session_id)
            status = effective_status(data, now, self._alive(data, session_id, now))
            with self._lock:
                self._first_seen.setdefault(session_id, now)
                trace = self._history.setdefault(session_id, [])
                if not trace or trace[-1][1] != status:
                    trace.append([now, status])
                cutoff = now - HISTORY_SECONDS
                while len(trace) > 1 and trace[1][0] < cutoff:
                    trace.pop(0)
                # Store the status the trace agrees with, not the raw reading.
                self._sessions[session_id] = {**data, "status": status, "seenAt": now}
        with self._lock:
            for session_id in list(self._sessions):
                if session_id not in seen:
                    # Keep it around briefly so the UI can show it closing out.
                    if now - self._sessions[session_id].get("seenAt", now) > 20:
                        gone = self._sessions.pop(session_id, None) or {}
                        self._history.pop(session_id, None)
                        self._first_seen.pop(session_id, None)
                        self._transcript_cache.pop(session_id, None)
                        self._spoken_cache.pop(session_id, None)
                        self._mode_cache.pop(session_id, None)
                        self._title_cache.pop(session_id, None)
                        self._question_cache.pop(session_id, None)
                        self._alive_at.pop(session_id, None)
                        self._cpu.pop(gone.get("pid"), None)

    def run_forever(self) -> None:
        while True:
            try:
                self.sample()
            except Exception:  # a sampler crash must not take the panel down
                pass
            time.sleep(SAMPLE_INTERVAL)

    # --- presenting

    def snapshot(self) -> dict:
        now = time.time()
        pairs = load_pairs()
        names = load_names()
        kept = kept_rows()
        with self._lock:
            raw = list(self._sessions.values())
            history = {k: list(v) for k, v in self._history.items()}

        out = []
        for data in raw:
            session_id = data.get("sessionId") or str(data.get("pid"))
            pid = data.get("pid")
            cwd = data.get("cwd") or ""
            alive = session_alive(data)
            status = data.get("status") or "idle"
            if not alive:
                status = "offline"
            chain = ancestors(pid) if isinstance(pid, int) else []
            agents_now = self._agents(session_id, cwd, now) if cwd else None
            session = {
                "sessionId": session_id,
                "pid": pid,
                # A name you typed wins over the one the session reports.
                "name": names.get(session_id) or data.get("name") or f"session {pid}",
                "givenName": names.get(session_id),
                "defaultName": data.get("name") or f"session {pid}",
                "cwd": cwd,
                "folder": os.path.basename(cwd) or cwd,
                "status": status,
                "kind": data.get("kind") or "interactive",
                "version": data.get("version"),
                "startedAt": (data.get("startedAt") or 0) / 1000 or self._first_seen.get(session_id, now),
                "statusSince": self._status_since(history.get(session_id), now),
                "branch": self._branch(cwd) if cwd else None,
                # The Git tab needs to tell "not a repository" apart from "a
                # repository whose HEAD would not read", which a null branch
                # cannot. Everything git runs against this root, not the cwd.
                "repoRoot": self._repo_root(cwd) if cwd else None,
                "activity": self._activity(session_id, cwd) if cwd else None,
                # How many subagents it has going. A session that has fanned out
                # six agents reads as one session doing one thing without this.
                **({"agents": agents_now} if agents_now else {}),
                # Whether there is a conversation behind it at all. Taking over a
                # session that has never spoken carries nothing over, and the
                # dialog says so rather than pretending otherwise.
                "spoken": self._spoken(session_id, cwd, now) if cwd else False,
                # The mode as of this session's last turn, and what was asked for
                # at the panel since — see read_permission_mode for why the two
                # can disagree for a while.
                "permissionMode": self._mode(session_id, cwd) if cwd else None,
                # What Claude says this session is about, for the line under its
                # name — the detail pane reads the same thing off the transcript.
                "title": self._title(session_id, cwd) if cwd else None,
                # How full the conversation is. The detail header draws it, and
                # offers to compact once it is past halfway. See _context.
                "context": self._context(session_id, cwd) if cwd else None,
                # The multiple-choice question this session is blocked on, so a
                # row can show what is being asked rather than only that
                # something is. See read_pending_question.
                "question": self._question(session_id, cwd, now) if cwd and alive else None,
                # The pane holding this session's prompt open, and so whether its
                # question can be answered from here at all. A session under a
                # bare terminal emulator has no pane and reads as null, which is
                # what the card says out loud rather than offering a dead button.
                "trace": self._trace(history.get(session_id), now),
                "alive": alive,
                # Whether a message would land over its socket right now. It is
                # no longer a gate on the composer — a message for a session that
                # is not listening is held, and the session started back up if
                # nothing is running it, see deliver_later — so what this drives
                # is wording rather than whether there is a box.
                "canSay": session_listening(data),
                "ancestors": chain,
                # Set for a nested session: the session it was started from. Its
                # own process parent is usually the terminal, not that session,
                # so the chain above does not lead back to it.
                "parentPid": data.get("parentPid"),
                # The pty is what tells two tabs of one terminal apart, and what
                # the window probe writes to.
                "tty": session_tty(pid) if isinstance(pid, int) else None,
                # Enough of the chain to spot a multiplexer or an ssh hop.
                "host": [proc_name(p) for p in chain[:5]],
                # Kept: its row outlives its process. Pinned: that row was
                # written down, so it outlives the panel too.
                "kept": session_id in kept,
                "pinned": bool((kept.get(session_id) or {}).get("pinned")),
            }
            # Keep what a kept row will need once the process is gone. Written
            # back now and then rather than every second — it is a poll loop.
            if session_id in kept:
                held = kept[session_id]
                fresh = {
                    "sessionId": session_id, "name": session["defaultName"], "cwd": cwd,
                    "startedAt": session["startedAt"], "lastSeen": now,
                    "version": session["version"], "kind": session["kind"],
                }
                if any(held.get(k) != fresh[k] for k in ("name", "cwd", "version")) \
                        or now - (held.get("lastSeen") or 0) > 30:
                    refresh_row(session_id, fresh)
            paired = (pairs.get(session_id) or {}).get("id")
            if paired and window_exists(paired):
                session["window"] = {
                    "id": paired,
                    "confidence": "paired" if pairs[session_id]["how"] == "picked" else "identified",
                    "title": window_title(paired),
                }
            else:
                if paired:
                    pairs.pop(session_id, None)
                    save_pairs(pairs)
                match = WINDOWS.match(session)
                session["window"] = match
            out.append(session)

        # A kept session whose process has gone still gets a row: same id, same
        # transcript, no pid. It is `stopped` rather than `offline` — nothing has
        # been lost, it is simply not running, and it can be started back up.
        live_ids = {s["sessionId"] for s in out}
        for session_id, entry in kept.items():
            if session_id in live_ids:
                continue
            cwd = entry.get("cwd") or ""
            agents_now = self._agents(session_id, cwd, now) if cwd else None
            out.append({
                "sessionId": session_id,
                "pid": None,
                "name": names.get(session_id) or entry.get("name") or "kept session",
                "givenName": names.get(session_id),
                "defaultName": entry.get("name") or "kept session",
                "cwd": cwd,
                "folder": os.path.basename(cwd) or cwd,
                # A held session is running — there is a process, it is
                # answering or waiting for you, and the row should read the way
                # any other running session's does. `stopped` is for a kept row
                # with nothing behind it at all.
                #
                # A standing ask beats busy. The turn *is* still running, but it
                # is parked on a prompt nobody outside this panel can answer, so
                # calling it "working" hid the one session on the list that could
                # not go on without you. Saying `waiting` is what puts it in the
                # amber band, at the top of the list, on the favicon, and through
                # the notification.
                "status": ("waiting" if session_id in OWNED_ASK
                           else "busy" if session_id in OWNED_BUSY
                           else "idle") if owned_running(session_id) else "stopped",
                "kind": entry.get("kind") or "interactive",
                "version": entry.get("version"),
                "startedAt": entry.get("startedAt") or entry.get("lastSeen") or now,
                "statusSince": entry.get("lastSeen") or now,
                "branch": self._branch(cwd) if cwd else None,
                "repoRoot": self._repo_root(cwd) if cwd else None,
                "activity": self._activity(session_id, cwd) if cwd else None,
                # A stopped session's agents are stopped with it, and the count
                # reads as whatever its files say, which is the truth.
                **({"agents": agents_now} if agents_now else {}),
                "spoken": self._spoken(session_id, cwd, now) if cwd else False,
                # For a stopped session these are the mode it was last in and
                # the last thing it was working on.
                "permissionMode": self._mode(session_id, cwd) if cwd else None,
                "title": self._title(session_id, cwd) if cwd else None,
                # How full the conversation is. The detail header draws it, and
                # offers to compact once it is past halfway. See _context.
                "context": self._context(session_id, cwd) if cwd else None,
                # A stopped session is not waiting for an answer, however its
                # transcript ends.
                "question": None,
                "trace": [],
                "alive": False,
                "canSay": False,
                # Starting runs a command here, so it follows the same gate as sending.
                "canStart": config.SAY_ENABLED,
                "ancestors": [],
                "tty": None,
                "host": [],
                "kept": True,
                "pinned": bool(entry.get("pinned")),
                "window": None,
            })

        # Whose child a nested session is, said by name rather than by pid — the
        # row has no room for a number nobody recognises. Resolved here because it
        # takes every session to answer, and a parent may not be on the list at
        # all: it can have closed while its child kept running.
        named = {s["pid"]: s["name"] for s in out if s.get("pid")}
        for session in out:
            session["parentName"] = named.get(session.get("parentPid"))

        order = {"waiting": 0, "busy": 1, "shell": 2, "idle": 3, "offline": 4, "stopped": 5}
        # State decides which band a row sits in; inside a band the order is the
        # session's own identity — when it started, then its id — and never how
        # long it has been in that state. A row therefore moves only when its
        # state visibly changes, and comes back to the same slot afterwards. The
        # id makes the key total, so two sessions never swap on the strength of
        # the order they happened to be discovered in.
        out.sort(key=lambda s: (order.get(s["status"], 6), s["startedAt"] or 0, s["sessionId"]))
        return {
            "now": now,
            "sessions": out,
            "historySeconds": HISTORY_SECONDS,
            "canFocus": WINDOWS.available(),
            "canSend": config.SAY_ENABLED,
            "canPickFolder": can_pick_folder(),
            # Keyed by session rather than folded into each row: only the
            # handful the panel has ever run a turn for have anything to say.
            "owned": owned_state(),
            # Where a cleared session went. See owned_rekey: clearing gives a
            # session a new id, and the browser is holding the old one.
            "moved": owned_moved(),
        }

    def forget(self, session_id: str) -> None:
        """Drop a session the panel has just ended, without the usual grace.

        A session that vanishes is held for a moment so the list can show it
        closing out. That is right when something else ended it and wrong when
        the panel did: the row is wanted back immediately, as the kept row it has
        become, because that is the state the next thing you do acts on.
        """
        with self._lock:
            self._sessions.pop(session_id, None)
            self._transcript_cache.pop(session_id, None)
            self._mode_cache.pop(session_id, None)
            self._question_cache.pop(session_id, None)

    def raw(self, session_id: str) -> dict | None:
        """The session file as last read — including the fields the UI never sees."""
        with self._lock:
            data = self._sessions.get(session_id)
            return dict(data) if data else None

    @staticmethod
    def _status_since(trace: list[list] | None, now: float) -> float:
        return trace[-1][0] if trace else now

    @staticmethod
    def _trace(trace: list[list] | None, now: float) -> list[dict]:
        if not trace:
            return []
        spans = []
        for index, (start, status) in enumerate(trace):
            end = trace[index + 1][0] if index + 1 < len(trace) else now
            spans.append({"from": start, "to": end, "status": status})
        return spans


STORE = SessionStore()

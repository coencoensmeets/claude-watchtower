#!/usr/bin/env python3
"""claude-watchtower — a live panel for every Claude Code session on this machine.

Reads ~/.claude/sessions/*.json, which Claude Code keeps current with one file
per running session (pid, name, cwd, status). Status is one of:

    busy     working right now
    waiting  blocked on you — a question or a permission prompt
    shell    running a foreground shell command
    idle     finished, waiting for your next prompt

A busy or shell reading is only believed while the session keeps refreshing it;
see effective_status. Some sessions — the VS Code extension among them — write
no status at all, and are read from their liveness instead. A session started
from inside another one writes no file at all; the panel builds one for it out
of /proc — see child_session.

Serves a small web UI and can raise the terminal or editor window that owns a
session, using xdotool on X11.

Standard library only. No install step.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOME = Path.home()
# Override to point at a fixture directory when trying out the panel's states.
# CLAUDE_BUSY_UI_SESSION_DIR is the pre-rename name, still honoured.
SESSION_DIR = Path(
    os.environ.get("CLAUDE_WATCHTOWER_SESSION_DIR")
    or os.environ.get("CLAUDE_BUSY_UI_SESSION_DIR")
    or HOME / ".claude" / "sessions"
)
PROJECT_DIR = HOME / ".claude" / "projects"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PAIR_FILE = HOME / ".config" / "claude-watchtower" / "pairs.json"
# Names you have given sessions yourself, keyed by session id.
NAME_FILE = HOME / ".config" / "claude-watchtower" / "names.json"
MAX_NAME = 80
# Session ids are never reused, so the file would grow forever without a cap.
MAX_NAMES = 500
# Sessions you asked the panel to keep after their process is gone. Only pinned
# rows are written down; the rest are kept in memory for as long as the panel
# runs. See "kept rows".
STICKY_FILE = HOME / ".config" / "claude-watchtower" / "sticky.json"
MAX_STICKY = 100
# The in-memory tier has the same cap, for the same reason: a panel left running
# for a month should not accumulate rows without end.
MAX_KEPT = 100
OWNED_FILE = HOME / ".config" / "claude-watchtower" / "owned.json"
MAX_OWNED = 200

# How long a state trace remembers, and how often we sample.
HISTORY_SECONDS = 30 * 60
SAMPLE_INTERVAL = 1.0

# How stale a session file may be before the session behind it counts as gone.
# One number for everyone who asks — the row that says "offline", the gate on
# sending, and the deliverer deciding whether to start it back up.
LIVE_SECONDS = 15.0

KNOWN_STATUSES = ("waiting", "busy", "shell", "idle")

# States that mean work is happening right now. Claude Code writes the status
# only when it changes, so an old reading is not proof of anything on its own —
# see effective_status.
ACTIVE_STATUSES = ("busy", "shell")
# Short on purpose. The age check is not what keeps a working session on screen —
# the liveness signals below do that — so this only has to be long enough not to
# flap between two of them.
STATUS_TTL = 15.0
# A transcript that grew this recently counts as a session still at work. Kept
# tight: the last thing a finished turn does is write to the transcript, so a
# long window would hold every session at "working" well past the end of its turn.
TRANSCRIPT_WINDOW = 10.0
# A working session burns a good fraction of a core; an idle one ticks along at
# well under a hundredth of one, so the gap between them is wide.
WORKING_CPU = 0.02
CPU_WINDOW = 5.0
# A working turn is not steady activity. While a request is out to the API, or a
# tool call is blocking on something slow, the process burns almost no CPU and
# appends nothing to its transcript — both liveness signals go quiet mid-turn.
# Read literally, that gap expires the session's `busy` reading and shows a
# working session as "Waiting" for a few seconds until the next append puts it
# back. So a reading of "alive" is remembered for this long after the signals
# fall silent, which is longer than those gaps and still short enough that a
# session whose status went stale for real settles within the minute.
LIVENESS_GRACE = 45.0


def cpu_seconds(pid: int) -> float | None:
    """CPU this process has burned so far — utime plus stime, in seconds."""
    fields = read_stat(pid)
    if not fields or len(fields) < 15:
        return None
    try:
        return (int(fields[13]) + int(fields[14])) / CLK_TCK
    except ValueError:
        return None


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


# ----------------------------------------------------------------- proc helpers


def clock_ticks() -> float:
    try:
        return os.sysconf("SC_CLK_TCK") or 100.0
    except (ValueError, OSError):
        return 100.0


CLK_TCK = clock_ticks()


def read_stat(pid: int) -> list[str] | None:
    """Fields of /proc/<pid>/stat, with the comm field's spaces neutralised."""
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text()
    except (OSError, ValueError):
        return None
    close = raw.rfind(")")
    if close == -1:
        return None
    # Field 1 is pid, field 2 is comm (parenthesised); the rest are space-split.
    return [raw[: raw.find("(")].strip(), raw[raw.find("(") + 1 : close]] + raw[
        close + 2 :
    ].split()


def proc_starttime(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat — identifies a pid across reuse."""
    fields = read_stat(pid)
    if not fields or len(fields) < 22:
        return None
    return fields[21]


def proc_gone(pid: int) -> bool:
    """Has this process finished, whether or not anybody has noticed?

    A process that has exited but has not been waited on keeps its entry under
    /proc — so "is there a stat file" answers yes indefinitely, and so does
    `kill -0`. It is a zombie: field 3 of stat is `Z`, and there is nothing left
    of it to run a conversation. Whoever started it has to reap it, which a shell
    does at once and a harness that is asleep may not do for minutes.

    Getting this wrong is how a session that has ended goes on reporting itself
    alive, which keeps the row out of the state the panel can act on.
    """
    fields = read_stat(pid)
    if not fields or len(fields) < 3:
        return True
    return fields[2].startswith("Z")


def parent_of(pid: int) -> int | None:
    fields = read_stat(pid)
    if not fields or len(fields) < 4:
        return None
    try:
        return int(fields[3])
    except ValueError:
        return None


def ancestors(pid: int, limit: int = 12) -> list[int]:
    """The pid's ancestor chain, nearest first, stopping before init."""
    chain: list[int] = []
    current = parent_of(pid)
    while current and current > 1 and len(chain) < limit:
        chain.append(current)
        current = parent_of(current)
    return chain


def proc_name(pid: int) -> str:
    fields = read_stat(pid)
    return fields[1] if fields and len(fields) > 1 else ""


def session_tty(pid: int) -> str | None:
    """The pty a session is attached to, as a device path, or None.

    Field 7 of /proc/<pid>/stat is the controlling terminal's device number:
    major 136 is a pty, and the minor is its /dev/pts entry. This is what tells
    two tabs of one terminal apart — they share a process, and therefore a
    window pid, but never a pty. Reading fd 0 is the fallback for a session
    that has moved its own standard input somewhere else.
    """
    fields = read_stat(pid)
    if fields and len(fields) >= 7:
        try:
            device = int(fields[6])
        except ValueError:
            device = 0
        if device > 0 and (device >> 8) & 0xFFF == 136:
            path = f"/dev/pts/{(device & 0xFF) | ((device >> 20) << 8)}"
            if os.path.exists(path):
                return path
    for fd in (0, 1, 2):
        try:
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if link.startswith("/dev/pts/"):
            return link
    return None


# --------------------------------------------- sessions that write no own file


# Claude Code's messaging sockets, one per session, named after the pid. A
# nested session opens one of these and writes a key file beside the session
# files, but no session file of its own — see child_session.
SOCK_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "cc-socks"
# What a nested session calls itself in the session file the panel makes for it.
CHILD_KIND = "child"


def boot_time() -> float:
    """When the machine came up, in epoch seconds.

    /proc/<pid>/stat counts a process's start from here, so this is what turns
    field 22 into a wall-clock time.
    """
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


BOOT_TIME = boot_time()


def proc_started_at(pid: int) -> float | None:
    """Epoch seconds a process started, or None if that cannot be worked out."""
    start = proc_starttime(pid)
    if start is None or not BOOT_TIME:
        return None
    try:
        return BOOT_TIME + int(start) / CLK_TCK
    except ValueError:
        return None


# A process's environment is fixed at exec, so it is read once. Keyed by
# starttime as well as pid, so a reused number cannot hand back the environment
# of whoever held it before.
ENVIRON_CACHE: dict[int, tuple[str, dict[str, str]]] = {}
MAX_ENVIRONS = 200


def proc_environ(pid: int) -> dict[str, str]:
    """The environment a process was started with, cached."""
    start = proc_starttime(pid)
    if start is None:
        ENVIRON_CACHE.pop(pid, None)
        return {}
    hit = ENVIRON_CACHE.get(pid)
    if hit and hit[0] == start:
        return hit[1]
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        name, sep, value = chunk.decode("utf-8", "replace").partition("=")
        if sep:
            env[name] = value
    if len(ENVIRON_CACHE) > MAX_ENVIRONS:
        ENVIRON_CACHE.clear()
    ENVIRON_CACHE[pid] = (start, env)
    return env


def stdin_is_terminal(pid: int) -> bool:
    """Is this process's standard input a pty — is there someone at it.

    Standard input rather than the controlling terminal, which cannot tell the
    two apart: a `claude -p` errand started from inside a session inherits that
    session's terminal, and only its fd 0 — a pipe, or /dev/null — gives it away.
    """
    try:
        return os.readlink(f"/proc/{pid}/fd/0").startswith("/dev/pts/")
    except OSError:
        return False


def child_session(pid: int, protocols: dict[str, int]) -> dict | None:
    """The session file a nested session would have written, read off /proc.

    Start `claude` from inside a session — from its own shell, or from a terminal
    that inherited its environment — and the new session marks itself a child
    (`CLAUDE_CODE_CHILD_SESSION=1`) and writes no session file at all: only a
    top-level session does that. It is a session in every other respect, with its
    own process, its own terminal and its own turn, so leaving it out hides real
    work being done on this machine.

    Everything the panel reads out of a session file is on /proc instead — pid,
    working folder, start, build, and the parent it names in CLAUDE_PID. Two
    things are genuinely absent rather than merely elsewhere. A child publishes
    no session id anything outside the process can read, so it is given one built
    from its pid and starttime, unique for as long as it runs. And nothing is
    written for it under ~/.claude/projects, so it has no transcript, and with it
    no title, no permission mode and no chat — the panel shows what it can and
    says so. Its status is read from its liveness, the way a session that reports
    none is; see inferred_status.

    Returns None for a pid that is not a nested session, headless errands
    included: a `claude -p` run from inside a session is a child too, but it is
    one turn of someone else's work rather than a session you could type at.
    """
    env = proc_environ(pid)
    if env.get("CLAUDE_CODE_CHILD_SESSION") != "1":
        return None
    start = proc_starttime(pid)
    if start is None:
        return None  # gone between the scan and the read
    if not stdin_is_terminal(pid):
        return None
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None
    # The build is in the path it was launched from; the session file's `version`
    # field is the same string.
    version = os.path.basename(env.get("CLAUDE_CODE_EXECPATH") or "") or None
    parent = env.get("CLAUDE_PID") or ""
    folder = os.path.basename(cwd) or cwd
    data = {
        "pid": pid,
        "sessionId": f"child:{pid}:{start}",
        "procStart": start,
        "cwd": cwd,
        "name": f"{folder} (nested)",
        "nameSource": "derived",
        "kind": CHILD_KIND,
        "entrypoint": env.get("CLAUDE_CODE_ENTRYPOINT"),
        "version": version,
        "parentPid": int(parent) if parent.isdigit() else None,
        "startedAt": (proc_started_at(pid) or time.time()) * 1000,
        # No status field on purpose: there is none to read, and an absent one is
        # already understood — inferred_status reads the liveness signals instead.
    }
    sock = SOCK_DIR / f"{pid}.sock"
    if sock.exists():
        data["messagingSocketPath"] = str(sock)
        # A child never states its protocol version, having written no file, but
        # it opened the socket and published the key the protocol needs. A
        # top-level session on the same build states it, and one build speaks one
        # protocol, so that reading stands in. Nothing rides on it being right:
        # say_to_session re-checks the pid and the socket before it writes, and
        # reports a refusal like any other.
        protocol = protocols.get(version or "")
        if protocol is not None:
            data["peerProtocol"] = protocol
    return data


def child_sessions(known: set[int], protocols: dict[str, int]) -> list[dict]:
    """Nested sessions, found by the two things they do leave behind.

    A child writes no session file, but it writes the key file that goes beside
    one and opens a socket named after its pid. Either is enough to turn up a
    candidate pid; child_session decides what it actually is.
    """
    candidates: set[int] = set()
    for directory, suffix in ((SESSION_DIR, ".key"), (SOCK_DIR, ".sock")):
        try:
            entries = list(directory.glob(f"*{suffix}"))
        except OSError:
            continue
        for path in entries:
            head = path.name.split(".", 1)[0]
            if head.isdigit():
                candidates.add(int(head))
    out = []
    for pid in sorted(candidates - known):
        if is_own_errand(pid):
            continue  # the panel's own headless run
        data = child_session(pid, protocols)
        if data:
            out.append(data)
    return out


def peer_protocols(sessions: list[dict]) -> dict[str, int]:
    """Build -> the peer protocol version sessions of that build report."""
    return {
        data["version"]: data["peerProtocol"]
        for data in sessions
        if isinstance(data.get("version"), str)
        and isinstance(data.get("peerProtocol"), int)
    }


def end_process(pid: int, recorded_start: str | None, force: bool) -> tuple[bool, str]:
    """Signal a session's process, refusing if that pid is no longer the session.

    Pids get reused, so the starttime recorded in the session file is checked
    again right before signalling — otherwise a stale panel could kill whatever
    inherited the number.
    """
    if not isinstance(pid, int) or pid <= 1:
        return False, "That session has no process to end"
    actual = proc_starttime(pid)
    if actual is None:
        return False, "That process has already gone"
    if recorded_start not in (None, "", actual):
        return False, "That pid belongs to a different process now"
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return False, "That process has already gone"
    except PermissionError:
        return False, "Not allowed to end that process"
    except OSError as exc:
        return False, str(exc)
    return True, "Session force quit" if force else "Ending the session…"


# --------------------------------------------------------------- sending input


# Claude Code listens on a per-session unix socket — the path is in the session
# file, the directory is mode 0700, and the socket itself 0600, so only this user
# can reach it. Two newline-delimited JSON lines inject a turn: an optional auth
# line, then the message. The protocol is internal, hence PEER_PROTOCOL below.
PEER_PROTOCOL = 1
# Sending is settled once, in main, from the address we bind. Off unless loopback:
# anyone who can POST /api/say can instruct an agent that has tools and a
# checkout, which is a different order of risk from raising a window.
SAY_ENABLED = False


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# A reply, if one comes, arrives within a moment. Nothing is lost by not waiting:
# the message is already written.
SAY_TIMEOUT = 2.0


def peer_token(pid: int) -> str | None:
    """The session's inbox token, if it published one.

    Newer sessions write <pid>.<hash>.key beside the session file; older ones
    don't, and their socket accepts an unauthenticated message instead. Both are
    normal, so a missing key is not an error.
    """
    for path in sorted(SESSION_DIR.glob(f"{pid}.*.key")):
        try:
            raw = path.read_text().strip()
        except OSError:
            continue
        if not raw:
            continue
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            for field in ("peerToken", "token", "key"):
                value = obj.get(field)
                if isinstance(value, str) and value:
                    return value
            continue
        return raw
    return None


def say_to_session(data: dict, text: str) -> tuple[bool, str]:
    """Inject a user turn into a live session over its messaging socket.

    The pid's starttime is re-checked first, exactly as end_process does — the
    socket is named after the pid, so a stale panel must not be able to talk to
    whatever inherited the number.
    """
    text = text.strip()
    if not text:
        return False, "Nothing to send"

    pid = data.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return False, "That session has no process to send to"
    actual = proc_starttime(pid)
    if actual is None:
        return False, "That process has already gone"
    if data.get("procStart") not in (None, "", actual):
        return False, "That pid belongs to a different process now"

    protocol = data.get("peerProtocol")
    if protocol != PEER_PROTOCOL:
        # Rather than guess at a protocol we have not seen, say so and stay read-only.
        return False, f"This session speaks messaging protocol {protocol!r}, which this panel does not know"

    sock_path = data.get("messagingSocketPath")
    if not sock_path:
        # Callers reach this through session_listening, which has already asked —
        # so this is the socket going in the gap between the two, and what
        # happens next is the deliverer holding the message, not a refusal.
        return False, "It has no socket open yet"
    if not Path(sock_path).exists():
        return False, "This session's message socket has gone"

    lines: list[dict] = []
    token = peer_token(pid)
    if token:
        lines.append({"type": "auth", "token": token})
    lines.append({"type": "user", "message": {"role": "user", "content": text}})
    payload = "".join(json.dumps(line) + "\n" for line in lines).encode()

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(SAY_TIMEOUT)
    try:
        conn.connect(sock_path)
        conn.sendall(payload)
    except (OSError, socket.timeout) as exc:
        conn.close()
        return False, f"Could not reach that session: {exc}"

    # Anything sent back is either an auth refusal or a receipt saying the
    # message was held for approval rather than delivered. Silence is the normal
    # case and means it went straight in.
    held = None
    try:
        conn.shutdown(socket.SHUT_WR)
        buf = conn.recv(4096)
        for raw in buf.decode("utf-8", "replace").splitlines():
            if not raw.strip():
                continue
            try:
                note = json.loads(raw)
            except ValueError:
                continue
            if note.get("status") in ("held", "denied", "expired"):
                held = note
    except (OSError, socket.timeout):
        pass
    finally:
        conn.close()

    if held:
        status = held.get("status")
        if status == "denied":
            return False, "That session declined the message"
        if status == "expired":
            return False, "The message expired before it was let through"
        return True, "Sent — waiting to be allowed through at the terminal"
    return True, "Sent"


# -------------------------------------------------------------------- git repo


def git_root(cwd: str) -> str | None:
    """The repository root above cwd, found by looking for .git — no subprocess.

    Returns the working tree root, which is what every git command below runs
    against: a session sitting three directories down would otherwise stage and
    diff relative to the wrong place.
    """
    if not cwd:
        return None
    path = Path(cwd)
    for candidate in [path, *path.parents]:
        git = candidate / ".git"
        # A file rather than a directory means a worktree or a submodule; either
        # way the tree root is still the directory holding it.
        if git.is_dir() or git.is_file():
            return str(candidate)
    return None


def git_branch(cwd: str) -> str | None:
    """Current branch by reading .git/HEAD — no subprocess, no repo lock.

    Kept file-based because every session in the list calls it once a poll;
    shelling out that often would be a subprocess per session per 15 seconds for
    a string that is sitting right there in a file.
    """
    path = Path(cwd)
    for candidate in [path, *path.parents]:
        git = candidate / ".git"
        head = None
        if git.is_dir():
            head = git / "HEAD"
        elif git.is_file():
            try:
                link = git.read_text().strip()
            except OSError:
                return None
            if link.startswith("gitdir:"):
                head = Path(link[7:].strip()) / "HEAD"
        if head and head.exists():
            try:
                text = head.read_text().strip()
            except OSError:
                return None
            if text.startswith("ref: refs/heads/"):
                return text[16:]
            return text[:7] if text else None
    return None


# --------------------------------------------------------------- git commands


# Only these reach the git binary. The panel builds every argument itself — the
# allowlist is here so that a later write path has to add itself deliberately
# rather than inheriting the ability to run anything.
GIT_READ_COMMANDS = frozenset({"status", "log", "rev-parse", "stash", "diff", "remote",
                               "for-each-ref", "check-ref-format"})


def git_run(root: str, args: list[str], timeout: float = 6.0) -> tuple[bool, str]:
    """Run one read-only git command in root and return (ok, stdout).

    --no-optional-locks so that reading a repository never blocks the session
    working in it, and never leaves an index.lock behind if we are killed
    mid-read. No shell: arguments are passed as a list, always.
    """
    if not args or args[0] not in GIT_READ_COMMANDS:
        return False, ""
    if not shutil.which("git"):
        return False, ""
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", root, *args],
            capture_output=True, text=True, timeout=timeout,
            # A repository is not a place to inherit the panel's environment
            # wholesale; a pager or an editor would hang the request.
            env={**os.environ, "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, result.stdout


def parse_status(text: str) -> list[dict]:
    """Parse `status --porcelain=v2 -z` into one entry per path.

    Structured rather than pre-rendered: staging a single file later needs each
    entry's own identity, which a formatted list cannot give back.
    """
    entries: list[dict] = []
    fields = text.split("\0")
    index = 0
    while index < len(fields):
        line = fields[index]
        index += 1
        if not line:
            continue
        kind = line[0]
        if kind in ("1", "2"):
            # A rename or copy record carries one extra field — the similarity
            # score — before the path, so the two shapes are split differently.
            width = 8 if kind == "1" else 9
            parts = line.split(" ", width)
            if len(parts) < width + 1:
                continue
            xy, path = parts[1], parts[width]
            orig = None
            if kind == "2" and index < len(fields):
                # Under -z the source path follows as its own field.
                orig = fields[index]
                index += 1
            entries.append({
                "path": path,
                "origPath": orig,
                "staged": xy[0] if xy[0] != "." else None,
                "unstaged": xy[1] if xy[1] != "." else None,
                "untracked": False,
                "conflicted": False,
            })
        elif kind == "u":
            parts = line.split(" ", 10)
            if len(parts) < 11:
                continue
            entries.append({
                "path": parts[10], "origPath": None,
                "staged": None, "unstaged": None,
                "untracked": False, "conflicted": True,
            })
        elif kind == "?":
            entries.append({
                "path": line[2:], "origPath": None,
                "staged": None, "unstaged": None,
                "untracked": True, "conflicted": False,
            })
    entries.sort(key=lambda e: e["path"])
    return entries


# One record per commit, unit-separated so a subject containing anything at all
# still parses. %x1f is the ASCII unit separator, %x1e the record separator.
LOG_FORMAT = "%H%x1f%h%x1f%P%x1f%an%x1f%at%x1f%D%x1f%s%x1e"


def parse_log(text: str) -> list[dict]:
    """Parse the log format above into commits carrying their parents.

    Parents come along because the lane-drawing a graph needs is a client-side
    job over the real ancestry, not something to scrape out of --graph's ASCII.
    """
    commits = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 7:
            continue
        sha, short, parents, author, when, refs, subject = parts[:7]
        try:
            timestamp = int(when)
        except ValueError:
            timestamp = 0
        commits.append({
            "sha": sha,
            "short": short,
            "parents": parents.split() if parents else [],
            "author": author,
            "at": timestamp,
            "refs": [r.strip() for r in refs.split(",") if r.strip()],
            "subject": subject,
        })
    return commits


# One record per ref: where it is, what it tracks, and when it last moved. The
# full refname comes along because it is the only part that says for certain
# whether a ref is a local branch or a remote one — the short name cannot, since
# `refs/remotes/origin/HEAD` shortens to plain `origin` and a local branch is
# free to have a slash in it. symref marks that pointer, which is not a branch.
REF_FORMAT = ("%(refname)%1f%(refname:short)%1f%(upstream:short)"
              "%1f%(committerdate:unix)%1f%(HEAD)%1f%(symref)")


def read_branches(root: str) -> dict:
    """The branches this repository could be switched to.

    Local ones first, most recently committed first — the order that matters when
    you are moving between two or three branches all week. Then the remote ones
    with no local branch of their own, which is what "check this out" means for a
    branch somebody else pushed.
    """
    out: dict = {"local": [], "remote": []}
    ok, text = git_run(root, ["for-each-ref", "--sort=-committerdate",
                             f"--format={REF_FORMAT}", "refs/heads", "refs/remotes"])
    if not ok:
        return out

    local_names = set()
    remotes = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 6:
            continue
        full, name, upstream, when, head, symref = parts[:6]
        if not name or symref:
            continue          # a remote's default-branch pointer, not a branch
        try:
            at = int(when)
        except ValueError:
            at = 0
        if full.startswith("refs/heads/"):
            local_names.add(name)
            out["local"].append({"name": name, "upstream": upstream or None,
                                 "at": at, "current": head == "*"})
        elif full.startswith("refs/remotes/"):
            remotes.append({"name": name, "at": at})

    for entry in remotes:
        # A remote branch that already has a local counterpart is reachable by
        # that name; listing it twice would only ask which one you meant.
        short = entry["name"].split("/", 1)[1]
        if short not in local_names:
            out["remote"].append({**entry, "short": short})
    return out


def read_git(root: str, log_limit: int = 60) -> dict:
    """Everything the Git tab reads, in one pass over the repository."""
    out: dict = {
        "ok": True, "repoRoot": root, "head": None, "branch": None,
        "upstream": None, "ahead": 0, "behind": 0, "detached": False,
        "files": [], "commits": [], "stashes": 0, "gitAvailable": True,
    }
    if not shutil.which("git"):
        return {**out, "ok": False, "gitAvailable": False,
                "message": "git is not installed, so this tab can only show the branch"}

    ok, status = git_run(root, ["status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z"])
    if not ok:
        return {**out, "ok": False, "message": "Could not read this repository"}

    # The --branch headers come through as ordinary NUL-separated fields.
    for field in status.split("\0"):
        if not field.startswith("# branch."):
            continue
        key, _, value = field[9:].partition(" ")
        if key == "oid":
            out["head"] = None if value == "(initial)" else value
        elif key == "head":
            out["detached"] = value == "(detached)"
            out["branch"] = None if value == "(detached)" else value
        elif key == "upstream":
            out["upstream"] = value
        elif key == "ab":
            for token in value.split():
                try:
                    count = int(token[1:])
                except ValueError:
                    continue
                if token.startswith("+"):
                    out["ahead"] = count
                elif token.startswith("-"):
                    out["behind"] = count

    out["files"] = parse_status(status)

    # --exclude=refs/stash keeps a stash's two internal commits out of what is
    # meant to be history; HEAD is named explicitly so a detached one still
    # appears, since --all only walks refs.
    ok, log = git_run(root, ["log", f"--max-count={log_limit}", "--date-order",
                             "--exclude=refs/stash", "--all", "HEAD",
                             f"--pretty=format:{LOG_FORMAT}"])
    # A repository with no commits yet fails here, and that is not an error.
    out["commits"] = parse_log(log) if ok else []

    ok, stash = git_run(root, ["stash", "list"])
    out["stashes"] = len([line for line in stash.splitlines() if line]) if ok else 0

    # Cheap enough to come along with every reading — one for-each-ref — and the
    # branch menu has to open on the branches that exist now, not the ones that
    # existed when the tab was opened.
    out["branches"] = read_branches(root)
    return out


def read_diff(root: str, path: str, staged: bool) -> dict:
    """One file's diff, as unified text, for the pane the file rows open.

    Untracked files have nothing to diff against, so they are read from disk and
    presented as an all-added patch — which is what the editor shows too.
    """
    out = {"ok": True, "path": path, "staged": bool(staged), "text": "", "binary": False}
    entry = next((f for f in read_status(root) if f["path"] == path), None)
    if entry is None:
        return {**out, "ok": False, "message": "That file no longer has changes"}

    if entry["untracked"]:
        full = Path(root) / path
        # A trailing slash is git reporting a directory it will not look inside —
        # a nested repository or a worktree. There is no patch to draw for one.
        if path.endswith("/") or full.is_dir():
            return {**out, "binary": True,
                    "message": "A directory git reports whole — it does not look inside this one"}
        try:
            body = full.read_text(errors="strict")
        except (OSError, UnicodeDecodeError):
            return {**out, "binary": True, "message": "New file — not text, so there is nothing to show"}
        lines = body.splitlines()
        head = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        return {**out, "text": head + "".join(f"+{line}\n" for line in lines)}

    args = ["diff", "--no-color", "--no-ext-diff", "--find-renames"]
    if staged:
        args.append("--cached")
    ok, text = git_run(root, [*args, "--", path], timeout=10.0)
    if not ok:
        return {**out, "ok": False, "message": "Could not read that diff"}
    if "Binary files" in text.split("@@", 1)[0]:
        return {**out, "binary": True, "message": "Binary file — nothing to show line by line"}
    return {**out, "text": text}


# --------------------------------------------------------------- git writes


# The writing half of the allowlist above. Split from the readers on purpose:
# nothing here runs unless an action below names it, and the panel builds every
# argument itself, so a path from the browser can only ever land after `--`.
GIT_WRITE_COMMANDS = frozenset({"add", "reset", "rm", "restore", "clean", "commit",
                                "push", "pull", "fetch", "stash", "switch"})

# Anything reaching the network gets the long one; an index write is local and
# should never take this long unless a hook is doing the work.
GIT_WRITE_TIMEOUT = 25.0
GIT_NETWORK_TIMEOUT = 180.0

# Two writes at once would race for index.lock and one would fail for a reason
# that has nothing to do with what was asked. The panel's own writes queue up
# here; a session writing at the same time is still git's own lock to report.
GIT_WRITE_LOCK = threading.Lock()


def git_write(root: str, args: list[str], timeout: float = GIT_WRITE_TIMEOUT) -> tuple[bool, str]:
    """Run one writing git command in root and return (ok, what git said).

    Unlike git_run this wants the index lock — that is the point of it — so the
    optional-locks flags are gone. Every interactive door git might open is shut:
    no terminal prompt, no askpass helper, no editor. A push that needs a
    passphrase nobody can type therefore fails with a message instead of hanging
    until the timeout.
    """
    if not args or args[0] not in GIT_WRITE_COMMANDS:
        return False, "That is not something this panel runs"
    if not shutil.which("git"):
        return False, "git is not installed"
    try:
        with GIT_WRITE_LOCK:
            result = subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0",
                     "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GIT_EDITOR": "true",
                     "LC_ALL": "C.UTF-8"},
            )
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} gave up after {int(timeout)}s"
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run git: {error}"
    # A failure keeps the remote's own words: a hook that rejected a push says why
    # on `remote:` lines, and dropping them as noise would drop the reason with
    # them. On the way through they are noise, and the tail is what matters.
    failed = result.returncode != 0
    said = git_said(result.stderr, remote=failed) or git_said(result.stdout, remote=failed)
    return not failed, said


def git_said(text: str, lines: int = 4, remote: bool = False) -> str:
    """The last few meaningful lines of git's output, for a snackbar.

    Progress lines are carriage-return rewrites of one line and would otherwise
    arrive as one very long string; the tail is where the reason lives anyway.
    """
    kept = [part.strip() for chunk in text.splitlines()
            for part in chunk.split("\r") if part.strip()]
    if not remote:
        kept = [line for line in kept
                if not line.startswith(("remote:", "Everything up-to-date"))] or kept
    return " · ".join(kept[-lines:])[:500]


def read_status(root: str) -> list[dict]:
    ok, text = git_run(root, ["status", "--porcelain=v2", "--untracked-files=all", "-z"])
    return parse_status(text) if ok else []


def known_paths(root: str, wanted: list[str]) -> tuple[list[str], list[dict]]:
    """Keep only the paths git itself is currently reporting as changed.

    The browser is not trusted to name a file: an action can only touch
    something the panel just showed, which rules out absolute paths, `..`,
    and anything outside this working tree by construction rather than by
    checking for those shapes one at a time.
    """
    entries = read_status(root)
    live = {}
    for entry in entries:
        live[entry["path"]] = entry
        if entry["origPath"]:
            live[entry["origPath"]] = entry
    return [p for p in wanted if p in live], entries


def has_commits(root: str) -> bool:
    ok, _ = git_run(root, ["rev-parse", "--verify", "HEAD"])
    return ok


def stage_paths(root: str, paths: list[str]) -> tuple[bool, str]:
    # `add` covers a deletion too — it records "this path is gone" in the index —
    # so one command serves modified, new and deleted alike.
    ok, said = git_write(root, ["add", "--", *paths])
    return ok, said or f"Staged {count(len(paths), 'file')}"


def unstage_paths(root: str, paths: list[str]) -> tuple[bool, str]:
    # Before the first commit there is no HEAD to reset back to, so the only way
    # out of the index is to drop the entry.
    args = (["reset", "-q", "HEAD", "--", *paths] if has_commits(root)
            else ["rm", "-q", "--cached", "-r", "--", *paths])
    ok, said = git_write(root, args)
    return ok, said or f"Unstaged {count(len(paths), 'file')}"


def discard_paths(root: str, paths: list[str], entries: list[dict]) -> tuple[bool, str]:
    """Throw away working-tree changes — the one action here that loses work.

    A tracked file goes back to what the index holds; an untracked one has no
    earlier version to go back to, so discarding it means deleting it. The two
    need different commands, hence the split.
    """
    by_path = {e["path"]: e for e in entries}
    untracked = [p for p in paths if by_path.get(p, {}).get("untracked")]
    tracked = [p for p in paths if p not in untracked]
    trouble = []
    if tracked:
        ok, said = git_write(root, ["restore", "--worktree", "--", *tracked])
        if not ok:
            trouble.append(said or "could not restore some files")
    if untracked:
        ok, said = git_write(root, ["clean", "-q", "-f", "--", *untracked])
        if not ok:
            trouble.append(said or "could not delete some new files")
    if trouble:
        return False, " · ".join(trouble)
    # Only say what actually happened: "0 files" alongside a deletion reads as a
    # failure when it is nothing of the sort.
    said = []
    if tracked:
        said.append(f"Discarded changes in {count(len(tracked), 'file')}")
    if untracked:
        said.append(f"{'d' if said else 'D'}eleted {count(len(untracked), 'new file')}")
    return True, ", ".join(said) or "Nothing to discard"


def count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def git_commit(root: str, message: str, amend: bool, stage_all: bool) -> tuple[bool, str]:
    """Commit what is staged, optionally staging everything first.

    `stage_all` is the panel's version of the editor's "there is nothing staged —
    commit all your changes?": the answer arrives as this flag rather than the
    panel deciding for you.
    """
    if stage_all:
        ok, said = git_write(root, ["add", "-A"])
        if not ok:
            return False, said or "Could not stage the changes"

    args = ["commit", "--no-status", "--cleanup=strip"]
    # An amend can keep the message it already has; a fresh commit cannot.
    if amend and not message:
        args += ["--amend", "--no-edit"]
    elif amend:
        args += ["--amend", "-m", message]
    elif message:
        args += ["-m", message]
    else:
        return False, "A commit needs a message"

    ok, said = git_write(root, args)
    if ok:
        head = read_git(root, log_limit=1)["commits"]
        subject = head[0]["subject"] if head else message
        return True, f"Committed — {subject}"
    if "nothing to commit" in said or "no changes added to commit" in said:
        return False, "Nothing staged to commit"
    if "Please tell me who you are" in said or "empty ident name" in said:
        return False, "git has no name and email set yet — git config user.name and user.email"
    return False, said or "Could not commit"


def git_push(root: str, state: dict, force: bool = False) -> tuple[bool, str]:
    """Push the current branch, publishing it if it has no upstream yet."""
    branch = state.get("branch")
    if state.get("detached") or not branch:
        return False, "HEAD is detached, so there is no branch to push"
    if state.get("upstream"):
        args = ["push"]
    else:
        remote = default_remote(root)
        if not remote:
            return False, "This repository has no remote to push to"
        # The editor calls this publishing, and it is the only push that decides
        # where a branch belongs — hence --set-upstream, once.
        args = ["push", "--set-upstream", remote, branch]
    if force:
        # Never plain --force: this refuses if someone else pushed in the meantime.
        args.append("--force-with-lease")
    ok, said = git_write(root, args, GIT_NETWORK_TIMEOUT)
    if ok:
        if not state.get("upstream"):
            return True, f"Published {branch} — it now tracks its remote"
        return True, f"Pushed {count(state.get('ahead') or 0, 'commit')} to {state['upstream']}"
    if "rejected" in said and "fetch first" in said:
        return False, "Rejected — the remote has commits you do not have yet. Pull first."
    return False, said or "Could not push"


def git_pull(root: str, state: dict) -> tuple[bool, str]:
    if state.get("detached"):
        return False, "HEAD is detached, so there is nothing to pull into"
    if not state.get("upstream"):
        return False, "This branch has no upstream yet, so there is nothing to pull"
    ok, said = git_write(root, ["pull", "--ff-only"], GIT_NETWORK_TIMEOUT)
    if ok:
        # A pull prints a diffstat across several lines; how many commits arrived
        # is the part worth saying, and the file list redraws either way.
        return True, f"Pulled {count(state.get('behind') or 0, 'commit')} from {state['upstream']}"
    # A pull that cannot fast-forward wants a merge or a rebase, and choosing
    # between those under a session that is editing the same tree is not the
    # panel's call to make.
    if "Not possible to fast-forward" in said or "diverging" in said or "divergent" in said:
        return False, "The branch and its upstream have diverged — merge or rebase in the terminal"
    return False, said or "Could not pull"


def switch_branch(root: str, payload: dict) -> tuple[bool, str]:
    """Move HEAD to another branch, or to a new one.

    `switch` rather than `checkout`: it only ever moves branches, so a branch name
    that happens to match a path cannot turn this into a file operation. git
    refuses on its own when the move would drop uncommitted work, and that refusal
    is the message that comes back.
    """
    name = str(payload.get("branch") or "").strip()
    if not name:
        return False, "No branch named"

    if payload.get("create"):
        ok, why = usable_branch_name(root, name)
        if not ok:
            return False, why
        start = str(payload.get("from") or "").strip()
        args = ["switch", "--create", name]
        if start:
            known = read_branches(root)
            if start not in {b["name"] for b in known["local"]} | {b["name"] for b in known["remote"]}:
                return False, "There is no such branch to start from"
            args.append(start)
        ok, said = git_write(root, args)
        return ok, (f"On a new branch, {name}" if ok else said or "Could not create that branch")

    # An existing branch is only switched to by a name the repository is currently
    # reporting — which is also what keeps a leading dash out of the argument list.
    known = read_branches(root)
    if name in {b["name"] for b in known["local"]}:
        ok, said = git_write(root, ["switch", name])
        return ok, (f"On {name}" if ok else said or "Could not switch")

    remote = next((b for b in known["remote"] if b["name"] == name), None)
    if remote:
        # Checking out somebody else's branch means making a local one that
        # follows it, which is what the editor does with the same click.
        ok, said = git_write(root, ["switch", "--track", name])
        return ok, (f"On {remote['short']}, tracking {name}" if ok
                    else said or "Could not check that branch out")

    return False, "That branch is not in this repository any more"


# A name git would take but the panel should not: one that could be read as an
# option, or that names nothing at all.
BRANCH_NAME_LIMIT = 200


def usable_branch_name(root: str, name: str) -> tuple[bool, str]:
    if name.startswith("-"):
        return False, "A branch name cannot start with a dash"
    if len(name) > BRANCH_NAME_LIMIT:
        return False, f"That name is longer than {BRANCH_NAME_LIMIT} characters"
    if any(ch.isspace() for ch in name):
        return False, "A branch name cannot contain spaces"
    # git's own rules are longer than anything worth restating here, so they are
    # the ones that decide.
    ok, _ = git_run(root, ["check-ref-format", "--branch", name])
    if not ok:
        return False, f"git will not accept “{name}” as a branch name"
    return True, ""


def default_remote(root: str) -> str | None:
    ok, text = git_run(root, ["remote"])
    if not ok:
        return None
    remotes = [line.strip() for line in text.splitlines() if line.strip()]
    if not remotes:
        return None
    return "origin" if "origin" in remotes else remotes[0]


# ------------------------------------------------------- writing the message


# A commit message is a small, closed job, so it goes to the quick model rather
# than whatever the session in that folder happens to be using. It runs headless
# — no terminal, no session — so a stopped session's repository can still have a
# message written for it, and the session's own conversation is never disturbed.
MESSAGE_MODEL = "haiku"
MESSAGE_TIMEOUT = 90.0

# A headless claude writes a session file like any other, so for the twenty
# seconds it runs the panel would list it — a row that appears, says nothing and
# vanishes, for a job the panel itself asked for and is about to throw away.
# Every claude somebody else started still belongs in the list; these are the
# panel's own errands, held by pid only while they run.
OWN_ERRANDS: set[int] = set()
OWN_ERRANDS_LOCK = threading.Lock()


def own_errand(pid: int, running: bool) -> None:
    with OWN_ERRANDS_LOCK:
        OWN_ERRANDS.add(pid) if running else OWN_ERRANDS.discard(pid)


def is_own_errand(pid: int) -> bool:
    with OWN_ERRANDS_LOCK:
        return pid in OWN_ERRANDS


# Knowing our own pids only hides our own errands. A second panel on another
# port — a test instance, a second window's server — reads the same folder, so
# its `/usage` run is somebody else's pid and lands in the list as a session
# that arrives, says nothing and goes; and even our own leaves a row behind for
# the twenty seconds the store keeps a session it can no longer see.
#
# A headless run says what it is in its own file. `claude -p` and the SDKs write
# an `sdk-*` entrypoint where a session you can type at writes `cli` (or
# `claude-vscode`), and no amount of watching one will ever let you answer it.
# So they are left out by what they are rather than by who started them, which
# holds however many panels are running and after the process is gone.


def is_headless(data: dict) -> bool:
    """Is this session file a headless run rather than a session to watch."""
    return str(data.get("entrypoint") or "").startswith("sdk-")
# Enough patch to describe a real change. Past this the subject would be a guess
# either way, and the diff is only there to be summarised.
MESSAGE_DIFF_LIMIT = 40_000

MESSAGE_TASK = """Write a git commit message for the change below.

Rules:
- One subject line, imperative mood ("Add", not "Added"), no trailing full stop,
  72 characters at the outside.
- Follow the style of the recent commits shown, if they have one.
- Add a body only if the change needs explaining, after one blank line, wrapped
  at 72 characters. Say why, not what — the diff already says what.
- Output the message and nothing else: no preamble, no code fences, no quotes
  around it, no "here is".
"""


def message_context(root: str) -> tuple[bool, str]:
    """What the model is shown: the patch that is about to be committed.

    The same scope the commit button would use — the index if anything is in it,
    the whole working tree otherwise — so the message describes the commit that
    is actually going to happen.
    """
    entries = read_status(root)
    staged = [e for e in entries if e["staged"]]
    parts = []

    if staged:
        ok, patch = git_run(root, ["diff", "--cached", "--no-color", "--no-ext-diff",
                                   "--find-renames"], timeout=15.0)
        new_files = [e["path"] for e in entries if e["staged"] == "A"]
    else:
        args = ["diff", "--no-color", "--no-ext-diff", "--find-renames"]
        # Before the first commit there is no HEAD to diff against, and nothing
        # is tracked yet either, so the file list below is the whole story.
        ok, patch = git_run(root, [*args, "HEAD"] if has_commits(root) else args, timeout=15.0)
        new_files = [e["path"] for e in entries if e["untracked"]]

    if not ok:
        patch = ""
    if new_files:
        parts.append("New files:\n" + "\n".join(f"  {p}" for p in new_files[:40]))
    if patch.strip():
        clipped = patch[:MESSAGE_DIFF_LIMIT]
        if len(patch) > MESSAGE_DIFF_LIMIT:
            clipped += "\n[diff truncated]"
        parts.append(f"Diff:\n{clipped}")
    if not parts:
        return False, "There is nothing staged or changed to describe yet"

    # The repository's own habits, so the message it gets back looks like the
    # ones around it rather than like a house style from somewhere else.
    ok, log = git_run(root, ["log", "--max-count=10", "--pretty=format:%s"])
    if ok and log.strip():
        parts.insert(0, "Recent commit subjects in this repository:\n"
                        + "\n".join(f"  {line}" for line in log.splitlines() if line.strip()))
    return True, "\n\n".join(parts)


def clean_message(text: str) -> str:
    """Take the model at its word, but not at its formatting.

    Asked for a bare message it usually gives one; a fenced block or a lead-in
    sentence is common enough that stripping them is cheaper than a retry.
    """
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        while lines and not lines[-1].startswith("```"):
            lines.pop()
        if lines:
            lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def suggest_message(root: str) -> tuple[bool, str]:
    """Ask a headless Claude for a commit message. Returns (ok, message or why)."""
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"

    ok, context = message_context(root)
    if not ok:
        return False, context

    # Popen rather than run, only so the pid is known while it is alive: that is
    # what keeps this errand out of the session list. See OWN_ERRANDS.
    try:
        process = subprocess.Popen(
            [claude, "--print", "--model", MESSAGE_MODEL,
             # It is handed everything it needs on stdin. No tools means no
             # permission prompt to answer and nothing it can do to the tree.
             "--allowed-tools", "", "--output-format", "text"],
            cwd=root, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run claude: {error}"

    own_errand(process.pid, True)
    try:
        out, err = process.communicate(f"{MESSAGE_TASK}\n{context}\n", timeout=MESSAGE_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return False, f"Claude did not answer within {int(MESSAGE_TIMEOUT)}s"
    except (OSError, subprocess.SubprocessError) as error:
        process.kill()
        return False, f"Could not run claude: {error}"
    finally:
        # Held only for the life of the process, so a recycled pid can never
        # inherit the hiding.
        own_errand(process.pid, False)

    if process.returncode != 0:
        return False, git_said(err) or "Claude could not write a message"
    message = clean_message(out)
    if not message:
        return False, "Claude answered with nothing"
    return True, message


# --------------------------------------------------------------- the plan left


# `/usage` is the one thing in this panel that no file on this machine knows.
# What a subscription has left is the account's, not the session's, and it lives
# behind Anthropic's API — so it is asked for the way you would ask yourself, by
# running Claude Code's own command and reading what it prints.
#
# Which is the point of doing it this way. The alternative was for the panel to
# read the OAuth token out of ~/.claude/.credentials.json and call an undocumented
# endpoint itself: a web server on this machine holding your credentials, for a
# reading the official client already gives away for free. This spawns `claude`,
# handles no secret, and asks for nothing the terminal would not have told you.
#
# It costs no tokens — the command fetches and prints, and samples no model, which
# a run against a fresh transcript confirms: not one usage entry — but it does take
# five seconds and a process, so it is asked rarely and its answer is kept.
PLAN_TIMEOUT = 45.0
PLAN_FRESH = 300.0

PLAN_LOCK = threading.Lock()
PLAN_HELD: dict = {}
PLAN_RUNNING = False

# "Current session: 34% used · resets Aug 12, 5:49pm (Europe/Amsterdam)", and the
# week's two lines in the same shape. The reset clause is optional: a limit at 0%
# has nothing to reset from yet.
PLAN_LIMIT = re.compile(
    r"^(?P<name>[^:]{1,60}?):\s*(?P<percent>\d{1,3})%\s*used"
    r"(?:\s*[·|-]\s*resets\s*(?P<resets>.+?))?\s*$")
# "Last 24h · 4141 requests · 46 sessions" — the heading of a block of bullets.
PLAN_BLOCK = re.compile(r"^(Last\s.+|What's contributing.*)$")


def parse_plan(text: str) -> dict:
    """Read `/usage`'s report into figures, keeping the text it came from.

    The output is a human's report rather than an interface, so nothing here
    insists on it. Every line that reads as a limit becomes one; anything else is
    kept in order as prose, and a run that parses to nothing still hands back
    what it was given rather than an empty panel.
    """
    headline, limits, blocks = "", [], []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = PLAN_LIMIT.match(line.strip())
        if match:
            limits.append({
                "name": match.group("name").strip(),
                "percent": min(100, int(match.group("percent"))),
                "resets": (match.group("resets") or "").strip(),
            })
            continue
        if PLAN_BLOCK.match(line.strip()):
            blocks.append({"title": line.strip(), "lines": []})
            continue
        if blocks and raw.startswith((" ", "\t")):
            blocks[-1]["lines"].append(line.strip())
            continue
        if not headline:
            headline = line.strip()
        elif blocks:
            blocks[-1]["lines"].append(line.strip())
    return {"headline": headline, "limits": limits, "blocks": blocks, "text": text.strip()}


def run_plan() -> dict:
    """Run `claude /usage` once and read the answer."""
    claude = shutil.which("claude")
    if not claude:
        return {"ok": False, "message": "Cannot find the claude command on PATH"}
    try:
        # Same shape as the commit-message errand: printing, no tools, so there is
        # no permission prompt to answer and nothing it can touch. Run from home
        # rather than a repository — this is the account's reading, not a folder's.
        process = subprocess.Popen(
            [claude, "--print", "/usage", "--model", MESSAGE_MODEL,
             "--allowed-tools", "", "--output-format", "text"],
            cwd=str(HOME), text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "message": f"Could not run claude: {error}"}

    own_errand(process.pid, True)
    try:
        out, err = process.communicate(timeout=PLAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return {"ok": False, "message": f"Claude did not answer within {int(PLAN_TIMEOUT)}s"}
    except (OSError, subprocess.SubprocessError) as error:
        process.kill()
        return {"ok": False, "message": f"Could not run claude: {error}"}
    finally:
        own_errand(process.pid, False)

    if process.returncode != 0:
        return {"ok": False, "message": git_said(err) or "Claude could not read your usage"}
    if not out.strip():
        return {"ok": False, "message": "Claude answered with nothing"}
    return {"ok": True, **parse_plan(out)}


def read_plan(force: bool = False) -> dict:
    """The account's remaining plan, read at most every few minutes.

    A reading costs five seconds and a process, and the figure moves in
    percentage points over hours, so it is kept and handed back until it is stale.
    Two people opening the dialog at once get the same answer rather than two
    runs: the second is told one is on its way and shown what there is.
    """
    global PLAN_RUNNING
    now = time.time()
    with PLAN_LOCK:
        held = dict(PLAN_HELD)
        fresh = held.get("ok") and now - held.get("at", 0) < PLAN_FRESH
        if fresh and not force:
            return {**held, "reading": False}
        if PLAN_RUNNING:
            # Somebody's run is already in flight. Hand back what we have and say
            # so, rather than starting a second `claude` for the same answer.
            return {**held, "reading": True} if held else {"ok": False, "reading": True,
                                                          "message": "Reading your usage…"}
        PLAN_RUNNING = True

    try:
        answer = run_plan()
    finally:
        with PLAN_LOCK:
            PLAN_RUNNING = False

    answer["at"] = time.time()
    if answer.get("ok"):
        with PLAN_LOCK:
            PLAN_HELD.clear()
            PLAN_HELD.update(answer)
        return {**answer, "reading": False}
    # A failed read does not throw away a good one: the last figures with the
    # reason the refresh failed is more use than the reason alone.
    if held.get("ok"):
        return {**held, "reading": False, "message": answer.get("message", "")}
    return {**answer, "reading": False}


def git_action(root: str, action: str, payload: dict) -> tuple[bool, str, int]:
    """One Source-Control action, named by the browser, run against one repo.

    Returns (ok, message, http status). Everything the browser sends is either a
    fixed action name, a message string, or a path that git is already reporting
    as changed — see known_paths.
    """
    if not shutil.which("git"):
        return False, "git is not installed", 409

    wanted = [p for p in (payload.get("paths") or []) if isinstance(p, str) and p]

    if action in ("stage", "unstage", "discard"):
        paths, entries = known_paths(root, wanted)
        if not paths:
            return False, "Those files have no changes any more", 409
        if action == "stage":
            ok, said = stage_paths(root, paths)
        elif action == "unstage":
            ok, said = unstage_paths(root, paths)
        else:
            ok, said = discard_paths(root, paths, entries)
        return ok, said, 200 if ok else 409

    if action == "stageAll":
        entries = read_status(root)
        if not entries:
            return False, "Nothing to stage", 409
        ok, said = git_write(root, ["add", "-A"])
        return ok, said or f"Staged {count(len(entries), 'file')}", 200 if ok else 409

    if action == "unstageAll":
        # A bare mixed reset puts the whole index back to HEAD, which is exactly
        # "unstage everything" and needs no path list.
        ok, said = (git_write(root, ["reset", "-q"]) if has_commits(root)
                    else git_write(root, ["rm", "-q", "--cached", "-r", "--", "."]))
        return ok, said or "Unstaged everything", 200 if ok else 409

    if action == "discardAll":
        entries = read_status(root)
        keep_new = not payload.get("includeUntracked")
        paths = [e["path"] for e in entries if not (keep_new and e["untracked"])]
        if not paths:
            return False, "Nothing to discard", 409
        ok, said = discard_paths(root, paths, entries)
        return ok, said, 200 if ok else 409

    if action == "switch":
        ok, said = switch_branch(root, payload)
        return ok, said, 200 if ok else 409

    if action == "commit":
        ok, said = git_commit(root, str(payload.get("message") or "").strip(),
                              bool(payload.get("amend")), bool(payload.get("stageAll")))
        return ok, said, 200 if ok else 409

    state = read_git(root, log_limit=0)

    if action == "push":
        ok, said = git_push(root, state, bool(payload.get("force")))
        return ok, said, 200 if ok else 409

    if action == "pull":
        ok, said = git_pull(root, state)
        return ok, said, 200 if ok else 409

    if action == "fetch":
        remote = default_remote(root)
        if not remote:
            return False, "This repository has no remote to fetch from", 409
        ok, said = git_write(root, ["fetch", "--prune", remote], GIT_NETWORK_TIMEOUT)
        return ok, said or f"Fetched {remote}", 200 if ok else 409

    if action == "sync":
        # Sync is the editor's one button for "catch up, then hand over": pull
        # first so a push cannot be rejected for being behind.
        if state.get("upstream") and state.get("behind"):
            ok, said = git_pull(root, state)
            if not ok:
                return False, said, 409
            state = read_git(root, log_limit=0)
        if not state.get("upstream") or state.get("ahead"):
            ok, said = git_push(root, state)
            return ok, said, 200 if ok else 409
        return True, "Already up to date", 200

    if action == "stash":
        ok, said = git_write(root, ["stash", "push", "--include-untracked",
                                    *(["-m", str(payload["message"])] if payload.get("message") else [])])
        return ok, said or "Stashed the changes", 200 if ok else 409

    if action == "stashPop":
        # A successful pop prints the whole working-tree status; the file list is
        # about to be redrawn anyway, so only a failure is worth repeating.
        ok, said = git_write(root, ["stash", "pop"])
        return ok, "Restored the latest stash" if ok else (said or "Could not restore the stash"), 200 if ok else 409

    return False, "Unknown action", 400


# ------------------------------------------------------ what it can be asked for


# A leading /name is expanded by the terminal, not by the session. A message
# injected over the messaging socket is queued as a peer turn with slash commands
# switched off — deliberately, since command markdown can carry inline shell, and
# an inbox that ran it would be a way to run anything in someone else's checkout.
#
# So the panel does not try to be the terminal. It reads the same folders Claude
# Code reads, offers what it finds by name, and sends a sentence asking for it.
# Asking is the one thing an injected turn can do, and it is enough: a skill is
# invoked by name anyway. Nothing here expands anything, substitutes arguments,
# or runs a line of a file it read.
CATALOG_FRESH = 20.0
CATALOG_LOCK = threading.Lock()
CATALOG_HELD: dict[str, tuple[float, dict]] = {}
# A walk of folders that are meant to be small. The caps are here so a stray
# checkout under ~/.claude/commands cannot turn one composer keystroke into a
# thousand-line answer.
MAX_ENTRIES = 400
MAX_DESCRIPTION = 240
MAX_SCAN = 600

PLUGIN_INSTALLS = HOME / ".claude" / "plugins" / "installed_plugins.json"
USER_SETTINGS = HOME / ".claude" / "settings.json"

# Commands that live in the terminal's own head — its screen, its model, its
# history — and that no message can reach, whatever it says. The panel names them
# rather than sending text that would quietly do nothing.
TERMINAL_ONLY = (
    "clear", "compact", "context", "model", "resume", "exit", "quit", "login", "logout",
    "config", "help", "doctor", "status", "cost", "upgrade", "release-notes", "plugin",
    "mcp", "agents", "ide", "terminal-setup", "vim", "memory", "permissions", "hooks",
    "add-dir", "export", "privacy-settings", "bashes", "statusline", "output-style",
    "todos", "install-github-app", "migrate-installer",
)

FRONT_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$")


def read_front_matter(path: Path) -> dict:
    """The `name:` and `description:` at the head of a skill or command file.

    A hand-rolled reader rather than a YAML one, and no dependency for it: these
    files are written by hand and read by half a dozen tools, so both fields are
    plain scalars, quoted or not — or a folded block, which a long description
    often is, and which is gathered from the indented lines under it. Anything
    more elaborate is left alone rather than guessed at.
    """
    found: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if handle.readline().strip() != "---":
                return found
            lines = []
            for _ in range(80):
                line = handle.readline()
                if not line or line.rstrip() == "---":
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        return {}

    for index, line in enumerate(lines):
        match = FRONT_FIELD.match(line)
        if not match:
            continue
        value = match.group(2)
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            # A block scalar: everything indented under it, as one line, which is
            # what a folded description means anyway.
            gathered = []
            for follower in lines[index + 1:]:
                if follower.strip() and not follower[:1].isspace():
                    break
                gathered.append(follower.strip())
            value = " ".join(part for part in gathered if part)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[match.group(1)] = value[:MAX_DESCRIPTION]
    return found


def scan_skills(root: Path, source: str, prefix: str = "") -> list[dict]:
    """Every SKILL.md one folder down, which is the only shape a skill has."""
    out: list[dict] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for folder in folders[:MAX_SCAN]:
        file = folder / "SKILL.md"
        if not file.is_file():
            continue
        front = read_front_matter(file)
        out.append({"name": prefix + (front.get("name") or folder.name),
                    "description": front.get("description", ""),
                    "source": source, "kind": "skill"})
    return out


def scan_commands(root: Path, source: str, prefix: str = "") -> list[dict]:
    """Command markdown, with a subfolder read as the namespace it stands for."""
    out: list[dict] = []
    try:
        files = sorted(p for p in root.rglob("*.md") if p.is_file())
    except OSError:
        return out
    for file in files[:MAX_SCAN]:
        try:
            parts = list(file.relative_to(root).with_suffix("").parts)
        except ValueError:
            continue
        front = read_front_matter(file)
        out.append({"name": prefix + ":".join(parts),
                    "description": front.get("description", ""),
                    "source": source, "kind": "command"})
    return out


def enabled_plugins(cwd: str | None) -> list[str]:
    """The plugin keys switched on, which is the terminal's own answer.

    `enabledPlugins` is keyed `plugin@marketplace` and a project can turn one on
    or off for itself, the nearer file winning — the order Claude Code reads them
    in. Reading the same switch is what keeps the panel from offering something
    the session would not answer to.
    """
    enabled: dict[str, bool] = {}
    files = [USER_SETTINGS]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json",
                  Path(cwd) / ".claude" / "settings.local.json"]
    for file in files:
        try:
            data = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        block = data.get("enabledPlugins")
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if isinstance(value, bool):
                enabled[key] = value
    return [key for key, on in enabled.items() if on]


def plugin_paths(keys: list[str]) -> list[tuple[str, Path]]:
    """Where each enabled plugin's files landed, taking its newest install."""
    try:
        data = json.loads(PLUGIN_INSTALLS.read_text())
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return []
    out: list[tuple[str, Path]] = []
    for key in keys:
        installs = plugins.get(key)
        if not isinstance(installs, list) or not installs:
            continue
        newest = max(installs, key=lambda item: str(
            item.get("lastUpdated") or item.get("installedAt") or ""))
        where = newest.get("installPath")
        if isinstance(where, str) and where:
            out.append((key.split("@", 1)[0], Path(where)))
    return out


def read_catalog(cwd: str | None) -> dict:
    """Everything this session could be asked for by name, from every source.

    Named the way it is addressed — bare for your own and the project's, prefixed
    `plugin:` for a plugin's — so what the picker shows is what the session
    answers to. Only names, descriptions and where they came from: no path on
    this machine leaves the server, and nothing is read but the head of each file.
    """
    key = cwd or ""
    now = time.monotonic()
    with CATALOG_LOCK:
        held = CATALOG_HELD.get(key)
        if held and now - held[0] < CATALOG_FRESH:
            return held[1]

    found: list[dict] = []
    found += scan_skills(HOME / ".claude" / "skills", "yours")
    found += scan_commands(HOME / ".claude" / "commands", "yours")
    for name, where in plugin_paths(enabled_plugins(cwd)):
        found += scan_skills(where / "skills", name, f"{name}:")
        found += scan_commands(where / "commands", name, f"{name}:")
    if cwd:
        found += scan_skills(Path(cwd) / ".claude" / "skills", "this project")
        found += scan_commands(Path(cwd) / ".claude" / "commands", "this project")

    # Scanned nearest last, so a project's own copy of a name overwrites the one
    # further away — which is the copy the session would use.
    by_name: dict[str, dict] = {}
    for entry in found:
        if entry["name"]:
            by_name[entry["name"]] = entry
    answer = {"ok": True,
              "entries": sorted(by_name.values(), key=lambda e: e["name"])[:MAX_ENTRIES],
              "terminalOnly": list(TERMINAL_ONLY)}
    with CATALOG_LOCK:
        CATALOG_HELD[key] = (now, answer)
        # One entry per folder the panel has looked at, and it only ever looks at
        # folders it was asked about, so this stays the size of the session list.
        if len(CATALOG_HELD) > 64:
            CATALOG_HELD.clear()
    return answer


# --------------------------------------------------------------- last activity


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


# ------------------------------------------------------------- permission mode


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


# --------------------------------------------------------- the question on screen


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


# ------------------------------------------------------------------ the subject


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
            if entry.get("isSidechain"):
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
            "sessionId": session_id,
            "title": title,
            "messages": entries[-max(1, min(limit, TRANSCRIPT_LIMIT_MAX)):],
            "truncated": more or len(entries) > limit,
            "path": str(path),
        }
    return {"sessionId": session_id, "title": None, "messages": [], "truncated": False, "path": None}


# ------------------------------------------------------------- tokens and cost


# List price per million tokens, input and output, as the API charges them. The
# multipliers below turn the input price into the other three rates: a cache
# write costs more than fresh input, a cache read a tenth of it.
#
# Keys are matched longest-first as a prefix of the model the transcript names,
# so a dated or suffixed id (`claude-opus-5[1m]`) prices as its family. A model
# with no entry is still counted — its tokens are real — but contributes no cost
# and is named as unpriced, which is honest and says what to fix.
MODEL_PRICES = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-mythos-preview": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-haiku": (0.8, 4.0),
}

# Fast mode is the same model at a premium, and the transcript says which one ran.
FAST_PRICES = {"claude-opus-5": (10.0, 50.0), "claude-opus-4-8": (10.0, 50.0)}

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1
WEB_SEARCH_PER_1K = 10.0

# What a full context is, for the reading of how much of one this session is
# carrying. Haiku's is the small one; everything current is a million.
SMALL_WINDOW = 200_000
BIG_WINDOW = 1_000_000


def price_of(model: str, fast: bool) -> tuple[float, float] | None:
    if fast:
        for name, rate in FAST_PRICES.items():
            if model.startswith(name):
                return rate
    for name in sorted(MODEL_PRICES, key=len, reverse=True):
        if model.startswith(name):
            return MODEL_PRICES[name]
    return None


def context_window(model: str) -> int:
    if "haiku" in model or "claude-3" in model:
        return SMALL_WINDOW
    return BIG_WINDOW


def blank_counters() -> dict:
    return {"requests": 0, "input": 0, "output": 0, "thinking": 0,
            "cacheWrite5m": 0, "cacheWrite1h": 0, "cacheRead": 0, "webSearch": 0}


def add_usage(bucket: dict, usage: dict) -> None:
    """Fold one request's usage into a model's running totals.

    Only the top-level figures are read. A response that took several passes
    also carries an `iterations` list holding the same numbers broken up, so
    counting both would bill every such turn twice.
    """
    bucket["requests"] += 1
    bucket["input"] += int(usage.get("input_tokens") or 0)
    bucket["output"] += int(usage.get("output_tokens") or 0)
    details = usage.get("output_tokens_details")
    if isinstance(details, dict):
        bucket["thinking"] += int(details.get("thinking_tokens") or 0)
    bucket["cacheRead"] += int(usage.get("cache_read_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        hour = int(split.get("ephemeral_1h_input_tokens") or 0)
        minutes = int(split.get("ephemeral_5m_input_tokens") or 0)
        bucket["cacheWrite1h"] += hour
        # Trust the total over the split: an unfamiliar bucket would otherwise
        # go uncounted rather than merely unclassified.
        bucket["cacheWrite5m"] += max(minutes, written - hour)
    else:
        bucket["cacheWrite5m"] += written
    tools = usage.get("server_tool_use")
    if isinstance(tools, dict):
        bucket["webSearch"] += int(tools.get("web_search_requests") or 0)


def cost_of(model: str, counters: dict, fast: bool = False) -> float | None:
    rate = price_of(model, fast)
    searches = counters["webSearch"] / 1000 * WEB_SEARCH_PER_1K
    if rate is None:
        return searches or None
    inp, out = rate
    return (
        counters["input"] / 1e6 * inp
        + counters["output"] / 1e6 * out
        + counters["cacheWrite5m"] / 1e6 * inp * CACHE_WRITE_5M
        + counters["cacheWrite1h"] / 1e6 * inp * CACHE_WRITE_1H
        + counters["cacheRead"] / 1e6 * inp * CACHE_READ
        + searches
    )


# A transcript only ever grows, so the scan remembers where it stopped and picks
# up from there. Without this, every poll would re-read and re-total a file that
# is megabytes long within an hour of work.
USAGE_SCANS: dict[str, dict] = {}
USAGE_LOCK = threading.Lock()


def scan_usage(path: Path) -> dict:
    """Every model request in this transcript, totalled, read once.

    A turn is written down more than once — one line per content block, all
    carrying the same `requestId` — so the id is what keeps a turn from being
    counted as many times as it had things to say. Sub-agent turns are marked as
    sidechains and are kept apart: they are the session's spend, but not the
    session's conversation, and the two are worth telling apart.
    """
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = str(path)
    with USAGE_LOCK:
        held = USAGE_SCANS.get(key)
        # Replaced or truncated rather than appended to: start over.
        if held is None or stat.st_size < held["offset"]:
            held = {"offset": 0, "seen": set(), "main": {}, "agents": {},
                    "context": None, "contextModel": None, "contextAt": None,
                    "firstAt": None, "lastAt": None}
            USAGE_SCANS[key] = held
        if stat.st_size == held["offset"]:
            return held

        start = held["offset"]
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                chunk = handle.read()
        except OSError:
            return held

        # A line still being written has no newline yet. Stop at the last one
        # there is and leave the remainder for the next pass, so the scan never
        # sees half a turn and never skips it either.
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return held
        held["offset"] = start + cut + 1
        for line in chunk[:cut].decode("utf-8", "replace").split("\n"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            model = str(message.get("model") or "unknown")
            if model.startswith("<"):
                continue          # a synthetic turn the API never billed
            mark = entry.get("requestId") or entry.get("uuid")
            if mark in held["seen"]:
                continue
            held["seen"].add(mark)
            fast = usage.get("speed") == "fast"
            name = f"{model} (fast)" if fast else model
            side = bool(entry.get("isSidechain"))
            where = held["agents"] if side else held["main"]
            add_usage(where.setdefault(name, blank_counters()), usage)
            at = entry.get("timestamp")
            if at:
                held["firstAt"] = held["firstAt"] or at
                held["lastAt"] = at
            if not side:
                # What the model was carrying on its last turn: everything that
                # went in, cached or not. This is the session's context size.
                held["context"] = (int(usage.get("input_tokens") or 0)
                                   + int(usage.get("cache_read_input_tokens") or 0)
                                   + int(usage.get("cache_creation_input_tokens") or 0))
                held["contextModel"] = model
                held["contextAt"] = at
        return held


def read_usage(session_id: str, cwd: str) -> dict:
    """What this session has spent, per model, with the cost that implies."""
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        held = scan_usage(path)
        if not held:
            break

        def rows(bucket: dict) -> list[dict]:
            out = []
            for name, counters in sorted(bucket.items(), key=lambda kv: -sum(
                    (kv[1]["input"], kv[1]["output"], kv[1]["cacheRead"],
                     kv[1]["cacheWrite5m"], kv[1]["cacheWrite1h"]))):
                fast = name.endswith(" (fast)")
                model = name[:-7] if fast else name
                out.append({**counters, "model": name,
                            "cost": cost_of(model, counters, fast),
                            "priced": price_of(model, fast) is not None})
            return out

        main, agents = rows(held["main"]), rows(held["agents"])
        every = main + agents
        totals = blank_counters()
        for row in every:
            for field in totals:
                totals[field] += row[field]
        cost = sum(row["cost"] or 0.0 for row in every)
        return {
            "ok": True,
            "sessionId": session_id,
            "models": main,
            "agentModels": agents,
            "totals": totals,
            "cost": cost,
            "unpriced": sorted({row["model"] for row in every if not row["priced"]}),
            "context": held["context"],
            "contextModel": held["contextModel"],
            "contextWindow": context_window(held["contextModel"] or ""),
            "contextAt": held["contextAt"],
            "firstAt": held["firstAt"],
            "lastAt": held["lastAt"],
            "path": str(path),
        }
    return {"ok": True, "sessionId": session_id, "models": [], "agentModels": [],
            "totals": blank_counters(), "cost": 0.0, "unpriced": [], "context": None,
            "contextModel": None, "contextWindow": BIG_WINDOW, "contextAt": None,
            "firstAt": None, "lastAt": None, "path": None}


# ------------------------------------------------------------------- X11 windows


def decode_xprop(text: str) -> str:
    """xprop escapes non-ASCII bytes, so unescape then read them back as UTF-8.

    Only when it actually escaped something: a title that already arrived as
    proper text must be left alone, since the round-trip cannot represent
    characters outside latin-1 and would replace them with '?'.
    """
    if "\\" not in text:
        return text
    try:
        return text.encode("latin-1", "backslashreplace").decode("unicode_escape").encode(
            "latin-1", "replace"
        ).decode("utf-8", "replace")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


# Window classes and process names that plausibly host a Claude Code session.
# A title alone is only allowed to identify a window if it is one of these:
# plenty of browsers and file managers put a project folder in their title too.
HOST_HINTS = (
    "terminal", "xterm", "urxvt", "rxvt", "konsole", "kitty", "ghostty",
    "wezterm", "alacritty", "tilix", "guake", "terminator", "foot", "st-256color",
    "code", "code-oss", "vscodium", "cursor", "windsurf", "jetbrains", "tmux",
)


def looks_like_host(wclass: str, pid: int | None) -> bool:
    """Whether this window belongs to something a session could be running in."""
    marks = f"{wclass} {proc_name(pid) if pid else ''}".lower()
    return any(hint in marks for hint in HOST_HINTS)


class WindowIndex:
    """Visible top-level windows with their pid and title, briefly cached."""

    def __init__(self, ttl: float = 4.0) -> None:
        self.ttl = ttl
        self._at = 0.0
        self._windows: list[dict] = []
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(shutil.which("xdotool") and shutil.which("xprop") and os.environ.get("DISPLAY"))

    def windows(self, force: bool = False) -> list[dict]:
        with self._lock:
            if not force and time.time() - self._at < self.ttl:
                return self._windows
            self._windows = self._scan()
            self._at = time.time()
            return self._windows

    def _scan(self) -> list[dict]:
        if not self.available():
            return []
        try:
            root = subprocess.run(
                ["xprop", "-root", "_NET_CLIENT_LIST"],
                capture_output=True, text=True, timeout=4,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        ids = re.findall(r"0x[0-9a-fA-F]+", root)
        found: list[dict] = []
        for window_id in ids:
            try:
                props = subprocess.run(
                    ["xprop", "-id", window_id, "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME", "WM_NAME"],
                    capture_output=True, text=True, timeout=4,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            pid_match = re.search(r"_NET_WM_PID\(\w+\) = (\d+)", props)
            title_match = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', props) or re.search(
                r'WM_NAME\(\w+\) = "(.*)"', props
            )
            class_match = re.search(r'WM_CLASS\(\w+\) = "(?:[^"]*)", "([^"]*)"', props)
            title = decode_xprop(title_match.group(1) if title_match else "")
            # xterm and a few others never set _NET_WM_PID. Such a window can
            # still be identified by its title, so it is kept rather than
            # dropped; only one with nothing at all to go on is useless.
            if not pid_match and not title:
                continue
            pid = int(pid_match.group(1)) if pid_match else None
            wclass = class_match.group(1) if class_match else ""
            found.append({
                "id": window_id,
                "pid": pid,
                "title": title,
                "wclass": wclass,
                "host": looks_like_host(wclass, pid),
            })
        return found

    def match(self, session: dict) -> dict | None:
        """Find the window most likely to own this session.

        A window's _NET_WM_PID is the terminal or editor process, so we look for
        a window whose pid sits on the session's ancestor chain, and read the
        title for corroboration.

        The pid is not always the discriminator it looks like. GNOME Terminal,
        and every other terminal with a server process, reports the *same* pid
        for every one of its windows: each one then sits on the chain and scores
        alike. The old code took the first of them, which is a coin flip wearing
        the word "likely". When the leaders tie, this says `ambiguous` and hands
        the choice on — to the probe, or to you.
        """
        windows = self.windows()
        if not windows:
            return None
        chain = session.get("ancestors") or []
        cwd = session.get("cwd") or ""
        folder = os.path.basename(cwd).lower()
        home = str(Path.home())
        # Terminals write the folder into the title contracted, as ~/work/thing.
        short_cwd = ("~" + cwd[len(home):]) if home and cwd.startswith(home) else cwd
        # The session's own name, not one you typed here — the window title
        # knows nothing about your renaming.
        name = (session.get("defaultName") or session.get("name") or "").lower()

        scored: list[tuple[int, dict]] = []
        for window in windows:
            score = 0
            if window["pid"] and window["pid"] in chain:
                # A nearer ancestor is a tighter relationship.
                score += 100 - chain.index(window["pid"])
            title = window["title"].lower()
            if cwd and (cwd.lower() in title or short_cwd.lower() in title):
                score += 50
            elif folder and folder in title:
                score += 40
            if name and name in title:
                score += 25
            # A title on its own is weak evidence, so it only counts for a
            # window that could host a session at all — not for the browser
            # tab that happens to be showing the same folder name.
            if score and (score >= 90 or window["host"]):
                scored.append((score, window))

        if not scored:
            return None
        best = max(score for score, _ in scored)
        if best < 40:
            return None
        leaders = [window for score, window in scored if score == best]
        if len(leaders) > 1:
            return {
                **leaders[0],
                "confidence": "ambiguous",
                "candidates": [
                    {"id": w["id"], "title": w["title"], "wclass": w["wclass"]} for w in leaders
                ],
                # Only a probe can separate tabs of one terminal; a pid cannot.
                "canIdentify": bool(session.get("tty")),
            }
        return {**leaders[0], "confidence": "high" if best >= 130 else "likely"}


WINDOWS = WindowIndex()


def load_pairs() -> dict[str, dict]:
    """Remembered window pairings, as {sessionId: {"id", "how"}}.

    `how` is "picked" when you clicked the window and "identified" when the
    probe found it, because the panel should not tell you that you chose a
    window you never clicked. A file from before this distinction holds bare
    window ids, and those read as picked.
    """
    try:
        data = json.loads(PAIR_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    pairs: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(value, dict) and value.get("id"):
            pairs[str(key)] = {"id": str(value["id"]),
                               "how": "identified" if value.get("how") == "identified" else "picked"}
        elif isinstance(value, str):
            pairs[str(key)] = {"id": value, "how": "picked"}
    return pairs


def save_pairs(pairs: dict[str, dict]) -> None:
    PAIR_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAIR_FILE.write_text(json.dumps(pairs, indent=2))


def load_names() -> dict[str, str]:
    try:
        data = json.loads(NAME_FILE.read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_names(names: dict[str, str]) -> None:
    if len(names) > MAX_NAMES:  # oldest first — dicts keep insertion order
        names = dict(list(names.items())[-MAX_NAMES:])
    NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
    NAME_FILE.write_text(json.dumps(names, indent=2))


def clean_name(text: object) -> str:
    """One line, no control characters, short enough to sit in a header."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:MAX_NAME]


# ----------------------------------------------------------------- kept rows
# A session file disappears when its process does, and with it the row. A kept
# row outlives it: the panel remembers enough about the session — id, name,
# folder — to go on showing the conversation, and can start Claude Code back up
# on that same transcript with `claude --resume`.
#
# Two tiers, and the only difference is how long "outlives it" means:
#
# - **Held** (`_KEPT`, memory only). Every row the panel makes for itself is
#   this: a session it started, a session it adopted. It survives a page reload,
#   which is a browser doing nothing of consequence, and goes when the panel
#   goes — because whatever was running here is not running any more either, and
#   a row for it would be a row for nothing.
# - **Pinned** (`sticky.json`, on disk). Asked for a row at a time, and the only
#   thing that survives a restart. Panel-run sessions used to be written here
#   too, which made every one of them permanent whether or not that was wanted:
#   the panel decided what you were keeping.
#
# Nothing about the session is copied either way. The transcript stays where
# Claude Code keeps it, and a forgotten row loses only the row.

_KEPT: dict[str, dict] = {}
_KEPT_LOCK = threading.Lock()


def load_pinned() -> dict[str, dict]:
    try:
        data = json.loads(STICKY_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_pinned(pinned: dict[str, dict]) -> None:
    if len(pinned) > MAX_STICKY:
        pinned = dict(list(pinned.items())[-MAX_STICKY:])
    STICKY_FILE.parent.mkdir(parents=True, exist_ok=True)
    STICKY_FILE.write_text(json.dumps(pinned, indent=2))


def kept_rows() -> dict[str, dict]:
    """Every row that outlives its process, held and pinned together.

    Each entry carries `pinned`, because that is the difference the row shows and
    the only thing the next restart cares about.
    """
    pinned = load_pinned()
    with _KEPT_LOCK:
        out = {key: dict(value) for key, value in _KEPT.items()}
    for key, entry in pinned.items():
        out[key] = {**out.get(key, {}), **entry}
    for key, entry in out.items():
        entry["pinned"] = key in pinned
    return out


def keep_row(entry: dict) -> None:
    """Keep this row for as long as the panel runs, and no longer."""
    session_id = str(entry.get("sessionId") or "")
    if not session_id:
        return
    entry = {key: value for key, value in entry.items() if key != "pinned"}
    with _KEPT_LOCK:
        _KEPT.pop(session_id, None)  # re-inserted, so the cap drops the oldest
        for old in list(_KEPT)[:max(0, len(_KEPT) - MAX_KEPT + 1)]:
            _KEPT.pop(old, None)
        _KEPT[session_id] = entry
    # A pinned row is the same row. Keeping the written copy in step is what
    # stops a pinned session coming back after a restart under a stale name.
    pinned = load_pinned()
    if session_id in pinned:
        pinned[session_id] = entry
        save_pinned(pinned)


def refresh_row(session_id: str, entry: dict) -> bool:
    """Update a row that is already kept, without making one that is not."""
    entry = {key: value for key, value in entry.items() if key != "pinned"}
    touched = False
    with _KEPT_LOCK:
        if session_id in _KEPT:
            _KEPT[session_id] = entry
            touched = True
    pinned = load_pinned()
    if session_id in pinned:
        pinned[session_id] = entry
        save_pinned(pinned)
        touched = True
    return touched


def pin_row(session_id: str, entry: dict) -> None:
    """Write this row down, so it is still here after a restart."""
    pinned = load_pinned()
    pinned[session_id] = {key: value for key, value in entry.items() if key != "pinned"}
    save_pinned(pinned)


def unpin_row(session_id: str) -> None:
    """Stop writing it down. A row the panel is holding stays until it stops."""
    pinned = load_pinned()
    if pinned.pop(session_id, None) is not None:
        save_pinned(pinned)


def forget_row(session_id: str) -> bool:
    """Drop the row outright, pinned or not. Says whether there was one."""
    with _KEPT_LOCK:
        gone = _KEPT.pop(session_id, None) is not None
    pinned = load_pinned()
    if pinned.pop(session_id, None) is not None:
        save_pinned(pinned)
        gone = True
    return gone


def drop_unpinned_row(session_id: str) -> bool:
    """Let go of the row unless it was pinned. Says whether the row is going.

    Stopping a session and taking its row off the list used to be two separate
    asks, so a session you had just ended sat on the dashboard until you asked a
    second time. Pinning is the one thing that means "keep this row past its
    process", so it is also the one thing a stop leaves standing.
    """
    if session_id in load_pinned():
        return False
    with _KEPT_LOCK:
        _KEPT.pop(session_id, None)
    return True


# Terminals that can be told to run one command, in the order we try them. The
# value is how that terminal takes it: everything after the flag is the command.
TERMINALS = [
    ("ghostty", ["-e"]), ("wezterm", ["start", "--"]), ("kitty", ["--"]),
    ("alacritty", ["-e"]), ("konsole", ["-e"]), ("gnome-terminal", ["--"]),
    ("xfce4-terminal", ["-x"]), ("x-terminal-emulator", ["-e"]), ("xterm", ["-e"]),
]


# The environment Claude Code stamps on everything it starts, and what makes a
# session started from inside another one call itself a child: see
# child_session. The panel is often itself started from inside a session, so
# without this the terminal it opens inherits that stamp and the fresh session it
# was asked for comes up nested — no session file, no transcript, no title, no
# chat, and a parent it does not really belong to. Only session-scoped names are
# dropped; the CLAUDE_CODE_* settings a user puts in their profile (model,
# config dir, output limits) are not ours to throw away.
SESSION_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
    "AI_AGENT",
)


def top_level_env() -> dict[str, str]:
    """Our environment with the marks of the session we may be running in removed.

    What a new `claude` needs to start as a session in its own right rather than
    as a child of ours. Also right for a resume: a resumed session that comes up
    nested writes nothing to the transcript it was resumed on.
    """
    return {k: v for k, v in os.environ.items() if k not in SESSION_ENV}


def terminal_argv(command: list[str], cwd: str) -> list[str] | None:
    """A terminal invocation that runs `command`, or None if none is installed.

    CLAUDE_WATCHTOWER_TERMINAL overrides the search: give it the terminal and
    any flags, and the command is appended —
    `CLAUDE_WATCHTOWER_TERMINAL="kitty --"`. CLAUDE_BUSY_UI_TERMINAL is the
    pre-rename name, still honoured.
    """
    override = os.environ.get("CLAUDE_WATCHTOWER_TERMINAL") or os.environ.get(
        "CLAUDE_BUSY_UI_TERMINAL"
    )
    if override:
        parts = override.split()
        if parts and shutil.which(parts[0]):
            return parts + command
        return None
    for name, flags in TERMINALS:
        if shutil.which(name):
            # gnome-terminal needs its own flag for the folder; the rest inherit ours.
            lead = [name, f"--working-directory={cwd}"] if name == "gnome-terminal" and cwd else [name]
            return lead + flags + command
    return None


def interactive_argv(command: list[str]) -> list[str]:
    """`command` run by an interactive shell that stays behind when it exits.

    A terminal handed a bare `claude` is a window with one program in it: the
    session comes up without the PATH, aliases and version managers the shell's
    rc file sets up, and the window vanishes the moment the session ends, taking
    the scrollback with it. Going through the shell instead gives a session
    started from the panel the same surroundings as one started by hand, and
    leaves a prompt behind afterwards, so the window is somewhere to work rather
    than something to watch.
    """
    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    line = " ".join(shlex.quote(part) for part in command)
    return [shell, "-i", "-c", f"{line}; exec {shlex.quote(shell)} -i"]


def start_session(entry: dict) -> tuple[bool, str]:
    """Open a terminal running `claude --resume <id>` in the session's folder."""
    session_id = str(entry.get("sessionId") or "")
    cwd = entry.get("cwd") or str(HOME)
    if not session_id:
        return False, "That session has no id to resume"
    if not Path(cwd).is_dir():
        return False, f"Its folder is gone: {cwd}"
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"
    argv = terminal_argv([claude, "--resume", session_id], cwd)
    if not argv:
        return False, "No terminal found to start it in — set CLAUDE_WATCHTOWER_TERMINAL"
    try:
        subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            env=top_level_env(),
        )
    except OSError as exc:
        return False, f"Could not start it: {exc}"
    return True, "Starting it up…"


def new_session(cwd: str) -> tuple[bool, str]:
    """Open a terminal running a fresh `claude` in a folder, from a shell prompt."""
    if not cwd or not Path(cwd).is_dir():
        return False, f"That folder is gone: {cwd}" if cwd else "That session has no folder"
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"
    argv = terminal_argv(interactive_argv([claude]), cwd)
    if not argv:
        return False, "No terminal found to start it in — set CLAUDE_WATCHTOWER_TERMINAL"
    try:
        subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            env=top_level_env(),
        )
    except OSError as exc:
        return False, f"Could not start it: {exc}"
    return True, "Opening a new session there…"


# --------------------------------------------------------- turns run from here
# The one thing a terminal keeps to itself is the flags a turn starts under. So
# the panel runs a turn of its own: `claude --print --resume <id>` picks up the
# conversation that is already there, appends to the same transcript, and exits.
#
# That is the whole trick, and it is why this is small. The permission mode is
# an argument, so changing it is changing what the next turn is launched with —
# no process to signal, nothing to restart, nothing to take over, and no state
# to keep in step beyond a remembered word. Switching is instant because there
# is nothing to switch: the choice is not applied until a turn is launched, and
# a turn is launched from scratch every time.
#
# What this deliberately does not do is take a session away from a terminal.
# Two processes appending to one transcript is the failure this area is littered
# with, so a turn only ever runs for a session no live process is holding, and
# only one at a time. A session in a terminal is not eligible and is not made
# eligible by force: end it first, which is a button that already exists.

# What the panel will launch a turn in, keyed the way the transcript reports it
# — `default`, translated to `manual` at the argv boundary, which is what the
# CLI actually accepts.
#
# `bypassPermissions` and `dontAsk` are still not offered: they never ask
# anybody, so a panel that can answer prompts has nothing to answer and no way
# to know what was done in its name.
#
# `auto` is offered, because it is Claude Code's own default and leaving it out
# meant the panel could not run a session the way its terminal already does. It
# is worth knowing what it is, though: a classifier decides what needs approval,
# and what it approves never reaches the panel at all — in testing it ran
# `rm -rf` with no prompt raised. The three others either ask or hold back.
#
# Asking is no longer a dead end in any of them: a turn here is launched with
# `--permission-prompt-tool stdio`, so a prompt arrives in the panel and the
# tool waits. What no mode changes is that Claude Code judges some commands safe
# enough not to ask about at all, and those still run.
OWNED_MODES = ("default", "auto", "plan", "acceptEdits")
CLI_MODES = {"default": "manual"}
# A turn is a whole conversation turn, which can be minutes. Long, not endless.
OWNED_SECONDS = 1800.0

# How long a prompt raised by a panel turn waits for somebody to answer it in the
# panel. Long, because the answer is a person noticing; not endless, because a
# turn holding a pipe open forever is a process nobody will ever reap.
OWNED_ASK_SECONDS = 600.0

_OWNED_LOCK = threading.Lock()
# sessionId -> the prompt standing in front of that turn, as the panel shows it.
# A turn holds its pipe open while this is set: the tool has not run, and will
# not, until an answer comes back down it.
OWNED_ASK: dict[str, dict] = {}
# sessionId -> the answer given, and the event the waiting turn is parked on.
_ASK_ANSWER: dict[str, dict] = {}
_ASK_EVENTS: dict[str, threading.Event] = {}
# sessionId -> when its turn started. Also the guard against a second turn, and
# against a terminal being opened on the transcript underneath a running one.
OWNED_BUSY: dict[str, float] = {}
# sessionId -> how the last one went, so the panel can say so after the fact.
# A turn is launched and let go of, so this is the only place its outcome lands.
OWNED_LAST: dict[str, dict] = {}
# sessionId -> what was typed at it while it was answering, in the order it was
# typed. One turn runs at a time down a held pipe — the transcript is being
# written by the turn in flight, and a second turn written into it is the
# two-turns-one-conversation failure the whole area is built to avoid — so the
# panel does here what the terminal's own prompt does when you type ahead of it:
# it holds the message and sends it the instant the turn ends.
#
# *It is still answering the last one* was the alternative, and it was the answer
# to a question nobody asked. Nothing about the panel's timing is the typist's
# business: the message was written, it is going in, and being told to come back
# in four minutes and press Send again is work the panel can do itself.
OWNED_QUEUE: dict[str, list[str]] = {}
# sessionId -> when it was told to stop. A turn ends in a result frame either way,
# and this is what tells one that was stopped from one that failed: an interrupted
# turn comes back `is_error` with `error_during_execution`, which read as the turn
# having gone wrong when in fact it did exactly what it was told.
OWNED_STOPPING: dict[str, float] = {}
# sessionId -> what compaction is doing, or how the last one went. A compaction
# is a turn like any other from the pipe's point of view, but its `result` frame
# carries an empty string, so on the outcome machinery alone it reads as a turn
# that finished and said nothing. What it actually did arrives in three `system`
# frames, which is what this records:
#
#   {"subtype":"status","status":"compacting"}                      it started
#   {"subtype":"status","status":null,"compact_result":"success"}   it worked
#   {"subtype":"compact_boundary","compact_metadata":{...}}         by how much
#
# `compact_metadata` carries `pre_tokens` and `post_tokens`, which is the only
# honest way to say what was gained — the panel's own context reading comes off
# the *next* assistant turn, so it does not move until the session is used again.
OWNED_COMPACT: dict[str, dict] = {}
# sessionId -> what it says it can be asked for: `available`, and `terminalOnly`,
# the ones it keeps for its own prompt. Straight off its `init` frame, because a
# held pipe and a messaging socket do not agree about slash commands and only the
# session knows which it is. Empty until it has spoken once.
OWNED_COMMANDS: dict[str, dict] = {}
# Bounded, because a queue with no end is a session answering questions you
# stopped caring about ten turns ago. Past this the message is refused, which is
# the one place a refusal says something true: you have typed more than you can
# have meant for one conversation to answer in order.
MAX_QUEUED = 10


def load_owned() -> dict[str, dict]:
    """sessionId -> {mode, here} for the sessions the panel runs turns for.

    `here` says the panel was handed this one deliberately — it ended the
    terminal session to take it — as against a mode merely picked for a session
    in passing. It is what stops an adopted row reading as a dead one: nothing
    is running, which is normal between turns, and the row should say which of
    those two it is.

    An older file holds a bare mode string per id, and is read as one.
    """
    try:
        data = json.loads(OWNED_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(value, str):
            value = {"mode": value}
        if not isinstance(value, dict):
            continue
        mode = str(value.get("mode") or OWNED_MODES[0])
        out[str(key)] = {"mode": mode if mode in OWNED_MODES else OWNED_MODES[0],
                         "here": bool(value.get("here"))}
    return out


def save_owned(owned: dict[str, dict]) -> None:
    if len(owned) > MAX_OWNED:
        owned = dict(list(owned.items())[-MAX_OWNED:])
    OWNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    OWNED_FILE.write_text(json.dumps(owned, indent=2))


def owned_state() -> dict[str, dict]:
    """What the panel knows about turns it runs, per session, for the feed."""
    kept = load_owned()
    with _OWNED_LOCK:
        busy = dict(OWNED_BUSY)
        last = {k: dict(v) for k, v in OWNED_LAST.items()}
        asking = {k: dict(v) for k, v in OWNED_ASK.items()}
        waiting = {k: list(v) for k, v in OWNED_QUEUE.items() if v}
        stopping = set(OWNED_STOPPING)
        compacting = {k: dict(v) for k, v in OWNED_COMPACT.items()}
        commands = {k: dict(v) for k, v in OWNED_COMMANDS.items()}
    out: dict[str, dict] = {}
    for session_id in (set(kept) | set(busy) | set(last) | set(asking) | set(waiting)
                       | set(compacting) | set(commands)):
        out[session_id] = {
            "mode": (kept.get(session_id) or {}).get("mode", OWNED_MODES[0]),
            # Adopted, as against merely having had a mode picked for it.
            "here": bool((kept.get(session_id) or {}).get("here")),
            "busy": session_id in busy,
            "since": busy.get(session_id),
            "last": last.get(session_id),
            "ask": asking.get(session_id),
            # What is standing behind the turn in flight. Sent in full rather
            # than counted: a queue you cannot read is a queue you cannot decide
            # to drop something from, and dropping is the whole point of seeing
            # it. The panel is loopback-only and shows the conversation itself,
            # so there is nothing here it is not already showing.
            "queued": waiting.get(session_id) or [],
            # Told to stop, and not yet come back saying it has. The button it
            # was pressed on says so for that moment rather than sitting there
            # looking unpressed.
            "stopping": session_id in stopping,
            # What compaction is doing, or what the last one did. Distinct from
            # `last` because a compaction's own result frame is empty — see
            # OWNED_COMPACT.
            "compact": compacting.get(session_id),
            # What this session says it takes, for the composer's /-picker. Null
            # until it has said — the panel's own list stands in until then.
            "commands": commands.get(session_id),
            # Held open, i.e. a process is there between turns — which is what
            # makes the row a running session rather than one that wakes up.
            "running": owned_running(session_id),
        }
    return out


# A session the panel holds open. One `claude --print --input-format stream-json`
# per adopted session, alive between turns rather than spawned for each: it
# serves turn after turn down the same pipe, which is what makes the session
# *running* rather than a row that wakes up when poked.
#
# Holding it is also what makes the mode a live setting. `set_permission_mode`
# is refused on the messaging socket because its callback is not registered
# there — but this transport owns the session's stdio, where it is registered,
# and it answers `{"subtype":"success","response":{"mode":"acceptEdits"}}` and
# takes effect on the next tool. That is the thing this whole area was opened to
# find, and it only works while something holds the pipe.
OWNED_PROCS: dict[str, dict] = {}


def owned_write(session_id: str, frame: dict) -> bool:
    """One JSON line into a held session's stdin, or False if it is not there."""
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
    if not held or held["proc"].poll() is not None:
        return False
    try:
        with held["write"]:
            held["proc"].stdin.write(json.dumps(frame) + "\n")
            held["proc"].stdin.flush()
        return True
    except (OSError, ValueError):
        return False


def owned_running(session_id: str) -> bool:
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
    return bool(held) and held["proc"].poll() is None


def ask_from_panel(session_id: str, request: dict) -> dict:
    """Park a turn on the prompt it raised and hand back the answer to send.

    This is the whole of what the control channel buys. `--permission-prompt-tool
    stdio` makes Claude Code ask over the pipe the panel is holding, so the
    question arrives here as a can_use_tool control_request and the tool does not
    run until a control_response goes back. A permission prompt and a
    multiple-choice question come up this same channel; `requires_user_interaction`
    is what tells them apart, and a question's answers ride back inside the input
    it was going to use.

    Nobody answering is the case worth being careful about: a turn parked forever
    holds a pipe and a process. So the wait has a deadline, and running out is a
    refusal — the safe direction, and one the turn can explain.
    """
    event = threading.Event()
    standing = {
        "requestId": str(request.get("request_id") or ""),
        "tool": str(request.get("tool_name") or "a tool"),
        "name": str(request.get("display_name") or request.get("tool_name") or "a tool"),
        "what": str(request.get("description") or "")[:300],
        "input": request.get("input") if isinstance(request.get("input"), dict) else {},
        # A question rather than a gate: the panel draws options instead of
        # allow-and-deny, and answering it *is* allowing it.
        "asks": bool(request.get("requires_user_interaction")),
        "at": time.time(),
        "seconds": OWNED_ASK_SECONDS,
    }
    with _OWNED_LOCK:
        OWNED_ASK[session_id] = standing
        _ASK_EVENTS[session_id] = event
        _ASK_ANSWER.pop(session_id, None)
    try:
        answered = event.wait(OWNED_ASK_SECONDS)
        with _OWNED_LOCK:
            given = _ASK_ANSWER.pop(session_id, None)
    finally:
        with _OWNED_LOCK:
            OWNED_ASK.pop(session_id, None)
            _ASK_EVENTS.pop(session_id, None)
    if not answered or not given:
        return {"behavior": "deny", "message": "Nobody answered this in the panel, so it was refused"}
    if given.get("behavior") != "allow":
        return {"behavior": "deny", "message": str(given.get("message") or "Refused from the panel")}
    # Allowing runs the tool on the input it asked with. A question is allowed the
    # same way, with the answers written into that input: `answers` is keyed by
    # the question's own text, and several picks join on ", ".
    used = dict(standing["input"])
    picks = given.get("answers")
    if isinstance(picks, dict) and isinstance(used.get("questions"), list):
        answers = {}
        for question in used["questions"]:
            if not isinstance(question, dict):
                continue
            chosen = picks.get(str(question.get("question") or ""))
            if isinstance(chosen, list):
                chosen = ", ".join(str(c) for c in chosen if str(c).strip())
            chosen = str(chosen or "").strip()
            if chosen:
                answers[question["question"]] = chosen
        if answers:
            used["answers"] = answers
    return {"behavior": "allow", "updatedInput": used}


def answer_from_panel(session_id: str, request_id: str, decision: dict) -> tuple[bool, str]:
    """Hand an answer to the turn parked on a prompt."""
    with _OWNED_LOCK:
        standing = OWNED_ASK.get(session_id)
        event = _ASK_EVENTS.get(session_id)
        if not standing or not event:
            return False, "Nothing here is waiting to be answered"
        # The id has to match. A panel repainted between two prompts would
        # otherwise answer the second with what was clicked for the first.
        if request_id and request_id != standing["requestId"]:
            return False, "That answer was for a prompt that has already gone"
        _ASK_ANSWER[session_id] = decision
        event.set()
    allowed = decision.get("behavior") == "allow"
    if standing["asks"] and allowed:
        return True, "Answered"
    return True, "Allowed" if allowed else "Refused"


def owned_name_itself(held: dict, said: str) -> None:
    """Register a session the panel started, the moment it says what it is.

    A session that does not exist yet cannot be resumed and has no id to be
    keyed by: `claude --print --input-format stream-json` with no `--resume`
    writes nothing and announces nothing until it is sent something, and only
    then says which session it has become. So the process is started first and
    identified afterwards, out of its own first frames.
    """
    held["id"] = said
    with _OWNED_LOCK:
        OWNED_PROCS[said] = held
        # Its first turn is already running — it was sent something to make it
        # exist at all.
        OWNED_BUSY[said] = time.time()
    if said not in kept_rows():
        cwd = held.get("cwd") or ""
        keep_row({
            "sessionId": said, "name": held.get("name") or os.path.basename(cwd) or "new session",
            "cwd": cwd, "startedAt": time.time(), "lastSeen": time.time(),
            "version": None, "kind": "interactive",
        })
    owned = load_owned()
    owned[said] = {"mode": held.get("mode") or OWNED_MODES[0], "here": True}
    save_owned(owned)
    held["named"].set()


def user_turn(text: str) -> dict:
    """One typed message, as the stream-json input format wants it."""
    return {"type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def ordinal(n: int) -> str:
    """2 -> "2nd". For saying where in the queue a message landed."""
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def owned_queue_add(session_id: str, text: str) -> tuple[bool, str]:
    """Hold a message behind the turn in flight, and say where it landed.

    Called once the caller has established that it cannot go now — a turn is
    running, or something is already waiting for one. It takes the lock itself
    rather than being called under it, so there is one place that knows how the
    queue is guarded.
    """
    with _OWNED_LOCK:
        queue = OWNED_QUEUE.setdefault(session_id, [])
        if len(queue) >= MAX_QUEUED:
            return False, (f"There are already {MAX_QUEUED} messages waiting behind this "
                           "turn — let it get through some of them first")
        queue.append(text)
        place = len(queue)
    return True, ("Held for it — this goes in the moment the turn ends" if place == 1
                  else f"Held for it — {ordinal(place)} in the queue behind this turn")


def owned_queued(session_id: str) -> list[str]:
    with _OWNED_LOCK:
        return list(OWNED_QUEUE.get(session_id) or [])


def owned_unqueue(session_id: str, index: int | None) -> tuple[bool, str]:
    """Take something back out of the queue before the turn reaches it.

    A message typed three turns ago can be answered by the turn that was running
    when you typed it, and then it is worse than unsent. Nothing can be taken
    back once it has gone down the pipe, so this only ever touches what is still
    waiting — and it says which of the two happened rather than reporting success
    for a message that has already been asked.
    """
    with _OWNED_LOCK:
        queue = OWNED_QUEUE.get(session_id) or []
        if not queue:
            return False, "Nothing is waiting behind this turn"
        if index is None:
            OWNED_QUEUE.pop(session_id, None)
            return True, ("Dropped the message that was waiting" if len(queue) == 1
                          else f"Dropped the {len(queue)} messages that were waiting")
        if not 0 <= index < len(queue):
            return False, "That message has already gone in"
        queue.pop(index)
        if not queue:
            OWNED_QUEUE.pop(session_id, None)
    return True, "Dropped it"


def owned_flush(session_id: str) -> None:
    """Send what was typed ahead, now that the turn it was typed behind is done.

    Called from the reader the instant a result lands, which is the instant the
    terminal's own prompt would have taken it. One at a time and in order: each
    queued message waits for the result of the one before it, exactly as it would
    have if you had waited yourself before pressing Send.

    A pipe that has gone in the meantime is not this function's problem to
    report — nobody is waiting on it — so the message goes back to the deliverer,
    which starts the session up and puts it in there. That is the same promise
    the composer made when it took it.
    """
    with _OWNED_LOCK:
        if session_id in OWNED_BUSY:
            return
        queue = OWNED_QUEUE.get(session_id) or []
        if not queue:
            OWNED_QUEUE.pop(session_id, None)
            return
        text = queue.pop(0)
        if not queue:
            OWNED_QUEUE.pop(session_id, None)
        OWNED_BUSY[session_id] = time.time()
    if owned_write(session_id, user_turn(text)):
        return
    with _OWNED_LOCK:
        OWNED_BUSY.pop(session_id, None)
    deliver_later(session_id, text)


def owned_interrupt(session_id: str) -> tuple[bool, str]:
    """Stop the turn a held session is in the middle of.

    `{"subtype": "interrupt"}` down the same control channel the mode is set on,
    which is the whole of it. Verified against a held session: the request is
    answered `{"subtype": "success", "response": {"still_queued": []}}`, the
    transcript gains a `[Request interrupted by user]` turn where Claude Code
    stopped, the turn ends `error_during_execution`, and the process stays up and
    takes the next turn normally. Nothing is killed and nothing is restarted.

    What is typed ahead goes with it. The queue was written for a train of thought
    that has just been stopped, and delivering it a tenth of a second later — into
    a session that is now waiting for you to say what you actually want — is the
    opposite of what stopping meant.
    """
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
        busy = session_id in OWNED_BUSY
    if not held:
        return False, "The panel is not running that session"
    if not busy:
        return False, "It is not working on anything"
    if not owned_write(session_id, {
            "type": "control_request", "request_id": f"interrupt-{uuid.uuid4()}",
            "request": {"subtype": "interrupt"}}):
        return False, "It would not take the interrupt — the pipe has gone"
    with _OWNED_LOCK:
        OWNED_STOPPING[session_id] = time.time()
        dropped = len(OWNED_QUEUE.pop(session_id, None) or [])
    if not dropped:
        return True, "Stopping it"
    return True, ("Stopping it — and the message that was waiting behind it is dropped"
                  if dropped == 1 else
                  f"Stopping it — and the {dropped} messages waiting behind it are dropped")


def owned_reader(session_id: str, held: dict) -> None:
    """Everything the held session says, for as long as it is held.

    `session_id` is what it was known as at the start, which for a session the
    panel has just created is nothing at all — so the key is read off the held
    record each time, and is filled in as soon as the session names itself.
    """
    proc = held["proc"]
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            kind = frame.get("type")
            # A session the panel started says which one it is in its first
            # frames, and nothing can be filed under it until it has.
            if held.get("id") is None and isinstance(frame.get("session_id"), str):
                owned_name_itself(held, frame["session_id"])
            session_id = held.get("id") or session_id
            if not session_id:
                continue
            if kind == "control_request":
                request = frame.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    continue
                # The answer waits on a person, so it cannot be waited for here:
                # this thread is the only reader, and the session goes on talking.
                def settle(request=request, request_id=frame.get("request_id")) -> None:
                    reply = ask_from_panel(session_id, {**request, "request_id": request_id})
                    owned_write(session_id, {
                        "type": "control_response",
                        "response": {"subtype": "success", "request_id": request_id,
                                     "response": reply},
                    })
                threading.Thread(target=settle, daemon=True).start()
                continue
            if kind == "result":
                cost = frame.get("total_cost_usd")
                with _OWNED_LOCK:
                    stopped = OWNED_STOPPING.pop(session_id, None) is not None
                # A stopped turn reports itself as an error, and it is not one:
                # it did what it was told. Said plainly, and not in the red that
                # the row keeps for a turn that actually went wrong.
                if stopped:
                    ok, message = True, "You stopped it"
                elif frame.get("is_error"):
                    said = frame.get("errors")
                    said = "; ".join(str(e) for e in said) if isinstance(said, list) and said else ""
                    ok, message = False, (said or str(frame.get("result") or "")
                                          or str(frame.get("subtype") or "It reported an error"))[:300]
                else:
                    ok = True
                    message = (f"The turn finished — ${cost:.4f}"
                               if isinstance(cost, (int, float)) else "The turn finished")
                with _OWNED_LOCK:
                    OWNED_BUSY.pop(session_id, None)
                    OWNED_LAST[session_id] = {"at": time.time(), "ok": ok,
                                              "message": message, "mode": held.get("mode")}
                # And straight into whatever was typed while it was answering.
                # Here rather than on the poll: the queue is drained by the thing
                # that knows the turn ended, so nothing waits a second for a
                # browser to notice, and a panel with no browser open drains too.
                owned_flush(session_id)
                continue
            if kind == "system":
                # What compaction is doing. See OWNED_COMPACT for the three
                # frames and why the turn's own result frame cannot say it.
                sub = frame.get("subtype")
                if sub == "init":
                    # What this session says it can be asked for, in its own
                    # words. The panel keeps a hand-written list of commands that
                    # live in the terminal's head (TERMINAL_ONLY) and it is right
                    # about the *messaging socket*, where slash commands are
                    # switched off entirely — but it is the wrong list for a held
                    # pipe, which expands them. The session settles it: an init
                    # frame down this transport reports 47 commands available and
                    # exactly two it keeps to itself.
                    #
                    #   "terminal_slash_commands": ["doctor", "color"]
                    #
                    # Read rather than guessed, and re-read on every init — a
                    # compaction sends a fresh one, and so does a new plugin.
                    with _OWNED_LOCK:
                        OWNED_COMMANDS[session_id] = {
                            "available": sorted(
                                str(x) for x in (frame.get("slash_commands") or [])
                                if isinstance(x, str)),
                            "terminalOnly": sorted(
                                str(x) for x in (frame.get("terminal_slash_commands") or [])
                                if isinstance(x, str)),
                        }
                if sub == "status" and frame.get("status") == "compacting":
                    with _OWNED_LOCK:
                        OWNED_COMPACT[session_id] = {"at": time.time(), "running": True}
                elif sub == "status" and (frame.get("compact_result")
                                          or frame.get("compact_error")):
                    failed = frame.get("compact_error")
                    with _OWNED_LOCK:
                        OWNED_COMPACT[session_id] = {
                            **(OWNED_COMPACT.get(session_id) or {}),
                            "at": time.time(), "running": False, "ok": not failed,
                            "message": str(failed)[:300] if failed else "",
                        }
                elif sub == "compact_boundary":
                    meta = frame.get("compact_metadata") or {}
                    with _OWNED_LOCK:
                        OWNED_COMPACT[session_id] = {
                            **(OWNED_COMPACT.get(session_id) or {}),
                            "at": time.time(), "running": False, "ok": True, "message": "",
                            "before": int(meta.get("pre_tokens") or 0),
                            "after": int(meta.get("post_tokens") or 0),
                            # `manual` is ours; `auto` is Claude Code compacting
                            # on its own, which the panel did not ask for and
                            # should still report rather than pretend to own.
                            "trigger": str(meta.get("trigger") or "manual"),
                        }
                continue
            if kind == "control_response":
                # Ours: the only control_request the panel sends unprompted is a
                # mode change, and its answer is what confirms the mode took.
                answered = (frame.get("response") or {}).get("response") or {}
                if isinstance(answered.get("mode"), str):
                    with _OWNED_LOCK:
                        held["mode"] = answered["mode"]
    except (OSError, ValueError):
        pass
    finally:
        # However it ended, it is no longer held.
        session_id = held.get("id") or session_id
        held["named"].set()
        with _OWNED_LOCK:
            if session_id and OWNED_PROCS.get(session_id) is held:
                OWNED_PROCS.pop(session_id, None)
            OWNED_BUSY.pop(session_id, None)
            OWNED_STOPPING.pop(session_id, None)
            # A compaction this process did not live to finish. The record stays
            # — how the last one went is worth keeping — but the running flag
            # cannot: nothing is coming to clear it, and the panel draws a
            # session with that flag up as *Compacting*, which it would then go
            # on saying about a session that is not running at all.
            going = OWNED_COMPACT.get(session_id) if session_id else None
            if going and going.get("running"):
                OWNED_COMPACT[session_id] = {
                    **going, "running": False, "ok": False,
                    "message": "the session stopped part way through",
                }
            OWNED_ASK.pop(session_id, None)
            _ASK_EVENTS.pop(session_id, None)
            # Whatever was still waiting outlives the process it was waiting
            # for: the promise was that it goes in, and the deliverer can start
            # the session back up to keep it. Deliberately letting go clears the
            # queue before it gets here, so this only fires for a process that
            # went on its own.
            left = OWNED_QUEUE.pop(session_id, None) or []
        own_errand(proc.pid, False)
        # `stderr` is a DEVNULL rather than a pipe, so it is `None` here — and
        # closing it threw, which took the rest of this block with it and left
        # anything still queued unhandled. The one place that showed was the
        # thread's own traceback, which nobody is reading.
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        for text in left:
            deliver_later(session_id, text)


def owned_hold(session_id: str, cwd: str, mode: str) -> tuple[bool, str]:
    """Start holding this session open, or say why it cannot be held."""
    if mode not in OWNED_MODES:
        return False, f"The panel does not run turns in {mode!r}"
    if owned_running(session_id):
        return True, "Already running here"
    if not cwd or not Path(cwd).is_dir():
        return False, f"Its folder is gone: {cwd}" if cwd else "That session has no folder"
    if not shutil.which("claude"):
        return False, "Cannot find the claude command on PATH"
    # Nothing to resume means nothing to hold: --resume fails on a session that
    # has never spoken, and it fails a second after being started rather than
    # visibly. A session the panel started and has not been typed at yet is the
    # one exception — it is named but empty, so it is started rather than
    # resumed, under the name it was given.
    empty = not has_conversation(session_id, cwd)
    argv = [
        shutil.which("claude") or "claude", "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        *(["--session-id", session_id] if empty else [f"--resume={session_id}"]),
        "--permission-mode", CLI_MODES.get(mode, mode),
        # Undocumented in --help, and the mechanism the official extension uses:
        # `stdio` means ask over this channel rather than at a terminal.
        "--permission-prompt-tool", "stdio",
    ]
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=top_level_env(), text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return False, f"Could not start it: {exc}"
    held = {"proc": proc, "write": threading.Lock(), "mode": mode, "cwd": cwd,
            "since": time.time(), "id": session_id, "named": threading.Event()}
    with _OWNED_LOCK:
        OWNED_PROCS[session_id] = held
    # A `claude` of ours on the session's own transcript would otherwise arrive
    # in the list as a second live session. See OWN_ERRANDS.
    own_errand(proc.pid, True)
    threading.Thread(target=owned_reader, args=(session_id, held), daemon=True).start()
    return True, "Running here"


def owned_new(cwd: str, mode: str) -> tuple[str | None, str]:
    """Start a brand new session the panel runs, and answer with its id.

    Interactive from the first word rather than adopted later. The panel names it
    — `--session-id` takes a uuid of our choosing — which is what lets the row
    exist before anything has been said: without a name of our own the process
    announces nothing until it is sent something, and the first message would
    have had to be asked for in a dialog before there was a session to type at.
    Now the session is simply there, and the first message is the first thing you
    type into it, like any other session.
    """
    if mode not in OWNED_MODES:
        return None, f"The panel does not run turns in {mode!r}"
    if not cwd or not Path(cwd).is_dir():
        return None, f"That folder is gone: {cwd}" if cwd else "There is no folder to start it in"
    claude = shutil.which("claude")
    if not claude:
        return None, "Cannot find the claude command on PATH"
    session_id = str(uuid.uuid4())
    argv = [
        claude, "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--session-id", session_id,
        "--permission-mode", CLI_MODES.get(mode, mode),
        "--permission-prompt-tool", "stdio",
    ]
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=top_level_env(), text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return None, f"Could not start it: {exc}"
    held = {"proc": proc, "write": threading.Lock(), "mode": mode, "cwd": cwd,
            "since": time.time(), "id": session_id, "named": threading.Event()}
    held["named"].set()
    with _OWNED_LOCK:
        OWNED_PROCS[session_id] = held
    own_errand(proc.pid, True)
    threading.Thread(target=owned_reader, args=(session_id, held), daemon=True).start()
    # The row has to exist for a session with no transcript yet, and the kept
    # record is the only thing that can carry it. Held, not pinned: it lasts as
    # long as the panel that is running it, which is as long as it means
    # anything. Pin it yourself and it outlives that too.
    keep_row({
        "sessionId": session_id, "name": os.path.basename(cwd) or "new session",
        "cwd": cwd, "startedAt": time.time(), "lastSeen": time.time(),
        "version": None, "kind": "interactive",
    })
    owned = load_owned()
    owned[session_id] = {"mode": mode, "here": True}
    save_owned(owned)
    return session_id, "Running here"


def owned_release(session_id: str) -> bool:
    """Stop holding a session, so something else can have its transcript."""
    with _OWNED_LOCK:
        held = OWNED_PROCS.pop(session_id, None)
        OWNED_BUSY.pop(session_id, None)
        OWNED_STOPPING.pop(session_id, None)
        # Letting go is deliberate, so what was queued for this session goes with
        # it rather than being restarted into a session somebody has just handed
        # back to a terminal.
        OWNED_QUEUE.pop(session_id, None)
    if not held:
        return False
    proc = held["proc"]
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    own_errand(proc.pid, False)
    return True


def owned_set_mode(session_id: str, mode: str) -> tuple[bool, str]:
    """Change the mode. On a held session this takes effect where it is running."""
    if mode not in OWNED_MODES:
        return False, f"The panel does not run turns in {mode!r}"
    owned = load_owned()
    owned[session_id] = {"mode": mode, "here": bool((owned.get(session_id) or {}).get("here"))}
    save_owned(owned)
    if not owned_running(session_id):
        return True, f"The next turn here runs in {mode}"
    sent = owned_write(session_id, {
        "type": "control_request",
        "request_id": f"mode-{uuid.uuid4()}",
        "request": {"subtype": "set_permission_mode", "mode": CLI_MODES.get(mode, mode)},
    })
    if not sent:
        return True, f"The next turn here runs in {mode}"
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
        if held:
            held["mode"] = mode
    return True, f"Now running in {mode}"


def owned_say(session_id: str, cwd: str, text: str, mode: str) -> tuple[bool, str]:
    """Send a turn to the held session, holding it first if it is not held yet."""
    if not text.strip():
        return False, "There is nothing to send"
    if not owned_running(session_id):
        ok, message = owned_hold(session_id, cwd, mode)
        if not ok:
            return False, message
    with _OWNED_LOCK:
        # Behind the turn in flight, or behind anything already waiting for it.
        # The second half matters: between a turn's result landing and the queue
        # draining there is an instant where nothing is running, and a message
        # sent into it would go down the pipe ahead of one typed before it. The
        # back of the queue is the only place either can go.
        wait = session_id in OWNED_BUSY or bool(OWNED_QUEUE.get(session_id))
        if not wait:
            OWNED_BUSY[session_id] = time.time()
    # Mid-turn is not a refusal. It is the ordinary case for anybody reading an
    # answer and thinking of the next thing to ask, and holding the message is
    # what the terminal's own prompt would have done with it.
    if wait:
        ok, said = owned_queue_add(session_id, text)
        # And the turn may have ended while that was being written down, in which
        # case nothing else is coming along to send it.
        if ok:
            owned_flush(session_id)
        return ok, said
    turn = user_turn(text)
    if owned_write(session_id, turn):
        return True, "Sent"
    # The pipe went between holding it and writing down it — a process that had
    # exited by the time we got here. Asking for the message to be sent again is
    # asking somebody to do what we can do ourselves: let go of the dead one,
    # start it back up, and write the same turn to that.
    with _OWNED_LOCK:
        OWNED_BUSY.pop(session_id, None)
    owned_release(session_id)
    ok, message = owned_hold(session_id, cwd, mode)
    if not ok:
        return False, message
    with _OWNED_LOCK:
        OWNED_BUSY[session_id] = time.time()
    if not owned_write(session_id, turn):
        with _OWNED_LOCK:
            OWNED_BUSY.pop(session_id, None)
        return False, "It would not take the message, even started back up"
    return True, "Started it back up and sent it"


def owned_resume_held() -> None:
    """Pick up every session that was interactive when the panel last ran.

    A held session is a process of ours, so it goes when the panel goes. A pinned
    one comes back: the row was written down, so it is still on the list after a
    restart, and a row saying *Runs from here* with nothing behind it reads as
    the interactive session having vanished when it was the panel that did.

    An unpinned one does not come back, and its claim to be running here goes
    with it — the row was only ever held for as long as the panel ran, and an
    `owned` entry saying *here* for a session with no row is a session nobody can
    see being run.

    Its **mode** stays. That is the part worth keeping across a restart whatever
    becomes of the row: it is a choice you made about this conversation, not a
    fact about the process that was serving it, and the session comes back into
    the mode it was left in rather than into the default. The whole record used
    to be dropped, which was harmless while `here` meant *adopted* and little
    else — but a turn run from the panel now sets it too, so dropping the record
    was quietly throwing away the mode of every session anyone had typed at.

    Anything that cannot be picked up is left alone rather than reported: a
    folder that has moved, a transcript that was cleared, a session somebody has
    since opened in a terminal. The row is still there to type at, and typing
    starts it back up.
    """
    pinned = load_pinned()
    owned = load_owned()
    stale = [key for key, entry in owned.items() if entry.get("here") and key not in pinned]
    if stale:
        for key in stale:
            owned[key] = {"mode": owned[key].get("mode") or OWNED_MODES[0], "here": False}
        save_owned(owned)
    for session_id, entry in owned.items():
        if not entry.get("here"):
            continue
        # Something else running it takes precedence — it holds the transcript,
        # and two processes on one conversation is the failure to avoid.
        if STORE.raw(session_id):
            continue
        cwd = str((pinned.get(session_id) or {}).get("cwd") or "")
        ok, said = owned_hold(session_id, cwd, entry.get("mode") or OWNED_MODES[0])
        # Flushed: this happens after the banner, on a stdout that is block
        # buffered the moment the panel is piped to a log or a service unit.
        print(f"note: {'running' if ok else 'could not run'} {session_id[:8]} here"
              + ("" if ok else f" — {said}"), flush=True)


def owned_release_all() -> None:
    """Let go of every held session, on the way out."""
    with _OWNED_LOCK:
        ids = list(OWNED_PROCS)
    for session_id in ids:
        owned_release(session_id)


# ------------------------------------------------------------- folder chooser
# A new session in a folder no session is in yet needs a folder from outside the
# panel's own list, and a path typed into the browser is exactly what /api/new
# refuses to accept. A chooser on this machine sidesteps the question rather
# than answering it: the browser can ask for the dialog but cannot say what it
# returns, so the folder is still chosen by the person at the desk, in a window
# the desktop drew, and the panel never takes a path from a request.
#
# Whatever the desktop already has, in the order a desktop would prefer:
# zenity under GNOME, kdialog under KDE, and Tk — which is stdlib, so it is
# always there — as the fallback. The Tk one runs as its own process because a
# toolkit main loop wants a main thread, and this is called from a request.
GUI_PICKERS = (
    ("zenity", lambda start: ["zenity", "--file-selection", "--directory",
                              "--title=Folder for the new session", f"--filename={start}/"]),
    ("kdialog", lambda start: ["kdialog", "--getexistingdirectory", start,
                               "--title", "Folder for the new session"]),
)
# Written as source rather than a file on disk: it is three lines, and a helper
# script beside the server would be one more thing to keep in step with it.
TK_PICKER = (
    "import sys, tkinter, tkinter.filedialog;"
    "root = tkinter.Tk(); root.withdraw();"
    "path = tkinter.filedialog.askdirectory(initialdir=sys.argv[1],"
    " title='Folder for the new session', mustexist=True);"
    "sys.stdout.write(path or '')"
)
# One dialog at a time. A second one is a second window nobody asked for, on a
# desktop that may not even raise it.
PICKER_LOCK = threading.Lock()
PICKER_SECONDS = 300.0


def picker_argv(start: str) -> list[str] | None:
    """The chooser this desktop can show, or None if it cannot show one."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    for name, build in GUI_PICKERS:
        if shutil.which(name):
            return build(start)
    try:
        import tkinter  # noqa: F401  — asking whether it is installed, not using it
    except ImportError:
        return None
    return [sys.executable, "-c", TK_PICKER, start]


def can_pick_folder() -> bool:
    return picker_argv(str(HOME)) is not None


def pick_folder(start: str) -> tuple[str | None, str]:
    """Ask the desktop for a folder. (path, message) — path is None if it did not."""
    argv = picker_argv(start)
    if not argv:
        return None, "No folder chooser on this desktop — install zenity, or Python's tkinter"
    if not PICKER_LOCK.acquire(blocking=False):
        return None, "The folder chooser is already open — it is waiting on the desktop"
    try:
        done = subprocess.run(
            argv, cwd=start, capture_output=True, text=True, timeout=PICKER_SECONDS,
            env=top_level_env(),
        )
    except subprocess.TimeoutExpired:
        return None, "The folder chooser was left open too long"
    except OSError as exc:
        return None, f"Could not open a folder chooser: {exc}"
    finally:
        PICKER_LOCK.release()
    # Cancelling is not a failure, and it is the same exit code as a real one —
    # so it is told apart by there being nothing on stdout rather than by status.
    path = done.stdout.strip().splitlines()[0].strip() if done.stdout.strip() else ""
    if not path:
        return None, "No folder picked"
    if not Path(path).is_dir():
        return None, f"That is not a folder: {path}"
    return str(Path(path).resolve()), "Picked"


# ------------------------------------------------------- getting a message in
# A session is reachable over its messaging socket, over the pipe of a turn the
# panel runs, or — if nothing is running it — after being started back up. Which
# of those applies changes from second to second: a session closes while the box
# is being typed into, a socket goes with a machine that suspended, a process
# comes up two seconds after the click.
#
# So nothing that takes a message decides in advance whether it can be delivered.
# The message is accepted, and this is what carries it: it re-asks every second,
# starts the session back up when nothing is running it, and drains in the order
# things were typed. A message is never refused for the state a session happened
# to be in at the moment Send was pressed, because that state is not the one it
# will be delivered in.

_PENDING: dict[str, list[str]] = {}
_PENDING_LOCK = threading.Lock()
DELIVER_SECONDS = 120.0


def session_listening(data: dict | None) -> bool:
    """Whether a message sent over this session's socket right now would be read.

    The three things the snapshot's `canSay` asks, in one place so the deliverer
    and the row cannot disagree: a process seen recently, a protocol we know, and
    a socket still on disk.
    """
    if not data:
        return False
    sock = data.get("messagingSocketPath")
    return bool(
        time.time() - data.get("seenAt", 0) < LIVE_SECONDS
        and data.get("peerProtocol") == PEER_PROTOCOL
        and sock
        and Path(sock).exists()
    )


def session_alive(data: dict | None) -> bool:
    """Whether something is running this session — listening or not.

    Kept apart from session_listening because the two want opposite things: a
    session that is up but not listening must be waited for, and only one that is
    not up at all may be started back up. Confusing them is how a second process
    lands on a transcript that already has one.
    """
    return bool(data) and time.time() - data.get("seenAt", 0) < LIVE_SECONDS


def resume_entry(session_id: str) -> dict:
    """What start_session needs to bring a session back: its id and its folder.

    The folder comes off the kept row first and the session file second. A
    session that has just closed still has a file for a few seconds, and a row
    that outlived one has no file at all, so between them one of the two answers.
    """
    entry = kept_rows().get(session_id) or {}
    data = STORE.raw(session_id) or {}
    return {"sessionId": session_id, "cwd": entry.get("cwd") or data.get("cwd") or ""}


def deliver_later(session_id: str, text: str, started: bool = False) -> tuple[bool, str]:
    """Take a message for a session that cannot read it this instant.

    Returns what to tell the person who typed it — which is a promise, not a
    refusal, and says which of the two things is about to happen. `started` is
    for callers that have already opened a terminal themselves: it stops this
    from opening a second one.
    """
    text = text.strip()
    if not text:
        return False, "Nothing to send"
    owned = load_owned().get(session_id) or {}
    alive = session_alive(STORE.raw(session_id))
    will_start = not started and not alive and not owned.get("here")
    if will_start and not resume_entry(session_id)["cwd"]:
        # Nothing to resume it in. The one case with no way through, and it is
        # about a folder rather than about the message.
        return False, "There is no folder to start it in"
    with _PENDING_LOCK:
        queue = _PENDING.setdefault(session_id, [])
        queue.append(text)
        first = len(queue) == 1
    if first:
        threading.Thread(
            target=_deliver_loop, args=(session_id, started), daemon=True,
        ).start()
    if will_start:
        return True, "Starting it back up — this goes in as soon as it is listening"
    return True, "Held for it — this goes in as soon as it is listening"


def _pending_head(session_id: str) -> str | None:
    with _PENDING_LOCK:
        queue = _PENDING.get(session_id) or []
        return queue[0] if queue else None


def _pending_drop(session_id: str, text: str) -> None:
    """Take a delivered message off the queue, leaving anything typed after it."""
    with _PENDING_LOCK:
        queue = _PENDING.get(session_id) or []
        if queue and queue[0] == text:
            queue.pop(0)
        if not queue:
            _PENDING.pop(session_id, None)


def _deliver_loop(session_id: str, started: bool) -> None:
    """Try every second until the queue is empty or the wait runs out.

    Only one of these runs per session, so the queue keeps the order things were
    typed in, and a second message never overtakes the first by finding a socket
    a moment sooner.
    """
    deadline = time.time() + DELIVER_SECONDS
    while time.time() < deadline:
        text = _pending_head(session_id)
        if text is None:
            break
        data = STORE.raw(session_id)
        owned = load_owned().get(session_id) or {}
        if owned.get("here"):
            # The panel's own: its channel is the pipe, and owned_say holds it
            # again first if it is not up. Never a terminal for this one — that
            # would take the transcript off a process that has it.
            ok, _ = owned_say(session_id, resume_entry(session_id)["cwd"], text,
                              owned.get("mode") or OWNED_MODES[0])
            if ok:
                _pending_drop(session_id, text)
                continue
        elif session_listening(data):
            ok, _ = say_to_session(data, text)
            if ok:
                _pending_drop(session_id, text)
                continue
        elif not started and not session_alive(data):
            # Nothing is running it, so there is nothing to wait for: bring it
            # back and go on waiting for the socket it opens. Once only, however
            # long the rest of this takes.
            started = True
            with _OWNED_LOCK:
                mid_turn = session_id in OWNED_BUSY
            if not mid_turn and not start_session(resume_entry(session_id))[0]:
                break
        time.sleep(1.0)
    # Whatever is left is dropped rather than held: a message typed two minutes
    # ago, delivered into whatever the session is doing now, is a surprise.
    with _PENDING_LOCK:
        _PENDING.pop(session_id, None)


def window_exists(window_id: str) -> bool:
    return any(w["id"].lower() == window_id.lower() for w in WINDOWS.windows())


def window_title(window_id: str) -> str:
    for window in WINDOWS.windows():
        if window["id"].lower() == window_id.lower():
            return window["title"]
    return ""


def activate(window_id: str) -> tuple[bool, str]:
    if not shutil.which("xdotool"):
        return False, "xdotool is not installed"
    try:
        result = subprocess.run(
            ["xdotool", "windowactivate", "--sync", window_id],
            capture_output=True, text=True, timeout=6,
        )
    except subprocess.TimeoutExpired:
        return False, "xdotool timed out"
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        subprocess.run(["xdotool", "windowraise", window_id], capture_output=True, timeout=6)
        return False, (result.stderr or "could not activate the window").strip()
    return True, "focused"


# How long to wait for a terminal to have retitled its window, and how often to
# look. A local terminal repaints in a frame or two; the ceiling is for a laden
# machine, and reaching it means the answer is no.
PROBE_TIMEOUT = 1.6
PROBE_STEP = 0.12


def probe_window(tty: str) -> tuple[str | None, str]:
    """Identify the window a pty is displayed in, by briefly retitling it.

    Nothing about an X window says which pty it is showing, and for a terminal
    with one process behind every window the pid says nothing either. So ask the
    terminal: writing an OSC title sequence to the pty is output, the way any
    program's output is, and the terminal answers by retitling the window that
    is showing it. Whichever window comes back wearing our marker is the one.

    The marker is pushed and popped on the xterm title stack, so the title the
    session had is put back exactly — including one Claude Code rewrites as it
    works. A terminal without the stack ignores both, and the recorded title is
    written back by hand instead.
    """
    if not WINDOWS.available():
        return None, "Window probing needs X11 and xdotool"
    marker = f"watchtower-probe-{uuid.uuid4().hex[:12]}"
    before = {w["id"]: w["title"] for w in WINDOWS.windows(force=True)}
    try:
        with open(tty, "w") as terminal:
            # Push the current title, then claim it.
            terminal.write(f"\033[22;2t\033]2;{marker}\007")
    except OSError as exc:
        return None, f"Could not write to {tty}: {exc}"

    found = None
    deadline = time.time() + PROBE_TIMEOUT
    while time.time() < deadline:
        time.sleep(PROBE_STEP)
        for window in WINDOWS.windows(force=True):
            if window["title"] == marker:
                found = window
                break
        if found:
            break

    try:
        with open(tty, "w") as terminal:
            terminal.write("\033[23;2t")  # pop it back
            if found and before.get(found["id"]):
                # Belt and braces, for a terminal with no title stack.
                if any(w["title"] == marker for w in WINDOWS.windows(force=True)):
                    terminal.write(f"\033]2;{before[found['id']]}\007")
    except OSError:
        pass
    WINDOWS.windows(force=True)

    if not found:
        return None, (
            "The terminal did not retitle a window — if this session is in a "
            "background tab, bring it to the front and try again, or pair by hand"
        )
    return found["id"], "identified"


def select_window() -> tuple[str | None, str]:
    """Block until the person clicks a window, then return its id."""
    if not shutil.which("xdotool"):
        return None, "xdotool is not installed"
    try:
        result = subprocess.run(
            ["xdotool", "selectwindow"], capture_output=True, text=True, timeout=45
        )
    except subprocess.TimeoutExpired:
        return None, "No window was clicked within 45 seconds"
    except OSError as exc:
        return None, str(exc)
    raw = (result.stdout or "").strip()
    if not raw.isdigit():
        return None, (result.stderr or "No window id came back").strip()
    return hex(int(raw)), "paired"


def identify_and_pair(session_id: str, session: dict) -> tuple[str | None, str]:
    """Probe for a session's window and remember the answer.

    The probe costs a title flicker, so its result is written to pairs.json
    like one you made by clicking: a session is identified once, not on every
    poll, and the answer survives a restart of the panel.
    """
    tty = session.get("tty")
    if not tty:
        return None, "This session is not attached to a terminal the panel can reach"
    window_id, message = probe_window(tty)
    if not window_id:
        return None, message
    pairs = load_pairs()
    pairs[session_id] = {"id": window_id, "how": "identified"}
    save_pairs(pairs)
    return window_id, message


def resolve_window(session_id: str, session: dict) -> tuple[dict | None, str, bool]:
    """The window to act on, identifying it first if the guess was a tie.

    Returns the window, a message for when there is none, and whether the probe
    is what found it — the caller says so, because a title that flickered wants
    explaining.
    """
    window = session.get("window")
    if window and window.get("confidence") != "ambiguous":
        return window, "", False
    if session.get("tty"):
        window_id, message = identify_and_pair(session_id, session)
        if window_id:
            return {"id": window_id, "confidence": "identified"}, "", True
        if window:
            # A tie is still a guess, and a guess is worse than saying so.
            return None, f"Could not tell this session's window from the others — {message}", False
        return None, message, False
    if window:
        return None, "Several windows look equally likely and there is no pty to tell them apart", False
    return None, "No window is paired with this session yet", False


# ---------------------------------------------------------------- pasted pictures


# A message over the wire is text, and it always will be: the messaging socket
# takes a string, and so does a held pipe. So a picture cannot travel *in* the
# message — but a path can, and a session with the Read tool can open the file
# the path names. That is the whole of this feature: the picture lands in a file
# on the same machine the session is running on, and the message says where.
#
# Where it lands matters. Not /tmp, which is swept from under a session that
# comes back to the conversation tomorrow, and not the panel's own config dir,
# which a session may not be allowed to read: it goes in the session's own
# folder, under .claude, because that is a place the session already reads from
# and already has permission for.
PASTE_DIR_NAME = ".claude/watchtower-images"
# What a browser hands over when a screenshot is pasted, and nothing else. The
# extension is taken from this list rather than from anything the request says,
# so no name in the payload can decide what kind of file gets written.
PASTE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
# Big enough for a full-screen screenshot at 4K, small enough that a stuck
# clipboard cannot fill the disk one paste at a time.
PASTE_MAX_BYTES = 12 * 1024 * 1024
# A pasted picture is worth keeping as long as the conversation that mentions
# it is being read, and no longer. Each paste sweeps what an earlier one left
# behind, so the folder does not grow for the rest of the machine's life.
PASTE_KEEP_SECONDS = 14 * 24 * 3600
# base64 costs a third on top, and the JSON around it a little more.
POST_MAX_BYTES = PASTE_MAX_BYTES * 4 // 3 + 65536


def paste_dir(cwd: str) -> Path:
    return Path(cwd) / PASTE_DIR_NAME


def sweep_pastes(folder: Path) -> None:
    """Drop pictures older than PASTE_KEEP_SECONDS. Failure is not worth a word."""
    cutoff = time.time() - PASTE_KEEP_SECONDS
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass


def save_pasted_image(cwd: str, mime: str, data: str) -> tuple[bool, str, str]:
    """Write one pasted picture into the session's folder.

    Returns (ok, path-or-empty, message). The name is ours — a timestamp and a
    short random tail — because a name from the request is a name a request
    could aim somewhere else.
    """
    suffix = PASTE_TYPES.get(mime.split(";", 1)[0].strip().lower())
    if not suffix:
        return False, "", "That is not a kind of picture the panel can save"
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        return False, "", "That picture did not arrive in one piece"
    if not raw:
        return False, "", "That picture is empty"
    if len(raw) > PASTE_MAX_BYTES:
        return False, "", f"That picture is larger than {PASTE_MAX_BYTES // (1024 * 1024)} MB"
    folder = paste_dir(cwd)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return False, "", f"Could not write into that session's folder — {error}"
    sweep_pastes(folder)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    path = folder / f"paste-{stamp}-{uuid.uuid4().hex[:6]}{suffix}"
    try:
        path.write_bytes(raw)
    except OSError as error:
        return False, "", f"Could not write the picture — {error}"
    return True, str(path), "Picture saved"


# ------------------------------------------------------------------ session store


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
                "canStart": SAY_ENABLED,
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
            "canSend": SAY_ENABLED,
            "canPickFolder": can_pick_folder(),
            # Keyed by session rather than folded into each row: only the
            # handful the panel has ever run a turn for have anything to say.
            "owned": owned_state(),
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


# ------------------------------------------------------------------- http server


class Handler(BaseHTTPRequestHandler):
    server_version = "claude-watchtower"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # --- helpers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            # A pasted picture arrives base64'd inside the JSON, which is the one
            # thing here big enough to be worth a ceiling. Over it, the body is
            # read and thrown away — read, so the connection stays usable, and
            # thrown away, so nothing decides to hold 200 MB in memory because a
            # header said to.
            if length > POST_MAX_BYTES:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                return {"oversize": True}
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _session_by_id(self, session_id: str) -> dict | None:
        for session in STORE.snapshot()["sessions"]:
            if session["sessionId"] == session_id:
                return session
        return None

    def _row_for(self, session_id: str) -> dict | None:
        """The kept row for this session, keeping it now if it is not kept yet.

        Every path that runs something on a session needs the same two things —
        an id and a folder — and a kept row is where they live once the process
        is gone. But a session whose terminal has closed is still on the list for
        a while without being kept, and both *Run it here* and *In a terminal*
        are offered on such a row: nothing holds its transcript, which is the
        only condition either one cares about. Refusing them with "that session
        is not being kept" reported an internal bookkeeping state as if it were
        a fact about the session.

        So the row is kept here, as part of acting on it. It is the same thing
        adopting does before it signals anything, and for the same reason: the
        row is what carries the id and the folder once the session file goes.
        """
        entry = kept_rows().get(session_id)
        if entry:
            return entry
        session = self._session_by_id(session_id)
        if not session or not session.get("cwd"):
            return None
        keep_row({
            "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
            "startedAt": session["startedAt"], "lastSeen": time.time(),
            "version": session["version"], "kind": session["kind"],
        })
        return kept_rows().get(session_id)

    def _session_repo(self, session_id: str) -> str | None:
        """The working tree a git request may act in — the session's own, or none.

        The root never comes from the request. Everything git runs against what
        the panel already discovered for the session it was asked about, so no
        request can point git at a repository the panel is not showing.
        """
        session = self._session_by_id(session_id)
        return (session or {}).get("repoRoot") or None

    # --- routes

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._json(STORE.snapshot())
            return
        if path == "/api/transcript":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            try:
                limit = max(1, min(TRANSCRIPT_LIMIT_MAX, int((query.get("limit") or ["60"])[0])))
            except ValueError:
                limit = 60
            self._json(read_transcript(session_id, session["cwd"], limit))
            return
        if path == "/api/change":
            # The whole of one file change, for a preview in the chat that was
            # clicked. A read of the same transcript the chat came from, so it
            # needs nothing the chat did not already have.
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            found = read_change(session_id, session["cwd"], (query.get("id") or [""])[0])
            self._json(found, 200 if found["ok"] else 404)
            return
        if path == "/api/usage":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            self._json(read_usage(session_id, session["cwd"]))
            return
        if path == "/api/commands":
            # A session that has gone still leaves the folders it could be asked
            # about, so a missing one is answered with what is true of every
            # session — your own skills and commands — rather than a 404.
            query = parse_qs(urlparse(self.path).query)
            session = self._session_by_id((query.get("sessionId") or [""])[0])
            self._json(read_catalog((session or {}).get("cwd")))
            return
        if path == "/api/plan":
            # Reading this runs a command on this machine, which is the same order
            # of risk as the panel's other errands — so it sits behind the same
            # loopback gate, however read-only the answer is.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Reading your plan is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            query = parse_qs(urlparse(self.path).query)
            self._json(read_plan((query.get("force") or [""])[0] == "1"))
            return
        if path == "/api/git":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            root = session.get("repoRoot")
            if not root:
                self._json({"ok": False, "isRepo": False,
                            "message": "This session's folder is not in a git repository"})
                return
            self._json({**read_git(root), "isRepo": True, "canWrite": SAY_ENABLED})
            return
        if path == "/api/git/diff":
            query = parse_qs(urlparse(self.path).query)
            root = self._session_repo((query.get("sessionId") or [""])[0])
            if not root:
                self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
                return
            file_path = (query.get("path") or [""])[0]
            if not file_path:
                self._json({"ok": False, "message": "No file asked for"}, 400)
                return
            self._json(read_diff(root, file_path, (query.get("staged") or [""])[0] == "1"))
            return
        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if path.startswith(("/fonts/", "/vendor/")):
            self._serve_static(path)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        payload = self._body()
        session_id = str(payload.get("sessionId") or "")

        if path == "/api/focus":
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            window, why, identified = resolve_window(session_id, session)
            if not window:
                self._json({"ok": False, "message": why, "needsPairing": True}, 409)
                return
            WINDOWS.windows(force=True)
            ok, message = activate(window["id"])
            if ok and identified:
                message = "focused — and this session's window is now remembered"
            self._json({"ok": ok, "message": message, "window": window["id"],
                        "identified": identified}, 200 if ok else 500)
            return

        if path == "/api/identify":
            # Pairing without the click: the session's own terminal is asked
            # which window it is showing. See probe_window.
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            window_id, message = identify_and_pair(session_id, session)
            if not window_id:
                self._json({"ok": False, "message": message, "needsPairing": True}, 409)
                return
            self._json({"ok": True, "message": "Found it — window identified and remembered",
                        "window": window_id, "title": window_title(window_id)})
            return

        if path == "/api/pair":
            if not self._session_by_id(session_id):
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            window_id, message = select_window()
            if not window_id:
                self._json({"ok": False, "message": message}, 400)
                return
            pairs = load_pairs()
            pairs[session_id] = {"id": window_id, "how": "picked"}
            save_pairs(pairs)
            WINDOWS.windows(force=True)
            self._json({"ok": True, "message": "Window paired", "window": window_id})
            return

        if path == "/api/end":
            data = STORE.raw(session_id)
            if not data:
                # A session the panel holds has no session file of its own to
                # signal — the process is ours. Ending it means letting go.
                if owned_release(session_id):
                    owned = load_owned()
                    if session_id in owned:
                        # The claim to be running it goes; the mode it was in
                        # stays. A pinned row survives this, and typing into it
                        # starts it back up — into the mode you left it in, not
                        # into the default, which is what dropping the whole
                        # record had it doing.
                        owned[session_id] = {"mode": owned[session_id].get("mode") or OWNED_MODES[0],
                                             "here": False}
                        save_owned(owned)
                    # Stopping is also removing. Only a pinned row is worth
                    # keeping once nothing is running behind it.
                    gone = drop_unpinned_row(session_id)
                    self._json({"ok": True, "removed": gone,
                                "message": "Stopped it and took the row off the list" if gone
                                           else "Stopped running it here — pinned, so the row stays"})
                    return
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            ok, message = end_process(data.get("pid"), data.get("procStart"), bool(payload.get("force")))
            gone = False
            if ok:
                # The window pairing dies with the session it pointed at.
                pairs = load_pairs()
                if pairs.pop(session_id, None) is not None:
                    save_pairs(pairs)
                # And so does the row, unless it was pinned: a session the panel
                # only knows because it was running is not worth a row once it
                # is not. A kept row is let go here; an unkept one has nothing
                # holding it and drops off as the process goes.
                gone = drop_unpinned_row(session_id)
                if gone:
                    message = "Ended it and took the row off the list"
            self._json({"ok": ok, "removed": gone, "message": message}, 200 if ok else 409)
            return

        if path == "/api/say":
            # A prompt is an instruction to an agent with tools, so this endpoint
            # is worth more than the others put together. It stays on loopback
            # even when the rest of the panel is served to the network.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Sending is off because the panel is not bound to loopback"}, 403)
                return
            text = str(payload.get("text") or "").strip()
            if not text:
                self._json({"ok": False, "message": "Nothing to send"}, 400)
                return
            data = STORE.raw(session_id)
            if not data and session_id not in kept_rows():
                self._json({"ok": False, "message": "There is no such session"}, 404)
                return
            # The straight road: it is up and listening, so the message goes down
            # its socket and the answer is immediate.
            if session_listening(data):
                ok, message = say_to_session(data, text)
                if ok:
                    self._json({"ok": True, "message": message})
                    return
            # And every other case is the same case. It closed, or it never opened
            # a socket, or the socket went, or it is coming up as we speak: the
            # message is held and delivered when it can be, and the session is
            # started back up if nothing is running it. This is why the composer
            # has no "not listening" dead end left — there is nothing that state
            # would save the person from.
            ok, message = deliver_later(session_id, text)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/paste-image":
            # The same gate as sending, because this is part of sending: a
            # picture is only ever saved to be named in a message, and writing a
            # file into somebody's checkout is not something to offer the
            # network either.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Sending is off because the panel is not bound to loopback"}, 403)
                return
            if payload.get("oversize"):
                self._json({"ok": False, "message": f"That picture is larger than "
                                                    f"{PASTE_MAX_BYTES // (1024 * 1024)} MB"}, 413)
                return
            # The folder is the session's own, and it is never taken from the
            # request: a live session's cwd, or the folder its kept row carries
            # once the process is gone. There is nowhere else a paste can land.
            session = self._session_by_id(session_id)
            cwd = str((session or {}).get("cwd") or (kept_rows().get(session_id) or {}).get("cwd") or "")
            if not cwd:
                self._json({"ok": False, "message": "There is no folder to save that picture in"}, 404)
                return
            ok, saved, message = save_pasted_image(cwd, str(payload.get("mime") or ""),
                                                   str(payload.get("data") or ""))
            if not ok:
                self._json({"ok": False, "message": message}, 400)
                return
            self._json({"ok": True, "path": saved, "message": message})
            return

        if path == "/api/owned/mode":
            # Nothing runs here. The mode is remembered, and the next turn the
            # panel launches is launched with it — which is the whole reason
            # switching is instant.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            mode = str(payload.get("mode") or "")
            if mode not in OWNED_MODES:
                self._json({"ok": False, "message": f"The panel does not run turns in {mode!r}"}, 400)
                return
            ok, message = owned_set_mode(session_id, mode)
            self._json({"ok": ok, "message": message, "mode": mode}, 200 if ok else 400)
            return

        if path == "/api/owned/adopt":
            # Taking a live session's turns over. There is exactly one way to do
            # it and it is not a gentle one: the transcript is held by a process
            # in a terminal, and nothing can run a turn on it while that is true.
            # So the row is kept first, then the process is ended, and what is
            # left is the same conversation with nobody holding it — which is the
            # state a panel turn needs.
            #
            # This was built once before and backed out, for reasons worth
            # keeping in mind: it killed the session and only cleared the way,
            # leaving a row whose most prominent button handed it straight back
            # to a terminal. What makes it safe now is that the panel can
            # actually take the next turn, that ending is asked about rather than
            # assumed, and that every path which starts a process on a transcript
            # checks who already holds it.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            session = self._session_by_id(session_id)
            data = STORE.raw(session_id)
            if not session or not data:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            # A session that has never taken a turn has no transcript, and
            # `--resume` on it fails with "No conversation found". That used to
            # be a refusal — send it something first — which is backwards: an
            # empty session is the one with nothing to lose, and being told to
            # type into the terminal in order to stop using the terminal makes
            # no sense. So it is taken over like any other, and started under
            # its own id rather than resumed, which is what owned_hold already
            # does for a session with nothing to resume.
            empty = not has_conversation(session_id, session.get("cwd") or "")
            # Kept *before* the process goes. The row is the only thing that
            # carries the folder and the id once the session file is gone, and
            # without it the conversation would drop off the panel on the way.
            keep_row({
                "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
                "startedAt": session["startedAt"], "lastSeen": time.time(),
                "version": session["version"], "kind": session["kind"],
            })
            force = bool(payload.get("force"))
            ok, message = end_process(data.get("pid"), data.get("procStart"), force)
            if not ok:
                # It is still running, so nothing has changed except that the row
                # is now kept — which is harmless and is what the panel would
                # have needed anyway.
                self._json({"ok": False, "message": f"Kept the row, but it is still running: {message}"}, 409)
                return
            # Signalled is not stopped. Waiting for it to actually go is the
            # difference between this and the takeover that shipped once and
            # cleared the way without ever freeing the transcript — the next
            # thing the panel does is run a turn on it, and that refuses while
            # anything still holds it.
            pid = data.get("pid")
            for _ in range(40):
                if not isinstance(pid, int) or proc_gone(pid):
                    break
                time.sleep(0.25)
            else:
                self._json({"ok": False, "needsForce": not force,
                            "message": "It has not stopped. Force it to end, or let it finish "
                                       "what it is doing and try again"}, 409)
                return
            pairs = load_pairs()
            if pairs.pop(session_id, None) is not None:
                save_pairs(pairs)
            # The row is wanted back as a kept row now, not in twenty seconds.
            STORE.forget(session_id)
            # The mode its first panel turn will run in, unless one was already
            # picked for it. Manual: the one that asks, now that asking works.
            owned = load_owned()
            owned[session_id] = {
                "mode": (owned.get(session_id) or {}).get("mode") or OWNED_MODES[0],
                "here": True,
            }
            save_owned(owned)
            mode = (owned.get(session_id) or {}).get("mode") or OWNED_MODES[0]
            up, said = owned_hold(session_id, session.get("cwd") or "", mode)
            self._json({"ok": True, "running": up,
                        "message": ("Running here now — it had said nothing, so it starts here empty"
                                    if empty else "Running here now") if up
                                   else f"Ended the terminal session, but it did not start here: {said}"})
            return

        if path == "/api/owned/new":
            # A new session the panel runs from its first word. The folder comes
            # off a session already on the list, or out of a chooser on this
            # machine — never as a path in the request, which is the same rule
            # /api/new keeps.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            if payload.get("pick"):
                folders = {x["cwd"] for x in STORE.snapshot()["sessions"] if x.get("cwd")}
                cwd, why = pick_folder(folders.pop() if len(folders) == 1 else str(HOME))
                if not cwd:
                    self._json({"ok": False, "cancelled": True, "message": why}, 200)
                    return
            else:
                session = self._session_by_id(session_id)
                entry = kept_rows().get(session_id) or {}
                cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
                if not cwd:
                    self._json({"ok": False, "message": "There is no folder to start it in"}, 404)
                    return
            mode = str(payload.get("mode") or OWNED_MODES[0])
            made, message = owned_new(cwd, mode)
            self._json({"ok": bool(made), "message": message, "sessionId": made, "cwd": cwd},
                       200 if made else 409)
            return

        if path == "/api/owned/answer":
            # Answering a prompt a panel turn raised. Same gate as running the
            # turn: this decides what a session holding tools is allowed to do,
            # which is the sharpest thing the panel does.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            behavior = "allow" if payload.get("behavior") == "allow" else "deny"
            answers = payload.get("answers")
            decision = {
                "behavior": behavior,
                "message": str(payload.get("message") or "")[:300],
                "answers": answers if isinstance(answers, dict) else None,
            }
            ok, message = answer_from_panel(session_id, str(payload.get("requestId") or ""), decision)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/owned/say":
            # Same gate as /api/say, and for a sharper version of the same
            # reason: this one does not hand a message to a session someone
            # else is running, it runs the session.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            # A live process holds the transcript, and nothing here will take it
            # off one. The row's own End is how you free it, deliberately.
            if STORE.raw(session_id):
                self._json({"ok": False, "message": "Something is already running this session — "
                                                    "end it first, or send to it instead"}, 409)
                return
            entry = self._row_for(session_id)
            if not entry:
                self._json({"ok": False, "message": "There is no folder to run that session in"}, 404)
                return
            cwd = str(entry.get("cwd") or "")
            owned = load_owned()
            mode = str(payload.get("mode") or (owned.get(session_id) or {}).get("mode") or OWNED_MODES[0])
            text = str(payload.get("text") or "")
            # No message, just run it. *Start it up* on the row menu asks for
            # exactly this and was being told there was nothing to send: holding
            # the session open is the whole of starting it, and a turn is what a
            # message adds rather than what makes the session run.
            ok, message = (owned_hold(session_id, cwd, mode) if not text.strip()
                           else owned_say(session_id, cwd, text, mode))
            if ok:
                # `here` is set by the turn, not only by adopting. It was not,
                # and the gap is what made a session go strange under you: the
                # panel would hold the process and run the turn while the record
                # still said the session was nobody's, so the moment the status
                # left `stopped` the row lost its mode chips and offered to make
                # interactive a session it was in the middle of running.
                owned[session_id] = {"mode": mode, "here": True}
                save_owned(owned)
            self._json({"ok": ok, "message": message, "mode": mode}, 200 if ok else 409)
            return

        if path == "/api/owned/compact":
            # Summarise the conversation so far and carry on from the summary.
            #
            # `/compact` is on the panel's TERMINAL_ONLY list, and rightly: a
            # message over a session's *messaging socket* is queued with slash
            # commands switched off, so sending the text there does nothing. A
            # held pipe is the other transport and it does expand them — checked
            # against 2.1.239, which answered a `/compact` turn with
            # `compact_boundary` and 24,071 → 3,661 tokens. So this is not the
            # composer sending text that happens to start with a slash; it is
            # its own action, on the one transport where it works.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            if STORE.raw(session_id):
                self._json({"ok": False, "message": "Something else is running this session — "
                                                    "make it interactive first"}, 409)
                return
            entry = self._row_for(session_id)
            if not entry:
                self._json({"ok": False, "message": "There is no folder to run that session in"}, 404)
                return
            # Not while anything is running or waiting. `owned_say` would queue
            # it, which is right for a message and wrong for this: a compaction
            # is not typed ahead, it rewrites what the session remembers, and
            # queueing one would report *Compacting…* for a compaction that had
            # not started and would fire later without being asked again.
            with _OWNED_LOCK:
                busy = session_id in OWNED_BUSY or bool(OWNED_QUEUE.get(session_id))
                already = bool((OWNED_COMPACT.get(session_id) or {}).get("running"))
            if busy:
                self._json({"ok": False, "message": "It is mid-turn — let that finish, then "
                                                    "compact"}, 409)
                return
            if already:
                self._json({"ok": False, "message": "It is already compacting"}, 409)
                return
            owned = load_owned()
            mode = str((owned.get(session_id) or {}).get("mode") or OWNED_MODES[0])
            ok, message = owned_say(session_id, str(entry.get("cwd") or ""), "/compact", mode)
            if ok:
                with _OWNED_LOCK:
                    # Said now rather than waited for. The first `compacting`
                    # frame does not arrive instantly, and a button that goes
                    # back to looking unpressed in the meantime invites a second
                    # press — which would queue a second compaction behind the
                    # first.
                    OWNED_COMPACT[session_id] = {"at": time.time(), "running": True}
                owned[session_id] = {"mode": mode, "here": True}
                save_owned(owned)
                message = "Compacting — it summarises the conversation and carries on from that"
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/owned/interrupt":
            # Stopping a turn acts on a process on this machine, so it sits
            # behind the same loopback gate as starting one.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Stopping a turn is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            ok, message = owned_interrupt(session_id)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/owned/unqueue":
            # Taking back something typed ahead. Behind the same gate as sending
            # it, for the plainest of reasons: nothing that cannot type at a
            # session has anything to untype.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Running turns here is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            raw = payload.get("index")
            index = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
            ok, message = owned_unqueue(session_id, index)
            self._json({"ok": ok, "message": message, "queued": owned_queued(session_id)},
                       200 if ok else 409)
            return

        if path == "/api/git":
            # Committing, pushing and discarding change a checkout on this
            # machine, which is the same order of risk as prompting the session
            # that lives in it — so they sit behind the same loopback gate.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Git actions are off — this panel is serving read-only"}, 403)
                return
            root = self._session_repo(session_id)
            if not root:
                self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
                return
            action = str(payload.get("action") or "")
            # The one action that answers with something other than a sentence
            # about what it did: the message it wrote, for the box to hold.
            if action == "suggestMessage":
                ok, said = suggest_message(root)
                self._json({"ok": ok, "text": said if ok else "",
                            "message": "" if ok else said}, 200 if ok else 409)
                return
            ok, message, status = git_action(root, action, payload)
            self._json({"ok": ok, "message": message}, status)
            return

        if path == "/api/sticky":
            # Pinning: the row is written down, and is the only kind that comes
            # back after a restart — and now the only kind that survives its own
            # session being ended. Unpinning does not take the row away by
            # itself: a session the panel is running still has one for as long as
            # it runs. Ending it is what takes it, or /api/forget for a row with
            # nothing left to end.
            session = self._session_by_id(session_id)
            want = bool(payload.get("pinned", payload.get("sticky", True)))
            if want:
                # Either a live session or a row the panel is already holding —
                # pinning an interactive session it started is the whole point,
                # and by then there is no session file to read it out of.
                held = kept_rows().get(session_id) or {}
                if not session and not held:
                    self._json({"ok": False, "message": "There is no such session"}, 404)
                    return
                pin_row(session_id, {
                    "sessionId": session_id,
                    "name": session["defaultName"] if session else held.get("name"),
                    "cwd": (session["cwd"] if session else held.get("cwd")) or "",
                    "startedAt": session["startedAt"] if session else held.get("startedAt"),
                    "lastSeen": time.time(),
                    "version": session["version"] if session else held.get("version"),
                    "kind": (session["kind"] if session else held.get("kind")) or "interactive",
                })
                self._json({"ok": True, "message": "Pinned — it survives a restart", "pinned": True})
                return
            unpin_row(session_id)
            with _KEPT_LOCK:
                still = session_id in _KEPT
            self._json({"ok": True, "pinned": False,
                        "message": "No longer pinned — kept until the panel restarts" if still
                                   else "No longer kept"})
            return

        if path == "/api/forget":
            # Removing the row. Nothing about the conversation goes with it: the
            # transcript is Claude Code's, where it always was, and `claude
            # --resume` in that folder still finds it. What goes is the panel's
            # memory of it — and the process, if the panel was running one,
            # because a held process with no row is a session nobody can see.
            held = owned_running(session_id)
            if held:
                if not SAY_ENABLED:
                    self._json({"ok": False, "message": "It is running here, and stopping it needs "
                                                        "the panel bound to loopback"}, 403)
                    return
                owned_release(session_id)
            owned = load_owned()
            if owned.pop(session_id, None) is not None:
                save_owned(owned)
            gone = forget_row(session_id)
            STORE.forget(session_id)
            if not gone and not held:
                self._json({"ok": False, "message": "There is no kept row to remove"}, 404)
                return
            self._json({"ok": True, "message": "Stopped it and took the row off the list" if held
                                               else "Took the row off the list"})
            return

        if path == "/api/start":
            # Starting a session runs a command on this machine, which is the same
            # order of risk as sending it a prompt — so it lives behind the same
            # loopback gate.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            entry = self._row_for(session_id)
            if not entry:
                self._json({"ok": False, "message": "There is no folder to start that session in"}, 404)
                return
            # A terminal opened on a transcript a panel turn is mid-way through
            # is the two-processes-one-conversation failure, arriving by the
            # politest possible route. Every path that starts a process on a
            # session asks this.
            with _OWNED_LOCK:
                mid_turn = session_id in OWNED_BUSY
            if mid_turn:
                self._json({"ok": False, "message": "A turn from the panel is running on it — "
                                                    "let it finish first"}, 409)
                return
            # Handing it back is the one thing that legitimately takes the
            # transcript off the panel, so it lets go rather than refusing.
            owned_release(session_id)
            owned = load_owned()
            if owned.pop(session_id, None) is not None:
                save_owned(owned)
            if STORE.raw(session_id):
                self._json({"ok": False, "message": "That session is already running"}, 409)
                return
            ok, message = start_session(entry)
            text = str(payload.get("text") or "").strip()
            if ok and text:
                # It cannot hear us yet, and the terminal is already opening —
                # so the deliverer waits for the socket without opening a second
                # one (`started`).
                deliver_later(session_id, text, started=True)
                message = "Starting it up — your message goes in as soon as it is listening"
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/new":
            # Same risk as /api/start — it runs a command on this machine — so it
            # sits behind the same loopback gate.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            session = self._session_by_id(session_id)
            entry = kept_rows().get(session_id) or {}
            cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
            if not session and not entry:
                self._json({"ok": False, "message": "That session is no longer around"}, 404)
                return
            ok, message = new_session(cwd)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/new-folder":
            # /api/new can only reach a folder the panel is already showing,
            # because it reads the folder off a session rather than off the
            # request. This reaches anywhere — but still not by being told
            # where: it opens a chooser on this machine and uses what the
            # person at the desk picked in it. The request says "ask", never
            # "here". Same loopback gate as everything that starts a process.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            # Open where the sessions are, when they agree on one place, rather
            # than at home every time. A hint about where to *start* is not the
            # folder it returns, so this one may come off the list.
            folders = {s["cwd"] for s in STORE.snapshot()["sessions"] if s.get("cwd")}
            start = folders.pop() if len(folders) == 1 else str(HOME)
            picked, message = pick_folder(start)
            if not picked:
                # Cancelling is the ordinary outcome, not an error: 200, and the
                # UI says what happened without dressing it as a failure.
                self._json({"ok": False, "cancelled": True, "message": message}, 200)
                return
            ok, message = new_session(picked)
            self._json({"ok": ok, "message": message, "cwd": picked}, 200 if ok else 409)
            return

        if path == "/api/rename":
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            # An empty name — or the session's own name typed back — clears the
            # override rather than storing a second copy of the default.
            name = clean_name(payload.get("name"))
            names = load_names()
            keep = bool(name) and name != session["defaultName"]
            if keep:
                names[session_id] = name
                save_names(names)
            elif names.pop(session_id, None) is not None:
                save_names(names)
            self._json({
                "ok": True,
                "message": "Renamed" if keep else "Name reset",
                "name": name if keep else session["defaultName"],
            })
            return

        if path == "/api/unpair":
            pairs = load_pairs()
            if pairs.pop(session_id, None) is not None:
                save_pairs(pairs)
            self._json({"ok": True, "message": "Pairing cleared"})
            return

        self._send(404, b"not found", "text/plain")

    MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }

    def _serve_static(self, name: str, content_type: str | None = None) -> None:
        target = (STATIC_DIR / name.lstrip("/")).resolve()
        # Never serve outside the static directory.
        if not str(target).startswith(str(STATIC_DIR) + os.sep) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        kind = content_type or self.MIME.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), kind)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live panel for local Claude Code sessions")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-send", action="store_true",
                        help="serve the conversation read-only, with no way to send input")
    args = parser.parse_args()

    global SAY_ENABLED
    SAY_ENABLED = is_loopback(args.host) and not args.no_send

    if not SESSION_DIR.exists():
        print(f"warning: {SESSION_DIR} does not exist yet — start a Claude Code session first")

    STORE.sample()
    threading.Thread(target=STORE.run_forever, daemon=True).start()

    # A held session is a `claude` of ours sitting on somebody's transcript. If
    # the panel goes without letting go, it is left there with nothing to send to
    # it and nobody to reap it — which is the two-processes-one-conversation
    # hazard arriving by the back door. Ctrl-C runs the `finally` below; a plain
    # `kill` would not, so it is caught too.
    atexit.register(owned_release_all)
    # Whatever was interactive when the panel last stopped is interactive again.
    # In a thread: each one is a process to start, and the panel should be
    # answering before the first of them is up.
    threading.Thread(target=owned_resume_held, daemon=True).start()

    def bow_out(_signum, _frame):
        owned_release_all()
        raise SystemExit(0)

    for caught in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(caught, bow_out)
        except (OSError, ValueError):
            pass

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"claude-watchtower → http://{args.host}:{args.port}")
    if not WINDOWS.available():
        print("note: xdotool/DISPLAY unavailable, so window focusing is switched off")
    if not SAY_ENABLED:
        why = "--no-send" if args.no_send else f"not bound to loopback ({args.host})"
        print(f"note: sending input is switched off — {why}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        # A held session is a process of ours. Leaving it behind would leave a
        # `claude` on a transcript with nobody to send to it or reap it.
        owned_release_all()


if __name__ == "__main__":
    main()

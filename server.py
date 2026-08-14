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
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
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
# Sessions you asked the panel to keep after their process is gone.
STICKY_FILE = HOME / ".config" / "claude-watchtower" / "sticky.json"
MAX_STICKY = 100

# How long a state trace remembers, and how often we sample.
HISTORY_SECONDS = 30 * 60
SAMPLE_INTERVAL = 1.0

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
        return False, "This session is not listening for messages"
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
                    entries.append({"role": kind, "at": at, "text": text[:4000], "tools": []})
                continue
            if not isinstance(content, list):
                continue

            texts, tools, only_results = [], [], True
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
                    tools.append({"name": block.get("name") or "tool", "detail": tool_detail(block.get("input"))})
                elif block_kind == "thinking":
                    only_results = False
            if only_results:
                continue  # a pure tool_result turn
            if texts or tools:
                entries.append({
                    "role": kind, "at": at,
                    "text": "\n\n".join(texts)[:4000],
                    "tools": tools[:12],
                })

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


# ------------------------------------------------------------- sticky sessions
# A session file disappears when its process does, and with it the row. A sticky
# session keeps its row: the panel remembers enough about it — id, name, folder —
# to go on showing the conversation, and can start Claude Code back up on that
# same transcript with `claude --resume`.


def load_sticky() -> dict[str, dict]:
    try:
        data = json.loads(STICKY_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_sticky(sticky: dict[str, dict]) -> None:
    if len(sticky) > MAX_STICKY:
        sticky = dict(list(sticky.items())[-MAX_STICKY:])
    STICKY_FILE.parent.mkdir(parents=True, exist_ok=True)
    STICKY_FILE.write_text(json.dumps(sticky, indent=2))


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


def resolve_folder(raw: str) -> tuple[str | None, str]:
    """A folder a person typed, made absolute, or why it will not do.

    `~` and a relative path are what anyone types, so both are accepted; a file,
    a path that is not there, and one that cannot be read are all refused by
    name, because "could not open a session there" on its own leaves you guessing
    which of the three it was.
    """
    text = (raw or "").strip()
    if not text:
        return None, "No folder was given"
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (HOME / path).resolve()
        else:
            path = path.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"That path will not resolve: {exc}"
    if not path.exists():
        return None, f"There is no {path}"
    if not path.is_dir():
        return None, f"{path} is a file, not a folder"
    if not os.access(path, os.R_OK | os.X_OK):
        return None, f"{path} cannot be opened"
    return str(path), ""


# Finding a folder the browser named but would not place. See locate_folder.
LOCATE_DEADLINE = 4.0
LOCATE_DEPTH = 7
LOCATE_MAX_HITS = 12
# Directories never worth walking into: they hold thousands of entries and no
# project anybody starts a session in.
LOCATE_SKIP = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".cache", ".local",
    "site-packages", ".npm", ".cargo", "target", "build", "dist", ".next",
    ".mypy_cache", ".pytest_cache", "snap", ".steam", ".rustup", ".nvm",
})


def locate_folder(name: str, children: list[str]) -> tuple[list[str], str]:
    """Where on disk the folder the browser let you pick actually is.

    The native picker is the browser's own, which is the point — but it will not
    say where the folder is. `webkitdirectory` hands back each file's path
    *relative* to the chosen folder, so what reaches us is the folder's name and
    the names directly inside it, and never the absolute path.

    Those two together are a fingerprint, and this is the search for it: walk down
    from home looking for a directory of that name holding those children. Almost
    always one thing matches. Where more than one does, the caller is given all of
    them and asks — a wrong guess would start a session in the wrong checkout,
    which is exactly the mistake worth a question.

    Bounded on every axis, because it is a filesystem walk answering a click:
    depth, wall clock, hits, and a skip list for the directories that hold
    thousands of entries nobody starts a session in.
    """
    name = (name or "").strip().strip("/")
    if not name or "/" in name or name in (".", ".."):
        return [], "That is not a folder name this can look for"
    wanted = {child for child in children if child and "/" not in child}
    deadline = time.time() + LOCATE_DEADLINE
    hits: list[str] = []
    ran_out = False

    # Home first and on its own: a folder you pick is almost always under it, and
    # starting there keeps the walk small enough to answer a click.
    stack: list[tuple[Path, int]] = [(HOME, 0)]
    while stack:
        if time.time() > deadline or len(hits) >= LOCATE_MAX_HITS:
            ran_out = True
            break
        here, depth = stack.pop()
        try:
            with os.scandir(here) as scan:
                for entry in scan:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if entry.name in LOCATE_SKIP:
                        continue
                    child = Path(entry.path)
                    if entry.name == name and folder_matches(child, wanted):
                        hits.append(str(child))
                    # A match is still walked past: a name can repeat further down.
                    if depth + 1 <= LOCATE_DEPTH and not entry.name.startswith("."):
                        stack.append((child, depth + 1))
        except OSError:
            continue

    if hits:
        return sorted(hits), ""
    return [], ("Could not find that folder under your home directory"
                + (" within the time this can spend looking" if ran_out else ""))


def folder_matches(here: Path, wanted: set[str]) -> bool:
    """Does this directory hold the entries the browser said were inside?

    Only the names the picker actually reported have to be present. It reports
    files, and a folder holding nothing but empty subfolders reports none at all —
    so an empty fingerprint matches on the name alone, and the caller asks.
    """
    if not wanted:
        return True
    try:
        here_names = {entry.name for entry in os.scandir(here)}
    except OSError:
        return False
    return wanted.issubset(here_names)


def new_session(cwd: str) -> tuple[bool, str]:
    """Open a terminal running a fresh `claude` in a folder."""
    if not cwd or not Path(cwd).is_dir():
        return False, f"That folder is gone: {cwd}" if cwd else "That session has no folder"
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"
    argv = terminal_argv([claude], cwd)
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


def wait_and_say(session_id: str, text: str, seconds: float = 90.0) -> None:
    """Deliver a message once the session it was meant for is listening again.

    Typing into a stopped session starts it up, and startup is not instant — the
    process has to come up and open its socket. This waits for that in the
    background so the click returns at once.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(1.0)
        data = STORE.raw(session_id)
        if not data or not data.get("messagingSocketPath"):
            continue
        if not Path(data["messagingSocketPath"]).exists():
            continue
        ok, _ = say_to_session(data, text)
        if ok:
            return


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
            if actual is None:
                continue  # process is gone
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
        sticky = load_sticky()
        dirty = False
        with self._lock:
            raw = list(self._sessions.values())
            history = {k: list(v) for k, v in self._history.items()}

        out = []
        for data in raw:
            session_id = data.get("sessionId") or str(data.get("pid"))
            pid = data.get("pid")
            cwd = data.get("cwd") or ""
            alive = now - data.get("seenAt", 0) < 15
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
                # The mode as of this session's last turn, and what was asked for
                # at the panel since — see read_permission_mode for why the two
                # can disagree for a while.
                "permissionMode": self._mode(session_id, cwd) if cwd else None,
                # What Claude says this session is about, for the line under its
                # name — the detail pane reads the same thing off the transcript.
                "title": self._title(session_id, cwd) if cwd else None,
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
                "canSay": bool(
                    alive
                    and data.get("peerProtocol") == PEER_PROTOCOL
                    and data.get("messagingSocketPath")
                    and Path(data["messagingSocketPath"]).exists()
                ),
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
                "sticky": session_id in sticky,
            }
            # Keep what a sticky row will need once the process is gone. Written
            # back now and then rather than every second — it is a poll loop.
            if session_id in sticky:
                held = sticky[session_id]
                fresh = {
                    "sessionId": session_id, "name": session["defaultName"], "cwd": cwd,
                    "startedAt": session["startedAt"], "lastSeen": now,
                    "version": session["version"], "kind": session["kind"],
                }
                if any(held.get(k) != fresh[k] for k in ("name", "cwd", "version")) \
                        or now - (held.get("lastSeen") or 0) > 30:
                    sticky[session_id] = fresh
                    dirty = True
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
        if dirty:
            save_sticky(sticky)

        live_ids = {s["sessionId"] for s in out}
        for session_id, entry in sticky.items():
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
                "status": "stopped",
                "kind": entry.get("kind") or "interactive",
                "version": entry.get("version"),
                "startedAt": entry.get("startedAt") or entry.get("lastSeen") or now,
                "statusSince": entry.get("lastSeen") or now,
                "branch": self._branch(cwd) if cwd else None,
                "repoRoot": self._repo_root(cwd) if cwd else None,
                "activity": self._activity(session_id, cwd) if cwd else None,
                # For a stopped session these are the mode it was last in and
                # the last thing it was working on.
                "permissionMode": self._mode(session_id, cwd) if cwd else None,
                "title": self._title(session_id, cwd) if cwd else None,
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
                "sticky": True,
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
        }

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
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _session_by_id(self, session_id: str) -> dict | None:
        for session in STORE.snapshot()["sessions"]:
            if session["sessionId"] == session_id:
                return session
        return None

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
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            ok, message = end_process(data.get("pid"), data.get("procStart"), bool(payload.get("force")))
            if ok:
                # The window pairing dies with the session it pointed at.
                pairs = load_pairs()
                if pairs.pop(session_id, None) is not None:
                    save_pairs(pairs)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/say":
            # A prompt is an instruction to an agent with tools, so this endpoint
            # is worth more than the others put together. It stays on loopback
            # even when the rest of the panel is served to the network.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Sending is off because the panel is not bound to loopback"}, 403)
                return
            data = STORE.raw(session_id)
            if not data:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            ok, message = say_to_session(data, str(payload.get("text") or ""))
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/locate":
            # Where the folder you picked in the browser's own dialog actually is.
            # Same gate as the listing it feeds: it reads this machine's
            # filesystem, and exists only to start a session.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Browsing folders is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            children = payload.get("children")
            found, why = locate_folder(str(payload.get("name") or ""),
                                       [str(c) for c in children] if isinstance(children, list) else [])
            if not found:
                self._json({"ok": False, "message": why}, 404)
                return
            self._json({"ok": True, "folders": found})
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
            session = self._session_by_id(session_id)
            sticky = load_sticky()
            want = bool(payload.get("sticky", True))
            if want:
                if not session:
                    self._json({"ok": False, "message": "There is no such session"}, 404)
                    return
                sticky[session_id] = {
                    "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
                    "startedAt": session["startedAt"], "lastSeen": time.time(),
                    "version": session["version"], "kind": session["kind"],
                }
                save_sticky(sticky)
                self._json({"ok": True, "message": "Kept in the dashboard", "sticky": True})
                return
            if sticky.pop(session_id, None) is not None:
                save_sticky(sticky)
            self._json({"ok": True, "message": "No longer kept", "sticky": False})
            return

        if path == "/api/start":
            # Starting a session runs a command on this machine, which is the same
            # order of risk as sending it a prompt — so it lives behind the same
            # loopback gate.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            entry = load_sticky().get(session_id)
            if not entry:
                self._json({"ok": False, "message": "That session is not being kept"}, 404)
                return
            if STORE.raw(session_id):
                self._json({"ok": False, "message": "That session is already running"}, 409)
                return
            ok, message = start_session(entry)
            text = str(payload.get("text") or "").strip()
            if ok and text:
                # It cannot hear us yet. Hand the message to a thread that waits
                # for its socket and then delivers it.
                threading.Thread(target=wait_and_say, args=(session_id, text), daemon=True).start()
                message = "Starting it up — your message goes in as soon as it is listening"
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/new":
            # Same risk as /api/start — it runs a command on this machine — so it
            # sits behind the same loopback gate.
            if not SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            # A folder named in the request opens a session there. This is a real
            # widening: every other form of this route took the folder from a
            # session already on screen, so it could not be pointed anywhere the
            # panel was not already showing. What is left holding it is the
            # loopback gate — and anyone through that gate can already put a
            # prompt into a session that holds tools and a checkout, which is the
            # greater power of the two. The path is still resolved and checked
            # before anything runs.
            if isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                folder, why = resolve_folder(payload["cwd"])
                if not folder:
                    self._json({"ok": False, "message": why}, 400)
                    return
                ok, message = new_session(folder)
                self._json({"ok": ok, "message": message}, 200 if ok else 409)
                return
            session = self._session_by_id(session_id)
            entry = load_sticky().get(session_id) or {}
            cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
            if not session and not entry:
                self._json({"ok": False, "message": "That session is no longer around"}, 404)
                return
            ok, message = new_session(cwd)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
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


if __name__ == "__main__":
    main()

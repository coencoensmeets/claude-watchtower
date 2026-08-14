"""Sessions that write no file of their own.

Claude Code keeps one file per session, except when a session is started from
inside another one — then there is nothing on disk and the panel builds the
record itself out of /proc. See child_session for what it can and cannot know.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from watchtower.config import SESSION_DIR
from watchtower.errands import is_own_errand
from watchtower.proc import CLK_TCK, proc_starttime


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


# Claude Code's messaging sockets, one per session, named after the pid. A
# nested session opens one of these and writes a key file beside the session
# files, but no session file of its own — see child_session.
SOCK_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "cc-socks"


# What a nested session calls itself in the session file the panel makes for it.
CHILD_KIND = "child"


MAX_ENVIRONS = 200


# Claude Code listens on a per-session unix socket — the path is in the session
# file, the directory is mode 0700, and the socket itself 0600, so only this user
# can reach it. Two newline-delimited JSON lines inject a turn: an optional auth
# line, then the message. The protocol is internal, hence PEER_PROTOCOL below.
PEER_PROTOCOL = 1


BOOT_TIME = boot_time()

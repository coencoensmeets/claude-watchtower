"""Getting a message into a session, whatever state it is in.

Claude Code opens a unix socket per session and prints a token for it. That is
not a documented interface, so everything here is written to fail politely: an
absent socket, a refused connection or a session mid-turn are all ordinary
answers rather than errors.

The socket is only one of three ways in, though — the others are the pipe of a
turn the panel is running, and starting the session back up — so the second
half of this module is the deliverer that keeps trying until one of them takes
it. See deliver_later.

Loopback only. is_loopback is what the panel checks before offering any of this.
"""

from __future__ import annotations

import ipaddress
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path

from watchtower.config import LIVE_SECONDS, SESSION_DIR
from watchtower.control import start_session
from watchtower.owned import OWNED_BUSY, OWNED_MODES, _OWNED_LOCK, load_owned, owned_say
from watchtower.proc import proc_starttime
from watchtower.rows import kept_rows
from watchtower.sessions import PEER_PROTOCOL


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
    from watchtower.store import STORE

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
    from watchtower.store import STORE

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
    from watchtower.store import STORE

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

"""Sending a message to a session, over the socket it publishes.

Claude Code opens a unix socket per session and prints a token for it. That is
not a documented interface, so everything here is written to fail politely: an
absent socket, a refused connection or a session mid-turn are all ordinary
answers rather than errors.

Loopback only. is_loopback is what the panel checks before offering any of this.
"""

from __future__ import annotations

import ipaddress
import json
import os
import signal
import socket
from pathlib import Path

from watchtower.config import SESSION_DIR
from watchtower.proc import proc_starttime
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

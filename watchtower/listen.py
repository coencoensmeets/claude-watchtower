"""Where the panel listens, and who may reach it.

Three things, all of them about the socket rather than the sessions behind it.

**A port of its own.** Two people on one network both running a panel is the
ordinary case, not the exception, and a hard-coded 8787 makes the second one
fail to start — or worse, makes a phone open somebody else's. So a first run
picks a port that is unique to this install and writes it down, and every run
after that uses the same one. Written down rather than derived on the spot
because the number has to be typeable on a phone and rememberable in a
bookmark: a port that moved when the hostname changed would be neither.

**A key, once the panel is not on loopback alone.** Loopback needs none: the
only thing that can reach it is this machine. A panel on the network is a
different proposition — it can prompt an agent holding tools and a checkout —
so it answers nobody who cannot show the key. It is short on purpose. It has to
be typed on a phone once, and it is a key to a socket on your own network for
the length of an afternoon, not a password.

**The address to type.** The panel prints the one a phone will actually reach:
the address of the interface holding the default route, not `0.0.0.0`, which is
what it is bound to and what nothing can open.

Standard library only, like the rest of the panel.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
from pathlib import Path

from watchtower.config import LISTEN_FILE, ROOT


# The band a first run picks from. Above the range a browser refuses to open
# (Chrome blocks a handful of well-known ports outright), clear of the usual
# development ports below 8100, and well below the ephemeral range the kernel
# hands out to outgoing connections — a saved port that the kernel might later
# lend to something else would fail to bind one morning for no visible reason.
PORT_BAND = (8800, 8899)
# How far to walk from the port this install hashes to before giving up. A hit
# means the number is already taken on this machine — the same person's second
# clone, most likely, which is exactly the case the walk is for.
PORT_TRIES = 40


# Base32 without the letters that read as digits. Eight of these is forty bits:
# far more than a guesser gets through over a home network, and short enough to
# type on a phone without hating it.
KEY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
KEY_LENGTH = 8


def _read() -> dict:
    """What was written down last time, or nothing.

    Defensive: it is a file in a config directory, so it may have been edited,
    truncated by a full disk, or written by a newer version of the panel. None
    of that is a reason for the panel not to start.
    """
    try:
        found = json.loads(LISTEN_FILE.read_text())
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(entry: dict) -> None:
    """Keep it, and say nothing if the config directory cannot be written.

    A panel that cannot remember its port still serves on it. It would only
    pick a different one next time, which is worth a line on the console — not
    worth refusing to start over.
    """
    try:
        LISTEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        LISTEN_FILE.write_text(json.dumps(entry, indent=2) + "\n")
    except OSError:
        pass


def _valid_port(value: object) -> int | None:
    try:
        port = int(value)                      # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _free(port: int) -> bool:
    """Whether this port can be bound right now, on every interface.

    Tested on 0.0.0.0 rather than on the host we are about to use: a port free
    on loopback may be taken on the interface a phone would come in on, and a
    panel that picked one of those would work until the day it was asked to
    serve the network.

    Deliberately without SO_REUSEADDR. The question is whether something else
    holds the port, and SO_REUSEADDR is how you get a yes anyway.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _hashed_port() -> int:
    """The port this install starts looking at.

    From who and where rather than at random, so a panel whose config file has
    been deleted comes back on the port it was on before, and two clones in two
    folders do not both want the same one. blake2s because it is in the standard
    library and this is a spreading function, not a security decision.
    """
    seed = f"{os.environ.get('USER') or os.environ.get('USERNAME') or ''}@{socket.gethostname()}:{ROOT}"
    digest = hashlib.blake2s(seed.encode("utf-8", "replace"), digest_size=4).digest()
    low, high = PORT_BAND
    return low + int.from_bytes(digest, "big") % (high - low + 1)


def chosen_port(explicit: int | None = None) -> tuple[int, bool]:
    """The port to serve on, and whether it was picked fresh just now.

    In order: what was asked for on the command line, what the environment
    says, what was written down last time, and only then a new one. A port
    asked for is not written down — it is this run's business, not the
    install's — so tomorrow's plain `python3 server.py` is still on the port
    the bookmark points at.
    """
    asked = _valid_port(explicit) or _valid_port(os.environ.get("CLAUDE_WATCHTOWER_PORT"))
    if asked:
        return asked, False

    kept = _valid_port(_read().get("port"))
    if kept:
        return kept, False

    start = _hashed_port()
    low, high = PORT_BAND
    span = high - low + 1
    for step in range(PORT_TRIES):
        port = low + (start - low + step) % span
        if _free(port):
            _write({**_read(), "port": port})
            return port, True
    # Every port in the band is busy, which takes forty panels. Serve on the one
    # this install hashes to and let the bind fail with the real reason.
    return start, False


def access_key(fresh: bool = False) -> str:
    """The key a request from off this machine has to show.

    Made once and kept, so a phone that has been given it stays given it across
    restarts — a key that changed every morning would be a key nobody uses.
    """
    kept = _read().get("key")
    if not fresh and isinstance(kept, str) and len(kept) >= 6:
        return kept
    key = "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))
    _write({**_read(), "key": key})
    return key


def lan_address() -> str | None:
    """This machine's address on the network a phone is on, or None.

    Found by asking the routing table where a packet to the outside would leave
    from, which is the interface a phone on the same wifi arrives on. Nothing is
    sent: a UDP socket that has been connected has a local address, and that is
    the whole of what this reads. `gethostname()` is not used — on most Linux
    boxes it resolves to 127.0.1.1, which is exactly the address that does not
    work.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            # Documentation-only address (RFC 5737): routable in the table,
            # never actually there.
            probe.connect(("192.0.2.1", 9))
            found = probe.getsockname()[0]
    except OSError:
        return None
    return None if not found or found.startswith("127.") else found


def where_kept() -> Path:
    return LISTEN_FILE

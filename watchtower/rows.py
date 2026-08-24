"""Rows that outlive the process behind them.

A session file disappears when its process does, and with it the row. A kept
row outlives it: the panel remembers enough about the session — id, name,
folder — to go on showing the conversation, and can start Claude Code back up
on that same transcript with `claude --resume`.

Two tiers, and the only difference is how long "outlives it" means:

- **Held** (`_KEPT`, memory only). Every row the panel makes for itself is
  this: a session it started, a session it adopted. It survives a page reload,
  which is a browser doing nothing of consequence, and goes when the panel
  goes — because whatever was running here is not running any more either, and
  a row for it would be a row for nothing.
- **Pinned** (`sticky.json`, on disk). Asked for a row at a time, and the only
  thing that survives a restart. Panel-run sessions used to be written here
  too, which made every one of them permanent whether or not that was wanted:
  the panel decided what you were keeping.

Nothing about the session is copied either way. The transcript stays where
Claude Code keeps it, and a forgotten row loses only the row.
"""

from __future__ import annotations

import json
import threading

from watchtower.config import MAX_KEPT, MAX_STICKY, STICKY_FILE


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

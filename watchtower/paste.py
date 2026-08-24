"""Pictures pasted into the composer, on their way to a session.

A message over the wire is text, and it always will be — so the picture lands
in a file the session can read and the message says where. Everything that
decides *what* is written and *where* is here rather than in the request.
"""

from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path



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


"""What the composer sends by path: pictures pasted, and files dropped.

A message over the wire is text, and it always will be — so the picture lands
in a file the session can read and the message says where. Everything that
decides *what* is written and *where* is here rather than in the request.

A dropped file is usually not written at all: it has a path already and the
message names it where it lies (see web/src/ui/dropped.ts). This module is for
the drop that has no path to name — dragged out of a browser's downloads, out
of a mail client, out of anything holding the bytes and not a file on disk.
There the choice is to write a copy or to refuse the drop, and a copy in the
session's own folder is the useful half of that.
"""

from __future__ import annotations

import base64
import re
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

# A dropped file that had no path, kept apart from the pictures: what is in the
# images folder is all thumbnailable and all disposable, and a 30 MB archive
# dragged out of a download is neither. Same parent, same fortnight, same reason
# for being under .claude — the session already reads there.
DROP_DIR_NAME = ".claude/watchtower-files"
# Larger than a screenshot's ceiling, because this is whatever was downloaded: a
# PDF, a log, a tarball. Still bounded — the body is held in memory to be
# decoded, and a drag is not a way to hand the panel a disk image.
DROP_MAX_BYTES = 32 * 1024 * 1024
# What a name off the request is allowed to keep. Anything else becomes a dash,
# so a name can only ever be a name: no separator survives it, and the file it
# lands as is always inside the folder the panel chose.
DROP_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
DROP_NAME_MAX = 80
# base64 costs a third on top, and the JSON around it a little more. The bigger
# of the two ceilings decides it: one ceiling for every POST the panel takes,
# and the routes say which of theirs was met.
POST_MAX_BYTES = max(PASTE_MAX_BYTES, DROP_MAX_BYTES) * 4 // 3 + 65536


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



def drop_dir(cwd: str) -> Path:
    return Path(cwd) / DROP_DIR_NAME


def safe_drop_name(name: str) -> str:
    """The dropped file's own name, reduced to something that can only be a name.

    The name is kept — a session told to read `quarterly.pdf` is being told
    something, where `drop-a1b2c3.bin` is being told to open an unmarked box —
    but it is kept the way a name is kept and not the way a path is: the
    directory part goes, every character outside a small set becomes a dash, and
    a leading dot cannot make the copy invisible in its own folder.
    """
    base = str(name or "").replace("\\", "/").split("/")[-1]
    cleaned = DROP_NAME_SAFE.sub("-", base).strip("-.")
    # Length is bounded here rather than at the filesystem, which answers a long
    # name with an error rather than a shorter name. The tail is kept, because
    # that is where the extension is.
    if len(cleaned) > DROP_NAME_MAX:
        cleaned = cleaned[-DROP_NAME_MAX:].strip("-.")
    return cleaned or "dropped-file"


def save_dropped_file(cwd: str, name: str, data: str) -> tuple[bool, str, str]:
    """Write one dropped file into the session's folder.

    Returns (ok, path-or-empty, message). Unlike a paste, the kind is not
    checked: what was dropped is what the session is being asked to read, and
    the panel is not the judge of which of somebody's own files are worth
    reading. What it does insist on is the folder and the shape of the name.
    """
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        return False, "", "That file did not arrive in one piece"
    if not raw:
        return False, "", "That file is empty"
    if len(raw) > DROP_MAX_BYTES:
        return False, "", f"That file is larger than {DROP_MAX_BYTES // (1024 * 1024)} MB"
    folder = drop_dir(cwd)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return False, "", f"Could not write into that session's folder — {error}"
    sweep_pastes(folder)
    safe = safe_drop_name(name)
    path = folder / safe
    # The name it was dropped under, while that name is free — it is the one the
    # message will read best. A second drop of the same name is a second file,
    # never an overwrite: something already in the conversation may be pointing
    # at the first one.
    if path.exists():
        stem, dot, suffix = safe.partition(".")
        tail = uuid.uuid4().hex[:6]
        path = folder / (f"{stem}-{tail}{dot}{suffix}" if dot else f"{safe}-{tail}")
    try:
        path.write_bytes(raw)
    except OSError as error:
        return False, "", f"Could not write the file — {error}"
    return True, str(path), "File saved"

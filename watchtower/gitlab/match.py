"""A work item on a server, to a folder on this machine.

The global page lists work items from every GitLab you have, and every one of
them is useless until it is a directory you can start a session in. Nothing on
GitLab knows where your checkout is, so this is a local index, built from what
the panel already knows and kept between runs.

Three sources, in the order they are trusted: the sessions running right now,
because a checkout with a session in it is certainly the one you work in; the
checkouts the panel has seen before, remembered in a file beside the other
config; and — only if you have named some — the folders in
CLAUDE_WATCHTOWER_PROJECT_DIRS, scanned one level down.

No path from a request ever lands here. The page asks about a work item, and
what comes back is a folder the panel found by itself.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from watchtower.config import MAX_REPOS, PROJECT_DIRS, REPO_FILE
from watchtower.gitlab.read import remotes_of, split_remote


SEEN_LOCK = threading.Lock()


# root -> when it was last seen, so the file can be trimmed by age rather than
# by whichever entry happens to be first.
SEEN: dict[str, float] = {}


LOADED = False


def load_seen() -> dict[str, float]:
    global LOADED
    with SEEN_LOCK:
        if LOADED:
            return dict(SEEN)
    try:
        data = json.loads(REPO_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    found = {str(k): float(v) for k, v in data.items()
             if isinstance(data, dict) and isinstance(v, (int, float))}
    with SEEN_LOCK:
        for root, when in found.items():
            SEEN.setdefault(root, when)
        LOADED = True
        return dict(SEEN)


def save_seen() -> None:
    with SEEN_LOCK:
        keep = dict(sorted(SEEN.items(), key=lambda kv: kv[1], reverse=True)[:MAX_REPOS])
        SEEN.clear()
        SEEN.update(keep)
    try:
        REPO_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPO_FILE.write_text(json.dumps(keep, indent=2))
    except OSError:
        pass          # a folder index is not worth failing a request over


def remember(roots: list[str]) -> None:
    """Note the checkouts the panel is looking at. Called with the session list."""
    load_seen()
    now = time.time()
    fresh = False
    with SEEN_LOCK:
        for root in roots:
            if root and (root not in SEEN or now - SEEN[root] > 3600):
                SEEN[root] = now
                fresh = True
    if fresh:
        save_seen()


def scanned_dirs() -> list[str]:
    """Checkouts one level down from each folder in CLAUDE_WATCHTOWER_PROJECT_DIRS."""
    found: list[str] = []
    for where in (d for d in PROJECT_DIRS.split(":") if d.strip()):
        try:
            for entry in sorted(Path(where).expanduser().iterdir()):
                if (entry / ".git").exists():
                    found.append(str(entry))
        except OSError:
            continue
    return found


def index() -> dict[str, list[str]]:
    """Every checkout the panel can find, keyed by the project its remote names.

    One key can hold several folders — a worktree, a second clone, a fork — and
    all of them come back. Picking between them is the page's job, not this
    file's, because the answer depends on what you are about to do with it.
    """
    roots = list(load_seen().keys()) + scanned_dirs()
    out: dict[str, list[str]] = {}
    for root in roots:
        if not Path(root).is_dir():
            continue
        for url in remotes_of(root).values():
            host, path = split_remote(url)
            if not host:
                continue
            key = f"{host}/{path}".lower()
            if root not in out.setdefault(key, []):
                out[key].append(root)
    return out


def folder_for(host: str, project: str, known: dict[str, list[str]] | None = None) -> str:
    """The one folder for a project, or "" when there is not exactly one obvious.

    A project with two checkouts returns the one seen most recently, which is the
    one you have been working in. Ties do not arise: the index is built from a
    file keyed by time.
    """
    found = (known if known is not None else index()).get(f"{host}/{project}".lower(), [])
    if not found:
        return ""
    seen = load_seen()
    return max(found, key=lambda root: seen.get(root, 0.0))

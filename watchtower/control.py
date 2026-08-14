"""Acting on a session, and starting new ones.

Everything here changes something outside the panel: it keeps a session on the
list after its process is gone, opens a terminal running a fresh claude, or
works out which folder the browser's picker actually returned.

The folder walk is the awkward one. A browser hands back a directory name and
the files inside it, never a path, so locate_folder searches for a directory
that matches — bounded in time, depth and hits, because the alternative is
walking somebody's whole home directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from watchtower.config import HOME, MAX_STICKY, STICKY_FILE


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



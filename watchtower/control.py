"""Acting on a session, and starting new ones.

Everything here changes something outside the panel: it keeps a session on the
list after its process is gone, opens a terminal running a fresh claude, or
asks the desktop where a new one should start.

The chooser is the awkward one. A browser will not tell a page where a folder
is, and a path typed into a request is the one thing the panel refuses to act
on — so pick_folder opens the desktop's own dialog and reads the answer back
from a process rather than from the browser.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from watchtower.config import HOME



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


def interactive_argv(command: list[str]) -> list[str]:
    """`command` run by an interactive shell that stays behind when it exits.

    A terminal handed a bare `claude` is a window with one program in it: the
    session comes up without the PATH, aliases and version managers the shell's
    rc file sets up, and the window vanishes the moment the session ends, taking
    the scrollback with it. Going through the shell instead gives a session
    started from the panel the same surroundings as one started by hand, and
    leaves a prompt behind afterwards, so the window is somewhere to work rather
    than something to watch.
    """
    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    line = " ".join(shlex.quote(part) for part in command)
    return [shell, "-i", "-c", f"{line}; exec {shlex.quote(shell)} -i"]


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



def new_session(cwd: str) -> tuple[bool, str]:
    """Open a terminal running a fresh `claude` in a folder, from a shell prompt."""
    if not cwd or not Path(cwd).is_dir():
        return False, f"That folder is gone: {cwd}" if cwd else "That session has no folder"
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"
    argv = terminal_argv(interactive_argv([claude]), cwd)
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


# The editor commands we try, in order: plain VS Code first, then the builds
# somebody has instead of it. CLAUDE_WATCHTOWER_EDITOR overrides the search —
# give it the command and any flags, and the folder is appended.
EDITORS = ("code", "code-insiders", "codium", "vscodium")


def open_editor(target: str, line: int | None = None) -> tuple[bool, str]:
    """Show a folder — or a file, at a line — in VS Code.

    No flags for a folder: that is what makes an editor already running the one
    that answers. The `code` command talks to a running instance over its own
    socket rather than starting a second one, and that instance raises the
    window already holding this folder if there is one, or opens a new window if
    there is not. `--new-window` would forfeit the first half of that,
    `--reuse-window` the second — it would take over whichever window you last
    looked at.

    A file goes through `--goto`, which is how the same command is told where in
    a file to land. Without a line it still beats plain `code file`: `--goto`
    opens the file in the window that already holds its folder.

    Only the folder case is behind the panel's own buttons. The file case is a
    path clicked out of a conversation — see the containment in http.py, which
    is what decides whether a path from a message may be opened at all.
    """
    if not target:
        return False, "That session has no folder"
    spot = Path(target)
    if not spot.exists():
        return False, f"That is not here any more: {target}"
    override = os.environ.get("CLAUDE_WATCHTOWER_EDITOR")
    # Where the editor is started from: a file's own folder, so an editor that
    # takes a working directory lands somewhere sensible either way.
    home = str(spot if spot.is_dir() else spot.parent)
    if override:
        parts = override.split()
        if not parts or not shutil.which(parts[0]):
            return False, f"CLAUDE_WATCHTOWER_EDITOR names something not on PATH: {override}"
        # An override is somebody else's editor and nobody else's flags: it gets
        # the path and nothing more, since --goto is VS Code's spelling alone.
        argv = parts + [str(spot)]
    else:
        found = next((shutil.which(name) for name in EDITORS if shutil.which(name)), None)
        if not found:
            return False, ("Cannot find the code command on PATH — install VS Code's shell "
                           "command, or set CLAUDE_WATCHTOWER_EDITOR")
        argv = [found] if spot.is_dir() else [found, "--goto"]
        argv.append(f"{spot}:{line}" if line and not spot.is_dir() else str(spot))
    try:
        subprocess.Popen(
            argv, cwd=home, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            env=top_level_env(),
        )
    except OSError as exc:
        return False, f"Could not open the editor: {exc}"
    return True, f"Opening {spot.name or target} in VS Code…"


# ------------------------------------------------------------- folder chooser
# A new session in a folder no session is in yet needs a folder from outside the
# panel's own list, and a path typed into the browser is exactly what /api/new
# refuses to accept. A chooser on this machine sidesteps the question rather
# than answering it: the browser can ask for the dialog but cannot say what it
# returns, so the folder is still chosen by the person at the desk, in a window
# the desktop drew, and the panel never takes a path from a request.
#
# Whatever the desktop already has, in the order a desktop would prefer:
# zenity under GNOME, kdialog under KDE, and Tk — which is stdlib, so it is
# always there — as the fallback. The Tk one runs as its own process because a
# toolkit main loop wants a main thread, and this is called from a request.
GUI_PICKERS = (
    ("zenity", lambda start: ["zenity", "--file-selection", "--directory",
                              "--title=Folder for the new session", f"--filename={start}/"]),
    ("kdialog", lambda start: ["kdialog", "--getexistingdirectory", start,
                               "--title", "Folder for the new session"]),
)



# Written as source rather than a file on disk: it is three lines, and a helper
# script beside the server would be one more thing to keep in step with it.
TK_PICKER = (
    "import sys, tkinter, tkinter.filedialog;"
    "root = tkinter.Tk(); root.withdraw();"
    "path = tkinter.filedialog.askdirectory(initialdir=sys.argv[1],"
    " title='Folder for the new session', mustexist=True);"
    "sys.stdout.write(path or '')"
)



# One dialog at a time. A second one is a second window nobody asked for, on a
# desktop that may not even raise it.
PICKER_LOCK = threading.Lock()



PICKER_SECONDS = 300.0



def picker_argv(start: str) -> list[str] | None:
    """The chooser this desktop can show, or None if it cannot show one."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    for name, build in GUI_PICKERS:
        if shutil.which(name):
            return build(start)
    try:
        import tkinter  # noqa: F401  — asking whether it is installed, not using it
    except ImportError:
        return None
    return [sys.executable, "-c", TK_PICKER, start]



def can_pick_folder() -> bool:
    return picker_argv(str(HOME)) is not None



def pick_folder(start: str) -> tuple[str | None, str]:
    """Ask the desktop for a folder. (path, message) — path is None if it did not."""
    argv = picker_argv(start)
    if not argv:
        return None, "No folder chooser on this desktop — install zenity, or Python's tkinter"
    if not PICKER_LOCK.acquire(blocking=False):
        return None, "The folder chooser is already open — it is waiting on the desktop"
    try:
        done = subprocess.run(
            argv, cwd=start, capture_output=True, text=True, timeout=PICKER_SECONDS,
            env=top_level_env(),
        )
    except subprocess.TimeoutExpired:
        return None, "The folder chooser was left open too long"
    except OSError as exc:
        return None, f"Could not open a folder chooser: {exc}"
    finally:
        PICKER_LOCK.release()
    # Cancelling is not a failure, and it is the same exit code as a real one —
    # so it is told apart by there being nothing on stdout rather than by status.
    path = done.stdout.strip().splitlines()[0].strip() if done.stdout.strip() else ""
    if not path:
        return None, "No folder picked"
    if not Path(path).is_dir():
        return None, f"That is not a folder: {path}"
    return str(Path(path).resolve()), "Picked"

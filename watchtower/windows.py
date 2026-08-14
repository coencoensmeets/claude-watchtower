"""Matching a session to the window that is showing it, and raising it.

X11 only, through xdotool and xprop. Nothing here is certain: a window is
matched by walking the process tree, which several terminals make ambiguous by
running every tab under one process. The confidence of a match is part of the
answer rather than hidden, and a pairing you make by hand is remembered.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from watchtower.config import MAX_NAME, MAX_NAMES, NAME_FILE, PAIR_FILE
from watchtower.proc import proc_name


def decode_xprop(text: str) -> str:
    """xprop escapes non-ASCII bytes, so unescape then read them back as UTF-8.

    Only when it actually escaped something: a title that already arrived as
    proper text must be left alone, since the round-trip cannot represent
    characters outside latin-1 and would replace them with '?'.
    """
    if "\\" not in text:
        return text
    try:
        return text.encode("latin-1", "backslashreplace").decode("unicode_escape").encode(
            "latin-1", "replace"
        ).decode("utf-8", "replace")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


# Window classes and process names that plausibly host a Claude Code session.
# A title alone is only allowed to identify a window if it is one of these:
# plenty of browsers and file managers put a project folder in their title too.
HOST_HINTS = (
    "terminal", "xterm", "urxvt", "rxvt", "konsole", "kitty", "ghostty",
    "wezterm", "alacritty", "tilix", "guake", "terminator", "foot", "st-256color",
    "code", "code-oss", "vscodium", "cursor", "windsurf", "jetbrains", "tmux",
)


def looks_like_host(wclass: str, pid: int | None) -> bool:
    """Whether this window belongs to something a session could be running in."""
    marks = f"{wclass} {proc_name(pid) if pid else ''}".lower()
    return any(hint in marks for hint in HOST_HINTS)


class WindowIndex:
    """Visible top-level windows with their pid and title, briefly cached."""

    def __init__(self, ttl: float = 4.0) -> None:
        self.ttl = ttl
        self._at = 0.0
        self._windows: list[dict] = []
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(shutil.which("xdotool") and shutil.which("xprop") and os.environ.get("DISPLAY"))

    def windows(self, force: bool = False) -> list[dict]:
        with self._lock:
            if not force and time.time() - self._at < self.ttl:
                return self._windows
            self._windows = self._scan()
            self._at = time.time()
            return self._windows

    def _scan(self) -> list[dict]:
        if not self.available():
            return []
        try:
            root = subprocess.run(
                ["xprop", "-root", "_NET_CLIENT_LIST"],
                capture_output=True, text=True, timeout=4,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        ids = re.findall(r"0x[0-9a-fA-F]+", root)
        found: list[dict] = []
        for window_id in ids:
            try:
                props = subprocess.run(
                    ["xprop", "-id", window_id, "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME", "WM_NAME"],
                    capture_output=True, text=True, timeout=4,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            pid_match = re.search(r"_NET_WM_PID\(\w+\) = (\d+)", props)
            title_match = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', props) or re.search(
                r'WM_NAME\(\w+\) = "(.*)"', props
            )
            class_match = re.search(r'WM_CLASS\(\w+\) = "(?:[^"]*)", "([^"]*)"', props)
            title = decode_xprop(title_match.group(1) if title_match else "")
            # xterm and a few others never set _NET_WM_PID. Such a window can
            # still be identified by its title, so it is kept rather than
            # dropped; only one with nothing at all to go on is useless.
            if not pid_match and not title:
                continue
            pid = int(pid_match.group(1)) if pid_match else None
            wclass = class_match.group(1) if class_match else ""
            found.append({
                "id": window_id,
                "pid": pid,
                "title": title,
                "wclass": wclass,
                "host": looks_like_host(wclass, pid),
            })
        return found

    def match(self, session: dict) -> dict | None:
        """Find the window most likely to own this session.

        A window's _NET_WM_PID is the terminal or editor process, so we look for
        a window whose pid sits on the session's ancestor chain, and read the
        title for corroboration.

        The pid is not always the discriminator it looks like. GNOME Terminal,
        and every other terminal with a server process, reports the *same* pid
        for every one of its windows: each one then sits on the chain and scores
        alike. The old code took the first of them, which is a coin flip wearing
        the word "likely". When the leaders tie, this says `ambiguous` and hands
        the choice on — to the probe, or to you.
        """
        windows = self.windows()
        if not windows:
            return None
        chain = session.get("ancestors") or []
        cwd = session.get("cwd") or ""
        folder = os.path.basename(cwd).lower()
        home = str(Path.home())
        # Terminals write the folder into the title contracted, as ~/work/thing.
        short_cwd = ("~" + cwd[len(home):]) if home and cwd.startswith(home) else cwd
        # The session's own name, not one you typed here — the window title
        # knows nothing about your renaming.
        name = (session.get("defaultName") or session.get("name") or "").lower()

        scored: list[tuple[int, dict]] = []
        for window in windows:
            score = 0
            if window["pid"] and window["pid"] in chain:
                # A nearer ancestor is a tighter relationship.
                score += 100 - chain.index(window["pid"])
            title = window["title"].lower()
            if cwd and (cwd.lower() in title or short_cwd.lower() in title):
                score += 50
            elif folder and folder in title:
                score += 40
            if name and name in title:
                score += 25
            # A title on its own is weak evidence, so it only counts for a
            # window that could host a session at all — not for the browser
            # tab that happens to be showing the same folder name.
            if score and (score >= 90 or window["host"]):
                scored.append((score, window))

        if not scored:
            return None
        best = max(score for score, _ in scored)
        if best < 40:
            return None
        leaders = [window for score, window in scored if score == best]
        if len(leaders) > 1:
            return {
                **leaders[0],
                "confidence": "ambiguous",
                "candidates": [
                    {"id": w["id"], "title": w["title"], "wclass": w["wclass"]} for w in leaders
                ],
                # Only a probe can separate tabs of one terminal; a pid cannot.
                "canIdentify": bool(session.get("tty")),
            }
        return {**leaders[0], "confidence": "high" if best >= 130 else "likely"}


WINDOWS = WindowIndex()


def load_pairs() -> dict[str, dict]:
    """Remembered window pairings, as {sessionId: {"id", "how"}}.

    `how` is "picked" when you clicked the window and "identified" when the
    probe found it, because the panel should not tell you that you chose a
    window you never clicked. A file from before this distinction holds bare
    window ids, and those read as picked.
    """
    try:
        data = json.loads(PAIR_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    pairs: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(value, dict) and value.get("id"):
            pairs[str(key)] = {"id": str(value["id"]),
                               "how": "identified" if value.get("how") == "identified" else "picked"}
        elif isinstance(value, str):
            pairs[str(key)] = {"id": value, "how": "picked"}
    return pairs


def save_pairs(pairs: dict[str, dict]) -> None:
    PAIR_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAIR_FILE.write_text(json.dumps(pairs, indent=2))


def load_names() -> dict[str, str]:
    try:
        data = json.loads(NAME_FILE.read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_names(names: dict[str, str]) -> None:
    if len(names) > MAX_NAMES:  # oldest first — dicts keep insertion order
        names = dict(list(names.items())[-MAX_NAMES:])
    NAME_FILE.parent.mkdir(parents=True, exist_ok=True)
    NAME_FILE.write_text(json.dumps(names, indent=2))


def clean_name(text: object) -> str:
    """One line, no control characters, short enough to sit in a header."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:MAX_NAME]


def window_exists(window_id: str) -> bool:
    return any(w["id"].lower() == window_id.lower() for w in WINDOWS.windows())


def window_title(window_id: str) -> str:
    for window in WINDOWS.windows():
        if window["id"].lower() == window_id.lower():
            return window["title"]
    return ""


def activate(window_id: str) -> tuple[bool, str]:
    if not shutil.which("xdotool"):
        return False, "xdotool is not installed"
    try:
        result = subprocess.run(
            ["xdotool", "windowactivate", "--sync", window_id],
            capture_output=True, text=True, timeout=6,
        )
    except subprocess.TimeoutExpired:
        return False, "xdotool timed out"
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        subprocess.run(["xdotool", "windowraise", window_id], capture_output=True, timeout=6)
        return False, (result.stderr or "could not activate the window").strip()
    return True, "focused"


# How long to wait for a terminal to have retitled its window, and how often to
# look. A local terminal repaints in a frame or two; the ceiling is for a laden
# machine, and reaching it means the answer is no.
PROBE_TIMEOUT = 1.6


PROBE_STEP = 0.12


def probe_window(tty: str) -> tuple[str | None, str]:
    """Identify the window a pty is displayed in, by briefly retitling it.

    Nothing about an X window says which pty it is showing, and for a terminal
    with one process behind every window the pid says nothing either. So ask the
    terminal: writing an OSC title sequence to the pty is output, the way any
    program's output is, and the terminal answers by retitling the window that
    is showing it. Whichever window comes back wearing our marker is the one.

    The marker is pushed and popped on the xterm title stack, so the title the
    session had is put back exactly — including one Claude Code rewrites as it
    works. A terminal without the stack ignores both, and the recorded title is
    written back by hand instead.
    """
    if not WINDOWS.available():
        return None, "Window probing needs X11 and xdotool"
    marker = f"watchtower-probe-{uuid.uuid4().hex[:12]}"
    before = {w["id"]: w["title"] for w in WINDOWS.windows(force=True)}
    try:
        with open(tty, "w") as terminal:
            # Push the current title, then claim it.
            terminal.write(f"\033[22;2t\033]2;{marker}\007")
    except OSError as exc:
        return None, f"Could not write to {tty}: {exc}"

    found = None
    deadline = time.time() + PROBE_TIMEOUT
    while time.time() < deadline:
        time.sleep(PROBE_STEP)
        for window in WINDOWS.windows(force=True):
            if window["title"] == marker:
                found = window
                break
        if found:
            break

    try:
        with open(tty, "w") as terminal:
            terminal.write("\033[23;2t")  # pop it back
            if found and before.get(found["id"]):
                # Belt and braces, for a terminal with no title stack.
                if any(w["title"] == marker for w in WINDOWS.windows(force=True)):
                    terminal.write(f"\033]2;{before[found['id']]}\007")
    except OSError:
        pass
    WINDOWS.windows(force=True)

    if not found:
        return None, (
            "The terminal did not retitle a window — if this session is in a "
            "background tab, bring it to the front and try again, or pair by hand"
        )
    return found["id"], "identified"


def select_window() -> tuple[str | None, str]:
    """Block until the person clicks a window, then return its id."""
    if not shutil.which("xdotool"):
        return None, "xdotool is not installed"
    try:
        result = subprocess.run(
            ["xdotool", "selectwindow"], capture_output=True, text=True, timeout=45
        )
    except subprocess.TimeoutExpired:
        return None, "No window was clicked within 45 seconds"
    except OSError as exc:
        return None, str(exc)
    raw = (result.stdout or "").strip()
    if not raw.isdigit():
        return None, (result.stderr or "No window id came back").strip()
    return hex(int(raw)), "paired"


def identify_and_pair(session_id: str, session: dict) -> tuple[str | None, str]:
    """Probe for a session's window and remember the answer.

    The probe costs a title flicker, so its result is written to pairs.json
    like one you made by clicking: a session is identified once, not on every
    poll, and the answer survives a restart of the panel.
    """
    tty = session.get("tty")
    if not tty:
        return None, "This session is not attached to a terminal the panel can reach"
    window_id, message = probe_window(tty)
    if not window_id:
        return None, message
    pairs = load_pairs()
    pairs[session_id] = {"id": window_id, "how": "identified"}
    save_pairs(pairs)
    return window_id, message


def resolve_window(session_id: str, session: dict) -> tuple[dict | None, str, bool]:
    """The window to act on, identifying it first if the guess was a tie.

    Returns the window, a message for when there is none, and whether the probe
    is what found it — the caller says so, because a title that flickered wants
    explaining.
    """
    window = session.get("window")
    if window and window.get("confidence") != "ambiguous":
        return window, "", False
    if session.get("tty"):
        window_id, message = identify_and_pair(session_id, session)
        if window_id:
            return {"id": window_id, "confidence": "identified"}, "", True
        if window:
            # A tie is still a guess, and a guess is worse than saying so.
            return None, f"Could not tell this session's window from the others — {message}", False
        return None, message, False
    if window:
        return None, "Several windows look equally likely and there is no pty to tell them apart", False
    return None, "No window is paired with this session yet", False

"""Answering a terminal session's prompt, when that terminal is a tmux pane.

A session in a terminal is blocked on a prompt the panel cannot reach: its
messaging socket takes a user turn and a rename and nothing else, and a turn
queues behind the prompt. Pressing keys is the only way through, and pressing
them at a *window* is not safe — a window holds tabs, and the key lands in
whichever has focus, which may be another Claude.

A tmux pane is different. It is one terminal with one id, `tmux send-keys -t
%3` reaches exactly that pane whatever is focused, and `capture-pane` reads
back what is on it. So for a session in tmux the panel can do what the terminal
itself would: read the prompt off the screen, and press the key for the option
you picked — after reading the screen again to make sure the same prompt is
still the one up.

The pane is found from the session's own environment, not from its session
file. Claude Code does write `tmux` into the session file, but as
`session:window.pane` without saying which tmux server, and two servers can
each have a `%0`. `TMUX` in the process's environment names the server's socket,
`TMUX_PANE` the pane, and the pane's own pid has to be the session or one of its
ancestors before anything is sent.

Measured against Claude Code 2.1.325. The screens it draws are not an interface
and can change; every function here reads them defensively, and a screen it
does not recognise is a prompt it does not offer to answer.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path

from watchtower.proc import ancestors, proc_starttime

TMUX_SECONDS = 2.0
# A screen read is a subprocess; a row polled every second does not need a
# fresher one than this.
SCREEN_SECONDS = 0.8
# How long to wait for the screen to move after a key, before deciding it did not.
STEP_SECONDS = 3.0

_PANES: dict[int, tuple[str | None, dict | None]] = {}
_SCREENS: dict[str, tuple[float, list[str]]] = {}


def tmux_binary() -> str | None:
    return shutil.which("tmux")


def _environ(pid: int) -> dict[str, str]:
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    found = {}
    for item in raw.split(b"\0"):
        key, _, value = item.partition(b"=")
        if key in (b"TMUX", b"TMUX_PANE"):
            found[key.decode()] = value.decode("utf-8", "replace")
    return found


def _run(pane: dict, *args: str) -> subprocess.CompletedProcess | None:
    binary = tmux_binary()
    if not binary:
        return None
    try:
        return subprocess.run([binary, "-S", pane["socket"], *args], capture_output=True,
                              text=True, timeout=TMUX_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None


def pane_of(pid: int) -> dict | None:
    """The tmux pane this session is running in, checked, or None.

    Cached per process: a process does not move between panes. The cache is
    keyed with the process's start time too, so a reused pid is asked afresh.
    """
    if not isinstance(pid, int) or pid <= 1 or not tmux_binary():
        return None
    started = proc_starttime(pid)
    hit = _PANES.get(pid)
    if hit and hit[0] == started:
        return hit[1]
    pane = None
    env = _environ(pid)
    socket_path = env.get("TMUX", "").split(",")[0]
    pane_id = env.get("TMUX_PANE", "")
    if socket_path and re.fullmatch(r"%\d{1,10}", pane_id):
        candidate = {"socket": socket_path, "pane": pane_id}
        done = _run(candidate, "display-message", "-p", "-t", pane_id, "#{pane_pid}")
        try:
            owner = int(done.stdout.strip()) if done and done.returncode == 0 else 0
        except ValueError:
            owner = 0
        # The pane's shell is the session or something above it. Anything else
        # means the environment is stale — a process that inherited TMUX from a
        # pane it has since left — and keys sent there would land in a stranger.
        if owner and (owner == pid or owner in ancestors(pid)):
            pane = candidate
    _PANES[pid] = (started, pane)
    return pane


def screen(pane: dict, fresh: bool = False) -> list[str]:
    """What the pane is showing, line by line, blank lines dropped."""
    key = f"{pane['socket']}|{pane['pane']}"
    hit = _SCREENS.get(key)
    if hit and not fresh and time.time() - hit[0] < SCREEN_SECONDS:
        return hit[1]
    done = _run(pane, "capture-pane", "-p", "-t", pane["pane"])
    lines = [line.rstrip() for line in (done.stdout if done and done.returncode == 0 else "").splitlines()]
    lines = [line for line in lines if line.strip()]
    _SCREENS[key] = (time.time(), lines)
    return lines


def press(pane: dict, *keys: str) -> bool:
    _SCREENS.pop(f"{pane['socket']}|{pane['pane']}", None)
    done = _run(pane, "send-keys", "-t", pane["pane"], *keys)
    return bool(done and done.returncode == 0)


# --- reading a permission prompt

OPTION = re.compile(r"^\s*(?:❯\s*)?(\d{1,2})\.\s+(.+?)\s*$")
RULE = re.compile(r"^[─━]{20,}")
DASHED = re.compile(r"^\s*[╌┄]{20,}")


def read_permission(lines: list[str]) -> dict | None:
    """The permission prompt on screen, if that is what is up.

    Claude Code draws it under a full-width rule: a header naming the kind of
    tool (`Bash command`, `Edit file`), what it wants to do, a question, then
    numbered options and a footer that starts `Esc to cancel`. A question asked
    with AskUserQuestion has a different footer (`Enter to select`) and is not
    read here.
    """
    foot = next((i for i in range(len(lines) - 1, -1, -1)
                 if lines[i].strip().startswith("Esc to cancel")), None)
    if foot is None:
        return None
    options: list[dict] = []
    i = foot - 1
    while i >= 0:
        hit = OPTION.match(lines[i])
        if not hit:
            break
        options.insert(0, {"n": int(hit.group(1)), "label": hit.group(2).strip()})
        i -= 1
    if len(options) < 2 or [o["n"] for o in options] != list(range(1, len(options) + 1)):
        return None
    question = lines[i].strip() if i >= 0 else ""
    top = next((j for j in range(i, -1, -1) if RULE.match(lines[j].strip())), None)
    if top is None:
        return None
    body = [line.strip() for line in lines[top + 1:i] if not line.strip().startswith("Tip:")]
    title = body[0] if body else "Permission"
    what = [line for line in body[1:] if not DASHED.match(line)]
    refuse = next((o["n"] for o in reversed(options) if o["label"].lower().startswith("no")), None)
    if refuse is None:
        return None
    block = "\n".join(lines[top + 1:foot + 1])
    return {
        "title": title[:120],
        "question": question[:200],
        "what": "\n".join(what)[:600],
        "options": options,
        "allow": 1,
        "refuse": refuse,
        "fingerprint": hashlib.sha1(block.encode()).hexdigest()[:16],
    }


# --- reading and answering a question

TICK = re.compile(r"^\[[ ✔]\]\s*")
NOT_AN_ANSWER = ("Type something", "Chat about this")


def question_up(lines: list[str]) -> bool:
    return any(line.strip().startswith("Enter to select") for line in lines)


def read_question(lines: list[str]) -> dict | None:
    """The AskUserQuestion question on screen, if one is.

    Read off the screen rather than the transcript, because Claude Code 2.1.325
    writes the call into the transcript only once it has been answered — while
    it is up, the transcript does not have it. Only the question showing is
    read: with several, the prompt shows them one at a time, and so does the
    panel.

    The layout: an optional row of tabs (`☐ Colour  ☐ Toppings  ✔ Submit`), the
    question, numbered options — `[ ]` before each when several may be picked —
    each with its description indented below, then `Type something` and `Chat
    about this`, which are not answers, and a footer that starts `Enter to
    select`.
    """
    foot = next((i for i in range(len(lines) - 1, -1, -1)
                 if lines[i].strip().startswith("Enter to select")), None)
    if foot is None:
        return None
    first = None
    for i in range(foot - 1, -1, -1):
        if re.match(r"^\s*(?:❯\s*)?1\.\s", lines[i]):
            first = i
            break
        if "Ready to submit" in lines[i]:
            return None
    if first is None:
        return None
    top = first - 1
    while top >= 0 and not (RULE.match(lines[top].strip()) or re.search(r"[☐☒]", lines[top])):
        top -= 1
    question = " ".join(line.strip() for line in lines[top + 1:first])
    options: list[dict] = []
    many = False
    for line in lines[first:foot]:
        hit = OPTION.match(line)
        if hit:
            label = hit.group(2).strip()
            if TICK.match(label):
                many = True
                label = TICK.sub("", label)
            options.append({"n": int(hit.group(1)), "label": label.rstrip("."), "description": ""})
        elif options and line.startswith("   ") and line.strip() != "Submit" \
                and not RULE.match(line.strip()):
            options[-1]["description"] = (options[-1]["description"] + " " + line.strip()).strip()
    options = [o for o in options if not o["label"].startswith(NOT_AN_ANSWER)]
    if not question or not options or [o["n"] for o in options] != list(range(1, len(options) + 1)):
        return None
    block = "\n".join([question] + [o["label"] for o in options])
    return {
        "question": question[:400],
        "multiSelect": many,
        "options": options[:9],
        "fingerprint": hashlib.sha1(block.encode()).hexdigest()[:16],
    }


def read_review(lines: list[str]) -> str | None:
    """The answers listed on the screen that asks `Ready to submit your answers?`."""
    at = next((i for i, line in enumerate(lines) if "Ready to submit" in line), None)
    if at is None or not any(re.match(r"^\s*(?:❯\s*)?1\.\s+Submit answers", line) for line in lines[at:]):
        return None
    begin = next((i for i, line in enumerate(lines[:at]) if "Review your answers" in line), at)
    return " ".join(" ".join(lines[begin:at]).split())


def _wait_for(pane: dict, test, seconds: float = STEP_SECONDS) -> list[str] | None:
    deadline = time.time() + seconds
    while True:
        lines = screen(pane, fresh=True)
        if test(lines):
            return lines
        if time.time() > deadline:
            return None
        time.sleep(0.15)


def answer_question(pane: dict, fingerprint: str, chosen: list[str]) -> tuple[bool, str]:
    """Answer the question on screen with these option labels, as a person would.

    Single choice: its number, and the prompt moves on by itself. Several:
    each number toggles its box, the boxes are checked, and the cursor goes down
    to Submit. The question is read again first, and nothing is pressed unless it
    is still the one that was answered. When the prompt ends on its review screen,
    that is confirmed too — once it lists what was just picked.
    """
    standing = read_question(screen(pane, fresh=True))
    if not standing:
        return False, "There is no question on its screen any more"
    if standing["fingerprint"] != fingerprint:
        return False, "The question on its screen has changed since — look again"
    labels = [o["label"] for o in standing["options"]]
    numbers = [labels.index(c) + 1 for c in chosen if c in labels]
    if not numbers or len(numbers) != len(chosen):
        return False, "Pick one of the options shown"
    if not standing["multiSelect"]:
        press(pane, str(numbers[0]))
    else:
        ticked = re.compile(r"^\s*(?:❯\s*)?(\d)\.\s+\[✔\]")
        already = {int(m.group(1)) for m in map(ticked.match, screen(pane, fresh=True)) if m}
        for n in sorted(set(numbers) ^ already):
            press(pane, str(n))
        want = set(numbers)
        if not _wait_for(pane, lambda lines: {int(m.group(1)) for m in map(ticked.match, lines) if m} == want):
            return False, "The boxes did not tick as expected, so nothing was submitted"
        for _ in range(len(labels) + 4):
            if any(re.match(r"^\s*❯\s*Submit\b", line) for line in screen(pane, fresh=True)):
                break
            press(pane, "Down")
            time.sleep(0.12)
        else:
            return False, "Could not reach Submit, so nothing was submitted"
        press(pane, "Enter")
    lines = _wait_for(pane, lambda lines: (read_question(lines) or {}).get("fingerprint") != fingerprint)
    if lines is None:
        return False, "The terminal did not take the answer"
    review = read_review(lines)
    if review is not None:
        if not all(c[:30] in review for c in chosen):
            return True, "Answered — its review screen is waiting to be submitted in the terminal"
        press(pane, "1")
    return True, "Answered in its terminal"


def skip_question(pane: dict, fingerprint: str) -> tuple[bool, str]:
    """Close the question without answering — if it is still the one you saw."""
    standing = read_question(screen(pane, fresh=True))
    if not standing or standing["fingerprint"] != fingerprint:
        return False, "That question is no longer on its screen"
    press(pane, "Escape")
    return True, "Skipped in its terminal"


def answer_permission(pane: dict, fingerprint: str, allow: bool) -> tuple[bool, str]:
    """Press Yes or No on the permission prompt — if it is still the one you saw."""
    standing = read_permission(screen(pane, fresh=True))
    if not standing:
        return False, "There is no permission prompt on its screen any more"
    if standing["fingerprint"] != fingerprint:
        return False, "The prompt on its screen has changed since — look again"
    press(pane, str(standing["allow"] if allow else standing["refuse"]))
    return True, "Allowed in its terminal" if allow else "Refused in its terminal"


def terminal_ask(pid: int, waiting_for: str | None) -> dict | None:
    """The prompt a waiting terminal session is standing on, in the shape the
    panel's own ask card draws — or None if it cannot be answered from here."""
    pane = pane_of(pid)
    if not pane:
        return None
    lines = screen(pane)
    asked = read_question(lines)
    if asked:
        return {
            "via": "tmux", "asks": True,
            "requestId": f"tmux:{asked['fingerprint']}",
            "name": "a question",
            "input": {"questions": [{
                "question": asked["question"], "header": "", "multiSelect": asked["multiSelect"],
                "options": [{"label": o["label"], "description": o["description"]} for o in asked["options"]],
            }]},
        }
    if waiting_for == "permission prompt":
        standing = read_permission(lines)
        if standing:
            return {
                "via": "tmux", "asks": False,
                "requestId": f"tmux:{standing['fingerprint']}",
                "name": standing["title"],
                "tool": standing["title"],
                "what": standing["what"] or standing["question"],
                "options": [o["label"] for o in standing["options"]],
            }
    return None


# --- the permission mode

# The footer under the prompt says which mode is on: `⏸ manual mode on`,
# `⏵⏵ accept edits on`, `⏸ plan mode on`, `⏵⏵ auto mode on`. It is drawn only
# while the prompt box is showing — a dialog covers it — so a screen without it
# says nothing about the mode, rather than that the mode is unknown.
FOOTER = re.compile(r"^\s*(?:⏸|⏵⏵?)\s+(.+?)\s+on\b")
MODE_WORDS = {
    "manual mode": "default",
    "accept edits": "acceptEdits",
    "plan mode": "plan",
    "auto mode": "auto",
    "bypass permissions": "bypassPermissions",
    "don't ask": "dontAsk",
    "dont ask": "dontAsk",
}


def read_mode(lines: list[str]) -> str | None:
    """The permission mode the footer shows, or None if no footer is showing."""
    for line in reversed(lines[-6:]):
        hit = FOOTER.match(line)
        if hit:
            return MODE_WORDS.get(hit.group(1).strip().lower())
    return None


def set_mode(pane: dict, target: str) -> tuple[bool, str]:
    """Move the mode with Shift+Tab, one press at a time, watching the footer.

    Shift+Tab cycles through the modes the session offers — manual, accept
    edits, plan, auto, in 2.1.325 — so the number of presses is not worked out
    in advance: each press is followed by reading the footer again, and it stops
    the moment the footer shows the mode asked for. A mode the cycle never
    reaches brings it back round to where it started, and says so.
    """
    start = read_mode(screen(pane, fresh=True))
    if start is None:
        return False, "Its prompt is not showing — a dialog is covering it, or it is somewhere else"
    if start == target:
        return True, "It is already in that mode"
    current = start
    for _ in range(8):
        press(pane, "BTab")
        before = current
        lines = _wait_for(pane, lambda lines: read_mode(lines) not in (None, before), 2.0)
        if lines is None:
            return False, "The footer did not move after Shift+Tab, so it was left alone"
        current = read_mode(lines)
        if current == target:
            return True, "Mode changed in its terminal"
        if current == start:
            return False, "That mode is not one this session offers with Shift+Tab"
    return False, "The mode did not come round, so it was left where it is"

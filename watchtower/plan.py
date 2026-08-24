"""How much of the subscription has gone.

The one figure in the panel that no file on this machine knows: it comes from
running Claude Code's own `/usage`, so the reading is the same one the terminal
would give you. Held for a while and read on the clock rather than on the poll,
because it is an errand rather than a file read.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

from watchtower import config
from watchtower.config import HOME
from watchtower.errands import own_errand
from watchtower.git.message import MESSAGE_MODEL
from watchtower.git.write import git_said


# `/usage` is the one thing in this panel that no file on this machine knows.
# What a subscription has left is the account's, not the session's, and it lives
# behind Anthropic's API — so it is asked for the way you would ask yourself, by
# running Claude Code's own command and reading what it prints.
#
# Which is the point of doing it this way. The alternative was for the panel to
# read the OAuth token out of ~/.claude/.credentials.json and call an undocumented
# endpoint itself: a web server on this machine holding your credentials, for a
# reading the official client already gives away for free. This spawns `claude`,
# handles no secret, and asks for nothing the terminal would not have told you.
#
# It costs no tokens — the command fetches and prints, and samples no model, which
# a run against a fresh transcript confirms: not one usage entry — but it does take
# five seconds and a process, so it is asked rarely and its answer is kept.
PLAN_TIMEOUT = 45.0


PLAN_FRESH = 300.0


PLAN_LOCK = threading.Lock()


PLAN_HELD: dict = {}


# "Current session: 34% used · resets Aug 12, 5:49pm (Europe/Amsterdam)", and the
# week's two lines in the same shape. The reset clause is optional: a limit at 0%
# has nothing to reset from yet.
PLAN_LIMIT = re.compile(
    r"^(?P<name>[^:]{1,60}?):\s*(?P<percent>\d{1,3})%\s*used"
    r"(?:\s*[·|-]\s*resets\s*(?P<resets>.+?))?\s*$")


# "Last 24h · 4141 requests · 46 sessions" — the heading of a block of bullets.
PLAN_BLOCK = re.compile(r"^(Last\s.+|What's contributing.*)$")


def parse_plan(text: str) -> dict:
    """Read `/usage`'s report into figures, keeping the text it came from.

    The output is a human's report rather than an interface, so nothing here
    insists on it. Every line that reads as a limit becomes one; anything else is
    kept in order as prose, and a run that parses to nothing still hands back
    what it was given rather than an empty panel.
    """
    headline, limits, blocks = "", [], []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = PLAN_LIMIT.match(line.strip())
        if match:
            limits.append({
                "name": match.group("name").strip(),
                "percent": min(100, int(match.group("percent"))),
                "resets": (match.group("resets") or "").strip(),
            })
            continue
        if PLAN_BLOCK.match(line.strip()):
            blocks.append({"title": line.strip(), "lines": []})
            continue
        if blocks and raw.startswith((" ", "\t")):
            blocks[-1]["lines"].append(line.strip())
            continue
        if not headline:
            headline = line.strip()
        elif blocks:
            blocks[-1]["lines"].append(line.strip())
    return {"headline": headline, "limits": limits, "blocks": blocks, "text": text.strip()}


def run_plan() -> dict:
    """Run `claude /usage` once and read the answer."""
    claude = shutil.which("claude")
    if not claude:
        return {"ok": False, "message": "Cannot find the claude command on PATH"}
    try:
        # Same shape as the commit-message errand: printing, no tools, so there is
        # no permission prompt to answer and nothing it can touch. Run from home
        # rather than a repository — this is the account's reading, not a folder's.
        process = subprocess.Popen(
            [claude, "--print", "/usage", "--model", MESSAGE_MODEL,
             "--allowed-tools", "", "--output-format", "text"],
            cwd=str(HOME), text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "message": f"Could not run claude: {error}"}

    own_errand(process.pid, True)
    try:
        out, err = process.communicate(timeout=PLAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return {"ok": False, "message": f"Claude did not answer within {int(PLAN_TIMEOUT)}s"}
    except (OSError, subprocess.SubprocessError) as error:
        process.kill()
        return {"ok": False, "message": f"Could not run claude: {error}"}
    finally:
        own_errand(process.pid, False)

    if process.returncode != 0:
        return {"ok": False, "message": git_said(err) or "Claude could not read your usage"}
    if not out.strip():
        return {"ok": False, "message": "Claude answered with nothing"}
    return {"ok": True, **parse_plan(out)}


def read_plan(force: bool = False) -> dict:
    """The account's remaining plan, read at most every few minutes.

    A reading costs five seconds and a process, and the figure moves in
    percentage points over hours, so it is kept and handed back until it is stale.
    Two people opening the dialog at once get the same answer rather than two
    runs: the second is told one is on its way and shown what there is.
    """
    now = time.time()
    with PLAN_LOCK:
        held = dict(PLAN_HELD)
        fresh = held.get("ok") and now - held.get("at", 0) < PLAN_FRESH
        if fresh and not force:
            return {**held, "reading": False}
        if config.PLAN_RUNNING:
            # Somebody's run is already in flight. Hand back what we have and say
            # so, rather than starting a second `claude` for the same answer.
            return {**held, "reading": True} if held else {"ok": False, "reading": True,
                                                          "message": "Reading your usage…"}
        config.PLAN_RUNNING = True

    try:
        answer = run_plan()
    finally:
        with PLAN_LOCK:
            config.PLAN_RUNNING = False

    answer["at"] = time.time()
    if answer.get("ok"):
        with PLAN_LOCK:
            PLAN_HELD.clear()
            PLAN_HELD.update(answer)
        return {**answer, "reading": False}
    # A failed read does not throw away a good one: the last figures with the
    # reason the refresh failed is more use than the reason alone.
    if held.get("ok"):
        return {**held, "reading": False, "message": answer.get("message", "")}
    return {**answer, "reading": False}

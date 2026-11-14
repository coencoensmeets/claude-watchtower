"""Recommended Claude Code add-ons the panel can install for you.

**Settings → Claude skills** lists them with a switch each. On installs, off
removes, and nothing is installed until someone turns it on. Six so far:

  - the panel's own MCP server (see watchtower/mcp.py), registered user-wide
    with `claude mcp add --scope user`, so every session can see and drive
    the others;
  - the same server allowed to approve another session's tool use — a
    separate switch because it is a separate decision, and off by default;
  - a `watchtower` skill that tells Claude when those tools are worth using.
  - a `watchtower-preview` skill that has Claude show anything viewable it
    builds in the panel's Preview tab;
  - a `watchtower-style` skill that makes the Watchtower styleguide the default
    look for anything visual Claude makes;
  - a `watchtower-research` skill with a method for researching on the
    internet: search wide, read primary sources, cross-check, cite.

What is installed is read from where Claude Code keeps it, not remembered
here: `~/.claude.json` for the MCP server, `~/.claude/skills/` for the skill.
So a server removed with `claude mcp remove`, or added by hand, shows as it
really is. Writes to `~/.claude.json` go through the `claude` CLI rather than
being made here, because Claude Code rewrites that file itself all the time
and a second writer would race it.

Nothing that is not ours is touched: a `watchtower` server or skill pointing
somewhere else is reported and left alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from watchtower.config import HOME, ROOT

MCP_NAME = "watchtower"
MCP_SCRIPT = ROOT / "mcp_server.py"
APPROVALS_ENV = "CLAUDE_WATCHTOWER_MCP_APPROVALS"
SKILL_NAME = "watchtower"
SKILL_SOURCE = ROOT / "skills" / SKILL_NAME
# Each switch that installs a skill, and the folder under skills/ it links.
SKILL_ITEMS = {"skill": SKILL_NAME, "preview-skill": "watchtower-preview",
               "style-skill": "watchtower-style", "research-skill": "watchtower-research"}

# Where Claude Code keeps things. Module attributes so the tests can move them.
CLAUDE_JSON = HOME / ".claude.json"
SKILLS_DIR = HOME / ".claude" / "skills"

CATALOG = [
    {"id": "mcp", "title": "Watchtower tools",
     "description": "Lets every Claude session see the other sessions on this machine, read them, "
                    "message them, start new ones and stop turns, through the panel. "
                    "Sessions already open need a restart to get it."},
    {"id": "mcp-approvals", "title": "Let Claude approve other sessions' tools", "requires": "mcp",
     "description": "Lets a session allow another session's tool use. Off is safer: a session that "
                    "can approve another's prompts can run anything through it."},
    {"id": "skill", "title": "Watchtower skill",
     "description": "Teaches Claude when the Watchtower tools are worth using: checking on, "
                    "handing work to and coordinating with your other sessions."},
    {"id": "preview-skill", "title": "Preview skill",
     "description": "Whenever Claude builds something you can look at — a page, a styleguide, a "
                    "report, a site — it shows it in the session's Preview tab, so you can open it "
                    "here or on your phone."},
    {"id": "style-skill", "title": "Watchtower house style",
     "description": "Every page, site, report or UI Claude makes uses the Watchtower look from the "
                    "styleguide — Urbanist, ink pills, chalk surfaces, lime accents — unless you ask "
                    "for a different style."},
    {"id": "research-skill", "title": "Research skill",
     "description": "When you ask Claude to look something up or research it online, it searches from "
                    "several angles, reads the primary sources, checks claims against each other and "
                    "cites every page it relied on, saying what is certain and what is not."},
]


def _run(args: list[str]) -> tuple[bool, str]:
    """A `claude` command, its success, and what it said."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    said = (done.stdout or done.stderr or "").strip().splitlines()
    return done.returncode == 0, said[-1] if said else ""


RUN = _run  # swapped in the tests


# ----------------------------------------------------------------- reading

def _mcp_entry() -> dict | None:
    try:
        data = json.loads(CLAUDE_JSON.read_text())
    except (OSError, ValueError):
        return None
    entry = (data.get("mcpServers") or {}).get(MCP_NAME)
    return entry if isinstance(entry, dict) else None


def _is_ours(entry: dict) -> bool:
    script = str(MCP_SCRIPT)
    return any(str(a) == script for a in entry.get("args") or []) or entry.get("command") == script


def _skill_state(name: str = SKILL_NAME) -> tuple[bool, bool]:
    """(installed, somebody else's)."""
    spot = SKILLS_DIR / name
    source = SKILL_SOURCE.parent / name
    if spot.is_symlink():
        try:
            return spot.resolve() == source.resolve(), spot.resolve() != source.resolve()
        except OSError:
            return False, True
    return False, spot.exists()


def read_skills() -> dict:
    """Every item, whether it is on, and whether it can be switched here."""
    claude = shutil.which("claude")
    entry = _mcp_entry()
    mcp_ours = bool(entry) and _is_ours(entry)
    approvals = mcp_ours and (entry.get("env") or {}).get(APPROVALS_ENV) == "1"
    items = []
    for item in CATALOG:
        row = dict(item)
        if item["id"] == "mcp":
            row["on"] = mcp_ours
            if entry and not mcp_ours:
                row["blocked"] = (f"Claude Code already has an MCP server called {MCP_NAME!r} that is not "
                                  f"this panel's. Remove it with `claude mcp remove {MCP_NAME}` to use this one.")
            elif not claude:
                row["blocked"] = "The panel cannot find `claude` on its PATH, which it needs to register this."
        elif item["id"] == "mcp-approvals":
            row["on"] = approvals
            if not mcp_ours:
                row["blocked"] = "Turn on Watchtower tools first."
        elif item["id"] in SKILL_ITEMS:
            name = SKILL_ITEMS[item["id"]]
            row["on"], theirs = _skill_state(name)
            if theirs:
                row["blocked"] = (f"There is already a skill at ~/.claude/skills/{name} that is not this "
                                  "panel's, so it is left alone.")
        items.append(row)
    return {"ok": True, "items": items}


# ----------------------------------------------------------------- writing

def _register(approvals: bool) -> tuple[bool, str]:
    claude = shutil.which("claude")
    if not claude:
        return False, "The panel cannot find `claude` on its PATH"
    entry = _mcp_entry()
    if entry and not _is_ours(entry):
        return False, f"An MCP server called {MCP_NAME!r} that is not this panel's is already registered"
    if entry:
        ok, said = RUN([claude, "mcp", "remove", "--scope", "user", MCP_NAME])
        if not ok:
            return False, said or "Could not replace the existing registration"
    # The name goes before -e: the CLI's -e takes every following word up to
    # the next option as another KEY=value, so after it the name is swallowed.
    args = [claude, "mcp", "add", "--scope", "user", MCP_NAME]
    if approvals:
        args += ["-e", f"{APPROVALS_ENV}=1"]
    args += ["--", sys.executable, str(MCP_SCRIPT)]
    ok, said = RUN(args)
    return ok, ("Registered. Sessions started from now on have the Watchtower tools; "
                "restart an open one to give it them." if ok else said or "claude mcp add failed")


def _unregister() -> tuple[bool, str]:
    entry = _mcp_entry()
    if not entry:
        return True, "Not registered"
    if not _is_ours(entry):
        return False, f"The {MCP_NAME!r} server registered is not this panel's, so it is left alone"
    claude = shutil.which("claude")
    if not claude:
        return False, "The panel cannot find `claude` on its PATH"
    ok, said = RUN([claude, "mcp", "remove", "--scope", "user", MCP_NAME])
    return ok, "Removed" if ok else said or "claude mcp remove failed"


def _skill(on: bool, name: str = SKILL_NAME) -> tuple[bool, str]:
    spot = SKILLS_DIR / name
    installed, theirs = _skill_state(name)
    if theirs:
        return False, f"~/.claude/skills/{name} is not this panel's, so it is left alone"
    if on:
        if installed:
            return True, "Already installed"
        try:
            SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            # A link rather than a copy, so the skill follows the panel when it
            # updates instead of going stale in a folder nobody looks in.
            os.symlink(SKILL_SOURCE.parent / name, spot, target_is_directory=True)
        except OSError as error:
            return False, f"Could not install it: {error.strerror or error}"
        return True, "Installed. Sessions started from now on have it"
    if installed:
        try:
            spot.unlink()
        except OSError as error:
            return False, f"Could not remove it: {error.strerror or error}"
    return True, "Removed"


def set_skill(item_id: str, on: bool) -> tuple[bool, str]:
    """Turn one item on or off; whether it worked, and what to say."""
    if item_id == "mcp":
        if on:
            return _register(approvals=False)
        return _unregister()
    if item_id == "mcp-approvals":
        entry = _mcp_entry()
        if not entry or not _is_ours(entry):
            return False, "Turn on Watchtower tools first"
        ok, said = _register(approvals=on)
        if ok:
            said = ("Sessions started from now on can approve other sessions' tools" if on
                    else "Sessions can no longer approve other sessions' tools")
        return ok, said
    if item_id in SKILL_ITEMS:
        return _skill(on, SKILL_ITEMS[item_id])
    return False, f"There is no item called {item_id!r}"

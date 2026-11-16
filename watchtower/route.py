"""Where a new piece of work should happen, decided by a headless Claude.

**+ New → Describe it…** in the panel. You say what you want to do; the panel
lists the projects it knows on this computer, hands both to a quick model with
no tools, and gets back one of two answers:

  - open: it belongs in a folder you already have — and which one;
  - create: it is something new — and what to call its folder.

Nothing happens on that answer alone. The panel shows it, with the reason, and
starts the session only when you press Start (and may be told another folder).
For a new project the folder is made then, and the session's first message
asks it to set the project up before doing what you asked.

The model runs like the commit-message one (watchtower/git/message.py):
`--print`, no tools, its pid held as the panel's own errand so it never shows
up as a session.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from watchtower.config import HOME, PROJECT_DIR
from watchtower.errands import own_errand

ROUTE_MODEL = "haiku"
ROUTE_TIMEOUT = 90.0
MAX_REQUEST = 4000
MAX_CANDIDATES = 60
SKIP_DIRS = {"node_modules", "venv", ".venv", "__pycache__", "snap", "go", "Library", "Applications"}


# ------------------------------------------------------------ what is known

def _tilde(path: str) -> str:
    home = str(HOME)
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _inside_home(path: Path) -> bool:
    try:
        home = HOME.resolve()
        spot = path.resolve()
    except (OSError, RuntimeError):
        return False
    return spot == home or home in spot.parents


def _transcript_cwd(folder: Path) -> tuple[str | None, float]:
    """The folder a project's newest transcript was recorded in, and when."""
    try:
        files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None, 0.0
    for spot in files[:3]:
        try:
            with spot.open() as handle:
                for _, line in zip(range(40), handle):
                    if '"cwd"' not in line:
                        continue
                    cwd = json.loads(line).get("cwd")
                    if cwd:
                        return cwd, spot.stat().st_mtime
        except (OSError, ValueError):
            continue
    return None, 0.0


def _about(folder: Path) -> str:
    """One line on what a folder is, from its README if it has one."""
    for name in ("README.md", "README", "readme.md", "README.rst", "package.json", "pyproject.toml"):
        spot = folder / name
        try:
            if not spot.is_file():
                continue
            text = spot.read_text(errors="replace")[:2000]
        except OSError:
            continue
        if name == "package.json":
            try:
                return str(json.loads(text).get("description") or "")[:140]
            except ValueError:
                continue
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if len(line) > 3 and not line.startswith(("[!", "<", "!", "=", "[project", "[tool")):
                return line[:140]
    return ""


def known_folders(extra: list[str] | None = None) -> list[dict]:
    """Projects on this computer, most recently used first.

    Three sources, because each misses some: folders sessions ran in
    (~/.claude/projects), folders on the panel's list right now (`extra`),
    and git checkouts two levels into home that Claude has never been run in.
    """
    seen: dict[str, float] = {}
    try:
        for folder in PROJECT_DIR.iterdir():
            if folder.is_dir():
                cwd, at = _transcript_cwd(folder)
                if cwd:
                    seen[cwd] = max(seen.get(cwd, 0.0), at)
    except OSError:
        pass
    for cwd in extra or []:
        seen[cwd] = max(seen.get(cwd, 0.0), time.time())
    try:
        for first in HOME.iterdir():
            if first.name.startswith(".") or first.name in SKIP_DIRS or not first.is_dir():
                continue
            if (first / ".git").exists():
                seen.setdefault(str(first), 0.0)
                continue
            try:
                for second in first.iterdir():
                    if not second.name.startswith(".") and (second / ".git").exists():
                        seen.setdefault(str(second), 0.0)
            except OSError:
                continue
    except OSError:
        pass
    rows = []
    for cwd, at in sorted(seen.items(), key=lambda kv: -kv[1]):
        spot = Path(cwd)
        if not spot.is_dir() or not _inside_home(spot):
            continue
        rows.append({"path": str(spot), "shown": _tilde(str(spot)), "usedAt": at,
                     "git": (spot / ".git").exists(), "about": _about(spot)})
        if len(rows) >= MAX_CANDIDATES:
            break
    return rows


def projects_root(folders: list[dict]) -> str:
    """Where new projects go: wherever most of the existing ones already live."""
    home = str(HOME)
    parents = Counter(str(Path(f["path"]).parent) for f in folders if f["path"] != home)
    for parent, _ in parents.most_common():
        if _inside_home(Path(parent)):
            return parent
    return home


# ------------------------------------------------------------ asking Claude

TASK = """You decide where a piece of work should happen on this computer.

The user wants to:
<request>
{request}
</request>

Folders they already have, most recently used first:
{folders}

New projects on this computer live in: {root}

Decide one of:
- "open": the request is about one of the folders above (improving it, fixing it,
  asking about it, adding to it). Use that folder's path exactly as listed.
  A general question that needs no project goes to the home folder: ~
- "create": the request is for something new that none of the folders is for.
  Give a new folder inside {root} with a short lowercase kebab-case name.

Answer with JSON only, no prose and no code fences:
{{"action": "open" or "create",
  "folder": "~/path",
  "reason": "one short sentence for the user on why this folder",
  "title": "a 2-5 word name for the session",
  "setup": "create only: the stack in at most six words, e.g. 'Static HTML with Leaflet', else empty"}}
"""


def _ask(prompt: str, run=None) -> tuple[bool, str]:
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"
    try:
        process = subprocess.Popen(
            [claude, "--print", "--model", ROUTE_MODEL, "--allowed-tools", "", "--output-format", "text",
             "--no-session-persistence"],
            cwd=str(HOME), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run claude: {error}"
    own_errand(process.pid, True)
    try:
        out, err = process.communicate(prompt, timeout=ROUTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return False, f"Claude did not answer within {int(ROUTE_TIMEOUT)}s"
    finally:
        own_errand(process.pid, False)
    if process.returncode != 0:
        said = (err or "").strip().splitlines()
        return False, said[-1] if said else "Claude could not decide"
    return True, out


ASK = _ask  # swapped in the tests


def _expand(path: str) -> Path:
    path = str(path or "").strip()
    if path == "~" or path.startswith("~/"):
        return HOME / path[2:]
    return Path(path)


def check_plan(raw: dict, folders: list[dict], root: str) -> tuple[dict | None, str]:
    """A model's answer made safe to act on, or None and why not."""
    action = raw.get("action")
    folder = _expand(raw.get("folder") or "")
    if action not in ("open", "create") or not str(folder):
        return None, "Claude's answer did not say where to go"
    if not folder.is_absolute():
        folder = Path(root) / folder
    if not _inside_home(folder.parent if action == "create" else folder):
        return None, "Claude picked a folder outside your home folder"
    plan = {"action": action, "reason": str(raw.get("reason") or "")[:240],
            "title": " ".join(str(raw.get("title") or "").split())[:60],
            "setup": str(raw.get("setup") or "")[:240] if action == "create" else ""}
    if action == "open":
        if not folder.is_dir():
            return None, f"Claude picked a folder that is not there: {_tilde(str(folder))}"
        plan["folder"] = str(folder.resolve())
    else:
        name = re.sub(r"[^a-z0-9._-]+", "-", folder.name.lower()).strip("-.") or "new-project"
        folder = folder.parent / name
        if folder.exists():
            # Something already has that name. If it is a folder, that is where
            # the work goes after all; if not, step to a free name.
            if folder.is_dir():
                plan.update(action="open", setup="", folder=str(folder.resolve()))
                plan["shown"] = _tilde(plan["folder"])
                return plan, ""
            n = 2
            while (folder.parent / f"{name}-{n}").exists():
                n += 1
            folder = folder.parent / f"{name}-{n}"
        if not folder.parent.is_dir():
            return None, f"There is no {_tilde(str(folder.parent))} to make it in"
        plan["folder"] = str(folder)
    plan["shown"] = _tilde(plan["folder"])
    return plan, ""


def decide(request: str, extra: list[str] | None = None) -> tuple[dict | None, str]:
    """Where `request` should run. (plan, "") or (None, why not)."""
    request = str(request or "").strip()
    if not request:
        return None, "Say what you want to do first"
    if len(request) > MAX_REQUEST:
        return None, f"Keep it under {MAX_REQUEST} characters"
    folders = known_folders(extra)
    root = projects_root(folders)
    listing = "\n".join(
        f"- {f['shown']}" + (" (git)" if f["git"] else "") + (f" — {f['about']}" if f["about"] else "")
        for f in folders) or "- (none yet)"
    ok, said = ASK(TASK.format(request=request, folders=listing, root=_tilde(root)))
    if not ok:
        return None, said
    found = re.search(r"\{.*\}", said, re.S)
    try:
        raw = json.loads(found.group(0)) if found else None
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        return None, "Claude answered, but not with a decision"
    plan, why = check_plan(raw, folders, root)
    if plan:
        plan["candidates"] = [{"path": f["path"], "shown": f["shown"]} for f in folders[:12]]
    return plan, why


def first_message(request: str, plan: dict) -> str:
    """What the new session is sent: your words, and for a new project, the
    instruction to set it up first."""
    request = str(request or "").strip()
    if plan.get("action") != "create":
        return request
    setup = f" ({plan['setup']})" if plan.get("setup") else ""
    return ("This folder was just created for this, and is empty. Set the project up first"
            f"{setup}: git init, a README, and whatever scaffolding it needs. Then do what I asked:\n\n"
            + request)


def make_folder(path: str) -> tuple[str | None, str]:
    """Make a new project's folder. Inside home, new, with a parent that exists."""
    spot = _expand(path)
    if not spot.is_absolute() or not _inside_home(spot.parent):
        return None, "New projects are made inside your home folder"
    if spot.exists():
        return (str(spot.resolve()), "") if spot.is_dir() else (None, f"{_tilde(str(spot))} is a file")
    if not spot.parent.is_dir():
        return None, f"There is no {_tilde(str(spot.parent))} to make it in"
    try:
        spot.mkdir()
    except OSError as error:
        return None, f"Could not make it: {error.strerror or error}"
    return str(spot.resolve()), ""

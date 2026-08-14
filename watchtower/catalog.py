"""What a session can be asked for: its skills and its slash commands.

Read from the folders Claude Code reads them from — the project's, yours, and
any enabled plugin's — so the panel offers the same names the session would
accept. Cached, because it is a directory walk and the answer changes rarely.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from watchtower.config import HOME


# A leading /name is expanded by the terminal, not by the session. A message
# injected over the messaging socket is queued as a peer turn with slash commands
# switched off — deliberately, since command markdown can carry inline shell, and
# an inbox that ran it would be a way to run anything in someone else's checkout.
#
# So the panel does not try to be the terminal. It reads the same folders Claude
# Code reads, offers what it finds by name, and sends a sentence asking for it.
# Asking is the one thing an injected turn can do, and it is enough: a skill is
# invoked by name anyway. Nothing here expands anything, substitutes arguments,
# or runs a line of a file it read.
CATALOG_FRESH = 20.0


CATALOG_LOCK = threading.Lock()


CATALOG_HELD: dict[str, tuple[float, dict]] = {}


# A walk of folders that are meant to be small. The caps are here so a stray
# checkout under ~/.claude/commands cannot turn one composer keystroke into a
# thousand-line answer.
MAX_ENTRIES = 400


MAX_DESCRIPTION = 240


MAX_SCAN = 600


PLUGIN_INSTALLS = HOME / ".claude" / "plugins" / "installed_plugins.json"


USER_SETTINGS = HOME / ".claude" / "settings.json"


# Commands that live in the terminal's own head — its screen, its model, its
# history — and that no message can reach, whatever it says. The panel names them
# rather than sending text that would quietly do nothing.
TERMINAL_ONLY = (
    "clear", "compact", "context", "model", "resume", "exit", "quit", "login", "logout",
    "config", "help", "doctor", "status", "cost", "upgrade", "release-notes", "plugin",
    "mcp", "agents", "ide", "terminal-setup", "vim", "memory", "permissions", "hooks",
    "add-dir", "export", "privacy-settings", "bashes", "statusline", "output-style",
    "todos", "install-github-app", "migrate-installer",
)


FRONT_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$")


def read_front_matter(path: Path) -> dict:
    """The `name:` and `description:` at the head of a skill or command file.

    A hand-rolled reader rather than a YAML one, and no dependency for it: these
    files are written by hand and read by half a dozen tools, so both fields are
    plain scalars, quoted or not — or a folded block, which a long description
    often is, and which is gathered from the indented lines under it. Anything
    more elaborate is left alone rather than guessed at.
    """
    found: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if handle.readline().strip() != "---":
                return found
            lines = []
            for _ in range(80):
                line = handle.readline()
                if not line or line.rstrip() == "---":
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        return {}

    for index, line in enumerate(lines):
        match = FRONT_FIELD.match(line)
        if not match:
            continue
        value = match.group(2)
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            # A block scalar: everything indented under it, as one line, which is
            # what a folded description means anyway.
            gathered = []
            for follower in lines[index + 1:]:
                if follower.strip() and not follower[:1].isspace():
                    break
                gathered.append(follower.strip())
            value = " ".join(part for part in gathered if part)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[match.group(1)] = value[:MAX_DESCRIPTION]
    return found


def scan_skills(root: Path, source: str, prefix: str = "") -> list[dict]:
    """Every SKILL.md one folder down, which is the only shape a skill has."""
    out: list[dict] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for folder in folders[:MAX_SCAN]:
        file = folder / "SKILL.md"
        if not file.is_file():
            continue
        front = read_front_matter(file)
        out.append({"name": prefix + (front.get("name") or folder.name),
                    "description": front.get("description", ""),
                    "source": source, "kind": "skill"})
    return out


def scan_commands(root: Path, source: str, prefix: str = "") -> list[dict]:
    """Command markdown, with a subfolder read as the namespace it stands for."""
    out: list[dict] = []
    try:
        files = sorted(p for p in root.rglob("*.md") if p.is_file())
    except OSError:
        return out
    for file in files[:MAX_SCAN]:
        try:
            parts = list(file.relative_to(root).with_suffix("").parts)
        except ValueError:
            continue
        front = read_front_matter(file)
        out.append({"name": prefix + ":".join(parts),
                    "description": front.get("description", ""),
                    "source": source, "kind": "command"})
    return out


def enabled_plugins(cwd: str | None) -> list[str]:
    """The plugin keys switched on, which is the terminal's own answer.

    `enabledPlugins` is keyed `plugin@marketplace` and a project can turn one on
    or off for itself, the nearer file winning — the order Claude Code reads them
    in. Reading the same switch is what keeps the panel from offering something
    the session would not answer to.
    """
    enabled: dict[str, bool] = {}
    files = [USER_SETTINGS]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json",
                  Path(cwd) / ".claude" / "settings.local.json"]
    for file in files:
        try:
            data = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        block = data.get("enabledPlugins")
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if isinstance(value, bool):
                enabled[key] = value
    return [key for key, on in enabled.items() if on]


def plugin_paths(keys: list[str]) -> list[tuple[str, Path]]:
    """Where each enabled plugin's files landed, taking its newest install."""
    try:
        data = json.loads(PLUGIN_INSTALLS.read_text())
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return []
    out: list[tuple[str, Path]] = []
    for key in keys:
        installs = plugins.get(key)
        if not isinstance(installs, list) or not installs:
            continue
        newest = max(installs, key=lambda item: str(
            item.get("lastUpdated") or item.get("installedAt") or ""))
        where = newest.get("installPath")
        if isinstance(where, str) and where:
            out.append((key.split("@", 1)[0], Path(where)))
    return out


def read_catalog(cwd: str | None) -> dict:
    """Everything this session could be asked for by name, from every source.

    Named the way it is addressed — bare for your own and the project's, prefixed
    `plugin:` for a plugin's — so what the picker shows is what the session
    answers to. Only names, descriptions and where they came from: no path on
    this machine leaves the server, and nothing is read but the head of each file.
    """
    key = cwd or ""
    now = time.monotonic()
    with CATALOG_LOCK:
        held = CATALOG_HELD.get(key)
        if held and now - held[0] < CATALOG_FRESH:
            return held[1]

    found: list[dict] = []
    found += scan_skills(HOME / ".claude" / "skills", "yours")
    found += scan_commands(HOME / ".claude" / "commands", "yours")
    for name, where in plugin_paths(enabled_plugins(cwd)):
        found += scan_skills(where / "skills", name, f"{name}:")
        found += scan_commands(where / "commands", name, f"{name}:")
    if cwd:
        found += scan_skills(Path(cwd) / ".claude" / "skills", "this project")
        found += scan_commands(Path(cwd) / ".claude" / "commands", "this project")

    # Scanned nearest last, so a project's own copy of a name overwrites the one
    # further away — which is the copy the session would use.
    by_name: dict[str, dict] = {}
    for entry in found:
        if entry["name"]:
            by_name[entry["name"]] = entry
    answer = {"ok": True,
              "entries": sorted(by_name.values(), key=lambda e: e["name"])[:MAX_ENTRIES],
              "terminalOnly": list(TERMINAL_ONLY)}
    with CATALOG_LOCK:
        CATALOG_HELD[key] = (now, answer)
        # One entry per folder the panel has looked at, and it only ever looks at
        # folders it was asked about, so this stays the size of the session list.
        if len(CATALOG_HELD) > 64:
            CATALOG_HELD.clear()
    return answer

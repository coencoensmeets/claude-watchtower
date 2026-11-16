"""GitHub and GitLab behind one set of questions.

There is no universal API for issues: GitHub and GitLab are different servers
with different endpoints, and each has its own CLI holding its own credentials.
So the panel speaks to both — `gh` for GitHub, `glab` for GitLab — and this file
is the one place that decides which a repository or a host is on. Everything
above it (the routes, the tab, the work items page) asks here and gets back the
same shapes whichever it was, each item marked with its `forge`.

Which forge a host is on is decided offline: github.com, or any host gh has an
entry for, is a GitHub; anything else is taken to be a GitLab, because a
self-hosted GitLab nobody has logged in to yet is far the likeliest thing an
unknown git server is in practice, and the tab then offers the right login.
"""

from __future__ import annotations

import re
import shutil
import threading
import time

from watchtower.config import HOME
from watchtower.control import run_in_terminal
from watchtower.github.actions import create_issue as gh_create_issue
from watchtower.github.read import (assigned_items as gh_assigned, hosts as gh_hosts,
                                    is_github, item_detail as gh_item,
                                    project_items as gh_project_items)
from watchtower.gitlab.actions import create_issue as gl_create_issue
from watchtower.gitlab.read import (assigned_items as gl_assigned, forget, hosts as gl_hosts,
                                    item_detail as gl_item, project_for,
                                    project_items as gl_project_items)


GITHUB = "github"

GITLAB = "gitlab"

NAMES = {GITHUB: "GitHub", GITLAB: "GitLab"}

CLIS = {GITHUB: "gh", GITLAB: "glab"}


def forge_of(host: str) -> str:
    return GITHUB if is_github(host) else GITLAB


# Asked for every session on every poll, and the answer only moves when a remote
# is changed or a CLI is logged in somewhere new — so it is held for a minute,
# like the repository root it is read from.
FORGE_CACHE: dict[str, tuple[float, str | None]] = {}

FORGE_LOCK = threading.Lock()


def forge_of_repo(root: str | None) -> str | None:
    """Which forge a checkout's remote is on, or None with no remote. No network."""
    if not root:
        return None
    now = time.monotonic()
    with FORGE_LOCK:
        hit = FORGE_CACHE.get(root)
        if hit and now - hit[0] < 60:
            return hit[1]
    project = project_for(root)
    value = forge_of(project["host"]) if project else None
    with FORGE_LOCK:
        if len(FORGE_CACHE) > 256:
            FORGE_CACHE.clear()
        FORGE_CACHE[root] = (now, value)
    return value


def tagged(items: list[dict], forge: str) -> list[dict]:
    return [{**item, "forge": item.get("forge") or forge} for item in items]


# ----------------------------------------------------------------- reading

def hosts(force: bool = False) -> dict:
    """Both CLIs' hosts, for the settings page: who you are on each, and what is wrong."""
    lab = gl_hosts(force)
    hub = gh_hosts(force)
    return {
        "ok": True,
        GITLAB: {**lab, "cli": "glab", "installed": bool(shutil.which("glab")),
                 "hosts": tagged(lab.get("hosts") or [], GITLAB)},
        GITHUB: {**hub, "cli": "gh", "installed": bool(shutil.which("gh")),
                 "hosts": tagged(hub.get("hosts") or [], GITHUB)},
    }


def assigned_items(force: bool = False) -> dict:
    """Everything assigned to you, on every GitHub and GitLab this machine can read.

    A CLI that is not installed is not trouble — somebody who only uses GitHub
    has no reason to install glab, and should not be told about it on every
    visit. It is only an answer when neither is there.
    """
    answers = [(GITLAB, gl_assigned(force)), (GITHUB, gh_assigned(force))]
    usable = [(f, a) for f, a in answers if a.get("reason") != "no-cli"]
    if not usable:
        return {"ok": False, "reason": "no-cli", "items": [], "trouble": [],
                "message": "Neither the GitHub CLI (gh) nor the GitLab CLI (glab) is on "
                           "this machine — install one and log in"}
    items: list[dict] = []
    trouble: list[dict] = []
    for forge, answer in usable:
        if not answer.get("ok"):
            trouble.append({"host": NAMES[forge], "message": answer.get("message")
                            or f"{NAMES[forge]} did not answer"})
            continue
        items += tagged(answer.get("items") or [], forge)
        trouble += answer.get("trouble") or []
    items.sort(key=lambda i: str(i.get("updatedAt") or ""), reverse=True)
    return {"ok": True, "items": items, "trouble": trouble}


def project_items(root: str, branch: str = "", force: bool = False) -> dict:
    """The tab's reading for one session, from whichever forge its remote is on."""
    project = project_for(root)
    if not project:
        return {"ok": False, "reason": "no-remote", "isProject": False,
                "message": "This repository has no remote, so there is no GitHub or "
                           "GitLab behind it"}
    if forge_of(project["host"]) == GITHUB:
        return {"forge": GITHUB, **gh_project_items(project, branch, force)}
    answer = gl_project_items(root, branch, force)
    if answer.get("reason") == "not-gitlab":
        answer = {**answer, "message": f"This repository is on {project['host']}, which "
                                       "neither gh nor glab is logged in to"}
    return {"forge": GITLAB, **answer,
            "items": tagged(answer.get("items") or [], GITLAB)}


def project_of(root: str) -> dict:
    return project_for(root)


def item_detail(host: str, project: str, iid: int, force: bool = False) -> dict:
    forge = forge_of(host)
    answer = (gh_item if forge == GITHUB else gl_item)(host, project, iid, force)
    if answer.get("ok"):
        answer = {**answer, "item": {**answer["item"], "forge": forge}}
    return answer


# ------------------------------------------------------------------ acting

def create_issue(project: dict, payload: dict) -> tuple[bool, str, int, dict]:
    if forge_of(project["host"]) == GITHUB:
        return gh_create_issue(project, payload)
    return gl_create_issue(project, payload)


# A host name and nothing else: letters, digits, dots, dashes, an optional port.
# It ends up in a command line, so this is the whole of what is let through —
# no scheme, no path, no leading dash that would read as a flag.
HOST_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                       r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?::\d{1,5})?$")


def clean_host(raw: object) -> str:
    """A host as somebody might type it, cut down to the host name, or ""."""
    text = str(raw or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text).split("/")[0]
    return text if HOST_NAME.match(text) else ""


def login(forge: str, raw_host: object) -> tuple[bool, str, int]:
    """Open a terminal running the CLI's own login for one host.

    The panel still holds no token: the login is gh's or glab's, in a window
    you type into, and the token lands in that CLI's own store. What the panel
    adds is not having to know the command.
    """
    if forge not in CLIS:
        return False, "Log in to what?", 400
    # Only an empty host means github.com. Anything typed that does not clean
    # up into a host name is refused, never quietly turned into the default.
    typed = str(raw_host or "").strip()
    host = clean_host(typed) if typed else ("github.com" if forge == GITHUB else "")
    if not host:
        return False, "That is not a host name", 400
    cli = shutil.which(CLIS[forge])
    if not cli:
        return False, f"{CLIS[forge]} is not installed on this machine", 409
    ok, message = run_in_terminal([cli, "auth", "login", "--hostname", host], str(HOME),
                                  f"Logging in to {host} in a terminal — "
                                  "this page notices when it is done")
    forget()
    return ok, message, 200 if ok else 409

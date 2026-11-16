"""Reading GitHub: which hosts you have, what is open in a repository, what is yours.

The same answers watchtower.gitlab.read gives, in the same shapes, so the tab and
the work items page draw a GitHub issue with the code that draws a GitLab one.
Every item carries `forge` so the few places that do differ — `#` against `!`,
*pull request* against *merge request* — can tell which they are looking at.

Readings are held in the GitLab reader's cache rather than a second one, so the
one `forget()` after a write empties both.
"""

from __future__ import annotations

from urllib.parse import quote

from watchtower.github.cli import (FAILED, NOT_LOGGED_IN, NO_CLI, OFFLINE, UNAUTHORIZED,
                                   api, configured_hosts, have_gh)
from watchtower.gitlab.read import FRESH, HOSTS_FRESH, MAX_BODY, MAX_NOTE, MAX_NOTES, held, who


FORGE = "github"


WHY = {
    NO_CLI: "The GitHub CLI is not on this machine — install gh to read GitHub",
    NOT_LOGGED_IN: "gh is not logged in to this GitHub yet",
    UNAUTHORIZED: "This GitHub turned the token down — gh needs logging in again",
    OFFLINE: "Could not reach this GitHub",
    FAILED: "GitHub did not answer that",
}


def said(reason: str) -> str:
    return WHY.get(reason, WHY[FAILED])


PROBE_TROUBLE = {
    UNAUTHORIZED: ("turned-down", "the token was turned down"),
    NOT_LOGGED_IN: ("no-token", "not logged in"),
    OFFLINE: ("offline", "it could not be reached"),
    FAILED: ("", "gh could not talk to it"),
}


TROUBLE = {
    "no-token": "gh is not logged in to {host}",
    "turned-down": "{host} turned gh's token down — it needs logging in again",
    "offline": "{host} could not be reached",
}


def trouble_line(entry: dict) -> str:
    template = TROUBLE.get(entry.get("code") or "", "gh cannot talk to {host}")
    return template.format(host=entry.get("host") or "that GitHub")


# ------------------------------------------------------------------- hosts

def probe(host: str) -> dict:
    """Ask a host who we are. The answer is the whole verdict, as it is for GitLab."""
    entry = {"host": host, "user": "", "ok": False, "why": "not logged in",
             "code": "no-token", "forge": FORGE}
    ok, me, reason = api("user", host=host)
    if ok and isinstance(me, dict) and me.get("login"):
        entry.update(ok=True, why="", code="", user=str(me["login"]))
        return entry
    code, why = PROBE_TROUBLE.get(reason, PROBE_TROUBLE[FAILED])
    entry.update(why=why, code=code)
    return entry


def hosts(force: bool = False) -> dict:
    """Which GitHubs this machine can talk to: github.com always, and any gh knows.

    github.com is asked about even when gh has no entry for it, because a token
    can come from GH_TOKEN as well as from gh's own file, and because "you are
    not logged in to github.com" with a button beside it is the answer worth
    showing to somebody who has never run `gh auth login`.
    """
    def read() -> dict:
        if not have_gh():
            return {"ok": False, "reason": NO_CLI, "message": said(NO_CLI), "hosts": []}
        names = ["github.com"] + [h for h in configured_hosts() if h != "github.com"]
        found = [probe(name) for name in names]
        return {"ok": True, "hosts": found, "usable": [h["host"] for h in found if h["ok"]]}
    return held("gh:hosts", HOSTS_FRESH, read, force)


# ----------------------------------------------------------------- an item

def text_on(colour: str) -> str:
    """Black or white, whichever reads on a label's colour.

    GitLab says which; GitHub does not, so it is worked out the way GitHub's own
    page does it — from the colour's luminance.
    """
    try:
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return ""
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def as_label(raw: object) -> dict:
    if isinstance(raw, str):
        return {"name": raw[:120], "colour": "", "onColour": ""}
    if not isinstance(raw, dict):
        return {"name": "", "colour": "", "onColour": ""}
    colour = str(raw.get("color") or "")[:6]
    colour = f"#{colour}" if len(colour) == 6 else ""
    return {"name": str(raw.get("name") or "")[:120], "colour": colour,
            "onColour": text_on(colour) if colour else ""}


def person(raw: object) -> dict:
    """A GitHub user as the shape `who` makes — GitHub sends a login and no name."""
    if not isinstance(raw, dict):
        return {}
    return who({"name": raw.get("name") or raw.get("login"), "username": raw.get("login")})


def repo_of(raw: dict) -> str:
    """`owner/name` for an issue, from whichever of its fields says so."""
    repo = raw.get("repository")
    if isinstance(repo, dict) and repo.get("full_name"):
        return str(repo["full_name"])
    url = str(raw.get("repository_url") or "")
    return "/".join(url.rstrip("/").split("/")[-2:]) if "/repos/" in url else ""


def as_item(raw: dict, host: str, me: str = "", project: str = "") -> dict:
    project = project or repo_of(raw)
    assignees = [person(a) for a in (raw.get("assignees") or [])]
    assignees = [a for a in assignees if a][:4]
    return {
        "forge": FORGE,
        "host": host,
        "project": project,
        "id": (raw.get("repository") or {}).get("id") if isinstance(raw.get("repository"), dict) else None,
        "iid": raw.get("number"),
        "title": str(raw.get("title") or "")[:300],
        "state": "opened" if raw.get("state") == "open" else raw.get("state"),
        "labels": [as_label(l) for l in (raw.get("labels") or [])][:12],
        "milestone": str((raw.get("milestone") or {}).get("title") or ""),
        "updatedAt": raw.get("updated_at"),
        "createdAt": raw.get("created_at"),
        "dueOn": (raw.get("milestone") or {}).get("due_on"),
        "webUrl": raw.get("html_url"),
        "comments": raw.get("comments") or 0,
        "reference": f"{project}#{raw.get('number')}" if project else f"#{raw.get('number')}",
        "author": person(raw.get("user")),
        "assignees": assignees,
        "mine": bool(me) and any(a.get("username") == me for a in assignees),
    }


def issues_only(rows: object) -> list[dict]:
    """GitHub's issue endpoints return pull requests too; a work item is not one."""
    return [r for r in (rows or []) if isinstance(r, dict) and "pull_request" not in r]


# ----------------------------------------------------------- what is assigned

def assigned_items(force: bool = False) -> dict:
    """Every open issue assigned to you, on every GitHub gh can reach."""
    def read() -> dict:
        known = hosts(force)
        if not known.get("ok"):
            return {"ok": False, "reason": known.get("reason"),
                    "message": known.get("message"), "items": [], "trouble": []}
        items: list[dict] = []
        trouble: list[dict] = []
        for host in known.get("usable") or []:
            ok, data, reason = api("issues?filter=assigned&state=open&sort=updated&per_page=50",
                                   host=host, paginate=True)
            if not ok or not isinstance(data, list):
                trouble.append({"host": host, "message": f"{said(reason)} ({host})"})
                continue
            items += [{**as_item(raw, host), "mine": True} for raw in issues_only(data)]
        items.sort(key=lambda i: str(i.get("updatedAt") or ""), reverse=True)
        return {"ok": True, "items": items, "trouble": trouble}
    return held("gh:assigned", FRESH, read, force)


# A workflow run says where it is and how it ended in two fields; the tab draws
# one word, in GitLab's vocabulary, because that is what its colours are keyed on.
RUN_ENDED = {
    "success": "success", "failure": "failed", "timed_out": "failed",
    "startup_failure": "failed", "cancelled": "canceled", "skipped": "skipped",
    "neutral": "success", "action_required": "manual", "stale": "canceled",
}


def run_status(run: dict) -> str:
    if run.get("status") == "completed":
        return RUN_ENDED.get(str(run.get("conclusion") or ""), "failed")
    return "running" if run.get("status") == "in_progress" else "pending"


MAX_ITEMS = 100


def project_items(project: dict, branch: str = "", force: bool = False) -> dict:
    """What the tab shows for a repository on GitHub. `project` is project_for's answer."""
    known = hosts(force)
    if not known.get("ok"):
        return {"ok": False, "isProject": True, "project": project, "forge": FORGE,
                "reason": known.get("reason"), "message": known.get("message")}
    entry = next((h for h in known.get("hosts", []) if h["host"] == project["host"]), None)
    if entry is None or not entry["ok"]:
        entry = entry or {"host": project["host"], "code": "no-token"}
        return {"ok": False, "reason": NOT_LOGGED_IN, "isProject": True, "project": project,
                "forge": FORGE, "message": trouble_line(entry)}
    me = entry.get("user") or ""
    host = project["host"]

    def read() -> dict:
        base = f"repos/{project['path']}"
        out: dict = {"ok": True, "isProject": True, "project": project, "me": me,
                     "forge": FORGE, "items": [], "mrs": [], "pipeline": None}
        ok, every, reason = api(f"{base}/issues?state=open&sort=updated&per_page={MAX_ITEMS}",
                                host=host)
        if not ok:
            return {"ok": False, "isProject": True, "project": project, "forge": FORGE,
                    "reason": reason, "message": said(reason)}
        out["items"] = [as_item(raw, host, me, project["path"]) for raw in issues_only(every)]
        if branch:
            owner = project["path"].split("/")[0]
            head = quote(f"{owner}:{branch}", safe="")
            ok, pulls, _ = api(f"{base}/pulls?state=open&head={head}", host=host)
            out["mrs"] = [{
                "iid": raw.get("number"), "title": str(raw.get("title") or "")[:300],
                "webUrl": raw.get("html_url"), "draft": bool(raw.get("draft")),
                "target": (raw.get("base") or {}).get("ref"),
            } for raw in (pulls or []) if isinstance(raw, dict)]
            ok, runs, _ = api(f"{base}/actions/runs?branch={quote(branch, safe='')}&per_page=1",
                              host=host)
            newest = next((r for r in ((runs or {}).get("workflow_runs") or [])
                           if isinstance(r, dict)), None) if isinstance(runs, dict) else None
            if newest:
                out["pipeline"] = {"status": run_status(newest), "webUrl": newest.get("html_url"),
                                   "at": newest.get("updated_at")}
        return out
    return held(f"gh:project:{host}/{project['path']}:{branch}", FRESH, read, force)


def item_detail(host: str, project: str, iid: int, force: bool = False) -> dict:
    """One issue in full: its body, and the last few comments on it."""
    def read() -> dict:
        base = f"repos/{project}/issues/{int(iid)}"
        ok, raw, reason = api(base, host=host)
        if not ok or not isinstance(raw, dict):
            return {"ok": False, "reason": reason, "message": said(reason)}
        item = as_item(raw, host, project=project)
        item["description"] = str(raw.get("body") or "")[:MAX_BODY]
        ok, notes, _ = api(f"{base}/comments?per_page=100", host=host)
        item["notes"] = [{
            "author": str((n.get("user") or {}).get("login") or ""),
            "at": n.get("created_at"),
            "body": str(n.get("body") or "")[:MAX_NOTE],
        } for n in (notes or []) if isinstance(n, dict)][-MAX_NOTES:]
        return {"ok": True, "item": item}
    return held(f"gh:item:{host}/{project}#{iid}", FRESH, read, force)


def is_github(host: str) -> bool:
    """Whether a remote's host is a GitHub, by the only evidence there is offline."""
    host = (host or "").lower()
    return host == "github.com" or host in configured_hosts()

"""Reading GitLab: which hosts you have, which project a checkout is, what is yours.

Nothing here writes anything anywhere. Every call costs a round trip to somebody
else's server, which is the difference between this and watchtower.git.read —
the git reader runs against a directory and can be called on a poll, and these
cannot. So each answer is held for a while and the panel asks again only when a
tab is opened, a session is switched to, or the refresh button is pressed.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from watchtower.gitlab.cli import (FAILED, NOT_LOGGED_IN, NO_CLI, OFFLINE, UNAUTHORIZED,
                                   api, have_glab, run_glab)


# A sentence for each named failure, because the tab has to say something, and
# "could not reach GitLab" in front of a list you know has things in it is worse
# than no list at all. The fix goes in the sentence: every one of these is
# something you can do, and only one of them is about the panel.
WHY = {
    NO_CLI: "The GitLab CLI is not on this machine — install glab to use this tab",
    NOT_LOGGED_IN: "glab is not logged in to this GitLab yet",
    UNAUTHORIZED: "This GitLab turned the token down — glab needs logging in again",
    OFFLINE: "Could not reach this GitLab",
    FAILED: "GitLab did not answer that",
}


def said(reason: str) -> str:
    return WHY.get(reason, WHY[FAILED])


# Long by the standards of the rest of the panel, and deliberately. A work item
# list is not live data: it changes when somebody assigns you something, which is
# a thing that happens a few times a day, and every read of it is a request to a
# server that is not ours. Opening the tab, switching session and the refresh
# button all bypass this — see force — so the only thing it governs is how long a
# page left open shows a stale list.
FRESH = 120.0


HOSTS_FRESH = 60.0


HELD_LOCK = threading.Lock()


HELD: dict[str, tuple[float, object]] = {}


def held(key: str, fresh: float, make, force: bool = False):
    """One reading, remembered for `fresh` seconds. `make` is called when it is not."""
    now = time.monotonic()
    if not force:
        with HELD_LOCK:
            found = HELD.get(key)
            if found and now - found[0] < fresh:
                return found[1]
    answer = make()
    with HELD_LOCK:
        HELD[key] = (now, answer)
        # One entry per host and per repository the panel has been asked about,
        # which is the size of the session list plus a handful.
        if len(HELD) > 64:
            HELD.clear()
    return answer


def forget() -> None:
    """Drop every held reading — after a write, which has just made them wrong."""
    with HELD_LOCK:
        HELD.clear()


# ------------------------------------------------------------------- hosts

# `glab auth status` writes prose, to stderr, and exits 0 whatever it found, so
# all three have to be taken as read. A host is a line with no indent under it;
# everything indented is a remark about the host above.
LOGGED_LINE = re.compile(r"Logged in to (\S+) as (\S+)")


def parse_auth(text: str) -> list[dict]:
    """The hosts glab is configured for, and what its own report claims of each.

    A claim, not a verdict — see `hosts` below, which checks. `glab auth status`
    is wrong about working tokens often enough that believing it locks you out of
    a GitLab you can read perfectly well: it prints `! Invalid token provided`
    whenever the extra call it makes to describe the token fails, which a token
    scoped for the API but not for reading its own metadata does. The same host
    then answers `/user` and lists your issues without complaint.

    So only one line here is taken as decisive: `No token provided` means there
    is nothing to try, and skipping the attempt saves a request that cannot work.
    Everything else is a starting point that gets tested.
    """
    hosts: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if raw[:1] not in (" ", "\t"):
            current = {"host": line, "user": "", "ok": False,
                       "why": "not logged in", "code": "not-logged-in"}
            hosts.append(current)
            continue
        if current is None:
            continue
        found = LOGGED_LINE.search(line)
        if found:
            current["host"] = found.group(1)
            current["user"] = found.group(2)
            current["ok"] = True
            current["why"] = ""
            current["code"] = ""
        # Both of these overrule the line above; glab prints both. Only the
        # first is believed — the second is checked before it is reported.
        if "No token provided" in line:
            current["ok"] = False
            current["why"] = "no token"
            current["code"] = "no-token"
        elif "Invalid token provided" in line:
            current["ok"] = False
            current["why"] = "the token was turned down"
            current["code"] = "turned-down"
    return hosts


# Why a host cannot be read, as a whole sentence. Kept apart from `why` above
# because the two are not the same string and one cannot be made from the other:
# `why` is the phrase that follows a host's own name in a list — "git.example.com
# — the token was turned down" — and dropping that phrase into a sentence
# template produced "glab has the token was turned down for git.example.com".
TROUBLE = {
    "no-token": "glab has no token for {host}",
    "turned-down": "{host} turned glab's token down — it needs logging in again",
    "not-logged-in": "glab is not logged in to {host}",
    "offline": "{host} could not be reached",
}


def trouble_line(entry: dict) -> str:
    """One sentence saying why this host cannot be read, and what to do."""
    template = TROUBLE.get(entry.get("code") or "", "glab cannot talk to {host}")
    return template.format(host=entry.get("host") or "that GitLab")


# What a failed probe means, in the two words each of these needs: the phrase
# that follows a host's name in a list, and the key trouble_line builds its
# sentence from.
PROBE_TROUBLE = {
    UNAUTHORIZED: ("turned-down", "the token was turned down"),
    NOT_LOGGED_IN: ("not-logged-in", "not logged in"),
    OFFLINE: ("offline", "it could not be reached"),
    FAILED: ("", "glab could not talk to it"),
}


def probe(entry: dict) -> dict:
    """Ask a host who we are, and let the answer decide whether it is usable.

    One cheap call — `/user`, the smallest authenticated thing there is — and it
    is the ground truth this whole file rests on, because `glab auth status` is
    not. A host it calls invalid answers this perfectly well when the token is
    merely scoped narrowly, and a host it calls fine can have been revoked five
    minutes ago.

    Held for as long as the host list is, so it costs one request per GitLab per
    minute at worst, and only when something asks.
    """
    if entry.get("code") == "no-token":
        return entry           # nothing to try, and no request worth spending
    ok, me, reason = api("user", host=entry["host"])
    if ok and isinstance(me, dict) and me.get("username"):
        entry.update(ok=True, why="", code="", user=str(me["username"]))
        return entry
    code, why = PROBE_TROUBLE.get(reason, PROBE_TROUBLE[FAILED])
    entry.update(ok=False, why=why, code=code)
    return entry


def hosts(force: bool = False) -> dict:
    """Which GitLabs this machine can talk to, ready to be shown in Settings."""
    def read() -> dict:
        if not have_glab():
            return {"ok": False, "reason": NO_CLI, "message": said(NO_CLI), "hosts": []}
        # Exits 0 with everything to say on stderr, so the output is taken from
        # both and the status is not consulted.
        ok, out, reason = run_glab(["auth", "status"], with_stderr=True)
        found = parse_auth(out)
        if not found and not ok:
            return {"ok": False, "reason": reason, "message": said(reason), "hosts": []}
        for entry in found:
            probe(entry)
        return {"ok": True, "hosts": found,
                "usable": [h["host"] for h in found if h["ok"]]}
    return held("hosts", HOSTS_FRESH, read, force)


# --------------------------------------------------------------- the project

# Where a remote points, in the two spellings git uses for the same thing:
# ssh://git@host/group/project.git and the scp-like git@host:group/project.git.
SCP_REMOTE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")


def split_remote(url: str) -> tuple[str, str]:
    """A remote URL as (host, project path), or ("", "") if it is not one.

    GitLab projects nest — `group/subgroup/project` is an ordinary path there —
    so nothing here assumes two segments. Only the `.git` suffix and any leading
    slash come off; what is left is the project's full path, which is what every
    API call below needs.
    """
    url = (url or "").strip()
    if not url:
        return "", ""
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        found = SCP_REMOTE.match(url)
        if not found:
            return "", ""
        host, path = found.group("host"), found.group("path")
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return (host.lower(), path) if host and path else ("", "")


# Read out of .git/config rather than asked of `git remote`: this is called for
# every session in the list, and a subprocess each is a subprocess too many for
# a string sitting in a file. The same reasoning as git_branch next door.
REMOTE_HEAD = re.compile(r'^\s*\[remote\s+"([^"]+)"\]')

URL_LINE = re.compile(r"^\s*url\s*=\s*(.+?)\s*$")


def remotes_of(root: str) -> dict[str, str]:
    """Every remote in a checkout's config, by name."""
    config = Path(root) / ".git" / "config"
    if not config.is_file():
        # A worktree or a submodule: .git is a file pointing at the real one.
        link = Path(root) / ".git"
        try:
            text = link.read_text() if link.is_file() else ""
        except OSError:
            return {}
        if not text.startswith("gitdir:"):
            return {}
        # A linked worktree keeps its config in the main checkout's directory,
        # one level up from the per-worktree folder.
        where = Path(text[7:].strip())
        config = next((c for c in (where / "config", where.parent.parent / "config")
                       if c.is_file()), config)
    try:
        lines = config.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    found: dict[str, str] = {}
    name = ""
    for line in lines:
        head = REMOTE_HEAD.match(line)
        if head:
            name = head.group(1)
            continue
        if line.lstrip().startswith("["):
            name = ""
            continue
        url = URL_LINE.match(line) if name else None
        if url and name not in found:
            found[name] = url.group(1)
    return found


def project_for(root: str) -> dict:
    """The GitLab project a checkout belongs to, from its remotes. No network.

    `origin` first and then whatever else there is, which is what a fork with an
    `upstream` remote wants: the one you push to is the one your work items are
    on. A host glab has never heard of is still returned — the tab says which
    GitLab it would need rather than pretending the repository is not on one.
    """
    remotes = remotes_of(root)
    order = (["origin"] if "origin" in remotes else []) + [
        n for n in remotes if n != "origin"]
    for name in order:
        host, path = split_remote(remotes[name])
        if host and path:
            return {"host": host, "path": path, "remote": name,
                    "url": f"https://{host}/{path}"}
    return {}


def encoded(path: str) -> str:
    """A project path as GitLab's API wants it in a URL: one encoded segment."""
    return quote(path, safe="")


# ----------------------------------------------------------- what is assigned

# What the panel keeps of an issue. GitLab sends a great deal more; carrying it
# around would mean deciding later which of two spellings of the same field the
# view meant, and none of it is drawn.
# A label as GitLab holds it: a name, and the colour the project gave it. Asked
# for with `with_labels_details`, because a label drawn in some colour of ours is
# a different label from the one everybody on the project recognises — and
# because a scoped label (`priority::high`) is a key and a value, which is a
# thing worth drawing as two halves rather than as one long word.
def as_label(raw: object) -> dict:
    if isinstance(raw, str):
        return {"name": raw[:120], "colour": "", "onColour": ""}
    if not isinstance(raw, dict):
        return {"name": "", "colour": "", "onColour": ""}
    return {
        "name": str(raw.get("name") or "")[:120],
        "colour": str(raw.get("color") or "")[:9],
        # GitLab works out which of black or white reads on the label's colour,
        # and it is the only one that knows: the colour is the project's.
        "onColour": str(raw.get("text_color") or "")[:9],
    }


def who(raw: object) -> dict:
    """A person, as little of them as the panel draws.

    No avatar URL. Every one of them is an image on somebody else's server, and
    a panel that promises nothing leaves this machine should not quietly have
    your browser fetch forty pictures from a GitLab to draw a list. Initials are
    made from the name here instead.
    """
    if not isinstance(raw, dict):
        return {}
    name = str(raw.get("name") or raw.get("username") or "")[:80]
    if not name:
        return {}
    parts = [p for p in re.split(r"[\s._-]+", name) if p]
    initials = (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")).upper()
    return {"name": name, "username": str(raw.get("username") or "")[:80],
            "initials": initials or name[:1].upper()}


def as_item(raw: dict, host: str, me: str = "") -> dict:
    project = ""
    reference = str(raw.get("references", {}).get("full") or "")
    if reference:
        # `group/project#42` — the project path is everything before the hash,
        # and it is the only place the list endpoint says which project this is.
        project = reference.rsplit("#", 1)[0]
    assignees = [who(a) for a in (raw.get("assignees") or [])]
    assignees = [a for a in assignees if a][:4]
    return {
        "host": host,
        "project": project,
        "id": raw.get("project_id"),
        "iid": raw.get("iid"),
        "title": str(raw.get("title") or "")[:300],
        "state": raw.get("state"),
        "labels": [as_label(l) for l in (raw.get("labels") or [])][:12],
        "milestone": str((raw.get("milestone") or {}).get("title") or ""),
        "updatedAt": raw.get("updated_at"),
        "createdAt": raw.get("created_at"),
        "dueOn": raw.get("due_date"),
        "webUrl": raw.get("web_url"),
        "comments": raw.get("user_notes_count") or 0,
        "reference": reference,
        "author": who(raw.get("author")),
        "assignees": assignees,
        # Whose it is, decided here rather than in the browser: one reading of
        # the project's open items serves both filters, so the tab can switch
        # between *yours* and *all of them* without spending a request.
        "mine": bool(me) and any(a.get("username") == me for a in assignees),
    }


def assigned_items(force: bool = False) -> dict:
    """Every open work item assigned to you, on every GitLab glab can reach.

    Per host rather than in one call, because there is no such thing as one call:
    each host is a different server with different credentials. A host that is
    down or logged out does not take the others down with it — it comes back in
    `trouble`, which the page shows beside the list rather than instead of it.
    """
    def read() -> dict:
        known = hosts(force)
        if not known.get("ok"):
            return {"ok": False, "reason": known.get("reason"),
                    "message": known.get("message"), "items": [], "trouble": []}
        usable = known.get("usable") or []
        if not usable:
            return {"ok": True, "items": [], "trouble": [
                {"host": h["host"], "message": trouble_line(h)}
                for h in known.get("hosts", [])]}
        items: list[dict] = []
        trouble: list[dict] = []
        for host in usable:
            ok, data, reason = api(
                "issues?scope=assigned_to_me&state=opened&order_by=updated_at"
                "&per_page=50&with_labels_details=true",
                host=host, paginate=True)
            if not ok or not isinstance(data, list):
                trouble.append({"host": host, "message": f"{said(reason)} ({host})"})
                continue
            # Everything here is assigned to you by definition — that is what
            # was asked for — so the flag the tab filters on is simply true.
            items += [{**as_item(raw, host), "mine": True}
                      for raw in data if isinstance(raw, dict)]
        items.sort(key=lambda i: str(i.get("updatedAt") or ""), reverse=True)
        return {"ok": True, "items": items, "trouble": trouble}
    return held("assigned", FRESH, read, force)


def project_items(root: str, branch: str = "", force: bool = False) -> dict:
    """What the GitLab tab shows for one session: its project, and what is open in it.

    Run with `cwd` set to the checkout so glab resolves the host the same way it
    would if you ran it there yourself — which is also the only way a repository
    on a GitLab glab knows about, but whose remote does not match the host it is
    configured under, works at all.
    """
    project = project_for(root)
    if not project:
        return {"ok": False, "reason": "no-remote", "isProject": False,
                "message": "This repository has no remote, so there is no GitLab behind it"}

    # A checkout is not on GitLab just because it is on a git server. Asked
    # about a GitHub remote, glab would answer about gitlab.com — a project that
    # either does not exist or, worse, is somebody else's with the same path. So
    # the host has to be one glab is actually configured for before anything is
    # sent, and when it is not, the tab says which GitLab it would need.
    known = hosts(force)
    configured = {h["host"] for h in known.get("hosts", [])} if known.get("ok") else set()
    if configured and project["host"] not in configured:
        return {"ok": False, "reason": "not-gitlab", "isProject": False, "project": project,
                "message": f"This repository is on {project['host']}, "
                           "which glab is not set up for"}
    stale = next((h for h in known.get("hosts", [])
                  if h["host"] == project["host"] and not h["ok"]), None)
    if stale:
        return {"ok": False, "reason": NOT_LOGGED_IN, "isProject": True, "project": project,
                "message": trouble_line(stale)}

    # Who you are on this host, so an item can say whether it is yours. Already
    # in hand: the host list probed for it.
    me = next((h.get("user") or "" for h in known.get("hosts", [])
               if h["host"] == project["host"]), "")

    def read() -> dict:
        base = f"projects/{encoded(project['path'])}"
        out: dict = {"ok": True, "isProject": True, "project": project, "me": me,
                     "items": [], "mrs": [], "pipeline": None}
        # Every open item in the project, once, rather than one request per
        # filter. The tab's *Yours* and *All open* are then the same reading
        # looked at two ways, which is what makes switching between them
        # instant and the counts on both chips honest.
        ok, every, reason = api(
            f"{base}/issues?state=opened&per_page={MAX_ITEMS}"
            "&order_by=updated_at&with_labels_details=true",
            host=project["host"], cwd=root)
        if not ok:
            return {"ok": False, "isProject": True, "project": project,
                    "reason": reason, "message": said(reason)}
        out["items"] = [as_item(raw, project["host"], me)
                        for raw in (every or []) if isinstance(raw, dict)]

        # Merge requests for the branch this session is actually on — the one
        # question about a repository that is about where you are standing in it.
        if branch:
            ok, mrs, _ = api(
                f"{base}/merge_requests?state=opened&source_branch={quote(branch, safe='')}",
                host=project["host"], cwd=root)
            out["mrs"] = [{
                "iid": raw.get("iid"), "title": str(raw.get("title") or "")[:300],
                "webUrl": raw.get("web_url"), "draft": bool(raw.get("draft")),
                "target": raw.get("target_branch"),
            } for raw in (mrs or []) if isinstance(raw, dict)]
            ok, pipes, _ = api(
                f"{base}/pipelines?ref={quote(branch, safe='')}&per_page=1",
                host=project["host"], cwd=root)
            newest = next((p for p in (pipes or []) if isinstance(p, dict)), None)
            if newest:
                out["pipeline"] = {"status": newest.get("status"),
                                   "webUrl": newest.get("web_url"),
                                   "at": newest.get("updated_at")}
        return out
    return held(f"project:{project['host']}/{project['path']}:{branch}", FRESH, read, force)


# How much of a work item goes into the prompt a session starts on. A long
# description is worth carrying — it is the brief — but an item with forty
# comments on it is a conversation, and the whole of it would push everything
# else out of the first thing the session reads.
MAX_ITEMS = 100


MAX_BODY = 8000

MAX_NOTES = 8

MAX_NOTE = 1200


def item_detail(host: str, project: str, iid: int, force: bool = False) -> dict:
    """One work item in full: its description, and the last few comments on it."""
    def read() -> dict:
        base = f"projects/{encoded(project)}/issues/{int(iid)}"
        ok, raw, reason = api(f"{base}?with_labels_details=true", host=host)
        if not ok or not isinstance(raw, dict):
            return {"ok": False, "reason": reason, "message": said(reason)}
        item = as_item(raw, host)
        item["description"] = str(raw.get("description") or "")[:MAX_BODY]
        # Oldest first, so the tail read is the end of the conversation rather
        # than the beginning of it.
        ok, notes, _ = api(f"{base}/notes?sort=asc&order_by=created_at&per_page=50", host=host)
        item["notes"] = [{
            "author": str((n.get("author") or {}).get("name") or ""),
            "at": n.get("created_at"),
            "body": str(n.get("body") or "")[:MAX_NOTE],
        } for n in (notes or []) if isinstance(n, dict) and not n.get("system")][-MAX_NOTES:]
        return {"ok": True, "item": item}
    return held(f"item:{host}/{project}#{iid}", FRESH, read, force)

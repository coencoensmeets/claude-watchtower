"""Updating the panel itself, from the releases in its own repository.

The panel is a git checkout and its releases are tags on that checkout, so "is
there a newer version" is a question git can answer on its own — no update
server in the middle, no second copy of the code to trust. Fetch the tags,
compare the newest release tag against the commit HEAD is sitting on. Pressing
**Update** checks that tag out, rebuilds the frontend and restarts the panel on
the new code.

Four rules keep that from being a foot-gun.

Nothing moves over uncommitted work, and nothing moves off a branch of your own.
A checkout that is dirty, or on a branch which is neither the default one nor a
release tag, is reported with the reason and left where it is. Somebody
developing the panel should not have the panel offering to move their HEAD.

A release is checked out *detached*, exactly as it was published. Fast-forwarding
the default branch instead would land on the tip of main, which is not a release
and not what the button says.

The browser cannot name a version. The request carries back the tag it was shown,
so a page left open for a week cannot update to something it never offered — and
that tag still has to be one this module just read out of the repository.

And a release only ever goes forwards. A HEAD with commits the newest tag does
not have is ahead of the releases, not behind them, so there is nothing to
update to.

The reading costs a network fetch, so it is held for hours and read on the clock
rather than on the poll — the same arrangement as the plan reading next to it in
the app bar.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from watchtower import build
from watchtower.config import ROOT
from watchtower.git.read import git_run
from watchtower.git.write import GIT_NETWORK_TIMEOUT, default_remote, git_said, git_write
from watchtower.owned import (
    OWNED_BUSY, OWNED_COMPACT, OWNED_PROCS, OWNED_QUEUE, _OWNED_LOCK, owned_release_all,
)
from watchtower.rows import kept_rows
from watchtower.windows import load_names


# What counts as a release. A tag is a release if it is a plain three-part
# version, with or without the customary `v` — `v1.4.0`, `1.4.0`. Anything with a
# pre-release or build suffix on it (`v1.4.0-rc1`) is deliberately not a release:
# a tag you push to try something out should not restart everybody's panel.
RELEASE_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


# A fetch reaches the network, and a release lands every few days at best, so the
# answer is kept for hours. Opening the dialog asks for a fresh one anyway.
UPDATE_FRESH = 6 * 3600.0


# for-each-ref writes one record per tag with the fields separated inside it.
# Unit and record separators rather than tabs and newlines: a release body is
# several lines of prose and would otherwise run into the next tag.
FIELD, RECORD = "\x1f", "\x1e"


# How much of a release note is worth carrying into a dialog.
NOTE_LIMIT = 4000

# How many releases back the dialog lists. Anyone further behind than this does
# not want to read the whole history in a scrim.
NOTES_MAX = 10


# Long enough for the answer to reach the browser before the process is replaced,
# short enough that nobody wonders whether the button worked.
RESTART_DELAY = 1.0


UPDATE_LOCK = threading.Lock()


UPDATE_HELD: dict = {}


# Whether a fetch is out right now, so two people opening the dialog at once do
# not start two of them. Guarded by UPDATE_LOCK.
UPDATE_FETCHING = False


# Set once the restart is committed to, so a second press cannot queue a second
# one behind it.
UPDATE_RESTARTING = False


def release_of(tag: str) -> tuple[int, int, int] | None:
    """The version a release tag names, for comparing — or None if it is not one."""
    match = RELEASE_TAG.match(tag.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())        # type: ignore[return-value]


def is_checkout() -> bool:
    """Is the panel running from a git checkout at all?

    A tarball, a distribution package or a copied directory has no releases to
    move between, and the chip stays away rather than offering something it
    cannot do.
    """
    return (ROOT / ".git").exists()


def parse_tags(text: str) -> list[dict]:
    """`for-each-ref` over refs/tags into one entry per release, newest first.

    Sorted here rather than by git: `--sort=-v:refname` orders `v1.10.0` after
    `v1.9.0` correctly but has its own ideas about anything that is not a plain
    version, and this only ever wants the releases anyway.
    """
    found = []
    for record in text.split(RECORD):
        parts = record.strip("\n").split(FIELD)
        if len(parts) < 5:
            continue
        name, at, sha, subject, body = parts[0].strip(), parts[1], parts[2].strip(), parts[3], parts[4]
        version = release_of(name)
        if not version:
            continue
        try:
            when = float(at)
        except ValueError:
            when = 0.0
        found.append({"tag": name, "version": list(version), "at": when, "sha": sha,
                      "subject": subject.strip(), "body": body.strip()[:NOTE_LIMIT]})
    found.sort(key=lambda entry: entry["version"], reverse=True)
    return found


def read_tags(root: str) -> list[dict]:
    ok, text = git_run(root, [
        "for-each-ref", "refs/tags",
        # `*objectname` follows an annotated tag through to the commit it points
        # at; for a lightweight tag it is empty and `objectname` is the commit.
        f"--format=%(refname:short){FIELD}%(creatordate:unix){FIELD}"
        f"%(if)%(*objectname)%(then)%(*objectname)%(else)%(objectname)%(end){FIELD}"
        f"%(contents:subject){FIELD}%(contents:body){RECORD}",
    ], timeout=15.0)
    return parse_tags(text) if ok else []


def default_branch(root: str) -> str:
    """The branch a fresh clone lands on, as the remote reports it."""
    ok, text = git_run(root, ["rev-parse", "--abbrev-ref", "origin/HEAD"])
    name = text.strip().split("/")[-1] if ok else ""
    if name and name != "HEAD":
        return name
    # No remote HEAD recorded — a clone with `--single-branch`, or a remote that
    # never published one. Whichever of the usual two exists.
    for guess in ("main", "master"):
        ok, _ = git_run(root, ["rev-parse", "--verify", f"refs/remotes/origin/{guess}"])
        if ok:
            return guess
    return "main"


def head_state(root: str) -> dict:
    """Where HEAD is and whether the tree under it is clean."""
    ok, sha = git_run(root, ["rev-parse", "HEAD"])
    _, branch = git_run(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()
    detached = branch == "HEAD" or not branch
    dirty_ok, dirty_text = git_run(root, ["status", "--porcelain", "--untracked-files=no"])
    return {
        "sha": sha.strip() if ok else "",
        "branch": "" if detached else branch,
        "detached": detached,
        # A read that failed says nothing about the tree, and treating silence as
        # clean is the one direction that loses work.
        "dirty": bool(dirty_text.strip()) or not dirty_ok,
    }


def commits_between(root: str, base: str, tip: str) -> int:
    """How many commits `tip` has that `base` does not."""
    ok, text = git_run(root, ["log", "--format=%H", f"{base}..{tip}"], timeout=15.0)
    if not ok:
        return 0
    return len([line for line in text.splitlines() if line.strip()])


def fetch_releases(root: str) -> tuple[bool, str]:
    """Bring the remote's tags down. Reaches the network, so the long timeout."""
    remote = default_remote(root)
    if not remote:
        return False, "This checkout has no remote to look for releases on"
    # --force so a tag that was moved upstream is followed rather than reported as
    # a clash the panel cannot do anything about, and --prune-tags so a release
    # that was withdrawn stops being offered.
    ok, said = git_write(root, ["fetch", "--quiet", "--tags", "--force", "--prune-tags", remote],
                         GIT_NETWORK_TIMEOUT)
    if ok:
        return True, ""
    return False, git_said(said) or f"Could not reach {remote}"


# How far back to walk looking for releases this checkout does not have. Anyone
# behind by more than this is told about the newest and can read the rest on the
# repository — it is a bound on git calls, not on what can be updated to.
WALK_MAX = 25


def releases_missing(root: str, releases: list[dict]) -> list[dict]:
    """The releases HEAD does not already contain, newest first.

    Asked of git rather than worked out from the version numbers, because a
    version number does not say whether a commit is in this history. A checkout
    sitting between two releases has a `describe` of `v1.3.0-4-g…` and no tag of
    its own, and counting every release as missing would tell it that it is six
    behind when it is one.

    `releases` arrives newest version first, so the first one that *is* already in
    HEAD ends the walk: everything older than it is an ancestor too. Which also
    keeps a maintenance release on an abandoned line from being reported as news
    to a checkout that has moved past it.
    """
    missing = []
    for entry in releases[:WALK_MAX]:
        if not commits_between(root, "HEAD", entry["tag"]):
            break
        missing.append(entry)
    return missing


def why_not(state: dict, branch: str, ahead: int) -> str:
    """The reason this checkout is not going to be moved, or "" if it can be.

    Ordered by what somebody would want to be told first: your own work before
    your own branch, and both before anything about versions.
    """
    if state["dirty"]:
        return "There is uncommitted work in this checkout, so nothing here will move HEAD"
    if state["branch"] and state["branch"] != branch:
        return (f"This checkout is on {state['branch']}, not {branch} or a release — "
                f"so the panel leaves it alone")
    if ahead:
        return (f"This checkout is {ahead} commit{'' if ahead == 1 else 's'} ahead of the newest "
                f"release, so there is nothing newer to move to")
    return ""


def read_update(force: bool = False) -> dict:
    """Which release the panel is on, which is newest, and whether it can move.

    Held for hours: the fetch is the expensive part and a release does not land
    twice an hour. `force` is the dialog being opened, which is somebody asking.
    """
    global UPDATE_FETCHING
    if not is_checkout():
        return {"ok": False, "repo": False, "canUpdate": False,
                "message": "The panel is not running from a git checkout, so it cannot update itself"}

    now = time.time()
    with UPDATE_LOCK:
        held = dict(UPDATE_HELD)
        fresh = held.get("ok") and now - held.get("at", 0) < UPDATE_FRESH
        if fresh and not force:
            return {**held, "checking": False}
        if UPDATE_FETCHING:
            # Somebody's fetch is already out. Hand back what there is and say so
            # rather than opening a second connection for the same answer.
            return {**held, "checking": True} if held else {
                "ok": False, "repo": True, "canUpdate": False, "checking": True,
                "message": "Looking for a newer release…"}
        UPDATE_FETCHING = True

    try:
        answer = survey()
    finally:
        with UPDATE_LOCK:
            UPDATE_FETCHING = False

    answer["at"] = time.time()
    if answer.get("ok"):
        with UPDATE_LOCK:
            UPDATE_HELD.clear()
            UPDATE_HELD.update(answer)
        return {**answer, "checking": False}
    # A failed check does not throw away a good reading: the version you are on
    # plus the reason the check failed is more use than the reason alone.
    if held.get("ok"):
        return {**held, "checking": False, "message": answer.get("message", "")}
    return {**answer, "checking": False}


def survey() -> dict:
    """One reading: fetch the tags, then work out where that leaves this checkout."""
    root = str(ROOT)
    if not shutil.which("git"):
        return {"ok": False, "repo": True, "canUpdate": False, "message": "git is not installed"}

    fetched, trouble = fetch_releases(root)
    releases = read_tags(root)
    state = head_state(root)
    branch = default_branch(root)

    # The release HEAD is standing on, if it is standing on one exactly.
    at = next((r for r in releases if r["sha"] and r["sha"] == state["sha"]), None)
    latest = releases[0] if releases else None
    # And what to call the version otherwise: the nearest release with how far
    # past it we are, which is what `git describe` is for.
    _, described = git_run(root, ["describe", "--tags", "--always", "--dirty"], timeout=15.0)

    ahead = commits_between(root, latest["tag"], "HEAD") if latest else 0
    blocked = why_not(state, branch, ahead)
    # Nothing to work out when HEAD is already past the newest release: the walk
    # would break on its first step anyway, and `ahead` is the thing to report.
    missing = releases_missing(root, releases) if latest and not ahead else []
    return {
        "ok": True,
        "repo": True,
        "checking": False,
        "current": at["tag"] if at else "",
        "described": described.strip(),
        "latest": latest["tag"] if latest else "",
        "latestAt": latest["at"] if latest else 0,
        "behind": len(missing),
        "branch": state["branch"],
        "detached": state["detached"],
        "dirty": state["dirty"],
        "defaultBranch": branch,
        "ahead": ahead,
        # There is something newer, and this checkout is in a state to take it.
        "canUpdate": bool(missing) and not blocked,
        "why": blocked,
        "notes": [{k: r[k] for k in ("tag", "at", "subject", "body")} for r in missing[:NOTES_MAX]],
        "restart": restart_kind(),
        "message": "" if fetched else trouble,
    }


# --- what a restart costs


# Enough names for a sentence. Past this the dialog says how many rather than
# listing them, which is what somebody with nine sessions open wanted anyway.
RUNNING_NAMES_MAX = 4


def running_here() -> dict:
    """The sessions the panel is running itself, and what a restart would cost.

    A restart only stops *these*. A session in a terminal is its own process with
    its own pid, and it carries on across a panel restart without noticing —
    warning about those would be warning about nothing, and would teach people to
    ignore the warning that matters.

    What matters, in the order it matters: a turn in flight, which is cut off
    mid-sentence; a compaction, likewise; messages typed ahead, which are held in
    this process and go with it. The conversations themselves are Claude Code's
    files and are never at stake — which is the other half of what the warning has
    to say, or it reads as though the work is being thrown away.

    Read live on every request, never out of the cached survey: this is the one
    part of the answer that is different a second later.
    """
    with _OWNED_LOCK:
        held = [sid for sid, entry in OWNED_PROCS.items() if entry["proc"].poll() is None]
        busy = [sid for sid in held if sid in OWNED_BUSY]
        compacting = [sid for sid in held if (OWNED_COMPACT.get(sid) or {}).get("running")]
        queued = sum(len(OWNED_QUEUE.get(sid) or []) for sid in held)
    # A session the panel runs has a kept row, which is where its name lives once
    # there is no session file to read one out of. A name you typed yourself wins,
    # the same way it does everywhere else.
    rows, names = kept_rows(), load_names()
    def called(session_id: str) -> str:
        return names.get(session_id) or (rows.get(session_id) or {}).get("name") or "a session"
    return {
        "here": len(held),
        "busy": len(busy),
        "compacting": len(compacting),
        "queued": queued,
        # Mid-turn first: those are the ones worth reading before pressing.
        "names": [called(sid) for sid in busy + [s for s in held if s not in busy]][:RUNNING_NAMES_MAX],
    }


# --- doing it


def unit_from_cgroup(text: str) -> str:
    """The systemd unit a cgroup path belongs to, or "" if it is not a unit's.

    Only the *leaf* of the path counts, and that is the whole care of this
    function — systemd puts a service's processes directly in the unit's own
    cgroup, so the last component is the unit and every component above it is a
    slice the process merely lives inside. Walking up the path looking for
    something ending in `.service` finds `user@1000.service` from anything at all
    in a desktop session, and restarting that logs you out.
    """
    # cgroup v2: one line, `0::/the/path`. A v1 machine has several lines and no
    # single answer, and gets none rather than a guess.
    lines = [line for line in text.splitlines() if line.startswith("0::")]
    if len(lines) != 1:
        return ""
    leaf = lines[0][3:].rstrip("/").rsplit("/", 1)[-1]
    if not leaf.endswith(".service") or leaf.startswith("user@"):
        return ""
    return leaf


def unit_name() -> str:
    """The systemd unit this process is running under, if it is under one.

    Read out of the cgroup rather than asked of systemctl: it is a file, it is
    exact, and it needs no subprocess. INVOCATION_ID is systemd's own mark on a
    service it started and is asked for as well — though not relied on, because it
    is inherited, so a terminal opened from a unit carries it too.
    """
    if not os.environ.get("INVOCATION_ID") or not shutil.which("systemctl"):
        return ""
    try:
        return unit_from_cgroup(Path("/proc/self/cgroup").read_text())
    except OSError:
        return ""


def restart_kind() -> str:
    """How the panel will come back: systemd's job, or its own."""
    return "systemd" if unit_name() else "exec"


def do_update(tag: str) -> tuple[bool, str, bool]:
    """Move to `tag`, rebuild, and arrange to come back on it.

    Returns (ok, what to say, whether a restart is on its way). The restart is
    deliberately not done here: the answer has to reach the browser first, so it
    is handed to a timer and this returns.
    """
    global UPDATE_RESTARTING
    with UPDATE_LOCK:
        if UPDATE_RESTARTING:
            return False, "An update is already on its way — the panel is about to restart", True

    if not is_checkout():
        return False, "The panel is not running from a git checkout, so it cannot update itself", False

    # Read again rather than trusting the reading the browser was shown. A page
    # left open overnight, a checkout somebody edited in the meantime, a release
    # withdrawn upstream: all of it is settled here, not in the browser.
    state = read_update(force=True)
    if not state.get("ok"):
        return False, state.get("message") or "Could not check for a newer release", False
    if not state.get("canUpdate"):
        return False, state.get("why") or "There is no newer release to move to", False
    wanted = str(tag or "").strip()
    if wanted and wanted != state["latest"]:
        return False, (f"The newest release is {state['latest']} now, not {wanted} — "
                       f"have another look before updating"), False

    target = state["latest"]
    # --detach: a release is a commit that was published, and this lands on
    # exactly that. Moving the default branch instead would put the panel on the
    # tip of main, which is not the thing the button offered.
    ok, said = git_write(str(ROOT), ["switch", "--detach", target])
    if not ok:
        return False, git_said(said) or f"Could not check {target} out", False

    with UPDATE_LOCK:
        UPDATE_HELD.clear()
        UPDATE_RESTARTING = True

    # The frontend is TypeScript, so the new version's panel does not exist until
    # it is built. Built here rather than left to the restart so that a build
    # failure is something you are told about now — the restart would fall back
    # to the previous build and say nothing.
    built, note = build.build()
    # Counted before the timer fires, because after it there is nothing to count.
    stopping = running_here()["here"]
    threading.Timer(RESTART_DELAY, restart_now).start()
    also = (f", stopping {stopping} session{'' if stopping == 1 else 's'} it was running"
            if stopping else "")
    if not built:
        return True, (f"On {target} — but its frontend did not build, so the panel restarts on "
                      f"the previous one{also}: {git_said(note)}"), True
    return True, f"Updated to {target} — restarting the panel on it now{also}", True


def restart_now() -> None:
    """Come back on the code that is now on disk.

    Under systemd it is systemd's job, queued with --no-block so the request is
    accepted before this process goes. Otherwise the panel replaces itself: the
    same interpreter, the same arguments, the code freshly read off disk.

    Either way the held sessions are let go of first. A held session is a
    `claude` of ours sitting on somebody's transcript, and `execv` runs no
    `atexit` handler, so nothing else would.
    """
    owned_release_all()

    unit = unit_name()
    if unit:
        try:
            subprocess.Popen(["systemctl", "--user", "restart", "--no-block", unit],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except (OSError, subprocess.SubprocessError):
            pass                                  # fall through and do it ourselves

    script = ROOT / "server.py"
    try:
        # The listening socket is not inherited across an exec — Python marks its
        # descriptors non-inheritable — so the new process finds the port free.
        os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
    except OSError:
        # Nothing left to try. Going down is still better than serving code that
        # is no longer the code on disk, and a systemd unit brings it back.
        os._exit(0)

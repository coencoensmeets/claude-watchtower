"""The one door every git write goes through.

A request names an action; this decides what that means and calls the writer.
The repository is never taken from the request — the caller passes the root the
panel already discovered for the session it was asked about.
"""

from __future__ import annotations

import shutil

from watchtower.git.read import read_git, read_status
from watchtower.git.write import (
    GIT_NETWORK_TIMEOUT, count, default_remote, discard_paths, git_commit, git_pull, git_push,
    git_write, has_commits, known_paths, stage_paths, switch_branch, unstage_paths,
)


def git_action(root: str, action: str, payload: dict) -> tuple[bool, str, int]:
    """One Source-Control action, named by the browser, run against one repo.

    Returns (ok, message, http status). Everything the browser sends is either a
    fixed action name, a message string, or a path that git is already reporting
    as changed — see known_paths.
    """
    if not shutil.which("git"):
        return False, "git is not installed", 409

    wanted = [p for p in (payload.get("paths") or []) if isinstance(p, str) and p]

    if action in ("stage", "unstage", "discard"):
        paths, entries = known_paths(root, wanted)
        if not paths:
            return False, "Those files have no changes any more", 409
        if action == "stage":
            ok, said = stage_paths(root, paths)
        elif action == "unstage":
            ok, said = unstage_paths(root, paths)
        else:
            ok, said = discard_paths(root, paths, entries)
        return ok, said, 200 if ok else 409

    if action == "stageAll":
        entries = read_status(root)
        if not entries:
            return False, "Nothing to stage", 409
        ok, said = git_write(root, ["add", "-A"])
        return ok, said or f"Staged {count(len(entries), 'file')}", 200 if ok else 409

    if action == "unstageAll":
        # A bare mixed reset puts the whole index back to HEAD, which is exactly
        # "unstage everything" and needs no path list.
        ok, said = (git_write(root, ["reset", "-q"]) if has_commits(root)
                    else git_write(root, ["rm", "-q", "--cached", "-r", "--", "."]))
        return ok, said or "Unstaged everything", 200 if ok else 409

    if action == "discardAll":
        entries = read_status(root)
        keep_new = not payload.get("includeUntracked")
        paths = [e["path"] for e in entries if not (keep_new and e["untracked"])]
        if not paths:
            return False, "Nothing to discard", 409
        ok, said = discard_paths(root, paths, entries)
        return ok, said, 200 if ok else 409

    if action == "switch":
        ok, said = switch_branch(root, payload)
        return ok, said, 200 if ok else 409

    if action == "commit":
        ok, said = git_commit(root, str(payload.get("message") or "").strip(),
                              bool(payload.get("amend")), bool(payload.get("stageAll")))
        return ok, said, 200 if ok else 409

    state = read_git(root, log_limit=0)

    if action == "push":
        ok, said = git_push(root, state, bool(payload.get("force")))
        return ok, said, 200 if ok else 409

    if action == "pull":
        ok, said = git_pull(root, state)
        return ok, said, 200 if ok else 409

    if action == "fetch":
        remote = default_remote(root)
        if not remote:
            return False, "This repository has no remote to fetch from", 409
        ok, said = git_write(root, ["fetch", "--prune", remote], GIT_NETWORK_TIMEOUT)
        return ok, said or f"Fetched {remote}", 200 if ok else 409

    if action == "sync":
        # Sync is the editor's one button for "catch up, then hand over": pull
        # first so a push cannot be rejected for being behind.
        if state.get("upstream") and state.get("behind"):
            ok, said = git_pull(root, state)
            if not ok:
                return False, said, 409
            state = read_git(root, log_limit=0)
        if not state.get("upstream") or state.get("ahead"):
            ok, said = git_push(root, state)
            return ok, said, 200 if ok else 409
        return True, "Already up to date", 200

    if action == "stash":
        ok, said = git_write(root, ["stash", "push", "--include-untracked",
                                    *(["-m", str(payload["message"])] if payload.get("message") else [])])
        return ok, said or "Stashed the changes", 200 if ok else 409

    if action == "stashPop":
        # A successful pop prints the whole working-tree status; the file list is
        # about to be redrawn anyway, so only a failure is worth repeating.
        ok, said = git_write(root, ["stash", "pop"])
        return ok, "Restored the latest stash" if ok else (said or "Could not restore the stash"), 200 if ok else 409

    return False, "Unknown action", 400

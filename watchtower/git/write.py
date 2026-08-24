"""Changing a repository: staging, committing, pushing, switching.

Split from the readers deliberately. Nothing here runs unless an action names
it, the panel builds every argument itself, and a path from the browser can only
ever land after `--`. Two writes at once would race for index.lock, so they
queue behind one lock rather than failing for a reason that has nothing to do
with what was asked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

from watchtower.git.read import git_run, read_branches, read_git, read_status


# The writing half of the allowlist above. Split from the readers on purpose:
# nothing here runs unless an action below names it, and the panel builds every
# argument itself, so a path from the browser can only ever land after `--`.
GIT_WRITE_COMMANDS = frozenset({"add", "reset", "rm", "restore", "clean", "commit",
                                "push", "pull", "fetch", "stash", "switch"})


# Anything reaching the network gets the long one; an index write is local and
# should never take this long unless a hook is doing the work.
GIT_WRITE_TIMEOUT = 25.0


GIT_NETWORK_TIMEOUT = 180.0


# Two writes at once would race for index.lock and one would fail for a reason
# that has nothing to do with what was asked. The panel's own writes queue up
# here; a session writing at the same time is still git's own lock to report.
GIT_WRITE_LOCK = threading.Lock()


def git_write(root: str, args: list[str], timeout: float = GIT_WRITE_TIMEOUT) -> tuple[bool, str]:
    """Run one writing git command in root and return (ok, what git said).

    Unlike git_run this wants the index lock — that is the point of it — so the
    optional-locks flags are gone. Every interactive door git might open is shut:
    no terminal prompt, no askpass helper, no editor. A push that needs a
    passphrase nobody can type therefore fails with a message instead of hanging
    until the timeout.
    """
    if not args or args[0] not in GIT_WRITE_COMMANDS:
        return False, "That is not something this panel runs"
    if not shutil.which("git"):
        return False, "git is not installed"
    try:
        with GIT_WRITE_LOCK:
            result = subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0",
                     "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GIT_EDITOR": "true",
                     "LC_ALL": "C.UTF-8"},
            )
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} gave up after {int(timeout)}s"
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run git: {error}"
    # A failure keeps the remote's own words: a hook that rejected a push says why
    # on `remote:` lines, and dropping them as noise would drop the reason with
    # them. On the way through they are noise, and the tail is what matters.
    failed = result.returncode != 0
    said = git_said(result.stderr, remote=failed) or git_said(result.stdout, remote=failed)
    return not failed, said


def git_said(text: str, lines: int = 4, remote: bool = False) -> str:
    """The last few meaningful lines of git's output, for a snackbar.

    Progress lines are carriage-return rewrites of one line and would otherwise
    arrive as one very long string; the tail is where the reason lives anyway.
    """
    kept = [part.strip() for chunk in text.splitlines()
            for part in chunk.split("\r") if part.strip()]
    if not remote:
        kept = [line for line in kept
                if not line.startswith(("remote:", "Everything up-to-date"))] or kept
    return " · ".join(kept[-lines:])[:500]


def known_paths(root: str, wanted: list[str]) -> tuple[list[str], list[dict]]:
    """Keep only the paths git itself is currently reporting as changed.

    The browser is not trusted to name a file: an action can only touch
    something the panel just showed, which rules out absolute paths, `..`,
    and anything outside this working tree by construction rather than by
    checking for those shapes one at a time.
    """
    entries = read_status(root)
    live = {}
    for entry in entries:
        live[entry["path"]] = entry
        if entry["origPath"]:
            live[entry["origPath"]] = entry
    return [p for p in wanted if p in live], entries


def has_commits(root: str) -> bool:
    ok, _ = git_run(root, ["rev-parse", "--verify", "HEAD"])
    return ok


def stage_paths(root: str, paths: list[str]) -> tuple[bool, str]:
    # `add` covers a deletion too — it records "this path is gone" in the index —
    # so one command serves modified, new and deleted alike.
    ok, said = git_write(root, ["add", "--", *paths])
    return ok, said or f"Staged {count(len(paths), 'file')}"


def unstage_paths(root: str, paths: list[str]) -> tuple[bool, str]:
    # Before the first commit there is no HEAD to reset back to, so the only way
    # out of the index is to drop the entry.
    args = (["reset", "-q", "HEAD", "--", *paths] if has_commits(root)
            else ["rm", "-q", "--cached", "-r", "--", *paths])
    ok, said = git_write(root, args)
    return ok, said or f"Unstaged {count(len(paths), 'file')}"


def discard_paths(root: str, paths: list[str], entries: list[dict]) -> tuple[bool, str]:
    """Throw away working-tree changes — the one action here that loses work.

    A tracked file goes back to what the index holds; an untracked one has no
    earlier version to go back to, so discarding it means deleting it. The two
    need different commands, hence the split.
    """
    by_path = {e["path"]: e for e in entries}
    untracked = [p for p in paths if by_path.get(p, {}).get("untracked")]
    tracked = [p for p in paths if p not in untracked]
    trouble = []
    if tracked:
        ok, said = git_write(root, ["restore", "--worktree", "--", *tracked])
        if not ok:
            trouble.append(said or "could not restore some files")
    if untracked:
        ok, said = git_write(root, ["clean", "-q", "-f", "--", *untracked])
        if not ok:
            trouble.append(said or "could not delete some new files")
    if trouble:
        return False, " · ".join(trouble)
    # Only say what actually happened: "0 files" alongside a deletion reads as a
    # failure when it is nothing of the sort.
    said = []
    if tracked:
        said.append(f"Discarded changes in {count(len(tracked), 'file')}")
    if untracked:
        said.append(f"{'d' if said else 'D'}eleted {count(len(untracked), 'new file')}")
    return True, ", ".join(said) or "Nothing to discard"


def count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def git_commit(root: str, message: str, amend: bool, stage_all: bool) -> tuple[bool, str]:
    """Commit what is staged, optionally staging everything first.

    `stage_all` is the panel's version of the editor's "there is nothing staged —
    commit all your changes?": the answer arrives as this flag rather than the
    panel deciding for you.
    """
    if stage_all:
        ok, said = git_write(root, ["add", "-A"])
        if not ok:
            return False, said or "Could not stage the changes"

    args = ["commit", "--no-status", "--cleanup=strip"]
    # An amend can keep the message it already has; a fresh commit cannot.
    if amend and not message:
        args += ["--amend", "--no-edit"]
    elif amend:
        args += ["--amend", "-m", message]
    elif message:
        args += ["-m", message]
    else:
        return False, "A commit needs a message"

    ok, said = git_write(root, args)
    if ok:
        head = read_git(root, log_limit=1)["commits"]
        subject = head[0]["subject"] if head else message
        return True, f"Committed — {subject}"
    if "nothing to commit" in said or "no changes added to commit" in said:
        return False, "Nothing staged to commit"
    if "Please tell me who you are" in said or "empty ident name" in said:
        return False, "git has no name and email set yet — git config user.name and user.email"
    return False, said or "Could not commit"


def git_push(root: str, state: dict, force: bool = False) -> tuple[bool, str]:
    """Push the current branch, publishing it if it has no upstream yet."""
    branch = state.get("branch")
    if state.get("detached") or not branch:
        return False, "HEAD is detached, so there is no branch to push"
    if state.get("upstream"):
        args = ["push"]
    else:
        remote = default_remote(root)
        if not remote:
            return False, "This repository has no remote to push to"
        # The editor calls this publishing, and it is the only push that decides
        # where a branch belongs — hence --set-upstream, once.
        args = ["push", "--set-upstream", remote, branch]
    if force:
        # Never plain --force: this refuses if someone else pushed in the meantime.
        args.append("--force-with-lease")
    ok, said = git_write(root, args, GIT_NETWORK_TIMEOUT)
    if ok:
        if not state.get("upstream"):
            return True, f"Published {branch} — it now tracks its remote"
        return True, f"Pushed {count(state.get('ahead') or 0, 'commit')} to {state['upstream']}"
    if "rejected" in said and "fetch first" in said:
        return False, "Rejected — the remote has commits you do not have yet. Pull first."
    return False, said or "Could not push"


def git_pull(root: str, state: dict) -> tuple[bool, str]:
    if state.get("detached"):
        return False, "HEAD is detached, so there is nothing to pull into"
    if not state.get("upstream"):
        return False, "This branch has no upstream yet, so there is nothing to pull"
    ok, said = git_write(root, ["pull", "--ff-only"], GIT_NETWORK_TIMEOUT)
    if ok:
        # A pull prints a diffstat across several lines; how many commits arrived
        # is the part worth saying, and the file list redraws either way.
        return True, f"Pulled {count(state.get('behind') or 0, 'commit')} from {state['upstream']}"
    # A pull that cannot fast-forward wants a merge or a rebase, and choosing
    # between those under a session that is editing the same tree is not the
    # panel's call to make.
    if "Not possible to fast-forward" in said or "diverging" in said or "divergent" in said:
        return False, "The branch and its upstream have diverged — merge or rebase in the terminal"
    return False, said or "Could not pull"


def switch_branch(root: str, payload: dict) -> tuple[bool, str]:
    """Move HEAD to another branch, or to a new one.

    `switch` rather than `checkout`: it only ever moves branches, so a branch name
    that happens to match a path cannot turn this into a file operation. git
    refuses on its own when the move would drop uncommitted work, and that refusal
    is the message that comes back.
    """
    name = str(payload.get("branch") or "").strip()
    if not name:
        return False, "No branch named"

    if payload.get("create"):
        ok, why = usable_branch_name(root, name)
        if not ok:
            return False, why
        start = str(payload.get("from") or "").strip()
        args = ["switch", "--create", name]
        if start:
            known = read_branches(root)
            if start not in {b["name"] for b in known["local"]} | {b["name"] for b in known["remote"]}:
                return False, "There is no such branch to start from"
            args.append(start)
        ok, said = git_write(root, args)
        return ok, (f"On a new branch, {name}" if ok else said or "Could not create that branch")

    # An existing branch is only switched to by a name the repository is currently
    # reporting — which is also what keeps a leading dash out of the argument list.
    known = read_branches(root)
    if name in {b["name"] for b in known["local"]}:
        ok, said = git_write(root, ["switch", name])
        return ok, (f"On {name}" if ok else said or "Could not switch")

    remote = next((b for b in known["remote"] if b["name"] == name), None)
    if remote:
        # Checking out somebody else's branch means making a local one that
        # follows it, which is what the editor does with the same click.
        ok, said = git_write(root, ["switch", "--track", name])
        return ok, (f"On {remote['short']}, tracking {name}" if ok
                    else said or "Could not check that branch out")

    return False, "That branch is not in this repository any more"


# A name git would take but the panel should not: one that could be read as an
# option, or that names nothing at all.
BRANCH_NAME_LIMIT = 200


def usable_branch_name(root: str, name: str) -> tuple[bool, str]:
    if name.startswith("-"):
        return False, "A branch name cannot start with a dash"
    if len(name) > BRANCH_NAME_LIMIT:
        return False, f"That name is longer than {BRANCH_NAME_LIMIT} characters"
    if any(ch.isspace() for ch in name):
        return False, "A branch name cannot contain spaces"
    # git's own rules are longer than anything worth restating here, so they are
    # the ones that decide.
    ok, _ = git_run(root, ["check-ref-format", "--branch", name])
    if not ok:
        return False, f"git will not accept “{name}” as a branch name"
    return True, ""


def default_remote(root: str) -> str | None:
    ok, text = git_run(root, ["remote"])
    if not ok:
        return None
    remotes = [line.strip() for line in text.splitlines() if line.strip()]
    if not remotes:
        return None
    return "origin" if "origin" in remotes else remotes[0]

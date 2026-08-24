"""Reading a repository: where HEAD is, what has changed, what the log says.

Every command here is on an allowlist and runs with the optional-locks flags,
so reading the panel's own view can never block a session's own git. Nothing
here writes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def git_root(cwd: str) -> str | None:
    """The repository root above cwd, found by looking for .git — no subprocess.

    Returns the working tree root, which is what every git command below runs
    against: a session sitting three directories down would otherwise stage and
    diff relative to the wrong place.
    """
    if not cwd:
        return None
    path = Path(cwd)
    for candidate in [path, *path.parents]:
        git = candidate / ".git"
        # A file rather than a directory means a worktree or a submodule; either
        # way the tree root is still the directory holding it.
        if git.is_dir() or git.is_file():
            return str(candidate)
    return None


def git_branch(cwd: str) -> str | None:
    """Current branch by reading .git/HEAD — no subprocess, no repo lock.

    Kept file-based because every session in the list calls it once a poll;
    shelling out that often would be a subprocess per session per 15 seconds for
    a string that is sitting right there in a file.
    """
    path = Path(cwd)
    for candidate in [path, *path.parents]:
        git = candidate / ".git"
        head = None
        if git.is_dir():
            head = git / "HEAD"
        elif git.is_file():
            try:
                link = git.read_text().strip()
            except OSError:
                return None
            if link.startswith("gitdir:"):
                head = Path(link[7:].strip()) / "HEAD"
        if head and head.exists():
            try:
                text = head.read_text().strip()
            except OSError:
                return None
            if text.startswith("ref: refs/heads/"):
                return text[16:]
            return text[:7] if text else None
    return None


# Only these reach the git binary. The panel builds every argument itself — the
# allowlist is here so that a later write path has to add itself deliberately
# rather than inheriting the ability to run anything.
GIT_READ_COMMANDS = frozenset({"status", "log", "rev-parse", "stash", "diff", "remote",
                               "for-each-ref", "check-ref-format", "describe"})


def git_run(root: str, args: list[str], timeout: float = 6.0) -> tuple[bool, str]:
    """Run one read-only git command in root and return (ok, stdout).

    --no-optional-locks so that reading a repository never blocks the session
    working in it, and never leaves an index.lock behind if we are killed
    mid-read. No shell: arguments are passed as a list, always.
    """
    if not args or args[0] not in GIT_READ_COMMANDS:
        return False, ""
    if not shutil.which("git"):
        return False, ""
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", root, *args],
            capture_output=True, text=True, timeout=timeout,
            # A repository is not a place to inherit the panel's environment
            # wholesale; a pager or an editor would hang the request.
            env={**os.environ, "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, result.stdout


def parse_status(text: str) -> list[dict]:
    """Parse `status --porcelain=v2 -z` into one entry per path.

    Structured rather than pre-rendered: staging a single file later needs each
    entry's own identity, which a formatted list cannot give back.
    """
    entries: list[dict] = []
    fields = text.split("\0")
    index = 0
    while index < len(fields):
        line = fields[index]
        index += 1
        if not line:
            continue
        kind = line[0]
        if kind in ("1", "2"):
            # A rename or copy record carries one extra field — the similarity
            # score — before the path, so the two shapes are split differently.
            width = 8 if kind == "1" else 9
            parts = line.split(" ", width)
            if len(parts) < width + 1:
                continue
            xy, path = parts[1], parts[width]
            orig = None
            if kind == "2" and index < len(fields):
                # Under -z the source path follows as its own field.
                orig = fields[index]
                index += 1
            entries.append({
                "path": path,
                "origPath": orig,
                "staged": xy[0] if xy[0] != "." else None,
                "unstaged": xy[1] if xy[1] != "." else None,
                "untracked": False,
                "conflicted": False,
            })
        elif kind == "u":
            parts = line.split(" ", 10)
            if len(parts) < 11:
                continue
            entries.append({
                "path": parts[10], "origPath": None,
                "staged": None, "unstaged": None,
                "untracked": False, "conflicted": True,
            })
        elif kind == "?":
            entries.append({
                "path": line[2:], "origPath": None,
                "staged": None, "unstaged": None,
                "untracked": True, "conflicted": False,
            })
    entries.sort(key=lambda e: e["path"])
    return entries


# One record per commit, unit-separated so a subject containing anything at all
# still parses. %x1f is the ASCII unit separator, %x1e the record separator.
LOG_FORMAT = "%H%x1f%h%x1f%P%x1f%an%x1f%at%x1f%D%x1f%s%x1e"


def parse_log(text: str) -> list[dict]:
    """Parse the log format above into commits carrying their parents.

    Parents come along because the lane-drawing a graph needs is a client-side
    job over the real ancestry, not something to scrape out of --graph's ASCII.
    """
    commits = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 7:
            continue
        sha, short, parents, author, when, refs, subject = parts[:7]
        try:
            timestamp = int(when)
        except ValueError:
            timestamp = 0
        commits.append({
            "sha": sha,
            "short": short,
            "parents": parents.split() if parents else [],
            "author": author,
            "at": timestamp,
            "refs": [r.strip() for r in refs.split(",") if r.strip()],
            "subject": subject,
        })
    return commits


# One record per ref: where it is, what it tracks, and when it last moved. The
# full refname comes along because it is the only part that says for certain
# whether a ref is a local branch or a remote one — the short name cannot, since
# `refs/remotes/origin/HEAD` shortens to plain `origin` and a local branch is
# free to have a slash in it. symref marks that pointer, which is not a branch.
REF_FORMAT = ("%(refname)%1f%(refname:short)%1f%(upstream:short)"
              "%1f%(committerdate:unix)%1f%(HEAD)%1f%(symref)")


def read_branches(root: str) -> dict:
    """The branches this repository could be switched to.

    Local ones first, most recently committed first — the order that matters when
    you are moving between two or three branches all week. Then the remote ones
    with no local branch of their own, which is what "check this out" means for a
    branch somebody else pushed.
    """
    out: dict = {"local": [], "remote": []}
    ok, text = git_run(root, ["for-each-ref", "--sort=-committerdate",
                             f"--format={REF_FORMAT}", "refs/heads", "refs/remotes"])
    if not ok:
        return out

    local_names = set()
    remotes = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 6:
            continue
        full, name, upstream, when, head, symref = parts[:6]
        if not name or symref:
            continue          # a remote's default-branch pointer, not a branch
        try:
            at = int(when)
        except ValueError:
            at = 0
        if full.startswith("refs/heads/"):
            local_names.add(name)
            out["local"].append({"name": name, "upstream": upstream or None,
                                 "at": at, "current": head == "*"})
        elif full.startswith("refs/remotes/"):
            remotes.append({"name": name, "at": at})

    for entry in remotes:
        # A remote branch that already has a local counterpart is reachable by
        # that name; listing it twice would only ask which one you meant.
        short = entry["name"].split("/", 1)[1]
        if short not in local_names:
            out["remote"].append({**entry, "short": short})
    return out


def read_git(root: str, log_limit: int = 60) -> dict:
    """Everything the Git tab reads, in one pass over the repository."""
    out: dict = {
        "ok": True, "repoRoot": root, "head": None, "branch": None,
        "upstream": None, "ahead": 0, "behind": 0, "detached": False,
        "files": [], "commits": [], "stashes": 0, "gitAvailable": True,
    }
    if not shutil.which("git"):
        return {**out, "ok": False, "gitAvailable": False,
                "message": "git is not installed, so this tab can only show the branch"}

    ok, status = git_run(root, ["status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z"])
    if not ok:
        return {**out, "ok": False, "message": "Could not read this repository"}

    # The --branch headers come through as ordinary NUL-separated fields.
    for field in status.split("\0"):
        if not field.startswith("# branch."):
            continue
        key, _, value = field[9:].partition(" ")
        if key == "oid":
            out["head"] = None if value == "(initial)" else value
        elif key == "head":
            out["detached"] = value == "(detached)"
            out["branch"] = None if value == "(detached)" else value
        elif key == "upstream":
            out["upstream"] = value
        elif key == "ab":
            for token in value.split():
                try:
                    count = int(token[1:])
                except ValueError:
                    continue
                if token.startswith("+"):
                    out["ahead"] = count
                elif token.startswith("-"):
                    out["behind"] = count

    out["files"] = parse_status(status)

    # --exclude=refs/stash keeps a stash's two internal commits out of what is
    # meant to be history; HEAD is named explicitly so a detached one still
    # appears, since --all only walks refs.
    ok, log = git_run(root, ["log", f"--max-count={log_limit}", "--date-order",
                             "--exclude=refs/stash", "--all", "HEAD",
                             f"--pretty=format:{LOG_FORMAT}"])
    # A repository with no commits yet fails here, and that is not an error.
    out["commits"] = parse_log(log) if ok else []

    ok, stash = git_run(root, ["stash", "list"])
    out["stashes"] = len([line for line in stash.splitlines() if line]) if ok else 0

    # Cheap enough to come along with every reading — one for-each-ref — and the
    # branch menu has to open on the branches that exist now, not the ones that
    # existed when the tab was opened.
    out["branches"] = read_branches(root)
    return out


def read_diff(root: str, path: str, staged: bool) -> dict:
    """One file's diff, as unified text, for the pane the file rows open.

    Untracked files have nothing to diff against, so they are read from disk and
    presented as an all-added patch — which is what the editor shows too.
    """
    out = {"ok": True, "path": path, "staged": bool(staged), "text": "", "binary": False}
    entry = next((f for f in read_status(root) if f["path"] == path), None)
    if entry is None:
        return {**out, "ok": False, "message": "That file no longer has changes"}

    if entry["untracked"]:
        full = Path(root) / path
        # A trailing slash is git reporting a directory it will not look inside —
        # a nested repository or a worktree. There is no patch to draw for one.
        if path.endswith("/") or full.is_dir():
            return {**out, "binary": True,
                    "message": "A directory git reports whole — it does not look inside this one"}
        try:
            body = full.read_text(errors="strict")
        except (OSError, UnicodeDecodeError):
            return {**out, "binary": True, "message": "New file — not text, so there is nothing to show"}
        lines = body.splitlines()
        head = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        return {**out, "text": head + "".join(f"+{line}\n" for line in lines)}

    args = ["diff", "--no-color", "--no-ext-diff", "--find-renames"]
    if staged:
        args.append("--cached")
    ok, text = git_run(root, [*args, "--", path], timeout=10.0)
    if not ok:
        return {**out, "ok": False, "message": "Could not read that diff"}
    if "Binary files" in text.split("@@", 1)[0]:
        return {**out, "binary": True, "message": "Binary file — nothing to show line by line"}
    return {**out, "text": text}


def read_status(root: str) -> list[dict]:
    ok, text = git_run(root, ["status", "--porcelain=v2", "--untracked-files=all", "-z"])
    return parse_status(text) if ok else []

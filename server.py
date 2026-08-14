#!/usr/bin/env python3
"""claude-watchtower — a live panel for every Claude Code session on this machine.

Reads ~/.claude/sessions/*.json, which Claude Code keeps current with one file
per running session (pid, name, cwd, status). Status is one of:

    busy     working right now
    waiting  blocked on you — a question or a permission prompt
    shell    running a foreground shell command
    idle     finished, waiting for your next prompt

A busy or shell reading is only believed while the session keeps refreshing it;
see effective_status. Some sessions — the VS Code extension among them — write
no status at all, and are read from their liveness instead. A session started
from inside another one writes no file at all; the panel builds one for it out
of /proc — see child_session.

Serves a small web UI and can raise the terminal or editor window that owns a
session, using xdotool on X11.

Standard library only. No install step.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from watchtower import build, config
from watchtower.proc import proc_starttime, ancestors, proc_name, session_tty, cpu_seconds
from watchtower.config import (
    HOME, SESSION_DIR, STATIC_DIR,
    HISTORY_SECONDS, SAMPLE_INTERVAL,
    ACTIVE_STATUSES, STATUS_TTL, TRANSCRIPT_WINDOW, WORKING_CPU,
    CPU_WINDOW, LIVENESS_GRACE,
)


def status_age(data: dict, now: float) -> float | None:
    written = data.get("statusUpdatedAt") or data.get("updatedAt")
    written = written / 1000 if isinstance(written, (int, float)) else data.get("fileMtime")
    return now - written if isinstance(written, (int, float)) else None


def effective_status(data: dict, now: float, live: bool) -> str:
    """The session's status, dropped to idle once nothing backs it up.

    Claude Code writes `busy` or `shell` and then goes quiet: it records a status
    when the status changes, not on a timer. So an eight-minute-old `busy` may
    mean eight minutes of hard thinking, or it may mean the session wrote one
    line at startup — while sourcing its shell snapshot — and has sat at the
    prompt ever since. Age alone cannot tell those apart, and guessing from age
    alone reports every long turn as ready.

    `live` carries the second opinion: is the process burning CPU, is its
    transcript still growing. Only when the reading is old *and* nothing else
    says the session is working does the panel stop believing it.

    Only the active states expire. `waiting` means blocked on you and stays put
    for as long as you take, which is not the same as going stale.

    A session that writes no status at all is a separate case — see
    inferred_status.
    """
    if not data.get("status"):
        return inferred_status(live)
    status = data["status"]
    if status not in ACTIVE_STATUSES or live:
        return status
    age = status_age(data, now)
    return "idle" if age is not None and age > STATUS_TTL else status


def inferred_status(live: bool) -> str:
    """The state of a session that never reports one.

    Not every entry point keeps the status field current. The VS Code extension
    (`entrypoint: claude-vscode`) writes its session file once at startup and
    never adds a status to it, so taking the absent field at face value pins
    such a session to `idle` — which the panel shows as "Waiting" — for its
    whole life, working turns included.

    The liveness signals are the only reading left, and they separate the two
    states that matter here: CPU burning or a transcript still growing means the
    turn is running, and nothing means the turn is over. `waiting` cannot be
    reached this way, so a permission prompt in such a session reads as done
    rather than as blocked on you; that is the same as before this inference.
    """
    return "busy" if live else "idle"


# ----------------------------------------------------------------- proc helpers


# --------------------------------------------- sessions that write no own file


def end_process(pid: int, recorded_start: str | None, force: bool) -> tuple[bool, str]:
    """Signal a session's process, refusing if that pid is no longer the session.

    Pids get reused, so the starttime recorded in the session file is checked
    again right before signalling — otherwise a stale panel could kill whatever
    inherited the number.
    """
    if not isinstance(pid, int) or pid <= 1:
        return False, "That session has no process to end"
    actual = proc_starttime(pid)
    if actual is None:
        return False, "That process has already gone"
    if recorded_start not in (None, "", actual):
        return False, "That pid belongs to a different process now"
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return False, "That process has already gone"
    except PermissionError:
        return False, "Not allowed to end that process"
    except OSError as exc:
        return False, str(exc)
    return True, "Session force quit" if force else "Ending the session…"


# --------------------------------------------------------------- sending input


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# A reply, if one comes, arrives within a moment. Nothing is lost by not waiting:
# the message is already written.
SAY_TIMEOUT = 2.0


def peer_token(pid: int) -> str | None:
    """The session's inbox token, if it published one.

    Newer sessions write <pid>.<hash>.key beside the session file; older ones
    don't, and their socket accepts an unauthenticated message instead. Both are
    normal, so a missing key is not an error.
    """
    for path in sorted(SESSION_DIR.glob(f"{pid}.*.key")):
        try:
            raw = path.read_text().strip()
        except OSError:
            continue
        if not raw:
            continue
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            for field in ("peerToken", "token", "key"):
                value = obj.get(field)
                if isinstance(value, str) and value:
                    return value
            continue
        return raw
    return None


def say_to_session(data: dict, text: str) -> tuple[bool, str]:
    """Inject a user turn into a live session over its messaging socket.

    The pid's starttime is re-checked first, exactly as end_process does — the
    socket is named after the pid, so a stale panel must not be able to talk to
    whatever inherited the number.
    """
    text = text.strip()
    if not text:
        return False, "Nothing to send"

    pid = data.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return False, "That session has no process to send to"
    actual = proc_starttime(pid)
    if actual is None:
        return False, "That process has already gone"
    if data.get("procStart") not in (None, "", actual):
        return False, "That pid belongs to a different process now"

    protocol = data.get("peerProtocol")
    if protocol != PEER_PROTOCOL:
        # Rather than guess at a protocol we have not seen, say so and stay read-only.
        return False, f"This session speaks messaging protocol {protocol!r}, which this panel does not know"

    sock_path = data.get("messagingSocketPath")
    if not sock_path:
        return False, "This session is not listening for messages"
    if not Path(sock_path).exists():
        return False, "This session's message socket has gone"

    lines: list[dict] = []
    token = peer_token(pid)
    if token:
        lines.append({"type": "auth", "token": token})
    lines.append({"type": "user", "message": {"role": "user", "content": text}})
    payload = "".join(json.dumps(line) + "\n" for line in lines).encode()

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(SAY_TIMEOUT)
    try:
        conn.connect(sock_path)
        conn.sendall(payload)
    except (OSError, socket.timeout) as exc:
        conn.close()
        return False, f"Could not reach that session: {exc}"

    # Anything sent back is either an auth refusal or a receipt saying the
    # message was held for approval rather than delivered. Silence is the normal
    # case and means it went straight in.
    held = None
    try:
        conn.shutdown(socket.SHUT_WR)
        buf = conn.recv(4096)
        for raw in buf.decode("utf-8", "replace").splitlines():
            if not raw.strip():
                continue
            try:
                note = json.loads(raw)
            except ValueError:
                continue
            if note.get("status") in ("held", "denied", "expired"):
                held = note
    except (OSError, socket.timeout):
        pass
    finally:
        conn.close()

    if held:
        status = held.get("status")
        if status == "denied":
            return False, "That session declined the message"
        if status == "expired":
            return False, "The message expired before it was let through"
        return True, "Sent — waiting to be allowed through at the terminal"
    return True, "Sent"


# -------------------------------------------------------------------- git repo


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


# --------------------------------------------------------------- git commands


# Only these reach the git binary. The panel builds every argument itself — the
# allowlist is here so that a later write path has to add itself deliberately
# rather than inheriting the ability to run anything.
GIT_READ_COMMANDS = frozenset({"status", "log", "rev-parse", "stash", "diff", "remote",
                               "for-each-ref", "check-ref-format"})


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


# --------------------------------------------------------------- git writes


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


def read_status(root: str) -> list[dict]:
    ok, text = git_run(root, ["status", "--porcelain=v2", "--untracked-files=all", "-z"])
    return parse_status(text) if ok else []


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


# ------------------------------------------------------- writing the message


# A commit message is a small, closed job, so it goes to the quick model rather
# than whatever the session in that folder happens to be using. It runs headless
# — no terminal, no session — so a stopped session's repository can still have a
# message written for it, and the session's own conversation is never disturbed.
MESSAGE_MODEL = "haiku"
MESSAGE_TIMEOUT = 90.0


# Knowing our own pids only hides our own errands. A second panel on another
# port — a test instance, a second window's server — reads the same folder, so
# its `/usage` run is somebody else's pid and lands in the list as a session
# that arrives, says nothing and goes; and even our own leaves a row behind for
# the twenty seconds the store keeps a session it can no longer see.
#
# A headless run says what it is in its own file. `claude -p` and the SDKs write
# an `sdk-*` entrypoint where a session you can type at writes `cli` (or
# `claude-vscode`), and no amount of watching one will ever let you answer it.
# So they are left out by what they are rather than by who started them, which
# holds however many panels are running and after the process is gone.


def is_headless(data: dict) -> bool:
    """Is this session file a headless run rather than a session to watch."""
    return str(data.get("entrypoint") or "").startswith("sdk-")
# Enough patch to describe a real change. Past this the subject would be a guess
# either way, and the diff is only there to be summarised.
MESSAGE_DIFF_LIMIT = 40_000

MESSAGE_TASK = """Write a git commit message for the change below.

Rules:
- One subject line, imperative mood ("Add", not "Added"), no trailing full stop,
  72 characters at the outside.
- Follow the style of the recent commits shown, if they have one.
- Add a body only if the change needs explaining, after one blank line, wrapped
  at 72 characters. Say why, not what — the diff already says what.
- Output the message and nothing else: no preamble, no code fences, no quotes
  around it, no "here is".
"""


def message_context(root: str) -> tuple[bool, str]:
    """What the model is shown: the patch that is about to be committed.

    The same scope the commit button would use — the index if anything is in it,
    the whole working tree otherwise — so the message describes the commit that
    is actually going to happen.
    """
    entries = read_status(root)
    staged = [e for e in entries if e["staged"]]
    parts = []

    if staged:
        ok, patch = git_run(root, ["diff", "--cached", "--no-color", "--no-ext-diff",
                                   "--find-renames"], timeout=15.0)
        new_files = [e["path"] for e in entries if e["staged"] == "A"]
    else:
        args = ["diff", "--no-color", "--no-ext-diff", "--find-renames"]
        # Before the first commit there is no HEAD to diff against, and nothing
        # is tracked yet either, so the file list below is the whole story.
        ok, patch = git_run(root, [*args, "HEAD"] if has_commits(root) else args, timeout=15.0)
        new_files = [e["path"] for e in entries if e["untracked"]]

    if not ok:
        patch = ""
    if new_files:
        parts.append("New files:\n" + "\n".join(f"  {p}" for p in new_files[:40]))
    if patch.strip():
        clipped = patch[:MESSAGE_DIFF_LIMIT]
        if len(patch) > MESSAGE_DIFF_LIMIT:
            clipped += "\n[diff truncated]"
        parts.append(f"Diff:\n{clipped}")
    if not parts:
        return False, "There is nothing staged or changed to describe yet"

    # The repository's own habits, so the message it gets back looks like the
    # ones around it rather than like a house style from somewhere else.
    ok, log = git_run(root, ["log", "--max-count=10", "--pretty=format:%s"])
    if ok and log.strip():
        parts.insert(0, "Recent commit subjects in this repository:\n"
                        + "\n".join(f"  {line}" for line in log.splitlines() if line.strip()))
    return True, "\n\n".join(parts)


def clean_message(text: str) -> str:
    """Take the model at its word, but not at its formatting.

    Asked for a bare message it usually gives one; a fenced block or a lead-in
    sentence is common enough that stripping them is cheaper than a retry.
    """
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        while lines and not lines[-1].startswith("```"):
            lines.pop()
        if lines:
            lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def suggest_message(root: str) -> tuple[bool, str]:
    """Ask a headless Claude for a commit message. Returns (ok, message or why)."""
    claude = shutil.which("claude")
    if not claude:
        return False, "Cannot find the claude command on PATH"

    ok, context = message_context(root)
    if not ok:
        return False, context

    # Popen rather than run, only so the pid is known while it is alive: that is
    # what keeps this errand out of the session list. See OWN_ERRANDS.
    try:
        process = subprocess.Popen(
            [claude, "--print", "--model", MESSAGE_MODEL,
             # It is handed everything it needs on stdin. No tools means no
             # permission prompt to answer and nothing it can do to the tree.
             "--allowed-tools", "", "--output-format", "text"],
            cwd=root, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run claude: {error}"

    own_errand(process.pid, True)
    try:
        out, err = process.communicate(f"{MESSAGE_TASK}\n{context}\n", timeout=MESSAGE_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return False, f"Claude did not answer within {int(MESSAGE_TIMEOUT)}s"
    except (OSError, subprocess.SubprocessError) as error:
        process.kill()
        return False, f"Could not run claude: {error}"
    finally:
        # Held only for the life of the process, so a recycled pid can never
        # inherit the hiding.
        own_errand(process.pid, False)

    if process.returncode != 0:
        return False, git_said(err) or "Claude could not write a message"
    message = clean_message(out)
    if not message:
        return False, "Claude answered with nothing"
    return True, message


# --------------------------------------------------------------- the plan left


# `/usage` is the one thing in this panel that no file on this machine knows.
# What a subscription has left is the account's, not the session's, and it lives
# behind Anthropic's API — so it is asked for the way you would ask yourself, by
# running Claude Code's own command and reading what it prints.
#
# Which is the point of doing it this way. The alternative was for the panel to
# read the OAuth token out of ~/.claude/.credentials.json and call an undocumented
# endpoint itself: a web server on this machine holding your credentials, for a
# reading the official client already gives away for free. This spawns `claude`,
# handles no secret, and asks for nothing the terminal would not have told you.
#
# It costs no tokens — the command fetches and prints, and samples no model, which
# a run against a fresh transcript confirms: not one usage entry — but it does take
# five seconds and a process, so it is asked rarely and its answer is kept.
PLAN_TIMEOUT = 45.0
PLAN_FRESH = 300.0

PLAN_LOCK = threading.Lock()
PLAN_HELD: dict = {}

# "Current session: 34% used · resets Aug 12, 5:49pm (Europe/Amsterdam)", and the
# week's two lines in the same shape. The reset clause is optional: a limit at 0%
# has nothing to reset from yet.
PLAN_LIMIT = re.compile(
    r"^(?P<name>[^:]{1,60}?):\s*(?P<percent>\d{1,3})%\s*used"
    r"(?:\s*[·|-]\s*resets\s*(?P<resets>.+?))?\s*$")
# "Last 24h · 4141 requests · 46 sessions" — the heading of a block of bullets.
PLAN_BLOCK = re.compile(r"^(Last\s.+|What's contributing.*)$")


def parse_plan(text: str) -> dict:
    """Read `/usage`'s report into figures, keeping the text it came from.

    The output is a human's report rather than an interface, so nothing here
    insists on it. Every line that reads as a limit becomes one; anything else is
    kept in order as prose, and a run that parses to nothing still hands back
    what it was given rather than an empty panel.
    """
    headline, limits, blocks = "", [], []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = PLAN_LIMIT.match(line.strip())
        if match:
            limits.append({
                "name": match.group("name").strip(),
                "percent": min(100, int(match.group("percent"))),
                "resets": (match.group("resets") or "").strip(),
            })
            continue
        if PLAN_BLOCK.match(line.strip()):
            blocks.append({"title": line.strip(), "lines": []})
            continue
        if blocks and raw.startswith((" ", "\t")):
            blocks[-1]["lines"].append(line.strip())
            continue
        if not headline:
            headline = line.strip()
        elif blocks:
            blocks[-1]["lines"].append(line.strip())
    return {"headline": headline, "limits": limits, "blocks": blocks, "text": text.strip()}


def run_plan() -> dict:
    """Run `claude /usage` once and read the answer."""
    claude = shutil.which("claude")
    if not claude:
        return {"ok": False, "message": "Cannot find the claude command on PATH"}
    try:
        # Same shape as the commit-message errand: printing, no tools, so there is
        # no permission prompt to answer and nothing it can touch. Run from home
        # rather than a repository — this is the account's reading, not a folder's.
        process = subprocess.Popen(
            [claude, "--print", "/usage", "--model", MESSAGE_MODEL,
             "--allowed-tools", "", "--output-format", "text"],
            cwd=str(HOME), text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "message": f"Could not run claude: {error}"}

    own_errand(process.pid, True)
    try:
        out, err = process.communicate(timeout=PLAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return {"ok": False, "message": f"Claude did not answer within {int(PLAN_TIMEOUT)}s"}
    except (OSError, subprocess.SubprocessError) as error:
        process.kill()
        return {"ok": False, "message": f"Could not run claude: {error}"}
    finally:
        own_errand(process.pid, False)

    if process.returncode != 0:
        return {"ok": False, "message": git_said(err) or "Claude could not read your usage"}
    if not out.strip():
        return {"ok": False, "message": "Claude answered with nothing"}
    return {"ok": True, **parse_plan(out)}


def read_plan(force: bool = False) -> dict:
    """The account's remaining plan, read at most every few minutes.

    A reading costs five seconds and a process, and the figure moves in
    percentage points over hours, so it is kept and handed back until it is stale.
    Two people opening the dialog at once get the same answer rather than two
    runs: the second is told one is on its way and shown what there is.
    """
    now = time.time()
    with PLAN_LOCK:
        held = dict(PLAN_HELD)
        fresh = held.get("ok") and now - held.get("at", 0) < PLAN_FRESH
        if fresh and not force:
            return {**held, "reading": False}
        if config.PLAN_RUNNING:
            # Somebody's run is already in flight. Hand back what we have and say
            # so, rather than starting a second `claude` for the same answer.
            return {**held, "reading": True} if held else {"ok": False, "reading": True,
                                                          "message": "Reading your usage…"}
        config.PLAN_RUNNING = True

    try:
        answer = run_plan()
    finally:
        with PLAN_LOCK:
            config.PLAN_RUNNING = False

    answer["at"] = time.time()
    if answer.get("ok"):
        with PLAN_LOCK:
            PLAN_HELD.clear()
            PLAN_HELD.update(answer)
        return {**answer, "reading": False}
    # A failed read does not throw away a good one: the last figures with the
    # reason the refresh failed is more use than the reason alone.
    if held.get("ok"):
        return {**held, "reading": False, "message": answer.get("message", "")}
    return {**answer, "reading": False}


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


# ------------------------------------------------------ what it can be asked for


# --------------------------------------------------------------- last activity


# ------------------------------------------------------------- permission mode


# --------------------------------------------------------- the question on screen


# ------------------------------------------------------------------ the subject


# ------------------------------------------------------------- tokens and cost


# ------------------------------------------------------------------- X11 windows


# ------------------------------------------------------------- sticky sessions
# A session file disappears when its process does, and with it the row. A sticky
# session keeps its row: the panel remembers enough about it — id, name, folder —
# to go on showing the conversation, and can start Claude Code back up on that
# same transcript with `claude --resume`.


from watchtower.sessions import child_sessions, peer_protocols
from watchtower.errands import own_errand, is_own_errand
from watchtower.sessions import PEER_PROTOCOL
from watchtower.transcript import transcript_paths, last_activity, read_permission_mode, read_pending_question, read_ai_title, TRANSCRIPT_LIMIT_MAX, read_transcript
from watchtower.usage import read_usage
from watchtower.catalog import read_catalog
from watchtower.windows import WINDOWS, load_pairs, save_pairs, load_names, save_names, clean_name, window_exists, window_title, activate, select_window, identify_and_pair, resolve_window
from watchtower.control import load_sticky, save_sticky, start_session, resolve_folder, locate_folder, new_session


# ------------------------------------------------------------------ session store


class SessionStore:
    """Polls the session files and keeps a state trace for each session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._history: dict[str, list[list]] = {}
        self._branch_cache: dict[str, tuple[float, str | None]] = {}
        self._root_cache: dict[str, tuple[float, str | None]] = {}
        self._activity_cache: dict[str, tuple[float, dict | None]] = {}
        self._mode_cache: dict[str, tuple[float, str | None]] = {}
        self._title_cache: dict[str, tuple[float, str | None]] = {}
        # session id -> (read at, transcript mtime then, the question or None)
        self._question_cache: dict[str, tuple[float, float | None, dict | None]] = {}
        self._first_seen: dict[str, float] = {}
        # pid -> (sampled at, cpu seconds then, was it working)
        self._cpu: dict[int, tuple[float, float, bool]] = {}
        # session id -> when its liveness signals last said it was working
        self._alive_at: dict[str, float] = {}
        self._transcript_cache: dict[str, tuple[float, float | None]] = {}

    # --- reading

    def _read_files(self) -> list[dict]:
        live: list[dict] = []
        try:
            files = sorted(SESSION_DIR.glob("*.json"))
        except OSError:
            files = []  # nested sessions are found without it — read on
        for path in files:
            try:
                data = json.loads(path.read_text())
                # The last resort for judging how fresh the status is.
                data["fileMtime"] = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            pid = data.get("pid")
            if not isinstance(pid, int):
                continue
            if is_own_errand(pid) or is_headless(data):
                continue  # a headless run, not a session to watch
            actual = proc_starttime(pid)
            recorded = data.get("procStart")
            if actual is None:
                continue  # process is gone
            if recorded not in (None, "", actual):
                continue  # this pid belongs to something else now
            live.append(data)
        # Sessions started from inside another one write no file of their own, so
        # they are read off /proc and appended here, as ordinary sessions.
        known = {data["pid"] for data in live}
        live.extend(child_sessions(known, peer_protocols(live)))
        return live

    def _branch(self, cwd: str) -> str | None:
        now = time.time()
        hit = self._branch_cache.get(cwd)
        if hit and now - hit[0] < 15:
            return hit[1]
        value = git_branch(cwd)
        self._branch_cache[cwd] = (now, value)
        return value

    def _repo_root(self, cwd: str) -> str | None:
        # A working folder does not move under a running session, so this is
        # cached longer than the branch, which changes whenever it checks out.
        now = time.time()
        hit = self._root_cache.get(cwd)
        if hit and now - hit[0] < 60:
            return hit[1]
        value = git_root(cwd)
        self._root_cache[cwd] = (now, value)
        return value

    def _activity(self, session_id: str, cwd: str) -> dict | None:
        now = time.time()
        hit = self._activity_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        value = last_activity(session_id, cwd)
        self._activity_cache[session_id] = (now, value)
        return value

    def _mode(self, session_id: str, cwd: str) -> str | None:
        # Read less often than the poll: it changes only when the transcript's
        # metadata block is re-appended, and finding it means walking back
        # through the tail of the transcript.
        now = time.time()
        hit = self._mode_cache.get(session_id)
        if hit and now - hit[0] < 10:
            return hit[1]
        value = read_permission_mode(session_id, cwd)
        self._mode_cache[session_id] = (now, value)
        return value

    def _title(self, session_id: str, cwd: str) -> str | None:
        # Rarer still than the mode: Claude renames a conversation when its
        # subject moves, which is minutes apart at best, and the reading costs
        # the same walk back through the tail.
        now = time.time()
        hit = self._title_cache.get(session_id)
        if hit and now - hit[0] < 20:
            return hit[1]
        value = read_ai_title(session_id, cwd)
        self._title_cache[session_id] = (now, value)
        return value

    def _question(self, session_id: str, cwd: str, now: float) -> dict | None:
        """The question this session is blocked on, re-read only when it could
        have changed.

        Unlike the mode and the title, this one is polled for its own sake — the
        panel wants to know the moment a question goes up — so it cannot sit
        behind a twenty-second cache. The transcript's mtime is what makes that
        affordable: a session that has written nothing since the last read cannot
        have asked or answered anything, and the walk is skipped.
        """
        touched = self._transcript_touched(session_id, cwd, now)
        hit = self._question_cache.get(session_id)
        if hit and hit[1] == touched and now - hit[0] < 30:
            return hit[2]
        value = read_pending_question(session_id, cwd)
        self._question_cache[session_id] = (now, touched, value)
        return value

    def _burning_cpu(self, pid: int, now: float) -> bool:
        """Is this process actually doing something, judged over a few seconds?"""
        current = cpu_seconds(pid)
        if current is None:
            return False
        at, before, verdict = self._cpu.get(pid, (0.0, current, True))
        if now - at >= CPU_WINDOW:
            # A session first seen gets the benefit of the doubt until there are
            # two readings to compare.
            verdict = (current - before) / (now - at) > WORKING_CPU if at else True
            self._cpu[pid] = (now, current, verdict)
        return verdict

    def _transcript_touched(self, session_id: str, cwd: str, now: float) -> float | None:
        """When the transcript last grew — a working session appends constantly."""
        hit = self._transcript_cache.get(session_id)
        if hit and now - hit[0] < 4:
            return hit[1]
        newest = None
        for path in transcript_paths(session_id, cwd):
            try:
                newest = max(newest or 0.0, path.stat().st_mtime)
            except OSError:
                continue
        self._transcript_cache[session_id] = (now, newest)
        return newest

    def _looks_alive(self, data: dict, now: float) -> bool:
        pid = data.get("pid")
        if isinstance(pid, int) and self._burning_cpu(pid, now):
            return True
        session_id = data.get("sessionId") or str(pid)
        touched = self._transcript_touched(session_id, data.get("cwd") or "", now)
        return touched is not None and now - touched < TRANSCRIPT_WINDOW

    def _alive(self, data: dict, session_id: str, now: float) -> bool:
        """Liveness, held across the quiet gaps inside a working turn.

        Both signals _looks_alive reads are bursty, and neither is absent only at
        the end of a turn — see LIVENESS_GRACE. Remembering the last time they
        agreed is what keeps a working session from blinking to "Waiting" and back
        while it sits on an API call.
        """
        if self._looks_alive(data, now):
            self._alive_at[session_id] = now
            return True
        last = self._alive_at.get(session_id)
        return last is not None and now - last < LIVENESS_GRACE

    # --- sampling loop

    def sample(self) -> None:
        now = time.time()
        seen: set[str] = set()
        for data in self._read_files():
            session_id = data.get("sessionId") or str(data.get("pid"))
            seen.add(session_id)
            status = effective_status(data, now, self._alive(data, session_id, now))
            with self._lock:
                self._first_seen.setdefault(session_id, now)
                trace = self._history.setdefault(session_id, [])
                if not trace or trace[-1][1] != status:
                    trace.append([now, status])
                cutoff = now - HISTORY_SECONDS
                while len(trace) > 1 and trace[1][0] < cutoff:
                    trace.pop(0)
                # Store the status the trace agrees with, not the raw reading.
                self._sessions[session_id] = {**data, "status": status, "seenAt": now}
        with self._lock:
            for session_id in list(self._sessions):
                if session_id not in seen:
                    # Keep it around briefly so the UI can show it closing out.
                    if now - self._sessions[session_id].get("seenAt", now) > 20:
                        gone = self._sessions.pop(session_id, None) or {}
                        self._history.pop(session_id, None)
                        self._first_seen.pop(session_id, None)
                        self._transcript_cache.pop(session_id, None)
                        self._mode_cache.pop(session_id, None)
                        self._title_cache.pop(session_id, None)
                        self._question_cache.pop(session_id, None)
                        self._alive_at.pop(session_id, None)
                        self._cpu.pop(gone.get("pid"), None)

    def run_forever(self) -> None:
        while True:
            try:
                self.sample()
            except Exception:  # a sampler crash must not take the panel down
                pass
            time.sleep(SAMPLE_INTERVAL)

    # --- presenting

    def snapshot(self) -> dict:
        now = time.time()
        pairs = load_pairs()
        names = load_names()
        sticky = load_sticky()
        dirty = False
        with self._lock:
            raw = list(self._sessions.values())
            history = {k: list(v) for k, v in self._history.items()}

        out = []
        for data in raw:
            session_id = data.get("sessionId") or str(data.get("pid"))
            pid = data.get("pid")
            cwd = data.get("cwd") or ""
            alive = now - data.get("seenAt", 0) < 15
            status = data.get("status") or "idle"
            if not alive:
                status = "offline"
            chain = ancestors(pid) if isinstance(pid, int) else []
            session = {
                "sessionId": session_id,
                "pid": pid,
                # A name you typed wins over the one the session reports.
                "name": names.get(session_id) or data.get("name") or f"session {pid}",
                "givenName": names.get(session_id),
                "defaultName": data.get("name") or f"session {pid}",
                "cwd": cwd,
                "folder": os.path.basename(cwd) or cwd,
                "status": status,
                "kind": data.get("kind") or "interactive",
                "version": data.get("version"),
                "startedAt": (data.get("startedAt") or 0) / 1000 or self._first_seen.get(session_id, now),
                "statusSince": self._status_since(history.get(session_id), now),
                "branch": self._branch(cwd) if cwd else None,
                # The Git tab needs to tell "not a repository" apart from "a
                # repository whose HEAD would not read", which a null branch
                # cannot. Everything git runs against this root, not the cwd.
                "repoRoot": self._repo_root(cwd) if cwd else None,
                "activity": self._activity(session_id, cwd) if cwd else None,
                # The mode as of this session's last turn, and what was asked for
                # at the panel since — see read_permission_mode for why the two
                # can disagree for a while.
                "permissionMode": self._mode(session_id, cwd) if cwd else None,
                # What Claude says this session is about, for the line under its
                # name — the detail pane reads the same thing off the transcript.
                "title": self._title(session_id, cwd) if cwd else None,
                # The multiple-choice question this session is blocked on, so a
                # row can show what is being asked rather than only that
                # something is. See read_pending_question.
                "question": self._question(session_id, cwd, now) if cwd and alive else None,
                # The pane holding this session's prompt open, and so whether its
                # question can be answered from here at all. A session under a
                # bare terminal emulator has no pane and reads as null, which is
                # what the card says out loud rather than offering a dead button.
                "trace": self._trace(history.get(session_id), now),
                "alive": alive,
                "canSay": bool(
                    alive
                    and data.get("peerProtocol") == PEER_PROTOCOL
                    and data.get("messagingSocketPath")
                    and Path(data["messagingSocketPath"]).exists()
                ),
                "ancestors": chain,
                # Set for a nested session: the session it was started from. Its
                # own process parent is usually the terminal, not that session,
                # so the chain above does not lead back to it.
                "parentPid": data.get("parentPid"),
                # The pty is what tells two tabs of one terminal apart, and what
                # the window probe writes to.
                "tty": session_tty(pid) if isinstance(pid, int) else None,
                # Enough of the chain to spot a multiplexer or an ssh hop.
                "host": [proc_name(p) for p in chain[:5]],
                "sticky": session_id in sticky,
            }
            # Keep what a sticky row will need once the process is gone. Written
            # back now and then rather than every second — it is a poll loop.
            if session_id in sticky:
                held = sticky[session_id]
                fresh = {
                    "sessionId": session_id, "name": session["defaultName"], "cwd": cwd,
                    "startedAt": session["startedAt"], "lastSeen": now,
                    "version": session["version"], "kind": session["kind"],
                }
                if any(held.get(k) != fresh[k] for k in ("name", "cwd", "version")) \
                        or now - (held.get("lastSeen") or 0) > 30:
                    sticky[session_id] = fresh
                    dirty = True
            paired = (pairs.get(session_id) or {}).get("id")
            if paired and window_exists(paired):
                session["window"] = {
                    "id": paired,
                    "confidence": "paired" if pairs[session_id]["how"] == "picked" else "identified",
                    "title": window_title(paired),
                }
            else:
                if paired:
                    pairs.pop(session_id, None)
                    save_pairs(pairs)
                match = WINDOWS.match(session)
                session["window"] = match
            out.append(session)

        # A kept session whose process has gone still gets a row: same id, same
        # transcript, no pid. It is `stopped` rather than `offline` — nothing has
        # been lost, it is simply not running, and it can be started back up.
        if dirty:
            save_sticky(sticky)

        live_ids = {s["sessionId"] for s in out}
        for session_id, entry in sticky.items():
            if session_id in live_ids:
                continue
            cwd = entry.get("cwd") or ""
            out.append({
                "sessionId": session_id,
                "pid": None,
                "name": names.get(session_id) or entry.get("name") or "kept session",
                "givenName": names.get(session_id),
                "defaultName": entry.get("name") or "kept session",
                "cwd": cwd,
                "folder": os.path.basename(cwd) or cwd,
                "status": "stopped",
                "kind": entry.get("kind") or "interactive",
                "version": entry.get("version"),
                "startedAt": entry.get("startedAt") or entry.get("lastSeen") or now,
                "statusSince": entry.get("lastSeen") or now,
                "branch": self._branch(cwd) if cwd else None,
                "repoRoot": self._repo_root(cwd) if cwd else None,
                "activity": self._activity(session_id, cwd) if cwd else None,
                # For a stopped session these are the mode it was last in and
                # the last thing it was working on.
                "permissionMode": self._mode(session_id, cwd) if cwd else None,
                "title": self._title(session_id, cwd) if cwd else None,
                # A stopped session is not waiting for an answer, however its
                # transcript ends.
                "question": None,
                "trace": [],
                "alive": False,
                "canSay": False,
                # Starting runs a command here, so it follows the same gate as sending.
                "canStart": config.SAY_ENABLED,
                "ancestors": [],
                "tty": None,
                "host": [],
                "sticky": True,
                "window": None,
            })

        # Whose child a nested session is, said by name rather than by pid — the
        # row has no room for a number nobody recognises. Resolved here because it
        # takes every session to answer, and a parent may not be on the list at
        # all: it can have closed while its child kept running.
        named = {s["pid"]: s["name"] for s in out if s.get("pid")}
        for session in out:
            session["parentName"] = named.get(session.get("parentPid"))

        order = {"waiting": 0, "busy": 1, "shell": 2, "idle": 3, "offline": 4, "stopped": 5}
        # State decides which band a row sits in; inside a band the order is the
        # session's own identity — when it started, then its id — and never how
        # long it has been in that state. A row therefore moves only when its
        # state visibly changes, and comes back to the same slot afterwards. The
        # id makes the key total, so two sessions never swap on the strength of
        # the order they happened to be discovered in.
        out.sort(key=lambda s: (order.get(s["status"], 6), s["startedAt"] or 0, s["sessionId"]))
        return {
            "now": now,
            "sessions": out,
            "historySeconds": HISTORY_SECONDS,
            "canFocus": WINDOWS.available(),
            "canSend": config.SAY_ENABLED,
        }

    def raw(self, session_id: str) -> dict | None:
        """The session file as last read — including the fields the UI never sees."""
        with self._lock:
            data = self._sessions.get(session_id)
            return dict(data) if data else None

    @staticmethod
    def _status_since(trace: list[list] | None, now: float) -> float:
        return trace[-1][0] if trace else now

    @staticmethod
    def _trace(trace: list[list] | None, now: float) -> list[dict]:
        if not trace:
            return []
        spans = []
        for index, (start, status) in enumerate(trace):
            end = trace[index + 1][0] if index + 1 < len(trace) else now
            spans.append({"from": start, "to": end, "status": status})
        return spans


STORE = SessionStore()


# ------------------------------------------------------------------- http server


def wait_and_say(session_id: str, text: str, seconds: float = 90.0) -> None:
    """Deliver a message once the session it was meant for is listening again.

    Typing into a stopped session starts it up, and startup is not instant — the
    process has to come up and open its socket. This waits for that in the
    background so the click returns at once.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(1.0)
        data = STORE.raw(session_id)
        if not data or not data.get("messagingSocketPath"):
            continue
        if not Path(data["messagingSocketPath"]).exists():
            continue
        ok, _ = say_to_session(data, text)
        if ok:
            return


class Handler(BaseHTTPRequestHandler):
    server_version = "claude-watchtower"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # --- helpers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _session_by_id(self, session_id: str) -> dict | None:
        for session in STORE.snapshot()["sessions"]:
            if session["sessionId"] == session_id:
                return session
        return None

    def _session_repo(self, session_id: str) -> str | None:
        """The working tree a git request may act in — the session's own, or none.

        The root never comes from the request. Everything git runs against what
        the panel already discovered for the session it was asked about, so no
        request can point git at a repository the panel is not showing.
        """
        session = self._session_by_id(session_id)
        return (session or {}).get("repoRoot") or None

    # --- routes

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._json(STORE.snapshot())
            return
        if path == "/api/transcript":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            try:
                limit = max(1, min(TRANSCRIPT_LIMIT_MAX, int((query.get("limit") or ["60"])[0])))
            except ValueError:
                limit = 60
            self._json(read_transcript(session_id, session["cwd"], limit))
            return
        if path == "/api/usage":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            self._json(read_usage(session_id, session["cwd"]))
            return
        if path == "/api/commands":
            # A session that has gone still leaves the folders it could be asked
            # about, so a missing one is answered with what is true of every
            # session — your own skills and commands — rather than a 404.
            query = parse_qs(urlparse(self.path).query)
            session = self._session_by_id((query.get("sessionId") or [""])[0])
            self._json(read_catalog((session or {}).get("cwd")))
            return
        if path == "/api/plan":
            # Reading this runs a command on this machine, which is the same order
            # of risk as the panel's other errands — so it sits behind the same
            # loopback gate, however read-only the answer is.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Reading your plan is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            query = parse_qs(urlparse(self.path).query)
            self._json(read_plan((query.get("force") or [""])[0] == "1"))
            return
        if path == "/api/git":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            root = session.get("repoRoot")
            if not root:
                self._json({"ok": False, "isRepo": False,
                            "message": "This session's folder is not in a git repository"})
                return
            self._json({**read_git(root), "isRepo": True, "canWrite": config.SAY_ENABLED})
            return
        if path == "/api/git/diff":
            query = parse_qs(urlparse(self.path).query)
            root = self._session_repo((query.get("sessionId") or [""])[0])
            if not root:
                self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
                return
            file_path = (query.get("path") or [""])[0]
            if not file_path:
                self._json({"ok": False, "message": "No file asked for"}, 400)
                return
            self._json(read_diff(root, file_path, (query.get("staged") or [""])[0] == "1"))
            return
        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        # Everything else the page asks for — the stylesheet, the modules it
        # imports, the fonts — is whatever the build put in dist/. The
        # confinement check in _serve_static is what keeps that honest, and it
        # is the same check as before: only files under the served directory.
        self._serve_static(path)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        payload = self._body()
        session_id = str(payload.get("sessionId") or "")

        if path == "/api/focus":
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            window, why, identified = resolve_window(session_id, session)
            if not window:
                self._json({"ok": False, "message": why, "needsPairing": True}, 409)
                return
            WINDOWS.windows(force=True)
            ok, message = activate(window["id"])
            if ok and identified:
                message = "focused — and this session's window is now remembered"
            self._json({"ok": ok, "message": message, "window": window["id"],
                        "identified": identified}, 200 if ok else 500)
            return

        if path == "/api/identify":
            # Pairing without the click: the session's own terminal is asked
            # which window it is showing. See probe_window.
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            window_id, message = identify_and_pair(session_id, session)
            if not window_id:
                self._json({"ok": False, "message": message, "needsPairing": True}, 409)
                return
            self._json({"ok": True, "message": "Found it — window identified and remembered",
                        "window": window_id, "title": window_title(window_id)})
            return

        if path == "/api/pair":
            if not self._session_by_id(session_id):
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            window_id, message = select_window()
            if not window_id:
                self._json({"ok": False, "message": message}, 400)
                return
            pairs = load_pairs()
            pairs[session_id] = {"id": window_id, "how": "picked"}
            save_pairs(pairs)
            WINDOWS.windows(force=True)
            self._json({"ok": True, "message": "Window paired", "window": window_id})
            return

        if path == "/api/end":
            data = STORE.raw(session_id)
            if not data:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            ok, message = end_process(data.get("pid"), data.get("procStart"), bool(payload.get("force")))
            if ok:
                # The window pairing dies with the session it pointed at.
                pairs = load_pairs()
                if pairs.pop(session_id, None) is not None:
                    save_pairs(pairs)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/say":
            # A prompt is an instruction to an agent with tools, so this endpoint
            # is worth more than the others put together. It stays on loopback
            # even when the rest of the panel is served to the network.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Sending is off because the panel is not bound to loopback"}, 403)
                return
            data = STORE.raw(session_id)
            if not data:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            ok, message = say_to_session(data, str(payload.get("text") or ""))
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/locate":
            # Where the folder you picked in the browser's own dialog actually is.
            # Same gate as the listing it feeds: it reads this machine's
            # filesystem, and exists only to start a session.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Browsing folders is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            children = payload.get("children")
            found, why = locate_folder(str(payload.get("name") or ""),
                                       [str(c) for c in children] if isinstance(children, list) else [])
            if not found:
                self._json({"ok": False, "message": why}, 404)
                return
            self._json({"ok": True, "folders": found})
            return

        if path == "/api/git":
            # Committing, pushing and discarding change a checkout on this
            # machine, which is the same order of risk as prompting the session
            # that lives in it — so they sit behind the same loopback gate.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Git actions are off — this panel is serving read-only"}, 403)
                return
            root = self._session_repo(session_id)
            if not root:
                self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
                return
            action = str(payload.get("action") or "")
            # The one action that answers with something other than a sentence
            # about what it did: the message it wrote, for the box to hold.
            if action == "suggestMessage":
                ok, said = suggest_message(root)
                self._json({"ok": ok, "text": said if ok else "",
                            "message": "" if ok else said}, 200 if ok else 409)
                return
            ok, message, status = git_action(root, action, payload)
            self._json({"ok": ok, "message": message}, status)
            return

        if path == "/api/sticky":
            session = self._session_by_id(session_id)
            sticky = load_sticky()
            want = bool(payload.get("sticky", True))
            if want:
                if not session:
                    self._json({"ok": False, "message": "There is no such session"}, 404)
                    return
                sticky[session_id] = {
                    "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
                    "startedAt": session["startedAt"], "lastSeen": time.time(),
                    "version": session["version"], "kind": session["kind"],
                }
                save_sticky(sticky)
                self._json({"ok": True, "message": "Kept in the dashboard", "sticky": True})
                return
            if sticky.pop(session_id, None) is not None:
                save_sticky(sticky)
            self._json({"ok": True, "message": "No longer kept", "sticky": False})
            return

        if path == "/api/start":
            # Starting a session runs a command on this machine, which is the same
            # order of risk as sending it a prompt — so it lives behind the same
            # loopback gate.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            entry = load_sticky().get(session_id)
            if not entry:
                self._json({"ok": False, "message": "That session is not being kept"}, 404)
                return
            if STORE.raw(session_id):
                self._json({"ok": False, "message": "That session is already running"}, 409)
                return
            ok, message = start_session(entry)
            text = str(payload.get("text") or "").strip()
            if ok and text:
                # It cannot hear us yet. Hand the message to a thread that waits
                # for its socket and then delivers it.
                threading.Thread(target=wait_and_say, args=(session_id, text), daemon=True).start()
                message = "Starting it up — your message goes in as soon as it is listening"
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/new":
            # Same risk as /api/start — it runs a command on this machine — so it
            # sits behind the same loopback gate.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
                return
            # A folder named in the request opens a session there. This is a real
            # widening: every other form of this route took the folder from a
            # session already on screen, so it could not be pointed anywhere the
            # panel was not already showing. What is left holding it is the
            # loopback gate — and anyone through that gate can already put a
            # prompt into a session that holds tools and a checkout, which is the
            # greater power of the two. The path is still resolved and checked
            # before anything runs.
            if isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                folder, why = resolve_folder(payload["cwd"])
                if not folder:
                    self._json({"ok": False, "message": why}, 400)
                    return
                ok, message = new_session(folder)
                self._json({"ok": ok, "message": message}, 200 if ok else 409)
                return
            session = self._session_by_id(session_id)
            entry = load_sticky().get(session_id) or {}
            cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
            if not session and not entry:
                self._json({"ok": False, "message": "That session is no longer around"}, 404)
                return
            ok, message = new_session(cwd)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/rename":
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            # An empty name — or the session's own name typed back — clears the
            # override rather than storing a second copy of the default.
            name = clean_name(payload.get("name"))
            names = load_names()
            keep = bool(name) and name != session["defaultName"]
            if keep:
                names[session_id] = name
                save_names(names)
            elif names.pop(session_id, None) is not None:
                save_names(names)
            self._json({
                "ok": True,
                "message": "Renamed" if keep else "Name reset",
                "name": name if keep else session["defaultName"],
            })
            return

        if path == "/api/unpair":
            pairs = load_pairs()
            if pairs.pop(session_id, None) is not None:
                save_pairs(pairs)
            self._json({"ok": True, "message": "Pairing cleared"})
            return

        self._send(404, b"not found", "text/plain")

    MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }

    def _serve_static(self, name: str, content_type: str | None = None) -> None:
        target = (STATIC_DIR / name.lstrip("/")).resolve()
        # Never serve outside the static directory.
        if not str(target).startswith(str(STATIC_DIR) + os.sep) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        kind = content_type or self.MIME.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), kind)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live panel for local Claude Code sessions")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-send", action="store_true",
                        help="serve the conversation read-only, with no way to send input")
    parser.add_argument("--build", action="store_true",
                        help="build the frontend and exit")
    parser.add_argument("--no-build", action="store_true",
                        help="serve whatever is already built, however stale")
    args = parser.parse_args()

    if args.build:
        ok, said = build.build()
        if said:
            print(said, file=sys.stderr)
        raise SystemExit(0 if ok else 1)
    if not args.no_build and not build.ensure_built():
        raise SystemExit(1)

    config.SAY_ENABLED = is_loopback(args.host) and not args.no_send

    if not SESSION_DIR.exists():
        print(f"warning: {SESSION_DIR} does not exist yet — start a Claude Code session first")

    STORE.sample()
    threading.Thread(target=STORE.run_forever, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"claude-watchtower → http://{args.host}:{args.port}")
    if not WINDOWS.available():
        print("note: xdotool/DISPLAY unavailable, so window focusing is switched off")
    if not config.SAY_ENABLED:
        why = "--no-send" if args.no_send else f"not bound to loopback ({args.host})"
        print(f"note: sending input is switched off — {why}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

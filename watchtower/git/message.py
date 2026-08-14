"""Asking a headless Claude for a commit message.

The one place the panel runs a model of its own. Printing, no tools, so there
is no permission prompt to answer and nothing it can touch — and its pid is
noted as the panel's own errand so the session scan does not pick it up.
"""

from __future__ import annotations

import shutil
import subprocess

from watchtower.errands import own_errand
from watchtower.git.read import git_run, read_status
from watchtower.git.write import git_said, has_commits


# A commit message is a small, closed job, so it goes to the quick model rather
# than whatever the session in that folder happens to be using. It runs headless
# — no terminal, no session — so a stopped session's repository can still have a
# message written for it, and the session's own conversation is never disturbed.
MESSAGE_MODEL = "haiku"


MESSAGE_TIMEOUT = 90.0


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

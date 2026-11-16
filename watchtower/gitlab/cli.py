"""The one place `glab` is run.

Everything the panel knows about GitLab comes through this file, and it comes
through the CLI rather than through a token of the panel's own. That is the
whole design: `glab` already holds the credentials, already knows which hosts
you have, and already refreshes them — so the panel stores no secret, adds no
dependency beyond a binary that is either on the machine or is not, and cannot
outlive the access you meant to give it. Log out of `glab` and the panel is out
too.

Nothing here takes an argv, an endpoint or a host from a request. Callers name
an endpoint from a template they wrote themselves, and it is checked again on
the way past — see SAFE_ENDPOINT. A request reaches this file as a project id,
an item number and a title, never as a URL.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


# A read is one HTTP call to somebody else's server, so the wait is theirs to
# set rather than ours: slow enough for a loaded GitLab behind a VPN, short
# enough that a tab does not sit there. --paginate walks several pages, which is
# why it gets its own.
GLAB_TIMEOUT = 20.0


GLAB_PAGE_TIMEOUT = 45.0


# What an endpoint may look like at all. The panel builds every one of these
# from a literal, so this can only ever catch a bug of ours — but it catches it
# here, where the process is started, rather than three files further up where
# somebody was sure the value was a number. A leading dash is refused outright:
# that is how a path turns into a flag.
SAFE_ENDPOINT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:%?&=,+~@\[\]-]*$")


# The failure we got, as a word the callers above can branch on, because "it did
# not work" is not something a tab can explain to anyone. Each one has a
# sentence attached in read.py and a different thing to do about it.
NO_CLI = "no-cli"
NOT_LOGGED_IN = "not-logged-in"
UNAUTHORIZED = "unauthorized"
OFFLINE = "offline"
FAILED = "failed"


def have_glab() -> bool:
    return bool(shutil.which("glab"))


def glab_env() -> dict[str, str]:
    """Our environment with every way `glab` could stop and wait shut off.

    A pager would never be read and never exit; a prompt for a token would hang
    until the timeout and then report a timeout, which is the wrong answer to
    "you are not logged in". NO_COLOR because some of this is parsed as text.
    """
    return {**os.environ, "PAGER": "cat", "GLAB_PAGER": "cat", "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0", "GLAB_CHECK_UPDATE": "0"}


def why_failed(text: str) -> str:
    """Which of the named failures this output is, read out of what glab said."""
    said = text.lower()
    if "401" in said or "unauthorized" in said:
        return UNAUTHORIZED
    if "no token provided" in said or "not logged in" in said or "authentication" in said:
        return NOT_LOGGED_IN
    for sign in ("no such host", "dial tcp", "connection refused", "i/o timeout",
                 "network is unreachable", "context deadline exceeded", "tls"):
        if sign in said:
            return OFFLINE
    return FAILED


def run_glab(args: list[str], cwd: str | None = None,
             timeout: float = GLAB_TIMEOUT, with_stderr: bool = False) -> tuple[bool, str, str]:
    """Run one glab command. Returns (ok, output, reason) — reason only when not ok.

    No shell, and no working directory unless the caller has a repository in
    mind: `glab` reads the remote of the directory it is in to decide which host
    it is talking to, which is exactly what we want for a session's own
    repository and exactly what we do not want for a question about all of them.

    `with_stderr` is for the one command whose answer is not on stdout at all:
    `auth status` writes its whole report to stderr and exits 0 either way. Every
    other caller wants stdout alone, because stdout is JSON and a warning glab
    felt like printing would stop it parsing.
    """
    if not have_glab():
        return False, "", NO_CLI
    try:
        result = subprocess.run(
            ["glab", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL, env=glab_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "", OFFLINE
    except (OSError, subprocess.SubprocessError):
        return False, "", FAILED
    out = f"{result.stdout}\n{result.stderr}" if with_stderr else result.stdout
    if result.returncode != 0:
        return False, out, why_failed(f"{result.stderr}\n{result.stdout}")
    return True, out, ""


def api(endpoint: str, host: str = "", method: str = "GET",
        fields: dict | None = None, paginate: bool = False,
        cwd: str | None = None) -> tuple[bool, object, str]:
    """One GitLab REST call through `glab api`. Returns (ok, parsed, reason).

    `fields` goes through --raw-field, which sends everything as a JSON string
    and never reinterprets it: a work item titled `true`, or one whose
    description begins with an `@`, is a title and a description rather than a
    boolean and a filename. That is the difference between --raw-field and
    --field, and it is the reason only one of them is used here.
    """
    if not SAFE_ENDPOINT.match(endpoint):
        return False, None, FAILED
    args = ["api"]
    if host:
        args += ["--hostname", host]
    if method != "GET":
        args += ["--method", method]
    for key, value in (fields or {}).items():
        args += ["--raw-field", f"{key}={value}"]
    if paginate:
        args.append("--paginate")
    args.append(endpoint)
    ok, out, reason = run_glab(
        args, cwd=cwd, timeout=GLAB_PAGE_TIMEOUT if paginate else GLAB_TIMEOUT)
    if not ok:
        return False, None, reason
    if not paginate:
        try:
            return True, json.loads(out), ""
        except ValueError:
            return False, None, FAILED
    pages = read_pages(out)
    return (True, pages, "") if pages is not None else (False, None, FAILED)


def read_pages(text: str) -> list | None:
    """The one list behind `--paginate`\'s several, or None if it did not parse.

    glab writes one JSON array per page, back to back, which is not a document
    json.loads will take. Decoded one at a time with raw_decode rather than
    stitched together as text: a description containing `][` is ordinary
    markdown — `[see][ref]` — and a splice on that string would quietly eat it.
    """
    decoder = json.JSONDecoder()
    out: list = []
    at = 0
    while at < len(text):
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            break
        try:
            page, at = decoder.raw_decode(text, at)
        except ValueError:
            return None
        out.extend(page if isinstance(page, list) else [page])
    return out

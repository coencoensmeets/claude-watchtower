"""The one place `gh` is run.

The GitHub half of the same bargain watchtower.gitlab.cli makes: the panel keeps
no token. `gh` holds the credentials, knows which hosts you have, and refreshes
them, and everything the panel knows about GitHub it asks `gh` for. Log out of
`gh` and the panel is out too.

The failure words, the endpoint check and the page reader are the GitLab ones,
imported rather than copied: the two CLIs fail in the same handful of ways and
the tabs above both of them branch on the same words.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from watchtower.gitlab.cli import (FAILED, GLAB_PAGE_TIMEOUT, GLAB_TIMEOUT, NO_CLI,
                                   NOT_LOGGED_IN, OFFLINE, SAFE_ENDPOINT, UNAUTHORIZED,
                                   read_pages)


GH_TIMEOUT = GLAB_TIMEOUT

GH_PAGE_TIMEOUT = GLAB_PAGE_TIMEOUT


def have_gh() -> bool:
    return bool(shutil.which("gh"))


def gh_env() -> dict[str, str]:
    """Our environment with every way `gh` could stop and wait shut off."""
    return {**os.environ, "PAGER": "cat", "GH_PAGER": "cat", "NO_COLOR": "1",
            "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
            "GIT_TERMINAL_PROMPT": "0"}


def why_failed(text: str) -> str:
    """Which of the named failures this output is, read out of what gh said."""
    said = text.lower()
    if "http 401" in said or "bad credentials" in said:
        return UNAUTHORIZED
    if "gh auth login" in said or "not logged in" in said or "authentication" in said:
        return NOT_LOGGED_IN
    for sign in ("no such host", "dial tcp", "connection refused", "i/o timeout",
                 "network is unreachable", "context deadline exceeded", "tls"):
        if sign in said:
            return OFFLINE
    return FAILED


def run_gh(args: list[str], timeout: float = GH_TIMEOUT) -> tuple[bool, str, str]:
    """Run one gh command. Returns (ok, stdout, reason) — reason only when not ok.

    Never with a working directory: every call here names its repository and
    host outright, so nothing depends on which checkout gh happens to stand in.
    """
    if not have_gh():
        return False, "", NO_CLI
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, env=gh_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "", OFFLINE
    except (OSError, subprocess.SubprocessError):
        return False, "", FAILED
    if result.returncode != 0:
        return False, result.stdout, why_failed(f"{result.stderr}\n{result.stdout}")
    return True, result.stdout, ""


def api(endpoint: str, host: str = "github.com", method: str = "GET",
        fields: dict | None = None, paginate: bool = False) -> tuple[bool, object, str]:
    """One GitHub REST call through `gh api`. Returns (ok, parsed, reason).

    `fields` goes through --raw-field, never --field, for the reason the GitLab
    one does. A list value is sent as `key[]=…` once per entry, which is gh's
    spelling for a JSON array — labels and assignees are arrays on GitHub, not
    the comma-joined strings GitLab takes.
    """
    if not SAFE_ENDPOINT.match(endpoint):
        return False, None, FAILED
    args = ["api", "--hostname", host or "github.com"]
    if method != "GET":
        args += ["--method", method]
    for key, value in (fields or {}).items():
        for one in (value if isinstance(value, list) else [value]):
            args += ["--raw-field", f"{key}{'[]' if isinstance(value, list) else ''}={one}"]
    if paginate:
        args.append("--paginate")
    args.append(endpoint)
    ok, out, reason = run_gh(args, timeout=GH_PAGE_TIMEOUT if paginate else GH_TIMEOUT)
    if not ok:
        return False, None, reason
    if not paginate:
        try:
            return True, json.loads(out), ""
        except ValueError:
            return False, None, FAILED
    pages = read_pages(out)
    return (True, pages, "") if pages is not None else (False, None, FAILED)


# ---------------------------------------------------------- configured hosts

def config_dir() -> Path:
    """Where gh keeps hosts.yml, by gh's own rules."""
    if os.environ.get("GH_CONFIG_DIR"):
        return Path(os.environ["GH_CONFIG_DIR"])
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "gh"
    return Path.home() / ".config" / "gh"


TOP_KEY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9.:-]*):\s*$")


def configured_hosts() -> list[str]:
    """The hosts gh has an entry for, read from its hosts.yml. No network.

    Only the top-level keys — the host names — and never anything under them,
    which is where the tokens are. Read rather than asked of `gh auth status`
    because this is called to decide which forge a repository is on, once per
    session on the list, and that must not cost a request.
    """
    try:
        lines = (config_dir() / "hosts.yml").read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [m.group(1).lower() for m in map(TOP_KEY.match, lines) if m]

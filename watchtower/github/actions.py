"""The one GitHub write: filing an issue on the repository behind a session.

The same door as watchtower.gitlab.actions, with the same rule — the repository
is read out of the session's own checkout, never taken from the request.
"""

from __future__ import annotations

import re

from watchtower.github.cli import api
from watchtower.github.read import said
from watchtower.gitlab.actions import MAX_DESCRIPTION, MAX_TITLE, clean_labels
from watchtower.gitlab.read import forget


def create_issue(project: dict, payload: dict) -> tuple[bool, str, int, dict]:
    """File an issue. Returns (ok, message, http status, the item)."""
    title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()[:MAX_TITLE]
    if not title:
        return False, "An issue needs a title", 400, {}
    fields: dict = {"title": title}
    body = str(payload.get("description") or "")[:MAX_DESCRIPTION]
    if body:
        fields["body"] = body
    labels = clean_labels(payload.get("labels"))
    if labels:
        fields["labels"] = labels
    if payload.get("assignToMe"):
        ok, me, _ = api("user", host=project["host"])
        if ok and isinstance(me, dict) and me.get("login"):
            fields["assignees"] = [str(me["login"])]

    ok, raw, reason = api(f"repos/{project['path']}/issues", host=project["host"],
                          method="POST", fields=fields)
    if not ok or not isinstance(raw, dict):
        return False, said(reason), 502, {}
    forget()
    return True, f"Filed #{raw.get('number')}", 200, {
        "iid": raw.get("number"), "title": title, "webUrl": raw.get("html_url")}

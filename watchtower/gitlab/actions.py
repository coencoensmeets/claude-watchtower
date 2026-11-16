"""The one door every GitLab write goes through.

There is exactly one write: creating a work item. It is here rather than in
read.py for the same reason watchtower.git keeps its two halves apart — a reader
that could also write is a reader nobody can check at a glance.

The project is never taken from the request. The browser names a session, the
route resolves that session's repository root itself, and the project is read
out of the checkout's own remote — so the worst a crafted request can do is file
an issue on a project you already have open in the panel.
"""

from __future__ import annotations

import re

from watchtower.gitlab.cli import api
from watchtower.gitlab.read import encoded, forget, said


MAX_TITLE = 255


MAX_DESCRIPTION = 20000


MAX_LABELS = 12


# A label is a name on a server, and this is the shape GitLab itself allows:
# anything but a comma, which is its own separator, and no leading or trailing
# space. Sent as one comma-joined field, which is the API's spelling.
def clean_labels(raw: object) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    out = []
    for label in raw[:MAX_LABELS]:
        name = re.sub(r"\s+", " ", str(label)).strip().strip(",")
        if name and name not in out:
            out.append(name[:120])
    return out


def create_issue(project: dict, payload: dict) -> tuple[bool, str, int, dict]:
    """File a work item on the project behind a session's checkout.

    Returns (ok, message, http status, the item). One line of title is required
    and everything else is optional, which is the same bargain the GitLab new
    issue form makes — an item with a title and nothing else is a note to
    yourself, and refusing it would only send you to the browser.
    """
    title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()[:MAX_TITLE]
    if not title:
        return False, "A work item needs a title", 400, {}
    fields = {"title": title}
    body = str(payload.get("description") or "")[:MAX_DESCRIPTION]
    if body:
        fields["description"] = body
    labels = clean_labels(payload.get("labels"))
    if labels:
        fields["labels"] = ",".join(labels)
    if payload.get("assignToMe"):
        # The point of the button is usually "and it is mine", and the id has to
        # be a number — GitLab's issue API takes no @me here, unlike the CLI.
        ok, me, _ = api("user", host=project["host"])
        if ok and isinstance(me, dict) and me.get("id"):
            fields["assignee_ids"] = str(me["id"])

    ok, raw, reason = api(f"projects/{encoded(project['path'])}/issues",
                          host=project["host"], method="POST", fields=fields)
    if not ok or not isinstance(raw, dict):
        return False, said(reason), 502, {}
    # Every held reading of this project now has one fewer item in it than the
    # server does, and the tab is about to redraw.
    forget()
    return True, f"Filed {raw.get('references', {}).get('short') or '#' + str(raw.get('iid'))}", 200, {
        "iid": raw.get("iid"), "title": title, "webUrl": raw.get("web_url")}

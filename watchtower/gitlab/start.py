"""Starting work on a work item: a session, in the right folder, already briefed.

This is the whole point of the feature. Everything else on the page is a list;
this is the thing the list is for. What it does is small — resolve the folder,
write the brief, open a terminal — and the only part worth arguing about is the
brief, because that is the first thing the session will read and the only
context it gets for free.
"""

from __future__ import annotations

from watchtower.control import new_session
from watchtower.gitlab.match import folder_for
from watchtower.gitlab.read import item_detail


# The whole brief, however long the item is. A description and eight comments
# capped in read.py can still add up, and the first message of a session is not
# the place to spend a large fraction of a context window.
MAX_BRIEF = 12000


def brief(item: dict) -> str:
    """The work item as the first thing said to a session.

    Written as a briefing rather than as an instruction. It says what the item
    is, quotes it, and stops — deciding what to do about it is the session's
    job, and a prompt that ends in "now implement this" gets an implementation
    of the first paragraph before anybody has looked at the code. The link is in
    there because the description is a copy and the item is the original.
    """
    head = f"# {item.get('reference') or ''} {item.get('title') or ''}".strip()
    lines = ["I'm picking up this GitLab work item.", head]
    facts = []
    if item.get("webUrl"):
        facts.append(item["webUrl"])
    if item.get("labels"):
        facts.append("Labels: " + ", ".join(item["labels"]))
    if item.get("milestone"):
        facts.append(f"Milestone: {item['milestone']}")
    if item.get("author"):
        facts.append(f"Raised by {item['author']}")
    if facts:
        lines.append("\n".join(facts))
    body = (item.get("description") or "").strip()
    lines.append(body if body else "_The item has no description._")
    for note in item.get("notes") or []:
        who = note.get("author") or "someone"
        lines.append(f"**Comment from {who}:**\n{(note.get('body') or '').strip()}")
    lines.append(
        "Please start by getting your bearings in this repository and telling me "
        "what you think the work is, before changing anything.")
    return "\n\n".join(part for part in lines if part)[:MAX_BRIEF]


def start_on(host: str, project: str, iid: int, folder: str = "") -> tuple[bool, str, int]:
    """Open a session on a work item. Returns (ok, message, http status).

    `folder` is not a path from the request — the route only ever passes one the
    matcher found or one the desktop's own chooser returned. Left empty, the
    matcher is asked here, which is the ordinary case: the page shows the folder
    it found on the row before you press anything.
    """
    where = folder or folder_for(host, project)
    if not where:
        return False, ("No checkout of that project on this machine — open it once, "
                       "or pick its folder"), 409
    detail = item_detail(host, project, iid)
    if not detail.get("ok"):
        return False, detail.get("message") or "Could not read that work item", 502
    ok, message = new_session(where, brief(detail["item"]))
    return ok, message, 200 if ok else 409

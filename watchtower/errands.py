"""The processes the panel started itself.

The panel runs a headless Claude of its own for a commit message and for the
plan reading. Those are Claude Code processes on this machine, and without this
they would appear in the panel as sessions to watch — so their pids are noted
while they run and the session scan skips them.
"""

from __future__ import annotations

import threading


# A headless claude writes a session file like any other, so for the twenty
# seconds it runs the panel would list it — a row that appears, says nothing and
# vanishes, for a job the panel itself asked for and is about to throw away.
# Every claude somebody else started still belongs in the list; these are the
# panel's own errands, held by pid only while they run.
OWN_ERRANDS: set[int] = set()


OWN_ERRANDS_LOCK = threading.Lock()


def own_errand(pid: int, running: bool) -> None:
    with OWN_ERRANDS_LOCK:
        OWN_ERRANDS.add(pid) if running else OWN_ERRANDS.discard(pid)


def is_own_errand(pid: int) -> bool:
    with OWN_ERRANDS_LOCK:
        return pid in OWN_ERRANDS

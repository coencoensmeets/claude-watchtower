"""Where the panel looks, how patient it is, and what it is allowed to do.

Two kinds of thing live here, and the difference matters once the panel is more
than one module.

The paths and tunables are constants: a module may import them by name, because
the value never changes after import.

SAY_ENABLED and PLAN_RUNNING are not. They are decided at startup or flipped
while the panel runs, so importing them by name would copy the value at import
time and leave every reader looking at a stale one. Reach them as attributes —
`config.SAY_ENABLED` — so the read happens when it is asked for.
"""

from __future__ import annotations

import os
from pathlib import Path


HOME = Path.home()


# Override to point at a fixture directory when trying out the panel's states.
# CLAUDE_BUSY_UI_SESSION_DIR is the pre-rename name, still honoured.
SESSION_DIR = Path(
    os.environ.get("CLAUDE_WATCHTOWER_SESSION_DIR")
    or os.environ.get("CLAUDE_BUSY_UI_SESSION_DIR")
    or HOME / ".claude" / "sessions"
)


PROJECT_DIR = HOME / ".claude" / "projects"


# What the panel serves: the built frontend, not its sources. server.py builds
# it on the way up when web/ is newer — see watchtower.build.
# The repository root, not this package: config.py sits one level down, and
# `parent` alone silently pointed STATIC_DIR at watchtower/dist.
ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "dist"


PAIR_FILE = HOME / ".config" / "claude-watchtower" / "pairs.json"


# Names you have given sessions yourself, keyed by session id.
NAME_FILE = HOME / ".config" / "claude-watchtower" / "names.json"


MAX_NAME = 80


# Session ids are never reused, so the file would grow forever without a cap.
MAX_NAMES = 500


# Sessions you asked the panel to keep after their process is gone. Only pinned
# rows are written down; the rest are kept in memory for as long as the panel
# runs. See watchtower.rows.
STICKY_FILE = HOME / ".config" / "claude-watchtower" / "sticky.json"


MAX_STICKY = 100


# The in-memory tier has the same cap, for the same reason: a panel left running
# for a month should not accumulate rows without end.
MAX_KEPT = 100


# Sessions the panel is holding open itself, over a pipe of its own. Written
# down so a restarted panel can pick them back up — see watchtower.owned.
OWNED_FILE = HOME / ".config" / "claude-watchtower" / "owned.json"


MAX_OWNED = 200


# How long a state trace remembers, and how often we sample.
HISTORY_SECONDS = 30 * 60


SAMPLE_INTERVAL = 1.0


# How stale a session file may be before the session behind it counts as gone.
# One number for everyone who asks — the row that says "offline", the gate on
# sending, and the deliverer deciding whether to start it back up.
LIVE_SECONDS = 15.0


KNOWN_STATUSES = ("waiting", "busy", "shell", "idle")


# States that mean work is happening right now. Claude Code writes the status
# only when it changes, so an old reading is not proof of anything on its own —
# see effective_status.
ACTIVE_STATUSES = ("busy", "shell")


# Short on purpose. The age check is not what keeps a working session on screen —
# the liveness signals below do that — so this only has to be long enough not to
# flap between two of them.
STATUS_TTL = 15.0


# A transcript that grew this recently counts as a session still at work. Kept
# tight: the last thing a finished turn does is write to the transcript, so a
# long window would hold every session at "working" well past the end of its turn.
TRANSCRIPT_WINDOW = 10.0


# A working session burns a good fraction of a core; an idle one ticks along at
# well under a hundredth of one, so the gap between them is wide.
WORKING_CPU = 0.02


CPU_WINDOW = 5.0


# A working turn is not steady activity. While a request is out to the API, or a
# tool call is blocking on something slow, the process burns almost no CPU and
# appends nothing to its transcript — both liveness signals go quiet mid-turn.
# Read literally, that gap expires the session's `busy` reading and shows a
# working session as "Waiting" for a few seconds until the next append puts it
# back. So a reading of "alive" is remembered for this long after the signals
# fall silent, which is longer than those gaps and still short enough that a
# session whose status went stale for real settles within the minute.
LIVENESS_GRACE = 45.0


# Whether the panel may send input at all. Decided in main() from the bind
# address and --no-send, then read on every request that would act.
SAY_ENABLED = False


# Whether a /usage errand is out right now, so a second poll does not start
# another one behind it.
PLAN_RUNNING = False

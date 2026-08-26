#!/usr/bin/env python3
"""Checks for a panel-run turn: the queue behind it, and stopping it — no server,
no Claude.

A held session takes one turn at a time, and what happens to the second thing
you type is entirely server side: the queue, its order, its cap, the flush the
reader does when a result lands, and what becomes of anything still waiting when
the process goes. None of that is visible to the browser checks, and all of it is
the sort of thing that breaks quietly — a message accepted and then dropped looks
exactly like a message delivered until somebody goes looking for the answer.

So the pieces are driven directly, with a fake process standing in for the pipe:
nothing is started, nothing is sent, and the only thing asserted is what the
panel writes down that pipe and when.

    python3 tests/turn-check.py

A failure prints the case and exits 1.
"""

import io
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from watchtower import input as delivery  # noqa: E402
from watchtower import owned as S  # noqa: E402

FAILED = 0


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


class FakePipe(io.StringIO):
    """A stdin that can be written to and read back, and closed without fuss."""

    def close(self) -> None:  # keep what was written after the reader tidies up
        pass


class FakeProc:
    """Enough of a Popen for the queue to be exercised against it."""

    pid = os.getpid()

    def __init__(self, says: str = "") -> None:
        self.stdin = FakePipe()
        self.stdout = io.StringIO(says)
        self.stderr = None
        self.gone = False

    def poll(self):
        return 0 if self.gone else None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.gone = True


def hold(session_id: str, says: str = "") -> FakeProc:
    """Put a fake held session in the table, mid-turn, and hand back its pipe."""
    proc = FakeProc(says)
    held = {"proc": proc, "write": threading.Lock(), "mode": "plan", "cwd": "/tmp",
            "since": time.time(), "id": session_id, "named": threading.Event()}
    held["named"].set()
    with S._OWNED_LOCK:
        S.OWNED_PROCS[session_id] = held
        S.OWNED_BUSY[session_id] = time.time()
        S.OWNED_QUEUE.pop(session_id, None)
    proc.held = held
    return proc


def written(proc: FakeProc) -> list[dict]:
    return [json.loads(line) for line in proc.stdin.getvalue().splitlines() if line.strip()]


def texts(proc: FakeProc) -> list[str]:
    return [f["message"]["content"][0]["text"] for f in written(proc)
            if f.get("type") == "user"]


# Nothing here may start a process, open a terminal or hand anything to the
# deliverer, so the one function that would is stubbed for the whole run.
DELIVERED: list[tuple[str, str]] = []
# It is looked up on watchtower.input at the moment it is used — owned imports
# it there rather than at the top, so the two do not make a cycle — which is
# exactly where this has to be replaced for the stub to be the one that runs.
delivery.deliver_later = lambda session_id, text, started=False: (
    DELIVERED.append((session_id, text)) or (True, "held"))


# --------------------------------------------------------------- the queue
# The case the whole thing is for: a second message typed while it is answering.
# This used to come back "It is still answering the last one" — a refusal that
# made the panel's timing the typist's problem — and now it is held.
proc = hold("a")
ok, said = S.owned_say("a", "/tmp", "and then the tests", "plan")
check("a message typed mid-turn is taken, not refused", ok, said)
check("and nothing of it goes down the pipe yet", texts(proc) == [], str(texts(proc)))
check("it says when it goes in", "the turn ends" in said, said)
ok, said = S.owned_say("a", "/tmp", "and push it", "plan")
check("a third says where it landed", ok and "2nd" in said, said)
check("the queue is in the order it was typed",
      S.owned_queued("a") == ["and then the tests", "and push it"], str(S.owned_queued("a")))
check("and the feed carries it", S.owned_state()["a"]["queued"] == S.owned_queued("a"))

# --------------------------------------------------------------- taking it back
check("a message that has already gone in cannot be taken back",
      S.owned_unqueue("a", 7)[0] is False)
check("one that is still waiting can", S.owned_unqueue("a", 0)[0] is True)
check("and only that one goes", S.owned_queued("a") == ["and push it"], str(S.owned_queued("a")))
check("dropping them all leaves nothing", S.owned_unqueue("a", None)[0] is True
      and S.owned_queued("a") == [])
check("and there is nothing to drop twice", S.owned_unqueue("a", None)[0] is False)

# --------------------------------------------------------------- the cap
proc = hold("b")
taken = [S.owned_say("b", "/tmp", f"message {i}", "plan")[0] for i in range(S.MAX_QUEUED + 2)]
check(f"the queue stops at {S.MAX_QUEUED}", taken.count(True) == S.MAX_QUEUED,
      f"{taken.count(True)} taken")
check("and says so rather than dropping it quietly",
      "already" in S.owned_say("b", "/tmp", "one more", "plan")[1])

# ------------------------------------------------------- the turn ending
# The flush: one message goes in per turn that ends, in order, and each waits for
# the result of the one before it exactly as it would have if you had waited.
proc = hold("c")
S.owned_say("c", "/tmp", "first ahead", "plan")
S.owned_say("c", "/tmp", "second ahead", "plan")
with S._OWNED_LOCK:
    S.OWNED_BUSY.pop("c")
S.owned_flush("c")
check("the end of a turn sends the next one", texts(proc) == ["first ahead"], str(texts(proc)))
check("which is a turn of its own", "c" in S.OWNED_BUSY)
check("and the one behind it waits", S.owned_queued("c") == ["second ahead"])
S.owned_flush("c")
check("a flush while it is answering sends nothing",
      texts(proc) == ["first ahead"], str(texts(proc)))
with S._OWNED_LOCK:
    S.OWNED_BUSY.pop("c")
S.owned_flush("c")
check("the next end of a turn sends the last one",
      texts(proc) == ["first ahead", "second ahead"], str(texts(proc)))
with S._OWNED_LOCK:
    S.OWNED_BUSY.pop("c")
S.owned_flush("c")
check("and an empty queue is a no-op", "c" not in S.OWNED_BUSY and not S.owned_queued("c"))

# The gap between a turn ending and the queue draining is an instant in which
# nothing is running, and a message sent into it must not overtake one typed
# before it: the back of the queue is the only place either can go.
proc = hold("g")
S.owned_say("g", "/tmp", "typed first", "plan")
with S._OWNED_LOCK:                     # the turn ends, and nothing has drained yet
    S.OWNED_BUSY.pop("g")
S.owned_say("g", "/tmp", "typed second", "plan")
check("a message sent between turns does not overtake one already waiting",
      texts(proc) == ["typed first"], str(texts(proc)))
check("and waits its own turn behind it", S.owned_queued("g") == ["typed second"],
      str(S.owned_queued("g")))

# --------------------------------------- the reader, which is what calls it
# The same thing through the real reader: a result frame is what ends a turn, and
# the queued message must be written by the thread that saw it rather than by a
# poll from a browser that may not be open.
proc = hold("d", says=json.dumps({"type": "result", "total_cost_usd": 0.01}) + "\n")
S.owned_say("d", "/tmp", "typed while it worked", "plan")
S.owned_reader("d", proc.held)
check("a result frame drains the queue itself",
      texts(proc) == ["typed while it worked"], str(texts(proc)))

# ------------------------------------------- when the process goes instead
# Whatever is still waiting outlives the process it was waiting for: the promise
# was that it goes in, and the deliverer is what keeps it.
proc = hold("e", says="")
S.owned_say("e", "/tmp", "still waiting", "plan")
DELIVERED.clear()
S.owned_reader("e", proc.held)          # no result, no more output: it ended
check("a session that dies hands what was waiting to the deliverer",
      DELIVERED == [("e", "still waiting")], str(DELIVERED))
check("and holds none of it itself", not S.owned_queued("e"))

# Letting go deliberately is the other way a held session ends, and it must not
# start the session back up to deliver messages the person let go of.
proc = hold("f")
S.owned_say("f", "/tmp", "never mind", "plan")
DELIVERED.clear()
S.owned_release("f")
check("letting go of a session drops what was queued for it",
      not S.owned_queued("f") and DELIVERED == [], str(DELIVERED))

# ----------------------------------------------------- stopping a turn
# One control_request down the same channel the mode is set on. What the panel
# has to get right around it is the two things that are not the frame: what
# becomes of anything typed ahead, and how the turn's own result is read
# afterwards, since an interrupted turn reports itself as an error.
proc = hold("h")
S.owned_say("h", "/tmp", "and then this", "plan")
ok, said = S.owned_interrupt("h")
check("a working session of ours can be stopped", ok, said)
frames = [f for f in written(proc) if f.get("type") == "control_request"]
check("by one interrupt on the control channel",
      len(frames) == 1 and frames[0]["request"]["subtype"] == "interrupt", str(frames))
check("what was typed ahead survives the thing it was typed behind",
      S.owned_queued("h") == ["and then this"], str(S.owned_queued("h")))
check("and it says it is holding it rather than sending or dropping it",
      "held, not sent" in said, said)
check("a session that is not working has nothing to stop",
      S.owned_interrupt("i")[0] is False)
with S._OWNED_LOCK:
    S.OWNED_BUSY.pop("h")
check("nor has one of ours that is between turns", S.owned_interrupt("h")[0] is False)

# The held queue, and the three ways out of it. The turn it was waiting for has
# now ended — that is the state above — so anything that goes in from here is
# something the panel decided to send on its own, which is the failure this
# whole arrangement exists to prevent.
S.owned_flush("h")
check("the end of the stopped turn does not release it",
      [f for f in written(proc) if f.get("type") == "user"] == [], "something went in")
check("the panel says it is held, so the strip can ask what to do with it",
      S.owned_state()["h"]["queueHeld"] is True)
ok, said = S.owned_resume("h")
check("sending it is a deliberate act, and then it goes", ok and said.startswith("Sending"), said)
sent = [f["message"]["content"][0]["text"] for f in written(proc) if f.get("type") == "user"]
check("in the order it was written", sent == ["and then this"], str(sent))
check("and it is an ordinary queue again afterwards",
      S.owned_state().get("h", {}).get("queueHeld", False) is False)

# Dropping instead, and typing instead — the other two ways out.
proc = hold("q1")
S.owned_say("q1", "/tmp", "stale thought", "plan")
S.owned_interrupt("q1")
S.owned_unqueue("q1", None)
check("dropping a held queue takes the hold with it",
      S.owned_queued("q1") == [] and "q1" not in S.OWNED_HELD)

proc = hold("q2")
S.owned_say("q2", "/tmp", "stale thought", "plan")
S.owned_interrupt("q2")
with S._OWNED_LOCK:
    S.OWNED_BUSY.pop("q2")
S.owned_say("q2", "/tmp", "what I actually want", "plan")
sent = [f["message"]["content"][0]["text"] for f in written(proc) if f.get("type") == "user"]
check("typing something else releases it too — sending is how you say carry on",
      sent == ["stale thought"], str(sent))
check("and what you typed goes in last, which is the order you wrote them in",
      S.owned_queued("q2") == ["what I actually want"], str(S.owned_queued("q2")))

# The result of a stopped turn: `is_error` with `error_during_execution`, which is
# the turn doing what it was told rather than going wrong. Read as an error it
# painted the row's last-turn line red and said `error_during_execution` at
# somebody who had just pressed Stop.
proc = hold("j", says=json.dumps({"type": "result", "is_error": True,
                                  "subtype": "error_during_execution"}) + "\n")
S.owned_interrupt("j")
S.owned_reader("j", proc.held)
last = S.OWNED_LAST.get("j") or {}
check("a stopped turn is not reported as a failure", last.get("ok") is True, str(last))
check("and says who stopped it", last.get("message") == "You stopped it", str(last))

# A turn that really did fail still reads as a failure.
proc = hold("k", says=json.dumps({"type": "result", "is_error": True,
                                  "subtype": "error_during_execution"}) + "\n")
S.owned_reader("k", proc.held)
check("a turn nobody stopped still reads as one that went wrong",
      (S.OWNED_LAST.get("k") or {}).get("ok") is False, str(S.OWNED_LAST.get("k")))

print()
print("all ok" if not FAILED else f"{FAILED} failed")
sys.exit(1 if FAILED else 0)

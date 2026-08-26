"""Turns the panel runs itself, on sessions nothing else is holding.

The one thing a terminal keeps to itself is the flags a turn starts under, so
the panel runs a turn of its own down a pipe it holds: `claude --print
--input-format stream-json` on an existing transcript, alive between turns.

Two imports are deliberately not at the top. `STORE` and `deliver_later` both
sit above this module — the store reads what is running here to draw a row, and
the deliverer sends a message through here when the panel is what holds the
session — so they are imported where they are used rather than making a cycle
out of a one-line call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from watchtower.config import MAX_OWNED, OWNED_FILE
from watchtower.control import top_level_env
from watchtower.errands import own_errand
from watchtower.rows import keep_row, kept_rows, load_pinned
from watchtower.transcript import has_conversation


# The one thing a terminal keeps to itself is the flags a turn starts under. So
# the panel runs a turn of its own: `claude --print --resume <id>` picks up the
# conversation that is already there, appends to the same transcript, and exits.
#
# That is the whole trick, and it is why this is small. The permission mode is
# an argument, so changing it is changing what the next turn is launched with —
# no process to signal, nothing to restart, nothing to take over, and no state
# to keep in step beyond a remembered word. Switching is instant because there
# is nothing to switch: the choice is not applied until a turn is launched, and
# a turn is launched from scratch every time.
#
# What this deliberately does not do is take a session away from a terminal.
# Two processes appending to one transcript is the failure this area is littered
# with, so a turn only ever runs for a session no live process is holding, and
# only one at a time. A session in a terminal is not eligible and is not made
# eligible by force: end it first, which is a button that already exists.

# What the panel will launch a turn in, keyed the way the transcript reports it
# — `default`, translated to `manual` at the argv boundary, which is what the
# CLI actually accepts.
#
# `bypassPermissions` and `dontAsk` are still not offered: they never ask
# anybody, so a panel that can answer prompts has nothing to answer and no way
# to know what was done in its name.
#
# `auto` is offered, because it is Claude Code's own default and leaving it out
# meant the panel could not run a session the way its terminal already does. It
# is worth knowing what it is, though: a classifier decides what needs approval,
# and what it approves never reaches the panel at all — in testing it ran
# `rm -rf` with no prompt raised. The three others either ask or hold back.
#
# Asking is no longer a dead end in any of them: a turn here is launched with
# `--permission-prompt-tool stdio`, so a prompt arrives in the panel and the
# tool waits. What no mode changes is that Claude Code judges some commands safe
# enough not to ask about at all, and those still run.
OWNED_MODES = ("default", "auto", "plan", "acceptEdits")
CLI_MODES = {"default": "manual"}
# A turn is a whole conversation turn, which can be minutes. Long, not endless.
OWNED_SECONDS = 1800.0

# How long a prompt raised by a panel turn waits for somebody to answer it in the
# panel. Long, because the answer is a person noticing; not endless, because a
# turn holding a pipe open forever is a process nobody will ever reap.
OWNED_ASK_SECONDS = 600.0

_OWNED_LOCK = threading.Lock()
# sessionId -> the prompt standing in front of that turn, as the panel shows it.
# A turn holds its pipe open while this is set: the tool has not run, and will
# not, until an answer comes back down it.
OWNED_ASK: dict[str, dict] = {}
# sessionId -> the answer given, and the event the waiting turn is parked on.
_ASK_ANSWER: dict[str, dict] = {}
_ASK_EVENTS: dict[str, threading.Event] = {}
# sessionId -> when its turn started. Also the guard against a second turn, and
# against a terminal being opened on the transcript underneath a running one.
OWNED_BUSY: dict[str, float] = {}
# sessionId -> how the last one went, so the panel can say so after the fact.
# A turn is launched and let go of, so this is the only place its outcome lands.
OWNED_LAST: dict[str, dict] = {}
# sessionId -> what was typed at it while it was answering, in the order it was
# typed. One turn runs at a time down a held pipe — the transcript is being
# written by the turn in flight, and a second turn written into it is the
# two-turns-one-conversation failure the whole area is built to avoid — so the
# panel does here what the terminal's own prompt does when you type ahead of it:
# it holds the message and sends it the instant the turn ends.
#
# *It is still answering the last one* was the alternative, and it was the answer
# to a question nobody asked. Nothing about the panel's timing is the typist's
# business: the message was written, it is going in, and being told to come back
# in four minutes and press Send again is work the panel can do itself.
OWNED_QUEUE: dict[str, list[str]] = {}
# sessionId -> when the queue behind it was held back. Stopping a turn used to
# throw the queue away, on the reasoning that a train of thought you have just
# stopped should not carry on regardless. Half of that was right: what must not
# happen is the next message going in a tenth of a second after you pressed
# Stop. Throwing away what you typed was the wrong half — it is your writing,
# sometimes several minutes of it, and the panel deleting it is not the panel's
# call to make. So it is kept and held instead, and the strip that shows it asks
# what you want done with it.
OWNED_HELD: dict[str, float] = {}
# sessionId -> when it was told to stop. A turn ends in a result frame either way,
# and this is what tells one that was stopped from one that failed: an interrupted
# turn comes back `is_error` with `error_during_execution`, which read as the turn
# having gone wrong when in fact it did exactly what it was told.
OWNED_STOPPING: dict[str, float] = {}
# sessionId -> what compaction is doing, or how the last one went. A compaction
# is a turn like any other from the pipe's point of view, but its `result` frame
# carries an empty string, so on the outcome machinery alone it reads as a turn
# that finished and said nothing. What it actually did arrives in three `system`
# frames, which is what this records:
#
#   {"subtype":"status","status":"compacting"}                      it started
#   {"subtype":"status","status":null,"compact_result":"success"}   it worked
#   {"subtype":"compact_boundary","compact_metadata":{...}}         by how much
#
# `compact_metadata` carries `pre_tokens` and `post_tokens`, which is the only
# honest way to say what was gained — the panel's own context reading comes off
# the *next* assistant turn, so it does not move until the session is used again.
OWNED_COMPACT: dict[str, dict] = {}
# sessionId -> what it says it can be asked for: `available`, and `terminalOnly`,
# the ones it keeps for its own prompt. Straight off its `init` frame, because a
# held pipe and a messaging socket do not agree about slash commands and only the
# session knows which it is. Empty until it has spoken once.
OWNED_COMMANDS: dict[str, dict] = {}
# Bounded, because a queue with no end is a session answering questions you
# stopped caring about ten turns ago. Past this the message is refused, which is
# the one place a refusal says something true: you have typed more than you can
# have meant for one conversation to answer in order.
MAX_QUEUED = 10


def load_owned() -> dict[str, dict]:
    """sessionId -> {mode, here} for the sessions the panel runs turns for.

    `here` says the panel was handed this one deliberately — it ended the
    terminal session to take it — as against a mode merely picked for a session
    in passing. It is what stops an adopted row reading as a dead one: nothing
    is running, which is normal between turns, and the row should say which of
    those two it is.

    An older file holds a bare mode string per id, and is read as one.
    """
    try:
        data = json.loads(OWNED_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(value, str):
            value = {"mode": value}
        if not isinstance(value, dict):
            continue
        mode = str(value.get("mode") or OWNED_MODES[0])
        out[str(key)] = {"mode": mode if mode in OWNED_MODES else OWNED_MODES[0],
                         "here": bool(value.get("here"))}
    return out


def save_owned(owned: dict[str, dict]) -> None:
    if len(owned) > MAX_OWNED:
        owned = dict(list(owned.items())[-MAX_OWNED:])
    OWNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    OWNED_FILE.write_text(json.dumps(owned, indent=2))


def owned_state() -> dict[str, dict]:
    """What the panel knows about turns it runs, per session, for the feed."""
    kept = load_owned()
    with _OWNED_LOCK:
        busy = dict(OWNED_BUSY)
        last = {k: dict(v) for k, v in OWNED_LAST.items()}
        asking = {k: dict(v) for k, v in OWNED_ASK.items()}
        waiting = {k: list(v) for k, v in OWNED_QUEUE.items() if v}
        held = set(OWNED_HELD)
        stopping = set(OWNED_STOPPING)
        compacting = {k: dict(v) for k, v in OWNED_COMPACT.items()}
        commands = {k: dict(v) for k, v in OWNED_COMMANDS.items()}
    out: dict[str, dict] = {}
    for session_id in (set(kept) | set(busy) | set(last) | set(asking) | set(waiting)
                       | set(compacting) | set(commands)):
        out[session_id] = {
            "mode": (kept.get(session_id) or {}).get("mode", OWNED_MODES[0]),
            # Adopted, as against merely having had a mode picked for it.
            "here": bool((kept.get(session_id) or {}).get("here")),
            "busy": session_id in busy,
            "since": busy.get(session_id),
            "last": last.get(session_id),
            "ask": asking.get(session_id),
            # What is standing behind the turn in flight. Sent in full rather
            # than counted: a queue you cannot read is a queue you cannot decide
            # to drop something from, and dropping is the whole point of seeing
            # it. The panel is loopback-only and shows the conversation itself,
            # so there is nothing here it is not already showing.
            "queued": waiting.get(session_id) or [],
            # And whether the queue is going anywhere on its own. Held is what a
            # queue is after you stop the turn in front of it: still there, still
            # yours, and waiting to be told to go rather than going.
            "queueHeld": session_id in held and bool(waiting.get(session_id)),
            # Told to stop, and not yet come back saying it has. The button it
            # was pressed on says so for that moment rather than sitting there
            # looking unpressed.
            "stopping": session_id in stopping,
            # What compaction is doing, or what the last one did. Distinct from
            # `last` because a compaction's own result frame is empty — see
            # OWNED_COMPACT.
            "compact": compacting.get(session_id),
            # What this session says it takes, for the composer's /-picker. Null
            # until it has said — the panel's own list stands in until then.
            "commands": commands.get(session_id),
            # Held open, i.e. a process is there between turns — which is what
            # makes the row a running session rather than one that wakes up.
            "running": owned_running(session_id),
        }
    return out


# A session the panel holds open. One `claude --print --input-format stream-json`
# per adopted session, alive between turns rather than spawned for each: it
# serves turn after turn down the same pipe, which is what makes the session
# *running* rather than a row that wakes up when poked.
#
# Holding it is also what makes the mode a live setting. `set_permission_mode`
# is refused on the messaging socket because its callback is not registered
# there — but this transport owns the session's stdio, where it is registered,
# and it answers `{"subtype":"success","response":{"mode":"acceptEdits"}}` and
# takes effect on the next tool. That is the thing this whole area was opened to
# find, and it only works while something holds the pipe.
OWNED_PROCS: dict[str, dict] = {}


def owned_write(session_id: str, frame: dict) -> bool:
    """One JSON line into a held session's stdin, or False if it is not there."""
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
    if not held or held["proc"].poll() is not None:
        return False
    try:
        with held["write"]:
            held["proc"].stdin.write(json.dumps(frame) + "\n")
            held["proc"].stdin.flush()
        return True
    except (OSError, ValueError):
        return False


def owned_running(session_id: str) -> bool:
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
    return bool(held) and held["proc"].poll() is None


def ask_from_panel(session_id: str, request: dict) -> dict:
    """Park a turn on the prompt it raised and hand back the answer to send.

    This is the whole of what the control channel buys. `--permission-prompt-tool
    stdio` makes Claude Code ask over the pipe the panel is holding, so the
    question arrives here as a can_use_tool control_request and the tool does not
    run until a control_response goes back. A permission prompt and a
    multiple-choice question come up this same channel; `requires_user_interaction`
    is what tells them apart, and a question's answers ride back inside the input
    it was going to use.

    Nobody answering is the case worth being careful about: a turn parked forever
    holds a pipe and a process. So the wait has a deadline, and running out is a
    refusal — the safe direction, and one the turn can explain.
    """
    event = threading.Event()
    standing = {
        "requestId": str(request.get("request_id") or ""),
        "tool": str(request.get("tool_name") or "a tool"),
        "name": str(request.get("display_name") or request.get("tool_name") or "a tool"),
        "what": str(request.get("description") or "")[:300],
        "input": request.get("input") if isinstance(request.get("input"), dict) else {},
        # A question rather than a gate: the panel draws options instead of
        # allow-and-deny, and answering it *is* allowing it.
        "asks": bool(request.get("requires_user_interaction")),
        "at": time.time(),
        "seconds": OWNED_ASK_SECONDS,
    }
    with _OWNED_LOCK:
        OWNED_ASK[session_id] = standing
        _ASK_EVENTS[session_id] = event
        _ASK_ANSWER.pop(session_id, None)
    try:
        answered = event.wait(OWNED_ASK_SECONDS)
        with _OWNED_LOCK:
            given = _ASK_ANSWER.pop(session_id, None)
    finally:
        with _OWNED_LOCK:
            OWNED_ASK.pop(session_id, None)
            _ASK_EVENTS.pop(session_id, None)
    if not answered or not given:
        return {"behavior": "deny", "message": "Nobody answered this in the panel, so it was refused"}
    if given.get("behavior") != "allow":
        return {"behavior": "deny", "message": str(given.get("message") or "Refused from the panel")}
    # Allowing runs the tool on the input it asked with. A question is allowed the
    # same way, with the answers written into that input: `answers` is keyed by
    # the question's own text, and several picks join on ", ".
    used = dict(standing["input"])
    picks = given.get("answers")
    if isinstance(picks, dict) and isinstance(used.get("questions"), list):
        answers = {}
        for question in used["questions"]:
            if not isinstance(question, dict):
                continue
            chosen = picks.get(str(question.get("question") or ""))
            if isinstance(chosen, list):
                chosen = ", ".join(str(c) for c in chosen if str(c).strip())
            chosen = str(chosen or "").strip()
            if chosen:
                answers[question["question"]] = chosen
        if answers:
            used["answers"] = answers
    return {"behavior": "allow", "updatedInput": used}


def answer_from_panel(session_id: str, request_id: str, decision: dict) -> tuple[bool, str]:
    """Hand an answer to the turn parked on a prompt."""
    with _OWNED_LOCK:
        standing = OWNED_ASK.get(session_id)
        event = _ASK_EVENTS.get(session_id)
        if not standing or not event:
            return False, "Nothing here is waiting to be answered"
        # The id has to match. A panel repainted between two prompts would
        # otherwise answer the second with what was clicked for the first.
        if request_id and request_id != standing["requestId"]:
            return False, "That answer was for a prompt that has already gone"
        _ASK_ANSWER[session_id] = decision
        event.set()
    allowed = decision.get("behavior") == "allow"
    if standing["asks"] and allowed:
        return True, "Answered"
    return True, "Allowed" if allowed else "Refused"


def owned_name_itself(held: dict, said: str) -> None:
    """Register a session the panel started, the moment it says what it is.

    A session that does not exist yet cannot be resumed and has no id to be
    keyed by: `claude --print --input-format stream-json` with no `--resume`
    writes nothing and announces nothing until it is sent something, and only
    then says which session it has become. So the process is started first and
    identified afterwards, out of its own first frames.
    """
    held["id"] = said
    with _OWNED_LOCK:
        OWNED_PROCS[said] = held
        # Its first turn is already running — it was sent something to make it
        # exist at all.
        OWNED_BUSY[said] = time.time()
    if said not in kept_rows():
        cwd = held.get("cwd") or ""
        keep_row({
            "sessionId": said, "name": held.get("name") or os.path.basename(cwd) or "new session",
            "cwd": cwd, "startedAt": time.time(), "lastSeen": time.time(),
            "version": None, "kind": "interactive",
        })
    owned = load_owned()
    owned[said] = {"mode": held.get("mode") or OWNED_MODES[0], "here": True}
    save_owned(owned)
    held["named"].set()


def user_turn(text: str) -> dict:
    """One typed message, as the stream-json input format wants it."""
    return {"type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def ordinal(n: int) -> str:
    """2 -> "2nd". For saying where in the queue a message landed."""
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def owned_queue_add(session_id: str, text: str) -> tuple[bool, str]:
    """Hold a message behind the turn in flight, and say where it landed.

    Called once the caller has established that it cannot go now — a turn is
    running, or something is already waiting for one. It takes the lock itself
    rather than being called under it, so there is one place that knows how the
    queue is guarded.
    """
    with _OWNED_LOCK:
        queue = OWNED_QUEUE.setdefault(session_id, [])
        if len(queue) >= MAX_QUEUED:
            return False, (f"There are already {MAX_QUEUED} messages waiting behind this "
                           "turn — let it get through some of them first")
        queue.append(text)
        place = len(queue)
    return True, ("Held for it — this goes in the moment the turn ends" if place == 1
                  else f"Held for it — {ordinal(place)} in the queue behind this turn")


def owned_queued(session_id: str) -> list[str]:
    with _OWNED_LOCK:
        return list(OWNED_QUEUE.get(session_id) or [])


def owned_unqueue(session_id: str, index: int | None) -> tuple[bool, str]:
    """Take something back out of the queue before the turn reaches it.

    A message typed three turns ago can be answered by the turn that was running
    when you typed it, and then it is worse than unsent. Nothing can be taken
    back once it has gone down the pipe, so this only ever touches what is still
    waiting — and it says which of the two happened rather than reporting success
    for a message that has already been asked.
    """
    with _OWNED_LOCK:
        queue = OWNED_QUEUE.get(session_id) or []
        if not queue:
            return False, "Nothing is waiting behind this turn"
        if index is None:
            OWNED_QUEUE.pop(session_id, None)
            OWNED_HELD.pop(session_id, None)
            return True, ("Dropped the message that was waiting" if len(queue) == 1
                          else f"Dropped the {len(queue)} messages that were waiting")
        if not 0 <= index < len(queue):
            return False, "That message has already gone in"
        queue.pop(index)
        if not queue:
            OWNED_QUEUE.pop(session_id, None)
            OWNED_HELD.pop(session_id, None)
    return True, "Dropped it"


def owned_flush(session_id: str) -> None:
    """Send what was typed ahead, now that the turn it was typed behind is done.

    Called from the reader the instant a result lands, which is the instant the
    terminal's own prompt would have taken it. One at a time and in order: each
    queued message waits for the result of the one before it, exactly as it would
    have if you had waited yourself before pressing Send.

    A pipe that has gone in the meantime is not this function's problem to
    report — nobody is waiting on it — so the message goes back to the deliverer,
    which starts the session up and puts it in there. That is the same promise
    the composer made when it took it.
    """
    with _OWNED_LOCK:
        if session_id in OWNED_BUSY:
            return
        # Held by a stop. The turn that was in front of the queue has ended —
        # that is what called this — but ending it is what you asked for, and
        # sending the next message on the strength of it is exactly the thing
        # the stop was meant to prevent. It goes when you say, see owned_resume.
        if session_id in OWNED_HELD:
            return
        queue = OWNED_QUEUE.get(session_id) or []
        if not queue:
            OWNED_QUEUE.pop(session_id, None)
            return
        text = queue.pop(0)
        if not queue:
            OWNED_QUEUE.pop(session_id, None)
        OWNED_BUSY[session_id] = time.time()
    if owned_write(session_id, user_turn(text)):
        return
    from watchtower.input import deliver_later

    with _OWNED_LOCK:
        OWNED_BUSY.pop(session_id, None)
    deliver_later(session_id, text)


def owned_interrupt(session_id: str) -> tuple[bool, str]:
    """Stop the turn a held session is in the middle of.

    `{"subtype": "interrupt"}` down the same control channel the mode is set on,
    which is the whole of it. Verified against a held session: the request is
    answered `{"subtype": "success", "response": {"still_queued": []}}`, the
    transcript gains a `[Request interrupted by user]` turn where Claude Code
    stopped, the turn ends `error_during_execution`, and the process stays up and
    takes the next turn normally. Nothing is killed and nothing is restarted.

    What is typed ahead is kept, and held. Delivering it a tenth of a second
    later — into a session that is now waiting for you to say what you actually
    want — is the opposite of what stopping meant, so it does not go in on its
    own. But it is not thrown away either: it is minutes of your writing, and it
    is still there in the strip with *Send them* and *Drop them* beside it. Which
    of those you want is not something the panel can work out for you.
    """
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
        busy = session_id in OWNED_BUSY
    if not held:
        return False, "The panel is not running that session"
    if not busy:
        return False, "It is not working on anything"
    if not owned_write(session_id, {
            "type": "control_request", "request_id": f"interrupt-{uuid.uuid4()}",
            "request": {"subtype": "interrupt"}}):
        return False, "It would not take the interrupt — the pipe has gone"
    with _OWNED_LOCK:
        OWNED_STOPPING[session_id] = time.time()
        kept = len(OWNED_QUEUE.get(session_id) or [])
        if kept:
            OWNED_HELD[session_id] = time.time()
    if not kept:
        return True, "Stopping it"
    return True, ("Stopping it — the message waiting behind it is held, not sent"
                  if kept == 1 else
                  f"Stopping it — the {kept} messages waiting behind it are held, not sent")


def owned_resume(session_id: str) -> tuple[bool, str]:
    """Let a held queue go after all — the *Send them* on the strip.

    The one thing this does not do is decide anything: it takes the hold off and
    hands over to the same flush that would have run when the turn ended, so a
    queue released here goes in one at a time and in order, exactly as a queue
    that was never stopped does.
    """
    with _OWNED_LOCK:
        queue = list(OWNED_QUEUE.get(session_id) or [])
        if not queue:
            OWNED_HELD.pop(session_id, None)
            return False, "Nothing is waiting"
        if session_id not in OWNED_HELD:
            return False, "It is already on its way in"
        if not OWNED_PROCS.get(session_id):
            return False, "The panel is not running that session"
        OWNED_HELD.pop(session_id, None)
    owned_flush(session_id)
    return True, ("Sending it" if len(queue) == 1
                  else f"Sending them — {len(queue)}, in the order you typed them")


def owned_reader(session_id: str, held: dict) -> None:
    """Everything the held session says, for as long as it is held.

    `session_id` is what it was known as at the start, which for a session the
    panel has just created is nothing at all — so the key is read off the held
    record each time, and is filled in as soon as the session names itself.
    """
    proc = held["proc"]
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            kind = frame.get("type")
            # A session the panel started says which one it is in its first
            # frames, and nothing can be filed under it until it has.
            if held.get("id") is None and isinstance(frame.get("session_id"), str):
                owned_name_itself(held, frame["session_id"])
            session_id = held.get("id") or session_id
            if not session_id:
                continue
            if kind == "control_request":
                request = frame.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    continue
                # The answer waits on a person, so it cannot be waited for here:
                # this thread is the only reader, and the session goes on talking.
                def settle(request=request, request_id=frame.get("request_id")) -> None:
                    reply = ask_from_panel(session_id, {**request, "request_id": request_id})
                    owned_write(session_id, {
                        "type": "control_response",
                        "response": {"subtype": "success", "request_id": request_id,
                                     "response": reply},
                    })
                threading.Thread(target=settle, daemon=True).start()
                continue
            if kind == "result":
                cost = frame.get("total_cost_usd")
                with _OWNED_LOCK:
                    stopped = OWNED_STOPPING.pop(session_id, None) is not None
                # A stopped turn reports itself as an error, and it is not one:
                # it did what it was told. Said plainly, and not in the red that
                # the row keeps for a turn that actually went wrong.
                if stopped:
                    ok, message = True, "You stopped it"
                elif frame.get("is_error"):
                    said = frame.get("errors")
                    said = "; ".join(str(e) for e in said) if isinstance(said, list) and said else ""
                    ok, message = False, (said or str(frame.get("result") or "")
                                          or str(frame.get("subtype") or "It reported an error"))[:300]
                else:
                    ok = True
                    message = (f"The turn finished — ${cost:.4f}"
                               if isinstance(cost, (int, float)) else "The turn finished")
                with _OWNED_LOCK:
                    OWNED_BUSY.pop(session_id, None)
                    OWNED_LAST[session_id] = {"at": time.time(), "ok": ok,
                                              "message": message, "mode": held.get("mode")}
                # And straight into whatever was typed while it was answering.
                # Here rather than on the poll: the queue is drained by the thing
                # that knows the turn ended, so nothing waits a second for a
                # browser to notice, and a panel with no browser open drains too.
                owned_flush(session_id)
                continue
            if kind == "system":
                # What compaction is doing. See OWNED_COMPACT for the three
                # frames and why the turn's own result frame cannot say it.
                sub = frame.get("subtype")
                if sub == "init":
                    # What this session says it can be asked for, in its own
                    # words. The panel keeps a hand-written list of commands that
                    # live in the terminal's head (TERMINAL_ONLY) and it is right
                    # about the *messaging socket*, where slash commands are
                    # switched off entirely — but it is the wrong list for a held
                    # pipe, which expands them. The session settles it: an init
                    # frame down this transport reports 47 commands available and
                    # exactly two it keeps to itself.
                    #
                    #   "terminal_slash_commands": ["doctor", "color"]
                    #
                    # Read rather than guessed, and re-read on every init — a
                    # compaction sends a fresh one, and so does a new plugin.
                    with _OWNED_LOCK:
                        OWNED_COMMANDS[session_id] = {
                            "available": sorted(
                                str(x) for x in (frame.get("slash_commands") or [])
                                if isinstance(x, str)),
                            "terminalOnly": sorted(
                                str(x) for x in (frame.get("terminal_slash_commands") or [])
                                if isinstance(x, str)),
                        }
                if sub == "status" and frame.get("status") == "compacting":
                    with _OWNED_LOCK:
                        OWNED_COMPACT[session_id] = {"at": time.time(), "running": True}
                elif sub == "status" and (frame.get("compact_result")
                                          or frame.get("compact_error")):
                    failed = frame.get("compact_error")
                    with _OWNED_LOCK:
                        OWNED_COMPACT[session_id] = {
                            **(OWNED_COMPACT.get(session_id) or {}),
                            "at": time.time(), "running": False, "ok": not failed,
                            "message": str(failed)[:300] if failed else "",
                        }
                elif sub == "compact_boundary":
                    meta = frame.get("compact_metadata") or {}
                    with _OWNED_LOCK:
                        OWNED_COMPACT[session_id] = {
                            **(OWNED_COMPACT.get(session_id) or {}),
                            "at": time.time(), "running": False, "ok": True, "message": "",
                            "before": int(meta.get("pre_tokens") or 0),
                            "after": int(meta.get("post_tokens") or 0),
                            # `manual` is ours; `auto` is Claude Code compacting
                            # on its own, which the panel did not ask for and
                            # should still report rather than pretend to own.
                            "trigger": str(meta.get("trigger") or "manual"),
                        }
                continue
            if kind == "control_response":
                # Ours: the only control_request the panel sends unprompted is a
                # mode change, and its answer is what confirms the mode took.
                answered = (frame.get("response") or {}).get("response") or {}
                if isinstance(answered.get("mode"), str):
                    with _OWNED_LOCK:
                        held["mode"] = answered["mode"]
    except (OSError, ValueError):
        pass
    finally:
        # However it ended, it is no longer held.
        session_id = held.get("id") or session_id
        held["named"].set()
        with _OWNED_LOCK:
            if session_id and OWNED_PROCS.get(session_id) is held:
                OWNED_PROCS.pop(session_id, None)
            OWNED_BUSY.pop(session_id, None)
            OWNED_STOPPING.pop(session_id, None)
            # A compaction this process did not live to finish. The record stays
            # — how the last one went is worth keeping — but the running flag
            # cannot: nothing is coming to clear it, and the panel draws a
            # session with that flag up as *Compacting*, which it would then go
            # on saying about a session that is not running at all.
            going = OWNED_COMPACT.get(session_id) if session_id else None
            if going and going.get("running"):
                OWNED_COMPACT[session_id] = {
                    **going, "running": False, "ok": False,
                    "message": "the session stopped part way through",
                }
            OWNED_ASK.pop(session_id, None)
            _ASK_EVENTS.pop(session_id, None)
            # Whatever was still waiting outlives the process it was waiting
            # for: the promise was that it goes in, and the deliverer can start
            # the session back up to keep it. Deliberately letting go clears the
            # queue before it gets here, so this only fires for a process that
            # went on its own.
            OWNED_HELD.pop(session_id, None)
            left = OWNED_QUEUE.pop(session_id, None) or []
        own_errand(proc.pid, False)
        # `stderr` is a DEVNULL rather than a pipe, so it is `None` here — and
        # closing it threw, which took the rest of this block with it and left
        # anything still queued unhandled. The one place that showed was the
        # thread's own traceback, which nobody is reading.
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        if left:
            from watchtower.input import deliver_later

            for text in left:
                deliver_later(session_id, text)


def owned_hold(session_id: str, cwd: str, mode: str) -> tuple[bool, str]:
    """Start holding this session open, or say why it cannot be held."""
    if mode not in OWNED_MODES:
        return False, f"The panel does not run turns in {mode!r}"
    if owned_running(session_id):
        return True, "Already running here"
    if not cwd or not Path(cwd).is_dir():
        return False, f"Its folder is gone: {cwd}" if cwd else "That session has no folder"
    if not shutil.which("claude"):
        return False, "Cannot find the claude command on PATH"
    # Nothing to resume means nothing to hold: --resume fails on a session that
    # has never spoken, and it fails a second after being started rather than
    # visibly. A session the panel started and has not been typed at yet is the
    # one exception — it is named but empty, so it is started rather than
    # resumed, under the name it was given.
    empty = not has_conversation(session_id, cwd)
    argv = [
        shutil.which("claude") or "claude", "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        *(["--session-id", session_id] if empty else [f"--resume={session_id}"]),
        "--permission-mode", CLI_MODES.get(mode, mode),
        # Undocumented in --help, and the mechanism the official extension uses:
        # `stdio` means ask over this channel rather than at a terminal.
        "--permission-prompt-tool", "stdio",
    ]
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=top_level_env(), text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return False, f"Could not start it: {exc}"
    held = {"proc": proc, "write": threading.Lock(), "mode": mode, "cwd": cwd,
            "since": time.time(), "id": session_id, "named": threading.Event()}
    with _OWNED_LOCK:
        OWNED_PROCS[session_id] = held
    # A `claude` of ours on the session's own transcript would otherwise arrive
    # in the list as a second live session. See OWN_ERRANDS.
    own_errand(proc.pid, True)
    threading.Thread(target=owned_reader, args=(session_id, held), daemon=True).start()
    return True, "Running here"


def owned_new(cwd: str, mode: str) -> tuple[str | None, str]:
    """Start a brand new session the panel runs, and answer with its id.

    Interactive from the first word rather than adopted later. The panel names it
    — `--session-id` takes a uuid of our choosing — which is what lets the row
    exist before anything has been said: without a name of our own the process
    announces nothing until it is sent something, and the first message would
    have had to be asked for in a dialog before there was a session to type at.
    Now the session is simply there, and the first message is the first thing you
    type into it, like any other session.
    """
    if mode not in OWNED_MODES:
        return None, f"The panel does not run turns in {mode!r}"
    if not cwd or not Path(cwd).is_dir():
        return None, f"That folder is gone: {cwd}" if cwd else "There is no folder to start it in"
    claude = shutil.which("claude")
    if not claude:
        return None, "Cannot find the claude command on PATH"
    session_id = str(uuid.uuid4())
    argv = [
        claude, "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--session-id", session_id,
        "--permission-mode", CLI_MODES.get(mode, mode),
        "--permission-prompt-tool", "stdio",
    ]
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=top_level_env(), text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return None, f"Could not start it: {exc}"
    held = {"proc": proc, "write": threading.Lock(), "mode": mode, "cwd": cwd,
            "since": time.time(), "id": session_id, "named": threading.Event()}
    held["named"].set()
    with _OWNED_LOCK:
        OWNED_PROCS[session_id] = held
    own_errand(proc.pid, True)
    threading.Thread(target=owned_reader, args=(session_id, held), daemon=True).start()
    # The row has to exist for a session with no transcript yet, and the kept
    # record is the only thing that can carry it. Held, not pinned: it lasts as
    # long as the panel that is running it, which is as long as it means
    # anything. Pin it yourself and it outlives that too.
    keep_row({
        "sessionId": session_id, "name": os.path.basename(cwd) or "new session",
        "cwd": cwd, "startedAt": time.time(), "lastSeen": time.time(),
        "version": None, "kind": "interactive",
    })
    owned = load_owned()
    owned[session_id] = {"mode": mode, "here": True}
    save_owned(owned)
    return session_id, "Running here"


def owned_release(session_id: str) -> bool:
    """Stop holding a session, so something else can have its transcript."""
    with _OWNED_LOCK:
        held = OWNED_PROCS.pop(session_id, None)
        OWNED_BUSY.pop(session_id, None)
        OWNED_STOPPING.pop(session_id, None)
        # Letting go is deliberate, so what was queued for this session goes with
        # it rather than being restarted into a session somebody has just handed
        # back to a terminal.
        OWNED_QUEUE.pop(session_id, None)
        OWNED_HELD.pop(session_id, None)
    if not held:
        return False
    proc = held["proc"]
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    own_errand(proc.pid, False)
    return True


def owned_set_mode(session_id: str, mode: str) -> tuple[bool, str]:
    """Change the mode. On a held session this takes effect where it is running."""
    if mode not in OWNED_MODES:
        return False, f"The panel does not run turns in {mode!r}"
    owned = load_owned()
    owned[session_id] = {"mode": mode, "here": bool((owned.get(session_id) or {}).get("here"))}
    save_owned(owned)
    if not owned_running(session_id):
        return True, f"The next turn here runs in {mode}"
    sent = owned_write(session_id, {
        "type": "control_request",
        "request_id": f"mode-{uuid.uuid4()}",
        "request": {"subtype": "set_permission_mode", "mode": CLI_MODES.get(mode, mode)},
    })
    if not sent:
        return True, f"The next turn here runs in {mode}"
    with _OWNED_LOCK:
        held = OWNED_PROCS.get(session_id)
        if held:
            held["mode"] = mode
    return True, f"Now running in {mode}"


def owned_say(session_id: str, cwd: str, text: str, mode: str) -> tuple[bool, str]:
    """Send a turn to the held session, holding it first if it is not held yet."""
    if not text.strip():
        return False, "There is nothing to send"
    if not owned_running(session_id):
        ok, message = owned_hold(session_id, cwd, mode)
        if not ok:
            return False, message
    with _OWNED_LOCK:
        # Behind the turn in flight, or behind anything already waiting for it.
        # The second half matters: between a turn's result landing and the queue
        # draining there is an instant where nothing is running, and a message
        # sent into it would go down the pipe ahead of one typed before it. The
        # back of the queue is the only place either can go.
        wait = session_id in OWNED_BUSY or bool(OWNED_QUEUE.get(session_id))
        if not wait:
            OWNED_BUSY[session_id] = time.time()
    # Mid-turn is not a refusal. It is the ordinary case for anybody reading an
    # answer and thinking of the next thing to ask, and holding the message is
    # what the terminal's own prompt would have done with it.
    if wait:
        ok, said = owned_queue_add(session_id, text)
        # Sending is how you say carry on. A queue held back by a stop is let go
        # here rather than sitting behind a Send that visibly did nothing — and
        # in the order it was written, this message last, because that is the
        # promise the strip makes. Wanting to say something *instead* of what is
        # waiting is what *Drop them* is for, and it is on the same strip.
        with _OWNED_LOCK:
            OWNED_HELD.pop(session_id, None)
        # And the turn may have ended while that was being written down, in which
        # case nothing else is coming along to send it.
        if ok:
            owned_flush(session_id)
        return ok, said
    turn = user_turn(text)
    if owned_write(session_id, turn):
        return True, "Sent"
    # The pipe went between holding it and writing down it — a process that had
    # exited by the time we got here. Asking for the message to be sent again is
    # asking somebody to do what we can do ourselves: let go of the dead one,
    # start it back up, and write the same turn to that.
    with _OWNED_LOCK:
        OWNED_BUSY.pop(session_id, None)
    owned_release(session_id)
    ok, message = owned_hold(session_id, cwd, mode)
    if not ok:
        return False, message
    with _OWNED_LOCK:
        OWNED_BUSY[session_id] = time.time()
    if not owned_write(session_id, turn):
        with _OWNED_LOCK:
            OWNED_BUSY.pop(session_id, None)
        return False, "It would not take the message, even started back up"
    return True, "Started it back up and sent it"


def owned_resume_held() -> None:
    """Pick up every session that was interactive when the panel last ran.

    A held session is a process of ours, so it goes when the panel goes. A pinned
    one comes back: the row was written down, so it is still on the list after a
    restart, and a row saying *Runs from here* with nothing behind it reads as
    the interactive session having vanished when it was the panel that did.

    An unpinned one does not come back, and its claim to be running here goes
    with it — the row was only ever held for as long as the panel ran, and an
    `owned` entry saying *here* for a session with no row is a session nobody can
    see being run.

    Its **mode** stays. That is the part worth keeping across a restart whatever
    becomes of the row: it is a choice you made about this conversation, not a
    fact about the process that was serving it, and the session comes back into
    the mode it was left in rather than into the default. The whole record used
    to be dropped, which was harmless while `here` meant *adopted* and little
    else — but a turn run from the panel now sets it too, so dropping the record
    was quietly throwing away the mode of every session anyone had typed at.

    Anything that cannot be picked up is left alone rather than reported: a
    folder that has moved, a transcript that was cleared, a session somebody has
    since opened in a terminal. The row is still there to type at, and typing
    starts it back up.
    """
    from watchtower.store import STORE

    pinned = load_pinned()
    owned = load_owned()
    stale = [key for key, entry in owned.items() if entry.get("here") and key not in pinned]
    if stale:
        for key in stale:
            owned[key] = {"mode": owned[key].get("mode") or OWNED_MODES[0], "here": False}
        save_owned(owned)
    for session_id, entry in owned.items():
        if not entry.get("here"):
            continue
        # Something else running it takes precedence — it holds the transcript,
        # and two processes on one conversation is the failure to avoid.
        if STORE.raw(session_id):
            continue
        cwd = str((pinned.get(session_id) or {}).get("cwd") or "")
        ok, said = owned_hold(session_id, cwd, entry.get("mode") or OWNED_MODES[0])
        # Flushed: this happens after the banner, on a stdout that is block
        # buffered the moment the panel is piped to a log or a service unit.
        print(f"note: {'running' if ok else 'could not run'} {session_id[:8]} here"
              + ("" if ok else f" — {said}"), flush=True)


def owned_release_all() -> None:
    """Let go of every held session, on the way out."""
    with _OWNED_LOCK:
        ids = list(OWNED_PROCS)
    for session_id in ids:
        owned_release(session_id)



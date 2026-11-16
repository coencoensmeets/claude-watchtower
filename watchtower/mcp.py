"""The panel as an MCP server, so a Claude session can drive it too.

Claude Code starts this as a stdio MCP server (see README → *Letting Claude
drive the panel*). It holds no state of its own and runs nothing itself: every
tool is one or two requests to the panel that is already running on this
machine, on loopback, where the panel asks no key. So whatever the panel will
or will not do — which folders it starts sessions in, what is behind the
loopback gate — the tools inherit unchanged, and a panel that is not running
is answered with "start the panel" rather than a second panel being started.

What it does not hand over by default is approving another session's tool
use. A session that can say yes to another session's permission prompts can
run anything through it, and a message read out of one conversation is enough
to steer the session that read it. Denying is always allowed; allowing needs
CLAUDE_WATCHTOWER_MCP_APPROVALS=1 in the server's environment.

Messages sent from here carry the cross-session envelope the panel already
reads, so the conversation shows them as coming from the sending session
rather than from you.

Standard library only, in keeping with the rest of the project: MCP over stdio
is newline-delimited JSON-RPC 2.0, which needs nothing more than json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from watchtower.config import LISTEN_FILE

SERVER_NAME = "claude-watchtower"
SERVER_VERSION = "1.0.0"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
APPROVALS_ENV = "CLAUDE_WATCHTOWER_MCP_APPROVALS"
MODES = ("default", "auto", "plan", "acceptEdits")
TEXT_CAP = 2000           # characters of one message handed back
SEND_CAP = 20_000         # characters of one message sent


def answer_route(ask: dict) -> str:
    """Where an answer to this prompt goes: the pipe of a panel turn, or keys
    pressed in the tmux pane a terminal session runs in."""
    return "/api/tmux/answer" if ask.get("via") == "tmux" else "/api/owned/answer"


class ToolError(Exception):
    """A tool that could not do what it was asked, in words for the model."""


# ----------------------------------------------------------------- the panel

def panel_port() -> int | None:
    """Where the panel is listening: the environment, else what it wrote down."""
    raw = os.environ.get("CLAUDE_WATCHTOWER_PORT")
    if not raw:
        try:
            raw = json.loads(LISTEN_FILE.read_text()).get("port")
        except (OSError, ValueError, AttributeError):
            raw = None
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def http_call(method: str, path: str, body: dict | None = None) -> dict:
    """One request to the running panel, its JSON back, or a ToolError."""
    port = panel_port()
    if not port:
        raise ToolError("The panel has never run on this machine, so there is no port to reach. "
                        "Start it with `python3 server.py`.")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        # The panel answers a refusal with JSON saying why; that is the answer.
        try:
            said = json.loads(error.read() or b"{}")
        except ValueError:
            said = {}
        raise ToolError(said.get("message") or f"The panel answered {error.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ToolError(f"The panel is not answering on port {port}. "
                        "Is it running? Start it with `python3 server.py`.") from None


# ------------------------------------------------------------- the sessions

def _ancestors(pid: int, depth: int = 6) -> list[int]:
    """This process's parents, nearest first, read from /proc."""
    found = []
    for _ in range(depth):
        try:
            with open(f"/proc/{pid}/stat") as handle:
                stat = handle.read()
            pid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break
        found.append(pid)
    return found


def _cmdline_session(pid: int) -> str | None:
    """The session id a `claude` process was started on, off its command line:
    what the panel's own turns are started with (`--resume=<id>`)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            args = handle.read().decode(errors="replace").split("\0")
    except OSError:
        return None
    for i, arg in enumerate(args):
        for flag in ("--resume", "--session-id"):
            if arg.startswith(flag + "="):
                return arg.split("=", 1)[1] or None
            if arg == flag and i + 1 < len(args):
                return args[i + 1] or None
    return None


def own_session(sessions: list[dict], parents: list[int] | None = None,
                env: dict | None = None, moved: dict | None = None) -> dict | None:
    """The session that started this server.

    Claude Code hands its session id to what it starts, which is the plain
    answer. Failing that, the nearest parent with a row of its own — a session
    in a terminal has its pid on the row — or, for one the panel runs, which
    has no pid on its row, the id on the parent's command line. A session that
    was cleared since has a new id, which `moved` maps it to.
    """
    env = os.environ if env is None else env
    parents = [os.getppid(), *_ancestors(os.getppid())] if parents is None else parents
    by_id = {s["sessionId"]: s for s in sessions}
    moved = moved or {}

    def found(sid: str | None) -> dict | None:
        for _ in range(4):
            if not sid or sid in by_id:
                break
            sid = moved.get(sid)
        return by_id.get(sid) if sid else None

    hit = found(env.get("CLAUDE_CODE_SESSION_ID"))
    if hit:
        return hit
    by_pid = {s.get("pid"): s for s in sessions if s.get("pid")}
    for pid in parents:
        if pid in by_pid:
            return by_pid[pid]
        hit = found(_cmdline_session(pid))
        if hit:
            return hit
    return None


def resolve(sessions: list[dict], ref: str) -> dict:
    """A session from what a model would call it: its id, the start of its id,
    or its name, exactly or as a unique part of one."""
    ref = str(ref or "").strip()
    if not ref:
        raise ToolError("Say which session: its name or its id, as list_sessions gives them.")
    low = ref.casefold()
    for test in (
        lambda s: s["sessionId"] == ref,
        lambda s: s["name"].casefold() == low,
        lambda s: len(ref) >= 6 and s["sessionId"].startswith(ref),
        lambda s: low in s["name"].casefold(),
    ):
        hits = [s for s in sessions if test(s)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            names = ", ".join(f"{s['name']} ({s['sessionId'][:8]})" for s in hits[:8])
            raise ToolError(f"{ref!r} fits more than one session: {names}. Use the id.")
    raise ToolError(f"No session called {ref!r}. list_sessions shows what is there.")


def runs_here(session: dict, owned: dict) -> bool:
    """Whether the panel runs this session's turns, mirroring the composer:
    a message to one of these is a turn the panel starts, and to anything
    else it is a message down the session's own socket."""
    mine = owned.get(session["sessionId"]) or {}
    return (mine.get("here") is True or mine.get("running") is True
            or not session.get("alive") or session.get("status") == "stopped")


STATE_WORDS = {"waiting": "needs an answer", "busy": "working", "idle": "waiting for input",
               "shell": "waiting for input", "offline": "closed", "stopped": "stopped"}


def summarise(session: dict, owned: dict, me: dict | None) -> dict:
    mine = owned.get(session["sessionId"]) or {}
    row = {
        "id": session["sessionId"],
        "name": session["name"],
        "state": STATE_WORDS.get(session.get("status"), session.get("status")),
        "folder": session.get("cwd"),
    }
    for key, value in (("branch", session.get("branch")), ("subject", session.get("title")),
                       ("mode", mine.get("mode") or session.get("permissionMode"))):
        if value:
            row[key] = value
    if runs_here(session, owned):
        row["runBy"] = "panel"
    if (session.get("agents") or {}).get("running"):
        row["agentsRunning"] = session["agents"]["running"]
    # Commands it left running in the background. Named, because "waiting for
    # input" with a build still going is a different situation from a quiet one.
    if (session.get("shells") or {}).get("live"):
        row["backgroundCommands"] = [item.get("description") or item.get("command")
                                     for item in session["shells"]["live"]]
    ask =mine.get("ask") or session.get("terminalAsk")
    if ask:
        row["waitingOn"] = "a question" if ask.get("asks") else f"permission for {ask.get('tool')}"
    elif session.get("question"):
        row["waitingOn"] = "a question in its terminal"
    if me and me["sessionId"] == session["sessionId"]:
        row["isYou"] = True
    return row



# ---------------------------------------------------------------- the clock

_SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)(?![a-z])",
                   re.IGNORECASE)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_span(raw: Any) -> int | None:
    """Seconds from '30m', '2h', '1h30m', '1 day', or a bare number of minutes."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return int(raw * 60)
    text = str(raw).strip().casefold()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text) * 60)
    text = re.sub(r"^every\s+", "", text)
    if text in ("minute", "hour", "day", "week"):
        return _UNIT[text[0]]
    parts = _SPAN.findall(text)
    if not parts or _SPAN.sub("", text).strip(" ,and") != "":
        raise ToolError(f"Cannot read {raw!r} as a span of time. Use e.g. '15m', '2h', '1d' or '1h30m'.")
    return int(sum(float(n) * _UNIT[u[0].casefold()] for n, u in parts))


def parse_moment(raw: Any, now: float | None = None) -> float | None:
    """A moment from 'in 10m', an ISO date-time (local time unless it says
    otherwise), 'HH:MM' (today, or tomorrow once it has gone), or epoch seconds."""
    now = time.time() if now is None else now
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    low = text.casefold()
    if low == "now":
        return now
    if low.startswith("in "):
        return now + (parse_span(low[3:]) or 0)
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = map(int, text.split(":"))
        today = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        at = today.timestamp()
        return at if at > now else at + 86400
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise ToolError(f"Cannot read {raw!r} as a moment. Use 'in 10m', '14:30', or an ISO "
                        "date-time such as '2026-11-17T09:00'.") from None


def when_words(at: float | None) -> str | None:
    return datetime.fromtimestamp(at).isoformat(timespec="minutes") if at else None


def task_summary(task: dict) -> dict:
    every = task.get("every")
    out = {"id": task["id"], "title": task["title"], "kind": task["kind"], "folder": task["cwd"],
           "what": task["text"] if len(task["text"]) <= 300 else task["text"][:300] + " …",
           "repeats": _every_words(every), "enabled": task.get("enabled"),
           "nextRun": when_words(task.get("next")), "runs": task.get("runs") or 0}
    if task["kind"] == "claude":
        out["mode"] = task.get("mode")
        out["session"] = task.get("session")
        if task.get("sessionId"):
            out["sessionId"] = task["sessionId"]
    if task.get("running"):
        out["running"] = True
    if task.get("starred"):
        out["starred"] = True
    last = task.get("last")
    if last:
        out["lastRun"] = {"at": when_words(last.get("at")), "ok": last.get("ok"),
                          "message": last.get("message")}
        if last.get("output"):
            out["lastRun"]["output"] = last["output"][-1500:]
    return out


def _every_words(every: int | None) -> str:
    if not every:
        return "once"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if every % size == 0:
            n = every // size
            return f"every {unit}" if n == 1 else f"every {n} {unit}s"
    return f"every {every} seconds"

# --------------------------------------------------------------------- tools

class Tools:
    """The tools, over a panel reached through `call` — swapped in the tests."""

    def __init__(self, call: Callable[[str, str, dict | None], dict] = http_call,
                 parents: list[int] | None = None, env: dict | None = None):
        self.call = call
        self.parents = parents
        self.env = os.environ if env is None else env

    # --- reading the feed

    def _feed(self) -> tuple[list[dict], dict, dict | None]:
        feed = self.call("GET", "/api/state", None)
        sessions = feed.get("sessions") or []
        me = own_session(sessions, self.parents, self.env, feed.get("moved"))
        return sessions, feed.get("owned") or {}, me

    def _target(self, ref: str) -> tuple[dict, dict, dict | None]:
        sessions, owned, me = self._feed()
        return resolve(sessions, ref), owned, me

    # --- the tools themselves

    def list_sessions(self, state: str = "") -> Any:
        sessions, owned, me = self._feed()
        rows = [summarise(s, owned, me) for s in sessions]
        if state:
            rows = [r for r in rows if state.casefold() in str(r["state"]).casefold()]
        return {"sessions": rows, "you": me["name"] if me else None}

    def read_conversation(self, session: str, limit: int = 20, include_tools: bool = False) -> Any:
        target, owned, _ = self._target(session)
        limit = max(1, min(200, int(limit or 20)))
        data = self.call("GET", f"/api/transcript?sessionId={target['sessionId']}&limit={limit}", None)
        messages = []
        for m in data.get("messages") or []:
            text = m.get("text") or ""
            tools = [f"{t.get('name')} {t.get('detail') or ''}".strip() for t in m.get("tools") or []]
            if not text and not (include_tools and tools):
                continue
            entry = {"role": m.get("role") or "tool", "at": m.get("at")}
            if m.get("from"):
                entry["from"] = m["from"]
            if text:
                entry["text"] = text if len(text) <= TEXT_CAP else text[:TEXT_CAP] + " …[cut]"
            if include_tools and tools:
                entry["tools"] = tools
            messages.append(entry)
        out = {"session": target["name"], "title": data.get("title"), "messages": messages[-limit:]}
        ask = (owned.get(target["sessionId"]) or {}).get("ask") or target.get("terminalAsk")
        if ask:
            out["waitingOn"] = {"kind": "question" if ask.get("asks") else "permission",
                                "tool": ask.get("tool"), "what": ask.get("what"),
                                "questions": (ask.get("input") or {}).get("questions")}
        elif target.get("question"):
            out["waitingOn"] = {"kind": "question in its terminal",
                                "questions": target["question"].get("questions"),
                                "note": "Asked in a terminal; only that terminal can answer it."}
        return out

    def send_message(self, session: str, text: str) -> Any:
        target, owned, me = self._target(session)
        text = str(text or "").strip()
        if not text:
            raise ToolError("Nothing to send.")
        if len(text) > SEND_CAP:
            raise ToolError(f"That is {len(text)} characters; keep a message under {SEND_CAP}.")
        if me and me["sessionId"] == target["sessionId"]:
            raise ToolError("That is this session. Sending to yourself would queue a turn behind this one.")
        sender = (me["name"] if me else "an MCP client").replace('"', "'")
        wrapped = f'<cross-session-message from-name="{sender}">\n{text}\n</cross-session-message>'
        sid = target["sessionId"]
        if runs_here(target, owned):
            said = self.call("POST", "/api/owned/say", {"sessionId": sid, "text": wrapped})
        else:
            said = self.call("POST", "/api/say", {"sessionId": sid, "text": wrapped})
        return {"ok": True, "session": target["name"], "message": said.get("message") or "Sent"}

    def start_session(self, folder: str, message: str = "", mode: str = "auto") -> Any:
        if mode not in MODES:
            raise ToolError(f"Mode must be one of {', '.join(MODES)}.")
        made = self.call("POST", "/api/owned/new", {"cwd": str(folder or ""), "mode": mode})
        sid = made.get("sessionId")
        out = {"ok": True, "id": sid, "folder": made.get("cwd"), "mode": mode,
               "message": made.get("message")}
        if message and sid:
            _, _, me = self._feed()
            sender = (me["name"] if me else "an MCP client").replace('"', "'")
            self.call("POST", "/api/owned/say", {
                "sessionId": sid, "mode": mode,
                "text": f'<cross-session-message from-name="{sender}">\n{message.strip()}\n</cross-session-message>'})
            out["sent"] = True
        return out

    def stop_turn(self, session: str) -> Any:
        target, owned, _ = self._target(session)
        if not runs_here(target, owned):
            raise ToolError(f"{target['name']} runs in a terminal; only turns the panel runs can be stopped from here.")
        said = self.call("POST", "/api/owned/interrupt", {"sessionId": target["sessionId"]})
        return {"ok": True, "message": said.get("message")}

    def _ask(self, session: str) -> tuple[dict, dict]:
        target, owned, me = self._target(session)
        if me and me["sessionId"] == target["sessionId"]:
            raise ToolError("That is this session; its prompts are for the person using it.")
        # A panel turn's prompt, or one read off a tmux pane's screen; both are
        # answered the same way, down different routes. See watchtower/tmux.py.
        ask = (owned.get(target["sessionId"]) or {}).get("ask") or target.get("terminalAsk")
        if not ask:
            if target.get("question"):
                raise ToolError(f"{target['name']} is asking in its terminal, which only that terminal can answer.")
            raise ToolError(f"{target['name']} is not waiting on anything the panel can answer.")
        return target, ask

    def answer_question(self, session: str, answers: dict) -> Any:
        target, ask = self._ask(session)
        if not ask.get("asks"):
            raise ToolError(f"{target['name']} is asking permission for {ask.get('tool')}, not a question. "
                            "Use answer_permission.")
        questions = (ask.get("input") or {}).get("questions") or []
        picks = {}
        for q in questions:
            given = (answers or {}).get(q.get("question"))
            if given is None:
                raise ToolError(f"No answer for: {q.get('question')}")
            labels = given if isinstance(given, list) else [given]
            if not q.get("multiSelect") and len(labels) > 1:
                raise ToolError(f"Only one answer for: {q.get('question')}")
            # A label not among the options is a free-text answer, which the
            # question takes the way it takes "Other".
            picks[q["question"]] = [str(x) for x in labels]
        said = self.call("POST", answer_route(ask), {
            "sessionId": target["sessionId"], "requestId": ask.get("requestId"),
            "behavior": "allow", "answers": picks, "asks": True})
        return {"ok": True, "message": said.get("message")}

    def answer_permission(self, session: str, allow: bool, message: str = "") -> Any:
        target, ask = self._ask(session)
        if ask.get("asks"):
            raise ToolError(f"{target['name']} is asking a question. Use answer_question.")
        if allow and self.env.get(APPROVALS_ENV) != "1":
            raise ToolError(
                "Allowing another session's tool use is off. A session that can approve another's "
                f"prompts can run anything through it. The person can turn it on with {APPROVALS_ENV}=1 "
                "in this MCP server's environment, or answer it in the panel. Denying is always allowed.")
        said = self.call("POST", answer_route(ask), {
            "sessionId": target["sessionId"], "requestId": ask.get("requestId"),
            "behavior": "allow" if allow else "deny", "message": str(message or "")[:300]})
        return {"ok": True, "message": said.get("message")}

    def set_mode(self, session: str, mode: str) -> Any:
        if mode not in MODES:
            raise ToolError(f"Mode must be one of {', '.join(MODES)}.")
        target, owned, _ = self._target(session)
        # A live terminal session in tmux is switched in its pane, with
        # Shift+Tab; anything else has the mode its next panel turn starts in.
        running_here = (owned.get(target["sessionId"]) or {}).get("running")
        route = "/api/tmux/mode" if target.get("tmux") and target.get("alive") and not running_here \
            else "/api/owned/mode"
        said = self.call("POST", route, {"sessionId": target["sessionId"], "mode": mode})
        return {"ok": True, "message": said.get("message"), "mode": mode}

    def rename_session(self, session: str, name: str) -> Any:
        target, _, _ = self._target(session)
        said = self.call("POST", "/api/rename", {"sessionId": target["sessionId"], "name": name})
        return {"ok": True, "name": said.get("name")}

    def focus_window(self, session: str) -> Any:
        target, _, _ = self._target(session)
        said = self.call("POST", "/api/focus", {"sessionId": target["sessionId"]})
        return {"ok": True, "message": said.get("message")}

    def show_preview(self, target: str, title: str = "", session: str = "") -> Any:
        target = str(target or "").strip()
        if not target:
            raise ToolError("Say what to show: a path to an HTML page or a folder with an index.html, "
                            "or the http(s) address of a server you started.")
        if session:
            owner, _, _ = self._target(session)
        else:
            _, _, owner = self._feed()
            if not owner:
                raise ToolError("The panel cannot tell which session this is. Pass `session` with "
                                "your own name as list_sessions gives it.")
        if not target.startswith(("http://", "https://")) and not os.path.isabs(os.path.expanduser(target)):
            # Relative to where the session works, which is where this server
            # was started; the panel would read it against the same folder.
            target = os.path.abspath(target)
        said = self.call("POST", "/api/previews", {
            "sessionId": owner["sessionId"], "target": target, "title": str(title or "")})
        row = said.get("preview") or {}
        out = {"ok": True, "title": row.get("title"), "session": owner["name"],
               "message": f"Shown in the Watchtower panel under {owner['name']} → Preview."}
        port = panel_port()
        if said.get("path") and port:
            out["url"] = f"http://127.0.0.1:{port}{said['path']}"
        elif row.get("url"):
            out["url"] = row["url"]
        return out

    def end_session(self, session: str) -> Any:
        target, _, me = self._target(session)
        if me and me["sessionId"] == target["sessionId"]:
            raise ToolError("That is this session. Ending yourself is the person's call, not a tool's.")
        said = self.call("POST", "/api/end", {"sessionId": target["sessionId"]})
        return {"ok": True, "message": said.get("message")}

    # --- scheduled tasks

    def _task_folder(self, folder: str) -> dict:
        """Where a task goes: the folder named, else this session's own."""
        if folder:
            return {"cwd": os.path.abspath(os.path.expanduser(str(folder)))}
        _, _, me = self._feed()
        if not me:
            raise ToolError("The panel cannot tell which session this is, so pass `folder`.")
        return {"sessionId": me["sessionId"]}

    def schedule_task(self, prompt: str = "", command: str = "", at: Any = "", every: Any = "",
                      title: str = "", folder: str = "", mode: str = "", session: str = "",
                      timeout_minutes: Any = None, id: str = "", enabled: Any = None,
                      starred: Any = None) -> Any:
        if prompt and command:
            raise ToolError("A task is a prompt for Claude or a command, not both.")
        body: dict[str, Any] = {"title": title or None, "mode": mode or None, "session": session or None}
        if prompt or command:
            body["kind"] = "claude" if prompt else "command"
            body["text"] = prompt or command
        elif not id:
            raise ToolError("Say what to do: `prompt` for Claude, or `command` for a shell command.")
        moment = parse_moment(at)
        span = parse_span(every)
        if moment is not None:
            body["at"] = moment
        if span is not None:
            body["every"] = span
        if timeout_minutes not in (None, ""):
            body["timeout"] = int(float(timeout_minutes) * 60)
        if enabled is not None:
            body["enabled"] = bool(enabled)
        if starred is not None:
            body["starred"] = bool(starred)
        if id:
            body["id"] = id
        else:
            body.update(self._task_folder(folder))
            _, _, me = self._feed()
            body["createdBy"] = me["name"] if me else "an MCP client"
        said = self.call("POST", "/api/schedules", {k: v for k, v in body.items() if v is not None})
        task = said.get("task") or {}
        return {"ok": True, "task": task_summary(task),
                "message": f"Scheduled in the Watchtower panel — {_every_words(task.get('every'))}, "
                           f"next at {when_words(task.get('next'))}. The panel must be running for it to run."}

    def list_scheduled_tasks(self, folder: str = "", everywhere: bool = False) -> Any:
        if everywhere:
            said = self.call("GET", "/api/schedules", None)
        else:
            where = self._task_folder(folder)
            query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in where.items())
            said = self.call("GET", f"/api/schedules?{query}", None)
        return {"tasks": [task_summary(t) for t in said.get("tasks") or []]}

    def cancel_scheduled_task(self, id: str) -> Any:
        said = self.call("POST", "/api/schedules/remove", {"id": str(id or "")})
        return {"ok": True, "message": said.get("message")}

    def run_scheduled_task(self, id: str) -> Any:
        said = self.call("POST", "/api/schedules/run", {"id": str(id or "")})
        return {"ok": True, "message": said.get("message")}


SESSION = {"type": "string", "description": "The session's name or id, as list_sessions gives them"}

TOOLS = [
    {"name": "list_sessions",
     "description": "Every Claude Code session on this machine the Watchtower panel knows about: "
                    "name, id, state (working, needs an answer, waiting for input, closed), folder, "
                    "branch, subject, mode, what it is waiting on, and any commands it left running in the "
                    "background (`backgroundCommands`). `isYou` marks this session.",
     "inputSchema": {"type": "object", "properties": {
         "state": {"type": "string", "description": "Only sessions whose state contains this, e.g. 'working'"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "read_conversation",
     "description": "The latest messages of one session, oldest first, and what it is waiting on if anything.",
     "inputSchema": {"type": "object", "required": ["session"], "properties": {
         "session": SESSION,
         "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
         "include_tools": {"type": "boolean", "default": False,
                           "description": "Also list the tool calls each turn made"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "send_message",
     "description": "Send a message to another session, as a new turn. It is shown there as coming "
                    "from this session. A session the panel runs is started if it is not running.",
     "inputSchema": {"type": "object", "required": ["session", "text"], "properties": {
         "session": SESSION, "text": {"type": "string"}}}},
    {"name": "start_session",
     "description": "Start a new Claude Code session that the panel runs, in a folder inside the home "
                    "folder, optionally with a first message.",
     "inputSchema": {"type": "object", "required": ["folder"], "properties": {
         "folder": {"type": "string", "description": "Absolute path, inside the home folder"},
         "message": {"type": "string"},
         "mode": {"type": "string", "enum": list(MODES), "default": "auto"}}}},
    {"name": "stop_turn",
     "description": "Stop the turn a panel-run session is in the middle of.",
     "inputSchema": {"type": "object", "required": ["session"], "properties": {"session": SESSION}}},
    {"name": "answer_question",
     "description": "Answer the multiple-choice question a panel-run session is asking. `answers` maps "
                    "each question's text to the chosen option label (or a list of labels where several "
                    "may be picked). read_conversation shows the questions.",
     "inputSchema": {"type": "object", "required": ["session", "answers"], "properties": {
         "session": SESSION,
         "answers": {"type": "object", "additionalProperties": {
             "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}}}}},
    {"name": "answer_permission",
     "description": "Allow or deny the tool a panel-run session is asking permission for. Denying always "
                    f"works; allowing is off unless the person set {APPROVALS_ENV}=1.",
     "inputSchema": {"type": "object", "required": ["session", "allow"], "properties": {
         "session": SESSION, "allow": {"type": "boolean"},
         "message": {"type": "string", "description": "Why, when denying"}}}},
    {"name": "set_mode",
     "description": "Set the permission mode a panel-run session's next turns run in, or switch "
                    "a terminal session running in tmux to it now (Shift+Tab in its pane).",
     "inputSchema": {"type": "object", "required": ["session", "mode"], "properties": {
         "session": SESSION, "mode": {"type": "string", "enum": list(MODES)}}}},
    {"name": "rename_session",
     "description": "Rename a session in the panel. An empty name restores its own.",
     "inputSchema": {"type": "object", "required": ["session", "name"], "properties": {
         "session": SESSION, "name": {"type": "string"}}},
     "annotations": {"idempotentHint": True}},
    {"name": "focus_window",
     "description": "Bring the session's terminal or editor window to the front on the desktop.",
     "inputSchema": {"type": "object", "required": ["session"], "properties": {"session": SESSION}}},
    {"name": "show_preview",
     "description": "Show the user something viewable you made — an HTML page, a styleguide, a "
                    "report, a static site, or a dev server you started — in the Watchtower panel, "
                    "where they can open it on this computer or their phone. Call it again after "
                    "changing the page so it moves to the top. Relative assets next to the page load.",
     "inputSchema": {"type": "object", "required": ["target"], "properties": {
         "target": {"type": "string", "description": "Path to an .html file (or a folder with an "
                    "index.html), or an http(s) URL such as http://localhost:5173"},
         "title": {"type": "string", "description": "A short name for it, e.g. 'Styleguide'"},
         "session": {"type": "string", "description": "Only if the panel cannot tell which session "
                     "you are: your own name from list_sessions"}}},
     "annotations": {"idempotentHint": True}},
    {"name": "end_session",
     "description": "End another session: stop its process, or stop running it if the panel runs it.",
     "inputSchema": {"type": "object", "required": ["session"], "properties": {"session": SESSION}},
     "annotations": {"destructiveHint": True}},
    {"name": "schedule_task",
     "description": "Schedule a task in a project folder, run by the Watchtower panel: once at a "
                    "moment, or repeating every so often. A task is either a `prompt` for Claude (sent "
                    "as a turn to a fresh session each run, or to the same session every time with "
                    "session='same') or a shell `command` (run with bash in the folder; exit code and "
                    "output are kept). Defaults to this session's folder and to running now. Pass `id` "
                    "to change an existing task; `enabled: false` pauses one. Runs only while the "
                    "panel is running.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "What to ask Claude to do, for a Claude task"},
         "command": {"type": "string", "description": "A shell command, for a command task"},
         "at": {"type": "string", "description": "When it first runs: 'now', 'in 10m', '14:30' "
                "(next time it is that time), or a local ISO date-time like '2026-11-17T09:00'"},
         "every": {"type": "string", "description": "Repeat interval, e.g. '15m', '2h', '1d', "
                   "'1h30m'. Leave out for a one-off. At least a minute."},
         "title": {"type": "string", "description": "A short name for the task"},
         "folder": {"type": "string", "description": "The project folder; defaults to this session's"},
         "mode": {"type": "string", "enum": list(MODES), "description": "Permission mode for a Claude "
                  "task's turns (default auto)"},
         "session": {"type": "string", "enum": ["new", "same"], "description": "Claude tasks: 'new' "
                     "starts a fresh session every run (default); 'same' keeps one conversation going"},
         "timeout_minutes": {"type": "number", "description": "Command tasks: stop it after this "
                             "long (default 30)"},
         "id": {"type": "string", "description": "Change this existing task instead of adding one"},
         "enabled": {"type": "boolean", "description": "false pauses the task, true resumes it"},
         "starred": {"type": "boolean", "description": "true lists it under the sessions in the panel, "
                     "so it stays in view when no session in its folder is open"}}}},
    {"name": "list_scheduled_tasks",
     "description": "The tasks scheduled in this session's folder (or `folder`, or everywhere): what "
                    "each does, how often, when it runs next, and how its last run went.",
     "inputSchema": {"type": "object", "properties": {
         "folder": {"type": "string"},
         "everywhere": {"type": "boolean", "default": False, "description": "Every folder's tasks"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "cancel_scheduled_task",
     "description": "Remove a scheduled task for good. To pause one instead, use schedule_task with "
                    "its id and enabled: false.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
     "annotations": {"destructiveHint": True}},
    {"name": "run_scheduled_task",
     "description": "Run a scheduled task once now, without moving its schedule.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
]


# ------------------------------------------------------------------ protocol

class Server:
    def __init__(self, tools: Tools | None = None):
        self.tools = tools or Tools()

    def handle(self, message: dict) -> dict | None:
        """One JSON-RPC message in, its response out (None for a notification)."""
        method = message.get("method")
        mid = message.get("id")
        if mid is None:
            return None
        params = message.get("params") or {}
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                          "instructions": "Watch and drive the other Claude Code sessions on this "
                                          "machine through the Watchtower panel. Start with list_sessions. "
                                          "When you make something viewable (an HTML page, styleguide, "
                                          "report or dev server), hand it to the user with show_preview. "
                                          "To do something later or on repeat in a project — a prompt "
                                          "for Claude or a shell command — use schedule_task."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self._call(params.get("name"), params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as error:  # a broken request must not take the server down
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(error)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call(self, name: str, arguments: dict) -> dict:
        if name not in {t["name"] for t in TOOLS}:
            return {"isError": True, "content": [{"type": "text", "text": f"No tool called {name!r}"}]}
        try:
            out = getattr(self.tools, name)(**arguments)
        except ToolError as error:
            return {"isError": True, "content": [{"type": "text", "text": str(error)}]}
        except TypeError as error:
            return {"isError": True, "content": [{"type": "text", "text": f"Bad arguments: {error}"}]}
        return {"content": [{"type": "text", "text": json.dumps(out, indent=1, ensure_ascii=False)}],
                "structuredContent": out}


def main() -> None:
    server = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = server.handle(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

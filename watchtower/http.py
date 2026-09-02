"""The web layer: what the browser may ask for, and what it gets back.

Two rules hold across every route here. A request acts on what the panel
already discovered for the session it names, rather than on a path it was
handed — so no request can point git, a paste or a new session at a folder the
panel is not showing. And anything that acts is behind config.SAY_ENABLED,
which is false unless the panel is bound to loopback and sending is switched on.

There is exactly one exception to the first rule, and it is written down here
because an exception nobody wrote down is a hole. /api/editor takes a path: it
is how a path clicked out of a conversation is opened, which is the whole point
of the paths in a conversation being clickable. It is fenced in three ways —
loopback only, like everything that acts; the path must already exist; and it
must be inside your home folder or the session's own. See _resolve_under.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from watchtower import config
from watchtower.agents import list_subagents, read_subagent
from watchtower.catalog import read_catalog
from watchtower.config import HOME, ROOT, STATIC_DIR
from watchtower.control import new_session, open_editor, pick_folder, show_folder, start_session
from watchtower.git.actions import git_action
from watchtower.git.message import suggest_message
from watchtower.git.read import read_diff, read_git
from watchtower.input import (
    deliver_later, end_process, is_loopback, say_to_session, session_listening,
)
from watchtower.owned import (
    OWNED_ASK, OWNED_BUSY, OWNED_COMPACT, OWNED_MODES, OWNED_QUEUE, _OWNED_LOCK,
    answer_from_panel, load_owned, owned_clear, owned_hold, owned_interrupt, owned_new,
    owned_queued, owned_release, owned_resume, owned_running, owned_say, owned_set_mode,
    owned_unqueue, save_owned,
)
from watchtower.paste import (DROP_MAX_BYTES, PASTE_MAX_BYTES, POST_MAX_BYTES,
                              save_dropped_file, save_pasted_image)
from watchtower.plan import read_plan
from watchtower.listen import lan_address
from watchtower.proc import proc_gone
from watchtower.qr import svg as qr_svg
from watchtower.rows import (
    _KEPT, _KEPT_LOCK, drop_unpinned_row, forget_row, keep_row, kept_rows, pin_row, unpin_row,
)
from watchtower.store import STORE
from watchtower.transcript import (
    TRANSCRIPT_LIMIT_MAX, has_conversation, read_change, read_transcript,
)
from watchtower.update import do_update, read_channel, read_update, write_channel, running_here
from watchtower.usage import read_usage
from watchtower.windows import (
    WINDOWS, activate, clean_name, identify_and_pair, load_names, load_pairs, resolve_window,
    save_names, save_pairs, select_window, window_title,
)


# Every route the panel answers, filled in by the decorator on each handler.
# A table rather than a chain of `if path ==`: it is the same dispatch, but the
# set of routes is a value the panel can look at rather than control flow.
ROUTES: dict[tuple[str, str], str] = {}


def route(method: str, path: str):
    """Register the method below as the handler for one exact path."""
    def mark(handler):
        if (method, path) in ROUTES:
            raise RuntimeError(f"{method} {path} is already handled by {ROUTES[(method, path)]}")
        ROUTES[(method, path)] = handler.__name__
        return handler
    return mark


# What every route that runs a turn from the panel says when it cannot.
OFF_HERE = ("Running turns here is off because the panel is not bound to loopback")


# The cookie the key is remembered in, so it is typed on a phone once rather
# than once per page. HttpOnly: no script has any business reading it, and the
# panel's own scripts never need to — the browser sends it with every request
# they make, including the fetches for the modules themselves.
# Longer than the file has any business being, and short enough that a browser
# is not handed a megabyte of prose to lay out.
CHANGELOG_MAX = 200_000


KEY_COOKIE = "wt-key"
KEY_PARAM = "k"
# A year. The key does not expire on its own — it is the same key until it is
# replaced with --new-key — so a shorter life would only mean typing it again
# for no gain.
KEY_MAX_AGE = 365 * 24 * 3600


# What a request without the key gets. Deliberately says nothing about the
# machine, the sessions on it, or whether the key it showed was close: only that
# there is a key and where the panel prints it.
NO_KEY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>claude-watchtower</title>
<style>
  html { color-scheme: light dark }
  body { font: 16px/1.5 system-ui, sans-serif; margin: 0; display: grid; place-items: center;
         min-height: 100dvh; padding: 24px; text-align: center }
  code { padding: 2px 6px; border-radius: 4px; background: color-mix(in srgb, currentColor 12%, transparent) }
  p { max-width: 34em }
</style></head>
<body><div>
  <h1>This panel needs its key</h1>
  <p>Open it with the key on the end: <code>?k=&hellip;</code></p>
  <p>The panel prints the whole address, key and all, in the terminal it is
     running in. Typing it once is enough — this browser remembers it.</p>
</div></body></html>
""".encode()



def _resolve_under(raw: str, cwd: str, repo_root: str,
                   *, temp: bool = False) -> tuple[Path | None, str]:
    """A path from a message, made absolute and judged — or None and why not.

    The two refusals are worth telling apart on screen. "It is not there" is
    about the path, and usually means the message is older than the file; "it is
    outside" is about the panel, and means it will not go looking there however
    real the path is.

    Judged against three roots — your home folder, the session's working folder
    and its repository — because those are what a path written in this session's
    transcript can be about. Everything else on the machine is somebody else's
    business, and a panel that opens any path a message contains is a panel that
    opens whatever a message can be made to contain.

    `temp` adds a fourth: the system temporary folder, which is where a
    screenshot lands by habit — `/tmp/plot.png` is how a picture usually arrives
    in a conversation, and a rule that refuses it makes the pictures in messages
    not work for the commonest case there is. It is granted to /api/file, which
    reads a picture, and not to /api/editor, which starts a process.

    Resolved first, and by the filesystem: `..` cannot climb out of the roots
    afterwards, and neither can a symlink that points out of them.
    """
    if not raw or "\x00" in raw or len(raw) > 4096:
        return None, "That is not a path this panel will open"
    spot = Path(raw).expanduser()
    if not spot.is_absolute():
        if not cwd:
            return None, "That session has no folder to read that path against"
        spot = Path(cwd) / spot
    try:
        spot = spot.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, f"Nothing is there now: {raw}"
    roots = [Path(HOME)] + [Path(p) for p in (cwd, repo_root) if p]
    if temp:
        roots.append(Path(tempfile.gettempdir()))
    for root in roots:
        try:
            if spot == root.resolve() or root.resolve() in spot.parents:
                return spot, ""
        except (OSError, RuntimeError):
            continue
    return None, ("That path is outside your home folder and this session's, "
                  "so the panel will not open it")



class Handler(BaseHTTPRequestHandler):
    server_version = "claude-watchtower"

    # Set on the one response that answers a request carrying the key in its
    # query, so the browser has it for every request after. An attribute on the
    # class, not the instance: a handler is made per request, and this is how
    # _send knows without every caller having to pass it along.
    _keep_key = False

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # --- who may ask
    #
    # Nothing below this line runs for a request that cannot show the key, and
    # the key only exists when the panel is reachable from off this machine. On
    # loopback there is no key and no check: the only thing that can open the
    # socket is already sitting at the keyboard.

    def _key_shown(self) -> bool:
        want = config.ACCESS_KEY
        if not want:
            return True
        # This machine, whatever the panel is bound to. A phone is not this
        # machine; a browser on the desktop running it is, and asking it for a
        # key would be asking it to prove it is where it plainly is.
        if is_loopback(self.client_address[0]):
            return True
        query = parse_qs(urlparse(self.path).query)
        given = (query.get(KEY_PARAM) or [""])[0]
        if given and secrets.compare_digest(given, want):
            self._keep_key = True
            return True
        for crumb in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = crumb.strip().partition("=")
            if name == KEY_COOKIE and secrets.compare_digest(value, want):
                return True
        return False

    def _no_key(self) -> None:
        """Turned away. The API says so in its own language, so a poll from a
        page whose cookie has been cleared reports it rather than parsing HTML
        as if it were state."""
        if self.path.startswith("/api/"):
            self._json({"ok": False, "message": "This panel needs its key"}, 403)
        else:
            self._send(403, NO_KEY_PAGE, "text/html; charset=utf-8")

    # --- helpers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self._keep_key and config.ACCESS_KEY:
            # Lax rather than Strict: a phone opening the panel from a QR code,
            # a chat message or a note to itself arrives as a cross-site
            # navigation, and Strict would drop the cookie on exactly that
            # first visit. HttpOnly for the reason above; no Secure, because
            # the panel is http on a local address and marking it Secure would
            # mean the browser never sent it at all.
            self.send_header("Set-Cookie", f"{KEY_COOKIE}={config.ACCESS_KEY}; Path=/; "
                                           f"Max-Age={KEY_MAX_AGE}; HttpOnly; SameSite=Lax")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            # A pasted picture arrives base64'd inside the JSON, which is the one
            # thing here big enough to be worth a ceiling. Over it, the body is
            # read and thrown away — read, so the connection stays usable, and
            # thrown away, so nothing decides to hold 200 MB in memory because a
            # header said to.
            if length > POST_MAX_BYTES:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                return {"oversize": True}
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _session_by_id(self, session_id: str) -> dict | None:
        for session in STORE.snapshot()["sessions"]:
            if session["sessionId"] == session_id:
                return session
        return None

    def _row_for(self, session_id: str) -> dict | None:
        """The kept row for this session, keeping it now if it is not kept yet.

        Every path that runs something on a session needs the same two things —
        an id and a folder — and a kept row is where they live once the process
        is gone. But a session whose terminal has closed is still on the list for
        a while without being kept, and both *Run it here* and *In a terminal*
        are offered on such a row: nothing holds its transcript, which is the
        only condition either one cares about. Refusing them with "that session
        is not being kept" reported an internal bookkeeping state as if it were
        a fact about the session.

        So the row is kept here, as part of acting on it. It is the same thing
        adopting does before it signals anything, and for the same reason: the
        row is what carries the id and the folder once the session file goes.
        """
        entry = kept_rows().get(session_id)
        if entry:
            return entry
        session = self._session_by_id(session_id)
        if not session or not session.get("cwd"):
            return None
        keep_row({
            "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
            "startedAt": session["startedAt"], "lastSeen": time.time(),
            "version": session["version"], "kind": session["kind"],
        })
        return kept_rows().get(session_id)

    def _session_repo(self, session_id: str) -> str | None:
        """The working tree a git request may act in — the session's own, or none.

        The root never comes from the request. Everything git runs against what
        the panel already discovered for the session it was asked about, so no
        request can point git at a repository the panel is not showing.
        """
        session = self._session_by_id(session_id)
        return (session or {}).get("repoRoot") or None

    # --- routes
    #
    # One table, filled by the decorator above each handler. Core routes are
    # matched first and by exact path, so nothing can shadow /api/state, and an
    # unknown /api/ path is a 404 rather than a file read.

    def do_GET(self) -> None:
        if not self._key_shown():
            self._no_key()
            return
        path = self.path.split("?", 1)[0]
        handler = ROUTES.get(("GET", path))
        if handler:
            getattr(self, handler)()
            return
        self._serve_page(path)

    def do_POST(self) -> None:
        if not self._key_shown():
            # The body goes unread, which is the point: nothing about a request
            # from a stranger is looked at, let alone acted on.
            self._no_key()
            return
        path = self.path.split("?", 1)[0]
        handler = ROUTES.get(("POST", path))
        if not handler:
            self._send(404, b"not found", "text/plain")
            return
        # Read once here rather than in every handler: they all want the body,
        # and all but a couple want the session it names.
        payload = self._body()
        getattr(self, handler)(payload, str(payload.get("sessionId") or ""))

    def _serve_page(self, path: str) -> None:
        """Anything that is not an API route is the built frontend, or nothing."""
        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        # The stylesheet, the modules it imports, the fonts — whatever the build
        # put in dist/. The confinement check in _serve_static is what keeps that
        # honest: only files under the served directory.
        self._serve_static(path)


    @route("GET", "/api/state")
    def _get_state(self) -> None:
        self._json(STORE.snapshot())

    @route("GET", "/api/changelog")
    def _get_changelog(self) -> None:
        """The changelog, as it is written down in the checkout.

        Read from disk on every ask rather than held: it changes when the panel
        updates itself, which is exactly when a held copy would be the old one.
        Sent as text for the browser to render, the way a message is — nothing
        here builds HTML out of a file.
        """
        path = ROOT / "CHANGELOG.md"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:CHANGELOG_MAX]
            when = path.stat().st_mtime
        except OSError:
            # A tarball without it, or a checkout where it has been removed. The
            # button that asks for this is hidden on this answer rather than
            # opening an empty dialog.
            self._json({"ok": False, "message": "This copy of the panel has no changelog"}, 404)
            return
        self._json({"ok": True, "text": text, "at": when})

    @route("GET", "/api/reach")
    def _get_reach(self) -> None:
        """Where a phone should point, for the settings page to show.

        The address is looked up now rather than at startup: a laptop moves
        between networks, and a code pointing at the address it had this morning
        is worse than no code at all. Only a request that got this far sees the
        key, which is the same rule as everything else here — and on loopback
        there is no key to see.
        """
        where = lan_address() if config.SERVE_LAN else None
        key = config.ACCESS_KEY or ""
        url = f"http://{where}:{config.SERVE_PORT}/{f'?k={key}' if key else ''}" if where else ""
        self._json({
            "ok": True,
            "lan": config.SERVE_LAN,
            "address": where or "",
            "port": config.SERVE_PORT,
            "key": key,
            "url": url,
        })

    @route("GET", "/api/qr")
    def _get_qr(self) -> None:
        """The same address as a code to point a camera at.

        Drawn here rather than in the browser because the panel has no packages
        and the encoder is a hundred lines of arithmetic either way — and here it
        can be checked against a reference encoder in a test.
        """
        where = lan_address() if config.SERVE_LAN else None
        if not where:
            self._send(404, b"nothing to point at", "text/plain; charset=utf-8")
            return
        key = config.ACCESS_KEY or ""
        url = f"http://{where}:{config.SERVE_PORT}/{f'?k={key}' if key else ''}"
        try:
            drawn = qr_svg(url)
        except ValueError:
            # A URL longer than a version 6 code holds. The settings page shows
            # the address as text regardless, so this is a missing picture
            # rather than a missing way in.
            self._send(404, b"that address is too long to draw", "text/plain; charset=utf-8")
            return
        self._send(200, drawn.encode(), "image/svg+xml; charset=utf-8")

    @route("GET", "/api/transcript")
    def _get_transcript(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        try:
            limit = max(1, min(TRANSCRIPT_LIMIT_MAX, int((query.get("limit") or ["60"])[0])))
        except ValueError:
            limit = 60
        # The subagents are read here rather than in the transcript reader: that
        # module is what agents.py reads, so it cannot read agents.py back.
        self._json(read_transcript(session_id, session["cwd"], limit,
                                   agents=list_subagents(session_id, session["cwd"])))

    @route("GET", "/api/change")
    def _get_change(self) -> None:
        # The whole of one file change, for a preview in the chat that was
        # clicked. A read of the same transcript the chat came from, so it
        # needs nothing the chat did not already have.
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        found = read_change(session_id, session["cwd"], (query.get("id") or [""])[0])
        self._json(found, 200 if found["ok"] else 404)

    @route("GET", "/api/subagent")
    def _get_subagent(self) -> None:
        # One subagent's conversation, for a tool row in the chat that was
        # tapped. The same shape as /api/transcript, because a subagent's
        # conversation is a conversation and the panel already draws those.
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        try:
            limit = max(1, min(TRANSCRIPT_LIMIT_MAX, int((query.get("limit") or ["60"])[0])))
        except ValueError:
            limit = 60
        found = read_subagent(session_id, session["cwd"],
                              (query.get("agentId") or [""])[0], limit)
        self._json(found, 200 if found["ok"] else 404)

    # What a picture in a conversation may be, and how much of one is worth
    # sending to a browser. The suffix list is the second half of the fence
    # around this route — the first is that the path has to resolve inside your
    # home folder or the session's own, exactly as /api/editor's does.
    PICTURES = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
        ".bmp": "image/bmp", ".svg": "image/svg+xml",
    }
    PICTURE_MAX = 32 * 1024 * 1024

    @route("GET", "/api/file")
    def _get_file(self) -> None:
        """A picture named in a message, so the message can show it.

        The second request in this panel that takes a path rather than a
        session, and behind the same fence as the first: it must exist, and it
        must resolve inside your home folder or the session's own. On top of
        that it must be a picture — the suffix list above — because a route that
        will hand over any readable file is a route for reading files, and that
        is not what this is for.

        Not behind SAY_ENABLED, unlike /api/editor. Nothing here acts on the
        machine: it reads a file the panel is already showing the path of, to
        the same browser, over the same connection. Holding it to loopback would
        mean a phone showing a message full of alt text and nothing else.

        `Content-Security-Policy: sandbox` for the SVG case. An SVG loaded
        through `<img>` cannot run script anyway — every browser disables it —
        but the header is what makes that true of the URL as well, for anyone
        who opens the picture in a tab of its own.
        """
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        entry = kept_rows().get(session_id) or {}
        cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
        spot, refused = _resolve_under((query.get("path") or [""])[0], cwd,
                                       (session or {}).get("repoRoot") or "", temp=True)
        if spot is None:
            self._send(404, refused.encode(), "text/plain; charset=utf-8")
            return
        kind = self.PICTURES.get(spot.suffix.lower())
        if not kind or not spot.is_file():
            self._send(415, b"that is not a picture", "text/plain; charset=utf-8")
            return
        try:
            if spot.stat().st_size > self.PICTURE_MAX:
                self._send(413, b"that picture is too big to send", "text/plain; charset=utf-8")
                return
            body = spot.read_bytes()
        except OSError:
            self._send(404, b"could not read it", "text/plain; charset=utf-8")
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "sandbox; default-src 'none'")
        # A picture in a transcript does not change: the same path is written
        # once and read on every poll that redraws the message.
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    @route("GET", "/api/usage")
    def _get_usage(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        self._json(read_usage(session_id, session["cwd"]))

    @route("GET", "/api/commands")
    def _get_commands(self) -> None:
        # A session that has gone still leaves the folders it could be asked
        # about, so a missing one is answered with what is true of every
        # session — your own skills and commands — rather than a 404.
        query = parse_qs(urlparse(self.path).query)
        session = self._session_by_id((query.get("sessionId") or [""])[0])
        self._json(read_catalog((session or {}).get("cwd")))

    @route("GET", "/api/plan")
    def _get_plan(self) -> None:
        # Reading this runs a command on this machine, which is the same order
        # of risk as the panel's other errands — so it sits behind the same
        # loopback gate, however read-only the answer is.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Reading your plan is off because the panel "
                                                "is not bound to loopback"}, 403)
            return
        query = parse_qs(urlparse(self.path).query)
        self._json(read_plan((query.get("force") or [""])[0] == "1"))

    @route("GET", "/api/update")
    def _get_update(self) -> None:
        # Behind the same gate as the plan reading, and for the same reason: the
        # answer is read-only but getting it runs git and reaches the network, and
        # the button beside it restarts a process on this machine. A panel that
        # cannot act should not be telling you about updates it cannot apply.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "repo": False, "canUpdate": False,
                        "message": "Updating is off because the panel is not bound "
                                   "to loopback"}, 403)
            return
        query = parse_qs(urlparse(self.path).query)
        # The survey is held for hours; what a restart would cost is not. A turn
        # starts and ends inside that window, so the running sessions are read
        # here, fresh, and laid over the cached answer rather than kept in it.
        found = read_update((query.get("force") or [""])[0] == "1")
        self._json({**found, "running": running_here()})

    @route("POST", "/api/update")
    def _post_update(self, payload: dict, session_id: str) -> None:
        # The sharpest thing in this file after /api/say: it moves the panel's own
        # HEAD and replaces the process. Loopback only, and the version comes back
        # from the browser only to be checked against what the server just read —
        # never to be trusted as the thing to check out.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Updating is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        ok, message, restarting = do_update(str(payload.get("tag") or ""))
        self._json({"ok": ok, "message": message, "restarting": restarting}, 200 if ok else 409)

    @route("POST", "/api/update/channel")
    def _post_update_channel(self, payload: dict, session_id: str) -> None:
        # Which line this install follows. Behind the same gate as the update
        # itself, and for the same reason: it decides what that button will
        # check out, so somewhere that cannot press it has no business choosing.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Choosing an update channel is off because the "
                                                "panel is not bound to loopback"}, 403)
            return
        ok, message = write_channel(str(payload.get("channel") or ""))
        self._json({"ok": ok, "message": message, "channel": read_channel()},
                   200 if ok else 400)

    @route("GET", "/api/git")
    def _get_git(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("sessionId") or [""])[0]
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        root = session.get("repoRoot")
        if not root:
            self._json({"ok": False, "isRepo": False,
                        "message": "This session's folder is not in a git repository"})
            return
        self._json({**read_git(root), "isRepo": True, "canWrite": config.SAY_ENABLED})

    @route("GET", "/api/git/diff")
    def _get_git_diff(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        root = self._session_repo((query.get("sessionId") or [""])[0])
        if not root:
            self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
            return
        file_path = (query.get("path") or [""])[0]
        if not file_path:
            self._json({"ok": False, "message": "No file asked for"}, 400)
            return
        self._json(read_diff(root, file_path, (query.get("staged") or [""])[0] == "1"))

    @route("POST", "/api/focus")
    def _post_focus(self, payload: dict, session_id: str) -> None:
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        window, why, identified = resolve_window(session_id, session)
        if not window:
            self._json({"ok": False, "message": why, "needsPairing": True}, 409)
            return
        WINDOWS.windows(force=True)
        ok, message = activate(window["id"])
        if ok and identified:
            message = "focused — and this session's window is now remembered"
        self._json({"ok": ok, "message": message, "window": window["id"],
                    "identified": identified}, 200 if ok else 500)

    @route("POST", "/api/identify")
    def _post_identify(self, payload: dict, session_id: str) -> None:
        # Pairing without the click: the session's own terminal is asked which
        # window it is showing. See probe_window.
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        window_id, message = identify_and_pair(session_id, session)
        if not window_id:
            self._json({"ok": False, "message": message, "needsPairing": True}, 409)
            return
        self._json({"ok": True, "message": "Found it — window identified and remembered",
                    "window": window_id, "title": window_title(window_id)})

    @route("POST", "/api/pair")
    def _post_pair(self, payload: dict, session_id: str) -> None:
        if not self._session_by_id(session_id):
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        window_id, message = select_window()
        if not window_id:
            self._json({"ok": False, "message": message}, 400)
            return
        pairs = load_pairs()
        pairs[session_id] = {"id": window_id, "how": "picked"}
        save_pairs(pairs)
        WINDOWS.windows(force=True)
        self._json({"ok": True, "message": "Window paired", "window": window_id})

    @route("POST", "/api/end")
    def _post_end(self, payload: dict, session_id: str) -> None:
        data = STORE.raw(session_id)
        if not data:
            # A session the panel holds has no session file of its own to
            # signal — the process is ours. Ending it means letting go.
            if owned_release(session_id):
                owned = load_owned()
                if session_id in owned:
                    # The claim to be running it goes; the mode it was in stays.
                    # A pinned row survives this, and typing into it starts it
                    # back up — into the mode you left it in, not into the
                    # default, which is what dropping the whole record had it
                    # doing.
                    owned[session_id] = {"mode": owned[session_id].get("mode") or OWNED_MODES[0],
                                         "here": False}
                    save_owned(owned)
                # Stopping is also removing. Only a pinned row is worth keeping
                # once nothing is running behind it.
                gone = drop_unpinned_row(session_id)
                self._json({"ok": True, "removed": gone,
                            "message": "Stopped it and took the row off the list" if gone
                                       else "Stopped running it here — pinned, so the row stays"})
                return
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        ok, message = end_process(data.get("pid"), data.get("procStart"), bool(payload.get("force")))
        gone = False
        if ok:
            # The window pairing dies with the session it pointed at.
            pairs = load_pairs()
            if pairs.pop(session_id, None) is not None:
                save_pairs(pairs)
            # And so does the row, unless it was pinned: a session the panel
            # only knows because it was running is not worth a row once it is
            # not. A kept row is let go here; an unkept one has nothing holding
            # it and drops off as the process goes.
            gone = drop_unpinned_row(session_id)
            if gone:
                message = "Ended it and took the row off the list"
        self._json({"ok": ok, "removed": gone, "message": message}, 200 if ok else 409)

    @route("POST", "/api/say")
    def _post_say(self, payload: dict, session_id: str) -> None:
        # A prompt is an instruction to an agent with tools, so this endpoint is
        # worth more than the others put together. It stays on loopback even
        # when the rest of the panel is served to the network.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Sending is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            self._json({"ok": False, "message": "Nothing to send"}, 400)
            return
        data = STORE.raw(session_id)
        if not data and session_id not in kept_rows():
            self._json({"ok": False, "message": "There is no such session"}, 404)
            return
        # The straight road: it is up and listening, so the message goes down its
        # socket and the answer is immediate.
        if session_listening(data):
            ok, message = say_to_session(data, text)
            if ok:
                self._json({"ok": True, "message": message})
                return
        # And every other case is the same case. It closed, or it never opened a
        # socket, or the socket went, or it is coming up as we speak: the message
        # is held and delivered when it can be, and the session is started back
        # up if nothing is running it. This is why the composer has no "not
        # listening" dead end left — there is nothing that state would save the
        # person from.
        ok, message = deliver_later(session_id, text)
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/paste-image")
    def _post_paste_image(self, payload: dict, session_id: str) -> None:
        # The same gate as sending, because this is part of sending: a picture is
        # only ever saved to be named in a message, and writing a file into
        # somebody's checkout is not something to offer the network either.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Sending is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        if payload.get("oversize"):
            self._json({"ok": False, "message": f"That picture is larger than "
                                                f"{PASTE_MAX_BYTES // (1024 * 1024)} MB"}, 413)
            return
        # The folder is the session's own, and it is never taken from the
        # request: a live session's cwd, or the folder its kept row carries once
        # the process is gone. There is nowhere else a paste can land.
        session = self._session_by_id(session_id)
        cwd = str((session or {}).get("cwd") or (kept_rows().get(session_id) or {}).get("cwd") or "")
        if not cwd:
            self._json({"ok": False, "message": "There is no folder to save that picture in"}, 404)
            return
        ok, saved, message = save_pasted_image(cwd, str(payload.get("mime") or ""),
                                               str(payload.get("data") or ""))
        if not ok:
            self._json({"ok": False, "message": message}, 400)
            return
        self._json({"ok": True, "path": saved, "message": message})

    @route("POST", "/api/drop-file")
    def _post_drop_file(self, payload: dict, session_id: str) -> None:
        # A dropped file usually needs none of this: it has a path already and
        # the message names it where it lies. This is the drop that came out of a
        # browser's downloads or a mail client, which hands over the bytes and no
        # path at all — so the panel writes a copy the session can read and names
        # that. Same gate as a paste, and for the same reason: it is a write into
        # somebody's checkout, made only to be named in a message.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Sending is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        if payload.get("oversize"):
            self._json({"ok": False, "message": f"That file is larger than "
                                                f"{DROP_MAX_BYTES // (1024 * 1024)} MB"}, 413)
            return
        session = self._session_by_id(session_id)
        cwd = str((session or {}).get("cwd") or (kept_rows().get(session_id) or {}).get("cwd") or "")
        if not cwd:
            self._json({"ok": False, "message": "There is no folder to save that file in"}, 404)
            return
        ok, saved, message = save_dropped_file(cwd, str(payload.get("name") or ""),
                                               str(payload.get("data") or ""))
        if not ok:
            self._json({"ok": False, "message": message}, 400)
            return
        self._json({"ok": True, "path": saved, "message": message})

    @route("POST", "/api/owned/mode")
    def _post_owned_mode(self, payload: dict, session_id: str) -> None:
        # Nothing runs here. The mode is remembered, and the next turn the panel
        # launches is launched with it — which is the whole reason switching is
        # instant.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        mode = str(payload.get("mode") or "")
        if mode not in OWNED_MODES:
            self._json({"ok": False, "message": f"The panel does not run turns in {mode!r}"}, 400)
            return
        ok, message = owned_set_mode(session_id, mode)
        self._json({"ok": ok, "message": message, "mode": mode}, 200 if ok else 400)

    @route("POST", "/api/owned/adopt")
    def _post_owned_adopt(self, payload: dict, session_id: str) -> None:
        # Taking a live session's turns over. There is exactly one way to do it
        # and it is not a gentle one: the transcript is held by a process in a
        # terminal, and nothing can run a turn on it while that is true. So the
        # row is kept first, then the process is ended, and what is left is the
        # same conversation with nobody holding it — which is the state a panel
        # turn needs.
        #
        # This was built once before and backed out, for reasons worth keeping in
        # mind: it killed the session and only cleared the way, leaving a row
        # whose most prominent button handed it straight back to a terminal. What
        # makes it safe now is that the panel can actually take the next turn,
        # that ending is asked about rather than assumed, and that every path
        # which starts a process on a transcript checks who already holds it.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        session = self._session_by_id(session_id)
        data = STORE.raw(session_id)
        if not session or not data:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        # A session that has never taken a turn has no transcript, and `--resume`
        # on it fails with "No conversation found". That used to be a refusal —
        # send it something first — which is backwards: an empty session is the
        # one with nothing to lose, and being told to type into the terminal in
        # order to stop using the terminal makes no sense. So it is taken over
        # like any other, and started under its own id rather than resumed, which
        # is what owned_hold already does for a session with nothing to resume.
        empty = not has_conversation(session_id, session.get("cwd") or "")
        # Kept *before* the process goes. The row is the only thing that carries
        # the folder and the id once the session file is gone, and without it the
        # conversation would drop off the panel on the way.
        keep_row({
            "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
            "startedAt": session["startedAt"], "lastSeen": time.time(),
            "version": session["version"], "kind": session["kind"],
        })
        force = bool(payload.get("force"))
        ok, message = end_process(data.get("pid"), data.get("procStart"), force)
        if not ok:
            # It is still running, so nothing has changed except that the row is
            # now kept — which is harmless and is what the panel would have
            # needed anyway.
            self._json({"ok": False,
                        "message": f"Kept the row, but it is still running: {message}"}, 409)
            return
        # Signalled is not stopped. Waiting for it to actually go is the
        # difference between this and the takeover that shipped once and cleared
        # the way without ever freeing the transcript — the next thing the panel
        # does is run a turn on it, and that refuses while anything still holds it.
        pid = data.get("pid")
        for _ in range(40):
            if not isinstance(pid, int) or proc_gone(pid):
                break
            time.sleep(0.25)
        else:
            self._json({"ok": False, "needsForce": not force,
                        "message": "It has not stopped. Force it to end, or let it finish "
                                   "what it is doing and try again"}, 409)
            return
        pairs = load_pairs()
        if pairs.pop(session_id, None) is not None:
            save_pairs(pairs)
        # The row is wanted back as a kept row now, not in twenty seconds.
        STORE.forget(session_id)
        # The mode its first panel turn will run in, unless one was already
        # picked for it. Manual: the one that asks, now that asking works.
        owned = load_owned()
        owned[session_id] = {
            "mode": (owned.get(session_id) or {}).get("mode") or OWNED_MODES[0],
            "here": True,
        }
        save_owned(owned)
        mode = (owned.get(session_id) or {}).get("mode") or OWNED_MODES[0]
        up, said = owned_hold(session_id, session.get("cwd") or "", mode)
        self._json({"ok": True, "running": up,
                    "message": ("Running here now — it had said nothing, so it starts here empty"
                                if empty else "Running here now") if up
                               else f"Ended the terminal session, but it did not start here: {said}"})

    @route("POST", "/api/owned/new")
    def _post_owned_new(self, payload: dict, session_id: str) -> None:
        # A new session the panel runs from its first word. The folder comes off
        # a session already on the list, or out of a chooser on this machine —
        # never as a path in the request, which is the same rule /api/new keeps.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        if payload.get("pick"):
            folders = {x["cwd"] for x in STORE.snapshot()["sessions"] if x.get("cwd")}
            cwd, why = pick_folder(folders.pop() if len(folders) == 1 else str(HOME))
            if not cwd:
                self._json({"ok": False, "cancelled": True, "message": why}, 200)
                return
        else:
            session = self._session_by_id(session_id)
            entry = kept_rows().get(session_id) or {}
            cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
            if not cwd:
                self._json({"ok": False, "message": "There is no folder to start it in"}, 404)
                return
        mode = str(payload.get("mode") or OWNED_MODES[0])
        made, message = owned_new(cwd, mode)
        self._json({"ok": bool(made), "message": message, "sessionId": made, "cwd": cwd},
                   200 if made else 409)

    @route("POST", "/api/owned/answer")
    def _post_owned_answer(self, payload: dict, session_id: str) -> None:
        # Answering a prompt a panel turn raised. Same gate as running the turn:
        # this decides what a session holding tools is allowed to do, which is
        # the sharpest thing the panel does.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        behavior = "allow" if payload.get("behavior") == "allow" else "deny"
        answers = payload.get("answers")
        decision = {
            "behavior": behavior,
            "message": str(payload.get("message") or "")[:300],
            "answers": answers if isinstance(answers, dict) else None,
        }
        ok, message = answer_from_panel(session_id, str(payload.get("requestId") or ""), decision)
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/owned/say")
    def _post_owned_say(self, payload: dict, session_id: str) -> None:
        # Same gate as /api/say, and for a sharper version of the same reason:
        # this one does not hand a message to a session someone else is running,
        # it runs the session.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        # A live process holds the transcript, and nothing here will take it off
        # one. The row's own End is how you free it, deliberately.
        if STORE.raw(session_id):
            self._json({"ok": False, "message": "Something is already running this session — "
                                                "end it first, or send to it instead"}, 409)
            return
        entry = self._row_for(session_id)
        if not entry:
            self._json({"ok": False, "message": "There is no folder to run that session in"}, 404)
            return
        cwd = str(entry.get("cwd") or "")
        owned = load_owned()
        mode = str(payload.get("mode") or (owned.get(session_id) or {}).get("mode") or OWNED_MODES[0])
        text = str(payload.get("text") or "")
        # No message, just run it. *Start it up* on the row menu asks for exactly
        # this and was being told there was nothing to send: holding the session
        # open is the whole of starting it, and a turn is what a message adds
        # rather than what makes the session run.
        ok, message = (owned_hold(session_id, cwd, mode) if not text.strip()
                       else owned_say(session_id, cwd, text, mode))
        if ok:
            # `here` is set by the turn, not only by adopting. It was not, and
            # the gap is what made a session go strange under you: the panel
            # would hold the process and run the turn while the record still said
            # the session was nobody's, so the moment the status left `stopped`
            # the row lost its mode chips and offered to make interactive a
            # session it was in the middle of running.
            owned[session_id] = {"mode": mode, "here": True}
            save_owned(owned)
        self._json({"ok": ok, "message": message, "mode": mode}, 200 if ok else 409)

    @route("POST", "/api/owned/compact")
    def _post_owned_compact(self, payload: dict, session_id: str) -> None:
        # Summarise the conversation so far and carry on from the summary.
        #
        # `/compact` is on the panel's TERMINAL_ONLY list, and rightly: a message
        # over a session's *messaging socket* is queued with slash commands
        # switched off, so sending the text there does nothing. A held pipe is
        # the other transport and it does expand them — checked against 2.1.239,
        # which answered a `/compact` turn with `compact_boundary` and
        # 24,071 → 3,661 tokens. So this is not the composer sending text that
        # happens to start with a slash; it is its own action, on the one
        # transport where it works.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        if STORE.raw(session_id):
            self._json({"ok": False, "message": "Something else is running this session — "
                                                "make it interactive first"}, 409)
            return
        entry = self._row_for(session_id)
        if not entry:
            self._json({"ok": False, "message": "There is no folder to run that session in"}, 404)
            return
        # Not while anything is running or waiting. `owned_say` would queue it,
        # which is right for a message and wrong for this: a compaction is not
        # typed ahead, it rewrites what the session remembers, and queueing one
        # would report *Compacting…* for a compaction that had not started and
        # would fire later without being asked again.
        with _OWNED_LOCK:
            busy = session_id in OWNED_BUSY or bool(OWNED_QUEUE.get(session_id))
            already = bool((OWNED_COMPACT.get(session_id) or {}).get("running"))
        if busy:
            self._json({"ok": False, "message": "It is mid-turn — let that finish, then "
                                                "compact"}, 409)
            return
        if already:
            self._json({"ok": False, "message": "It is already compacting"}, 409)
            return
        owned = load_owned()
        mode = str((owned.get(session_id) or {}).get("mode") or OWNED_MODES[0])
        ok, message = owned_say(session_id, str(entry.get("cwd") or ""), "/compact", mode)
        if ok:
            with _OWNED_LOCK:
                # Said now rather than waited for. The first `compacting` frame
                # does not arrive instantly, and a button that goes back to
                # looking unpressed in the meantime invites a second press —
                # which would queue a second compaction behind the first.
                OWNED_COMPACT[session_id] = {"at": time.time(), "running": True}
            owned[session_id] = {"mode": mode, "here": True}
            save_owned(owned)
            message = "Compacting — it summarises the conversation and carries on from that"
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/owned/interrupt")
    def _post_owned_interrupt(self, payload: dict, session_id: str) -> None:
        # Stopping a turn acts on a process on this machine, so it sits behind
        # the same loopback gate as starting one.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Stopping a turn is off because the panel is "
                                                "not bound to loopback"}, 403)
            return
        ok, message = owned_interrupt(session_id)
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/owned/unqueue")
    def _post_owned_unqueue(self, payload: dict, session_id: str) -> None:
        # Taking back something typed ahead. Behind the same gate as sending it,
        # for the plainest of reasons: nothing that cannot type at a session has
        # anything to untype.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        raw = payload.get("index")
        index = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        ok, message = owned_unqueue(session_id, index)
        self._json({"ok": ok, "message": message, "queued": owned_queued(session_id)},
                   200 if ok else 409)

    @route("POST", "/api/owned/clear")
    def _post_owned_clear(self, payload: dict, session_id: str) -> None:
        """Start this session's conversation again, empty.

        The same argument as /api/owned/compact, and the same one transport:
        `/clear` over a session's messaging socket is queued with expansion
        switched off and arrives as prose, so this is offered for a session the
        panel holds and refused for one in a terminal — where it is the
        terminal's own command and always was.

        The answer carries the id the session has *become*. Clearing does not
        empty a conversation in place: Claude Code starts a new one and reports a
        new session_id from then on, so the browser is told where to look rather
        than left watching a row that has moved.
        """
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        if STORE.raw(session_id):
            self._json({"ok": False, "message": "That session is in a terminal, and /clear is the "
                                                "terminal's own — make it interactive first"}, 409)
            return
        ok, message, moved = owned_clear(session_id)
        self._json({"ok": ok, "message": message, "sessionId": moved},
                   200 if ok else 409)

    @route("POST", "/api/owned/resume")
    def _post_owned_resume(self, payload: dict, session_id: str) -> None:
        # Letting go of a queue that a stop held back. Same gate as sending,
        # because that is what it is: the messages go down the pipe.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": OFF_HERE}, 403)
            return
        ok, message = owned_resume(session_id)
        self._json({"ok": ok, "message": message, "queued": owned_queued(session_id)},
                   200 if ok else 409)

    @route("POST", "/api/git")
    def _post_git(self, payload: dict, session_id: str) -> None:
        # Committing, pushing and discarding change a checkout on this machine,
        # which is the same order of risk as prompting the session that lives in
        # it — so they sit behind the same loopback gate.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Git actions are off — this panel is serving "
                                                "read-only"}, 403)
            return
        root = self._session_repo(session_id)
        if not root:
            self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
            return
        action = str(payload.get("action") or "")
        # The one action that answers with something other than a sentence about
        # what it did: the message it wrote, for the box to hold.
        if action == "suggestMessage":
            ok, said = suggest_message(root)
            self._json({"ok": ok, "text": said if ok else "",
                        "message": "" if ok else said}, 200 if ok else 409)
            return
        ok, message, status = git_action(root, action, payload)
        self._json({"ok": ok, "message": message}, status)

    @route("POST", "/api/sticky")
    def _post_sticky(self, payload: dict, session_id: str) -> None:
        # Pinning: the row is written down, and is the only kind that comes back
        # after a restart — and now the only kind that survives its own session
        # being ended. Unpinning does not take the row away by itself: a session
        # the panel is running still has one for as long as it runs. Ending it is
        # what takes it, or /api/forget for a row with nothing left to end.
        session = self._session_by_id(session_id)
        want = bool(payload.get("pinned", payload.get("sticky", True)))
        if want:
            # Either a live session or a row the panel is already holding —
            # pinning an interactive session it started is the whole point, and
            # by then there is no session file to read it out of.
            held = kept_rows().get(session_id) or {}
            if not session and not held:
                self._json({"ok": False, "message": "There is no such session"}, 404)
                return
            pin_row(session_id, {
                "sessionId": session_id,
                "name": session["defaultName"] if session else held.get("name"),
                "cwd": (session["cwd"] if session else held.get("cwd")) or "",
                "startedAt": session["startedAt"] if session else held.get("startedAt"),
                "lastSeen": time.time(),
                "version": session["version"] if session else held.get("version"),
                "kind": (session["kind"] if session else held.get("kind")) or "interactive",
            })
            self._json({"ok": True, "message": "Pinned — it survives a restart", "pinned": True})
            return
        unpin_row(session_id)
        with _KEPT_LOCK:
            still = session_id in _KEPT
        self._json({"ok": True, "pinned": False,
                    "message": "No longer pinned — kept until the panel restarts" if still
                               else "No longer kept"})

    @route("POST", "/api/forget")
    def _post_forget(self, payload: dict, session_id: str) -> None:
        # Removing the row. Nothing about the conversation goes with it: the
        # transcript is Claude Code's, where it always was, and `claude --resume`
        # in that folder still finds it. What goes is the panel's memory of it —
        # and the process, if the panel was running one, because a held process
        # with no row is a session nobody can see.
        held = owned_running(session_id)
        if held:
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "It is running here, and stopping it needs "
                                                    "the panel bound to loopback"}, 403)
                return
            owned_release(session_id)
        owned = load_owned()
        if owned.pop(session_id, None) is not None:
            save_owned(owned)
        gone = forget_row(session_id)
        STORE.forget(session_id)
        if not gone and not held:
            self._json({"ok": False, "message": "There is no kept row to remove"}, 404)
            return
        self._json({"ok": True, "message": "Stopped it and took the row off the list" if held
                                           else "Took the row off the list"})

    @route("POST", "/api/start")
    def _post_start(self, payload: dict, session_id: str) -> None:
        # Starting a session runs a command on this machine, which is the same
        # order of risk as sending it a prompt — so it lives behind the same
        # loopback gate.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Starting is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        entry = self._row_for(session_id)
        if not entry:
            self._json({"ok": False, "message": "There is no folder to start that session in"}, 404)
            return
        # A terminal opened on a transcript a panel turn is mid-way through is
        # the two-processes-one-conversation failure, arriving by the politest
        # possible route. Every path that starts a process on a session asks this.
        with _OWNED_LOCK:
            mid_turn = session_id in OWNED_BUSY
            # Standing on a prompt is mid-turn as well, and refused on the same
            # grounds — but that turn ends when the ask is answered and never on
            # its own, so it cannot be told to wait for it. Same word as the
            # button, which is where this is read from.
            on_ask = mid_turn and session_id in OWNED_ASK
        if mid_turn:
            self._json({"ok": False,
                        "message": "It is waiting on a permission answer — answer it, or stop "
                                   "the turn, first" if on_ask else
                                   "A turn from the panel is running on it — let it finish first"},
                       409)
            return
        # Handing it back is the one thing that legitimately takes the transcript
        # off the panel, so it lets go rather than refusing.
        owned_release(session_id)
        owned = load_owned()
        if owned.pop(session_id, None) is not None:
            save_owned(owned)
        if STORE.raw(session_id):
            self._json({"ok": False, "message": "That session is already running"}, 409)
            return
        ok, message = start_session(entry)
        text = str(payload.get("text") or "").strip()
        if ok and text:
            # It cannot hear us yet, and the terminal is already opening — so the
            # deliverer waits for the socket without opening a second one
            # (`started`).
            deliver_later(session_id, text, started=True)
            message = "Starting it up — your message goes in as soon as it is listening"
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/editor")
    def _post_editor(self, payload: dict, session_id: str) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Opening an editor is off because the panel "
                                                "is not bound to loopback"}, 403)
            return
        session = self._session_by_id(session_id)
        entry = kept_rows().get(session_id) or {}
        cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
        if not session and not entry:
            self._json({"ok": False, "message": "That session is no longer around"}, 404)
            return
        # Without a path this is the header's button, and the folder is the
        # session's own — the request names a session and nothing more.
        raw = str(payload.get("path") or "").strip()
        if not raw:
            ok, message = open_editor(cwd)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return
        # With one it is a path clicked out of the conversation, which is the one
        # place the panel takes a path from the browser at all. What keeps that
        # from being "open anything on this machine": it must already exist, and
        # it must be inside your home folder or inside this session's own — the
        # two places a path in this session's transcript can honestly be about.
        # Resolved before it is judged, so neither .. nor a symlink walks out.
        spot, refused = _resolve_under(raw, cwd, (session or {}).get("repoRoot") or "")
        if spot is None:
            self._json({"ok": False, "message": refused}, 403)
            return
        # A file goes to the editor, at the line the message quoted. A folder
        # goes to the desktop's own file manager: a path in a conversation is as
        # often a place things are kept as a place code is written, and an
        # editor pointed at a build directory is not what was wanted.
        line = payload.get("line")
        ok, message = (show_folder(str(spot)) if spot.is_dir()
                       else open_editor(str(spot),
                                        int(line) if isinstance(line, (int, float)) else None))
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/new")
    def _post_new(self, payload: dict, session_id: str) -> None:
        # Same risk as /api/start — it runs a command on this machine — so it
        # sits behind the same loopback gate.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Starting is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        session = self._session_by_id(session_id)
        entry = kept_rows().get(session_id) or {}
        cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
        if not session and not entry:
            self._json({"ok": False, "message": "That session is no longer around"}, 404)
            return
        ok, message = new_session(cwd)
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/new-folder")
    def _post_new_folder(self, payload: dict, session_id: str) -> None:
        # /api/new can only reach a folder the panel is already showing, because
        # it reads the folder off a session rather than off the request. This
        # reaches anywhere — but still not by being told where: it opens a
        # chooser on this machine and uses what the person at the desk picked in
        # it. The request says "ask", never "here". Same loopback gate as
        # everything that starts a process.
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Starting is off because the panel is not bound "
                                                "to loopback"}, 403)
            return
        # Open where the sessions are, when they agree on one place, rather than
        # at home every time. A hint about where to *start* is not the folder it
        # returns, so this one may come off the list.
        folders = {s["cwd"] for s in STORE.snapshot()["sessions"] if s.get("cwd")}
        start = folders.pop() if len(folders) == 1 else str(HOME)
        picked, message = pick_folder(start)
        if not picked:
            # Cancelling is the ordinary outcome, not an error: 200, and the UI
            # says what happened without dressing it as a failure.
            self._json({"ok": False, "cancelled": True, "message": message}, 200)
            return
        ok, message = new_session(picked)
        self._json({"ok": ok, "message": message, "cwd": picked}, 200 if ok else 409)

    @route("POST", "/api/rename")
    def _post_rename(self, payload: dict, session_id: str) -> None:
        session = self._session_by_id(session_id)
        if not session:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        # An empty name — or the session's own name typed back — clears the
        # override rather than storing a second copy of the default.
        name = clean_name(payload.get("name"))
        names = load_names()
        keep = bool(name) and name != session["defaultName"]
        if keep:
            names[session_id] = name
            save_names(names)
        elif names.pop(session_id, None) is not None:
            save_names(names)
        self._json({
            "ok": True,
            "message": "Renamed" if keep else "Name reset",
            "name": name if keep else session["defaultName"],
        })

    @route("POST", "/api/unpair")
    def _post_unpair(self, payload: dict, session_id: str) -> None:
        pairs = load_pairs()
        if pairs.pop(session_id, None) is not None:
            save_pairs(pairs)
        self._json({"ok": True, "message": "Pairing cleared"})


    MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }

    def _serve_static(self, name: str, content_type: str | None = None) -> None:
        target = (STATIC_DIR / name.lstrip("/")).resolve()
        # Never serve outside the static directory.
        if not str(target).startswith(str(STATIC_DIR) + os.sep) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        kind = content_type or self.MIME.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), kind)

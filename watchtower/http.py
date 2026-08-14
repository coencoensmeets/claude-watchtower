"""The web layer: what the browser may ask for, and what it gets back.

Two rules hold across every route here. A request never names a path on this
machine — the panel acts on what it already discovered for the session it was
asked about, so no request can point git at a repository the panel is not
showing. And anything that acts is behind config.SAY_ENABLED, which is false
unless the panel is bound to loopback and sending is switched on.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from watchtower import config
from watchtower.catalog import read_catalog
from watchtower.config import STATIC_DIR
from watchtower.control import (
    load_sticky, locate_folder, new_session, resolve_folder, save_sticky, start_session,
)
from watchtower.git.actions import git_action
from watchtower.git.message import suggest_message
from watchtower.git.read import read_diff, read_git
from watchtower.input import end_process, say_to_session
from watchtower.plan import read_plan
from watchtower.store import STORE
from watchtower.transcript import TRANSCRIPT_LIMIT_MAX, read_transcript
from watchtower.usage import read_usage
from watchtower.windows import (
    WINDOWS, activate, clean_name, identify_and_pair, load_names, load_pairs, resolve_window,
    save_names, save_pairs, select_window, window_title,
)


def wait_and_say(session_id: str, text: str, seconds: float = 90.0) -> None:
    """Deliver a message once the session it was meant for is listening again.

    Typing into a stopped session starts it up, and startup is not instant — the
    process has to come up and open its socket. This waits for that in the
    background so the click returns at once.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(1.0)
        data = STORE.raw(session_id)
        if not data or not data.get("messagingSocketPath"):
            continue
        if not Path(data["messagingSocketPath"]).exists():
            continue
        ok, _ = say_to_session(data, text)
        if ok:
            return


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



class Handler(BaseHTTPRequestHandler):
    server_version = "claude-watchtower"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # --- helpers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _session_by_id(self, session_id: str) -> dict | None:
        for session in STORE.snapshot()["sessions"]:
            if session["sessionId"] == session_id:
                return session
        return None

    def _session_repo(self, session_id: str) -> str | None:
        """The working tree a git request may act in — the session's own, or none.

        The root never comes from the request. Everything git runs against what
        the panel already discovered for the session it was asked about, so no
        request can point git at a repository the panel is not showing.
        """
        session = self._session_by_id(session_id)
        return (session or {}).get("repoRoot") or None

    # --- routes

    # --- routes
    #
    # One table, filled by the decorator above each handler. Core routes are
    # matched first and by exact path, so nothing can shadow /api/state, and an
    # unknown /api/ path is a 404 rather than a file read.

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        handler = ROUTES.get(("GET", path))
        if handler:
            getattr(self, handler)()
            return
        self._serve_page(path)

    def do_POST(self) -> None:
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
        self._json(read_transcript(session_id, session["cwd"], limit))

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
        query = parse_qs(urlparse(self.path).query)
        session = self._session_by_id((query.get("sessionId") or [""])[0])
        self._json(read_catalog((session or {}).get("cwd")))

    @route("GET", "/api/plan")
    def _get_plan(self) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Reading your plan is off because the panel "
                                                "is not bound to loopback"}, 403)
            return
        query = parse_qs(urlparse(self.path).query)
        self._json(read_plan((query.get("force") or [""])[0] == "1"))

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
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        ok, message = end_process(data.get("pid"), data.get("procStart"), bool(payload.get("force")))
        if ok:
            # The window pairing dies with the session it pointed at.
            pairs = load_pairs()
            if pairs.pop(session_id, None) is not None:
                save_pairs(pairs)
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/say")
    def _post_say(self, payload: dict, session_id: str) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Sending is off because the panel is not bound to loopback"}, 403)
            return
        data = STORE.raw(session_id)
        if not data:
            self._json({"ok": False, "message": "That session is no longer running"}, 404)
            return
        ok, message = say_to_session(data, str(payload.get("text") or ""))
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/locate")
    def _post_locate(self, payload: dict, session_id: str) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Browsing folders is off because the panel "
                                                "is not bound to loopback"}, 403)
            return
        children = payload.get("children")
        found, why = locate_folder(str(payload.get("name") or ""),
                                   [str(c) for c in children] if isinstance(children, list) else [])
        if not found:
            self._json({"ok": False, "message": why}, 404)
            return
        self._json({"ok": True, "folders": found})

    @route("POST", "/api/git")
    def _post_git(self, payload: dict, session_id: str) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Git actions are off — this panel is serving read-only"}, 403)
            return
        root = self._session_repo(session_id)
        if not root:
            self._json({"ok": False, "message": "That session is not in a git repository"}, 404)
            return
        action = str(payload.get("action") or "")
        # The one action that answers with something other than a sentence
        # about what it did: the message it wrote, for the box to hold.
        if action == "suggestMessage":
            ok, said = suggest_message(root)
            self._json({"ok": ok, "text": said if ok else "",
                        "message": "" if ok else said}, 200 if ok else 409)
            return
        ok, message, status = git_action(root, action, payload)
        self._json({"ok": ok, "message": message}, status)

    @route("POST", "/api/sticky")
    def _post_sticky(self, payload: dict, session_id: str) -> None:
        session = self._session_by_id(session_id)
        sticky = load_sticky()
        want = bool(payload.get("sticky", True))
        if want:
            if not session:
                self._json({"ok": False, "message": "There is no such session"}, 404)
                return
            sticky[session_id] = {
                "sessionId": session_id, "name": session["defaultName"], "cwd": session["cwd"],
                "startedAt": session["startedAt"], "lastSeen": time.time(),
                "version": session["version"], "kind": session["kind"],
            }
            save_sticky(sticky)
            self._json({"ok": True, "message": "Kept in the dashboard", "sticky": True})
            return
        if sticky.pop(session_id, None) is not None:
            save_sticky(sticky)
        self._json({"ok": True, "message": "No longer kept", "sticky": False})

    @route("POST", "/api/start")
    def _post_start(self, payload: dict, session_id: str) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
            return
        entry = load_sticky().get(session_id)
        if not entry:
            self._json({"ok": False, "message": "That session is not being kept"}, 404)
            return
        if STORE.raw(session_id):
            self._json({"ok": False, "message": "That session is already running"}, 409)
            return
        ok, message = start_session(entry)
        text = str(payload.get("text") or "").strip()
        if ok and text:
            # It cannot hear us yet. Hand the message to a thread that waits
            # for its socket and then delivers it.
            threading.Thread(target=wait_and_say, args=(session_id, text), daemon=True).start()
            message = "Starting it up — your message goes in as soon as it is listening"
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

    @route("POST", "/api/new")
    def _post_new(self, payload: dict, session_id: str) -> None:
        if not config.SAY_ENABLED:
            self._json({"ok": False, "message": "Starting is off because the panel is not bound to loopback"}, 403)
            return
        # A folder named in the request opens a session there. This is a real
        # widening: every other form of this route took the folder from a
        # session already on screen, so it could not be pointed anywhere the
        # panel was not already showing. What is left holding it is the
        # loopback gate — and anyone through that gate can already put a
        # prompt into a session that holds tools and a checkout, which is the
        # greater power of the two. The path is still resolved and checked
        # before anything runs.
        if isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
            folder, why = resolve_folder(payload["cwd"])
            if not folder:
                self._json({"ok": False, "message": why}, 400)
                return
            ok, message = new_session(folder)
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return
        session = self._session_by_id(session_id)
        entry = load_sticky().get(session_id) or {}
        cwd = (session or {}).get("cwd") or entry.get("cwd") or ""
        if not session and not entry:
            self._json({"ok": False, "message": "That session is no longer around"}, 404)
            return
        ok, message = new_session(cwd)
        self._json({"ok": ok, "message": message}, 200 if ok else 409)

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

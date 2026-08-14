#!/usr/bin/env python3
"""claude-watchtower — a live panel for every Claude Code session on this machine.

Reads ~/.claude/sessions/*.json, which Claude Code keeps current with one file
per running session (pid, name, cwd, status). Status is one of:

    busy     working right now
    waiting  blocked on you — a question or a permission prompt
    shell    running a foreground shell command
    idle     finished, waiting for your next prompt

A busy or shell reading is only believed while the session keeps refreshing it;
see effective_status. Some sessions — the VS Code extension among them — write
no status at all, and are read from their liveness instead. A session started
from inside another one writes no file at all; the panel builds one for it out
of /proc — see child_session.

Serves a small web UI and can raise the terminal or editor window that owns a
session, using xdotool on X11.

Standard library only. No install step.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from watchtower import build, config
from watchtower.config import (
    SESSION_DIR, STATIC_DIR,
)


# ----------------------------------------------------------------- proc helpers


# --------------------------------------------- sessions that write no own file


# --------------------------------------------------------------- sending input


# -------------------------------------------------------------------- git repo


# --------------------------------------------------------------- git commands


# --------------------------------------------------------------- git writes


# ------------------------------------------------------- writing the message


# Knowing our own pids only hides our own errands. A second panel on another
# port — a test instance, a second window's server — reads the same folder, so
# its `/usage` run is somebody else's pid and lands in the list as a session
# that arrives, says nothing and goes; and even our own leaves a row behind for
# the twenty seconds the store keeps a session it can no longer see.
#
# A headless run says what it is in its own file. `claude -p` and the SDKs write
# an `sdk-*` entrypoint where a session you can type at writes `cli` (or
# `claude-vscode`), and no amount of watching one will ever let you answer it.
# So they are left out by what they are rather than by who started them, which
# holds however many panels are running and after the process is gone.


# --------------------------------------------------------------- the plan left


# ------------------------------------------------------ what it can be asked for


# --------------------------------------------------------------- last activity


# ------------------------------------------------------------- permission mode


# --------------------------------------------------------- the question on screen


# ------------------------------------------------------------------ the subject


# ------------------------------------------------------------- tokens and cost


# ------------------------------------------------------------------- X11 windows


# ------------------------------------------------------------- sticky sessions
# A session file disappears when its process does, and with it the row. A sticky
# session keeps its row: the panel remembers enough about it — id, name, folder —
# to go on showing the conversation, and can start Claude Code back up on that
# same transcript with `claude --resume`.


from watchtower.transcript import TRANSCRIPT_LIMIT_MAX, read_transcript
from watchtower.usage import read_usage
from watchtower.catalog import read_catalog
from watchtower.windows import WINDOWS, load_pairs, save_pairs, load_names, save_names, clean_name, window_title, activate, select_window, identify_and_pair, resolve_window
from watchtower.control import load_sticky, save_sticky, start_session, resolve_folder, locate_folder, new_session
from watchtower.git.read import read_git, read_diff
from watchtower.git.message import suggest_message
from watchtower.plan import read_plan
from watchtower.git.actions import git_action
from watchtower.input import is_loopback, say_to_session, end_process
from watchtower.store import STORE


# ------------------------------------------------------------------ session store


# ------------------------------------------------------------------- http server


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

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._json(STORE.snapshot())
            return
        if path == "/api/transcript":
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
            return
        if path == "/api/usage":
            query = parse_qs(urlparse(self.path).query)
            session_id = (query.get("sessionId") or [""])[0]
            session = self._session_by_id(session_id)
            if not session:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            self._json(read_usage(session_id, session["cwd"]))
            return
        if path == "/api/commands":
            # A session that has gone still leaves the folders it could be asked
            # about, so a missing one is answered with what is true of every
            # session — your own skills and commands — rather than a 404.
            query = parse_qs(urlparse(self.path).query)
            session = self._session_by_id((query.get("sessionId") or [""])[0])
            self._json(read_catalog((session or {}).get("cwd")))
            return
        if path == "/api/plan":
            # Reading this runs a command on this machine, which is the same order
            # of risk as the panel's other errands — so it sits behind the same
            # loopback gate, however read-only the answer is.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Reading your plan is off because the panel "
                                                    "is not bound to loopback"}, 403)
                return
            query = parse_qs(urlparse(self.path).query)
            self._json(read_plan((query.get("force") or [""])[0] == "1"))
            return
        if path == "/api/git":
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
            return
        if path == "/api/git/diff":
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
            return
        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        # Everything else the page asks for — the stylesheet, the modules it
        # imports, the fonts — is whatever the build put in dist/. The
        # confinement check in _serve_static is what keeps that honest, and it
        # is the same check as before: only files under the served directory.
        self._serve_static(path)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        payload = self._body()
        session_id = str(payload.get("sessionId") or "")

        if path == "/api/focus":
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
            return

        if path == "/api/identify":
            # Pairing without the click: the session's own terminal is asked
            # which window it is showing. See probe_window.
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
            return

        if path == "/api/pair":
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
            return

        if path == "/api/end":
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
            return

        if path == "/api/say":
            # A prompt is an instruction to an agent with tools, so this endpoint
            # is worth more than the others put together. It stays on loopback
            # even when the rest of the panel is served to the network.
            if not config.SAY_ENABLED:
                self._json({"ok": False, "message": "Sending is off because the panel is not bound to loopback"}, 403)
                return
            data = STORE.raw(session_id)
            if not data:
                self._json({"ok": False, "message": "That session is no longer running"}, 404)
                return
            ok, message = say_to_session(data, str(payload.get("text") or ""))
            self._json({"ok": ok, "message": message}, 200 if ok else 409)
            return

        if path == "/api/locate":
            # Where the folder you picked in the browser's own dialog actually is.
            # Same gate as the listing it feeds: it reads this machine's
            # filesystem, and exists only to start a session.
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
            return

        if path == "/api/git":
            # Committing, pushing and discarding change a checkout on this
            # machine, which is the same order of risk as prompting the session
            # that lives in it — so they sit behind the same loopback gate.
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
            return

        if path == "/api/sticky":
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
            return

        if path == "/api/start":
            # Starting a session runs a command on this machine, which is the same
            # order of risk as sending it a prompt — so it lives behind the same
            # loopback gate.
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
            return

        if path == "/api/new":
            # Same risk as /api/start — it runs a command on this machine — so it
            # sits behind the same loopback gate.
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
            return

        if path == "/api/rename":
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
            return

        if path == "/api/unpair":
            pairs = load_pairs()
            if pairs.pop(session_id, None) is not None:
                save_pairs(pairs)
            self._json({"ok": True, "message": "Pairing cleared"})
            return

        self._send(404, b"not found", "text/plain")

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Live panel for local Claude Code sessions")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-send", action="store_true",
                        help="serve the conversation read-only, with no way to send input")
    parser.add_argument("--build", action="store_true",
                        help="build the frontend and exit")
    parser.add_argument("--no-build", action="store_true",
                        help="serve whatever is already built, however stale")
    args = parser.parse_args()

    if args.build:
        ok, said = build.build()
        if said:
            print(said, file=sys.stderr)
        raise SystemExit(0 if ok else 1)
    if not args.no_build and not build.ensure_built():
        raise SystemExit(1)

    config.SAY_ENABLED = is_loopback(args.host) and not args.no_send

    if not SESSION_DIR.exists():
        print(f"warning: {SESSION_DIR} does not exist yet — start a Claude Code session first")

    STORE.sample()
    threading.Thread(target=STORE.run_forever, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"claude-watchtower → http://{args.host}:{args.port}")
    if not WINDOWS.available():
        print("note: xdotool/DISPLAY unavailable, so window focusing is switched off")
    if not config.SAY_ENABLED:
        why = "--no-send" if args.no_send else f"not bound to loopback ({args.host})"
        print(f"note: sending input is switched off — {why}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

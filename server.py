#!/usr/bin/env python3
"""claude-watchtower — a live panel for every Claude Code session on this machine.

Reads ~/.claude/sessions/*.json, which Claude Code keeps current with one file
per running session (pid, name, cwd, status). Status is one of:

    busy     working right now
    waiting  blocked on you — a question or a permission prompt
    shell    running a foreground shell command
    idle     finished, waiting for your next prompt

A busy or shell reading is only believed while the session keeps refreshing it;
see watchtower.store.effective_status. Some sessions — the VS Code extension
among them — write no status at all, and are read from their liveness instead.
A session started from inside another one writes no file at all; the panel
builds one for it out of /proc — see watchtower.sessions.child_session.

Serves a small web UI and can raise the terminal or editor window that owns a
session, using xdotool on X11.

This file is the way in and nothing else: parse the arguments, build the
frontend if its sources have changed, start the polling thread, serve. The panel
itself is the watchtower package — see docs/cleanup-plan.md for how it is laid
out and why.

Python standard library only. The frontend build needs a Node binary and no
packages at all; `python3 server.py` runs it for you.
"""

from __future__ import annotations

import argparse
import atexit
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from watchtower import build, config, listen
from watchtower.config import SESSION_DIR
from watchtower.http import Handler
from watchtower.input import is_loopback
from watchtower.owned import owned_release_all, owned_resume_held
from watchtower.store import STORE
from watchtower.windows import WINDOWS


def main() -> None:
    parser = argparse.ArgumentParser(description="Live panel for local Claude Code sessions")
    # No default port. A first run picks one for this install and writes it down,
    # and every run after that is on the same one — see watchtower.listen, and
    # `--port` here for the run where you want a different one.
    parser.add_argument("--port", type=int, default=None,
                        help="serve on this port for this run only; the remembered one is left alone")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--lan", action="store_true",
                        help="serve to the local network as well, so a phone can open it "
                             "(the same as --host 0.0.0.0)")
    parser.add_argument("--no-key", action="store_true",
                        help="with --lan, answer anyone who can reach the port — no key, and "
                             "read-only, since there is then nothing between the network and the "
                             "composer")
    parser.add_argument("--new-key", action="store_true",
                        help="throw away the remembered key and make another")
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

    host = "0.0.0.0" if args.lan else args.host
    port, picked = listen.chosen_port(args.port)

    # Set before anything can serve a request: every route reads one of these.
    #
    # The key is what a panel on the network has instead of the loopback bind
    # that used to be the whole of its protection: nothing off this machine is
    # answered without it, so a phone on your own wifi can send a turn while a
    # laptop on the café's cannot. Which is why sending survives --lan now,
    # where it used to be switched off by the bind alone.
    #
    # --no-key takes the key away, and takes sending with it. A panel that
    # answers anyone who can reach the port must not also be a panel anyone who
    # can reach the port may prompt an agent through.
    config.ACCESS_KEY = None if (is_loopback(host) or args.no_key) else listen.access_key(args.new_key)
    config.SAY_ENABLED = (is_loopback(host) or bool(config.ACCESS_KEY)) and not args.no_send

    if not SESSION_DIR.exists():
        print(f"warning: {SESSION_DIR} does not exist yet — start a Claude Code session first")

    # One sample up front, so the first page load has something to draw.
    STORE.sample()
    threading.Thread(target=STORE.run_forever, daemon=True).start()

    # A held session is a `claude` of ours sitting on somebody's transcript. If
    # the panel goes without letting go, it is left there with nothing to send to
    # it and nobody to reap it — which is the two-processes-one-conversation
    # hazard arriving by the back door. Ctrl-C runs the `finally` below; a plain
    # `kill` would not, so it is caught too.
    atexit.register(owned_release_all)
    # Whatever was interactive when the panel last stopped is interactive again.
    # In a thread: each one is a process to start, and the panel should be
    # answering before the first of them is up.
    threading.Thread(target=owned_resume_held, daemon=True).start()

    def bow_out(_signum, _frame):
        owned_release_all()
        raise SystemExit(0)

    for caught in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(caught, bow_out)
        except (OSError, ValueError):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"claude-watchtower → http://127.0.0.1:{port}")
    if not is_loopback(host):
        # The address a phone will actually reach, not the one the socket is
        # bound to: nothing can open http://0.0.0.0.
        where = listen.lan_address()
        key = f"/?k={config.ACCESS_KEY}" if config.ACCESS_KEY else ""
        if where:
            print(f"on this network  → http://{where}:{port}{key}")
            if key:
                print("                  type that on the phone once — it is remembered after that")
        else:
            print(f"on this network  → this machine's own address, port {port}{key}")
        if not config.ACCESS_KEY:
            print("note: --no-key, so anyone who can reach that port can read every conversation")
    if picked:
        print(f"note: port {port} is this install's from now on — kept in {listen.where_kept()}")
    if not WINDOWS.available():
        print("note: xdotool/DISPLAY unavailable, so window focusing is switched off")
    if not config.SAY_ENABLED:
        why = ("--no-send" if args.no_send
               else f"--no-key, so the panel on {host} is read-only" if not is_loopback(host)
               else f"not bound to loopback ({host})")
        print(f"note: sending input is switched off — {why}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        # A held session is a process of ours. Leaving it behind would leave a
        # `claude` on a transcript with nobody to send to it or reap it.
        owned_release_all()


if __name__ == "__main__":
    main()

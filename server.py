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
    # Unset rather than defaulted, so "did anyone ask for a host" is a question
    # that can still be answered below.
    parser.add_argument("--host", default=None,
                        help="bind this address instead (default: every interface)")
    parser.add_argument("--local", action="store_true",
                        help="serve to this machine only — no phone, and no key to type")
    parser.add_argument("--lan", action="store_true",
                        help="serve to the local network (the default; kept so the flag still works)")
    parser.add_argument("--no-key", action="store_true",
                        help="answer anyone who can reach the port — no key, and "
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

    # The network by default. The panel is worth having on a phone, and a phone
    # cannot reach loopback — so the thing you have to ask for is the narrower
    # setting, not the wider one. What guards it is the key below, which is a
    # tighter gate than binding to loopback ever was: loopback lets in every
    # process on this machine, key or no key.
    host = "127.0.0.1" if args.local else (args.host or "0.0.0.0")
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
    # What the settings page reads to show the address and draw the code.
    config.SERVE_PORT = port
    config.SERVE_LAN = not is_loopback(host)

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
    # flush on every line: stdout is a pipe under a service unit, and the
    # address is the one thing here nobody can work out for themselves.
    say = lambda line: print(line, flush=True)
    say(f"claude-watchtower → http://127.0.0.1:{port}")
    if not is_loopback(host):
        # The address a phone will actually reach, not the one the socket is
        # bound to: nothing can open http://0.0.0.0.
        where = listen.lan_address()
        key = f"/?k={config.ACCESS_KEY}" if config.ACCESS_KEY else ""
        if where:
            say(f"on this network  → http://{where}:{port}{key}")
            if key:
                say("                  or scan the code in Settings — it is the same address")
        else:
            say(f"on this network  → this machine's own address, port {port}{key}")
        if not config.ACCESS_KEY:
            say("note: --no-key, so anyone who can reach that port can read every conversation")
        elif not listen.key_is_remembered(config.ACCESS_KEY):
            # The one case where the key is not the same tomorrow, and a phone
            # would silently stop getting in.
            say(f"warning: could not save the key in {listen.where_kept()} — "
                "the next start will print a different one")
    if picked:
        say(f"note: port {port} is this install's from now on — kept in {listen.where_kept()}")
    if not WINDOWS.available():
        say("note: xdotool/DISPLAY unavailable, so window focusing is switched off")
    if not config.SAY_ENABLED:
        why = ("--no-send" if args.no_send
               else f"--no-key, so the panel on {host} is read-only" if not is_loopback(host)
               else f"not bound to loopback ({host})")
        say(f"note: sending input is switched off — {why}")
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

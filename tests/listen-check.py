#!/usr/bin/env python3
"""Checks for where the panel listens and who it answers — no browser, no phone.

Two things are asserted here, and both are the sort that only bite in a
situation you cannot easily stand up by hand.

The port: that a first run picks one and writes it down, that every run after
that uses the same one, that two installs in two folders do not want the same
port, and that --port is a loan rather than a change of mind.

The key: that a request from off this machine is answered only when it carries
the key — in the URL or in the cookie — and that it is turned away in the API's
own language when it asked the API. The server is real; the client address is
not, since a test cannot come in over the wifi.

    python3 tests/listen-check.py

A failure prints the case and exits 1.
"""

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from watchtower import config, listen  # noqa: E402
from watchtower.http import Handler  # noqa: E402

FAILED = 0


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


# ---------------------------------------------------------------- the port
# Every one of these runs against a config file of its own: the real one belongs
# to whoever is running the tests, and a test that rewrote it would move their
# panel to a different address.
with tempfile.TemporaryDirectory() as tmp:
    kept = Path(tmp) / "listen.json"
    listen.LISTEN_FILE = kept
    config.LISTEN_FILE = kept

    first, picked = listen.chosen_port()
    check("a first run picks a port", listen.PORT_BAND[0] <= first <= listen.PORT_BAND[1], str(first))
    check("and says it was picked, so the panel can mention it", picked is True)
    check("it is written down", json.loads(kept.read_text()).get("port") == first)

    again, picked_again = listen.chosen_port()
    check("every run after that is the same port", again == first, f"{first} then {again}")
    check("and quietly, since it is not news any more", picked_again is False)

    asked, picked_asked = listen.chosen_port(9123)
    check("--port is answered", asked == 9123 and picked_asked is False)
    check("and does not disturb what is remembered",
          json.loads(kept.read_text()).get("port") == first)

    os.environ["CLAUDE_WATCHTOWER_PORT"] = "9124"
    check("the environment is answered too", listen.chosen_port()[0] == 9124)
    check("but the command line comes first", listen.chosen_port(9125)[0] == 9125)
    del os.environ["CLAUDE_WATCHTOWER_PORT"]

    # A port that is taken is stepped over. Held for real rather than mocked:
    # the check is a bind, so only a bind proves it.
    kept.unlink()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("0.0.0.0", first))
        held.listen(1)
        moved, _ = listen.chosen_port()
    check("a port something else holds is stepped over", moved != first, f"{first} → {moved}")
    check("and the one it moved to is in the band",
          listen.PORT_BAND[0] <= moved <= listen.PORT_BAND[1], str(moved))

    # Two clones, two ports: the seed is the path this install sits at.
    here = listen.ROOT
    try:
        listen.ROOT = Path("/tmp/one-clone")
        one = listen._hashed_port()
        listen.ROOT = Path("/tmp/another-clone")
        two = listen._hashed_port()
    finally:
        listen.ROOT = here
    check("two clones do not want the same port", one != two, f"{one} vs {two}")
    check("and one clone wants the same port every time", listen._hashed_port() == listen._hashed_port())

    # The key, whose whole job is being the same one tomorrow.
    kept.unlink(missing_ok=True)
    key = listen.access_key()
    check("a key is made when one is wanted", len(key) == listen.KEY_LENGTH, key)
    check("and kept, so a phone stays let in", listen.access_key() == key)
    check("it is written down as well, so it can be looked up",
          json.loads(kept.read_text()).get("key") == key)
    check("--new-key replaces it", listen.access_key(fresh=True) != key)

    # The case that used to hand out a different key every start: nothing can be
    # written down. Derived from the machine instead, so it is still constant.
    kept.unlink(missing_ok=True)
    real_write = listen._write
    listen._write = lambda entry: None          # a config directory that refuses
    try:
        first, second = listen.access_key(), listen.access_key()
        derived = listen._derived_key()
        check("with nothing saveable the key is still the same every time", first == second, first)
        check("because it comes from the machine rather than from chance",
              derived is not None and first == derived, f"{first} vs {derived}")
        check("and the panel knows it will survive a restart",
              listen.key_is_remembered(first))
        check("while a key it could neither derive nor save is reported as temporary",
              not listen.key_is_remembered("zzzzzzzz"))
    finally:
        listen._write = real_write


# ----------------------------------------------------------------- the gate
# A real server, answering real requests. What is faked is only where they came
# from: a test cannot arrive over the wifi, so the handler is asked to believe
# an address that is not loopback.
class OffMachine(Handler):
    """The same handler, told the request came from the network."""
    def __init__(self, *args, **kwargs):
        self.client_address = ("192.168.1.99", 51000)
        super().__init__(*args, **kwargs)

    # BaseHTTPRequestHandler sets client_address in __init__ from the socket, so
    # it has to be put back after the base class has had its way with it.
    def setup(self):
        super().setup()
        self.client_address = ("192.168.1.99", 51000)


def serve(handler_class) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def ask(server, path: str, headers: dict | None = None, method: str = "GET"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request(method, path, body=b"{}" if method == "POST" else None,
                     headers={**(headers or {}), **({"Content-Length": "2"} if method == "POST" else {})})
        answer = conn.getresponse()
        return answer.status, answer.getheader("Set-Cookie") or "", answer.read()
    finally:
        conn.close()


config.ACCESS_KEY = "testkey1"
off = serve(OffMachine)
here_only = serve(Handler)
try:
    code, _, body = ask(off, "/")
    check("a page asked for from the network without the key is refused", code == 403, str(code))
    check("and says so in a page a phone can read", b"needs its key" in body)

    code, _, body = ask(off, "/api/state")
    check("the API refuses in its own language", code == 403 and json.loads(body)["ok"] is False,
          str(code))

    code, _, _ = ask(off, "/api/say", method="POST")
    check("so does anything that would act", code == 403)

    code, _, _ = ask(off, "/?k=nearlythe")
    check("a wrong key is a wrong key", code == 403)

    code, cookie, _ = ask(off, "/?k=testkey1")
    check("the key in the URL is let in", code == 200, str(code))
    check("and comes back as a cookie, so it is typed once",
          "wt-key=testkey1" in cookie and "HttpOnly" in cookie, cookie)

    code, _, _ = ask(off, "/api/state", {"Cookie": "wt-key=testkey1"})
    check("the cookie alone is enough after that", code == 200, str(code))
    code, _, _ = ask(off, "/api/state", {"Cookie": "other=1; wt-key=testkey1; more=2"})
    check("even among other cookies", code == 200, str(code))

    code, _, _ = ask(here_only, "/api/state")
    check("this machine is never asked for a key", code == 200, str(code))

    # And with no key wanted at all — a panel on loopback alone — nothing is
    # asked of anybody.
    config.ACCESS_KEY = None
    code, _, _ = ask(off, "/api/state")
    check("with no key set, the network is answered like anyone else", code == 200, str(code))
finally:
    off.shutdown()
    here_only.shutdown()
    config.ACCESS_KEY = None

print()
print("all ok" if not FAILED else f"{FAILED} failed")
sys.exit(1 if FAILED else 0)

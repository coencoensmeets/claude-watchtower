#!/usr/bin/env python3
"""Checks for reaching the panel from anywhere over Tailscale — no tailnet needed.

Three things: that `tailscale status --json` is read into the address a phone
should open (from canned output, since a test machine is on no tailnet); that
the settings answer carries that address with the key on the end; and that
switching Tailscale on or off is refused to anything but this machine, key or
no key — from a phone, a login link is a way onto somebody else's tailnet and
switching off strands the phone.

    python3 tests/tailnet-check.py

A failure prints the case and exits 1.
"""

import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from watchtower import config, tailnet  # noqa: E402
from watchtower.http import Handler  # noqa: E402

FAILED = 0


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


# ---------------------------------------------------------------- reading status
RUNNING = json.dumps({
    "BackendState": "Running", "AuthURL": "",
    "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
    "Self": {"DNSName": "laptop.tail1234.ts.net.", "TailscaleIPs": ["100.101.102.103"]},
})
found = tailnet.parse_status(RUNNING)
check("running: the IPv4 tailnet address is the one to open", found["address"] == "100.101.102.103",
      found["address"])
check("running: the MagicDNS name loses its trailing dot", found["name"] == "laptop.tail1234.ts.net")
check("running: says it is running", found["running"] is True)

LOGIN = json.dumps({"BackendState": "NeedsLogin", "AuthURL": "https://login.tailscale.com/a/abc",
                    "TailscaleIPs": None, "Self": {"DNSName": ""}})
found = tailnet.parse_status(LOGIN)
check("needs login: the link is passed on", found["login"] == "https://login.tailscale.com/a/abc")
check("needs login: no address to open", found["address"] == "" and found["running"] is False)

STOPPED = json.dumps({"BackendState": "Stopped", "TailscaleIPs": ["100.101.102.103"],
                      "Self": {"DNSName": "laptop.tail1234.ts.net."}})
found = tailnet.parse_status(STOPPED)
check("stopped: an address it keeps is not offered", found["address"] == "" and not found["running"])

check("garbage: unknown rather than a crash", tailnet.parse_status("not json")["state"] == "unknown")
check("a JSON list: unknown rather than a crash", tailnet.parse_status("[]")["state"] == "unknown")


# ---------------------------------------------------------------- the routes
class OffMachine(Handler):
    """The same handler, told the request came from the network."""
    def setup(self):
        super().setup()
        self.client_address = ("100.64.0.7", 51000)


def serve(handler_class) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def ask(server, path: str, method: str = "GET", body: dict | None = None, key: bool = True):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    sep = "&" if "?" in path else "?"
    try:
        data = json.dumps(body or {}).encode()
        conn.request(method, f"{path}{sep}k=testkey1" if key else path,
                     body=data if method == "POST" else None,
                     headers={"Content-Type": "application/json", "Content-Length": str(len(data))}
                     if method == "POST" else {})
        answer = conn.getresponse()
        return answer.status, answer.read()
    finally:
        conn.close()


# No real Tailscale is touched: status is pinned, and switching is recorded.
switched = []
tailnet.status = lambda fresh=False: {**tailnet.parse_status(RUNNING), "installed": True,
                                      "operator": "sudo tailscale set --operator=me"}
tailnet.turn_on = lambda: (switched.append("on") or (True, "Connected", {}))
tailnet.turn_off = lambda: (switched.append("off") or (True, "Off", {}))

config.ACCESS_KEY = "testkey1"
config.SERVE_LAN = True
config.SERVE_PORT = 8867
config.SAY_ENABLED = True
off = serve(OffMachine)
here = serve(Handler)

code, raw = ask(off, "/api/reach")
net = json.loads(raw).get("tailnet") or {}
check("reach: the tailnet address comes with the port and key",
      net.get("url") == "http://100.101.102.103:8867/?k=testkey1", net.get("url"))
check("reach from a phone: the switch is not offered", net.get("canSwitch") is False)
code, raw = ask(here, "/api/reach")
check("reach from this machine: the switch is offered", json.loads(raw)["tailnet"]["canSwitch"] is True)

code, _ = ask(off, "/api/reach/tailnet", "POST", {"on": True})
check("a phone with the key cannot switch Tailscale", code == 403 and not switched, f"{code} {switched}")
code, _ = ask(off, "/api/reach/tailnet", "POST", {"on": False})
check("nor switch it off", code == 403 and not switched, f"{code} {switched}")
code, _ = ask(here, "/api/reach/tailnet", "POST", {"on": True}, key=False)
check("this machine can switch it on", code == 200 and switched == ["on"], f"{code} {switched}")
code, _ = ask(here, "/api/reach/tailnet", "POST", {"on": False}, key=False)
check("and off", code == 200 and switched == ["on", "off"], f"{code} {switched}")

code, raw = ask(off, "/api/qr?via=tailnet")
check("the code can be drawn for the tailnet address", code == 200 and raw.startswith(b"<svg"), str(code))
code, _ = ask(off, "/api/reach", key=False)
check("without the key, a tailnet phone is turned away like any other", code == 403, str(code))

config.SERVE_LAN = False
code, raw = ask(here, "/api/reach")
check("a --local panel offers no tailnet", json.loads(raw).get("tailnet") is None)
code, _ = ask(here, "/api/reach/tailnet", "POST", {"on": True})
check("and cannot switch it", code == 403)

off.shutdown()
here.shutdown()
sys.exit(1 if FAILED else 0)

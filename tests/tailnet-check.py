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


# ---------------------------------------------------------------- https, same port
# One port answers both: a TLS handshake is wrapped, plain http is not. The
# certificate is a throwaway self-signed one standing in for Tailscale's.
import shutil  # noqa: E402
import ssl  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from watchtower.http import PanelServer  # noqa: E402

if not shutil.which("openssl"):
    print("  --  no openssl, so the https checks are skipped")
else:
    tmp = tempfile.mkdtemp(prefix="watchtower-tls-")
    cert, key = f"{tmp}/c.pem", f"{tmp}/k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=laptop.tail1234.ts.net", "-keyout", key, "-out", cert],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    config.SERVE_LAN = True
    server = PanelServer(("127.0.0.1", 0), OffMachine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def plain(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        answer = conn.getresponse()
        return answer.status, answer.read()

    def secure(path):
        client = ssl.create_default_context(cafile=cert)
        client.check_hostname = False
        conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=client)
        conn.request("GET", path)
        answer = conn.getresponse()
        return answer.status, answer.read()

    code, _ = plain("/api/reach?k=testkey1")
    check("without a certificate, plain http is served as before", code == 200, str(code))

    tailnet._tls.update(context=context, ok=True, name="laptop.tail1234.ts.net", message="")
    code, raw = plain("/api/reach?k=testkey1")
    check("with one, plain http still works on the same port", code == 200, str(code))
    code, raw = secure("/api/reach?k=testkey1")
    check("and https is served on that port too", code == 200, str(code))
    https = (json.loads(raw).get("tailnet") or {}).get("https") or {}
    check("reach offers the https address on the tailnet name",
          https.get("url") == "https://laptop.tail1234.ts.net:8867/?k=testkey1", https.get("url"))
    code, _ = secure("/api/reach")
    check("over https, a phone without the key is still turned away", code == 403, str(code))
    code, raw = secure("/api/qr?via=tailnet&k=testkey1")
    check("the code is drawn for the https address", code == 200 and raw.startswith(b"<svg"))
    # A broken handshake must not take the server with it.
    import socket  # noqa: E402
    with socket.create_connection(("127.0.0.1", port), timeout=5) as junk:
        junk.sendall(b"\x16\x03\x01garbage-not-a-handshake")
        try:
            junk.recv(10)
        except OSError:
            pass
    code, _ = secure("/api/reach?k=testkey1")
    check("a garbled handshake does not stop the next one", code == 200, str(code))

    tailnet._tls.update(context=None, ok=False, name="", message="Not on a tailnet")
    server.shutdown()

check("a missing HTTPS setting is explained with the admin page",
      tailnet.TLS_ADMIN_URL in tailnet._cert_trouble("500 Internal Server Error: your Tailscale account does not support getting TLS certs"))
check("a permission refusal is explained with the operator command",
      "--operator=" in tailnet._cert_trouble("Access denied: cert access denied"))

sys.exit(1 if FAILED else 0)

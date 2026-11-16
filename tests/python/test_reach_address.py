"""Which address a phone is handed, and the service unit filled in per install.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import listen, service  # noqa: E402

# Two real networks at once, a Docker bridge, Tailscale and a VPN: the laptop
# that had the panel handing phones an address they could not reach.
IP_JSON = json.dumps([
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
    {"ifname": "enxc8a3629044ef", "addr_info": [{"family": "inet", "local": "192.168.2.18"}]},
    {"ifname": "wlp0s20f3", "addr_info": [{"family": "inet", "local": "192.168.179.22"}]},
    {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1"}]},
    {"ifname": "br-3f2c710b086d", "addr_info": [{"family": "inet", "local": "172.18.0.1"}]},
    {"ifname": "tailscale0", "addr_info": [{"family": "inet", "local": "100.82.11.25"}]},
    {"ifname": "wg0", "addr_info": [{"family": "inet", "local": "10.8.0.2"}]},
])


class Address(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.object(listen, "LISTEN_FILE", self.tmp / "listen.json")
        p.start()
        self.addCleanup(p.stop)

    def candidates(self, routed):
        with mock.patch.object(listen, "lan_address", lambda: routed):
            return listen.address_candidates(IP_JSON)

    def test_bridges_and_tailscale_are_left_out_and_vpns_go_last(self):
        found = self.candidates("192.168.2.18")
        self.assertEqual([c["address"] for c in found], ["192.168.2.18", "192.168.179.22", "10.8.0.2"])
        self.assertEqual([c["kind"] for c in found], ["wired", "wifi", "vpn"])

    def test_a_vpn_holding_the_route_is_not_what_a_phone_gets(self):
        found = self.candidates("10.8.0.2")
        self.assertEqual(listen.chosen_address(found, env={}), ("192.168.2.18", "auto"))

    def test_a_pick_wins_while_the_machine_has_it(self):
        found = self.candidates("192.168.2.18")
        listen.set_address("192.168.179.22")
        self.assertEqual(listen.chosen_address(found, env={}), ("192.168.179.22", "picked"))
        # Moved to another network: that address is gone, so not handed out.
        elsewhere = [c for c in found if c["address"] != "192.168.179.22"]
        self.assertEqual(listen.chosen_address(elsewhere, env={})[1], "auto")

    def test_a_host_name_is_kept_and_the_environment_beats_everything(self):
        found = self.candidates("192.168.2.18")
        listen.set_address("mypc.local")
        self.assertEqual(listen.chosen_address(found, env={}), ("mypc.local", "picked"))
        env = {listen.ADDRESS_ENV: "10.0.0.5"}
        self.assertEqual(listen.chosen_address(found, env=env), ("10.0.0.5", "env"))
        listen.set_address(None)
        self.assertEqual(listen.chosen_address(found, env={})[1], "auto")

    def test_picking_keeps_the_port_and_key_and_refuses_junk(self):
        (self.tmp / "listen.json").write_text(json.dumps({"port": 8842, "key": "abcd2345"}))
        self.assertTrue(listen.set_address("192.168.179.22"))
        self.assertFalse(listen.set_address("http://evil/?x"))
        kept = json.loads((self.tmp / "listen.json").read_text())
        self.assertEqual(kept, {"port": 8842, "key": "abcd2345", "address": "192.168.179.22"})

    def test_without_ip_the_routing_table_is_the_list(self):
        with mock.patch.object(listen, "lan_address", lambda: "192.168.1.9"):
            self.assertEqual(listen.address_candidates("not json"),
                             [{"address": "192.168.1.9", "iface": "", "kind": "other"}])


class Service(unittest.TestCase):
    def test_the_unit_is_filled_in_for_this_install(self):
        text = service.unit_text("/opt/py/bin/python3", Path("/srv/clones/wt"), ":1", "/home/x/.local/bin:/usr/bin")
        self.assertIn("ExecStart=/opt/py/bin/python3 /srv/clones/wt/server.py", text)
        self.assertIn("WorkingDirectory=/srv/clones/wt", text)
        self.assertIn("Environment=DISPLAY=:1", text)
        self.assertIn("Environment=PATH=/home/x/.local/bin:/usr/bin", text)
        self.assertNotIn("@", text.split("[Unit]", 1)[1])
        self.assertNotIn("Workspace/Projects", text)

    def test_install_writes_reloads_and_starts(self):
        home = Path(tempfile.mkdtemp())
        ran = []
        fake = lambda argv, **_: (ran.append(argv), mock.Mock(returncode=0, stderr=""))[1]
        with mock.patch.object(service.shutil, "which", lambda _: "/bin/systemctl"):
            ok, said = service.install(env={"XDG_CONFIG_HOME": str(home), "DISPLAY": ":0", "PATH": "/usr/bin"},
                                       run=fake)
        self.assertTrue(ok, said)
        unit = home / "systemd" / "user" / service.UNIT_NAME
        self.assertIn(str(service.ROOT), unit.read_text())
        self.assertEqual(ran[-1], ["systemctl", "--user", "enable", "--now", service.UNIT_NAME])


if __name__ == "__main__":
    unittest.main()


class Route(unittest.TestCase):
    """POST /api/reach/address through the real Handler, from loopback."""

    def setUp(self):
        import threading
        from watchtower import config
        from watchtower import http as web
        self.web = web
        self.tmp = Path(tempfile.mkdtemp())
        for p in (mock.patch.object(listen, "LISTEN_FILE", self.tmp / "listen.json"),
                  mock.patch.object(config, "SERVE_LAN", True),
                  mock.patch.object(config, "SERVE_PORT", 8842),
                  mock.patch.object(config, "ACCESS_KEY", "abcd2345"),
                  mock.patch.object(web, "address_candidates", lambda: [
                      {"address": "192.168.179.22", "iface": "wlp0s20f3", "kind": "wifi"},
                      {"address": "192.168.2.18", "iface": "enx1", "kind": "wired"}]),
                  mock.patch.object(listen, "address_candidates", lambda: [
                      {"address": "192.168.179.22", "iface": "wlp0s20f3", "kind": "wifi"},
                      {"address": "192.168.2.18", "iface": "enx1", "kind": "wired"}])):
            p.start()
            self.addCleanup(p.stop)
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path, body=None):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_picking_moves_the_code_and_automatic_moves_it_back(self):
        _, reach = self.call("/api/reach")
        self.assertEqual((reach["address"], reach["addressSource"], reach["canPick"]),
                         ("192.168.179.22", "auto", True))
        code, said = self.call("/api/reach/address", {"address": "192.168.2.18"})
        self.assertEqual(code, 200, said)
        _, reach = self.call("/api/reach")
        self.assertEqual((reach["address"], reach["addressSource"]), ("192.168.2.18", "picked"))
        self.assertEqual(reach["url"], "http://192.168.2.18:8842/?k=abcd2345")
        self.assertEqual(self.call("/api/reach/address", {"address": "a b"})[0], 400)
        self.call("/api/reach/address", {"address": ""})
        self.assertEqual(self.call("/api/reach")[1]["addressSource"], "auto")

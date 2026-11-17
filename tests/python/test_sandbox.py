"""The sandbox: what a boxed session can reach, and the ways back out it cannot use.

Docker is never run here. What is pinned down is the command line the panel
builds and the guards around it, because a mistake in either is a session
running with more than the switch says it has.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from watchtower import sandbox  # noqa: E402
from watchtower.git import read as git_read, write as git_write  # noqa: E402


def make_repo(where: Path) -> Path:
    repo = where / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def pairs(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == flag]


class Box(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "access-123", "refreshToken": "refresh-456",
            "expiresAt": (time.time() + 6 * 3600) * 1000}}))
        (self.home / ".gitconfig").write_text("[user]\n\tname = t\n")
        self.repo = make_repo(self.tmp)
        sub = self.repo / "src"
        sub.mkdir()
        self.cwd = str(sub)
        patches = [
            mock.patch.object(sandbox, "HOME", self.home),
            mock.patch.object(sandbox, "SANDBOX_DIR", self.tmp / "sbx"),
            mock.patch.object(sandbox, "SANDBOX_HOME", self.tmp / "sbx" / "home"),
            mock.patch.object(sandbox, "SANDBOX_LOGS", self.tmp / "sbx" / "logs"),
            mock.patch.object(sandbox, "TOKEN_FILE", self.tmp / "sbx" / "token"),
            mock.patch.object(sandbox, "SANDBOX_ROOTS_FILE", self.tmp / "roots.json"),
            mock.patch.object(sandbox, "project_dir", lambda cwd: self.tmp / "projects" / "-slug"),
            mock.patch.object(sandbox, "_docker_state", lambda: (True, "", ["runc"])),
            mock.patch.object(sandbox, "claude_binary", lambda: ("/opt/claude", "")),
            mock.patch.object(sandbox, "image_ready", lambda: True),
            mock.patch.object(sandbox, "own_addresses", lambda: ["192.168.1.20", "203.0.113.7"]),
            mock.patch.object(sandbox, "SANDBOX_LAN_FILE", self.tmp / "lan.json"),
            mock.patch.object(sandbox, "SETTINGS_FILE", self.tmp / "sbx" / "settings.json"),
            mock.patch.object(sandbox, "SECRETS_FILE", self.tmp / "sbx" / "secrets.json"),
            mock.patch.object(sandbox, "SSH_DIR", self.tmp / "sbx" / "home" / ".ssh"),
            mock.patch.object(sandbox, "SSH_KEY", self.tmp / "sbx" / "home" / ".ssh" / "id_ed25519"),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CLAUDE_WATCHTOWER_SANDBOX_RUNTIME", None)

    def wrapped(self) -> tuple[list[str], dict]:
        argv, facts, why = sandbox.wrap(["/usr/bin/claude", "--print", "--verbose"], self.cwd, "abcdef12-x")
        self.assertIsNotNone(argv, why)
        return argv, facts

    def test_the_whole_checkout_is_mounted_and_nothing_else_of_home(self) -> None:
        argv, _ = self.wrapped()
        volumes = pairs(argv, "-v")
        root = str(self.repo)
        self.assertIn(f"{root}:{root}", volumes)
        sources = [v.split(":", 1)[0] for v in volumes]
        self.assertNotIn(str(self.home), sources)
        self.assertNotIn(str(self.home / ".claude"), sources)
        self.assertFalse(any(".ssh" in s for s in sources))
        self.assertEqual(pairs(argv, "-w"), [self.cwd])

    def test_git_settings_and_hooks_are_read_only_and_git_is_its_own_mount(self) -> None:
        argv, _ = self.wrapped()
        volumes = pairs(argv, "-v")
        git = self.repo / ".git"
        self.assertIn(f"{git}:{git}", volumes)
        self.assertIn(f"{git / 'config'}:{git / 'config'}:ro", volumes)
        self.assertIn(f"{git / 'hooks'}:{git / 'hooks'}:ro", volumes)

    def test_the_refresh_token_never_reaches_the_box_and_the_token_is_not_on_the_command_line(self) -> None:
        argv, facts = self.wrapped()
        self.assertEqual(facts["env"], {"CLAUDE_CODE_OAUTH_TOKEN": "access-123"})
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", pairs(argv, "-e"))
        joined = " ".join(argv)
        self.assertNotIn("access-123", joined)
        self.assertNotIn("refresh-456", joined)
        self.assertNotIn(".credentials.json", joined)

    def test_a_saved_long_lived_token_wins_and_never_expires(self) -> None:
        sandbox.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        sandbox.TOKEN_FILE.write_text("long-lived\n")
        _, facts = self.wrapped()
        self.assertEqual(facts["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "long-lived")
        self.assertIsNone(facts["expires"])

    def test_an_expired_login_is_a_refusal(self) -> None:
        (self.home / ".claude" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "old", "expiresAt": (time.time() - 10) * 1000}}))
        argv, _, why = sandbox.wrap(["claude"], self.cwd, "x")
        self.assertIsNone(argv)
        self.assertIn("run out", why)

    def test_it_runs_privileges_dropped_behind_the_entrypoint(self) -> None:
        argv, _ = self.wrapped()
        self.assertEqual(pairs(argv, "--cap-drop"), ["ALL"])
        self.assertEqual(sorted(pairs(argv, "--cap-add")), ["NET_ADMIN", "SETGID", "SETPCAP", "SETUID"])
        self.assertIn("no-new-privileges", pairs(argv, "--security-opt"))
        self.assertEqual(pairs(argv, "--entrypoint"), [sandbox.BOX_ENTRY])
        self.assertIn(f"BOX_UID={os.getuid()}", pairs(argv, "-e"))
        self.assertIn("BOX_SELF=192.168.1.20 203.0.113.7", pairs(argv, "-e"))
        self.assertIn("BOX_ALLOW=", pairs(argv, "-e"))
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("host", pairs(argv, "--network"))
        self.assertFalse(any("docker.sock" in v for v in pairs(argv, "-v")))
        at = argv.index("claude")
        self.assertEqual(argv[at:at + 3], ["claude", "--print", "--verbose"])
        self.assertEqual(argv[-2], "--append-system-prompt")

    def test_gvisor_is_used_when_docker_has_it_unless_told_not_to(self) -> None:
        self.assertEqual(sandbox.pick_runtime(["runc", "runsc"]), "runsc")
        self.assertEqual(sandbox.pick_runtime(["runc"]), "")
        with mock.patch.dict(os.environ, {"CLAUDE_WATCHTOWER_SANDBOX_RUNTIME": "runc"}):
            self.assertEqual(sandbox.pick_runtime(["runc", "runsc"]), "")

    def test_a_folder_that_is_not_a_repository_is_refused(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        ok, why = sandbox.can_box(str(plain))
        self.assertFalse(ok)
        self.assertIn("git repository", why)
        argv, _, _ = sandbox.wrap(["claude"], str(plain), "x")
        self.assertIsNone(argv)

    def test_a_boxed_checkout_is_remembered_for_the_panels_own_git(self) -> None:
        self.wrapped()
        self.assertIn(str(self.repo), json.loads(sandbox.SANDBOX_ROOTS_FILE.read_text()))

    def test_a_linked_worktree_keeps_its_pointer_read_only(self) -> None:
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        tree = self.tmp / "tree"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", str(tree)], check=True)
        argv, _, why = sandbox.wrap(["claude"], str(tree), "x")
        self.assertIsNotNone(argv, why)
        volumes = pairs(argv, "-v")
        self.assertIn(f"{tree / '.git'}:{tree / '.git'}:ro", volumes)
        shared = self.repo / ".git"
        self.assertIn(f"{shared}:{shared}", volumes)
        self.assertIn(f"{shared / 'config'}:{shared / 'config'}:ro", volumes)

    def test_a_token_close_to_running_out_is_stale(self) -> None:
        self.assertTrue(sandbox.token_stale(time.time() + 60))
        self.assertFalse(sandbox.token_stale(time.time() + 3600))
        self.assertFalse(sandbox.token_stale(None))


    def test_allowed_local_addresses_reach_the_firewall_for_that_session_only(self) -> None:
        ok, why, saved = sandbox.set_lan("abcdef12-x", ["192.168.2.10", "10.0.0.0/24:22", "[fd00::1]:22"])
        self.assertTrue(ok, why)
        self.assertEqual(saved, ["192.168.2.10", "10.0.0.0/24:22", "[fd00::1]:22"])
        argv, facts = self.wrapped()
        self.assertIn("BOX_ALLOW=192.168.2.10/32 10.0.0.0/24,22 fd00::1/128,22", pairs(argv, "-e"))
        other, _, _ = sandbox.wrap(["claude"], self.cwd, "someone-else")
        self.assertIn("BOX_ALLOW=", pairs(other, "-e"))

    def test_a_list_with_one_bad_entry_is_refused_whole(self) -> None:
        ok, why, _ = sandbox.set_lan("s", ["192.168.2.10", "8.8.8.8"])
        self.assertFalse(ok)
        self.assertIn("internet", why)
        self.assertEqual(sandbox.lan_for("s"), [])

    def test_what_may_and_may_not_be_allowed(self) -> None:
        good = ["192.168.2.10", "10.0.0.0/24", "172.20.1.5:22", "[fd00::1]:22", "169.254.10.1"]
        bad = ["8.8.8.8", "0.0.0.0/0", "10.0.0.0/4", "127.0.0.1", "::1", "localhost",
               "192.168.1.5:99999", "a b", "", "192.168.1.5:ssh"]
        for entry in good:
            self.assertIsNotNone(sandbox.parse_allow(entry)[0], entry)
        for entry in bad:
            self.assertIsNone(sandbox.parse_allow(entry)[0], entry)

    def test_the_allowed_list_follows_a_cleared_session(self) -> None:
        sandbox.set_lan("old", ["192.168.2.10"])
        sandbox.lan_rekey("old", "new")
        self.assertEqual(sandbox.lan_for("new"), ["192.168.2.10"])
        self.assertEqual(sandbox.lan_for("old"), [])


    def test_granted_secrets_reach_the_box_by_name_and_never_the_command_line(self) -> None:
        self.assertTrue(sandbox.set_secret("DEVICE_PASSWORD", "hunter2")[0])
        self.assertTrue(sandbox.set_secret("OTHER_TOKEN", "nope")[0])
        self.assertTrue(sandbox.grant_secrets("abcdef12-x", ["DEVICE_PASSWORD"])[0])
        argv, facts = self.wrapped()
        self.assertEqual(facts["env"]["DEVICE_PASSWORD"], "hunter2")
        self.assertNotIn("OTHER_TOKEN", facts["env"])
        self.assertIn("DEVICE_PASSWORD", pairs(argv, "-e"))
        self.assertNotIn("hunter2", " ".join(argv))
        self.assertIn("DEVICE_PASSWORD", argv[-1])
        self.assertNotIn("hunter2", argv[-1])
        self.assertEqual(sandbox.SECRETS_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sandbox.secret_names(), ["DEVICE_PASSWORD", "OTHER_TOKEN"])

    def test_secret_names_the_box_runs_on_are_refused(self) -> None:
        for name in ("HOME", "PATH", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "BOX_ALLOW",
                     "LD_PRELOAD", "lower", "1ABC", "A-B"):
            self.assertFalse(sandbox.set_secret(name, "x")[0], name)
        self.assertFalse(sandbox.set_secret("FINE", "")[0])

    def test_removing_a_secret_takes_it_from_every_session(self) -> None:
        sandbox.set_secret("KEY_A", "1")
        sandbox.grant_secrets("s1", ["KEY_A"])
        sandbox.delete_secret("KEY_A")
        self.assertEqual(sandbox.secrets_for("s1"), [])
        self.assertFalse(sandbox.grant_secrets("s1", ["KEY_A"])[0])

    def test_packages_are_checked_quoted_and_change_the_image(self) -> None:
        plain = sandbox.image_name()
        ok, why, saved = sandbox.set_packages({"apt": ["libusb-1.0-0"], "pip": ["pyserial>=3.5", "requests[socks]"],
                                               "npm": ["@scope/tool@1.2.3"]})
        self.assertTrue(ok, why)
        layer = sandbox.packages_dockerfile()
        self.assertIn("FROM claude-watchtower-sandbox:", layer)
        self.assertIn("'pyserial>=3.5'", layer)
        self.assertNotEqual(sandbox.image_name(), plain)
        for kind, bad in (("pip", "x; rm -rf /"), ("apt", "Bad Name"), ("npm", "$(evil)"), ("pip", "a`b`")):
            self.assertFalse(sandbox.set_packages({kind: [bad]})[0], bad)
        sandbox.set_packages({})
        self.assertEqual(sandbox.image_name(), plain)

    def test_the_brief_says_what_the_box_is_without_secrets(self) -> None:
        sandbox.set_lan("abcdef12-x", ["192.168.2.10"])
        argv, _ = self.wrapped()
        brief = argv[-1]
        self.assertIn(str(self.repo), brief)
        self.assertIn("192.168.2.10", brief)
        self.assertIn("Sandbox tab", brief)


class PanelGit(unittest.TestCase):
    def test_reads_never_run_a_filesystem_monitor(self) -> None:
        with mock.patch.object(git_read.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            git_read.git_run("/r", ["status"])
        self.assertIn("core.fsmonitor=false", run.call_args.args[0])

    def test_writes_in_a_boxed_checkout_run_no_hooks(self) -> None:
        with mock.patch.object(git_write.subprocess, "run") as run, \
                mock.patch.object(git_write, "guarded_roots", lambda: {"/boxed"}):
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            git_write.git_write("/boxed", ["commit", "-m", "x"])
            boxed = run.call_args.args[0]
            git_write.git_write("/other", ["commit", "-m", "x"])
            other = run.call_args.args[0]
        self.assertIn("core.hooksPath=/dev/null", boxed)
        self.assertNotIn("core.hooksPath=/dev/null", other)
        self.assertIn("core.fsmonitor=false", other)


class Entry(unittest.TestCase):
    def test_the_entrypoint_fails_closed_and_drops_everything(self) -> None:
        text = (ROOT / "sandbox" / "entry.sh").read_text()
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn("set -eu", text)
        for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"):
            self.assertIn(net, text)
        self.assertIn("--bounding-set=-all", text)
        # This computer is shut out before anything is let in, so allowing the
        # subnet it is on cannot open it up.
        self.assertLess(text.index("for addr in $gw ${BOX_SELF:-}"), text.index("for entry in ${BOX_ALLOW:-}"))
        self.assertLess(text.index("for entry in ${BOX_ALLOW:-}"), text.index("for net in 0.0.0.0/8"))
        self.assertIn("--no-new-privs", text)
        self.assertTrue(os.access(ROOT / "sandbox" / "entry.sh", os.X_OK))


if __name__ == "__main__":
    unittest.main()

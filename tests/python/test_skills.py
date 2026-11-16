"""Settings → Claude skills: reading what is installed, and installing it.

Claude Code's files are moved into a temporary folder and the `claude` CLI is
a fake that records what it was asked, so nothing here touches the real
configuration.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import skills  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude_json = self.tmp / ".claude.json"
        self.claude_json.write_text(json.dumps({"mcpServers": {}}))
        self.calls = []
        patches = [
            mock.patch.object(skills, "CLAUDE_JSON", self.claude_json),
            mock.patch.object(skills, "SKILLS_DIR", self.tmp / "skills"),
            mock.patch.object(skills, "RUN", self.fake_claude),
            mock.patch.object(skills.shutil, "which", lambda name: "/bin/claude"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_claude(self, args):
        """Does to the fake ~/.claude.json what the real CLI would."""
        self.calls.append(args)
        data = json.loads(self.claude_json.read_text())
        if args[1:3] == ["mcp", "add"]:
            # Parse like the real CLI: -e is variadic, taking every word up
            # to the next option, and each must be KEY=value.
            cut = args.index("--")
            env, positional, i = {}, [], 3
            while i < cut:
                if args[i] == "--scope":
                    i += 2
                elif args[i] == "-e":
                    i += 1
                    while i < cut and not args[i].startswith("-"):
                        key, eq, value = args[i].partition("=")
                        if not eq:
                            return False, f"Invalid environment variable format: {args[i]}"
                        env[key] = value
                        i += 1
                else:
                    positional.append(args[i])
                    i += 1
            if len(positional) != 1:
                return False, "expected exactly one server name"
            data["mcpServers"][positional[0]] = {"type": "stdio", "command": args[cut + 1],
                                                 "args": args[cut + 2:], "env": env}
        elif args[1:3] == ["mcp", "remove"]:
            data["mcpServers"].pop(args[-1], None)
        self.claude_json.write_text(json.dumps(data))
        return True, ""

    def item(self, item_id):
        return next(i for i in skills.read_skills()["items"] if i["id"] == item_id)


class Mcp(Fixture):
    def test_off_until_turned_on_then_registered_user_wide(self):
        self.assertFalse(self.item("mcp")["on"])
        ok, _ = skills.set_skill("mcp", True)
        self.assertTrue(ok)
        add = self.calls[-1]
        self.assertEqual(add[:5], ["/bin/claude", "mcp", "add", "--scope", "user"])
        self.assertEqual(add[-1], str(skills.MCP_SCRIPT))
        self.assertTrue(self.item("mcp")["on"])
        self.assertFalse(self.item("mcp-approvals")["on"])

    def test_off_removes_it(self):
        skills.set_skill("mcp", True)
        skills.set_skill("mcp", False)
        self.assertEqual(self.calls[-1][1:], ["mcp", "remove", "--scope", "user", "watchtower"])
        self.assertFalse(self.item("mcp")["on"])

    def test_approvals_need_the_server_and_reregister_with_the_flag(self):
        ok, _ = skills.set_skill("mcp-approvals", True)
        self.assertFalse(ok)
        self.assertIn("blocked", self.item("mcp-approvals"))
        skills.set_skill("mcp", True)
        ok, _ = skills.set_skill("mcp-approvals", True)
        self.assertTrue(ok)
        self.assertIn(f"{skills.APPROVALS_ENV}=1", self.calls[-1])
        self.assertTrue(self.item("mcp-approvals")["on"])
        skills.set_skill("mcp-approvals", False)
        self.assertFalse(self.item("mcp-approvals")["on"])
        self.assertTrue(self.item("mcp")["on"])

    def test_someone_elses_server_is_left_alone(self):
        self.claude_json.write_text(json.dumps(
            {"mcpServers": {"watchtower": {"command": "node", "args": ["/elsewhere.js"]}}}))
        self.assertIn("blocked", self.item("mcp"))
        self.assertFalse(skills.set_skill("mcp", True)[0])
        self.assertFalse(skills.set_skill("mcp", False)[0])
        self.assertEqual(self.calls, [])


class Skill(Fixture):
    def test_installed_as_a_link_and_removed(self):
        ok, _ = skills.set_skill("skill", True)
        self.assertTrue(ok)
        spot = self.tmp / "skills" / "watchtower"
        self.assertTrue(spot.is_symlink())
        self.assertTrue((spot / "SKILL.md").exists())
        self.assertTrue(self.item("skill")["on"])
        skills.set_skill("skill", False)
        self.assertFalse(spot.exists())

    def test_a_skill_that_is_not_ours_is_left_alone(self):
        spot = self.tmp / "skills" / "watchtower"
        spot.mkdir(parents=True)
        (spot / "SKILL.md").write_text("mine")
        self.assertIn("blocked", self.item("skill"))
        self.assertFalse(skills.set_skill("skill", True)[0])
        self.assertFalse(skills.set_skill("skill", False)[0])
        self.assertEqual((spot / "SKILL.md").read_text(), "mine")

    def test_the_skill_has_its_frontmatter(self):
        text = (skills.SKILL_SOURCE / "SKILL.md").read_text()
        self.assertTrue(text.startswith("---\nname: watchtower\ndescription: "))

    def test_the_other_skills_install_the_same_way_and_carry_their_files(self):
        for item, name in (("preview-skill", "watchtower-preview"), ("style-skill", "watchtower-style"),
                           ("research-skill", "watchtower-research")):
            self.assertTrue(skills.set_skill(item, True)[0])
            spot = self.tmp / "skills" / name
            self.assertTrue(spot.is_symlink())
            self.assertTrue((spot / "SKILL.md").read_text().startswith(f"---\nname: {name}\ndescription: "))
            self.assertTrue(self.item(item)["on"])
        # The style skill's stylesheet, and the full guide through its link.
        style = self.tmp / "skills" / "watchtower-style"
        self.assertIn("--wt-lime", (style / "watchtower.css").read_text())
        self.assertIn("Watchtower", (style / "styleguide.html").read_text())

    def test_unknown_item(self):
        self.assertFalse(skills.set_skill("nope", True)[0])


if __name__ == "__main__":
    unittest.main()

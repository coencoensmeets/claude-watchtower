"""The MCP server: the protocol, finding sessions, and what it refuses.

Run against a fake panel — a function standing in for the HTTP calls — so
nothing here sends to a real session.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower.mcp import APPROVALS_ENV, Server, ToolError, Tools, own_session, resolve  # noqa: E402

ME = "aaaaaaaa-0000-0000-0000-000000000000"
TERM = "bbbbbbbb-0000-0000-0000-000000000000"
HELD = "cccccccc-0000-0000-0000-000000000000"
QUESTION = {"question": "Which colour?", "header": "Colour", "multiSelect": False,
            "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}


def feed(ask=None):
    return {
        "sessions": [
            {"sessionId": ME, "name": "me", "pid": None, "alive": False, "status": "busy", "cwd": "/h/me"},
            {"sessionId": TERM, "name": "api-server", "pid": 4242, "alive": True, "status": "idle", "cwd": "/h/api"},
            {"sessionId": HELD, "name": "api-client", "pid": None, "alive": False, "status": "waiting", "cwd": "/h/c"},
        ],
        "owned": {ME: {"here": True, "running": True}, HELD: {"here": True, "running": True, "ask": ask}},
        "moved": {"old-id": ME},
    }


class FakePanel:
    def __init__(self, ask=None):
        self.ask = ask
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/api/state":
            return feed(self.ask)
        if path.startswith("/api/transcript"):
            return {"messages": [{"role": "user", "text": "hello", "from": "x"},
                                 {"role": None, "text": "", "tools": [{"name": "Read", "detail": "a.py"}]},
                                 {"role": "assistant", "text": "hi"}]}
        return {"ok": True, "message": "done"}


def tools(panel, env=None):
    return Tools(call=panel, parents=[], env={"CLAUDE_CODE_SESSION_ID": ME, **(env or {})})


class Finding(unittest.TestCase):
    def test_by_id_name_prefix_and_part(self):
        sessions = feed()["sessions"]
        self.assertEqual(resolve(sessions, TERM)["name"], "api-server")
        self.assertEqual(resolve(sessions, "API-SERVER")["sessionId"], TERM)
        self.assertEqual(resolve(sessions, "cccccccc")["sessionId"], HELD)
        self.assertEqual(resolve(sessions, "client")["sessionId"], HELD)

    def test_ambiguous_and_missing_are_refused(self):
        sessions = feed()["sessions"]
        with self.assertRaisesRegex(ToolError, "more than one"):
            resolve(sessions, "api")
        with self.assertRaisesRegex(ToolError, "No session"):
            resolve(sessions, "nothing")

    def test_self_from_env_pid_and_moved_id(self):
        sessions = feed()["sessions"]
        self.assertEqual(own_session(sessions, [], {"CLAUDE_CODE_SESSION_ID": ME})["name"], "me")
        self.assertEqual(own_session(sessions, [1, 4242], {})["name"], "api-server")
        self.assertEqual(own_session(sessions, [], {"CLAUDE_CODE_SESSION_ID": "old-id"},
                                     {"old-id": ME})["name"], "me")
        self.assertIsNone(own_session(sessions, [], {}))


class Sending(unittest.TestCase):
    def test_terminal_session_goes_down_its_socket_with_the_sender_named(self):
        panel = FakePanel()
        tools(panel).send_message("api-server", "run the tests")
        method, path, body = panel.calls[-1]
        self.assertEqual(path, "/api/say")
        self.assertIn('from-name="me"', body["text"])
        self.assertIn("run the tests", body["text"])

    def test_panel_run_session_gets_a_turn(self):
        panel = FakePanel()
        tools(panel).send_message("api-client", "hello")
        self.assertEqual(panel.calls[-1][1], "/api/owned/say")

    def test_never_to_itself(self):
        with self.assertRaisesRegex(ToolError, "this session"):
            tools(FakePanel()).send_message("me", "hello")

    def test_ending_itself_is_refused(self):
        panel = FakePanel()
        with self.assertRaises(ToolError):
            tools(panel).end_session(ME)
        self.assertFalse(any(c[1] == "/api/end" for c in panel.calls))


class Answering(unittest.TestCase):
    def test_question_answered_with_labels(self):
        panel = FakePanel(ask={"requestId": "r1", "asks": True, "tool": "AskUserQuestion",
                               "input": {"questions": [QUESTION]}})
        tools(panel).answer_question("api-client", {"Which colour?": "Blue"})
        _, path, body = panel.calls[-1]
        self.assertEqual(path, "/api/owned/answer")
        self.assertEqual(body["answers"], {"Which colour?": ["Blue"]})
        self.assertEqual(body["behavior"], "allow")

    def test_allowing_a_tool_is_off_by_default_but_denying_is_not(self):
        ask = {"requestId": "r2", "asks": False, "tool": "Bash", "input": {}}
        panel = FakePanel(ask=ask)
        with self.assertRaisesRegex(ToolError, APPROVALS_ENV):
            tools(panel).answer_permission("api-client", True)
        self.assertFalse(any(c[1] == "/api/owned/answer" for c in panel.calls))
        tools(panel).answer_permission("api-client", False, "not now")
        self.assertEqual(panel.calls[-1][2]["behavior"], "deny")

    def test_allowing_works_once_switched_on(self):
        panel = FakePanel(ask={"requestId": "r3", "asks": False, "tool": "Bash", "input": {}})
        tools(panel, {APPROVALS_ENV: "1"}).answer_permission("api-client", True)
        self.assertEqual(panel.calls[-1][2]["behavior"], "allow")


class Protocol(unittest.TestCase):
    def test_handshake_list_and_call(self):
        server = Server(tools(FakePanel()))
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                 ["result"]["tools"]]
        self.assertIn("send_message", names)
        listed = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "list_sessions", "arguments": {}}})["result"]
        self.assertEqual(listed["structuredContent"]["you"], "me")

    def test_errors_are_tool_results_not_crashes(self):
        server = Server(tools(FakePanel()))
        bad = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "send_message", "arguments": {"session": "zzz", "text": "x"}}})
        self.assertTrue(bad["result"]["isError"])
        wrong = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                               "params": {"name": "send_message", "arguments": {"nope": 1}}})
        self.assertTrue(wrong["result"]["isError"])
        self.assertIn("error", server.handle({"jsonrpc": "2.0", "id": 6, "method": "bogus"}))

    def test_conversation_skips_tool_rows_unless_asked(self):
        out = tools(FakePanel()).read_conversation("api-server")
        self.assertEqual([m.get("text") for m in out["messages"]], ["hello", "hi"])
        full = tools(FakePanel()).read_conversation("api-server", include_tools=True)
        self.assertEqual(full["messages"][1]["tools"], ["Read a.py"])


if __name__ == "__main__":
    unittest.main()

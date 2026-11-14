#!/usr/bin/env python3
"""A made-up machine for the README's screenshots.

Builds a throwaway HOME with five Claude Code sessions in it, each in a small
git repository, with conversations, edits, a question waiting for an answer
and token usage. Nothing in it comes from your own sessions, so the pictures
can go in a public README.

    python3 tools/readme/demo.py /tmp/wt-demo     # build, and hold the sessions open

It prints the command that starts a panel against it. The panel must run with
HOME set to the demo's home, so it cannot see your real sessions or pins.
Ctrl-C takes the sessions down; delete the folder afterwards.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BURN = (
    "import time\n"
    "while True:\n"
    "    end = time.time() + 0.01\n"
    "    while time.time() < end: pass\n"
    "    time.sleep(0.19)\n"
)

HOLD = (
    "import json, signal, subprocess, sys\n"
    "child = subprocess.Popen(json.loads(sys.argv[1]))\n"
    "print(child.pid, flush=True)\n"
    "signal.signal(signal.SIGTERM, lambda *_: (child.terminate(), sys.exit(0)))\n"
    "child.wait()\n"
)

NOW = datetime.now(timezone.utc)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def slug_of(cwd: str) -> str:
    return "-" + re.sub(r"[^A-Za-z0-9]+", "-", cwd.lstrip("/"))


def usage(inp: int, out: int, cached: int, written: int = 0) -> dict:
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cached, "cache_creation_input_tokens": written}


class Talk:
    """A transcript, written the way Claude Code writes one."""

    def __init__(self, sid: str, cwd: str, model: str):
        self.sid, self.cwd, self.model = sid, cwd, model
        self.lines: list[dict] = []
        self.parent: str | None = None
        self.n = 0

    def _add(self, entry: dict, ago: float) -> None:
        uid = str(uuid.uuid4())
        entry.update({"uuid": uid, "parentUuid": self.parent, "timestamp": iso(ago),
                      "sessionId": self.sid, "cwd": self.cwd, "isSidechain": False,
                      "userType": "external", "version": "2.4.1"})
        self.parent = uid
        self.lines.append(entry)

    def user(self, text: str, ago: float) -> None:
        self._add({"type": "user", "message": {"role": "user", "content": text}}, ago)

    def say(self, text: str, ago: float, u: dict | None = None) -> None:
        self._add({"type": "assistant", "message": {
            "role": "assistant", "model": self.model, "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": u or usage(6, 420, 38000, 1200)}}, ago)

    def tool(self, name: str, inp: dict, ago: float, result: str | None = None,
             extra: dict | None = None, text: str | None = None, u: dict | None = None) -> str:
        self.n += 1
        tid = f"toolu_demo{self.n:04d}{self.sid[:8]}"
        blocks = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]
        self._add({"type": "assistant", "message": {
            "role": "assistant", "model": self.model, "stop_reason": "tool_use",
            "content": blocks, "usage": u or usage(4, 180, 36000, 900)}}, ago)
        if result is not None:
            entry = {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": result}]}}
            if extra:
                entry["toolUseResult"] = extra
            self._add(entry, ago - 0.2)
        return tid

    def edit(self, path: str, hunks: list[dict], ago: float, text: str | None = None) -> None:
        old = "\n".join(l[1:] for h in hunks for l in h["lines"] if l[0] in " -")
        new = "\n".join(l[1:] for h in hunks for l in h["lines"] if l[0] in " +")
        self.tool("Edit", {"file_path": path, "old_string": old, "new_string": new}, ago,
                  result=f"The file {path} has been updated.",
                  extra={"filePath": path, "oldString": old, "newString": new,
                         "structuredPatch": hunks, "userModified": False}, text=text)

    def title(self, text: str) -> None:
        self.lines.append({"type": "ai-title", "aiTitle": text, "sessionId": self.sid})

    def write(self, home: Path) -> None:
        folder = home / ".claude" / "projects" / slug_of(self.cwd)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{self.sid}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in self.lines))


def git(cwd: Path, *args: str, when: float | None = None) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Sam Rivera", "GIT_AUTHOR_EMAIL": "sam@example.com",
           "GIT_COMMITTER_NAME": "Sam Rivera", "GIT_COMMITTER_EMAIL": "sam@example.com",
           "HOME": str(cwd.parent)}
    if when is not None:
        stamp = (NOW - timedelta(hours=when)).strftime("%Y-%m-%dT%H:%M:%S")
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def repo(root: Path, files: dict[str, str], history: list[tuple[str, dict[str, str], float]],
         branch: tuple[str, list[tuple[str, dict[str, str], float]]] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for msg, changes, hours in history:
        for name, body in changes.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", msg, when=hours)
    if branch:
        name, commits = branch
        git(root, "checkout", "-q", "-b", name)
        for msg, changes, hours in commits:
            for fname, body in changes.items():
                (root / fname).parent.mkdir(parents=True, exist_ok=True)
                (root / fname).write_text(body)
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", msg, when=hours)
        git(root, "checkout", "-q", "main")
        git(root, "merge", "-q", "--no-ff", name, "-m", f"Merge {name}", when=commits[-1][2] - 0.5)
    # What the sessions are in the middle of: uncommitted work.
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body)


def build(base: Path) -> tuple[Path, Path, list[dict]]:
    # DEMO_HOME puts the home somewhere that reads like a real one, such as
    # /home/sam inside a private mount namespace (see shoot.sh).
    home = Path(os.environ.get("DEMO_HOME") or base / "home")
    sessions = base / "sessions"
    if base.exists():
        shutil.rmtree(base)
    if home.exists():
        for child in home.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    sessions.mkdir(parents=True)
    home.mkdir(parents=True, exist_ok=True)
    plan: list[dict] = []

    # ---- robot-arm: working, on Opus, mid test run ---------------------------------
    arm = home / "robot-arm"
    repo(arm, {
        "gripper/pid.py": "class PID:\n    def __init__(self, kp=1.4, ki=0.08, kd=0.22, limit=0.6):\n"
                          "        self.kp, self.ki, self.kd, self.limit = kp, ki, kd, limit\n"
                          "        self.integral = 0.0\n        self.last = None\n",
        "tests/test_pid.py": "from gripper.pid import PID\n\n\ndef test_soft_object_does_not_overshoot():\n"
                             "    pid = PID()\n    assert max(pid.simulate(target=0.4)) < 0.42\n",
    }, [
        ("First import of the arm controller", {"README.md": "# Robot arm\n", "gripper/pid.py": "class PID:\n    pass\n"}, 70),
        ("Add joint limits", {"arm/limits.py": "LIMITS = {'shoulder': 170, 'elbow': 145}\n"}, 52),
        ("Read the force sensor at 1 kHz", {"gripper/force.py": "RATE = 1000\n"}, 30),
    ], ("feature/gripper", [
        ("Gripper: first PID controller", {"gripper/pid.py": "class PID:\n    def __init__(self, kp=2.0, ki=0.2, kd=0.0):\n        pass\n"}, 20),
        ("Gripper: clamp the output", {"gripper/clamp.py": "def clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n"}, 12),
    ]))
    sid = str(uuid.uuid4())
    t = Talk(sid, str(arm), "claude-opus-5-5")
    t.user("The gripper overshoots when it closes on soft objects — the strawberry test squashes about one in five. "
           "Can you tune the PID loop and add a test that catches it?", 14)
    t.tool("Read", {"file_path": str(arm / "gripper/pid.py")}, 13.6, result="class PID: ...",
           text="I'll start with the controller and the force readings it gets.")
    t.tool("Grep", {"pattern": "PID\\(", "path": str(arm)}, 13.2, result="gripper/close.py:12: pid = PID(kp=2.0, ki=0.2)")
    t.say("Found it. The integral term winds up while the fingers are still travelling, so by the time they touch "
          "the fruit there is a lot of stored push. Three changes:\n\n"
          "1. **Lower the gains** — `kp` 2.0 → 1.4, `ki` 0.2 → 0.08\n"
          "2. **Add damping** — `kd` 0.22, so it slows as it closes\n"
          "3. **Anti-windup** — clamp the integral to `±limit`\n\n"
          "Then a test that closes on a simulated soft object and fails on overshoot.", 12.4,
          u=usage(8, 610, 41000, 2400))
    t.edit(str(arm / "gripper/pid.py"), [{"oldStart": 1, "oldLines": 4, "newStart": 1, "newLines": 6, "lines": [
        " class PID:",
        "-    def __init__(self, kp=2.0, ki=0.2, kd=0.0):",
        "-        self.kp, self.ki, self.kd = kp, ki, kd",
        "+    def __init__(self, kp=1.4, ki=0.08, kd=0.22, limit=0.6):",
        "+        self.kp, self.ki, self.kd, self.limit = kp, ki, kd, limit",
        "         self.integral = 0.0",
        "+        self.last = None",
        "+",
    ]}], 11.5)
    t.edit(str(arm / "gripper/pid.py"), [{"oldStart": 14, "oldLines": 3, "newStart": 16, "newLines": 4, "lines": [
        "     def step(self, error, dt):",
        "-        self.integral += error * dt",
        "+        # Anti-windup: stored push is what squashed the strawberries.",
        "+        self.integral = max(-self.limit, min(self.limit, self.integral + error * dt))",
        "         return self.kp * error + self.ki * self.integral",
    ]}], 10.8)
    t.tool("Write", {"file_path": str(arm / "tests/test_pid.py"), "content": "..."}, 9.9,
           result="File created successfully", extra={"type": "create", "filePath": str(arm / "tests/test_pid.py"),
           "content": "from gripper.pid import PID\n\n\ndef test_soft_object_does_not_overshoot():\n"
                      "    pid = PID()\n    assert max(pid.simulate(target=0.4)) < 0.42\n"})
    t.tool("Bash", {"command": "pytest tests/ -q", "description": "Run the test suite"}, 8.9,
           result="........F.\nFAILED tests/test_pid.py::test_soft_object_does_not_overshoot - assert 0.431 < 0.42\n1 failed, 9 passed in 1.84s")
    t.say("Close — 0.431 against a limit of 0.42. The derivative is reading a noisy force signal, so I'll filter it "
          "before it reaches `kd`.", 8.1)
    t.edit(str(arm / "gripper/pid.py"), [{"oldStart": 18, "oldLines": 2, "newStart": 18, "newLines": 4, "lines": [
        "-        derivative = (error - self.last) / dt",
        "+        # Low-pass the derivative: the force sensor is noisy at 1 kHz.",
        "+        raw = (error - self.last) / dt",
        "+        derivative = 0.8 * self.d_last + 0.2 * raw",
        "         self.last = error",
    ]}], 7.2)
    t.tool("Bash", {"command": "pytest tests/ -q && python -m sim.strawberry --runs 200",
                    "description": "Run the tests and the strawberry simulation"}, 0.3,
           u=usage(5, 120, 47000, 600))
    t.title("Tune the gripper PID loop")
    t.write(home)
    plan.append({"sid": sid, "cwd": arm, "name": "robot-arm-3f", "status": "busy", "busy": True, "age": 0.05, "host": "ghostty"})

    # ---- robot-arm, second session: finished, wrote docs ----------------------------
    sid = str(uuid.uuid4())
    t = Talk(sid, str(arm), "claude-sonnet-5-5")
    t.user("Write a calibration guide for the arm, for someone setting it up for the first time.", 48)
    t.tool("Read", {"file_path": str(arm / "arm/limits.py")}, 47.5, result="LIMITS = {...}")
    t.tool("Write", {"file_path": str(arm / "docs/calibration.md"), "content": "..."}, 46,
           result="File created successfully", extra={"type": "create", "filePath": str(arm / "docs/calibration.md"),
           "content": "# Calibrating the arm\n\n1. Home every joint.\n2. Zero the force sensor with the gripper open.\n"
                      "3. Close on the calibration block and check the reading is 2.0 N ± 0.1.\n"})
    t.say("The guide is in `docs/calibration.md`: homing, zeroing the force sensor, and a check with the "
          "calibration block. It takes about ten minutes.", 45, u=usage(6, 380, 22000, 800))
    t.title("Write the calibration guide")
    t.write(home)
    plan.append({"sid": sid, "cwd": arm, "name": "robot-arm-9c", "status": "idle", "busy": False, "age": 45, "host": "tmux"})

    # ---- coffee-roastery: waiting on a question --------------------------------------
    shop = home / "coffee-roastery"
    site = shop / "site"
    repo(shop, {"site/checkout.html": "<!-- checkout -->\n"}, [
        ("Landing page", {"site/index.html": "<h1>Roastery</h1>\n"}, 40),
        ("Menu and opening hours", {"site/menu.html": "<h1>Menu</h1>\n"}, 26),
        ("Photos of the beans", {"site/beans.html": "<h1>Beans</h1>\n"}, 6),
    ])
    sid = str(uuid.uuid4())
    t = Talk(sid, str(shop), "claude-opus-5-5")
    t.user("Add a checkout to the roastery site, so people can order beans for pickup.", 6)
    t.tool("Read", {"file_path": str(site / "index.html")}, 5.6, result="<h1>Roastery</h1>")
    t.say("I've sketched the basket and the pickup slot picker. Before I wire up payment I need one decision from you.", 3.2,
          u=usage(6, 520, 30000, 1800))
    t.tool("AskUserQuestion", {"questions": [{
        "question": "Which payment provider should the checkout use?",
        "header": "Payments", "multiSelect": False,
        "options": [
            {"label": "Stripe", "description": "Cards, Apple Pay and iDEAL. Most docs, 1.5% + €0.25 per order."},
            {"label": "Mollie", "description": "Strong in the Netherlands, iDEAL first. 1.8% + €0.25."},
            {"label": "Pay at pickup", "description": "No online payment yet. Simplest, but more no-shows."},
        ]}]}, 3.0)
    t.title("Checkout for the roastery site")
    t.write(home)
    plan.append({"sid": sid, "cwd": shop, "name": "coffee-roastery-a1", "status": "waiting", "busy": False, "age": 3.0, "host": "ghostty"})

    # A page the session made, for the Preview tab.
    site.mkdir(parents=True, exist_ok=True)
    (site / "watchtower.css").write_text((Path(__file__).resolve().parents[2] / "skills/watchtower-style/watchtower.css").read_text())
    (site / "index.html").write_text(ROASTERY)

    # ---- api-client: finished its turn, ready -----------------------------------------
    api = home / "api-client"
    repo(api, {"client/retry.py": "def backoff(attempt, base=0.5, cap=30):\n    return min(cap, base * 2 ** attempt)\n"}, [
        ("HTTP client with sessions", {"client/http.py": "import urllib.request\n"}, 90),
        ("Typed responses", {"client/types.py": "from dataclasses import dataclass\n"}, 60),
        ("Retry on 5xx", {"client/retry.py": "def backoff(attempt):\n    return 1\n"}, 4),
    ])
    sid = str(uuid.uuid4())
    t = Talk(sid, str(api), "claude-sonnet-5-5")
    t.user("Requests to the weather API fail every few minutes with a 503. Make the client retry properly.", 25)
    t.tool("Read", {"file_path": str(api / "client/retry.py")}, 24, result="def backoff(attempt): return 1")
    t.edit(str(api / "client/retry.py"), [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": [
        "-def backoff(attempt):",
        "-    return 1",
        "+def backoff(attempt, base=0.5, cap=30):",
        "+    return min(cap, base * 2 ** attempt)",
    ]}], 22)
    t.tool("Bash", {"command": "pytest -q", "description": "Run the tests"}, 20, result="24 passed in 0.92s")
    t.say("Done. The client now retries a 503 or a timeout up to five times, waiting longer each time.\n\n"
          "| Attempt | Waits |\n|---|---|\n| 1 | 0.5 s |\n| 2 | 1 s |\n| 3 | 2 s |\n| 4 | 4 s |\n| 5 | 8 s |\n\n"
          "It honours a `Retry-After` header when the server sends one, and gives up straight away on a 4xx. "
          "All 24 tests pass. Want me to add jitter, so many clients don't retry in step?", 19,
          u=usage(8, 700, 28000, 1500))
    t.title("Retry the weather API properly")
    t.write(home)
    plan.append({"sid": sid, "cwd": api, "name": "api-client-5e", "status": "idle", "busy": False, "age": 19, "host": "code"})

    # ---- garden-planner: working on Sonnet ---------------------------------------------
    garden = home / "garden-planner"
    repo(garden, {"src/frost.ts": "export const lastFrost = (zone: number) => new Date();\n"}, [
        ("Plant database", {"src/plants.ts": "export const plants = [];\n"}, 120),
        ("Bed layout grid", {"src/beds.ts": "export const beds = [];\n"}, 80),
    ])
    sid = str(uuid.uuid4())
    t = Talk(sid, str(garden), "claude-sonnet-5-5")
    t.user("Add a sowing calendar: for each plant, when to sow indoors and outdoors, based on the last frost date.", 9)
    t.tool("Read", {"file_path": str(garden / "src/plants.ts")}, 8.5, result="export const plants = [...]")
    t.tool("Agent", {"subagent_type": "Explore", "description": "Find frost dates by climate zone"}, 1.2,
           u=usage(4, 90, 26000, 400))
    t.title("Sowing calendar from frost dates")
    t.write(home)
    plan.append({"sid": sid, "cwd": garden, "name": "garden-planner-77", "status": "busy", "busy": True, "age": 0.1, "host": "kitty"})

    return home, sessions, plan


ROASTERY = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Roastery · Order beans</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Urbanist:wght@300..800&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="watchtower.css">
<style>
  .beans { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--wt-s4); margin-top: var(--wt-s5); }
  .bean { padding: var(--wt-s5); border-radius: var(--wt-r-lg); background: var(--wt-surface); display: flex; flex-direction: column; gap: 10px; }
  .bean__art { height: 120px; border-radius: 22px; background: #1E1F24; display: grid; place-items: center; }
  .bean h3 { margin: 0; font-size: 19px; }
  .bean p { margin: 0; color: var(--wt-text-2); font-size: 14px; }
  .bean .row { display: flex; align-items: center; justify-content: space-between; margin-top: auto; }
  .price { font-weight: 700; font-size: 20px; font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<section class="hero"><div class="wrap">
  <h1>Fresh beans, <span class="pill-word">roasted</span> on Friday</h1>
  <p class="lede">Order before Thursday noon and pick them up warm from the roastery on Mill Lane.</p>
</div></section>
<section><div class="wrap">
  <div class="beans">
    <div class="bean"><div class="bean__art"><svg width="54" height="54" viewBox="0 0 24 24"><ellipse cx="12" cy="12" rx="6" ry="9" fill="none" stroke="#F2F1F6" stroke-width="1.6" transform="rotate(30 12 12)"/><path d="M9 5c3 4 3 10 6 14" fill="none" stroke="#CBDA72" stroke-width="1.6"/></svg></div>
      <h3>Huila, Colombia</h3><p>Red apple, panela, a round body.</p><span class="tag">Filter</span>
      <div class="row"><span class="price">€ 14</span><button class="btn btn--sm">Add</button></div></div>
    <div class="bean"><div class="bean__art"><svg width="54" height="54" viewBox="0 0 24 24"><ellipse cx="12" cy="12" rx="6" ry="9" fill="none" stroke="#F2F1F6" stroke-width="1.6" transform="rotate(-20 12 12)"/><path d="M10 4c2 5 1 11 4 16" fill="none" stroke="#F28C4E" stroke-width="1.6"/></svg></div>
      <h3>Yirgacheffe, Ethiopia</h3><p>Jasmine, bergamot, lemon curd.</p><span class="tag">Filter</span>
      <div class="row"><span class="price">€ 16</span><button class="btn btn--sm">Add</button></div></div>
    <div class="bean"><div class="bean__art"><svg width="54" height="54" viewBox="0 0 24 24"><ellipse cx="12" cy="12" rx="6" ry="9" fill="none" stroke="#F2F1F6" stroke-width="1.6" transform="rotate(10 12 12)"/><path d="M9.5 4.5c3 5 2 10 5 15" fill="none" stroke="#A59FE2" stroke-width="1.6"/></svg></div>
      <h3>House espresso</h3><p>Dark chocolate, hazelnut, sticky.</p><span class="tag">Espresso</span>
      <div class="row"><span class="price">€ 12</span><button class="btn btn--sm">Add</button></div></div>
  </div>
</div></section>
</body>
</html>
"""


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/wt-demo").resolve()
    home, sessions, plan = build(base)
    # Each session runs under a stand-in for the app that owns it: python under
    # a terminal's or an editor's name, which the panel reads off the process
    # tree to label the row.
    bindir = base / "bin"
    bindir.mkdir(exist_ok=True)
    children = []
    for p in plan:
        host = bindir / p["host"]
        if not host.exists():
            host.symlink_to(sys.executable)
        cmd = [sys.executable, "-c", BURN] if p["busy"] else ["sleep", "86400"]
        holder = subprocess.Popen([str(host), "-c", HOLD, json.dumps(cmd)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        children.append(holder)
        pid = int(holder.stdout.readline())
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
        written = time.time() - p["age"] * 60
        data = {"sessionId": p["sid"], "pid": pid, "procStart": stat[19], "cwd": str(p["cwd"]),
                "name": p["name"], "startedAt": int((time.time() - 3 * 3600) * 1000),
                "updatedAt": int(written * 1000), "status": p["status"], "statusUpdatedAt": int(written * 1000)}
        f = sessions / f"{p['sid']}.json"
        f.write_text(json.dumps(data, indent=2))
        os.utime(f, (written, written))
    (base / "plan.json").write_text(json.dumps([{**p, "cwd": str(p["cwd"])} for p in plan], indent=2))
    print(f"{len(plan)} demo sessions under {base}\n")
    print(f"  HOME={home} CLAUDE_WATCHTOWER_SESSION_DIR={sessions} python3 server.py --local --port 8790\n")
    sys.stdout.flush()

    def clean(*_):
        for c in children:
            c.terminate()
        print("demo sessions taken down")
        sys.exit(0)

    signal.signal(signal.SIGINT, clean)
    signal.signal(signal.SIGTERM, clean)
    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())

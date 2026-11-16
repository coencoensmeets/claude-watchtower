"""Installing the panel as a systemd user service.

The unit in the repository (claude-watchtower.service) is a template. The
paths in it are this install's own — where the clone is, which Python, which
display, and the PATH that finds `claude` and `node` — so they are filled in
here, from the shell that runs `python3 server.py --install-service`, rather
than written into the file for one person's folder layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from watchtower.config import HOME, ROOT

UNIT_NAME = "claude-watchtower.service"
TEMPLATE = ROOT / UNIT_NAME


def unit_text(python: str, root: Path, display: str, path: str) -> str:
    text = TEMPLATE.read_text()
    # The template's own note is about the template; the installed unit says
    # where it came from instead.
    body = text[text.index("[Unit]"):]
    for key, value in (("@PYTHON@", python), ("@ROOT@", str(root)),
                       ("@DISPLAY@", display), ("@PATH@", path)):
        body = body.replace(key, value)
    return (f"# Installed by `{python} {root}/server.py --install-service`.\n"
            "# Run that again after moving the clone or changing Python.\n" + body)


def install(env: dict | None = None, run=subprocess.run) -> tuple[bool, str]:
    """Write the unit for this machine, reload systemd and start it."""
    env = os.environ if env is None else env
    target = Path(env.get("XDG_CONFIG_HOME") or HOME / ".config") / "systemd" / "user" / UNIT_NAME
    text = unit_text(sys.executable, ROOT, env.get("DISPLAY") or ":0", env.get("PATH") or "/usr/bin:/bin")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    if not shutil.which("systemctl"):
        return False, f"Wrote {target}, but there is no systemctl here to start it."
    for argv in (["systemctl", "--user", "daemon-reload"],
                 ["systemctl", "--user", "enable", "--now", UNIT_NAME]):
        done = run(argv, capture_output=True, text=True)
        if done.returncode != 0:
            return False, f"Wrote {target}, but `{' '.join(argv)}` failed: {(done.stderr or '').strip()}"
    return True, (f"Installed and started {target}.\n"
                  f"  status: systemctl --user status {UNIT_NAME}\n"
                  f"  logs:   journalctl --user -u {UNIT_NAME}")

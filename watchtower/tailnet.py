"""Reaching the panel from anywhere, through Tailscale.

On the same wifi a phone opens the panel's LAN address. Away from it there is
no address to open: the laptop is behind somebody's router and the panel is not
on the internet — on purpose, see the README. Tailscale is the way round that
the panel arranges rather than the way it builds: a private WireGuard network
between your own devices, which the laptop joins with outgoing connections only.
Nothing is opened on any router, and under WSL nothing has to get through
Windows' firewall either, since the tunnel is dialled out from Linux.

**Why the key still holds.** A phone on the tailnet arrives from its own
100.x address, not from loopback, so it is asked for the key exactly as a phone
on the wifi is. That is why the panel uses the tailnet address directly and
never `tailscale serve` or `funnel`: both are a proxy on this machine, every
request would arrive from 127.0.0.1, and the panel lets loopback in without a
key.

**What it can and cannot do.** Installing Tailscale and letting a user who is
not root change its settings both take sudo, so for those the panel shows the
one command to run. Everything after that — turning it on, the login link,
turning it off — is the `tailscale` CLI, which talks to the daemon over its
socket.

Standard library only, like the rest of the panel.
"""

from __future__ import annotations

import getpass
import json
import shutil
import subprocess
import threading
import time

STATUS_SECONDS = 5.0
CACHE_SECONDS = 10.0
# How long a fresh `tailscale up` is given to come back with a login link.
LOGIN_WAIT = 10.0
# And how long it stays alive waiting for that link to be opened.
LOGIN_TIMEOUT = "10m"

INSTALL_COMMAND = "curl -fsSL https://tailscale.com/install.sh | sh"


def operator_command() -> str:
    """What lets this user turn Tailscale on and off without sudo every time."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "$USER"
    return f"sudo tailscale set --operator={user}"


def _cli() -> str | None:
    return shutil.which("tailscale")


def _run(*args: str, timeout: float = STATUS_SECONDS) -> subprocess.CompletedProcess | None:
    cli = _cli()
    if not cli:
        return None
    try:
        return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _denied(text: str) -> bool:
    lowered = text.lower()
    return "access denied" in lowered or "permission denied" in lowered or "operator" in lowered


def parse_status(raw: str) -> dict:
    """The parts of `tailscale status --json` the settings page needs.

    Split out from the call so it can be checked against canned output: a test
    machine has no tailnet.
    """
    try:
        found = json.loads(raw)
    except ValueError:
        return {"installed": True, "state": "unknown"}
    if not isinstance(found, dict):
        return {"installed": True, "state": "unknown"}
    me = found.get("Self") if isinstance(found.get("Self"), dict) else {}
    ips = [ip for ip in (found.get("TailscaleIPs") or me.get("TailscaleIPs") or [])
           if isinstance(ip, str)]
    v4 = next((ip for ip in ips if "." in ip), "")
    name = str(me.get("DNSName") or "").rstrip(".")
    state = str(found.get("BackendState") or "unknown")
    return {
        "installed": True,
        "state": state,                         # Running, NeedsLogin, Stopped, Starting, …
        "running": state == "Running" and bool(v4),
        "address": v4 if state == "Running" else "",
        "name": name if state == "Running" else "",
        "login": str(found.get("AuthURL") or ""),
    }


_cache: dict = {}
_lock = threading.Lock()


def status(fresh: bool = False) -> dict:
    """Whether Tailscale is here and up, and the address a phone on it opens.

    {"installed": False, "install": …} when there is no CLI, otherwise what
    parse_status says plus the command that grants operator rights.
    """
    with _lock:
        if not fresh and time.monotonic() - _cache.get("at", 0) < CACHE_SECONDS:
            return _cache["answer"]
        if not _cli():
            answer = {"installed": False, "install": INSTALL_COMMAND}
        else:
            done = _run("status", "--json")
            if done is None:
                answer = {"installed": True, "state": "unknown"}
            elif not done.stdout.strip():
                # No daemon: the CLI is there and tailscaled is not running.
                answer = {"installed": True, "state": "NoDaemon",
                          "message": (done.stderr.strip().splitlines() or [""])[-1]}
            else:
                answer = parse_status(done.stdout)
            answer["operator"] = operator_command()
        _cache.update(at=time.monotonic(), answer=answer)
        return answer


def _reap(process: subprocess.Popen) -> None:
    try:
        process.wait()
    except OSError:
        pass


def turn_on() -> tuple[bool, str, dict]:
    """Bring this machine onto the tailnet.

    Already signed in, that is the whole of it. Not signed in, `tailscale up`
    waits for a login and prints a link to open — so it is started in the
    background and the link read back from the daemon, for the settings page to
    open in a new tab. It goes on waiting there and the page polls until the
    machine is Running.
    """
    cli = _cli()
    if not cli:
        return False, "Tailscale is not installed on this machine", status(fresh=True)
    try:
        process = subprocess.Popen([cli, "up", f"--timeout={LOGIN_TIMEOUT}"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        return False, f"Could not start tailscale: {error}", status(fresh=True)

    deadline = time.monotonic() + LOGIN_WAIT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            said = ((process.stderr.read() if process.stderr else "") or "").strip()
            now = status(fresh=True)
            if process.returncode == 0 or now.get("running"):
                return True, "Connected to your tailnet", now
            if _denied(said):
                return False, f"Tailscale needs permission first — run: {operator_command()}", now
            return False, (said.splitlines() or ["tailscale up failed"])[-1], now
        now = status(fresh=True)
        if now.get("login"):
            threading.Thread(target=_reap, args=(process,), daemon=True).start()
            return True, "Sign in to Tailscale to finish", now
        if now.get("running"):
            threading.Thread(target=_reap, args=(process,), daemon=True).start()
            return True, "Connected to your tailnet", now
        time.sleep(0.4)
    threading.Thread(target=_reap, args=(process,), daemon=True).start()
    return False, "Tailscale did not answer in time — try again", status(fresh=True)


def turn_off() -> tuple[bool, str, dict]:
    """Take this machine off the tailnet: every device on it loses the laptop,
    not just the phone — the settings page says so before it is pressed."""
    done = _run("down", timeout=15.0)
    now = status(fresh=True)
    if done is None:
        return False, "Tailscale did not answer", now
    if done.returncode == 0:
        return True, "Off your tailnet", now
    said = done.stderr.strip()
    if _denied(said):
        return False, f"Tailscale needs permission first — run: {operator_command()}", now
    return False, (said.splitlines() or ["tailscale down failed"])[-1], now

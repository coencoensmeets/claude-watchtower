"""Whether a phone can get through Windows to a panel running under WSL.

Under WSL the panel's socket is fine and the address on the code is right, and
a phone on the same wifi is still told the site cannot be reached. The packets
are stopped on the Windows side, before Linux ever sees them, and nothing on
the Linux side can tell — so the panel asks Windows.

**Mirrored networking** (`networkingMode=mirrored` in .wslconfig) puts WSL on
the laptop's own address, which is why the code points somewhere sensible. But
inbound traffic to the WSL VM goes through the Hyper-V firewall, whose default
for WSL is *block*, and WSL only ships allow rules for ICMP and mDNS. A rule for
the panel's port is the fix, and making one takes an administrator — so the
panel can check, explain, and ask Windows to raise a UAC prompt, but it cannot
quietly do it.

**NAT networking** (the default before mirrored existed) puts WSL behind a
private 172.x address the phone has no route to at all. That needs a port proxy
on Windows and a different address on the code; switching to mirrored is the
smaller change, so that is what the panel suggests.

Everything here is a question to powershell.exe, which takes a couple of
seconds to start. The answer is cached briefly: the settings page asks on every
visit and a rule does not appear by itself.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import threading
import time

from watchtower.control import wsl_powershell

# WSL's VM creator id: the fixed GUID every Hyper-V firewall rule for WSL is
# filed under. From Microsoft's WSL networking documentation.
WSL_VM_CREATOR = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"

CHECK_SECONDS = 20.0
CACHE_SECONDS = 30.0
# Long enough for someone to read a UAC prompt and press Yes.
ELEVATE_SECONDS = 180.0

# Prints exactly one word: open, blocked, or unknown. Read-only, and works
# without elevation.
CHECK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$id = '__ID__'; $port = '__PORT__'
try {
  $vm = Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore -Name $id
} catch { 'unknown'; return }
if ("$($vm.Enabled)" -notin 'True', '1' -or "$($vm.DefaultInboundAction)" -eq 'Allow') { 'open'; return }
$hit = Get-NetFirewallHyperVRule -VMCreatorId $id -Direction Inbound -ErrorAction SilentlyContinue |
  Where-Object {
    "$($_.Enabled)" -in 'True', '1' -and "$($_.Action)" -eq 'Allow' -and
    "$($_.Protocol)" -in 'TCP', 'Any' -and
    (@($_.LocalPorts) -contains $port -or @($_.LocalPorts) -contains 'Any')
  }
if ($hit) { 'open' } else { 'blocked' }
"""


def rule_command(port: int) -> str:
    """The one line an administrator PowerShell needs, shown on the settings
    page as well as run by the button — so it can be pasted by hand where UAC
    is not an option."""
    return (f"New-NetFirewallHyperVRule -Name 'claude-watchtower-{port}' "
            f"-DisplayName 'claude-watchtower (port {port})' -Direction Inbound "
            f"-VMCreatorId '{WSL_VM_CREATOR}' -Protocol TCP -LocalPorts {port} -Action Allow")


def networking_mode() -> str | None:
    """'mirrored', 'nat', something newer, or None when this is not WSL (or a
    WSL too old to have wslinfo, which predates mirrored mode anyway)."""
    if not wsl_powershell() or not shutil.which("wslinfo"):
        return None
    try:
        done = subprocess.run(["wslinfo", "--networking-mode"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    mode = done.stdout.strip().lower()
    return mode if done.returncode == 0 and mode else None


def _powershell(script: str, timeout: float) -> subprocess.CompletedProcess | None:
    powershell = wsl_powershell()
    if not powershell:
        return None
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        # From a Windows folder: started from a \\wsl.localhost\ path,
        # powershell.exe warns about UNC paths on stderr before it does anything.
        return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                              capture_output=True, text=True, timeout=timeout, cwd="/mnt/c")
    except (OSError, subprocess.TimeoutExpired):
        return None


def _firewall(port: int) -> str:
    done = _powershell(CHECK_SCRIPT.replace("__ID__", WSL_VM_CREATOR).replace("__PORT__", str(port)),
                       CHECK_SECONDS)
    word = done.stdout.strip().splitlines()[-1].strip() if done and done.stdout.strip() else ""
    return word if word in ("open", "blocked") else "unknown"


_cache: dict = {}
_lock = threading.Lock()


def phone_reach(port: int, fresh: bool = False) -> dict | None:
    """What stands between a phone and this port, or None when nothing the
    panel knows about does (not WSL).

    {"mode": "mirrored", "port": …, "firewall": "open"|"blocked"|"unknown", "command": …}
    or {"mode": "nat"} — in NAT mode the firewall is not the first problem.
    """
    with _lock:
        if not fresh and _cache.get("port") == port and time.monotonic() - _cache.get("at", 0) < CACHE_SECONDS:
            return _cache["answer"]
        mode = networking_mode()
        if mode is None:
            answer = None
        elif mode == "mirrored":
            answer = {"mode": mode, "port": port, "firewall": _firewall(port),
                      "command": rule_command(port)}
        else:
            answer = {"mode": mode}
        _cache.update(port=port, at=time.monotonic(), answer=answer)
        return answer


def allow_port(port: int) -> tuple[bool, str]:
    """Ask Windows to add the rule, behind a UAC prompt on the desktop.

    The prompt is the point: a panel that could open the firewall without one
    would be a panel any page with the key could do it through.
    """
    inner = base64.b64encode(rule_command(port).encode("utf-16-le")).decode("ascii")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{inner}'\n"
        "  'exit ' + $p.ExitCode\n"
        "} catch { 'declined' }\n"
    )
    done = _powershell(script, ELEVATE_SECONDS)
    said = done.stdout.strip() if done else ""
    if not done:
        return False, "Could not ask Windows — powershell.exe did not answer"
    if "declined" in said:
        return False, "Windows did not get permission — the prompt was declined"
    reach = phone_reach(port, fresh=True)
    if reach and reach.get("firewall") == "open":
        return True, f"Port {port} is open to your network"
    return False, "Windows ran the command but the port still looks closed — try the command by hand"

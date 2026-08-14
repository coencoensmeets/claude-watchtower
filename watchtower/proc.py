"""Reading /proc: what a process is, when it started, and what owns its terminal.

Everything here is a read of the kernel's own bookkeeping, and everything here
is defensive about it. A process can exit between one line and the next, so a
missing file or an unreadable link means "gone", never an error.
"""

from __future__ import annotations

import os
from pathlib import Path


def cpu_seconds(pid: int) -> float | None:
    """CPU this process has burned so far — utime plus stime, in seconds."""
    fields = read_stat(pid)
    if not fields or len(fields) < 15:
        return None
    try:
        return (int(fields[13]) + int(fields[14])) / CLK_TCK
    except ValueError:
        return None


def clock_ticks() -> float:
    try:
        return os.sysconf("SC_CLK_TCK") or 100.0
    except (ValueError, OSError):
        return 100.0


CLK_TCK = clock_ticks()


def read_stat(pid: int) -> list[str] | None:
    """Fields of /proc/<pid>/stat, with the comm field's spaces neutralised."""
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text()
    except (OSError, ValueError):
        return None
    close = raw.rfind(")")
    if close == -1:
        return None
    # Field 1 is pid, field 2 is comm (parenthesised); the rest are space-split.
    return [raw[: raw.find("(")].strip(), raw[raw.find("(") + 1 : close]] + raw[
        close + 2 :
    ].split()


def proc_starttime(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat — identifies a pid across reuse."""
    fields = read_stat(pid)
    if not fields or len(fields) < 22:
        return None
    return fields[21]


def parent_of(pid: int) -> int | None:
    fields = read_stat(pid)
    if not fields or len(fields) < 4:
        return None
    try:
        return int(fields[3])
    except ValueError:
        return None


def ancestors(pid: int, limit: int = 12) -> list[int]:
    """The pid's ancestor chain, nearest first, stopping before init."""
    chain: list[int] = []
    current = parent_of(pid)
    while current and current > 1 and len(chain) < limit:
        chain.append(current)
        current = parent_of(current)
    return chain


def proc_name(pid: int) -> str:
    fields = read_stat(pid)
    return fields[1] if fields and len(fields) > 1 else ""


def session_tty(pid: int) -> str | None:
    """The pty a session is attached to, as a device path, or None.

    Field 7 of /proc/<pid>/stat is the controlling terminal's device number:
    major 136 is a pty, and the minor is its /dev/pts entry. This is what tells
    two tabs of one terminal apart — they share a process, and therefore a
    window pid, but never a pty. Reading fd 0 is the fallback for a session
    that has moved its own standard input somewhere else.
    """
    fields = read_stat(pid)
    if fields and len(fields) >= 7:
        try:
            device = int(fields[6])
        except ValueError:
            device = 0
        if device > 0 and (device >> 8) & 0xFFF == 136:
            path = f"/dev/pts/{(device & 0xFF) | ((device >> 20) << 8)}"
            if os.path.exists(path):
                return path
    for fd in (0, 1, 2):
        try:
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if link.startswith("/dev/pts/"):
            return link
    return None

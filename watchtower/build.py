"""Turn web/ into dist/, which is what the panel serves.

The frontend is TypeScript and needs a build, and the project's promise is that
`python3 server.py` is the only command anyone types. So the panel builds its
own frontend on the way up, when — and only when — the sources are newer than
the output.

Nothing is installed to make that work. Node strips TypeScript types itself, so
the build needs a Node binary and no packages at all; see tools/build.mjs. What
it does need is *a* Node binary, which is looked for in more places than PATH so
that a machine you cannot install to can still put one in a virtualenv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = ROOT / "dist"
STAMP = DIST / ".build-stamp"
SCRIPT = ROOT / "tools" / "build.mjs"

# Type stripping arrived in Node 22.6 and is what the build is built on.
MIN_NODE = (22, 6)

HOW_TO_GET_NODE = """\
The panel builds its frontend with Node, which was not found.

  your package manager, e.g.   sudo apt install nodejs
  or, without touching the system:
      python3 -m venv .venv && .venv/bin/pip install nodejs-wheel-binaries

Or set WATCHTOWER_NODE to a Node binary you already have."""


def find_node() -> str | None:
    """A Node binary, from the most deliberate choice to the most ordinary.

    A virtualenv is looked in before PATH on purpose: someone who has installed
    Node into this project's venv has said which one they mean.
    """
    chosen = os.environ.get("WATCHTOWER_NODE")
    if chosen:
        return chosen if Path(chosen).is_file() else None

    for venv in (ROOT / ".venv", ROOT / "venv"):
        candidate = venv / ("Scripts/node.exe" if os.name == "nt" else "bin/node")
        if candidate.is_file():
            return str(candidate)

    # nodejs-wheel-binaries ships the binary inside the installed package rather
    # than on PATH, so it is asked where it put it.
    try:
        from nodejs_wheel.executable import node_path  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        try:
            found = node_path()
            if found and Path(found).is_file():
                return str(found)
        except Exception:                     # a repackage's own business
            pass

    return shutil.which("node")


def node_version(node: str) -> tuple[int, ...] | None:
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return tuple(int(part) for part in result.stdout.strip().lstrip("v").split(".")[:3])
    except ValueError:
        return None


def newest_source() -> float:
    """The most recently touched file under web/, in seconds."""
    latest = 0.0
    for path in WEB.rglob("*"):
        if path.is_file():
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
    return latest


def is_stale() -> bool:
    """Does dist/ need rebuilding?

    Answered here rather than by asking the build script, for two reasons.
    Starting Node costs more than this check does, and on a panel that is up to
    date the answer is always no. And the stamp is written here too: Python and
    Node do not round file timestamps identically, so a stamp recorded by one
    and compared by the other reads as stale the moment it is written.
    """
    if not (DIST / "index.html").is_file():
        return True
    try:
        stamped = float(STAMP.read_text().strip())
    except (OSError, ValueError):
        return True
    return newest_source() > stamped


def build(node: str | None = None) -> tuple[bool, str]:
    """Run the build once. Returns (ok, what to say about it)."""
    node = node or find_node()
    if not node:
        return False, HOW_TO_GET_NODE

    version = node_version(node)
    if version and version < MIN_NODE:
        have = ".".join(str(part) for part in version)
        want = ".".join(str(part) for part in MIN_NODE)
        return False, (f"Node {have} cannot strip TypeScript types; {want} or newer is needed.\n"
                       + HOW_TO_GET_NODE)

    # Read before the build, not after: a source edited while Node is running
    # must leave the output stale, rather than being stamped as already built.
    sources_at = newest_source()
    try:
        result = subprocess.run(
            # The type stripper is flagged experimental and says so on every run.
            # That is a note for whoever wrote the build, not for whoever is
            # starting a panel.
            [node, "--no-warnings", str(SCRIPT)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "The frontend build gave up after 120s"
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run the frontend build: {error}"

    said = (result.stderr or result.stdout).strip()
    if result.returncode != 0:
        return False, said or "The frontend build failed"
    try:
        STAMP.write_text(repr(sources_at))
    except OSError as error:
        return False, f"Built, but could not record the stamp: {error}"
    return True, said


def ensure_built(quiet: bool = False) -> bool:
    """Build if anything has changed. Returns whether dist/ is usable."""
    if not is_stale():
        return True
    if not quiet:
        print("building the frontend…", file=sys.stderr)
    ok, said = build()
    if said and not quiet:
        print(said, file=sys.stderr)
    if not ok and (DIST / "index.html").is_file():
        # An edit that does not compile should not take a working panel down
        # with it — the last good build is still on disk, and still serves.
        print("serving the previous build instead", file=sys.stderr)
        return True
    return ok

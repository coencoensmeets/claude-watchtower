"""Sessions the panel runs inside a container, so they cannot touch this machine.

A session the panel holds is one `claude --print --input-format stream-json`
process on a pipe of ours (see watchtower.owned). Boxing it is wrapping that same
argv in `docker run -i`: the pipe, the frames, the permission prompts and the
transcript all work exactly as before, because the process on the other end of
the pipe is the same program saying the same things. What changes is what it can
reach.

**Files.** It sees its checkout read-write — the repository root above the
session's folder, at the same path, so every path in the transcript means the
same on both sides — and its own transcript folder under ~/.claude/projects, so
the panel can go on reading the conversation. Your Claude settings, CLAUDE.md,
skills and git identity are there read-only. Nothing else of your home is: no
SSH keys, no cloud credentials, no other checkouts, no docker socket. What
Claude Code keeps in a home folder goes to the sandbox's own.

**The way back out through git.** A checkout is written by the box and read by
this machine, and git will run commands a repository's own config names —
`core.fsmonitor` on every `git status`, hooks on every commit. The panel runs
`git status` on the checkout every few seconds, so a box that could write
`.git/config` could run anything here within seconds. So `.git` is its own mount
(a mount point cannot be renamed or replaced from inside), and `.git/config`,
`.git/hooks` and `.git/modules` inside it are read-only; a linked worktree's
`.git` file is read-only too, so it cannot be pointed somewhere else. Commits
still work. And the panel's own git never runs a filesystem monitor anywhere,
and runs no hooks in a checkout a box has been in (watchtower.git). Which is
also why it must be a repository: in a plain folder the box could `git init`
one whose config is entirely its own.

What none of this can cover is code you run yourself afterwards. A changed
Makefile, `package.json` script, `.envrc`, husky hook or test runs with your
rights when you run it on this machine. Review the diff first, as you would a
pull request from somebody you do not know.

**Network.** It has the internet, because it needs the API. It does not have
this machine or the network this machine is on: before Claude starts, the
entrypoint (sandbox/entry.sh, as root with NET_ADMIN and nothing else) rejects
every private, link-local, carrier-grade and multicast range and this machine's
own public addresses, letting through only DNS to the configured resolvers.
Then it drops to your uid with every capability gone, the bounding set emptied
and no-new-privileges, so the rules cannot be undone from inside.

**Login.** It gets your current access token in CLAUDE_CODE_OAUTH_TOKEN and
never the refresh token, so it cannot rotate the login every other session
uses. A token lasts hours; a box whose token is about to run out is restarted
between turns with a fresh one (owned._renew_box). A long-lived token from
`claude setup-token`, saved to ~/.config/claude-watchtower/sandbox/token, is
used instead when it is there. ANTHROPIC_API_KEY and friends pass through.

**The kernel.** A container shares this machine's kernel. With gVisor installed
as a docker runtime (`runsc`) the box gets one of its own, and the panel uses it
— CLAUDE_WATCHTOWER_SANDBOX_RUNTIME picks another, `runc` for none.

**Resources.** A process limit and a memory ceiling, so a runaway build slows
the box rather than the machine.

Claude Code is not installed in the image. The host's own binary is mounted in
read-only, so a boxed session runs the same build as the rest and the image never
needs rebuilding for an update. That needs the native build (a single ELF file);
an npm install is refused with a message saying so.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

from watchtower.config import HOME, ROOT, SANDBOX_ROOTS_FILE
from watchtower.git.read import git_root
from watchtower.past import project_dir


SANDBOX_DIR = HOME / ".config" / "claude-watchtower" / "sandbox"
# Which sessions are boxed, by id. A file of its own rather than a field in
# owned.json: half a dozen places rewrite an owned entry as `{mode, here}`, and a
# safety setting that silently fell off whenever one of them did would be worse
# than none.
SANDBOX_FILE = HOME / ".config" / "claude-watchtower" / "sandbox.json"
SANDBOX_HOME = SANDBOX_DIR / "home"
# Local addresses a boxed session may reach after all, per session: a controller
# it has to ssh to, a device on the bench. Everything else on the local network
# stays shut out, and this computer always does.
SANDBOX_LAN_FILE = HOME / ".config" / "claude-watchtower" / "sandbox-lan.json"
MAX_LAN = 20
# A long-lived token from `claude setup-token`, if you saved one.
TOKEN_FILE = SANDBOX_DIR / "token"
# What the container last said on stderr, per session. The pipe's stderr is
# otherwise thrown away, and a box that would not start says why only there.
SANDBOX_LOGS = SANDBOX_DIR / "logs"
# Where the sandbox's home appears inside the container. Fixed, so nothing about
# the host's layout leaks in through $HOME.
BOX_HOME = "/home/claude"
ENTRY = ROOT / "sandbox" / "entry.sh"
BOX_ENTRY = "/usr/local/sbin/watchtower-entry"
# Bump when the Dockerfile changes, so an install picks up the new one.
SANDBOX_IMAGE_VERSION = "4"
DOCKERFILE = ROOT / "sandbox" / "Dockerfile"
LABEL = "claude-watchtower.sandbox"
MAX_SANDBOXED = 200
PIDS_LIMIT = "4096"
# Restarted between turns once its token has less than this left.
TOKEN_MARGIN = 20 * 60
# What every box shares, set on the Sandbox tab from this computer only:
# whether new sessions start boxed, and packages layered onto the image.
SETTINGS_FILE = SANDBOX_DIR / "settings.json"
# Named values a session can be given as environment variables, and which
# session has been given which. Outside the sandbox's home, so no box can read
# the file — only the values its own session was granted, in its environment.
SECRETS_FILE = SANDBOX_DIR / "secrets.json"
# The one SSH key a box can use: generated here, in the box's own home.
SSH_DIR = SANDBOX_HOME / ".ssh"
SSH_KEY = SSH_DIR / "id_ed25519"
PACKAGE_KINDS = ("apt", "pip", "npm")
MAX_PACKAGES = 40

_LOCK = threading.Lock()
# The image build, which takes minutes the first time and so is never waited on.
BUILD: dict = {"running": False, "error": None, "at": None}
# How long a reading of "is docker there, is the image built" is trusted. The
# feed asks every second; docker does not change that fast.
REPORT_SECONDS = 20.0
_REPORT: tuple[float, dict | None] = (0.0, None)


def base_image() -> str:
    return (os.environ.get("CLAUDE_WATCHTOWER_SANDBOX_IMAGE")
            or f"claude-watchtower-sandbox:{SANDBOX_IMAGE_VERSION}")


def image_name() -> str:
    """The image boxes run: the base, or the base with your packages on top.

    The packages are part of the tag (a hash of the list), so changing them is
    a new image, built once, while boxes already running keep the one they
    started on."""
    extra = packages_dockerfile()
    if not extra:
        return base_image()
    digest = hashlib.sha256((base_image() + extra).encode()).hexdigest()[:10]
    return f"claude-watchtower-sandbox-extra:{SANDBOX_IMAGE_VERSION}-{digest}"


def custom_image() -> bool:
    return bool(os.environ.get("CLAUDE_WATCHTOWER_SANDBOX_IMAGE"))


# --- which sessions are boxed

def load_sandboxed() -> set[str]:
    try:
        data = json.loads(SANDBOX_FILE.read_text())
    except (OSError, ValueError):
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def _save(ids: set[str]) -> None:
    SANDBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    SANDBOX_FILE.write_text(json.dumps(sorted(ids)[-MAX_SANDBOXED:], indent=2))


def is_sandboxed(session_id: str) -> bool:
    return session_id in load_sandboxed()


def set_sandboxed(session_id: str, on: bool) -> None:
    with _LOCK:
        ids = load_sandboxed()
        ids.add(session_id) if on else ids.discard(session_id)
        _save(ids)


def sandbox_rekey(old: str, new: str) -> None:
    """Follow a session that changed its id (a `/clear`): the box goes with it."""
    with _LOCK:
        ids = load_sandboxed()
        if old in ids:
            ids.discard(old)
            ids.add(new)
            _save(ids)


# --- local addresses a box may reach

def load_lan() -> dict[str, list[str]]:
    try:
        data = json.loads(SANDBOX_LAN_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}


def lan_for(session_id: str) -> list[str]:
    return load_lan().get(session_id, [])


def _save_lan(lan: dict[str, list[str]]) -> None:
    SANDBOX_LAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SANDBOX_LAN_FILE.write_text(json.dumps({k: v for k, v in lan.items() if v}, indent=2))


HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$")


def parse_allow(raw: str) -> tuple[dict | None, str]:
    """One entry as typed — `192.168.2.10`, `10.0.0.0/24`, `controller.local`,
    any of them with `:22` — or why it is not one.

    Only local addresses: the internet is open already, and an entry that let a
    box at the whole world, or at this computer's loopback, is not what this is
    for. A hostname is checked now and resolved again each time a box starts,
    on this computer, where `.local` names work.
    """
    text = raw.strip()
    if not text:
        return None, "Type an address"
    port = None
    host = text
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        if rest:
            if not rest.startswith(":") or not rest[1:].isdigit():
                return None, f"{text}: a port goes after the brackets, as [fd00::1]:22"
            port = int(rest[1:])
    elif text.count(":") == 1:
        host, _, tail = text.partition(":")
        if not tail.isdigit():
            return None, f"{text}: the part after the colon should be a port number"
        port = int(tail)
    if port is not None and not 1 <= port <= 65535:
        return None, f"{text}: {port} is not a port"
    try:
        net = ipaddress.ip_network(host, strict=False)
    except ValueError:
        net = None
    if net is None:
        if not HOSTNAME.match(host):
            return None, f"{text} is not an address, a range or a host name"
        try:
            addrs = {str(info[4][0]).split("%", 1)[0] for info in socket.getaddrinfo(host, None)}
        except OSError:
            return None, f"{host} does not resolve from this computer"
        local = [a for a in addrs if not ipaddress.ip_address(a).is_loopback
                 and not ipaddress.ip_address(a).is_global]
        if not local:
            return None, f"{host} is not on the local network ({', '.join(sorted(addrs))})"
        return {"text": text, "host": host, "port": port}, ""
    if net.prefixlen < (8 if net.version == 4 else 32):
        return None, f"{text} is too wide. Allow the hosts or the subnet you need"
    if net.is_loopback or net.network_address.is_unspecified:
        return None, f"{text} is this computer's own loopback, which a box never gets"
    if net.network_address.is_global and net.broadcast_address.is_global:
        return None, f"{text} is on the internet, which a box can reach already"
    return {"text": text, "host": str(net), "port": port}, ""


def set_lan(session_id: str, entries: list[str]) -> tuple[bool, str, list[str]]:
    """Replace a session's allowed local addresses. (ok, why not, as saved)."""
    if len(entries) > MAX_LAN:
        return False, f"At most {MAX_LAN} addresses", []
    saved: list[str] = []
    for raw in entries:
        parsed, why = parse_allow(str(raw))
        if not parsed:
            return False, why, []
        if parsed["text"] not in saved:
            saved.append(parsed["text"])
    with _LOCK:
        lan = load_lan()
        lan[session_id] = saved
        _save_lan(lan)
    return True, "", saved


def lan_rekey(old: str, new: str) -> None:
    with _LOCK:
        lan = load_lan()
        if old in lan:
            lan[new] = lan.pop(old)
            _save_lan(lan)


def resolve_allow(entries: list[str]) -> tuple[list[str], list[str]]:
    """Entries as the firewall takes them — `net` or `net,port` — and any that
    did not resolve this time, so the box can say which."""
    out: list[str] = []
    missed: list[str] = []
    for raw in entries:
        parsed, _ = parse_allow(raw)
        if not parsed:
            missed.append(raw)
            continue
        # A comma, not a colon, before the port: a colon is part of an IPv6 address.
        port = f",{parsed['port']}" if parsed["port"] else ""
        try:
            out.append(str(ipaddress.ip_network(parsed["host"], strict=False)) + port)
            continue
        except ValueError:
            pass
        # A host name, resolved now rather than when it was saved: a
        # controller's address can change between one start and the next.
        try:
            addrs = {str(info[4][0]) for info in socket.getaddrinfo(parsed["host"], None)}
        except OSError:
            missed.append(raw)
            continue
        for addr in sorted(addrs):
            try:
                ip = ipaddress.ip_address(addr.split("%", 1)[0])
            except ValueError:
                continue
            if not ip.is_loopback and not ip.is_global:
                out.append(f"{ip}/{ip.max_prefixlen}{port}")
    return out, missed


def _guard_root(root: str) -> None:
    """Remember for good that a box has written here. See config.SANDBOX_ROOTS_FILE."""
    try:
        roots = json.loads(SANDBOX_ROOTS_FILE.read_text())
        roots = roots if isinstance(roots, list) else []
    except (OSError, ValueError):
        roots = []
    if root not in roots:
        roots.append(root)
        SANDBOX_ROOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SANDBOX_ROOTS_FILE.write_text(json.dumps(roots, indent=2))


# --- what it needs from the machine

# --- what every box shares: settings, packages, the SSH key, secrets

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_private(path: Path, data: dict) -> None:
    """Written readable by you alone: it may hold secrets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(data, indent=2))
    os.replace(tmp, path)


def load_settings() -> dict:
    data = _read_json(SETTINGS_FILE)
    raw = data.get("packages") if isinstance(data.get("packages"), dict) else {}
    packages = {kind: [str(x) for x in raw.get(kind, []) if isinstance(x, str)] for kind in PACKAGE_KINDS}
    return {"newSessions": bool(data.get("newSessions")), "packages": packages}


def set_new_sessions(on: bool) -> None:
    with _LOCK:
        data = load_settings()
        data["newSessions"] = bool(on)
        _write_private(SETTINGS_FILE, data)


# A package name as each tool takes it, versions included. Each is quoted into
# the build anyway; this is so a typo is caught here and not three minutes into
# a failed build.
PACKAGE_NAME = {
    "apt": re.compile(r"^[a-z0-9][a-z0-9+.\-]*(=[A-Za-z0-9.+:~\-]+)?$"),
    "pip": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(\[[A-Za-z0-9._,\-]+\])?"
                      r"([<>=!~]=?[A-Za-z0-9.*+!\-]+(,[<>=!~]=?[A-Za-z0-9.*+!\-]+)*)?$"),
    "npm": re.compile(r"^(@[a-z0-9][a-z0-9._\-]*/)?[a-z0-9][a-z0-9._\-]*(@[A-Za-z0-9.^~<>=*\-]+)?$"),
}


def set_packages(packages: dict) -> tuple[bool, str, dict]:
    """Replace the packages layered onto the image. (ok, why not, as saved)."""
    clean: dict[str, list[str]] = {}
    for kind in PACKAGE_KINDS:
        names = packages.get(kind) or []
        if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
            return False, f"Send the {kind} packages as a list of names", {}
        names = list(dict.fromkeys(x.strip() for x in names if x.strip()))
        if len(names) > MAX_PACKAGES:
            return False, f"At most {MAX_PACKAGES} {kind} packages", {}
        for name in names:
            if not PACKAGE_NAME[kind].match(name):
                return False, f"{name} is not a {kind} package name", {}
        clean[kind] = names
    with _LOCK:
        data = load_settings()
        data["packages"] = clean
        _write_private(SETTINGS_FILE, data)
    return True, "", clean


def packages_dockerfile() -> str:
    """The layer your packages add on top of the base image, or '' for none."""
    packages = load_settings()["packages"]
    if not any(packages.values()):
        return ""
    q = lambda names: " ".join(shlex.quote(x) for x in names)  # noqa: E731
    lines = [f"FROM {base_image()}", "USER root"]
    if packages["apt"]:
        lines.append("RUN apt-get update && apt-get install -y --no-install-recommends "
                     f"{q(packages['apt'])} && rm -rf /var/lib/apt/lists/*")
    if packages["pip"]:
        lines.append(f"RUN pip install --no-cache-dir --break-system-packages {q(packages['pip'])}")
    if packages["npm"]:
        lines.append(f"RUN npm install -g {q(packages['npm'])} && npm cache clean --force")
    return "\n".join(lines) + "\n"


def ssh_public_key() -> str:
    try:
        return (SSH_DIR / "id_ed25519.pub").read_text().strip()
    except OSError:
        return ""


def create_ssh_key() -> tuple[bool, str]:
    """A key pair for the boxes, in their home. Never one of yours.

    Also an ssh config that takes a new host's key on first sight
    (accept-new): a box cannot answer ssh's yes/no question, and a changed key
    is still refused."""
    if SSH_KEY.exists():
        return False, "There is a key already. Remove it first to make a new one"
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        return False, "ssh-keygen is not installed on this computer"
    SSH_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SSH_DIR, 0o700)
    try:
        done = subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "claude-watchtower-sandbox",
                               "-f", str(SSH_KEY)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not make a key: {exc}"
    if done.returncode != 0:
        return False, (done.stderr.strip() or "ssh-keygen failed")
    config = SSH_DIR / "config"
    if not config.exists():
        config.write_text("Host *\n    StrictHostKeyChecking accept-new\n    IdentityFile ~/.ssh/id_ed25519\n")
        os.chmod(config, 0o600)
    return True, "Made a key for the sandbox"


def delete_ssh_key() -> tuple[bool, str]:
    found = False
    for path in (SSH_KEY, SSH_DIR / "id_ed25519.pub"):
        try:
            path.unlink()
            found = True
        except FileNotFoundError:
            pass
    return (True, "Removed the sandbox's key") if found else (False, "There is no key to remove")


SECRET_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
# Names a secret may not take: what the box itself runs on, and Claude Code's
# own configuration, which a value set here would quietly change.
SECRET_RESERVED = {"HOME", "PATH", "USER", "SHELL", "LANG", "TERM", "PWD", "NODE_OPTIONS",
                   "PYTHONPATH", "PYTHONSTARTUP", "HOSTNAME"}
SECRET_RESERVED_PREFIXES = ("BOX_", "CLAUDE_", "ANTHROPIC_", "LD_", "GIT_", "DOCKER_")
MAX_SECRETS = 50


def _load_secrets() -> dict:
    data = _read_json(SECRETS_FILE)
    values = {str(k): str(v) for k, v in (data.get("values") or {}).items()}
    sessions = {str(k): [str(x) for x in v] for k, v in (data.get("sessions") or {}).items()
                if isinstance(v, list)}
    return {"values": values, "sessions": sessions}


def secret_names() -> list[str]:
    """The names only. Values never leave this computer's files except into a box."""
    return sorted(_load_secrets()["values"])


def secrets_for(session_id: str) -> list[str]:
    data = _load_secrets()
    return [n for n in data["sessions"].get(session_id, []) if n in data["values"]]


def set_secret(name: str, value: str) -> tuple[bool, str]:
    name = name.strip()
    if not SECRET_NAME.match(name):
        return False, "A name is capitals, digits and underscores, like CONTROLLER_PASSWORD"
    if name in SECRET_RESERVED or name.startswith(SECRET_RESERVED_PREFIXES):
        return False, f"{name} is a name the box itself uses. Pick another"
    if not value or len(value) > 4096 or "\0" in value:
        return False, "Give it a value, up to 4096 characters"
    with _LOCK:
        data = _load_secrets()
        if name not in data["values"] and len(data["values"]) >= MAX_SECRETS:
            return False, f"At most {MAX_SECRETS} secrets"
        data["values"][name] = value
        _write_private(SECRETS_FILE, data)
    return True, f"Saved {name}"


def delete_secret(name: str) -> tuple[bool, str]:
    with _LOCK:
        data = _load_secrets()
        if name not in data["values"]:
            return False, f"There is no {name}"
        data["values"].pop(name)
        for granted in data["sessions"].values():
            if name in granted:
                granted.remove(name)
        _write_private(SECRETS_FILE, data)
    return True, f"Removed {name} from every session"


def grant_secrets(session_id: str, names: list[str]) -> tuple[bool, str, list[str]]:
    """Which secrets this session's box gets. Replaces the whole list."""
    with _LOCK:
        data = _load_secrets()
        unknown = [n for n in names if n not in data["values"]]
        if unknown:
            return False, f"There is no {unknown[0]}", []
        data["sessions"][session_id] = list(dict.fromkeys(names))
        if not data["sessions"][session_id]:
            data["sessions"].pop(session_id)
        _write_private(SECRETS_FILE, data)
        return True, "", list(dict.fromkeys(names))


def secrets_rekey(old: str, new: str) -> None:
    with _LOCK:
        data = _load_secrets()
        if old in data["sessions"]:
            data["sessions"][new] = data["sessions"].pop(old)
            _write_private(SECRETS_FILE, data)


def box_brief(root: str, lan: list[str], secrets: list[str], has_key: bool) -> str:
    """What the session is told about the box it is in, as part of its system prompt.

    Without it a session finds out by failing: a push that cannot find ssh, a
    connection refused by a firewall it does not know about — and then tries
    to work around what it took for a fault."""
    lines = [
        "You are running inside a sandbox (a Docker container) that the user set up with Claude "
        "Watchtower, so that you cannot affect the rest of their computer.",
        f"- Files: you can change {root} and nothing else of the user's computer is visible. "
        ".git/config and .git/hooks are read-only, so `git config` and new hooks fail there; "
        "commits work.",
        "- Git remotes: the user's SSH keys are not here. Commit; the user pulls and pushes from "
        "their own computer.",
        "- Network: the internet is open. The user's computer and local network are blocked"
        + (f", except: {', '.join(lan)}." if lan else "."),
    ]
    if secrets:
        lines.append(f"- Secrets the user gave this session, as environment variables: {', '.join(secrets)}. "
                     "Use them by name (e.g. \"$NAME\"); never print, echo or write their values.")
    if has_key:
        lines.append("- An SSH key for local devices the user allowed is at ~/.ssh/id_ed25519.")
    lines.append("If something you need is blocked, say what to allow on the Sandbox tab rather than "
                 "trying to get around it.")
    return "\n".join(lines)


def docker_binary() -> str | None:
    return shutil.which("docker")


def _docker(*args: str, timeout: float = 15.0, stdin: str | None = None) -> subprocess.CompletedProcess | None:
    docker = docker_binary()
    if not docker:
        return None
    try:
        return subprocess.run([docker, *args], capture_output=True, text=True, timeout=timeout,
                              input=stdin)
    except (OSError, subprocess.TimeoutExpired):
        return None


def claude_binary() -> tuple[str | None, str]:
    """The host's claude as one file that can be mounted in, or why it cannot be."""
    found = shutil.which("claude")
    if not found:
        return None, "Cannot find the claude command on PATH"
    real = os.path.realpath(found)
    try:
        with open(real, "rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        return None, f"Cannot read {real}: {exc}"
    if magic != b"\x7fELF":
        return None, ("This claude is an npm install, which cannot be mounted into a container on "
                      "its own. The native build can — `claude install` switches to it")
    return real, ""


def _docker_state() -> tuple[bool, str, list[str]]:
    """(reachable, why not, runtimes docker knows)."""
    if not docker_binary():
        return False, "Docker is not installed", []
    done = _docker("info", "--format", "{{json .Runtimes}}")
    if done is None or done.returncode != 0:
        why = ((done.stderr if done else "") or "").strip().splitlines()
        return False, ("Docker is installed but the panel cannot reach it"
                       + (f": {why[-1]}" if why else "")), []
    try:
        runtimes = list(json.loads(done.stdout.strip() or "{}"))
    except ValueError:
        runtimes = []
    return True, "", runtimes


def pick_runtime(runtimes: list[str]) -> str:
    """gVisor when docker has it, unless told otherwise. '' means docker's default."""
    asked = os.environ.get("CLAUDE_WATCHTOWER_SANDBOX_RUNTIME")
    if asked is not None:
        return "" if asked in ("", "runc", "default") else asked
    return "runsc" if "runsc" in runtimes else ""


def image_ready() -> bool:
    done = _docker("image", "inspect", "--format", "{{.Id}}", image_name())
    return bool(done and done.returncode == 0)


def _host_token() -> tuple[str, float | None]:
    """The access token the box gets, and when it runs out (None: does not)."""
    try:
        saved = TOKEN_FILE.read_text().strip()
    except OSError:
        saved = ""
    if saved:
        return saved, None
    try:
        oauth = json.loads((HOME / ".claude" / ".credentials.json").read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return "", None
    token = str(oauth.get("accessToken") or "")
    expires = oauth.get("expiresAt")
    return token, (float(expires) / 1000 if isinstance(expires, (int, float)) else None)


def login_kind() -> str:
    """How the box will sign in: 'token' (long-lived), 'session', 'apikey' or ''."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "apikey"
    token, expires = _host_token()
    if not token:
        return ""
    return "token" if expires is None else "session"


def sandbox_report(fresh: bool = False) -> dict:
    """Whether boxing can work here, for the Sandbox tab to say so up front."""
    global _REPORT
    now = time.time()
    at, cached = _REPORT
    if cached is None or fresh or now - at >= REPORT_SECONDS:
        ok, why, runtimes = _docker_state()
        if ok:
            binary, why = claude_binary()
            ok = bool(binary)
        if ok and not login_kind():
            ok, why = False, "There is no login to give it. Sign in to Claude Code first"
        runtime = pick_runtime(runtimes) if ok else ""
        cached = {
            "available": ok,
            "why": why,
            "image": image_name(),
            "imageReady": ok and image_ready(),
            # Which isolation it gets: its own kernel, or this machine's.
            "runtime": runtime or "runc",
            "gvisor": runtime == "runsc",
            "login": login_kind(),
        }
        _REPORT = (now, cached)
    settings = load_settings()
    return {**cached, "image": image_name(), "building": BUILD["running"], "buildError": BUILD["error"],
            # Read fresh each time (small files), so a change shows at once.
            "newSessions": settings["newSessions"], "packages": settings["packages"],
            "sshKey": ssh_public_key(), "secrets": secret_names()}


def ensure_image(then=None) -> tuple[bool, str]:
    """Have the image, or start building it. True when it is there already.

    The base image first, from sandbox/Dockerfile, then your packages on top of
    it when there are any. The build runs on a thread of its own and `then` is
    called when it ends well, so a request that turned the box on returns at
    once instead of holding the browser for the minutes a first build takes.
    """
    if image_ready():
        return True, "ready"
    base_missing = not _image_exists(base_image())
    if base_missing and custom_image():
        return False, f"The image {base_image()} is not on this machine — pull or build it first"
    with _LOCK:
        if BUILD["running"]:
            return False, "The sandbox image is still being built"
        BUILD.update(running=True, error=None, at=time.time())

    def build() -> None:
        global _REPORT
        done = None
        ok = True
        if base_missing:
            done = _docker("build", "-t", base_image(), "-f", str(DOCKERFILE), str(DOCKERFILE.parent),
                           timeout=1800.0)
            ok = bool(done and done.returncode == 0)
        extra = packages_dockerfile()
        target = image_name()
        if ok and extra and not _image_exists(target):
            # No build context: the layer only installs packages.
            done = _docker("build", "-t", target, "-", timeout=1800.0, stdin=extra)
            ok = bool(done and done.returncode == 0)
        tail = ((done.stderr or done.stdout) if done else ("" if ok else "docker did not run")).strip().splitlines()
        with _LOCK:
            BUILD.update(running=False, error=None if ok else (tail[-1] if tail else "the build failed"))
        _REPORT = (0.0, None)
        print(f"note: sandbox image {target} {'built' if ok else 'failed to build'}", flush=True)
        if ok and then:
            then()

    threading.Thread(target=build, daemon=True).start()
    return False, ("Adding your packages to the sandbox image" if not base_missing
                   else "Building the sandbox image — the first time takes a few minutes")


def _image_exists(name: str) -> bool:
    done = _docker("image", "inspect", "--format", "{{.Id}}", name)
    return bool(done and done.returncode == 0)


# --- the box itself

def can_box(cwd: str) -> tuple[bool, str]:
    """Whether this folder can be boxed at all, before anything is started."""
    if not cwd or not Path(cwd).is_dir():
        return False, "Its folder is gone"
    root = git_root(cwd)
    if not root:
        return False, ("The sandbox needs the folder to be a git repository: that is what keeps its "
                       "git settings out of the box's reach, and what lets you review what it "
                       "changed. Run `git init` there first")
    if os.path.realpath(root) in (os.path.realpath(HOME), "/"):
        return False, "That repository is your whole home folder, which is what the box keeps out"
    return True, ""


def _gitdir_of(root: str) -> tuple[str, str | None]:
    """(this checkout's git dir, the shared one when it is a linked worktree)."""
    marker = Path(root) / ".git"
    if marker.is_dir():
        return str(marker), None
    try:
        line = marker.read_text().strip()
    except OSError:
        return str(marker), None
    gitdir = Path(line.split(":", 1)[1].strip()) if line.startswith("gitdir:") else marker
    if not gitdir.is_absolute():
        gitdir = (Path(root) / gitdir).resolve()
    try:
        common = (gitdir / "commondir").read_text().strip()
        shared = (gitdir / common).resolve() if common else gitdir
    except OSError:
        shared = gitdir
    return str(gitdir), str(shared)


def _locked_git(gitdir: str) -> list[str]:
    """The parts of a git dir that make git run things, mounted read-only."""
    out = []
    hooks = Path(gitdir) / "hooks"
    if not hooks.exists():
        # Mounted read-only so a hook cannot be planted, which needs it to exist.
        hooks.mkdir(exist_ok=True)
    for name in ("config", "config.worktree", "hooks", "modules", "commondir"):
        part = Path(gitdir) / name
        if part.exists():
            out += ["-v", f"{part}:{part}:ro"]
    return out


def mounts(cwd: str) -> list[str]:
    """The `-v` arguments for a session in this folder. See the module note."""
    root = git_root(cwd) or cwd
    out = ["-v", f"{root}:{root}"]
    gitdir, shared = _gitdir_of(root)
    marker = Path(root) / ".git"
    if shared is None:
        # Its own mount, so it cannot be renamed away and replaced with one
        # whose config the box wrote.
        out += ["-v", f"{gitdir}:{gitdir}"]
        out += _locked_git(gitdir)
    else:
        # A linked worktree: the pointer file stays pointing where it does, and
        # the shared .git it points into comes along with the same locks.
        out += ["-v", f"{marker}:{marker}:ro"]
        for directory in dict.fromkeys((shared, gitdir)):
            if not directory.startswith(root + os.sep):
                out += ["-v", f"{directory}:{directory}"]
            out += _locked_git(directory)
    transcripts = project_dir(cwd)
    transcripts.mkdir(parents=True, exist_ok=True)
    out += ["-v", f"{transcripts}:{BOX_HOME}/.claude/projects/{transcripts.name}"]
    claude_home = HOME / ".claude"
    for name in ("settings.json", "CLAUDE.md", "skills", "agents", "commands"):
        source = claude_home / name
        if source.exists():
            out += ["-v", f"{source}:{BOX_HOME}/.claude/{name}:ro"]
    for source, inside in ((HOME / ".gitconfig", ".gitconfig"), (HOME / ".config" / "git", ".config/git")):
        if source.exists():
            out += ["-v", f"{source}:{BOX_HOME}/{inside}:ro"]
    return out


def own_addresses() -> list[str]:
    """Every address this computer has, private ones included.

    The box rejects all of them before it looks at what it is allowed, so
    allowing the subnet this computer is on lets a box reach the controller
    beside it and still not this computer.
    """
    found: set[str] = set()
    try:
        done = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        found.update(done.stdout.split())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(str(info[4][0]))
    except OSError:
        pass
    out = []
    for raw in sorted(found):
        try:
            addr = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            continue
        if not addr.is_loopback:
            out.append(str(addr))
    return out


def memory_limit() -> str:
    """Half the machine, unless told otherwise. '' for none."""
    asked = os.environ.get("CLAUDE_WATCHTOWER_SANDBOX_MEMORY")
    if asked is not None:
        return asked
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return f"{int(line.split()[1]) // 2}k"
    except (OSError, ValueError, IndexError):
        pass
    return ""


# Passed through when set, because they are how a login that is not the
# credentials file reaches Claude Code: an API key, a proxy, a cloud provider.
PASS_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_REGION", "CLOUD_ML_REGION")


def wrap(argv: list[str], cwd: str,
         session_id: str | None) -> tuple[list[str] | None, dict, str]:
    """`argv` (a claude command line) run inside the box.

    Returns (argv, facts, why not). `facts` carries what the caller needs to
    keep: the container's name, the environment the docker client must be run
    with (the token travels in it, never on the command line), and when the
    token runs out.
    """
    ok, why = can_box(cwd)
    if not ok:
        return None, {}, why
    ok, why, runtimes = _docker_state()
    if not ok:
        return None, {}, why
    binary, why = claude_binary()
    if not binary:
        return None, {}, why
    if not image_ready():
        # Missing after an update bumped SANDBOX_IMAGE_VERSION, usually. Start
        # the build here rather than leave a boxed session refusing to start
        # until somebody happens to flip its switch off and on again.
        ready, said = ensure_image()
        if not ready:
            return None, {}, f"{said}. Send it again once that is done"
    env: dict[str, str] = {}
    passed: list[str] = []
    for key in sorted(os.environ):
        if key.startswith(PASS_ENV_PREFIXES):
            passed += ["-e", key]
    expires: float | None = None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        token, expires = _host_token()
        if not token:
            return None, {}, "There is no login to give it. Sign in to Claude Code first"
        if expires is not None and expires - time.time() < 60:
            return None, {}, ("Your Claude login has run out. Use Claude Code once anywhere on this "
                              "machine to renew it, or save a long-lived token from "
                              f"`claude setup-token` to {TOKEN_FILE}")
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        passed += ["-e", "CLAUDE_CODE_OAUTH_TOKEN"]
    SANDBOX_HOME.mkdir(parents=True, exist_ok=True)
    (SANDBOX_HOME / ".claude").mkdir(exist_ok=True)
    root = git_root(cwd) or cwd
    _guard_root(root)
    name = f"watchtower-{(session_id or 'new')[:8]}-{uuid.uuid4().hex[:6]}"
    runtime = pick_runtime(runtimes)
    memory = memory_limit()
    allowed, missed = resolve_allow(lan_for(session_id or ""))
    # The secrets this session was given, by name. Values ride in the docker
    # client's environment like the token, never on its command line.
    granted = secrets_for(session_id or "")
    values = _load_secrets()["values"]
    for secret in granted:
        env[secret] = values[secret]
        passed += ["-e", secret]
    brief = box_brief(root, lan_for(session_id or ""), granted, SSH_KEY.exists())
    box = [
        docker_binary() or "docker", "run", "--rm", "-i", "--init",
        "--name", name, "--label", f"{LABEL}={session_id or ''}",
        *(["--runtime", runtime] if runtime else []),
        # Root at the start only to set the firewall; entry.sh drops it all.
        "--user", "0:0",
        "--cap-drop", "ALL",
        "--cap-add", "NET_ADMIN", "--cap-add", "SETUID", "--cap-add", "SETGID",
        "--cap-add", "SETPCAP",
        "--security-opt", "no-new-privileges",
        "--pids-limit", PIDS_LIMIT,
        *(["--memory", memory] if memory else []),
        "-e", f"HOME={BOX_HOME}",
        "-e", f"BOX_UID={os.getuid()}", "-e", f"BOX_GID={os.getgid()}",
        "-e", f"BOX_SELF={' '.join(own_addresses())}",
        "-e", f"BOX_ALLOW={' '.join(allowed)}",
        *passed,
        "-v", f"{SANDBOX_HOME}:{BOX_HOME}",
        "-v", f"{binary}:/usr/local/bin/claude:ro",
        "-v", f"{ENTRY}:{BOX_ENTRY}:ro",
        *mounts(cwd),
        "-w", cwd,
        "--entrypoint", BOX_ENTRY,
        image_name(),
        "claude", *argv[1:],
        "--append-system-prompt", brief,
    ]
    return box, {"container": name, "env": env, "expires": expires, "runtime": runtime or "runc",
                 "lan": allowed, "lanMissed": missed, "secrets": granted}, ""


def token_stale(expires: float | None) -> bool:
    """Has a box's token got too little time left to start another turn on."""
    return expires is not None and expires - time.time() < TOKEN_MARGIN


def log_file(session_id: str | None) -> Path:
    SANDBOX_LOGS.mkdir(parents=True, exist_ok=True)
    return SANDBOX_LOGS / f"{session_id or 'new'}.log"


def last_error(session_id: str | None) -> str:
    """The last thing a box said on stderr, for a box that would not start."""
    try:
        lines = [x for x in log_file(session_id).read_text().splitlines() if x.strip()]
    except OSError:
        return ""
    return lines[-1][:300] if lines else ""


def stop_container(name: str) -> None:
    if name:
        _docker("rm", "-f", name, timeout=20.0)


def reap_stale() -> None:
    """Remove boxes a previous panel left behind: it held their pipes, so nothing can use them."""
    if not docker_binary() or not load_sandboxed():
        return
    done = _docker("ps", "-aq", "--filter", f"label={LABEL}")
    ids = (done.stdout.split() if done and done.returncode == 0 else [])
    if ids:
        _docker("rm", "-f", *ids, timeout=30.0)

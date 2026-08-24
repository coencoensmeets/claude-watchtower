"""What a session has asked of the models, and what that costs at list price.

The figures exist in one place only — every reply Claude Code writes down
carries the usage the API reported for it — so this reads them back out of the
transcript. A transcript only grows, so the scan remembers where it stopped and
picks up from there rather than re-totalling megabytes on every poll.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from watchtower.transcript import transcript_paths


# List price per million tokens, input and output, as the API charges them. The
# multipliers below turn the input price into the other three rates: a cache
# write costs more than fresh input, a cache read a tenth of it.
#
# Keys are matched longest-first as a prefix of the model the transcript names,
# so a dated or suffixed id (`claude-opus-5[1m]`) prices as its family. A model
# with no entry is still counted — its tokens are real — but contributes no cost
# and is named as unpriced, which is honest and says what to fix.
MODEL_PRICES = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-mythos-preview": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-haiku": (0.8, 4.0),
}


# Fast mode is the same model at a premium, and the transcript says which one ran.
FAST_PRICES = {"claude-opus-5": (10.0, 50.0), "claude-opus-4-8": (10.0, 50.0)}


CACHE_WRITE_5M = 1.25


CACHE_WRITE_1H = 2.0


CACHE_READ = 0.1


WEB_SEARCH_PER_1K = 10.0


# What a full context is, for the reading of how much of one this session is
# carrying. Haiku's is the small one; everything current is a million.
SMALL_WINDOW = 200_000


BIG_WINDOW = 1_000_000


def price_of(model: str, fast: bool) -> tuple[float, float] | None:
    if fast:
        for name, rate in FAST_PRICES.items():
            if model.startswith(name):
                return rate
    for name in sorted(MODEL_PRICES, key=len, reverse=True):
        if model.startswith(name):
            return MODEL_PRICES[name]
    return None


def context_window(model: str) -> int:
    if "haiku" in model or "claude-3" in model:
        return SMALL_WINDOW
    return BIG_WINDOW


def blank_counters() -> dict:
    return {"requests": 0, "input": 0, "output": 0, "thinking": 0,
            "cacheWrite5m": 0, "cacheWrite1h": 0, "cacheRead": 0, "webSearch": 0}


def add_usage(bucket: dict, usage: dict) -> None:
    """Fold one request's usage into a model's running totals.

    Only the top-level figures are read. A response that took several passes
    also carries an `iterations` list holding the same numbers broken up, so
    counting both would bill every such turn twice.
    """
    bucket["requests"] += 1
    bucket["input"] += int(usage.get("input_tokens") or 0)
    bucket["output"] += int(usage.get("output_tokens") or 0)
    details = usage.get("output_tokens_details")
    if isinstance(details, dict):
        bucket["thinking"] += int(details.get("thinking_tokens") or 0)
    bucket["cacheRead"] += int(usage.get("cache_read_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        hour = int(split.get("ephemeral_1h_input_tokens") or 0)
        minutes = int(split.get("ephemeral_5m_input_tokens") or 0)
        bucket["cacheWrite1h"] += hour
        # Trust the total over the split: an unfamiliar bucket would otherwise
        # go uncounted rather than merely unclassified.
        bucket["cacheWrite5m"] += max(minutes, written - hour)
    else:
        bucket["cacheWrite5m"] += written
    tools = usage.get("server_tool_use")
    if isinstance(tools, dict):
        bucket["webSearch"] += int(tools.get("web_search_requests") or 0)


def cost_of(model: str, counters: dict, fast: bool = False) -> float | None:
    rate = price_of(model, fast)
    searches = counters["webSearch"] / 1000 * WEB_SEARCH_PER_1K
    if rate is None:
        return searches or None
    inp, out = rate
    return (
        counters["input"] / 1e6 * inp
        + counters["output"] / 1e6 * out
        + counters["cacheWrite5m"] / 1e6 * inp * CACHE_WRITE_5M
        + counters["cacheWrite1h"] / 1e6 * inp * CACHE_WRITE_1H
        + counters["cacheRead"] / 1e6 * inp * CACHE_READ
        + searches
    )


# A transcript only ever grows, so the scan remembers where it stopped and picks
# up from there. Without this, every poll would re-read and re-total a file that
# is megabytes long within an hour of work.
USAGE_SCANS: dict[str, dict] = {}


USAGE_LOCK = threading.Lock()


def scan_usage(path: Path) -> dict:
    """Every model request in this transcript, totalled, read once.

    A turn is written down more than once — one line per content block, all
    carrying the same `requestId` — so the id is what keeps a turn from being
    counted as many times as it had things to say. Sub-agent turns are marked as
    sidechains and are kept apart: they are the session's spend, but not the
    session's conversation, and the two are worth telling apart.
    """
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = str(path)
    with USAGE_LOCK:
        held = USAGE_SCANS.get(key)
        # Replaced or truncated rather than appended to: start over.
        if held is None or stat.st_size < held["offset"]:
            held = {"offset": 0, "seen": set(), "main": {}, "agents": {},
                    "context": None, "contextModel": None, "contextAt": None,
                    "firstAt": None, "lastAt": None}
            USAGE_SCANS[key] = held
        if stat.st_size == held["offset"]:
            return held

        start = held["offset"]
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                chunk = handle.read()
        except OSError:
            return held

        # A line still being written has no newline yet. Stop at the last one
        # there is and leave the remainder for the next pass, so the scan never
        # sees half a turn and never skips it either.
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return held
        held["offset"] = start + cut + 1
        for line in chunk[:cut].decode("utf-8", "replace").split("\n"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            model = str(message.get("model") or "unknown")
            if model.startswith("<"):
                continue          # a synthetic turn the API never billed
            mark = entry.get("requestId") or entry.get("uuid")
            if mark in held["seen"]:
                continue
            held["seen"].add(mark)
            fast = usage.get("speed") == "fast"
            name = f"{model} (fast)" if fast else model
            side = bool(entry.get("isSidechain"))
            where = held["agents"] if side else held["main"]
            add_usage(where.setdefault(name, blank_counters()), usage)
            at = entry.get("timestamp")
            if at:
                held["firstAt"] = held["firstAt"] or at
                held["lastAt"] = at
            if not side:
                # What the model was carrying on its last turn: everything that
                # went in, cached or not. This is the session's context size.
                held["context"] = (int(usage.get("input_tokens") or 0)
                                   + int(usage.get("cache_read_input_tokens") or 0)
                                   + int(usage.get("cache_creation_input_tokens") or 0))
                held["contextModel"] = model
                held["contextAt"] = at
        return held


def read_usage(session_id: str, cwd: str) -> dict:
    """What this session has spent, per model, with the cost that implies."""
    for path in transcript_paths(session_id, cwd):
        if not path.exists():
            continue
        held = scan_usage(path)
        if not held:
            break

        def rows(bucket: dict) -> list[dict]:
            out = []
            for name, counters in sorted(bucket.items(), key=lambda kv: -sum(
                    (kv[1]["input"], kv[1]["output"], kv[1]["cacheRead"],
                     kv[1]["cacheWrite5m"], kv[1]["cacheWrite1h"]))):
                fast = name.endswith(" (fast)")
                model = name[:-7] if fast else name
                out.append({**counters, "model": name,
                            "cost": cost_of(model, counters, fast),
                            "priced": price_of(model, fast) is not None})
            return out

        main, agents = rows(held["main"]), rows(held["agents"])
        every = main + agents
        totals = blank_counters()
        for row in every:
            for field in totals:
                totals[field] += row[field]
        cost = sum(row["cost"] or 0.0 for row in every)
        return {
            "ok": True,
            "sessionId": session_id,
            "models": main,
            "agentModels": agents,
            "totals": totals,
            "cost": cost,
            "unpriced": sorted({row["model"] for row in every if not row["priced"]}),
            "context": held["context"],
            "contextModel": held["contextModel"],
            "contextWindow": context_window(held["contextModel"] or ""),
            "contextAt": held["contextAt"],
            "firstAt": held["firstAt"],
            "lastAt": held["lastAt"],
            "path": str(path),
        }
    return {"ok": True, "sessionId": session_id, "models": [], "agentModels": [],
            "totals": blank_counters(), "cost": 0.0, "unpriced": [], "context": None,
            "contextModel": None, "contextWindow": BIG_WINDOW, "contextAt": None,
            "firstAt": None, "lastAt": None, "path": None}

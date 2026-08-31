# Subagent visibility

The panel is blind to subagents. A session that has fanned out six agents reads
as one session doing one thing, and the work those agents did is missing from the
conversation, from the row, and from the spend.

This design gives the panel two things: a count on the session row saying how
many subagents are running, and a way to read one subagent's conversation by
tapping the tool row that spawned it.

## What is on disk

Subagents are not inline in the transcript. Claude Code writes each one its own
file, beside the session's:

```
~/.claude/projects/<slug>/<session-id>.jsonl          the session
~/.claude/projects/<slug>/<session-id>/subagents/
    agent-<agentId>.jsonl                             the subagent
    agent-<agentId>.meta.json                         what it is
```

The meta is small and says everything needed to name a subagent and tie it back
to the conversation:

```json
{"agentType": "Explore", "description": "Research live mode readout",
 "toolUseId": "toolu_018KL3v3Zj1589Nz6qufVsVb", "spawnDepth": 1, "model": "haiku"}
```

`toolUseId` is the `Task`/`Agent` tool_use block in the parent transcript, so the
mapping between a tool row and a subagent is exact rather than guessed. `model`
appears on newer files and not on older ones, so it is optional. `spawnDepth`
gives nesting.

The subagent's own file uses the same entry shape as a session transcript, every
entry carrying `isSidechain: true` and an `agentId`.

## What the panel does today

Three places treat a sidechain as something to drop, and two of them were right
when sidechains were written inline:

- `transcript.py:629` (`read_transcript`) skips them, so a subagent's work never
  reaches the conversation.
- `transcript.py:249` (`read_pending_question`) skips them, because a subagent's
  question is not one you can answer. This stays.
- `usage.py:205` splits them into their own bucket.

`transcript_paths` (`transcript.py:76`) only ever returns the session's own file,
so the `subagents/` directory is never opened by anything. Two consequences: the
work is invisible, and because `usage.py:225` goes through the same function,
**subagent spend is not counted at all** — the split at `usage.py:205` only ever
caught legacy inline sidechains.

One existing inconsistency is worth naming: `last_activity` (`transcript.py:99`)
does *not* filter sidechains. It reads the session's own file, which no longer
holds any, so it is harmless today and is left alone.

## Section 1 — The reader: `watchtower/agents.py`

A new module. `transcript.py` means "this session's own transcript" and keeps
that meaning; `agents.py` means "the subagents it spawned".

**`subagent_dir(session_id, cwd) -> Path | None`** — locates the parent
transcript through `transcript_paths`, then derives
`<transcript>.parent / <session-id> / "subagents"`. Going through
`transcript_paths` rather than rebuilding the slug keeps the existing
direct-hit-then-glob fallback working for a session whose `cwd` does not match
its slug.

**`list_subagents(session_id, cwd) -> list[dict]`** — one `iterdir`, then a read
of each `agent-*.meta.json`. Each is about 126 bytes. Pairs every meta with a
`stat()` of its `.jsonl` for size and mtime. Returns newest first, running ones
first.

**`read_subagent(session_id, cwd, agent_id, limit) -> dict`** — resolves
`agent-<agent_id>.jsonl` and parses it as a transcript.

### The one refactor

`read_transcript` (`transcript.py:553`) interleaves finding the path with parsing
the lines, and it must stop dropping `isSidechain` entries when the file it is
reading *is* a sidechain. Split it:

- `read_transcript(session_id, cwd, limit)` keeps its signature and its callers.
- `parse_transcript(path, limit, sidechain=False)` does the work.

The skip at `transcript.py:629` becomes conditional on that flag. The skip at
`transcript.py:249` stays unconditional.

### Is it running

The three states are named on the wire as `state: "running" | "done" | "stopped"`
rather than a `running` boolean, because "stopped" is a thing the panel says out
loud: an agent that died mid-tool reads differently from one that finished.

A subagent reads as **done** when the tail of its own file ends in an `assistant`
entry with `stop_reason: "end_turn"` and no `tool_use` block. Otherwise it reads
as **running**, unless the file's mtime is more than 120 seconds old, in which
case it reads as **stopped**.

The alternative — looking for the parent's `tool_result` for `toolUseId` — is
authoritative but unaffordable: on a long conversation the result falls off the
96 KB tail, and a finished subagent would then read as running forever. The
signal chosen is local to a file already being opened, and its failure mode is
safe: a subagent between turns can read as done a moment early, which corrects
itself on the next poll.

The mtime guard is what stops a killed or crashed subagent showing as running
until the panel restarts.

### Reading an id off the wire

`agent_id` arrives from a query string, and the panel is reachable from a phone
over the network. It is validated against `^[A-Za-z0-9_-]{1,64}$` and resolved
strictly inside `subagent_dir` — a rejected id is a 404, not a path.

## Section 2 — The server surface

### The live count, on the state poll

No new endpoint. The session payload grows one optional field, at both sites that
build it: `store.py:406` (live sessions) and `store.py:515` (kept rows).

```python
"agents": {"running": 2, "total": 6, "newest": "Explore: Research live mode readout"} | None
```

`newest` is the `agentType` and `description` of the most recently started
running subagent, or of the newest subagent when none are running. It is what the
row has to hand if it ever wants to say more than a count.

`None` when the session has no `subagents/` directory, which is the common case —
so a session that never spawned one is unchanged and no null travels on every
poll. This is the rule `ToolCall.change` already follows.

### Caching

A new `SessionStore._agents(session_id, cwd, now)`, following `_question`
(`store.py:243`) and gated on the **subagents directory's** mtime rather than the
transcript's: that directory changes only when a subagent is spawned or finishes,
so the common case is a `stat()` and a cache hit. Inside it, a subagent resolved
as done is cached as done permanently — a finished file does not reopen. A
session with six finished subagents and one running therefore costs one `stat()`
and one `tail_bytes` per poll.

`_transcript_touched`'s shape is reused rather than a second idiom invented.

### The drill-in: `GET /api/subagent`

`?sessionId=…&agentId=…&limit=60`. Modelled on `/api/change` (`http.py:433`) down
to its failure shape: the `_session_by_id` guard, and
`{"ok": false, "message": …}` with 404 when the session or the agent file is
gone. It returns the `Transcript` shape with the meta on top:

```ts
interface Subagent extends Transcript {
  agentId: string;
  agentType: string;      // "Explore", "general-purpose"
  description: string;    // "Research live mode readout"
  model?: string;
  spawnDepth: number;
  state: "running" | "done" | "stopped";
}
```

Reusing `Transcript` is deliberate: the chat view already renders that shape, so
the drill-in is a fetch and a header rather than a second renderer.

### Naming a subagent on the tool row

Listing a session's subagents needs no endpoint of its own. `/api/transcript`
gets the treatment `change` already has — a tool row that spawned a subagent
carries a small object naming it, matched by `toolUseId`:

```ts
interface ToolCall {
  name: string;
  detail: string;
  change?: ChangePreview;
  agent?: {
    agentId: string;
    agentType: string;
    state: "running" | "done" | "stopped";
  };
}
```

Absent on every tool call that spawned nothing. This is what makes the row
tappable, and it means opening a subagent costs no request to discover what to
open.

## Section 3 — The panel

### The row

`paintListItem` (`main.ts:823`) grows one badge in the supporting line, beside
the state word and the folder. It says `2 agents` while any are running and
nothing at all when none are — a session with six finished subagents is a session
doing one thing again.

`session.agents?.running` **must** join the `signature` string (`main.ts:859`).
That signature is what decides whether the row repaints; a field left out of it
is a field that never updates on screen.

### The tool row and the drill-in

`web/src/views/subagent.ts`, modelled closely on `views/change.ts`:

- A folded block on the `Task`/`Agent` tool row, like `changeBlock`
  (`change.ts:21`): the agent type, its description, its step count, and whether
  its state. Tapping it is `data-act="subagent"`.

  No step count. Counting a subagent's turns means reading its whole file, and it
  is the only thing on the folded block that would — the type, the description
  and the state all come from the meta and a `stat()`. Not worth a full read per
  agent per poll.
- Opened, it takes the pane the way a change does — `chat.agentShown` alongside
  `chat.changeShown`, the way back where the way back goes, the conversation
  where you left it. A subagent's conversation is a conversation, so it renders
  through the same message renderer, with a header naming the agent and its
  model.
- A fetched subagent is held in a `Map` keyed by `agentId`, as `changeFull`
  holds patches. A running one is re-fetched on the detail refresh; a finished
  one is fetched once.

### Spend

`scan_usage(path)` (`usage.py:140`) is already per-path and incremental, and
subagent entries already carry `isSidechain: true`, so they route themselves into
the `agents` bucket with no change to the counting. What is missing is the call.

In `read_usage` (`usage.py:223`), after the main path is scanned, scan each
`subagents/*.jsonl` and merge their `agents` buckets into the main scan's. Merging
per-model counters needs a small helper; `context` needs no guard, because the
existing `if not side` already refuses to take a context reading from a sidechain.

The usage view then reports what a fanned-out session actually cost. Nothing in
`views/usage.ts` changes: the `agents` bucket it already renders simply stops
being empty.

## Testing

Standard library only, matching the project.

- `tests/python/test_agents.py` — `subagent_dir` resolution including the glob
  fallback; `list_subagents` against a fixture directory; the running signal in
  its three states (ends in `end_turn` → done; mid-tool with a fresh mtime →
  running; mid-tool with an old mtime → stopped); a rejected `agentId`.
- `tests/python/test_parsing.py` — that `parse_transcript(sidechain=True)` keeps
  the entries `read_transcript` drops, and that `read_transcript` still drops
  them.
- `tests/python/test_usage.py` — a session with two subagent files reports their
  spend in the `agents` bucket and takes no `context` reading from them.
- `tests/change-check.py:95` locks in that a subagent's file change stays out of
  the parent's change view. That expectation is unchanged and the test stays.
- `tests/fixtures.py` gains a session with a `subagents/` directory — one running
  agent and one finished — so the row badge and the drill-in can be seen in the
  UI checks.

## Out of scope

- No dedicated agents pane. The row badge and the drill-in are the whole of it.
- A subagent's file changes stay out of the parent's change view.
- Nesting is carried (`spawnDepth`) but not drawn: a subagent that spawned its
  own subagents reads as one agent. Revisit when it is a real annoyance.

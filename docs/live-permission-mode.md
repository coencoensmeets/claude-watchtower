# Changing a running session's permission mode

Notes from an investigation on 2026-08-18/19. The short version: the panel
cannot change the permission mode of a session it did not start, three separate
routes were closed off by testing, and a fourth — the IDE bridge — looks viable
but is unproven. This records what was verified, what was tried and taken out
again, and what the next experiment is, so none of it has to be rediscovered.

Everything below was checked against Claude Code **2.1.234** on Linux. It reads
undocumented internals throughout; an update can invalidate any of it.

---

## The problem

A session's permission mode is settled by the process that starts it. The panel
shows sessions it did not start, so for those there is nothing to change — the
mode was decided at a moment the panel was not present for, by a process it does
not own.

This matters because the panel *can* read the mode (`read_permission_mode`), and
a reading without a control next to it invites the question "so why can't I
change it". The answer is below.

## What was ruled out, and how

### 1. The messaging socket ignores `set_permission_mode`

The per-session unix socket takes an optional `auth` line and then a message.
`set_permission_mode` sent down it is **accepted in silence and does nothing**.

Silence is not the tell — a known-good `{"type":"user"}` message is also
answered with silence. The tell is behavioural:

```
start a session with --permission-mode default --permission-prompt-tool stdio
send  {"type":"control_request","request":{"subtype":"set_permission_mode","mode":"acceptEdits"}}
then  ask it to write a file
=>    it still raised a permission prompt, i.e. it was still in `default`
```

The binary explains why: `set_permission_mode is not supported in this context
(onSetPermissionMode callback not registered)`. The callback is registered on
another transport — see [the bridge](#the-bridge-the-one-route-left).

### 2. There is no keypress to send

Pre-existing finding, recorded in the README's *Permission mode* section: X11
pairs windows, a terminal window holds tabs and an editor window holds
terminals, so a keypress aimed at a window lands in whichever Claude has focus.
Built, tried, removed.

### 3. Restarting the session (the "takeover" that was built and backed out)

The panel could SIGTERM the process holding a session and resume the transcript
itself, applying the new mode to the next turn. This was built, shipped briefly,
and taken out. It is recorded here because it looks like the obvious answer and
is not.

What went wrong, in order:

1. Takeover killed the session and only *cleared the way* — nothing resumed it
2. That left a stopped row, whose most prominent button is **Start it up**
3. **Start it up** opens a terminal, which silently handed the session back
4. Net effect: session killed, restarted in a terminal, mode unchanged

Worse, two processes ended up resuming one transcript at once — pids 52835 and
53200 both on `58dd76b5`, both alive, both appending turns to the same file. The
guard against that existed but only on `/api/owned/say`; `/api/start` never
consulted it.

The decisive argument against the approach is not that the implementation was
buggy. It is that **claude-code-chat will not do this either**, and it owns every
process it talks to:

> An ongoing Claude execution cannot be interrupted and restarted just to toggle
> plan mode; the user must wait for the current response to finish.

A restart is not a settings change, and dressing it as one produced exactly the
damage you would expect.

**Fixes kept from that episode** (they are correct regardless):

- `STORE.holders(sessionId)` — the single "who is resuming this transcript"
  check, now consulted by every path that starts a process on one
- Two processes on one transcript are surfaced in the row and detail header
  rather than only prevented
- `proc_gone()` — a process that has exited but not been reaped keeps its
  `/proc` entry, so "is there a stat file" reports it alive indefinitely. Check
  the state field for `Z`
- **Start it up** refuses on a session the panel owns

---

## The bridge: the one route left

The official VS Code extension *can* switch a running session's mode. The
mechanism is a WebSocket, and the direction is the surprise.

### How it works

1. The extension runs a **WebSocket server** and advertises it in a lockfile:

   ```jsonc
   // ~/.claude/ide/<port>.lock
   {
     "pid": 2818,
     "workspaceFolders": ["/home/you/project"],
     "ideName": "Visual Studio Code",
     "transport": "ws",
     "runningInWindows": false,
     "authToken": "…"
   }
   ```

2. **Claude dials out to the IDE**, not the other way round:

   ```
   ESTAB  claude,pid=58645      → 127.0.0.1:29407     (the session)
   ESTAB  MainThread,pid=3088   ← 127.0.0.1:48537     (the IDE, listening)
   ```

3. An unauthenticated handshake gets `101 Switching Protocols` and then
   `Unauthorized` — the token gates the session, not the upgrade.

4. The connection is bidirectional. The IDE serves MCP tools *to* Claude
   (`mcp__ide__getDiagnostics`, `mcp__ide__executeCode`) and Claude accepts
   `control_request` frames coming back.

### What the bridge can do that the socket cannot

Subtypes seen in the binary alongside `set_permission_mode`:

`interrupt` · `set_model` · `rename_session` · `get_usage` ·
`get_context_usage` · `set_max_thinking_tokens` · `apply_flag_settings` ·
`set_color` · `file_suggestions` · `read_file` · `mcp_status` ·
`mcp_reconnect` · `mcp_authenticate` · `initialize`

### When a session connects

```js
CLAUDE_CODE_SSE_PORT !== undefined || CLAUDE_CODE_AUTO_CONNECT_IDE === true
```

`CLAUDE_CODE_SSE_PORT` is set at exec by whatever launches the session. An
environment cannot be changed after exec, so **a session already running can
never be attached to a different bridge**. Whatever it connected to at startup
is what it has for life.

### Headless sessions do not participate

Tested with a matching lockfile and `CLAUDE_CODE_AUTO_CONNECT_IDE=true`: a
`--print --input-format stream-json` session opened eleven connections, all
outbound HTTPS to the API, none to any local bridge.

This cuts both ways:

- Sessions the panel **owns** cannot use the bridge — but they do not need it,
  since their mode is a flag on each spawn
- Sessions the panel owns also cannot *steal* a bridge from an IDE

## The proposed design

The panel runs its own WebSocket server and sets `CLAUDE_CODE_SSE_PORT` to it
**only for terminal sessions the panel itself launches** (`start_session` /
`new_session`, which already build a clean environment via `top_level_env()`),
and **only when the variable is not already set**.

Consequences:

- It never competes for a session VS Code would have had — those already have
  the variable set at exec
- It only reaches sessions the panel was present for at launch; existing
  sessions stay unreachable, permanently
- It requires no ownership, kills nothing, and interrupts no turn — this is a
  side-channel into a session its terminal still runs

Ownership and the bridge are for different session kinds and do not overlap:

| | Ownership | Bridge |
|---|---|---|
| Who runs the session | the panel, per turn | its own terminal |
| What the panel holds | stdin/stdout | a side-channel |
| Needs the session stopped | yes | no |
| Kills anything | yes, to take over | never |
| Applies to | headless stream-json | interactive terminal sessions |

### Feasibility

A WebSocket server is roughly 150 lines of stdlib Python — `base64` + `hashlib`
for the handshake, manual frame parsing — so the project's no-dependencies rule
survives. A working prototype listener is in the scratchpad notes for this
investigation; it completes the handshake and logs frames.

The unknown is the **handshake above the transport**: the `initialize` exchange
and how the `authToken` is presented. That has to be reverse-engineered.

### Next experiment

An interactive session launched with `CLAUDE_CODE_SSE_PORT` pointed at our
server did **not** connect within 15s. That is *not* evidence the approach
fails — the test lockfile's `pid` field named a Python process that had already
exited, and Claude Code plausibly checks that the advertiser is alive.

Redo it with:

- the lockfile's `pid` naming a **live** process (the panel itself)
- `workspaceFolders` matching the session's cwd
- the server up before the session starts, and a longer window than 15s

Prove `initialize` and `get_usage` round-trip before going near
`set_permission_mode`.

#### The harness for it, and what stopped it

Written on 2026-08-21, against 2.1.238 rather than the 2.1.234 the rest of this
was checked on:

- `probe.py` — the listener the design calls for, in stdlib alone: the
  `base64`/`hashlib` upgrade, manual frame parsing, a `<port>.lock` naming its
  own live pid, and an `initialize` / `tools/list` answer so a session that does
  connect gets past the MCP handshake. It logs the request headers, which is
  where the `authToken` is expected to arrive, and every frame either way.
- `drive.py` — an interactive session on a pty with `CLAUDE_CODE_SSE_PORT` set
  at exec. A pty because `--print` does not participate; nothing is typed at it,
  because if it connects at all it connects at startup.

The listener runs. The experiment did not, and the reason is worth recording:
**an agent working in this repository cannot run it.** Three separate actions
were refused by the sandbox — reading the protocol strings out of the `claude`
binary, writing a lockfile into `~/.claude/ide/`, and launching a `claude` in
the project. Writing the lockfile anywhere else is allowed, which places the
line precisely: standing in for the IDE is what is off limits, not listening on
a socket. So this experiment is a thing to be run by hand, or behind an explicit
permission rule, and an agent asked to "just implement the bridge" will get as
far as this paragraph.

Two facts fell out of the attempt anyway:

- **The port is in the file name, not in the file.** A live VS Code advertised
  `~/.claude/ide/19844.lock` whose `pid` was `436` — the extension host. Reading
  `pid` as the port, or the name as anything else, finds nothing.
- **A session in an untrusted folder never reaches the bridge.** Started in a
  folder Claude Code has not been trusted with, it stops on *"Is this a project
  you created or one you trust?"* and dials nothing until that is answered. It
  bears on the panel's own launches as much as on the experiment: a new session
  in a folder new to Claude Code sits on that prompt, so a bridge that has not
  connected yet is not evidence the bridge is broken.

---

## How it was settled, 2026-08-21

Two of the three questions this document was opened for now have answers, and
neither is the bridge.

### Prompts and questions come over stdio, not the bridge

`--permission-prompt-tool` is **gone from `--help` in 2.1.238 but still live**.
The official extension's own argv builder is the proof:

```js
if (M) {                    // a canUseTool callback was provided
  if (v) throw Error("canUseTool callback cannot be used with permissionPromptToolName…")
  W.push("--permission-prompt-tool", "stdio")
}
```

`stdio` means *ask over the control channel*. No `initialize` handshake is
needed to enable it — the flag is the declaration. Captured, in full, from a
real turn:

```
→ claude --print --input-format stream-json --output-format stream-json --verbose
         --resume=<id> --permission-mode manual --permission-prompt-tool stdio
← {"type":"control_request","request_id":"…","request":{
     "subtype":"can_use_tool","tool_name":"Write","display_name":"Write",
     "input":{…},"description":"probe.txt","tool_use_id":"…",
     "permission_suggestions":[{"type":"setMode","mode":"acceptEdits","destination":"session"}]}}
→ {"type":"control_response","response":{"subtype":"success","request_id":"…",
     "response":{"behavior":"allow","updatedInput":{…}}}}
```

A **multiple-choice question is the same channel**, exactly as the extension
suggested: `AskUserQuestion` arrives as `can_use_tool` with
`requires_user_interaction: true` and no `permission_suggestions`. Answering it
*is* allowing it — the picks go back in `updatedInput.answers`, an object keyed
by the question's own text, with several picks joined on `", "`. Proved by
picking the last option deliberately and getting *"You picked **Tabs**"* back.

So the earlier reading in this file stands: the bridge is not a prompt sink, and
never needed to be. The transport that owns stdio is where prompts live, and a
turn the panel launches owns stdio by construction.

### The takeover, rebuilt and this time kept

What was backed out was a takeover that killed a session and only *cleared the
way*. What works is the same first half with the second half actually present:
the panel can now run the next turn itself, so ending the terminal leads
somewhere. **Make interactive** keeps the row, ends the process, waits for it to
really go, and hands the conversation to a `claude --print --resume` turn.

Three things had to be true, and each was a bug first:

- **A zombie is not a live session.** `proc_gone()` is back, and it is not
  optional: a `claude` that exits on `SIGTERM` but is not reaped keeps its
  `/proc` entry, `kill -0` succeeds on it, and the row went on reporting `idle`
  indefinitely. Check field 3 of `stat` for `Z`.
- **A session with no transcript cannot be *resumed*.** `--resume` on one fails
  with *"No conversation found with session ID"*, and by then the terminal is
  already dead. This was a refusal at first — *send it something first* — which
  had it backwards: the empty session is the one with nothing to lose, and being
  told to type into the terminal in order to stop using the terminal is not an
  instruction anyone can follow. Emptiness is still read while the session is
  alive, but it now decides *how* it comes up here: `--session-id` under its own
  name instead of `--resume`, which is the same path a panel-started session
  that nobody has typed at already takes.
- **A row wanted back now must not wait.** A vanished session is held for twenty
  seconds so the list can show it closing out, which is right when something
  else ended it and wrong when the panel did. `STORE.forget()` drops it at once.
- **The kept row is held, not written down.** It first went into `sticky.json`,
  which made every session the panel touched permanent: the panel decided what
  you were keeping, and a week of adopting sessions left a list of rows nobody
  asked for. It now lives in memory for as long as the panel runs — which is
  exactly as long as the claim *runs from here* is true — and pinning is the
  separate, asked-for thing that survives a restart. Removing a row is its own
  action (`/api/forget`), and it stops the held process on the way.

What has *not* changed: a session running in a terminal still cannot have its
mode changed in place. Adopting it is not that — it is ending one thing and
starting another on the same conversation, which is the honest version of the
question and the only one with an answer.

### The bridge, still unproven

Unchanged and still the only route to a mode change without ending anything. The
handshake above the transport was never tested, because an agent working in this
repository cannot test it — see the paragraph above about the sandbox.

---

## One turn at a time, and the queue that follows

The held pipe serves turn after turn, but only one at a time: a second `user`
frame written while a turn is in flight is not something the panel is willing to
find out about experimentally, because the failure it would be finding out about
is two turns appending to one transcript — the failure the rest of this document
is about.

So the panel keeps the second message itself (`OWNED_QUEUE`) and writes it when
the turn's `result` frame lands. That is the whole mechanism, and its two edges
are where the thinking went:

- **The reader drains it, not the poll.** The thread reading the pipe is what
  sees the result, so it sends the next message in the same instant the turn
  ended — no browser involved, and a panel with nothing open drains too.
- **The queue is the panel's, so the panel has to answer for it.** It is drawn
  above the composer with a way to drop each item, capped at ten, handed to
  `deliver_later` if the process dies under it, and cleared when somebody lets
  go of the session deliberately. A queue that is not visible, not droppable and
  not bounded is worse than the refusal it replaced.

What it replaced was `"It is still answering the last one"` — a refusal for a
state the panel could see, could time, and could do something about.

---

## Stopping a turn: what the control channel gives, and what it does not

`{"type":"control_request","request":{"subtype":"interrupt"}}` on the held pipe
works, and it is the same channel `set_permission_mode` is refused on everywhere
else — one more thing that only exists while something holds the session's stdio.

Measured on a held session, mid-answer:

- answered `{"subtype":"success","request_id":"…","response":{"still_queued":[]}}`
  in about a tenth of a second
- the transcript gains a user turn reading `[Request interrupted by user]` at the
  point it stopped
- the turn ends `result` / `is_error: true` / `subtype: "error_during_execution"`
- the process stays up and served the next turn normally

The third of those is the one that needed handling rather than recording: an
interrupted turn is indistinguishable from a failed one in its own result frame,
so the panel notes that it asked (`OWNED_STOPPING`) and reads the result in that
light. Otherwise pressing Stop painted the row red and reported
`error_during_execution` at the person who had just pressed it.

### And what SIGINT does, which is not this

Worth writing down because it is the obvious thing to reach for on a session the
panel does *not* hold, and it is wrong. Ctrl+C at a terminal is a **keystroke**:
Claude Code puts the tty in raw mode, so no `SIGINT` is generated by the terminal
driver and nothing outside the pty can deliver the keypress either.

`SIGINT` to the pid from outside was tested in a throwaway pty session, both
while it was generating and while it sat at its prompt. In both cases it **ended
the session** — Claude Code printed `Resume this session with: claude --resume …`
and exited. So there is no interrupt for a terminal session from out here, and
the panel says so on a disabled button rather than shipping a Stop that quietly
kills sessions.

---

## Incidental findings worth keeping

- **`--permission-mode` accepts `manual`, not `default`.** Documented choices are
  `acceptEdits, auto, bypassPermissions, manual, dontAsk, plan`; `default` also
  works as an undocumented alias and is what transcripts report. The panel keys
  modes the transcript's way and translates at the argv boundary (`CLI_MODES`).
  Invalid choices exit 1, so the list is enforced.
- **Plan mode overrides `--model`.** `--permission-mode plan --model haiku` runs
  on `claude-sonnet-5`. Claude Code's doing, not the panel's.
- **Auto is not the cautious mode its name suggests.** It is Claude Code's
  default, but a classifier decides what needs approval — in testing it approved
  and ran `rm -rf` with no prompt reaching the panel.
- **A stream-json process emits nothing until it receives a message** — no
  `init`, no session id, no transcript. Naming the session ourselves with
  `--session-id` is what makes an empty panel-run session possible at all: the
  row can exist before anything is said, so "New session, run from here" no
  longer has to ask for a first message, and a never-spoken terminal session can
  be taken over the same way.
- **Entry points seen in session files:** `cli` (terminal), `sdk-cli`
  (stream-json), `claude-vscode` (the official extension, which writes its
  session file once at startup and never updates the status — hence
  `inferred_status`).
- **`MainThread` is not a host name.** It is the `comm` a Python or Electron
  process gives its first thread, so an editor or agent harness appears in the
  process chain under it. `hostOf` treats it as anonymous.

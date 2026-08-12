<br />
<p align="center">
  <a href="https://github.com/coencoensmeets/claude-watchtower">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="static/claude-watchtower-transparent-dark.svg">
      <img src="static/claude-watchtower-transparent.svg" alt="Logo" height="170">
    </picture>
  </a>

  <h3 align="center">Claude WatchTower</h3>

  <p align="center">
    A live web panel for every Claude Code session running on this machine
    <br />
    <a href="#what-it-shows"><strong>Explore the panel »</strong></a>
    <br />
    <br />
    <a href="#quick-start">Quick start</a>
    ·
    <a href="https://github.com/coencoensmeets/claude-watchtower/issues">Report Bug</a>
    ·
    <a href="https://github.com/coencoensmeets/claude-watchtower/issues">Request Feature</a>
  </p>
</p>

> **Disclaimer:** This project is an independent effort by a community member and is not an official Anthropic product. It reads Claude Code's own session files and per-session sockets, neither of which is a documented interface, so an update to Claude Code can change what the panel sees.

<p align="center">
  <a href="https://github.com/coencoensmeets/claude-watchtower"><img src="https://img.shields.io/badge/Leave%20a%20star-⭐️-yellow?style=for-the-badge" alt="Leave a star"></a>
  <a href="https://www.paypal.com/donate/?hosted_button_id=JFMLJWDVTZRUY"><img src="https://img.shields.io/badge/Donate%20a%20coffee-☕️-blue?style=for-the-badge" alt="Donate a cup of coffee"></a>
  <a href="#what-it-shows"><img src="https://img.shields.io/badge/Docs-Read%20the%20docs-green?style=for-the-badge" alt="Docs"></a>
</p>

See at a glance which session is working, which finished, and which is waiting on you — then click **Focus window** to jump to the terminal or editor that owns it.

Python standard library only. No install step, no dependencies, nothing leaves the machine.

## Quick start

1. Clone the repository:

```bash
git clone https://github.com/coencoensmeets/claude-watchtower.git
cd claude-watchtower
```

2. Run the panel:

```bash
python3 server.py
```

3. Open <http://127.0.0.1:8787>. Every Claude Code session on this machine is already in the list — there is nothing to configure and nothing to install.

4. Optional: `sudo apt install xdotool` to enable **Focus window** under X11, and see [Keeping it running](#keeping-it-running) for the bundled systemd user unit.

## Documentation

| Section | What's inside |
|---|---|
| [What it shows](#what-it-shows) | The four states, and why the one you care about is inferred rather than read |
| [Layout](#layout-an-index-and-a-detail-pane) | The index, groups, the detail pane and its four tabs |
| [Sending a message](#sending-a-message) | The composer, the unix socket behind it, and its three honest limits |
| [Permission mode](#permission-mode) | How much rope a session has, when it was true, and why it is read-only |
| [Git and History](#git-and-history) | Staging, committing and pushing in the editor's own Source Control layout, and a commit graph drawn from real ancestry |
| [Naming a session](#naming-a-session) | Call a session what you are using it for |
| [Focusing a window](#focusing-a-window) | Matching by pid, identifying over the pty, pairing by hand |
| [Ending a session](#ending-a-session) | SIGTERM, force quit, and the stale-pid check |
| [Keeping a session](#keeping-a-session-after-it-closes) | Rows that outlive their process, and resuming them |
| [Design](#design) | Dynamic colour, MD3 tokens, typography, components |
| [Options](#options) | Flags, URL overrides, fixture sessions |
| [Keeping it running](#keeping-it-running) | The bundled systemd user unit |
| [Tests](#tests) | What `tests/ui-check.mjs` asserts over CDP |
| [API](#api) | Every route |

Start with [What it shows](#what-it-shows) if you are new, or jump straight to the [API](#api) if you know what you need.

## What it shows

Claude Code keeps one file per running session in `~/.claude/sessions/`, and updates the `status` field as the session changes. The panel reads those files every second and reports:

| State | The panel reads | What it means |
|---|---|---|
| 🟠 `waiting` | Needs an answer | Blocked on a question or a permission prompt |
| 🔵 `busy` | Working | Thinking or calling tools |
| 🟢 `idle`, `shell` | Waiting | Finished its turn, or on its way into a foreground shell command |
| ⚪ — | Closed | The process is gone; it clears after 20 seconds |

Sessions started from the VS Code extension never fill that field in; their state is read from liveness alone — see below.

`shell` gets no colour of its own. Claude Code writes it both when it starts a foreground command *and* on the way out of a turn, so as a state on screen it was a second colour for the same thing — the panel shows it as Waiting and merges it into its neighbours on the trace. It still counts as mid-turn where that matters: a message sent to such a session is queued, not delivered.

> **The state you care about most is never written down.** Claude Code records a status when the status *changes*, not on a timer, and it does not record one for sitting at the prompt. Watch a session answer you and then wait: the last thing it writes is `shell`, and it holds that for as long as you take to reply. The same happens at startup, where it writes `shell` while sourcing its shell snapshot and then goes quiet. Taken at face value, every session that has ever finished a turn reads as busy forever.

So "Waiting" is inferred, not reported. Age alone cannot infer it — a `busy` written fifteen minutes ago may equally mean fifteen minutes of hard thinking, and this panel has a session doing exactly that. The panel therefore asks for a second opinion before it overrules a reading: is the process burning CPU (measured over five seconds — a working session runs two orders of magnitude above one at the prompt), and is its transcript still growing. Only when the reading has gone unrefreshed for fifteen seconds *and* neither signal shows life does the session flip to waiting for you.

The age threshold is deliberately short, because it is not what protects a working session from being mislabelled — the liveness signals are. `waiting` never expires either way: blocked on you is not the same as stale.

**A session inside the VS Code extension writes no status at all.** Its session file lands once at startup — pid, cwd, name, `entrypoint: claude-vscode` — and never gains a `status` field, so there is nothing to overrule and nothing to expire. Read at face value that is an absent status, which the panel used to take as `idle`: such a session sat at "Waiting" for its entire life, working turns included. The same two liveness signals settle it instead, since they are the whole reading here rather than a second opinion: CPU or a growing transcript means Working, neither means Waiting. What cannot be reached this way is `waiting` — a permission prompt in a VS Code session reads as finished rather than as blocked on you, because nothing on disk distinguishes the two.

The obvious worry — a long, silent build being called ready while it runs — does not materialise: Claude Code keeps redrawing its own spinner throughout, which measured 0.17 of a core during a deliberately quiet twelve-second command, an order of magnitude above the threshold. The reading only expires for a session that is both quiet on disk and doing nothing measurable.

**A session started from inside another one writes no session file at all.** Run `claude` from a session's own shell, or from a terminal that inherited its environment, and the new session marks itself a child — `CLAUDE_CODE_CHILD_SESSION=1` — and leaves nothing in `~/.claude/sessions/` but the key file that goes beside a session file. Nothing in that directory to read means the panel used to miss such a session completely, however hard it was working.

So the panel writes that session file itself, from `/proc`: the pid, its working folder, when it started, which build it is running, and the parent it names in `CLAUDE_PID`. Its row is a row like any other — state, branch, Git tab, **Focus window**, **Send**, **End** — and it says whose child it is under its name.

Two things about a nested session are genuinely missing rather than merely somewhere else:

- **It has no transcript.** Nothing is written for it under `~/.claude/projects`, so there is no conversation to read, and nothing to take a title or a permission mode from. The Chat tab says so rather than promising one is coming.
- **It publishes no session id.** Nothing outside the process can read the id it knows itself by, so the panel gives it one of its own — `child:<pid>:<starttime>`, unique for as long as the process lives. A name you type, a window you pair and a row you pin are all remembered against that, and outlive nothing but the process itself.

Its status comes from the liveness signals, exactly as a VS Code session's does, with the same limit: a nested session sitting at a permission prompt reads as Waiting rather than as blocked on you.

Headless work is left out. A `claude -p` errand started from inside a session is a child too, but it is one turn of someone else's work rather than a session you could type at, and the panel tells them apart by what is on standard input — a pty means somebody is there, a pipe or `/dev/null` means nobody is.

## Layout: an index and a detail pane

The window is split the way Material 3's list-detail layout prescribes.

### Left: the index

Every session, one row each, sorted so anything waiting on you is at the top. Each row carries a round avatar whose *fill* is the session's state colour and whose *icon* is where the session lives — `</>` for VS Code, a terminal glyph for GNOME Terminal, Konsole, kitty, Alacritty, WezTerm, Ghostty or xterm, split panes for tmux or screen, a globe for a session over SSH. A lamp on the corner of the avatar repeats the state and animates: a slow pulse while working, a blink while waiting. Under the name you get what the session is working on, then the state and the folder, and on the right how long it has been in that state.

The middle line is Claude's own one-line description of the conversation, which it writes a few turns in and rewrites as the subject moves. It is what tells four Claudes in one repository apart — the name says what a session is called, the description says what it is doing — and the detail pane shows the same line under the name. A session too young to have been described yet keeps a two-line row.

The filter chips at the top of the index count each state and narrow the list to one of them. Click the same chip again, or **all**, to go back.

### Groups

Several Claudes in one repository is the usual shape, so the index groups them: two or more sessions sharing a working folder sit under a header named after the folder, with a count and one dot per state inside it. A folder with a single session stays a plain row — a group of one is noise. Click a header to fold the group away; a folded group holding something that needs an answer still shows the dot for it.

You can also group by hand:

| Gesture | Does |
|---|---|
| **Ctrl-click** a row | Pick it |
| **Shift-click** a row | Pick the run between |
| **Space** on a focused row | Pick it, from the keyboard |
| **Group** in the bar above the list | Make the picked rows a group of their own |
| **Right-click** a header | Fold, rename, ungroup, or pick everything inside |
| **Right-click** a row | **Take out of the group** |
| **Right-click** a folder group | **Do not group this folder** |

A bar above the list says how many are picked. A group you made is named after its folder when the rows share one, and wins over the folder grouping for the rows in it, so it can gather sessions from different repositories. **Do not group this folder** leaves that folder as plain rows until you choose **Group every folder again**.

Grouping is a view of the list rather than something the sessions carry, so it is kept in the browser — in `localStorage`, like muting — not on the server.

### Right: the detail

Click a row and the pane shows that session in full: its name, state and how long, the AI-written title of the conversation if there is one, the host, folder, branch, uptime and permission mode, and a **Focus window** button. Below that, the tabs:

| Tab | What's inside |
|---|---|
| **Conversation** | The recent transcript, as speech bubbles, plus the composer |
| **Git** | What the session has done to the working tree |
| **History** | The same repository's recent commits, drawn as a graph |
| **Details** | Window pairing, notification switch, the facts, and **End session** |

**Conversation.** Your prompts and Claude's replies are speech bubbles; turns that only ran tools are quiet single rows with the tool name and its target, so the actual conversation stays readable. It is read from the end backwards and stops at a page of messages, which is why a five-megabyte transcript opens as fast as a fresh one — and why the page is a page of *conversation*: a fixed tail of a working session is nearly all tool output, so it would show Claude's last twenty turns and none of your own messages. The bubbles render Markdown — headings, lists, tables, quotes, emphasis, links and code blocks — because an answer read as raw `###` and `**` is hard work. Nothing from a message reaches the page as markup: code is held aside first, every other scrap is escaped before a tag is added, links are only made for http, https and mailto, and an image becomes a link rather than a remote fetch. It follows the live session, and stays pinned to the newest message unless you scroll up. A long answer buries what you asked for, so a **Last request** pill floats over the corner whenever your most recent message is off screen; it scrolls back to it and its arrow points the way it will travel.

**Git** and **History** are described under [Git and History](#git-and-history). **Details** carries window pairing, a per-session switch for whether it may raise a desktop notification when it starts waiting, the facts (pid, Claude Code version, session id, start time, where the transcript lives), and **End session**.

Below 900px the two panes become one at a time, with a back arrow in the app bar.

## Sending a message

Below the transcript is a **composer**: type and the message goes into that session, no keystroke faking and no window stealing involved. Claude Code listens on a per-session unix socket — the path is in the session file, the socket is `0600`, and the panel writes two lines of JSON to it. Enter sends, Shift-Enter starts a newline.

Three honest limits, each stated on the composer itself rather than left to be discovered:

| Limit | Why |
|---|---|
| **A session blocked on a prompt cannot be answered this way** | A permission dialog or a question is modal in the terminal; a queued message waits behind it. Since "needs an answer" is exactly what this panel sorts to the top, the composer switches itself off there and says to answer in the terminal. It is the one case you might most want and cannot have. |
| **A message arrives labelled as coming from another session**, not as something you typed | Measured, not assumed: a session that received one reported it appeared "clearly marked as coming from another session rather than from Coen". So the receiving Claude may treat it with a peer's authority rather than yours, and will not take it as approval for anything. |
| **The protocol is internal** | The session file records `peerProtocol`, and the panel refuses to send unless it reads the version it knows, going quietly read-only after an upgrade rather than writing malformed lines at a socket. |

A message sent to a session that is mid-turn is queued at its prompt, exactly as if you had typed ahead in the terminal, and lands when the turn ends.

**It then appears in the conversation above, marked with where it came from** — `you · from here` for something typed into this composer, and the sending session's name for a message from another Claude. That takes some digging out: Claude Code never writes such a message down as a turn of its own. It records the envelope it wraps it in (`<cross-session-message …>`) on the queue and hands the body to the model as an attachment, so read at face value the transcript shows Claude answering something nobody said. The panel therefore reads the queue entries too and unwraps them, and skips the duplicate the queue leaves behind when the message comes back off it.

> **Sending is loopback-only, regardless of `--host`.** A prompt is an instruction to an agent holding tools and a checkout, so a panel exposed to the network keeps the transcript and loses the composer. `--no-send` switches it off on loopback too.

## Permission mode

Among the facts in the detail header is a pill for how much rope the session has: **Manual**, **Plan**, **Accept edits**, **Auto**, **Bypass**, **Don't ask**. The last four are filled rather than outlined, because those are the modes where the session may act without coming back to you — the thing you want to spot across several sessions at once, and the one thing you cannot see without visiting every terminal in turn.

**It says when it was true, because it cannot always be current.** A session writes its mode into the transcript only when the metadata block is re-appended — on a resume, at exit, once the transcript has grown past a threshold — and never at the moment the mode changes. A session that is working therefore reports within seconds; one sitting at its prompt keeps reporting whatever it last said, for as long as it sits there. A few older sessions have never written one at all, and those show nothing rather than a guess.

> **There is deliberately no way to change it from here**, and the reason is worth recording. Nothing in a session's socket sets the mode — it takes a message and a rename, and that is all; `set_permission_mode` exists in Claude Code but only on the transports an SDK or the browser bridge speaks. That leaves pressing Shift+Tab, and the panel cannot aim a keypress at a *session*: X11 pairs windows, while a terminal window holds tabs and a VS Code window holds terminals. A press sent at a window lands in whichever tab has focus, which may be a different Claude — so the panel's own matcher regularly has two sessions behind one window id. Quietly loosening the wrong session's permissions is not a thing to get wrong occasionally, and a keypress the panel cannot verify (see the paragraph above) would do exactly that. It was built, tried, and taken out again.

**State trace.** The strip under the detail header is that session's recent history, one coloured band per state, newest at the right. It answers the question a single status lamp cannot: has this been waiting for a while, is it churning between shell commands, or has it been quietly working the whole time. The strip scales to the session's own lifetime up to 30 minutes — the left label tells you the span.

**Notifications.** The first click anywhere on the page asks for notification permission. After that, any session flipping into `waiting` raises a desktop notification, and the tab title and favicon show how many are waiting.

## Git and History

A session is nearly always working inside a repository, and the question you actually have while watching one work is what it has done to the tree. **Git** answers it, and then lets you act on the answer: the branch, how far it has drifted from its upstream, and every changed file in the three groups the editor uses — **merge changes**, **staged changes**, **changes** — with a message box and a commit button above them.

**The branch in the header is a button.** Pressing it opens the branch list, as the editor's status-bar branch does: *Create new branch…*, *Create new branch from…*, then the local branches most-recently-committed-first — the two or three you are actually moving between all week, rather than an alphabet — and then any remote branch with no local copy yet, which checks out as a branch tracking it. The one you are on is marked and cannot be picked again. It runs `switch` rather than `checkout`, so a branch name that also happens to be a path can never turn the click into a file operation, and a new name is refused before it reaches git if it starts with a dash or carries a space, then refused by `check-ref-format` if git would not have it either.

Switching branches rewrites the files underneath whoever is working in them, and the panel knows something the editor never did: whether a session is mid-turn in that folder. So a switch under a **working** session asks first. Everything else is git's own refusal, passed through as it stands — uncommitted work that would be overwritten, or a branch already checked out in another worktree.

**The drift counts are buttons too**, and they are filled rather than quiet, because an unpushed commit is something to do and a transparent arrow in a row of transparent arrows reads as one more fact about the repository. `↑2 to push` pushes those two commits; `↓1 to pull` pulls that one. Push takes the amber this panel already uses for a session that needs you; pull takes the primary tone, being work arriving rather than work owed. The arrow you are looking at when you think "push that" is the arrow you press, and its tooltip names the upstream it means.

**The interface is VS Code's Source Control view, deliberately.** Not as flattery: it is the one arrangement of these controls that everybody who would open this panel already knows, so nothing about staging a file needs explaining. The message box sits at the top with a split button under it — **Commit**, and an arrow holding *Commit & push*, *Commit & sync* and *Commit (amend)*. Each group header carries the actions that apply to the whole group, each row the ones that apply to it, and the row itself opens that file's diff in place. With nothing staged the button says **Commit all 3** rather than quietly committing something else, which is the same offer the editor makes and the same answer, said earlier. `Ctrl+Enter` in the box commits, as it does there.

**The sparkle in the corner of the message box writes the message for you.** It runs a headless `claude --print` in the repository with the diff that is about to be committed on stdin — the index if anything is staged, the whole working tree otherwise, so the message describes the commit the button would actually make — along with the last ten commit subjects, so what comes back looks like the messages already in that history rather than a house style from somewhere else. It goes to Haiku, because a commit message is a small closed job and a cheap one; it is given no tools, so there is no permission prompt to answer and nothing it can do to the tree; and it takes ten or twenty seconds, which the button says by pulsing while it waits.

The message lands in the box rather than in a commit — reading it before pressing Commit is the point. With something already typed it asks before replacing it. Nothing about this touches the session working in that folder: no message goes down its socket, and its conversation is left alone, so a stopped session's repository can have a message written for it just as well as a live one's.

**And it does not appear in the list.** A headless run writes a session file like any other, so the panel would otherwise show a row that arrives, says nothing and vanishes twenty seconds later — for an errand the panel itself asked for and is about to discard. Its pid is held in a set for exactly as long as the process lives, and the reader skips it. Only the panel's own errands: a headless `claude` you started yourself still shows up, because that one is a session you might want to watch.

Clicking a row opens its diff below it — the staged side of a file for a row in *Staged changes*, the unstaged side for one in *Changes*, so a file changed twice over shows each half where that half lives. An untracked file has nothing to diff against, so it is read from disk and shown as the all-added patch it amounts to. Binary files say so instead.

The commit graph is a **second tab** rather than the foot of the first. History is much the longer of the two, and putting it below the file list buried the thing you check most often above a graph you had to scroll past. Both tabs are drawn from one reading of the repository, so switching between them costs nothing, and both open with the same branch header — neither is worth much without knowing which branch it is describing.

Both tabs only appear for a session whose folder is inside a repository. That is found by walking up from the session's working folder looking for `.git`, which also picks up worktrees and submodules, where `.git` is a file rather than a directory. Everything git then runs against that repository root, not the session's folder, so a session sitting three directories down still reports the whole tree.

Each row reads name first, then the folder it sits in, then one letter for what happened to it — `M`, `A`, `D`, `R`, `U` for untracked, `!` for a conflict — and the letter is the one that belongs to that row's own group rather than both of git's at once. A file modified in the index and again in the tree therefore appears twice, once per side, which is the truth about what would be committed. A rename shows where it came from.

**The graph is drawn, not scraped.** `git log` is asked for each commit's parents, and the lanes are laid out in the browser from the real ancestry: a commit takes the lane that was waiting for it, hands that lane to its first parent, and a merge's remaining parents open lanes of their own. Each row's rail is its own small SVG the exact height of the row, which is what lets the lanes meet across the joins. Lane colours come from the same generated scheme as everything else, so the graph follows your base colour.

> **Reading a repository never disturbs the session working in it.** Every command runs with `--no-optional-locks`, takes no lock and leaves no `index.lock` behind, and is passed as a list of arguments — never a shell string. The branch in the header and in the list is still read straight out of `.git/HEAD` without a subprocess, because that one runs for every session on every poll; the heavier read behind these two tabs happens only for the session you are looking at, and no more than once every twenty seconds. A read of a real 42-commit repository measured 34ms.

**It is deliberately slow, and it stops when you look away.** A working tree does not change on its own — you or a session changes it — and everything the panel itself does re-reads immediately: staging, committing, switching branch, opening the tab, selecting another session. So that twenty seconds only governs how long a change made *somewhere else* takes to appear, which is worth much less than running git against your repository every couple of seconds all day. While the page is hidden — another tab, another window — it reads nothing at all, and reads once the moment you come back. Measured with a wrapper counting real `git` invocations: four commands per read (`status`, `log`, `stash list`, `for-each-ref`), one read every twenty seconds while you are watching, none across twenty-five seconds hidden, and an immediate re-read after an action rather than a wait.

### What it can do to a repository

| Action | What runs |
| --- | --- |
| Stage / unstage a file or a whole group | `add --`, `reset -q HEAD --` — or `rm --cached` before the first commit, where there is no HEAD to reset to |
| Discard changes | `restore --worktree --` for a tracked file; `clean -f --` for one that was never committed, because deleting it is the only way to discard it |
| Commit, commit all, amend | `commit -m`, with `add -A` first when nothing was staged, `--amend --no-edit` when the box is empty |
| Push, publish, pull, fetch, sync | `push`, `push --set-upstream` for a branch that has none, `pull --ff-only`, `fetch --prune`; sync pulls and then pushes |
| Switch branch, start one | `switch <branch>`, `switch --track <remote>/<branch>` for a remote-only one, `switch --create <name> [<start>]` |
| Stash, restore the latest stash | `stash push --include-untracked`, `stash pop` |
| Write the commit message | `claude --print --model haiku --allowed-tools ""`, the diff and the recent subjects on stdin |

**Writing is gated exactly the way sending a message is.** Both change something on this machine, so both need the panel bound to loopback; served to the network or started with `--no-send`, the Git tab says **read-only** in its header, drops every button, and the endpoint answers 403. Reading is untouched by that — diffs still open.

**A path from the browser is never trusted.** Every action names files, and the panel keeps only the paths git is *currently* reporting as changed. Absolute paths, `..`, and anything else outside this working tree are ruled out by construction — git is asked what changed and the request is filtered against that answer — rather than by pattern-matching for the shapes of them one at a time, and the repository is always the one the panel already discovered for that session — never anything the request asks for. Arguments are a list, never a shell string, and a path only ever lands after `--`.

**Nothing here opens a prompt you cannot answer.** Writes run with no terminal prompt, no askpass helper and no editor, so a push that needs a passphrase nobody can type fails with git's own message in a snackbar instead of hanging. Network commands get 180 seconds, local ones 25. The panel's own writes are serialised, so two clicks cannot race each other for `index.lock` — a session racing you for it is git's lock to report, and it does.

**The panel does not choose for you.** A pull that cannot fast-forward, a merge, a rebase, a force-push without a lease: none of them happen here. `pull --ff-only` refuses and says the branch has diverged, because deciding between a merge and a rebase under a session that is editing the same tree belongs to whoever can see the conflict. Discarding is the one action that cannot be undone with git, so it is the one action that asks first — and it says *delete* rather than *discard* for a file that was never committed, because that is what happens to it.

## Naming a session

A session arrives with the name Claude Code gives it, which says nothing about what you are using it for. Click the name in the detail header — not the one in the list — and it turns into a field: type a name and press Enter, or just click away. Escape leaves it alone, and clearing the field puts the session's own name back.

The name you type shows in the list as well and is kept in `~/.config/claude-watchtower/names.json`, keyed by session id, so it survives a restart of both the panel and the session. Window matching still goes by the session's own name — a name you typed here means nothing to a window title.

## Focusing a window

Claude Code sessions do not know which window they live in, so the panel works it out: it walks the session's parent-process chain and looks for a window whose `_NET_WM_PID` sits on that chain, corroborating with the window title — the session's folder, its full path as a terminal writes it (`~/work/thing`), or the name Claude Code gave it. The detail pane labels the result **matched**, **best guess**, **confirmed**, **paired by you**, or **can't tell yet**.

**A pid is not always a discriminator.** GNOME Terminal — and every other terminal with a server process behind it — reports the *same* `_NET_WM_PID` for every one of its windows. Each of them then sits on the chain and scores alike, and picking the first is a coin flip wearing the word "likely". When the leaders tie, the panel says **can't tell yet** and stops rather than raising somebody else's terminal.

**Identifying asks the terminal instead of guessing.** Two tabs of one terminal share a process, but never a pty: the session's is read from field 7 of `/proc/<pid>/stat`. Click **Identify window** — or just click **Focus window**, which identifies first when it has to — and the panel writes an OSC title sequence to that pty. That is output, the way any program's output is, and the terminal answers by retitling the window showing it. Whichever window comes back wearing the marker is the one. The marker is pushed and popped on the xterm title stack, so the title the session had is put back exactly, including one Claude Code rewrites as it works. You see a flicker and nothing else.

It runs when you ask for it, never on a poll, and the answer is written to `pairs.json` like a pairing you made by clicking — so a session is identified once, not every second.

Two things it cannot do: a session in a **background tab** does not drive its window's title, so the probe finds nothing and says so; and a session with no pty at all — one behind an ssh hop, say — has nothing to write to.

When the answer is wrong or missing either way, click **pick another** / **Pair window** and then click the real window. The pairing persists in `~/.config/claude-watchtower/pairs.json` as `{"id": "0x…", "how": "picked"}` — `how` is what keeps the panel from telling you that you chose a window the probe found — and survives restarts. A file written before that distinction holds bare window ids, and those read as picked. It is dropped automatically if that window disappears.

Two honest limits:

* **Focusing needs X11 and `xdotool`** (`sudo apt install xdotool`). Your session is X11, so this works. Under Wayland, `xdotool` cannot activate windows and the buttons switch themselves off.
* **A terminal tab cannot be raised, only its window.** GNOME Terminal exposes no way to select a tab from outside, and the same is true of a VS Code integrated terminal — the panel raises the VS Code window, then you pick the terminal yourself. Sessions in their own window jump exactly where you want.

## Ending a session

**End session**, at the bottom of the Details tab, closes a session you no longer want running. It asks first — naming the session, its folder and its pid, and saying so plainly when the session is mid-turn, because ending it there drops whatever it was doing.

Confirming sends `SIGTERM`, the same signal `Ctrl-C`-ing the process would, so Claude Code shuts itself down and writes out its transcript. **Force quit** in the same dialog sends `SIGKILL` instead, for a session too wedged to answer; nothing is flushed. Either way the transcript already on disk is left alone — only the process goes.

The pid recorded in the session file is re-checked against `/proc` immediately before signalling, so a stale panel can never kill an unrelated process that inherited the number. The ended session shows as **closed** for a few seconds, then leaves the list.

## Keeping a session after it closes

A row normally lives and dies with its process. Turn on **Keep in the dashboard** — from the right-click menu, or the switch in the Details tab — and the row stays after the session is gone: same name, same folder, same conversation, marked **stopped** and pinned with a small marker in the list. Nothing about the session is copied; the transcript is read from where Claude Code already keeps it.

**Start it up** in the header runs `claude --resume <session id>` in that folder, in a new terminal window, and the session picks the conversation up where it left off. You can skip the button: type into the composer of a stopped session and it starts up *and* delivers what you wrote, as soon as the new process is listening (the panel waits for its socket in the background, up to 90 seconds).

The terminal is whichever of Ghostty, WezTerm, kitty, Alacritty, Konsole, GNOME Terminal, Xfce Terminal, `x-terminal-emulator` or xterm it finds first. Override it with `CLAUDE_WATCHTOWER_TERMINAL`, giving the terminal and the flag that takes a command:

```bash
CLAUDE_WATCHTOWER_TERMINAL="kitty --"
```

Starting a session runs a command on this machine, so it sits behind the same loopback gate as sending a message: off unless the panel is bound to a loopback address. What is kept lives in `~/.config/claude-watchtower/sticky.json`.

## Design

The interface follows Material Design 3. Nothing about it is hand-picked colour:

**Dynamic colour.** The whole palette is generated in the browser from a single seed by `@material/material-color-utilities` — the same library Material uses — and written to the document as `--md-sys-color-*` custom properties. Open **Settings** (the gear in the app bar) to change the base colour from a preset or any colour at all; every surface, container, outline, and state colour is derived from it and the choice is remembered. The scheme uses the `SchemeVibrant` variant, which keeps the seed's hue with strong accents while leaving containers at the pastel tones that guarantee contrast against their `on-` roles.

Settings also exposes MD3's three **contrast levels** (standard, medium, high), which widen the tonal distance between paired roles for legibility.

**State colours.** Each session state gets a legal MD3 role pair — the container tone fills the avatar and the detail header, the matching `on-` tone draws every glyph on it, so contrast is guaranteed by construction. `working` uses the scheme's own primary, so it always matches your base colour. `waiting`, `running`, and `ready` are extended custom colours with semantic base hues (warm, teal, indigo) that are nudged to the nearest hue keeping at least 35° from the primary and from each other. That is why no two states ever look alike, whichever base colour you pick.

**Typography** is Roboto, MD3's typeface, self-hosted in `static/fonts` with the baseline type scale as `--md-sys-typescale-*` tokens. Shapes come from the shape scale (chips small/8dp, chat bubbles large/16dp, list rows and buttons full, dialog extra-large/28dp) and elevation is expressed as container tone rather than shadow, with shadows reserved for the scrolled app bar, the dialog, and the snackbar.

Components used: top app bar, navigation-drawer style list items, filter chips, primary tabs, filled/tonal/text/outlined buttons, icon button, switch, segmented button, dialog, divider, snackbar, state layers and ripples. Everything is one static HTML file — no build step, no network at runtime.

## Options

```bash
python3 server.py --port 8787 --host 127.0.0.1 [--no-send]
```

> **`--host 0.0.0.0` exposes the panel to your network.** There is no authentication and the focus endpoint moves windows on this machine, so only do that on a network you trust. Sending input switches itself off on any non-loopback bind; `--no-send` switches it off on loopback as well.

The URL can pin appearance for one load, which is handy for a second monitor or a wall display:

| Query | Values |
|---|---|
| `?theme=` | `dark`, `light` |
| `?seed=` | a URL-encoded hex colour, e.g. `%23E8288F` |
| `?contrast=` | `standard`, `medium`, `high` |

Without them the panel uses your saved settings, defaulting to your system light/dark preference.

To preview all four states without waiting for real sessions to reach them, point the panel at a fixture directory:

```bash
CLAUDE_WATCHTOWER_SESSION_DIR=/path/to/fixtures python3 server.py --port 8788
```

## Keeping it running

Install the bundled user service so the panel starts with your session:

```bash
mkdir -p ~/.config/systemd/user
cp claude-watchtower.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-watchtower
```

`systemctl --user status claude-watchtower` to check it, `journalctl --user -u claude-watchtower` for logs.

## Repository layout

| Path | What's inside |
|---|---|
| `server.py` | Session discovery, window matching, JSON API |
| `static/index.html` | The panel — Material 3, one file, no build step |
| `static/fonts/` | Roboto and Roboto Mono, self-hosted |
| `static/vendor/` | `material-color-utilities`, for dynamic colour |
| `tests/ui-check.mjs` | UI checks over CDP (tokens, contrast, settings) |
| `claude-watchtower.service` | Optional systemd user unit |

## Tests

1. Start a panel — a fixture directory shows every state at once:

```bash
CLAUDE_WATCHTOWER_SESSION_DIR=/path/to/fixtures python3 server.py --port 8788
```

2. Start a throwaway browser with CDP open:

```bash
google-chrome --headless=new --remote-debugging-port=9333 \
  --user-data-dir=$(mktemp -d) about:blank
```

3. Run the checks:

```bash
node tests/ui-check.mjs
```

`tests/ui-check.mjs` drives a throwaway headless Chrome over the DevTools protocol and asserts the things a screenshot cannot: that every MD3 token resolves, that the four state containers are distinct and stay distinct after the base colour changes, that every piece of text on screen clears 4.5:1, that the index lists each session with a host icon and a state lamp, that clicking a row opens its detail and every tab renders, that the filter chips filter, that sessions sharing a folder group themselves and picked rows can be grouped, folded and ungrouped by hand, that the settings dialog changes the scheme and persists it, and that interactive targets reach 48dp. Node 24+, no dependencies. Override `PANEL_URL` / `CDP_URL` to point elsewhere.

The Git checks want a session whose folder is in a repository: they find one from `/api/state`, then assert that both tabs appear, that Git reads the branch and marks every file with its status letter and a way to open it while carrying no graph, that the files land in the editor's three groups, that the commit box and its split button are there when writing is on — and that a read-only panel says so and offers nothing — that a row opens exactly one diff and closes it again, that History draws one node per commit and keeps no file list, that each rail is the same height as its row — a mismatch there is what leaves the lanes broken at every join — that both clear 4.5:1, and that all four tabs stay reachable at 48dp. With no such fixture they say so and skip rather than failing for a reason that has nothing to do with the panel.

**No check stages or commits anything.** The suite runs against whatever real sessions are on the machine, and a test that commits in somebody's checkout to prove a button works has done more than it was asked. It asserts that the controls are there and wired; the one action it actually performs is opening a diff, which only reads.

Point it at a real panel to measure the chat bubbles too, since fixture sessions have no transcript:

```bash
PANEL_URL=http://127.0.0.1:8787 node tests/ui-check.mjs
```

## API

| Route | Purpose |
|---|---|
| `GET /api/state` | Every live session, with status, trace, and window match |
| `GET /api/transcript` | `?sessionId=…&limit=…` — the recent conversation |
| `GET /api/git` | `?sessionId=…` — that session's repository: branch, upstream drift, changed files, recent commits with their parents, and the branches it could switch to |
| `GET /api/git/diff` | `?sessionId=…&path=…&staged=1` — one changed file's unified diff, one side at a time |
| `POST /api/git` | `{"sessionId": "...", "action": "...", …}` — one source-control action: `stage`, `unstage`, `discard` (each with `paths`), `stageAll`, `unstageAll`, `discardAll`, `commit` (`message`, `amend`, `stageAll`), `push` (`force` uses a lease), `pull`, `fetch`, `sync`, `stash`, `stashPop`, `switch` (`branch`, `create`, `from`), `suggestMessage` (answers with `text`, the message it wrote); loopback only |
| `POST /api/focus` | `{"sessionId": "..."}` — raise that session's window |
| `POST /api/identify` | Ask a session's terminal which window it is showing, and remember it |
| `POST /api/pair` | Click a window to bind it to a session |
| `POST /api/unpair` | Forget a manual pairing |
| `POST /api/sticky` | `{"sessionId": "...", "sticky": true}` — keep this session's row after its process goes |
| `POST /api/start` | `{"sessionId": "...", "text": "..."}` — resume a kept session in a terminal, delivering `text` once it listens; loopback only |
| `POST /api/rename` | `{"sessionId": "...", "name": "..."}` — name a session yourself; an empty name puts its own name back |
| `POST /api/end` | `{"sessionId": "...", "force": false}` — SIGTERM that session, or SIGKILL when `force` |
| `POST /api/say` | `{"sessionId": "...", "text": "..."}` — send a message into that session; loopback only |

A dead process is never reported: each session file records the pid's start time, and the panel re-checks it against `/proc` so a recycled pid cannot masquerade as a live session.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

<br />
<p align="center">
  <a href="https://github.com/coencoensmeets/claude-watchtower">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/assets/claude-watchtower-transparent-dark.svg">
      <img src="docs/assets/claude-watchtower-transparent.svg" alt="Logo" height="170">
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

Python standard library only, no packages to install, nothing leaves the machine.

The panel's frontend is TypeScript, so it is built before it is served — but there is still nothing to install for it. Node strips TypeScript types itself, so the build needs a Node binary and no npm packages at all, and `python3 server.py` runs it for you when anything under `web/` has changed. If Node is not on the machine and cannot be put there, `python3 -m venv .venv && .venv/bin/pip install nodejs-wheel-binaries` puts one in the project instead.

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

3. Open the address it prints. The panel picks a port for this install on its first run and stays on it from then on, so the address is worth a bookmark — see [Its own port](#its-own-port). Every Claude Code session on this machine is already in the list: there is nothing to configure and nothing to install.

4. It prints a second address, the one your phone can reach. Open **Settings** and point a camera at the code, or type that address in once. See [From your phone](#from-your-phone), and `--local` if you would rather it served this machine only.

5. Optional: `sudo apt install xdotool` to enable **Focus window** under X11, and see [Keeping it running](#keeping-it-running) for the bundled systemd user unit.

## Documentation

| Section | What's inside |
|---|---|
| [What it shows](#what-it-shows) | The four states, and why the one you care about is inferred rather than read |
| [Layout](#layout-an-index-and-a-detail-pane) | The index, groups, dragging the rows into order, the detail pane and its five tabs |
| [From your phone](#from-your-phone) | The code to scan, the key that guards it, and the port it keeps |
| [Sending a message](#sending-a-message) | The composer, the unix socket behind it, and its three honest limits |
| [Pasting a picture](#pasting-a-picture) | A screenshot pasted into the box, saved into the session's folder and sent as a path |
| [Dropping a file](#dropping-a-file) | A file dragged onto the box types its path — or is saved and named, when the drag has no path to give |
| [The question it is asking](#the-question-it-is-asking) | The multiple-choice question a blocked session is standing at, read from the panel |
| [Opening a session](#opening-a-session) | The native folder picker, and placing the folder it returns |
| [Changes to files](#changes-to-files-in-the-conversation) | The patch a message carries, folded to a few lines, and the whole of it on a click |
| [Commenting on a passage](#commenting-on-a-passage) | Select what it said, and say what you think of it |
| [Permission mode](#permission-mode) | How much rope a session has, when it was true, and why it is read-only |
| [Git and History](#git-and-history) | Staging, committing and pushing in the editor's own Source Control layout, and a commit graph drawn from real ancestry |
| [Usage and cost](#usage-and-cost) | What a session has spent in tokens, and what that comes to at list price |
| [How much of your plan has gone](#how-much-of-your-plan-has-gone) | The subscription's session and weekly limits, in the app bar |
| [Updating the panel](#updating-the-panel) | The release tags on its own repository, the one button that moves to the newest, and what it warns you it will stop |
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

Every session, one row each, sorted so anything waiting on you is at the top. State decides the band a row sits in and nothing else does: inside a band the order is when the session started, then its id, so a row moves only when its state visibly changes and comes back to the same slot afterwards. A session that blinks busy for a second does not shuffle the list under your pointer, and two sessions never swap places on the strength of the order they happened to be found in. A folder or a group of your own sits where its most pressing member would have been, while the rows inside it keep that same fixed order. That is the order until you say otherwise, and [dragging a row](#putting-the-rows-in-order) is how you say it. Each row carries a round avatar whose *fill* is the session's state colour and whose *icon* is where the session lives — `</>` for VS Code, a terminal glyph for GNOME Terminal, Konsole, kitty, Alacritty, WezTerm, Ghostty or xterm, split panes for tmux or screen, a globe for a session over SSH. A lamp on the corner of the avatar repeats the state and animates: a slow pulse while working, a blink while waiting. Under the name you get what the session is working on, then the state and the folder, and on the right how long it has been in that state.

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

### Putting the rows in order

The state sort is a guess about what you want to look at, and a good one, but it is still a guess: the session you are shepherding today is not always the one shouting loudest. So **drag a row where you want it**. From the first drag the order is yours, and a session going busy and idle again no longer moves it. Every row carries a grip on its right, faint until the pointer is on it, and **alt with the up or down arrow** does the same move from the keyboard.

| Gesture | Does |
|---|---|
| **Drag** a row | Put it where you dropped it |
| **Alt** with **up** or **down** on a focused row | The same move, one step at a time |
| **Sort by state again**, on any row's menu | Hands the order back to the panel |

A row only ever lands in the list it came out of. Dropping one into a group would have to mean joining that group, which is its own item in the menu, and a drag is too easy to do by accident to be the way you discover it — so at the top level a group counts as one block and a row dragged past it goes past the whole thing. Inside a group the members reorder among themselves.

A session started since you last dragged anything sits above the arrangement rather than at the bottom of it: it is the new thing on the list, and the point of the state sort was that new work is what you look at first. It takes its place the next time you drag. **Sort by state again**, on any row's right-click menu, forgets the arrangement and hands the order back to the panel. Like the grouping, it is a view of the list and lives in `localStorage`.

### Right: the detail

Click a row and the pane shows that session in full: its name, state and how long, the AI-written title of the conversation if there is one, the host, folder, branch, uptime and permission mode, and a **Focus window** button. Below that, the tabs:

| Tab | What's inside |
|---|---|
| **Conversation** | The recent transcript, as speech bubbles, plus the composer — and the question it is standing at, when it is standing at one |
| **Git** | What the session has done to the working tree |
| **History** | The same repository's recent commits, drawn as a graph |
| **Usage** | Tokens and cost, per model, and how full the context is |
| **Details** | Window pairing, notification switch, the facts, and **End session** |

**Conversation.** Your prompts and Claude's replies are speech bubbles; turns that only ran tools are quiet single rows with the tool name and its target, so the actual conversation stays readable. It is read from the end backwards and stops at a page of messages, which is why a five-megabyte transcript opens as fast as a fresh one — and why the page is a page of *conversation*: a fixed tail of a working session is nearly all tool output, so it would show Claude's last twenty turns and none of your own messages. The bubbles render Markdown — headings, lists, tables, quotes, emphasis, links and code blocks — because an answer read as raw `###` and `**` is hard work. Nothing from a message reaches the page as markup: code is held aside first, every other scrap is escaped before a tag is added, links are only made for http, https and mailto, and an image becomes a link rather than a remote fetch. It follows the live session, and stays pinned to the newest message unless you scroll up. A long answer buries what you asked for, so a **Last request** pill floats over the corner whenever your most recent message is off screen; it scrolls back to it and its arrow points the way it will travel. Beside it, a round **jump to latest** button fades in once the end of the transcript is properly out of sight and takes you back down to it in one move. Selecting any passage offers to quote it into the composer — see [Commenting on a passage](#commenting-on-a-passage).

**Pictures in a message are shown.** A screenshot Claude took, a plot it wrote, a picture you pasted into the box — named as `![alt](/path/to/it.png)`, as an `<img src="…">`, or as the `[Pasted image: …]` line the composer writes — is drawn in the conversation rather than described in it. Clicking one opens the file itself, where the browser's own picture view does the zooming.

The panel serves it back through `GET /api/file`, and the fence around that route is the same one `/api/editor` uses with one addition: the path must already exist, must be a picture (png, jpeg, gif, webp, avif, bmp, svg), and must resolve inside your home folder, the session's own folder, or the system temp folder — which is there because `/tmp/plot.png` is how a screenshot usually arrives. Anything else is refused and the message shows the alt text, which is the file's name when it was not given one. A remote picture is deliberately *not* fetched: an `https://` image stays the link it always was, because drawing a conversation should not make requests to somebody else's server. And an `<img>` tag is never passed through — it is read for its `src` and `alt` and a new one is built from those, so a handler or a style written into the tag is gone by construction rather than by sanitising.

**Maths is drawn as maths.** Claude writes TeX the way it writes anything else — `$S$` in a sentence, `$$\frac{\partial a}{\partial q}$$` on its own line — and read raw a matrix is a paragraph of backslashes. The panel renders it: fractions, roots, scripts, matrices and cases, delimiters that stretch to what they hold, accents, spacing, the greek alphabet, the operators and arrows, `\text` and its family, `\mathbb`, `\underbrace`, sums and integrals with their limits.

It is rendered to **MathML**, and by a converter written for this panel — about four hundred lines in `web/src/ui/math.ts`. Both halves of that are deliberate. MathML is what the browser already draws: nothing is downloaded, no fonts are shipped, and an equation reflows on a phone and scales with the page rather than being a picture. And the alternative was KaTeX, which is 280KB of script and about a megabyte of fonts against a panel whose entire assets folder is 156KB — a dependency ten times the size of the thing it is added to, fetched at install time by a project whose promise is that `python3 server.py` is the only command anyone types. What the converter does not understand it hands back as the TeX that was written, rather than mangling it; a message is worth more than the maths in it.

Two details worth knowing. A display looks best where a maths font is installed — the browser builds a tall bracket out of pieces that only a maths font carries, and most machines have one (`DejaVu Math TeX Gyre`, `Noto Sans Math`, `Cambria Math`, `STIX`); without one it is still legible, just plainer. And a `$` in prose is left alone: the pair has to be on one line, must not open or close against a space, and must not sit against a word character — which tells `$S$ is the Jacobian` from `$PATH:$HOME`, and leaves `it costs $5 or $6` as it was written.

**Paths in a message open.** A path written into the conversation is a place on this machine, so clicking one opens it: a file goes to VS Code at the line it was quoted at (`web/src/main.ts:1560`), and a folder goes to whatever this desktop uses to look at folders. Not the editor, for a folder — a path in a conversation is as often somewhere things are *kept* as somewhere code is written, and an editor pointed at a build directory gives you a sidebar of a thousand artefacts. The tool lines above a turn are the same, so the file an `Edit` touched is one click from the panel. What counts as a path is deliberately narrow, because every line of every message goes through it: something rooted (`/tmp/out.log`, `~/.motorcortex/build/…`, `./web`), or something with a folder and a suffix (`docs/cleanup-plan.md`) — and inside code marks, a bare file name with a suffix the panel knows (`server.py:44`). Which leaves `and/or`, `I/O`, `km/h`, `Array.from` and a URL alone. The panel will only open what already exists inside your home folder or the session's own, and says so when it will not; it is the same loopback-only route as the header's **Open in VS Code** button, and it goes away with the same setting.

**Git** and **History** are described under [Git and History](#git-and-history), **Usage** under [Usage and cost](#usage-and-cost). **Details** carries window pairing, a per-session switch for whether it may raise a desktop notification when it starts waiting, the facts (pid, Claude Code version, session id, start time, where the transcript lives), and **End session**.

Below 900px the two panes become one at a time, with a back arrow in the app bar.

## Sending a message

Below the transcript is a **composer**: type and the message goes into that session, no keystroke faking and no window stealing involved. Claude Code listens on a per-session unix socket — the path is in the session file, the socket is `0600`, and the panel writes two lines of JSON to it. Enter sends, Shift-Enter starts a newline.

Three honest limits, each stated on the composer itself rather than left to be discovered:

| Limit | Why |
|---|---|
| **A session blocked on a prompt cannot be answered this way** | A permission dialog or a question is modal in the terminal; a queued message waits behind it. Since "needs an answer" is exactly what this panel sorts to the top, the composer switches itself off there and points at the prompt that has to be answered — naming the question on the card above when there is one. It is the one case you might most want and cannot have. |
| **A message arrives labelled as coming from another session**, not as something you typed | Measured, not assumed: a session that received one reported it appeared "clearly marked as coming from another session rather than from Coen". So the receiving Claude may treat it with a peer's authority rather than yours, and will not take it as approval for anything. |
| **The protocol is internal** | The session file records `peerProtocol`, and the panel refuses to send unless it reads the version it knows, going quietly read-only after an upgrade rather than writing malformed lines at a socket. |

A message sent to a session that is mid-turn is queued at its prompt, exactly as if you had typed ahead in the terminal, and lands when the turn ends.

**It then appears in the conversation above, marked with where it came from** — `you · from here` for something typed into this composer, and the sending session's name for a message from another Claude. That takes some digging out: Claude Code never writes such a message down as a turn of its own. It records the envelope it wraps it in (`<cross-session-message …>`) on the queue and hands the body to the model as an attachment, so read at face value the transcript shows Claude answering something nobody said. The panel therefore reads the queue entries too and unwraps them, and skips the duplicate the queue leaves behind when the message comes back off it.

> **Off this machine, sending needs the key.** A prompt is an instruction to an agent holding tools and a checkout, so the composer used to disappear on any non-loopback bind. It no longer has to: a panel on the network answers nobody who cannot show the key it printed, which is a tighter gate than the bind ever was — see [From your phone](#from-your-phone). `--no-send` switches sending off everywhere, and `--no-key` trades the key for a read-only panel.

### Pasting a picture

**Ctrl-V a screenshot into the composer and it goes with the message.** Not in it — as a path.

Every transport the panel has takes a string: the messaging socket takes one, a held pipe takes one, and neither has anywhere to put a PNG. So the picture is written to a file and the message names the file, which is a thing any session can act on: it opens it with the tool it opens any file with. What goes out is what you typed, then a line per picture:

```
what is wrong with this dialog?

[Pasted image: /home/you/project/.claude/watchtower-images/paste-20260822-141233-a1b2c3.png]
```

The file lands in the session's **own folder**, under `.claude/watchtower-images/`. Not `/tmp`, which is swept out from under a conversation you come back to tomorrow, and not the panel's config directory, which a session may have no permission to read: it goes somewhere the session already reads from. Pictures older than a fortnight are swept by the next paste, so the folder does not grow for the life of the machine.

That folder is inside your repository, so **it is worth ignoring**: a screenshot pasted to ask a question about it is scratch for one message and has no business in a commit. This repository ignores `.claude/watchtower-images/` and `.claude/watchtower-files/` — the same two lines are worth adding to any repository you use the panel in. The panel does not add them for you: writing to a `.gitignore` it was never asked to touch is not something a panel should do behind your back.

The upload starts on the paste rather than on Send, so the round trip happens while you are still writing the sentence about it. What is drawn above the box is the file as it stands — a thumbnail, and either the name it was saved as, `Saving…`, or why not — with **Remove** on each to leave one out. Sending waits for anything still saving rather than sending a path to a file that is not there yet, and a picture with nothing typed beside it is a message in its own right.

Only what a browser puts on the clipboard as a picture is taken over: PNG, JPEG, GIF, WebP and BMP, up to 12 MB. A pasted picture the panel does not recognise is refused before it is uploaded, and the extension is decided by its type rather than by any name in the request. Pasting text is left entirely to the browser.

> **Same gate as sending**, and for the same reason turned up a notch: this writes a file into a checkout. Loopback only, and off entirely under `--no-send`.

### Dropping a file

**Drag a file onto the composer and its path is typed in at the caret.** Nothing is uploaded and nothing is copied: the session is already sitting in a folder with that file in it, so the shortest true thing to say is where it is, and it reads it with the tool it reads any file with. The path lands where you were typing, spaced off the words either side, and you write the sentence around it — drop several and they go in as a list, and a path with a space in it is quoted the way a terminal would take it.

Which is why this is not the paste route. A screenshot on the clipboard has no path — it exists nowhere until the panel writes it — so pasting has to save it first. A dropped file has a path already, and copying it into `.claude/` to get one would leave a second copy of something that was never in doubt.

The path comes off the drag, not off the file: a browser hands over a dropped file's name and bytes and never its path, but a drag out of a file manager usually carries the `file://` URI beside it, and that is what is read. When it is there, the real file is named and nothing is copied. A file on another host (`file://nas/share/x`) is not turned into a local path, and a drag of plain text is left entirely to the browser, which puts it in better than this would.

**And plenty of drags have no path to give** — enough of them that this is the half that had better work. A file dragged out of Chrome's downloads is the ordinary case — the drag carries the bytes, and its `text/uri-list` is the address the file was fetched from rather than where it landed — and the same goes for a mail attachment or an image dragged off a page. There is nothing to name, so those take the route a pasted screenshot takes: the file is written into the session's folder, under `.claude/watchtower-files/`, and the message names the copy. It waits above the box while it saves, beside any pasted pictures, with **Remove** to leave it out.

It keeps the name it was dropped under, because `read quarterly.pdf` tells the session something that `read drop-a1b2c3.bin` does not — but only the name: the directory part of it goes, anything outside `A–Z a–z 0–9 . _ -` becomes a dash, a leading dot cannot make the copy invisible in its own folder, and a long name is shortened here rather than erroring at the disk. A second drop of the same name is a second file and never an overwrite, since something already in the conversation may be pointing at the first. Unlike a paste, the kind is not checked — a paste is held to five picture types because that is all a clipboard should be offering, where a dropped file is whatever you meant the session to read — and it is bounded at 32 MB, which is a download rather than a screenshot.

The one drop that fails is the drag that offers files and then has none, which the panel says rather than inventing a path for.

> **The path is the path on the machine running the browser.** Watch a session on another host and a dropped path means nothing to it — the panel has no way to tell, so it types what you dropped and leaves the reading of it to you.

## The question it is asking

"Needs an answer" says a session is blocked on you. It does not say what it wants, and until you know that you have to go and look — which is the trip the panel exists to save.

So when Claude asks a multiple-choice question, the panel shows the question. Above the composer: the header Claude gave it, the question itself, whether one answer is wanted or several, and every option with the sentence Claude wrote about it — numbered the way they are numbered at the prompt, so the card reads as a legend for what is on screen over there. The row in the index says `asks “Delete what”` beside its state, which is what tells two waiting sessions apart without opening either.

Nothing in the session file says a question is up. What the transcript has is the call — an `AskUserQuestion` tool use — and, once it has been answered, a tool result carrying the same id. Walking back from the newest line, a call whose result has not been seen yet is a question still on screen. The walk is skipped entirely while the transcript's mtime has not moved, so a session that has written nothing cannot have asked or answered anything and costs nothing to check.

A question a subagent asked is left out: it is not one you can answer.

> **The card is a card and not a form**, and that is the same limit the composer states. The only channel into a live session is its messaging socket, and that socket takes exactly one kind of message: a user turn, which lands in the prompt queue. The queue is *behind* the question — Claude Code is waiting on a keypress at its own prompt — so an answer sent from here would go unread until somebody answered at the terminal anyway, and then arrive afterwards as a stray message. Rather than offer a button that quietly does that, the card offers **Answer there**, which raises the window. Reading it is what saves the trip; the keypress still happens at the prompt.

## Opening a session

**New**, beside the filter chips at the head of the session list, starts a session without going through a row's menu first. A new session needs a folder, so the button offers the folders already on the list — one entry each, the panel reading a folder off a session it is already showing rather than taking a path from the browser.

**What it makes is an interactive session**, run by the panel: pick a folder and it is simply there, selected, with the caret in the box — no dialog, and nothing to fill in first. That takes naming it ourselves: `--session-id` accepts a uuid of the panel's choosing, so the row can exist before a word has been said. Without a name of its own a session announces nothing until it is sent something, which would have meant asking for the first message in a dialog before there was a session to type at.

*In a terminal instead* is still there, at the foot of the menu, and says what it costs: a session a terminal runs cannot have its mode picked here and cannot have its prompts answered here. It is the exception now rather than the only door.

Under those, **Another folder…** reaches anywhere on the disk, and still without being told a path: it opens your desktop's own folder chooser — `zenity`, `kdialog`, or the one Python's `tkinter` draws, whichever is installed — and starts the session where you picked. The browser asks for the dialog; what comes back out of it never passed through the browser at all, so `/api/new` keeps its rule that a folder comes off a session on screen and never off a request. Cancelling is an ordinary outcome and says so.

Both need the loopback gate that sending needs, for the same reason: they run a command on this machine. A desktop with none of the three choosers, or a panel with no display to draw on, says so on the menu item rather than offering a button that cannot work.

## Changes to files, in the conversation

A session that edits a file used to leave one line in the chat — `Edit  /home/you/project/server.py` — which says that something happened to a file and nothing whatever about what. **The change itself is shown**, under the tool line that made it: a few lines of patch, `+14 −3`, and the whole of it a click away.

It is not reconstructed from what the tool was asked to do. Claude Code writes a `structuredPatch` beside every Edit and Write result — real hunks against the real file, with the line numbers the file actually has — so the panel shows what was recorded rather than a diff rebuilt from an `old_string` and a `new_string`, which would have neither line numbers nor any guarantee of being what landed.

**Folded, and folded at the right place.** A hunk opens with its context, so a preview that starts at the top of the patch is a preview of the three lines that did *not* change, followed by a fade. The preview starts one line above the first line that actually changes. Green rows for what arrived, red for what went — the tint on the row rather than colour on the text, which is how an editor draws it and what keeps the text the colour text is. Click the bar or the patch itself and it opens whole; the count on the bar says how much more there is before you do.

The folded preview **clips** rather than scrolls, and that is a deliberate word. The diff a Git row opens is a scroller with `overscroll-behavior: contain`, which is right there and wrong in the middle of a conversation: a box that cannot scroll but has been told not to pass scrolling on is a dead patch of the chat — the wheel lands on it and nothing moves. Clipping puts the wheel back where it belongs, which is the conversation.

**Open, it takes the whole pane**, and shows two files side by side the way you would read a change in an editor or in Meld: before on the left in red, after on the right in green, each with its own line numbers, and the lines that answer each other on the same row.

Two files want the width of both, and the conversation is the one thing on screen that can lend it — a comparison squeezed into the column a message occupies is two narrow columns, which is worse than the one column it replaced. So reading a change is somewhere the pane *goes* rather than something that unfolds inside it: the bar carries the way back, Escape does the same, and the conversation is exactly where you left it when you come back to it. The session, its state, its composer and every other tab stay put — this stands in front of the conversation, not the panel.

With the pane to itself it takes the pane's width rather than the longest line's: two halves, and a long line wraps inside its half. The comparison scrolls one way only, because a comparison you have to scroll sideways to read is a comparison of two things you cannot see at once. Both cells of a row are cells of one grid row, so whatever wraps, the two sides stay level.

That pairing is the whole of the work. A unified patch is one column because it is a text format — the sign in the first character is the only room it has to say what happened — and on screen there is room for the thing the sign stands in for. So a run of removals and the run of additions that follows it are read as one edit written twice, and zipped: first removed against first added, second against second. Whichever run is longer leaves rows with nothing on the other side, washed flat rather than coloured, because the file really does not have a line there.

It is one grid rather than two scrollers, so the two sides cannot drift apart — there is nothing to keep in sync because there is only one thing. The hunk header stays a row across the full width, since the jump it marks is in the file rather than in either version of it.

> **The pane keeps your place.** It used to keep it only in the two cases it had been taught — you were at the bottom, or the conversation had just grown at the top — and every other rebuild landed at scroll position 0. Opening a change threw you to the very top of the conversation, and so did a poll arriving while you read back through it. Anything that is neither of those two cases now comes back exactly where it was, and the comparison is the one thing that deliberately does not: it opens at the top of the change, because that is where a change starts.

The [Git tab](#git-and-history)'s diff stays one column. It opens *inside* a list of files you are scanning, several at a time, where a second column costs more than it gives; the change in the chat opens because you asked for that one.

**Opening it is what fetches it.** The transcript is re-read on every poll while the chat tab is open, and carrying every patch in full through every one of those reads would be paying, once a second, for something read once. So the conversation carries a preview and a size, and `/api/change` answers with the whole patch for the one you clicked — its own walk back through the same transcript, stopped at the tool call you named.

A selection wins over the click, the same way [Ctrl+C](#stopping-a-turn) defers to it: dragging across the visible lines to quote them must not fold the thing you were reading out from under you.

> **A file changed by a shell command does not appear here.** `sed -i`, a heredoc, `git checkout`, a formatter — the tool that ran was Bash, and Bash records what it printed, not what it touched. There is nothing written down to show, and the panel does not go looking at the file to guess. [Git](#git-and-history) is where that shows up: it reads the working tree itself, so a change is a change however it was made.

What a subagent changed stays out, like the rest of a sidechain: it is not this conversation.

## Commenting on a passage

Most of what you want to say to a working session is about something it just said — a line to change, a claim to push back on, a paragraph to keep. Typing "the bit about the poll loop" and hoping it finds the bit is the long way round.

**Select a passage and a small bar rises over it offering Copy and Comment.** Copy puts the passage on the clipboard — the panel is the one place the transcript is readable without opening the terminal. Comment does what a document does: the passage is marked where it stands and a **card opens in the margin beside it**, with the caret in it, waiting for the remark.

Cards accumulate. Comment on a second passage and a second card opens under the first, level with its own passage, and neither pushes the other out of sight. When you are done, **Send *n* comments** delivers them as a single message:

```
> [you, 14:32]
> I refactored the poll loop to fire every second regardless of activity

make this configurable instead
```

Nothing else is added — no preamble explaining the panel, no instructions about what to do with it. The speaker and the time are what locate the passage; the remark says the rest.

**The attribution is written from the reader's point of view, not the panel's.** The panel says *claude* and *you* meaning the assistant and the person watching; both invert on the way over, so the session's own words are quoted as `[you, 14:32]` and yours as `[me, 14:32]`. A passage from another session keeps that session's name, which means the same thing at both ends. Getting this wrong is not cosmetic: `[claude, …]` reaching Claude reads as a third party, and `[you, …]` reads as the session itself.

The attribution is also what keeps two quotes apart when you send several at once — because you can. Select, comment, select, comment, then send: they arrive as one message rather than a burst the session queues separately. **A selection running across several messages** does the same thing in one gesture: it is split at the message boundaries into one attributed quote each, rather than refused for carrying one attribution over two speakers.

**Alt+C** takes the offer without reaching for the mouse. Not a bare letter, which would fire while you were typing, and not Ctrl, whose useful combinations are already the browser's.

### What comes back as what

| Selected | Arrives as |
|---|---|
| Prose in a bubble | A blockquote under its attribution |
| A passage inside a code block | The same, with the fence and its language put back, so it reaches the session as code with its indentation intact rather than as prose flattened into a blockquote |
| A **tool row** | The command, attributed like the session's own speech — because a tool call is the session's own doing, and the row is the only place it is written down. "This command was wrong" needs somewhere to attach. |
| More than 40 lines | Head and tail, with `… N lines not quoted …` counted out loud between them. A whole-answer selection would bury the remark under hundreds of lines, and a silent trim would misrepresent what you picked — the marker is in the composer, so you can edit it or take the passage again smaller. |

A passage half inside a code block and half outside is prose: quoting a sentence and the top of a function as code would be a worse guess than not guessing. Inline code mid-sentence stays inline, since fencing it would be heavier than the thing it quotes.

### Where the cards sit

Beside the passage, in a margin to the right of the transcript, each one level with what it is about and pushed down only as far as it must be to clear the card above. The margin is an overlay rather than a column, so the conversation's own layout is untouched — only its right padding changes while the rail is up. Scrolling moves the whole rail by a transform rather than re-laying-out every card.

**The threshold is measured on the detail pane, not the window.** The index takes 340-380px before the pane sees any of it, so a number picked as if it were a window width puts every ordinary laptop into the fallback and leaves a margin nobody ever sees — a 1440px window leaves the pane about 1060px. The rail appears from about 860px of pane, which a 1280px window clears. Below that there is no room for a margin, so the card becomes a popover over the transcript, anchored under its passage and clamped into the window; the pane becomes the whole window below 900px, where a fixed margin would squeeze the conversation exactly where it is tightest.

### Keeping your place

Having commented on a passage, it stays **marked in the transcript** — underlined rather than filled, so it never competes with the selection highlight, and so several of them can sit on screen at once by the time you have worked through a long answer. The mark is redrawn after every rebuild of the pane, since the transcript is built from the transcript data and anything drawn over it goes with the rebuild. Sent comments leave the rail but keep their marks: the rail is what is still outstanding, the marks are where you have been.

Two honest limits. It is drawn as a real element wrapped around a real text node rather than spliced into the HTML — that is what keeps the guarantee that nothing from a message ever reaches the page as markup — and the price is that **a passage crossing a bold run, a link or a code span is quoted correctly but goes unmarked**. And the marks are kept in memory only, not on the server and not in `localStorage`: they are a note about this sitting rather than a property of the session, and a mark outliving the conversation it referred to would be worse than no mark at all.

**The cards are where you write; the wire format is unchanged.** Each becomes an attributed quote with its remark under it, gathered in the order they appear in the conversation rather than the order you wrote them, and sent as one message. Nothing new reaches the socket — it is the same thing you would have typed, quoting the parts you meant. The composer below is untouched and still there for anything that is not about a particular passage.

Writing the remark next to the passage rather than in the composer is the whole point of the change: in a composer, a second quote pushed the first out of sight above what you were typing, so commenting on three things meant losing track of the first two. Enter finishes a card, Shift-Enter puts a newline in it, Escape abandons an empty one. **A card being typed in holds off the pane's rebuild**, the same guard a half-typed session name and a drag on the composer grip already get — the panel polls every second, which is exactly while you are writing.

A comment that cannot be sent is a note to nobody, so a session the panel cannot message says why at the moment you ask for the card rather than after you have written it.

Two things it deliberately will not do:

| It refuses | Why |
|---|---|
| **A passage scrolled out of the transcript** | The chip points at the passage. Pointing at something off screen puts it over the header instead. |
| **Anything outside the conversation** | Selecting the folder in the header is reading, not commenting. |

The chip follows the passage while the transcript scrolls, rather than dismissing on the first scroll event — the conversation stays pinned to the newest message while a session works, and losing the chip to a poll landing mid-gesture is the one failure that would make it useless. Escape puts it away.

**The highlight comes from the scheme rather than the browser's blue, and each bubble takes the other one's ground.** One colour could not do it: your messages are drawn in `primary-container` and Claude's sit barely off the surface, which are opposite ends of the tonal range, so measured against both, every single role in the scheme clears one and vanishes on the other at around 1.0–1.6:1. A highlight in `primary-container` looked right on Claude's messages and was invisible on your own — the ones you most often want to quote back. So a selection in Claude's bubble highlights in `primary-container` and one in yours highlights in `surface`; both are legal MD3 pairs, and both are measured in the tests against the bubble they actually land on.

## Permission mode

Among the facts in the detail header is a pill for how much rope the session has: **Manual**, **Plan**, **Accept edits**, **Auto**, **Bypass**, **Don't ask**. The last four are filled rather than outlined, because those are the modes where the session may act without coming back to you — the thing you want to spot across several sessions at once, and the one thing you cannot see without visiting every terminal in turn.

**It says when it was true, because it cannot always be current.** A session writes its mode into the transcript only when the metadata block is re-appended — on a resume, at exit, once the transcript has grown past a threshold — and never at the moment the mode changes. A session that is working therefore reports within seconds; one sitting at its prompt keeps reporting whatever it last said, for as long as it sits there. A few older sessions have never written one at all, and those show nothing rather than a guess.

> **There is deliberately no way to change it from here**, and the reason is worth recording. Nothing in a session's socket sets the mode — it takes a message and a rename, and that is all; `set_permission_mode` exists in Claude Code but only on the transports an SDK or the browser bridge speaks. That leaves pressing Shift+Tab, and the panel cannot aim a keypress at a *session*: X11 pairs windows, while a terminal window holds tabs and a VS Code window holds terminals. A press sent at a window lands in whichever tab has focus, which may be a different Claude — so the panel's own matcher regularly has two sessions behind one window id. Quietly loosening the wrong session's permissions is not a thing to get wrong occasionally, and a keypress the panel cannot verify (see the paragraph above) would do exactly that. It was built, tried, and taken out again.
>
> **What did become possible is narrower and sits elsewhere.** A session no process is holding can have its next turn *launched* by the panel, in a mode you pick — see [Turns run from the panel](#turns-run-from-the-panel). That is not changing a running session's mode; it is choosing the mode a turn starts in, which is the only form of the question that has an honest answer.

**State trace.** The strip under the detail header is that session's recent history, one coloured band per state, newest at the right. It answers the question a single status lamp cannot: has this been waiting for a while, is it churning between shell commands, or has it been quietly working the whole time. The strip scales to the session's own lifetime up to 30 minutes — the left label tells you the span.

**Notifications.** The first click anywhere on the page asks for notification permission. After that, a session arriving at a prompt raises a desktop notification, and the tab title and favicon show how many are waiting. The notification names what is being asked — *needs permission* with the tool it wants to run, *has a question* with the header Claude gave it, or plainly *needs you* for a terminal prompt the panel cannot read — and clicking it brings the panel up with that session selected. A prompt the panel is holding stays on screen until it is looked at, because those run out and are refused if nobody answers; a terminal session's prompt waits as long as you do.

**And when it has finished.** A turn ending raises its own notification — *is done*, *Finished, and waiting at its prompt* — which is the other half of watching a session: you go away while it works, and the thing you want to be told is that it has stopped needing to. It is announced once per turn and only for a session actually seen working, so a row arriving from offline, or a kept session waking up already idle, says nothing. `shell` is deliberately not counted as finished though the list draws it as waiting: Claude Code writes it both on the way into a foreground command and again as a turn ends, so a session dipping through it mid-turn would otherwise announce itself done and then carry on working.

**Two levels of switch, because there are two questions.** Which *kinds* of notification you want at all is a setting for the whole panel, under the gear in the top right. **Settings → Notifications** reads as one sentence — *raise a desktop notification when a session…* — with a switch completing it four ways: *wants permission to use a tool*, *asks you a multiple-choice question*, *stops at a prompt only its own terminal can answer*, *finishes a turn and goes quiet*. All four are on by default, and each carries a sample of the notification it turns on, drawn as the little card it will arrive as. A sample says more than a sentence about when it fires, and it is the only place you can see one without waiting for a session to raise it. Which *sessions* may use the kinds you have left on is a switch on each session, under **Details → Notifications**: *when it waits for an answer* and *when it finishes a turn*. Muting a session from its row menu silences it altogether, and the finished-a-turn switch dims to say so.

**The browser's permission is a separate question, and gets its own answer.** What the switches decide is what the panel would like to tell you; whether it is allowed to tell you anything at all is the browser's to say. So when it has not said yes, the page carries a notice rather than a line of small print — *your browser has not let the panel show notifications yet, none of the switches above can reach you until it does* — with an **Allow notifications** button that asks. If the browser is blocking them outright the notice turns red and points at the padlock in the address bar, because there is nothing the panel can press from here.

**Light and dark is a setting like any other**, so it lives on that page beside contrast and the base colour rather than in the app bar. It was the only setting up there, and a switch in the bar reads as a thing to flip often — it is not; it is picked once and left.

**Settings is a page, not a dialog.** The gear opens it where a conversation would be, and the gear stays lit while it is there. Nothing on it is a decision to come back from — every switch takes effect as it is pressed and there is nothing to confirm — so a scrim was buying a modality the page did not want, and on a short window it put a scrolling box inside a scrolling box. Picking any session is the way out, as is **Done** or Escape; no row on the list claims to be current while the page is showing, because none of them is what the pane is showing.

All of it is keyed on what the session is standing on rather than on its status, so a session that goes from one gate straight to the next — a tool allowed, the next one gated a moment later — is announced both times rather than only when it first stopped.

**A prompt shows on the row.** A session standing at one carries a badge on its list row saying which kind it is, followed by what it is: `permission · wants to run Bash`, `question · asks "Which database?"`, or `prompt · waiting at its own prompt`. The badge goes first on the supporting line, which is clipped from the right, so it survives however narrow the pane gets. A permission gate the panel is holding also moves the row into the amber band and up to the top of the list — the turn is still technically running, but it cannot go on without you, and calling that "working" hid the one session that needed a decision.

## Git and History

A session is nearly always working inside a repository, and the question you actually have while watching one work is what it has done to the tree. **Git** answers it, and then lets you act on the answer: the branch, how far it has drifted from its upstream, and every changed file in the three groups the editor uses — **merge changes**, **staged changes**, **changes** — with a message box and a commit button above them.

**The branch in the header is a button.** Pressing it opens the branch list, as the editor's status-bar branch does: *Create new branch…*, *Create new branch from…*, then the local branches most-recently-committed-first — the two or three you are actually moving between all week, rather than an alphabet — and then any remote branch with no local copy yet, which checks out as a branch tracking it. The one you are on is marked and cannot be picked again. It runs `switch` rather than `checkout`, so a branch name that also happens to be a path can never turn the click into a file operation, and a new name is refused before it reaches git if it starts with a dash or carries a space, then refused by `check-ref-format` if git would not have it either.

Switching branches rewrites the files underneath whoever is working in them, and the panel knows something the editor never did: whether a session is mid-turn in that folder. So a switch under a **working** session asks first. Everything else is git's own refusal, passed through as it stands — uncommitted work that would be overwritten, or a branch already checked out in another worktree.

**The drift counts are buttons too**, and they are filled rather than quiet, because an unpushed commit is something to do and a transparent arrow in a row of transparent arrows reads as one more fact about the repository. `↑2 to push` pushes those two commits; `↓1 to pull` pulls that one. Push takes the amber this panel already uses for a session that needs you; pull takes the primary tone, being work arriving rather than work owed. The arrow you are looking at when you think "push that" is the arrow you press, and its tooltip names the upstream it means.

**The interface is VS Code's Source Control view, deliberately.** Not as flattery: it is the one arrangement of these controls that everybody who would open this panel already knows, so nothing about staging a file needs explaining. The message box sits at the top with a split button under it — **Commit**, and an arrow holding *Commit & push*, *Commit & sync* and *Commit (amend)*. Each group header carries the actions that apply to the whole group, each row the ones that apply to it, and the row itself opens that file's diff in place. With nothing staged the button says **Commit all 3** rather than quietly committing something else, which is the same offer the editor makes and the same answer, said earlier. `Ctrl+Enter` in the box commits, as it does there.

**The sparkle in the corner of the message box writes the message for you.** It runs a headless `claude --print` in the repository with the diff that is about to be committed on stdin — the index if anything is staged, the whole working tree otherwise, so the message describes the commit the button would actually make — along with the last ten commit subjects, so what comes back looks like the messages already in that history rather than a house style from somewhere else. It goes to Haiku, because a commit message is a small closed job and a cheap one; it is given no tools, so there is no permission prompt to answer and nothing it can do to the tree; and it takes ten or twenty seconds, which the button says by pulsing while it waits.

The message lands in the box rather than in a commit — reading it before pressing Commit is the point. With something already typed it asks before replacing it. Nothing about this touches the session working in that folder: no message goes down its socket, and its conversation is left alone, so a stopped session's repository can have a message written for it just as well as a live one's.

**And it does not appear in the list.** A headless run writes a session file like any other, so the panel would otherwise show a row that arrives, says nothing and vanishes twenty seconds later — for an errand the panel itself asked for and is about to discard. Its pid is held in a set for exactly as long as the process lives, and the reader skips it.

That covers the panel's own errands only, which is not enough on its own: a second panel on another port reads the same folder and sees a pid it knows nothing about, and a row lingers for the twenty seconds the store keeps a session it can no longer see. So headless runs are left out by what they are as well as by who started them — `claude -p` and the SDKs write an `sdk-*` entrypoint in their session file where a session you can type at writes `cli`, and nothing you could watch about one would ever let you answer it. Interactive sessions are untouched by this, however they were started.

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

## Usage and cost

A session working on its own for an hour is spending money, and nothing in the terminal says how much until you ask it. **Usage** says it: what this session has cost so far, how many requests it took, the tokens in and out, and one row per model it used.

It is read out of the transcript, because that is the only place the figures exist. Every reply Claude Code writes down carries the usage the API reported for it — fresh input, cache writes and reads, output, thinking tokens, any web searches — and the tab is the sum of them at Anthropic's published per-token prices, with a cache write at 1.25× fresh input for the five-minute cache or 2× for the hour, and a cache read at a tenth.

**The cost is a list price, and the tab says so.** A Claude subscription bills a plan rather than tokens, and a negotiated rate is not the published one, so the figure is what this work is worth rather than what you will be charged — which is still the number you want when deciding whether a model is worth pointing at a job. A model the panel has no price for is counted in tokens and named as unpriced rather than quietly costed at zero.

**Cache reads are most of it, and the tab shows why.** A long session re-reads its whole conversation on every turn, so its token count runs into the hundreds of millions while the bill does not — the *Tokens in* tile gives the share that came out of the cache, and the per-model row splits fresh input, cache writes and cache reads into their own columns so the cost adds up in front of you.

**Sub-agent work is kept apart.** A turn handed to an agent of the session's own is marked as a sidechain in the transcript, and it gets its own table under the models: the same bill, listed separately because it is not the conversation you are reading in the tab next door.

**The context bar is the other thing you came for.** Its last request carried some number of tokens into the model, cached or not, and that is the session's context size; the bar puts it against that model's window and turns amber past three quarters, which is roughly where compaction starts to be the next thing that happens.

> **A transcript only grows, so it is only read once.** The server remembers the byte it stopped at and picks up from there on the next poll, which is what keeps a hundred-megabyte transcript from being re-totalled every few seconds. A turn is written down once per content block, all carrying the same request id, so the id is what keeps a turn that had three things to say from being billed three times.

A nested session has no transcript of its own, so there is nothing for it to total — its usage lands in the transcript of the session that started it, and the tab says so rather than showing zeros.

## How much of your plan has gone

The Usage tab answers what a session has spent. The other question — how much of the *plan* has gone before everything stops — is not a session's to answer, and no file on this machine knows it: it belongs to the account and lives behind Anthropic's API.

So the app bar carries a chip reading **35% · 28%** — how much of the current session's allowance has gone, and of the week's — and pressing it opens the full report: a bar per limit with what each resets at, and Claude Code's own breakdown of what has been contributing to them (requests and sessions over the last day and week, how much of it was at large context, in parallel, or in sessions running for hours). Each figure is coloured for its own band: green while there is room, amber past three quarters, red past nine tenths — so a tight session next to a quiet week reads as exactly that, and the app bar starts saying it before a session does.

**It is read by running `claude --print /usage`.** The panel handles no credentials and calls no endpoint of its own. The alternative was to read the OAuth token out of `~/.claude/.credentials.json` and ask Anthropic directly — a web server on this machine holding your login, for a figure the official client already gives away — and that is not a trade this panel makes for a number. What you see is what the terminal would have told you, parsed.

**It costs no tokens.** `/usage` fetches and prints; it samples no model, which a run against a fresh transcript confirms — not one usage entry in it. What it does cost is about five seconds and a process, so the panel asks rarely: once when the page opens, then no more than once every five minutes, never while the page is hidden, and immediately when you press **Refresh**. Two readings never run at once — the second is handed the first one's answer.

Being a headless errand, it stays out of the list, the same way the commit-message errand does — including in another panel watching the same machine, which never started it and cannot recognise its pid. It does leave a transcript behind under `~/.claude/projects`, as any headless run does; there is nothing the panel can do about that from the outside.

Like sending a message and writing to a repository, this runs a command on this machine, so it sits behind the same gate: **loopback only**. A panel served to the network, or started with `--no-send`, answers 403 and shows no chip at all rather than one that refuses.

**The output is a report for a person, not an interface, so nothing here insists on it.** Every line that reads as a limit becomes a bar; anything else is kept as prose in the order it arrived; and a release that changes the wording shows you the text it could not parse rather than an empty dialog. An API-key user, who has no plan to have anything left of, sees whatever `/usage` says about that.

## Updating the panel

The panel is a git checkout, and its releases are tags on that checkout. So "is there a newer version" needs no update server, no version endpoint and no second copy of the code to trust: it fetches the tags from the remote it was cloned from and compares the newest one against the commit `HEAD` is sitting on. Whoever can push a tag decides what a release is, and nothing else does.

When there is a newer release *and* this checkout can take it, a chip appears in the app bar beside the plan chip, reading the version it found — **v1.4.0**, with an arrow that falls into a tray once every few seconds. Press it and the dialog says where you are, where the release is, how far apart they are, and what changed: the tag messages of every release between the two, in order, newest first. **Update and restart** takes it.

**The chip is only ever there when there is something to press.** Not when you are up to date, and not when a release is out that this checkout is being left alone for — a chip in the app bar is an interruption, and none of those are worth one. All of it lives on the settings page instead, under **Panel version**: which release you are on, that it is the newest one and when that one was cut, or the reason a checkout is not going to be moved. Beside it is **Check for updates**, because "have I got the latest" is a question people ask on their own schedule rather than waiting for the clock to come round; it goes straight past the six-hour hold, says what it found in a snackbar, and turns into **See what changed** when the answer is yes.

**And nothing appears at all when there is nothing to say.** Not a git checkout — a tarball, a copied directory — no chip and no section, and the panel stops asking. A panel that is not bound to loopback answers 403 and shows neither either: updating runs git and restarts a process, so it sits behind the same gate as sending a message.

**Two channels, and the default is the one everybody else is on.** **Releases** is the above: the tags, and only the tags. **Development** follows the tip of the `develop` branch instead — the features that are written and merged but not released yet — for somebody who wants to try them and report on them before they are cut into a version. It is picked under **Panel version** on the settings page, beside the version itself, and it changes what *Update* moves to and nothing else: the same fetch, the same refusals, the same detached checkout, with the branch's newest commit standing where the newest tag stands. The dialog counts commits instead of releases and lists their subject lines, since a branch has no release notes.

The choice is remembered outside the checkout — in `~/.config/claude-watchtower/channel.json` — because an update replaces the checkout under it. Switching throws away the held reading, since "you are up to date" about releases is not an answer to a question about the branch. And going back to **Releases** does not roll anything back on its own: the next release that is newer than the commit you are sitting on is what it offers, which for somebody who has been following development usually means waiting until the release catches up.

**A release only ever goes forwards.** A pre-release or build suffix on a tag is deliberately not a release — `v1.4.0` is, `v1.4.0-rc1` is not — so a tag you push to try something out does not restart everybody's panel. And a `HEAD` with commits the newest tag does not have is *ahead* of the releases rather than behind them, which the dialog says rather than offering to move you backwards.

**It will not move over your work, or off your branch.** A checkout with uncommitted changes is left exactly where it is; so is one on a branch that is neither the default one nor a release tag. In both cases no chip appears, and **Panel version** on the settings page names which of the two it is. Somebody developing the panel should not have the panel offering to move their `HEAD` — nor nagging them about it from the app bar.

**It says what the restart will cost before you press.** Coming back on the new code means letting go of every session the panel is *running itself*, so the dialog counts them and names them: three sessions, one of them mid-turn with a turn about to be cut off, two typed-ahead messages that would be dropped. The count is read live rather than out of the cached check — a turn starts and ends well inside six hours — and it keeps up while the dialog stands open, because a turn can begin between reading the warning and pressing the button. It is tinted only when something is actually in flight; a couple of idle sessions being restarted is a fact, not a warning.

Sessions running in a **terminal** are deliberately not counted. One of those is its own process with its own pid and lives straight through a panel restart without noticing, and a warning that includes them is one people learn to press past. Either way no conversation is at stake — the transcripts are Claude Code's own files, exactly where they were, and `claude --resume` in the folder still finds them. The warning says that too, or it reads as though updating throws the work away.

**The tag is checked out detached**, exactly as it was published. Fast-forwarding the default branch instead would land on the tip of `main`, which is not a release and not what the button said; `git switch main` puts you back on the branch whenever you want it. The dialog says this before you press, not afterwards in a snackbar.

**The browser cannot name a version.** The request carries back the tag the page was shown, and the server checks it against what it reads for itself — so a page left open for a week cannot update to something it never offered, and a tag in a request is never the thing that gets checked out.

Then the panel comes back on the new code. The frontend is TypeScript, so it is rebuilt first — here rather than on the way up, so a release whose frontend does not build is something you are told about now instead of silently serving the previous one. Under the bundled systemd unit the restart is systemd's job, queued with `--no-block` so the request is accepted before the process goes; started by hand, the panel replaces itself with `os.execv` — same interpreter, same arguments, code freshly read off disk. Either way the sessions it was running here are let go of first, because `execv` runs no `atexit` handler and a held `claude` with nobody to reap it is the two-processes-one-conversation hazard arriving by the back door. Their conversations stay on disk, where they always were. The page waits for the panel to answer again and reloads itself onto the new frontend — the code that pressed the button is the old version's, and it has just been superseded.

**The check is on a long clock.** A fetch reaches the network and a release lands every few days at best, so the server holds its answer for six hours and the page asks every half hour; opening the dialog, or pressing **Check again**, asks for a fresh one. Two checks never run at once — the second is told one is on its way and shown what there is.

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

Ending it also takes its row off the dashboard, unless the row is [pinned](#keeping-a-session-after-it-closes). Stopping and removing were two separate asks for a while, which meant a session you had just ended sat on the list as a stopped row until you noticed and asked again — the panel keeping things you had explicitly finished with. Pinning is the one thing that says *keep this row past its process*, so it is the one thing a stop leaves standing, and the dialog says which of the two is about to happen.

Confirming sends `SIGTERM`, the same signal `Ctrl-C`-ing the process would, so Claude Code shuts itself down and writes out its transcript. **Force quit** in the same dialog sends `SIGKILL` instead, for a session too wedged to answer; nothing is flushed. Either way the transcript already on disk is left alone — only the process goes.

The pid recorded in the session file is re-checked against `/proc` immediately before signalling, so a stale panel can never kill an unrelated process that inherited the number. The ended session shows as **closed** for a few seconds, then leaves the list.

## Keeping a session after it closes

A row normally lives and dies with its process. A **kept** row outlives it: same name, same folder, same conversation, marked **stopped**. Nothing about the session is copied; the transcript is read from where Claude Code already keeps it.

There are two ways a row is kept, and the only difference is how long:

| | |
|---|---|
| **Held**, for as long as the panel runs | Every row the panel makes for itself — a session it [started](#turns-run-from-the-panel), a session it [made interactive](#make-interactive). It survives a page reload, and goes when the panel goes, because whatever was running here is not running any more either |
| **Pinned**, for good | **Pin to the dashboard**, from the right-click menu or the switch in the Details tab. Written down, marked with a small pin in the list, and the only kind that is still there after the panel restarts |

Panel-run sessions used to be written down as well, which made every one of them permanent whether or not that was wanted: the panel decided what you were keeping, and the only way out was to go and unkeep each one. Now it keeps them for as long as it can honestly claim to be running them, and pinning is yours to ask for.

**Remove from the dashboard** is what **End session** becomes once there is nothing left to end: a kept row whose process went on its own. It asks about the row rather than about the conversation — nothing of the transcript goes with it, and `claude --resume` in that folder still finds it. There is only ever one of the two on a row, because stopping and removing are the same act reached from different sides, and offering both meant a menu with two endings and a dashboard that quietly filled up with sessions you had already finished with.

**Stop running it here** is that act on a session the panel is holding: the panel lets go, and the row goes with it unless it is pinned. It is the same **End session** every other row has — a session the panel runs has a process behind it too, a pipe rather than a terminal — and the row spent a while as the one row on the list whose menu ended at *Mute notifications*, because `alive` reads false for it and that was what the item was gated on. Pin it first if you want to keep typing at it later; unpinned, ending it is ending it.

**Start it up** in the header runs `claude --resume <session id>` in that folder, in a new terminal window, and the session picks the conversation up where it left off. You can skip the button: type into the composer of a stopped session and it starts up *and* delivers what you wrote, as soon as the new process is listening.

That is not a special case of the composer, it is the whole of it. **A message is never refused for the state the session was in when you pressed Send**, because that is not the state it will be delivered in: sessions close while you are typing, sockets go with a suspended machine, a process comes up two seconds after the click. So every message is accepted and handed to one deliverer per session, which asks again every second for two minutes, drains in the order you typed, and starts the session back up if nothing is running it — over its messaging socket if it is listening, down the panel's own pipe if the panel runs it, in a fresh terminal if nothing does. Only three things still turn a message away: the panel not being on loopback, a session standing at a prompt of its own, which cannot read a queued message until somebody answers it, and a tenth message [typed ahead](#typing-ahead-of-a-turn) of a turn the panel is running.

The composer used to say *"this session is not listening for messages"* and take the box away. It was true and useless: it described a state you could not see, had not caused, and could do nothing about — while the panel already knew what to do about it.

The terminal is whichever of Ghostty, WezTerm, kitty, Alacritty, Konsole, GNOME Terminal, Xfce Terminal, `x-terminal-emulator` or xterm it finds first. Override it with `CLAUDE_WATCHTOWER_TERMINAL`, giving the terminal and the flag that takes a command:

```bash
CLAUDE_WATCHTOWER_TERMINAL="kitty --"
```

A session the panel starts is a session in its own right, never a child of whatever started the panel. The panel is often run from inside a session itself, and Claude Code stamps its environment on everything it launches — inherit that stamp and the new session comes up nested, with no session file, no transcript and no chat of its own — what [What it shows](#what-it-shows) describes about nested sessions. So the terminal is opened with those session-scoped variables stripped — `CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_CODE_SESSION_ID` and the rest. Settings you put in your own profile, `CLAUDE_CONFIG_DIR` and the other `CLAUDE_CODE_*` options among them, are passed through untouched.

Starting a session runs a command on this machine, so it sits behind the same loopback gate as sending a message: off unless the panel is bound to a loopback address. Pinned rows live in `~/.config/claude-watchtower/sticky.json`; held ones live in the panel's memory and nowhere else. A file written before the distinction holds panel-run rows too, and those read as pinned — what was already permanent stays permanent.

## Turns run from the panel

A kept session — one whose process has gone, but whose row is still here — takes its next turn from the panel. **Send** does it: the panel launches `claude --print --resume <id>` in the session's folder, which picks up the conversation that is already there and appends to the same transcript. Same chat, no fork, nothing new to look at afterwards.

The button says *Send* and nothing else. It was a pair for a while — **Run it here** beside **In a terminal** — and a pair asks, of every message you write, a question about process management before it will carry the message: *who should run this?* The answer was the panel every time, and the other button's real function was to send your words somewhere you were not looking. So there is one button, and starting a stopped session is part of sending to it rather than a decision to make first. Nothing hands a conversation back to a terminal any more; **Make interactive** is a one-way door, and the dialog says so.

Which makes the permission mode a per-turn choice. **Runs in** — *Manual*, *Plan*, *Accept edits* — sits above the box, and switching is instant because nothing is applied when you switch: the mode is an argument, and the next turn is launched with whatever it says. There is no process to signal and nothing to restart, which is the whole reason this is small enough to trust.

Four modes and not six. `bypassPermissions` and `dontAsk` are left out because they never ask anybody: a panel that can answer prompts would have nothing to answer, and no way to know what had been done in its name.

**Auto** is offered, because it is Claude Code's own default and leaving it out meant the panel could not run a session the way its terminal already does — but it is not the cautious mode its name suggests. A classifier decides what needs approval, and what it approves never reaches the panel at all; in testing it ran `rm -rf` with no prompt raised. *Manual*, *Plan* and *Accept edits* either ask or hold back.

What *Manual* does not mean is that every tool is gated: Claude Code decides for itself that some commands are safe enough not to ask about — an `echo` ran in a Manual turn with no prompt raised at all — so the guarantee is that nothing needing approval gets it, not that nothing runs.

### The mode, and what it asks

**The mode sits in the header, beside the session's name** — a single connected run of segments, the way M3 draws one choice out of several, with the one in force filled. No tick beside it: the fill has already said so, and a tick is the same fact told twice.

**And it is small on purpose** — a line among the header's other lines rather than a control in a form. Part of why it read large was a bug rather than a size: the rule set `font: inherit` on the chip, which as the later class selector beat the typescale class in its markup, so it had been rendering at the header's body size all along with `md-label-small` doing nothing.

**It is drawn in the header's own colour, not in one of its own.** The header wears whatever colour the session's state is, so the bar is made from that: the track is the header's foreground held back to a hairline and a wash, and the chosen segment is that foreground at full strength with the header colour punched out of it. Nothing in it is a fixed hex, so it belongs to the header on every state and in both schemes — where before it was a panel of `surface` with a grey outline, sitting on the colour like something pasted on. The four modes had an accent each, which made the loudest thing in the header a setting rather than what the session was doing; the chip names its mode, so it does not also need a hue.

### Answering what it asks

A turn the panel runs is launched with `--permission-prompt-tool stdio`, which is undocumented in `--help` and is the same mechanism the official VS Code extension uses. It means Claude Code asks over the pipe the panel is holding rather than at a terminal, so **the prompt arrives in the panel and the tool does not run until you answer it**. The composer gives up its room to what is being asked, and the turn stands there — genuinely stopped, not polling.

Two things come up that one channel, told apart by whether the request says it needs a person:

| What it is | What you get |
|---|---|
| A permission prompt | The tool it wants and what it wants to do with it, then **Allow** or **Refuse** |
| A multiple-choice question | The question, every option with the sentence Claude wrote about it, and **Answer** — one pick, or several where the question allows it |

Answering a question *is* allowing it: the picks are written back into the input the tool was going to run with, keyed by the question's own text, which is the shape Claude Code reads them out of.

It is drawn as the one place in the panel where a decision is *taken* rather than reported — its own raised surface with a tinted bar naming what is being asked, each option a full row you press with the indicator M3 uses for the kind of choice it is (a radio where one answer is wanted, a box where several are), and one primary action. **Enter sends**, in the composer and in the new-session dialog both.

**And your answer appears in the conversation.** It has to be dug out: a question is a tool call, so what you picked is written down as that tool's result and nowhere else — `Your questions have been answered: "…"="Tabs"` — and a turn holding nothing but a tool result is otherwise skipped, which left the transcript showing Claude asking something and then carrying on for no visible reason. The panel reads those results and shows the answer where you gave it, marked *answered here*.

> **Nobody answering is a refusal.** A turn parked on a prompt holds a pipe and a process, so the wait has a deadline — ten minutes — and running out refuses rather than allows. The turn is told, and says so in the conversation. Verified: an unanswered write came back `The permission request timed out with no answer, so it was refused`, and no file was created.

### Typing ahead of a turn

A turn takes minutes, and the next thing you want to ask usually arrives while you are reading the last answer. So type it: the box stays open while a turn runs, and what you write is **held and sent the moment that turn ends**.

What it used to say was *"It is still answering the last one"*. That was true, and it was the panel making its own timing your problem — the message was written, the panel knew exactly when it could go in, and it asked you to remember to come back and press Send again. A terminal does not ask that. Typing ahead of a running turn is one of the oldest things Claude Code does, and it lands at the prompt.

One turn runs at a time, and that part is not a limitation the queue works around — two turns appending to one conversation is the failure this whole area is built to avoid. So each queued message waits for the *result* of the one before it, exactly as it would have if you had waited yourself before sending, and the queue drains in the order you typed.

**What is waiting is shown, above the box it was typed in**, with **Drop** beside each. Both halves matter. A message that vanished into a promise is indistinguishable from one that was dropped, so it is on screen; and the turn that was running when you typed it may well answer it anyway, in which case the last thing you want is for it to be asked again as though nothing had happened. Nothing can be taken back once it has gone down the pipe, and the panel says which of the two happened rather than reporting success for a message that is already being answered.

The queue is drained by the reader thread that saw the turn end, not by the browser — a panel with no page open drains it too — and it is capped at ten, which is the one place a refusal says something worth hearing: past that you have typed more than you can have meant one conversation to answer in order.

Two more things follow from the queue being the panel's rather than Claude Code's:

- **A prompt standing in front of the turn does not hide it.** The composer gives its room to what is being asked, but what you typed ahead is still drawn above it — that queue goes in after the turn this prompt is holding up, which is exactly when you might want it back.
- **Nothing waiting is lost if the process goes.** It is handed back to the same deliverer every other message goes through, which starts the session up and puts it in. Letting go of a session deliberately — **End session** — clears the queue instead, because that is not a session anyone wants restarted to deliver a message they have finished with.
- **Stopping the turn in front of it holds it rather than emptying it.** See [Stopping a turn](#stopping-a-turn): the messages stay on screen, and stay put, until you send them, drop them, or type another one.

### Stopping a turn

**Stop** sits beside Send, and Ctrl+C does the same thing. It stops the turn where it stands: the session stays up, the conversation keeps everything it had got to, and the next thing you type is answered normally — which is what Ctrl+C at a prompt does, and the reason for wanting it is the same. You read the first paragraph of what it is doing and you can already see it is answering the wrong question.

It is one `{"subtype": "interrupt"}` down the control channel the [mode](#the-mode-and-what-it-asks) is set on, and the panel holds that channel for exactly as long as it is running the session. Measured against a held session: the request comes back `{"subtype": "success", "response": {"still_queued": []}}`, the transcript gains a `[Request interrupted by user]` turn where Claude Code stopped, the turn ends `error_during_execution`, and the process is still there and takes the next turn as if nothing had happened.

That result matters more than it looks. An interrupted turn *reports itself as an error*, and read at face value the row said `error_during_execution` in red at somebody who had just pressed Stop. A turn the panel stopped is a turn that did what it was told, so it is recorded as **you stopped it** and is not a failure.

**Anything typed ahead is kept, and held.** The [queue](#typing-ahead-of-a-turn) was written for the train of thought you have just stopped, and delivering it a tenth of a second later — into a session now waiting to hear what you actually want — is the opposite of what stopping meant. So it does not go in on its own. But it is not thrown away either, which is what used to happen: it is minutes of your writing, and deleting it is not the panel's call to make. The strip stops reporting and starts asking — **Send it**, **Drop it**, and the note that typing something else sends that last, after what is already waiting. Anything you do to it clears the hold, and from then on the queue drains as any other does.

While a turn is standing at a [prompt of its own](#answering-what-it-asks) the box and the button both give their room to the question, so there is no Stop there: **Refuse** — or **Skip**, for a question — is the way out of that one, and it is on the card already.

Ctrl+C defers to the browser in one way: **anything selected wins**. A selection in the page, or in the box you are typing in, and Ctrl+C copies as it always did. It only reaches the turn when there is nothing to copy — which is the same moment a terminal would have read it as an interrupt.

> **A session running in a terminal cannot be stopped from here, and the button says so rather than going missing.** Ctrl+C at a terminal is not a signal, it is a *keystroke*: Claude Code reads it in raw mode, and nothing outside the pty can deliver a keystroke to it. The nearest thing from out here is `SIGINT` to its pid, and that was measured rather than assumed — it does not stop the turn, it **ends the session**. Working or idle, Claude Code printed its `claude --resume` line and exited. So the panel does not offer a Stop that quietly kills a session: the button is drawn disabled, naming the Ctrl+C that does work, with **Make interactive** on the bar above as the way to have a turn it *can* stop.

### How full the conversation is

Under the mode chips, a rule and a percentage: how much of the model's context window this conversation is carrying. It is the last request's total input — fresh tokens, cache reads and cache writes together, which is everything the model had in front of it when it last answered. That is the same figure `/context` reports, and it comes off the transcript, so **every session has one**, terminal or not.

Nothing new is read for it. `scan_usage` already walked the transcript for the Usage tab and already remembered this number; it was simply never leaving that tab, so the one place it mattered — a conversation getting full while you are in it — was the one place it was not. The scan is incremental, which is what makes it affordable on every poll: measured on this machine, 33–76 ms to read a 2.6–7.4 MB transcript the first time and 0.03–0.1 ms every time after.

The bar changes colour twice: at three quarters, and again at nine tenths, which is roughly where Claude Code stops waiting and compacts on its own.

**Compact** appears past halfway. It summarises everything so far and carries on from the summary, and it asks first — not because anything is lost from disk, since the transcript is untouched and the chat above reads the same afterwards, but because what the session *remembers* is replaced by a summary of itself and there is no putting that back. Below half it is not offered at all: an offer to throw away the middle of a conversation with half its room still free is an offer to lose something for nothing.

**It is only offered for a session the panel runs**, and the reason is the transport rather than the policy — see [Slash commands](#slash-commands), which is the general version of it. A message over a session's messaging socket is queued with slash commands switched off, so the text would arrive and do nothing; a held pipe is the other transport and it *does* expand them. Checked against 2.1.239 rather than assumed — a `/compact` turn down a held pipe answered with:

```jsonc
{"type":"system","subtype":"status","status":"compacting"}
{"type":"system","subtype":"status","status":null,"compact_result":"success"}
{"type":"system","subtype":"init", …}          // it starts over from the summary
{"type":"system","subtype":"compact_boundary",
 "compact_metadata":{"trigger":"manual","pre_tokens":24071,"post_tokens":3661, …}}
```

Those frames are the whole reason the panel reads `system` frames at all. **The turn's own result is empty** — a compaction ends `is_error: false` with `""` — so on the outcome machinery alone it reads as a turn that finished and said nothing. And the percentage above the button does not move either, because it is taken from the last request the model *answered* and the next one has not happened yet. Between them, a compaction would look exactly like a button that did nothing. So what it actually did is reported from `compact_metadata` — *compacted 24k → 3,661* — and a refusal (`compact_error`, e.g. *Not enough messages to compact*) is reported as a refusal rather than folded into a success.

`trigger` is carried through as well, so a compaction Claude Code did **on its own** is reported too, and marked as its own rather than passed off as something you asked for.

**While it runs, the session is called *Compacting*.** From the pipe's side a compaction is a turn like any other, so on `status` alone it read as *Working* — the one thing it is not doing. It is throwing the middle of the conversation away, and that is worth telling apart from a turn you asked for, so the word is taken from `OWNED_COMPACT` rather than from the status. It is a drawn state only: deliberately out of `STATE_ORDER`, so it gets no filter chip and no lamp, and no session is counted twice.

And the bar fills while it does. Nothing on the wire says how far along a compaction is — `compacting` goes up, then there is silence until the result — so the figure is elapsed time bent through a curve, which is exactly what the terminal shows you and exactly the same curve: Claude Code 2.1.239 computes `1 - exp(-seconds / 90)` and caps it at 95%. The cap is the honest part, and the reason it was copied rather than invented: it never claims to be finished, and a compaction watched in both places should not be 40% in one and 70% in the other. The tooltip says what it is measuring — *how long it has been going, not how much is left* — and the bar stops reporting the conversation while it runs, since that reading cannot move until the next turn anyway. It walks forward on the panel's own clock, beside the durations, rather than by repainting the pane once a second for a number no signature can see change.

> **A compaction the process does not outlive** is marked as one that stopped part way through. The record of how the last one went is worth keeping; the *running* flag is not, because nothing is coming to clear it and the panel would go on saying *Compacting* about a session that is not running at all.

> **Not while a turn is running.** Sending it through the ordinary path would put it in the [queue](#typing-ahead-of-a-turn), which is right for a message and wrong for this: a compaction is not typed ahead, it rewrites what the session remembers. Queued, the button would say *Compacting…* for a compaction that had not started, and then fire one later that nobody asked for a second time. It refuses instead, and says to let the turn finish.

### Slash commands

Whether `/something` works is a question about the **transport**, not about the command — and the panel used to answer it with one hardcoded list for both transports, which made it wrong for half of them.

Over a session's **messaging socket**, every slash command is inert. Claude Code queues an injected message with expansion switched off on purpose, since command markdown can carry inline shell, so `/compact` arrives as eight characters of prose. The panel does not pretend otherwise: it turns what you typed into the sentence that asks for it — *"Use the compact command."* — which is the one thing an injected turn can do. For a session in a terminal that is all still true.

Over a **held pipe** they are expanded, and the session says so itself. Its `init` frame carries both lists, and down that transport the terminal-only one is two entries long:

```jsonc
"terminal_slash_commands": ["doctor", "color"],
"slash_commands": ["compact", "context", "model", "clear", "usage", "effort", …]  // 47 of them
```

So for a session the panel runs, **the session's own answer wins over the panel's guess**. Guessed, it refused `/compact` on the one kind of session where compacting works — *"/compact only works at this session's own prompt"*, said to somebody looking at a Compact button that does exactly that. Asked, it is right, and it stays right when Claude Code adds a command or a plugin brings one.

Two things follow, and both are the opposite of what the panel did before:

- **What you typed is what goes.** No rewriting into prose: the session expands it, and `Use the compact command.` reads as a request where `/compact` compacts. The line under the box says so — *Goes in as `/compact` — this session runs it* — rather than reporting a translation that is no longer happening.
- **The picker offers them.** The catalogue is a walk of the skill and command folders on disk, so it finds what was written down and misses what is built in — `/compact`, `/context`, `/model`, `/clear` have no file to find. A command you cannot type the name of is a command you do not have, so the session's list is folded in, with the catalogue's entries winning where both know a name, since only those carry a description.

**A session that has not spoken yet still takes them.** Falling back to the socket list until the first `init` frame arrived looked careful and was the bug in its own right: a held process emits nothing until it is sent something, so *every* session started and not yet typed at has an empty list — and a session you have just brought up is exactly when you reach for `/compact`. Whether a command is expanded is settled by the transport and is known without asking anyone; only the exceptions need the session. So a held pipe defaults to the two above and replaces them the moment it is told better.

### Clearing the conversation

`/clear` starts a session's conversation again with nothing behind it, and it is the terminal's own command: over a session's messaging socket it is queued with expansion switched off and arrives as six characters of prose, so for a session in a terminal the panel names it rather than sending it. Down a **held pipe** it is expanded like every other command, so a session the panel runs gets a **Clear** button beside the context bar, next to *Compact*.

What it does is not what the name suggests, and the difference is the whole of the work behind the button. **Claude Code does not empty a conversation in place — it starts a new one.** Measured against 2.1.239: a `/clear` turn comes back with a fresh `init` frame carrying a *different* `session_id`, and everything after it is filed under that. The process is the same process and the folder is the same folder, so from the panel's side one session has changed its name.

So the panel follows it. The held process, the queue, the mode, the row, the name you gave it and its place in your order all move to the new id together — there is one list of everything keyed by a session id, walked in one place, because a clear that moved eight of the nine would be a bug nobody found until the ninth mattered. The answer to the request carries the new id so the pane can follow rather than reporting the row as gone, and the old transcript is deliberately left alone: it is the conversation that was cleared, it is still on disk, and it is still resumable. Deleting somebody's history is not what *clear* asked for.

It is refused mid-turn and while anything is typed ahead — a queue is for the conversation it was typed into, and delivering it to a session that has just forgotten what it was about is not what waiting for a turn meant.

**And the browser follows it too.** The row moving is only half the job: the pane is looking at the old id, and a row that vanishes reads as a session that ended rather than one that carried on — the panel used to drop you on whatever was at the top of the list. So the feed carries where a cleared session went (`moved`, old id to new, kept for five minutes), and the selection follows it before falling back to anything else. A session cleared twice leads all the way to where it is now rather than to the id it had in between.

**Typing `/clear` into the box does the same thing.** It has to: for a session the panel runs, what you type is what goes, so `/clear` reaches the pipe as the command and Claude Code acts on it whether or not the panel was the one asking. So following the session is not conditional on the button — the reader moves the row whenever the id on the pipe changes, however it changed. Gating that on "the panel asked for this" was the first version and it was a hole: the conversation cleared, and the row went on showing a transcript that had stopped growing. The same is true of a `/clear` typed ahead and delivered a turn later.

### Make interactive

A session running in a terminal cannot have its turns taken, because something else is holding its transcript, and two processes appending to one conversation is the failure this whole area is littered with. **Make interactive** is the one way across: it keeps the row, ends the terminal session, and leaves the same conversation with nobody holding it — which is the state a panel turn needs. It asks first, because it ends a running process.

**And that is the only session it is offered for.** A terminal holding the transcript is the whole of what stops the panel running a session, so it is the whole of the test: alive in a terminal means *Make interactive*, and everything else — kept, closed, adopted, held open, or simply not known to be alive — is run from here, with its mode chips and its box. What that replaced was a test spelt out state by state, which had gaps between the states nobody had thought of. A session whose terminal had closed fell in one: past *stopped*, so it was nobody's, and it got no mode, no way in, and a box that only offered to open a terminal. A session in the middle of a turn the panel was running fell in another, because starting the turn moved its status off *stopped* without ever recording that the session was now ours — so mid-turn the chips went away and the row offered to make interactive the thing it was running. Both looked like the panel losing track of a session when you reloaded the page, which is when a row is drawn from scratch. Running a turn on a session now records that it is ours, on the way, the same as adopting one does.

Nothing is lost from the conversation. The row is kept *before* anything is signalled, so the transcript, the folder and the name survive the process that was showing them. It does not go back, though: there is no button that hands a conversation to a terminal, and the dialog says as much before you agree to it. `claude --resume` in the session's folder is still there if a terminal is what you want.

**It comes up running, and stays running.** The panel holds one `claude --print --input-format stream-json` open per interactive session — alive between turns, serving turn after turn down the same pipe — so the row reads *Waiting · here* or *Working · here* the way any other session does, and there is a real process behind it. Adopting starts it at once; you do not have to send anything to bring it up.

Picking a chip moves it in the same frame you click it, and the request follows. It can afford to: the endpoint answers in single-digit milliseconds, while the state poll that used to be waited on afterwards takes half a second or more — so waiting for confirmation made a setting applied in one frame feel like it had not registered. A refusal puts the chip back and says why.

Holding the pipe is also what makes the mode a **live** setting rather than a choice for next time. `set_permission_mode` is refused on the messaging socket, because its callback is not registered on that transport — but this one owns the session's stdio, where it is, and it answers `{"subtype":"success","response":{"mode":"acceptEdits"}}` and takes effect on the very next tool. Picking a chip on a running session changes what it is allowed to do, then and there, with nothing restarted. The bar says **Running in** rather than *Runs in* when that is what is happening.

**Pin it and it survives the panel restarting.** A held process belongs to the panel, so it goes when the panel goes. A [pinned](#keeping-a-session-after-it-closes) session comes back: its row was written down, so the panel picks the session back up on its next start and says so on the way (`note: running 66574f01 here`) — without that, a restart left the row saying *Runs from here* with nothing behind it, which reads as the interactive session having vanished when it was the panel that had. An unpinned one does not come back, and its claim to be *running here* is dropped on the way past, because a session the panel is running that nothing on the list shows is worse than no row at all. A session somebody has since opened in a terminal is left alone either way, because that terminal holds its transcript.

**The mode outlives all of it.** Pinned or not, ended or restarted, the mode you picked is a choice about the conversation rather than a fact about whatever process was serving it — so the session comes back into the mode it was left in, not into the default. The whole `owned` record used to be dropped in both those places, which was near enough harmless while it only ever meant *adopted*; a turn run from the panel now records itself there too, so dropping it was quietly resetting the mode of every session anyone had typed at.

Letting go is **End session**, which for an interactive session means the panel stops holding it rather than signalling anything. The panel also lets go on its way out — on Ctrl-C, on `kill`, and through `atexit` — because a held `claude` left on a transcript with nobody to send to it is the two-processes-one-conversation hazard arriving by the back door.

A session that has never spoken is taken over too. It has no transcript, so `--resume` would fail with *"No conversation found"* — it is started here under its own id instead, empty, in the same folder, which is what the panel already does for a session it started itself and nobody has typed at. The dialog says there is nothing to carry over rather than promising a conversation that does not exist.

Two things it will not do:

| | |
|---|---|
| Take *"signalled"* for *"stopped"* | It waits for the process to actually go before reporting success. Interactive Claude Code exits on `SIGTERM` but is not always reaped at once, so what is checked is whether the process has ended, not whether `/proc` still lists it |
| Force anything you did not ask it to | A session mid-turn takes a moment to stop. **Force it** appears only once asking has demonstrably not worked |

> **What makes a session the panel's is never its status.** A held session's status is `idle` or `busy`, exactly like one in a terminal, and it has no messaging socket at all — so anything that asks *is this stopped?* or *is it listening?* to decide whether the panel owns it gets the answer wrong the moment it comes up running. Ownership is its own question (`runsHere`), asked before either of those, and what it asks is whether anything else is alive on the transcript. Getting it wrong four times produced, in turn: the mode chips vanishing, **Make interactive** reappearing on an interactive session, a composer that said *"this session is not listening for messages"* about a session the panel was holding a live pipe to, and a session standing at a prompt with a composer that said only *answer the prompt in the terminal* — no box, and no button to stop it being a terminal's problem, which is a dead end on the one row you are most stuck on. A reason and a way out are both true at once, so both are drawn.

> **A turn of the panel's own is not a session.** The `claude` a panel turn runs holds the same transcript as the session it belongs to, so without being told otherwise the panel lists it as a live session — the row leaves *Runs from here* mid-turn and offers to make interactive the thing it is in the middle of running. Turns register as the panel's own errands, the way `/usage` and the commit-message run do.

> **Two processes on one transcript stays impossible.** A turn only runs for a session no live process holds, and only one at a time. `/api/start` refuses while a panel turn is in flight, for the same reason from the other direction.

The turn is launched and let go of: the panel does not sit holding it. What comes back appears in the conversation above like any other turn, because it is read from the transcript like any other turn, and the composer says a turn is running while one is. If it fails, the reason is kept and shown under **Runs in** until the next one.

## Design

The interface follows Material Design 3. Nothing about it is hand-picked colour:

**Dynamic colour.** The whole palette is generated in the browser from a single seed by `@material/material-color-utilities` — the same library Material uses — and written to the document as `--md-sys-color-*` custom properties. Open **Settings** (the gear in the app bar, which opens a page in the detail pane) for light or dark, contrast, and the base colour from a preset or any colour at all; every surface, container, outline, and state colour is derived from it and the choice is remembered. The scheme uses the `SchemeVibrant` variant, which keeps the seed's hue with strong accents while leaving containers at the pastel tones that guarantee contrast against their `on-` roles.

Settings also exposes MD3's three **contrast levels** (standard, medium, high), which widen the tonal distance between paired roles for legibility.

**State colours.** Each session state gets a legal MD3 role pair — the container tone fills the avatar and the detail header, the matching `on-` tone draws every glyph on it, so contrast is guaranteed by construction. `working` uses the scheme's own primary, so it always matches your base colour. `waiting`, `running`, and `ready` are extended custom colours with semantic base hues (warm, teal, indigo) that are nudged to the nearest hue keeping at least 35° from the primary and from each other. That is why no two states ever look alike, whichever base colour you pick.

**Typography** is Roboto, MD3's typeface, self-hosted in `web/assets/fonts` with the baseline type scale as `--md-sys-typescale-*` tokens. Shapes come from the shape scale (chips small/8dp, chat bubbles large/16dp, list rows and buttons full, dialog extra-large/28dp) and elevation is expressed as container tone rather than shadow, with shadows reserved for the scrolled app bar, the dialog, and the snackbar.

**Motion** comes from the same token set — `--md-sys-motion-duration-*` and `--md-sys-motion-easing-*` — and one rule governs everything that floats over the panel: it arrives and it leaves, rather than arriving and then blinking out. The jump buttons over the transcript, the context menu, and the quote bar all rise into place and shrink away on the standard 200ms; the panel below the tab strip fades in when it changes tab or session, and pointedly *not* when a poll rebuilds it, so a working session never has what you are reading pulsing at you. Anyone whose system asks for less of it gets none of it, smooth scrolling included: `prefers-reduced-motion` is honoured globally.

Components used: top app bar, navigation-drawer style list items, filter chips, primary tabs, filled/tonal/text/outlined buttons, icon button, switch, segmented button, dialog, divider, snackbar, state layers and ripples. Everything is one static HTML file — no build step, no network at runtime.

## Options

```bash
python3 server.py [--local] [--port N] [--host H] [--no-key] [--new-key] [--no-send] [--build] [--no-build]
```

| Flag | Does |
|---|---|
| `--local` | Serve this machine only. No phone, and no key to type — the panel as it was before it could be reached from anywhere else |
| `--port N` | Serve on this port **for this run only**. What is remembered is left alone, so the bookmark still works tomorrow |
| `--no-key` | Answer anyone who can reach the port, with no key — and read-only, since nothing then stands between the network and the composer |
| `--new-key` | Throw the remembered key away and make another. Every phone has to be given the new one |
| `--no-send` | Read-only, wherever it is bound |
| `--lan` | Nothing: serving the network is the default. Kept so the flag still works |

`--build` builds the frontend and exits; `--no-build` serves whatever is already
built, however stale. Neither is needed day to day — starting the panel builds
what has changed and nothing else.

### Its own port

The panel picks its port on the first run and writes it down in `~/.config/claude-watchtower/listen.json`. Every run after that is on the same port, which is the point: an address you can bookmark, and type on a phone from memory.

It is picked rather than fixed because two people on one network both running a panel is the ordinary case. The number comes from who and where — your username, this machine's name, and the path this clone sits at — hashed into the 8800–8899 band, and then stepped forward if something already holds it. Two clones in two folders get two ports; the same clone gets the same port even if the file is deleted.

`--port` overrides it for one run without touching what is written down. `CLAUDE_WATCHTOWER_PORT` does the same from the environment, which is the one to use in a service unit.

### From your phone

Nothing to pass: the network is where the panel serves by default. It prints two addresses — the loopback one, and the one your phone can reach, with the key on the end.

```
claude-watchtower → http://127.0.0.1:8867
on this network  → http://192.168.1.24:8867/?k=r4pm7dwq
                  or scan the code in Settings — it is the same address
```

**Or don't type it at all.** **Settings → On your phone** shows that address as a QR code. Point a camera at it and the phone opens the panel already holding the key. The code is drawn by the panel itself — `watchtower/qr.py`, a hundred lines of the standard's own arithmetic, because a panel that installs nothing does not grow a dependency for one picture — and it is checked against an independent encoder, module for module, in `tests/qr-check.py`.

The address in that section is read at the moment you open it rather than at startup, so it is right after the laptop has moved between networks.

**The key is the same key every time.** It is derived from this machine and written down in `listen.json`; a phone that has been given it stays given it, across restarts and across networks. `--new-key` is the only thing that changes it — and if the panel can neither derive nor save one, it says so on startup rather than letting a phone discover it.

**The key is the whole gate, and it is a real one.** Anything arriving from off this machine — a page, a stylesheet, an API call, a POST — is answered only if it carries the key, in the URL or in the cookie the first answer set. Everything else gets a 403 that says nothing about the machine behind it. A request from this machine is never asked: the only thing that can reach loopback is already at the keyboard.

**What the phone gets is the whole panel**, composer included: sending, running a turn here, pasting a picture, git writes. That is the trade the key buys, and it is why `--no-key` — which takes the key away — takes sending with it.

Two things a phone cannot have over plain http, because browsers reserve them for secure contexts:

- **Notifications** are unavailable, so a session finishing will not raise one. The panel checks and simply does not offer them.
- **The clipboard API** is absent, so *Copy* falls back to the older `execCommand` path. It works; it is just not the modern one.

> **Only do this on a network you trust.** The key stops a stranger on the same wifi reading your conversations, and it is eight characters over plain http on a local network — not a password protecting a public service. The panel is not built to be on the internet, and putting it there is not a thing a flag here will do for you.

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

The unit names no port and no host, so the service is on this install's remembered port and reachable from your phone — the same address as when you start the panel by hand. Add `--local` to its `ExecStart` to keep it to this machine; to pin a port there, set `Environment=CLAUDE_WATCHTOWER_PORT=8787` rather than passing `--port`, which would leave the remembered one to drift out of step with what the unit uses.

## Repository layout

| Path | What's inside |
|---|---|
| `server.py` | The way in: arguments, the frontend build, the polling thread, serve |
| `watchtower/` | The panel itself — `config`, `proc`, `sessions`, `rows`, `store`, `transcript`, `usage`, `catalog`, `windows`, `control`, `input`, `owned`, `paste`, `plan`, `update`, `git/`, `http` |
| `watchtower/build.py` | Finds Node and runs the frontend build when `web/` has changed |
| `tools/build.mjs` | The build: strips types, concatenates stylesheets, copies assets |
| `web/index.html` | The page shell — markup only |
| `web/styles/` | The stylesheet, Material 3 |
| `web/src/` | The panel's TypeScript: `main.ts` orchestrates, `views/` are the tabs, `ui/` the pieces they share |
| `web/assets/fonts/` | Roboto and Roboto Mono, self-hosted |
| `web/assets/vendor/` | `material-color-utilities`, for dynamic colour |
| `dist/` | The built frontend, which is what the panel serves. Generated; not in git |
| `tests/python/` | Unit tests over the readers, and over the package's own wiring |
| `tests/python/test_update_repo.py` | The updater against a real history: which releases a checkout is actually missing |
| `tests/update-check.mjs` | The two sentences the update dialog is judged by, drawn without a browser |
| `tests/fixtures.py` | Stands up a session in every state |
| `tests/ui-check.mjs` | UI checks over CDP (tokens, contrast, settings) |
| `tests/paste-check.py` | The write behind a pasted picture, and the endpoint that does it |
| `tests/turn-check.py` | The queue behind a panel-run turn, and stopping one |
| `tests/chat-check.mjs` | The change a message carries, drawn without a browser |
| `tests/composer-check.mjs` | The composer's own template, drawn without a browser |
| `tests/drop-check.mjs` | A file dropped on the box, in a real browser: the path typed in, and the copy saved when there is no path |
| `claude-watchtower.service` | Optional systemd user unit |
| `docs/cleanup-plan.md` | The staged refactor this layout is partway through |

## Tests

There are two suites: unit tests over the readers, which need nothing, and the
UI checks, which drive a real browser against a running panel.

Before either, if you have a `tsc` to hand, there is a type check:

```bash
tsc --noEmit
```

`tsconfig.json` is checked in and configured for this and nothing else — the
build does not read it, so this stays optional and the project still installs
nothing. `web/src/types.ts` is where the shapes the server sends are written
down, hand-written from the Python that emits each one, so a payload that has
changed shows up here before it shows up on screen. It is clean at the settings
in that file; `noImplicitAny` and `strictNullChecks` are not on yet, and
`docs/cleanup-plan.md` says what turning each on would cost.

```bash
python3 -m unittest discover -s tests/python
```

These cover the parsing and arithmetic the panel is built on — the git status
and log readers, the `/usage` report, how a stale status expires, what a
transcript block summarises to, and the per-model cost. Standard library only,
no fixtures, no panel, well under a second.

The UI checks want a panel with something to show. `tests/fixtures.py` stands
one up with a session in every state:

1. Start the fixtures, and leave them running:

```bash
python3 tests/fixtures.py
```

Give it a minute before believing what you see. A session first seen gets the
benefit of the doubt until there are two CPU readings to compare, so everything
reads as working for the first fifty seconds or so — including the fixtures
whose whole point is to settle to ready.

2. Start a panel against them — the command is printed for you:

```bash
CLAUDE_WATCHTOWER_SESSION_DIR=/path/to/fixtures python3 server.py --port 8788
```

3. Start a throwaway browser with CDP open:

```bash
google-chrome --headless=new --remote-debugging-port=9333 \
  --user-data-dir=$(mktemp -d) about:blank
```

4. Run the checks:

```bash
node tests/ui-check.mjs
```

`tests/composer-check.mjs` needs neither a browser nor a running panel: it lifts the composer's template out of the page, hands it a session in each state and asserts what it draws — that a live session still shows the mode bar, disabled and saying what would free it, that a kept one's chips can be picked, that a standing prompt takes the box's room, and that a blocked session leads with its reason. It also drives the drop side of the box away from a browser: that a dragged `file://` URI becomes the path it stands for, escapes and all, that several files come in as a list, that a file on another host or a dragged link is refused rather than invented, and that the path lands at the caret spaced off the words either side. `node tests/composer-check.mjs`, any Node, no setup. It exists because a duplicate `const` in that function turned the whole inline script into a syntax error, and nothing else in the suite runs without a Chrome.

`tests/paste-check.py` needs neither a browser nor a panel of your own: it drives the write behind a pasted picture, then the endpoint over a real socket with a fake session standing in for a running one. That the file lands under `.claude/watchtower-images/` in the session's own folder and carries the bytes off the clipboard, that two pastes in the same second do not collide, that anything which is not a picture — HTML, a shell script — is refused rather than written with an extension of its choosing, that a body far too big for any screenshot is turned away without being held in memory, that the fortnight sweep clears what an earlier paste left while leaving the fresh ones, and that with sending off nothing is written at all. The drop side of it too — the copy a pathless drag has to be saved as: that it lands in `.claude/watchtower-files/` under the name it was dropped as, that a name which tried to be a path is only ever a name, that a hidden name cannot hide the copy, that a second drop of the same name is a second file, and that its kind is not judged the way a paste's is. `python3 tests/paste-check.py`.

`tests/chat-check.mjs` lifts the functions that draw a file change in the conversation and asserts what they draw. Folded: that a tool which changed nothing carries nothing, that the patch is coloured line by line and folded until it is asked to open, that the bar counts what it did and says how much more there is, that the folded patch is a click target of its own, that opening keeps the preview on screen until the whole change lands, and that what a patch contains is escaped rather than run. Opened: that the hunk header is a row across both sides, that an unchanged line is the same row on both, that a removal and the line that replaced it share a row while a removal with nothing to answer it leaves the other side empty, that the numbering carries on correctly past a change of unequal length, and that both panes are drawn and named. `node tests/chat-check.mjs`, any Node, no setup.

`tests/math-check.mjs` lifts the TeX converter out of the page and checks both halves of it: that a fraction is a fraction, that a `bmatrix` is a table with a row per line and brackets its own height, that greek is the letter rather than its name, that `\text` keeps its spaces, that an under-brace puts its label under the brace, that an unknown command is shown rather than swallowed, and that what is inside is escaped rather than run. Then the harder half — which dollar signs count. A page of real maths out of a real conversation has to come out with every display and every inline drawn, no dollar signs and no backslash commands left on screen; and `it costs $5 or $6`, `$PATH:$HOME`, `$(pwd)` and a `$S$` inside code marks all have to be left exactly as they were written. `node tests/math-check.mjs`, any Node, no setup.

`tests/change-check.py` writes a transcript by hand into a temp folder and reads it back through the panel's own reader: that a change is filed under the tool call that made it, that a command which only printed something carries none, that a file written whole reads as all added, that a subagent's edits stay out with the rest of its sidechain, that the preview starts one line above the first thing that changed, and that the whole patch comes back when it is asked for by id. `python3 tests/change-check.py`.

`tests/turn-check.py` needs neither, and no panel either: it drives what the server does with a turn it is running — against a fake pipe, so nothing is started and nothing is sent, and every assertion is on what the panel writes down that pipe and when. The queue: a message typed mid-turn is held rather than refused, order and cap hold, a `result` frame is what drains it, a message sent in the gap between a turn ending and the queue draining does not overtake one already waiting, dropping takes back only what has not gone in yet, and anything still waiting when the process dies goes to the deliverer while letting go of a session deliberately clears it. And stopping: one `interrupt` on the control channel, the queue dropped with it, and the interrupted turn's own `error_during_execution` result read as *you stopped it* rather than as a turn that went wrong — while a turn nobody stopped still reads as one that did. `python3 tests/turn-check.py`.

`tests/ui-check.mjs` drives a throwaway headless Chrome over the DevTools protocol and asserts the things a screenshot cannot: that every MD3 token resolves, that the four state containers are distinct and stay distinct after the base colour changes, that every piece of text on screen clears 4.5:1, that the index lists each session with a host icon and a state lamp, that clicking a row opens its detail and every tab renders, that the filter chips filter, that sessions sharing a folder group themselves and picked rows can be grouped, folded and ungrouped by hand, that a row dragged to the top of its list goes there and stays there through a poll while the rows around it keep their order, that alt with an arrow is the same move and that the state sort can be had back from the menu, that the plan chip shows how much has gone, colours each figure for its band and opens a dialog with a bar per limit — skipped, with a reason, on a read-only panel or where `/usage` does not answer — that the settings page changes the scheme and persists it, that Usage either shows a cost and a row per model or says plainly that there is nothing to total yet, and that interactive targets reach 48dp. Node 24+, no dependencies. Override `PANEL_URL` / `CDP_URL` to point elsewhere.

The Git checks want a session whose folder is in a repository: they find one from `/api/state`, then assert that both tabs appear, that Git reads the branch and marks every file with its status letter and a way to open it while carrying no graph, that the files land in the editor's three groups, that the commit box and its split button are there when writing is on — and that a read-only panel says so and offers nothing — that a row opens exactly one diff and closes it again, that History draws one node per commit and keeps no file list, that each rail is the same height as its row — a mismatch there is what leaves the lanes broken at every join — that both clear 4.5:1, and that all five tabs stay reachable at 48dp. With no such fixture they say so and skip rather than failing for a reason that has nothing to do with the panel.

**No check stages or commits anything.** The suite runs against whatever real sessions are on the machine, and a test that commits in somebody's checkout to prove a button works has done more than it was asked. It asserts that the controls are there and wired; the one action it actually performs is opening a diff, which only reads.

`tests/drop-check.mjs` is the one check that has to be a browser, because what it is checking is what a browser puts on a drag. It wants the same panel and the same headless Chrome as the UI checks, then drops a `text/uri-list` on the box and asserts the path is typed in at the caret and quoted for the space in it; drops a `File` with no path on it — which is exactly the shape of a drag out of Chrome's downloads — and asserts the copy is saved into the session's folder under the name it was dropped as, that the strip says plainly that it is a copy, that the box itself stays empty, and that the file really is on disk with the bytes off the drag; and asserts that a drag of plain text is left to the browser. `node tests/drop-check.mjs`.

The commenting checks want a session with a readable transcript, which fixtures do not have: they find one, prefer a quiet session over a working one — a busy session rewrites its transcript underneath the run — and sweep the transcript's scroll positions looking for a run of text genuinely on screen, since a viewport holding only a table or a tool row has nothing to select however long you wait. They then assert that selecting a passage raises a bar offering Copy and Comment, that Copy puts the passage on the clipboard, that Comment opens a card carrying that passage with the caret in it and level with the mark in the transcript, that the rail is a margin when there is room and a popover clamped on screen when there is not, that nothing is sendable until a remark is written, that a card being typed in survives several polls, that a second passage opens a second card without the two overlapping, that a selection across bubbles becomes one card each, that a passage out of a code block goes back fenced rather than flattened, that a tool row can be commented on, that Alt+C opens a card from the keyboard, that sent comments leave the rail while their marks stay, and that Escape puts the bar away. The highlight is measured on **both** kinds of bubble against the ground each actually sits on.

**Nothing in the run messages a live session.** `/api/say` and `/api/start` are intercepted and their bodies kept, which is also how the wire format is asserted — what the panel would have sent, without a real Claude ever seeing it. A highlight sharing a role with its bubble disappears, and it disappeared on user messages only, so a check that reads one bubble and calls it done misses exactly the half that was broken. With no such session they say so and skip.

Point it at a real panel to measure the chat bubbles too, since fixture sessions have no transcript:

```bash
PANEL_URL=http://127.0.0.1:8787 node tests/ui-check.mjs
```

## API

| Route | Purpose |
|---|---|
| `GET /api/state` | Every live session, with status, trace, window match, and the `question` it is standing at if it is standing at one , plus `moved`: where a cleared session went, so a browser holding the old id can follow it |
| `GET /api/transcript` | `?sessionId=…&limit=…` — the recent conversation |
| `GET /api/change?sessionId=…&id=…` | The whole of one file change, by the tool-use id its preview in the chat carries: the patch as unified text, what it added and removed, and whether it was long enough to be clipped |
| `GET /api/usage` | `?sessionId=…` — that session's token totals per model, the cost they come to, and the size of its last context |
| `GET /api/commands` | `?sessionId=…` — the skills and slash commands that session could be asked for, read from the project's folders, yours, and any enabled plugin's. A session that has gone is answered with what is true of every session rather than a 404 |
| `GET /api/plan` | The subscription's limits, read by running `claude --print /usage`; `?force=1` skips the five-minute cache; loopback only |
| `GET /api/update` | Whether a newer release is tagged on this checkout's own remote: the release HEAD is on, the newest one, the notes in between, and whether it can be applied; `?force=1` skips the six-hour cache; loopback only |
| `GET /api/changelog` | The changelog as it is written down in this checkout, as text for the browser to render. Read from disk on every ask, since it changes exactly when the panel updates itself. 404 for a copy that has none |
| `GET /api/file` | `?sessionId=…&path=…` — a picture a message names, so the message can show it. The path must already exist and be inside your home folder or the session's own, and it must be a picture: png, jpeg, gif, webp, avif, bmp or svg, up to 32MB. Sent with `Content-Security-Policy: sandbox`. Not loopback-only, since it reads rather than acts — a phone showing a conversation needs the pictures in it |
| `GET /api/reach` | The address a phone should point at — looked up now rather than at startup, because a laptop moves between networks — with the key on it. Empty when the panel is on loopback only |
| `GET /api/qr` | The same address as an SVG code to point a camera at. 404 when there is nothing to point at |
| `POST /api/update` | `{"tag": "v1.4.0"}` — check that release out, rebuild the frontend and restart the panel on it. The tag is checked against what the server reads for itself rather than trusted; loopback only |
| `POST /api/update/channel` | `{"channel": "release"|"development"}` — which line this install follows: the release tags, or the tip of the development branch. Remembered outside the checkout, since an update replaces the checkout. Loopback only, like the update it decides the target of |
| `GET /api/git` | `?sessionId=…` — that session's repository: branch, upstream drift, changed files, recent commits with their parents, and the branches it could switch to |
| `GET /api/git/diff` | `?sessionId=…&path=…&staged=1` — one changed file's unified diff, one side at a time |
| `POST /api/git` | `{"sessionId": "...", "action": "...", …}` — one source-control action: `stage`, `unstage`, `discard` (each with `paths`), `stageAll`, `unstageAll`, `discardAll`, `commit` (`message`, `amend`, `stageAll`), `push` (`force` uses a lease), `pull`, `fetch`, `sync`, `stash`, `stashPop`, `switch` (`branch`, `create`, `from`), `suggestMessage` (answers with `text`, the message it wrote); loopback only |
| `POST /api/focus` | `{"sessionId": "..."}` — raise that session's window |
| `POST /api/identify` | Ask a session's terminal which window it is showing, and remember it |
| `POST /api/pair` | Click a window to bind it to a session |
| `POST /api/unpair` | Forget a manual pairing |
| `POST /api/sticky` | `{"sessionId": "...", "pinned": true}` — pin this session's row, so it is still here after the panel restarts. Unpinning does not remove the row: a session the panel runs keeps one for as long as it runs |
| `POST /api/forget` | `{"sessionId": "..."}` — take a kept row off the list, pinned or not, stopping the session first if the panel was running it. The transcript is left alone. `/api/end` covers the ordinary case; this is the one for a row with no process behind it |
| `POST /api/start` | `{"sessionId": "...", "text": "..."}` — resume a kept session in a terminal, delivering `text` once it listens; loopback only. Nothing in the panel calls it any more: the composer's *In a terminal* was its only caller and is gone. Kept because the endpoint is the documented way to ask for a terminal, and because `deliver_later` still starts a session this way when a message is waiting for one that has closed |
| `POST /api/editor` | `{"sessionId": "...", "path": "...", "line": 12}` — open something. Without a path it is the session's own folder in VS Code, read off the session. With one — how a path clicked out of a conversation is opened — a file goes to VS Code at `line`, a folder to the desktop's own opener (`xdg-open`); the path must already exist and be inside your home folder or the session's own, or it is refused. Loopback only |
| `POST /api/new` | `{"sessionId": "..."}` — a fresh session in a terminal, in that session's folder; the folder is read off the session, never taken from the request; loopback only |
| `POST /api/new-folder` | A fresh session in a folder chosen at a chooser on this machine. Takes no path — the body is ignored — and answers `cancelled` when nobody picked one; loopback only |
| `POST /api/rename` | `{"sessionId": "...", "name": "..."}` — name a session yourself; an empty name puts its own name back |
| `POST /api/end` | `{"sessionId": "...", "force": false}` — SIGTERM that session, or SIGKILL when `force`. Its row goes too unless pinned; the answer's `removed` says whether it did |
| `POST /api/owned/mode` | `{"sessionId": "...", "mode": "default\|plan\|acceptEdits"}` — remember the mode the next panel-run turn uses; starts nothing; loopback only |
| `POST /api/owned/adopt` | `{"sessionId": "...", "force": false}` — keep the row, end the terminal session holding it, and leave its next turn to the panel. A session that has never spoken is started here under its own id rather than resumed. Refuses rather than reporting success if the process does not actually stop; loopback only |
| `POST /api/owned/new` | `{"sessionId": "..."}` for a folder off that session, or `{"pick": true}` for one from a chooser — a new session the panel runs, named by the panel and answering with that name. Takes no first message: the session exists before anything is said to it; loopback only |
| `POST /api/owned/answer` | `{"sessionId": "...", "requestId": "...", "behavior": "allow\|deny", "answers": {"<the question>": ["Label"]}}` — settle the prompt a panel turn is standing on. The `requestId` must be the one now standing, so an answer meant for a prompt that has gone is refused; loopback only |
| `POST /api/owned/say` | `{"sessionId": "...", "text": "...", "mode": "..."}` — run one turn on that session's transcript with `claude --print --resume`, answering as soon as it is launched. A turn already running does not refuse it: the message is held and goes in when that turn ends. Refuses if a live process holds the session, or if ten messages are already waiting; loopback only |
| `POST /api/owned/interrupt` | `{"sessionId": "..."}` — stop the turn a session the panel holds is in the middle of. Anything typed ahead of it is kept and held rather than sent or dropped. Refuses for a session the panel is not holding, or one that is not working; loopback only |
| `POST /api/owned/compact` | `{"sessionId": "..."}` — summarise the conversation so far and carry on from the summary, by sending `/compact` down the held pipe. Refuses mid-turn, with anything queued, while a compaction is already running, or for a session something else is running; loopback only |
| `POST /api/owned/clear` | `{"sessionId": "..."}` — start that session's conversation again, empty, in the same folder: `/clear` down the held pipe, which is the one transport that expands it. Refused mid-turn, with something typed ahead waiting, or for a session in a terminal. Answers with the id the session has *become*, since clearing starts a new conversation rather than emptying the old |
| `POST /api/owned/unqueue` | `{"sessionId": "...", "index": 0}` — take back something typed ahead, before the session reaches it. Omit `index` to drop everything still waiting. Answers with what is left; loopback only |
| `POST /api/owned/resume` | `{"sessionId": "..."}` — let go of a queue a stop held back: it drains one at a time and in order, as an unstopped queue does. Refuses if nothing is waiting, if nothing is holding it, or if the panel is not running that session. Answers with what is left waiting |
| `POST /api/say` | `{"sessionId": "...", "text": "..."}` — send a message into that session; loopback only |
| `POST /api/paste-image` | `{"sessionId": "...", "mime": "image/png", "data": "<base64>"}` — write a pasted picture into that session's folder under `.claude/watchtower-images/` and answer with its `path`, for the message to name. The folder is the session's own and never comes from the request; the extension comes from `mime`, which must be one of PNG, JPEG, GIF, WebP or BMP, and the picture from 12 MB down; loopback only |
| `POST /api/drop-file` | `{"sessionId": "...", "name": "report.pdf", "data": "<base64>"}` — write a dropped file that came with no path of its own into that session's folder under `.claude/watchtower-files/` and answer with its `path`. The folder is the session's own and never comes from the request; the name is reduced to a name and can only land inside that folder; 32 MB down; loopback only |

A dead process is never reported: each session file records the pid's start time, and the panel re-checks it against `/proc` so a recycled pid cannot masquerade as a live session.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

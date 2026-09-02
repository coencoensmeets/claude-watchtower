# Changelog

What changed in the panel, newest first. Releases are the tags on this repository — `v0.1.0` and so on — which is also what the panel's own updater reads, so a heading here has a tag beside it in **Settings → Panel version**.

Entries say what changed and, where it is not obvious, why. Anything worth a paragraph gets one: the reasoning is the part that is expensive to reconstruct later.

## Unreleased

- **Open in terminal stops telling you to wait for a turn that will not finish.** A session parked on a permission ask is mid-turn — the turn really is in flight — so the hand-back was refused with *let that finish first*, on a turn whose only ending is the answer being asked for. The refusal stands, because letting go of the pipe abandons a prompt nobody else can answer; the sentence now names the two ways out instead: answer it, or stop the turn. The button and the server's own 409 say the same words.
- **A session says how many subagents it is running**, and a `Task` row in the conversation opens what that agent did — its own conversation, tools and changes included, drawn the way the session's is. A session that has fanned out six agents used to read as one session doing one thing, and everything those agents did was missing from between the row that sent them and the answer that came back.
- **A subagent's conversation is drawn as somebody else's.** It reads in the panel's second colour, on a deeper surface, behind a rail: the errand it was sent on is the headline, what was said *to* it is a briefing rather than a bubble in your own colour, and while it is still working the transcript says so at the foot rather than ending mid-thought. Two conversations that look alike are two conversations you will confuse.
- **The agents a session has out are named over the box you type in**, one chip each, and a chip opens what that agent is doing. A running subagent is the one thing a working session is doing that the conversation cannot show you: the row that started it is however far back you have scrolled.
- **A session's spend now includes its subagents.** Each one keeps its own transcript, so the bill was counting only what the session itself said — on a fanned-out session that is a fraction of what was actually paid for.
- **And the pane follows a cleared session rather than dropping you elsewhere.** Clearing gives a session a new id, so the row you were reading disappears and the panel used to fall back to whatever was at the top of the list. The feed now carries where it went, and the selection follows.
- **`/clear` typed into the box works too, and does not strand the panel.** Following a cleared session to its new id was conditional on the panel's own button having asked for it, which left the command you can type doing half the job: the conversation cleared and the row went on showing a transcript that had stopped growing. The reader now follows the session whenever the id on its pipe changes, however it changed.
- **Clear works, on the transport where it can.** A **Clear** button beside *Compact* for a session the panel runs: `/clear` down the held pipe, which expands it, where a session's messaging socket queues it with expansion off and turns it into prose. The catch is that Claude Code does not empty a conversation in place — it starts a new one under a new session id — so the panel follows the session there: the held process, the queue, the mode, the row, and the name you gave it all move together, and the old transcript stays on disk. For a session in a terminal `/clear` is still the terminal's own, and the panel still says so rather than sending something that does nothing.

- **The address on the phone page carries its own copy button.** A copy icon on the right-hand end of the address, in the box the address already sits in, instead of a *Copy the address* button underneath saying what it would copy. It ticks when it has, the way the copy on a code block does.

- **Pictures in a message are shown.** A screenshot, a plot, a picture pasted into the box: named as markdown, as an `<img>` tag, or as the `[Pasted image: …]` line the composer writes, it is drawn in the conversation instead of described in it. The panel serves the file itself, and only if it exists, is a picture, and sits inside your home folder, the session's own, or the system temp folder. A remote image is still not fetched, and a tag is rebuilt from its `src` rather than passed through.
- **Maths in a message is drawn as maths.** TeX between dollar signs — inline or as a display — is rendered to MathML, which the browser draws itself: fractions, matrices, scripts, stretching delimiters, accents, the greek alphabet, `\text`, `\mathbb`, `\underbrace`, sums with their limits. The converter is written for this panel rather than pulled in: KaTeX is 280KB of script and a megabyte of fonts against a panel whose whole assets folder is 156KB, and nothing here should need installing. What it cannot read it hands back as the TeX that was written.
- **An update channel, so unreleased features can be tried.** **Panel version** in Settings now picks between **Releases** — the tags, which is the default and what everybody else is running — and **Development**, which follows the tip of the `develop` branch: written and merged, not released yet. The rules do not change with the channel: nothing moves over uncommitted work, nothing takes the browser's word for the target, and the checkout lands detached on the commit it was offered. The choice is remembered outside the checkout, because an update replaces the checkout.
- **Open in terminal works again.** It read the flag for *the panel is holding this session* as if it meant *a turn is running*, and the button is only drawn for sessions the panel holds — so it was disabled for the whole of a session's life, explaining itself by asking you to wait for a turn that had usually finished minutes ago. It now asks the same question the server does, and says how many typed-ahead messages the hand-back would take with it.

## 0.2.1 — 2026-08-27

- **A click on an icon does what the icon says.** Every icon-only control in the panel — the copy button on a code block, the ⋯ on a turn, the buttons in the app bar — is a button with an `<svg>` filling its face, and the delegated hit test insisted the thing clicked was an HTML element. An `<svg>` is not one. So the ring of button around the glyph answered and the middle did not, which reads as a button that works if you hit it just right and is dead if you aim at it. The copy button was the worst of them, being almost entirely glyph.
- **Copying is synchronous now**, inside the click that asked for it, rather than after an `await` that some browsers treat as the end of the gesture — and when the clipboard cannot be reached at all, the text is selected instead so Ctrl+C finishes the job, rather than a snackbar reporting failure and leaving you to find it again.

## 0.2.0 — 2026-08-27

- **A path written in a conversation is a link.** Click one and it opens: a file goes to the editor at the line the message quoted it at, a folder goes to whatever the desktop opens folders with. The tool lines above a turn are clickable too, so the file an `Edit` touched is one click away. What counts as a path is deliberately narrow — rooted, or a folder and a suffix, or a bare name inside code marks with a known extension — because a false positive turns prose into a live control.
- **The session header folds away at every width, and remembers it.** It was a phone control on the reasoning that a header beside a conversation costs nothing; it costs five lines of preamble over every session. Folding on scroll stays phone-only, where the header is most of the glass.
- **Stopping a turn keeps what you typed ahead of it.** It used to throw the queue away, on the reasoning that a train of thought you have just stopped should not carry on regardless. Half of that was right: the messages no longer go in on their own. Deleting them was the wrong half — it is minutes of your writing — so they stay on screen and the strip asks what you want done with them: send them, drop them, or type something else, which goes in last.
- **Every code block in a message carries the button that copies it**, because selecting one by hand means dragging across a box that scrolls sideways under the finger.
- **The app bar says when the panel has stopped answering**, rather than leaving a page quietly showing a reading from ten minutes ago.
- **This changelog**, and a button beside *Check for updates* that reads it without leaving the panel.

## 0.1.0 — 2026-08-25

The first tagged release, and the one that made the updater useful: with no tags in the repository it had nothing to compare against and said so.

### The panel

- **An index and a detail pane**, in Material 3's list-detail shape. Every session on the machine, one row each, sorted so anything waiting on you is at the top — or dragged into an order of your own, which then survives sessions going busy and idle again.
- **State that is inferred rather than believed.** Claude Code writes a status only when it changes, so an old `busy` proves nothing. The panel asks for a second opinion — CPU over five seconds, and whether the transcript is still growing — before it overrules a reading, and holds a session's liveness across the quiet gaps inside a turn.
- **The conversation**, read from the transcript on disk: messages, tool calls, and the patch a change carries — folded to a few lines, or opened full-width as a side-by-side comparison.
- **Git and History** for the repository a session works in, including staging, committing and the diff of a single file.
- **Usage and cost**, totalled from the transcript at Anthropic's published per-token prices, and how much of the subscription has gone in the app bar.

### Talking to a session

- **A composer** that sends over the session's own messaging socket, with the three limits it has stated plainly rather than hidden.
- **Pictures pasted** into the box and **files dropped** on it are saved and sent as paths, because every transport a session has takes a string.
- **Turns run by the panel itself**, for a session whose terminal has closed — the permission mode picked per turn, and the prompts it raises answered from here.
- **Commenting on a passage**: select it and the remark goes back attributed, as a quote the session can find in its own transcript.
- **The question a blocked session is standing at**, drawn as a card so you can read it without going to find the terminal.

### On a phone

- **Served to the local network by default**, guarded by a key that nothing off this machine is answered without. `--local` keeps it to this machine.
- **A port of its own**, picked once for the install and kept, so the address is the same tomorrow and two panels on one network do not collide.
- **A code to scan** in Settings, so the address and its key need not be typed.
- **The panel itself works on a phone**: the actions on a turn reachable without a right-click, press-and-hold on the list, and the shell measured against the visible glass rather than the glass plus a toolbar.

### Taking things out

- **A message downloads as markdown** from a right-click or the button on the turn — the markdown that was written, not the HTML it was drawn as.

### Updating itself

- **The panel updates from these tags.** It reads them out of its own checkout, offers only what it does not already have, and refuses to move over uncommitted work or off a branch of your own.

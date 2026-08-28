/* Sessions the panel runs itself — the mode it runs them in, the prompt a turn
   is standing on, what is typed ahead of it, and how full the conversation is. */

import { reloadState } from "../refresh.js";
import { displaySince, stateKeyOf } from "../sessions/state.js";
import { app, chat, ui } from "../state.js";
import { clockOf, duration, escapeHtml, shorten, tokens } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { imagesFor } from "../ui/images.js";
import { showSnackbar } from "../ui/snackbar.js";
import { headerActions, sendBlockedReason, traceFor } from "./chat.js";
import { plan } from "./plan.js";

/* The mode a session the panel runs is in, as the one row of chips that changes
   what it may do. No tick on the chosen one: the fill says it, and a tick beside
   a filled chip is the same fact told twice. */
/* How full the conversation is, and the way to make it smaller.

   The reading is the last request's total input — fresh tokens, cache reads and
   cache writes together, which is everything the model was carrying when it last
   answered — against that model's context window. It comes off the transcript,
   so it is a fact about the conversation rather than about who is running it,
   and every session has one, terminal or not.

   Compacting is not like that. It needs the held pipe: a slash command sent over
   a session's messaging socket is queued with expansion switched off and does
   nothing, while the pipe a panel turn owns expands it. So the reading is drawn
   for everyone and the button only for a session the panel runs; in a terminal,
   `/compact` at its own prompt is the way.

   Past halfway is when the button appears. Below that, offering to throw away
   the middle of a conversation with half its room still free is an offer to lose
   something for nothing. */
const COMPACT_AT = 0.5;
/* Where the bar changes colour. Not the same question as when to offer the
   button: these say *this is getting tight*, and the last one is roughly where
   Claude Code stops waiting and compacts on its own. */
const CONTEXT_TIGHT = 0.75;
const CONTEXT_FULL = 0.9;

/* How far along a compaction is — and it is an estimate, not a measurement.
   Nothing on the wire says how much of the conversation has been summarised: the
   pipe sends `compacting`, then silence, then the result. So this is elapsed
   time bent through a curve, which is exactly what the terminal does with the
   same numbers — Claude Code 2.1.239 draws its own bar from

     1 - exp(-seconds / 90), capped at 95%

   and the cap is the honest part: it never claims to be finished, because it
   does not know. Copied rather than invented so the two agree on screen: a
   compaction watched in both places should not be 40% in one and 70% in the
   other. The title says what it is measuring, so nobody reads it as progress. */
const COMPACT_TAU = 90;
const COMPACT_CAP = 95;
export const compactPct = (elapsed) => Math.min(
  COMPACT_CAP, Math.round((1 - Math.exp(-Math.max(0, elapsed) / COMPACT_TAU)) * 100));

function contextBar(session) {
  const ctx = session.context;
  if (!ctx || !ctx.tokens) return "";
  const share = Math.min(1, ctx.share || 0);
  const pct = Math.round(share * 100);
  const tight = share >= CONTEXT_FULL ? 2 : share >= CONTEXT_TIGHT ? 1 : 0;
  const done = ownedFor(session).compact;
  const running = !!done?.running;
  // The offer stands on the same footing as the mode chips: a turn the panel can
  // run. A terminal session gets the reading and no button.
  const offer = share >= COMPACT_AT && runsHere(session) && app.feed.canSend;
  // What the last compaction actually did. Worth saying because the reading
  // above it does *not* move when a conversation is compacted — it is taken from
  // the last request the model answered, and the next one has not happened yet.
  // Without this the button looks like it did nothing.
  // While it runs the bar stops reporting the conversation and reports the
  // compaction instead — the reading underneath it cannot move until the next
  // turn anyway, so an unchanging bar beside the word "compacting…" was the one
  // moment the bar had nothing to say. See compactPct for what the number is.
  const going = running ? compactPct(Date.now() / 1000 + app.skew - (done.at || 0)) : 0;
  const said = running ? `compacting… ${going}%`
    : done && done.ok === false ? `did not compact: ${done.message || "it would not"}`
    : done && done.before && done.after
      ? `compacted ${tokens(done.before)} → ${tokens(done.after)}${
          done.trigger === "auto" ? ", on its own" : ""}`
    : "";
  return `<div class="ctx" title="${escapeHtml(
      `${ctx.tokens.toLocaleString()} tokens of a ${ctx.window.toLocaleString()} window`
      + (ctx.model ? ` on ${ctx.model}` : ""))}">
      <div class="ctx__bar" role="img" ${running ? `data-compact-since="${done.at || 0}"
        title="How long it has been going, not how much is left — the same estimate the terminal draws"` : ""}
        aria-label="${running ? `Compacting, about ${going}% of the way`
          : `Conversation is ${pct}% of the context window`}">
        <div class="ctx__fill" style="width: ${Math.max(2, running ? going : pct)}%"
          data-tight="${running ? 0 : tight}" data-going="${running ? 1 : 0}"></div>
      </div>
      <span class="ctx__read md-label-small">${pct}% of ${tokens(ctx.window)}</span>
      ${offer ? `<button class="button button--text md-state ctx__do" type="button"
          data-act="compact" ${running ? "disabled" : ""}
          title="Summarises the conversation so far and carries on from the summary">
          ${ICON.compact} ${running ? "Compacting…" : "Compact"}
        </button>` : ""}
      ${said ? `<span class="ctx__said md-label-small"
        data-bad="${done && done.ok === false ? 1 : 0}">${escapeHtml(said)}</span>` : ""}
    </div>`;
}

function modeBar(session) {
  if (!runsHere(session) || !app.feed.canSend) return "";
  const owned = ownedFor(session);
  return `<div class="mode-bar" role="group" aria-label="Permission mode">
      ${Object.keys(OWNED_MODE_LABEL).map((key) => `
        <button class="mode-chip md-state md-label-small" type="button" data-mode="${key}"
          aria-pressed="${String((owned.mode || "default") === key)}"
          data-mark="${key}">${escapeHtml(OWNED_MODE_LABEL[key])}</button>`).join("")}
    </div>`;
}

export function detailHeader(session, state, host) {
  // No host to name and no uptime worth counting: a kept row has no process, and
  // a held one's process is the panel's own pipe rather than a terminal.
  const stopped = runsHere(session);
  // A stopped session has no host to name and no uptime to count — what it has
  // is a folder to come back to and a time it was last seen.
  const meta = stopped ? [`<span class="md-mono md-body-small">${escapeHtml(shorten(session.cwd, 3))}</span>`] : [
    `<span class="host md-label-small">${host.icon}${escapeHtml(host.label)}</span>`,
    `<span class="md-mono md-body-small">${escapeHtml(shorten(session.cwd, 3))}</span>`,
  ];
  if (session.branch) meta.push(`<span class="meta-sep">·</span><span class="md-mono md-body-small">${escapeHtml(session.branch)}</span>`);
  meta.push(stopped
    ? `<span class="meta-sep">·</span><span class="md-body-small">kept here</span>`
    : `<span class="meta-sep">·</span><span class="md-body-small">up ${duration(Date.now() / 1000 + app.skew - session.startedAt)}</span>`);
  // The transcript's own reading first — it is the fresher of the two — and the
  // one that came with the session behind it, so the subject is there on the
  // first paint rather than a moment after it.
  const title = (chat.transcriptFor === session.sessionId && chat.transcript?.title) || session.title || null;
  const folded = ui.headerFolded;
  return `<header class="detail-header" data-folded="${folded ? 1 : 0}">
      <div class="detail-header__top">
        <div class="detail-header__text">
          <div class="detail-header__title">
            <h2 class="md-headline-small">
              <button class="name-button md-state" data-act="rename" title="Click to rename this session"
                      aria-label="Rename ${escapeHtml(session.name)}"><span>${escapeHtml(session.name)}</span>${ICON.pencil}</button>
            </h2>
            <span class="md-label-large">${escapeHtml(state.label)}
              <span class="md-mono md-body-small" data-since="${displaySince(session)}">${duration(Date.now() / 1000 + app.skew - displaySince(session))}</span></span>
          </div>
          <div class="detail-header__fold" id="headerFold"><div class="detail-header__fold-inner">
            ${title ? `<p class="detail-header__subtitle md-body-medium">${escapeHtml(title)}</p>` : ""}
            ${modeBar(session)}
            ${contextBar(session)}
            <div class="detail-header__meta">${meta.join(" ")}</div>
          </div></div>
        </div>
        <div class="detail-header__actions">${headerActions(session)}${terminalAction(session)}${editorAction(session)}</div>
        ${foldButton(folded)}
      </div>
      ${traceFor(session)}
    </header>`;
}
/* The handle on the fold. Last in the header's top row and hard against the
   right edge, which is the one place it does not move between the two states:
   the actions column beside it goes when the header folds, and a handle in the
   title row would slide across the pane as it went. The chevron turns rather
   than being swapped — the same control saying which way it will go next. */
function foldButton(folded) {
  return `<button class="icon-button md-state fold-button" type="button" data-act="fold"
      aria-expanded="${String(!folded)}" aria-controls="headerFold"
      title="${folded ? "Show the session's details" : "Fold the details away"}"
      aria-label="${folded ? "Show the session's details" : "Fold the details away"}">${ICON.chevron}</button>`;
}
/* Handing an interactive session back to a terminal — `claude --resume` in a
   window of your own, on the same conversation.

   The other half of *Make interactive*, and offered in the same place that one
   is not: a session the panel runs has no window to focus, so the actions column
   holds only the editor button, and this goes above it. A session already in a
   terminal is left alone — it is already where this would put it, and *Focus
   window* is the button for that.

   Not offered mid-turn. The panel refuses it anyway — a terminal opened on a
   transcript a panel turn is halfway through is two processes on one
   conversation — and a disabled button saying so is a better answer than a
   snackbar after the press. Mid-turn is the same question the server asks, and
   the same word for it: `busy`, a turn in flight, rather than `running`, which
   is only whether the panel holds the session at all.

   Whatever is typed ahead of the session goes with the hand-back, which is why
   the hint counts it rather than the queue quietly emptying. */
function terminalAction(session) {
  if (!runsHere(session) || !session.cwd) return "";
  const owned = ownedFor(session);
  // Mid-turn is `busy`, not `running`. `running` is whether the panel is
  // holding this session's process at all, which is true of every session this
  // button is drawn for — the row has to be one the panel runs before it is
  // offered — so reading it as "a turn is in flight" disabled the button for
  // the whole of its life and explained it by saying to wait for a turn that
  // had usually finished minutes ago. `busy` is the flag /api/start itself
  // refuses on, and the two now say the same thing.
  const midTurn = owned.busy === true;
  const can = app.feed.canSend && !midTurn;
  // What goes with the hand-back. Letting go deliberately clears the queue —
  // that session is about to be somebody else's — and after a stop the queue is
  // held rather than dropped precisely so that nothing you typed disappears
  // without being mentioned. This is the other place it could.
  const waiting = (owned.queued || []).length;
  const alsoGoes = waiting === 1 ? ", and the message waiting behind it goes too"
    : waiting ? `, and the ${waiting} messages waiting behind it go too` : "";
  const why = midTurn ? "A turn from the panel is running on it — let that finish first"
    : !app.feed.canSend ? "opening a terminal needs the panel on loopback"
    : `Resume this session in a terminal — the panel lets go of it${alsoGoes}`;
  return `<button class="button button--outlined md-state detail-header__terminal" data-act="terminal"
                  ${can ? "" : "disabled"} title="${escapeHtml(why)}">${ICON.terminal} Open in terminal</button>`;
}

/* Opening the session's folder in VS Code. Offered whatever state the session is
   in — a stopped one still has the checkout you were working in — and disabled
   rather than dropped when the panel cannot act, so its absence is never read as
   a missing feature.

   Turned off in Settings it goes away entirely: someone who does not open their
   sessions in an editor is not helped by a disabled button explaining why. */
function editorAction(session) {
  if (!session.cwd || !app.settings.showEditor) return "";
  const can = app.feed.canSend;
  return `<button class="button button--outlined md-state detail-header__editor" data-act="editor"
                  ${can ? "" : "disabled"} title="${can ? `Open ${escapeHtml(session.cwd)} in VS Code`
                    : "opening an editor needs the panel on loopback"}">${ICON.editor} Open in VS Code</button>`;
}

/* What the panel knows about turns it runs for this session: the mode the next
   one uses, whether one is running, how the last one went. */
const OWNED_MODE_LABEL = { default: "Manual", auto: "Auto", plan: "Plan", acceptEdits: "Accept edits" };
export const ownedFor = (session) => (app.feed.owned || {})[session.sessionId] || {};
/* Whether the panel runs this session's next turn — which is every session but
   one kind. A terminal holds the transcript of the session running in it, and
   nothing can take a turn on a transcript something else holds. That is the only
   case the panel cannot run, and so the only case offered *Make interactive*;
   everything else is ours, whether adopted, held open, kept, closed, or simply
   not known to be alive.

   Asking `here || status === "stopped"` left two holes, and both read as the
   panel losing track of a session across a refresh:

   - a turn run from a row that had never been adopted flipped its status from
     `stopped` to `idle`, so mid-turn the row stopped counting as ours: the mode
     chips went away and *Make interactive* came back for a session the panel was
     at that moment running
   - an `offline` session belonged to nobody. It was past `stopped`, so it got no
     mode and no way in — and if it was blocked when its terminal went, no
     composer either, which is a row with nothing on it at all

   Liveness is what both were really asking about, so it is what is asked here.
   Unknown counts as not alive: a row the panel cannot vouch for is better
   offered a turn it can run than left with nothing. */
export const runsHere = (session) =>
  ownedFor(session).here === true || ownedFor(session).running === true ||
  !session.alive || session.status === "stopped";

/* The prompt a panel-run turn is standing on, drawn where the composer would be.

   This one *is* a form, and the difference from the read-only card above is the
   channel rather than the styling: a turn the panel launched was launched with
   `--permission-prompt-tool stdio`, so Claude Code asks over the pipe the panel
   is holding and the tool does not run until an answer goes back down it. There
   is a real answer to give here, so there are real buttons.

   A permission gate and a multiple-choice question arrive the same way and are
   told apart by `asks`: a gate gets allow-and-refuse, a question gets its
   options, and answering a question is what allowing it means. */
let askPicks = { requestId: null, picks: {} };

export function askPicksFor(ask) {
  if (askPicks.requestId !== ask.requestId) askPicks = { requestId: ask.requestId, picks: {} };
  return askPicks.picks;
}

export function ownedAskCard(session, ask) {
  const picks = askPicksFor(ask);
  const questions = ask.asks && Array.isArray(ask.input?.questions) ? ask.input.questions : [];
  const rows = questions.map((q, qi) => {
    const chosen = picks[q.question] || [];
    const many = !!q.multiSelect;
    const options = (q.options || []).map((option) => {
      const on = chosen.includes(option.label);
      return `<li>
        <button class="ask-opt md-state" type="button" data-answer="${qi}"
          data-label="${escapeHtml(option.label)}" aria-pressed="${String(on)}"
          role="${many ? "checkbox" : "radio"}" aria-checked="${String(on)}">
          <span class="ask-opt__mark ask-opt__mark--${many ? "many" : "one"}" aria-hidden="true"></span>
          <span class="ask-opt__body">
            <span class="ask-opt__label md-body-medium">${escapeHtml(option.label)}</span>
            ${option.description
              ? `<span class="ask-opt__why md-body-small">${escapeHtml(option.description)}</span>` : ""}
          </span>
        </button>
      </li>`;
    }).join("");
    return `<section class="ask-q" data-q="${qi}" data-question="${escapeHtml(q.question)}">
        ${q.question ? `<h4 class="ask-q__text md-title-small">${escapeHtml(q.question)}</h4>` : ""}
        ${options ? `<p class="ask-q__how md-label-small">${many
              ? "pick as many as apply" : "pick one"}</p>
          <ul class="ask-q__options" role="${many ? "group" : "radiogroup"}">${options}</ul>` : ""}
      </section>`;
  }).join("");
  const ready = questions.length ? questions.every((q) => (picks[q.question] || []).length) : true;
  // A question names itself; a permission gate is named for the tool it wants.
  const head = ask.asks
    ? (questions[0]?.header || "It is asking you something")
    : ask.name;
  return `<div class="ask-sheet ask--live" role="group" aria-label="${escapeHtml(head)}">
      <div class="ask-sheet__bar">
        <span class="ask-sheet__icon" aria-hidden="true">${ask.asks ? ICON.ask : ICON.power}</span>
        <span class="ask-sheet__head md-title-medium">${escapeHtml(head)}</span>
        <span class="ask-sheet__badge md-label-small">${ask.asks ? "your answer" : "permission"}</span>
      </div>
      <div class="ask-sheet__body">
        ${rows}
        ${!ask.asks
          ? `<p class="ask-q__text md-title-small">Let it use ${escapeHtml(ask.name)}?</p>
             ${ask.what ? `<p class="ask-sheet__what md-body-small md-mono">${escapeHtml(ask.what)}</p>` : ""}`
          : ""}
      </div>
      <div class="ask-sheet__foot">
        <span class="ask-sheet__note md-label-small">Nothing runs until you answer.</span>
        <button class="button button--text md-state" data-act="ask-deny">${ask.asks ? "Skip" : "Refuse"}</button>
        <button class="button button--filled md-state" data-act="ask-allow"${ready ? "" : " disabled"}>
          ${ask.asks ? "Answer" : "Allow"}</button>
      </div>
    </div>`;
}

/* Picking a mode moves the chip now, not when the server gets back to us.

   It can afford to: the endpoint answers in single-digit milliseconds while the
   state poll that would confirm it takes half a second or more, so waiting for
   confirmation made a setting applied in one frame feel like it had not
   registered. A refusal puts the chip back and says why; a stale refusal for a
   mode since clicked away from is dropped, the newer choice being the true one. */
let modePick = 0;

export async function pickMode(session, chip) {
  const mode = chip.dataset.mode;
  const before = ownedFor(session).mode;
  if (before === mode) return;
  const mine = ++modePick;
  const bar = chip.closest(".mode-bar");
  const paint = (which) => {
    for (const other of bar.querySelectorAll("[data-mode]")) {
      other.setAttribute("aria-pressed", String(other.dataset.mode === which));
    }
  };
  paint(mode);
  // Written even where there was no entry — a kept session that has never been
  // adopted has none — or the next poll, a second later, repaints the chip back
  // to the default and swallows the click.
  app.feed.owned = app.feed.owned || {};
  app.feed.owned[session.sessionId] = { ...(app.feed.owned[session.sessionId] || {}), mode };
  try {
    const response = await fetch("/api/owned/mode", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, mode }),
    });
    const data = await response.json().catch(() => ({}));
    if (!data.ok && mine === modePick) {
      app.feed.owned[session.sessionId] = { ...(app.feed.owned[session.sessionId] || {}), mode: before };
      paint(before);
      showSnackbar(data.message || "That mode did not stick");
    }
  } catch (error) {
    if (mine === modePick) {
      app.feed.owned[session.sessionId] = { ...(app.feed.owned[session.sessionId] || {}), mode: before };
      paint(before);
      showSnackbar("Could not reach the server");
    }
  }
}

export async function sendAskAnswer(session, ask, behavior, button) {
  button.disabled = true;
  const picks = askPicksFor(ask);
  try {
    const response = await fetch("/api/owned/answer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sessionId: session.sessionId, requestId: ask.requestId, behavior,
        ...(behavior === "allow" && ask.asks ? { answers: picks } : {}),
      }),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Sent" : "That did not go through"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    button.disabled = false;
    reloadState();
  }
}

/* The one way from a terminal session to an interactive one, drawn wherever a
   session that is not ours is given a composer. Its own function because it now
   has two callers: the box below, and the *blocked* box above it — which is the
   caller that matters most. A session standing at a prompt cannot be typed at
   from here, and the reason used to be all that was drawn, so the row you were
   most stuck on was the one row with no way out of the terminal. */
const wayIn = (session) => !runsHere(session) && app.feed.canSend
  ? `<div class="owned-bar">
      <button class="button button--tonal md-state owned-bar__adopt" type="button" data-act="adopt"
        title="Ends the session in the terminal and takes its next turn here, on the same conversation">
        ${ICON.play} Make interactive
      </button>
      <span class="owned-bar__why md-label-small">answer its prompts and pick its mode here</span>
    </div>`
  : "";

/* What is standing behind the turn in flight, drawn above the box it was typed
   in.

   A message typed at a session that is mid-turn is held rather than refused —
   *It is still answering the last one* was the panel making its own timing the
   typist's problem — and once it is held, two things follow. It has to be visible,
   because a message that vanished into a promise is indistinguishable from one
   that was dropped. And it has to be droppable: the turn that was running when
   you typed it may well answer it, and then the thing you least want is for it
   to be asked again as though nothing had happened. */
function queuedStrip(session) {
  const owned = ownedFor(session);
  const waiting = owned.queued || [];
  if (!waiting.length) return "";
  // Stopping the turn in front of the queue holds the queue rather than
  // emptying it — see owned_interrupt. That is a different thing from waiting,
  // and it has to read as one: what is waiting goes on its own, and what is
  // held is waiting on you.
  const held = !!owned.queueHeld;
  const rows = waiting.map((text, i) => `<li class="queued__item">
      <span class="queued__place md-label-small md-mono">${i + 1}</span>
      <span class="queued__text md-body-small" title="${escapeHtml(text)}">${escapeHtml(text)}</span>
      <button class="button button--text md-state queued__drop" type="button"
        data-act="unqueue" data-index="${i}"
        title="Take this back before the session gets to it">Drop</button>
    </li>`).join("");
  return `<div class="queued" data-held="${held ? 1 : 0}">
      <p class="queued__head md-label-small">${held
        ? (waiting.length === 1
            ? "You stopped the turn this was waiting for. It is still here."
            : `You stopped the turn these ${waiting.length} were waiting for. They are still here.`)
        : waiting.length === 1
          ? "1 message waiting for this turn to end"
          : `${waiting.length} messages waiting, in the order you typed them`}</p>
      <ul class="queued__list">${rows}</ul>
      ${held ? `<div class="queued__ask">
        <button class="button button--filled md-state queued__go" type="button" data-act="resume">
          ${ICON.play}${waiting.length === 1 ? "Send it" : "Send them"}</button>
        <button class="button button--text md-state" type="button" data-act="unqueue">
          ${waiting.length === 1 ? "Drop it" : "Drop them"}</button>
        <span class="queued__note md-label-small">or type something — what you send goes in last</span>
      </div>` : ""}
    </div>`;
}

/* Stopping the train of thought a session is in the middle of.

   Only a session the panel holds can be stopped, and that is not a gap in the
   panel — it is what the two channels are. A held session is one
   `{"subtype":"interrupt"}` down a pipe the panel owns, which is what the
   terminal's own Ctrl+C amounts to, and it leaves the session up and ready for
   the next thing you say.

   A session running in a terminal has no such channel. Ctrl+C there is a
   *keystroke*, read by Claude Code in raw mode, and nothing outside the pty can
   deliver one. The nearest thing from out here is `SIGINT` to its pid, and that
   was measured rather than assumed: it does not stop the turn, it ends the
   session — working or idle, Claude Code printed its `claude --resume` farewell
   and exited. So the button is drawn for such a session and disabled, saying
   which Ctrl+C is the one that works, with **Make interactive** already on the
   bar above as the way to have the other one. */
function stopButton(session) {
  const owned = ownedFor(session);
  if (runsHere(session)) {
    if (!owned.running || !owned.busy) return "";
    const why = owned.stopping ? "Stopping it…" : "Stop what it is doing (Ctrl+C)";
    return `<button class="button button--tonal md-state composer__stop" type="button"
      data-act="stop"${owned.stopping ? " disabled" : ""}
      aria-label="${escapeHtml(why)}" title="${escapeHtml(why)}">${ICON.stop}</button>`;
  }
  if (stateKeyOf(session.status) !== "busy") return "";
  const why = "Only its own terminal can stop it — Ctrl+C there. A signal from here "
    + "would end the session rather than the turn.";
  return `<button class="button button--tonal md-state composer__stop" type="button"
    data-act="stop" disabled aria-label="${escapeHtml(why)}"
    title="${escapeHtml(why)}">${ICON.stop}</button>`;
}

/* What is going with the message by path, above the box it was put into.

   Two ways in and one strip: a picture pasted, which the panel had to save
   before it could be named, and a file dropped from somewhere with no path to
   give, which it saved for the same reason. A thumbnail for the picture, because
   a file name is not how anyone recognises a screenshot; a glyph for the file,
   which is known by its name and has no picture to show. The path is on both,
   because the path is what is actually being sent and there should be no doubt
   about that.

   A file dropped *with* a path is not here at all — it was typed into the box as
   a path and is part of the sentence. */
function attachedStrip(session) {
  const shots = imagesFor(session.sessionId);
  if (!shots.length) return "";
  const rows = shots.map((shot) => {
    // A dropped file knows its name before it is saved, so it is named while it
    // saves rather than being a nameless row that says "Saving…".
    const state = shot.failed ? shot.why
      : shot.path ? shot.name
      : shot.name ? `${shot.name} — saving…`
      : "Saving…";
    const thumb = shot.url
      ? `<img class="attached__thumb" src="${escapeHtml(shot.url)}" alt="">`
      : `<span class="attached__thumb attached__thumb--file">${ICON.file}</span>`;
    return `<li class="attached__item${shot.failed ? " attached__item--failed" : ""}">
        ${thumb}
        <span class="attached__name md-label-small md-mono"
          title="${escapeHtml(shot.path
            ? (shot.kind === "file"
               ? `${shot.path}\n\nA copy: the drag carried no path of its own.`
               : shot.path)
            : state)}">${escapeHtml(state)}</span>
        <button class="button button--text md-state attached__drop" type="button"
          data-act="unattach" data-id="${escapeHtml(shot.id)}"
          title="Leave this out of the message">Remove</button>
      </li>`;
  }).join("");
  // "picture" while they all are, which is the common case and the more precise
  // word; "file" as soon as one of them is not.
  const noun = shots.every((shot) => shot.kind !== "file") ? "picture" : "file";
  return `<div class="attached">
      <p class="attached__head md-label-small">${shots.length === 1
        ? `1 ${noun} goes with this message, by path`
        : `${shots.length} ${noun}s go with this message, by path`}</p>
      <ul class="attached__list">${rows}</ul>
    </div>`;
}

export function composer(session) {
  const why = sendBlockedReason(session);
  if (why) {
    return `<div class="composer">
        <p class="composer__why md-label-medium">${escapeHtml(why)}</p>
        ${wayIn(session)}
      </div>`;
  }
  const owned = ownedFor(session);
  const queued = stateKeyOf(session.status) === "busy";
  // "ours" rather than "stopped": a held session is running, and is still ours.
  const stopped = runsHere(session);
  // A turn the panel is running is the one case where the box would be a lie:
  // a second turn on the same transcript is refused, so it says so instead.
  if (stopped && owned.ask) {
    // The turn is not merely busy: it is stopped in front of something only you
    // can settle, so the box gives its room to the thing being asked.
    // The queue outlives the prompt standing in front of it, so it is still
    // shown here: what you typed ahead goes in after the turn this prompt is
    // holding up, and that is exactly when you might want it back.
    return `<div class="composer composer--asking">${queuedStrip(session)}${attachedStrip(session)}${ownedAskCard(session, owned.ask)}</div>`;
  }
  // Alive, but with nothing to send *to* this second: it is up without a
  // messaging socket, or its socket is still opening. The box stays either way —
  // the message is held and delivered when it can be — and only what it promises
  // changes, because "Send a message" would be a lie about the timing. A closed
  // session is no longer one of these: it is not alive, so it is ours, and it is
  // typed at to be run from here rather than to be woken in a terminal.
  const away = !stopped && !session.canSay;
  const placeholder = stopped
    ? (owned.running && owned.busy ? "Type ahead — this goes in when the turn ends…"
       : owned.running ? "Send it a message…"
       : "Type and it starts back up…")
    : away ? "Write here — it goes in as soon as it is listening…"
    : queued ? "Queue a message for when it finishes…" : "Send a message — drop a file to name it, paste a picture to send it…";
  // The mode lives in the header, beside the session's name, so what is left in
  // this bar is only what belongs to sending: the way in, for a session that is
  // not ours yet, and the failure of the last turn if there was one.
  //
  // `wayIn` draws nothing for a session that is already ours, which includes a
  // closed one — there is no terminal to end and nothing to take over from, and
  // typing is all it takes to bring it back on the same conversation.
  //
  // There is one Send, and it says Send. It used to be a pair — *Run it here*
  // beside *In a terminal* — which asked, of every message, a question about
  // process management that has nothing to do with the message: the answer was
  // always the panel, and the second button was a way to send your words
  // somewhere you were not looking. A stopped session is started as part of
  // sending, not instead of it.
  const modes = stopped
    ? (owned.last && !owned.last.ok
        ? `<div class="owned-bar">
             <span class="owned-bar__last md-label-small">last turn: ${escapeHtml(owned.last.message)}</span>
           </div>` : "")
    : wayIn(session);
  return `<div class="composer">
      <div class="cmdbar" id="cmdBar" hidden></div>
      ${queuedStrip(session)}
      ${attachedStrip(session)}
      <div class="composer-grip" id="composerGrip" role="separator" aria-orientation="horizontal"
        tabindex="0" aria-label="Resize the message box"
        title="Drag to resize · double-click to fit the text"></div>
      ${modes}
      <textarea class="composer__field md-body-large" id="sayField" rows="1"
        aria-label="Message this session"
        placeholder="${placeholder}"></textarea>
      ${stopped
        ? `<button class="button button--filled md-state composer__send" data-act="own">Send</button>`
        : `<button class="button button--filled md-state composer__send" data-act="say">Send</button>`}
      ${stopButton(session)}
    </div>`;
}

/* A stable handle on one turn, so a comment can say which message it was made
   against rather than which words. Two turns can carry the same sentence — and
   matching a comment back by its words alone attached it to whichever one came
   first in the transcript, which was usually not the one you were reading. */
export function messageKey(message) {
  const when = clockOf(message.at);
  const what = message.text
    ? message.text.slice(0, 48)
    : (message.tools || []).map((t) => t.name).join(",");
  return `${message.role || "tool"}|${when}|${what}`;
}

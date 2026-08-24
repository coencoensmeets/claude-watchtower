import { refresh, refreshDetail, reloadState } from "../refresh.js";
import { isAmbiguous, isRemembered, windowSays } from "../sessions/facts.js";
import { STATE, stateKeyOf } from "../sessions/state.js";
import { app, selected, sidebar } from "../state.js";
import { detailPane } from "../ui/dom.js";
import { duration, escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { conceal, reveal } from "../ui/overlay.js";
import { showSnackbar } from "../ui/snackbar.js";
import { runsHere } from "./owned.js";

/* ==========================================================================
   Commenting on what was said — select a passage in the transcript and a chip
   rises over it; taking it drops the passage into the composer as a quote with
   your cursor under it.

   It goes through the composer rather than sending on its own, which buys the
   whole feature for very little: the draft already survives the poll's
   re-render, a session that cannot be sent to has already said why in place of
   a composer, and several passages can be gathered into one message instead of
   arriving as a burst the session queues separately.
   ========================================================================== */
const quoteChip = document.getElementById("quoteChip");
quoteChip.innerHTML =
  `<button class="quote-chip__act" type="button" data-sel="copy">${ICON.copy}<span>Copy</span></button>`
  + `<button class="quote-chip__act" type="button" data-sel="comment">${ICON.chat}<span>Comment</span></button>`;
// Captured when the chip is shown, not read when it is clicked: clicking is a
// pointer event on another element, and by then the selection may be gone.
let pendingQuote = null;

function hideQuoteChip() {
  pendingQuote = null;
  conceal(quoteChip);
}

/* What can be commented on: a speech bubble's body, and a tool row — the only
   place a command the session ran is written down. */
const QUOTABLE = ".msg__text, .activity-row__tools";

/* The passages under the selection, whose they were, and where to put the chip —
   or null if this selection is not something to comment on.

   A selection running across several messages is split into one quote each
   rather than refused: the reason it used to be refused was that a single
   attribution cannot cover two speakers, and giving each its own answers that
   properly. */
function quotableSelection() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  if (!selection.toString().trim()) return null;
  const range = selection.getRangeAt(0);
  const scroller = document.getElementById("chatScroll");
  if (!scroller || !scroller.contains(range.commonAncestorContainer)) return null;

  const parts = [];
  for (const body of scroller.querySelectorAll(QUOTABLE)) {
    if (!range.intersectsNode(body)) continue;
    // The selection clipped to this one body, so each passage carries only what
    // was selected inside it.
    const clipped = range.cloneRange();
    const whole = document.createRange();
    whole.selectNodeContents(body);
    if (clipped.compareBoundaryPoints(Range.START_TO_START, whole) < 0) clipped.setStart(body, 0);
    if (clipped.compareBoundaryPoints(Range.END_TO_END, whole) > 0) clipped.setEnd(body, body.childNodes.length);
    const text = clipped.toString().replace(/\s+$/, "").replace(/^\n+/, "");
    if (!text.trim()) continue;
    const owner = body.closest(".msg, .activity-row");
    // Which turn this came from, and which occurrence within it — the same
    // sentence can appear in two turns, and twice in one.
    const first = text.split("\n").map((l) => l.trim()).find((l) => l.length >= 12) || text.trim();
    let nth = 0;
    if (owner && first) {
      const before = document.createRange();
      before.setStart(owner, 0);
      before.setEnd(clipped.startContainer, clipped.startOffset);
      nth = before.toString().split(first).length - 1;
    }
    parts.push({
      text,
      who: owner?.dataset.who || "",
      at: owner?.dataset.at || "",
      key: owner?.dataset.key || "",
      nth,
      // A passage taken wholly from inside a code block goes back as code, not
      // as prose with its indentation flattened into a blockquote.
      code: codeContext(clipped, body),
    });
  }
  if (!parts.length) return null;

  const rect = range.getBoundingClientRect();
  if (!rect.width && !rect.height) return null;
  // Scrolled out of the transcript's own box, the passage is no longer on
  // screen — a chip pointing at it would be floating over the header.
  const box = scroller.getBoundingClientRect();
  if (rect.bottom < box.top || rect.top > box.bottom) return null;
  return { parts, rect, box };
}

/* The language to fence a passage with, or null when it is not code. Both ends
   have to be inside the same code element — half a sentence and half a function
   is prose as far as the quote is concerned. */
function codeContext(range, body) {
  const codeOf = (node) => (node?.nodeType === 1 ? node : node?.parentElement)?.closest("pre.md-code, code");
  const start = codeOf(range.startContainer);
  if (!start || !body.contains(start)) return null;
  if (start !== codeOf(range.endContainer)) return null;
  // Inline code is a few words mid-sentence; fencing it would be heavier than
  // the thing it quotes.
  const pre = start.closest("pre.md-code");
  if (!pre) return null;
  return pre.dataset.lang || "";
}

function showQuoteChip(quote) {
  pendingQuote = quote;
  quoteChip.hidden = false;
  // Above the selection by preference, below it when there is no room — and the
  // room is the transcript's own box, not the window, so a passage half off the
  // top of the scroller does not put the chip over the header above it.
  const { rect, box } = quote;
  const width = quoteChip.offsetWidth;
  const height = quoteChip.offsetHeight;
  const gap = 8;
  const left = Math.min(Math.max(gap, rect.left + rect.width / 2 - width / 2), window.innerWidth - width - gap);
  const above = rect.top - height - gap;
  const top = above >= box.top
    ? above
    : Math.min(rect.bottom + gap, box.bottom - height - gap, window.innerHeight - height - gap);
  quoteChip.style.left = `${Math.round(left)}px`;
  quoteChip.style.top = `${Math.round(Math.max(gap, top))}px`;
  reveal(quoteChip);
}

/* The chip is placed against a viewport rectangle, so anything that moves the
   passage has to move the chip with it — and the transcript moves on its own,
   staying pinned to the newest message while a session works. Following the
   selection is the only version of this that survives that; dismissing on
   scroll loses the chip to a poll landing mid-gesture. */
let quoteFrame = 0;
function syncQuoteChip() {
  if (quoteFrame) return;
  quoteFrame = requestAnimationFrame(() => {
    quoteFrame = 0;
    const quote = quotableSelection();
    if (quote) showQuoteChip(quote); else hideQuoteChip();
  });
}

/* Whose words these were, written from the point of view of the session that is
   about to read it — the panel says "claude" and "you" meaning the assistant and
   the person watching, and both of those invert on the way over. */
function speakerOf(who) {
  if (who === "claude") return "you";
  if (who === "you") return "me";
  return who; // another session, which arrives under its own name at both ends
}

/* A whole-answer selection would put hundreds of lines in the composer and bury
   the remark under them. Long passages keep their head and tail with the gap
   counted out loud — a silent trim would misrepresent what was selected, and the
   marker is in the composer where it can be edited or the passage re-taken
   smaller. */
const QUOTE_MAX_LINES = 40;
function trimLines(lines) {
  if (lines.length <= QUOTE_MAX_LINES) return lines;
  const head = lines.slice(0, QUOTE_MAX_LINES - 12);
  const tail = lines.slice(-10);
  const gone = lines.length - head.length - tail.length;
  return [...head, `… ${gone} lines not quoted …`, ...tail];
}

/* `> [you, 14:32]` over the passage, then a blank line for the remark. The
   attribution is what lets the session find the passage in its own transcript,
   and what keeps two quotes in one message apart. */
function quoteBlock({ text, who, at, code }) {
  const speaker = speakerOf(who);
  const head = speaker && at ? `[${speaker}, ${at}]` : speaker ? `[${speaker}]` : "";
  let lines = trimLines(text.replace(/\r/g, "").split("\n"));
  // Code goes back inside a fence, so it reaches the session as code with its
  // indentation intact rather than as prose flattened into a blockquote.
  if (code !== null && code !== undefined) lines = ["```" + code, ...lines, "```"];
  return [...(head ? [head] : []), ...lines].map((line) => `> ${line}`.trimEnd()).join("\n");
}

/* Commenting opens a card in the margin against the passage, the way a document
   does it, rather than dropping the quote into the composer. The remark stays
   attached to what it is about while you write it, and several can be open at
   once — which is the thing the composer could not do: there, a second quote
   pushed the first out of sight above what you were typing.

   What is sent is unchanged. The cards are gathered into the same attributed
   quote-and-remark message, so the session reads exactly what it read before. */
let commentSeq = 0;
function commentOnSelection() {
  const quote = pendingQuote;
  hideQuoteChip();
  if (!quote) return;
  const why = sendBlockedReason(selected());
  if (why) {
    // A comment that cannot be sent is a note to nobody. Say why now rather than
    // after it has been written.
    showSnackbar(why);
    return;
  }
  const id = app.selectedId;
  if (!id) return;
  if (!comments.has(id)) comments.set(id, []);
  const list = comments.get(id);
  const made = quote.parts.map((part) => {
    const entry = { id: `c${++commentSeq}`, ...part, remark: "", editing: true };
    list.push(entry);
    return entry;
  });
  rememberCommented(made);
  window.getSelection()?.removeAllRanges();
  markCommented();
  activeComment = made[made.length - 1].id;
  renderRail();
  focusComment(activeComment);
}

/* One card gets the caret when it opens; the rest are there to be filled in
   after. */
function focusComment(id) {
  const field = detailPane.querySelector(`.ccard[data-id="${CSS.escape(id)}"] .ccard__field`);
  if (field) { field.focus(); field.selectionStart = field.selectionEnd = field.value.length; }
}

/* ------------------------------------------------- what you already said on */
/* On a long answer you lose your place: nothing about a passage you have
   commented on looks any different from one you have not. These keep a note of
   what was quoted and put a mark back over it after every rebuild.

   Kept in memory rather than on the server or in localStorage, because it is a
   note about this sitting rather than a property of the session — and a mark
   that outlived the conversation it referred to would be worse than none. */
const commented = new Map(); // sessionId -> Set of exact passages
/* Which passages each comment put there, so deleting one takes its underline
   with it. A flat set could not: it had no way of knowing whether a passage was
   still spoken for by another comment. Entries outlive the comment when it is
   sent — a sent comment keeps its mark on purpose — and only a delete removes
   one. */
const commentSnippets = new Map(); // commentId -> [passages]
const COMMENT_MARK_MAX = 200;
/* The comments waiting to be sent, per session. Held in memory for the same
   reason the marks are: they are about this sitting, and one outliving the
   conversation it referred to would be worse than none. */
const comments = new Map(); // sessionId -> [{ id, text, who, at, code, remark, editing }]
let activeComment = null;
/* Measured on the detail pane, not the window — the index takes 340-380px of the
   window before the pane sees any of it, so a threshold picked as if it were a
   window width puts every ordinary laptop into the popover fallback and the
   margin nobody ever sees. A 1280px window leaves the pane about 940px, which
   still has room for the rail and a readable transcript beside it. */
const RAIL_MIN_WIDTH = 860;

const commentsFor = (id) => comments.get(id) || [];
/* A card being typed in must not be rebuilt underneath, the same way a
   half-typed name or a drag on the composer grip holds off a repaint. */
export const commentIsOpen = () => commentsFor(app.selectedId).some((c) => c.editing);

function rememberCommented(entries) {
  const id = app.selectedId;
  if (!id) return;
  if (!commented.has(id)) commented.set(id, new Set());
  const set = commented.get(id);
  for (const entry of entries) {
    const mine = [];
    for (const line of entry.text.split("\n")) {
      const snippet = line.trim();
      if (snippet.length >= 12 && set.size < COMMENT_MARK_MAX) { set.add(snippet); mine.push(snippet); }
    }
    commentSnippets.set(entry.id, mine);
  }
}

/* Take one comment's underlines back, keeping any passage another comment — sent
   or still open — also laid claim to. */
function forgetCommented(commentId) {
  const mine = commentSnippets.get(commentId);
  commentSnippets.delete(commentId);
  if (!mine || !mine.length) return;
  const set = commented.get(app.selectedId);
  if (!set) return;
  const stillClaimed = new Set();
  for (const list of commentSnippets.values()) for (const snippet of list) stillClaimed.add(snippet);
  for (const snippet of mine) if (!stillClaimed.has(snippet)) set.delete(snippet);
  // The marks are already in the page, so they have to come out by hand before
  // the remaining ones are drawn again.
  unmarkCommented();
  markCommented();
}

/* Unwrap every mark and put the text back as it was. normalize() re-merges the
   text nodes marking split, so the next pass sees whole runs again rather than
   the fragments left behind by the last one. */
function unmarkCommented() {
  const scroller = detailPane.querySelector("#chatScroll");
  if (!scroller) return;
  for (const mark of [...scroller.querySelectorAll("mark.commented")]) {
    const parent = mark.parentNode;
    if (!parent) continue;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  }
}

/* Put the marks back. This works on the DOM rather than on the HTML being
   built, so nothing from a message can reach the page as markup — the mark is a
   real element wrapped around a real text node, never a string spliced into
   innerHTML.

   It only matches a passage lying wholly inside one text node. A selection
   crossing a bold run or a link is quoted correctly but goes unmarked, which is
   the honest trade for never rewriting a bubble's structure underneath itself. */
export function markCommented() {
  const set = commented.get(app.selectedId);
  if (!set || !set.size) return;
  const scroller = detailPane.querySelector("#chatScroll");
  if (!scroller) return;
  const snippets = [...set].sort((a, b) => b.length - a.length);
  for (const body of scroller.querySelectorAll(QUOTABLE)) {
    // The body's text as one string, with the map back to the nodes it came
    // from. A passage crossing a bold run, a link or a code span occupies
    // several text nodes, and this is what lets it be found across them.
    const nodes = [];
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    let node, flat = "";
    while ((node = walker.nextNode())) {
      if (node.parentElement?.closest("mark.commented")) continue;
      nodes.push({ node: node, from: flat.length });
      flat += node.data;
    }
    for (const snippet of snippets) {
      let at = flat.indexOf(snippet);
      while (at >= 0) {
        // One mark per text node the passage passes through. Wrapping the whole
        // range in one go fails the moment it straddles an element boundary,
        // which is exactly the case that used to go unmarked.
        const pieces = [];
        for (const entry of nodes) {
          const s0 = Math.max(at, entry.from);
          const e0 = Math.min(at + snippet.length, entry.from + entry.node.data.length);
          if (s0 < e0) pieces.push({ n: entry.node, start: s0 - entry.from, end: e0 - entry.from });
        }
        // Back to front, so wrapping one piece cannot shift the offsets of the
        // pieces still to be wrapped.
        for (const piece of pieces.reverse()) {
          const range = document.createRange();
          range.setStart(piece.n, piece.start);
          range.setEnd(piece.n, piece.end);
          const mark = document.createElement("mark");
          mark.className = "commented";
          mark.title = "you commented on this";
          try { range.surroundContents(mark); } catch { /* leave this piece bare */ }
        }
        if (pieces.length) break;   // one occurrence marked is enough
        at = flat.indexOf(snippet, at + 1);
      }
    }
  }
}

/* -------------------------------------------------------------- the margin */
/* Cards are drawn once and then only moved: their tops are recomputed against
   the passages they belong to, and the whole rail is shifted by the scroller's
   offset, so scrolling costs a transform rather than a relayout. */
export function renderRail() {
  const rail = detailPane.querySelector("#commentRail");
  const inner = detailPane.querySelector("#commentRailInner");
  const wrap = detailPane.querySelector(".panel-wrap");
  if (!rail || !inner || !wrap) return;
  const list = commentsFor(app.selectedId);
  const scroller = detailPane.querySelector("#chatScroll");
  rail.hidden = !list.length;
  // Cards live in the transcript now, so anything left over from a previous
  // render has to come out before this one goes in.
  for (const stale of detailPane.querySelectorAll("#chatScroll .ccard")) stale.remove();
  if (!list.length) { inner.innerHTML = ""; return; }

  const sendable = list.filter((c) => c.remark.trim()).length;
  inner.innerHTML = list.map((c) => `
    <div class="ccard" data-id="${escapeHtml(c.id)}" data-active="${c.id === activeComment}">
      <p class="ccard__label md-label-medium">${ICON.chat}<span>your comment</span></p>
      <p class="ccard__quote md-body-small">${escapeHtml(c.text.split("\n")[0].slice(0, 160))}</p>
      ${c.editing
        ? `<textarea class="ccard__field md-body-medium" aria-label="Your comment on this passage"
             placeholder="What about it?">${escapeHtml(c.remark)}</textarea>
           <div class="ccard__actions">
             <button class="button button--text md-state md-label-large" data-cc="drop" data-id="${escapeHtml(c.id)}">Delete</button>
             <button class="button button--text md-state md-label-large" data-cc="keep" data-id="${escapeHtml(c.id)}">Done</button>
           </div>`
        : `<p class="ccard__remark md-body-medium">${escapeHtml(c.remark) || "<em>no comment yet</em>"}</p>
           <div class="ccard__actions">
             <button class="button button--text md-state md-label-large" data-cc="drop" data-id="${escapeHtml(c.id)}">Delete</button>
             <button class="button button--text md-state md-label-large" data-cc="edit" data-id="${escapeHtml(c.id)}">Edit</button>
           </div>`}
    </div>`).join("");

  /* Move each card into the conversation, directly after the message it is
     about. The transcript's own layout then places it — there is no column to
     anchor against and nothing to keep in sync while it scrolls. */
  if (scroller) {
    for (const card of [...inner.querySelectorAll(".ccard")]) {
      const entry = list.find((c) => c.id === card.dataset.id);
      const anchor = findAnchor(scroller, entry);
      const node = anchor && (anchor.nodeType === 1 ? anchor : anchor.commonAncestorContainer);
      const owner = node && (node.nodeType === 1 ? node : node.parentElement)?.closest(".msg, .activity-row");
      // No owner means the passage has scrolled out of the page of transcript
      // being shown; the card waits at the end rather than vanishing with it.
      card.classList.toggle("ccard--user", !!owner?.classList.contains("msg--user"));
      (owner || scroller.lastElementChild || scroller).after(card);
    }
  }

  // The send button lives outside the scrolled inner so it stays put.
  let send = rail.querySelector(".rail__send");
  if (!send) {
    send = document.createElement("button");
    send.className = "button button--filled md-state rail__send";
    rail.appendChild(send);
  }
  send.textContent = sendable
    ? `Send ${sendable} comment${sendable === 1 ? "" : "s"}`
    : "Write a comment to send";
  send.disabled = !sendable;

  // Wherever the cards ended up, not where they were built: they have already
  // been moved into the transcript by this point, so inner no longer holds them.
  for (const card of detailPane.querySelectorAll(".ccard")) {
    const id = card.dataset.id;
    const entry = list.find((c) => c.id === id);
    card.addEventListener("mousedown", () => { activeComment = id; });
    const field = card.querySelector(".ccard__field");
    if (field) {
      field.addEventListener("input", () => { entry.remark = field.value; refreshSendLabel(); });
      field.addEventListener("keydown", (event) => {
        // Enter finishes the card; a newline inside a remark needs the modifier,
        // which is the same bargain the composer makes.
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); closeComment(id, true); }
        else if (event.key === "Escape") { event.preventDefault(); closeComment(id, !!entry.remark.trim()); }
      });
    }
  }
  positionRail();
}

let railFrame = 0;
function scheduleRail() {
  if (railFrame) return;
  railFrame = requestAnimationFrame(() => { railFrame = 0; renderRail(); });
}

function refreshSendLabel() {
  const send = detailPane.querySelector(".rail__send");
  if (!send) return;
  const n = commentsFor(app.selectedId).filter((c) => c.remark.trim()).length;
  send.textContent = n ? `Send ${n} comment${n === 1 ? "" : "s"}` : "Write a comment to send";
  send.disabled = !n;
}

function closeComment(id, keep) {
  const list = commentsFor(app.selectedId);
  const entry = list.find((c) => c.id === id);
  if (!entry) return;
  if (!keep && !entry.remark.trim()) {
    comments.set(app.selectedId, list.filter((c) => c.id !== id));
    forgetCommented(id);
  } else {
    entry.editing = false;
  }
  if (activeComment === id) activeComment = null;
  renderRail();
}

/* The card is in the flow now, so nothing needs positioning. What is left is the
   pairing: the transcript says which message the open card belongs to, rather
   than leaving you to match them up by eye. */
function positionRail() {
  const scroller = detailPane.querySelector("#chatScroll");
  if (!scroller) return;
  for (const lit of scroller.querySelectorAll(".msg--linked, .activity-row--linked")) {
    lit.classList.remove("msg--linked", "activity-row--linked");
  }
  const card = scroller.querySelector('.ccard[data-active="true"]');
  if (!card) return;
  const entry = commentsFor(app.selectedId).find((c) => c.id === card.dataset.id);
  const anchor = findAnchor(scroller, entry);
  const node = anchor && (anchor.nodeType === 1 ? anchor : anchor.commonAncestorContainer);
  const owner = node && (node.nodeType === 1 ? node : node.parentElement)?.closest(".msg, .activity-row");
  if (owner) owner.classList.add(owner.classList.contains("msg") ? "msg--linked" : "activity-row--linked");
}

/* Where the passage this comment belongs to is now. The transcript is rebuilt
   from data, so the element it was selected in never survives — this finds it
   again, in three widening steps.

   It deliberately does not rely on the mark. A passage crossing a bold run or a
   link cannot be wrapped in one, and anchoring to marks alone put exactly those
   cards at the top of the rail rather than beside anything. */
function findAnchor(scroller, entry) {
  if (!entry) return null;
  const first = entry.text.split("\n").map((l) => l.trim()).find((l) => l.length >= 12)
    || entry.text.trim();

  /* The turn it was made against, first and by preference. Searching the whole
     transcript by words attached the comment to whichever turn happened to say
     the same thing first, which is usually not the one you were reading. */
  const owner = entry.key
    ? [...scroller.querySelectorAll(".msg, .activity-row")].find((m) => m.dataset.key === entry.key)
    : null;
  const hunt = (root, skip) => {
    if (!first || first.length < 8) return null;
    let seen = 0;
    for (const body of root.querySelectorAll(QUOTABLE)) {
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        let at = node.data.indexOf(first);
        while (at >= 0) {
          if (seen++ === skip) {
            const range = document.createRange();
            range.setStart(node, at);
            range.setEnd(node, at + first.length);
            return range;
          }
          at = node.data.indexOf(first, at + 1);
        }
      }
    }
    return null;
  };

  if (owner) {
    // The right occurrence inside the right turn; failing that, its first; and
    // failing that the turn itself, which is still the correct message.
    return hunt(owner, entry.nth || 0) || hunt(owner, 0) || owner;
  }
  // 1. The mark, when there is one: the tightest anchor available.
  if (first) {
    for (const mark of scroller.querySelectorAll("mark.commented")) {
      if (mark.textContent === first) return mark;
    }
  }
  // 2. The text itself, wherever it sits — the turn is no longer in the page of
  //    transcript being shown, so this is a best effort rather than the answer.
  const loose = hunt(scroller, 0);
  if (loose) return loose;
  // 3. The message it came from, by who and when. Coarse, but it still puts the
  //    card beside the right turn rather than at the top of the rail.
  if (entry.at) {
    for (const owner of scroller.querySelectorAll(".msg, .activity-row")) {
      if (owner.dataset.at === entry.at && owner.dataset.who === entry.who) return owner;
    }
  }
  return null;
}

/* Gathering the cards into one message, in the order they appear in the
   conversation rather than the order they were written. */
function commentsAsMessage(list) {
  return list.filter((c) => c.remark.trim())
    .map((c) => `${quoteBlock(c)}\n\n${c.remark.trim()}`)
    .join("\n\n");
}

async function sendComments(session, button) {
  const list = commentsFor(app.selectedId).filter((c) => c.remark.trim());
  if (!list.length || app.inFlight) return;
  const text = commentsAsMessage(commentsFor(app.selectedId));
  // The same two destinations as the composer's own Send, for the same reasons:
  // a session of ours takes the turn here whether or not it happens to be held
  // open at this moment, and a session in a terminal is sent to. `/api/start`
  // was the third, and it does not belong here — it opens a terminal, which
  // sends a page of comments somewhere you are not looking.
  const url = runsHere(session) ? "/api/owned/say" : "/api/say";
  app.inFlight = url;
  button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, text }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.ok) {
      // Sent comments leave the rail but keep their marks: the rail is what is
      // outstanding, the marks are where you have been.
      comments.set(app.selectedId, commentsFor(app.selectedId).filter((c) => !c.remark.trim()));
      activeComment = null;
      renderRail();
    }
    showSnackbar(data.message || (response.ok ? "Sent" : "That did not send"));
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    app.inFlight = null;
    button.disabled = false;
    reloadState();
  }
}

/* Attached once, to the pane, and never inside a render.

   This was bound inside renderDetail while the cards lived in a container that
   was rebuilt with it, so each listener died with the element it was on. On the
   pane, which survives every render, they accumulated instead — one per repaint,
   each holding the session that happened to be selected when it was attached.
   Sending then fired all of them, and comments landed on sessions that were not
   even on screen. The session is read at click time now, not captured. */
detailPane.addEventListener("click", (event) => {
  const act = event.target.closest("[data-cc]");
  if (act) {
    const id = act.dataset.id;
    if (act.dataset.cc === "drop") {
      comments.set(app.selectedId, commentsFor(app.selectedId).filter((c) => c.id !== id));
      if (activeComment === id) activeComment = null;
      forgetCommented(id);
      renderRail();
    } else if (act.dataset.cc === "edit") {
      const entry = commentsFor(app.selectedId).find((c) => c.id === id);
      if (entry) { entry.editing = true; activeComment = id; renderRail(); focusComment(id); }
    } else if (act.dataset.cc === "keep") {
      closeComment(id, true);
    }
    return;
  }
  const sendButton = event.target.closest(".rail__send");
  if (!sendButton) return;
  const live = selected();
  if (live) sendComments(live, sendButton);
});

// Fires for every selection change, including the ones that clear it, so this
// is both the show and the hide.
document.addEventListener("selectionchange", syncQuoteChip);
// Capture, because the transcript scrolls in its own box and scroll does not
// bubble — the same reason the jump-to-last pill listens this way.
window.addEventListener("scroll", syncQuoteChip, true);
window.addEventListener("resize", syncQuoteChip);
// The cards are placed against passages in the same scrolling box, so they move
// for the same reasons the chip does.
// Nothing to reposition on scroll or resize any more: the cards are in the
// transcript and move with it.
// Taking an action must not take the selection first — mousedown outside a
// selection collapses it, and the passage goes with it.
quoteChip.addEventListener("mousedown", (event) => event.preventDefault());
quoteChip.addEventListener("click", (event) => {
  const act = event.target.closest("[data-sel]")?.dataset.sel;
  if (act === "copy") copySelection();
  else if (act === "comment") commentOnSelection();
});

/* Copy is the other half of what you want from a selection, and the panel is the
   one place the transcript is readable without opening the terminal. */
async function copySelection() {
  const quote = pendingQuote;
  const text = quote ? quote.parts.map((p) => p.text).join("\n\n") : "";
  hideQuoteChip();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showSnackbar("Copied");
  } catch {
    showSnackbar("Could not reach the clipboard");
  }
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !quoteChip.hidden) { hideQuoteChip(); event.stopPropagation(); }
}, true);
/* A keyboard path to the same thing. The chip is a real button and Tab reaches
   it, but reaching for the mouse to take an offer the keyboard just raised is
   the sort of thing that stops people using it. Alt is used rather than Ctrl or
   a bare letter: a bare letter would fire while you were typing, and the common
   Ctrl combinations are already the browser's. */
document.addEventListener("keydown", (event) => {
  if (!event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key !== "c" && event.key !== "C") return;
  // Keyboard selection does not run through the pointer, so the chip may not be
  // up yet — read the selection directly rather than waiting for a frame.
  const quote = pendingQuote || quotableSelection();
  if (!quote) return;
  event.preventDefault();
  pendingQuote = quote;
  commentOnSelection();
});

/* Ctrl+C stops the turn, as it does at the prompt this panel is standing in for.

   With one deference: Ctrl+C is *copy* in a browser, and taking that away from a
   panel whose whole content is text you want to quote would be a bad trade. So
   anything selected wins — a selection in the page, or in the box you are typing
   in — and Ctrl+C only reaches the turn when there is nothing to copy, which is
   the same moment the terminal would have read it as an interrupt.

   It goes through the button rather than around it, so there is one rule about
   when a turn can be stopped and it is the one on screen. A session whose turn
   the panel cannot stop has that button disabled, and the key says what it says
   rather than doing nothing at all. */
document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
  if (event.key !== "c" && event.key !== "C") return;
  if (String(window.getSelection?.() ?? "")) return;
  const inField = event.target?.closest?.("textarea, input");
  if (inField && inField.selectionStart !== inField.selectionEnd) return;
  const stop = detailPane.querySelector("[data-act='stop']");
  if (!stop) return;
  event.preventDefault();
  if (stop.disabled) { showSnackbar(stop.getAttribute("title") || "It cannot be stopped from here"); return; }
  stop.click();
});

/* Renaming. The title in the header is a button; clicking it swaps in a field
   in the same spot. Enter or leaving the field keeps the name, Escape drops it,
   and an empty name puts the session's own name back. The name is kept by the
   server, so it outlives a reload and shows in the list too. */
export function startRename(session, button) {
  if (sidebar.renamingId) return;
  sidebar.renamingId = session.sessionId;
  const field = document.createElement("input");
  field.type = "text";
  field.className = "name-field md-headline-small";
  field.value = session.name;
  field.maxLength = 80;
  field.setAttribute("aria-label", "Session name");
  // Wide enough that a whole name is readable while you type it, and it grows
  // with what you type rather than scrolling inside a short box.
  const fit = () => { field.style.width = `${Math.min(72, Math.max(28, field.value.length + 2))}ch`; };
  fit();
  field.addEventListener("input", fit);
  button.replaceWith(field);
  field.focus();
  field.select();

  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    sidebar.renamingId = null;
    const name = field.value.trim();
    if (save && name !== session.name) commitRename(session, name);
    else refreshDetail(true);
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    else if (event.key === "Escape") { event.preventDefault(); finish(false); }
  });
  field.addEventListener("blur", () => finish(true));
}

async function commitRename(session, name) {
  // Show the new name at once; the next poll confirms it from the server.
  const local = app.feed.sessions.find((s) => s.sessionId === session.sessionId);
  if (local) local.name = name || local.defaultName || local.name;
  refresh();
  try {
    const response = await fetch("/api/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: session.sessionId, name }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) showSnackbar(data.message || "That rename did not stick");
  } catch (error) {
    showSnackbar("Could not reach the server");
  } finally {
    reloadState();
  }
}

/* Identifying asks the session's own terminal which window it is showing, by
   retitling it for a moment. The title comes straight back. */
export const IDENTIFY_NOTE = "Asking the terminal — its title will flicker";

export function headerActions(session) {
  // Nothing to raise and nothing to start: a session the panel runs is running,
  // and one that is not is started by typing at it.
  if (runsHere(session)) return "";
  if (!app.feed.canFocus) return `<p class="hint md-label-small">focusing needs xdotool</p>`;
  const win = session.window;
  if (!win) {
    return `<button class="button button--tonal md-state" data-act="pair">${ICON.pair} Pair window</button>
            <p class="hint md-label-small">then one click jumps here</p>`;
  }
  const paired = isRemembered(win);
  // An ambiguous match still gets a Focus button: pressing it identifies the
  // window first and then raises it, which is the one click you wanted anyway.
  const title = isAmbiguous(win) ? "" : win.title;
  return `<button class="button button--filled md-state" data-act="focus"${title ? ` title="${escapeHtml(title)}"` : ""}>${ICON.focus} Focus window</button>
          <p class="hint md-label-small">${windowSays(win)} <span class="meta-sep">·</span>
            <button class="link-button" data-act="${paired ? "unpair" : "pair"}">${paired ? "clear" : "pick another"}</button></p>`;
}

/* An empty shell. The bar is filled by paintTrace and then kept up to date in
   place: the trace changes every second, and rebuilding it under the pointer
   would blink the tooltip out on every poll. */
export function traceFor(session) {
  if (!(session.trace || []).length) return "";
  return `<div class="trace">
      <div class="trace__bar" role="img" aria-label="State over the last while"></div>
      <div class="trace__axis md-label-small"><span></span><span>now</span></div>
      <div class="trace__tip md-label-small" data-open="0"></div>
    </div>`;
}

/* The spans to draw: everything inside the window, with neighbours that show as
   the same state joined up — otherwise folding `shell` into Waiting would leave
   a seam every time a session ran a command. */
function traceSpans(session) {
  const spans = session.trace || [];
  if (!spans.length) return null;
  const end = app.feed.now;
  const observed = end - spans[0].from;
  const window = Math.min(app.feed.historySeconds, Math.max(observed, 15));
  const start = end - window;
  const merged = [];
  for (const span of spans) {
    if (span.to <= start || span.from >= end) continue;
    const key = stateKeyOf(span.status);
    const last = merged[merged.length - 1];
    if (last && last.key === key) last.to = span.to;
    else merged.push({ key, from: span.from, to: span.to });
  }
  const drawn = [];
  for (const span of merged) {
    const from = Math.max(span.from, start), to = Math.min(span.to, end);
    if (to <= from) continue;
    drawn.push({
      key: span.key, from: span.from, to: span.to,
      width: ((to - from) / window) * 100,
      live: span.to >= end,
      clipped: span.from < start,
    });
  }
  return { drawn, observed: Math.min(window, observed) };
}

export function paintTrace(root, session) {
  const bar = root.querySelector(".trace__bar");
  if (!bar) return;
  const model = traceSpans(session);
  if (!model) return;
  const { drawn, observed } = model;
  const span = duration(observed);
  bar.setAttribute("aria-label", `State over the last ${span}`);
  root.querySelector(".trace__axis span").textContent = `last ${span}`;

  // Same run of states as last time? Then only the numbers moved: widen the
  // slices where they are, and leave every node — and the hover on it — alone.
  const shape = drawn.map((s) => `${s.key}@${s.from.toFixed(3)}`).join("|");
  const same = bar.dataset.shape === shape && bar.children.length === drawn.length;
  if (!same) bar.innerHTML = drawn.map(() => `<span class="trace__span"></span>`).join("");
  bar.dataset.shape = shape;

  drawn.forEach((s, i) => {
    const node = bar.children[i];
    const state = STATE[s.key] || STATE.idle;
    node.style.width = `${s.width.toFixed(3)}%`;
    if (!same) {
      node.style.setProperty("--seg", state.colour);
      node.dataset.state = state.label;
      node.dataset.colour = state.colour;
      node.dataset.from = s.from;
      node.dataset.clipped = s.clipped ? "1" : "";
    }
    const to = s.live ? "" : String(s.to);
    if (node.dataset.to !== to) node.dataset.to = to;
  });

  // A tooltip standing open over the live slice is counting up; refresh its text
  // without disturbing the pointer.
  root._traceRefresh?.();
}

/* Wall-clock time of a server epoch, corrected for the client's clock offset. */
function clockAt(epoch) {
  return new Date((epoch - app.skew) * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

/* Hovering a slice of the trace names the state and says when it started and
   ended. A slice running up to now has no end yet, so it says "now" instead of
   inventing one; a slice older than the window says its true start with a "from
   before" marker rather than the clipped edge. */
let tracePointer = null; // last pointer position, so a repaint can pick the tip back up
export function wireTrace(root) {
  const bar = root.querySelector(".trace__bar");
  const tip = root.querySelector(".trace__tip");
  if (!bar || !tip) return;
  let hot = null;

  const hide = () => {
    tip.dataset.open = "0";
    hot?.classList.remove("trace__span--hot");
    hot = null;
  };

  const showAt = (clientX, clientY) => {
    const seg = document.elementFromPoint(clientX, clientY)?.closest(".trace__span");
    if (!seg || !bar.contains(seg)) return hide();
    if (seg !== hot) {
      hot?.classList.remove("trace__span--hot");
      seg.classList.add("trace__span--hot");
      hot = seg;
    }
    const from = Number(seg.dataset.from);
    const to = seg.dataset.to ? Number(seg.dataset.to) : null;
    const ended = to === null ? "now" : clockAt(to);
    const began = seg.dataset.clipped ? `before ${clockAt(from)}` : clockAt(from);
    const text = `<span class="trace__tip-state md-label-medium">
        <span class="trace__tip-dot" style="--seg:${seg.dataset.colour}"></span>
        ${escapeHtml(seg.dataset.state)} <span class="md-mono">${duration((to ?? app.feed.now) - from)}</span>
      </span>
      <span class="trace__tip-times md-mono">${escapeHtml(began)} → ${escapeHtml(ended)}</span>`;
    if (tip.innerHTML !== text) tip.innerHTML = text;
    // Follow the pointer along the bar, kept clear of both edges.
    const rect = bar.getBoundingClientRect();
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(clientX - rect.left, half), rect.width - half)}px`;
    tip.dataset.open = "1";
  };

  bar.addEventListener("pointermove", (event) => {
    tracePointer = { x: event.clientX, y: event.clientY };
    showAt(event.clientX, event.clientY);
  });
  // A bar torn out from under the pointer also fires leave. That is not the
  // reader moving away, so it must not forget where the pointer is — the fresh
  // bar is about to ask.
  bar.addEventListener("pointerleave", () => {
    if (!bar.isConnected) return;
    tracePointer = null;
    hide();
  });

  // Repaints and full re-renders both land here. If the pointer never left the
  // bar, put the tip straight back — a stationary reader should not have to
  // jiggle the mouse to get it again, and the live slice keeps counting up.
  root._traceRefresh = () => { if (tracePointer) showAt(tracePointer.x, tracePointer.y); };
  root._traceRefresh();
}

/* Why this session cannot be sent to, or null if it can. Every case says what is
   wrong rather than leaving a dead box on screen.

   Being unreachable is not one of those cases and no longer appears here. A
   session that has closed, or is up without a messaging socket, or is still
   opening one, takes the message anyway: the server holds it and starts the
   session back up if nothing is running it. "This session is not listening for
   messages" put the box away and left the reader to work out what to do about a
   state they could not see and did not cause — when the panel already knew what
   to do about it. */
export function sendBlockedReason(session) {
  if (!app.feed.canSend) return "sending is off — the panel is not on loopback";
  // A session the panel runs is never blocked: its turns go down a pipe the
  // panel holds, so whether it is listening on its messaging socket — which it
  // is not, having none — has nothing to do with it. This has to come before
  // every other test, because a held session's status is `idle` like any
  // running session's and its `canSay` is nothing at all.
  if (runsHere(session)) return null;
  // A blocked session cannot read a queued message: the prompt in front of it is
  // modal. Queuing one anyway would look like it was ignored. What it says next
  // depends on whether the prompt can be answered from here, because "answer it in
  // the terminal" is wrong advice for a question with buttons right above the
  // composer.
  if (session.status === "waiting") {
    // A question is on the card above, so point at it rather than at "the
    // terminal" in the abstract; a permission prompt has no card and no options.
    return session.question
      ? "answer the question above at this session's own prompt"
      : "answer the prompt in the terminal";
  }
  return null;
}

/* The multiple-choice question a session is standing at, drawn above the
   composer.

   It is a card and not a form, and that is not a shortcut. The only channel into a
   live session is its messaging socket, and the socket takes exactly one kind of
   message: a user turn, which lands in the prompt queue. The queue is behind the
   question — Claude Code is waiting on a keypress at its own prompt — so an answer
   sent from here would sit unread until somebody answered at the terminal anyway,
   and then arrive afterwards as a stray message. Rather than offer a button that
   quietly does that, the card shows what was asked, numbers the options the way the
   prompt does, and offers the window. Reading it is what saves the trip; the
   keypress still happens there. */
export function questionCard(session) {
  const asked = session.question;
  if (!asked || !asked.questions?.length) return "";
  const rows = asked.questions.map((q) => {
    const options = (q.options || []).map((option, i) => `<li class="ask__option">
        <span class="ask__index md-label-medium md-mono">${i + 1}</span>
        <span><span class="ask__label md-body-medium">${escapeHtml(option.label)}</span>
        ${option.description ? `<span class="ask__why md-body-small">${escapeHtml(option.description)}</span>` : ""}</span>
      </li>`).join("");
    return `<div class="ask__q">
        ${q.question ? `<p class="ask__text md-body-medium">${escapeHtml(q.question)}</p>` : ""}
        ${options ? `<p class="ask__how md-label-small">${q.multiSelect ? "pick any" : "pick one"}
          <span class="meta-sep">·</span> ${q.options.length} options</p>
          <ul class="ask__options">${options}</ul>` : ""}
      </div>`;
  }).join("");
  // Raising the window is the whole of what the panel can do here, so it is only
  // offered when there is a window to raise.
  const go = session.window && app.feed.canFocus
    ? `<button class="button button--text md-state ask__go" data-act="focus">${ICON.focus}Answer there</button>`
    : "";
  return `<div class="ask">
      <div class="ask__card">
        <p class="ask__head md-label-large">${ICON.ask}
          <span class="ask__head-label">${escapeHtml(asked.questions[0].header || "Claude asked you something")}</span>${go}</p>
        ${rows}
        <p class="ask__note md-label-small">Answer at this session's own prompt.</p>
      </div>
    </div>`;
}

import { snackbar } from "../ui/dom.js";
import { copyText } from "../ui/clipboard.js";
import { escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { openMenu, sessionMenu } from "../ui/menu.js";
import { conceal, reveal } from "../ui/overlay.js";
import { showSnackbar } from "../ui/snackbar.js";
import type { Preview, Session } from "../types.js";
import { addDraft, changed, draftsOf, dropDraft, sendBlockedReason, sendDrafts, sendDraftsNow, typed } from "./chat.js";
import type { PreviewSource } from "./chat.js";

/* ==========================================================================
   Commenting on a preview — the conversation's comments, on a page.

   Select a passage in the frame and the same Comment chip rises over it, or
   right-click it for Comment and Copy. Take it and a butter draft card opens
   beside the frame with the passage quoted.

   There is one set of drafts per session, kept in chat.ts: a comment written
   here is also a card at the end of the conversation, labelled with the page
   it is about, and a comment written on the conversation goes with these when
   you send from here. Send is the same everywhere — every draft the session
   has, in one message.

   The frame is sandboxed with an origin of its own, so the panel cannot read
   a selection in it. Pages the panel serves carry a small script for this
   (watchtower/preview_pin.js) that reports the selection by postMessage. A
   dev server is somebody else's page and has no such script, so there is
   nothing to comment on there.

   The frame survives the pane being rebuilt, and so does what is wired to it.
   ========================================================================== */

type Draft = { id: string; text: string; remark: string; editing: boolean; preview?: PreviewSource };
type Offer = { text: string; where: string; page: string };

let seq = 0;
/* The frame on screen. Only its window is listened to: a message from any
   other window, or from a popup the page opened, is ignored. */
let live: { root: HTMLElement; view: HTMLIFrameElement; session: Session; preview: Preview } | null = null;
let offer: Offer | null = null;
/* The passage just taken as a comment. Opening the notes column resizes the
   frame, and the page reports its selection again on a resize — sometimes
   before it has heard that the passage was taken. That report is the same
   passage, and raising the chip for it again was the chip that would not go. */
let taken = "";
const sameAs = (o: Offer) => `${o.page}|${o.where}|${o.text}`;

const mine = (session: Session, preview: Preview): Draft[] =>
  draftsOf(session.sessionId).filter((c) => c.preview?.id === preview.id);
const ready = (session: Session) => draftsOf(session.sessionId).filter((c) => c.remark.trim()).length;
const clip = (value: unknown, n: number) => String(value ?? "").slice(0, n);

/* The chip: the conversation's own pill, made once and kept on body for the
   reason that one is — the pane is rebuilt under it. */
const chip = document.createElement("div");
chip.className = "quote-chip md-label-large";
chip.setAttribute("role", "toolbar");
chip.setAttribute("aria-label", "What to do with the selected passage");
chip.dataset.open = "false";
chip.hidden = true;
chip.innerHTML = `<button class="quote-chip__act" type="button" data-pvsel="copy">${ICON.copy}<span>Copy</span></button>`
  + `<button class="quote-chip__act quote-chip__act--lit" type="button" data-pvsel="comment">${ICON.chat}<span>Comment</span></button>`;
document.body.appendChild(chip);

function hideChip() {
  offer = null;
  conceal(chip);
}

/* Over the passage by preference, under it when there is no room above. The
   passage's box is in the frame's own pixels, so the frame's place on screen
   is added on. */
function showChip(view: HTMLIFrameElement, rect) {
  const box = view.getBoundingClientRect();
  const left = box.left + rect.left;
  const top = box.top + rect.top;
  // A passage scrolled out of the frame has nothing to put the chip on.
  if (top + rect.height < box.top || top > box.bottom) { conceal(chip); return; }
  chip.hidden = false;
  const width = chip.offsetWidth, height = chip.offsetHeight, gap = 8;
  const x = Math.min(Math.max(gap, left + rect.width / 2 - width / 2), window.innerWidth - width - gap);
  const above = top - height - gap;
  const y = above >= box.top ? above : Math.min(top + rect.height + gap, window.innerHeight - height - gap);
  chip.style.left = `${Math.round(x)}px`;
  chip.style.top = `${Math.round(y)}px`;
  reveal(chip);
}

/* Taking the passage on offer as a comment: a draft card, open, with the caret
   in it. The page is told so it can keep the passage highlighted. */
function takeOffer() {
  if (!live || !offer) return;
  const { root, view, session, preview } = live;
  const why = sendBlockedReason(session);
  if (why) {
    // A comment that cannot be sent is a note to nobody: say so before it is
    // written, as the conversation does.
    showSnackbar(why);
    hideChip();
    return;
  }
  taken = sameAs(offer);
  const draft: Draft = {
    id: `pn${++seq}-${Date.now().toString(36)}`, text: offer.text, remark: "", editing: true,
    preview: { id: preview.id, title: preview.title, entry: preview.entry || "", where: offer.where, page: offer.page },
  };
  try { view.contentWindow?.postMessage({ wt: "sel-take", id: draft.id }, "*"); } catch { /* gone */ }
  // A card left open with nothing in it is replaced by the next passage taken,
  // rather than piling up behind it.
  for (const d of mine(session, preview)) if (d.editing && !d.remark.trim()) dropDraft(session.sessionId, d.id);
  hideChip();
  addDraft(session.sessionId, draft);
  focus(root, draft.id);
}

function copyOffer() {
  if (offer) copyText(offer.text, "Copied");
  hideChip();
}

chip.addEventListener("mousedown", (event) => event.preventDefault());
chip.addEventListener("click", (event) => {
  const act = (event.target as HTMLElement).closest<HTMLElement>("[data-pvsel]")?.dataset.pvsel;
  if (act === "comment") takeOffer();
  else if (act === "copy") copyOffer();
});

/* --------------------------------------------------------------- markup */

export function notesColumn(): string {
  return `<aside class="pv-notes" aria-label="Comments on this preview" hidden>
      <div class="pv-notes__list"></div>
      <p class="pv-notes__also md-label-medium" hidden></p>
      <button class="md-state md-label-large rail__send pv-notes__send" type="button"></button>
    </aside>`;
}

/* The full-screen button. Not the browser's own full screen of the frame: the
   cards and the Send pill have to come too, so it is the frame and its notes
   column together that fill the screen. */
export function fullButton(): string {
  return `<button class="icon-button pv-tool md-state" type="button" data-pv="full"
      title="Full screen — comments come too" aria-label="Full screen">${ICON.expand}</button>`;
}

/* -------------------------------------------------------- the Open tab
   A page opened with the Open button is a tab of its own, with no panel around
   it. The script in the page draws its own chip, editor and Send there, and
   talks to this window — the one that opened it. A comment written there is a
   draft here like any other, and its Send sends the session's drafts through
   the same door every other Send does.

   Only windows opened from here are listened to. A tab reached any other way
   has no opener, and its script does nothing. */
const tabs = new Map<Window, { session: Session; preview: Preview }>();

export function openInTab(session: Session, preview: Preview, href: string): boolean {
  // Not noopener: the opener is the channel back. The page still cannot reach
  // into this window — it is sandboxed with an origin of its own — it can only
  // post to it.
  const win = window.open(href, "_blank");
  if (!win) return false;
  tabs.set(win, { session, preview });
  return true;
}

function tell(sessionId: string, message: object) {
  for (const [win, tab] of tabs) {
    if (win.closed) { tabs.delete(win); continue; }
    if (tab.session.sessionId !== sessionId) continue;
    try { win.postMessage(message, "*"); } catch { /* gone */ }
  }
}

function tellCounts() {
  const seen = new Set<string>();
  for (const tab of tabs.values()) {
    if (seen.has(tab.session.sessionId)) continue;
    seen.add(tab.session.sessionId);
    tell(tab.session.sessionId, { wt: "note-count", n: ready(tab.session) });
  }
}

async function fromTab(win: Window, m) {
  const tab = tabs.get(win);
  if (!tab) return;
  const { session, preview } = tab;
  if (m.wt === "tab-hello") {
    tell(session.sessionId, { wt: "note-count", n: ready(session) });
  } else if (m.wt === "note-add") {
    const remark = clip(m.remark, 8000).trim();
    if (!remark) return;
    addDraft(session.sessionId, {
      id: clip(m.id, 60) || `pn${++seq}`, text: clip(m.text, 4000), remark, editing: false,
      preview: { id: preview.id, title: preview.title, entry: preview.entry || "",
                 where: clip(m.where, 200), page: clip(m.page, 200) },
    });
    try { win.postMessage({ wt: "note-ok", id: clip(m.id, 60) }, "*"); } catch { /* gone */ }
  } else if (m.wt === "notes-send") {
    const result = await sendDrafts(session, null, { now: !!m.now });
    try { win.postMessage({ wt: "sent", ok: result.ok, message: result.message }, "*"); } catch { /* gone */ }
    if (result.ok) showSnackbar(`Sent from the open tab of “${preview.title}”`);
  }
}

/* ---------------------------------------------------------------- wiring */

/* Called once per frame, from wirePreviews. Everything here lives on elements
   that are kept across rebuilds, so it is never wired twice. */
export function wireNotes(root: HTMLElement, session: Session, preview: Preview): void {
  const view = root.querySelector<HTMLIFrameElement>(".pv-view");
  if (!view) return;
  live = { root, view, session, preview };
  hideChip();

  // A reload is a new page, and the highlights went with the old one; the
  // drafts stay, because what they say is still true of the passage.
  view.addEventListener("load", () => hideChip());

  root.querySelector("[data-pv='full']")?.addEventListener("click", () => {
    const body = root.querySelector<HTMLElement>(".pv-body");
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else body?.requestFullscreen?.().catch(() => showSnackbar("This browser would not go full screen"));
  });
  root.querySelector("[data-pv='leave-full']")?.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  });

  root.querySelector(".pv-notes")?.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    const act = target.closest<HTMLElement>("[data-pn]");
    if (act) {
      const id = act.dataset.id;
      const draft = mine(session, preview).find((d) => d.id === id);
      if (act.dataset.pn === "drop") dropDraft(session.sessionId, id);
      else if (act.dataset.pn === "edit" && draft) { draft.editing = true; changed(); focus(root, id); }
      else if (act.dataset.pn === "keep" && draft) closeDraft(session, draft);
      return;
    }
    const send = target.closest<HTMLButtonElement>(".pv-notes__send");
    if (send) showSnackbar((await sendDrafts(session, send)).message);
  });

  paint();
}

/* Every view of the drafts repaints from the list when it changes (chat.ts). */
window.addEventListener("wt-comments", () => { paint(); tellCounts(); });
window.addEventListener("wt-comment-typed", () => { paintSend(); tellCounts(); });
window.addEventListener("wt-comment-dropped", (event) => {
  const { sessionId, id } = (event as CustomEvent).detail || {};
  if (live && live.session.sessionId === sessionId) {
    try { live.view.contentWindow?.postMessage({ wt: "pin-drop", id }, "*"); } catch { /* gone */ }
  }
  tell(sessionId, { wt: "pin-drop", id });
});

window.addEventListener("message", (event) => {
  const source = event.source as Window;
  if (source && tabs.has(source)) { fromTab(source, event.data || {}); return; }
  if (!live || source !== live.view.contentWindow) return;
  if (!live.root.isConnected) { live = null; hideChip(); return; }
  const m = event.data || {};
  if (m.wt === "sel" && m.rect) {
    const said = { text: clip(m.text, 4000), where: clip(m.where, 200), page: clip(m.page, 200) };
    if (sameAs(said) === taken) return;
    offer = said;
    showChip(live.view, m.rect);
  } else if (m.wt === "sel-clear") {
    taken = "";
    hideChip();
  } else if (m.wt === "menu") {
    // A right-click on a selection in the page: the chip's two actions, as a
    // menu at the pointer. The point is in the frame's pixels.
    offer = { text: clip(m.text, 4000), where: clip(m.where, 200), page: clip(m.page, 200) };
    conceal(chip);
    const box = live.view.getBoundingClientRect();
    openMenu({
      title: "Selected passage", label: "What to do with the selected passage",
      items: [
        { key: "comment", icon: ICON.chat, label: "Comment", run: () => takeOffer() },
        { key: "copy", icon: ICON.copy, label: "Copy", run: () => copyOffer() },
      ],
    }, box.left + (Number(m.x) || 0), box.top + (Number(m.y) || 0));
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && offer) hideChip();
}, true);

/* Any press in the panel that is not on the chip puts it away — switching to
   another tab most of all, which would otherwise leave it floating over the
   conversation. A press in the page itself never reaches this document, so
   selecting there is unaffected; the page says when its selection is gone. */
document.addEventListener("pointerdown", (event) => {
  if (!chip.hidden && !chip.contains(event.target as Node)) hideChip();
}, true);

/* Only what is inside the full-screen element is drawn while it is up, and the
   chip, the menu and the snackbar live on body — so they go in with the frame
   and come back out after. */
document.addEventListener("fullscreenchange", () => {
  hideChip();
  const full = document.fullscreenElement;
  const home = full && full.classList.contains("pv-body") ? full : document.body;
  home.appendChild(chip);
  home.appendChild(sessionMenu);
  home.appendChild(snackbar);
  for (const button of document.querySelectorAll<HTMLElement>("[data-pv='full']")) {
    const on = !!full;
    button.innerHTML = on ? ICON.shrink : ICON.expand;
    button.title = on ? "Leave full screen" : "Full screen — comments come too";
    button.setAttribute("aria-label", on ? "Leave full screen" : "Full screen");
  }
});

/* ----------------------------------------------------------------- cards */

function closeDraft(session: Session, draft: Draft) {
  // Closed with nothing written in it, a card has nothing to send.
  if (!draft.remark.trim()) { dropDraft(session.sessionId, draft.id); return; }
  draft.editing = false;
  changed();
}

function focus(root: HTMLElement, id: string) {
  const field = root.querySelector<HTMLTextAreaElement>(`.ccard[data-id="${CSS.escape(id)}"] .ccard__field`);
  if (field) { field.focus(); field.selectionStart = field.selectionEnd = field.value.length; }
}

function paint() {
  if (!live || !live.root.isConnected) return;
  const { root, session, preview } = live;
  const list = mine(session, preview);
  const column = root.querySelector<HTMLElement>(".pv-notes");
  const box = root.querySelector<HTMLElement>(".pv-notes__list");
  if (!column || !box) return;
  const others = ready(session) - list.filter((d) => d.remark.trim()).length;
  column.hidden = !list.length && !others;
  // A card being typed in keeps its caret through a repaint caused elsewhere —
  // a comment arriving from the open tab, say.
  const typing = (document.activeElement as HTMLElement)?.closest?.(".pv-card")?.getAttribute("data-id");
  const caret = typing ? (document.activeElement as HTMLTextAreaElement).selectionStart : 0;
  box.innerHTML = list.map((d) => `
    <div class="ccard pv-card" data-id="${escapeHtml(d.id)}" data-editing="${d.editing}">
      <div class="ccard__head">
        <span class="ccard__badge" aria-hidden="true">${ICON.chat}</span>
        <span class="ccard__label md-label-large">You</span>
        <span class="ccard__tag md-label-small">Draft</span>
        ${d.editing ? "" : `<span class="ccard__tools">
          <button class="ccard__icon md-state" type="button" data-pn="edit" data-id="${escapeHtml(d.id)}"
            title="Edit" aria-label="Edit this comment">${ICON.pencil}</button>
          <button class="ccard__icon md-state" type="button" data-pn="drop" data-id="${escapeHtml(d.id)}"
            title="Delete" aria-label="Delete this comment">${ICON.trash}</button>
        </span>`}
      </div>
      <p class="ccard__quote md-body-small">${escapeHtml(d.text.split("\n")[0].slice(0, 160))}</p>
      ${d.editing
        ? `<textarea class="ccard__field md-body-medium" aria-label="Your comment on this passage"
             placeholder="What about it?">${escapeHtml(d.remark)}</textarea>
           <div class="ccard__actions">
             <span class="ccard__hint md-label-small"><kbd>Enter</kbd> done · <kbd>Ctrl</kbd>+<kbd>Enter</kbd> send all</span>
             <button class="ccard__icon md-state" type="button" data-pn="drop" data-id="${escapeHtml(d.id)}"
               title="Delete" aria-label="Delete this comment">${ICON.trash}</button>
             <button class="ccard__done md-state md-label-large" type="button" data-pn="keep"
               data-id="${escapeHtml(d.id)}"><span class="ccard__ride" aria-hidden="true">${ICON.check}</span>Done</button>
           </div>`
        : `<p class="ccard__remark md-body-medium">${escapeHtml(d.remark)}</p>`}
    </div>`).join("");

  for (const card of box.querySelectorAll<HTMLElement>(".ccard")) {
    const draft = list.find((d) => d.id === card.dataset.id);
    const field = card.querySelector<HTMLTextAreaElement>(".ccard__field");
    if (!draft || !field) continue;
    field.addEventListener("input", () => { draft.remark = field.value; typed(); });
    field.addEventListener("keydown", (event) => {
      // Ctrl+Enter finishes the card and sends every draft the session has.
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        event.stopPropagation();
        sendDraftsNow(session);
        return;
      }
      // Enter finishes the card and Shift+Enter is a new line, as in the
      // conversation; Escape puts it down, and an empty one goes.
      if ((event.key === "Enter" && !event.shiftKey) || event.key === "Escape") {
        event.preventDefault();
        closeDraft(session, draft);
      }
    });
  }
  if (typing) {
    const field = box.querySelector<HTMLTextAreaElement>(`.ccard[data-id="${CSS.escape(typing)}"] .ccard__field`);
    if (field) { field.focus(); field.selectionStart = field.selectionEnd = caret; }
  }
  paintSend();
}

/* The conversation's Send pill: the count in a butter bubble and the arrow
   riding at the end. The count is every draft the session has, because that is
   what it sends; the line over it says how many of them are not on this page. */
function paintSend() {
  if (!live || !live.root.isConnected) return;
  const { root, session, preview } = live;
  const send = root.querySelector<HTMLButtonElement>(".pv-notes__send");
  const also = root.querySelector<HTMLElement>(".pv-notes__also");
  if (!send || !also) return;
  const n = ready(session);
  const here = mine(session, preview).filter((d) => d.remark.trim()).length;
  const elsewhere = n - here;
  also.hidden = !elsewhere;
  also.textContent = elsewhere === 1
    ? "1 comment from the conversation or another preview goes with these"
    : `${elsewhere} comments from the conversation or other previews go with these`;
  const words = n ? `Send ${n} comment${n === 1 ? "" : "s"} to the chat` : "Write a comment to send";
  send.innerHTML = n
    ? `<span class="rail__count" aria-hidden="true">${n}</span><span>Send to chat</span>`
      + `<span class="rail__ride" aria-hidden="true">${ICON.up}</span>`
    : `<span>${words}</span>`;
  send.setAttribute("aria-label", words);
  send.disabled = !n;
}

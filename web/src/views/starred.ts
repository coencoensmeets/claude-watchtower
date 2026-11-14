import { app } from "../state.js";
import { reloadState } from "../refresh.js";
import { copyText } from "../ui/clipboard.js";
import { escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import { openMenu, type MenuItem } from "../ui/menu.js";
import { onLongPress } from "../ui/press.js";
import { showSnackbar } from "../ui/snackbar.js";
import { previewHref } from "./previews.js";
import type { Preview, Session, Starred } from "../types.js";

/* ==========================================================================
   Starred sites — previews you starred, listed under the sessions.

   A preview belongs to the session that showed it, and goes when that session
   is forgotten. A star copies it out (server side, see watchtower/previews.py)
   so it stays openable with no session attached. A tap opens it in a tab of
   its own; a right-click or a long press has the rest, including starting a
   chat about it in the folder it came from.
   ========================================================================== */

const section = document.getElementById("starred");
const list = document.getElementById("starredList");

let startChat: (cwd: string, prefill: string, item: HTMLButtonElement) => void = () => {};
let showSession: (id: string) => void = () => {};

export function starredSites(): Starred[] {
  return app.feed.starred || [];
}

const keyOf = (p: Preview) => p.path || p.url || "";

/* The star a session's preview already has, if any — matched on what it shows,
   since the same page starred from another session is the same site. */
export function starFor(p: Preview): Starred | null {
  return starredSites().find((s) => keyOf(s) === keyOf(p)) || null;
}

async function post(url: string, body: Record<string, unknown>, button?: HTMLButtonElement | null) {
  if (button) button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    showSnackbar(data.message || (response.ok ? "Done" : "That did not work"));
    return data;
  } catch {
    showSnackbar("Could not reach the server");
    return {};
  } finally {
    if (button) button.disabled = false;
    await reloadState();
  }
}

export function toggleStar(session: Session, p: Preview, button?: HTMLButtonElement | null) {
  const had = starFor(p);
  return had
    ? post("/api/starred/remove", { id: had.id }, button)
    : post("/api/previews/star", { sessionId: session.sessionId, id: p.id }, button);
}

function where(s: Starred): string {
  if (s.kind === "url") {
    try { const u = new URL(previewHref(s)); return u.host + (u.pathname === "/" ? "" : u.pathname); }
    catch { return s.url || ""; }
  }
  return (s.path || "").split("/").filter(Boolean).slice(-2).join("/");
}

export function renderStarred(): void {
  const all = starredSites();
  section.hidden = !all.length;
  const signature = all.map((s) => `${s.id}:${s.title}:${previewHref(s)}`).join("|");
  if (list.dataset.signature === signature) return;
  list.dataset.signature = signature;
  list.innerHTML = all.map((s) => `
    <li data-star="${escapeHtml(s.id)}">
      <a class="starred-item md-state" href="${escapeHtml(previewHref(s))}" target="_blank" rel="noopener"
         draggable="false" title="${escapeHtml(where(s))}">
        <span class="starred-item__icon">${s.kind === "url" ? ICON.globe : ICON.preview}</span>
        <span class="starred-item__text">
          <span class="starred-item__title md-title-small">${escapeHtml(s.title)}</span>
          <span class="starred-item__meta md-body-small md-mono">${escapeHtml(where(s))}</span>
        </span>
      </a>
    </li>`).join("");
}

function openStarMenu(s: Starred, x: number, y: number) {
  const href = previewHref(s);
  const origin = s.from && app.feed.sessions.find((x) => x.sessionId === s.from);
  const items: MenuItem[] = [
    { key: "open", icon: ICON.focus, label: "Open", run: () => window.open(href, "_blank", "noopener") },
    { key: "copy", icon: ICON.link, label: "Copy link", run: () => copyText(href, "Link copied") },
    { divider: true },
    { key: "chat", icon: ICON.chat, label: "Open a chat with it",
      hint: !app.feed.canSend ? "needs loopback" : s.cwd || "",
      disabled: !app.feed.canSend || !s.cwd,
      run: (el) => startChat(s.cwd, `About ${s.title} (${s.path || s.url}): `, el) },
  ];
  if (origin) {
    items.push({ key: "session", icon: ICON.preview, label: "Show the session it came from",
      hint: origin.name, run: () => showSession(origin.sessionId) });
  }
  items.push({ divider: true });
  items.push({ key: "rename", icon: ICON.pencil, label: "Rename…", run: (el) => {
    const title = window.prompt("Name for this site", s.title);
    if (title && title.trim() && title.trim() !== s.title) post("/api/starred/rename", { id: s.id, title }, el);
  } });
  items.push({ key: "unstar", icon: ICON.star, label: "Unstar", danger: true,
    run: (el) => post("/api/starred/remove", { id: s.id }, el) });
  openMenu({ title: s.title, label: `Actions for ${s.title}`, items }, x, y);
}

function starAt(target: EventTarget | null): Starred | null {
  const row = (target as Element | null)?.closest?.("li[data-star]") as HTMLElement | null;
  return row ? starredSites().find((s) => s.id === row.dataset.star) || null : null;
}

export function wireStarred(hooks: {
  startChat: (cwd: string, prefill: string, item: HTMLButtonElement) => void;
  showSession: (id: string) => void;
}): void {
  startChat = hooks.startChat;
  showSession = hooks.showSession;
  list.addEventListener("contextmenu", (event) => {
    const s = starAt(event.target);
    if (!s) return;
    event.preventDefault();
    openStarMenu(s, event.clientX, event.clientY);
  });
  // A phone has no right-click; holding the row is the same menu. See ui/press.ts.
  onLongPress(list, ({ x, y, target }) => {
    const s = starAt(target);
    if (s) openStarMenu(s, x, y);
  });
}

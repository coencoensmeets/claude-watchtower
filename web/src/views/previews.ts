import { still } from "../ui/motion.js";
import { run } from "../net.js";
import { app } from "../state.js";
import { copyText } from "../ui/clipboard.js";
import { detailPane } from "../ui/dom.js";
import { ago, escapeHtml } from "../ui/format.js";
import { ICON } from "../ui/icons.js";
import type { Preview, Session } from "../types.js";
import { starFor, toggleStar } from "./starred.js";

/* ==========================================================================
   Preview — pages a session made for you to look at. A session hands them to
   the panel with the `show_preview` MCP tool; they arrive on the feed, and
   this tab shows them in a frame you can size to a desktop, a tablet or a
   phone, reload, or open on their own.

   The frame outlives the pane being rebuilt. A working session repaints the
   detail pane every few seconds, and an iframe rebuilt with it would reload
   the page under you each time — so main.ts keeps the old .panel-wrap in
   place whenever stageKey says it is still showing the same thing.
   ========================================================================== */

type Device = "desktop" | "tablet" | "phone";
const DEVICES: [Device, string][] = [["desktop", "Desktop"], ["tablet", "Tablet"], ["phone", "Phone"]];
const DEVICE_KEY = "wt-preview-device";
const SEEN_KEY = "wt-preview-seen";
// A preview this new wears a lamp: it is what the session just did.
const FRESH_SECONDS = 90;

// The preview you picked, per session, and when — a newer one shown after the
// pick takes over, because that is the one the session is telling you about.
const picks = new Map<string, { id: string; at: number }>();

// The session whose preview has the whole screen, on a phone. Kept here rather
// than on the element so a rebuilt pane comes back still full screen.
let immersive: string | null = null;
const CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function readSeen(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}") || {}; } catch { return {}; }
}

export function previewsOf(session: Session): Preview[] {
  return app.feed.previews?.[session.sessionId] || [];
}

function device(): Device {
  const d = localStorage.getItem(DEVICE_KEY);
  return d === "tablet" || d === "phone" ? d : "desktop";
}

export function chosenPreview(session: Session): Preview | null {
  const all = previewsOf(session);
  if (!all.length) return null;
  const pick = picks.get(session.sessionId);
  const picked = pick && all.find((p) => p.id === pick.id);
  if (picked && all[0].at <= pick.at) return picked;
  return all[0];
}

/* Whether the session has shown something since you last had this tab open. */
export function previewUnseen(session: Session): boolean {
  const newest = previewsOf(session)[0];
  return !!newest && newest.at > (readSeen()[session.sessionId] || 0);
}

export function markPreviewsSeen(session: Session): void {
  const newest = previewsOf(session)[0];
  if (!newest) return;
  const seen = readSeen();
  if ((seen[session.sessionId] || 0) >= newest.at) return;
  seen[session.sessionId] = newest.at;
  // Bounded the same way the server bounds the previews themselves.
  const ids = Object.keys(seen);
  if (ids.length > 150) for (const id of ids.slice(0, ids.length - 150)) delete seen[id];
  localStorage.setItem(SEEN_KEY, JSON.stringify(seen));
}

/* Everything the tab draws from, for the pane's signature. */
export function previewStamp(session: Session): string {
  const all = previewsOf(session);
  return `${all.map((p) => `${p.id}:${p.at}`).join(",")}|${chosenPreview(session)?.id ?? ""}|${previewUnseen(session)}`;
}

/* What the frame is showing. While this stays the same the frame is kept. */
export function stageKey(session: Session): string {
  const all = previewsOf(session);
  const shown = chosenPreview(session);
  return `${session.sessionId}|${shown?.id ?? ""}|${shown?.at ?? ""}|${all.map((p) => p.id).join(",")}`;
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

/* Where a preview opens from this browser. A dev server on `localhost` is this
   computer's localhost, so from a phone the panel's own host name stands in. */
export function previewHref(p: Preview): string {
  if (p.kind === "file") return `${location.origin}/preview/${p.token}/${encodeURI(p.entry || "")}`;
  try {
    const url = new URL(p.url || "");
    if (LOOPBACK.has(url.hostname) && !LOOPBACK.has(location.hostname)) url.hostname = location.hostname;
    return url.href;
  } catch {
    return p.url || "";
  }
}

function where(p: Preview): string {
  if (p.kind === "url") {
    try { const u = new URL(previewHref(p)); return `${u.host}${u.pathname === "/" ? "" : u.pathname}`; }
    catch { return p.url || ""; }
  }
  const path = p.path || p.entry || "";
  const parts = path.split("/").filter(Boolean);
  return parts.slice(-2).join("/");
}

function chip(p: Preview, shown: Preview, now: number): string {
  const fresh = now - p.at < FRESH_SECONDS;
  return `<button class="pv-chip md-state" type="button" data-preview="${escapeHtml(p.id)}"
      aria-pressed="${p.id === shown.id}" title="${escapeHtml(where(p))}">
      <span class="pv-chip__icon">${p.kind === "url" ? ICON.globe : ICON.preview}</span>
      <span class="pv-chip__text">
        <span class="pv-chip__title md-label-large">${escapeHtml(p.title)}</span>
        <span class="pv-chip__meta md-label-small">${ago(now - p.at)}</span>
      </span>
      ${fresh ? `<span class="pv-lamp" aria-label="New"></span>` : ""}
    </button>`;
}

/* The star: keeps the preview listed under the sessions, with no session attached. */
function starButton(p: Preview): string {
  const on = !!starFor(p);
  const label = on ? "Unstar — take it off the sidebar" : "Star — keep it under the sessions";
  return `<button class="icon-button pv-tool pv-star md-state" type="button" data-pv="star"
      aria-pressed="${on}" title="${label}" aria-label="${label}">${on ? ICON.starFilled : ICON.star}</button>`;
}

function paintStar(root: HTMLElement, p: Preview | null): void {
  const old = root.querySelector<HTMLElement>("[data-pv='star']");
  if (!old || !p || old.getAttribute("aria-pressed") === String(!!starFor(p))) return;
  const holder = document.createElement("div");
  holder.innerHTML = starButton(p);
  const fresh = holder.firstElementChild as HTMLElement;
  // Same element, new insides: the listener is on it already.
  old.setAttribute("aria-pressed", fresh.getAttribute("aria-pressed"));
  old.title = fresh.title;
  old.setAttribute("aria-label", fresh.getAttribute("aria-label"));
  old.innerHTML = fresh.innerHTML;
}

export function previewPanel(session: Session): string {
  const all = previewsOf(session);
  const shown = chosenPreview(session);
  if (!shown) {
    return `<div class="pv-empty">
      <div class="pv-empty__art">${ICON.preview}</div>
      <h3 class="md-title-medium">Nothing to preview yet</h3>
      <p class="md-body-medium">When this session builds a page, a styleguide or a site, it shows up here
        — ready to open on this computer or your phone. Ask it to <em>show it in Watchtower</em>.</p>
    </div>`;
  }
  const now = app.feed.now || Date.now() / 1000;
  const d = device();
  const href = previewHref(shown);
  // An artifact with no local copy the panel may serve is only its claude.ai
  // address, and claude.ai will not be framed by another site — so it says so
  // and points at the way through, rather than showing an empty frame.
  const remote = shown.kind === "url" && !!shown.artifactUrl;
  const frame = remote
    ? `<div class="pv-remote md-body-medium">${ICON.globe}<p>This artifact is published on claude.ai,
         and claude.ai pages do not open inside the panel. <a href="${escapeHtml(shown.artifactUrl)}"
         target="_blank" rel="noopener">Open it on claude.ai</a>.</p></div>`
    : shown.kind === "file"
    ? `<iframe class="pv-view" src="${escapeHtml(href)}" title="${escapeHtml(shown.title)}"
         sandbox="allow-scripts allow-forms allow-popups allow-modals allow-downloads allow-popups-to-escape-sandbox"></iframe>`
    : `<iframe class="pv-view" src="${escapeHtml(href)}" title="${escapeHtml(shown.title)}"></iframe>`;
  const mixed = shown.kind === "url" && location.protocol === "https:" && href.startsWith("http:");
  const full = immersive === session.sessionId;
  return `<div class="pv${full ? " pv--immersive" : ""}" data-stage-key="${escapeHtml(stageKey(session))}">
    ${all.length > 1 ? `<div class="pv-picker" role="toolbar" aria-label="Previews">
      ${all.map((p) => chip(p, shown, now)).join("")}</div>` : ""}
    <div class="pv-bar">
      <div class="pv-bar__text">
        <div class="pv-bar__title md-title-medium">${escapeHtml(shown.title)}${
          now - shown.at < FRESH_SECONDS ? `<span class="pv-badge md-label-small">New</span>` : ""}</div>
        <div class="pv-bar__meta md-label-medium"><span class="pv-bar__kind">${shown.artifactUrl ? "Artifact" : shown.kind === "url" ? "Server" : "Page"}</span>
          <span class="md-mono">${escapeHtml(where(shown))}</span> · <span data-pv-ago>${ago(now - shown.at)}</span></div>
      </div>
      <div class="pv-tools">
        <div class="pv-devices" role="group" aria-label="Frame size">
          ${DEVICES.map(([key, label]) => `<button class="pv-device md-state" type="button" data-device="${key}"
              aria-pressed="${d === key}" title="${label}" aria-label="${label}">${ICON[key]}</button>`).join("")}
        </div>
        ${starButton(shown)}
        <button class="icon-button pv-tool md-state" type="button" data-pv="reload" title="Reload" aria-label="Reload">${ICON.sync}</button>
        <button class="icon-button pv-tool md-state" type="button" data-pv="copy" title="Copy link" aria-label="Copy link">${ICON.link}</button>
        <button class="icon-button pv-tool md-state" type="button" data-pv="remove" title="Remove from the panel" aria-label="Remove from the panel">${ICON.trash}</button>
        ${shown.artifactUrl && !remote ? `<a class="button button--text pv-artifact md-state md-label-large"
            href="${escapeHtml(shown.artifactUrl)}" target="_blank" rel="noopener"
            title="The published page on claude.ai — what anyone you share it with sees">${ICON.globe}claude.ai</a>` : ""}
        <a class="pv-open md-state md-label-large" href="${escapeHtml(href)}" target="_blank" rel="noopener">${ICON.focus}Open</a>
      </div>
    </div>
    <div class="pv-stage" data-device="${d}">
      <div class="pv-frame">
        <div class="pv-chrome" aria-hidden="true">
          <span class="pv-chrome__dots"><i></i><i></i><i></i></span>
          <span class="pv-chrome__address md-label-medium md-mono">${shown.kind === "url" ? ICON.globe : ICON.gate}${escapeHtml(where(shown))}</span>
        </div>
        <div class="pv-screen">
          ${frame}
          <div class="pv-loading" aria-hidden="true"><span></span></div>
          <button class="pv-catch" type="button" aria-label="Show full screen">
            <span class="pv-catch__hint md-label-large">${ICON.up}Swipe up for full screen</span>
          </button>
        </div>
      </div>
      <button class="pv-exit md-state" type="button" title="Leave full screen" aria-label="Leave full screen">${CLOSE_ICON}</button>
      ${mixed ? `<p class="pv-hint md-body-small">This panel is on https and the server is plain http, so the
        browser may refuse to show it inside the panel. <strong>Open</strong> shows it in a tab of its own.</p>` : ""}
    </div>
  </div>`;
}

/* Wired once per frame: the pane keeps the frame across rebuilds, so wiring it
   again on every rebuild would stack listeners on the same buttons. */
export function wirePreviews(session: Session, redraw: () => void): void {
  const root = detailPane.querySelector<HTMLElement>(".pv");
  if (!root) return;
  markPreviewsSeen(session);
  if (root.dataset.wired === "1") {
    const shown = chosenPreview(session);
    const stamp = root.querySelector<HTMLElement>("[data-pv-ago]");
    if (shown && stamp) stamp.textContent = ago((app.feed.now || Date.now() / 1000) - shown.at);
    // The frame is kept across rebuilds, so the star is brought up to date in place.
    paintStar(root, shown);
    return;
  }
  root.dataset.wired = "1";
  wireImmersive(root, session);

  const view = root.querySelector<HTMLIFrameElement>(".pv-view");
  const screen = root.querySelector<HTMLElement>(".pv-screen");
  const loaded = () => screen?.classList.add("is-loaded");
  view?.addEventListener("load", loaded);
  // A page that will not be framed never fires load in some browsers; the
  // shimmer is not left running over it forever.
  setTimeout(loaded, 6000);

  for (const button of root.querySelectorAll<HTMLElement>("[data-preview]")) {
    button.addEventListener("click", () => {
      picks.set(session.sessionId, { id: button.dataset.preview, at: Date.now() / 1000 });
      redraw();
    });
  }
  for (const button of root.querySelectorAll<HTMLElement>("[data-device]")) {
    button.addEventListener("click", () => {
      // In place: changing the size must not reload the page.
      localStorage.setItem(DEVICE_KEY, button.dataset.device);
      root.querySelector<HTMLElement>(".pv-stage").dataset.device = button.dataset.device;
      for (const other of root.querySelectorAll<HTMLElement>("[data-device]")) {
        other.setAttribute("aria-pressed", String(other === button));
      }
    });
  }
  root.querySelector("[data-pv='reload']")?.addEventListener("click", () => {
    if (!view) return;
    screen?.classList.remove("is-loaded");
    // Reassigning src reloads a cross-origin frame too, where
    // contentWindow.location.reload() would be refused.
    const src = view.src;
    view.src = "about:blank";
    requestAnimationFrame(() => { view.src = src; });
  });
  root.querySelector<HTMLButtonElement>("[data-pv='star']")?.addEventListener("click", async (event) => {
    const shown = chosenPreview(session);
    if (!shown) return;
    await toggleStar(session, shown, event.currentTarget as HTMLButtonElement);
    paintStar(root, shown);
  });
  root.querySelector("[data-pv='copy']")?.addEventListener("click", () => {
    const shown = chosenPreview(session);
    if (shown) copyText(previewHref(shown), "Link copied");
  });
  root.querySelector<HTMLButtonElement>("[data-pv='remove']")?.addEventListener("click", async (event) => {
    const shown = chosenPreview(session);
    if (!shown) return;
    await run("/api/previews/remove", session, event.currentTarget as HTMLButtonElement, null, { id: shown.id });
  });
}

/* ------------------------------------------------------------ full screen
   On a phone the frame is a postcard under the session's header, and a swipe
   on it scrolls the page inside rather than the panel — so there is no way to
   make it bigger. The frame wears a catch over it on a phone: swipe up on it
   (or tap it) and the stage grows out of where it sits to fill the screen,
   with the page live underneath. The cross, Escape or the phone's own back
   button put it back.

   The page itself is never moved in the document: moving an iframe reloads it.
   The stage turns `position: fixed` where it stands and a clip-path animates
   between the two boxes. */

const reduceMotion = () => still();

function clipFor(rect: DOMRect, radius: number): string {
  const w = window.innerWidth, h = window.innerHeight;
  return `inset(${Math.max(0, rect.top)}px ${Math.max(0, w - rect.right)}px ${Math.max(0, h - rect.bottom)}px ${Math.max(0, rect.left)}px round ${radius}px)`;
}

function enterImmersive(root: HTMLElement, session: Session): void {
  if (root.classList.contains("pv--immersive")) return;
  const stage = root.querySelector<HTMLElement>(".pv-stage");
  const from = stage.getBoundingClientRect();
  immersive = session.sessionId;
  root.classList.add("pv--immersive");
  document.documentElement.classList.add("pv-immersive");
  // So the phone's back button leaves full screen rather than the panel.
  try { history.pushState({ pvImmersive: true }, ""); } catch { /* sandboxed */ }
  if (!reduceMotion()) {
    stage.animate([{ clipPath: clipFor(from, 24) }, { clipPath: "inset(0 round 0)" }],
      { duration: 420, easing: "cubic-bezier(0.2, 0, 0, 1)" });
  }
}

function leaveImmersive(root: HTMLElement): void {
  if (!root.classList.contains("pv--immersive")) return;
  immersive = null;
  const stage = root.querySelector<HTMLElement>(".pv-stage");
  const done = () => {
    root.classList.remove("pv--immersive", "pv--leaving");
    document.documentElement.classList.remove("pv-immersive");
  };
  if (reduceMotion()) return done();
  // Where it goes back to: measured with the stage briefly back in the flow.
  root.classList.remove("pv--immersive");
  const to = stage.getBoundingClientRect();
  root.classList.add("pv--immersive", "pv--leaving");
  stage.animate([{ clipPath: "inset(0 round 0)" }, { clipPath: clipFor(to, 24) }],
    { duration: 320, easing: "cubic-bezier(0.3, 0, 0.8, 0.15)" }).finished.then(done, done);
}

/* Leaving goes through history when entering pushed an entry, so the entry is
   not left behind for the back button to trip over later. */
function requestLeave(root: HTMLElement): void {
  if (history.state?.pvImmersive) history.back();
  else leaveImmersive(root);
}

function wireImmersive(root: HTMLElement, session: Session): void {
  const catcher = root.querySelector<HTMLElement>(".pv-catch");
  document.documentElement.classList.toggle("pv-immersive", root.classList.contains("pv--immersive"));

  let startY = 0, startX = 0, tracking = false;
  catcher?.addEventListener("touchstart", (event) => {
    tracking = true;
    startY = event.touches[0].clientY;
    startX = event.touches[0].clientX;
  }, { passive: true });
  // Not passive: an upward swipe is taken here instead of nudging the panel.
  catcher?.addEventListener("touchmove", (event) => {
    if (!tracking) return;
    const dy = event.touches[0].clientY - startY;
    const dx = event.touches[0].clientX - startX;
    if (dy < -14 && Math.abs(dy) > Math.abs(dx)) {
      tracking = false;
      event.preventDefault();
      enterImmersive(root, session);
    } else if (Math.abs(dy) > 14 || Math.abs(dx) > 14) {
      tracking = false; // a swipe some other way is the panel's to scroll
    }
  }, { passive: false });
  catcher?.addEventListener("touchend", () => { tracking = false; }, { passive: true });
  // A tap — or a click, on a narrow desktop window — goes full screen too.
  catcher?.addEventListener("click", () => enterImmersive(root, session));
  root.querySelector(".pv-exit")?.addEventListener("click", () => requestLeave(root));

  const onPop = () => {
    if (!root.isConnected) return window.removeEventListener("popstate", onPop);
    if (!history.state?.pvImmersive) leaveImmersive(root);
  };
  window.addEventListener("popstate", onPop);
  const onKey = (event: KeyboardEvent) => {
    if (!root.isConnected) return document.removeEventListener("keydown", onKey, true);
    if (event.key === "Escape" && root.classList.contains("pv--immersive")) {
      event.stopImmediatePropagation();
      requestLeave(root);
    }
  };
  document.addEventListener("keydown", onKey, true);
}

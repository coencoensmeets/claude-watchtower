/* Motion that has to know what changed.

   The list rows and the whole detail pane are rebuilt from markup whenever
   something in them changes, so a CSS transition never has an old value to
   start from, and an entrance animation on a message would replay for every
   message on every poll. This module remembers what was on screen last time
   and marks only what is genuinely new: a message that just landed, a row
   that just appeared or changed state, a header whose state colour moved, a
   tab that was just chosen. The look of each is in styles/motion.css. */

/* Still when the system asks for less motion, or Settings → Appearance does. */
export const still = () => {
  const asked = document.documentElement.dataset.motion;
  return asked === "reduced" || asked === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches;
};

/* Adds a class for one run of its animation, then takes it off again so the
   element is not left claiming to be arriving. A fallback timer covers an
   element whose animation never runs (hidden, or reduced motion). */
function playOnce(el: HTMLElement, cls: string, ms = 1200) {
  el.classList.add(cls);
  const done = () => el.classList.remove(cls);
  el.addEventListener("animationend", (e) => { if (e.target === el) done(); }, { once: true });
  setTimeout(done, ms);
}

/* ------------------------------------------------------------- list rows */
const rowStatus = new Map<string, string>();
let listPainted = false;
let cascade = 0;

/* Called for a row each time it is painted. The first paint of the list
   cascades every row in; after that a row arrives only if its session is new,
   and pops its avatar only if its state actually moved. */
export function rowPainted(button: HTMLElement, id: string, status: string, rebuilt: boolean) {
  const was = rowStatus.get(id);
  rowStatus.set(id, status);
  if (still() || !rebuilt) return;
  if (!listPainted) {
    button.style.setProperty("--stagger", String(cascade++));
    playOnce(button, "is-arriving", 1600);
  } else if (was === undefined) {
    playOnce(button, "is-arriving");
  } else if (was !== status) {
    playOnce(button, "is-shifting");
  }
}
/* Called once the list has been painted, so the cascade runs only once. */
export function listDone(count: number) { if (count) { listPainted = true; cascade = 0; } }

/* ---------------------------------------------------------- detail pane */
let lastHeader: { id: string; container: string } | null = null;
const seenMessages = new Map<string, Set<string>>();
let tabFrom: { id: string; tab: string; left: number; width: number } | null = null;

/* Read just before the pane is rebuilt: where the selected tab sits now. */
export function beforeDetail(pane: HTMLElement, id: string) {
  const tabs = pane.querySelector<HTMLElement>(".tabs");
  const tab = tabs?.querySelector<HTMLElement>('.tab[aria-selected="true"]');
  tabFrom = tab && pane.dataset.sessionId === id
    ? { id, tab: tab.dataset.tab, left: tab.offsetLeft, width: tab.offsetWidth } : null;
}

/* Called right after the pane is rebuilt. */
export function afterDetail(pane: HTMLElement, id: string, status: string, container: string, loaded: boolean) {
  const header = pane.querySelector<HTMLElement>(".detail-header");
  if (header) header.dataset.status = status;
  const prev = lastHeader;
  lastHeader = { id, container };
  const moving = !still();

  // The header changing state: the new colour grows out of the corner the
  // state lamp would be in, over the old one.
  if (moving && header && prev && prev.id === id && prev.container !== container) {
    header.style.setProperty("--wipe-from", prev.container);
    playOnce(header, "is-shifting");
  }

  // The tab strip: a pill slides from the tab you were on to the one you chose.
  const tabs = pane.querySelector<HTMLElement>(".tabs");
  const tab = tabs?.querySelector<HTMLElement>('.tab[aria-selected="true"]');
  if (moving && tabs && tab && tabFrom && tabFrom.id === id && tabFrom.tab !== tab.dataset.tab) {
    const ink = document.createElement("span");
    ink.className = "tabs__ink";
    ink.style.left = `${tab.offsetLeft}px`;
    ink.style.width = `${tab.offsetWidth}px`;
    ink.style.top = `${tab.offsetTop}px`;
    ink.style.height = `${tab.offsetHeight}px`;
    tabs.appendChild(ink);
    tab.classList.add("tab--landing");
    const run = ink.animate([
      { transform: `translateX(${tabFrom.left - tab.offsetLeft}px)`, width: `${tabFrom.width}px` },
      { transform: "translateX(0)", width: `${tab.offsetWidth}px` },
    ], { duration: 320, easing: "cubic-bezier(0.2, 0, 0, 1)" });
    const land = () => { ink.remove(); tab.classList.remove("tab--landing"); };
    run.onfinish = land;
    run.oncancel = land;
  }
  tabFrom = null;

  // Messages: only the ones after the last one already seen rise in, so a
  // page of older history loading above does not animate, and neither does
  // the first paint of a conversation.
  if (!loaded) return;
  const rows = [...pane.querySelectorAll<HTMLElement>(".chat .msg[data-key], .chat .activity-row[data-key]")];
  const seen = seenMessages.get(id);
  const now = new Set(rows.map((row) => row.dataset.key));
  if (seen && moving) {
    let lastSeen = -1;
    rows.forEach((row, i) => { if (seen.has(row.dataset.key)) lastSeen = i; });
    let order = 0;
    // A session you come back to after a while has a lot of news; only the
    // last few rise in, the rest are simply there.
    for (const row of rows.slice(lastSeen + 1).slice(-6)) {
      row.style.setProperty("--stagger", String(order++));
      playOnce(row, "is-arriving");
    }
  }
  seenMessages.set(id, seen ? new Set([...seen, ...now]) : now);
}

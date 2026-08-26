/* ---------------------------------------------------------------- element refs

   The page's own furniture, looked up once. Everything here is in
   web/index.html, so each one is typed as the element it actually is rather
   than as the `HTMLElement` getElementById promises — a button that can be
   disabled, a link that has an href, a field that has a value. Getting this
   wrong is caught by `tsc --noEmit`, which is most of the point.

   Asserted rather than checked: these are static furniture, present before the
   first script runs, so a null here is not a case to handle but a page that is
   broken — and every reader would otherwise carry the same dead branch. */

const byId = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

export const panes = byId<HTMLDivElement>("panes");
export const settingsButton = byId<HTMLButtonElement>("settingsButton");
export const sessionList = byId<HTMLUListElement>("sessionList");
export const listEmpty = byId<HTMLParagraphElement>("listEmpty");
export const detailPane = byId<HTMLElement>("detailPane");
export const chipSet = byId<HTMLUListElement>("chipSet");
export const barSupporting = byId<HTMLSpanElement>("barSupporting");
export const barNudge = byId<HTMLDivElement>("barNudge");
export const barNudgeLink = byId<HTMLAnchorElement>("barNudgeLink");
export const barNudgeIcon = byId<HTMLSpanElement>("barNudgeIcon");
export const barNudgeText = byId<HTMLSpanElement>("barNudgeText");
export const linkChip = byId<HTMLSpanElement>("linkChip");
export const linkChipText = byId<HTMLSpanElement>("linkChipText");
export const snackbar = byId<HTMLDivElement>("snackbar");
export const endScrim = byId<HTMLDivElement>("endScrim");
export const backButton = byId<HTMLButtonElement>("backButton");
export const pickBar = byId<HTMLDivElement>("pickBar");
export const pickCount = byId<HTMLSpanElement>("pickCount");
export const pickGroup = byId<HTMLButtonElement>("pickGroup");
export const pickClear = byId<HTMLButtonElement>("pickClear");

/* -------------------------------------------------------------------- events

   What a listener was actually clicked on. `event.target` is an `EventTarget`,
   which is the honest type — an event can be raised on the document or a
   window, neither of which has a `closest` to call. These narrow it once, at
   the top of a handler, instead of every handler asserting it for itself. */

/** The element an event happened on, or null if it was not on one. */
export const hitElement = (event: Event): HTMLElement | null =>
  event.target instanceof HTMLElement ? event.target : null;

/** The control a listener is attached to — which is the thing that was
    operated, unlike `target`, which is whatever inside it was hit. Buttons by
    default, since that is what almost every listener here is bound to. */
export const control = <T extends HTMLElement = HTMLButtonElement>(event: Event): T =>
  event.currentTarget as T;

/** The nearest ancestor of the event's target matching `selector`. */
export const hitClosest = <T extends HTMLElement = HTMLElement>(
  event: Event, selector: string,
): T | null => hitElement(event)?.closest<T>(selector) ?? null;

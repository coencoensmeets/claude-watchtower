import { ui } from "../state.js";
import { sessionList } from "./dom.js";
import { escapeHtml } from "./format.js";
import { conceal, reveal } from "./overlay.js";

/* ==========================================================================
   Sidebar context menu — the per-session actions, at the pointer.
   Right-clicking does NOT change the selection: on a narrow screen selecting
   swaps the sidebar out for the detail pane, which would take the menu with it.
   The menu names its session instead, so the target is never ambiguous.
   ========================================================================== */
export const sessionMenu = document.getElementById("sessionMenu");   // or the key of the group it belongs to, for a header menu
/* Whether a menu is standing at all, which is not the same question as what it
   points at: a menu opened from a toolbar button — the Git tab's overflow, the
   commit split button — belongs to neither a row nor a group, and asking after
   those two left it with nothing to dismiss it. The DOM is the honest answer. */
export const menuIsOpen = () => sessionMenu.dataset.open === "true";

/* One menu, opened over a row or over a group header. `forId` / `forGroup` say
   which, so the thing it acts on is marked while it stands open. An item's hint
   — which window matched, why it is disabled — is a tooltip rather than a line
   of its own: a menu you have to read is a menu nobody reads. */
export function openMenu({ title, label, items, forId = null, forGroup = null }, x, y) {
  closeSessionMenu();
  sessionMenu.setAttribute("aria-label", label);
  sessionMenu.innerHTML =
    `<p class="menu__title md-label-small" role="none">${escapeHtml(title)}</p>` +
    items.map((item) => item.divider
      ? `<hr class="menu__divider" role="separator">`
      : `<button class="menu__item md-state${item.danger ? " menu__item--danger" : ""}" type="button"
           role="menuitem" data-key="${item.key}"${item.disabled ? " disabled" : ""}${
             item.hint ? ` title="${escapeHtml(item.hint)}"` : ""}>
          <span class="menu__icon">${item.icon}</span>
          <span class="menu__label md-label-large">${escapeHtml(item.label)}</span>
        </button>`).join("");

  for (const button of sessionMenu.querySelectorAll(".menu__item")) {
    const item = items.find((i) => i.key === button.dataset.key);
    if (!item?.run) continue;
    button.addEventListener("click", () => {
      closeSessionMenu({ restoreFocus: false });
      item.run(button);
    });
  }

  ui.menuFor = forId;
  ui.menuGroup = forGroup;
  sessionMenu.hidden = false;
  // offsetWidth, not the bounding rect: the open transition scales the box and
  // would give a measurement smaller than the space it is about to need.
  const pad = 8;
  // Cap before measuring: a menu taller than the window has to scroll, or the
  // clamp below would push its last items off the bottom edge.
  sessionMenu.style.maxHeight = `${window.innerHeight - pad * 2}px`;
  const width = sessionMenu.offsetWidth;
  const height = sessionMenu.offsetHeight;
  sessionMenu.style.left = `${Math.max(pad, Math.min(x, window.innerWidth - width - pad))}px`;
  sessionMenu.style.top = `${Math.max(pad, Math.min(y, window.innerHeight - height - pad))}px`;
  // Opened only now it is in the right place, so a menu reopened before the last
  // one finished leaving does not slide across from where that one stood.
  reveal(sessionMenu);

  const mark = forId
    ? sessionList.querySelector(`li[data-id="${CSS.escape(forId)}"]`)
    : forGroup ? sessionList.querySelector(`li[data-group="${CSS.escape(forGroup)}"]`) : null;
  if (mark) mark.dataset.menu = "open";
  sessionMenu.querySelector(".menu__item:not([disabled])")?.focus();
}

export function closeSessionMenu({ restoreFocus = true } = {}) {
  if (!menuIsOpen()) return;
  for (const row of sessionList.querySelectorAll('[data-menu="open"]')) delete row.dataset.menu;
  conceal(sessionMenu);
  ui.menuFor = null;
  ui.menuGroup = null;
  const back = ui.menuReturn;
  ui.menuReturn = null;
  if (restoreFocus && back) {
    sessionList.querySelector(`[data-id="${CSS.escape(back)}"] .session-item`)?.focus();
  }
}

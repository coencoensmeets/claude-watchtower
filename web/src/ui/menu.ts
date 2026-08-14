import { ui } from "../state.js";
import { sessionList } from "./dom.js";
import { escapeHtml } from "./format.js";
import { conceal, reveal } from "./overlay.js";
export const sessionMenu = document.getElementById("sessionMenu");

export const menuIsOpen = () => sessionMenu.dataset.open === "true";

export function openMenu({ title, label, items, forId = null, forGroup = null }, x, y) {
  closeSessionMenu();
  sessionMenu.setAttribute("aria-label", label);
  sessionMenu.innerHTML =
    `<p class="menu__title md-label-small" role="none">${escapeHtml(title)}</p>` +
    items.map((item) => item.divider
      ? `<hr class="menu__divider" role="separator">`
      : `<button class="menu__item md-state${item.danger ? " menu__item--danger" : ""}" type="button"
           role="menuitem" data-key="${item.key}"${item.disabled ? " disabled" : ""}>
          <span class="menu__icon">${item.icon}</span>
          <span class="menu__label md-label-large">${escapeHtml(item.label)}</span>
          ${item.hint ? `<span class="menu__hint md-label-small">${escapeHtml(item.hint)}</span>` : ""}
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

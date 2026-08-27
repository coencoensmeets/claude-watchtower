/* ==========================================================================
   Copying, on a page that may not be allowed to.

   `navigator.clipboard` is a secure-context API. On loopback the panel has it
   and it is instant. Opened from a phone the panel is `http://192.168.…`, which
   is not a secure context, so the whole object is *absent* — not present and
   refusing — and the panel's three copy actions would all throw on exactly the
   screen they were added for.

   So there are two paths, and the order they are tried in is the whole of the
   design:

   1. Nothing may be awaited before the copy happens. Both paths need the user
      activation of the click that asked for it, and an `await` hands control
      back to the page loop — Safari drops the activation across it outright,
      and it is not a thing to be relying on elsewhere either. So the legacy
      path runs synchronously inside the handler, and `navigator.clipboard` is
      called without waiting for its promise.

   2. The legacy path is the one that works everywhere, so it goes first when
      there is no clipboard object at all. `execCommand` is deprecated and every
      browser still implements it; the trade only comes up because serving the
      panel to the network means serving it without TLS.

   And when neither works — no activation, a browser that has finally dropped
   `execCommand`, a locked-down clipboard — the text is *selected* instead and
   the snackbar says so. Ctrl+C then does what the button could not, which is a
   great deal better than a button that reports failure and leaves you to find
   the text again.
   ========================================================================== */

import { showSnackbar } from "./snackbar.js";

/** Put `text` on the clipboard, synchronously, and say so. True if it landed.

    Not async, deliberately: everything here has to happen inside the click. */
export function copyText(text: string, done: string): boolean {
  const said = String(text ?? "");
  if (legacyCopy(said)) {
    showSnackbar(done);
    return true;
  }
  // No activation, or no execCommand: the modern API may still take it, and on
  // a secure origin it usually does. Not awaited — the answer is a promise, and
  // waiting for it is what would cost the activation if this is ever reordered.
  // The failure branch is the only one that says anything, since success has
  // already been reported by the time it resolves.
  const modern = navigator.clipboard?.writeText?.(said);
  if (modern) {
    modern.then(() => showSnackbar(done)).catch(() => offerTheSelection(said));
    return true;
  }
  offerTheSelection(said);
  return false;
}

/* The off-screen field, selected, with `copy` intercepted so the data is set by
   hand rather than left to whatever the browser makes of the selection. The
   interception is what makes this reliable across the cases where a selection
   alone is not enough — a readonly field, a field the page has styled away. */
function legacyCopy(text: string): boolean {
  if (!document.execCommand) return false;
  const box = document.createElement("textarea");
  box.value = text;
  box.setAttribute("readonly", "");
  // Off the screen but not `display: none`, `hidden`, or zero-sized: a field
  // with no box at all cannot hold a selection, and a selection is what is
  // being copied.
  box.style.cssText = "position:fixed;top:0;left:-9999px;width:1px;height:1px;opacity:0;pointer-events:none";
  document.body.appendChild(box);
  const was = document.activeElement as HTMLElement | null;
  let ok = false;
  const onCopy = (event: ClipboardEvent) => {
    event.clipboardData?.setData("text/plain", text);
    event.preventDefault();
    ok = true;
  };
  document.addEventListener("copy", onCopy, true);
  try {
    box.focus({ preventScroll: true });
    box.select();
    // iOS wants a range on the field, not just focus, before it will copy.
    box.setSelectionRange(0, text.length);
    // Both halves have to agree: execCommand returns false when the browser
    // refused, and the listener not firing means nothing reached the clipboard
    // even if it returned true.
    ok = document.execCommand("copy") && ok;
  } catch {
    ok = false;
  } finally {
    document.removeEventListener("copy", onCopy, true);
    box.remove();
    // Whatever was focused before — the composer, mid-sentence — gets it back.
    was?.focus?.({ preventScroll: true });
  }
  return ok;
}

/* Last resort, and not a failure message: the text is put under a selection so
   the browser's own copy works on it. The one thing worse than a button that
   cannot reach the clipboard is one that cannot and leaves you no closer. */
function offerTheSelection(text: string): void {
  const block = [...document.querySelectorAll<HTMLElement>("pre.md-code code")]
    .find((code) => code.textContent === text);
  const range = document.createRange();
  if (block) range.selectNodeContents(block);
  const picked = window.getSelection();
  if (block && picked) {
    picked.removeAllRanges();
    picked.addRange(range);
    showSnackbar("Selected it — press Ctrl+C");
    return;
  }
  showSnackbar("Could not reach the clipboard");
}

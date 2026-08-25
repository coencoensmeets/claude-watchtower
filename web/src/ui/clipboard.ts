/* ==========================================================================
   Copying, on a page that may not be allowed to.

   `navigator.clipboard` is a secure-context API, and a panel opened from a
   phone is `http://192.168.…` — not a secure context, so the whole object is
   absent rather than present-and-refusing. The panel's own three copy actions
   would all throw on exactly the screen they were added for.

   So: the modern path when there is one, and a selected off-screen textarea
   with execCommand when there is not. execCommand is deprecated and every
   browser still implements it, which is the trade being made here — one that
   only comes up because serving the panel to the network means serving it
   without TLS.

   Both paths need to run inside the gesture that asked for the copy, so this is
   called straight from a click handler and never after an await of its own.
   ========================================================================== */

import { showSnackbar } from "./snackbar.js";

export async function copyText(text: string, done: string): Promise<boolean> {
  try {
    // Absent on an insecure origin, so this throws rather than resolving false.
    await navigator.clipboard.writeText(text);
    showSnackbar(done);
    return true;
  } catch (error) {
    // Clipboard permission can be refused even on loopback, and off loopback
    // there is no clipboard object at all. Fall back rather than leaving a menu
    // item that silently does nothing.
    const box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "");
    box.style.cssText = "position:fixed;top:-1000px;left:0;opacity:0";
    document.body.appendChild(box);
    box.select();
    // iOS wants a range on the field, not just focus, before it will copy.
    box.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    box.remove();
    showSnackbar(ok ? done : "Could not reach the clipboard");
    return ok;
  }
}

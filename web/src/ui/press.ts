/* ==========================================================================
   A long press, for the actions a right-click opens.

   A phone has no right-click. Android's browser turns a long press into a
   `contextmenu` event and iOS mostly does not, so a panel that waits for one
   works on half the phones in the house — which is the same as not working.
   This is the gesture itself: press, hold still, and the handler fires with
   where the finger is.

   Only for touch and pen. A mouse already has the button, and running both
   would mean a slow right-hand click opening the menu twice.

   What it is *not* used for is the conversation: a long press on text is how a
   phone starts a selection, and selecting a passage is what raises the Comment
   chip. The turns carry a button instead — see views/change.js.
   ========================================================================== */

const HOLD_MS = 450;      // long enough not to fire on a tap, short enough to feel deliberate
const SLOP = 10;          // px of drift allowed before it is a scroll, not a press
/* A press that fired swallows what the browser sends next. Letting go after a
   long press still produces a click, and that click lands on whatever is now
   under the finger — which is the menu that just opened there. Android sends its
   own `contextmenu` at about the same moment, which would open the menu a second
   time.

   A flag rather than a stopwatch, because the click arrives when the finger
   lifts and that can be a second later or five: any window long enough to cover
   a slow release is long enough to eat a real tap after a quick one. So exactly
   one click is swallowed, and it is the one that ends this press.

   The staleness guard is the next press: a long press whose finger slid off and
   released over nothing produces no click at all, and the flag would otherwise
   sit there waiting to eat something it was never meant to. Anything that
   starts a new press clears it — by then it can only be a leftover, our own
   pointerdown having run long before the hold fired. */
let armed = false;

export function onLongPress(root, handler) {
  let timer: ReturnType<typeof setTimeout> | 0 = 0;
  let from = null;

  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = 0;
    from = null;
  };

  root.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" || !event.isPrimary) return;
    cancel();
    from = { x: event.clientX, y: event.clientY, target: event.target };
    timer = setTimeout(() => {
      const held = from;
      cancel();
      if (!held) return;
      armed = true;
      handler(held);
    }, HOLD_MS);
  });
  root.addEventListener("pointermove", (event) => {
    if (!from) return;
    if (Math.abs(event.clientX - from.x) > SLOP || Math.abs(event.clientY - from.y) > SLOP) cancel();
  });
  for (const done of ["pointerup", "pointercancel", "pointerleave"]) {
    root.addEventListener(done, cancel);
  }
  // The list scrolls under the finger, so a scroll anywhere is a scroll here.
  // Capture, because scroll does not bubble.
  document.addEventListener("scroll", cancel, true);
}

/* The swallowing. Capture and on the document, so it runs before the listener
   the click was heading for — including the menu's own items. */
document.addEventListener("click", (event) => {
  if (!armed) return;
  armed = false;
  event.preventDefault();
  event.stopPropagation();
}, true);
/* Android's own long-press menu request, which the gesture above has already
   answered. It does not disarm: the click that ends the press is still coming. */
document.addEventListener("contextmenu", (event) => {
  if (!armed) return;
  event.preventDefault();
  event.stopPropagation();
}, true);
document.addEventListener("pointerdown", () => { armed = false; }, true);

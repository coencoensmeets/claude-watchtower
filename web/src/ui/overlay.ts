/* --------------------------------------------------- coming and going ------ */
/* Everything that floats over the panel is written to fade in and out, and until
   now none of it faded out: the close paths set data-open="false" and `hidden`
   in the same breath, `hidden` is display:none, and a box that is display:none
   is not drawn — so the exit each of these was given a transition for never got
   a single frame. Things arrived gently and then blinked out of existence.

   These two keep the attribute and the transition in step. Opening flushes the
   closed style first, the way the menu already did by measuring itself between
   the two lines; closing holds the box on screen for exactly as long as the fade
   it is playing, and only then takes it out of the layout. */
const EXIT_MS = 200; /* --md-sys-motion-duration-short4 */
const exitTimers = new WeakMap();

export function reveal(el) {
  if (!el) return;
  // Already open. Worth the check rather than setting it again: these are called
  // from a scroll handler, and the reflow below on every frame of a flick is a
  // measurable cost for no change at all.
  if (!el.hidden && el.dataset.open === "true") return;
  clearTimeout(exitTimers.get(el));
  exitTimers.delete(el);
  el.hidden = false;
  // A box arriving from display:none has no previous style to move from, and
  // would land open on the first frame. Reading a layout value forces the closed
  // state to be computed, which gives the transition its starting point.
  void el.offsetWidth;
  el.dataset.open = "true";
}

export function conceal(el) {
  if (!el || el.hidden) return;
  // Already on its way out. Without this a scroll handler calling it every frame
  // would restart the timer every frame, and the box would sit there faded to
  // nothing but still holding its place for as long as you kept scrolling.
  if (el.dataset.open === "false") return;
  el.dataset.open = "false";
  clearTimeout(exitTimers.get(el));
  // Hidden on a timer rather than on transitionend: a box whose parent is torn
  // out mid-fade — the detail pane rebuilds under these on any poll — never
  // fires the event, and would be left behind holding its space forever.
  exitTimers.set(el, setTimeout(() => {
    el.hidden = true;
    exitTimers.delete(el);
  }, EXIT_MS));
}

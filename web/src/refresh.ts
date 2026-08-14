/* Redrawing, without every part of the panel importing the whole of it.

   Almost everything here ends by asking for a redraw: stage a file and the
   index, the header and the git pane all have to catch up. While the panel was
   one scope that was a bare call to render(). Split into modules it becomes a
   cycle — git imports render from main, main imports the git pane from git —
   and a cycle is a poor thing to build a plugin API on later.

   So the direction is inverted. main.ts, which owns the render loop, hands its
   functions here once at boot; everything else asks for a redraw through this
   module and never learns where the loop lives.

   Before boot these are no-ops rather than errors: a module may run code while
   it is being imported, which happens before main.ts has finished starting up,
   and "nothing to redraw yet" is the truth at that point. */

type Redraw = () => void;
/* The detail pane takes one argument: whether to rebuild even when the panel
   thinks nothing it draws from has changed. Dropping it here is invisible —
   the call still succeeds, and the pane simply never updates. */
type RedrawDetail = (force?: boolean) => void;
type Reload = () => void | Promise<void>;

let redraw: Redraw = () => {};
let redrawDetail: RedrawDetail = () => {};
let reload: Reload = () => {};

/** Called once by main.ts, which owns the loop. */
export function serveRefresh(handlers: {
  render: Redraw; renderDetail: RedrawDetail; poll: Reload;
}): void {
  redraw = handlers.render;
  redrawDetail = handlers.renderDetail;
  reload = handlers.poll;
}

/** Redraw from the state the panel already has. */
export const refresh = (): void => redraw();

/** Redraw the detail pane only. `force` rebuilds it even if nothing changed. */
export const refreshDetail = (force?: boolean): void => redrawDetail(force);

/** Ask the server for the state again, then redraw. */
export const reloadState = (): void | Promise<void> => reload();

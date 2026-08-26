// The panel on a phone, in a real browser emulating one.
//
// Everything here is a thing only a browser can answer: whether the shell is as
// tall as the glass rather than as tall as the glass plus a toolbar, whether a
// long press opens the actions on a row, whether letting go of that press then
// picks whatever the menu put under the finger. None of it is visible to a test
// that only reads the sources.
//
//   1. start a panel (a fixture directory is fine, but a real one has a
//      conversation to right-click in):
//        python3 server.py --port 8788
//   2. start a throwaway browser with CDP open:
//        google-chrome --headless=new --remote-debugging-port=9333 \
//          --user-data-dir=$(mktemp -d) about:blank
//   3. node tests/phone-check.mjs
//
// Override PANEL_URL / CDP_URL to point elsewhere.
//
// One thing it cannot check on the machine: headless Chrome reports
// `(hover: none)` whatever it is running on, so the hover-revealed button is
// always revealed here. What the browser can be asked is checked in the browser;
// the hover rule itself is read out of the stylesheet at the end.

import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const PANEL = process.env.PANEL_URL || "http://127.0.0.1:8788";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
const here = dirname(fileURLToPath(import.meta.url));

let failures = 0;
function check(what, ok, note = "") {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${note ? `  — ${note}` : ""}`);
  if (!ok) failures++;
}

const targets = await (await fetch(`${CDP}/json/list`)).json();
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const consoleErrors = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") consoleErrors.push(msg.params.entry.text);
  if (msg.method === "Runtime.exceptionThrown") consoleErrors.push(String(msg.params.exceptionDetails.text));
});
await new Promise((r) => ws.addEventListener("open", r));
const send = (method, params = {}) =>
  new Promise((resolve) => { const n = ++id; pending.set(n, resolve); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (expression) => {
  const res = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (res.result?.exceptionDetails) {
    throw new Error(String(res.result.exceptionDetails.exception?.description || res.result.exceptionDetails.text));
  }
  return res.result.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Runtime.enable");
await send("Log.enable");
await send("Page.enable");

/* A phone: a 390pt screen, a touch digitiser, and no mouse. */
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
await send("Emulation.setEmitTouchEventsForMouse", { enabled: true, configuration: "mobile" });
await send("Page.navigate", { url: PANEL });
await sleep(2000);
// Arriving fresh, rather than remembering which session was last open.
await evaluate(`localStorage.clear()`);
await send("Page.reload");
await sleep(3500);

/* ------------------------------------------------------------- the shell fits */
const shell = await evaluate(`(() => ({
  h: innerHeight, appH: Math.round(document.querySelector('.app').getBoundingClientRect().height),
  view: document.querySelector('.panes').dataset.view,
  wide: document.documentElement.scrollWidth > innerWidth,
}))()`);
check("the shell is as tall as the visible screen, not as tall as the page",
  shell.appH === shell.h, JSON.stringify(shell));
check("and nothing spills off the side of it", !shell.wide);
check("one pane at a time, starting on the list", shell.view === "list");

/* --------------------------------------------------- press and hold a row */
/* The only route to a session's actions on a phone: there is no right-click, and
   the `contextmenu` event a long press produces is Android's alone. */
const hold = await evaluate(`(async () => {
  const item = document.querySelector('li[data-id] .session-item');
  if (!item) return { skip: 'no sessions on the list' };
  const r = item.getBoundingClientRect();
  const at = { clientX: Math.round(r.left + 40), clientY: Math.round(r.top + 20),
               pointerId: 1, pointerType: 'touch', isPrimary: true, bubbles: true, cancelable: true };
  item.dispatchEvent(new PointerEvent('pointerdown', at));
  await new Promise(r => setTimeout(r, 1600));           // a slow release, deliberately
  const menu = document.getElementById('sessionMenu');
  const open = menu.dataset.open;
  const box = menu.getBoundingClientRect();
  const first = menu.querySelector('.menu__item');
  // Measured now, while it stands open: the closing transition scales the box,
  // and a height read after the Escape below is the height of a menu leaving.
  const itemH = Math.round(first.getBoundingClientRect().height);
  let picked = false;
  first.addEventListener('click', () => { picked = true; }, { once: true });
  item.dispatchEvent(new PointerEvent('pointerup', at));
  first.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  await new Promise(r => setTimeout(r, 40));
  // And the tap after the press must not be eaten with it.
  let through = false;
  document.addEventListener('click', () => { through = true; }, { once: true });
  document.body.dispatchEvent(new PointerEvent('pointerdown', { ...at, pointerId: 2 }));
  document.body.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  await new Promise(r => setTimeout(r, 40));
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  return { open, picked, through, itemH,
           onScreen: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight };
})()`);
if (hold.skip) check("holding a row opens its actions", false, hold.skip);
else {
  check("holding a row opens its actions", hold.open === "true", JSON.stringify(hold));
  check("the menu lands wholly on the screen", hold.onScreen);
  check("its items are a tap target, not a mouse target", hold.itemH >= 48, `${hold.itemH}px`);
  check("letting go — however late — does not pick what opened under the finger", hold.picked === false);
  check("but the tap after the press goes through", hold.through === true);
}

/* ------------------------------------------- into a session, and back out */
const opened = await evaluate(`(async () => {
  for (const row of document.querySelectorAll('li[data-id] .session-item')) {
    row.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch', pointerId: 9, isPrimary: true }));
    row.click();
    await new Promise(r => setTimeout(r, 1300));
    if (document.querySelector('#chatScroll .msg')) break;
  }
  const more = document.querySelector('#chatScroll .msg .msg__more');
  return { view: document.querySelector('.panes').dataset.view,
           back: getComputedStyle(document.getElementById('backButton')).display,
           more: !!more, tap: more ? Math.round(more.getBoundingClientRect().width + 20) : 0,
           header: getComputedStyle(document.querySelector('.detail-header__top')).flexDirection };
})()`);
check("tapping a row brings the conversation forward", opened.view === "detail", JSON.stringify(opened));
check("with a way back to the list", opened.back !== "none");
check("the header stacks rather than pushing its buttons off the screen",
  opened.header === "column", opened.header);
check("every turn carries the button that opens its actions", opened.more === true);
check("and it is a real target, not a 24px one", opened.tap >= 44, `${opened.tap}px`);

/* ----------------------------------- the button opens the message's menu */
const tapped = await evaluate(`(async () => {
  const seen = [];
  const realCreate = URL.createObjectURL;
  URL.createObjectURL = (b) => { seen.push(b); return realCreate.call(URL, b); };
  const realClick = HTMLAnchorElement.prototype.click;
  let name = null;
  HTMLAnchorElement.prototype.click = function () { name = this.download; };
  document.querySelector('#chatScroll .msg .msg__more').click();
  await new Promise(r => setTimeout(r, 80));
  const menu = document.getElementById('sessionMenu');
  const box = menu.getBoundingClientRect();
  const out = { title: menu.querySelector('.menu__title')?.textContent,
                onScreen: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
                marked: !!document.querySelector('#chatScroll [data-menu="open"]') };
  menu.querySelector('.menu__item[data-key="save"]').click();
  await new Promise(r => setTimeout(r, 100));
  out.name = name;
  out.head = seen.length ? (await seen[0].text()).split('\\n')[0] : null;
  URL.createObjectURL = realCreate;
  HTMLAnchorElement.prototype.click = realClick;
  await new Promise(r => setTimeout(r, 60));
  out.stillMarked = !!document.querySelector('#chatScroll [data-menu="open"]');
  return out;
})()`);
check("the button opens the turn's own menu", !!tapped.title, JSON.stringify({ title: tapped.title }));
check("wholly on the screen, hung off the button", tapped.onScreen);
check("the turn is marked while the menu stands over it", tapped.marked === true);
check("and unmarked when it goes", tapped.stillMarked === false);
check("the download is the file a mouse would have got",
  /\.md$/.test(tapped.name || "") && /^# /.test(tapped.head || ""), `${tapped.name} · ${tapped.head}`);

/* ------------------------------------------ is the server still answering? */
/* A phone has no room for the line under the title, so a dropped connection
   used to show as nothing whatever: the conversation simply stopped arriving,
   which is exactly what a quiet session looks like. The bar says which. */
const link = await evaluate(`(async () => {
  const chip = document.getElementById('linkChip');
  const read = () => ({ state: chip.dataset.state, text: chip.textContent.trim(),
                        wide: Math.round(chip.getBoundingClientRect().width) });
  const settle = (ms) => new Promise(r => setTimeout(r, ms));
  const live = read();
  const real = window.fetch;
  try {
    // Everything fails, the way a dropped connection does.
    window.fetch = () => Promise.reject(new TypeError('failed'));
    await settle(1500);
    const blink = read();                    // one missed poll is not a drop
    await settle(4000);
    const lost = read();
    // Now only the conversation fails: the server is up and the chat is not
    // arriving, which is the fault that started all this.
    window.fetch = (u, o) => String(u).startsWith('/api/transcript')
      ? Promise.reject(new TypeError('failed')) : real(u, o);
    await settle(3000);
    const patchy = read();
    window.fetch = real;
    await settle(2500);
    return { live, blink, lost, patchy, back: read() };
  } finally { window.fetch = real; }
})()`);
check("the bar says the panel is connected, with a dot and no words",
  link.live.state === "live" && link.live.text === "" && link.live.wide < 20, JSON.stringify(link.live));
check("one missed poll is not a lost connection", link.blink.state === "live", JSON.stringify(link.blink));
check("but a server that has stopped answering is said out loud",
  link.lost.state === "lost" && /^offline /.test(link.lost.text), JSON.stringify(link.lost));
check("and how long it has been quiet, since nothing else on screen moves",
  /\d+s$/.test(link.lost.text), link.lost.text);
check("a conversation that will not load is told apart from a lost server",
  link.patchy.state === "patchy" && !!link.patchy.text, JSON.stringify(link.patchy));
check("and the bar goes quiet again when the server comes back",
  link.back.state === "live" && link.back.text === "", JSON.stringify(link.back));

/* --------------------------------------------- the header folds out of the way */
/* The header is the whole session in five lines, and on a phone those five lines
   are the conversation's room. Scrolling into the transcript folds it to its
   title; the chevron does the same on demand, and holds against the scroll. */
const fold = await evaluate(`(async () => {
  const scroller = document.getElementById('chatScroll');
  const header = document.querySelector('.detail-header');
  const button = document.querySelector('.fold-button');
  const tall = () => Math.round(header.getBoundingClientRect().height);
  const settle = () => new Promise(r => setTimeout(r, 420));
  scroller.scrollTop = 0; scroller.dispatchEvent(new Event('scroll')); await settle();
  const open = tall();
  const room = scroller.scrollHeight - scroller.clientHeight;
  scroller.scrollTop = scroller.scrollHeight; scroller.dispatchEvent(new Event('scroll')); await settle();
  const scrolled = tall();
  // Unfolded by hand while still scrolled down, and it must stay unfolded.
  button.click(); await settle();
  const byHand = tall();
  scroller.scrollTop = Math.max(200, scroller.scrollTop - 40);
  scroller.dispatchEvent(new Event('scroll')); await settle();
  const held = tall();
  scroller.scrollTop = 0; scroller.dispatchEvent(new Event('scroll')); await settle();
  const back = tall();
  return { open, scrolled, byHand, held, back, room,
           shown: getComputedStyle(button).display,
           tap: Math.round(button.getBoundingClientRect().height) };
})()`);
if (fold.room <= 200) console.log("  --  the folding header: this conversation has nothing to scroll");
else {
  check("scrolling into the conversation folds the header away",
    fold.scrolled < fold.open - 40, JSON.stringify(fold));
  check("the chevron brings it back", fold.byHand === fold.open);
  check("and it stays back while you carry on reading", fold.held === fold.open);
  check("scrolling to the top unfolds it again", fold.back === fold.open);
}
check("the chevron is a tap target on a phone", fold.shown !== "none" && fold.tap >= 40,
  `${fold.shown} · ${fold.tap}px`);

/* ------------------------------------------------ nothing under the chrome */
const box = await evaluate(`(() => {
  const c = document.querySelector('.composer');
  if (!c) return { skip: 'this session has no composer' };
  const r = c.getBoundingClientRect();
  return { bottom: Math.round(r.bottom), h: innerHeight };
})()`);
if (box.skip) console.log(`  --  the message box: ${box.skip}`);
else check("the message box sits on the bottom edge, not under it", box.bottom === box.h, JSON.stringify(box));

const wide = await evaluate(`(() => {
  const scroll = (sel) => { const el = document.querySelector(sel); return el ? el.scrollWidth - el.clientWidth : 0; };
  return { panel: scroll('.tab-panel'), chat: scroll('#chatScroll') };
})()`);
check("and the conversation does not scroll sideways", wide.panel <= 0 && wide.chat <= 0, JSON.stringify(wide));

/* ================================ a desktop ============================== */
/* The same panel with a mouse: what the phone gained must not have cost the
   pointer anything. */
await send("Emulation.setTouchEmulationEnabled", { enabled: false });
await send("Emulation.setEmitTouchEventsForMouse", { enabled: false });
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await sleep(600);
const desk = await evaluate(`(async () => {
  document.querySelector('li[data-id] .session-item')?.click();
  await new Promise(r => setTimeout(r, 1400));
  const item = document.querySelector('li[data-id] .session-item');
  const r = item.getBoundingClientRect();
  const at = { clientX: Math.round(r.left + 40), clientY: Math.round(r.top + 20),
               pointerId: 1, pointerType: 'mouse', isPrimary: true, bubbles: true, cancelable: true };
  item.dispatchEvent(new PointerEvent('pointerdown', at));
  await new Promise(r => setTimeout(r, 800));
  const held = document.getElementById('sessionMenu').dataset.open;
  item.dispatchEvent(new PointerEvent('pointerup', at));
  const msg = document.querySelector('#chatScroll .msg');
  const b = msg.getBoundingClientRect();
  msg.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
    clientX: Math.round(b.left + 20), clientY: Math.round(b.top + 10) }));
  const menu = document.getElementById('sessionMenu');
  const out = { held, right: menu.dataset.open, title: menu.querySelector('.menu__title')?.textContent,
                panes: getComputedStyle(document.querySelector('.list-pane')).display,
                back: getComputedStyle(document.getElementById('backButton')).display,
                header: getComputedStyle(document.querySelector('.detail-header__top')).flexDirection,
                fold: getComputedStyle(document.querySelector('.fold-button')).display };
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  return out;
})()`);
check("holding a mouse button down opens nothing — a mouse has the button and the right-click",
  desk.held === "false", JSON.stringify(desk));
check("right-clicking a message still opens its menu", desk.right === "true" && !!desk.title, desk.title);
check("both panes are back", desk.panes !== "none");
check("and the back button is gone with them", desk.back === "none");
check("the header puts its actions beside the name again", desk.header === "row");
check("and the fold handle is gone: a header beside the conversation costs nothing",
  desk.fold === "none", desk.fold);

/* ============================== the stylesheet ============================ */
/* The two rules the browser here cannot be asked about: headless Chrome always
   claims `hover: none`, so the button is always out. */
const styles = readdirSync(join(here, "..", "web", "styles"))
  .map((name) => readFileSync(join(here, "..", "web", "styles", name), "utf8")).join("\n");
check("the actions button is hidden until a pointer is on the turn",
  /\.msg__more \{[^}]*opacity: 0;/.test(styles));
const noHover = (styles.match(/@media \(hover: none\) \{\s*\.msg__more \{ opacity: ([^;]+);/) || [])[1];
check("and never hidden where there is no pointer to bring it out",
  !!noHover && Number(noHover) > 0, `opacity ${noHover}`);
check("the way back is matched through the app, not through the panes",
  styles.includes('.app:has(.panes[data-view="detail"]) .back-button'));
check("the shell is measured in dvh as well as vh", /height: 100vh; height: 100dvh/.test(styles));
check("and the chrome keeps out of the insets", styles.includes("env(safe-area-inset-bottom)"));

console.log();
console.log("console errors:", consoleErrors);
console.log(failures ? `${failures} failed` : "all ok");
process.exit(failures ? 1 : 0);

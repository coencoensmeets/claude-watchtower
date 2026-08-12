// UI checks for the panel: MD3 tokens, contrast, the list-detail panes, the
// conversation view, the settings dialog and touch targets.
// Node 24+ only, no dependencies.
//
//   1. start a panel (a fixture directory shows every state at once):
//        CLAUDE_WATCHTOWER_SESSION_DIR=/path/to/fixtures python3 server.py --port 8788
//   2. start a throwaway browser with CDP open:
//        google-chrome --headless=new --remote-debugging-port=9333 \
//          --user-data-dir=$(mktemp -d) about:blank
//   3. node tests/ui-check.mjs
//
// Override PANEL_URL / CDP_URL to point elsewhere.

const PANEL = process.env.PANEL_URL || "http://127.0.0.1:8788";
const CDP = process.env.CDP_URL || "http://127.0.0.1:9333";
let failures = 0;

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
  if (res.result?.exceptionDetails) throw new Error(String(res.result.exceptionDetails.text));
  return res.result.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (name, pass, detail = "") => {
  if (!pass) failures += 1;
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

// Contrast helpers, injected once and reused.
const CONTRAST_HELPERS = String.raw`
  window.__lum = (hex) => { const n = parseInt(hex.slice(1),16);
    const f = (c) => { c/=255; return c<=0.03928?c/12.92:Math.pow((c+0.055)/1.055,2.4); };
    return 0.2126*f((n>>16)&255)+0.7152*f((n>>8)&255)+0.0722*f(n&255); };
  window.__hex = (rgb) => { const m = rgb.match(/\d+/g); return '#'+m.slice(0,3).map(v=>(+v).toString(16).padStart(2,'0')).join(''); };
  window.__ratio = (a,b) => { const l1=window.__lum(a), l2=window.__lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };
  window.__bgOf = (el) => { let node = el;
    while (node && node !== document.documentElement) {
      const bg = getComputedStyle(node).backgroundColor;
      if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return window.__hex(bg);
      node = node.parentElement; }
    return window.__hex(getComputedStyle(document.body).backgroundColor); };
  true`;

await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
// Start from a clean slate so the run does not depend on a previous one.
await send("Page.navigate", { url: `${PANEL}/` });
await sleep(2500);
await evaluate(`localStorage.clear()`);
await send("Page.navigate", { url: `${PANEL}/?theme=light` });
await sleep(4000);
await evaluate(CONTRAST_HELPERS);

/* -------------------------------------------------------------------- theme */
const tokens = await evaluate(`(() => {
  const s = getComputedStyle(document.documentElement);
  const names = ['--md-sys-color-primary','--md-sys-color-surface','--md-sys-color-on-surface',
    '--md-sys-color-surface-container-highest','--md-sys-color-outline-variant','--md-sys-color-scrim',
    '--md-extended-color-waiting-container','--md-extended-color-idle-container'];
  return JSON.stringify(Object.fromEntries(names.map(n => [n, s.getPropertyValue(n).trim()])));
})()`);
const parsedTokens = JSON.parse(tokens);
check("all md-sys / md-extended tokens resolve",
  Object.values(parsedTokens).every((v) => /^#[0-9a-f]{6}$/i.test(v)), `${Object.keys(parsedTokens).length} tokens`);
check("first paint is not blocked", await evaluate(`document.documentElement.dataset.schemeReady === 'true'`));
check("Roboto actually loaded", await evaluate(`document.fonts.check('16px Roboto')`));

const distinct = await evaluate(`(() => {
  const s = getComputedStyle(document.documentElement);
  const hexes = ['waiting','idle'].map(n => s.getPropertyValue('--md-extended-color-'+n+'-container').trim())
    .concat([s.getPropertyValue('--md-sys-color-primary-container').trim()]);
  return JSON.stringify({hexes, unique: new Set(hexes).size});
})()`);
check("three state containers are distinct", JSON.parse(distinct).unique === 3, JSON.parse(distinct).hexes.join(" "));

/* ------------------------------------------------------------- list + detail */
const listed = await evaluate(`JSON.stringify({
  items: document.querySelectorAll('.session-item').length,
  icons: document.querySelectorAll('.session-item__avatar svg').length,
  lamps: document.querySelectorAll('.session-item__lamp').length,
  states: [...document.querySelectorAll('.session-item')].map(i => i.dataset.status),
  selected: document.querySelectorAll('.session-item[aria-current="true"]').length,
})`);
const list = JSON.parse(listed);
check("sidebar lists every session", list.items >= 4, `${list.items} items`);
check("each item has a host icon and a state lamp",
  list.icons === list.items && list.lamps === list.items, `${list.icons} icons, ${list.lamps} lamps`);
check("sidebar shows distinct states", new Set(list.states).size >= 3, list.states.join(","));
check("exactly one item is selected", list.selected === 1);
check("detail pane rendered for the selection", await evaluate(`!!document.querySelector('.detail-header h2')`),
  await evaluate(`document.querySelector('.detail-header h2')?.textContent || ''`));

const firstName = await evaluate(`document.querySelector('.detail-header h2').textContent`);
await evaluate(`[...document.querySelectorAll('.session-item')].find(i => i.getAttribute('aria-current') !== 'true').click()`);
await sleep(1200);
const secondName = await evaluate(`document.querySelector('.detail-header h2').textContent`);
check("clicking a session opens its detail", firstName !== secondName, `${firstName} -> ${secondName}`);
check("selection is remembered", await evaluate(`!!localStorage.getItem('cbu-selected')`));

/* ---------------------------------------------------------------- grouping */
/* Sessions sharing a folder group themselves, and rows you pick can be grouped
   by hand. Both need at least two sessions in one folder to show at all, so
   these say so and skip rather than failing on fixtures that have none. */
const shape = () => evaluate(`JSON.stringify([...sessionList.children].map((li) => li.dataset.group
  ? { group: li.dataset.group, name: li.querySelector('.group__name')?.textContent,
      collapsed: li.dataset.collapsed, count: li.querySelectorAll('li[data-id]').length }
  : { row: li.querySelector('.session-item__headline')?.textContent.trim() }))`);
const picked = () => evaluate(`document.querySelectorAll('.session-item[data-picked="true"]').length`);
const blocks = JSON.parse(await shape());
const folderGroups = blocks.filter((b) => b.group?.startsWith("folder:"));
if (!folderGroups.length) {
  console.log("SKIP  grouping — no two fixture sessions share a folder");
} else {
  check("sessions in one folder are grouped under its name",
    folderGroups.every((g) => g.count >= 2 && g.name), folderGroups.map((g) => `${g.name}:${g.count}`).join(" "));
  check("a folder with one session stays a bare row",
    blocks.filter((b) => b.row).length + folderGroups.reduce((n, g) => n + g.count, 0) === list.items);

  await evaluate(`sessionList.querySelector('li[data-group^="folder:"] > .group__header').click()`);
  await sleep(300);
  check("clicking a group header folds it",
    JSON.parse(await shape()).find((b) => b.group?.startsWith("folder:"))?.collapsed === "true");
  check("the fold is remembered", await evaluate(`!!localStorage.getItem('cbu-collapsed-folders')`));
  await evaluate(`sessionList.querySelector('li[data-group^="folder:"] > .group__header').click()`);
  await sleep(300);

  // Ctrl-click two rows, then group them by hand.
  await evaluate(`(() => { const rows = [...sessionList.querySelectorAll('.session-item')].slice(0, 2);
    for (const row of rows) row.dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }));
    return true; })()`);
  await sleep(300);
  check("ctrl-click picks rows without opening them", await picked() === 2, `${await picked()} picked`);
  check("picking shows the selection bar", await evaluate(`!pickBar.hidden && !pickGroup.disabled`));
  await evaluate(`pickGroup.click()`);
  await sleep(400);
  const made = JSON.parse(await shape()).find((b) => b.group?.startsWith("custom:"));
  check("Group puts the picked rows in a group of their own", made?.count === 2, JSON.stringify(made));
  check("the bar clears once they are grouped", await evaluate(`pickBar.hidden`) && await picked() === 0);
  check("a group you made is kept", await evaluate(`(JSON.parse(localStorage.getItem('cbu-groups') || '[]')).length === 1`));

  // And it can be taken apart again from the header's menu.
  await evaluate(`(() => {
    const header = sessionList.querySelector('li[data-group^="custom:"] > .group__header');
    const box = header.getBoundingClientRect();
    header.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: box.left + 8, clientY: box.top + 8 }));
    return true; })()`);
  await sleep(300);
  check("a group header has a menu of its own", await evaluate(`sessionMenu.dataset.open === "true"`));
  await evaluate(`[...sessionMenu.querySelectorAll('.menu__item')].find((b) => b.textContent.includes('Ungroup')).click()`);
  await sleep(400);
  check("ungroup gives the rows back",
    !JSON.parse(await shape()).some((b) => b.group?.startsWith("custom:"))
    && await evaluate(`document.querySelectorAll('.session-item').length`) === list.items);
}

/* ------------------------------------------------------------ state trace */
// A settled session: its pane will not rebuild under us mid-check.
await evaluate(`document.querySelector('.session-item[data-status="idle"]')?.click()`);
await sleep(1200);
const traceGeom = JSON.parse(await evaluate(`JSON.stringify([...document.querySelectorAll('.trace__span')].map(s => {
  const r = s.getBoundingClientRect();
  return { state: s.dataset.state, from: s.dataset.from, x: r.x + r.width / 2, y: r.y + r.height / 2 };
}))`));
check("the trace draws its spans", traceGeom.length >= 1, `${traceGeom.length} spans`);
check("no two neighbouring spans show the same state",
  traceGeom.every((s, i) => i === 0 || s.state !== traceGeom[i - 1].state), traceGeom.map((s) => s.state).join(","));

if (traceGeom.length) {
  const spot = traceGeom[Math.floor(traceGeom.length / 2)];
  const hoverTip = async () => JSON.parse(await evaluate(`(() => { const t = document.querySelector('.trace__tip');
    return JSON.stringify({ open: t.dataset.open, text: t.textContent.replace(/\\s+/g, ' ').trim(),
      hot: document.querySelectorAll('.trace__span--hot').length }); })()`));
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: spot.x, y: spot.y, pointerType: "mouse" });
  await sleep(300);
  const tip = await hoverTip();
  check("hovering a span names the state and its clock times",
    tip.open === "1" && tip.hot === 1 && /\d.*→.*(\d|now)/.test(tip.text), tip.text);

  // The trace moves every second. The tooltip must not blink out under a
  // pointer that never moved.
  const marked = await evaluate(`(() => { const s = document.querySelector('.trace__span'); s.dataset.mark = '1'; return true; })()`);
  await sleep(3200);
  const held = await hoverTip();
  // The live slice counts up, so the text may differ — what must not change is
  // that the tip is up, on the same slice, describing the same start.
  const startOf = (t) => t.text.split("→")[0];
  check("the tooltip survives the polls under a still pointer",
    held.open === "1" && held.hot === 1 && startOf(held).slice(-12) === startOf(tip).slice(-12), held.text);
  check("the bar is repainted in place, not rebuilt",
    marked && await evaluate(`document.querySelector('.trace__span').dataset.mark === '1'`));

  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 4, y: 4, pointerType: "mouse" });
  await sleep(300);
  const gone = await hoverTip();
  check("leaving the bar closes the tooltip", gone.open === "0" && gone.hot === 0);
}

/* -------------------------------------------------------------------- tabs */
check("conversation tab is present", await evaluate(`!!document.querySelector('[data-tab="chat"]')`));
check("conversation panel renders", await evaluate(`!!document.querySelector('.chat, .chat__note')`));
await evaluate(`document.querySelector('[data-tab="about"]').click()`);
await sleep(800);
const about = JSON.parse(await evaluate(`JSON.stringify({
  selected: document.querySelector('[data-tab="about"]').getAttribute('aria-selected'),
  facts: document.querySelectorAll('.facts dt').length,
  window: !!document.querySelector('[data-act="pair"], [data-act="unpair"]'),
  mute: !!document.getElementById('muteToggle'),
})`));
check("details tab switches and shows facts", about.selected === "true" && about.facts >= 6, `${about.facts} facts`);
check("details tab exposes window pairing and notifications", about.window && about.mute);
await evaluate(`document.querySelector('[data-tab="chat"]').click()`);
await sleep(800);

/* --------------------------------------------------------------------- git */
/* The Git tab only exists for a session whose folder is in a repository, so
   these look for one and say so plainly when the fixtures have none rather than
   failing for a reason that is nothing to do with the panel. */
const gitSession = await evaluate(`(async () => {
  const state = await (await fetch('/api/state', { cache: 'no-store' })).json();
  return (state.sessions.find((s) => s.repoRoot) || {}).sessionId || "";
})()`);
if (!gitSession) {
  console.log("SKIP  git tab — no fixture session sits in a repository");
} else {
  await evaluate(`document.querySelector('[data-id="' + CSS.escape(${JSON.stringify(gitSession)}) + '"] .session-item').click()`);
  await sleep(900);
  const hasTab = await evaluate(`!!document.querySelector('[data-tab="git"]') && !!document.querySelector('[data-tab="history"]')`);
  check("git and history tabs appear for a session inside a repository", hasTab);
  if (hasTab) {
    /* --- Git: the working tree --- */
    await evaluate(`document.querySelector('[data-tab="git"]').click()`);
    await sleep(2200);
    const git = JSON.parse(await evaluate(`JSON.stringify({
      selected: document.querySelector('[data-tab="git"]').getAttribute('aria-selected'),
      branch: (document.querySelector('.git-badge')?.textContent || '').trim(),
      files: document.querySelectorAll('.git-file').length,
      marked: [...document.querySelectorAll('.git-file')].every((f) => (f.querySelector('.git-file__xy')?.textContent || '').length === 2),
      // The graph belongs to the other tab now; finding it here means the split
      // did not take.
      graph: document.querySelectorAll('.git-commit').length,
    })`));
    check("git tab switches and reads the repository", git.selected === "true" && !!git.branch, git.branch);
    check("every changed file shows two status letters", git.marked, `${git.files} files`);
    check("the git tab carries no history", git.graph === 0);

    /* --- History: the graph --- */
    await evaluate(`document.querySelector('[data-tab="history"]').click()`);
    await sleep(2200);
    const hist = JSON.parse(await evaluate(`JSON.stringify({
      selected: document.querySelector('[data-tab="history"]').getAttribute('aria-selected'),
      branch: (document.querySelector('.git-badge')?.textContent || '').trim(),
      commits: document.querySelectorAll('.git-commit').length,
      rails: document.querySelectorAll('.git-commit__rail circle').length,
      // The rail is drawn to the same height as the row it belongs to; a
      // mismatch is what leaves the lanes broken at every join.
      aligned: (() => {
        const row = document.querySelector('.git-commit');
        const rail = row?.querySelector('.git-commit__rail');
        return row && rail ? Math.abs(row.getBoundingClientRect().height - rail.getBoundingClientRect().height) <= 1 : false;
      })(),
      files: document.querySelectorAll('.git-file').length,
    })`));
    check("history tab switches and keeps the branch header",
      hist.selected === "true" && !!hist.branch, hist.branch);
    check("history draws a commit per row, each with its own node",
      hist.commits >= 1 && hist.commits === hist.rails, `${hist.commits} commits`);
    check("the graph rail is the same height as its row", hist.aligned);
    check("the history tab carries no file list", hist.files === 0);

    const gitContrast = JSON.parse(await evaluate(`JSON.stringify((() => {
      const out = [];
      const measure = (label, el) => { if (!el) return;
        out.push({ label, ratio: +window.__ratio(window.__hex(getComputedStyle(el).color), window.__bgOf(el)).toFixed(2) }); };
      measure('git branch badge', document.querySelector('.git-badge'));
      measure('commit subject', document.querySelector('.git-commit__subject'));
      measure('commit meta', document.querySelector('.git-commit__meta'));
      return out;
    })())`));
    const gitWorst = gitContrast.length ? Math.min(...gitContrast.map((r) => r.ratio)) : 0;
    check("history tab text clears 4.5:1", gitContrast.length >= 3 && gitWorst >= 4.5,
      `worst ${gitWorst}:1 over ${gitContrast.length} spots`);

    // Back to Git for the file-row contrast, which only exists there.
    await evaluate(`document.querySelector('[data-tab="git"]').click()`);
    await sleep(1600);
    const fileContrast = JSON.parse(await evaluate(`JSON.stringify((() => {
      const out = [];
      const measure = (label, el) => { if (!el) return;
        out.push({ label, ratio: +window.__ratio(window.__hex(getComputedStyle(el).color), window.__bgOf(el)).toFixed(2) }); };
      measure('file status', document.querySelector('.git-file__xy'));
      measure('file path', document.querySelector('.git-file__path'));
      return out;
    })())`));
    const fileWorst = fileContrast.length ? Math.min(...fileContrast.map((r) => r.ratio)) : 99;
    check("git file rows clear 4.5:1", fileWorst >= 4.5, `worst ${fileWorst}:1`);

    // Every tab has to stay reachable once there are four of them.
    const reach = JSON.parse(await evaluate(`JSON.stringify((() => {
      const strip = document.querySelector('.tabs');
      const tabs = [...document.querySelectorAll('.tab')];
      return {
        count: tabs.length,
        scrollable: strip.scrollWidth <= strip.clientWidth || getComputedStyle(strip).overflowX === 'auto',
        tall: tabs.every((t) => t.getBoundingClientRect().height >= 44),
      };
    })())`));
    check("all four tabs stay reachable and 48dp tall",
      reach.count === 4 && reach.scrollable && reach.tall, `${reach.count} tabs`);
  }
  await evaluate(`document.querySelector('[data-tab="chat"]')?.click()`);
  await sleep(800);
}

/* ---------------------------------------------- contrast of what is on screen */
const rows = JSON.parse(await evaluate(`(() => {
  const out = [];
  const measure = (label, el) => { if (!el) return;
    const cs = getComputedStyle(el);
    out.push({ label, ratio: +window.__ratio(window.__hex(cs.color), window.__bgOf(el)).toFixed(2) }); };
  measure('detail header', document.querySelector('.detail-header'));
  measure('detail title', document.querySelector('.detail-header h2'));
  measure('selected item', document.querySelector('.session-item[aria-current="true"] .session-item__headline'));
  measure('item supporting', document.querySelector('.session-item__supporting'));
  // A row ticked for grouping, and the header of the group it would go into.
  const row = document.querySelector('.session-item');
  if (row) { row.dataset.picked = 'true';
    measure('picked item', row.querySelector('.session-item__headline'));
    delete row.dataset.picked; }
  measure('group header', document.querySelector('.group__header'));
  measure('chip', document.querySelector('.chip'));
  measure('activity row', document.querySelector('.activity-row'));
  const button = document.querySelector('.detail-header .button');
  if (button) out.push({ label: 'header button',
    ratio: +window.__ratio(window.__hex(getComputedStyle(button).color), window.__hex(getComputedStyle(button).backgroundColor)).toFixed(2) });
  for (const cls of ['.msg--user', '.msg--assistant']) measure(cls, document.querySelector(cls + ' .msg__text'));
  return JSON.stringify(out);
})()`));
const worst = Math.min(...rows.map((r) => r.ratio));
check("everything on screen clears 4.5:1", rows.length >= 5 && worst >= 4.5, `worst ${worst}:1 over ${rows.length} spots`);
console.log("      " + rows.map((r) => `${r.label} ${r.ratio}`).join("  "));

/* ------------------------------------------------------------ filter chips */
const beforeFilter = await evaluate(`document.querySelectorAll('.session-item').length`);
await evaluate(`[...document.querySelectorAll('.chip')].find(c => c.textContent.includes('waiting')).click()`);
await sleep(800);
const afterFilter = await evaluate(`document.querySelectorAll('.session-item').length`);
check("filter chip narrows the list", afterFilter >= 1 && afterFilter < beforeFilter, `${beforeFilter} -> ${afterFilter}`);
await evaluate(`[...document.querySelectorAll('.chip')].find(c => c.textContent.includes('all')).click()`);
await sleep(600);

/* ------------------------------------------------- settings / dynamic colour */
await evaluate(`document.getElementById('settingsButton').click()`);
await sleep(700);
check("settings dialog opens", await evaluate(`document.getElementById('settingsScrim').dataset.open === 'true'`));
check("dialog has swatches and a contrast group", await evaluate(
  `document.querySelectorAll('#swatches .swatch').length >= 6 && document.querySelectorAll('#contrastGroup .segmented__item').length === 3`));

const primaryBefore = await evaluate(`getComputedStyle(document.documentElement).getPropertyValue('--md-sys-color-primary').trim()`);
await evaluate(`[...document.querySelectorAll('#swatches .swatch')][2].click()`);
await sleep(700);
const primaryAfter = await evaluate(`getComputedStyle(document.documentElement).getPropertyValue('--md-sys-color-primary').trim()`);
check("changing the base colour regenerates the scheme", primaryBefore !== primaryAfter, `${primaryBefore} -> ${primaryAfter}`);
check("base colour persists", await evaluate(`localStorage.getItem('cbu-seed') === '#00A18F'`));
check("states stay distinct after the seed change", await evaluate(`(() => {
  const s = getComputedStyle(document.documentElement);
  const hexes = ['waiting','idle'].map(n => s.getPropertyValue('--md-extended-color-'+n+'-container').trim())
    .concat([s.getPropertyValue('--md-sys-color-primary-container').trim()]);
  return new Set(hexes).size === 3; })()`));

const onSurfaceBefore = await evaluate(`getComputedStyle(document.documentElement).getPropertyValue('--md-sys-color-on-surface').trim()`);
await evaluate(`[...document.querySelectorAll('#contrastGroup .segmented__item')][2].click()`);
await sleep(600);
check("high contrast changes the scheme", onSurfaceBefore !==
  await evaluate(`getComputedStyle(document.documentElement).getPropertyValue('--md-sys-color-on-surface').trim()`));

await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await sleep(600);
check("Escape closes the dialog", await evaluate(`document.getElementById('settingsScrim').dataset.open === 'false'`));

await evaluate(`document.getElementById('themeToggle').click()`);
await sleep(700);
check("theme switch flips the scheme", await evaluate(`localStorage.getItem('cbu-theme') === 'dark'`));

/* ---------------------------------------------------------- touch targets */
const smallTargets = JSON.parse(await evaluate(`(() => {
  const small = [];
  for (const el of document.querySelectorAll('.chip, .icon-button, .button, .swatch, .session-item, .tab')) {
    if (el.closest('.scrim[data-open="false"]')) continue;   // scaled down while closed
    const r = el.getBoundingClientRect();
    if (!r.height) continue;
    let h = r.height;
    const after = getComputedStyle(el, '::after');
    if (after.content !== 'none') {
      const top = parseFloat(after.top) || 0;
      if (top < 0) h += Math.abs(top) * 2;
    }
    if (h < 44) small.push(el.className.split(' ')[0] + ':' + Math.round(h));
  }
  return JSON.stringify([...new Set(small)]);
})()`));
check("interactive targets reach ~48dp", smallTargets.length === 0, smallTargets.join(",") || "all ok");

check("only the tab panel scrolls", await evaluate(
  `getComputedStyle(document.querySelector('.detail-pane')).overflow === 'hidden'
   && getComputedStyle(document.querySelector('.tab-panel')).overflowY === 'auto'`));

check("no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));

ws.close();
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exitCode = failures ? 1 : 0;

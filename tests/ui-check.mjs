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
/* The chips are drawn from the states actually present, so against a real panel
   there may be no "waiting" chip to click. Say so and move on — reaching for one
   that is not there took the whole run down with it. */
const beforeFilter = await evaluate(`document.querySelectorAll('.session-item').length`);
const filterChip = await evaluate(
  `!!([...document.querySelectorAll('.chip')].find(c => c.textContent.includes('waiting')))`);
if (!filterChip) {
  console.log("SKIP  filter chips — no session is waiting right now");
} else {
  await evaluate(`[...document.querySelectorAll('.chip')].find(c => c.textContent.includes('waiting')).click()`);
  await sleep(800);
  const afterFilter = await evaluate(`document.querySelectorAll('.session-item').length`);
  check("filter chip narrows the list", afterFilter >= 1 && afterFilter < beforeFilter, `${beforeFilter} -> ${afterFilter}`);
  await evaluate(`[...document.querySelectorAll('.chip')].find(c => c.textContent.includes('all'))?.click()`);
  await sleep(600);
}

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

/* --------------------------------------------------- commenting on a passage */
/* Select a passage and the panel offers Copy and Comment; commenting opens a
   card in the margin against that passage, the way a document does it. The cards
   are gathered into one attributed quote-and-remark message, so what reaches the
   session is the same thing the composer used to send.

   Fixture sessions have no transcript, so this needs a real one and says so
   plainly when there is none. Everything here selects a run of text that is
   genuinely on screen: the panel deliberately offers nothing for a passage
   scrolled out of the transcript's own box. */
const chatSession = await evaluate(`(async () => {
  const state = await (await fetch('/api/state', { cache: 'no-store' })).json();
  // A busy session rewrites its transcript underneath the run, and the pane
  // rebuild takes the selection with it. Prefer a quiet one.
  const quietFirst = [...state.sessions].sort((a, b) =>
    (a.status === 'busy' ? 1 : 0) - (b.status === 'busy' ? 1 : 0));
  for (const s of quietFirst) {
    const t = await (await fetch('/api/transcript?sessionId=' + encodeURIComponent(s.sessionId) + '&limit=40',
      { cache: 'no-store' })).json();
    if ((t.messages || []).filter((m) => m.text && m.text.length > 40).length >= 2) return s.sessionId;
  }
  return "";
})()`);
if (!chatSession) {
  console.log("SKIP  commenting — no session has a readable transcript (point PANEL_URL at a real panel)");
} else {
  await evaluate(`document.querySelector('[data-id="' + CSS.escape(${JSON.stringify(chatSession)}) + '"] .session-item').click()`);
  await sleep(1200);
  await evaluate(`document.querySelector('[data-tab="chat"]')?.click()`);
  await sleep(1200);
  // Wide enough for the margin rail; the narrow behaviour is checked separately.
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(700);

  /* Nothing here may actually message a live session, so /api/say and /api/start
     are intercepted and their bodies kept. That is also how the wire format is
     asserted: what the panel would have sent, without sending it. */
  await evaluate(`(() => {
    window.__sent = [];
    if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
    window.fetch = (url, opts) => {
      const u = String(url);
      if ((u.includes('/api/say') || u.includes('/api/start')) && opts && opts.method === 'POST') {
        window.__sent.push(JSON.parse(opts.body));
        return Promise.resolve(new Response(JSON.stringify({ ok: true, message: 'Sent (intercepted)' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return window.__realFetch(url, opts);
    };
    return true;
  })()`);

  const SELECT = (nth) => `(() => {
    const scroller = document.getElementById('chatScroll');
    if (!scroller) return null;
    // What a mousedown in the transcript does first. It matters: with the caret
    // in a field, getSelection() reports that field rather than the document.
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const box = scroller.getBoundingClientRect();
    const found = [];
    for (const body of document.querySelectorAll('#chatScroll .msg__text')) {
      if (body.textContent.trim().length < 40) continue;
      const walk = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walk.nextNode())) {
        if (node.textContent.trim().length < 25) continue;
        const r = document.createRange();
        r.setStart(node, 0); r.setEnd(node, Math.min(node.textContent.length, 60));
        const rect = r.getBoundingClientRect();
        if (!rect.width || rect.top < box.top || rect.bottom > box.bottom) continue;
        found.push(r);
      }
    }
    const avoid = ${nth};
    const range = found.find((r) => r.toString() !== avoid);
    if (!range) return JSON.stringify({ text: "", runs: found.length });
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    return JSON.stringify({ text: sel.toString(), runs: found.length });
  })()`;

  /* Sweep the transcript rather than nudging blindly: the view opens pinned to
     the newest message, and a viewport holding only a table or a tool row has
     nothing that qualifies however long you wait. Says which way it failed,
     since "nothing to select" and "selected, but no bar" are different answers. */
  let lastMiss = "";
  const selectAndOffer = async (avoid = "") => {
    const nth = JSON.stringify(avoid);
    lastMiss = "";
    let seen = 0;
    for (let pass = 0; pass < 2; pass++) {
      for (let stop = 0; stop <= 6; stop++) {
        await evaluate(`(() => { const s = document.getElementById('chatScroll');
          if (s) s.scrollTop = (s.scrollHeight - s.clientHeight) * ${stop / 6}; })()`);
        await sleep(250);
        const got = JSON.parse((await evaluate(SELECT(nth))) || '{"text":"","runs":0}');
        seen = Math.max(seen, got.runs);
        if (!got.text) continue;
        await sleep(400);
        if (await evaluate(`document.getElementById('quoteChip')?.hidden === false`)) return got.text;
        lastMiss = "selected, but no bar";
      }
    }
    if (lastMiss !== "selected, but no bar") {
      lastMiss = seen ? `${seen} runs on screen, all already used` : "no run on screen to select";
    }
    return null;
  };
  const clearComments = () => evaluate(`(() => {
    for (const b of document.querySelectorAll('.ccard [data-cc="drop"]')) b.click();
    return true; })()`);

  check("bubbles carry who and when, for a quote's attribution", await evaluate(
    `(() => { const m = document.querySelector('#chatScroll .msg'); return !!m?.dataset.who && !!m?.dataset.at; })()`));

  const firstPick = await selectAndOffer();
  check("selecting a passage offers a bar", !!firstPick,
    firstPick ? JSON.stringify(firstPick.slice(0, 40)) : lastMiss);

  if (firstPick) {
    const bar = JSON.parse(await evaluate(`(() => { const c = document.getElementById('quoteChip');
      const r = c.getBoundingClientRect();
      return JSON.stringify({ acts: [...c.querySelectorAll('[data-sel]')].map((b) => b.dataset.sel),
        label: c.textContent.replace(/\\s+/g, ' ').trim(),
        onScreen: r.top >= 0 && r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight }); })()`));
    check("the bar offers Copy and Comment", bar.acts.join(",") === "copy,comment" && /Copy/.test(bar.label) && /Comment/.test(bar.label),
      JSON.stringify(bar));
    check("the bar is fully on screen", bar.onScreen);

    /* Copy. The clipboard is not readable in a headless context without a
       permission grant, so this asserts the call was made with the passage
       rather than reading it back. */
    await evaluate(`(() => { window.__copied = null;
      if (navigator.clipboard) navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };
      return true; })()`);
    await evaluate(`document.querySelector('#quoteChip [data-sel="copy"]').click()`);
    await sleep(400);
    const copied = await evaluate(`window.__copied`);
    check("Copy puts the passage on the clipboard", typeof copied === "string" && copied.length > 10,
      JSON.stringify((copied || "").slice(0, 40)));
    check("Copy puts the bar away", await evaluate(`document.getElementById('quoteChip').hidden`) === true);

    // Comment.
    const second = await selectAndOffer();
    if (!second) {
      console.log(`SKIP  commenting — could not re-select after Copy (${lastMiss})`);
    } else {
      await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
      await sleep(600);
      const card = JSON.parse(await evaluate(`(() => {
        const c = document.querySelector('.ccard');
        if (!c) return JSON.stringify({ none: true });
        const f = c.querySelector('.ccard__field');
        return JSON.stringify({ quote: (c.querySelector('.ccard__quote')?.textContent || '').trim().slice(0, 40),
          hasField: !!f, focused: document.activeElement === f,
          railShown: !document.getElementById('commentRail').hidden,
          mode: document.getElementById('commentRail').dataset.mode,
          padded: document.querySelector('.panel-wrap').dataset.rail }); })()`));
      check("Comment opens a card against the passage", !card.none && card.hasField, JSON.stringify(card));
      check("the card carries the passage it is about", (card.quote || "").length > 5, JSON.stringify(card.quote));
      check("the card takes the caret", card.focused === true);
      check("the card sits in the conversation, under its message",
        await evaluate(`(() => { const c = document.querySelector('.ccard');
          return !!c && !!c.closest('#chatScroll'); })()`));
      check("the card is indented, so it does not read as another turn",
        await evaluate(`(() => { const c = document.querySelector('.ccard');
          return c ? parseFloat(getComputedStyle(c).marginLeft) >= 24 : false; })()`),
        await evaluate(`(() => { const c = document.querySelector('.ccard');
          return c ? getComputedStyle(c).marginLeft : 'none'; })()`));
      check("the passage is marked in the transcript",
        await evaluate(`document.querySelectorAll('#chatScroll mark.commented').length`) >= 1);
      check("the card sits level with its passage", await evaluate(`(() => {
        const mark = document.querySelector('#chatScroll mark.commented');
        const card = document.querySelector('.ccard');
        if (!mark || !card) return false;
        // Both are measured in the viewport, so a card level with its passage
        // lands within a bubble's height of it.
        return Math.abs(mark.getBoundingClientRect().top - card.getBoundingClientRect().top) < 200; })()`));

      // Nothing is sendable until a remark is written.
      check("nothing is sendable until something is written",
        await evaluate(`document.querySelector('.rail__send')?.disabled === true`),
        await evaluate(`document.querySelector('.rail__send')?.textContent || ''`));

      await evaluate(`(() => { const f = document.querySelector('.ccard[data-active="true"] .ccard__field')
          || document.querySelector('.ccard__field');
        f.value = 'make this configurable instead';
        f.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
      await sleep(300);
      check("writing a remark arms the send button", await evaluate(
        `(() => { const b = document.querySelector('.rail__send');
          return !b.disabled && /Send 1 comment$/.test(b.textContent.trim()); })()`),
        await evaluate(`document.querySelector('.rail__send')?.textContent || ''`));

      /* The pane must not be rebuilt out from under a card being typed in — the
         same guard a half-typed name and a composer drag already get. The panel
         polls every second, so sitting through several of them is the real test;
         renderDetail itself is module-scoped and cannot be called from here. */
      await sleep(3200);
      check("a card being typed in survives the polls", await evaluate(
        `(() => { const f = document.querySelector('.ccard__field');
          return !!f && f.value === 'make this configurable instead'; })()`),
        await evaluate(`document.querySelector('.ccard__field')?.value ?? 'gone'`));

      // A second passage becomes a second card rather than replacing the first.
      const third = await selectAndOffer(second);
      if (!third) {
        console.log(`SKIP  a second card — no other passage to select (${lastMiss})`);
      } else {
        await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
        await sleep(500);
        check("a second passage opens a second card",
          await evaluate(`document.querySelectorAll('.ccard').length`) >= 2,
          `${await evaluate(`document.querySelectorAll('.ccard').length`)} cards`);
        check("cards do not overlap each other", await evaluate(`(() => {
          const cards = [...document.querySelectorAll('.ccard')]
            .map((c) => c.getBoundingClientRect()).sort((a, b) => a.top - b.top);
          for (let i = 1; i < cards.length; i++) if (cards[i].top < cards[i - 1].bottom - 1) return false;
          return true; })()`));
        // The newest card, not the last in the DOM: cards are ordered by where
        // their message sits in the conversation, not by when they were made.
        await evaluate(`(() => { const f = document.querySelector('.ccard[data-active="true"] .ccard__field');
          if (!f) return false;
          f.value = 'and this one too'; f.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
        await sleep(300);
      }

      /* What would go over the wire. Sending is intercepted, so this reads the
         message the panel built without a live session ever seeing it. */
      await evaluate(`(() => { window.__sent = []; return true; })()`);
      await evaluate(`document.querySelector('.rail__send').click()`);
      await sleep(700);
      const sent = JSON.parse(await evaluate(`JSON.stringify(window.__sent)`));
      check("sending delivers one message for all the comments", sent.length === 1, `${sent.length} messages`);
      /* And to the session on screen. The click handler used to be re-attached
         on every repaint, each copy holding the session selected when it was
         bound, so a send could land on a session that was not even open. The
         count check could not see it: the in-flight guard let only the first
         stale handler through, so exactly one message went to the wrong place. */
      check("it goes to the session being looked at",
        sent.length === 1 && sent[0].sessionId === chatSession,
        sent.length ? `sent to ${sent[0].sessionId.slice(0, 8)}, looking at ${chatSession.slice(0, 8)}` : "not sent");
      if (sent.length) {
        const text = sent[0].text;
        check("each comment goes as an attributed quote with its remark below",
          /^> \[[^\],]+, [^\]]+\]$/m.test(text) && /make this configurable instead/.test(text),
          JSON.stringify(text.split("\n").slice(0, 2).join(" / ")));
        check("the quote is written from the reader's point of view",
          /^> \[(you|me|[^\]]+), /m.test(text) && !/^> \[claude, /m.test(text),
          text.split("\n").find((l) => l.startsWith("> [")) || "");
        check("every quoted line is prefixed and the remark is not",
          text.split("\n").filter((l) => l.trim()).some((l) => !l.startsWith(">")) &&
          text.split("\n").filter((l) => l.startsWith(">")).length >= 2);
      }
      check("sent comments leave the rail", await evaluate(
        `document.querySelectorAll('.ccard').length`) === 0,
        `${await evaluate(`document.querySelectorAll('.ccard').length`)} left`);
      check("their marks stay, so you can see where you have been",
        await evaluate(`document.querySelectorAll('#chatScroll mark.commented').length`) >= 1);
    }
  }

  /* Narrow windows need no special case any more: the card is in the flow of the
     conversation, so it reflows with it rather than needing a margin to live in. */
  const forNarrow = await selectAndOffer();
  if (forNarrow) {
    await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
    await sleep(500);
    await send("Emulation.setDeviceMetricsOverride", { width: 820, height: 900, deviceScaleFactor: 1, mobile: false });
    await sleep(900);
    const narrow = JSON.parse(await evaluate(`(() => {
      const card = document.querySelector('.ccard');
      if (!card) return JSON.stringify({ none: true });
      const r = card.getBoundingClientRect();
      return JSON.stringify({ inFlow: !!card.closest('#chatScroll'),
        onScreen: r.left >= 0 && r.right <= innerWidth + 1 && r.width > 100 }); })()`));
    check("the card stays in the conversation when the window narrows",
      narrow.inFlow === true && narrow.onScreen === true, JSON.stringify(narrow));
    await clearComments();
    await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await sleep(600);
  } else {
    console.log(`SKIP  narrow layout — nothing to select (${lastMiss})`);
  }

  /* A selection running across several messages becomes one quote each rather
     than being refused for carrying one attribution over two speakers. */
  const spanning = await evaluate(`(() => {
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const bodies = [...document.querySelectorAll('#chatScroll .msg__text')].filter((b) => b.textContent.trim().length > 40);
    if (bodies.length < 2) return null;
    const r = document.createRange();
    r.setStart(bodies[0], 0); r.setEnd(bodies[1], bodies[1].childNodes.length);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    return sel.toString().length;
  })()`);
  await sleep(500);
  if (spanning === null) console.log("SKIP  cross-bubble selection — only one bubble in the transcript");
  else if (await evaluate(`document.getElementById('quoteChip')?.hidden !== false`)) {
    console.log("SKIP  cross-bubble selection — the bubbles are not on screen");
  } else {
    await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
    await sleep(600);
    check("a selection across bubbles becomes one card each",
      await evaluate(`document.querySelectorAll('.ccard').length`) >= 2,
      `${await evaluate(`document.querySelectorAll('.ccard').length`)} cards`);
    await clearComments();
    await sleep(300);
  }

  /* A passage out of a code block goes back fenced. Quoted as prose its
     indentation is flattened, which is the part worth quoting. */
  // Whether a transcript has a fenced block at all varies run to run, so this
  // goes looking for a session that has one rather than skipping by luck.
  if (!(await evaluate(`document.querySelectorAll('#chatScroll pre.md-code').length`))) {
    const withCode = await evaluate(`(async () => {
      const state = await (await fetch('/api/state', { cache: 'no-store' })).json();
      for (const s of state.sessions) {
        const t = await (await fetch('/api/transcript?sessionId=' + encodeURIComponent(s.sessionId) + '&limit=40',
          { cache: 'no-store' })).json();
        if ((t.messages || []).some((m) => m.text && m.text.includes('\\n\`\`\`'))) return s.sessionId;
      }
      return "";
    })()`);
    if (withCode) {
      await evaluate(`document.querySelector('[data-id="' + CSS.escape(${JSON.stringify(withCode)}) + '"] .session-item')?.click()`);
      await sleep(1800);
    }
  }
  const codeSel = await evaluate(`(() => {
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const pre = [...document.querySelectorAll('#chatScroll pre.md-code')].find((p) => p.textContent.trim().length > 20);
    if (!pre) return null;
    pre.scrollIntoView({ block: 'center' });
    const code = pre.querySelector('code') || pre;
    const r = document.createRange(); r.selectNodeContents(code);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    return sel.toString().slice(0, 30);
  })()`);
  await sleep(500);
  // The transcript is live, so a rebuild between selecting and taking the offer
  // can move the selection off the code block. Assert only when it is still the
  // code that is selected — otherwise this would grade whatever it caught.
  const codeHeld = codeSel !== null
    && (await evaluate(`window.getSelection().toString().slice(0, 30)`)) === codeSel;
  if (codeSel === null) console.log("SKIP  code quoting — no code block in this transcript");
  else if (!codeHeld) console.log("SKIP  code quoting — the selection moved before it could be taken");
  else if (await evaluate(`document.getElementById('quoteChip')?.hidden !== false`)) {
    console.log("SKIP  code quoting — the code block is not on screen");
  } else {
    await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
    await sleep(500);
    await evaluate(`(() => { const f = document.querySelector('.ccard__field');
      if (!f) return false; f.value = 'x'; f.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
    await evaluate(`(() => { window.__sent = []; return true; })()`);
    await evaluate(`document.querySelector('.rail__send')?.click()`);
    await sleep(700);
    const sent = JSON.parse(await evaluate(`JSON.stringify(window.__sent)`));
    check("a code passage goes back fenced, not flattened",
      sent.length === 1 && (sent[0].text.match(/^> ```/gm) || []).length === 2,
      sent.length ? JSON.stringify(sent[0].text.split("\n").slice(0, 3).join(" / ")) : "not sent");
    await clearComments();
  }

  /* A tool row is the only place a command the session ran is written down, so
     "this command was wrong" needs it to be quotable. */
  const toolSel = await evaluate(`(() => {
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const row = [...document.querySelectorAll('#chatScroll .activity-row__tools')].find((t) => t.textContent.trim().length > 20);
    if (!row) return null;
    row.scrollIntoView({ block: 'center' });
    const r = document.createRange(); r.selectNodeContents(row);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    return sel.toString().slice(0, 40);
  })()`);
  await sleep(500);
  if (toolSel === null) console.log("SKIP  tool-row quoting — no tool rows in this transcript");
  else check("a tool row can be commented on too",
    await evaluate(`document.getElementById('quoteChip')?.hidden === false`), JSON.stringify(toolSel));

  /* A passage crossing inline markup cannot be wrapped in a mark, and anchoring
     to marks alone put exactly those cards at the top of the rail instead of
     beside anything. This is that case. */
  const spanMarkup = await evaluate(`(() => {
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const scroller = document.getElementById('chatScroll');
    const box = scroller.getBoundingClientRect();
    for (const body of document.querySelectorAll('#chatScroll .msg__text')) {
      const inline = body.querySelector('code, strong, em, a');
      if (!inline) continue;
      const before = inline.previousSibling, after = inline.nextSibling;
      if (!before || before.nodeType !== 3 || !after || after.nodeType !== 3) continue;
      if (before.data.trim().length < 8 || after.data.trim().length < 8) continue;
      const r = document.createRange();
      r.setStart(before, Math.max(0, before.data.length - 8));
      r.setEnd(after, Math.min(after.data.length, 8));
      const rect = r.getBoundingClientRect();
      if (!rect.width || rect.top < box.top || rect.bottom > box.bottom) continue;
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
      return sel.toString().slice(0, 40);
    }
    return null;
  })()`);
  await sleep(500);
  if (spanMarkup === null) console.log("SKIP  markup-spanning passage — none on screen to select");
  else if (await evaluate(`document.getElementById('quoteChip')?.hidden !== false`)) {
    console.log("SKIP  markup-spanning passage — no bar offered");
  } else {
    await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
    await sleep(600);
    const anchored = await evaluate(`(() => {
      const card = document.querySelector('.ccard');
      const scroller = document.getElementById('chatScroll');
      if (!card) return null;
      const top = Math.round(card.getBoundingClientRect().top);
      const box = scroller.getBoundingClientRect();
      // Anchored means somewhere in the transcript's own band, not pinned to the
      // very top of the rail, which is where an unanchored card lands.
      return JSON.stringify({ top, boxTop: Math.round(box.top),
        pinnedToTop: Math.abs(top - box.top) < 2 });
    })()`);
    if (!anchored) check("a passage crossing markup still anchors its card", false, "no card");
    else {
      const a = JSON.parse(anchored);
      check("a passage crossing markup still anchors its card", !a.pinnedToTop,
        `card top ${a.top}, transcript top ${a.boxTop}`);
      // And it is underlined. A range straddling an element boundary cannot be
      // wrapped in one go, so this is marked piece by piece — without that, the
      // passages most worth commenting on were the ones left unmarked.
      const spanMarks = await evaluate(`(() => {
        const ms = [...document.querySelectorAll('#chatScroll mark.commented')];
        return JSON.stringify({ n: ms.length,
          underlined: ms.every((m) => /inset/.test(getComputedStyle(m).boxShadow)) }); })()`);
      const sm = JSON.parse(spanMarks);
      check("a passage crossing markup is underlined too", sm.n >= 1 && sm.underlined,
        `${sm.n} marks, underlined ${sm.underlined}`);
    }
    await clearComments();
    await sleep(300);
  }

  /* A phrase that appears in more than one turn must attach to the turn it was
     selected in. Matching by words alone put the card on whichever turn said it
     first, which is usually not the one being read. */
  const dupe = await evaluate(`(() => {
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const scroller = document.getElementById('chatScroll');
    const box = scroller.getBoundingClientRect();
    const msgs = [...document.querySelectorAll('#chatScroll .msg')];
    // A run of text that occurs in an earlier turn as well as a later one.
    for (let i = msgs.length - 1; i > 0; i--) {
      const body = msgs[i].querySelector('.msg__text');
      if (!body) continue;
      const walk = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walk.nextNode())) {
        const words = node.data.trim();
        if (words.length < 14) continue;
        const run = words.slice(0, Math.min(30, words.length));
        const earlier = msgs.slice(0, i).some((m) => m.textContent.includes(run));
        if (!earlier) continue;
        const at = node.data.indexOf(run);
        const r = document.createRange();
        r.setStart(node, at); r.setEnd(node, at + run.length);
        const rect = r.getBoundingClientRect();
        if (!rect.width || rect.top < box.top || rect.bottom > box.bottom) continue;
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        return JSON.stringify({ run: run.slice(0, 30), key: msgs[i].dataset.key });
      }
    }
    return null;
  })()`);
  await sleep(500);
  if (dupe === null) console.log("SKIP  repeated phrase — no phrase appears in two turns on screen");
  else if (await evaluate(`document.getElementById('quoteChip')?.hidden !== false`)) {
    console.log("SKIP  repeated phrase — the passage is not on screen");
  } else {
    const want = JSON.parse(dupe);
    await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
    await sleep(600);
    const landed = await evaluate(`(() => {
      const card = document.querySelector('.ccard');
      const owner = card && card.previousElementSibling;
      return owner ? (owner.dataset.key || '') : null; })()`);
    check("a repeated phrase comments on the turn it was selected in",
      landed === want.key, `landed on ${JSON.stringify((landed || '').slice(0, 40))}`);
    await clearComments();
    await sleep(300);
  }

  /* Deleting a comment has to take its underline with it. The marks are already
     in the page by then, so they come out by hand — and a passage another
     comment still claims, or one whose comment was sent, has to keep its own. */
  await clearComments();
  await sleep(400);
  const beforeDelete = await selectAndOffer();
  if (!beforeDelete) console.log(`SKIP  deleting a comment — nothing to select (${lastMiss})`);
  else {
    await evaluate(`document.querySelector('#quoteChip [data-sel="comment"]').click()`);
    await sleep(600);
    const marked = await evaluate(`document.querySelectorAll('#chatScroll mark.commented').length`);
    check("commenting underlines the passage", marked >= 1, `${marked} marks`);
    await evaluate(`document.querySelector('.ccard [data-cc="drop"]')?.click()`);
    await sleep(600);
    const after = JSON.parse(await evaluate(`JSON.stringify({
      marks: document.querySelectorAll('#chatScroll mark.commented').length,
      cards: document.querySelectorAll('.ccard').length })`));
    // Fewer, not none: a comment that was already sent keeps its underline on
    // purpose, so only the deleted one's marks come away.
    check("deleting the comment takes its underline with it",
      after.marks < marked && after.cards === 0,
      `${marked} -> ${after.marks} marks, ${after.cards} cards left`);
    // And the text is put back whole, not left in fragments by the unwrapping.
    check("the passage is left whole where the mark was",
      await evaluate(`(() => {
        const body = document.querySelector('#chatScroll .msg__text');
        if (!body) return true;
        return !body.querySelector('mark.commented'); })()`));
  }

  /* Alt+C takes the offer without reaching for the mouse. */
  if (await selectAndOffer()) {
    await evaluate(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', altKey: true, bubbles: true, cancelable: true }))`);
    await sleep(500);
    check("Alt+C opens a card from the keyboard",
      await evaluate(`document.querySelectorAll('.ccard').length`) >= 1);
    await clearComments();
  }

  /* The highlight has to be visible on both kinds of bubble, and the mark over a
     commented passage must not compete with it. Your messages are drawn in
     primary-container, so a highlight in that same role vanished on exactly the
     ones you most want to quote back — a check reading one bubble misses it. */
  if (await selectAndOffer()) {
    const highlight = await evaluate(`(() => {
      const one = (cls) => {
        const b = document.querySelector('#chatScroll .msg--' + cls);
        const body = b && b.querySelector('.msg__text');
        if (!body) return null;
        const sel = getComputedStyle(body, '::selection');
        return { bubble: getComputedStyle(b).backgroundColor, bg: sel.backgroundColor, fg: sel.color };
      };
      return JSON.stringify({ user: one('user'), assistant: one('assistant') });
    })()`);
    const hl = JSON.parse(highlight);
    const asHex = (rgb) => { const m = (rgb || "").match(/\d+/g); return m ? "#" + m.slice(0, 3).map((v) => (+v).toString(16).padStart(2, "0")).join("") : ""; };
    const lum = (hex) => { const n = parseInt(hex.slice(1), 16);
      const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
      return 0.2126 * f((n >> 16) & 255) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255); };
    const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
    const kinds = ["user", "assistant"].filter((k) => hl[k]);
    const standOff = kinds.map((k) => ({ k, r: +ratio(asHex(hl[k].bg), asHex(hl[k].bubble)).toFixed(2) }));
    check("the highlight stands off the bubble it sits on, for both kinds",
      standOff.length === 2 && standOff.every((a) => a.r >= 3),
      standOff.map((a) => `${a.k} ${a.r}:1`).join("  ") + (standOff.length < 2 ? " (only one kind on screen)" : ""));
    check("selected text stays readable on the highlight",
      kinds.every((k) => ratio(asHex(hl[k].fg), asHex(hl[k].bg)) >= 4.5),
      kinds.map((k) => `${k} ${ratio(asHex(hl[k].fg), asHex(hl[k].bg)).toFixed(2)}:1`).join("  "));

    await evaluate(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
    await sleep(300);
    check("Escape puts the bar away", await evaluate(`document.getElementById('quoteChip')?.hidden`) === true);
  }

  const markStyle = await evaluate(`(() => {
    const m = document.querySelector('#chatScroll mark.commented');
    if (!m) return null;
    const cs = getComputedStyle(m);
    return JSON.stringify({ bg: cs.backgroundColor, shadow: cs.boxShadow }); })()`);
  if (markStyle) {
    const ms = JSON.parse(markStyle);
    check("the mark does not compete with the selection highlight",
      /rgba\(0, 0, 0, 0\)|transparent/.test(ms.bg) && /inset/.test(ms.shadow), ms.bg);
  }

  // Put the page back the way the rest of the run expects it.
  await clearComments();
  await evaluate(`(() => { if (window.__realFetch) window.fetch = window.__realFetch;
    window.getSelection().removeAllRanges(); return true; })()`);
  await send("Emulation.clearDeviceMetricsOverride");
  await sleep(600);
}
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

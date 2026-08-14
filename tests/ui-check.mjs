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

/* The order the list is kept in. States decide the bands, and inside a band it
   is when the session started and then its id — never how long it has been in
   that state, which would move a row every time it blinked. */
const ordering = JSON.parse(await evaluate(`(async () => {
  const feed = await (await fetch('/api/state')).json();
  const rank = ['waiting','busy','shell','idle','offline','stopped'];
  const at = Object.fromEntries(feed.sessions.map((s) => [s.sessionId, s]));
  const rowsOf = (el) => [...(el.dataset.id ? [el] : el.querySelectorAll('li[data-id]'))]
    .map((li) => at[li.dataset.id]).filter(Boolean);
  // A group sits at the band of its most pressing member.
  const blocks = [...sessionList.children].map((li) => rowsOf(li)).filter((rows) => rows.length);
  const bands = blocks.map((rows) => Math.min(...rows.map((s) => rank.indexOf(s.status))));
  const identity = (list) => list.every((s, i) => !i
    || (list[i - 1].startedAt || 0) < (s.startedAt || 0)
    || ((list[i - 1].startedAt || 0) === (s.startedAt || 0) && list[i - 1].sessionId < s.sessionId));
  // Bare rows are only ever compared with the ones sharing their band.
  const bare = blocks.filter((rows) => rows.length === 1).map((rows) => rows[0]);
  const perBand = [...new Set(bare.map((s) => s.status))].map((st) => bare.filter((s) => s.status === st));
  return JSON.stringify({
    blocks: blocks.length,
    banded: bands.every((b, i) => !i || bands[i - 1] <= b),
    groups: blocks.filter((rows) => rows.length > 1).length,
    fixed: blocks.filter((rows) => rows.length > 1).every(identity) && perBand.every(identity),
  });
})()`));
check("the list runs in state order, worst first",
  ordering.banded, `${ordering.blocks} rows and groups`);
check("rows sit in a fixed order of their own inside that",
  ordering.fixed, `${ordering.groups} groups`);
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

/* The Usage tab totals the transcript, so a fixture with no recorded model
   request has nothing to add up. Either way the tab must be there and must say
   which of the two it is rather than drawing an empty page. */
await evaluate(`document.querySelector('[data-tab="usage"]').click()`);
await sleep(1200);
const use = JSON.parse(await evaluate(`JSON.stringify({
  selected: document.querySelector('[data-tab="usage"]').getAttribute('aria-selected'),
  tiles: document.querySelectorAll('.use-tile').length,
  cost: document.querySelector('.use-tile--lead .use-tile__value')?.textContent.trim() || "",
  rows: document.querySelectorAll('.use-table tbody tr').length,
  note: document.querySelector('.git-empty')?.textContent.trim().slice(0, 40) || "",
  wide: document.documentElement.scrollWidth > document.documentElement.clientWidth,
})`));
check("usage tab switches", use.selected === "true");
if (use.tiles) {
  check("usage shows a cost and a row per model", /^\$/.test(use.cost) && use.rows >= 1,
    `${use.cost}, ${use.rows} models`);
  check("the usage tab does not push the page sideways", !use.wide);
} else {
  check("usage says plainly when there is nothing to total", !!use.note, use.note);
}

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
      // One letter per row, for the side of the file that row stands for.
      marked: [...document.querySelectorAll('.git-file')].every((f) => (f.querySelector('.git-file__xy')?.textContent || '').length === 1),
      // Every row names a file and opens it.
      named: [...document.querySelectorAll('.git-file')].every((f) =>
        f.dataset.path && f.querySelector('.git-file__name')?.textContent.trim()
        && f.querySelector('[data-git="diff"]')),
      // The groups the editor uses, in its order.
      groups: [...document.querySelectorAll('.scm-group')].map((s) => s.dataset.group).join(','),
      // The graph belongs to the other tab now; finding it here means the split
      // did not take.
      graph: document.querySelectorAll('.git-commit').length,
    })`));
    check("git tab switches and reads the repository", git.selected === "true" && !!git.branch, git.branch);
    check("every changed file shows its status letter and opens", git.marked && git.named, `${git.files} files`);
    check("files are grouped the way the editor groups them",
      git.groups.split(',').filter(Boolean).every((g) => ["merge", "staged", "changes"].includes(g)),
      git.groups || "clean tree");
    check("the git tab carries no history", git.graph === 0);

    /* --- Source control: the controls, and what they are wired to ---

       Nothing here presses stage or commit. This suite runs against whatever
       real sessions are on the machine, and a test that commits in somebody's
       checkout to prove a button works has done more than it was asked. What is
       checked is that the controls are there when writing is on, gone when it is
       off, and that the one read-only action — opening a diff — actually reads. */
    const scm = JSON.parse(await evaluate(`(async () => {
      const canWrite = (await (await fetch('/api/git?sessionId=' + encodeURIComponent(${JSON.stringify(gitSession)}), { cache: 'no-store' })).json()).canWrite;
      const row = document.querySelector('.git-file');
      return JSON.stringify({
        canWrite,
        commitField: Boolean(document.querySelector('#commitField')),
        splitButton: Boolean(document.querySelector("[data-git='commit']") && document.querySelector("[data-git='commit-menu']")),
        // The sparkle has to sit inside the field it fills in. It is only ever
        // measured here — pressing it would spend tokens to test a layout.
        sparkleInside: (() => {
          const f = document.querySelector('#commitField');
          const s = document.querySelector("[data-git='suggest']");
          if (!f || !s) return false;
          const a = f.getBoundingClientRect(), b = s.getBoundingClientRect();
          return b.left >= a.left && b.right <= a.right + 1 && b.top >= a.top
            && parseFloat(getComputedStyle(f).paddingRight) >= b.width;
        })(),
        headButtons: [...document.querySelectorAll('.git-head [data-git]')].map((b) => b.dataset.git).join(','),
        // A drift count is a button when there is drift to act on, and says which
        // upstream it means.
        driftActs: [...document.querySelectorAll('.git-head [data-git="push"], .git-head [data-git="pull"]')]
          .every((b) => /^(Push|Pull) \\d+ commits? (to|from) /.test(b.title)),
        // And it has to look like work waiting: a filled pill, saying what to do
        // about it, not another transparent line of description.
        driftFilled: [...document.querySelectorAll('.git-badge--drift')].every((b) => {
          const bg = getComputedStyle(b).backgroundColor;
          return bg && bg !== 'rgba(0, 0, 0, 0)' && !bg.endsWith(', 0)')
            && /to (push|pull)/.test(b.textContent);
        }),
        driftCount: document.querySelectorAll('.git-badge--drift').length,
        readOnlyBadge: Boolean([...document.querySelectorAll('.git-head span')].some((s) => s.textContent.trim() === 'read-only')),
        groupActions: [...document.querySelectorAll('.scm-group__head [data-git]')].map((b) => b.dataset.git).join(','),
        rowActions: row ? [...row.querySelectorAll(".scm-actions [data-git]")].map((b) => b.dataset.git).join(',') : "",
        hasRow: Boolean(row),
      });
    })()`));
    if (scm.canWrite) {
      check("the commit box and its split button are there when writing is on",
        scm.commitField && scm.splitButton);
      check("the sparkle sits inside the message box, with room kept for it",
        scm.sparkleInside);
      check("any drift count on show is a button that says what it would do",
        scm.driftActs, scm.headButtons);
      check("a drift count is a filled pill, not a quiet annotation",
        scm.driftFilled, scm.driftCount ? `${scm.driftCount} on show` : "none to show");
      check("the header offers sync and an overflow",
        scm.headButtons.includes("sync") && scm.headButtons.includes("menu"), scm.headButtons);

      /* The branch badge opens the branch list. It is opened and read, never
         picked from: switching a branch under a real session to prove a menu
         works is not this suite's business. */
      const branches = JSON.parse(await evaluate(`(async () => {
        const reading = await (await fetch('/api/git?sessionId=' + encodeURIComponent(${JSON.stringify(gitSession)}), { cache: 'no-store' })).json();
        document.querySelector("[data-git='branch-menu']").click();
        await new Promise((r) => setTimeout(r, 250));
        const items = [...document.querySelectorAll('#sessionMenu .menu__item')];
        const answer = {
          open: document.getElementById('sessionMenu').dataset.open,
          creates: items.filter((b) => b.dataset.key === 'new' || b.dataset.key === 'new-from').length,
          listed: items.filter((b) => b.dataset.key?.startsWith('local:')).length,
          current: items.filter((b) => b.dataset.key?.startsWith('local:') && b.disabled).length,
          fromGit: (reading.branches?.local ?? []).length,
          onABranch: Boolean(reading.branch),
          // A remote's default-branch pointer is not a branch. refs/remotes/origin/HEAD
          // shortens to plain "origin" -- no backticks in here, this is inside a
          // template literal -- so it once landed in the local list and was offered
          // as somewhere to switch to.
          noRemotePointers: (reading.branches?.remote ?? [])
            .map((b) => b.name.split('/')[0])
            .every((remote) => !(reading.branches?.local ?? []).some((b) => b.name === remote)),
        };
        // Put it away, so the checks after this one are not measuring a menu.
        // Dispatched on an element, not on document: listeners here reasonably
        // expect a pointer event to have an Element for a target.
        document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
        return JSON.stringify(answer);
      })()`));
      check("the branch badge opens the branch list",
        branches.open === "true" && branches.creates === 2
        && branches.listed === branches.fromGit,
        `${branches.listed} branches, ${branches.creates} ways to make one`);
      // Detached HEAD has no current branch to mark, which is not a failure.
      check("the branch you are on is marked, not offered",
        branches.current === (branches.onABranch ? 1 : 0));
      check("no remote's default-branch pointer is listed as a branch",
        branches.noRemotePointers);
      if (scm.hasRow) {
        check("a file row carries stage or unstage",
          /(^|,)(stage|unstage)(,|$)/.test(scm.rowActions), scm.rowActions);
        check("a group header carries its own actions", scm.groupActions.length > 0, scm.groupActions);
      } else {
        console.log("SKIP  row and group actions — the tree is clean");
      }
    } else {
      check("a read-only panel says so and offers no actions",
        scm.readOnlyBadge && !scm.commitField && scm.headButtons === "");
      check("a read-only panel offers no sparkle either",
        await evaluate(`!document.querySelector("[data-git='suggest']")`));
    }

    // Clicking a row opens that file's diff underneath it, and only that one.
    if (scm.hasRow) {
      await evaluate(`document.querySelector('.git-file [data-git="diff"]').click()`);
      await sleep(1400);
      const diff = JSON.parse(await evaluate(`JSON.stringify({
        open: document.querySelectorAll('.git-file[data-open="1"]').length,
        // Either real lines, or the one line saying why there are none.
        lines: document.querySelectorAll('.scm-diff__line').length,
        note: document.querySelector('.scm-diff') ? "" : (document.querySelector('.git-empty')?.textContent.trim() || "nothing at all"),
      })`));
      check("a row opens one diff, and only its own", diff.open === 1, `${diff.open} open`);
      check("the diff reads something back", diff.lines > 0 || diff.note.length > 0,
        diff.lines ? `${diff.lines} lines` : diff.note);
      // Put it away again, so the checks below measure the list and not a patch.
      await evaluate(`document.querySelector('.git-file[data-open="1"] [data-git="diff"]').click()`);
      await sleep(700);
      check("clicking the same row again closes it",
        await evaluate(`document.querySelectorAll('.git-file[data-open="1"]').length === 0`));
    }

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
      measure('file name', document.querySelector('.git-file__name'));
      measure('file folder', document.querySelector('.git-file__dir'));
      measure('group title', document.querySelector('.scm-group__title'));
      measure('drift count', document.querySelector('.git-badge--drift'));
      measure('drift verb', document.querySelector('.git-badge__verb'));
      measure('row action', document.querySelector('.scm-actions .scm-icon'));
      return out;
    })())`));
    const fileWorst = fileContrast.length ? Math.min(...fileContrast.map((r) => r.ratio)) : 99;
    check("git file rows clear 4.5:1", fileWorst >= 4.5,
      `worst ${fileWorst}:1 over ${fileContrast.length} spots`);

    // Every tab has to stay reachable once there are five of them.
    const reach = JSON.parse(await evaluate(`JSON.stringify((() => {
      const strip = document.querySelector('.tabs');
      const tabs = [...document.querySelectorAll('.tab')];
      return {
        count: tabs.length,
        scrollable: strip.scrollWidth <= strip.clientWidth || getComputedStyle(strip).overflowX === 'auto',
        tall: tabs.every((t) => t.getBoundingClientRect().height >= 44),
      };
    })())`));
    check("all five tabs stay reachable and 48dp tall",
      reach.count === 5 && reach.scrollable && reach.tall, `${reach.count} tabs`);
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

/* ---------------------------------------------------------------- the plan */
/* The plan chip reads /usage through the server, which takes a few seconds and
   only answers a loopback panel at all. So this waits for it, and says which of
   the three it got rather than failing for whichever one it is. */
const planState = JSON.parse(await evaluate(`(async () => {
  const answer = await fetch('/api/plan', { cache: 'no-store' });
  const body = answer.status === 200 ? await answer.json() : null;
  return JSON.stringify({ status: answer.status, ok: !!body?.ok });
})()`));
if (planState.status === 403) {
  console.log("SKIP  plan chip — this panel is read-only, so it does not read your plan");
} else if (!planState.ok) {
  console.log("SKIP  plan chip — /usage did not answer on this machine");
} else {
  // The chip appears once the reading lands; the page asks for it on boot.
  for (let i = 0; i < 20 && await evaluate(`planButton.hidden`); i++) await sleep(1000);
  const chip = JSON.parse(await evaluate(`JSON.stringify({
    hidden: planButton.hidden,
    text: planChipText.textContent,
    tight: planButton.dataset.tight,
    title: planButton.title,
  })`));
  check("the plan chip shows how much has gone", !chip.hidden && /%/.test(chip.text), `${chip.text}`);
  check("the chip names its limits in full", /used/.test(chip.title), chip.title);
  // Green, amber, red — each figure in the band its own number falls in.
  const bands = JSON.parse(await evaluate(`JSON.stringify(
    [...planChipText.querySelectorAll('.plan-pct')].map((s) => ({
      text: s.textContent, band: s.dataset.band, color: window.__hex(getComputedStyle(s).color) }))
  )`));
  check("each figure is coloured for its band",
    bands.length >= 1 && bands.every((b) => /^[012]$/.test(b.band)) &&
      new Set(bands.map((b) => b.band)).size === new Set(bands.map((b) => b.color)).size,
    bands.map((b) => `${b.text} band ${b.band} ${b.color}`).join(", "));
  await evaluate(`planButton.click()`);
  await sleep(900);
  const dialog = JSON.parse(await evaluate(`JSON.stringify({
    open: planScrim.dataset.open,
    bars: document.querySelectorAll('#planBody .plan-limit').length,
    fills: [...document.querySelectorAll('#planBody .use-bar__fill')].map(f => f.style.width),
    age: document.getElementById('planAge').textContent,
    refresh: !!document.getElementById('planRefresh'),
  })`));
  check("the chip opens a dialog with one bar per limit",
    dialog.open === "true" && dialog.bars >= 1, `${dialog.bars} limits`);
  check("each bar is drawn to its own figure and says how old the reading is",
    dialog.fills.every((w) => /%$/.test(w)) && /read|reading/.test(dialog.age),
    `${dialog.fills.join(" ")} — ${dialog.age}`);
  check("the dialog offers a refresh", dialog.refresh);
  const planContrast = JSON.parse(await evaluate(`JSON.stringify((() => {
    const out = [];
    for (const el of document.querySelectorAll('#planHead, .plan-limit__pct, .plan-limit__resets, .plan-block li, .plan-note')) {
      const fg = window.__hex(getComputedStyle(el).color);
      out.push({ ratio: Math.round(window.__ratio(fg, window.__bgOf(el)) * 100) / 100 });
    }
    return out;
  })())`));
  const planWorst = planContrast.length ? Math.min(...planContrast.map((r) => r.ratio)) : 99;
  check("plan dialog text clears 4.5:1", planWorst >= 4.5,
    `worst ${planWorst}:1 over ${planContrast.length} spots`);
  await evaluate(`document.getElementById('closePlan').click()`);
  await sleep(500);
  check("the plan dialog closes", await evaluate(`planScrim.dataset.open === 'false'`));
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

/* ---------------------------------------------------------- touch targets */
const smallTargets = JSON.parse(await evaluate(`(() => {
  const small = [];
  for (const el of document.querySelectorAll('.chip, .icon-button, .button, .swatch, .session-item, .tab, .scm-icon')) {
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

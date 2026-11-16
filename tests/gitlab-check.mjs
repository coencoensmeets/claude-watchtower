// The GitLab tab and the work items page, in a real browser.
//
// Almost everything this covers is a state you cannot arrange on the machine
// running it: a repository whose remote is not on a GitLab, a host glab has no
// token for, a work item with no checkout to start a session in. So the panel
// is served its answers here — /api/forge and /api/forge/items are stubbed in
// the page before the tab is opened — and what is checked is what the panel
// draws from them, which is the part that has to be right whatever GitLab says.
//
// The one thing not stubbed is the tab strip: that it is there, that it is in
// the right place, and that it disappears with the repository.
//
//   1. start a panel (a fixture directory is fine):
//        CLAUDE_WATCHTOWER_SESSION_DIR=/path/to/fixtures python3 server.py --port 8788
//   2. start a throwaway browser with CDP open:
//        google-chrome --headless=new --remote-debugging-port=9333 \
//          --user-data-dir=$(mktemp -d) about:blank
//   3. node tests/gitlab-check.mjs
//
// Override PANEL_URL / CDP_URL to point elsewhere. A failure prints the case and
// exits 1.

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
  if (msg.method === "Runtime.exceptionThrown") consoleErrors.push(
    [msg.params.exceptionDetails.text,
     msg.params.exceptionDetails.exception?.description].filter(Boolean).join(" :: ").slice(0, 300));
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
/* Whitespace in the markup is the template's, not the interface's: every one of
   these strings is read out of a multi-line template literal. */
const tidy = (s) => String(s).replace(/\s+/g, " ").trim();
const check = (name, pass, detail = "") => {
  if (!pass) failures += 1;
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
await send("Page.navigate", { url: `${PANEL}/` });
await sleep(2500);
await evaluate("localStorage.clear()");
await send("Page.navigate", { url: `${PANEL}/` });
await sleep(4000);

/* Every GitLab answer the page will get, put in front of fetch. Written before
   anything is clicked, so nothing in this file ever reaches a real GitLab — and
   so the cases below are the ones worth drawing rather than the ones this
   machine happens to be in. */
const ITEMS = {
  ok: true, canWrite: true, canPick: true, trouble: [
    { host: "git.down.com", message: "Could not reach this GitLab (git.down.com)" },
  ],
  items: [
    // Labels as bare strings on purpose: GitLab only returns their colours from
    // the list endpoint with `with_labels_details`, and older ones not at all.
    // Reading a name off a string as if it were an object is what emptied this
    // whole page once, so the page fixture keeps the awkward shape.
    { host: "git.example.com", project: "group/proj", iid: 4, title: "Fix the flaky thing",
      state: "opened", labels: ["priority::high", "type::bug"], milestone: "1.2",
      updatedAt: new Date(Date.now() - 3600e3).toISOString(),
      webUrl: "https://git.example.com/group/proj/-/issues/4", comments: 2,
      reference: "group/proj#4", folder: "/home/someone/code/proj" },
    { host: "git.example.com", project: "group/other", iid: 9, title: "Write the docs",
      state: "opened", labels: [{ name: "priority::low", color: "#61a8ff", text_color: "#fff" }],
      milestone: "",
      updatedAt: new Date(Date.now() - 86400e3).toISOString(),
      webUrl: "https://git.example.com/group/other/-/issues/9", comments: 0,
      reference: "group/other#9", folder: "" },
  ],
};

await evaluate(`(() => {
  window.__asked = [];
  const real = window.fetch;
  window.fetch = (input, init) => {
    const url = String(input);
    window.__asked.push({ url, body: init?.body ? String(init.body) : "" });
    if (url.startsWith("/api/forge/items")) {
      return Promise.resolve(new Response(JSON.stringify(${JSON.stringify(ITEMS)}),
        { headers: { "Content-Type": "application/json" } }));
    }
    if (url.startsWith("/api/forge/start")) {
      return Promise.resolve(new Response(JSON.stringify({ ok: true, message: "Starting it up…" }),
        { headers: { "Content-Type": "application/json" } }));
    }
    if (url.startsWith("/api/forge?")) {
      return Promise.resolve(new Response(JSON.stringify(window.__gitlab || {}),
        { headers: { "Content-Type": "application/json" } }));
    }
    return real(input, init);
  };
  return true;
})()`);

/* ============================== the tab strip ============================= */

/* A session in a repository — the only kind with the tab at all. */
const picked = await evaluate(`(async () => {
  for (const head of document.querySelectorAll(".group__header[aria-expanded='false']")) head.click();
  await new Promise((r) => setTimeout(r, 500));
  for (const row of document.querySelectorAll(".session-item")) {
    row.click();
    await new Promise((r) => setTimeout(r, 700));
    if (document.querySelector('[data-tab="gitlab"]')) return true;
  }
  return false;
})()`);

if (!picked) {
  console.log("--  no session in a git repository in this panel: the tab cases are skipped");
} else {
  const order = await evaluate(
    `[...document.querySelectorAll('.tab')].map((t) => t.dataset.tab).join(',')`);
  check("the GitLab tab sits after History and before Usage",
    order === "chat,git,history,gitlab,usage,about", order);

  /* A repository on something that is not a GitLab. The commonest case on a
     real machine, and the one where a tab that just said "no items" would be
     actively misleading. */
  await evaluate(`(() => {
    window.__gitlab = { ok: false, isRepo: true, isProject: false, reason: "not-gitlab",
      project: { host: "github.com", path: "someone/thing", url: "https://github.com/someone/thing" },
      message: "This repository is on github.com, which glab is not set up for", canWrite: true };
    return true;
  })()`);
  await evaluate(`document.querySelector('[data-tab="gitlab"]').click()`);
  await sleep(1200);
  const notGitlab = await evaluate(`document.querySelector('.tab-panel').textContent`);
  check("a repository that is not on a GitLab says so, and names the host",
    notGitlab.includes("github.com") && notGitlab.includes("not set up for"),
    notGitlab.slice(0, 80));

  /* A host whose token has expired: the fix is the CLI's login, and it has to
     be a button on screen rather than a line in the documentation. */
  await evaluate(`(() => {
    window.__gitlab = { ok: false, isRepo: true, isProject: true, reason: "unauthorized",
      project: { host: "git.example.com", path: "group/proj", url: "https://git.example.com/group/proj" },
      message: "This GitLab turned the token down — glab needs logging in again", canWrite: true };
    return true;
  })()`);
  await evaluate(`document.querySelector('#gitlabRefresh')?.click()`);
  await sleep(1200);
  const stale = await evaluate(`document.querySelector('.tab-panel').textContent`);
  const loginButton = await evaluate(`(() => {
    const b = document.querySelector('[data-act="forge-login"]');
    return b ? b.dataset.forge + "@" + b.dataset.host : "";
  })()`);
  check("an expired token is reported with a button that logs glab in to that host",
    stale.includes("Log in to git.example.com") && loginButton === "gitlab@git.example.com",
    `${loginButton} ${stale.slice(0, 120)}`);

  /* And the ordinary case, with the content this tab is actually designed
     around: scoped labels, which are a field and a value rather than a word,
     and a priority, which is an order to work in rather than a label. */
  const LABEL = (name, colour, on) => ({ name, colour, textColor: on, color: colour });
  const ITEM = (iid, title, labels, mine, extra = {}) => ({
    host: "git.example.com", project: "group/proj", iid, title, state: "opened",
    labels: labels.map((l) => ({ name: l, colour: "#ff6961", onColour: "#FFFFFF" })),
    milestone: "", updatedAt: new Date(Date.now() - iid * 3600e3).toISOString(),
    webUrl: `https://git.example.com/group/proj/-/issues/${iid}`, comments: 0,
    reference: `group/proj#${iid}`, mine,
    assignees: mine ? [{ name: "Coen Smeets", username: "coen", initials: "CS" }] : [],
    author: { name: "Ann Blake", username: "ann", initials: "AB" }, ...extra,
  });

  await evaluate(`(() => {
    window.__gitlab = { ok: true, isRepo: true, isProject: true, canWrite: true, me: "coen",
      project: { host: "git.example.com", path: "group/proj", url: "https://git.example.com/group/proj" },
      pipeline: { status: "failed", webUrl: "https://git.example.com/p/1" },
      items: [
        ${JSON.stringify(ITEM(4, "Fix the flaky thing", ["priority::low", "type::bug"], true))},
        ${JSON.stringify(ITEM(7, "Rewrite the importer", ["priority::high", "type::feature"], true))},
        ${JSON.stringify(ITEM(9, "Somebody else's job", ["priority::medium"], false))}
      ],
      mrs: [{ iid: 12, title: "Draft: the fix", webUrl: "https://x", draft: true, target: "main" }] };
    return true;
  })()`);
  await evaluate(`document.querySelector('#gitlabRefresh')?.click()`);
  await sleep(1200);

  const list = JSON.parse(await evaluate(`JSON.stringify({
    rows: [...document.querySelectorAll('.gl-list--items .gl-row__num')].map((n) => n.textContent.trim()),
    parts: [...document.querySelectorAll('.gl-part')].map((p) => ({
      key: p.dataset.part, n: p.querySelector('.gl-part__n')?.textContent.trim(),
      rows: [...p.querySelectorAll('.gl-row__num')].map((n) => n.textContent.trim()) })),
    pipe: document.querySelector('.gl-pipe')?.dataset.tone || "",
    scoped: document.querySelector('.gl-label--scoped')?.textContent || "",
    keys: [...document.querySelectorAll('.gl-label__key')].map((k) => k.textContent),
    rails: [...document.querySelectorAll('.gl-row__rail')].map((r) => r.dataset.priority || ""),
  })`));

  /* One list: everything open, with what is assigned to you in its own part
     at the top, so a project where nothing is yours still shows its work. */
  check("everything open is listed, yours first", list.rows.join(",") === "#7,#4,#9",
    list.rows.join(","));
  check("in two parts that carry honest counts",
    list.parts.map((p) => `${p.key} ${p.n} ${p.rows.join(",")}`).join(" / ") === "mine 2 #7,#4 / rest 1 #9",
    JSON.stringify(list.parts));
  check("high priority sorts above low, whatever the dates say",
    list.rails.slice(0, 2).join(",") === "high,low", list.rails.join(","));
  check("a scoped label is drawn as its two halves",
    tidy(list.scoped).replace(/ /g, "") === "typefeature" && list.keys.includes("type"),
    `${tidy(list.scoped)} | ${list.keys}`);
  check("a failed pipeline is drawn as failed", list.pipe === "bad", list.pipe);

  /* The filter runs over the list without taking the field from under the caret. */
  await evaluate(`(() => {
    const f = document.querySelector('#glSearch');
    f.focus(); f.value = "importer";
    f.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);
  await sleep(600);
  const filtered = JSON.parse(await evaluate(`JSON.stringify({
    rows: document.querySelectorAll('.gl-list--items .gl-row').length,
    focused: document.activeElement?.id || "",
  })`));
  check("the filter narrows the list and keeps the caret",
    filtered.rows === 1 && filtered.focused === "glSearch", JSON.stringify(filtered));

  /* One item, in front of the list. Built here and handed over as JSON: a
     backslash written into one of these page expressions is eaten by this
     file's own template literal long before the browser sees it, so the item's
     markdown is assembled in Node where it means what it says. */
  const DETAIL = {
    ok: true,
    item: {
      ...ITEM(7, "Rewrite the importer", ["priority::high", "type::feature"], true),
      description: ["It drops rows silently.", "", "- one", "- two", "",
                    "`code` and **bold**."].join("\n"),
      notes: [{ author: "Ann Blake", at: new Date().toISOString(),
                body: "Still happening on main." }],
      createdAt: new Date(Date.now() - 86400e3).toISOString(),
    },
  };
  await evaluate(`(() => {
    window.__item = ${JSON.stringify(DETAIL)};
    if (window.__itemPatched) return true;
    window.__itemPatched = true;
    const real = window.fetch;
    window.fetch = (input, init) => {
      const url = String(input);
      // The query mark matters: /api/forge/items starts with /api/forge/item,
      // and a prefix match here quietly answers the list with one item.
      if (url.startsWith("/api/forge/item?")) {
        window.__asked.push({ url, body: "" });
        return Promise.resolve(new Response(JSON.stringify(window.__item),
          { headers: { "Content-Type": "application/json" } }));
      }
      return real(input, init);
    };
    return true;
  })()`);
  await evaluate(`document.querySelector('.gl-list--items [data-act="open-item"]').click()`);
  await sleep(1200);
  const detail = JSON.parse(await evaluate(`JSON.stringify({
    title: document.querySelector('.gl-detail__title')?.textContent || "",
    priority: document.querySelector('.gl-detail__head')?.dataset.priority || "",
    listGone: document.querySelectorAll('.gl-list--items .gl-row').length,
    markdown: Boolean(document.querySelector('.gl-body li') && document.querySelector('.gl-body code')),
    note: document.querySelector('.gl-note__body')?.textContent || "",
    acts: [...document.querySelectorAll('.gl-actions button')].map((b) => b.textContent.trim()),
  })`));
  check("opening an item stands it in front of the list",
    detail.title === "Rewrite the importer" && detail.listGone === 0, JSON.stringify(detail.title));
  check("its description is rendered as markdown, not printed as source", detail.markdown);
  check("its comments come with it", tidy(detail.note) === "Still happening on main.", detail.note);
  check("and it carries the actions worth taking on a work item",
    detail.acts.some((a) => a.includes("Start a session")) &&
    detail.acts.some((a) => a.includes("Branch for it")), detail.acts.join(" / "));
  check("the heading keeps the priority the row showed", detail.priority === "high", detail.priority);

  /* Escape is the way back, as it is everywhere else in the pane. */
  await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`);
  await sleep(700);
  check("Escape comes back to the list",
    await evaluate(`document.querySelectorAll('.gl-list--items .gl-row').length > 0
      && !document.querySelector('.gl-detail')`));

  /* Filing one, in the pane rather than in a dialog. */
  await evaluate(`document.querySelector('[data-act="compose"]').click()`);
  await sleep(600);
  check("New opens a compose card with the project's own labels on it",
    await evaluate(`Boolean(document.querySelector('#glCompose'))
      && [...document.querySelectorAll('[data-label]')].some((b) => b.dataset.label === "type::feature")`));
  check("and it will not file an untitled one",
    await evaluate(`document.querySelector('#glFile').disabled === true`));
  await evaluate(`(() => {
    const t = document.querySelector('#glTitle');
    t.value = "A new thing";
    t.dispatchEvent(new Event("input", { bubbles: true }));
    document.querySelector('[data-label="type::feature"]').click();
    return true;
  })()`);
  await sleep(400);
  check("a title enables it", await evaluate(`document.querySelector('#glFile').disabled === false`));
  await evaluate(`document.querySelector('[data-act="compose-cancel"]').click()`);
  await sleep(500);
  check("and Cancel puts it away", await evaluate(`!document.querySelector('#glCompose')`));
}

/* ============================ the work items page ========================= */

await evaluate(`document.querySelector('#itemsButton').click()`);
// The page asks on opening, so this waits for the answer as well as the paint.
await sleep(2500);

const pageText = await evaluate(`document.querySelector('.detail-pane').textContent`);
check("the page opens from the app bar", pageText.includes("Work items"), pageText.slice(0, 60));
check("and it asked for every item rather than one session's",
  await evaluate(`window.__asked.some((a) => a.url.startsWith("/api/forge/items"))`));

const shape = JSON.parse(await evaluate(`JSON.stringify({
  groups: [...document.querySelectorAll('.wi-group__title')].map((g) => g.textContent),
  rows: document.querySelectorAll('.gl-row--launch').length,
  missing: Boolean(document.querySelector('.wi-group__missing')),
  trouble: document.querySelector('.wi-trouble')?.textContent || "",
  starts: [...document.querySelectorAll('[data-start] .gl-go__word')].map((b) => b.textContent),
  rails: [...document.querySelectorAll('.gl-row--launch .gl-row__rail')].map((r) => r.dataset.priority || ""),
  priorities: [...document.querySelectorAll('.gl-row--launch .gl-priority')].map((p) => p.textContent),
})`));
shape.starts = shape.starts.map(tidy);
shape.groups = shape.groups.map(tidy);

check("the items are grouped by project",
  [...shape.groups].sort().join("|") === "group/other|group/proj", shape.groups.join("|"));
/* A project you cannot start in is still worth seeing, but it is not what you
   came here to press — so it goes last however urgent its items are. */
check("the project with a checkout here comes first",
  shape.groups[0] === "group/proj", shape.groups.join("|"));
check("a project with no checkout says so, and its row asks for one first",
  shape.missing && shape.starts.some((s) => s === "Pick a folder"), shape.starts.join(" / "));
check("a project with one offers the press itself",
  shape.starts.some((s) => s === "Start"), shape.starts.join(" / "));
check("the rows are the ones the tab draws — rail, priority and labels come too",
  shape.rails.join(",") === "high,low" && shape.priorities.map(tidy).join(",") === "high,low",
  `${shape.rails} / ${shape.priorities.map(tidy)}`);
check("a GitLab that could not be reached is said beside the list, not instead of it",
  shape.trouble.includes("git.down.com") && shape.rows === 2, `${shape.rows} rows`);

/* The filter runs over the list without taking the field out from under the
   caret — the pane is rebuilt on every poll, so this is the thing most likely
   to break later. */
await evaluate(`(() => {
  const field = document.querySelector('#itemSearch');
  field.focus();
  field.value = "docs";
  field.dispatchEvent(new Event("input", { bubbles: true }));
  return true;
})()`);
await sleep(600);
const filtered = JSON.parse(await evaluate(`JSON.stringify({
  rows: document.querySelectorAll('.gl-row--launch').length,
  focused: document.activeElement?.id || "",
  value: document.querySelector('#itemSearch')?.value || "",
})`));
check("the filter narrows the list", filtered.rows === 1, `${filtered.rows} rows`);
check("and the field keeps the focus and what was typed in it",
  filtered.focused === "itemSearch" && filtered.value === "docs", JSON.stringify(filtered));

/* Starting a session says what it is doing, and says it about the right item. */
await evaluate(`document.querySelector('[data-start]').click()`);
await sleep(900);
const asked = JSON.parse(await evaluate(
  `JSON.stringify(window.__asked.filter((a) => a.url.startsWith("/api/forge/start")))`));
check("starting a session names the work item, and not a folder",
  asked.length === 1 && asked[0].body.includes('"iid":9') && !asked[0].body.includes("folder"),
  asked[0]?.body || "nothing was sent");
check("an item with no checkout asks for the folder chooser",
  asked[0]?.body.includes('"pick":true'), asked[0]?.body || "");

/* Escape is how you come back out of it, the way it is for the settings page. */
await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`);
await sleep(700);
check("Escape closes the page",
  await evaluate(`document.querySelector('#itemsButton').getAttribute('aria-pressed') === "false"`));

check("nothing threw while any of that happened", consoleErrors.length === 0,
  consoleErrors.slice(0, 3).join(" | "));

console.log(failures ? `\n${failures} check(s) failed` : "\nall ok");
ws.close();
process.exit(failures ? 1 : 0);

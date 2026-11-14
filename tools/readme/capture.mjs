// Screenshots and GIFs of the demo panel, for the README.
//
//   tools/readme/stage.sh &                      # the demo machine, port 8790
//   PLAYWRIGHT=/path/to/node_modules/playwright-core CHROME=/path/to/chrome \
//     node tools/readme/capture.mjs [stills|gifs|all]
//
// Writes docs/readme/*.png and *.gif, each in a light and a dark version.
// GIFs are recorded from Chrome's screencast, so they are as sharp as the
// stills, then cut down to a palette with ffmpeg. Headless Chrome draws no
// pointer, so the page gets a stand-in cursor that shows where each click lands.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync, rmSync, readdirSync, utimesSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT || "playwright-core");
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = join(ROOT, "docs", "readme");
const URL = process.env.PANEL_URL || "http://127.0.0.1:8790";
const SESSIONS = process.env.SESSIONS || "/tmp/wt-readme/demo/sessions";
const what = process.argv[2] || "all";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- A visible cursor, and a ring where a click or tap lands ----------------
const CURSOR = () => {
  addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24"><path d="M5 3l14 8-6 1.6L10 19z" fill="#1E1F24" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, { position: "fixed", left: "-40px", top: "-40px", zIndex: 2147483647, pointerEvents: "none",
      transform: "translate(-4px,-3px)", filter: "drop-shadow(0 2px 3px rgba(0,0,0,.35))", display: window.__touch ? "none" : "block" });
    document.body.append(c);
    addEventListener("pointermove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
    addEventListener("pointerdown", (e) => {
      const r = document.createElement("div");
      Object.assign(r.style, { position: "fixed", left: e.clientX - 18 + "px", top: e.clientY - 18 + "px", width: "36px", height: "36px",
        borderRadius: "50%", background: "rgba(165,159,226,.45)", border: "2px solid #A59FE2", zIndex: 2147483646, pointerEvents: "none",
        transition: "transform .45s cubic-bezier(.2,0,0,1), opacity .45s", transform: "scale(.4)" });
      document.body.append(r);
      requestAnimationFrame(() => { r.style.transform = "scale(1.4)"; r.style.opacity = "0"; });
      setTimeout(() => r.remove(), 600);
    }, true);
  });
};

async function open(theme, { phone = false, scale = 2 } = {}) {
  const ctx = await browser.newContext(phone
    ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: scale, isMobile: true, hasTouch: true }
    : { viewport: { width: 1280, height: 800 }, deviceScaleFactor: scale });
  const page = await ctx.newPage();
  if (phone) await page.addInitScript(() => { window.__touch = true; });
  await page.addInitScript(CURSOR);
  await page.goto(`${URL}/?theme=${theme}`);
  await page.waitForTimeout(1800);
  return page;
}

// Glide the cursor to an element, then click it.
async function go(page, locator, { click = true, steps = 18 } = {}) {
  const box = await locator.boundingBox();
  const x = box.x + Math.min(box.width / 2, 120), y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps });
  if (click) { await sleep(120); await page.mouse.click(x, y); }
}
const row = (page, text) => page.locator("button.session-item", { hasText: text });
const tab = (page, text) => page.locator("button.tab", { hasText: text });

// Set a demo session's status, the way Claude Code would.
function setStatus(name, status) {
  const plan = JSON.parse(readFileSync(join(SESSIONS, "..", "plan.json"), "utf8"));
  const sid = plan.find((p) => p.name === name).sid;
  const file = join(SESSIONS, `${sid}.json`);
  const data = JSON.parse(readFileSync(file, "utf8"));
  data.status = status; data.statusUpdatedAt = data.updatedAt = Date.now();
  writeFileSync(file, JSON.stringify(data, null, 2));
  const now = new Date(); utimesSync(file, now, now);
}

// ---- GIF recording through the screencast ------------------------------------
async function record(page, name, script, { width = 1000, fps = 15, crop = null } = {}) {
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  cdp.on("Page.screencastFrame", async (f) => {
    frames.push({ data: f.data, t: f.metadata.timestamp });
    await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });
  const vp = page.viewportSize();
  await cdp.send("Page.startScreencast", { format: "png", everyNthFrame: 1, maxWidth: vp.width * 2, maxHeight: vp.height * 2 });
  await script();
  await sleep(300);
  await cdp.send("Page.stopScreencast");
  const tmp = join(OUT, `.frames-${name}`);
  rmSync(tmp, { recursive: true, force: true }); mkdirSync(tmp);
  let list = "";
  frames.forEach((f, i) => {
    const file = `f${String(i).padStart(5, "0")}.png`;
    writeFileSync(join(tmp, file), Buffer.from(f.data, "base64"));
    const next = frames[i + 1] ? frames[i + 1].t - f.t : 1.6;
    list += `file '${file}'\nduration ${Math.max(0.02, next).toFixed(3)}\n`;
  });
  list += `file 'f${String(frames.length - 1).padStart(5, "0")}.png'\n`;
  writeFileSync(join(tmp, "list.txt"), list);
  const k = await page.evaluate(() => devicePixelRatio);
  const cut = crop ? `crop=${crop.w * k}:${crop.h * k}:${crop.x * k}:${crop.y * k},` : "";
  const vf = `${cut}fps=${fps},scale=${width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle`;
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", join(tmp, "list.txt"), "-vf", vf, "-loop", "0", join(OUT, `${name}.gif`)]);
  rmSync(tmp, { recursive: true, force: true });
  console.log(`${name}.gif  ${frames.length} frames`);
}

// ---- Stills --------------------------------------------------------------------
// Down to a 256-colour palette: the panel is flat colour, so this looks the
// same and weighs about a third.
function squash(file) {
  const tmp = file.replace(/\.png$/, ".q.png");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", file, "-vf",
    "split[a][b];[a]palettegen=max_colors=256[p];[b][p]paletteuse=dither=none", tmp]);
  execFileSync("mv", [tmp, file]);
}
async function stills(theme) {
  const page = await open(theme);
  const shot = async (name) => { await page.evaluate(() => { const c = document.getElementById("demo-cursor"); if (c) c.style.display = "none"; }); await page.mouse.move(1270, 790); await sleep(500); await page.screenshot({ path: join(OUT, `${name}-${theme}.png`) }); console.log(`${name}-${theme}.png`); };
  await row(page, "Checkout").click(); await sleep(1000);
  await shot("overview");
  await row(page, "Tune the gripper").click(); await sleep(1000); await shot("conversation");
  await tab(page, "Git").click(); await sleep(1300); await shot("git");
  await tab(page, "History").click(); await sleep(1300); await shot("history");
  await tab(page, "Usage").click(); await sleep(1300); await shot("usage");
  await row(page, "Checkout").click(); await sleep(800);
  await tab(page, "Preview").click(); await sleep(2200); await shot("preview");
  await page.locator('button[aria-label="Settings"]').click(); await sleep(1000); await shot("settings");
  await page.locator("button", { hasText: "Claude skills" }).first().click(); await sleep(1000); await shot("skills");
  await page.context().close();

  const phone = await open(theme, { phone: true, scale: 3 });
  await row(phone, "Checkout").tap(); await sleep(1200);
  await phone.screenshot({ path: join(OUT, `phone-question-${theme}.png`) });
  await phone.locator('button[aria-label="Back to the session list"]').tap(); await sleep(800);
  await row(phone, "Tune the gripper").tap(); await sleep(1200);
  await phone.screenshot({ path: join(OUT, `phone-conversation-${theme}.png`) });
  console.log(`phone-*-${theme}.png`);
  await phone.context().close();
}

// ---- GIFs ------------------------------------------------------------------------
async function tour(theme) {
  const page = await open(theme, { scale: 1 });
  await row(page, "Checkout").click(); await sleep(600);
  await page.mouse.move(640, 600);
  await record(page, `tour-${theme}`, async () => {
    await sleep(1400);
    await go(page, row(page, "Tune the gripper")); await sleep(1600);
    await page.mouse.move(800, 560, { steps: 10 }); await page.mouse.wheel(0, -260); await sleep(1300);
    await go(page, tab(page, "Git")); await sleep(1500);
    await go(page, tab(page, "History")); await sleep(1500);
    setStatus("garden-planner-77", "waiting");
    await go(page, tab(page, "Usage")); await sleep(1900);
    await go(page, row(page, "Sowing calendar")); await sleep(1700);
    await go(page, row(page, "Checkout")); await sleep(1300);
    await go(page, tab(page, "Preview")); await sleep(1700);
    await go(page, page.locator('button[aria-label="Phone"]')); await sleep(1800);
    await go(page, page.locator('button[aria-label="Desktop"]')); await sleep(1300);
    setStatus("garden-planner-77", "busy");
    await go(page, tab(page, "Conversation")); await sleep(1600);
  }, { width: 1000, fps: 15 });
  await page.context().close();
}

async function live(theme) {
  const page = await open(theme, { scale: 1 });
  await page.mouse.move(-50, -50);
  await record(page, `states-${theme}`, async () => {
    await sleep(1500);
    setStatus("api-client-5e", "busy"); await sleep(2200);
    setStatus("garden-planner-77", "waiting"); await sleep(2400);
    setStatus("robot-arm-9c", "busy"); await sleep(2200);
    setStatus("garden-planner-77", "busy"); setStatus("api-client-5e", "idle"); await sleep(2200);
    setStatus("robot-arm-9c", "idle"); await sleep(1800);
  }, { width: 760, fps: 15, crop: { x: 0, y: 0, w: 380, h: 560 } });
  await page.context().close();
}

async function phoneGif(theme) {
  const page = await open(theme, { phone: true, scale: 2 });
  await record(page, `phone-${theme}`, async () => {
    await sleep(1300);
    await row(page, "Checkout").tap(); await sleep(2200);
    await page.mouse.wheel(0, 300); await sleep(1400);
    await page.locator('button[aria-label="Back to the session list"]').tap(); await sleep(1300);
    await row(page, "Tune the gripper").tap(); await sleep(2200);
    await tab(page, "Usage").tap(); await sleep(2000);
    await page.locator('button[aria-label="Back to the session list"]').tap(); await sleep(1200);
  }, { width: 360, fps: 15 });
  await page.context().close();
}

const only = process.argv[3];  // e.g. "live", to redo one kind
for (const theme of ["light", "dark"]) {
  if (what === "all" || what === "stills") await stills(theme);
  if (what === "all" || what === "gifs") {
    if (!only || only === "tour") await tour(theme);
    if (!only || only === "live") await live(theme);
    if (!only || only === "phone") await phoneGif(theme);
  }
}
await browser.close();
if (what === "all" || what === "stills") for (const f of readdirSync(OUT).filter((f) => f.endsWith(".png"))) squash(join(OUT, f));
for (const f of readdirSync(OUT).filter((f) => f.endsWith(".gif"))) {
  const { size } = (await import("node:fs")).statSync(join(OUT, f));
  console.log(f.padEnd(26), (size / 1e6).toFixed(2), "MB");
}

// The warning the update dialog gives about sessions the panel is running, and
// the reason a checkout is being left alone — run without a browser.
//
// Same trick as composer-check.mjs and chat-check.mjs, and for the same reason:
// these are the two sentences the update feature is judged by, and both of them
// are pure functions of one object. So they are lifted out of the page and given
// every shape of that object, rather than needing a Chrome and a repository with
// real releases in it to see one line of text.
//
//   node tests/update-check.mjs
//
// A failure prints the case and exits 1.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".ts") ? [readFileSync(path, "utf8")] : [];
  });
}
const page = sources(join(here, "..", "web", "src")).join("\n");

const unexport = (text) => text.replace(/(^|\n)export /g, "$1");

function lift(name) {
  const start = page.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} is not in the page any more`);
  let depth = 0;
  for (let at = page.indexOf("{", start); at < page.length; at++) {
    if (page[at] === "{") depth++;
    else if (page[at] === "}" && --depth === 0) return unexport(page.slice(start, at + 1));
  }
  throw new Error(`${name} does not close`);
}

const stubs = `
const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
`;

const runningSays = new Function(`${stubs}\n${lift("runningSays")}\nreturn runningSays;`)();

let failures = 0;
const strip = (html) => html.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();

function check(label, got, want) {
  const ok = typeof want === "function" ? want(got) : got === want;
  if (ok) { console.log(`PASS  ${label}`); return; }
  failures++;
  console.log(`FAIL  ${label}\n      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`);
}

console.log("\nWhat a restart costs, said as a sentence.\n");

check("nothing running says nothing at all",
  runningSays({ here: 0, busy: 0, compacting: 0, queued: 0, names: [] }), "");
check("a missing reading says nothing either", runningSays(undefined), "");
check("a reading with no count says nothing", runningSays({}), "");

check("one idle session is a fact, not a warning",
  strip(runningSays({ here: 1, busy: 0, compacting: 0, queued: 0, names: ["watchtower-55"] })),
  "One session is running here — watchtower-55. They are stopped when the panel restarts. "
  + "Their conversations stay on disk either way, and claude --resume in the folder still finds them.");
check("...and is not tinted",
  runningSays({ here: 1, busy: 0, compacting: 0, queued: 0, names: ["a"] }).includes('data-sharp="0"'), true);

check("a turn in flight is named as cut off",
  strip(runningSays({ here: 2, busy: 1, compacting: 0, queued: 0, names: ["busy-one", "idle-one"] })),
  "2 sessions are running here — busy-one, idle-one. They are stopped when the panel restarts "
  + "— one is mid-turn, and that turn is cut off. Their conversations stay on disk either way, "
  + "and claude --resume in the folder still finds them.");
check("...and is tinted",
  runningSays({ here: 1, busy: 1, compacting: 0, queued: 0, names: ["a"] }).includes('data-sharp="1"'), true);

check("two mid-turn read as plural",
  strip(runningSays({ here: 2, busy: 2, compacting: 0, queued: 0, names: ["a", "b"] })).includes("2 are mid-turn"), true);
check("a compaction gets its own clause",
  strip(runningSays({ here: 1, busy: 0, compacting: 1, queued: 0, names: ["a"] })).includes("one is compacting"), true);
check("typed-ahead messages are counted",
  strip(runningSays({ here: 1, busy: 1, compacting: 0, queued: 3, names: ["a"] })).includes("3 typed-ahead messages would be dropped"), true);
check("one typed-ahead message is singular",
  strip(runningSays({ here: 1, busy: 1, compacting: 0, queued: 1, names: ["a"] })).includes("1 typed-ahead message would be dropped"), true);
check("every clause is joined rather than one winning",
  strip(runningSays({ here: 3, busy: 1, compacting: 1, queued: 2, names: ["a", "b", "c"] })),
  (got) => got.includes("one is mid-turn") && got.includes("one is compacting")
        && got.includes("2 typed-ahead messages"));

check("more sessions than names says how many are left",
  strip(runningSays({ here: 9, busy: 0, compacting: 0, queued: 0, names: ["a", "b", "c", "d"] })).includes("a, b, c, d, and 5 more"), true);
check("no names at all still says how many",
  strip(runningSays({ here: 4, busy: 0, compacting: 0, queued: 0, names: [] })),
  (got) => got.startsWith("4 sessions are running here. They are stopped"));

// A session name is whatever somebody typed into the rename field, so it reaches
// this sentence the same way every other name does: escaped.
check("a name cannot carry markup into the dialog",
  runningSays({ here: 1, busy: 0, compacting: 0, queued: 0, names: ["<img src=x onerror=alert(1)>"] }),
  (got) => !got.includes("<img") && got.includes("&lt;img"));

check("the reassurance is never left off",
  [{ here: 1, busy: 1, compacting: 1, queued: 5, names: ["a"] },
   { here: 7, busy: 0, compacting: 0, queued: 0, names: [] }]
    .every((r) => strip(runningSays(r)).includes("conversations stay on disk")), true);

console.log(failures ? `\n${failures} failed` : "\nall ok");
process.exit(failures ? 1 : 0);

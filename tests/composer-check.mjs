// Checks for the composer's own template, run without a browser.
//
// The panel is one HTML file with the script inline, so most of it can only be
// checked by driving a real browser — which ui-check.mjs does, and which needs a
// Chrome and Node 24. This one needs neither: it lifts the `composer` function
// out of the page, gives it stubs for what it leans on, and asserts what it
// draws for a session in each state. Cheap enough to run on every edit, and it
// is the check that would have caught a duplicate `const` that turned the whole
// script into a syntax error.
//
//   node tests/composer-check.mjs
//
// A failure prints the case and exits 1.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(join(here, "..", "static", "index.html"), "utf8");

/* The function, lifted by matching its braces — no bundler, no parser. */
function lift(name) {
  const start = page.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} is not in the page any more`);
  let depth = 0;
  for (let at = page.indexOf("{", start); at < page.length; at++) {
    if (page[at] === "{") depth++;
    else if (page[at] === "}" && --depth === 0) return page.slice(start, at + 1);
  }
  throw new Error(`${name} does not close`);
}

/* The same, for the ones written as a const holding an arrow. `runsHere` was
   stubbed here rather than lifted, which meant the one predicate the whole
   composer turns on was the one thing this check could not see: it went on
   passing while the page's own copy said something else. Brackets, braces and
   backticks are tracked so a template literal's own punctuation does not end
   the statement early. */
function liftConst(name) {
  const start = page.search(new RegExp(`(?:^|\\n)const ${name} = `));
  if (start < 0) throw new Error(`${name} is not in the page any more`);
  let depth = 0, tick = false;
  for (let at = page.indexOf("=", start); at < page.length; at++) {
    const ch = page[at];
    if (ch === "\\") { at++; continue; }
    if (ch === "`") { tick = !tick; continue; }
    if (tick) continue;
    if ("({[".includes(ch)) depth++;
    else if (")}]".includes(ch)) depth--;
    else if (ch === ";" && depth === 0) return page.slice(start, at + 1);
  }
  throw new Error(`${name} does not close`);
}

const stubs = `
const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const OWNED_MODE_LABEL = { default: "Manual", auto: "Auto", plan: "Plan", acceptEdits: "Accept edits" };
let FEED = {};
const ownedFor = (s) => FEED[s.sessionId] || {};
const ICON = { check: "<svg/>", ask: "<svg/>", play: "<svg/>", stop: "<svg/>", compact: "<svg/>" };
const ownedAskCard = () => "<ASKCARD>";
let IMAGES = {};
const imagesFor = (id) => IMAGES[id] || [];
const sendBlockedReason = LIFTED_BLOCKED;
let skew = 0;
let CATALOG = { entries: [], terminalOnly: [] };
const catalog = { get entries() { return CATALOG.entries; },
                  get terminalOnly() { return CATALOG.terminalOnly; } };
`;

const { composer, modeBar, setFeed, setImages, sendBlockedReason, runsHere, queuedStrip,
        stopButton, attachedStrip, contextBar, terminalOnly, sentAs, knownCommand,
        compactPct, drawnStateOf, STATE, STATE_ORDER, setCatalog } = new Function(
  `const feed = { canSend: true };
   ${lift("sendBlockedReason").replace("function sendBlockedReason", "const LIFTED_BLOCKED = function")}
   ${stubs}
   ${liftConst("runsHere")}
   ${liftConst("wayIn")}
   ${lift("modeBar")}
   ${lift("queuedStrip")}
   ${lift("attachedStrip")}
   ${lift("stopButton")}
   ${liftConst("STATE")}
   ${liftConst("STATE_ALIAS")}
   ${liftConst("STATE_ORDER")}
   ${liftConst("stateKeyOf")}
   ${liftConst("stateOf")}
   ${liftConst("drawnStateOf")}
   ${liftConst("COMPACT_AT")}
   ${liftConst("CONTEXT_TIGHT")}
   ${liftConst("CONTEXT_FULL")}
   ${liftConst("COMPACT_TAU")}
   ${liftConst("COMPACT_CAP")}
   ${liftConst("compactPct")}
   ${lift("tokens")}
   ${lift("contextBar")}
   ${lift("slashOf")}
   ${liftConst("PIPE_TERMINAL_ONLY")}
   ${liftConst("terminalOnly")}
   ${liftConst("knownCommand")}
   ${liftConst("cmdEntry")}
   ${lift("sentAs")}
   ${lift("composer")}
   return { composer, modeBar, runsHere, queuedStrip, stopButton, attachedStrip, contextBar,
            compactPct, drawnStateOf, STATE, STATE_ORDER,
            sendBlockedReason: LIFTED_BLOCKED, setFeed: (f) => { FEED = f; feed.owned = f; },
            setImages: (i) => { IMAGES = i; },
            terminalOnly, sentAs, knownCommand,
            setCatalog: (c) => { CATALOG = c; } };`)();

let failures = 0;

/* Every name the page calls but might not define. Rebuilding one block by
   replacing everything between two functions deleted `pickMode` while leaving
   its one caller behind, so every mode chip threw a ReferenceError on click and
   nothing said so. A syntax check cannot see that; this can. */
const CALLED = [
  "pickMode", "runsHere", "wayIn", "modeBar", "composer", "ownedAskCard", "sendAskAnswer",
  "askPicksFor", "ownedFor", "sendBlockedReason", "openAdoptDialog", "openNewMenu",
  "newSessionFolders", "pickNewFolder", "drawnStateOf", "sendMessage", "detailHeader", "queuedStrip",
  "stopButton", "showSnackbar", "attachedStrip", "imagesFor", "attachPicture", "picturesOn",
  "dropImage", "withImages", "imagesStamp", "clearImages", "setSayImages", "readAsBase64",
  "contextBar", "compactSession", "compactPct", "tokens", "terminalOnly", "knownCommand", "cmdMatches",
  "headerActions", "menuItemsFor", "openMenu",
];

function check(what, ok, note = "") {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${note ? `  — ${note}` : ""}`);
  if (!ok) failures++;
}

/* A live session cannot have its turns taken — something else is running it — but
   the bar is still drawn, disabled and saying so. Left out, the whole feature
   read as missing rather than as inapplicable. */
setFeed({ live: {}, kept: { mode: "plan" } });
const live = composer({ sessionId: "live", status: "idle", canSay: true, alive: true });
check("a live session offers the way in", live.includes(`data-act="adopt"`));
check("named for what it gives you", /Make interactive/.test(live));
check("and offers no mode to pick yet", !live.includes("data-mode="));
check("and does not offer to run a turn", !live.includes(`data-act="own"`));

/* A kept session is the one the panel can run, so its chips are live and there
   is one button on it. It used to be a pair — *Run it here* beside *In a
   terminal* — which made every message ask a question about process management
   first. Starting a stopped session is part of sending to it. */
setFeed({ kept: { mode: "plan" } });
const kept = composer({ sessionId: "kept", status: "stopped" });
check("one Send, and the panel is what runs it",
  kept.includes(`data-act="own"`) && !kept.includes(`data-act="say"`));
check("and it is not qualified with where it runs",
  !/Run it here|In a terminal/.test(kept));
check("nor is there a second button beside it",
  (kept.match(/composer__send/g) || []).length === 1);

/* The modes are a header control now, beside the session's name, because they
   say what the session may do rather than anything about this message. */
const bar = modeBar({ sessionId: "kept", status: "stopped" });
check("every mode the panel runs is offered", (bar.match(/data-mode="/g) || []).length === 4,
  (bar.match(/data-mode="([a-zA-Z]+)"/g) || []).join(" "));
check("the stored mode reads as chosen", /data-mode="plan"[\s\S]{0,60}aria-pressed="true"/.test(bar));
check("and says so by colour alone, with no tick beside it", !/chip__check|ICON.check|<svg/.test(bar));
check("a session that is not ours has no mode to set",
  modeBar({ sessionId: "live", status: "idle", canSay: true, alive: true }) === "");

/* The rule the whole composer turns on, stated once: a session alive in a
   terminal is the only one the panel cannot run, because that terminal holds the
   transcript. Everything else is ours. */
setFeed({ any: {}, mine: { here: true }, held: { running: true } });
check("a session alive in a terminal is not ours",
  runsHere({ sessionId: "any", status: "idle", alive: true }) === false);
check("one that has closed is",
  runsHere({ sessionId: "any", status: "offline", alive: false }) === true);
check("a kept row with nothing behind it is",
  runsHere({ sessionId: "any", status: "stopped", alive: false }) === true);
check("an adopted one is, whatever its status says",
  runsHere({ sessionId: "mine", status: "busy", alive: false }) === true);
/* The case that regressed after a refresh: a turn run from a row nobody had
   adopted moved its status off `stopped`, and the row stopped being ours
   mid-turn. Holding the process is enough to be ours, whatever `here` says. */
check("and so is one the panel is holding open right now",
  runsHere({ sessionId: "held", status: "idle", alive: false }) === true);

/* A prompt outranks the box: there is nothing useful to type while a turn is
   stopped in front of something only a person can settle. */
setFeed({ kept: { mode: "plan", ask: { requestId: "r", asks: false, name: "Write" } } });
check("a standing prompt takes the box's room",
  composer({ sessionId: "kept", status: "stopped" }).includes("<ASKCARD>"));

/* An adopted session that is not up yet: typing starts it back up. */
setFeed({ kept: { mode: "plan", here: true } });
const adopted = composer({ sessionId: "kept", status: "stopped" });
check("an adopted but idle session offers to start back up",
  /starts back up/.test(adopted), (adopted.match(/placeholder="([^"]*)"/) || [])[1] || "");

/* Held open and running: this is the case that regressed. Its status is `idle`
   like any running session, so anything keyed on `stopped` stopped treating it
   as ours — and the live-session bar came back with Make interactive on it. */
setFeed({ kept: { mode: "plan", here: true, running: true } });
const running = composer({ sessionId: "kept", status: "idle" });
check("a running session of ours is still ours", !running.includes(`data-act="adopt"`));
check("it never offers to make interactive what is already interactive",
  !/Make interactive/.test(running));
check("its mode is set from the header", modeBar({ sessionId: "kept", status: "idle" }).includes("mode-chip"));
check("and it just sends", running.includes(`data-act="own"`) && !running.includes(`data-act="say"`));

setFeed({ kept: { mode: "plan", here: true, running: true, busy: true } });
const midTurn = composer({ sessionId: "kept", status: "busy" });
check("a turn in flight leaves the box alone", midTurn.includes("sayField"));
/* And says what the box does with what you type into it, which is the whole of
   the change: the message is held for the end of the turn rather than refused
   with "it is still answering the last one". */
check("and says the message goes in when the turn ends",
  /goes in when the turn ends/.test(midTurn),
  (midTurn.match(/placeholder="([^"]*)"/) || [])[1] || "");
check("with nothing waiting, nothing is drawn", !midTurn.includes("queued__item"));

/* Typed ahead: what is waiting is shown, in order, each with a way back out. A
   promise you cannot see is indistinguishable from a message that was dropped. */
setFeed({ kept: { mode: "plan", here: true, running: true, busy: true,
                  queued: ["and then the tests", "and push it"] } });
const typedAhead = composer({ sessionId: "kept", status: "busy" });
check("everything waiting is drawn", (typedAhead.match(/queued__item/g) || []).length === 2);
check("in the order it was typed",
  typedAhead.indexOf("and then the tests") < typedAhead.indexOf("and push it"));
check("each with a way to take it back",
  /data-act="unqueue" data-index="0"/.test(typedAhead) &&
  /data-act="unqueue" data-index="1"/.test(typedAhead));
check("and it says how many are waiting", /2 messages waiting/.test(typedAhead));
setFeed({ kept: { mode: "plan", here: true, running: true, busy: true, queued: ["one thing"] } });
check("a single message counts itself in the singular",
  /1 message waiting/.test(composer({ sessionId: "kept", status: "busy" })));
setFeed({ one: { queued: ["<b>hi</b>"] } });
check("what is queued is escaped where it is drawn",
  queuedStrip({ sessionId: "one" }).includes("&lt;b&gt;hi&lt;/b&gt;"));

/* Stopping the train of thought. Only a session the panel holds has a channel
   for it — the interrupt goes down the pipe the panel owns — so that is the only
   one whose button does anything. */
setFeed({ kept: { mode: "plan", here: true, running: true, busy: true } });
const working = composer({ sessionId: "kept", status: "busy" });
check("a working session of ours can be stopped", working.includes(`data-act="stop"`));
check("and the button is live", !/data-act="stop"[^>]*disabled/.test(working));
check("it says how else to reach it", /Ctrl\+C/.test(working));
setFeed({ kept: { mode: "plan", here: true, running: true, busy: false } });
check("one that is not working has nothing to stop",
  !composer({ sessionId: "kept", status: "idle" }).includes(`data-act="stop"`));
setFeed({ kept: { mode: "plan", here: true, running: true, busy: true, stopping: true } });
check("a stop already sent is not offered twice",
  /data-act="stop"[\s\S]{0,80}disabled/.test(composer({ sessionId: "kept", status: "busy" })));
/* A session in a terminal is the case that cannot be done, and saying so is the
   point: SIGINT to it ends the session rather than the turn, which was measured.
   The button is there and disabled, naming the Ctrl+C that does work. */
setFeed({ terminal: {} });
const inTerminalBusy = composer({ sessionId: "terminal", status: "busy", canSay: true, alive: true });
check("a session in a terminal is told which Ctrl+C works",
  /data-act="stop"[\s\S]{0,60}disabled/.test(inTerminalBusy) &&
  /own terminal/.test(inTerminalBusy));
check("and is still offered the way to a turn the panel can stop",
  inTerminalBusy.includes(`data-act="adopt"`));
check("an idle session in a terminal is offered no stop at all",
  stopButton({ sessionId: "terminal", status: "idle", canSay: true, alive: true }) === "");

/* A prompt takes the box's room, but not the queue's: what you typed ahead goes
   in after the turn this prompt is holding up, so this is exactly when you might
   want it back. */
setFeed({ kept: { mode: "plan", here: true, running: true, busy: true,
                  queued: ["carry on"], ask: { requestId: "r", asks: false, name: "Bash" } } });
const askingWithQueue = composer({ sessionId: "kept", status: "busy" });
check("a standing prompt still shows what is waiting behind it",
  askingWithQueue.includes("<ASKCARD>") && askingWithQueue.includes("queued__item"));

/* Pictures pasted into the box. They cannot travel in the message — every
   transport the panel has takes a string — so what is drawn is the file each one
   was saved to, which is what the message will name. */
setFeed({ kept: { mode: "plan", here: true, running: true } });
setImages({ kept: [
  { id: "shot1", url: "blob:one", path: "/w/.claude/watchtower-images/paste-a.png", name: "paste-a.png" },
  { id: "shot2", url: "blob:two", path: "", name: "", failed: false },
] });
const withPictures = composer({ sessionId: "kept", status: "idle" });
check("every pasted picture is drawn", (withPictures.match(/attached__item/g) || []).length === 2);
check("a saved one is named by the file it went to", /paste-a\.png/.test(withPictures));
check("one still going up says so", /Saving…/.test(withPictures));
check("each has a way to leave it out",
  /data-act="unattach" data-id="shot1"/.test(withPictures) &&
  /data-act="unattach" data-id="shot2"/.test(withPictures));
check("and the box says they go by path", /by path/.test(withPictures));
setImages({ kept: [{ id: "shot1", url: "blob:one", path: "", failed: true, why: "Could not write it" }] });
check("one that did not save says why rather than pretending",
  /attached__item--failed/.test(composer({ sessionId: "kept", status: "idle" })) &&
  /Could not write it/.test(composer({ sessionId: "kept", status: "idle" })));
setImages({ kept: [{ id: "shot1", url: 'blob:"><b>x</b>', path: "<b>p</b>", name: "<b>p</b>" }] });
check("what came off the clipboard is escaped where it is drawn",
  !attachedStrip({ sessionId: "kept" }).includes("<b>"));
/* A prompt takes the box's room, and a picture pasted before it went up still
   belongs to the message waiting behind it. */
setImages({ kept: [{ id: "shot1", url: "blob:one", path: "/w/p.png", name: "p.png" }] });
setFeed({ kept: { mode: "plan", here: true, running: true, ask: { requestId: "r", asks: false, name: "Write" } } });
check("a standing prompt keeps what was pasted behind it",
  composer({ sessionId: "kept", status: "busy" }).includes("attached__item"));
setImages({});
setFeed({ kept: { mode: "plan" } });
check("with nothing pasted, nothing is drawn",
  !composer({ sessionId: "kept", status: "stopped" }).includes("attached__item"));

/* A session that cannot be typed at at all keeps saying why, ahead of any of
   this — but saying why was all it did, and that made the row you were most
   stuck on the one row with no way off the terminal. The reason and the way in
   are both true at once, so both are drawn. */
setFeed({ blocked: {} });
const blocked = composer({ sessionId: "blocked", status: "waiting", canSay: true, alive: true });
check("a blocked session still leads with its reason", /answer the prompt/.test(blocked));
check("and is still offered the way out of the terminal", blocked.includes(`data-act="adopt"`));
check("but no box, because the prompt in front of it is modal", !blocked.includes("sayField"));

/* The bug that started this: a held session's status is `idle` and it has no
   messaging socket at all, so a gate testing canSay before asking who runs the
   session says "this session is not listening for messages" about a session the
   panel is holding a live pipe to. */
setFeed({ held: { mode: "plan", here: true, running: true } });
check("a session the panel holds is never called unreachable",
  sendBlockedReason({ sessionId: "held", status: "idle" }) === null,
  String(sendBlockedReason({ sessionId: "held", status: "idle" })));
/* And the one after it: a session that is not listening used to have its box
   taken away and be called unreachable. Nothing was unreachable about it — the
   server holds the message and starts it back up — so the box stays, and only
   what it promises changes. */
setFeed({ nosocket: {} });
const inTerminal = { sessionId: "nosocket", status: "idle", alive: true };
check("a session that is not listening is no longer turned away",
  sendBlockedReason(inTerminal) === null, String(sendBlockedReason(inTerminal)));
const nosocket = composer(inTerminal);
check("it gets a box that says when the message goes in",
  nosocket.includes("sayField") && /as soon as it is listening/.test(nosocket),
  (nosocket.match(/placeholder="([^"]*)"/) || [])[1] || "");
/* A closed session belonged to nobody: past `stopped`, so it got no mode, no way
   in, and a box that only offered to open a terminal. Nothing holds its
   transcript, so it is ours like any other kept row. */
setFeed({ gone: {} });
const gone = composer({ sessionId: "gone", status: "offline", alive: false });
check("a closed session is typed at to bring it back",
  gone.includes("sayField") && /starts back up/.test(gone),
  (gone.match(/placeholder="([^"]*)"/) || [])[1] || "");
check("and Send is what brings it back, with no terminal offered",
  gone.includes(`data-act="own"`) && !gone.includes(`data-act="say"`));
check("it has a mode to pick, like every session the panel runs",
  modeBar({ sessionId: "gone", status: "offline", alive: false }).includes("mode-chip"));
check("and is not offered a session to make interactive",
  !/Make interactive/.test(gone));

/* How full the conversation is, and when the way to make it smaller is offered.
   Compacting needs the held pipe — a slash command over a session's messaging
   socket is queued unexpanded and does nothing — so the reading is for everyone
   and the button is for a session the panel runs. */
const ctxOf = (tokens, extra = {}) => ({
  sessionId: "ctx", status: "stopped", alive: false,
  context: { tokens, window: 1_000_000, share: tokens / 1_000_000, model: "claude-opus-5" },
  ...extra,
});
setFeed({ ctx: {} });
const roomy = contextBar(ctxOf(120_000));
check("a conversation with room says how full it is", /12% of 1.0M/.test(roomy),
  (roomy.match(/>([^<]*% of[^<]*)</) || [])[1] || "");
check("and is not offered a compaction it does not need",
  !roomy.includes(`data-act="compact"`));
const halfway = contextBar(ctxOf(550_000));
check("past halfway the way to make it smaller appears",
  halfway.includes(`data-act="compact"`));
check("a session with no reading yet draws nothing at all",
  contextBar({ sessionId: "ctx", status: "stopped", alive: false }) === "");
/* Compacting is the one thing here that is not about the conversation but about
   who is running it. A terminal session gets the reading and no button. */
check("a session in a terminal is told how full it is",
  /55% of/.test(contextBar(ctxOf(550_000, { status: "idle", alive: true }))));
check("but is not offered a button that could not work there",
  !contextBar(ctxOf(550_000, { status: "idle", alive: true })).includes(`data-act="compact"`));
/* The bar changes colour before it is offered, and again where Claude Code
   stops waiting and compacts on its own. */
check("a full one is coloured for it", /data-tight="2"/.test(contextBar(ctxOf(950_000))));
check("a tight one is coloured differently again",
  /data-tight="1"/.test(contextBar(ctxOf(800_000))));

/* What a compaction did has to be reported from its own frames: the percentage
   above it is taken from the last request the model answered, so it does not
   move until the session is next used, and a button that visibly changes nothing
   reads as a button that did nothing. */
setFeed({ ctx: { compact: { running: false, ok: true, before: 24071, after: 3661, trigger: "manual" } } });
check("what the last compaction saved is said out loud",
  /compacted 24k → 3,661/.test(contextBar(ctxOf(550_000))),
  (contextBar(ctxOf(550_000)).match(/ctx__said[^>]*>([^<]*)</) || [])[1] || "");
setFeed({ ctx: { compact: { running: true } } });
const midway = contextBar(ctxOf(550_000));
check("one under way says so and cannot be pressed again",
  /Compacting…/.test(midway) && /disabled/.test(midway));

/* And while it runs the bar reports the compaction rather than the conversation.
   Nothing on the wire says how far along it is — the pipe sends `compacting`,
   then silence — so the figure is elapsed time bent through the terminal's own
   curve, and the test that matters is the last one: it never reaches 100. */
check("the curve starts at nothing", compactPct(0) === 0);
check("and is most of the way there after a minute and a half",
  compactPct(90) === 63, String(compactPct(90)));
check("and never claims to have finished, however long it takes",
  compactPct(60 * 60 * 24) === 95, String(compactPct(60 * 60 * 24)));
setFeed({ ctx: { compact: { running: true, at: Date.now() / 1000 - 90 } } });
const going = contextBar(ctxOf(550_000));
check("a compaction under way fills the bar instead of the reading",
  /data-going="1"/.test(going) && /width: 63%/.test(going),
  (going.match(/style="width: [^"]*"/) || [])[0] || "");
check("and says how far along beside it",
  /compacting… 63%/.test(going),
  (going.match(/ctx__said[^>]*>([^<]*)</) || [])[1] || "");
check("and hands the clock a hook to walk it forward between polls",
  /data-compact-since="/.test(going));
check("while the reading underneath still says what it said",
  /55% of/.test(going));
check("and the bar does not also wear the conversation's colour",
  /data-tight="0"/.test(going));

/* The word for it, which was the whole complaint: compaction is a turn like any
   other from the pipe's side, so on `status` alone every compacting session read
   as Working — the one thing it is not doing. */
setFeed({ ctx: { here: true, running: true, compact: { running: true, at: 0 } } });
check("a compacting session is called what it is doing",
  drawnStateOf({ sessionId: "ctx", status: "busy" }).label === "Compacting",
  drawnStateOf({ sessionId: "ctx", status: "busy" }).label);
setFeed({ ctx: { here: true, running: true, compact: { running: false, ok: true } } });
check("and goes back to Working when it is a turn again",
  drawnStateOf({ sessionId: "ctx", status: "busy" }).label === "Working");
check("but is never a state to filter or count by — only one to draw",
  !STATE_ORDER.includes("compacting") && STATE.compacting.label === "Compacting");
setFeed({ ctx: { compact: { running: false, ok: false, message: "Not enough messages to compact." } } });
check("and one that refused is reported as a refusal, not a success",
  /did not compact/.test(contextBar(ctxOf(550_000)))
  && /data-bad="1"/.test(contextBar(ctxOf(550_000))));

/* Slash commands, and the fact that whether one works is a question about the
   transport rather than about the command.

   The two lists below are verbatim from a real `init` frame down a held pipe
   (Claude Code 2.1.239). The panel's own list is the one it uses for a session
   in a terminal, where an injected message is queued with slash commands
   switched off and every one of them is inert. */
const SESSION_SAYS = {                      // what a held session reports
  available: ["clear", "color", "compact", "config", "context", "doctor", "model", "usage"],
  terminalOnly: ["doctor", "color"],
};
const PANEL_GUESSES = {                     // what the panel assumes otherwise
  entries: [{ name: "compact", kind: "command", description: "" }],
  terminalOnly: ["clear", "compact", "context", "model", "config", "doctor"],
};
setCatalog(PANEL_GUESSES);
const inTerm = { sessionId: "term", status: "idle", alive: true };
const ofOurs = { sessionId: "ours", status: "idle", alive: false };
setFeed({ term: {}, ours: { here: true, running: true, commands: SESSION_SAYS } });

check("a terminal session still keeps /compact to itself",
  terminalOnly("compact", inTerm) === true);
/* The bug this fixes: the panel refused /compact on the one kind of session
   where compacting actually works. */
check("but a session the panel runs does not",
  terminalOnly("compact", ofOurs) === false);
check("and the two it really does keep are still kept",
  terminalOnly("doctor", ofOurs) === true && terminalOnly("color", ofOurs) === true);
/* The one that was wrong in practice. A held process emits nothing until it is
   sent something, so a session brought up and not yet typed at has no list —
   which is exactly when you would reach for /compact. Falling back to the socket
   list there refused it on a session where it works. Whether a command is
   expanded is settled by the transport; only the exceptions need the session. */
setFeed({ ...({ term: {}, ours: { here: true, running: true, commands: SESSION_SAYS } }),
          fresh: { here: true, running: true } });
const unspoken = { sessionId: "fresh", status: "idle", alive: false };
check("a held session that has not spoken yet still takes /compact",
  terminalOnly("compact", unspoken) === false);
check("and still keeps the two that are terminal-only down a pipe",
  terminalOnly("doctor", unspoken) === true);
check("and it goes in as typed, not turned into a sentence",
  sentAs("/compact", unspoken) === "/compact", sentAs("/compact", unspoken));
/* A session in a terminal has not changed: the socket list is right for it. */
check("while a session in a terminal keeps falling back to the panel's list",
  terminalOnly("compact", { sessionId: "term", status: "idle", alive: true }) === true);

/* The other half. A held pipe expands slash commands itself, so rewriting one
   into prose would be the panel talking over you — "Use the compact command."
   reads as a request where `/compact` compacts. */
check("what you typed is what a session of ours gets",
  sentAs("/compact", ofOurs) === "/compact",
  sentAs("/compact", ofOurs));
check("with its arguments intact",
  sentAs("/model opus", ofOurs) === "/model opus", sentAs("/model opus", ofOurs));
check("while a terminal session still gets it turned into a sentence",
  sentAs("/compact", inTerm) === "Use the compact command.", sentAs("/compact", inTerm));
check("and plain text is left alone either way",
  sentAs("just a message", ofOurs) === "just a message");

check("a command the session lists is known",
  knownCommand("context", ofOurs) === true);
check("one it does not list is not",
  knownCommand("nonesuch", ofOurs) === false);
check("and before it has said anything, nothing is ruled out",
  knownCommand("nonesuch", { sessionId: "quiet", status: "stopped", alive: false }) === true);

for (const name of CALLED) {
  const declared = new RegExp(`(?:^|\\n)\\s*(?:async\\s+)?(?:function|const|let)\\s+${name}\\b`).test(page);
  if (!declared) check(`${name} is defined, not just called`, false);
}
check(`every name the page leans on is defined (${CALLED.length} checked)`,
  CALLED.every((name) => new RegExp(`(?:^|\\n)\\s*(?:async\\s+)?(?:function|const|let)\\s+${name}\\b`).test(page)));

console.log(failures ? `\n${failures} failed` : "\nall ok");
process.exit(failures ? 1 : 0);

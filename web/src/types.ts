/* The shapes the server sends, written down.

   Hand-written from the Python that emits them, which is the only place the
   wire format existed until now:

     Feed, Session          watchtower/store.py     — SessionStore.snapshot
     Owned                  watchtower/owned.py     — owned_state
     Question               watchtower/transcript.py — question_asked
     Transcript, Message    watchtower/transcript.py — read_transcript
     Change                 watchtower/transcript.py — read_change
     Usage, ModelRow        watchtower/usage.py     — read_usage
     Plan, PlanLimit        watchtower/plan.py      — parse_plan
     Update, ReleaseNote    watchtower/update.py    — update_state
     Git, GitFile, Commit   watchtower/git/read.py  — read_git
     Catalog, CatalogEntry  watchtower/catalog.py   — read_catalog

   Two rules, because both were broken by guessing rather than reading:

   - A field the server can leave out is optional (`?`); a field it always
     sends but may send as null is `| null`. They are not the same thing and
     the panel treats them differently — `question` is always there and often
     null, `window` is absent on a row that has no window at all.
   - Every name here is the JSON name. The Python is snake_case internally and
     camelCase on the wire; this file is the wire, so it is camelCase only.
*/

/* ------------------------------------------------------------------ sessions */

/** What a row's lamp shows. `stopped` is a kept row with no process behind it;
    `offline` is a process that has gone while the row was still live. */
export type Status = "waiting" | "busy" | "shell" | "idle" | "offline" | "stopped";

/** As the transcript writes it. `default` is what Claude Code records for the
    mode its CLI calls `manual` — see docs/live-permission-mode.md. */
export type PermissionMode =
  | "default" | "acceptEdits" | "plan" | "bypassPermissions" | "auto" | "dontAsk";

export interface Span {
  from: number;
  to: number;
  status: Status;
}

export interface WindowMatch {
  id: string;
  /** `paired` was picked by hand, `identified` was matched by the probe. */
  confidence: "paired" | "identified" | string;
  title: string;
}

export interface Activity {
  role: "assistant" | "user";
  text: string | null;
  mtime: number;
}

export interface Context {
  tokens: number;
  window: number;
  /** 0..1 — how full, already clamped by the server. */
  share: number;
  model: string | null;
}

export interface QuestionOption {
  label: string;
  description: string;
}

export interface QuestionItem {
  question: string;
  header: string;
  multiSelect: boolean;
  options: QuestionOption[];
}

/** The AskUserQuestion a session is standing at. */
export interface Question {
  toolUseId: string;
  questions: QuestionItem[];
}

export interface Session {
  sessionId: string;
  pid: number | null;
  /** The name shown: `givenName` if one was typed, else `defaultName`. */
  name: string;
  givenName: string | null;
  defaultName: string;
  cwd: string;
  folder: string;
  status: Status;
  kind: string;
  version: string | null;
  startedAt: number;
  statusSince: number;
  branch: string | null;
  /** Null means "not a repository", which a null `branch` cannot say on its own. */
  repoRoot: string | null;
  activity: Activity | null;
  spoken: boolean;
  permissionMode: PermissionMode | null;
  title: string | null;
  context: Context | null;
  question: Question | null;
  trace: Span[];
  alive: boolean;
  canSay: boolean;
  /** Only on a kept row: whether it could be started back up from here. */
  canStart?: boolean;
  ancestors: number[];
  parentPid?: number | null;
  /** Filled in over the whole list, so it is absent until that pass runs. */
  parentName?: string | null;
  tty: string | null;
  host: (string | null)[];
  kept: boolean;
  pinned: boolean;
  window: WindowMatch | null;
  /** How many subagents this session has going, absent when it has none.
      `newest` names one: its type and what it was asked to do. */
  agents?: { running: number; total: number; newest: string };
}

/** What the panel is running for a session, keyed by session id in the feed.
    Only the handful of sessions the panel has ever run a turn for appear. */
export interface Owned {
  mode: string;
  here: boolean;
  busy: boolean;
  since: number | null;
  last: OwnedResult | null;
  ask: OwnedAsk | null;
  queued: string[];
  /** Held back by a stop: still queued, and going nowhere until you say. */
  queueHeld: boolean;
  stopping: boolean;
  compact: OwnedCompact | null;
  /** What this session says it can be asked for, once it has said anything.
      Null until then — see the note on a held pipe in the README. */
  commands: OwnedCommands | null;
  running: boolean;
}

/** A session's own answer about its slash commands, which beats the panel's
    guess. `terminalOnly` are the ones that need a real terminal, so the panel
    offers none of them. */
export interface OwnedCommands {
  available: string[];
  terminalOnly: string[];
}

export interface OwnedResult {
  at: number;
  ok: boolean;
  message: string;
  mode: string;
}

/** The prompt a panel-run turn is standing on: a permission gate, or a
    question, which is the same channel with `asks` set. Answering a question
    *is* allowing it — see request_permission in watchtower/owned.py. */
export interface OwnedAsk {
  requestId: string;
  tool: string;
  name: string;
  what: string;
  /** The tool's own arguments. For an AskUserQuestion this is where the
      questions are, which is why it is typed rather than opaque. */
  input: { questions?: QuestionItem[] } & Record<string, unknown>;
  asks: boolean;
  at: number;
  /** How long the turn will wait before refusing on its own. */
  seconds: number;
}

/** The last compaction of a panel-run session, running or finished. Only `at`
    and `running` are set while it runs; the rest arrive with the result, and
    `before`/`after` only when the session reported them. */
export interface OwnedCompact {
  at: number;
  running: boolean;
  ok?: boolean;
  message?: string;
  before?: number;
  after?: number;
  /** `manual` when it was asked for here, `auto` when the session did it. */
  trigger?: string;
}

/** GET /api/state — the whole dashboard in one object. */
export interface Feed {
  now: number;
  sessions: Session[];
  historySeconds: number;
  canFocus: boolean;
  canSend: boolean;
  canPickFolder?: boolean;
  /** Partial, and not because the server sends a partial one: picking a mode
      patches the entry it already has so the chips move under your finger
      before the next poll confirms it. See pickMode in views/owned.ts. */
  owned?: Record<string, Partial<Owned>>;
}

/* --------------------------------------------------------------- conversation */

/** The preview of a file change, carried on the tool call that made it. */
export interface ChangePreview {
  id: string;
  path?: string;
  text?: string;
  added?: number;
  removed?: number;
  clipped?: boolean;
  more?: number;
}

export interface ToolCall {
  name: string;
  detail: string;
  /** Only on a call that changed a file; absent on every other one. */
  change?: ChangePreview;
  /** Only on a Task/Agent call, naming the subagent it started — which is what
      makes the row openable. Absent on every other call. */
  agent?: {
    agentId: string;
    agentType: string;
    state: "running" | "done" | "stopped";
  };
}

export interface Message {
  role: "assistant" | "user";
  at: number | string | null;
  text: string;
  tools: ToolCall[];
  /** Who sent it, for a message that came in over the socket, or
      `answered here` for a question answered in the terminal. */
  from?: string;
}

/** GET /api/transcript */
export interface Transcript {
  sessionId: string;
  title: string | null;
  messages: Message[];
  truncated: boolean;
  path: string | null;
}

/** GET /api/subagent — one subagent's conversation, in the shape the chat
    already draws, with the meta on top. */
export interface Subagent extends Transcript {
  ok: boolean;
  message?: string;
  agentId: string;
  agentType: string;
  description: string;
  spawnDepth: number;
  state: "running" | "done" | "stopped";
  model?: string;
}

/** GET /api/change — the whole of one change, by its tool-use id. */
export interface Change {
  ok: boolean;
  id: string;
  path: string;
  text: string;
  added: number;
  removed: number;
  clipped: boolean;
  message?: string;
}

/* --------------------------------------------------------------------- usage */

export interface Counters {
  requests: number;
  input: number;
  output: number;
  thinking: number;
  cacheWrite5m: number;
  cacheWrite1h: number;
  cacheRead: number;
  webSearch: number;
}

export interface ModelRow extends Counters {
  model: string;
  cost: number | null;
  priced: boolean;
}

/** GET /api/usage */
export interface Usage {
  ok: boolean;
  sessionId: string;
  models: ModelRow[];
  agentModels: ModelRow[];
  totals: Counters;
  cost: number;
  unpriced: string[];
  context: number | null;
  contextModel: string | null;
  contextWindow: number;
  contextAt: number | null;
  firstAt: number | null;
  lastAt: number | null;
  path: string | null;
  message?: string;
}

/* ---------------------------------------------------------------------- plan */

export interface PlanLimit {
  name: string;
  /** 0..100, already clamped by the server. */
  percent: number;
  resets: string;
}

export interface PlanBlock {
  title: string;
  lines: string[];
}

/** GET /api/plan — parsed out of `claude --print /usage`, so every field is
    best-effort and `ok: false` carries only `message`. */
export interface Plan {
  ok: boolean;
  headline?: string;
  limits?: PlanLimit[];
  blocks?: PlanBlock[];
  text?: string;
  /** True while the answer is still being read; the cached one comes with it. */
  reading?: boolean;
  message?: string;
}

/* -------------------------------------------------------------------- update */

export interface ReleaseNote {
  tag: string;
  at: number;
  subject: string;
  body: string;
}

/** How many sessions a restart would take down with it. */
export interface RunningHere {
  here: number;
  busy: number;
  compacting: number;
  queued: number;
  names: string[];
}

/** GET /api/update */
export interface Update {
  ok: boolean;
  repo: boolean;
  checking: boolean;
  current?: string;
  described?: string;
  latest?: string;
  latestAt?: number;
  behind?: number;
  branch?: string;
  detached?: boolean;
  dirty?: boolean;
  defaultBranch?: string;
  ahead?: number;
  canUpdate: boolean;
  /** Why not, when `canUpdate` is false and it is not simply up to date. */
  why?: string;
  notes?: ReleaseNote[];
  restart?: string;
  message?: string;
  at?: number;
  running?: RunningHere;
}

/* ----------------------------------------------------------------------- git */

export interface GitFile {
  path: string;
  origPath: string | null;
  /** The porcelain-v2 status letter, or null where there is none on that side. */
  staged: string | null;
  unstaged: string | null;
  untracked: boolean;
  conflicted: boolean;
}

export interface Commit {
  sha: string;
  short: string;
  parents: string[];
  author: string;
  at: number;
  refs: string[];
  subject: string;
}

export interface LocalBranch {
  name: string;
  upstream: string | null;
  at: number;
  current: boolean;
}

export interface RemoteBranch {
  name: string;
  at: number;
  /** The name without its remote, which is what checking it out means. */
  short: string;
}

export interface Branches {
  local: LocalBranch[];
  remote: RemoteBranch[];
}

/** GET /api/git */
export interface Git {
  ok: boolean;
  repoRoot: string;
  head: string | null;
  branch: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  detached: boolean;
  files: GitFile[];
  commits: Commit[];
  stashes: number;
  gitAvailable: boolean;
  branches?: Branches;
  message?: string;
}

/** GET /api/git/diff — one file, one side. */
export interface Diff {
  ok: boolean;
  path: string;
  staged: boolean;
  text: string;
  binary: boolean;
  message?: string;
}

/* ------------------------------------------------------------------- catalog */

export interface CatalogEntry {
  name: string;
  description: string;
  source: string;
  kind: "skill" | "command";
}

/** GET /api/commands */
export interface Catalog {
  ok?: boolean;
  entries?: CatalogEntry[];
  /** Commands that only work at a real terminal, so the panel offers none. */
  terminalOnly?: string[];
  message?: string;
}

/* ------------------------------------------------------------------ POST'ing */

/** What every POST answers with. Each route adds its own fields on top; the
    panel only ever reads `ok` and `message` off the common part, and `run`
    returns `{}` when the request never reached the server. */
export interface Reply {
  ok?: boolean;
  message?: string;
  [key: string]: unknown;
}

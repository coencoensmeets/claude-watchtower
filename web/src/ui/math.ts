/* ==========================================================================
   TeX in a message, drawn as maths.

   Claude writes maths the way it writes anything else — `$S$`, `$$\frac{a}{b}$$`
   — and read raw it is worse than useless: a matrix comes out as a paragraph of
   backslashes. So the panel renders it.

   Rendered to **MathML**, and by hand. Both halves of that are the decision:

   - MathML is what every browser this panel runs in already draws. Chromium has
     had MathML Core since 109, Firefox and Safari for far longer. Nothing is
     downloaded, no fonts are shipped, and the output is text the browser lays
     out itself — which is also why it reflows on a phone and scales with the
     page rather than being a picture of an equation.
   - By hand, because the alternative is KaTeX: 280KB of script and about a
     megabyte of fonts. The panel's entire assets folder is 156KB. A dependency
     ten times the size of the thing it is being added to, fetched at install
     time by a project whose promise is that `python3 server.py` is the only
     command anyone types, is not a trade worth making for the subset of TeX a
     chat message actually contains.

   That subset is what is here: fractions, roots, sub- and superscripts,
   matrices and cases, delimiters, accents, spacing, the greek alphabet, the
   operators and arrows, `\text` and its family, `\mathbb`, `\underbrace`, sums
   and integrals with their limits. It is not TeX and does not pretend to be.
   What it does not understand it hands back unrendered rather than mangling —
   see `renderMath`, where a failure returns the source as code. A message is
   worth more than the maths in it.
   ========================================================================== */

import { escapeHtml } from "./format.js";

/* ------------------------------------------------------------------ symbols */
const GREEK: Record<string, string> = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ϵ", varepsilon: "ε",
  zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", varpi: "ϖ", rho: "ρ",
  varrho: "ϱ", sigma: "σ", varsigma: "ς", tau: "τ", upsilon: "υ", phi: "ϕ",
  varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

/* Everything that is drawn as an operator: relations, arrows, the binary
   operators, and the punctuation that has a name. */
const OPS: Record<string, string> = {
  times: "×", cdot: "⋅", div: "÷", pm: "±", mp: "∓", ast: "∗", star: "⋆",
  circ: "∘", bullet: "∙", oplus: "⊕", ominus: "⊖", otimes: "⊗", odot: "⊙",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠", equiv: "≡",
  approx: "≈", simeq: "≃", sim: "∼", cong: "≅", propto: "∝", ll: "≪", gg: "≫",
  in: "∈", notin: "∉", ni: "∋", subset: "⊂", subseteq: "⊆", supset: "⊃",
  supseteq: "⊇", cup: "∪", cap: "∩", setminus: "∖", emptyset: "∅",
  varnothing: "∅", forall: "∀", exists: "∃", nexists: "∄", neg: "¬",
  land: "∧", lor: "∨", wedge: "∧", vee: "∨",
  to: "→", rightarrow: "→", longrightarrow: "⟶", Rightarrow: "⇒",
  Longrightarrow: "⟹", leftarrow: "←", longleftarrow: "⟵", Leftarrow: "⇐",
  leftrightarrow: "↔", Leftrightarrow: "⇔", mapsto: "↦", longmapsto: "⟼",
  uparrow: "↑", downarrow: "↓", nearrow: "↗", searrow: "↘",
  infty: "∞", partial: "∂", nabla: "∇", top: "⊤", bot: "⊥", perp: "⊥",
  angle: "∠", parallel: "∥", mid: "∣", colon: ":", semicolon: ";",
  cdots: "⋯", ldots: "…", dots: "…", vdots: "⋮", ddots: "⋱", prime: "′",
  degree: "°", deg: "°", aleph: "ℵ", hbar: "ℏ", ell: "ℓ", Re: "ℜ", Im: "ℑ",
  surd: "√", triangle: "△", square: "□", diamond: "⋄", dagger: "†",
  leq_slant: "⩽", models: "⊨", vdash: "⊢", implies: "⟹", iff: "⟺",
};

/* Drawn large, and — in a display — with their limits above and below rather
   than beside. That is the whole reason they are their own table. */
const BIG: Record<string, string> = {
  sum: "∑", prod: "∏", coprod: "∐", int: "∫", iint: "∬", iiint: "∭",
  oint: "∮", bigcup: "⋃", bigcap: "⋂", bigoplus: "⨁", bigotimes: "⨂",
  bigvee: "⋁", bigwedge: "⋀", lim: "lim", limsup: "lim sup", liminf: "lim inf",
  max: "max", min: "min", sup: "sup", inf: "inf", argmax: "arg max", argmin: "arg min",
};

/* Upright, and spaced as a function name rather than as a product of letters —
   which is what `<mi>` with more than one character already means in MathML. */
const FUNCS = new Set([
  "log", "ln", "lg", "exp", "sin", "cos", "tan", "sec", "csc", "cot",
  "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth", "det", "dim",
  "ker", "gcd", "hom", "arg", "Pr", "tr", "rank", "diag", "sgn", "erf",
]);

const DELIMS: Record<string, string> = {
  "{": "{", "}": "}", "|": "‖", "\\|": "‖",
  lbrace: "{", rbrace: "}", langle: "⟨", rangle: "⟩",
  lVert: "‖", rVert: "‖", lvert: "|", rvert: "|", Vert: "‖", vert: "|",
  lfloor: "⌊", rfloor: "⌋", lceil: "⌈", rceil: "⌉", lbrack: "[", rbrack: "]",
  backslash: "\\",
};

/* TeX's spaces, in the em widths TeX gives them. */
const SPACES: Record<string, string> = {
  ",": "0.167em", ":": "0.222em", ">": "0.222em", ";": "0.278em", "!": "-0.167em",
  " ": "0.25em", quad: "1em", qquad: "2em", enspace: "0.5em",
  thinspace: "0.167em", medspace: "0.222em", thickspace: "0.278em", hspace: "1em",
};

/* What goes over the letter. `\overline` and `\overrightarrow` stretch; the
   rest sit above a single symbol and do not. */
const ACCENTS: Record<string, [string, boolean]> = {
  hat: ["^", false], widehat: ["^", true], check: ["ˇ", false],
  tilde: ["~", false], widetilde: ["~", true], bar: ["¯", false],
  overline: ["‾", true], underline: ["_", true], vec: ["→", false],
  dot: ["˙", false], ddot: ["¨", false], dddot: ["⃛", false],
  overbrace: ["⏞", true], underbrace: ["⏟", true],
  overrightarrow: ["→", true], overleftarrow: ["←", true],
};

/* `\mathbb{R}` is ℝ, and the double-struck letters are scattered across two
   Unicode blocks: the seven that were standardised early sit among the letterlike
   symbols, and the rest are in the mathematical alphanumerics. */
const BLACKBOARD: Record<string, string> = { C: "ℂ", H: "ℍ", N: "ℕ", P: "ℙ", Q: "ℚ", R: "ℝ", Z: "ℤ" };
const blackboard = (letter: string): string => BLACKBOARD[letter]
  ?? (/^[A-Z]$/.test(letter) ? String.fromCodePoint(0x1d538 + letter.charCodeAt(0) - 65)
    : /^[a-z]$/.test(letter) ? String.fromCodePoint(0x1d552 + letter.charCodeAt(0) - 97)
      : letter);

/* The families `\mathrm` and friends put their argument in. `text` is prose and
   keeps its spaces; the others are letters in a different face. */
const VARIANTS: Record<string, string> = {
  mathrm: "normal", mathbf: "bold", mathit: "italic", mathsf: "sans-serif",
  mathtt: "monospace", mathcal: "script", mathscr: "script", mathfrak: "fraktur",
  mathbb: "double-struck",
  boldsymbol: "bold-italic", bm: "bold-italic", mathnormal: "italic",
};
const TEXTUAL = new Set(["text", "textrm", "textbf", "textit", "texttt", "textsf", "mbox", "operatorname"]);

/* Delimiter sizes, which are all treated as "draw it plainly": the browser
   stretches what needs stretching inside \left…\right, and outside it \big[ is
   a bracket like any other. */
const SIZED = new Set(["big", "Big", "bigg", "Bigg", "bigl", "Bigl", "biggl", "Biggl",
  "bigr", "Bigr", "biggr", "Biggr", "bigm", "Bigm", "left", "right", "middle"]);

/* ---------------------------------------------------------------- tokenising */
type Token =
  | { t: "cmd"; v: string } | { t: "chr"; v: string } | { t: "num"; v: string }
  | { t: "arg"; v: string }
  | { t: "{" } | { t: "}" } | { t: "^" } | { t: "_" } | { t: "&" };

/* Commands whose argument is prose rather than maths. Their braces are read
   here, in the tokeniser, and not a moment later: the tokeniser is the last
   place that still has the spaces in `\text{base frame}`, since everything
   after it works on tokens and a token boundary is where a space went. */
const VERBATIM = new Set([...TEXTUAL, ...Object.keys(VARIANTS), "begin", "end"]);

function tokenise(src: string): Token[] {
  const out: Token[] = [];
  for (let i = 0; i < src.length;) {
    const c = src[i];
    if (c === "\\") {
      const word = /^[A-Za-z]+/.exec(src.slice(i + 1));
      if (word) {
        out.push({ t: "cmd", v: word[0] });
        i += 1 + word[0].length;
        if (VERBATIM.has(word[0]) && src[i] === "{") {
          let depth = 0;
          let end = i;
          for (; end < src.length; end++) {
            if (src[end] === "{") depth++;
            else if (src[end] === "}" && --depth === 0) break;
          }
          out.push({ t: "arg", v: src.slice(i + 1, end) });
          i = end + 1;
          continue;
        }
        // TeX eats the space after a control word, which is what makes
        // `\hat z` and `\hatz` different things.
        while (src[i] === " ") i++;
        continue;
      }
      out.push({ t: "cmd", v: src[i + 1] ?? "" });
      i += 2;
      continue;
    }
    if (c === "{" || c === "}" || c === "^" || c === "_" || c === "&") {
      out.push({ t: c } as Token);
      i++;
      continue;
    }
    if (/\s/.test(c)) { i++; continue; }
    const num = /^\d+(?:[.,]\d+)*/.exec(src.slice(i));
    if (num) { out.push({ t: "num", v: num[0] }); i += num[0].length; continue; }
    out.push({ t: "chr", v: c });
    i++;
  }
  return out;
}

/* ------------------------------------------------------------------ parsing */
/* An atom is a piece of MathML with two things remembered about it: whether a
   script attached to it belongs above and below rather than beside (`\sum`,
   `\lim`, an under-brace), and whether it is a bare delimiter, which is what
   lets `\left(` stretch to the height of what it holds. */
interface Atom { ml: string; limits?: boolean }

const mi = (s: string, variant?: string) =>
  `<mi${variant ? ` mathvariant="${variant}"` : ""}>${escapeHtml(s)}</mi>`;
const mo = (s: string, extra = "") => `<mo${extra}>${escapeHtml(s)}</mo>`;
const mn = (s: string) => `<mn>${escapeHtml(s)}</mn>`;
const mrow = (parts: string[]) => (parts.length === 1 ? parts[0] : `<mrow>${parts.join("")}</mrow>`);

class Parser {
  private at = 0;
  private readonly ts: Token[];

  // Written out rather than as a parameter property: the build is Node's own
  // type stripping, which blanks types in place and cannot synthesise the
  // assignment a `constructor(private ts)` implies.
  constructor(ts: Token[]) {
    this.ts = ts;
  }

  /** Everything up to the end, or to a closing brace this level did not open. */
  parse(stopAtEnd = false): string {
    const out: Atom[] = [];
    while (this.at < this.ts.length) {
      const token = this.ts[this.at];
      if (token.t === "}") break;
      if (token.t === "cmd" && stopAtEnd && (token.v === "end" || token.v === "right")) break;
      if (token.t === "&" || (token.t === "cmd" && token.v === "\\")) break;
      const atom = this.atom();
      if (atom) out.push(this.scripts(atom));
    }
    return mrow(out.map((a) => a.ml)) || "<mrow></mrow>";
  }

  /** `^` and `_` after an atom, in either order, at most one of each. */
  private scripts(base: Atom): Atom {
    let sup = "";
    let sub = "";
    for (;;) {
      const token = this.ts[this.at];
      if (!token || (token.t !== "^" && token.t !== "_")) break;
      this.at++;
      const arg = this.group();
      if (token.t === "^") sup = arg; else sub = arg;
    }
    if (!sup && !sub) return base;
    const kind = base.limits
      ? (sup && sub ? "munderover" : sup ? "mover" : "munder")
      : (sup && sub ? "msubsup" : sup ? "msup" : "msub");
    const inner = base.limits && sup && !sub ? [base.ml, sup]
      : sup && sub ? [base.ml, sub, sup]
        : [base.ml, sup || sub];
    return { ml: `<${kind}>${inner.join("")}</${kind}>` };
  }

  /** One argument: a braced group, or the single atom that follows. */
  private group(): string {
    const token = this.ts[this.at];
    if (!token) return "<mrow></mrow>";
    if (token.t === "{") {
      this.at++;
      const inner = this.parse();
      if (this.ts[this.at]?.t === "}") this.at++;
      return inner;
    }
    const atom = this.atom();
    return atom ? atom.ml : "<mrow></mrow>";
  }

  /** One argument as it was written — see VERBATIM, which is what takes it. */
  private rawGroup(): string {
    const token = this.ts[this.at];
    if (!token) return "";
    this.at++;
    if (token.t === "arg") return token.v;
    return "v" in token ? token.v : "";
  }

  private atom(): Atom | null {
    const token = this.ts[this.at];
    if (!token) return null;
    this.at++;
    switch (token.t) {
      case "num": return { ml: mn(token.v) };
      case "chr": return { ml: this.character(token.v) };
      case "{": {
        const inner = this.parse();
        if (this.ts[this.at]?.t === "}") this.at++;
        return { ml: `<mrow>${inner}</mrow>` };
      }
      case "cmd": return this.command(token.v);
      default: return null;
    }
  }

  private character(c: string): string {
    if (/[A-Za-z]/.test(c)) return mi(c);
    if (c === "'") return mo("′");
    // A bracket typed as itself does not stretch — only `\left(` and a matrix
    // fence do. Without this, the `(q)` in `a(q) = [matrix]` grows to the
    // height of the matrix beside it, because MathML's own dictionary says
    // brackets are stretchy and the row is what it stretches against.
    if ("()[]{}|".includes(c)) return mo(c, ' stretchy="false"');
    return mo(c);
  }

  private command(name: string): Atom | null {
    // Sizes and \left…\right: the delimiter that follows is what matters, and
    // MathML stretches it against its row without being told the size.
    if (SIZED.has(name)) {
      const next = this.ts[this.at];
      if (!next) return null;
      this.at++;
      const glyph = next.t === "cmd" ? (DELIMS[next.v] ?? OPS[next.v] ?? next.v)
        : "v" in next ? next.v : "";
      if (glyph === ".") return { ml: "" };                 // \left. is nothing
      return {
        ml: mo(glyph, name === "left" || name === "right"
          ? ' stretchy="true" symmetric="true"' : ' stretchy="false"'),
      };
    }
    if (name === "begin") return this.environment();
    if (name === "\\") return null;

    if (GREEK[name]) return { ml: mi(GREEK[name]) };
    if (OPS[name]) return { ml: mo(OPS[name]) };
    if (DELIMS[name]) return { ml: mo(DELIMS[name], ' stretchy="false"') };
    if (SPACES[name] !== undefined) return { ml: `<mspace width="${SPACES[name]}"/>` };
    if (BIG[name]) return { ml: mo(BIG[name], ' movablelimits="false"'), limits: true };
    if (FUNCS.has(name)) return { ml: mi(name) };

    if (name === "frac" || name === "dfrac" || name === "tfrac" || name === "cfrac") {
      const top = this.group();
      const bottom = this.group();
      const style = name === "tfrac" ? ' displaystyle="false"' : "";
      return { ml: `<mfrac${style}>${top}${bottom}</mfrac>` };
    }
    if (name === "binom" || name === "dbinom") {
      const top = this.group();
      const bottom = this.group();
      return { ml: `<mrow>${mo("(")}<mfrac linethickness="0">${top}${bottom}</mfrac>${mo(")")}</mrow>` };
    }
    if (name === "sqrt") {
      // An optional index in brackets, which is the one place TeX uses them.
      if (this.ts[this.at]?.t === "chr" && (this.ts[this.at] as { v: string }).v === "[") {
        this.at++;
        const parts: string[] = [];
        while (this.at < this.ts.length) {
          const next = this.ts[this.at];
          if (next.t === "chr" && next.v === "]") { this.at++; break; }
          const atom = this.atom();
          if (atom) parts.push(atom.ml); else break;
        }
        return { ml: `<mroot>${this.group()}${mrow(parts)}</mroot>` };
      }
      return { ml: `<msqrt>${this.group()}</msqrt>` };
    }
    if (ACCENTS[name]) {
      const [glyph, stretchy] = ACCENTS[name];
      const under = name === "underbrace" || name === "underline";
      const base = this.group();
      const tag = under ? "munder" : "mover";
      return {
        ml: `<${tag} accent="true">${base}${mo(glyph, stretchy ? ' stretchy="true"' : ' stretchy="false"')}</${tag}>`,
        // `\underbrace{x}_{y}` puts the label under the brace, not beside it.
        limits: name === "underbrace" || name === "overbrace",
      };
    }
    if (VARIANTS[name]) {
      const variant = VARIANTS[name];
      if (name === "mathbb") return { ml: mi(blackboard(this.rawGroup())) };
      const text = this.rawGroup();
      // A single letter stays an identifier; a word in a different face is one
      // token, not a product of its letters.
      return { ml: mi(text, variant) };
    }
    if (TEXTUAL.has(name)) {
      // A `\,` inside prose is a thin space, and `\texttt{…}` inside a `\text`
      // is somebody nesting faces — neither is worth a second parser, so the
      // one is a space and the other keeps its own argument.
      const text = this.rawGroup()
        .replace(/\\(?:text|mathrm|texttt|mathbf)\{([^}]*)\}/g, "$1")
        .replace(/\\[,;:!]/g, " ")
        .replace(/\\ /g, " ");
      if (name === "operatorname") return { ml: mi(text) };
      const style = name === "textbf" ? ' mathvariant="bold"'
        : name === "textit" ? ' mathvariant="italic"'
          : name === "texttt" ? ' mathvariant="monospace"' : "";
      return { ml: `<mtext${style}>${escapeHtml(text)}</mtext>` };
    }
    if (name === "boxed") {
      // MathML Core has no `menclose`, so the box is a border in the
      // stylesheet — see `.md-math .boxed`.
      return { ml: `<mrow class="boxed">${this.group()}</mrow>` };
    }
    if (name === "substack") return { ml: this.group() };
    if (name === "limits" || name === "nolimits" || name === "displaystyle"
        || name === "textstyle" || name === "scriptstyle" || name === "nonumber"
        || name === "notag" || name === "vphantom" || name === "mathstrut") {
      return null;
    }
    if (name === "phantom") { this.group(); return { ml: "<mspace width=\"0.5em\"/>" }; }
    if (name === "%" || name === "$" || name === "#" || name === "&" || name === "_") {
      return { ml: mo(name) };
    }
    // Anything unknown is shown as its own name rather than swallowed: a
    // message with one command this does not know is still worth reading.
    return { ml: mi(name) };
  }

  /* `\begin{bmatrix}…\end{bmatrix}` and its family, plus the aligned blocks,
     which are all one table with different fences around it. */
  private environment(): Atom {
    const kind = this.rawGroup();
    const rows: string[][] = [[]];
    while (this.at < this.ts.length) {
      const cell = this.parse(true);
      rows[rows.length - 1].push(cell);
      const token = this.ts[this.at];
      if (!token) break;
      if (token.t === "&") { this.at++; continue; }
      if (token.t === "cmd" && token.v === "\\") { this.at++; rows.push([]); continue; }
      if (token.t === "cmd" && token.v === "end") { this.at++; this.rawGroup(); break; }
      break;
    }
    // A trailing `\\` leaves an empty row, which would draw as a blank line.
    if (rows.length > 1 && rows[rows.length - 1].every((c) => !c || c === "<mrow></mrow>")) rows.pop();
    const align = kind.startsWith("align") || kind === "aligned" || kind === "cases"
      ? ' columnalign="left"' : "";
    const table = `<mtable${align}>${rows.map((cells) =>
      `<mtr>${cells.map((c) => `<mtd>${c}</mtd>`).join("")}</mtr>`).join("")}</mtable>`;
    const FENCE: Record<string, [string, string]> = {
      bmatrix: ["[", "]"], pmatrix: ["(", ")"], Bmatrix: ["{", "}"],
      vmatrix: ["|", "|"], Vmatrix: ["‖", "‖"], cases: ["{", ""],
    };
    const fence = FENCE[kind];
    if (!fence) return { ml: table };
    // Told to stretch, and told to stay centred on the row while it does.
    // Chrome will not grow a bracket to the height of a table beside it on the
    // strength of the operator dictionary alone, and a three-row matrix inside
    // two full stops of bracket is worse than no bracket at all.
    const grow = ' stretchy="true" symmetric="true"';
    return { ml: `<mrow>${mo(fence[0], grow)}${table}${fence[1] ? mo(fence[1], grow) : ""}</mrow>` };
  }
}

/* ------------------------------------------------------------------- drawing */
/** One piece of TeX as MathML, or as its own source if it cannot be read.

    `display` is the difference between `$…$` and `$$…$$`: a block of its own,
    centred, with sums and integrals drawn full size and their limits above and
    below. */
export function renderMath(tex: string, display: boolean): string {
  const source = String(tex ?? "").trim();
  if (!source) return "";
  try {
    const ml = new Parser(tokenise(source)).parse();
    return `<math class="md-math" display="${display ? "block" : "inline"}"`
      + ` xmlns="http://www.w3.org/1998/Math/MathML">${ml}</math>`;
  } catch {
    // Never the reason a message fails to draw. The TeX is shown as it was
    // written, which is what would have been on screen before any of this.
    return `<code class="md-mono md-math-raw">${escapeHtml(source)}</code>`;
  }
}

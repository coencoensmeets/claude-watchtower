// TeX in a message, drawn as MathML — no browser, no Claude.
//
// The converter is a few hundred lines of tokeniser and parser, and every one
// of the cases below came out of a real message. Two halves are checked: that
// the maths a message contains is found at all (which is a question about
// dollar signs in prose, and is the half that can quietly ruin ordinary text),
// and that what is found comes out as the MathML element it should be.
//
//     node tests/math-check.mjs
//
// A failure prints the case and exits 1.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const asJs = (text) => stripTypeScriptTypes(text, { mode: "strip" });

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".ts") ? [asJs(readFileSync(path, "utf8"))] : [];
  });
}
const page = sources(join(here, "..", "web", "src")).join("\n");
const unexport = (text) => text.replace(/(^|\n)export /g, "$1");

/* Everything lifted here is declared at the top level, so its closing brace is
   the first `}` in the first column. Counting braces instead would trip over
   the ones inside strings — `cases: ["{", ""]` is a real line in math.ts. */
function liftBlock(pattern, what) {
  const found = pattern.exec(page);
  if (!found) throw new Error(`${what} is not in the page any more`);
  const rest = page.slice(found.index);
  const end = rest.search(/\n\}\s*(\n|$)/);
  if (end < 0) throw new Error(`${what} does not close`);
  return unexport(rest.slice(0, end + 3));
}
const lift = (name) => liftBlock(new RegExp(`function ${name}\\s*\\(`), name);
const liftClass = (name) => liftBlock(new RegExp(`class ${name} \\{`), name);
/* A const may carry a type annotation, which stripping blanks in place — so the
   `=` is not next to the name any more. */
function liftConst(name) {
  const found = new RegExp(`const ${name}\\b[^=\\n]*=`).exec(page);
  if (!found) throw new Error(`${name} is not in the page any more`);
  const rest = page.slice(found.index);
  const end = rest.search(/;[ \t]*(\n|$)/);
  if (end < 0) throw new Error(`${name} does not end`);
  return unexport(rest.slice(0, end + 1));
}

const TABLES = ["GREEK", "OPS", "BIG", "FUNCS", "DELIMS", "SPACES", "ACCENTS",
  "BLACKBOARD", "blackboard", "VARIANTS", "TEXTUAL", "SIZED", "VERBATIM",
  "mi", "mo", "mn", "mrow"];

const { renderMath, renderMarkdown } = new Function(`
  const escapeHtml = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const app = { settings: { showEditor: true } };
  const ICON = { copy: "<svg/>" };
  ${TABLES.map(liftConst).join("\n")}
  ${lift("tokenise")}
  ${liftClass("Parser")}
  ${lift("renderMath")}
  ${["MD_HOLD", "PATH_RE", "TRAILING", "SUFFIX", "BARE_RE", "MATH_RE", "copyButton"].map(liftConst).join("\n")}
  ${lift("safeUrl")}
  ${lift("linkPaths")}
  ${lift("mark")}
  ${lift("outsideLinks")}
  ${lift("renderMarkdown")}
  return { renderMath, renderMarkdown };`)();

let failures = 0;
function check(what, ok, note = "") {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${note ? `  — ${note}` : ""}`);
  if (!ok) failures++;
}

const ml = (tex, display = false) => renderMath(tex, display);
/* What is on screen, with the tags taken off — the reading of the equation
   rather than its markup. */
const reads = (tex) => ml(tex).replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();

/* ============================== the shapes ================================ */
check("a fraction is a fraction", /<mfrac><mi>a<\/mi><mi>b<\/mi><\/mfrac>/.test(ml(String.raw`\frac{a}{b}`)),
  ml(String.raw`\frac{a}{b}`));
check("a superscript is msup", /<msup><mi>x<\/mi><mn>2<\/mn><\/msup>/.test(ml("x^2")));
check("a subscript is msub", /<msub><mi>a<\/mi><mn>0<\/mn><\/msub>/.test(ml("a_0")));
check("both at once is msubsup", /<msubsup>/.test(ml("S_{ij}^{2}")));
check("a braced script keeps all of itself", ml("R^{\\top}").includes("⊤"));
check("a root is msqrt", /<msqrt>/.test(ml(String.raw`\sqrt{x}`)));
check("an nth root is mroot", /<mroot>/.test(ml(String.raw`\sqrt[3]{x}`)));
check("greek is the letter, not its name", reads(String.raw`\omega\theta\Sigma`) === "ωθΣ", reads(String.raw`\omega\theta\Sigma`));
check("the operators are their symbols", reads(String.raw`a \in b \times c \approx d \to e`) === "a∈b×c≈d→e",
  reads(String.raw`a \in b \times c \approx d \to e`));
check("blackboard R is ℝ", reads(String.raw`\mathbb{R}`) === "ℝ");
check("and a letter with no letterlike form still gets one", reads(String.raw`\mathbb{A}`) === "𝔸");
check("a hat sits over its letter", /<mover accent="true"><mi>z<\/mi><mo[^>]*>\^<\/mo><\/mover>/.test(ml(String.raw`\hat z`)),
  ml(String.raw`\hat z`));
check("a dot too", /<mover/.test(ml(String.raw`\dot q`)) && reads(String.raw`\dot q`).includes("˙"));
check("text is text, and keeps its spaces", ml(String.raw`\text{robot-tool, base frame}`)
  .includes("<mtext>robot-tool, base frame</mtext>"), ml(String.raw`\text{robot-tool, base frame}`));
check("texttt is text in the other face", /<mtext mathvariant="monospace">qRobot<\/mtext>/.test(ml(String.raw`\texttt{qRobot}`)));
check("mathrm is upright", /mathvariant="normal"/.test(ml(String.raw`R_{\mathrm{rel}}`)));
check("a function name is upright by being more than one letter",
  ml(String.raw`\log R`).includes("<mi>log</mi>"), ml(String.raw`\log R`));
check("the spaces are spaces", (ml(String.raw`a \; b \quad c`).match(/<mspace/g) || []).length === 2);
check("a sum takes its limits above and below in a display",
  /<munderover>/.test(ml(String.raw`\sum_{i=1}^{n} i`, true)), ml(String.raw`\sum_{i=1}^{n} i`, true));

/* A matrix is the case that reads worst unrendered, and the one the message
   this was built for is full of. */
const matrix = ml(String.raw`\begin{bmatrix} a_0\\ a_1\\ a_2\end{bmatrix}`);
check("a bmatrix is a table", /<mtable>/.test(matrix), matrix.slice(0, 120));
check("with a row per line", (matrix.match(/<mtr>/g) || []).length === 3, matrix);
check("and square brackets around it", matrix.includes("[") && matrix.includes("]"));
check("a trailing row separator does not draw an empty row",
  (ml(String.raw`\begin{bmatrix} a\\ b\\ \end{bmatrix}`).match(/<mtr>/g) || []).length === 2);
const cells = ml(String.raw`\begin{bmatrix} a & b \\ c & d \end{bmatrix}`);
check("columns are cells of their own", (cells.match(/<mtd>/g) || []).length === 4, cells);
check("cases gets its brace and its left alignment",
  ml(String.raw`\begin{cases} 1 & x>0 \\ 0 & x\le 0 \end{cases}`).includes('columnalign="left"'));

check("delimiters stretch when they are asked to",
  /<mo stretchy="true"[^>]*>\(<\/mo>/.test(ml(String.raw`\left(\frac{a}{b}\right)`)),
  ml(String.raw`\left(\frac{a}{b}\right)`));
check("and a matrix gets brackets its own height",
  /<mo stretchy="true"[^>]*>\[<\/mo>/.test(ml(String.raw`\begin{bmatrix}a\\b\end{bmatrix}`)),
  ml(String.raw`\begin{bmatrix}a\\b\end{bmatrix}`));
check("and a sized bracket is just a bracket",
  reads(String.raw`\big[\log R\big]_y`).startsWith("[logR]"), reads(String.raw`\big[\log R\big]_y`));
check("the norm bars are bars", reads(String.raw`\lVert a\rVert`) === "‖a‖");
check("boxed is a box for the stylesheet to draw", ml(String.raw`\boxed{S}`).includes('class="boxed"'));
check("an under-brace puts its label under the brace, not beside it",
  /<munder><munder/.test(ml(String.raw`\underbrace{ab}_{\text{why}}`)), ml(String.raw`\underbrace{ab}_{\text{why}}`));

/* Nothing may throw, and nothing may vanish. A command this does not know is
   shown rather than swallowed. */
check("an unknown command is shown, not dropped", reads(String.raw`\wobble x`).includes("wobble"));
check("and unbalanced braces do not take the message down",
  typeof ml(String.raw`\frac{a}{`) === "string" && ml(String.raw`\frac{a}{`).length > 0);
check("what is inside is escaped, not run",
  !ml(String.raw`\text{<img src=x onerror=alert(1)>}`).includes("<img"),
  ml(String.raw`\text{<img src=x onerror=alert(1)>}`));

/* ========================= finding it in a message ======================== */
const draws = (text) => renderMarkdown(text);
check("inline maths becomes an inline element",
  /<math class="md-math" display="inline"/.test(draws("$S$ is the Jacobian")));
check("display maths becomes a block of its own",
  /md-math-block/.test(draws("Then\n\n$$a = b$$\n")));
check("and \\[…\\] is the same thing said the other way",
  /md-math-block/.test(draws("Then\n\n\\[a = b\\]\n")));
check("a display may run over several lines",
  /<mtable>/.test(draws("$$\\begin{bmatrix}\na\\\\\nb\n\\end{bmatrix}$$")));

/* The half that can ruin prose. Every one of these is a dollar sign that turns
   up in ordinary writing and must be left exactly as it was. */
for (const [text, why] of [
  ["it costs $5 or $6 all in", "money"],
  ["export $PATH:$HOME then run it", "shell variables"],
  ["run $(pwd) first", "a command substitution"],
  ["the price went from $5.00 to $7.50", "two prices"],
  ["`$S$` in code marks", "maths inside code marks"],
]) {
  const drawn = draws(text);
  check(`${why} is left alone`, !drawn.includes("<math"), drawn.slice(0, 90));
}
check("but a dollar pair around real maths is found",
  draws("with $q\\in\\mathbb{R}^{7}$ angles").includes("<math"));
check("and the maths inside it is not read as markdown emphasis",
  !draws("$a_1 + a_2$").includes("<em>"), draws("$a_1 + a_2$"));

/* ===================== the message this was built for ===================== */
/* A page of real maths — swing-twist Jacobians, written by Claude into a real
   conversation. The whole of it has to come out as maths, with nothing left
   over and nothing handed back unrendered. */
const message = readFileSync(join(here, "fixtures", "math-message.md"), "utf8");
const drawn = draws(message);
const displays = (drawn.match(/display="block"/g) || []).length;
const inlines = (drawn.match(/display="inline"/g) || []).length;
check("every display in it is drawn", displays === 8, `${displays} of 8`);
check("and every piece of inline maths", inlines === 18, `${inlines} of 18`);
check("none of it fell back to raw TeX", !drawn.includes("md-math-raw"));
check("no dollar signs are left on screen", !/\$/.test(drawn.replace(/<[^>]*>/g, "")),
  (drawn.replace(/<[^>]*>/g, "").match(/.{0,20}\$.{0,20}/) || [""])[0]);
check("no stray backslash commands are left on screen",
  !/\\[a-zA-Z]/.test(drawn.replace(/<[^>]*>/g, "")),
  (drawn.replace(/<[^>]*>/g, "").match(/.{0,24}\\[a-zA-Z]+/) || [""])[0]);
check("the matrices in it are tables", (drawn.match(/<mtable>/g) || []).length === 3,
  `${(drawn.match(/<mtable>/g) || []).length}`);
check("the boxed result is boxed", drawn.includes('class="boxed"'));
check("and the prose around it is still prose",
  drawn.includes("is the Jacobian of the force-feedback error vector"));

console.log();
console.log(failures ? `${failures} failed` : "all ok");
process.exit(failures ? 1 : 0);

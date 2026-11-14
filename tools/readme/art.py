#!/usr/bin/env python3
"""The README's drawings: animated SVGs in the Watchtower style.

    python3 tools/readme/art.py        # writes docs/readme/*-{light,dark}.svg

Each drawing comes in a light and a dark version; the README picks one with
<picture> and prefers-color-scheme, the way GitHub switches themes. The fonts
are embedded (Urbanist and Roboto Mono, from web/assets/fonts), because an SVG
shown as an image may not load anything from outside. The animation is plain
CSS inside the SVG, which GitHub plays, and it holds still for anyone who
asks for reduced motion.
"""

from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "readme"
FONTS = ROOT / "web" / "assets" / "fonts"

EMBER, LILAC, LIME, BUTTER, ROSE, INK = "#F28C4E", "#A59FE2", "#CBDA72", "#F5CD62", "#E9839B", "#1E1F24"
THEMES = {
    "light": dict(page="#ECEBF1", surface="#FAFAFC", surface2="#F1F0F5", line="#DCDAE3",
                  text="#1E1F24", text2="#6C6B77", text3="#9B9AA6", act="#1E1F24", on_act="#FFFFFF",
                  ember_t="#FBE3D3", lilac_t="#E6E3F8", lime_t="#EEF3D2", hatch="#C9C7D3"),
    "dark": dict(page="#141518", surface="#1E1F24", surface2="#282930", line="#34353D",
                 text="#F2F1F6", text2="#A9A8B4", text3="#74737F", act="#F2F1F6", on_act="#1E1F24",
                 ember_t="#3A2A22", lilac_t="#2B2A3D", lime_t="#2C3020", hatch="#4A4B55"),
}


def font(name: str, file: str) -> str:
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return f"@font-face{{font-family:'{name}';src:url(data:font/woff2;base64,{data}) format('woff2');font-weight:100 900;}}"


FONT_CSS = font("Urbanist", "Urbanist.woff2") + font("WTMono", "RobotoMono.woff2")

BASE_CSS = """
text{font-family:Urbanist,'Segoe UI',system-ui,sans-serif;}
.mono{font-family:WTMono,ui-monospace,monospace;}
.spin{transform-box:fill-box;transform-origin:center;animation:spin 1.6s linear infinite;}
.nudge{transform-box:fill-box;transform-origin:center;animation:nudge 3.2s cubic-bezier(.2,0,0,1) infinite;}
.pulse{transform-box:fill-box;transform-origin:center;animation:pulse 2s cubic-bezier(.2,0,0,1) infinite;}
.halo{transform-box:fill-box;transform-origin:center;animation:halo 2.4s cubic-bezier(.2,0,0,1) infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
@keyframes nudge{0%,70%,100%{transform:rotate(0)}74%{transform:rotate(-14deg)}78%{transform:rotate(12deg)}82%{transform:rotate(-8deg)}86%{transform:rotate(5deg)}90%{transform:rotate(0)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes halo{0%{transform:scale(.6);opacity:.9}100%{transform:scale(1.9);opacity:0}}
@media (prefers-reduced-motion: reduce){*{animation:none!important;}}
"""

ICON_TERM = '<rect x="-11" y="-9" width="22" height="18" rx="4" fill="none" stroke="{c}" stroke-width="2.2"/><path d="M-6 -3l3.5 3-3.5 3M1 4h5" fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
ICON_CODE = '<path d="M-4 -7l-7 7 7 7M4 -7l7 7-7 7" fill="none" stroke="{c}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
ICON_PANES = '<rect x="-10" y="-9" width="20" height="18" rx="3.5" fill="none" stroke="{c}" stroke-width="2.2"/><path d="M-1 -9v18M-1 0h11" stroke="{c}" stroke-width="2.2"/>'


def svg(w: int, h: int, body: str, css: str = "", title: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{title}">'
            f'<title>{title}</title><style>{FONT_CSS}{BASE_CSS}{css}</style>{body}</svg>')


def avatar(x: float, y: float, r: float, fill: str, icon: str, extra: str = "", cls: str = "") -> str:
    # The class goes on an inner group, centred on the avatar, so a rotation
    # or a pop turns about its middle.
    inner = f'<circle r="{r}" fill="{fill}"/><g transform="scale({r / 24:.3f})">{icon.format(c=INK)}</g>'
    if cls:
        inner = f'<g class="{cls}">{inner}</g>'
    return f'<g transform="translate({x} {y})">{extra}{inner}</g>'


def working_arc(r: float, color: str) -> str:
    # The arc that circles a working avatar in the panel.
    return (f'<g class="spin"><circle r="{r + 6}" fill="none" stroke="{color}" stroke-width="3" '
            f'stroke-dasharray="{(r + 6) * 1.6:.0f} 999" stroke-linecap="round" opacity=".9"/></g>')


# ---- Hero ---------------------------------------------------------------------------
def hero(t: dict) -> str:
    W, H = 1280, 540
    css = f"""
    .flip-a{{animation:flipA 9s steps(1) infinite;}} .flip-b{{animation:flipB 9s steps(1) infinite;}}
    @keyframes flipA{{0%,49.99%{{opacity:1}}50%,100%{{opacity:0}}}}
    @keyframes flipB{{0%,49.99%{{opacity:0}}50%,100%{{opacity:1}}}}
    .pop{{transform-box:fill-box;transform-origin:center;animation:pop 9s cubic-bezier(.2,0,0,1) infinite;}}
    @keyframes pop{{0%,49%{{transform:scale(1)}}52%{{transform:scale(1.18)}}56%,100%{{transform:scale(1)}}}}
    .ring{{transform-box:fill-box;transform-origin:center;animation:ring 9s cubic-bezier(.2,0,0,1) infinite;}}
    @keyframes ring{{0%,49%{{transform:scale(.8);opacity:0}}50%{{transform:scale(.8);opacity:.9}}60%,100%{{transform:scale(1.7);opacity:0}}}}
    .rise{{animation:rise .9s cubic-bezier(.2,0,0,1) both;}}
    .rise2{{animation:rise .9s .12s cubic-bezier(.2,0,0,1) both;}} .rise3{{animation:rise .9s .24s cubic-bezier(.2,0,0,1) both;}}
    .rise4{{animation:rise .9s .36s cubic-bezier(.2,0,0,1) both;}} .rise5{{animation:rise .9s .48s cubic-bezier(.2,0,0,1) both;}}
    @keyframes rise{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:none}}}}
    .sheen{{animation:sheen 3.5s cubic-bezier(.2,0,0,1) infinite;}}
    @keyframes sheen{{0%{{transform:translateX(-160px)}}60%,100%{{transform:translateX(520px)}}}}
    """
    b = [f'<rect width="{W}" height="{H}" rx="44" fill="{t["page"]}"/>']
    # Hatching in the corner: the one texture, meaning "nothing here".
    b.append(f'<defs><pattern id="hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
             f'<rect width="3" height="10" fill="{t["hatch"]}"/></pattern>'
             f'<clipPath id="bg"><rect width="{W}" height="{H}" rx="44"/></clipPath></defs>')
    b.append(f'<circle cx="1240" cy="520" r="120" fill="url(#hatch)" opacity=".55" clip-path="url(#bg)"/>')
    # Left: the words.
    b.append(f'<g class="rise"><rect x="64" y="70" width="238" height="40" rx="20" fill="{t["surface"]}"/>'
             f'<circle cx="88" cy="90" r="6" fill="{LIME}"/><circle cx="88" cy="90" r="6" fill="{LIME}" class="halo"/>'
             f'<text x="104" y="96" font-size="16" font-weight="600" fill="{t["text"]}">Live for Claude Code</text></g>')
    b.append(f'<g class="rise2"><text x="62" y="196" font-size="64" font-weight="650" letter-spacing="-2.4" fill="{t["text"]}">Every session,</text></g>')
    b.append(f'<g class="rise3"><rect x="62" y="224" width="400" height="92" rx="46" fill="{LIME}"/>'
             f'<text x="262" y="291" text-anchor="middle" font-size="64" font-weight="650" letter-spacing="-2.4" fill="{INK}">at a glance.</text></g>')
    b.append(f'<g class="rise4"><text x="64" y="370" font-size="21" font-weight="500" fill="{t["text2"]}">'
             f'<tspan x="64">Which one is working, which one is done,</tspan>'
             f'<tspan x="64" dy="30">and which one is waiting on you. On your desk</tspan>'
             f'<tspan x="64" dy="30">and on your phone.</tspan></text></g>')
    b.append(f'<g class="rise5">')
    for i, (label, col) in enumerate((("Needs an answer", EMBER), ("Working", LILAC), ("Waiting on you", LIME))):
        x = 64 + [0, 196, 336][i]
        wdt = [180, 128, 168][i]
        b.append(f'<rect x="{x}" y="462" width="{wdt}" height="36" rx="18" fill="{t["surface"]}"/>'
                 f'<circle cx="{x + 20}" cy="480" r="6" fill="{col}"/>'
                 f'<text x="{x + 34}" y="485.5" font-size="15" font-weight="600" fill="{t["text"]}">{label}</text>')
    b.append('</g>')

    # Right: a small panel, alive.
    b.append(f'<g class="rise3"><rect x="700" y="56" width="524" height="428" rx="36" fill="{t["surface"]}"/>')
    b.append(f'<text x="736" y="104" font-size="20" font-weight="700" fill="{t["text"]}" letter-spacing="-.4">Claude Watchtower</text>'
             f'<text x="922" y="104" font-size="13" font-weight="600" fill="{t["text2"]}">4 live · 1 needs an answer</text>'
             f'<circle cx="1188" cy="98" r="5" fill="#4CAF6A" class="pulse"/>')
    rows = [
        ("Checkout for the roastery", "asks “Payments” · coffee-roastery", EMBER, ICON_TERM, "ember"),
        ("Tune the gripper PID loop", "working · robot-arm", LILAC, ICON_TERM, "work"),
        ("Sowing calendar from frost dates", None, None, ICON_PANES, "flip"),
        ("Retry the weather API properly", "waiting · api-client", LIME, ICON_CODE, "done"),
    ]
    for i, (title, sub, col, icon, kind) in enumerate(rows):
        y = 132 + i * 84
        fill = t["ember_t"] if kind == "ember" else t["surface2"]
        b.append(f'<rect x="720" y="{y}" width="484" height="70" rx="35" fill="{fill}"/>')
        ax, ay = 756, y + 35
        if kind == "ember":
            b.append(avatar(ax, ay, 25, EMBER, ICON_TERM, cls="nudge") +
                     f'<circle cx="{ax + 19}" cy="{ay - 19}" r="7" fill="{EMBER}" stroke="{fill}" stroke-width="3"/>')
        elif kind == "work":
            b.append(f'<g transform="translate({ax} {ay})">{working_arc(25, LILAC)}</g>{avatar(ax, ay, 25, LILAC, icon)}')
        elif kind == "done":
            b.append(avatar(ax, ay, 25, LIME, icon))
        else:
            # Working, then finished: the avatar pops and a ring of the new colour spreads.
            b.append(f'<g class="flip-a"><g transform="translate({ax} {ay})">{working_arc(25, LILAC)}</g>{avatar(ax, ay, 25, LILAC, icon)}</g>'
                     f'<g class="flip-b"><circle cx="{ax}" cy="{ay}" r="25" fill="none" stroke="{LIME}" stroke-width="4" class="ring"/>'
                     + avatar(ax, ay, 25, LIME, icon, cls="pop") + '</g>')
        b.append(f'<text x="796" y="{y + 32}" font-size="18" font-weight="650" fill="{t["text"]}">{title}</text>')
        if kind == "flip":
            b.append(f'<text x="796" y="{y + 54}" font-size="14" fill="{t["text2"]}" class="flip-a">working · garden-planner</text>'
                     f'<text x="796" y="{y + 54}" font-size="14" fill="{t["text2"]}" class="flip-b">waiting · garden-planner</text>')
        elif kind == "ember":
            b.append(f'<rect x="796" y="{y + 41}" width="88" height="20" rx="10" fill="{EMBER}"/>'
                     f'<text x="840" y="{y + 55.5}" text-anchor="middle" font-size="12.5" font-weight="700" fill="{INK}">question</text>'
                     f'<text x="892" y="{y + 56}" font-size="14" fill="{t["text2"]}">asks “Payments”</text>')
        else:
            b.append(f'<text x="796" y="{y + 54}" font-size="14" fill="{t["text2"]}">{sub}</text>')
        b.append(f'<text x="1180" y="{y + 40}" text-anchor="end" font-size="13" class="mono" fill="{t["text3"]}">{["3s", "12s", "1m", "19m"][i]}</text>')
    b.append('</g>')
    return svg(W, H, "".join(b), css, "Claude Watchtower: every Claude Code session at a glance")


# ---- The four states ------------------------------------------------------------------
def states(t: dict) -> str:
    W, H = 1280, 180
    b = [f'<defs><pattern id="h2" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         f'<rect width="3" height="9" fill="{t["hatch"]}"/></pattern></defs>',
         f'<rect width="{W}" height="{H}" rx="36" fill="{t["page"]}"/>']
    tiles = [
        ("Needs an answer", "Asked you something", EMBER, "ember"),
        ("Working", "Thinking or running tools", LILAC, "work"),
        ("Waiting on you", "Finished its turn", LIME, "done"),
        ("Closed", "The process has gone", None, "closed"),
    ]
    for i, (label, sub, col, kind) in enumerate(tiles):
        x = 24 + i * 310
        b.append(f'<rect x="{x}" y="24" width="294" height="132" rx="28" fill="{t["surface"]}"/>')
        ax, ay = x + 58, 90
        if kind == "ember":
            b.append(avatar(ax, ay, 28, EMBER, ICON_TERM, cls="nudge"))
        elif kind == "work":
            b.append(f'<g transform="translate({ax} {ay})">{working_arc(28, LILAC)}</g>{avatar(ax, ay, 28, LILAC, ICON_TERM)}')
        elif kind == "done":
            b.append(avatar(ax, ay, 28, LIME, ICON_CODE))
        else:
            b.append(f'<circle cx="{ax}" cy="{ay}" r="28" fill="url(#h2)"/><circle cx="{ax}" cy="{ay}" r="28" fill="none" stroke="{t["hatch"]}" stroke-width="2"/>')
        b.append(f'<text x="{x + 104}" y="86" font-size="21" font-weight="650" fill="{t["text"]}">{label}</text>'
                 f'<text x="{x + 104}" y="111" font-size="14.5" fill="{t["text2"]}">{sub}</text>')
    return svg(W, H, "".join(b), "", "The four states: needs an answer, working, waiting on you, closed")


# ---- The terminal ----------------------------------------------------------------------
def terminal(t: dict) -> str:
    W, H = 1280, 456
    lines = [
        ("$ ", "git clone https://github.com/coencoensmeets/claude-watchtower.git", "cmd"),
        ("$ ", "cd claude-watchtower", "cmd"),
        ("$ ", "python3 server.py", "cmd"),
        ("", "claude-watchtower → http://127.0.0.1:8787", LIME),
        ("", "on this network  → http://192.168.1.20:8787/?k=3f9c…", LILAC),
        ("", "                  or scan the code in Settings — it is the same address", "dim"),
        ("", "from anywhere    → https://laptop.tail5e1a.ts.net:8787/?k=3f9c…  (Tailscale, https)", LILAC),
        ("", "note: port 8787 is this install's from now on", "dim"),
    ]
    cycle = 14.0
    starts, t0 = [], 0.6
    for p, text, kind in lines:
        dur = len(text) * 0.035 if kind == "cmd" else 0.12
        starts.append((t0, dur))
        t0 += dur + (0.5 if kind == "cmd" else 0.18)
    css = [".term{font-family:WTMono,ui-monospace,monospace;font-size:17px;}",
           ".blink{animation:blink 1s steps(1) infinite;}@keyframes blink{50%{opacity:0}}"]
    b = [f'<rect width="{W}" height="{H}" rx="40" fill="{t["page"]}"/>',
         f'<rect x="40" y="32" width="{W - 80}" height="{H - 64}" rx="28" fill="{INK}"/>',
         f'<rect x="40" y="32" width="{W - 80}" height="{H - 64}" rx="28" fill="none" stroke="{t["line"]}" stroke-width="1.5" opacity="{0 if t is THEMES["light"] else 1}"/>']
    for i, c in enumerate((ROSE, BUTTER, LIME)):
        b.append(f'<circle cx="{76 + i * 24}" cy="64" r="7" fill="{c}"/>')
    b.append(f'<text x="{W / 2}" y="69" text-anchor="middle" font-size="14" font-weight="600" fill="#74737F">~ — bash</text><defs>')
    for i, ((p, text, kind), (s, d)) in enumerate(zip(lines, starts)):
        a, e = s / cycle * 100, (s + d) / cycle * 100
        n = max(1, len(text) if kind == "cmd" else 1)
        css.append(f"@keyframes w{i}{{0%,{a:.2f}%{{width:0}}{e:.2f}%,94%{{width:1200px}}100%{{width:0}}}}"
                   f".c{i}{{animation:w{i} {cycle}s infinite;animation-timing-function:steps({n},end);}}")
        b.append(f'<clipPath id="k{i}"><rect class="c{i}" x="{92 if kind == "cmd" else 72}" y="{96 + i * 34}" width="0" height="34"/></clipPath>')
    b.append('</defs>')
    for i, ((p, text, kind), (s, d)) in enumerate(zip(lines, starts)):
        y = 120 + i * 34
        a = s / cycle * 100
        color = {"cmd": "#F2F1F6", "dim": "#74737F"}.get(kind, kind)
        if p:
            css.append(f"@keyframes p{i}{{0%,{max(0, a - .01):.2f}%{{opacity:0}}{a:.2f}%,94%{{opacity:1}}100%{{opacity:0}}}}.q{i}{{animation:p{i} {cycle}s steps(1) infinite;}}")
            b.append(f'<text x="72" y="{y}" class="term q{i}" fill="{LIME}" font-weight="500">$</text>')
        b.append(f'<g clip-path="url(#k{i})"><text x="{92 if p else 72}" y="{y}" class="term" fill="{color}" xml:space="preserve">{text.replace("&", "&amp;")}</text></g>')
    y = 120 + len(lines) * 34
    css.append(f"@keyframes pz{{0%,{t0 / cycle * 100:.2f}%{{opacity:0}}{t0 / cycle * 100 + .01:.2f}%,94%{{opacity:1}}100%{{opacity:0}}}}.pz{{animation:pz {cycle}s steps(1) infinite;}}")
    b.append(f'<g class="pz"><text x="72" y="{y}" class="term" fill="{LIME}">$</text><rect x="92" y="{y - 16}" width="10" height="20" fill="#F2F1F6" class="blink"/></g>')
    return svg(W, H, "".join(b), "".join(css), "Quick start: clone the repository and run python3 server.py")


# ---- How it works ----------------------------------------------------------------------
def architecture(t: dict) -> str:
    W, H = 1280, 520
    css = """
    .flow{stroke-dasharray:3 11;animation:flow 1.1s linear infinite;}
    .flow-slow{stroke-dasharray:3 11;animation:flow 1.8s linear infinite;}
    @keyframes flow{to{stroke-dashoffset:-14}}
    .dot{animation:pulse 2s cubic-bezier(.2,0,0,1) infinite;}
    """
    b = [f'<rect width="{W}" height="{H}" rx="44" fill="{t["page"]}"/>',
         f'<defs><marker id="ah" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         f'<path d="M1 1L8 5L1 9" fill="none" stroke="{t["text2"]}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>'
         f'<marker id="al" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         f'<path d="M1 1L8 5L1 9" fill="none" stroke="{LILAC}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>']

    def label(x, y, text, size=13, weight=600, color=None, anchor="start", mono=False):
        cls = ' class="mono"' if mono else ""
        return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color or t["text2"]}" text-anchor="{anchor}"{cls}>{text}</text>'

    # Column heads.
    for x, text in ((40, "YOUR SESSIONS"), (352, "WHAT CLAUDE CODE WRITES"), (690, "THE PANEL"), (1010, "WHERE YOU LOOK")):
        b.append(label(x + 4, 62, text, 12, 700, t["text3"]))
    # Sessions.
    for i, (host, folder, col, icon, kind) in enumerate((("Ghostty", "robot-arm", LILAC, ICON_TERM, "work"),
                                                         ("VS Code", "api-client", LIME, ICON_CODE, "done"),
                                                         ("tmux", "coffee-roastery", EMBER, ICON_PANES, "ember"))):
        y = 84 + i * 104
        b.append(f'<rect x="40" y="{y}" width="262" height="84" rx="42" fill="{t["surface"]}"/>')
        ax, ay = 82, y + 42
        if kind == "work":
            b.append(f'<g transform="translate({ax} {ay})">{working_arc(24, LILAC)}</g>')
        b.append(avatar(ax, ay, 24, col, icon, cls="nudge" if kind == "ember" else ""))
        b.append(label(118, y + 38, "claude", 18, 650, t["text"]) + label(118, y + 60, f"{host} · {folder}", 13.5, 500))

    # Files.
    for i, (path, what) in enumerate((("~/.claude/sessions/*.json", "status, pid, folder"),
                                      ("~/.claude/projects/*.jsonl", "the conversation, edits, tokens"))):
        y = 116 + i * 132
        b.append(f'<rect x="352" y="{y}" width="290" height="104" rx="24" fill="{t["surface"]}"/>'
                 f'<path d="M{380} {y + 30}h16l8 8v26a4 4 0 0 1-4 4h-20a4 4 0 0 1-4-4v-30a4 4 0 0 1 4-4z" fill="none" stroke="{t["text2"]}" stroke-width="2" stroke-linejoin="round"/>')
        b.append(label(416, y + 46, path, 13, 500, t["text"], mono=True) + label(420, y + 70, what, 13.5, 500))
    # Arrows: sessions write.
    for i in range(3):
        y1 = 126 + i * 104
        y2 = 168 if i < 2 else 300
        b.append(f'<path d="M306 {y1} C 330 {y1}, 326 {y2}, 346 {y2}" fill="none" stroke="{t["text2"]}" stroke-width="2" class="flow" marker-end="url(#ah)"/>')

    # The panel.
    b.append(f'<rect x="690" y="84" width="276" height="300" rx="36" fill="{INK}"/>')
    b.append(f'<circle cx="732" cy="128" r="18" fill="{LIME}"/><path d="M724 128l6 6 11-12" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
    b.append(label(762, 135, "Watchtower", 24, 700, "#F2F1F6"))
    b.append(label(716, 186, "python3 server.py", 15, 500, "#F2F1F6", mono=True))
    b.append(label(716, 212, "standard library only", 14, 500, "#A9A8B4"))
    for i, (txt, col) in enumerate((("reads every second", LIME), ("works out who needs you", EMBER), ("talks back to sessions", LILAC))):
        y = 246 + i * 40
        b.append(f'<rect x="716" y="{y}" width="224" height="30" rx="15" fill="#282930"/>'
                 f'<circle cx="734" cy="{y + 15}" r="5.5" fill="{col}" class="dot"/>' + label(748, y + 20, txt, 14, 600, "#F2F1F6"))
    b.append(f'<path d="M646 168 C 668 168, 664 200, 686 200" fill="none" stroke="{t["text2"]}" stroke-width="2" class="flow" marker-end="url(#ah)"/>'
             f'<path d="M646 300 C 668 300, 664 260, 686 260" fill="none" stroke="{t["text2"]}" stroke-width="2" class="flow" marker-end="url(#ah)"/>')
    b.append(label(666, 236, "read", 12, 700, t["text3"], "middle"))

    # Back to the sessions: send, answer, stop.
    b.append(f'<path d="M828 388 C 828 470, 171 470, 171 404" fill="none" stroke="{LILAC}" stroke-width="2.4" class="flow-slow" marker-end="url(#al)"/>')
    b.append(f'<rect x="398" y="440" width="300" height="34" rx="17" fill="{t["surface"]}"/>'
             + label(548, 462, "send · answer · stop · start new ones", 14, 650, t["text"], "middle"))

    # Devices.
    devices = [("Your browser", "on this computer", "laptop"),
               ("Your phone", "scan a code, or Tailscale", "phone"),
               ("Claude itself", "through MCP tools", "mcp")]
    for i, (name, sub, kind) in enumerate(devices):
        y = 84 + i * 104
        b.append(f'<rect x="1010" y="{y}" width="232" height="84" rx="28" fill="{t["surface"]}"/>')
        cx, cy = 1050, y + 42
        if kind == "laptop":
            b.append(f'<rect x="{cx - 15}" y="{cy - 12}" width="30" height="20" rx="3" fill="none" stroke="{t["text"]}" stroke-width="2.2"/><path d="M{cx - 19} {cy + 12}h38" stroke="{t["text"]}" stroke-width="2.4" stroke-linecap="round"/>')
        elif kind == "phone":
            b.append(f'<rect x="{cx - 10}" y="{cy - 16}" width="20" height="32" rx="5" fill="none" stroke="{t["text"]}" stroke-width="2.2"/><circle cx="{cx}" cy="{cy + 10}" r="1.6" fill="{t["text"]}"/>')
        else:
            b.append(f'<path d="M{cx} {cy - 15}l3.4 9.6 9.6 3.4-9.6 3.4-3.4 9.6-3.4-9.6-9.6-3.4 9.6-3.4z" fill="{LILAC}"/>')
        b.append(label(1080, y + 38, name, 17, 650, t["text"]) + label(1080, y + 60, sub, 12.5, 500))
        arrow_y = y + 42
        b.append(f'<path d="M970 {200 + (i - 1) * 60} C 990 {200 + (i - 1) * 60}, 986 {arrow_y}, 1004 {arrow_y}" fill="none" '
                 f'stroke="{LILAC if kind == "mcp" else t["text2"]}" stroke-width="2" class="flow" marker-end="url({"#al" if kind == "mcp" else "#ah"})"/>')
    b.append(label(1126, 420, "nothing leaves the machine", 13, 500, t["text3"], "middle"))
    return svg(W, H, "".join(b), css, "How it works: sessions write files, the panel reads them and serves your browser and phone")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("hero", hero), ("states", states), ("terminal", terminal), ("architecture", architecture)):
        for theme, tokens in THEMES.items():
            path = OUT / f"{name}-{theme}.svg"
            path.write_text(fn(tokens))
            print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

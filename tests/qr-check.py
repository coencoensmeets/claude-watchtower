#!/usr/bin/env python3
"""Checks for the QR code the settings page shows — no camera, no packages.

A QR code that is subtly wrong looks exactly like one that is right, so this
does not eyeball it. Three kinds of check:

**Golden vectors.** The four codes below were compared module for module against
an independent encoder (the `qrcode` library, in a throwaway venv) across every
version this module supports and all eight masks, and agreed everywhere. What is
stored here is the agreed answer, compressed. If a change to watchtower/qr.py
moves a single module, one of these fails.

**Structure.** Anything a scanner looks for first — three finders, the timing
lines, the dark module, the quiet zone — asserted directly, so a failure says
which part of the code broke rather than only that the bits moved.

**The parts that are arithmetic.** The format information against the standard's
own table, the version chosen for a given length, the mask chosen being the one
with the lowest penalty, and the SVG drawing the matrix it was handed.

    python3 tests/qr-check.py

A failure prints the case and exits 1.
"""

import base64
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from watchtower import qr  # noqa: E402

FAILED = 0


def check(what: str, ok: bool, note: str = "") -> None:
    global FAILED
    print(f"{'  ok  ' if ok else 'FAIL  '}{what}{f'  — {note}' if note else ''}")
    if not ok:
        FAILED += 1


def unpack(packed: str, side: int) -> list[list[bool]]:
    bits = zlib.decompress(base64.b64decode(packed)).decode()
    return [[bits[row * side + col] == "1" for col in range(side)] for row in range(side)]


# ------------------------------------------------------------ golden vectors
GOLDEN = [
    ("A", 3,
     "eNpdkAEWwCAIQq8E97/cXgqos94Kc3yLrABRU4EXRI6eKlmCPSqjpPY3Wcsk21PF8gydxqnQxAjbxttA5F90d645nS8pbnbAngf4fxYiQICrbjBzd2P2KzFuqfTd3Zy+xvDQp8sPM+pTkw=="),
    ("http://192.168.1.24:8867/?k=r4pm7dwq", 0,
     "eNpdUgcSwDAI+hL8/3MdApK012Usy5D/AeC93qfuTPlbcP1/g6qcQuo59TmL87sIrsUAidkEEEeal1OyrrPEGpGhhU0MY7uR66iY9xLm/qik0dkWTSHKuKN5Bez/yxNRjZIteLBm4ggW5OzuzoDVdyxUPgHEKsowje7E1MT4X58dlxWeE8n0OxCS5+TWr8LdVH3tXjE7uQnVWJjYr4R69zFTqe29IS0cjn1m9sj8Hg8LKZ9X"),
    ("http://192.168.1.24:8867/?k=r4pm7dwq", 5,
     "eNptUwkOgDAM+hL8/3NGS4EtarLFXhyN5PcAe7wnyA3j+8TkJ6AoJ4RpmEu9leR0I4OThAHQyYSEU5iidb1Ftig4UAOULIGuTZsmTkgnWeAHr2YSKQu7PdIdzR5VNHCBd1KdNLaK2qftFk62ulTZ9yGcxmCVM4C1h1tnO7fzI9tOmFWpyRZ2OrMjL452NBYUnrmfItY+Wjb+kusYD5QTn+yfgpEY7x8f1p9U"),
    ("x" * 86, 2,
     "eNp9VAESwjAM+hL8/3OeawIknarnqssaIBTyeQHnclbnUguyS/D9E8/iqX1+s77qRj3RN/oevN9ZMQqpHdSoYOzCRpoAmYXV6GCjQTTwxkg99+sdpJP3tWOwE7qky6Hj6b1kuwurYAh2tSYteSsg9jfxyauFneNWpwM1R+uHPX9q3D15g0JgKoweSbYQXmGUEcIfhtkYL3C2MLFNkyZm+4tLRzD80boGJc4SbZNeKznET7RNOvnYvBjrn7Oe278UtqMsooSfny5DaEm+fnIKZvDHj7b2a2H0o83NPCfkGuo48M6I1BIMkxIBnEnNhw55MBBg7a+ZghuHfBVJIV/vIM0zRAUcVpBGGmaaRdqoPbxHyAUskRLkCp8ZpGmnnY5zilnudORVGKOcuYNttt4AH6tuPpc="),
]
for text, mask, packed in GOLDEN:
    mine = qr.matrix(text, mask=mask)
    want = unpack(packed, len(mine))
    version = (len(mine) - 17) // 4
    check(f"version {version}, mask {mask}, {len(text)} bytes — module for module",
          mine == want,
          "" if mine == want else
          f"{sum(1 for r in range(len(mine)) for c in range(len(mine)) if mine[r][c] != want[r][c])} modules moved")

# ---------------------------------------------------------------- structure
code = qr.matrix("http://192.168.1.24:8867/?k=r4pm7dwq")
side = len(code)
check("the code is square and an odd number of modules across",
      all(len(row) == side for row in code) and side % 4 == 1, str(side))

def finder_at(top: int, left: int) -> bool:
    for row in range(7):
        for col in range(7):
            edge = max(abs(row - 3), abs(col - 3))
            if code[top + row][left + col] != (edge in (0, 1, 3)):
                return False
    return True

check("a finder in each of the three corners",
      finder_at(0, 0) and finder_at(0, side - 7) and finder_at(side - 7, 0))
check("the timing lines run between them",
      all(code[6][at] == (at % 2 == 0) for at in range(8, side - 8))
      and all(code[at][6] == (at % 2 == 0) for at in range(8, side - 8)))
check("the module that is always dark, is", code[side - 8][8] is True)
check("and the separators around the finders are light",
      not any(code[7][at] for at in range(8)) and not any(code[at][7] for at in range(8)))

# ------------------------------------------------------------- the format
# The standard's own table for level M, mask 0 through 7.
FORMATS = ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
           "100010111111001", "100000011001110", "100111110010111", "100101010100000"]
for mask, want in enumerate(FORMATS):
    got = f"{qr._format_bits(mask):015b}"
    check(f"the format bits for mask {mask}", got == want, f"{got} wanted {want}")

# ------------------------------------------------------ version and capacity
check("a short string is a version 1 code", len(qr.matrix("hi")) == 21)
check("a URL with a key on it is version 3",
      len(qr.matrix("http://192.168.1.24:8867/?k=r4pm7dwq")) == 29)
check("86 bytes still fits", len(qr.matrix("x" * 86)) == 41)
try:
    qr.matrix("x" * (qr.MAX_BYTES + 1))
    check("a payload too long to draw is refused rather than drawn wrong", False, "no error")
except ValueError:
    check("a payload too long to draw is refused rather than drawn wrong", True)

# ------------------------------------------------------------- mask choice
scores = []
for mask in range(8):
    drawn = [[int(cell) for cell in row] for row in qr.matrix("http://192.168.1.24:8867/?k=r4pm7dwq", mask=mask)]
    scores.append(qr._penalty(drawn))
picked = qr.matrix("http://192.168.1.24:8867/?k=r4pm7dwq")
which = [m for m in range(8) if qr.matrix("http://192.168.1.24:8867/?k=r4pm7dwq", mask=m) == picked]
check("the mask chosen is the one that scores lowest",
      which and scores[which[0]] == min(scores), f"picked {which}, scores {scores}")

# --------------------------------------------------------------- the SVG
drawn = qr.svg("http://192.168.1.24:8867/?k=r4pm7dwq")
check("the SVG is black on white, whatever the theme is doing",
      'fill="#ffffff"' in drawn and 'fill="#000000"' in drawn and "var(" not in drawn)
box = re.search(r'viewBox="0 0 (\d+) (\d+)"', drawn)
check("with the quiet zone the standard asks for on every side",
      box and int(box.group(1)) == side + qr.QUIET * 2, box.group(1) if box else "no viewBox")
# Every dark module, read back out of the path, has to be the matrix again.
back = [[False] * side for _ in range(side)]
strays = 0
for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", drawn):
    row, col = int(y) - qr.QUIET, int(x) - qr.QUIET
    if 0 <= row < side and 0 <= col < side:
        back[row][col] = True
    else:
        strays += 1
check("nothing is drawn outside the code", strays == 0, f"{strays} strays")
check("and the picture is the matrix it was handed", back == code)
check("it is one path and one rectangle, so it stays sharp at any size",
      drawn.count("<path") == 1 and drawn.count("<rect") == 1)
check("and it says what it is, for anyone who cannot see it",
      'role="img"' in drawn and "aria-label=" in drawn)

print()
print("all ok" if not FAILED else f"{FAILED} failed")
sys.exit(1 if FAILED else 0)

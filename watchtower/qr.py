"""A QR code, drawn by hand, because a phone should not have to type anything.

The panel prints an address with a key on the end. Typing that on a phone once
is tolerable and twice is not, so the settings page shows it as a code to point a
camera at. That means encoding one here: the panel has no packages and is not
about to grow one for this.

What is implemented is the subset the job needs and no more — byte mode, error
correction level M, versions 1 to 6 — which covers any `http://…/?k=…` on a
local network with room to spare. Level M rather than L because the code is
being read off a screen at an angle in whatever light the room has; versions
stop at 6 because 7 and up carry a version block of their own and 6 already
holds 108 bytes.

Everything below is ISO/IEC 18004: the Galois field, the Reed-Solomon
remainder, the interleave, the zig-zag placement, the eight masks and the
penalty scoring that picks between them. tests/qr-check.py compares what comes
out against a reference encoder, module for module, because a QR code that is
subtly wrong looks exactly like one that is right.
"""

from __future__ import annotations


# --- the field
#
# GF(256) with the primitive polynomial x^8 + x^4 + x^3 + x^2 + 1 (0x11d), the
# one QR uses. Built once at import: two 256-entry tables turn every
# multiplication below into a lookup and an add.
_EXP: list[int] = [0] * 512
_LOG: list[int] = [0] * 256
_value = 1
for _power in range(255):
    _EXP[_power] = _value
    _LOG[_value] = _power
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _power in range(255, 512):
    _EXP[_power] = _EXP[_power - 255]


def _mul(a: int, b: int) -> int:
    if not a or not b:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree: int) -> list[int]:
    """The Reed-Solomon generator polynomial (x - 2^0)(x - 2^1)…, degree terms."""
    poly = [1]
    for step in range(degree):
        # Multiply by (x - 2^step), which in this field is (x + 2^step).
        nxt = [0] * (len(poly) + 1)
        for i, coefficient in enumerate(poly):
            nxt[i] ^= coefficient
            nxt[i + 1] ^= _mul(coefficient, _EXP[step])
        poly = nxt
    return poly


def _remainder(data: list[int], degree: int) -> list[int]:
    """The error-correction codewords for one block: polynomial division, and
    what is left over."""
    gen = _generator(degree)
    rest = list(data) + [0] * degree
    for at in range(len(data)):
        lead = rest[at]
        if not lead:
            continue
        for i, coefficient in enumerate(gen):
            rest[at + i] ^= _mul(coefficient, lead)
    return rest[len(data):]


# --- what fits where
#
# Per version at level M: how many data codewords in total, how many blocks
# they are split into, and how many error-correction codewords each block
# carries. Every one of these versions happens to split into equal blocks,
# which is why there is one number for the block size rather than two — see
# _blocks, which still divides generically.
_CAPACITY: dict[int, tuple[int, int, int]] = {
    #        data  blocks  ec per block
    1:      (  16,      1,          10),
    2:      (  28,      1,          16),
    3:      (  44,      1,          26),
    4:      (  64,      2,          18),
    5:      (  86,      2,          24),
    6:      ( 108,      4,          16),
}
MAX_BYTES = _CAPACITY[6][0] - 2      # less the mode indicator and the count

# Where the one alignment pattern sits, per version. v1 has none; v7 and up have
# several, which is where this table would have to grow into a formula.
_ALIGN: dict[int, int] = {2: 18, 3: 22, 4: 26, 5: 30, 6: 34}

# Level M, as the two bits that go into the format information.
_EC_BITS = 0b00


def _version_for(payload: bytes) -> int:
    need = len(payload) + 2          # the 4-bit mode and 8-bit count, rounded up
    for version, (data, _blocks_, _ec_) in _CAPACITY.items():
        if need <= data:
            return version
    raise ValueError(f"{len(payload)} bytes is more than a version 6 code holds")


def _codewords(payload: bytes, version: int) -> list[int]:
    """The payload as data codewords: header, bytes, terminator, padding."""
    total, blocks, ec = _CAPACITY[version]
    bits: list[int] = []

    def put(value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    put(0b0100, 4)                   # byte mode
    put(len(payload), 8)             # the count field is 8 bits below version 10
    for byte in payload:
        put(byte, 8)
    # Terminator, as much of it as there is room for.
    put(0, min(4, total * 8 - len(bits)))
    # Up to the next byte boundary.
    while len(bits) % 8:
        bits.append(0)

    words = [int("".join(str(b) for b in bits[at:at + 8]), 2) for at in range(0, len(bits), 8)]
    # The two pad codewords the standard names, alternating from 0xEC, to the end.
    pad = (0xEC, 0x11)
    for index in range(total - len(words)):
        words.append(pad[index % 2])
    return words


def _blocks(words: list[int], version: int) -> list[int]:
    """Split into blocks, add each block's remainder, and interleave both — data
    codewords one from each block in turn, then the error correction the same
    way. Which is what makes a scratch across the code survivable: it lands in
    every block a little rather than in one block entirely."""
    total, count, ec = _CAPACITY[version]
    short, extra = divmod(total, count)
    data_blocks: list[list[int]] = []
    at = 0
    for index in range(count):
        size = short + (1 if index >= count - extra else 0)
        data_blocks.append(words[at:at + size])
        at += size
    ec_blocks = [_remainder(block, ec) for block in data_blocks]

    out: list[int] = []
    for column in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if column < len(block):
                out.append(block[column])
    for column in range(ec):
        for block in ec_blocks:
            out.append(block[column])
    return out


# --- the drawing

def _blank(size: int) -> list[list[int | None]]:
    return [[None] * size for _ in range(size)]


def _patterns(grid: list[list[int | None]], version: int) -> None:
    """Everything that is the same in every code of this version: the three
    finders and their separators, the timing lines, the alignment pattern, the
    dark module, and the areas the format information will land in — reserved
    with a zero so the data placement below knows to step over them."""
    size = len(grid)

    def finder(top: int, left: int) -> None:
        for row in range(-1, 8):
            for col in range(-1, 8):
                r, c = top + row, left + col
                if not (0 <= r < size and 0 <= c < size):
                    continue
                edge = max(abs(row - 3), abs(col - 3))
                # The concentric square: dark, light, dark, and the separator
                # ring outside it.
                grid[r][c] = 1 if edge in (0, 1, 3) else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for at in range(size):
        if grid[6][at] is None:
            grid[6][at] = 1 - (at % 2)
        if grid[at][6] is None:
            grid[at][6] = 1 - (at % 2)

    centre = _ALIGN.get(version)
    if centre is not None:
        for row in range(-2, 3):
            for col in range(-2, 3):
                edge = max(abs(row), abs(col))
                grid[centre + row][centre + col] = 1 if edge != 1 else 0

    # The one module that is always dark, and the strips the format bits go in.
    grid[size - 8][8] = 1
    for at in range(9):
        if grid[8][at] is None:
            grid[8][at] = 0
        if grid[at][8] is None:
            grid[at][8] = 0
    for at in range(size - 8, size):
        if grid[8][at] is None:
            grid[8][at] = 0
        if grid[at][8] is None:
            grid[at][8] = 0


def _place(grid: list[list[int | None]], stream: list[int]) -> list[list[bool]]:
    """The data, up and down two-module columns from the right, stepping over
    everything already in the grid. Column 6 is skipped whole: it is the
    vertical timing line."""
    size = len(grid)
    used = [[cell is not None for cell in row] for row in grid]
    bits = [(word >> shift) & 1 for word in stream for shift in range(7, -1, -1)]
    at = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if used[row][c]:
                    continue
                grid[row][c] = bits[at] if at < len(bits) else 0
                at += 1
        upward = not upward
        col -= 2
    return used                       # which modules are function patterns


def _format_bits(mask: int) -> int:
    """Fifteen bits: five that say the level and the mask, ten of BCH(15,5) over
    them, and the standard XOR that keeps an all-zero format — level M, mask 0 —
    from looking like a blank corner."""
    value = (_EC_BITS << 3) | mask
    remainder = value << 10
    for shift in range(4, -1, -1):
        if remainder & (1 << (shift + 10)):
            remainder ^= 0b101_0011_0111 << shift
    return ((value << 10) | (remainder & 0x3FF)) ^ 0b101_0100_0001_0010


def _write_format(grid: list[list[int]], mask: int) -> None:
    """The format information, twice: once around the top-left finder, once
    split between the other two corners.

    The mapping is the standard's and it is easy to get backwards — the least
    significant bit is the one nearest the top-left finder going down column 8,
    and the one at the far right of row 8 going the other way. Written wrong it
    still looks like a QR code and no camera reads it, which is why
    tests/qr-check.py compares these thirty modules against a reference.
    """
    size = len(grid)
    bits = _format_bits(mask)
    for i in range(15):
        bit = (bits >> i) & 1
        # Down column 8 from the top, stepping over the timing row at 6, and
        # then the bottom-left leg — which starts below the dark module.
        if i < 6:
            grid[i][8] = bit
        elif i < 8:
            grid[i + 1][8] = bit
        else:
            grid[size - 15 + i][8] = bit
        # Along row 8, leftwards from the right edge, then the top-left leg.
        if i < 8:
            grid[8][size - 1 - i] = bit
        elif i == 8:
            grid[8][7] = bit
        else:
            grid[8][14 - i] = bit


def _masked(bit: int, mask: int, row: int, col: int) -> int:
    if mask == 0:
        flip = (row + col) % 2 == 0
    elif mask == 1:
        flip = row % 2 == 0
    elif mask == 2:
        flip = col % 3 == 0
    elif mask == 3:
        flip = (row + col) % 3 == 0
    elif mask == 4:
        flip = (row // 2 + col // 3) % 2 == 0
    elif mask == 5:
        flip = (row * col) % 2 + (row * col) % 3 == 0
    elif mask == 6:
        flip = ((row * col) % 2 + (row * col) % 3) % 2 == 0
    else:
        flip = ((row + col) % 2 + (row * col) % 3) % 2 == 0
    return bit ^ 1 if flip else bit


def _penalty(grid: list[list[int]]) -> int:
    """How badly a masked code reads, by the standard's four rules: runs of one
    colour, solid 2x2 blocks, the finder-like pattern appearing in the data, and
    an unbalanced amount of dark overall. The mask with the lowest score wins."""
    size = len(grid)
    score = 0

    # 1. Runs of five or more.
    for line in list(grid) + [list(col) for col in zip(*grid)]:
        run, last = 0, None
        for cell in line:
            if cell == last:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, last = 1, cell
        if run >= 5:
            score += 3 + (run - 5)

    # 2. Blocks of the same colour.
    for row in range(size - 1):
        for col in range(size - 1):
            if grid[row][col] == grid[row][col + 1] == grid[row + 1][col] == grid[row + 1][col + 1]:
                score += 3

    # 3. The 1:1:3:1:1 finder proportion, with four light modules either side.
    want = [1, 0, 1, 1, 1, 0, 1]
    quiet = [0, 0, 0, 0]
    for line in list(grid) + [list(col) for col in zip(*grid)]:
        for at in range(size - 6):
            if line[at:at + 7] == want:
                before = line[max(0, at - 4):at]
                after = line[at + 7:at + 11]
                if before == quiet[:len(before)] and len(before) == 4:
                    score += 40
                elif after == quiet[:len(after)] and len(after) == 4:
                    score += 40
    # 4. Dark modules, as a proportion.
    dark = sum(sum(row) for row in grid)
    percent = dark * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


def matrix(text: str, mask: int | None = None) -> list[list[bool]]:
    """The code for this string, as rows of dark/light, without a quiet zone.

    `mask` is for the checks, which compare against a reference encoder one mask
    at a time; left alone, all eight are scored and the best kept — the
    difference between a code a camera finds at once and one it hunts for.
    """
    payload = text.encode("utf-8")
    version = _version_for(payload)
    size = 17 + 4 * version
    stream = _blocks(_codewords(payload, version), version)

    grid = _blank(size)
    _patterns(grid, version)
    function = _place(grid, stream)

    def with_mask(which: int) -> list[list[int]]:
        drawn = [[int(grid[r][c]) if function[r][c] else _masked(int(grid[r][c]), which, r, c)
                  for c in range(size)] for r in range(size)]
        _write_format(drawn, which)
        return drawn

    if mask is not None:
        return [[bool(cell) for cell in row] for row in with_mask(mask)]
    best: tuple[int, list[list[int]]] | None = None
    for which in range(8):
        drawn = with_mask(which)
        score = _penalty(drawn)
        if best is None or score < best[0]:
            best = (score, drawn)
    return [[bool(cell) for cell in row] for row in best[1]]


# The quiet zone the standard asks for. Four modules, and it is not optional:
# a code drawn to the edge of its box is a code a camera cannot find.
QUIET = 4


def svg(text: str, module: int = 8) -> str:
    """The code as an SVG: one white rectangle, and every dark module as one
    path, so it stays sharp at any size.

    Black on white and not the panel's own colours, however themed the page
    around it is. A scanner is looking for dark modules on a light field; a code
    drawn in the panel's ink on the panel's dark surface is the same code with
    its polarity inverted, and nothing reads it. The two colours were CSS
    variables for exactly one afternoon, which is how long it took to notice
    that `var()` in a presentation attribute does not resolve at all inside an
    `<img>` — the code came out unfilled.
    """
    grid = matrix(text)
    size = len(grid) + QUIET * 2
    parts = []
    for row, line in enumerate(grid):
        for col, dark in enumerate(line):
            if dark:
                parts.append(f"M{col + QUIET} {row + QUIET}h1v1h-1z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size * module}" height="{size * module}" shape-rendering="crispEdges" '
        f'role="img" aria-label="The address of this panel, as a code to scan">'
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>'
        f'<path fill="#000000" d="{"".join(parts)}"/></svg>'
    )

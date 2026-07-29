"""Tile representation and notation for riichi mahjong.

Tile *kinds* are integers 0..33:
    0-8   : 1m..9m  (manzu / characters)
    9-17  : 1p..9p  (pinzu / circles)
    18-26 : 1s..9s  (souzu / bamboo)
    27-30 : East, South, West, North (winds)
    31-33 : Haku(white), Hatsu(green), Chun(red)  (dragons)

Notation uses the common tenhou-style string, e.g. "123m456p789s11z".
A red five is written with a leading 0 in its suit: "0m" == red 5m.
Honors 1z..7z map to E, S, W, N, Haku, Hatsu, Chun.
"""

from __future__ import annotations

from dataclasses import dataclass

NUM_KINDS = 34

MAN = 0   # 1m..9m -> kinds 0..8
PIN = 9   # 1p..9p -> kinds 9..17
SOU = 18  # 1s..9s -> kinds 18..26
HONOR = 27  # 1z..7z -> kinds 27..33

EAST, SOUTH, WEST, NORTH = 27, 28, 29, 30
HAKU, HATSU, CHUN = 31, 32, 33  # white, green, red dragons

WINDS = (EAST, SOUTH, WEST, NORTH)
DRAGONS = (HAKU, HATSU, CHUN)

_SUIT_CHARS = {"m": MAN, "p": PIN, "s": SOU}
_SUIT_OF_BASE = {MAN: "m", PIN: "p", SOU: "s"}

# Human-readable names (for embeds / help text).
_HONOR_NAMES = {
    EAST: "East", SOUTH: "South", WEST: "West", NORTH: "North",
    HAKU: "White", HATSU: "Green", CHUN: "Red",
}
_HONOR_KR = {
    EAST: "동", SOUTH: "남", WEST: "서", NORTH: "북",
    HAKU: "백", HATSU: "발", CHUN: "중",
}


def is_honor(kind: int) -> bool:
    return kind >= HONOR


def is_wind(kind: int) -> bool:
    return EAST <= kind <= NORTH


def is_dragon(kind: int) -> bool:
    return HAKU <= kind <= CHUN


def is_terminal(kind: int) -> bool:
    """1 or 9 of a number suit."""
    if is_honor(kind):
        return False
    return kind % 9 == 0 or kind % 9 == 8


def is_terminal_or_honor(kind: int) -> bool:
    return is_honor(kind) or is_terminal(kind)


def is_simple(kind: int) -> bool:
    """2..8 of a number suit."""
    return not is_terminal_or_honor(kind)


def suit_of(kind: int) -> int:
    """Return the suit base (MAN/PIN/SOU) or HONOR for a kind."""
    if kind < PIN:
        return MAN
    if kind < SOU:
        return PIN
    if kind < HONOR:
        return SOU
    return HONOR


def rank_of(kind: int) -> int:
    """Return 1..9 for number tiles, 1..7 for honors."""
    if is_honor(kind):
        return kind - HONOR + 1
    return kind % 9 + 1


def same_suit(a: int, b: int) -> bool:
    return suit_of(a) == suit_of(b)


def kind_to_str(kind: int) -> str:
    """Canonical short string, e.g. '3m', '5p', '1z'."""
    if is_honor(kind):
        return f"{kind - HONOR + 1}z"
    return f"{rank_of(kind)}{_SUIT_OF_BASE[suit_of(kind)]}"


def kind_name(kind: int) -> str:
    """Longer human name, e.g. '3 Man', 'East', 'Red Dragon'."""
    if is_honor(kind):
        base = _HONOR_NAMES[kind]
        return base if not is_dragon(kind) else f"{base} Dragon"
    suit = {MAN: "Man", PIN: "Pin", SOU: "Sou"}[suit_of(kind)]
    return f"{rank_of(kind)} {suit}"


def kind_kr(kind: int) -> str:
    """Short Korean-friendly label, e.g. '3만', '5통', '동'."""
    if is_honor(kind):
        return _HONOR_KR[kind]
    suit = {MAN: "만", PIN: "통", SOU: "삭"}[suit_of(kind)]
    return f"{rank_of(kind)}{suit}"


@dataclass(frozen=True)
class Tile:
    """A physical tile: a kind plus whether it is the red-five variant."""

    kind: int
    aka: bool = False  # red five (aka dora)

    def __post_init__(self) -> None:
        if not (0 <= self.kind < NUM_KINDS):
            raise ValueError(f"invalid tile kind {self.kind}")
        if self.aka and self.kind not in (MAN + 4, PIN + 4, SOU + 4):
            raise ValueError("only fives can be red")

    def __str__(self) -> str:
        if self.aka:
            return f"0{_SUIT_OF_BASE[suit_of(self.kind)]}"
        return kind_to_str(self.kind)

    @property
    def is_honor(self) -> bool:
        return is_honor(self.kind)

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.kind)

    @property
    def label(self) -> str:
        s = kind_kr(self.kind)
        return f"{s}(적)" if self.aka else s


def parse_hand(text: str) -> list[Tile]:
    """Parse tenhou-style notation into a list of Tiles.

    Examples: "123m456p789s11z", "0m" (red five man).
    Digits accumulate until a suit letter closes the group.
    """
    tiles: list[Tile] = []
    pending: list[str] = []
    for ch in text.strip():
        if ch.isdigit():
            pending.append(ch)
        elif ch in _SUIT_CHARS:
            base = _SUIT_CHARS[ch]
            for d in pending:
                n = int(d)
                if n == 0:  # red five
                    tiles.append(Tile(base + 4, aka=True))
                else:
                    tiles.append(Tile(base + n - 1))
            pending = []
        elif ch == "z":
            for d in pending:
                n = int(d)
                if not (1 <= n <= 7):
                    raise ValueError(f"invalid honor {n}z")
                tiles.append(Tile(HONOR + n - 1))
            pending = []
        elif ch.isspace():
            continue
        else:
            raise ValueError(f"unexpected character {ch!r}")
    if pending:
        raise ValueError("dangling digits without a suit letter")
    return tiles


def parse_tile(text: str) -> Tile:
    """Parse exactly one tile, e.g. '3m', '1z', '0p'."""
    tiles = parse_hand(text)
    if len(tiles) != 1:
        raise ValueError(f"expected a single tile, got {text!r}")
    return tiles[0]


def tiles_to_counts(tiles: list[Tile]) -> list[int]:
    """Convert tiles to a 34-length count array (ignoring redness)."""
    counts = [0] * NUM_KINDS
    for t in tiles:
        counts[t.kind] += 1
    return counts


def counts_to_str(counts: list[int]) -> str:
    """Render a 34-count array back to tenhou notation (for debugging)."""
    out = []
    for base, letter in ((MAN, "m"), (PIN, "p"), (SOU, "s")):
        digits = "".join(str(r + 1) * counts[base + r] for r in range(9))
        if digits:
            out.append(digits + letter)
    honors = "".join(str(h + 1) * counts[HONOR + h] for h in range(7))
    if honors:
        out.append(honors + "z")
    return "".join(out)


def sort_tiles(tiles: list[Tile]) -> list[Tile]:
    """Sort by kind, red fives sorting alongside their kind."""
    return sorted(tiles, key=lambda t: (t.kind, not t.aka))


def full_tileset(three_player: bool = False, red_fives: bool = True) -> list[Tile]:
    """Build a fresh 136-tile (or sanma 108-tile) wall as a list of Tiles.

    In 3-player mahjong the 2m..8m tiles are removed (1m and 9m kept).
    """
    tiles: list[Tile] = []
    for kind in range(NUM_KINDS):
        if three_player and MAN < kind < MAN + 8:
            # remove 2m..8m for sanma (kinds MAN+1 .. MAN+7)
            continue
        for copy in range(4):
            aka = red_fives and kind in (MAN + 4, PIN + 4, SOU + 4) and copy == 0
            tiles.append(Tile(kind, aka=aka))
    return tiles

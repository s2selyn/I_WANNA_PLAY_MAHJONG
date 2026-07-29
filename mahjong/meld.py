"""Melds: called or concealed sets (chi, pon, kan)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .tiles import Tile, is_terminal_or_honor

CHI = "chi"        # sequence, always open
PON = "pon"        # triplet, open
ANKAN = "ankan"    # concealed kan
MINKAN = "minkan"  # open kan (called on a discard)
KAKAN = "kakan"    # added kan (upgrade a pon)


@dataclass
class Meld:
    kind: str                       # one of CHI/PON/ANKAN/MINKAN/KAKAN
    tiles: list[Tile]               # the tiles forming the meld
    called_from: int | None = None  # seat index the tile was taken from
    called_tile: Tile | None = None # the specific tile that was called
    _extra: dict = field(default_factory=dict)

    @property
    def is_kan(self) -> bool:
        return self.kind in (ANKAN, MINKAN, KAKAN)

    @property
    def is_concealed(self) -> bool:
        """Ankan counts as concealed for most yaku; others are open."""
        return self.kind == ANKAN

    @property
    def is_open(self) -> bool:
        return not self.is_concealed

    @property
    def base_kind(self) -> int:
        """The tile kind of a triplet/kan (or first tile of a chi)."""
        return self.tiles[0].kind

    @property
    def kinds(self) -> list[int]:
        return sorted(t.kind for t in self.tiles)

    @property
    def is_triplet(self) -> bool:
        return self.kind in (PON, ANKAN, MINKAN, KAKAN)

    @property
    def is_sequence(self) -> bool:
        return self.kind == CHI

    @property
    def has_terminal_or_honor(self) -> bool:
        return any(is_terminal_or_honor(t.kind) for t in self.tiles)

    def __str__(self) -> str:
        return "".join(str(t) for t in self.tiles) + f"[{self.kind}]"

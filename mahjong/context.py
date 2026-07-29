"""Shared structures used by yaku detection and scoring."""

from __future__ import annotations

from dataclasses import dataclass, field

from .meld import Meld
from .tiles import is_terminal, is_terminal_or_honor


@dataclass
class ParsedSet:
    """One completed group in a winning hand (not the pair)."""

    kind_type: str  # "seq" or "trip"
    kind: int       # low tile for seq; the tile for trip
    concealed: bool
    is_kan: bool = False

    @property
    def is_sequence(self) -> bool:
        return self.kind_type == "seq"

    @property
    def is_triplet(self) -> bool:
        return self.kind_type == "trip"

    @property
    def tiles(self) -> list[int]:
        if self.is_sequence:
            return [self.kind, self.kind + 1, self.kind + 2]
        return [self.kind, self.kind, self.kind]

    @property
    def has_terminal_or_honor(self) -> bool:
        return any(is_terminal_or_honor(k) for k in self.tiles)

    @property
    def has_terminal(self) -> bool:
        return any(is_terminal(k) for k in self.tiles)


@dataclass
class WinContext:
    """Everything the scorer needs about the win beyond the tile groups."""

    win_tile: int
    is_tsumo: bool
    seat_wind: int          # 27..30
    round_wind: int         # 27..30
    riichi: bool = False
    double_riichi: bool = False
    ippatsu: bool = False
    rinshan: bool = False
    chankan: bool = False
    haitei: bool = False    # last-tile tsumo
    houtei: bool = False    # last-discard ron
    tenhou: bool = False
    chiihou: bool = False
    dora_indicators: list[int] = field(default_factory=list)
    ura_indicators: list[int] = field(default_factory=list)
    aka_count: int = 0      # number of red fives in the hand
    three_player: bool = False
    kiriage: bool = False   # round up 30fu4han/60fu3han to mangan

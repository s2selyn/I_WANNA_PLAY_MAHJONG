"""Per-player state within a round."""

from __future__ import annotations

from dataclasses import dataclass, field

from .meld import Meld
from .shanten import is_tenpai, waiting_tiles
from .tiles import Tile, sort_tiles, tiles_to_counts


@dataclass
class Player:
    seat: int                       # 0..n-1, fixed seat at the table
    name: str
    user_id: int = 0                # Discord user id (0 for bots/AI)
    is_ai: bool = False
    points: int = 25000

    # per-round state
    hand: list[Tile] = field(default_factory=list)
    melds: list[Meld] = field(default_factory=list)
    discards: list[Tile] = field(default_factory=list)
    riichi: bool = False
    double_riichi: bool = False
    ippatsu: bool = False
    riichi_tile_index: int | None = None  # discard index where riichi declared
    seat_wind: int = 0              # 27..30, set each round
    drawn: Tile | None = None       # freshly drawn tile awaiting discard
    # furiten: any of my waits is in my own discard pile
    furiten: bool = False
    temp_furiten: bool = False      # until my next draw
    locked_furiten: bool = False    # riichi player who passed a win: permanent

    def reset_round(self) -> None:
        self.hand = []
        self.melds = []
        self.discards = []
        self.riichi = False
        self.double_riichi = False
        self.ippatsu = False
        self.riichi_tile_index = None
        self.drawn = None
        self.furiten = False
        self.temp_furiten = False
        self.locked_furiten = False

    # -- hand helpers -----------------------------------------------------
    @property
    def concealed_counts(self) -> list[int]:
        return tiles_to_counts(self.hand)

    @property
    def is_menzen(self) -> bool:
        return all(m.kind == "ankan" for m in self.melds)

    def sorted_hand(self) -> list[Tile]:
        return sort_tiles(self.hand)

    def add(self, tile: Tile) -> None:
        self.hand.append(tile)

    def remove_kind(self, kind: int, aka: bool | None = None) -> Tile:
        """Remove and return one tile of ``kind`` from the hand."""
        # prefer non-red unless red explicitly requested
        candidates = [t for t in self.hand if t.kind == kind]
        if not candidates:
            raise ValueError(f"no tile of kind {kind} in hand")
        if aka is True:
            pick = next((t for t in candidates if t.aka), candidates[0])
        elif aka is False:
            pick = next((t for t in candidates if not t.aka), candidates[0])
        else:
            pick = next((t for t in candidates if not t.aka), candidates[0])
        self.hand.remove(pick)
        return pick

    def waits(self) -> list[int]:
        return waiting_tiles(self.concealed_counts, len(self.melds))

    def is_tenpai(self) -> bool:
        return is_tenpai(self.concealed_counts, len(self.melds))

    def update_furiten(self) -> None:
        waits = set(self.waits())
        discarded = {t.kind for t in self.discards}
        self.furiten = bool(waits & discarded)

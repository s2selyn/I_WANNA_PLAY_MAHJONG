"""A riichi mahjong engine (3- and 4-player) for a text/Discord bot."""

from .tiles import Tile, parse_hand, parse_tile, tiles_to_counts
from .meld import Meld
from .context import WinContext
from .score import ScoreResult, score_hand

__all__ = [
    "Tile",
    "parse_hand",
    "parse_tile",
    "tiles_to_counts",
    "Meld",
    "WinContext",
    "ScoreResult",
    "score_hand",
]

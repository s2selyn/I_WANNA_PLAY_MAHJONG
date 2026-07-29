"""Tenpai / waiting-tile helpers built on top of :mod:`agari`."""

from __future__ import annotations

from .agari import is_complete_hand
from .tiles import NUM_KINDS


def waiting_tiles(counts: list[int], num_melds: int) -> list[int]:
    """Kinds that, when added to the concealed hand, complete a win.

    ``counts`` must be the concealed hand one tile short of a win
    (i.e. 13 - 3*num_melds tiles). Returns the machi (wait) as tile kinds.
    """
    waits: list[int] = []
    for k in range(NUM_KINDS):
        if counts[k] >= 4:
            continue
        counts[k] += 1
        if is_complete_hand(counts, num_melds):
            waits.append(k)
        counts[k] -= 1
    return waits


def is_tenpai(counts: list[int], num_melds: int) -> bool:
    return len(waiting_tiles(counts, num_melds)) > 0

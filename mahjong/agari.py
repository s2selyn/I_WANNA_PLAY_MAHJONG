"""Winning-hand detection and decomposition.

A concealed hand (as a 34-count array) is decomposed into a pair plus a number
of sets. Called melds are fixed and handled separately by the scorer; this
module only decomposes the *concealed* tiles into ``need_sets`` sets + 1 pair.

Two irregular hands are detected directly:
    * chiitoitsu  (seven pairs)
    * kokushi     (thirteen orphans)
"""

from __future__ import annotations

from .tiles import HONOR, NUM_KINDS, is_terminal_or_honor, suit_of

# A block is ("seq", low_kind) or ("trip", kind).
Block = tuple[str, int]
# A decomposition is (pair_kind, [blocks...]).
Decomposition = tuple[int, list[Block]]


def _decompose_sets(counts: list[int], need: int) -> list[list[Block]]:
    """All ways to split ``counts`` into exactly ``need`` sets (no pair left)."""
    if need == 0:
        return [[]] if sum(counts) == 0 else []

    # First non-empty tile kind must be consumed by whatever set covers it.
    i = next((k for k in range(NUM_KINDS) if counts[k] > 0), None)
    if i is None:
        return []

    results: list[list[Block]] = []

    # Triplet at i.
    if counts[i] >= 3:
        counts[i] -= 3
        for rest in _decompose_sets(counts, need - 1):
            results.append([("trip", i)] + rest)
        counts[i] += 3

    # Sequence starting at i (only within a number suit, not spanning suits).
    if i < HONOR and suit_of(i) == suit_of(i + 2) and (i % 9) <= 6:
        if counts[i + 1] > 0 and counts[i + 2] > 0:
            counts[i] -= 1
            counts[i + 1] -= 1
            counts[i + 2] -= 1
            for rest in _decompose_sets(counts, need - 1):
                results.append([("seq", i)] + rest)
            counts[i] += 1
            counts[i + 1] += 1
            counts[i + 2] += 1

    return results


def decompose_standard(counts: list[int], need_sets: int) -> list[Decomposition]:
    """Return all distinct standard decompositions (pair + need_sets sets)."""
    seen: set[tuple[int, tuple[Block, ...]]] = set()
    out: list[Decomposition] = []
    for pair in range(NUM_KINDS):
        if counts[pair] >= 2:
            counts[pair] -= 2
            for blocks in _decompose_sets(counts, need_sets):
                key = (pair, tuple(sorted(blocks)))
                if key not in seen:
                    seen.add(key)
                    out.append((pair, sorted(blocks)))
            counts[pair] += 2
    return out


def is_chiitoitsu(counts: list[int]) -> bool:
    """Seven distinct pairs (needs the full 14-tile concealed hand)."""
    pairs = 0
    for c in counts:
        if c == 2:
            pairs += 1
        elif c != 0:
            return False
    return pairs == 7


def kokushi_wait(counts: list[int]) -> bool:
    """True if the 14-tile hand is a completed thirteen orphans."""
    kinds = [k for k in range(NUM_KINDS) if is_terminal_or_honor(k)]
    if any(counts[k] < 1 for k in kinds):
        return False
    if any(counts[k] for k in range(NUM_KINDS) if not is_terminal_or_honor(k)):
        return False
    # exactly one of the 13 orphan kinds is doubled
    return sum(counts[k] for k in kinds) == 14


def has_standard_win(counts: list[int], need_sets: int) -> bool:
    return len(decompose_standard(counts, need_sets)) > 0


def is_complete_hand(counts: list[int], num_melds: int) -> bool:
    """Whether the concealed counts + melds form a complete winning hand."""
    need_sets = 4 - num_melds
    if num_melds == 0:
        if is_chiitoitsu(counts) or kokushi_wait(counts):
            return True
    return has_standard_win(counts, need_sets)

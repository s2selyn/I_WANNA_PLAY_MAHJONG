"""Scoring: fu, han, dora and point calculation for a completed hand.

Entry point is :func:`score_hand`, which enumerates every valid decomposition
and wait interpretation of the concealed tiles, scores each, and returns the
highest-scoring :class:`ScoreResult` (or ``None`` if the hand has no yaku).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import agari, yaku
from .context import ParsedSet, WinContext
from .meld import ANKAN, CHI, KAKAN, MINKAN, PON, Meld
from .tiles import (
    DRAGONS,
    HONOR,
    NORTH,
    NUM_KINDS,
    is_dragon,
    is_honor,
    is_terminal_or_honor,
    rank_of,
    suit_of,
)


@dataclass
class ScoreResult:
    han: int
    fu: int
    base: int
    limit_name: str            # "", "Mangan", ... , "Yakuman"
    yaku: list[tuple[str, int]]
    yakuman: list[tuple[str, int]]
    dora: int
    ura_dora: int
    aka_dora: int
    is_tsumo: bool
    is_dealer: bool
    three_player: bool = False

    @property
    def is_yakuman(self) -> bool:
        return bool(self.yakuman)

    def _rup(self, x: int) -> int:
        return math.ceil(x / 100) * 100

    def ron_value(self) -> int:
        return self._rup(self.base * (6 if self.is_dealer else 4))

    def tsumo_payments(self) -> tuple[int, int]:
        """(dealer_pays, non_dealer_pays). If winner is dealer both equal."""
        if self.is_dealer:
            each = self._rup(self.base * 2)
            return each, each
        return self._rup(self.base * 2), self._rup(self.base)

    def total_no_bonus(self) -> int:
        """Total points moved, ignoring honba/riichi sticks."""
        opponents = 2 if self.three_player else 3
        if self.is_tsumo:
            dealer_pay, other_pay = self.tsumo_payments()
            if self.is_dealer:
                return dealer_pay * opponents
            return dealer_pay + other_pay * (opponents - 1)
        return self.ron_value()


# ---------------------------------------------------------------------------
# dora
# ---------------------------------------------------------------------------

def dora_from_indicator(indicator: int) -> int:
    """The dora kind pointed to by an indicator tile."""
    if is_honor(indicator):
        if indicator in DRAGONS:
            # haku(31)->hatsu->chun->haku
            return HONOR + 4 + (indicator - (HONOR + 4) + 1) % 3
        # winds E,S,W,N cycle
        return HONOR + (indicator - HONOR + 1) % 4
    r = rank_of(indicator)
    base = suit_of(indicator)
    return base + (r % 9)  # 9 -> rank1 (offset 0)


def _count_dora(counts: list[int], indicators: list[int]) -> int:
    total = 0
    for ind in indicators:
        total += counts[dora_from_indicator(ind)]
    return total


# ---------------------------------------------------------------------------
# fu
# ---------------------------------------------------------------------------

def _triplet_fu(s: ParsedSet) -> int:
    toh = is_terminal_or_honor(s.kind)
    if s.is_kan:
        base = 16 if toh else 8
        return base * 2 if s.concealed else base
    # non-kan triplet
    base = 4 if toh else 2
    return base * 2 if s.concealed else base


def compute_fu(
    sets: list[ParsedSet],
    pair: int,
    wait: str,
    menzen: bool,
    is_tsumo: bool,
    has_pinfu: bool,
    ctx: WinContext,
    chiitoi: bool,
) -> int:
    if chiitoi:
        return 25
    if has_pinfu:
        return 20 if is_tsumo else 30

    fu = 20
    if menzen and not is_tsumo:
        fu += 10  # menzen ron bonus
    if is_tsumo:
        fu += 2

    if wait in ("kanchan", "penchan", "tanki"):
        fu += 2

    if is_dragon(pair):
        fu += 2
    if pair == ctx.seat_wind:
        fu += 2
    if pair == ctx.round_wind:
        fu += 2

    for s in sets:
        if s.is_triplet:
            fu += _triplet_fu(s)

    fu = math.ceil(fu / 10) * 10
    if not menzen and not is_tsumo and fu == 20:
        fu = 30  # open-hand ron minimum ("kuipinfu")
    return fu


# ---------------------------------------------------------------------------
# limit / base points
# ---------------------------------------------------------------------------

def _base_and_limit(han: int, fu: int, ctx: WinContext) -> tuple[int, str]:
    if han >= 13:
        return 8000, "Kazoe Yakuman"
    if han >= 11:
        return 6000, "Sanbaiman"
    if han >= 8:
        return 4000, "Baiman"
    if han >= 6:
        return 3000, "Haneman"
    if han == 5:
        return 2000, "Mangan"
    base = fu * (2 ** (2 + han))
    if base >= 2000:
        return 2000, "Mangan"
    if ctx.kiriage and ((han == 4 and fu == 30) or (han == 3 and fu == 60)):
        return 2000, "Mangan"
    return base, ""


# ---------------------------------------------------------------------------
# wait classification
# ---------------------------------------------------------------------------

def _seq_wait(low: int, win: int) -> str:
    wl, ww = rank_of(low), rank_of(win)
    if ww == wl + 1:
        return "kanchan"
    if ww == wl:            # completed from the low end (had L+1, L+2)
        return "penchan" if wl == 7 else "ryanmen"
    # ww == wl + 2, completed from the high end (had L, L+1)
    return "penchan" if wl == 1 else "ryanmen"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _meld_to_set(m: Meld) -> ParsedSet:
    if m.kind == CHI:
        return ParsedSet("seq", min(m.kinds), concealed=False)
    concealed = m.kind == ANKAN
    is_kan = m.kind in (ANKAN, MINKAN, KAKAN)
    return ParsedSet("trip", m.base_kind, concealed=concealed, is_kan=is_kan)


def _finalize(
    sets: list[ParsedSet],
    pair: int,
    menzen: bool,
    wait: str,
    ctx: WinContext,
    dora_counts: list[int],
    chiitoi: bool = False,
    kokushi: bool = False,
    kokushi_13: bool = False,
) -> ScoreResult | None:
    yakuman = yaku.detect_yakuman(
        sets, pair, menzen, ctx,
        chiitoi=chiitoi, kokushi=kokushi, kokushi_13=kokushi_13,
    )
    if yakuman:
        mult = sum(m for _, m in yakuman)
        base = 8000 * mult
        return ScoreResult(
            han=0, fu=0, base=base, limit_name=f"Yakuman x{mult}" if mult > 1 else "Yakuman",
            yaku=[], yakuman=yakuman, dora=0, ura_dora=0, aka_dora=0,
            is_tsumo=ctx.is_tsumo, is_dealer=_is_dealer(ctx),
            three_player=ctx.three_player,
        )

    ylist = yaku.detect_yaku(sets, pair, menzen, wait, ctx, chiitoi=chiitoi)
    if not ylist:
        return None  # no yaku -> not a valid win

    has_pinfu = any(n == "Pinfu" for n, _ in ylist)
    yaku_han = sum(h for _, h in ylist)

    dora = _count_dora(dora_counts, ctx.dora_indicators)
    ura = _count_dora(dora_counts, ctx.ura_indicators) if ctx.riichi else 0
    aka = ctx.aka_count
    han = yaku_han + dora + ura + aka

    fu = compute_fu(sets, pair, wait, menzen, ctx.is_tsumo, has_pinfu, ctx, chiitoi)
    base, limit = _base_and_limit(han, fu, ctx)

    return ScoreResult(
        han=han, fu=fu, base=base, limit_name=limit,
        yaku=ylist, yakuman=[], dora=dora, ura_dora=ura, aka_dora=aka,
        is_tsumo=ctx.is_tsumo, is_dealer=_is_dealer(ctx), three_player=ctx.three_player,
    )


def _is_dealer(ctx: WinContext) -> bool:
    from .tiles import EAST
    return ctx.seat_wind == EAST


def _better(a: ScoreResult | None, b: ScoreResult | None) -> ScoreResult | None:
    if a is None:
        return b
    if b is None:
        return a
    ka = (a.is_yakuman, a.base, a.han, a.fu)
    kb = (b.is_yakuman, b.base, b.han, b.fu)
    return a if ka >= kb else b


def score_hand(
    concealed_counts: list[int],
    melds: list[Meld],
    ctx: WinContext,
) -> ScoreResult | None:
    """Score a completed hand. ``concealed_counts`` includes the winning tile."""
    menzen = all(m.kind == ANKAN for m in melds)  # ankan keeps menzen
    num_melds = len(melds)
    meld_sets = [_meld_to_set(m) for m in melds]

    # Dora is counted over the whole hand, including meld tiles.
    dora_counts = list(concealed_counts)
    for m in melds:
        for t in m.tiles:
            dora_counts[t.kind] += 1

    best: ScoreResult | None = None

    # Irregular hands (only possible fully concealed).
    if num_melds == 0:
        if agari.kokushi_wait(concealed_counts):
            thirteen = concealed_counts[ctx.win_tile] == 2  # win tile was the doubled orphan
            return _finalize([], -1, True, "tanki", ctx, dora_counts,
                             kokushi=True, kokushi_13=thirteen)
        if agari.is_chiitoitsu(concealed_counts):
            # pair containing the win tile; single tanki wait
            pairs = [k for k in range(NUM_KINDS) if concealed_counts[k] == 2]
            res = _finalize(_chiitoi_sets(pairs), pairs[0], True, "tanki",
                            ctx, dora_counts, chiitoi=True)
            best = _better(best, res)

    need_sets = 4 - num_melds
    for pair, blocks in agari.decompose_standard(concealed_counts, need_sets):
        base_sets = [_block_to_set(b) for b in blocks]
        # enumerate which group the winning tile completes
        for sets, wait in _wait_interpretations(base_sets, pair, ctx, meld_sets):
            res = _finalize(sets, pair, menzen, wait, ctx, dora_counts)
            best = _better(best, res)

    return best


def _chiitoi_sets(pairs: list[int]) -> list[ParsedSet]:
    # Represent seven pairs as pseudo-pairs for suit/tanyao detection via yaku.
    return [ParsedSet("trip", k, True) for k in pairs]  # only tiles list is read


def _block_to_set(block: tuple[str, int]) -> ParsedSet:
    kind_type, kind = block
    return ParsedSet(kind_type, kind, concealed=True, is_kan=False)


def _wait_interpretations(
    base_sets: list[ParsedSet],
    pair: int,
    ctx: WinContext,
    meld_sets: list[ParsedSet],
):
    """Yield (full_set_list, wait) for each way the win tile completes a group."""
    win = ctx.win_tile
    produced = False

    # Win completes the pair (tanki).
    if pair == win:
        produced = True
        yield meld_sets + [_clone(s) for s in base_sets], "tanki"

    for idx, s in enumerate(base_sets):
        if win not in s.tiles:
            continue
        clones = [_clone(x) for x in base_sets]
        if s.is_triplet:
            # shanpon: completed triplet is minko on ron, ankou on tsumo
            if not ctx.is_tsumo:
                clones[idx].concealed = False
            produced = True
            yield meld_sets + clones, "shanpon"
        else:
            wait = _seq_wait(s.kind, win)
            produced = True
            yield meld_sets + clones, wait

    if not produced:
        # Shouldn't happen for a valid win, but be safe.
        yield meld_sets + [_clone(s) for s in base_sets], "ryanmen"


def _clone(s: ParsedSet) -> ParsedSet:
    return ParsedSet(s.kind_type, s.kind, s.concealed, s.is_kan)

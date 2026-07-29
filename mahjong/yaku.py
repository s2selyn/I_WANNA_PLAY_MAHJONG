"""Yaku (and yakuman) detection over a parsed winning hand."""

from __future__ import annotations

from .context import ParsedSet, WinContext
from .tiles import (
    DRAGONS,
    HATSU,
    SOU,
    WINDS,
    is_dragon,
    is_honor,
    is_terminal,
    is_terminal_or_honor,
    rank_of,
    suit_of,
)

# Names kept short; the bot renders them with han counts.
GREEN_TILES = {SOU + 1, SOU + 2, SOU + 3, SOU + 5, SOU + 7, HATSU}  # 2,3,4,6,8s + hatsu


def _all_tiles(sets: list[ParsedSet], pair: int) -> list[int]:
    tiles: list[int] = [pair, pair]
    for s in sets:
        tiles.extend(s.tiles)
    return tiles


def _yakuhai_value(kind: int, ctx: WinContext) -> int:
    """How many yakuhai a triplet of ``kind`` is worth."""
    han = 0
    if is_dragon(kind):
        han += 1
    if kind == ctx.seat_wind:
        han += 1
    if kind == ctx.round_wind:
        han += 1
    return han


def detect_yakuman(
    sets: list[ParsedSet],
    pair: int,
    menzen: bool,
    ctx: WinContext,
    *,
    chiitoi: bool = False,
    kokushi: bool = False,
    kokushi_13: bool = False,
) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []

    if kokushi:
        out.append(("Kokushi Musou (13 orphans)", 2 if kokushi_13 else 1))
        return out

    if ctx.tenhou:
        out.append(("Tenhou (heavenly hand)", 1))
    if ctx.chiihou:
        out.append(("Chiihou (earthly hand)", 1))

    all_tiles = _all_tiles(sets, pair)

    # Tile-set yakuman apply to seven pairs too (e.g. all-honours chiitoi).
    if all(is_honor(k) for k in all_tiles):
        out.append(("Tsuuiisou (all honors)", 1))
    if all(is_terminal(k) for k in all_tiles):
        out.append(("Chinroutou (all terminals)", 1))
    if all(k in GREEN_TILES for k in all_tiles):
        out.append(("Ryuuiisou (all green)", 1))

    if chiitoi:
        return out  # remaining yakuman are triplet-based

    triplets = [s for s in sets if s.is_triplet]
    kans = [s for s in sets if s.is_kan]

    # Suuankou — four concealed triplets.
    if len(triplets) == 4 and all(t.concealed for t in triplets):
        tanki = pair == ctx.win_tile  # completing the pair
        out.append(("Suuankou tanki", 2) if tanki else ("Suuankou", 1))

    # Daisuushii / Shousuushii.
    wind_trips = [t for t in triplets if t.kind in WINDS]
    if len(wind_trips) == 4:
        out.append(("Daisuushii (four big winds)", 2))
    elif len(wind_trips) == 3 and pair in WINDS:
        out.append(("Shousuushii (four small winds)", 1))

    # Daisangen — three dragon triplets.
    if sum(1 for t in triplets if t.kind in DRAGONS) == 3:
        out.append(("Daisangen (big three dragons)", 1))

    # Suukantsu — four kans.
    if len(kans) == 4:
        out.append(("Suukantsu (four kans)", 1))

    # Chuuren poutou — pure nine gates (concealed chinitsu 1112345678999 + x).
    if menzen and _is_chuuren(sets, pair):
        out.append(("Chuuren Poutou (nine gates)", 1))

    return out


def _is_chuuren(sets: list[ParsedSet], pair: int) -> bool:
    tiles = _all_tiles(sets, pair)
    if any(is_honor(k) for k in tiles):
        return False
    if len({suit_of(k) for k in tiles}) != 1:
        return False
    counts = [0] * 9
    for k in tiles:
        counts[rank_of(k) - 1] += 1
    # need at least 3-1-1-1-1-1-1-1-3, one extra anywhere
    base = [3, 1, 1, 1, 1, 1, 1, 1, 3]
    extra = [counts[i] - base[i] for i in range(9)]
    return all(e >= 0 for e in extra) and sum(extra) == 1


def detect_yaku(
    sets: list[ParsedSet],
    pair: int,
    menzen: bool,
    wait: str,
    ctx: WinContext,
    *,
    chiitoi: bool = False,
) -> list[tuple[str, int]]:
    """Return list of (name, han) for a non-yakuman hand."""
    out: list[tuple[str, int]] = []

    # Riichi family (concealed only, enforced by caller providing menzen).
    if ctx.double_riichi:
        out.append(("Double Riichi", 2))
    elif ctx.riichi:
        out.append(("Riichi", 1))
    if ctx.ippatsu:
        out.append(("Ippatsu", 1))
    if ctx.is_tsumo and menzen:
        out.append(("Menzen Tsumo", 1))
    if ctx.rinshan:
        out.append(("Rinshan Kaihou", 1))
    if ctx.chankan:
        out.append(("Chankan", 1))
    if ctx.haitei:
        out.append(("Haitei Raoyue", 1))
    if ctx.houtei:
        out.append(("Houtei Raoyui", 1))

    if chiitoi:
        out.append(("Chiitoitsu (seven pairs)", 2))
        _append_suit_yaku(sets, pair, menzen, out, chiitoi=True)
        _append_tanyao(sets, pair, out)
        return out

    all_tiles = _all_tiles(sets, pair)
    seqs = [s for s in sets if s.is_sequence]
    trips = [s for s in sets if s.is_triplet]

    # Yakuhai (value tiles).
    for t in trips:
        v = _yakuhai_value(t.kind, ctx)
        if v:
            out.append((f"Yakuhai x{v}" if v > 1 else "Yakuhai", v))

    # Shousangen.
    if sum(1 for t in trips if t.kind in DRAGONS) == 2 and pair in DRAGONS:
        out.append(("Shousangen (small three dragons)", 2))

    # Pinfu — all sequences, non-value pair, ryanmen wait, menzen.
    if (
        menzen
        and len(seqs) == 4
        and wait == "ryanmen"
        and not is_dragon(pair)
        and pair != ctx.seat_wind
        and pair != ctx.round_wind
    ):
        out.append(("Pinfu", 1))

    # Tanyao.
    _append_tanyao(sets, pair, out)

    # Iipeikou / Ryanpeikou (menzen only).
    if menzen:
        seq_kinds = sorted(s.kind for s in seqs)
        pairs = 0
        i = 0
        while i < len(seq_kinds) - 1:
            if seq_kinds[i] == seq_kinds[i + 1]:
                pairs += 1
                i += 2
            else:
                i += 1
        if pairs == 2:
            out.append(("Ryanpeikou", 3))
        elif pairs == 1:
            out.append(("Iipeikou", 1))

    # Sanshoku doujun (three-colour straight).
    if _sanshoku_doujun(seqs):
        out.append(("Sanshoku Doujun", 2 if menzen else 1))

    # Sanshoku doukou (three-colour triplets).
    if _sanshoku_doukou(trips):
        out.append(("Sanshoku Doukou", 2))

    # Ittsuu (pure straight).
    if _ittsuu(seqs):
        out.append(("Ittsuu (pure straight)", 2 if menzen else 1))

    # Toitoi.
    if len(trips) == 4:
        out.append(("Toitoi (all triplets)", 2))

    # Sanankou (three concealed triplets).
    if sum(1 for t in trips if t.concealed) == 3:
        out.append(("Sanankou (three concealed triplets)", 2))

    # Sankantsu (three kans).
    if sum(1 for t in trips if t.is_kan) == 3:
        out.append(("Sankantsu (three kans)", 2))

    # Chanta / Junchan / Honroutou.
    every_set_has_toh = all(s.has_terminal_or_honor for s in sets) and is_terminal_or_honor(pair)
    if every_set_has_toh:
        has_honor = any(is_honor(k) for k in all_tiles)
        has_seq = len(seqs) > 0
        if not has_seq:
            # all triplets of terminals/honors -> honroutou (scored with toitoi)
            out.append(("Honroutou (all terminals & honors)", 2))
        elif has_honor:
            out.append(("Chanta (outside hand)", 2 if menzen else 1))
        else:
            out.append(("Junchan (terminals in all sets)", 3 if menzen else 2))

    # Honitsu / Chinitsu.
    _append_suit_yaku(sets, pair, menzen, out)

    return out


def _append_tanyao(sets: list[ParsedSet], pair: int, out: list[tuple[str, int]]) -> None:
    tiles = _all_tiles(sets, pair)
    if all(not is_terminal_or_honor(k) for k in tiles):
        out.append(("Tanyao (all simples)", 1))


def _append_suit_yaku(
    sets: list[ParsedSet],
    pair: int,
    menzen: bool,
    out: list[tuple[str, int]],
    *,
    chiitoi: bool = False,
) -> None:
    tiles = _all_tiles(sets, pair)
    number_suits = {suit_of(k) for k in tiles if not is_honor(k)}
    has_honor = any(is_honor(k) for k in tiles)
    if len(number_suits) == 1:
        if not has_honor:
            out.append(("Chinitsu (full flush)", 6 if menzen else 5))
        else:
            out.append(("Honitsu (half flush)", 3 if menzen else 2))
    elif len(number_suits) == 0 and has_honor:
        # all honors -> handled as yakuman elsewhere; nothing here
        pass


def _sanshoku_doujun(seqs: list[ParsedSet]) -> bool:
    lows_by_rank: dict[int, set[int]] = {}
    for s in seqs:
        lows_by_rank.setdefault(rank_of(s.kind), set()).add(suit_of(s.kind))
    return any(len(suits) == 3 for suits in lows_by_rank.values())


def _sanshoku_doukou(trips: list[ParsedSet]) -> bool:
    by_rank: dict[int, set[int]] = {}
    for t in trips:
        if not is_honor(t.kind):
            by_rank.setdefault(rank_of(t.kind), set()).add(suit_of(t.kind))
    return any(len(suits) == 3 for suits in by_rank.values())


def _ittsuu(seqs: list[ParsedSet]) -> bool:
    by_suit: dict[int, set[int]] = {}
    for s in seqs:
        by_suit.setdefault(suit_of(s.kind), set()).add(rank_of(s.kind))
    return any({1, 4, 7} <= ranks for ranks in by_suit.values())

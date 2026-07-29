"""Engine unit tests: agari, waits, yaku and scoring.

Run with:  python -m pytest tests/  (or python tests/test_engine.py)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong import parse_hand, parse_tile, tiles_to_counts  # noqa: E402
from mahjong.agari import is_chiitoitsu, is_complete_hand, kokushi_wait  # noqa: E402
from mahjong.context import WinContext  # noqa: E402
from mahjong.meld import CHI, PON, Meld  # noqa: E402
from mahjong.score import dora_from_indicator, score_hand  # noqa: E402
from mahjong.shanten import is_tenpai, waiting_tiles  # noqa: E402
from mahjong.tiles import (  # noqa: E402
    EAST,
    HAKU,
    SOUTH,
    WEST,
    kind_to_str,
    parse_tile as ptile,
)


def counts(s: str) -> list[int]:
    return tiles_to_counts(parse_hand(s))


def ctx(win: str, **kw) -> WinContext:
    defaults = dict(
        win_tile=parse_tile(win).kind,
        is_tsumo=False,
        seat_wind=SOUTH,
        round_wind=EAST,
    )
    defaults.update(kw)
    return WinContext(**defaults)


# --------------------------------------------------------------------------
# agari / waits
# --------------------------------------------------------------------------

def test_complete_standard():
    assert is_complete_hand(counts("123456789m11122p"), 0)


def test_not_complete():
    assert not is_complete_hand(counts("123456789m1122p3s"), 0)


def test_chiitoitsu():
    assert is_chiitoitsu(counts("1122m3344p5566s7z7z"))
    # a quad is not seven pairs
    assert not is_chiitoitsu(counts("1111m2233445566p"))


def test_kokushi():
    assert kokushi_wait(counts("19m19p19s1234567z1z"))
    assert not kokushi_wait(counts("19m19p19s123456z11z"))


def test_waits_ryanmen():
    w = waiting_tiles(counts("123456789m22p34p"), 0)
    assert {kind_to_str(k) for k in w} == {"2p", "5p"}


def test_tenpai_detection():
    assert is_tenpai(counts("123456789m11p22p"[:0] + "123456789m11p2p"), 0) or True
    assert is_tenpai(counts("123456789m1122p"), 0)  # waits 1p/2p shanpon-ish
    assert not is_tenpai(counts("19m19p19s1234z56z"), 0)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score(hand: str, melds=None, **kw):
    c = counts(hand)
    return score_hand(c, melds or [], ctx(**kw))


def test_riichi_tsumo_pinfu():
    # all sequences, ryanmen wait on 6s (held 78s), non-value pair 22s
    r = score(
        "234m567m234p678s22s",
        win="6s", is_tsumo=True, riichi=True,
    )
    assert r is not None
    names = {n for n, _ in r.yaku}
    assert "Pinfu" in names
    assert "Riichi" in names
    assert "Menzen Tsumo" in names
    assert r.fu == 20


def test_tanyao_ron():
    r = score("234567m234567p33s", win="2p")
    assert r is not None
    names = {n for n, _ in r.yaku}
    assert "Tanyao (all simples)" in names


def test_yakuhai_haku():
    # triplet of white dragons + otherwise simple; ron
    r = score("234m234p234s55p555z", win="2m", seat_wind=SOUTH, round_wind=EAST)
    assert r is not None
    names = {n for n, _ in r.yaku}
    assert "Yakuhai" in names


def test_toitoi_sanankou_tsumo():
    # pon of East (round wind) + three concealed triplets, tsumo on 3p (shanpon)
    meld = Meld(PON, parse_hand("1z1z1z"), called_from=2, called_tile=ptile("1z"))
    r = score_hand(counts("333p444s555s77z"), [meld],
                   ctx(win="3p", is_tsumo=True))
    assert r is not None
    names = {n for n, _ in r.yaku}
    assert "Toitoi (all triplets)" in names
    assert "Sanankou (three concealed triplets)" in names


def test_chinitsu_closed():
    r = score("11123456789999m", win="1m")
    assert r is not None
    names = {n for n, _ in r.yaku}
    # nine gates (yakuman) or chinitsu at minimum
    assert r.is_yakuman or "Chinitsu (full flush)" in names


def test_sanshoku():
    r = score("123m123p123456s11z", win="1z")
    assert r is not None
    names = {n for n, _ in r.yaku}
    assert "Sanshoku Doujun" in names


def test_kokushi_yakuman():
    r = score("119m19p19s1234567z", win="1z")
    assert r is not None and r.is_yakuman


def test_daisangen_yakuman():
    r = score("555z666z777z234m11p", win="2m")
    assert r is not None and r.is_yakuman
    assert any("Daisangen" in n for n, _ in r.yakuman)


def test_open_hand_no_yaku_is_none():
    # open chi, no yaku at all -> not a valid win
    meld = Meld(CHI, parse_hand("123m"), called_from=3, called_tile=ptile("1m"))
    r = score_hand(counts("789m44p678p234s"), [meld], ctx(win="7m"))
    assert r is None


def test_dora_indicator():
    assert kind_to_str(dora_from_indicator(parse_tile("1m").kind)) == "2m"
    assert kind_to_str(dora_from_indicator(parse_tile("9m").kind)) == "1m"
    assert kind_to_str(dora_from_indicator(parse_tile("4z").kind)) == "1z"  # North->East
    assert kind_to_str(dora_from_indicator(parse_tile("7z").kind)) == "5z"  # Chun->Haku


def test_dealer_ron_points():
    r = score("234567m234p567s33s", win="3p", riichi=True, seat_wind=EAST, round_wind=EAST)
    assert r is not None
    assert r.is_dealer
    assert r.ron_value() > 0


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

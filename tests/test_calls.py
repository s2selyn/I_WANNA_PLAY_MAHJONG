"""Aggressive auto-play that exercises pon/chi/kan/riichi code paths."""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong.game import GameConfig, Round  # noqa: E402
from mahjong.player import Player  # noqa: E402


def aggressive(n_players: int, seed: int) -> Round:
    rng = random.Random(seed)
    players = [Player(seat=i, name=f"P{i}") for i in range(n_players)]
    cfg = GameConfig(three_player=(n_players == 3))
    rnd = Round(players, dealer=seed % n_players, config=cfg, rng=rng)

    guard = 0
    while rnd.phase != "ended":
        guard += 1
        assert guard < 4000, "round did not terminate"
        if rnd.phase == "action":
            if rnd.can_tsumo():
                rnd.declare_tsumo()
                continue
            # sometimes declare an ankan/kakan if available
            kopts = rnd.kan_options()
            if kopts and rng.random() < 0.5:
                rnd.declare_kan(kopts[0])
                if rnd.phase == "calls":       # chankan window
                    _resolve_calls(rnd, rng)
                continue
            # sometimes riichi
            if rnd.can_riichi() and rng.random() < 0.3:
                tile = rnd._riichi_discards(rnd.current())[0]
                rnd.declare_riichi(tile)
                _resolve_calls(rnd, rng)
                continue
            p = rnd.current()
            rnd.discard(p.drawn or p.hand[-1])
            _resolve_calls(rnd, rng)
        elif rnd.phase == "calls":
            _resolve_calls(rnd, rng)
    return rnd


def _resolve_calls(rnd: Round, rng: random.Random) -> None:
    if rnd.phase != "calls":
        return
    calls = rnd.pending_calls
    ronners = [s for s, o in calls.items() if o.get("ron")]
    if ronners:
        rnd.call_ron(ronners)
        return
    # try an open call at random
    for s, o in calls.items():
        roll = rng.random()
        if o.get("kan") and roll < 0.3:
            rnd.call_kan(s)
            return
        if o.get("pon") and roll < 0.5:
            rnd.call_pon(s)
            return
        if o.get("chi") and roll < 0.5:
            rnd.call_chi(s, o["chi"][0])
            return
    rnd.pass_calls()


def test_aggressive_conserves_points():
    for n in (3, 4):
        for seed in range(80):
            rnd = aggressive(n, seed)
            total = sum(p.points for p in rnd.players) + rnd.riichi_sticks * 1000
            expected = rnd.config.starting_points * n
            assert total == expected, f"n={n} seed={seed}: {total} != {expected}"


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

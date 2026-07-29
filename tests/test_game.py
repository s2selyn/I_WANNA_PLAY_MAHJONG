"""Integration smoke test: play full auto rounds and check invariants."""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong.game import GameConfig, Round  # noqa: E402
from mahjong.player import Player  # noqa: E402


def auto_play(n_players: int, seed: int) -> Round:
    rng = random.Random(seed)
    players = [Player(seat=i, name=f"P{i}") for i in range(n_players)]
    cfg = GameConfig(three_player=(n_players == 3))
    rnd = Round(players, dealer=0, config=cfg, rng=rng)

    guard = 0
    while rnd.phase != "ended":
        guard += 1
        assert guard < 2000, "round did not terminate"
        if rnd.phase == "action":
            # always take a tsumo if available, else discard the drawn tile
            if rnd.can_tsumo():
                rnd.declare_tsumo()
                continue
            p = rnd.current()
            rnd.discard(p.drawn if p.drawn else p.hand[-1])
        elif rnd.phase == "calls":
            # everyone passes (ignore pon/chi to keep it simple), except ron
            ronners = [s for s, o in rnd.pending_calls.items() if o.get("ron")]
            if ronners:
                rnd.call_ron(ronners)
            else:
                rnd.pass_calls()
    return rnd


def test_rounds_terminate_and_conserve_points():
    for n in (3, 4):
        for seed in range(30):
            rnd = auto_play(n, seed)
            total = sum(p.points for p in rnd.players) + rnd.riichi_sticks * 1000
            expected = rnd.config.starting_points * n
            assert total == expected, f"points not conserved n={n} seed={seed}: {total} != {expected}"
            assert rnd.result is not None


def test_result_shapes():
    rnd = auto_play(4, 1)
    r = rnd.result
    assert r.kind in ("tsumo", "ron", "draw")
    # deltas sum to zero minus any riichi sticks left on the table
    assert sum(r.deltas.values()) + rnd.riichi_sticks * 1000 == 0 or r.kind == "draw"


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

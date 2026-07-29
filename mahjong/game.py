"""Round orchestration: turns, calls, riichi, kan, draws and win resolution.

The :class:`Round` object is a synchronous state machine. A driver (the Discord
bot or a test) advances it by inspecting :attr:`phase` and calling action
methods. It never sleeps or does I/O itself.

Phases:
    "action" – the current player has drawn and must act (discard/tsumo/kan/riichi)
    "calls"  – a tile was just discarded; other players may call (ron/pon/kan/chi)
    "ended"  – the round is over (see :attr:`result`)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .context import WinContext
from .meld import ANKAN, CHI, KAKAN, MINKAN, PON, Meld
from .player import Player
from .score import ScoreResult, score_hand
from .tiles import (
    EAST,
    Tile,
    WINDS,
    is_terminal_or_honor,
    parse_tile,
    same_suit,
    sort_tiles,
    suit_of,
    tiles_to_counts,
)
from .wall import Wall


@dataclass
class GameConfig:
    three_player: bool = False
    red_fives: bool = True
    kuitan: bool = True          # open tanyao allowed
    starting_points: int = 25000
    kiriage: bool = False


@dataclass
class RoundResult:
    kind: str                     # "tsumo", "ron", "draw", "abort"
    winners: list[int] = field(default_factory=list)      # seats
    loser: int | None = None      # discarder seat (ron)
    scores: dict[int, ScoreResult] = field(default_factory=dict)
    deltas: dict[int, int] = field(default_factory=dict)  # seat -> point change
    dealer_repeat: bool = False
    tenpai: list[int] = field(default_factory=list)
    detail: str = ""


class Round:
    def __init__(
        self,
        players: list[Player],
        dealer: int,
        round_wind: int = EAST,
        honba: int = 0,
        riichi_sticks: int = 0,
        config: GameConfig | None = None,
        rng: random.Random | None = None,
    ):
        self.players = players
        self.n = len(players)
        self.dealer = dealer
        self.round_wind = round_wind
        self.honba = honba
        self.riichi_sticks = riichi_sticks
        self.config = config or GameConfig(three_player=len(players) == 3)
        self.rng = rng or random.Random()

        self.wall = Wall(self.config.three_player, self.config.red_fives, self.rng)
        self.phase = "action"
        self.result: RoundResult | None = None

        self.turn = dealer
        self.last_discard: tuple[int, Tile] | None = None
        self.pending_calls: dict[int, dict] = {}
        self.first_uninterrupted = True   # no calls/kan yet this round
        self.any_discard = False
        self.just_kan = False             # current draw was a rinshan draw
        self.pending_kakan: tuple[int, Tile] | None = None  # chankan window

        self._deal()

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def _deal(self) -> None:
        for i, p in enumerate(self.players):
            p.reset_round()
            p.seat_wind = WINDS[(i - self.dealer) % self.n]
            for _ in range(13):
                p.add(self.wall.draw())
        self._draw_for_current()

    def _draw_for_current(self, rinshan: bool = False) -> Tile | None:
        p = self.players[self.turn]
        tile = self.wall.draw_rinshan() if rinshan else self.wall.draw()
        if tile is None:
            self._exhaustive_draw()
            return None
        p.drawn = tile
        p.add(tile)
        p.temp_furiten = False
        self.just_kan = rinshan
        self.phase = "action"
        return tile

    # ------------------------------------------------------------------
    # queries for the driver
    # ------------------------------------------------------------------
    def current(self) -> Player:
        return self.players[self.turn]

    def can_tsumo(self) -> bool:
        p = self.current()
        if p.drawn is None:
            return False
        res = self._score_win(self.turn, p.drawn.kind, is_tsumo=True)
        return res is not None

    def kan_options(self) -> list[Tile]:
        """Tiles the current player may declare an ankan/kakan on."""
        p = self.current()
        opts: list[Tile] = []
        counts = tiles_to_counts(p.hand)
        for kind in range(34):
            if counts[kind] == 4:
                opts.append(Tile(kind))
        # kakan: a drawn/held tile matching an existing pon
        for m in p.melds:
            if m.kind == PON and any(t.kind == m.base_kind for t in p.hand):
                opts.append(Tile(m.base_kind))
        # riichi locks the hand: only ankan that doesn't change waits is allowed;
        # keep it simple and forbid kan after riichi.
        if p.riichi:
            return []
        return opts

    def can_riichi(self) -> bool:
        p = self.current()
        if p.riichi or not p.is_menzen or p.points < 1000:
            return False
        if self.wall.remaining < 4:
            return False
        # tenpai after discarding some tile
        return self._riichi_discards(p) != []

    def _riichi_discards(self, p: Player) -> list[Tile]:
        """Which tiles the player can discard and remain tenpai (for riichi)."""
        outs = []
        seen = set()
        for t in p.hand:
            if t.kind in seen:
                continue
            seen.add(t.kind)
            idx = p.hand.index(t)
            removed = p.hand.pop(idx)
            if p.is_tenpai():
                outs.append(removed)
            p.hand.insert(idx, removed)
        return outs

    # ------------------------------------------------------------------
    # actions by the current player
    # ------------------------------------------------------------------
    def declare_tsumo(self) -> RoundResult:
        p = self.current()
        res = self._score_win(self.turn, p.drawn.kind, is_tsumo=True)
        if res is None:
            raise ValueError("no valid tsumo")
        return self._win_tsumo(self.turn, res)

    def declare_riichi(self, tile: Tile) -> None:
        p = self.current()
        if not self.can_riichi():
            raise ValueError("cannot declare riichi")
        p.riichi = True
        if self.first_uninterrupted and len(p.discards) == 0:
            p.double_riichi = True
        p.ippatsu = True
        self.discard(tile, from_riichi=True)

    def discard(self, tile: Tile, from_riichi: bool = False) -> dict[int, dict]:
        p = self.current()
        # clear ippatsu for the discarder unless they just declared riichi
        if not from_riichi:
            p.ippatsu = False
        removed = p.remove_kind(tile.kind, aka=tile.aka)
        p.discards.append(removed)
        if p.riichi and p.riichi_tile_index is None:
            p.riichi_tile_index = len(p.discards) - 1
        p.drawn = None
        p.update_furiten()
        self.any_discard = True
        self.last_discard = (self.turn, removed)
        self.pending_calls = self._compute_calls(self.turn, removed)
        self.phase = "calls"
        return self.pending_calls

    def declare_kan(self, tile: Tile) -> None:
        """Ankan or kakan by the current player."""
        p = self.current()
        counts = tiles_to_counts(p.hand)
        if counts[tile.kind] == 4:
            # ankan
            tiles = [t for t in p.hand if t.kind == tile.kind]
            for t in tiles:
                p.hand.remove(t)
            p.melds.append(Meld(ANKAN, tiles, called_from=self.turn))
            self.first_uninterrupted = False
            self._clear_ippatsu_all()
            self._draw_for_current(rinshan=True)
            return
        # kakan (upgrade a pon)
        meld = next((m for m in p.melds if m.kind == PON and m.base_kind == tile.kind), None)
        if meld is None:
            raise ValueError("invalid kan")
        added = p.remove_kind(tile.kind)
        meld.tiles.append(added)
        meld.kind = KAKAN
        # chankan window: others may ron on this tile
        self.pending_kakan = (self.turn, added)
        self.pending_calls = self._compute_chankan(self.turn, added)
        if self.pending_calls:
            self.phase = "calls"
        else:
            self._finish_kakan()

    def _finish_kakan(self) -> None:
        self.pending_kakan = None
        self.first_uninterrupted = False
        self._clear_ippatsu_all()
        self._draw_for_current(rinshan=True)

    # ------------------------------------------------------------------
    # calls by other players
    # ------------------------------------------------------------------
    def _compute_calls(self, discarder: int, tile: Tile) -> dict[int, dict]:
        calls: dict[int, dict] = {}
        last = self.wall.remaining == 0
        for i, p in enumerate(self.players):
            if i == discarder:
                continue
            opt: dict = {}
            # ron
            res = self._score_win(i, tile.kind, is_tsumo=False, ron_tile=tile,
                                   houtei=last)
            if res is not None and not self._is_furiten_for_ron(p, tile):
                opt["ron"] = True
            if not last:
                counts = tiles_to_counts(p.hand)
                # pon
                if counts[tile.kind] >= 2 and not p.riichi:
                    opt["pon"] = True
                # kan (open, minkan)
                if counts[tile.kind] == 3 and not p.riichi:
                    opt["kan"] = True
                # chi only from the player to the left (previous seat), 4p only
                if not self.config.three_player and i == (discarder + 1) % self.n \
                        and not p.riichi and not tile.is_honor:
                    chis = self._chi_options(p, tile)
                    if chis:
                        opt["chi"] = chis
            if opt:
                calls[i] = opt
        return calls

    def _compute_chankan(self, kanner: int, tile: Tile) -> dict[int, dict]:
        calls: dict[int, dict] = {}
        for i, p in enumerate(self.players):
            if i == kanner:
                continue
            res = self._score_win(i, tile.kind, is_tsumo=False, ron_tile=tile,
                                   chankan=True)
            if res is not None and not self._is_furiten_for_ron(p, tile):
                calls[i] = {"ron": True}
        return calls

    def _chi_options(self, p: Player, tile: Tile) -> list[list[Tile]]:
        k = tile.kind
        held = {t.kind: [x for x in p.hand if x.kind == t.kind] for t in p.hand}
        opts: list[list[Tile]] = []

        def pick(a: int, b: int):
            if a in held and b in held and same_suit(a, k) and same_suit(b, k):
                return [held[a][0], held[b][0]]
            return None

        for lo in (k - 2, k - 1, k):
            hi = lo + 2
            if lo < suit_of(k) or hi > suit_of(k) + 8:
                continue
            others = [r for r in (lo, lo + 1, lo + 2) if r != k]
            got = pick(others[0], others[1])
            if got:
                opts.append(got)
        return opts

    def call_ron(self, seats: list[int]) -> RoundResult:
        """One or more players declare ron on the last discard/chankan tile."""
        if self.pending_kakan is not None:
            discarder, tile = self.pending_kakan
            chankan = True
        else:
            discarder, tile = self.last_discard
            chankan = False
        results: dict[int, ScoreResult] = {}
        for s in seats:
            res = self._score_win(s, tile.kind, is_tsumo=False, ron_tile=tile,
                                   houtei=self.wall.remaining == 0, chankan=chankan)
            if res is None:
                raise ValueError(f"seat {s} has no valid ron")
            results[s] = res
        return self._win_ron(discarder, results, tile)

    def call_pon(self, seat: int) -> None:
        discarder, tile = self.last_discard
        p = self.players[seat]
        taken = [t for t in p.hand if t.kind == tile.kind][:2]
        for t in taken:
            p.hand.remove(t)
        meld = Meld(PON, taken + [tile], called_from=discarder, called_tile=tile)
        p.melds.append(meld)
        self._after_open_call(seat)

    def call_kan(self, seat: int) -> None:
        """Open (minkan) call on a discard."""
        discarder, tile = self.last_discard
        p = self.players[seat]
        taken = [t for t in p.hand if t.kind == tile.kind][:3]
        for t in taken:
            p.hand.remove(t)
        meld = Meld(MINKAN, taken + [tile], called_from=discarder, called_tile=tile)
        p.melds.append(meld)
        self._clear_ippatsu_all()
        self.first_uninterrupted = False
        self.turn = seat
        self.pending_calls = {}
        self._draw_for_current(rinshan=True)

    def call_chi(self, seat: int, tiles: list[Tile]) -> None:
        discarder, tile = self.last_discard
        p = self.players[seat]
        for t in tiles:
            p.hand.remove(t)
        meld = Meld(CHI, sort_tiles(tiles + [tile]), called_from=discarder,
                    called_tile=tile)
        p.melds.append(meld)
        self._after_open_call(seat)

    def _after_open_call(self, seat: int) -> None:
        self._clear_ippatsu_all()
        self.first_uninterrupted = False
        self.turn = seat
        self.pending_calls = {}
        self.phase = "action"          # caller must now discard (no draw)
        self.players[seat].drawn = None

    def pass_calls(self) -> None:
        """No one called on the discard (or chankan); advance the game."""
        # any player who could have ronned but passed is temp-furiten
        for s, opt in self.pending_calls.items():
            if opt.get("ron"):
                p = self.players[s]
                p.temp_furiten = True
                if p.riichi:
                    p.locked_furiten = True  # riichi furiten is permanent
        self.pending_calls = {}
        if self.pending_kakan is not None:
            self._finish_kakan()
            return
        self._advance_turn()

    def _advance_turn(self) -> None:
        self.turn = (self.turn + 1) % self.n
        self._draw_for_current()

    # ------------------------------------------------------------------
    # furiten
    # ------------------------------------------------------------------
    def _is_furiten_for_ron(self, p: Player, tile: Tile) -> bool:
        if p.temp_furiten or p.locked_furiten:
            return True
        waits = set(p.waits())
        if tile.kind not in waits:
            # not a winning tile at all — handled by score returning None
            pass
        # permanent furiten: any wait is in own discards
        if waits & {t.kind for t in p.discards}:
            return True
        # riichi furiten: passed a winning tile since declaring riichi
        return False

    # ------------------------------------------------------------------
    # scoring
    # ------------------------------------------------------------------
    def _score_win(self, seat: int, win_kind: int, is_tsumo: bool,
                   ron_tile: Tile | None = None, houtei: bool = False,
                   chankan: bool = False) -> ScoreResult | None:
        p = self.players[seat]
        counts = tiles_to_counts(p.hand)
        if is_tsumo:
            # drawn tile already in hand
            pass
        else:
            counts[win_kind] += 1
        # aka count across final 14 tiles
        aka = sum(1 for t in p.hand if t.aka)
        for m in p.melds:
            aka += sum(1 for t in m.tiles if t.aka)
        if not is_tsumo and ron_tile is not None and ron_tile.aka:
            aka += 1

        haitei = is_tsumo and self.wall.remaining == 0 and not self.just_kan
        rinshan = is_tsumo and self.just_kan
        first_turn = self.first_uninterrupted and len(p.discards) == 0
        tenhou = is_tsumo and first_turn and seat == self.dealer
        chiihou = is_tsumo and first_turn and seat != self.dealer

        ctx = WinContext(
            win_tile=win_kind,
            is_tsumo=is_tsumo,
            seat_wind=p.seat_wind,
            round_wind=self.round_wind,
            riichi=p.riichi,
            double_riichi=p.double_riichi,
            ippatsu=p.ippatsu and p.riichi,
            rinshan=rinshan,
            chankan=chankan,
            haitei=haitei,
            houtei=houtei and not is_tsumo,
            tenhou=tenhou,
            chiihou=chiihou,
            dora_indicators=[t.kind for t in self.wall.dora_indicators()],
            ura_indicators=[t.kind for t in self.wall.ura_indicators()],
            aka_count=aka,
            three_player=self.config.three_player,
            kiriage=self.config.kiriage,
        )
        res = score_hand(counts, p.melds, ctx)
        if res is None:
            return None
        # kuitan off: open tanyao is not a valid yaku
        if not self.config.kuitan and not p.is_menzen:
            names = {n for n, _ in res.yaku}
            if names == {"Tanyao (all simples)"}:
                return None
        return res

    # ------------------------------------------------------------------
    # win / draw resolution and point transfer
    # ------------------------------------------------------------------
    def _honba_bonus(self) -> int:
        return 300 * self.honba

    def _win_tsumo(self, seat: int, res: ScoreResult) -> RoundResult:
        deltas = {i: 0 for i in range(self.n)}
        dealer_pay, other_pay = res.tsumo_payments()
        honba_each = 100 * self.honba
        total = 0
        for i in range(self.n):
            if i == seat:
                continue
            pay = dealer_pay if i == self.dealer else other_pay
            pay += honba_each
            deltas[i] -= pay
            total += pay
        deltas[seat] += total + self.riichi_sticks * 1000
        self._apply(deltas)
        self.riichi_sticks = 0
        result = RoundResult(
            kind="tsumo", winners=[seat], scores={seat: res}, deltas=deltas,
            dealer_repeat=(seat == self.dealer),
            detail=self._score_line(res),
        )
        self.result = result
        self.phase = "ended"
        return result

    def _win_ron(self, discarder: int, results: dict[int, ScoreResult],
                 tile: Tile) -> RoundResult:
        deltas = {i: 0 for i in range(self.n)}
        honba_bonus = self._honba_bonus()
        # multiple ron: discarder pays each; honba added once to the first (head-bump order)
        order = sorted(results.keys(), key=lambda s: (s - discarder) % self.n)
        first = True
        for s in order:
            res = results[s]
            val = res.ron_value() + (honba_bonus if first else 0)
            deltas[discarder] -= val
            deltas[s] += val
            first = False
        # riichi sticks go to the closest winner
        deltas[order[0]] += self.riichi_sticks * 1000
        self._apply(deltas)
        self.riichi_sticks = 0
        result = RoundResult(
            kind="ron", winners=order, loser=discarder, scores=results, deltas=deltas,
            dealer_repeat=(self.dealer in order),
            detail=" / ".join(self._score_line(results[s]) for s in order),
        )
        self.result = result
        self.phase = "ended"
        return result

    def _exhaustive_draw(self) -> None:
        tenpai = [i for i, p in enumerate(self.players) if p.is_tenpai()]
        deltas = {i: 0 for i in range(self.n)}
        noten = [i for i in range(self.n) if i not in tenpai]
        if tenpai and noten:
            pool = 3000
            gain = pool // len(tenpai)
            loss = pool // len(noten)
            for i in tenpai:
                deltas[i] += gain
            for i in noten:
                deltas[i] -= loss
        self._apply(deltas)
        result = RoundResult(
            kind="draw", deltas=deltas, tenpai=tenpai,
            dealer_repeat=(self.dealer in tenpai),
            detail="流局 (exhaustive draw)",
        )
        self.result = result
        self.phase = "ended"

    def _apply(self, deltas: dict[int, int]) -> None:
        for i, d in deltas.items():
            self.players[i].points += d

    def _clear_ippatsu_all(self) -> None:
        for p in self.players:
            p.ippatsu = False

    def _score_line(self, res: ScoreResult) -> str:
        if res.is_yakuman:
            names = ", ".join(n for n, _ in res.yakuman)
            return f"{res.limit_name}: {names}"
        parts = [f"{n} ({h})" for n, h in res.yaku]
        extra = []
        if res.dora:
            extra.append(f"dora {res.dora}")
        if res.aka_dora:
            extra.append(f"aka {res.aka_dora}")
        if res.ura_dora:
            extra.append(f"ura {res.ura_dora}")
        limit = f" [{res.limit_name}]" if res.limit_name else ""
        return f"{res.han}han {res.fu}fu{limit} — " + ", ".join(parts + extra)

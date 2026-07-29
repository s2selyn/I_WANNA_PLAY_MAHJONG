"""The wall: live tiles, dead wall, dora indicators and rinshan draws."""

from __future__ import annotations

import random

from .tiles import Tile, full_tileset


class Wall:
    """Manages tile drawing for one round.

    Layout mirrors real mahjong: a 14-tile dead wall holds the rinshan (kan
    replacement) tiles and up to 5 dora / 5 ura-dora indicators.
    """

    DEAD_WALL_SIZE = 14

    def __init__(self, three_player: bool = False, red_fives: bool = True,
                 rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.tiles: list[Tile] = full_tileset(three_player, red_fives)
        self.rng.shuffle(self.tiles)
        # dead wall = last 14 tiles
        self.dead_wall = self.tiles[-self.DEAD_WALL_SIZE:]
        self.live = self.tiles[: -self.DEAD_WALL_SIZE]
        self._draw_pos = 0
        self._rinshan_drawn = 0
        self.revealed_dora = 1  # first dora indicator always shown

    # -- live wall --------------------------------------------------------
    @property
    def remaining(self) -> int:
        return len(self.live) - self._draw_pos

    def draw(self) -> Tile | None:
        if self.remaining <= 0:
            return None
        t = self.live[self._draw_pos]
        self._draw_pos += 1
        return t

    # -- dead wall --------------------------------------------------------
    def draw_rinshan(self) -> Tile | None:
        """Draw a replacement tile after a kan (reveals a new dora indicator)."""
        if self._rinshan_drawn >= 4:
            return None
        # rinshan tiles are the first 4 of the dead wall
        t = self.dead_wall[self._rinshan_drawn]
        self._rinshan_drawn += 1
        # A kan reveals the next dora indicator.
        if self.revealed_dora < 5:
            self.revealed_dora += 1
        return t

    def dora_indicators(self) -> list[Tile]:
        # indicators sit at dead-wall positions 4,6,8,10,12
        return [self.dead_wall[4 + 2 * i] for i in range(self.revealed_dora)]

    def ura_indicators(self) -> list[Tile]:
        # ura indicators sit directly beneath: positions 5,7,9,11,13
        return [self.dead_wall[5 + 2 * i] for i in range(self.revealed_dora)]

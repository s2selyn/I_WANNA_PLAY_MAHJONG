"""Text rendering helpers for the Discord layer."""

from __future__ import annotations

from .meld import Meld
from .player import Player
from .score import ScoreResult
from .tiles import Tile, kind_kr, kind_to_str, sort_tiles

# Unicode mahjong tiles make hands readable at a glance in Discord.
_UNICODE = {
    # man 1-9
    0: "🀇", 1: "🀈", 2: "🀉", 3: "🀊", 4: "🀋", 5: "🀌", 6: "🀍", 7: "🀎", 8: "🀏",
    # pin 1-9
    9: "🀙", 10: "🀚", 11: "🀛", 12: "🀜", 13: "🀝", 14: "🀞", 15: "🀟", 16: "🀠", 17: "🀡",
    # sou 1-9
    18: "🀐", 19: "🀑", 20: "🀒", 21: "🀓", 22: "🀔", 23: "🀕", 24: "🀖", 25: "🀗", 26: "🀘",
    # honors E S W N Haku Hatsu Chun
    27: "🀀", 28: "🀁", 29: "🀂", 30: "🀃", 31: "🀆", 32: "🀅", 33: "🀄",
}


def tile_glyph(t: Tile) -> str:
    g = _UNICODE[t.kind]
    return f"{g}(적)" if t.aka else g


def tile_code(t: Tile) -> str:
    return str(t)  # e.g. "3m", "0p", "1z"


def tile_label(t: Tile) -> str:
    """Short Korean button label, e.g. '3만', '동', '적5통'."""
    return t.label  # Player.label already handles the red-five prefix


def tile_button_emoji(t: Tile) -> str:
    return _UNICODE[t.kind]


def hand_line(tiles: list[Tile], drawn: Tile | None = None) -> str:
    """Render a hand as glyphs with the drawn tile separated."""
    base = sort_tiles([t for t in tiles if t is not drawn]) if drawn else sort_tiles(tiles)
    if drawn:
        # remove one instance of the drawn tile from the sorted base
        removed = False
        cleaned = []
        for t in base:
            if not removed and t.kind == drawn.kind and t.aka == drawn.aka:
                removed = True
                continue
            cleaned.append(t)
        base = cleaned
    glyphs = " ".join(tile_glyph(t) for t in base)
    if drawn:
        return f"{glyphs}  ＋ {tile_glyph(drawn)}"
    return glyphs


def hand_codes(tiles: list[Tile]) -> str:
    return " ".join(tile_code(t) for t in sort_tiles(tiles))


def meld_str(m: Meld) -> str:
    return "".join(tile_glyph(t) for t in m.tiles)


def melds_line(melds: list[Meld]) -> str:
    return "  |  ".join(meld_str(m) for m in melds) if melds else ""


def discards_line(p: Player) -> str:
    return " ".join(tile_glyph(t) for t in p.discards) or "—"


def score_summary(res: ScoreResult) -> str:
    if res.is_yakuman:
        names = ", ".join(f"{n} ×{m}" for n, m in res.yakuman)
        return f"**{res.limit_name}** — {names}"
    lines = [f"{n}  `{h}han`" for n, h in res.yaku]
    if res.dora:
        lines.append(f"Dora  `{res.dora}han`")
    if res.aka_dora:
        lines.append(f"Aka dora  `{res.aka_dora}han`")
    if res.ura_dora:
        lines.append(f"Ura dora  `{res.ura_dora}han`")
    limit = f"  **{res.limit_name}**" if res.limit_name else ""
    header = f"**{res.han}han {res.fu}fu**{limit}"
    return header + "\n" + "\n".join(f"· {l}" for l in lines)

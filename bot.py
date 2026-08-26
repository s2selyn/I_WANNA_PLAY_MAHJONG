"""Discord riichi mahjong bot (3- or 4-player) — button UI edition.

No tile codes or commands to memorise: players tap buttons. A player's hand
is sent privately in their DMs as a row of tile buttons; tap a tile to discard
it. When someone discards, eligible players get Ron/Pon/Chi/Kan/Skip buttons in
their DMs. Public info (discards, calls, results) is posted in the channel.

Setup:
    pip install -r requirements.txt
    export DISCORD_TOKEN=...        # your bot token (needs MESSAGE CONTENT intent)
    python bot.py

Type `!mj` (or `!mj 3`) in a channel to open a lobby, then everyone clicks 참가.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import random
import re

import discord
from discord.ext import commands

from mahjong import render as render_module
from mahjong.game import GameConfig, Round
from mahjong.player import Player
from mahjong.render import (
    discards_line,
    hand_line,
    melds_line,
    score_summary,
    tile_button_emoji,
    tile_glyph,
    tile_label,
)
from mahjong.tiles import Tile, kind_kr, kind_to_str

# Remembered per-user hand-delivery preference, so nobody has to re-pick every
# game. Just {user_id: "dm"|"channel"} — no game or message content is stored.
DATA_DIR = os.environ.get(
    "MJ_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
PREFS_PATH = os.path.join(DATA_DIR, "prefs.json")

CALL_TIMEOUT = 30     # seconds to decide on a call
TURN_TIMEOUT = 60     # auto-discard if a human goes AFK
LOBBY_TIMEOUT = 3600  # close an unstarted lobby after this much inactivity

# Optional sound effects: drop files like riichi.mp3 / ron.mp3 in sounds/.
# Missing files (or missing voice deps) are silently skipped.
#
# Lookup order for a guild: sounds/<guild_id>/<name>.<ext> (uploaded in that
# server via `!mj sound`) → sounds/<name>.<ext> (shipped default). So each
# server can customise its own effects without affecting anyone else.
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
SOUND_EXTS = (".mp3", ".ogg", ".wav", ".m4a")
SOUND_NAMES = ("riichi", "ron", "tsumo", "pon", "chi", "kan")
MAX_SOUND_BYTES = 1024 * 1024  # 1 MB — effects should be short


# --- custom tile emoji ------------------------------------------------------
# Uploaded as *application* emoji, so they work in every server the bot joins
# without taking up that server's emoji slots. Names look like "mj_m1"/"mj_z3".
TILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "tiles")
EMOJI_PREFIX = "mj_"


def tile_asset_name(kind: int, aka: bool) -> str:
    """Asset/emoji stem for a tile kind: m1..m9, p1.., s1.., z1..z7, m0=red 5."""
    if kind >= 27:
        return f"z{kind - 26}"
    suit = "mps"[kind // 9]
    num = kind % 9 + 1
    return f"{suit}0" if aka else f"{suit}{num}"


async def load_tile_emoji() -> int:
    """Point the renderer at whatever tile emoji this application already has."""
    try:
        emojis = await bot.fetch_application_emojis()
    except Exception as exc:
        print(f"[emoji] fetch failed: {exc}", flush=True)
        return 0
    by_name = {e.name: str(e) for e in emojis if e.name.startswith(EMOJI_PREFIX)}
    mapping = {}
    for kind in range(34):
        for aka in (False, True):
            if aka and kind not in (4, 13, 22):  # 적5는 만·통·삭에만 있어요
                continue
            got = by_name.get(EMOJI_PREFIX + tile_asset_name(kind, aka))
            if got:
                mapping[(kind, aka)] = got
    render_module.GLYPH_OVERRIDE.clear()
    render_module.GLYPH_OVERRIDE.update(mapping)
    return len(mapping)


def load_prefs() -> dict[int, str]:
    """Read remembered mode preferences; a broken file just means 'no prefs'."""
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    prefs = {}
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        if v in ("dm", "channel"):
            try:
                prefs[int(k)] = v
            except (TypeError, ValueError):
                continue
    return prefs


def save_prefs(prefs: dict[int, str]) -> None:
    """Write atomically so an interrupted write can't truncate the file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = PREFS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in prefs.items()}, f)
        os.replace(tmp, PREFS_PATH)
    except OSError as exc:  # 저장 실패해도 게임은 계속돼요
        print(f"[prefs] save failed: {exc}", flush=True)


user_prefs: dict[int, str] = load_prefs()


def set_user_pref(user_id: int, mode: str) -> None:
    user_prefs[user_id] = mode
    save_prefs(user_prefs)


def guild_sounds_dir(guild_id: int) -> str:
    return os.path.join(SOUNDS_DIR, str(guild_id))


def sound_variants(name: str, guild_id: int | None = None) -> list[str]:
    """All recordings for one event, e.g. tsumo.mp3 / tsumo2.mp3 / tsumo_a.m4a.

    A server's own uploads win outright: if this guild has any recording for
    the event we use only those, so a custom set never gets mixed with the
    shipped defaults.
    """
    dirs = []
    if guild_id is not None:
        dirs.append(guild_sounds_dir(guild_id))
    dirs.append(SOUNDS_DIR)
    for d in dirs:
        hits = []
        for ext in SOUND_EXTS:
            hits += glob.glob(os.path.join(d, f"{name}*{ext}"))
        # "chi" 가 "chi3" 는 잡되 엉뚱한 이름은 안 잡도록 접미사를 제한해요
        hits = [h for h in hits
                if re.fullmatch(rf"{re.escape(name)}[-_ ]?\d*[a-zA-Z]?",
                                os.path.splitext(os.path.basename(h))[0])]
        if hits:
            return sorted(hits)
    return []


def sound_path(name: str, guild_id: int | None = None) -> str | None:
    """Pick one recording for the event (randomly, when several exist)."""
    hits = sound_variants(name, guild_id)
    return random.choice(hits) if hits else None


def play_sound(table: "Table", name: str) -> None:
    """Queue a sound effect for the table's voice channel (best-effort)."""
    vc = table.voice
    if not vc or not vc.is_connected():
        return
    guild = getattr(table.channel, "guild", None)
    path = sound_path(name, guild.id if guild else None)
    if not path:
        return
    table.sound_queue.append(path)
    if table.sound_task is None or table.sound_task.done():
        table.sound_task = asyncio.create_task(_sound_runner(table))


async def _sound_runner(table: "Table") -> None:
    while table.sound_queue:
        vc = table.voice
        if not vc or not vc.is_connected():
            table.sound_queue.clear()
            return
        path = table.sound_queue.pop(0)
        if vc.is_playing():
            vc.stop()
        done = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _after(err, _loop=loop, _done=done):
            _loop.call_soon_threadsafe(_done.set)

        try:
            vc.play(discord.FFmpegPCMAudio(path), after=_after)
        except Exception as exc:  # ffmpeg missing / decode error → give up quietly
            print(f"[sound] play failed: {exc}")
            table.sound_queue.clear()
            return
        await done.wait()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def disable_view(view: discord.ui.View) -> discord.ui.View:
    for child in view.children:
        child.disabled = True
    return view


def tile_btn_kwargs(t: Tile) -> dict:
    """Button face for a tile.

    A custom emoji has to go in the button's ``emoji`` field — Discord will not
    render ``<:name:id>`` inside label text — so split it out when we have one
    and fall back to putting the Unicode glyph in the label.
    """
    glyph = tile_button_emoji(t)
    if glyph.startswith("<"):
        return {"label": tile_label(t), "emoji": discord.PartialEmoji.from_str(glyph)}
    return {"label": f"{glyph} {tile_label(t)}"}


async def dm_of(user_id: int) -> discord.DMChannel:
    user = bot.get_user(user_id) or await bot.fetch_user(user_id)
    return user.dm_channel or await user.create_dm()


class Btn(discord.ui.Button):
    """A button that delegates to an async callback ``cb(interaction)``."""

    def __init__(self, label, cb, *, style=discord.ButtonStyle.secondary,
                 emoji=None, row=None):
        super().__init__(label=label, style=style, emoji=emoji, row=row)
        self._cb = cb

    async def callback(self, interaction: discord.Interaction):
        await self._cb(interaction)


# ---------------------------------------------------------------------------
# Table: one game bound to a Discord channel
# ---------------------------------------------------------------------------
class Table:
    def __init__(self, channel: discord.TextChannel, size: int):
        self.channel = channel
        self.size = size
        self.seats: list[Player] = []
        self.round: Round | None = None
        self.started = False
        self.honba = 0
        self.riichi_sticks = 0
        self.dealer = 0
        self.round_wind_idx = 0
        self._ai = 0
        self.lobby_msg: discord.Message | None = None
        self.board_msg: discord.Message | None = None  # live floor, edited in place
        self.control_msg: discord.Message | None = None  # 내 손패/콜 buttons (channel mode)
        # 진행용 메시지(로비·보드·차례 알림·콜 안내…). 대국이 끝나면 지워서
        # 결과만 남기고, 남은 버튼으로 끝난 판을 되살리는 일도 막아요.
        self.transient: list[discord.Message] = []
        # 매 턴 새로 뜨는 알림(차례·콜)은 종류별로 최신 하나만 남겨요.
        self.notices: dict[str, discord.Message] = {}
        self.mode = "channel"  # table default: "channel" (mobile) or "dm" (PC)
        self.player_modes: dict[int, str] = {}  # user_id -> personal override
        self.host_id: int | None = None  # who opened the room (lobby controls)

        # lobby expiry (unstarted rooms shouldn't linger forever)
        self.lobby_deadline: float = 0.0
        self.lobby_task: asyncio.Task | None = None

        # optional voice / sound effects
        self.voice: discord.VoiceClient | None = None
        self.sound_queue: list[str] = []
        self.sound_task: asyncio.Task | None = None

        # call-window state
        self.awaiting = False
        self.call_eligible: set[int] = set()
        self.call_choices: dict[int, tuple[str, object]] = {}
        self.call_messages: dict[int, discord.Message] = {}
        self.call_task: asyncio.Task | None = None
        self.call_resolved = False

        # AFK guard: turn_token identifies "this exact turn" so a timer that
        # wakes up late can tell it is stale and do nothing.
        self.afk_task: asyncio.Task | None = None
        self.turn_token = 0

    # lobby ----------------------------------------------------------------
    def add_human(self, user) -> bool:
        if any(p.user_id == user.id for p in self.seats) or len(self.seats) >= self.size:
            return False
        self.seats.append(Player(seat=len(self.seats), name=user.display_name,
                                 user_id=user.id))
        return True

    def add_ai(self) -> bool:
        if len(self.seats) >= self.size:
            return False
        self._ai += 1
        self.seats.append(Player(seat=len(self.seats), name=f"AI-{self._ai}", is_ai=True))
        return True

    def shuffle_seats(self) -> None:
        """Randomise seating once, at game start (real mahjong draws for seats).

        Seats stay fixed for the rest of the game; only the dealer rotates.
        """
        random.shuffle(self.seats)
        for i, p in enumerate(self.seats):
            p.seat = i

    def remove_human(self, user_id: int) -> bool:
        """Leave an unstarted lobby. Seats are renumbered to stay 0..n-1."""
        if self.started:
            return False
        before = len(self.seats)
        self.seats = [p for p in self.seats if p.user_id != user_id]
        if len(self.seats) == before:
            return False
        for i, p in enumerate(self.seats):  # seat_wind 는 국 시작 때 다시 정해져요
            p.seat = i
        self.player_modes.pop(user_id, None)
        if user_id == self.host_id:
            # 방장이 나가면 남은 사람 중 첫 사람에게 넘겨요
            nxt = next((p for p in self.seats if not p.is_ai), None)
            self.host_id = nxt.user_id if nxt else None
        return True

    def seat_of(self, user_id: int) -> int | None:
        for p in self.seats:
            if p.user_id == user_id:
                return p.seat
        return None

    # lobby expiry --------------------------------------------------------
    def touch_lobby(self) -> None:
        """Push the lobby's expiry back; call on any lobby activity."""
        self.lobby_deadline = asyncio.get_running_loop().time() + LOBBY_TIMEOUT
        if self.lobby_task is None or self.lobby_task.done():
            self.lobby_task = asyncio.create_task(_lobby_expiry(self))

    def mode_of(self, user_id: int | None) -> str:
        """Delivery mode: this game's choice > remembered pref > table default."""
        if user_id is None:
            return self.mode
        if user_id in self.player_modes:
            return self.player_modes[user_id]
        return user_prefs.get(user_id, self.mode)

    def round_wind_name(self) -> str:
        return kind_kr([27, 28, 29, 30][self.round_wind_idx])

    def cancel_timers(self) -> None:
        """Stop every pending timer — call when a game ends or is torn down."""
        for name in ("afk_task", "call_task", "lobby_task"):
            task = getattr(self, name)
            if task is not None and not task.done():
                task.cancel()
            setattr(self, name, None)

    # voice ----------------------------------------------------------------
    async def join_voice(self, member) -> None:
        """Join (or move to) the member's voice channel. Best-effort — a failure
        here just means the game runs without sound.

        Returns the channel joined, or None if there was nothing to join.
        """
        ch = getattr(getattr(member, "voice", None), "channel", None)
        if ch is None:
            return None
        try:
            if self.voice is not None and self.voice.is_connected():
                if self.voice.channel.id == ch.id:
                    return ch  # 이미 그 채널에 있어요
                await self.voice.move_to(ch)
            else:
                self.voice = await ch.connect()
            return ch
        except Exception as exc:  # missing PyNaCl, no perms, etc. → text-only
            print(f"[voice] connect failed: {exc}", flush=True)
            self.voice = None
            return None

    async def leave_voice(self) -> None:
        self.sound_queue.clear()
        if self.voice is not None:
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass
            self.voice = None


tables: dict[int, Table] = {}


def is_live(table: Table) -> bool:
    """Is this still the channel's active game?

    Buttons live on messages that outlast their game, and every view holds a
    reference to the Table it was built for. Without this check, clicking an
    old message resurrects a finished game — it keeps mutating that round and
    editing its board long after the table was closed.
    """
    return tables.get(table.channel.id) is table


async def send_transient(table: Table, *args, **kwargs) -> discord.Message:
    """Send a progress message that gets cleaned up when the game ends."""
    msg = await table.channel.send(*args, **kwargs)
    table.transient.append(msg)
    return msg


async def send_notice(table: Table, key: str, *args, **kwargs) -> discord.Message:
    """Post a notice, replacing the previous one of the same kind.

    Turn pings and call alerts fire every single turn. Posting them plainly
    buries the board under a stack of stale "your turn" lines, so only the
    newest of each kind is kept. A fresh message (not an edit) is still sent
    so the @mention actually notifies.
    """
    old = table.notices.pop(key, None)
    msg = await table.channel.send(*args, **kwargs)
    table.notices[key] = msg
    if old is not None:
        try:
            await old.delete()
        except discord.HTTPException:
            pass
    return msg


async def clear_notice(table: Table, key: str) -> None:
    """Remove a notice once it no longer applies (e.g. the call window closed)."""
    old = table.notices.pop(key, None)
    if old is not None:
        try:
            await old.delete()
        except discord.HTTPException:
            pass


async def clear_transient(table: Table) -> None:
    """Delete the progress messages, leaving only the results in the channel."""
    msgs, table.transient = table.transient, []
    msgs.extend(table.notices.values())
    table.notices.clear()
    for m in (table.lobby_msg, table.control_msg, table.board_msg):
        if m is not None and m not in msgs:
            msgs.append(m)
    table.lobby_msg = table.control_msg = table.board_msg = None
    for m in msgs:
        try:
            await m.delete()
        except discord.HTTPException:
            pass  # 이미 지워졌거나 권한이 없어도 그냥 넘어가요


async def dismiss(interaction: discord.Interaction, fallback: str = "완료") -> None:
    """Close the private (ephemeral) panel once the player has acted.

    Leaving them around means every turn stacks another panel and pushes the
    board out of view. Deleting is the clean outcome; if Discord refuses we at
    least strip the buttons and shrink it to one line.
    """
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
        await interaction.delete_original_response()
    except discord.HTTPException:
        try:
            await interaction.edit_original_response(content=fallback, view=None)
        except discord.HTTPException:
            pass


def auto_dismiss(interaction: discord.Interaction, delay: float = 30.0) -> None:
    """Fade out an informational private panel so it stops piling up."""
    async def run():
        await asyncio.sleep(delay)
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass
    asyncio.create_task(run())


async def dead_game(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "이미 끝난 대국이에요. `!mj` 로 새로 시작해주세요.", ephemeral=True)
    auto_dismiss(interaction, 15)


# ---------------------------------------------------------------------------
# public board rendering
# ---------------------------------------------------------------------------
def seat_wind_name(p: Player) -> str:
    return kind_kr(p.seat_wind)


def board_text(table: Table, note: str = "") -> str:
    r = table.round
    dora = " ".join(tile_glyph(t) for t in r.wall.dora_indicators())
    head = (f"🀄 **{table.round_wind_name()}{table.dealer + 1}국 · {table.honba}본장 · "
            f"리치봉 {table.riichi_sticks}**　(남은 패 {r.wall.remaining})\n"
            f"도라: {dora}")
    if note:
        head += f"\n{note}"
    rows = []
    for p in r.players:
        mark = "▶️" if p.seat == r.turn else "▫️"
        riichi = " 🎏리치" if p.riichi else ""
        melds = melds_line(p.melds)
        melds = f"　[{melds}]" if melds else ""
        rows.append(f"{mark} **{p.name}** ({seat_wind_name(p)}) {p.points}점{riichi}{melds}\n"
                    f"　　버림패: {discards_line(p)}")
    return head + "\n\n" + "\n".join(rows)


async def update_board(table: Table, note: str = "") -> None:
    """Send the shared floor once, then edit it in place on every change."""
    content = board_text(table, note)
    if table.board_msg is None:
        table.board_msg = await send_transient(table, content)
    else:
        try:
            await table.board_msg.edit(content=content)
        except discord.HTTPException:
            table.board_msg = await send_transient(table, content)


def turn_content(table: Table, p: Player) -> str:
    r = table.round
    wait = ""
    if p.is_tenpai():
        wait = "\n🎯 대기: " + " ".join(f"{tile_glyph(Tile(k))}" for k in p.waits())
    melds = melds_line(p.melds)
    melds = f"\n부로: {melds}" if melds else ""
    drew = f"　(방금 {tile_glyph(p.drawn)} 쯔모)" if p.drawn else ""
    return (f"**{table.round_wind_name()}{table.dealer + 1}국** · 자풍 {seat_wind_name(p)}"
            f" · 남은 패 {r.wall.remaining}{drew}\n"
            f"{hand_line(p.hand, p.drawn)}{melds}{wait}\n"
            f"버릴 패를 누르세요 👇")


def readonly_hand_content(table: Table, p: Player) -> str:
    wait = ""
    if p.is_tenpai():
        wait = "\n🎯 대기: " + " ".join(f"{tile_glyph(Tile(k))}" for k in p.waits())
    melds = melds_line(p.melds)
    melds = f"\n부로: {melds}" if melds else ""
    return (f"🎴 **내 손패** (자풍 {seat_wind_name(p)})\n"
            f"{hand_line(p.hand, p.drawn)}{melds}{wait}\n"
            f"_아직 내 차례가 아니에요. 보기 전용._")


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------
class TurnView(discord.ui.View):
    def __init__(self, table: Table, seat: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.table = table
        self.seat = seat
        r = table.round
        p = r.players[seat]

        # discardable tiles (dedupe by kind+aka); riichi locks to tsumogiri
        if p.riichi and p.drawn is not None:
            choices = [p.drawn]
        else:
            seen = set()
            choices = []
            for t in p.sorted_hand():
                key = (t.kind, t.aka)
                if key not in seen:
                    seen.add(key)
                    choices.append(t)
        for t in choices:
            self.add_item(Btn(cb=self._make_discard(t), **tile_btn_kwargs(t)))

        if r.can_tsumo():
            self.add_item(Btn("🀄 쯔모", self._tsumo, style=discord.ButtonStyle.success))
        if r.can_riichi():
            self.add_item(Btn("🎏 리치", self._riichi, style=discord.ButtonStyle.primary))
        if r.kan_options():
            self.add_item(Btn("🔶 깡", self._kan, style=discord.ButtonStyle.primary))

    def _valid(self) -> bool:
        r = self.table.round
        return (is_live(self.table) and r and r.phase == "action"
                and r.turn == self.seat)

    def _make_discard(self, tile: Tile):
        async def cb(interaction: discord.Interaction):
            if not self._valid():
                await dismiss(interaction, "이미 지난 차례예요.")
                return
            r = self.table.round
            r.discard(Tile(tile.kind, tile.aka))
            await dismiss(interaction, f"버렸어요: {tile_glyph(tile)}")
            await announce_discard(self.table, r.players[self.seat], tile)
            await advance(self.table)
        return cb

    async def _tsumo(self, interaction: discord.Interaction):
        if not self._valid():
            await dismiss(interaction, "이미 지난 차례예요.")
            return
        self.table.round.declare_tsumo()
        play_sound(self.table, "tsumo")
        await dismiss(interaction, "🀄 쯔모!")
        await finish_round(self.table)

    async def _riichi(self, interaction: discord.Interaction):
        r = self.table.round
        if not self._valid() or not r.can_riichi():
            return
        view = ChoiceView(self.table, self.seat, "action",
                          r._riichi_discards(r.players[self.seat]),
                          self._do_riichi, "리치할 패(버릴 패)를 누르세요 🎏")
        await interaction.response.edit_message(content="🎏 리치! 버릴 패 선택:", view=view)

    def _do_riichi(self, tile: Tile):
        async def run(interaction):
            r = self.table.round
            r.declare_riichi(tile)
            play_sound(self.table, "riichi")
            await dismiss(interaction, f"🎏 리치 선언, {tile_glyph(tile)} 버림")
            await self.table.channel.send(
                f"🎏 **{r.players[self.seat].name}** 리치! → {tile_glyph(tile)}")
            await advance(self.table)
        return run

    async def _kan(self, interaction: discord.Interaction):
        r = self.table.round
        if not self._valid():
            return
        view = ChoiceView(self.table, self.seat, "action", r.kan_options(),
                          self._do_kan, "깡할 패를 누르세요 🔶")
        await interaction.response.edit_message(content="🔶 깡 선택:", view=view)

    def _do_kan(self, tile: Tile):
        async def run(interaction):
            r = self.table.round
            r.declare_kan(tile)
            await dismiss(interaction, f"🔶 깡: {tile_glyph(tile)}")
            if r.phase == "calls":            # chankan window opened
                await run_call_window(self.table)
            else:
                await self.table.channel.send(
                    f"🔶 **{r.players[self.seat].name}** 깡! (새 도라 공개)")
                await update_board(self.table)
                await advance(self.table)
        return run

    async def on_timeout(self):
        # AFK: auto-discard the drawn tile to keep the game moving
        r = self.table.round
        if self._valid():
            p = r.players[self.seat]
            tile = p.drawn or p.sorted_hand()[-1]
            r.discard(tile)
            await announce_discard(self.table, p, tile)
            await advance(self.table)


class ChoiceView(discord.ui.View):
    """Generic secondary picker (riichi discard / kan tile)."""

    def __init__(self, table, seat, phase, tiles, cb_factory, prompt):
        super().__init__(timeout=TURN_TIMEOUT)
        self.table = table
        self.seat = seat
        seen = set()
        for t in tiles:
            key = (t.kind, t.aka)
            if key in seen:
                continue
            seen.add(key)
            self.add_item(Btn(cb=cb_factory(t), **tile_btn_kwargs(t)))


class CallView(discord.ui.View):
    def __init__(self, table: Table, seat: int, options: dict):
        super().__init__(timeout=CALL_TIMEOUT + 5)
        self.table = table
        self.seat = seat
        if options.get("ron"):
            self.add_item(Btn("🀄 론", self._make("ron"), style=discord.ButtonStyle.success))
        if options.get("pon"):
            self.add_item(Btn("퐁", self._make("pon"), style=discord.ButtonStyle.primary))
        if options.get("kan"):
            self.add_item(Btn("깡", self._make("kan"), style=discord.ButtonStyle.primary))
        for combo in options.get("chi", []):
            label = "치 " + "·".join(kind_kr(t.kind) for t in combo)
            self.add_item(Btn(label, self._make("chi", combo),
                              style=discord.ButtonStyle.primary))
        self.add_item(Btn("스킵", self._make("skip"), style=discord.ButtonStyle.danger))

    _LABEL = {"ron": "🀄 론!", "pon": "퐁!", "kan": "깡!", "chi": "치!",
              "skip": "스킵"}

    def _make(self, action, value=None):
        async def cb(interaction: discord.Interaction):
            if not is_live(self.table):
                await dismiss(interaction, "이미 끝난 대국이에요.")
                return
            await dismiss(interaction, self._LABEL.get(action, action))
            await record_call(self.table, self.seat, action, value)
        return cb


class CallNoticeView(discord.ui.View):
    """Buttons on the public call alert.

    Skipping used to require opening the private call panel first, which is a
    lot of taps for the common "not interested" answer — so it gets its own
    button right on the alert.
    """

    def __init__(self, table: Table):
        super().__init__(timeout=None)
        self.table = table
        self.add_item(Btn("🔔 콜", self._call, style=discord.ButtonStyle.primary))
        self.add_item(Btn("⏭️ 스킵", self._skip, style=discord.ButtonStyle.secondary))

    def _seat(self, interaction) -> int | None:
        """The clicker's seat, if they actually have a pending call."""
        t = self.table
        if not is_live(t) or not t.awaiting:
            return None
        seat = t.seat_of(interaction.user.id)
        if seat is None or seat not in t.call_eligible or seat in t.call_choices:
            return None
        return seat

    async def _call(self, interaction: discord.Interaction):
        t = self.table
        seat = self._seat(interaction)
        if seat is None:
            await interaction.response.send_message(
                "지금 콜할 것이 없어요.", ephemeral=True)
            auto_dismiss(interaction, 10)
            return
        tile = (t.round.pending_kakan[1] if t.round.pending_kakan
                else t.round.last_discard[1])
        await interaction.response.send_message(
            f"❗ {tile_glyph(tile)} — 콜?", view=CallView(t, seat, t.round.pending_calls[seat]),
            ephemeral=True)

    async def _skip(self, interaction: discord.Interaction):
        t = self.table
        seat = self._seat(interaction)
        if seat is None:
            await interaction.response.send_message(
                "지금 콜할 것이 없어요.", ephemeral=True)
            auto_dismiss(interaction, 10)
            return
        await interaction.response.send_message("⏭️ 스킵했어요.", ephemeral=True)
        auto_dismiss(interaction, 10)
        await record_call(t, seat, "skip", None)


class ControlView(discord.ui.View):
    """Persistent channel controls for mobile (ephemeral) mode.

    Anyone can tap; the bot replies privately (ephemeral) based on who they are
    and the current game state, so no DM ↔ server switching is needed.
    """

    def __init__(self, table: Table):
        super().__init__(timeout=None)
        self.table = table
        self.add_item(Btn("🎴 내 손패", self._hand, style=discord.ButtonStyle.primary))
        self.add_item(Btn("🔔 콜", self._call, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("⚙️ 내 방식", self._my_mode, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("🚪 나가기", self._leave_game, style=discord.ButtonStyle.danger))

    async def _leave_game(self, interaction: discord.Interaction):
        """Drop out mid-game — the AI takes over the seat so the hand continues."""
        t = self.table
        if not is_live(t):
            return await dead_game(interaction)
        if t.seat_of(interaction.user.id) is None:
            await interaction.response.send_message("이 게임의 플레이어가 아니에요.",
                                                    ephemeral=True)
            return
        name = interaction.user.display_name
        await interaction.response.send_message(
            "🚪 나갔어요. 남은 자리는 AI가 이어서 둘게요.", ephemeral=True)
        await t.channel.send(f"🚪 **{name}** 님이 나가서 **AI가 대신** 둡니다 🤖")
        await leave_mid_game(t, interaction.user.id)

    async def _my_mode(self, interaction: discord.Interaction):
        """Toggle *this player's* hand delivery, independent of the table default."""
        t = self.table
        if not is_live(t):
            return await dead_game(interaction)
        uid = interaction.user.id
        if t.seat_of(uid) is None:
            await interaction.response.send_message("이 게임의 플레이어가 아니에요.",
                                                    ephemeral=True)
            return
        new = "dm" if t.mode_of(uid) == "channel" else "channel"
        t.player_modes[uid] = new
        set_user_pref(uid, new)  # 다음 판에도 기억해요
        if new == "dm":
            msg = ("💻 **DM 방식**으로 바꿨어요 — 이제 손패가 DM으로 자동으로 와요.\n"
                   "_DM이 막혀 있으면 자동으로 채널 방식으로 돌아가요._")
        else:
            msg = ("📱 **채널 방식**으로 바꿨어요 — **🎴 내 손패** 버튼으로 진행하세요.\n"
                   "_이 설정은 나에게만 적용돼요._")
        await interaction.response.send_message(
            msg + "\n_이 선택은 다음 판에도 기억돼요._", ephemeral=True)
        auto_dismiss(interaction, 20)

    async def _hand(self, interaction: discord.Interaction):
        t = self.table
        if not is_live(t):
            return await dead_game(interaction)
        seat = t.seat_of(interaction.user.id)
        if seat is None or not t.round:
            await interaction.response.send_message("이 게임의 플레이어가 아니에요.",
                                                    ephemeral=True)
            return
        r = t.round
        p = r.players[seat]
        if r.phase == "action" and r.turn == seat:
            await interaction.response.send_message(
                turn_content(t, p), view=TurnView(t, seat), ephemeral=True)
        else:
            await interaction.response.send_message(
                readonly_hand_content(t, p), ephemeral=True)
            auto_dismiss(interaction)  # 보기 전용 패널은 잠시 뒤 사라져요

    async def _call(self, interaction: discord.Interaction):
        t = self.table
        if not is_live(t):
            return await dead_game(interaction)
        seat = t.seat_of(interaction.user.id)
        if (seat is not None and t.awaiting and seat in t.call_eligible
                and seat not in t.call_choices):
            tile = (t.round.pending_kakan[1] if t.round.pending_kakan
                    else t.round.last_discard[1])
            await interaction.response.send_message(
                f"❗ {tile_glyph(tile)} — 콜?", view=CallView(t, seat, t.round.pending_calls[seat]),
                ephemeral=True)
        else:
            await interaction.response.send_message("지금 콜할 것이 없어요.", ephemeral=True)


class LobbyView(discord.ui.View):
    def __init__(self, table: Table):
        super().__init__(timeout=None)
        self.table = table
        self.add_item(Btn("참가", self._join, style=discord.ButtonStyle.success))
        self.add_item(Btn("나가기", self._leave, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("⚙️ 내 방식", self._my_mode, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("AI 추가", self._ai, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("기본 방식 전환", self._mode, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("시작", self._start, style=discord.ButtonStyle.primary))
        self.add_item(Btn("빈자리 AI 채우고 시작", self._fill_start,
                          style=discord.ButtonStyle.primary))
        self.add_item(Btn("취소", self._cancel, style=discord.ButtonStyle.danger))

    def _text(self) -> str:
        t = self.table
        host = f"　(방장: <@{t.host_id}>)" if t.host_id else ""
        def tag(p):
            if p.is_ai:
                return " 🤖"
            # 개인 설정(이번 판 선택 또는 기억된 취향)이 있는 사람만 표시해요
            if p.user_id in t.player_modes or p.user_id in user_prefs:
                return " 📱" if t.mode_of(p.user_id) == "channel" else " 💻"
            return ""

        names = "\n".join(f"　{i+1}. {p.name}{tag(p)}" for i, p in enumerate(t.seats)) \
            or "　(아직 없음)"
        mode = ("📱 채널(모바일: 나만 보이는 손패)" if t.mode == "channel"
                else "💻 DM(PC: 손패 자동 전송)")
        return (f"🀄 **{t.size}인 리치마작 로비** ({len(t.seats)}/{t.size}){host}\n{names}\n\n"
                f"손패 방식(기본값): **{mode}**　_**⚙️ 내 방식** 으로 각자 따로 정할 수 있어요_\n"
                f"**참가**·**나가기**·**⚙️ 내 방식**은 누구나 · 시작/취소/AI/기본방식은 **방장 전용** 👑")

    def _is_host(self, interaction) -> bool:
        return self.table.host_id == interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ignore clicks on a lobby message left over from a closed game."""
        if is_live(self.table) and not self.table.started:
            return True
        await interaction.response.edit_message(
            content="이미 닫힌 로비예요. `!mj` 로 새로 열어주세요.",
            view=disable_view(self))
        return False

    async def _deny(self, interaction):
        await interaction.response.send_message("방장만 할 수 있어요.", ephemeral=True)

    async def _mode(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        self.table.mode = "dm" if self.table.mode == "channel" else "channel"
        self.table.touch_lobby()
        await interaction.response.edit_message(content=self._text(), view=self)

    async def _my_mode(self, interaction):
        """Pick your own delivery mode before the game starts (anyone)."""
        t = self.table
        uid = interaction.user.id
        if t.seat_of(uid) is None:
            await interaction.response.send_message(
                "먼저 **참가** 를 눌러 자리에 앉아주세요.", ephemeral=True)
            return
        new = "dm" if t.mode_of(uid) == "channel" else "channel"
        t.player_modes[uid] = new
        set_user_pref(uid, new)  # 다음 판에도 기억해요
        t.touch_lobby()
        if new == "dm":
            msg = ("💻 **DM 방식**으로 설정했어요 — 시작하면 손패가 DM으로 자동으로 와요.\n"
                   "_DM이 막혀 있으면 자동으로 채널 방식으로 돌아가요._")
        else:
            msg = ("📱 **채널 방식**으로 설정했어요 — **🎴 내 손패** 버튼으로 진행해요.\n"
                   "_이 설정은 나에게만 적용돼요._")
        await interaction.response.send_message(
            msg + "\n_이 선택은 다음 판에도 기억돼요._", ephemeral=True)
        auto_dismiss(interaction, 20)

    async def _join(self, interaction):
        if self.table.add_human(interaction.user):
            self.table.touch_lobby()
            await interaction.response.edit_message(content=self._text(), view=self)
        else:
            await interaction.response.send_message("참가할 수 없어요 (이미 참가/자리 참).",
                                                    ephemeral=True)

    async def _leave(self, interaction):
        """Anyone may leave their own seat before the game starts."""
        t = self.table
        if not t.remove_human(interaction.user.id):
            await interaction.response.send_message("참가한 상태가 아니에요.", ephemeral=True)
            return
        if not any(not p.is_ai for p in t.seats):
            # 사람이 아무도 안 남으면 방을 닫아요
            await t.leave_voice()
            tables.pop(t.channel.id, None)
            await interaction.response.edit_message(
                content="🛑 모두 나가서 로비를 닫았어요. `!mj` 로 다시 열 수 있어요.",
                view=disable_view(self))
            return
        t.touch_lobby()
        await interaction.response.edit_message(content=self._text(), view=self)

    async def _ai(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        if self.table.add_ai():
            self.table.touch_lobby()
            await interaction.response.edit_message(content=self._text(), view=self)
        else:
            await interaction.response.send_message("자리가 없어요.", ephemeral=True)

    async def _start(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        t = self.table
        if len(t.seats) != t.size:
            await interaction.response.send_message(
                f"인원이 부족해요 ({len(t.seats)}/{t.size}). '빈자리 AI 채우고 시작'을 눌러보세요.",
                ephemeral=True)
            return
        if t.started:
            return
        t.shuffle_seats()
        t.started = True
        await interaction.response.edit_message(content="🀄 대국 시작!",
                                                view=disable_view(self))
        await send_transient(t, seating_line(t))
        await start_round(t)

    async def _fill_start(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        t = self.table
        if t.started:
            return
        while len(t.seats) < t.size:
            t.add_ai()
        t.shuffle_seats()
        t.started = True
        await interaction.response.edit_message(content="🤖 빈자리를 AI로 채우고 🀄 대국 시작!",
                                                view=disable_view(self))
        await t.channel.send(seating_line(t))
        await start_round(t)

    async def _cancel(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        t = self.table
        await t.leave_voice()
        t.cancel_timers()
        tables.pop(t.channel.id, None)
        t.lobby_msg = None  # 이 메시지는 아래에서 안내문으로 바꿔 남겨둬요
        await clear_transient(t)
        await interaction.response.edit_message(content="🛑 취소되었어요.",
                                                view=disable_view(self))


# ---------------------------------------------------------------------------
# game flow
# ---------------------------------------------------------------------------
async def announce_discard(table: Table, p: Player, tile: Tile, silent: bool = False):
    if silent:
        return
    await update_board(table, note=f"🀫 **{p.name}** 버림 → {tile_glyph(tile)}")


def seating_line(table: Table) -> str:
    order = " · ".join(f"{i+1}. {p.name}" for i, p in enumerate(table.seats))
    return f"🎲 **자리 정하기** (무작위) — {order}"


async def leave_mid_game(table: Table, user_id: int) -> bool:
    """Hand a seat over to the AI so a player can drop out mid-hand."""
    seat = table.seat_of(user_id)
    r = table.round
    if seat is None or r is None or not table.started:
        return False
    p = table.seats[seat]
    p.is_ai = True
    p.name = f"{p.name} 🤖"
    p.user_id = 0
    table.player_modes.pop(user_id, None)

    if not any(not q.is_ai for q in table.seats):
        await table.channel.send("🚪 사람이 모두 나가서 대국을 종료할게요.")
        await end_game(table, "남은 플레이어가 없습니다.")
        return True

    # 그 사람 차례였거나 콜 대기 중이었으면 판이 멈추지 않게 이어줘요
    if table.awaiting and seat in table.call_eligible and seat not in table.call_choices:
        await record_call(table, seat, "skip", None)
    elif r.phase == "action" and r.turn == seat:
        await advance(table)
    return True


async def start_round(table: Table):
    cfg = GameConfig(three_player=(table.size == 3))
    table.round = Round(
        table.seats, dealer=table.dealer,
        round_wind=[27, 28, 29, 30][table.round_wind_idx],
        honba=table.honba, riichi_sticks=table.riichi_sticks,
        config=cfg, rng=random.Random(),
    )
    table.started = True
    table.board_msg = None   # fresh floor for the new round
    await update_board(table)
    # 방식과 무관하게 항상 띄워요 — 각자 **⚙️ 내 방식** 으로 바꿀 수 있으니까요
    table.control_msg = await send_transient(
        table,
        "📱 아래 버튼으로 진행하세요 — 손패는 **나만 보여요**.\n"
        "PC라서 DM으로 받고 싶으면 **⚙️ 내 방식** 을 눌러 개인 설정을 바꾸세요.",
        view=ControlView(table))
    await advance(table)


async def send_turn(table: Table, seat: int):
    p = table.round.players[seat]
    await update_board(table)  # move the ▶️ marker to this player
    # 자리를 비워도 판이 멈추지 않도록, 방식과 무관하게 AFK 보호를 걸어둬요.
    # 이전 차례의 타이머는 확실히 취소하고, 이번 차례에만 유효한 토큰을 넘겨요.
    if table.afk_task is not None and not table.afk_task.done():
        table.afk_task.cancel()
    table.turn_token += 1
    table.afk_task = asyncio.create_task(
        _turn_afk(table, seat, len(p.discards), table.round, table.turn_token))
    if table.mode_of(p.user_id) == "dm":
        try:
            dm = await dm_of(p.user_id)
            await dm.send(content=turn_content(table, p), view=TurnView(table, seat))
            await clear_notice(table, "turn")  # DM 으로 갔으니 채널 알림은 치워요
            return
        except discord.Forbidden:
            # DM 이 막혀 있으면 채널 방식으로 자연스럽게 넘어가요
            await table.channel.send(
                f"⚠️ <@{p.user_id}> DM을 열 수 없어 채널 방식으로 진행할게요.")
    # channel mode: no DM push; nudge the player to tap 🎴 내 손패
    await send_notice(table, "turn",
                      f"▶️ <@{p.user_id}> 님 차례 — **🎴 내 손패** 를 누르세요")


async def advance(table: Table):
    """Drive AI turns and auto-passes until a human must act or the round ends."""
    r = table.round
    while True:
        if r.phase == "ended":
            await finish_round(table)
            return
        if r.phase == "action":
            p = r.current()
            if p.is_ai:
                await asyncio.sleep(1)
                if r.can_tsumo():
                    r.declare_tsumo()
                    continue
                tile = p.drawn or p.sorted_hand()[-1]
                r.discard(tile)
                await announce_discard(table, p, tile)
                continue
            await send_turn(table, p.seat)
            return
        if r.phase == "calls":
            eligible = {s for s in r.pending_calls if not r.players[s].is_ai}
            if not eligible:
                r.pass_calls()
                continue
            await run_call_window(table)
            return


async def run_call_window(table: Table):
    r = table.round
    tile = (r.pending_kakan[1] if r.pending_kakan else r.last_discard[1])
    table.awaiting = True
    table.call_resolved = False
    table.call_choices = {}
    table.call_messages = {}
    table.call_eligible = {s for s in r.pending_calls if not r.players[s].is_ai}

    # DM 방식인 사람에게만 콜 창을 밀어주고, 채널 방식인 사람은 🔔 콜 버튼을 눌러요
    dm_seats = [s for s in table.call_eligible
                if table.mode_of(r.players[s].user_id) == "dm"]
    for s in dm_seats:
        p = r.players[s]
        try:
            dm = await dm_of(p.user_id)
            msg = await dm.send(
                content=f"❗ **{tile_glyph(tile)}** — 콜 하시겠어요? ({CALL_TIMEOUT}s)",
                view=CallView(table, s, r.pending_calls[s]))
            table.call_messages[s] = msg
        except discord.Forbidden:
            pass

    if len(dm_seats) == len(table.call_eligible):
        where = f"(DM 또는 아래 버튼으로 {CALL_TIMEOUT}초 안에 선택)"
    else:
        where = f"(아래 버튼으로 {CALL_TIMEOUT}초 안에 선택 · 안 누르면 자동 스킵)"

    await send_notice(
        table, "call",
        f"❗ {tile_glyph(tile)} 에 콜 가능: "
        + ", ".join(f"<@{r.players[s].user_id}>" for s in table.call_eligible)
        + f" {where}",
        view=CallNoticeView(table))

    table.call_task = asyncio.create_task(_call_timer(table))


async def _lobby_expiry(table: Table):
    """Close a lobby that never started, once it has been idle long enough."""
    loop = asyncio.get_running_loop()
    while True:
        remaining = table.lobby_deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)  # deadline may have moved; loop re-checks

    if table.started or tables.get(table.channel.id) is not table:
        return  # 이미 시작했거나 닫힌 방
    tables.pop(table.channel.id, None)
    await table.leave_voice()
    idle = (f"{LOBBY_TIMEOUT // 60}분" if LOBBY_TIMEOUT >= 60
            else f"{LOBBY_TIMEOUT}초")
    if table.lobby_msg is not None:
        try:
            await table.lobby_msg.edit(
                content=f"⌛ {idle} 동안 조용해서 로비를 닫았어요. `!mj` 로 다시 열 수 있어요.",
                view=None)
        except discord.HTTPException:
            pass


async def _turn_afk(table: Table, seat: int, ndiscards: int, rnd, token: int):
    """AFK guard: auto-discard if the player never acts (both modes).

    ``rnd``/``token`` pin this timer to one specific turn. Without them a timer
    from an earlier hand could wake up after the deal reset everyone's discards
    and match the conditions again, forcing a phantom discard and starting a
    second ``advance()`` loop on a table that had already finished.
    """
    await asyncio.sleep(TURN_TIMEOUT)
    r = table.round
    if r is not rnd or token != table.turn_token:
        return  # 이미 지난 차례의 타이머
    if (r and r.phase == "action" and r.turn == seat and not table.awaiting
            and len(r.players[seat].discards) == ndiscards):
        p = r.players[seat]
        tile = p.drawn or p.sorted_hand()[-1]
        r.discard(tile)
        await announce_discard(table, p, tile)
        await advance(table)


async def _call_timer(table: Table):
    try:
        await asyncio.sleep(CALL_TIMEOUT)
    except asyncio.CancelledError:
        return
    await resolve_calls(table)


async def record_call(table: Table, seat: int, action: str, value):
    if not is_live(table) or not table.awaiting or seat not in table.call_eligible:
        return
    table.call_choices[seat] = (action, value)
    # a ron resolves immediately; otherwise wait until everyone answered
    if action == "ron" or set(table.call_choices) >= table.call_eligible:
        if table.call_task:
            table.call_task.cancel()
        await resolve_calls(table)


async def resolve_calls(table: Table):
    if table.call_resolved:
        return
    table.call_resolved = True
    table.awaiting = False
    await clear_notice(table, "call")  # 콜 창이 닫혔으니 안내도 치워요
    r = table.round
    choices = table.call_choices

    # disable any outstanding call DMs
    for s, msg in table.call_messages.items():
        if s not in choices:
            try:
                await msg.edit(content="⏱️ 시간 초과 (스킵)", view=None)
            except discord.HTTPException:
                pass

    ronners = [s for s, (a, _) in choices.items() if a == "ron"]
    if ronners:
        r.call_ron(ronners)
        play_sound(table, "ron")
        await finish_round(table)
        return
    ponkan = [(s, a) for s, (a, _) in choices.items() if a in ("pon", "kan")]
    if ponkan:
        s, a = ponkan[0]
        (r.call_pon if a == "pon" else r.call_kan)(s)
        play_sound(table, a)  # pon / kan
        await send_transient(table, f"**{r.players[s].name}** {'퐁' if a == 'pon' else '깡'}!")
        if a == "kan":
            await update_board(table)
        await advance(table)
        return
    chis = [(s, v) for s, (a, v) in choices.items() if a == "chi"]
    if chis:
        s, combo = chis[0]
        r.call_chi(s, list(combo))
        play_sound(table, "chi")
        await send_transient(table, f"**{r.players[s].name}** 치!")
        await advance(table)
        return
    r.pass_calls()
    await advance(table)


async def finish_round(table: Table):
    # 국이 끝났으니 남아 있는 차례/콜 알림은 의미가 없어요
    await clear_notice(table, "turn")
    await clear_notice(table, "call")
    r = table.round
    res = r.result
    lines = []
    if res.kind == "tsumo":
        w = r.players[res.winners[0]]
        lines.append(f"🏆 **{w.name}** 쯔모!")
        lines.append(score_summary(res.scores[res.winners[0]]))
    elif res.kind == "ron":
        for s in res.winners:
            lines.append(f"🏆 **{r.players[s].name}** 론! (방총: {r.players[res.loser].name})")
            lines.append(score_summary(res.scores[s]))
    else:
        tenpai = ", ".join(r.players[s].name for s in res.tenpai) or "없음"
        lines.append(f"🌊 유국 — 텐파이: {tenpai}")
    lines.append(" · ".join(f"{p.name} **{p.points}**({res.deltas[p.seat]:+d})"
                            for p in r.players))
    await table.channel.send("\n".join(lines))

    # rotate dealer / honba / round wind
    table.riichi_sticks = r.riichi_sticks
    if res.dealer_repeat:
        table.honba += 1
    else:
        table.honba = 0
        table.dealer += 1
        if table.dealer >= table.size:
            table.dealer = 0
            table.round_wind_idx = min(table.round_wind_idx + 1, 3)

    table.started = False
    if any(p.points < 0 for p in r.players):
        await end_game(table, "누군가 점수가 0 미만이 되어 종료합니다.")
        return
    await send_transient(table, "다음 국은 아래 **다음 국** 버튼으로!", view=NextView(table))


class NextView(discord.ui.View):
    def __init__(self, table: Table):
        super().__init__(timeout=None)
        self.table = table
        self.add_item(Btn("▶️ 다음 국", self._next, style=discord.ButtonStyle.success))
        self.add_item(Btn("🎌 종료", self._end, style=discord.ButtonStyle.danger))

    async def _next(self, interaction):
        if not is_live(self.table):
            return await dead_game(interaction)
        # 대국에 앉아 있는 사람만 (구경꾼이 진행시키면 안 되니까)
        if self.table.seat_of(interaction.user.id) is None:
            await interaction.response.send_message("이 대국의 플레이어가 아니에요.",
                                                    ephemeral=True)
            return
        if self.table.started:
            return
        await interaction.response.edit_message(content="다음 국 시작!", view=None)
        await start_round(self.table)

    async def _end(self, interaction):
        if not is_live(self.table):
            return await dead_game(interaction)
        # 종료는 대국을 끝내는 되돌릴 수 없는 동작이라 방장만
        if self.table.host_id != interaction.user.id:
            await interaction.response.send_message(
                "대국 종료는 **방장만** 할 수 있어요.", ephemeral=True)
            return
        await interaction.response.edit_message(view=None)
        await end_game(self.table, "방장이 대국을 종료했습니다.")


async def end_game(table: Table, reason: str):
    if tables.get(table.channel.id) is not table:
        return  # 이미 종료된 대국 (버튼 중복 클릭 등)
    table.cancel_timers()  # 남은 타이머가 끝난 판을 건드리지 않게
    table.started = False
    r = table.round
    if r is None:  # 아직 한 국도 시작하지 않은 방 (로비만 있던 상태)
        tables.pop(table.channel.id, None)
        await clear_transient(table)
        await table.channel.send(f"🛑 **방을 닫았어요**\n{reason}")
        await table.leave_voice()
        return
    ranking = sorted(r.players, key=lambda p: -p.points)
    board = "\n".join(f"{i+1}위 **{p.name}** — {p.points}점"
                      for i, p in enumerate(ranking))
    tables.pop(table.channel.id, None)
    await clear_transient(table)  # 진행용 메시지·버튼을 치우고 결과만 남겨요
    await table.channel.send(f"🎌 **대국 종료**\n{reason}\n{board}")
    await table.leave_voice()


# ---------------------------------------------------------------------------
# sound effect management (`!mj sound ...`) — per server, admins only
# ---------------------------------------------------------------------------
SOUND_HELP = (
    "🔊 **효과음 설정** (서버 관리자 전용)\n"
    f"등록 가능: {' · '.join(f'`{n}`' for n in SOUND_NAMES)}\n"
    "· `!mj sound` — 현재 등록된 효과음 목록\n"
    "· `!mj sound <이름>` + **음성파일 첨부** — 등록/교체 (여러 개 첨부 가능)\n"
    "· `!mj sound add <이름>` + 첨부 — 기존 건 두고 **한 종류 더** 추가\n"
    "　같은 이름에 여러 개면 **랜덤**으로 하나씩 재생돼요 🎲\n"
    "· `!mj sound clear <이름>` — 삭제 (`clear all` 이면 전체)\n"
    f"파일: {' / '.join(SOUND_EXTS)} · 최대 {MAX_SOUND_BYTES // 1024}KB · 짧게(1~3초) 권장"
)


def _is_sound_admin(ctx) -> bool:
    perms = getattr(ctx.author, "guild_permissions", None)
    return bool(perms and (perms.manage_guild or perms.administrator))


async def sound_cmd(ctx, args: list[str]):
    if ctx.guild is None:
        await ctx.send("효과음 설정은 서버 채널에서만 할 수 있어요.")
        return

    # list ---------------------------------------------------------------
    if not args or args[0] in ("list", "목록"):
        lines = []
        for n in SOUND_NAMES:
            hits = sound_variants(n, ctx.guild.id)
            more = f" · {len(hits)}종 랜덤" if len(hits) > 1 else ""
            if hits and hits[0].startswith(guild_sounds_dir(ctx.guild.id)):
                lines.append(f"· `{n}` — ✅ 이 서버 전용{more}")
            elif hits:
                lines.append(f"· `{n}` — 🌐 기본 효과음{more}")
            else:
                lines.append(f"· `{n}` — ❌ 없음")
        await ctx.send("🔊 **이 서버의 효과음**\n" + "\n".join(lines)
                       + f"\n\n{SOUND_HELP}")
        return

    # clear ---------------------------------------------------------------
    if args[0] in ("clear", "삭제"):
        if not _is_sound_admin(ctx):
            await ctx.send("서버 관리자만 효과음을 바꿀 수 있어요.")
            return
        target = args[1] if len(args) > 1 else None
        if target not in SOUND_NAMES and target != "all":
            await ctx.send(f"사용법: `!mj sound clear <{'|'.join(SOUND_NAMES)}|all>`")
            return
        names = SOUND_NAMES if target == "all" else (target,)
        removed = []
        gdir = guild_sounds_dir(ctx.guild.id)
        for n in names:
            for p in sound_variants(n, ctx.guild.id):
                if p.startswith(gdir):  # 기본 효과음은 건드리지 않아요
                    os.remove(p)
                    removed.append(n)
        if removed:
            await ctx.send(f"🗑 삭제했어요: {', '.join(f'`{n}`' for n in removed)}"
                           " (기본 효과음이 있으면 그걸로 돌아가요)")
        else:
            await ctx.send("지울 게 없어요 (이 서버에 등록된 효과음이 없음).")
        return

    # upload --------------------------------------------------------------
    # `!mj sound add tsumo` 는 기존 것을 두고 한 종류를 더 얹어요
    append = args[0] in ("add", "추가")
    name = args[1] if append and len(args) > 1 else args[0]
    if name not in SOUND_NAMES:
        await ctx.send(f"`{name}` 은(는) 모르는 이름이에요.\n\n{SOUND_HELP}")
        return
    if not _is_sound_admin(ctx):
        await ctx.send("서버 관리자만 효과음을 등록할 수 있어요.")
        return
    if not ctx.message.attachments:
        await ctx.send(f"음성 파일을 **첨부**해서 다시 보내주세요.\n\n{SOUND_HELP}")
        return

    dest_dir = guild_sounds_dir(ctx.guild.id)
    os.makedirs(dest_dir, exist_ok=True)
    gdir_hits = [p for p in sound_variants(name, ctx.guild.id) if p.startswith(dest_dir)]
    if not append:
        for p in gdir_hits:  # 교체: 이 서버의 기존 녹음은 지워요
            os.remove(p)
        gdir_hits = []

    saved = []
    for att in ctx.message.attachments:  # 한 번에 여러 개 첨부해도 돼요
        ext = os.path.splitext(att.filename)[1].lower()
        if ext not in SOUND_EXTS:
            await ctx.send(f"건너뜀 — 지원하지 않는 형식 `{att.filename}` "
                           f"({' / '.join(SOUND_EXTS)} 만 돼요)")
            continue
        if att.size > MAX_SOUND_BYTES:
            await ctx.send(f"건너뜀 — `{att.filename}` 이 너무 커요 "
                           f"({att.size // 1024}KB > {MAX_SOUND_BYTES // 1024}KB)")
            continue
        n = len(gdir_hits) + len(saved)
        stem = name if n == 0 else f"{name}{n + 1}"  # tsumo, tsumo2, tsumo3 …
        try:
            await att.save(os.path.join(dest_dir, stem + ext))
            saved.append(stem + ext)
        except Exception as exc:
            print(f"[sound] save failed: {exc}", flush=True)
            await ctx.send(f"`{att.filename}` 저장에 실패했어요.")

    if not saved:
        return
    total = len(sound_variants(name, ctx.guild.id))
    extra = f"\n이제 `{name}` 은 **{total}종 중 랜덤**으로 재생돼요 🎲" if total > 1 else ""
    await ctx.send(f"✅ `{name}` 효과음 **{len(saved)}개** 등록! ({', '.join(saved)}){extra}")


# ---------------------------------------------------------------------------
# force-stop (`!mj stop`) — escape hatch for a game that got stuck
# ---------------------------------------------------------------------------
async def stop_cmd(ctx):
    """Abort the channel's game.

    🎌 종료 only appears between hands, so a game that wedges mid-hand used to
    leave the channel unusable — `!mj` just kept answering "이미 이 채널에
    게임이 있어요" with no way out short of restarting the bot.
    """
    table = tables.get(ctx.channel.id)
    if table is None:
        await ctx.send("이 채널에 진행 중인 대국이 없어요. `!mj` 로 새로 열 수 있어요.")
        return

    perms = getattr(ctx.author, "guild_permissions", None)
    is_admin = bool(perms and (perms.manage_guild or perms.administrator))
    if table.host_id != ctx.author.id and not is_admin:
        await ctx.send("**방장** 또는 **서버 관리자**만 강제 종료할 수 있어요.")
        return

    await end_game(table, f"{ctx.author.display_name} 님이 강제 종료했습니다.")
    await ctx.send("정리했어요. `!mj` 로 새 판을 열 수 있어요 🀄")


# ---------------------------------------------------------------------------
# voice channel (`!mj voice`) — call the bot in or send it away mid-game
# ---------------------------------------------------------------------------
async def voice_cmd(ctx, args: list[str]):
    table = tables.get(ctx.channel.id)
    if table is None:
        await ctx.send("이 채널에 진행 중인 대국이 없어요. 먼저 `!mj` 로 방을 열어주세요.")
        return

    if args and args[0] in ("leave", "out", "나가", "퇴장"):
        if table.voice is None:
            await ctx.send("봇이 음성 채널에 없어요.")
            return
        await table.leave_voice()
        await ctx.send("👋 음성 채널에서 나왔어요.")
        return

    ch = await table.join_voice(ctx.author)
    if ch is None:
        if getattr(getattr(ctx.author, "voice", None), "channel", None) is None:
            await ctx.send("먼저 **음성 채널에 들어간 뒤** `!mj voice` 를 쳐주세요 🎙")
        else:
            await ctx.send("음성 채널에 못 들어갔어요. 봇에게 **연결·말하기** 권한이 있는지 "
                           "확인해주세요.")
        return
    n = sum(len(sound_variants(s, ctx.guild.id if ctx.guild else None))
            for s in SOUND_NAMES)
    tail = "" if n else "\n_아직 등록된 효과음이 없어요 — `!mj sound` 로 올릴 수 있어요._"
    await ctx.send(f"🎙 **{ch.name}** 에 들어왔어요! 이제 효과음이 나와요 🔊{tail}")


# ---------------------------------------------------------------------------
# tile emoji management (`!mj emoji ...`) — bot owner only
# ---------------------------------------------------------------------------
async def emoji_cmd(ctx, args: list[str]):
    action = args[0] if args else "status"

    if action in ("status", "상태"):
        n = len(render_module.GLYPH_OVERRIDE)
        if n:
            sample = " ".join(list(render_module.GLYPH_OVERRIDE.values())[:9])
            await ctx.send(f"🀄 타일 이모지 **{n}개** 사용 중이에요.\n{sample}")
        else:
            await ctx.send("타일 이모지가 없어서 기본 유니코드 문자를 쓰고 있어요.\n"
                           "봇 소유자가 `!mj emoji install` 을 실행하면 예쁜 패로 바뀌어요.")
        return

    if not await bot.is_owner(ctx.author):
        await ctx.send("이 명령은 **봇 소유자**만 쓸 수 있어요.")
        return

    if action in ("install", "설치", "업로드"):
        if not os.path.isdir(TILES_DIR):
            await ctx.send(f"타일 이미지 폴더가 없어요: `{TILES_DIR}`")
            return
        try:
            existing = {e.name for e in await bot.fetch_application_emojis()}
        except Exception as exc:
            await ctx.send(f"이모지 목록을 못 읽었어요: `{exc}`")
            return

        msg = await ctx.send("🀄 타일 이모지를 올리는 중… (37개, 1분쯤 걸려요)")
        added = skipped = failed = 0
        for kind in range(34):
            for aka in (False, True):
                if aka and kind not in (4, 13, 22):
                    continue
                stem = tile_asset_name(kind, aka)
                name = EMOJI_PREFIX + stem
                if name in existing:
                    skipped += 1
                    continue
                path = os.path.join(TILES_DIR, f"{stem}.png")
                if not os.path.exists(path):
                    failed += 1
                    continue
                try:
                    with open(path, "rb") as f:
                        await bot.create_application_emoji(name=name, image=f.read())
                    added += 1
                except Exception as exc:
                    print(f"[emoji] upload {name} failed: {exc}", flush=True)
                    failed += 1
        n = await load_tile_emoji()
        await msg.edit(content=(
            f"✅ 완료 — 새로 올림 **{added}** · 이미 있음 **{skipped}**"
            + (f" · 실패 **{failed}**" if failed else "")
            + f"\n이제 **{n}개**의 타일 이모지를 씁니다. 모든 서버에서 바로 적용돼요!"))
        return

    if action in ("remove", "삭제", "제거"):
        try:
            emojis = await bot.fetch_application_emojis()
        except Exception as exc:
            await ctx.send(f"이모지 목록을 못 읽었어요: `{exc}`")
            return
        removed = 0
        for e in emojis:
            if e.name.startswith(EMOJI_PREFIX):
                try:
                    await e.delete()
                    removed += 1
                except Exception:
                    pass
        render_module.GLYPH_OVERRIDE.clear()
        await ctx.send(f"🗑 타일 이모지 **{removed}개**를 지웠어요. 유니코드 문자로 돌아갑니다.")
        return

    await ctx.send("사용법: `!mj emoji status` · `!mj emoji install` · `!mj emoji remove`")


# ---------------------------------------------------------------------------
# entry command
# ---------------------------------------------------------------------------
GREETINGS = ("안녕", "안녕하세요", "안뇽", "하이", "ㅎㅇ", "hi", "hello", "헬로",
             "인사", "여어", "반가워", "왔니", "야")

HELLOS = (
    "🀄 안녕하세요! 마작 봇이에요. 오늘도 즐거운 대국 되세요!",
    "🀄 안녕하세요~ 부르셨나요? 패는 언제나 준비돼 있어요!",
    "🀄 반갑습니다! 한 판 하실래요? 자리는 넉넉해요.",
    "🀄 안녕하세요! 오늘은 리치 한 방 크게 가시죠 🎏",
    "🀄 어서 오세요! 쯔모 잘 붙는 날이길 바랄게요.",
)

MENU = ("· `!mj` — 4인 대국\n"
        "· `!mj 3` — 3인(산마)\n"
        "· `!mj sound` — 효과음 설정\n"
        "· `!mj help` — 자세한 도움말")


@bot.command(name="mj")
async def mj_cmd(ctx, arg: str = None, *rest: str):
    if arg and arg.lower() in GREETINGS:
        await ctx.send(f"{random.choice(HELLOS)}\n{MENU}")
        return
    if arg in ("help", "도움", "도움말"):
        await ctx.send(
            "🀄 **리치마작 봇**\n"
            "`!mj` (4인) 또는 `!mj 3` (3인) 으로 로비를 열고, 나머지는 **버튼**으로 진행해요.\n"
            "손패 방식은 로비에서 전환할 수 있어요:\n"
            "· 📱 **채널(모바일)**: 채널의 **🎴 내 손패** 버튼 → 나만 보이는 손패. DM 전환 없음\n"
            "· 💻 **DM(PC)**: 손패가 DM으로 자동으로 와요 (DM 허용 필요)\n"
            "남이 버리면 **🔔 콜**(채널) 또는 DM으로 론/퐁/치/깡/스킵 버튼이 떠요.\n"
            "🔊 효과음: `!mj sound` (서버 관리자만 등록/삭제 가능)\n"
            "🎙 `!mj voice` — 봇을 지금 내 음성 채널로 부르기 (`!mj voice leave` 는 내보내기)\n"
            "🛑 `!mj stop` — 진행 중인 판이 꼬였을 때 강제 종료 (방장·서버 관리자)")
        return
    if arg in ("sound", "효과음"):
        await sound_cmd(ctx, list(rest))
        return
    if arg in ("emoji", "이모지"):
        await emoji_cmd(ctx, list(rest))
        return
    if arg in ("voice", "음성", "보이스"):
        await voice_cmd(ctx, list(rest))
        return
    if arg in ("stop", "종료", "그만", "reset", "리셋"):
        await stop_cmd(ctx)
        return
    if ctx.guild is None:
        # DM 에서는 로비를 열 수 없어요 — 여러 명이 참가해야 하고, 나만 보이는
        # 손패·음성 효과음 같은 기능이 서버 채널을 전제로 하거든요.
        await ctx.send(
            "🀄 대국은 **서버 채널**에서 열어주세요! (DM에서는 방을 만들 수 없어요)\n"
            "서버 채널에서 `!mj` 를 치면 로비가 열려요.\n"
            "_손패를 DM으로 받고 싶으면, 게임 중 채널의 **⚙️ 내 방식** 버튼을 누르면 돼요._")
        return
    size = 3 if arg == "3" else 4
    if arg not in (None, "3", "4"):
        # 모르는 말이어도 무뚝뚝하게 굴지 않기 — 인사부터 하고 메뉴를 안내해요
        await ctx.send(f"{random.choice(HELLOS)}\n"
                       f"음… `{arg}` 라는 말은 제가 몰라요 😅 이런 걸 할 수 있어요:\n{MENU}")
        return
    if ctx.channel.id in tables:
        await ctx.send("이미 이 채널에 게임이 있어요.")
        return
    t = Table(ctx.channel, size)
    t.host_id = ctx.author.id
    t.add_human(ctx.author)
    tables[ctx.channel.id] = t
    # if the caller is in a voice channel, join it for sound effects
    await t.join_voice(ctx.author)
    view = LobbyView(t)
    t.lobby_msg = await ctx.send(view._text(), view=view)
    t.touch_lobby()  # 아무도 안 들어오면 자동으로 닫혀요


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})", flush=True)
    n = await load_tile_emoji()
    print(f"Tile emoji loaded: {n}"
          + ("" if n else "  (run `!mj emoji install` for tile images)"), flush=True)


def load_env_file() -> None:
    """Load KEY=VALUE lines from a `.env` file next to this script (if present).

    Keeps things dependency-free so casual users can just drop their token in
    `.env` and run the bot — no need to export it every time.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def main():
    load_env_file()
    token = os.environ.get("DISCORD_TOKEN")
    if not token or token == "your-bot-token-here":
        raise SystemExit(
            "❌ 봇 토큰이 없어요.\n"
            "   mahjong-bot 폴더의 .env.example 을 .env 로 복사한 뒤,\n"
            "   DISCORD_TOKEN=... 에 디스코드 봇 토큰을 넣어주세요."
        )
    print("봇을 켜는 중… (끄려면 이 창에서 Ctrl+C)")
    bot.run(token)


if __name__ == "__main__":
    main()

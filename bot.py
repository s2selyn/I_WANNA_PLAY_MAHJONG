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
import os
import random

import discord
from discord.ext import commands

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

CALL_TIMEOUT = 30   # seconds to decide on a call
TURN_TIMEOUT = 120  # auto-discard if a human goes AFK

# Optional sound effects: drop files like riichi.mp3 / ron.mp3 in sounds/.
# Missing files (or missing voice deps) are silently skipped.
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
SOUND_EXTS = (".mp3", ".ogg", ".wav", ".m4a")


def sound_path(name: str) -> str | None:
    for ext in SOUND_EXTS:
        p = os.path.join(SOUNDS_DIR, name + ext)
        if os.path.exists(p):
            return p
    return None


def play_sound(table: "Table", name: str) -> None:
    """Queue a sound effect for the table's voice channel (best-effort)."""
    vc = table.voice
    if not vc or not vc.is_connected():
        return
    path = sound_path(name)
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
        self.mode = "channel"  # "channel" (mobile, ephemeral) or "dm"
        self.host_id: int | None = None  # who opened the room (lobby controls)

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

    def seat_of(self, user_id: int) -> int | None:
        for p in self.seats:
            if p.user_id == user_id:
                return p.seat
        return None

    def round_wind_name(self) -> str:
        return kind_kr([27, 28, 29, 30][self.round_wind_idx])

    # voice ----------------------------------------------------------------
    async def join_voice(self, member) -> None:
        """Join the member's current voice channel (best-effort, optional)."""
        ch = getattr(getattr(member, "voice", None), "channel", None)
        if ch is None or self.voice is not None:
            return
        try:
            self.voice = await ch.connect()
        except Exception as exc:  # missing PyNaCl, no perms, etc. → text-only
            print(f"[voice] connect failed: {exc}")
            self.voice = None

    async def leave_voice(self) -> None:
        self.sound_queue.clear()
        if self.voice is not None:
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass
            self.voice = None


tables: dict[int, Table] = {}


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
        table.board_msg = await table.channel.send(content)
    else:
        try:
            await table.board_msg.edit(content=content)
        except discord.HTTPException:
            table.board_msg = await table.channel.send(content)


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
            self.add_item(Btn(f"{tile_button_emoji(t)} {tile_label(t)}",
                              self._make_discard(t)))

        if r.can_tsumo():
            self.add_item(Btn("🀄 쯔모", self._tsumo, style=discord.ButtonStyle.success))
        if r.can_riichi():
            self.add_item(Btn("🎏 리치", self._riichi, style=discord.ButtonStyle.primary))
        if r.kan_options():
            self.add_item(Btn("🔶 깡", self._kan, style=discord.ButtonStyle.primary))

    def _valid(self) -> bool:
        r = self.table.round
        return r and r.phase == "action" and r.turn == self.seat

    def _make_discard(self, tile: Tile):
        async def cb(interaction: discord.Interaction):
            if not self._valid():
                await interaction.response.edit_message(view=disable_view(self))
                return
            r = self.table.round
            r.discard(Tile(tile.kind, tile.aka))
            await interaction.response.edit_message(
                content=f"버렸어요: {tile_glyph(tile)}", view=disable_view(self))
            await announce_discard(self.table, r.players[self.seat], tile)
            await advance(self.table)
        return cb

    async def _tsumo(self, interaction: discord.Interaction):
        if not self._valid():
            return
        self.table.round.declare_tsumo()
        play_sound(self.table, "tsumo")
        await interaction.response.edit_message(content="🀄 쯔모!", view=disable_view(self))
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
            await interaction.response.edit_message(
                content=f"🎏 리치 선언, {tile_glyph(tile)} 버림", view=None)
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
            await interaction.response.edit_message(
                content=f"🔶 깡: {tile_glyph(tile)}", view=None)
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
            self.add_item(Btn(f"{tile_button_emoji(t)} {tile_label(t)}", cb_factory(t)))


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

    def _make(self, action, value=None):
        async def cb(interaction: discord.Interaction):
            await interaction.response.edit_message(
                content=f"선택: {action}", view=disable_view(self))
            await record_call(self.table, self.seat, action, value)
        return cb


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

    async def _hand(self, interaction: discord.Interaction):
        t = self.table
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

    async def _call(self, interaction: discord.Interaction):
        t = self.table
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
        self.add_item(Btn("AI 추가", self._ai, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("손패 방식 전환", self._mode, style=discord.ButtonStyle.secondary))
        self.add_item(Btn("시작", self._start, style=discord.ButtonStyle.primary))
        self.add_item(Btn("빈자리 AI 채우고 시작", self._fill_start,
                          style=discord.ButtonStyle.primary))
        self.add_item(Btn("취소", self._cancel, style=discord.ButtonStyle.danger))

    def _text(self) -> str:
        t = self.table
        host = f"　(방장: <@{t.host_id}>)" if t.host_id else ""
        names = "\n".join(f"　{i+1}. {p.name}{' 🤖' if p.is_ai else ''}"
                          for i, p in enumerate(t.seats)) or "　(아직 없음)"
        mode = ("📱 채널(모바일: 나만 보이는 손패)" if t.mode == "channel"
                else "💻 DM(PC: 손패 자동 전송)")
        return (f"🀄 **{t.size}인 리치마작 로비** ({len(t.seats)}/{t.size}){host}\n{names}\n\n"
                f"손패 방식: **{mode}**\n"
                f"누구나 **참가** 가능 · 시작/취소/AI/방식은 **방장 전용**")

    def _is_host(self, interaction) -> bool:
        return self.table.host_id == interaction.user.id

    async def _deny(self, interaction):
        await interaction.response.send_message("방장만 할 수 있어요.", ephemeral=True)

    async def _mode(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        self.table.mode = "dm" if self.table.mode == "channel" else "channel"
        await interaction.response.edit_message(content=self._text(), view=self)

    async def _join(self, interaction):
        if self.table.add_human(interaction.user):
            await interaction.response.edit_message(content=self._text(), view=self)
        else:
            await interaction.response.send_message("참가할 수 없어요 (이미 참가/자리 참).",
                                                    ephemeral=True)

    async def _ai(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        if self.table.add_ai():
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
        t.started = True
        await interaction.response.edit_message(content="🀄 대국 시작!",
                                                view=disable_view(self))
        await start_round(t)

    async def _fill_start(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        t = self.table
        if t.started:
            return
        while len(t.seats) < t.size:
            t.add_ai()
        t.started = True
        await interaction.response.edit_message(content="🤖 빈자리를 AI로 채우고 🀄 대국 시작!",
                                                view=disable_view(self))
        await start_round(t)

    async def _cancel(self, interaction):
        if not self._is_host(interaction):
            return await self._deny(interaction)
        await self.table.leave_voice()
        tables.pop(self.table.channel.id, None)
        await interaction.response.edit_message(content="🛑 취소되었어요.",
                                                view=disable_view(self))


# ---------------------------------------------------------------------------
# game flow
# ---------------------------------------------------------------------------
async def announce_discard(table: Table, p: Player, tile: Tile, silent: bool = False):
    if silent:
        return
    await update_board(table, note=f"🀫 **{p.name}** 버림 → {tile_glyph(tile)}")


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
    if table.mode == "channel":
        table.control_msg = await table.channel.send(
            "📱 아래 버튼으로 진행하세요 — 손패는 **나만 보여요**.",
            view=ControlView(table))
    await advance(table)


async def send_turn(table: Table, seat: int):
    p = table.round.players[seat]
    await update_board(table)  # move the ▶️ marker to this player
    if table.mode == "channel":
        # mobile: no DM push; nudge the player to tap 🎴 내 손패
        await table.channel.send(f"▶️ <@{p.user_id}> 님 차례 — **🎴 내 손패** 를 누르세요")
        asyncio.create_task(_turn_afk(table, seat, len(p.discards)))
        return
    try:
        dm = await dm_of(p.user_id)
        await dm.send(content=turn_content(table, p), view=TurnView(table, seat))
    except discord.Forbidden:
        await table.channel.send(
            f"⚠️ <@{p.user_id}> DM을 열 수 없어요. (개인정보 보호 → 서버 멤버 DM 허용)")


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

    if table.mode == "dm":
        for s in table.call_eligible:
            p = r.players[s]
            try:
                dm = await dm_of(p.user_id)
                msg = await dm.send(
                    content=f"❗ **{tile_glyph(tile)}** — 콜 하시겠어요? ({CALL_TIMEOUT}s)",
                    view=CallView(table, s, r.pending_calls[s]))
                table.call_messages[s] = msg
            except discord.Forbidden:
                pass
        where = f"(DM에서 {CALL_TIMEOUT}s 안에 선택)"
    else:
        # mobile: players tap the 🔔 콜 button for a private (ephemeral) prompt
        where = f"(**🔔 콜** 버튼을 {CALL_TIMEOUT}s 안에 누르세요)"

    await table.channel.send(
        f"❗ {tile_glyph(tile)} 에 콜 가능: "
        + ", ".join(f"<@{r.players[s].user_id}>" for s in table.call_eligible)
        + f" {where}")

    table.call_task = asyncio.create_task(_call_timer(table))


async def _turn_afk(table: Table, seat: int, ndiscards: int):
    """Channel-mode AFK guard: auto-discard if the player never acts."""
    await asyncio.sleep(TURN_TIMEOUT)
    r = table.round
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
    if not table.awaiting or seat not in table.call_eligible:
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
        await table.channel.send(f"**{r.players[s].name}** {'퐁' if a == 'pon' else '깡'}!")
        if a == "kan":
            await update_board(table)
        await advance(table)
        return
    chis = [(s, v) for s, (a, v) in choices.items() if a == "chi"]
    if chis:
        s, combo = chis[0]
        r.call_chi(s, list(combo))
        play_sound(table, "chi")
        await table.channel.send(f"**{r.players[s].name}** 치!")
        await advance(table)
        return
    r.pass_calls()
    await advance(table)


async def finish_round(table: Table):
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
    await table.channel.send("다음 국은 아래 **다음 국** 버튼으로!", view=NextView(table))


class NextView(discord.ui.View):
    def __init__(self, table: Table):
        super().__init__(timeout=None)
        self.table = table
        self.add_item(Btn("▶️ 다음 국", self._next, style=discord.ButtonStyle.success))
        self.add_item(Btn("🎌 종료", self._end, style=discord.ButtonStyle.danger))

    async def _next(self, interaction):
        if self.table.started:
            return
        await interaction.response.edit_message(content="다음 국 시작!", view=None)
        await start_round(self.table)

    async def _end(self, interaction):
        await interaction.response.edit_message(view=None)
        await end_game(self.table, "플레이어가 대국을 종료했습니다.")


async def end_game(table: Table, reason: str):
    r = table.round
    ranking = sorted(r.players, key=lambda p: -p.points)
    board = "\n".join(f"{i+1}위 **{p.name}** — {p.points}점"
                      for i, p in enumerate(ranking))
    await table.channel.send(f"🎌 **대국 종료**\n{reason}\n{board}")
    await table.leave_voice()
    tables.pop(table.channel.id, None)


# ---------------------------------------------------------------------------
# entry command
# ---------------------------------------------------------------------------
@bot.command(name="mj")
async def mj_cmd(ctx, arg: str = None):
    if arg in ("help", "도움", "도움말"):
        await ctx.send(
            "🀄 **리치마작 봇**\n"
            "`!mj` (4인) 또는 `!mj 3` (3인) 으로 로비를 열고, 나머지는 **버튼**으로 진행해요.\n"
            "손패 방식은 로비에서 전환할 수 있어요:\n"
            "· 📱 **채널(모바일)**: 채널의 **🎴 내 손패** 버튼 → 나만 보이는 손패. DM 전환 없음\n"
            "· 💻 **DM(PC)**: 손패가 DM으로 자동으로 와요 (DM 허용 필요)\n"
            "남이 버리면 **🔔 콜**(채널) 또는 DM으로 론/퐁/치/깡/스킵 버튼이 떠요.")
        return
    size = 3 if arg == "3" else 4
    if arg not in (None, "3", "4"):
        await ctx.send("사용법: `!mj` (4인) · `!mj 3` (3인) · `!mj help`")
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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")


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

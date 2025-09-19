import os
import logging
from typing import Optional, List

import math
import collections
import asyncio
import discord
from discord import app_commands
import asyncpg
import yt_dlp

# ─────────────────────────────
# 環境変数
# ─────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")         # Discord Botトークン
DATABASE_URL = os.getenv("DATABASE_URL")   # PostgreSQL接続文字列（自己紹介設定用）

# ─────────────────────────────
# ロギング
# ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s:%(name)s: %(message)s"
)
log = logging.getLogger("yado-bot")

# ─────────────────────────────
# Intents
# ─────────────────────────────
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True          # /hlt xp で本文検索に必要
intents.guild_scheduled_events = True   # eventrank に必要
intents.voice_states = True             # 音楽でVC状態を扱うなら有効が安心

# ─────────────────────────────
# メンション抑止（@通知を飛ばさない）
# ─────────────────────────────
ALLOWED_NONE = discord.AllowedMentions(
    everyone=False, roles=False, users=False, replied_user=False
)

# ─────────────────────────────
# Botクラス
# ─────────────────────────────
class YadoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.pool: Optional[asyncpg.Pool] = None
        # ギルドごとの XP 参照チャンネル（シンプル：メモリ保持）
        self.xp_channels: dict[int, int] = {}

    async def setup_hook(self):
        # DB接続とテーブル作成（自己紹介設定用）
        if not DATABASE_URL:
            log.warning("DATABASE_URL が未設定です。自己紹介系の一部コマンドは失敗します。")
        else:
            self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id BIGINT PRIMARY KEY,
                        intro_channel_id BIGINT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
        # スラッシュコマンド同期
        await self.tree.sync()

client = YadoBot()

# ─────────────────────────────
# スラッシュコマンド（/hlt グループ）
# ─────────────────────────────
hlt = app_commands.Group(name="hlt", description="ヘルパーコマンド集")
client.tree.add_command(hlt)

def _is_admin_or_manager(interaction: discord.Interaction) -> bool:
    perms = interaction.user.guild_permissions
    return perms.administrator or perms.manage_guild

def admin_only():
    def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        return _is_admin_or_manager(interaction)
    return app_commands.check(predicate)

# ─────────────────────────────
# 便利関数（自己紹介設定用）
# ─────────────────────────────
async def set_intro_channel(guild_id: int, channel_id: int):
    assert client.pool is not None
    async with client.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO guild_settings (guild_id, intro_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE
              SET intro_channel_id = EXCLUDED.intro_channel_id,
                  updated_at = NOW();
        """, guild_id, channel_id)

async def get_intro_channel_id(guild_id: int) -> Optional[int]:
    if client.pool is None:
        return None
    async with client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT intro_channel_id FROM guild_settings WHERE guild_id = $1",
            guild_id
        )
    return int(row["intro_channel_id"]) if row else None

def looks_like_intro_name(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(key in n for key in [
        "自己紹介", "introduc", "intro", "self-intro", "自己紹介部屋", "はじめまして", "自己紹介チャンネル"
    ])

async def find_latest_intro_message(
    channel: discord.TextChannel,
    user_id: int,
    search_limit: int = 800
) -> Optional[discord.Message]:
    async for msg in channel.history(limit=search_limit, oldest_first=False):
        if msg.author.id == user_id:
            return msg
    return None

# ─────────────────────────────
# /hlt set-intro（管理者）
# ─────────────────────────────
@hlt.command(name="set-intro", description="このサーバーの自己紹介チャンネルを登録します（管理者のみ）")
@app_commands.describe(channel="自己紹介用のテキストチャンネル")
@app_commands.default_permissions(manage_guild=True)
@admin_only()
async def hlt_set_intro(interaction: discord.Interaction, channel: discord.TextChannel):
    if client.pool is None:
        return await interaction.response.send_message(
            "設定エラー：DATABASE_URL が未設定です。", ephemeral=True
        )
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)
    await set_intro_channel(interaction.guild.id, channel.id)
    await interaction.followup.send(f"自己紹介チャンネルを {channel.mention} に設定しました。", ephemeral=True)

# ─────────────────────────────
# /hlt auto（管理者）
# ─────────────────────────────
@hlt.command(name="auto", description="自己紹介チャンネルを自動検出して登録します（管理者のみ）")
@app_commands.default_permissions(manage_guild=True)
@admin_only()
async def hlt_auto(interaction: discord.Interaction):
    if client.pool is None:
        return await interaction.response.send_message(
            "設定エラー：DATABASE_URL が未設定です。", ephemeral=True
        )
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)

    candidates: List[discord.TextChannel] = []
    for ch in interaction.guild.text_channels:
        try:
            if looks_like_intro_name(ch.name):
                candidates.append(ch)
        except Exception:
            continue

    if not candidates:
        return await interaction.followup.send(
            "自己紹介っぽいチャンネル名が見つかりませんでした。`/hlt set-intro` で手動登録してください。",
            ephemeral=True
        )

    chosen = sorted(candidates, key=lambda c: c.position)[0]
    await set_intro_channel(interaction.guild.id, chosen.id)
    await interaction.followup.send(f"自己紹介チャンネルを自動検出：{chosen.mention} に設定しました。", ephemeral=True)

# ─────────────────────────────
# /hlt config
# ─────────────────────────────
@hlt.command(name="config", description="このサーバーの自己紹介チャンネル設定を表示します。")
async def hlt_config(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    ch_id = await get_intro_channel_id(interaction.guild.id)
    if ch_id is None:
        return await interaction.response.send_message(
            "自己紹介チャンネルは未設定です。管理者に `/hlt set-intro #チャンネル` を依頼してください。",
            ephemeral=True
        )
    channel = interaction.guild.get_channel(ch_id)
    mention = channel.mention if isinstance(channel, discord.TextChannel) else f"<#{ch_id}>"
    await interaction.response.send_message(f"現在の自己紹介チャンネル：{mention}", ephemeral=True)

# ─────────────────────────────
# /hlt intro
# ─────────────────────────────
@hlt.command(name="intro", description="指定ユーザーの最新の自己紹介を呼び出します。")
@app_commands.describe(user="自己紹介を取り出したいユーザー")
async def hlt_intro(interaction: discord.Interaction, user: discord.User):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)

    ch_id = await get_intro_channel_id(interaction.guild.id)
    if ch_id is None:
        return await interaction.response.send_message(
            "このサーバーでは自己紹介チャンネルが未設定です。管理者に `/hlt set-intro #チャンネル` を依頼してください。",
            ephemeral=True
        )

    await interaction.response.defer(thinking=True)

    intro_ch: Optional[discord.TextChannel] = interaction.client.get_channel(ch_id)
    if intro_ch is None:
        try:
            intro_ch = await interaction.client.fetch_channel(ch_id)
        except Exception:
            return await interaction.followup.send(
                "自己紹介チャンネルを取得できませんでした。Botに閲覧権限があるか確認してください。",
                ephemeral=True
            )
    if not isinstance(intro_ch, discord.TextChannel):
        return await interaction.followup.send("設定されたチャンネルがテキストチャンネルではありません。", ephemeral=True)

    target_msg = await find_latest_intro_message(intro_ch, user.id, search_limit=800)

    if target_msg is None:
        return await interaction.followup.send(
            f"{user.mention} の自己紹介投稿は見つかりませんでした（直近800件）。",
            allowed_mentions=ALLOWED_NONE,
            ephemeral=True
        )

    created = discord.utils.format_dt(target_msg.created_at, style='F')
    header = f"**{user.mention} の自己紹介（{created}）**\n"
    body = target_msg.content or "*（本文なし・Message Content Intentを有効にしていない可能性）*"
    footer = f"\n\n[元メッセージへ]({target_msg.jump_url})"

    files = []
    try:
        for a in target_msg.attachments[:5]:
            if a.size and a.size > 8 * 1024 * 1024:
                footer += f"\n添付（大容量）: {a.url}"
            else:
                files.append(await a.to_file())
    except Exception as e:
        log.warning("Attachment reupload failed: %s", e)

    await interaction.followup.send(
        header + body + footer,
        files=files,
        allowed_mentions=ALLOWED_NONE
    )

# ─────────────────────────────
# /hlt set-xp（参照チャンネルを指定：シンプル版）
# ─────────────────────────────
@hlt.command(name="set-xp", description="XP募集の参照チャンネルを設定します（管理者のみ）")
@app_commands.describe(channel="XP募集のテキストチャンネル")
@app_commands.default_permissions(manage_guild=True)
@admin_only()
async def hlt_set_xp(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    client.xp_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(
        f"XP参照チャンネルを {channel.mention} に設定しました。",
        ephemeral=True
    )

# ─────────────────────────────
# /hlt xp <名前>（シンプル版：複数行から一致行を引用）
# ─────────────────────────────
@hlt.command(name="xp", description="設定チャンネルから『名前を含む行』を探して引用します。")
@app_commands.describe(name="検索する名前（部分一致）")
async def hlt_xp(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)

    ch_id = client.xp_channels.get(interaction.guild.id)
    if ch_id is None:
        return await interaction.response.send_message(
            "XP参照チャンネルが未設定です。まず `/hlt set-xp #チャンネル` を実行してください。",
            ephemeral=True
        )

    channel = interaction.guild.get_channel(ch_id)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("設定されたチャンネルが見つかりませんでした。", ephemeral=True)

    await interaction.response.defer(thinking=True)

    target_lower = name.lower()
    # 直近500件を新しい順に検索
    async for msg in channel.history(limit=500, oldest_first=False):
        if not msg.content:
            continue
        for line in msg.content.splitlines():
            if target_lower in line.lower():
                await interaction.followup.send(f"引用: {line}", allowed_mentions=ALLOWED_NONE)
                return

    await interaction.followup.send(f"'{name}' を含む行は見つかりませんでした。", allowed_mentions=ALLOWED_NONE)

# ─────────────────────────────
# ==== イベントランキング（ユーザー指定対応） ====
#   /hlt eventrank [@ユーザー]
# ─────────────────────────────
EMOJI_PREV = "◀️"
EMOJI_NEXT = "▶️"
EMOJI_STOP = "⏹️"

async def _build_event_interest_ranking_for_guild(guild: discord.Guild) -> list[tuple[int, int]]:
    counts = collections.Counter()
    try:
        events = await guild.fetch_scheduled_events()
    except discord.Forbidden:
        return []

    for ev in events:
        try:
            async for u in ev.fetch_users(limit=None, with_members=False):
                counts[u.id] += 1
        except discord.Forbidden:
            continue

    ranking = [(uid, c) for uid, c in counts.items() if c > 0]
    ranking.sort(key=lambda x: (-x[1], x[0]))
    return ranking

def _build_eventrank_pages(guild: discord.Guild, ranking: list[tuple[int, int]], page_size: int = 10) -> list[str]:
    if not ranking:
        return [f"**{guild.name}** では、まだ『興味あり』にしたメンバーが見つかりませんでした。"]

    total = len(ranking)
    total_pages = math.ceil(total / page_size)
    pages: list[str] = []

    for i in range(total_pages):
        start = i * page_size
        end = min(start + page_size, total)
        chunk = ranking[start:end]

        header = (
            f"**{guild.name}** の『興味あり』数ランキング（メンバー別）\n"
            f"（このサーバーのイベントで「興味あり」を押した回数・多い順）\n\n"
        )
        lines = []
        for idx, (uid, cnt) in enumerate(chunk, start=start + 1):
            lines.append(f"{idx}. <@{uid}> — **{cnt} 件**")
        footer = f"\nページ {i+1}/{total_pages}｜対象メンバー数: {total}"
        pages.append(header + "\n".join(lines) + footer)
    return pages

@hlt.command(
    name="eventrank",
    description="サーバー内イベントの『興味あり』回数ランキングを表示。または指定ユーザーの件数を表示。"
)
@app_commands.describe(user="対象ユーザー（指定すると件数のみ表示）")
async def hlt_eventrank(interaction: discord.Interaction, user: discord.Member | None = None):
    await interaction.response.defer(thinking=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("このコマンドはサーバー内でのみ使用できます。", ephemeral=True)
        return

    me = guild.me or guild.get_member(interaction.client.user.id)  # type: ignore
    if me is None:
        await interaction.followup.send("内部エラー：Botメンバーを確認できませんでした。", ephemeral=True)
        return

    if not interaction.channel:
        await interaction.followup.send("チャンネルを取得できませんでした。", ephemeral=True)
        return
    ch_perms = interaction.channel.permissions_for(me)  # type: ignore
    if not (ch_perms.send_messages and ch_perms.read_message_history and ch_perms.view_channel):
        await interaction.followup.send("権限不足：Send Messages / Read Message History / View Channel が必要です。", ephemeral=True)
        return

    ranking = await _build_event_interest_ranking_for_guild(guild)

    if user is not None:
        count = next((c for uid, c in ranking if uid == user.id), 0)
        await interaction.followup.send(
            f"{user.display_name} さんがこのサーバーで『興味あり』を押した回数は **{count} 件** です。",
            allowed_mentions=ALLOWED_NONE
        )
        return

    pages = _build_eventrank_pages(guild, ranking, page_size=10)
    page_index = 0

    msg = await interaction.followup.send(pages[page_index], allowed_mentions=ALLOWED_NONE)

    if len(pages) > 1 and ch_perms.add_reactions:
        try:
            await msg.add_reaction(EMOJI_PREV)
            await msg.add_reaction(EMOJI_NEXT)
            await msg.add_reaction(EMOJI_STOP)
        except discord.Forbidden:
            await msg.edit(
                content=pages[page_index] + "\n\n（※Botにリアクション追加権限がないためページ送りは無効です）",
                allowed_mentions=ALLOWED_NONE
            )
            return

        def check(payload: discord.RawReactionActionEvent):
            return (
                payload.message_id == msg.id
                and str(payload.emoji) in {EMOJI_PREV, EMOJI_NEXT, EMOJI_STOP}
                and payload.user_id == interaction.user.id
            )

        while True:
            try:
                payload = await client.wait_for("raw_reaction_add", timeout=120.0, check=check)
            except asyncio.TimeoutError:
                try:
                    await msg.clear_reactions()
                except discord.Forbidden:
                    pass
                break

            emoji = str(payload.emoji)
            try:
                await msg.remove_reaction(emoji, discord.Object(id=payload.user_id))
            except discord.Forbidden:
                pass

            if emoji == EMOJI_STOP:
                try:
                    await msg.clear_reactions()
                except discord.Forbidden:
                    pass
                break
            elif emoji == EMOJI_PREV:
                page_index = (page_index - 1) % len(pages)
                await msg.edit(content=pages[page_index], allowed_mentions=ALLOWED_NONE)
            elif emoji == EMOJI_NEXT:
                page_index = (page_index + 1) % len(pages)
                await msg.edit(content=pages[page_index], allowed_mentions=ALLOWED_NONE)

# ─────────────────────────────
# 音楽再生：/hlt m … サブグループ
#   依存: pip install -U "discord.py[voice]" yt-dlp
#   FFmpeg がシステムにインストールされていること
# ─────────────────────────────
YDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "nocheckcertificate": True,
    "noplaylist": True,
    "default_search": "ytsearch",
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

class GuildPlayer:
    def __init__(self, guild: discord.Guild):
        self.guild = guild
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.play_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def is_connected(self) -> bool:
        return self.guild.voice_client is not None and self.guild.voice_client.is_connected()

    async def ensure_joined(self, interaction: discord.Interaction):
        vc = self.guild.voice_client
        if vc and vc.is_connected():
            return vc
        # 呼び出しユーザーのいるVCへ接続
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            raise RuntimeError("先にボイスチャンネルに参加してください。")
        return await interaction.user.voice.channel.connect()

    async def enqueue(self, url: str):
        await self.queue.put(url)

    async def stop(self):
        self._stop.set()
        vc = self.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        # キュー消去
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception:
                break
        self._stop.clear()

    async def player_loop(self, interaction: discord.Interaction):
        try:
            vc = await self.ensure_joined(interaction)
        except Exception as e:
            await interaction.followup.send(f"参加できませんでした：{e}", ephemeral=True)
            return

        ydl = yt_dlp.YoutubeDL(YDL_OPTS)
        while not self._stop.is_set():
            try:
                url = await asyncio.wait_for(self.queue.get(), timeout=300)  # 5分無音で終了
            except asyncio.TimeoutError:
                break
            try:
                info = ydl.extract_info(url, download=False)
                if "entries" in info:  # 検索語の場合
                    info = info["entries"][0]
                stream_url = info["url"]
                title = info.get("title", url)
                source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTS)
                vc.play(source)
                await interaction.followup.send(f"▶️ 再生開始：{title}")
                # 再生完了を待機
                while vc.is_playing():
                    await asyncio.sleep(0.5)
            except Exception as e:
                await interaction.followup.send(f"再生エラー：{e}")
                continue

# ギルド別プレイヤー保持
players: dict[int, GuildPlayer] = {}
def get_player(guild: discord.Guild) -> GuildPlayer:
    if guild.id not in players:
        players[guild.id] = GuildPlayer(guild)
    return players[guild.id]

# /hlt の下に m サブグループ
music = app_commands.Group(name="m", description="音楽コマンド")
hlt.add_command(music)

@music.command(name="join", description="あなたのボイスチャンネルに参加します。")
async def m_join(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    player = get_player(interaction.guild)
    try:
        await player.ensure_joined(interaction)
        await interaction.followup.send("✅ 参加しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"参加できませんでした：{e}", ephemeral=True)

@music.command(name="play", description="YouTubeのURL（または検索語）を再生キューに追加します。")
@app_commands.describe(url="YouTube URL または 検索語（短め推奨）")
async def m_play(interaction: discord.Interaction, url: str):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    await interaction.response.defer(thinking=True)
    player = get_player(interaction.guild)
    try:
        await player.ensure_joined(interaction)
    except Exception as e:
        return await interaction.followup.send(f"参加できませんでした：{e}", ephemeral=True)

    await player.enqueue(url)
    await interaction.followup.send("➕ キューに追加しました。")
    if not player.play_task or player.play_task.done():
        player.play_task = asyncio.create_task(player.player_loop(interaction))

@music.command(name="skip", description="現在の曲をスキップします。")
async def m_skip(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        return await interaction.response.send_message("ボイスチャンネルに未接続です。", ephemeral=True)
    if vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ スキップしました。", ephemeral=True)
    else:
        await interaction.response.send_message("現在再生していません。", ephemeral=True)

@music.command(name="stop", description="再生を停止し、キューをクリアします。")
async def m_stop(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    player = get_player(interaction.guild)
    await player.stop()
    await interaction.followup.send("⏹️ 停止しました（キュー消去）。", ephemeral=True)

@music.command(name="leave", description="ボイスチャンネルから退出します。")
async def m_leave(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
    vc = interaction.guild.voice_client
    if vc and vc.is_connected():
        await vc.disconnect()
        await interaction.response.send_message("👋 退出しました。", ephemeral=True)
    else:
        await interaction.response.send_message("ボイスチャンネルに未接続です。", ephemeral=True)

# ─────────────────────────────
# /hlt help
# ─────────────────────────────
@hlt.command(name="help", description="コマンドの使い方を表示します。")
async def hlt_help(interaction: discord.Interaction):
    text = (
        "**Yado Bot - ヘルプ**\n"
        "【自己紹介】\n"
        "• `/hlt set-intro #ch`（管理）… 自己紹介チャンネルを登録\n"
        "• `/hlt auto`（管理）… 自動検出して登録\n"
        "• `/hlt config` … 現在の設定を表示\n"
        "• `/hlt intro @user` … 指定ユーザーの最新自己紹介を呼び出し\n\n"
        "【XPシンプル検索】\n"
        "• `/hlt set-xp #ch`（管理）… XP参照チャンネルを登録\n"
        "• `/hlt xp 名前` … 参照チャンネルから『名前を含む行』を引用\n\n"
        "【イベント】\n"
        "• `/hlt eventrank` … 『興味あり』回数ランキング（10位/ページ）\n"
        "• `/hlt eventrank @user` … 指定ユーザーの『興味あり』件数を表示\n\n"
        "【音楽 /hlt m ...】\n"
        "• `/hlt m join` … あなたのVCに参加\n"
        "• `/hlt m play <url or words>` … YouTubeから再生（キュー追加）\n"
        "• `/hlt m skip` … 次の曲へ\n"
        "• `/hlt m stop` … 停止＆キュー消去\n"
        "• `/hlt m leave` … VCから退出\n\n"
        "※ 権限: View Channel / Send Messages / Read Message History / Add Reactions（ランキング） / Connect・Speak（音声）など。\n"
        "※ 音楽機能は `pip install -U \"discord.py[voice]\" yt-dlp` と FFmpeg が必要です。\n"
        "※ メッセージ本文を扱う機能は Developer Portal の **MESSAGE CONTENT INTENT** を ON にしてください。"
    )
    await interaction.response.send_message(text, ephemeral=True)

# ─────────────────────────────
# イベント
# ─────────────────────────────
@client.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", client.user, client.user.id)

@client.event
async def on_guild_join(guild: discord.Guild):
    # 参加直後に自己紹介チャンネルを軽く推測（未設定なら）
    try:
        if await get_intro_channel_id(guild.id):
            return
        candidates = [ch for ch in guild.text_channels if looks_like_intro_name(ch.name)]
        if candidates:
            chosen = sorted(candidates, key=lambda c: c.position)[0]
            if client.pool:
                await set_intro_channel(guild.id, chosen.id)
                log.info("Auto-registered intro channel for guild %s: #%s", guild.id, chosen.name)
    except Exception as e:
        log.warning("on_guild_join auto-set failed for guild %s: %s", guild.id, e)

# ─────────────────────────────
# エントリーポイント
# ─────────────────────────────
def main():
    if not TOKEN:
        raise RuntimeError("環境変数 DISCORD_TOKEN が未設定です。")
    client.run(TOKEN)

if __name__ == "__main__":
    main()

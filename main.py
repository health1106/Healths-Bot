import os
import logging
from typing import Optional, List

import math
import collections
import discord
from discord import app_commands
import asyncpg

# 追加: スケジュール取得・時刻/並列・HTTP
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import aiohttp

# ─────────────────────────────
# 環境変数
# ─────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")         # Discord Botトークン
DATABASE_URL = os.getenv("DATABASE_URL")   # RenderのPostgreSQL接続文字列

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

# ─────────────────────────────
# メンション抑止（@通知を飛ばさない）
# ─────────────────────────────
ALLOWED_NONE = discord.AllowedMentions(
    everyone=False, roles=False, users=False, replied_user=False
)

# ─────────────────────────────
# ここから 追加: スプラ3 スケジュール機能 共通
# ─────────────────────────────
JST = ZoneInfo("Asia/Tokyo")
S3_SCHEDULES_URL = "https://splatoon3.ink/data/schedules.json"
UA = "YadoBot-S3/1.1 (+github.com/yourname)"

async def fetch_json(url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        async with session.get(url) as r:
            r.raise_for_status()
            return await r.json()

def fmt_dt_any(iso_or_none) -> str:
    if not iso_or_none:
        return "?"
    try:
        s = str(iso_or_none).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(JST).strftime("%m/%d %H:%M")
    except Exception:
        return "?"

def safe_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            return default
    return cur if cur is not None else default

# ─────────────────────────────
# GraphQL対応: 1ページ分（=1枠）を作るビルダー（対戦）
# ─────────────────────────────
def build_schedule_page(data: dict, idx: int) -> List[discord.Embed]:
    """
    指定インデックス(idx)の枠で、ナワバリ/バンカラ(OPEN/CHALLENGE)/Xマッチ を
    説明Embed + 画像(各モード1枚: stage1) で返す。
    ※ 1ページのEmbed数 <= 8（Discord制限10以下）
    """
    d = data.get("data") or {}
    embeds: List[discord.Embed] = []
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    # ── レギュラー
    n = safe_get(d, "regularSchedules", "nodes", idx)
    if n:
        setting = safe_get(n, "regularMatchSetting")
        st, en = n.get("startTime"), n.get("endTime")
        s1 = safe_get(setting, "vsStages", 0, "name")
        s2 = safe_get(setting, "vsStages", 1, "name")
        rule = safe_get(setting, "vsRule", "name") or "Turf War"
        desc = f"{fmt_dt_any(st)}–{fmt_dt_any(en)}｜{rule}\n{s1} / {s2}\n（{now} 現在）"
        info = discord.Embed(title="🏷 ナワバリ", description=desc, color=0x00AEEF)
        embeds.append(info)
        img1 = safe_get(setting, "vsStages", 0, "image", "url")
        if img1: embeds.append(discord.Embed(color=0x00AEEF).set_image(url=img1))

    # ── バンカラ（OPEN/CHALLENGE）
    n = safe_get(d, "bankaraSchedules", "nodes", idx)
    if n:
        settings = safe_get(n, "bankaraMatchSettings") or []
        st, en = n.get("startTime"), n.get("endTime")
        for mode_label in ("OPEN", "CHALLENGE"):
            setting = next((s for s in settings if s.get("bankaraMode") == mode_label), None)
            if not setting: continue
            s1 = safe_get(setting, "vsStages", 0, "name")
            s2 = safe_get(setting, "vsStages", 1, "name")
            rule = safe_get(setting, "vsRule", "name") or "?"
            title = "🏷 バンカラ(オープン)" if mode_label == "OPEN" else "🏷 バンカラ(チャレンジ)"
            desc = f"{fmt_dt_any(st)}–{fmt_dt_any(en)}｜{rule}\n{s1} / {s2}\n（{now} 現在）"
            info = discord.Embed(title=title, description=desc, color=0x00AEEF)
            embeds.append(info)
            img1 = safe_get(setting, "vsStages", 0, "image", "url")
            if img1: embeds.append(discord.Embed(color=0x00AEEF).set_image(url=img1))

    # ── Xマッチ
    n = safe_get(d, "xSchedules", "nodes", idx)
    if n:
        setting = safe_get(n, "xMatchSetting")
        st, en = n.get("startTime"), n.get("endTime")
        s1 = safe_get(setting, "vsStages", 0, "name")
        s2 = safe_get(setting, "vsStages", 1, "name")
        rule = safe_get(setting, "vsRule", "name") or "?"
        desc = f"{fmt_dt_any(st)}–{fmt_dt_any(en)}｜{rule}\n{s1} / {s2}\n（{now} 現在）"
        info = discord.Embed(title="🏷 Xマッチ", description=desc, color=0x00AEEF)
        embeds.append(info)
        img1 = safe_get(setting, "vsStages", 0, "image", "url")
        if img1: embeds.append(discord.Embed(color=0x00AEEF).set_image(url=img1))

    # ページ見出し
    if embeds:
        page_title = discord.Embed(
            title=f"🗓 対戦スケジュール ページ {idx+1}（現在を1とした {idx} つ先まで）",
            description="※ 画像は各モード1枚（stage1）。両ステージ名は説明に記載。",
            color=0x0067C0
        )
        embeds.insert(0, page_title)
    return embeds

# ─────────────────────────────
# GraphQL対応: 1ページ分（=1枠）を作るビルダー（サーモン）
# ─────────────────────────────
def build_salmon_page(data: dict, idx: int) -> List[discord.Embed]:
    """
    指定インデックス(idx)の枠で、通常/ビッグラン/限定 を
    説明Embed + 画像(1枚)で返す。
    """
    d = data.get("data") or {}
    embeds: List[discord.Embed] = []
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    def add_stream(label: str, *path):
        n = safe_get(d, "coopGroupingSchedule", *path, "nodes", idx)
        if not n: return
        st, en = n.get("startTime"), n.get("endTime")
        setting = n.get("setting") or {}
        stage = safe_get(setting, "coopStage", "name") or "?"
        weps = setting.get("weapons") or []
        wnames = [safe_get(w, "name") for w in weps if safe_get(w, "name")]
        desc = f"{fmt_dt_any(st)}–{fmt_dt_any(en)}｜{stage}\n" + (" / ".join(wnames) if wnames else "（支給ブキ情報なし）") + f"\n（{now} 現在）"
        info = discord.Embed(title=label, description=desc, color=0xF49A1A)
        embeds.append(info)
        img = safe_get(setting, "coopStage", "image", "url")
        if img:
            embeds.append(discord.Embed(color=0xF49A1A).set_image(url=img))

    add_stream("🧰 サーモンラン（通常）", "regularSchedules")
    add_stream("🌊 ビッグラン", "bigRunSchedules")
    add_stream("🎪 期間限定(他)", "limitedSchedules")

    if embeds:
        page_title = discord.Embed(
            title=f"🗓 サーモンラン ページ {idx+1}（現在を1とした {idx} つ先まで）",
            description="※ 画像は各カテゴリ1枚（ステージ画像）。",
            color=0xC46A00
        )
        embeds.insert(0, page_title)
    return embeds

# ─────────────────────────────
# Botクラス
# ─────────────────────────────
class YadoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.pool: Optional[asyncpg.Pool] = None
        # ── 追加：ギルドごとの XP 参照チャンネル（メモリ保持のシンプル実装）
        self.xp_channels: dict[int, int] = {}

    async def setup_hook(self):
        # DB接続とテーブル作成（自己紹介設定用）
        if not DATABASE_URL:
            log.warning("DATABASE_URL が未設定です。DBを使うコマンドは失敗します。")
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
        # グローバルコマンドを同期
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
# 便利関数（自己紹介設定用）…（ここは従来どおり）
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
# /hlt set-intro / auto / config / intro / set-xp / xp / eventrank
# （ここはあなたの前回コードと同じ・省略なしで残しています）
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
    async for msg in channel.history(limit=500, oldest_first=False):
        if not msg.content:
            continue
        for line in msg.content.splitlines():
            if target_lower in line.lower():
                await interaction.followup.send(f"引用: {line}", allowed_mentions=ALLOWED_NONE)
                return

    await interaction.followup.send(f"'{name}' を含む行は見つかりませんでした。", allowed_mentions=ALLOWED_NONE)

# ==== eventrank（既存どおり） ====
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
            return (payload.message_id == msg.id and str(payload.emoji) in {EMOJI_PREV, EMOJI_NEXT, EMOJI_STOP}
                    and payload.user_id == interaction.user.id)
        while True:
            try:
                payload = await client.wait_for("raw_reaction_add", timeout=120.0, check=check)
            except asyncio.TimeoutError:
                try: await msg.clear_reactions()
                except discord.Forbidden: pass
                break
            emoji = str(payload.emoji)
            try: await msg.remove_reaction(emoji, discord.Object(id=payload.user_id))
            except discord.Forbidden: pass
            if emoji == EMOJI_STOP:
                try: await msg.clear_reactions()
                except discord.Forbidden: pass
                break
            elif emoji == EMOJI_PREV:
                page_index = (page_index - 1) % len(pages)
                await msg.edit(content=pages[page_index], allowed_mentions=ALLOWED_NONE)
            elif emoji == EMOJI_NEXT:
                page_index = (page_index + 1) % len(pages)
                await msg.edit(content=pages[page_index], allowed_mentions=ALLOWED_NONE)

# ─────────────────────────────
# 追加: /hlt s3 （スプラ3スケジュール：リアクションページャ）
# ─────────────────────────────
EMOJI_LEFT = "◀️"
EMOJI_RIGHT = "▶️"
EMOJI_CLOSE = "⏹️"

@hlt.command(name="s3", description="Splatoon 3 スケジュール（リアクションでページ送り：現在＋3つ先まで）")
@app_commands.describe(kind="schedule=対戦 / salmon=サーモンラン")
@app_commands.choices(kind=[
    app_commands.Choice(name="schedule（対戦）", value="schedule"),
    app_commands.Choice(name="salmon（サーモン）", value="salmon"),
])
async def hlt_s3(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)

    # 権限チェック（Embed Linksが無いと画像が出ません）
    if interaction.channel and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        me = interaction.guild.me if interaction.guild else None
        if me:
            perms = interaction.channel.permissions_for(me)  # type: ignore
            if not (perms.send_messages and perms.embed_links and perms.add_reactions):
                return await interaction.followup.send("権限不足：Send Messages / Embed Links / Add Reactions が必要です。", ephemeral=True)

    try:
        data = await fetch_json(S3_SCHEDULES_URL)
    except Exception as e:
        log.warning("S3 schedules fetch failed: %s", e)
        return await interaction.followup.send("スケジュール取得に失敗しました。時間をおいて再試行してください。", ephemeral=True)

    # ページ（=枠）を作成：0=現在, 1=次, 2=2つ先, 3=3つ先
    build_page = build_salmon_page if kind.value == "salmon" else build_schedule_page
    pages: List[List[discord.Embed]] = []
    for i in range(4):
        embeds = build_page(data, i)
        if embeds:
            pages.append(embeds)

    if not pages:
        return await interaction.followup.send("表示できるスケジュールが見つかりませんでした。", ephemeral=True)

    # 1メッセージ（複数Embed）でページ送り
    page_index = 0
    msg = await interaction.followup.send(embeds=pages[page_index])

    # リアクション設置
    try:
        await msg.add_reaction(EMOJI_LEFT)
        await msg.add_reaction(EMOJI_RIGHT)
        await msg.add_reaction(EMOJI_CLOSE)
    except discord.Forbidden:
        return  # 権限なし

    # 120秒でタイムアウト＆メッセージ自動削除
    end_at_delete = 120.0

    def check(payload: discord.RawReactionActionEvent):
        return payload.message_id == msg.id and str(payload.emoji) in {EMOJI_LEFT, EMOJI_RIGHT, EMOJI_CLOSE} and payload.user_id == interaction.user.id

    start = datetime.now()
    while True:
        try:
            timeout_left = max(1.0, end_at_delete - (datetime.now() - start).total_seconds())
            payload = await client.wait_for("raw_reaction_add", timeout=timeout_left, check=check)
        except asyncio.TimeoutError:
            break

        emoji = str(payload.emoji)
        try:
            await msg.remove_reaction(emoji, discord.Object(id=payload.user_id))
        except discord.Forbidden:
            pass

        if emoji == EMOJI_CLOSE:
            break
        elif emoji == EMOJI_LEFT:
            page_index = (page_index - 1) % len(pages)
        elif emoji == EMOJI_RIGHT:
            page_index = (page_index + 1) % len(pages)

        # ページ更新
        try:
            await msg.edit(embeds=pages[page_index])
        except discord.HTTPException:
            pass

    # 反応を消してから削除（権限なければ無視）
    try:
        await msg.clear_reactions()
    except discord.Forbidden:
        pass
    try:
        await msg.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

# ─────────────────────────────
# /hlt help
# ─────────────────────────────
@hlt.command(name="help", description="コマンドの使い方を表示します。")
async def hlt_help(interaction: discord.Interaction):
    text = (
        "**Yado Bot - ヘルプ**\n"
        "`/hlt set-intro #チャンネル` …（管理者）自己紹介チャンネルを登録\n"
        "`/hlt auto` …（管理者）自己紹介チャンネルを自動検出して登録\n"
        "`/hlt config` … 現在の設定を表示\n"
        "`/hlt intro @ユーザー` … 登録チャンネルから、指定ユーザーの最新自己紹介を呼び出す\n\n"
        "`/hlt set-xp #チャンネル` …（管理者）XP参照チャンネルを登録（シンプル版）\n"
        "`/hlt xp 名前` … 参照チャンネルから『名前を含む行』を検索して引用\n\n"
        "`/hlt eventrank` … サーバーのイベント『興味あり』回数ランキング（リアクションで操作）\n"
        "`/hlt eventrank @ユーザー` … 指定ユーザーの件数のみ表示\n\n"
        "`/hlt s3 kind:(schedule|salmon)` … スプラ3スケジュール（リアクションでページ送り：現在＋3つ先まで／120秒で自動削除）\n"
        "※ Botには「View Channel」「Read Message History」「Send Messages」「Embed Links」「Add Reactions（推奨）」の権限が必要です。"
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
    try:
        existing = await get_intro_channel_id(guild.id)
        if existing:
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

import os
import io
import logging
from typing import Optional, List, Tuple

import math
import collections
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
import asyncpg
import aiohttp

# 画像合成（左右配置）に使用
from PIL import Image

# ─────────────────────────────
# 環境変数
# ─────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")         # Discord Botトークン
DATABASE_URL = os.getenv("DATABASE_URL")   # PostgreSQL接続文字列（例: Render）

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
intents.message_content = True          # /hlt xp に必要
intents.guild_scheduled_events = True   # /hlt eventrank に必要

# ─────────────────────────────
# メンション抑止
# ─────────────────────────────
ALLOWED_NONE = discord.AllowedMentions(
    everyone=False, roles=False, users=False, replied_user=False
)

# ─────────────────────────────
# 便利：管理者チェック
# ─────────────────────────────
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
# スプラ3 スケジュール 共通（日本語対応）
# ─────────────────────────────
JST = ZoneInfo("Asia/Tokyo")
S3_SCHEDULES_URL = "https://splatoon3.ink/data/schedules.json"
UA = "YadoBot-S3/1.4 (+github.com/yourname)"

async def fetch_json(url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=10)
    headers = {"User-Agent": UA, "Accept-Language": "ja-JP,ja;q=0.9"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
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

# ルール名の英→日フォールバック（保険）
_RULE_EN2JA = {
    "Turf War": "ナワバリバトル",
    "Splat Zones": "ガチエリア",
    "Tower Control": "ガチヤグラ",
    "Rainmaker": "ガチホコバトル",
    "Clam Blitz": "ガチアサリ",
    "Tricolor Turf War": "トリカラバトル",
}
def to_ja_rule(name: str | None) -> str:
    if not name:
        return "?"
    return _RULE_EN2JA.get(name, name)

# ─────────────────────────────
# ステージ画像 合成（左右配置）
# ─────────────────────────────
async def fetch_image_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url) as r:
        r.raise_for_status()
        return await r.read()

async def compose_side_by_side(url1: str, url2: str, total_width: int = 1000, gap: int = 8) -> io.BytesIO:
    """
    2つの画像URLを横に並べて1枚にする。
    アスペクト比を保ちつつ高さを揃える。左右に8pxの隙間。
    """
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": UA}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        b1, b2 = await asyncio.gather(
            fetch_image_bytes(session, url1),
            fetch_image_bytes(session, url2),
        )

    im1, im2 = Image.open(io.BytesIO(b1)).convert("RGBA"), Image.open(io.BytesIO(b2)).convert("RGBA")

    # 目標の片側幅
    target_each_w = (total_width - gap) // 2

    # まず幅を揃えて縮小（縦は比率に従う）
    def resize_to_width(img: Image.Image, w: int) -> Image.Image:
        if img.width == 0: return img
        scale = w / img.width
        h = max(1, int(round(img.height * scale)))
        return img.resize((w, h), Image.LANCZOS)

    im1r = resize_to_width(im1, target_each_w)
    im2r = resize_to_width(im2, target_each_w)

    # 高さを小さい方に合わせて上下トリミング（センター）
    h = min(im1r.height, im2r.height)
    def crop_center_h(img: Image.Image, h_target: int) -> Image.Image:
        if img.height == h_target:
            return img
        top = max(0, (img.height - h_target) // 2)
        return img.crop((0, top, img.width, top + h_target))

    im1c = crop_center_h(im1r, h)
    im2c = crop_center_h(im2r, h)

    canvas = Image.new("RGBA", (target_each_w * 2 + gap, h), (0, 0, 0, 0))
    canvas.paste(im1c, (0, 0))
    canvas.paste(im2c, (target_each_w + gap, 0))

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return out

# ─────────────────────────────
# スケジュールページ（各モード 2枚→横並び1枚に合成）
# 返り値: (embeds, files)
# ─────────────────────────────
async def build_schedule_page_with_images(data: dict, idx: int) -> Tuple[List[discord.Embed], List[discord.File]]:
    d = data.get("data") or {}
    embeds: List[discord.Embed] = []
    files: List[discord.File] = []
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    async def add_mode(title: str, st, en, stage1_name, stage2_name, rule_name, img1_url, img2_url, file_tag: str, color: int):
        desc = f"{fmt_dt_any(st)}–{fmt_dt_any(en)}｜{to_ja_rule(rule_name)}\n{stage1_name} / {stage2_name}\n（{now} 現在）"
        em = discord.Embed(title=title, description=desc, color=color)
        if img1_url and img2_url:
            composed = await compose_side_by_side(img1_url, img2_url)
            fname = f"{file_tag}_{idx}.png"
            f = discord.File(composed, filename=fname)
            files.append(f)
            em.set_image(url=f"attachment://{fname}")
        embeds.append(em)

    # レギュラー
    n = safe_get(d, "regularSchedules", "nodes", idx)
    if n:
        setting = safe_get(n, "regularMatchSetting")
        st, en = n.get("startTime"), n.get("endTime")
        s1n = safe_get(setting, "vsStages", 0, "name")
        s2n = safe_get(setting, "vsStages", 1, "name")
        s1u = safe_get(setting, "vsStages", 0, "image", "url")
        s2u = safe_get(setting, "vsStages", 1, "image", "url")
        rule = safe_get(setting, "vsRule", "name")
        await add_mode("🏷 ナワバリ", st, en, s1n, s2n, rule, s1u, s2u, "regular", 0x00AEEF)

    # バンカラ OPEN/CHALLENGE
    n = safe_get(d, "bankaraSchedules", "nodes", idx)
    if n:
        settings = safe_get(n, "bankaraMatchSettings") or []
        st, en = n.get("startTime"), n.get("endTime")

        for mode_label in ("OPEN", "CHALLENGE"):
            setting = next((s for s in settings if s.get("bankaraMode") == mode_label), None)
            if not setting:
                continue
            s1n = safe_get(setting, "vsStages", 0, "name")
            s2n = safe_get(setting, "vsStages", 1, "name")
            s1u = safe_get(setting, "vsStages", 0, "image", "url")
            s2u = safe_get(setting, "vsStages", 1, "image", "url")
            rule = safe_get(setting, "vsRule", "name")
            title = "🏷 バンカラ(オープン)" if mode_label == "OPEN" else "🏷 バンカラ(チャレンジ)"
            tag = "bankara_open" if mode_label == "OPEN" else "bankara_challenge"
            await add_mode(title, st, en, s1n, s2n, rule, s1u, s2u, tag, 0x00AEEF)

    # Xマッチ
    n = safe_get(d, "xSchedules", "nodes", idx)
    if n:
        setting = safe_get(n, "xMatchSetting")
        st, en = n.get("startTime"), n.get("endTime")
        s1n = safe_get(setting, "vsStages", 0, "name")
        s2n = safe_get(setting, "vsStages", 1, "name")
        s1u = safe_get(setting, "vsStages", 0, "image", "url")
        s2u = safe_get(setting, "vsStages", 1, "image", "url")
        rule = safe_get(setting, "vsRule", "name")
        await add_mode("🏷 Xマッチ", st, en, s1n, s2n, rule, s1u, s2u, "xmatch", 0x00AEEF)

    if embeds:
        head = discord.Embed(
            title=f"🗓 対戦スケジュール（ページ {idx+1}：現在を1として {idx} つ先）",
            description="※ 各モード：2ステージ画像を左右に合成して表示",
            color=0x0067C0
        )
        embeds.insert(0, head)

    return embeds, files

# サーモンはステージが1つのため、従来どおり（必要なら合成拡張可）
def build_salmon_page(data: dict, idx: int) -> List[discord.Embed]:
    d = data.get("data") or {}
    embeds: List[discord.Embed] = []
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    def add_stream(label: str, *path):
        n = safe_get(d, "coopGroupingSchedule", *path, "nodes", idx)
        if not n:
            return
        st, en = n.get("startTime"), n.get("endTime")
        setting = n.get("setting") or {}
        stage = safe_get(setting, "coopStage", "name") or "?"
        weps = setting.get("weapons") or []
        wnames = [safe_get(w, "name") for w in weps if safe_get(w, "name")]
        desc = f"{fmt_dt_any(st)}–{fmt_dt_any(en)}｜{stage}\n" + (" / ".join(wnames) if wnames else "（支給ブキ情報なし）") + f"\n（{now} 現在）"
        embeds.append(discord.Embed(title=label, description=desc, color=0xF49A1A))

    add_stream("🧰 サーモンラン（通常）", "regularSchedules")
    add_stream("🌊 ビッグラン", "bigRunSchedules")
    add_stream("🎪 期間限定(他)", "limitedSchedules")

    if embeds:
        head = discord.Embed(
            title=f"🗓 サーモンラン（ページ {idx+1}：現在を1として {idx} つ先）",
            color=0xC46A00
        )
        embeds.insert(0, head)

    return embeds

# ─────────────────────────────
# Botクラス
# ─────────────────────────────
class YadoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.pool: Optional[asyncpg.Pool] = None
        # ギルドごとの XP 参照チャンネル（メモリ保持）
        self.xp_channels: dict[int, int] = {}

    async def setup_hook(self):
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
        await self.tree.sync()

client = YadoBot()

# ─────────────────────────────
# /hlt グループ
# ─────────────────────────────
hlt = app_commands.Group(name="hlt", description="ヘルパーコマンド集")
client.tree.add_command(hlt)

# ─────────────────────────────
# 自己紹介設定（DB）
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
        return await interaction.response.send_message("設定エラー：DATABASE_URL が未設定です。", ephemeral=True)
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
        return await interaction.response.send_message("設定エラー：DATABASE_URL が未設定です。", ephemeral=True)
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
# /hlt intro（指定ユーザーの自己紹介を呼び出し）
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
# XP 参照（メモリ保持シンプル版）
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

# ─────────────────────────────
# イベントランキング（興味あり）
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
# スプラ3：/hlt s3（画像左右合成＆ページ送り：現在＋3つ先）
# ─────────────────────────────
EMOJI_LEFT = "◀️"
EMOJI_RIGHT = "▶️"
EMOJI_CLOSE = "⏹️"

@hlt.command(name="s3", description="Splatoon 3 スケジュール（各モード2ステージ画像を左右合成／リアクションでページ送り）")
@app_commands.describe(kind="schedule=対戦 / salmon=サーモンラン")
@app_commands.choices(kind=[
    app_commands.Choice(name="schedule（対戦）", value="schedule"),
    app_commands.Choice(name="salmon（サーモン）", value="salmon"),
])
async def hlt_s3(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)

    # 権限チェック
    if interaction.channel and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        me = interaction.guild.me if interaction.guild else None
        if me:
            perms = interaction.channel.permissions_for(me)  # type: ignore
            if not (perms.send_messages and perms.embed_links and perms.add_reactions and perms.attach_files):
                return await interaction.followup.send("権限不足：Send Messages / Embed Links / Add Reactions / Attach Files が必要です。", ephemeral=True)

    try:
        data = await fetch_json(S3_SCHEDULES_URL)
    except Exception as e:
        log.warning("S3 schedules fetch failed: %s", e)
        return await interaction.followup.send("スケジュール取得に失敗しました。時間をおいて再試行してください。", ephemeral=True)

    pages: List[Tuple[List[discord.Embed], List[discord.File]]] = []
    if kind.value == "salmon":
        # サーモンは画像合成なし
        for i in range(4):
            embeds_only = build_salmon_page(data, i)
            if embeds_only:
                pages.append((embeds_only, []))
    else:
        # 対戦：各ページごとに合成画像を準備
        for i in range(4):
            embeds, files = await build_schedule_page_with_images(data, i)
            if embeds:
                pages.append((embeds, files))

    if not pages:
        return await interaction.followup.send("表示できるスケジュールが見つかりませんでした。", ephemeral=True)

    page_index = 0

    # 初回メッセージ送信
    embeds, files = pages[page_index]
    msg = await interaction.followup.send(embeds=embeds, files=files)

    async def add_nav_reactions(m: discord.Message):
        try:
            await m.add_reaction(EMOJI_LEFT)
            await m.add_reaction(EMOJI_RIGHT)
            await m.add_reaction(EMOJI_CLOSE)
        except discord.Forbidden:
            pass

    await add_nav_reactions(msg)

    # 120秒でタイムアウト＆最後にメッセージ削除
    end_at_delete = 120.0
    start = datetime.now()

    def check(payload: discord.RawReactionActionEvent):
        return payload.message_id == msg.id and str(payload.emoji) in {EMOJI_LEFT, EMOJI_RIGHT, EMOJI_CLOSE} and payload.user_id == interaction.user.id

    while True:
        try:
            timeout_left = max(1.0, end_at_delete - (datetime.now() - start).total_seconds())
            payload = await client.wait_for("raw_reaction_add", timeout=timeout_left, check=check)
        except asyncio.TimeoutError:
            break

        emoji = str(payload.emoji)

        # リアクションはなるべく消しておく（権限無い場合は無視）
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

        # 画像（添付ファイル）を差し替える必要があるため、メッセージ再送 → 旧メッセージ削除
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        embeds, files = pages[page_index]
        msg = await interaction.channel.send(embeds=embeds, files=files)  # type: ignore
        await add_nav_reactions(msg)

    # 終了時にメッセージ削除（権限なければ無視）
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
        "`/hlt eventrank` … このサーバーのイベントで『興味あり』回数のランキング（10位/ページ、リアクションで操作）\n"
        "`/hlt eventrank @ユーザー` … 指定ユーザーが『興味あり』を押した回数（数値のみ）を表示\n\n"
        "`/hlt s3 kind:(schedule|salmon)` … スプラ3スケジュール（各モード2ステージ画像を左右合成／リアクションでページ送り：現在＋3つ先まで／120秒で自動削除）\n"
        "※ Botには「View Channel」「Read Message History」「Send Messages」「Embed Links」「Attach Files」「Add Reactions（推奨）」の権限が必要です。\n"
        "※ /hlt xp は Developer Portal の **MESSAGE CONTENT INTENT** をONにしておく必要があります。\n"
        "※ 画像合成には `pillow` が必要です： `pip install pillow`"
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

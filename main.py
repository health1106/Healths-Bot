# yado_bot.py
import os
import io
import logging
from typing import Optional, List, Tuple

import collections
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
import asyncpg
import aiohttp
from PIL import Image

# ─────────────────────────────
# 環境変数
# ─────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

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
intents.message_content = True
intents.guild_scheduled_events = True

# ─────────────────────────────
# メンション抑止
# ─────────────────────────────
ALLOWED_NONE = discord.AllowedMentions(
    everyone=False, roles=False, users=False, replied_user=False
)

# ─────────────────────────────
# 共通便利関数
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
# スプラ3スケジュール 共通
# ─────────────────────────────
JST = ZoneInfo("Asia/Tokyo")
S3_SCHEDULES_URL = "https://splatoon3.ink/data/schedules.json"
UA = "YadoBot-S3/2.0 (+github.com/yourname)"

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

# ルール名フォールバック
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
# ステージ日本語辞書
# ─────────────────────────────
STAGE_JA: dict[str, str] = {}

async def load_stage_locale():
    global STAGE_JA
    url = "https://splatoon3.ink/data/locale/ja-JP.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                r.raise_for_status()
                data = await r.json()
                STAGE_JA = {k: v["name"] for k, v in data.get("data", {}).get("vsStages", {}).items()}
                log.info("日本語ステージ辞書ロード成功: %d件", len(STAGE_JA))
    except Exception as e:
        log.warning("日本語ステージ辞書ロード失敗: %s", e)

def stage_name_ja(stage: dict) -> str:
    if not stage:
        return "?"
    sid = stage.get("id")
    if sid and sid in STAGE_JA:
        return STAGE_JA[sid]
    return stage.get("name") or "?"

# ─────────────────────────────
# ステージ画像合成
# ─────────────────────────────
async def fetch_image_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url) as r:
        r.raise_for_status()
        return await r.read()

async def compose_side_by_side(url1: str, url2: str, total_width: int = 1000, gap: int = 8) -> io.BytesIO:
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": UA}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        b1, b2 = await asyncio.gather(
            fetch_image_bytes(session, url1),
            fetch_image_bytes(session, url2),
        )
    im1, im2 = Image.open(io.BytesIO(b1)).convert("RGBA"), Image.open(io.BytesIO(b2)).convert("RGBA")
    target_each_w = (total_width - gap) // 2

    def resize_to_width(img: Image.Image, w: int) -> Image.Image:
        scale = w / img.width
        h = max(1, int(round(img.height * scale)))
        return img.resize((w, h), Image.LANCZOS)

    im1r = resize_to_width(im1, target_each_w)
    im2r = resize_to_width(im2, target_each_w)
    h = min(im1r.height, im2r.height)

    def crop_center_h(img: Image.Image, h_target: int) -> Image.Image:
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
# Botクラス
# ─────────────────────────────
class YadoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.pool: Optional[asyncpg.Pool] = None
        self.xp_channels: dict[int, int] = {}

    async def setup_hook(self):
        # DBはオプション
        if DATABASE_URL:
            try:
                self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS guild_settings (
                            guild_id BIGINT PRIMARY KEY,
                            intro_channel_id BIGINT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        );
                    """)
            except Exception:
                log.exception("PostgreSQL初期化に失敗しました（DBなしでも動作を継続します）")
                self.pool = None

        await load_stage_locale()
        # グローバル同期（開発中は guild 指定に切り替えてOK）
        try:
            await self.tree.sync()
            log.info("App commands synced")
        except Exception:
            log.exception("App commands sync failed")

client = YadoBot()

# ─────────────────────────────
# /hlt グループ定義
# ─────────────────────────────
hlt = app_commands.Group(name="hlt", description="ヘルパーコマンド集")
client.tree.add_command(hlt)

# かんたんヘルプ
@hlt.command(name="help", description="コマンド一覧を表示")
async def hlt_help(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**/hlt help** … このヘルプ\n"
        "**/hlt set-intro <#ch>** … 自己紹介チャンネル設定（管理者）\n"
        "**/hlt auto** … 自己紹介チャンネル自動検出（管理者）\n"
        "**/hlt config** … 自己紹介設定表示\n"
        "**/hlt intro <@user>** … 最新の自己紹介を引用\n"
        "**/hlt set-xp <#ch>** … XP参照チャンネル設定（管理者）\n"
        "**/hlt xp <名前>** … XP参照チャンネルから行を引用\n"
        "**/hlt eventrank** … イベント『興味あり』ランキング\n"
        "**/hlt s3** … Splatoon3スケジュール（2枚横並び画像）\n",
        ephemeral=True
    )

# ─────────────────────────────
# 自己紹介関連
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
    n = name.lower()
    return any(key in n for key in ["自己紹介", "introduc", "intro", "self-intro"])

async def find_latest_intro_message(channel: discord.TextChannel, user_id: int, search_limit: int = 800) -> Optional[discord.Message]:
    async for msg in channel.history(limit=search_limit, oldest_first=False):
        if msg.author.id == user_id:
            return msg
    return None

@hlt.command(name="set-intro", description="自己紹介チャンネルを登録（管理者のみ）")
@admin_only()
async def hlt_set_intro(interaction: discord.Interaction, channel: discord.TextChannel):
    if not client.pool:
        return await interaction.response.send_message("DB未設定です。DATABASE_URL を設定してください。", ephemeral=True)
    await set_intro_channel(interaction.guild.id, channel.id)
    await interaction.response.send_message(f"自己紹介チャンネルを {channel.mention} に設定しました。", ephemeral=True)

@hlt.command(name="auto", description="自己紹介チャンネルを自動検出（管理者のみ）")
@admin_only()
async def hlt_auto(interaction: discord.Interaction):
    if not client.pool:
        return await interaction.response.send_message("DB未設定です。DATABASE_URL を設定してください。", ephemeral=True)
    candidates = [ch for ch in interaction.guild.text_channels if looks_like_intro_name(ch.name)]
    if not candidates:
        return await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
    chosen = sorted(candidates, key=lambda c: c.position)[0]
    await set_intro_channel(interaction.guild.id, chosen.id)
    await interaction.response.send_message(f"自己紹介チャンネルを {chosen.mention} に設定しました。", ephemeral=True)

@hlt.command(name="config", description="自己紹介チャンネル設定を表示")
async def hlt_config(interaction: discord.Interaction):
    ch_id = await get_intro_channel_id(interaction.guild.id)
    if not ch_id:
        return await interaction.response.send_message("未設定です。", ephemeral=True)
    channel = interaction.guild.get_channel(ch_id)
    if channel is None:
        return await interaction.response.send_message("保存されているチャンネルが見つかりません。再設定してください。", ephemeral=True)
    await interaction.response.send_message(f"現在の自己紹介チャンネル：{channel.mention}", ephemeral=True)

@hlt.command(name="intro", description="指定ユーザーの最新自己紹介を表示")
async def hlt_intro(interaction: discord.Interaction, user: discord.User):
    ch_id = await get_intro_channel_id(interaction.guild.id)
    if not ch_id:
        return await interaction.response.send_message("未設定です。", ephemeral=True)
    intro_ch: Optional[discord.TextChannel] = interaction.guild.get_channel(ch_id)
    if intro_ch is None:
        return await interaction.response.send_message("自己紹介チャンネルが見つかりません。再設定してください。", ephemeral=True)
    msg = await find_latest_intro_message(intro_ch, user.id)
    if not msg:
        return await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
    await interaction.response.send_message(f"**{user.display_name} の自己紹介**\n{msg.content}", allowed_mentions=ALLOWED_NONE)

# ─────────────────────────────
# XP関連
# ─────────────────────────────
@hlt.command(name="set-xp", description="XP参照チャンネルを設定（管理者のみ）")
@admin_only()
async def hlt_set_xp(interaction: discord.Interaction, channel: discord.TextChannel):
    client.xp_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(f"XP参照チャンネルを {channel.mention} に設定しました。", ephemeral=True)

@hlt.command(name="xp", description="XP参照チャンネルから名前を検索して引用")
async def hlt_xp(interaction: discord.Interaction, name: str):
    ch_id = client.xp_channels.get(interaction.guild.id)
    if not ch_id:
        return await interaction.response.send_message("XP参照チャンネルが未設定です。", ephemeral=True)
    channel = interaction.guild.get_channel(ch_id)
    if channel is None:
        return await interaction.response.send_message("XP参照チャンネルが見つかりません。再設定してください。", ephemeral=True)

    # 長くなる可能性があるので defer
    await interaction.response.defer(ephemeral=True)
    async for msg in channel.history(limit=500):
        for line in msg.content.splitlines():
            if name.lower() in line.lower():
                return await interaction.followup.send(f"引用: {line}", allowed_mentions=ALLOWED_NONE)
    await interaction.followup.send("見つかりませんでした。", ephemeral=True)

# ─────────────────────────────
# イベントランキング
# ─────────────────────────────
async def _build_event_interest_ranking_for_guild(guild: discord.Guild) -> list[tuple[int, int]]:
    counts = collections.Counter()
    events = await guild.fetch_scheduled_events()
    for ev in events:
        async for u in ev.fetch_users(limit=None, with_members=False):
            counts[u.id] += 1
    ranking = [(uid, c) for uid, c in counts.items() if c > 0]
    ranking.sort(key=lambda x: (-x[1], x[0]))
    return ranking

def _build_eventrank_pages(guild: discord.Guild, ranking: list[tuple[int, int]], page_size: int = 10) -> list[str]:
    if not ranking:
        return [f"{guild.name} ではまだ『興味あり』がありません。"]
    pages = []
    for i in range(0, len(ranking), page_size):
        chunk = ranking[i:i+page_size]
        lines = [f"{idx+1}. <@{uid}> — {cnt}件" for idx,(uid,cnt) in enumerate(chunk, start=i)]
        pages.append("\n".join(lines))
    return pages

@hlt.command(name="eventrank", description="イベント『興味あり』ランキング")
async def hlt_eventrank(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
        ranking = await _build_event_interest_ranking_for_guild(interaction.guild)
        pages = _build_eventrank_pages(interaction.guild, ranking)
        await interaction.followup.send(pages[0], allowed_mentions=ALLOWED_NONE)
    except Exception:
        log.exception("/hlt eventrank でエラー")
        await interaction.followup.send("取得中にエラーが発生しました。後でもう一度お試しください。", ephemeral=True)

# ─────────────────────────────
# スプラ3スケジュール表示
# ─────────────────────────────
@hlt.command(name="s3", description="Splatoon3の最新スケジュールを表示")
async def hlt_s3(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
        data = await fetch_json(S3_SCHEDULES_URL)

        bankara_nodes = safe_get(data, "data", "bankaraSchedules", "nodes", default=[])
        if not bankara_nodes:
            return await interaction.followup.send("スケジュール取得に失敗しました。")

        pages: list[tuple[discord.Embed, Optional[discord.File]]] = []

        for node in bankara_nodes[:3]:
            # 設定の取り出しはフォールバック付き
            setting = (
                safe_get(node, "bankaraMatchSettings", 0) or
                safe_get(node, "regularMatchSetting") or
                {}
            )
            s1 = stage_name_ja(safe_get(setting, "vsStages", 0) or {})
            s2 = stage_name_ja(safe_get(setting, "vsStages", 1) or {})
            rule = to_ja_rule(safe_get(setting, "vsRule", "name"))
            start = fmt_dt_any(node.get("startTime"))
            end = fmt_dt_any(node.get("endTime"))
            text = f"**{start} - {end}**\n{rule}\n{s1} / {s2}"

            img1 = safe_get(setting, "vsStages", 0, "image", "url")
            img2 = safe_get(setting, "vsStages", 1, "image", "url")

            embed = discord.Embed(description=text)
            file: Optional[discord.File] = None

            if img1 and img2:
                try:
                    buf = await compose_side_by_side(img1, img2)
                    file = discord.File(buf, filename="stage.png")
                    embed.set_image(url="attachment://stage.png")
                except Exception:
                    log.exception("画像合成に失敗しました。テキストのみで送信します。")

            pages.append((embed, file))

        first_embed, first_file = pages[0]
        if first_file is not None:
            await interaction.followup.send(embed=first_embed, file=first_file)
        else:
            await interaction.followup.send(embed=first_embed)

    except Exception:
        log.exception("/hlt s3 でエラー")
        await interaction.followup.send("取得中にエラーが発生しました。後でもう一度お試しください。")

# ─────────────────────────────
# エントリポイント
# ─────────────────────────────
def main():
    if not TOKEN:
        raise RuntimeError("環境変数 DISCORD_TOKEN が未設定です。")
    client.run(TOKEN)

if __name__ == "__main__":
    main()

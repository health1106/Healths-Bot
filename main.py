import os
import logging
from typing import Optional, List

import re
import discord
from discord import app_commands
import asyncpg


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
# ※ 自己紹介メッセージの本文を読むために message_content を有効化。
#   Developer Portal > Bot > Privileged Gateway Intents で
#   "MESSAGE CONTENT INTENT" を ON にしてください。
# ─────────────────────────────
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True


# ─────────────────────────────
# Botクラス
# ─────────────────────────────
class YadoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.pool: Optional[asyncpg.Pool] = None

    async def setup_hook(self):
        # DB接続とテーブル作成
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
hlt = app_commands.Group(name="hlt", description="自己紹介ヘルパー")
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
# 便利関数
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
    # 自己紹介っぽい名前をゆるく判定
    return any(key in n for key in [
        "自己紹介", "introduc", "intro", "self-intro", "自己紹介部屋", "はじめまして", "自己紹介チャンネル"
    ])

async def find_latest_intro_message(
    channel: discord.TextChannel,
    user_id: int,
    search_limit: int = 800
) -> Optional[discord.Message]:
    # 新しい順に走査して最初に見つかったものを返す
    async for msg in channel.history(limit=search_limit, oldest_first=False):
        if msg.author.id == user_id:
            return msg
    return None

# ==== /hlt xp 用ユーティリティ ====
XP_CHANNEL_CANDIDATES = ["XP募集", "xp募集", "xp-募集", "ｘｐ募集"]

ZEN2HAN_TABLE = str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")

NUM_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)")

def _normalize_num_text(text: str) -> str:
    return text.translate(ZEN2HAN_TABLE).replace(",", "")

async def _find_xp_channel(guild: discord.Guild) -> discord.TextChannel | None:
    lowers = [c.lower() for c in XP_CHANNEL_CANDIDATES]
    for ch in guild.text_channels:
        if ch.name.lower() in lowers:
            return ch
    return None

async def _latest_number_for_user(
    channel: discord.TextChannel, user_id: int, limit: int = 1000
) -> str | None:
    async for msg in channel.history(limit=limit, oldest_first=False):
        if msg.author.id != user_id:
            continue
        m = NUM_PATTERN.search(_normalize_num_text(msg.content))
        if m:
            return m.group(1)
    return None


# ─────────────────────────────
# /hlt set-intro  … 管理者用：自己紹介チャンネルを登録
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
# /hlt auto  … 自動検出（管理者向け）
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

    # 一番ユーザー数が多い or 一番古い順など、ここでは一番上（役職順）の候補を採用
    chosen = sorted(candidates, key=lambda c: c.position)[0]
    await set_intro_channel(interaction.guild.id, chosen.id)
    await interaction.followup.send(f"自己紹介チャンネルを自動検出：{chosen.mention} に設定しました。", ephemeral=True)

# ─────────────────────────────
# /hlt config  … 現在の設定を確認
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
# /hlt intro  … 指定ユーザーの自己紹介（最新投稿）を呼び出す
# ─────────────────────────────
@hlt.command(name="intro", description="指定ユーザーの自己紹介（最新投稿）をこのチャンネルに呼び出します。")
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

    # チャンネル取得
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

    # 最新投稿を検索
    target_msg = await find_latest_intro_message(intro_ch, user.id, search_limit=800)

    if target_msg is None:
        return await interaction.followup.send(
            f"{user.mention} の自己紹介投稿は見つかりませんでした（直近800件を確認）。",
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True
        )

    created = discord.utils.format_dt(target_msg.created_at, style='F')
    header = f"**{user.mention} の自己紹介（{created}）**\n"
    body = target_msg.content if target_msg.content else "*（本文なし・Message Content Intentを有効にしていない可能性があります）*"
    footer = f"\n\n[元メッセージへ]({target_msg.jump_url})"

    files = []
    try:
        for a in target_msg.attachments[:5]:
            # 8MB超はURLのみ（Renderの無料枠などを想定）
            if a.size and a.size > 8 * 1024 * 1024:
                footer += f"\n添付（大容量）: {a.url}"
            else:
                files.append(await a.to_file())
    except Exception as e:
        log.warning("Attachment reupload failed: %s", e)

    await interaction.followup.send(
        header + body + footer,
        files=files,
        allowed_mentions=discord.AllowedMentions.none()
    )

# ─────────────────────────────
# /hlt xp  … XP募集から数値取得（既存の hlt グループに統合）
# ─────────────────────────────
@hlt.command(
    name="xp",
    description="『XP募集』チャンネルから指定ユーザーの最新数値を取得します。"
)
@app_commands.describe(user="対象ユーザー（サーバーメンバー）")
async def hlt_xp(interaction: discord.Interaction, user: discord.Member):
    import asyncio

    # ❶ 最初に必ず defer（以後は followup.send に統一）
    await interaction.response.defer(thinking=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("サーバー内で実行してください。", ephemeral=True)
        return

    xp_ch = await _find_xp_channel(guild)
    if xp_ch is None:
        await interaction.followup.send("『XP募集』チャンネルが見つかりません。", ephemeral=True)
        return

    me = guild.me or guild.get_member(interaction.client.user.id)  # type: ignore
    perms = xp_ch.permissions_for(me)
    if not (perms.view_channel and perms.read_messages and perms.read_message_history):
        await interaction.followup.send("『XP募集』の履歴を読めません（権限不足）。", ephemeral=True)
        return

    # ❷ 履歴スキャンにタイムアウトを付与（Unknown interactionの予防）
    async def _scan():
        return await _latest_number_for_user(xp_ch, user.id, limit=600)

    try:
        number = await asyncio.wait_for(_scan(), timeout=7)
    except asyncio.TimeoutError:
        await interaction.followup.send("検索に時間がかかりすぎました。後でもう一度お試しください。", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"検索中にエラー: {e}", ephemeral=True)
        return

    # ❸ 出力（ご希望どおりユーザー名を前に）
    if number is None:
        await interaction.followup.send(f"{user.display_name} さんの記入が見つかりませんでした。")
    else:
        await interaction.followup.send(f"{user.display_name} さん: XP {number}")

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
        "`/hlt xp @ユーザー` … 『XP募集』からそのユーザーの最新の数値を取得\n\n"
        "※ Botには「View Channel」「Read Message History」「Send Messages」「Embed Links」「Attach Files」の権限が必要です。\n"
        "※ メッセージ本文を取得するには Developer Portal で **MESSAGE CONTENT INTENT** をONにしてください。"
    )
    await interaction.response.send_message(text, ephemeral=True)

# ─────────────────────────────
# イベント
# ─────────────────────────────
@client.event
async def on_ready():
    # setup_hook() で sync 済みなのでログだけでOK
    log.info("Logged in as %s (ID: %s)", client.user, client.user.id)

@client.event
async def on_guild_join(guild: discord.Guild):
    # 参加直後に自己紹介チャンネルを軽く推測（未設定なら）
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

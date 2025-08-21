import os
import asyncio
import datetime
import discord
from discord import app_commands
from typing import Optional

TOKEN = os.getenv("DISCORD_TOKEN")
INTRO_CHANNEL_ID = int(os.getenv("INTRO_CHANNEL_ID", "0"))

# Intents: メッセージ履歴とギルド取得ができればOK
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True

class HLTRobot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # グローバルコマンド同期（初回は数分かかることあり）
        await self.tree.sync()

client = HLTRobot()

# ----- /hlt コマンド定義 -----
@client.tree.command(name="hlt", description="指定ユーザーの自己紹介（最新投稿）を呼び出します。")
@app_commands.describe(
    user="自己紹介を呼び出したい相手（ユーザー）",
    topic="固定：自己紹介"
)
@app_commands.choices(
    topic=[app_commands.Choice(name="自己紹介", value="intro")]
)
async def hlt(
    interaction: discord.Interaction,
    user: discord.User,
    topic: app_commands.Choice[str]
):
    # ガード
    if INTRO_CHANNEL_ID == 0:
        return await interaction.response.send_message(
            "設定エラー：INTRO_CHANNEL_ID（自己紹介チャンネルID）が未設定です。",
            ephemeral=True
        )

    # 返信は少し時間がかかる可能性があるのでdefer
    await interaction.response.defer(thinking=True)

    # 自己紹介チャンネルを取得
    intro_ch: Optional[discord.TextChannel] = interaction.client.get_channel(INTRO_CHANNEL_ID)
    if intro_ch is None:
        try:
            intro_ch = await interaction.client.fetch_channel(INTRO_CHANNEL_ID)
        except Exception:
            return await interaction.followup.send(
                "自己紹介チャンネルが見つかりませんでした。Botに閲覧権限があるか確認してください。",
                ephemeral=True
            )

    # 最新投稿を検索（上から最大200件を新しい順でチェック）
    target_msg: Optional[discord.Message] = None
    async for msg in intro_ch.history(limit=200, oldest_first=False):
        if msg.author.id == user.id:
            target_msg = msg
            break

    if target_msg is None:
        return await interaction.followup.send(
            f"{user.mention} の自己紹介チャンネルでの投稿は見つかりませんでした（直近200件まで確認）。",
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True
        )

    # 転記用の本文を整形
    created = discord.utils.format_dt(target_msg.created_at, style='D')  # 例: August 21, 2025
    header = f"**{user.mention} の自己紹介（{created}）**\n"
    body = target_msg.content if target_msg.content else "*（本文なし）*"
    footer = f"\n\n[元メッセージへ]({target_msg.jump_url})"

    content_to_send = header + body + footer

    # 画像などの添付ファイルを再アップロード（最大5件）
    files = []
    try:
        for a in target_msg.attachments[:5]:
            # ファイルサイズが大きすぎる場合はURLのみ提示
            if a.size and a.size > 8 * 1024 * 1024:  # 8MB超はURLにフォールバック（権限やプランに合わせて調整）
                content_to_send += f"\n添付（大容量）: {a.url}"
            else:
                files.append(await a.to_file())
    except Exception:
        # 添付の取り扱いでエラーが出ても本文だけは送る
        pass

    # 実行チャンネルへ送信
    await interaction.followup.send(
        content_to_send,
        files=files,
        allowed_mentions=discord.AllowedMentions.none()
    )

@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    try:
        synced = await client.tree.sync()
        print(f"Slash commands synced: {len(synced)}")
    except Exception as e:
        print("Sync error:", e)

def main():
    if not TOKEN:
        raise RuntimeError("環境変数 DISCORD_TOKEN が未設定です。")
    if INTRO_CHANNEL_ID == 0:
        print("警告：INTRO_CHANNEL_ID が未設定です。/hlt は動作しません。")
    client.run(TOKEN)

if __name__ == "__main__":
    main()

import os
import asyncio
import logging
import discord
from discord.ext import commands
# Typing用の型定義をインポート
from typing import Dict, Set

# 1. ロギングの設定（デバッグに便利）
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("bot")

# 2. 環境変数の読み込み（RenderのEnvironment設定から取得します）
TOKEN = os.getenv("DISCORD_TOKEN")
# 型エラーを防ぐため、IDは整数型に変換（設定されていない場合はNone）
DEVELOPER_ID_ENV = os.getenv("DEVELOPER_ID")
DEVELOPER_ID = int(DEVELOPER_ID_ENV) if DEVELOPER_ID_ENV else None

# 3. インテントの設定（必要最小限）
intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容を読み取る場合に必要

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        self.developer_id = DEVELOPER_ID
        # ギルドごとの設定をメモリに保持 (再起動でリセットされます)
        self.authorized_users: Dict[int, Set[int]] = {}  # guild_id -> set(user_ids)
        self.target_channels: Dict[int, int] = {}       # guild_id -> channel_id

    def is_authorized(self, interaction: discord.Interaction) -> bool:
        """
        コマンドの実行権限があるかチェックする
        管理者、開発者、または許可されたユーザーリストに含まれている場合にTrueを返す
        """
        # 開発者は常に許可
        if self.developer_id and interaction.user.id == self.developer_id:
            return True

        # 管理者は常に許可
        if interaction.user.guild_permissions.administrator:
            return True
        
        # 許可されたユーザーリストに含まれているかチェック
        gid = interaction.guild_id
        if gid in self.authorized_users:
            if interaction.user.id in self.authorized_users[gid]:
                return True
        
        return False

    async def setup_hook(self):
        # cogs フォルダ内のファイルを自動的に読み込む
        # ※cogsフォルダが存在しない場合のエラーを防ぐ判定を追加
        if os.path.exists("./cogs"):
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py"):
                    await self.load_extension(f"cogs.{filename[:-3]}")
        
        # スラッシュコマンドを Discord に同期
        await self.tree.sync()

async def main():
    bot = MyBot()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN が設定されていません。RenderのEnvironment設定を確認してください。")
    else:
        # Render（Linux環境）で動かすため、最後のinput()による待機を削除
        asyncio.run(main())

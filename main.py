import os
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv

# 1. 環境変数の読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_ID_STR = os.getenv("DEVELOPER_ID")
DEVELOPER_ID = int(DEV_ID_STR) if DEV_ID_STR and DEV_ID_STR.isdigit() else None

# 2. ロギングの設定（デバッグに便利）
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("bot")

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
    try:
        if not TOKEN:
            print("Error: DISCORD_TOKEN が設定されていません。.env ファイルを確認してください。")
        else:
            asyncio.run(main())
    except Exception as e:
        print(f"エラーが発生しました:\n{e}")
        import traceback
        traceback.print_exc()
    finally:
        input("\nEnterキーを押すと終了します...")

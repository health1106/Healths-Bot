import os
import asyncio
import logging
import discord
from discord.ext import commands
from typing import Dict, Set
from flask import Flask
from threading import Thread
from waitress import serve  # waitressをインポート

# ==========================================
# 1. ロギングの設定
# ==========================================
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("bot")

TOKEN = os.getenv("DISCORD_TOKEN")
DEVELOPER_ID_ENV = os.getenv("DEVELOPER_ID")
DEVELOPER_ID = int(DEVELOPER_ID_ENV) if DEVELOPER_ID_ENV else None

intents = discord.Intents.default()
intents.message_content = True

# ==========================================
# 4. Webサーバー設定（Waitress版）
# ==========================================
app = Flask("")

@app.route("/")
def home():
    return "Bot is safe and sound! Powered by Render.", 200

# HEADリクエスト対策（UptimeRobot用）
@app.route("/", methods=["HEAD"])
def home_head():
    return "", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    # app.run ではなく waitress の serve を使用
    serve(app, host="0.0.0.0", port=port, _quiet=True)

def keep_alive():
    t = Thread(target=run_web_server, daemon=True)  # daemon=True にしてメインプロセス終了時に連動させる
    t.start()

# ==========================================
# 5. Discord Botのメインクラス定義
# ==========================================
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        self.developer_id = DEVELOPER_ID
        self.authorized_users: Dict[int, Set[int]] = {}
        self.target_channels: Dict[int, int] = {}

    def is_authorized(self, interaction: discord.Interaction) -> bool:
        if self.developer_id and interaction.user.id == self.developer_id:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        gid = interaction.guild_id
        if gid in self.authorized_users:
            if interaction.user.id in self.authorized_users[gid]:
                return True
        return False

    async def setup_hook(self):
        if os.path.exists("./cogs"):
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py"):
                    await self.load_extension(f"cogs.{filename[:-3]}")
        await self.tree.sync()

# ==========================================
# 6. メイン起動処理
# ==========================================
async def main():
    bot = MyBot()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN が設定されていません。")
    else:
        keep_alive()
        asyncio.run(main())

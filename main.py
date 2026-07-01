import os
import asyncio
import logging
import discord
from discord.ext import commands
from typing import Dict, Set
# Webサーバーを立ち上げるためのライブラリ（Renderのスリープ・タイムアウト対策）
from flask import Flask
from threading import Thread

# ==========================================
# 1. ロギングの設定（ログの出力形式を決める）
# ==========================================
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("bot")

# ==========================================
# 2. 環境変数の読み込み（Renderの設定画面から取得）
# ==========================================
# Discordの接続に必要なトークン（パスワード）を取得
TOKEN = os.getenv("DISCORD_TOKEN")

# 開発者のDiscordユーザーIDを取得（設定されていれば数値に変換、なければNone）
DEVELOPER_ID_ENV = os.getenv("DEVELOPER_ID")
DEVELOPER_ID = int(DEVELOPER_ID_ENV) if DEVELOPER_ID_ENV else None

# ==========================================
# 3. インテントの設定（Botが受け取る情報の制限）
# ==========================================
intents = discord.Intents.default()
intents.message_content = True  # メッセージの内容を読み取る権限を有効化

# ==========================================
# 4. Renderのタイムアウト・スリープを回避するWebサーバー設定
# ==========================================
# 空のFlaskアプリ（ミニWebサーバー）のインスタンスを作成
app = Flask("")

# UptimeRobotなどの監視サービスがアクセス（/）してきたときに「200 OK」を返す処理
@app.route("/")
def home():
    # 正常に稼働していることをテキストで返す（UptimeRobotがこれを検知して「Up」と判定します）
    return "Bot is safe and sound! Powered by Render."

def run_web_server():
    # Renderは標準で10000番ポートを使用するため、環境変数からポート番号を取得（なければ10000番）
    port = int(os.environ.get("PORT", 10000))
    # 外部からのアクセス（0.0.0.0）を許可する設定でWebサーバーを起動
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    # Discord Botの起動（メイン処理）を邪魔しないよう、別のスレッド（裏側）でWebサーバーを走らせる
    t = Thread(target=run_web_server)
    t.start()

# ==========================================
# 5. Discord Botのメインクラス定義
# ==========================================
class MyBot(commands.Bot):
    def __init__(self):
        # 継承元のクラス（commands.Bot）を初期化
        # コマンドの頭文字を「!」に設定、インテントを適用、デフォルトのヘルプコマンドは無効化
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        # 開発者のIDをBotの変数に保持
        self.developer_id = DEVELOPER_ID
        
        # サーバー（ギルド）ごとの設定を一時的に記憶するメモリ
        # ※Botが再起動（デプロイなど）されるとこれらのメモリはリセットされます
        self.authorized_users: Dict[int, Set[int]] = {}  # サーバーID -> 許可されたユーザーIDのセット
        self.target_channels: Dict[int, int] = {}       # サーバーID -> 対象のチャンネルID

    def is_authorized(self, interaction: discord.Interaction) -> bool:
        """
        スラッシュコマンドなどを実行する権限があるかを判定する関数
        """
        # 1. 実行したユーザーが開発者（あなた）なら無条件で許可
        if self.developer_id and interaction.user.id == self.developer_id:
            return True
            
        # 2. 実行したユーザーがそのサーバーの「管理者権限」を持っていれば許可
        if interaction.user.guild_permissions.administrator:
            return True
        
        # 3. 許可されたユーザーリスト（メモリ内）に対象ユーザーが含まれていれば許可
        gid = interaction.guild_id
        if gid in self.authorized_users:
            if interaction.user.id in self.authorized_users[gid]:
                return True
        
        # 上記のどれにも当てはまらない場合は権限なし（False）
        return False

    async def setup_hook(self):
        """
        BotがDiscordにログインする直前に実行される準備処理
        """
        # プロジェクト内に「cogs」という名前のフォルダが存在するか確認
        if os.path.exists("./cogs"):
            # cogsフォルダ内のファイル一覧をループ処理
            for filename in os.listdir("./cogs"):
                # 拡張子が「.py」で終わるファイル（Pythonコード）を探す
                if filename.endswith(".py"):
                    # 発見したコグファイル（機能モジュール）をBotに自動ロードする（例: cogs.music）
                    await self.load_extension(f"cogs.{filename[:-3]}")
        
        # 作成したスラッシュコマンドをDiscordサーバー側へ同期・登録する
        await self.tree.sync()

# ==========================================
# 6. メイン起動処理
# ==========================================
async def main():
    # 自作したBotのインスタンスを作成
    bot = MyBot()
    # 非同期処理のコンテキスト（接続維持）を開始
    async with bot:
        # Renderから提供されたトークンを使って、Discordサーバーへログイン開始
        await bot.start(TOKEN)

# プログラムが直接実行された（python main.py された）場合の入り口
if __name__ == "__main__":
    # Renderの環境変数（パスワード）が設定されていない場合の安全装置
    if not TOKEN:
        print("Error: DISCORD_TOKEN が設定されていません。RenderのEnvironment設定を確認してください。")
    else:
        # 裏側でタイムアウト回避用のWebサーバーを起動
        keep_alive()
        # Discord Botの非同期メインループを実行・維持
        async with asyncio.Runner() as runner:
            runner.run(main())

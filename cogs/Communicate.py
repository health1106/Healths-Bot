import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict, Set, List
import json
import os
import datetime

# 設定データを保存するJSONファイルのパス
SETTINGS_FILE = "bot_settings.json"
# ユーザー統計データを保存するJSONファイルのパス（新規追加）
STATS_FILE = "user_stats.json"

def load_settings() -> dict:
    """JSONファイルからすべての設定データを読み込む関数"""
    if not os.path.exists(SETTINGS_FILE):
        return {"authorized_users": {}, "target_channels": {}}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            if "authorized_users" not in data:
                data["authorized_users"] = {}
            if "target_channels" not in data:
                data["target_channels"] = {}
                
            auth_users = {int(gid): set(uids) for gid, uids in data["authorized_users"].items()}
            target_chs = {int(gid): int(ch_id) for gid, ch_id in data["target_channels"].items()}
            
            return {"authorized_users": auth_users, "target_channels": target_chs}
    except Exception as e:
        print(f"設定データの読み込みエラー: {e}")
        return {"authorized_users": {}, "target_channels": {}}

def save_settings(authorized_users: Dict[int, Set[int]], target_channels: Dict[int, int]):
    """設定データをまとめてJSONファイルに書き込む関数"""
    try:
        data = {
            "authorized_users": {str(gid): list(uids) for gid, uids in authorized_users.items()},
            "target_channels": {str(gid): ch_id for gid, ch_id in target_channels.items()}
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"設定データの保存エラー: {e}")


# --- ここから統計データ管理用の関数 ---

def load_stats() -> dict:
    """JSONファイルからユーザーの統計データを読み込む関数"""
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"統計データの読み込みエラー: {e}")
        return {}

def save_stats(stats_data: dict):
    """統計データをJSONファイルに書き込む関数"""
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"統計データの保存エラー: {e}")


class Communicate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # 設定データの読み込み
        settings = load_settings()
        if not hasattr(self.bot, "authorized_users") or not self.bot.authorized_users:
            self.bot.authorized_users = settings["authorized_users"]
        if not hasattr(self.bot, "target_channels") or not self.bot.target_channels:
            self.bot.target_channels = settings["target_channels"]

        # 統計データの読み込みとVC入室管理用の一時変数
        self.stats_data = load_stats()
        self.vc_tracking: Dict[int, datetime.datetime] = {}  # user_id: join_time

    # 親グループ /set
    set_group = app_commands.Group(name="set", description="HealthsBotの各種設定を行います")
    # 子グループ /set 自己紹介
    intro_config_group = app_commands.Group(name="自己紹介", description="自己紹介機能の設定を行います", parent=set_group)

    # --- データの自動記録（イベントリスナー） ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """テキストが打ち込まれた際に文字数を加算するイベント"""
        if message.author.bot:
            return

        user_id = str(message.author.id)
        if user_id not in self.stats_data:
            self.stats_data[user_id] = {"vc_seconds": 0, "text_chars": 0}

        # メッセージの文字数を加算
        self.stats_data[user_id]["text_chars"] += len(message.content)
        save_stats(self.stats_data)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """VCへの入退室を検知して浮上時間を計測するイベント"""
        if member.bot:
            return

        user_id_int = member.id
        user_id_str = str(member.id)
        now = datetime.datetime.now(datetime.timezone.utc)

        # VCに参加した（beforeのチャンネルが無、または別のチャンネルから移動してきた場合かつ、ミュート状態等ではなく純粋な参加・移動を想定）
        if before.channel is None and after.channel is not None:
            self.vc_tracking[user_id_int] = now

        # VCから完全に退出した
        elif before.channel is not None and after.channel is None:
            join_time = self.vc_tracking.pop(user_id_int, None)
            if join_time:
                duration = (now - join_time).total_seconds()
                
                if user_id_str not in self.stats_data:
                    self.stats_data[user_id_str] = {"vc_seconds": 0, "text_chars": 0}
                
                self.stats_data[user_id_str]["vc_seconds"] += int(duration)
                save_stats(self.stats_data)

    # --- 新規追加コマンド (/rank) ---

    @app_commands.command(name="rank", description="ユーザーの総VC浮上時間と総テキスト文字数を表示します")
    @app_commands.describe(user="ステータスを表示したいユーザー（省略した場合は自分）")
    async def show_rank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        # ターゲットユーザー（指定がない場合は実行したユーザー自身）
        target_user = user or interaction.user
        user_id_str = str(target_user.id)

        # データが存在しない場合の初期値
        user_stats = self.stats_data.get(user_id_str, {"vc_seconds": 0, "text_chars": 0})
        
        # 現在リアルタイムでVCに滞在中の場合、そこまでの経過時間も一時的に合算して計算する
        current_vc_seconds = user_stats["vc_seconds"]
        if target_user.id in self.vc_tracking:
            now = datetime.datetime.now(datetime.timezone.utc)
            live_duration = (now - self.vc_tracking[target_user.id]).total_seconds()
            current_vc_seconds += int(live_duration)

        # 秒数を「〇時間〇分〇秒」の形に変換
        hours, remainder = divmod(current_vc_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        vc_time_str = f"{hours}時間 {minutes}分 {seconds}秒"

        total_chars = user_stats["text_chars"]

        # 綺麗に装飾してEmbedで出力
        embed = discord.Embed(
            title=f"📊 {target_user.display_name} のアクティビティ統計",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="🎙️ 総VC浮上時間", value=f"`{vc_time_str}`", inline=False)
        embed.add_field(name="✍️ 総テキスト入力文字数", value=f"`{total_chars:,}` 文字", inline=False)
        
        if target_user.id in self.vc_tracking:
            embed.set_footer(text="※現在VCに接続中のため、現セッションの時間を含めています。")

        await interaction.response.send_message(embed=embed)

    # --- 設定コマンド (/set admin) ---

    @set_group.command(name="admin", description="HealthsBotの全てのコマンドを実行する権限を付与します")
    @app_commands.describe(user="権限を与えるユーザー")
    async def add_permission(self, interaction: discord.Interaction, user: discord.Member):
        if not self.bot.is_authorized(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return

        gid = interaction.guild_id
        if gid not in self.bot.authorized_users:
            self.bot.authorized_users[gid] = set()
        
        self.bot.authorized_users[gid].add(user.id)
        save_settings(self.bot.authorized_users, self.bot.target_channels)
        
        await interaction.response.send_message(f"{user.mention} にHealthsBotの全コマンド実行権限を付与しました。", ephemeral=True)

    # --- 設定コマンド (/set 自己紹介 ch) ---

    @intro_config_group.command(name="ch", description="自己紹介を検索するチャンネルを指定します")
    @app_commands.describe(channel="対象 of テキストチャンネル")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.bot.is_authorized(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return

        self.bot.target_channels[interaction.guild_id] = channel.id
        save_settings(self.bot.authorized_users, self.bot.target_channels)
        
        await interaction.response.send_message(f"検索対象チャンネルを {channel.mention} に設定しました。", ephemeral=True)

    # --- 表示コマンド (/自己紹介 ...) ---

    @app_commands.command(name="自己紹介", description="指定したユーザーの最新の自己紹介を表示します")
    @app_commands.describe(user="自己紹介を表示したいユーザー")
    async def get_intro(self, interaction: discord.Interaction, user: discord.User):
        gid = interaction.guild_id
        if gid not in self.bot.target_channels:
            await interaction.response.send_message("対象のチャンネルが設定されていません。管理者に `/set 自己紹介 ch` での設定を依頼してください。", ephemeral=True)
            return

        target_ch_id = self.bot.target_channels[gid]
        channel = self.bot.get_channel(target_ch_id)
        
        if not channel or not isinstance(channel, discord.TextChannel):
            try:
                channel = await self.bot.fetch_channel(target_ch_id)
            except:
                await interaction.response.send_message("設定されたチャンネルが見つからないか、Botに閲覧権限がありません。", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)

        target_msg: Optional[discord.Message] = None
        async for msg in channel.history(limit=2000):
            if msg.author.id == user.id:
                target_msg = msg
                break

        if not target_msg:
            await interaction.followup.send(f"{user.mention} のメッセージが指定チャンネルで見つかりませんでした。", ephemeral=True)
            return

        embed = discord.Embed(
            description=target_msg.content or "*本文なし*",
            color=discord.Color.blue(),
            timestamp=target_msg.created_at
        )
        embed.set_author(name=f"{user.display_name} の自己紹介", icon_url=user.display_avatar.url)
        embed.add_field(name="元メッセージ", value=f"[クリックで移動]({target_msg.jump_url})")

        files = []
        if target_msg.attachments:
            for attr in target_msg.attachments[:5]:
                try:
                    files.append(await attr.to_file())
                except:
                    pass

        await interaction.followup.send(embed=embed, files=files)

async def setup(bot: commands.Bot):
    cog = Communicate(bot)
    await bot.add_cog(cog)

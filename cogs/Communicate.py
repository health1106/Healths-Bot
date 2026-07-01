import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict, Set, List
import datetime
import sqlite3
import os

class Communicate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # VCの一時的な入室時間を記録する用（メモリ上でのみ管理）
        self.vc_start_times: Dict[int, datetime.datetime] = {}
        
        # データベースの初期設定
        self.db_path = "user_stats.db"
        self._init_db()

    def _init_db(self):
        """データベースとテーブルを初期化する"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # ユーザーごとのVC時間と文字数を保存するテーブル
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id INTEGER PRIMARY KEY,
                vc_minutes INTEGER DEFAULT 0,
                text_chars INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def _get_user_stats(self, user_id: int) -> Dict[str, int]:
        """特定のユーザーのデータを取得する（なければ0で初期化）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT vc_minutes, text_chars FROM stats WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row is None:
            # まだデータがない場合は新規登録
            cursor.execute("INSERT INTO stats (user_id, vc_minutes, text_chars) VALUES (?, 0, 0)", (user_id,))
            conn.commit()
            result = {"vc_minutes": 0, "text_chars": 0}
        else:
            result = {"vc_minutes": row[0], "text_chars": row[1]}
            
        conn.close()
        return result

    def _update_stats(self, user_id: int, vc_diff: int = 0, text_diff: int = 0):
        """データを加算して更新する"""
        # 事前にデータが存在することを確認
        self._get_user_stats(user_id)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE stats 
            SET vc_minutes = vc_minutes + ?, text_chars = text_chars + ? 
            WHERE user_id = ?
        """, (vc_diff, text_diff, user_id))
        conn.commit()
        conn.close()

    def _get_all_stats(self) -> List[tuple]:
        """全ユーザーのデータを取得する (user_id, vc_minutes, text_chars)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, vc_minutes, text_chars FROM stats")
        rows = cursor.fetchall()
        conn.close()
        return rows


    # --- 自動集計用のイベントリスナー ---

    # 1. 文字数の自動カウントとデータベース保存
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        
        # 文字数をデータベースに直接加算保存
        self._update_stats(message.author.id, text_diff=len(message.content))

    # 2. VC時間の自動カウントとデータベース保存
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        user_id = member.id
        now = datetime.datetime.now(datetime.timezone.utc)

        # VCに入室した時
        if before.channel is None and after.channel is not None:
            self.vc_start_times[user_id] = now

        # VCから退室した時
        elif before.channel is not None and after.channel is None:
            start_time = self.vc_start_times.pop(user_id, None)
            if start_time:
                duration = now - start_time
                minutes = max(1, int(duration.total_seconds() / 60))
                
                # VC時間をデータベースに加算保存
                self._update_stats(user_id, vc_diff=minutes)


    # --- 設定コマンド (/set admin) ---

    set_group = app_commands.Group(name="set", description="HealthsBotの各種設定を行います")
    intro_config_group = app_commands.Group(name="自己紹介", description="自己紹介機能の設定を行います", parent=set_group)

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
        await interaction.response.send_message(f"{user.mention} にHealthsBotの全コマンド実行権限を付与しました。", ephemeral=True)

    # --- 設定コマンド (/set 自己紹介 ch) ---

    @intro_config_group.command(name="ch", description="自己紹介を検索するチャンネルを指定します")
    @app_commands.describe(channel="対象のテキストチャンネル")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.bot.is_authorized(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return

        self.bot.target_channels[interaction.guild_id] = channel.id
        await interaction.response.send_message(f"検索対象チャンネルを {channel.mention} に設定しました。", ephemeral=True)

    # --- 表示コマンド (/自己紹介 ...) ---

    @app_commands.command(name="自己紹介", description="指定したユーザーの最新の自己紹介を表示します")
    @app_commands.describe(user="自己紹介を表示したいユーザー")
    async def get_intro(self, interaction: discord.Interaction, user: discord.User):
        gid = interaction.guild_id
        if gid not in self.bot.target_channels:
            await interaction.response.send_message("対象のチャンネルが設定されていません。管理者に `/hlt ch` での設定を依頼してください。", ephemeral=True)
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


    # --- ランクコマンド (/rank) ---

    @app_commands.command(name="rank", description="ユーザーの戦績やサーバー内のランキングを表示します")
    @app_commands.describe(
        user="戦績を表示したいユーザー（ランキング表示の時は未選択）",
        view_type="top10 または was10 を選択してランキングを表示します"
    )
    @app_commands.choices(view_type=[
        app_commands.Choice(name="top10 (上位10人)", value="top10"),
        app_commands.Choice(name="was10 (下位10人)", value="was10")
    ])
    async def rank_command(
        self, 
        interaction: discord.Interaction, 
        user: Optional[discord.Member] = None, 
        view_type: Optional[str] = None
    ):
        # DBから全データをロード
        raw_stats = self._get_all_stats()
        
        if not raw_stats:
            await interaction.response.send_message("まだサーバー内でデータ（発言やVCへの参加）が記録されていません。しばらく経ってから再度お試しください。", ephemeral=True)
            return

        # ランキング用にソート（VC時間を優先、次いで文字数）
        sorted_stats = sorted(
            raw_stats, 
            key=lambda item: (item[1], item[2]), 
            reverse=True
        )

        # 1. ランキング表示（top10 / was10）の処理
        if view_type:
            is_top = (view_type == "top10")
            title = "🏆 VC時間＆入力文字数 TOP10" if is_top else "📉 VC時間＆入力文字数 WORST10"
            color = discord.Color.gold() if is_top else discord.Color.red()
            
            target_list = sorted_stats[:10] if is_top else list(reversed(sorted_stats))[:10]

            embed = discord.Embed(title=title, color=color)
            
            for idx, (u_id, vc_time, text_count) in enumerate(target_list, start=1):
                member = interaction.guild.get_member(u_id)
                name = member.display_name if member else f"ユーザー({u_id})"
                
                actual_rank = idx if is_top else len(sorted_stats) - idx + 1
                
                embed.add_field(
                    name=f"{actual_rank}位: {name}",
                    value=f"⏱ VC時間: {vc_time}分 / 💬 文字数: {text_count}文字",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed)
            return

        # 2. ユーザー個人の戦績表示の処理
        target_user = user or interaction.user
        user_id = target_user.id

        # 対象ユーザーの現在のステータスを確保・取得
        user_data = self._get_user_stats(user_id)
        
        # 順位を割り出すために再度全データを読み込み直し（新規追加された可能性があるため）
        updated_raw_stats = self._get_all_stats()
        updated_sorted_stats = sorted(updated_raw_stats, key=lambda item: (item[1], item[2]), reverse=True)

        user_rank = 1
        for u_id, _, _ in updated_sorted_stats:
            if u_id == user_id:
                break
            user_rank += 1

        embed = discord.Embed(
            title=f"📊 {target_user.display_name} の戦績リポート",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="サーバー内総合順位", value=f"**{user_rank}** 位 / {len(updated_sorted_stats)}人中", inline=False)
        embed.add_field(name="⏱ トータルVC時間", value=f"{user_data['vc_minutes']} 分", inline=True)
        embed.add_field(name="💬 トータル入力文字数", value=f"{user_data['text_chars']} 文字", inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    cog = Communicate(bot)
    await bot.add_cog(cog)

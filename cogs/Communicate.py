import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict, Set, List
import datetime
import sqlite3
import re

class Communicate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.vc_start_times: Dict[int, datetime.datetime] = {}
        self.db_path = "user_stats_monthly.db"
        self._init_db()

    def _init_db(self):
        """データベースの初期化（年月ごとにデータを管理する構造）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # user_id と year_month の組み合わせを主キーにする
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_stats (
                user_id INTEGER,
                year_month TEXT,
                vc_minutes INTEGER DEFAULT 0,
                text_chars INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, year_month)
            )
        """)
        conn.commit()
        conn.close()

    def _get_current_ym(self, dt: Optional[datetime.datetime] = None) -> str:
        """現在の年月（または指定された日時）を 'YYYY-MM' 形式で返す"""
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))) # JSTベース
        return dt.strftime("%Y-%m")

    def _format_input_month(self, month_str: str) -> Optional[str]:
        """ユーザーが入力した '2026.7' などの形式を '2026-07' に正規化する"""
        # ドットやハイフン、スラッシュで区切られた数字を抽出
        match = re.match(r"^(\d{4})[\.\-/](\d{1,2})$", month_str.strip())
        if match:
            year, month = match.groups()
            return f"{int(year):04d}-{int(month):02d}"
        return None

    def _update_stats(self, user_id: int, ym: str, vc_diff: int = 0, text_diff: int = 0):
        """指定された年月のデータを加算・更新する"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # データがなければ作成、あれば加算するSQL (Upsert)
        cursor.execute("""
            INSERT INTO monthly_stats (user_id, year_month, vc_minutes, text_chars)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, year_month) DO UPDATE SET
                vc_minutes = vc_minutes + excluded.vc_minutes,
                text_chars = text_chars + excluded.text_chars
        """, (user_id, ym, vc_diff, text_diff))
        conn.commit()
        conn.close()

    def _get_user_stats(self, user_id: int, ym: str) -> Dict[str, int]:
        """特定ユーザーの指定年月のデータを取得"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT vc_minutes, text_chars FROM monthly_stats WHERE user_id = ? AND year_month = ?", (user_id, ym))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"vc_minutes": row[0], "text_chars": row[1]}
        return {"vc_minutes": 0, "text_chars": 0}

    def _get_all_stats(self, ym: str) -> List[tuple]:
        """指定年月の全ユーザーデータを取得"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, vc_minutes, text_chars FROM monthly_stats WHERE year_month = ?", (ym,))
        rows = cursor.fetchall()
        conn.close()
        return rows


    # --- 自動集計用のイベントリスナー ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        
        # メッセージが投稿された時点の年月を取得して保存
        ym = self._get_current_ym(message.created_at.astimezone(datetime.timezone(datetime.timedelta(hours=9))))
        self._update_stats(message.author.id, ym, text_diff=len(message.content))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        user_id = member.id
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))) # JST

        if before.channel is None and after.channel is not None:
            self.vc_start_times[user_id] = now
        elif before.channel is not None and after.channel is None:
            start_time = self.vc_start_times.pop(user_id, None)
            if start_time:
                duration = now - start_time
                minutes = max(1, int(duration.total_seconds() / 60))
                
                # VCを切断した時点の年月で保存
                ym = self._get_current_ym(now)
                self._update_stats(user_id, ym, vc_diff=minutes)


    # --- 設定コマンド (/set) と /自己紹介 は既存のものを維持 ---
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
        await interaction.response.send_message(f"{user.mention} に権限を付与しました。", ephemeral=True)

    @intro_config_group.command(name="ch", description="自己紹介を検索するチャンネルを指定します")
    @app_commands.describe(channel="対象のテキストチャンネル")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.bot.is_authorized(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return
        self.bot.target_channels[interaction.guild_id] = channel.id
        await interaction.response.send_message(f"検索対象チャンネルを {channel.mention} に設定しました。", ephemeral=True)

    @app_commands.command(name="自己紹介", description="指定したユーザーの最新の自己紹介を表示します")
    @app_commands.describe(user="自己紹介を表示したいユーザー")
    async def get_intro(self, interaction: discord.Interaction, user: discord.User):
        # (既存の自己紹介コマンドのコード。省略せずそのままここに配置してください)
        pass


    # --- ランクコマンド群 (/rank) ---

    rank_group = app_commands.Group(name="rank", description="ランク・統計に関するコマンド")

    # 1. 過去データ同期コマンド（/rank sync）※メッセージインテントが必要です
    @rank_group.command(name="sync", description="【管理者用】過去の全チャンネルのメッセージから文字数を集計し同期します")
    @app_commands.describe(limit_per_ch="1チャンネルあたり遡る最大メッセージ数（デフォルト: 5000）")
    async def sync_history(self, interaction: discord.Interaction, limit_per_ch: int = 5000):
        if not self.bot.is_authorized(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        await interaction.followup.send("過去ログの解析を開始します。サーバーの規模によっては数分かかります...")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        synchronized_channels = 0
        total_messages_processed = 0

        # サーバー内の全テキストチャンネルを取得
        for channel in interaction.guild.text_channels:
            # Botが読み込み権限を持っているかチェック
            if not channel.permissions_for(interaction.guild.me).read_message_history:
                continue
            
            try:
                async for msg in channel.history(limit=limit_per_ch):
                    if msg.author.bot:
                        continue
                    
                    # メッセージの作成日時から年月を取得
                    msg_jst = msg.created_at.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                    ym = msg_jst.strftime("%Y-%m")
                    char_count = len(msg.content)

                    # データベースを即時更新（高速化のためオンコンフリクトを使用）
                    cursor.execute("""
                        INSERT INTO monthly_stats (user_id, year_month, text_chars)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, year_month) DO UPDATE SET
                            text_chars = text_chars + excluded.text_chars
                    """, (msg.author.id, ym, char_count))
                    
                    total_messages_processed += 1
                synchronized_channels += 1
            except Exception as e:
                print(f"チャンネル {channel.name} の同期中にエラー: {e}")

        conn.commit()
        conn.close()

        await interaction.followup.send(
            f"✅ 同期が完了しました！\n"
            f"・スキャン成功: {synchronized_channels} 個のチャンネル\n"
            f"・解析メッセージ数: {total_messages_processed} 件\n"
            f"※過去のテキスト文字数が各月に割り振られました（VC時間は同期されません）。"
        )

    # 2. メインの表示コマンド（/rank show）※指定月対応
    @rank_group.command(name="show", description="ユーザーの戦績やサーバー内のランキングを表示します")
    @app_commands.describe(
        user="戦績を表示したいユーザー（ランキング表示の時は未選択）",
        view_type="top10 または was10 を選択してランキングを表示します",
        month="指定したい年月（例: 2026.7 や 2026-07）。未入力なら今月"
    )
    @app_commands.choices(view_type=[
        app_commands.Choice(name="top10 (上位10人)", value="top10"),
        app_commands.Choice(name="was10 (下位10人)", value="was10")
    ])
    async def show_rank(
        self, 
        interaction: discord.Interaction, 
        user: Optional[discord.Member] = None, 
        view_type: Optional[str] = None,
        month: Optional[str] = None
    ):
        # 1. 検索対象年月の確定
        if month:
            target_ym = self._format_input_month(month)
            if not target_ym:
                await interaction.response.send_message("❌ 年月の形式が正しくありません。「2026.7」や「2026-07」のように入力してください。", ephemeral=True)
                return
        else:
            target_ym = self._get_current_ym()

        # DBから指定年月のデータをロード
        raw_stats = self._get_all_stats(target_ym)
        display_ym = target_ym.replace("-", ".") # 表示用に '2026.07' に戻す
        
        if not raw_stats:
            await interaction.response.send_message(f"データがありません。対象月（{display_ym}）にまだ誰も発言していないか、同期が行われていません。", ephemeral=True)
            return

        # ランキング用にソート（VC時間、次いで文字数）
        sorted_stats = sorted(raw_stats, key=lambda item: (item[1], item[2]), reverse=True)

        # ランキング表示（top10 / was10）の処理
        if view_type:
            is_top = (view_type == "top10")
            title = f"🏆 {display_ym} VC時間＆文字数 TOP10" if is_top else f"📉 {display_ym} VC時間＆文字数 WORST10"
            color = discord.Color.gold() if is_top else discord.Color.red()
            
            target_list = sorted_stats[:10] if is_top else list(reversed(sorted_stats))[:10]
            embed = discord.Embed(title=title, color=color)
            
            for idx, (u_id, vc_time, text_count) in enumerate(target_list, start=1):
                member = interaction.guild.get_member(u_id)
                name = member.display_name if member else f"ユーザー({u_id})"
                actual_rank = idx if is_top else len(sorted_stats) - idx + 1
                
                embed.add_field(
                    name=f"{actual_rank}位: {name}",
                    value=f"⏱ VC: {vc_time}分 / 💬 文字数: {text_count}文字",
                    inline=False
                )
            await interaction.response.send_message(embed=embed)
            return

        # ユーザー個人の戦績表示の処理
        target_user = user or interaction.user
        user_id = target_user.id

        # 指定されたユーザーの対象月のデータを取得
        user_data = self._get_user_stats(user_id, target_ym)

        user_rank = 1
        in_list = False
        for u_id, _, _ in sorted_stats:
            if u_id == user_id:
                in_list = True
                break
            user_rank += 1

        embed = discord.Embed(
            title=f"📊 {target_user.display_name} の戦績リポート ({display_ym})",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        if in_list:
            embed.add_field(name="当月総合順位", value=f"**{user_rank}** 位 / {len(sorted_stats)}人中", inline=False)
        else:
            embed.add_field(name="当月総合順位", value="圏外（データなし）", inline=False)
            
        embed.add_field(name="⏱ VC時間", value=f"{user_data['vc_minutes']} 分", inline=True)
        embed.add_field(name="💬 入力文字数", value=f"{user_data['text_chars']} 文字", inline=True)

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    cog = Communicate(bot)
    await bot.add_cog(cog)

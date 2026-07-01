import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict, Set, List

class Communicate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # 親グループ /set
    set_group = app_commands.Group(name="set", description="HealthsBotの各種設定を行います")
    # 子グループ /set 自己紹介
    intro_config_group = app_commands.Group(name="自己紹介", description="自己紹介機能の設定を行います", parent=set_group)

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


    # --- 新規追加: ランクコマンド (/rank) ---

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
        # データの取得（仮の実装。実際は self.bot.db などから取得する想定）
        # 構造の例: { user_id: {"vc_minutes": 120, "text_chars": 5000} }
        stats_data: Dict[int, Dict[str, int]] = getattr(self.bot, "user_stats", {})
        
        # データが空だった場合の処理
        if not stats_data:
            await interaction.response.send_message("データが登録されていません。", ephemeral=True)
            return

        # ランキング用にデータをソート（ここではVC時間を第1基準、文字数を第2基準として降順ソート）
        # ※下位表示の時はこれを反転させます
        sorted_stats = sorted(
            stats_data.items(), 
            key=lambda item: (item[1].get("vc_minutes", 0), item[1].get("text_chars", 0)), 
            reverse=True
        )

        # 1. ランキング表示（top10 / was10）の処理
        if view_type:
            is_top = (view_type == "top10")
            title = "🏆 VC時間＆入力文字数 TOP10" if is_top else "📉 VC時間＆入力文字数 WORST10"
            color = discord.Color.gold() if is_top else discord.Color.red()
            
            # top10なら上から10人、was10なら下から10人（逆順にして取得）
            target_list = sorted_stats[:10] if is_top else list(reversed(sorted_stats))[:10]

            embed = discord.Embed(title=title, color=color)
            
            for idx, (u_id, data) in enumerate(target_list, start=1):
                member = interaction.guild.get_member(u_id)
                name = member.display_name if member else f"ユーザー({u_id})"
                
                # 実際の順位の計算
                actual_rank = idx if is_top else len(sorted_stats) - idx + 1
                
                vc_time = data.get("vc_minutes", 0)
                text_count = data.get("text_chars", 0)
                
                embed.add_field(
                    name=f"{actual_rank}位: {name}",
                    value=f"⏱ VC時間: {vc_time}分 / 💬 文字数: {text_count}文字",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed)
            return

        # 2. ユーザー個人の戦績表示の処理
        # ターゲットユーザーが指定されていない場合はコマンド実行者にする
        target_user = user or interaction.user
        user_id = target_user.id

        if user_id not in stats_data:
            await interaction.response.send_message(f"{target_user.mention} のデータが見つかりませんでした。", ephemeral=True)
            return

        # ユーザーの順位を取得
        user_rank = 1
        for u_id, _ in sorted_stats:
            if u_id == user_id:
                break
            user_rank += 1

        user_data = stats_data[user_id]
        vc_time = user_data.get("vc_minutes", 0)
        text_count = user_data.get("text_chars", 0)

        embed = discord.Embed(
            title=f"📊 {target_user.display_name} の戦績リポート",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="サーバー内総合順位", value=f"**{user_rank}** 位 / {len(sorted_stats)}人中", inline=False)
        embed.add_field(name="⏱ トータルVC時間", value=f"{vc_time} 分", inline=True)
        embed.add_field(name="💬 トータル入力文字数", value=f"{text_count} 文字", inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    cog = Communicate(bot)
    await bot.add_cog(cog)

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
        # 実行者に権限があるか確認
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

        # 指定チャンネルの履歴からユーザーの最新メッセージを1件取得
        target_msg: Optional[discord.Message] = None
        # 効率のためlimitを設けて検索
        async for msg in channel.history(limit=2000):
            if msg.author.id == user.id:
                target_msg = msg
                break

        if not target_msg:
            await interaction.followup.send(f"{user.mention} のメッセージが指定チャンネルで見つかりませんでした。", ephemeral=True)
            return

        # メッセージの整形
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
    # Cogをロード
    cog = Communicate(bot)
    await bot.add_cog(cog)

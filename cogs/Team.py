import discord
from discord import app_commands
from discord.ext import commands
import random
import re

class Team(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def get_sort_priority(self, name: str) -> int:
        """
        ソート順の優先度を返す:
        0: ひらがな・カタカナ
        1: アルファベット
        2: その他
        """
        if not name:
            return 2
        
        first_char = name[0]
        # ひらがな (\u3040-\u309F) or カタカナ (\u30A0-\u30FF)
        if re.match(r'[\u3040-\u30ff]', first_char):
            return 0
        # アルファベット (a-zA-Z)
        if re.match(r'[a-zA-Z]', first_char):
            return 1
        # その他
        return 2

    @app_commands.command(name="team", description="VC内のメンバーをチーム分けします")
    @app_commands.describe(num="チーム数")
    @app_commands.rename(num="チーム数")
    async def team_split(self, interaction: discord.Interaction, num: int):
        if num <= 0:
            await interaction.response.send_message("チーム数は1以上にしてください。", ephemeral=True)
            return

        # 実行者がVCにいるか確認
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("ボイスチャンネルに入った状態で実行してください。", ephemeral=True)
            return

        vc = interaction.user.voice.channel
        # Botを除外してメンバーリストを取得
        members = [m for m in vc.members if not m.bot]

        if len(members) < num:
            await interaction.response.send_message(f"メンバー数がチーム数（{num}）より少ないため分けられません。", ephemeral=True)
            return

        # ランダムにシャッフル
        random.shuffle(members)

        # チーム分け
        teams = [[] for _ in range(num)]
        for i, member in enumerate(members):
            teams[i % num].append(member)

        # レスポンスの構築
        embed = discord.Embed(
            title="チーム分け結果",
            color=discord.Color.green(),
            description=f"対象チャンネル: {vc.name}\nメンバー数: {len(members)}名"
        )

        for i, team_members in enumerate(teams):
            team_name = f"チーム {chr(65 + i)}" # チーム A, B, C...
            
            # メンバーを指定の順序でソート
            # 【ひらがな・カタカナ＞アルファベット＞その他】
            sorted_members = sorted(
                team_members, 
                key=lambda m: (self.get_sort_priority(m.display_name), m.display_name.lower())
            )
            
            member_list_str = "\n".join([f"・{m.display_name}" for m in sorted_members])
            embed.add_field(name=team_name, value=member_list_str, inline=False)

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Team(bot))

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import os

# CSVから選択肢を汎用的に読み込む関数（mode, rule用）
def load_choices_from_csv(filename: str):
    choices = []
    if not filename.endswith(".csv"):
        filename += ".csv"
    csv_path = os.path.join("CSV", filename)
    
    if not os.path.exists(csv_path):
        return [app_commands.Choice(name=f"ファイルが見つかりません ({filename})", value="none")]

    try:
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
            for line in lines:
                name = line.strip()
                if name:
                    choices.append(app_commands.Choice(name=name, value=name))
    except Exception as e:
        print(f"CSV読み込みエラー ({filename}): {e}")
        return [app_commands.Choice(name="読み込みエラー", value="error")]
    
    return choices if choices else [app_commands.Choice(name="選択肢が空です", value="empty")]


# ステージ用のオートコンプリート関数
async def stage_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    stages = []
    csv_path = os.path.join("CSV", "Spl3_stage_buttle.csv")
    
    if not os.path.exists(csv_path):
        return [app_commands.Choice(name="ステージファイルが見つかりません", value="none")]
        
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
            for line in lines:
                stage_name = line.strip()
                if stage_name:
                    # ユーザーが入力中の文字(current)が含まれるもの、または入力が空のときに対象とする
                    if current.lower() in stage_name.lower():
                        stages.append(app_commands.Choice(name=stage_name, value=stage_name))
    except Exception as e:
        print(f"ステージオートコンプリートエラー: {e}")
        return []

    # Discordの仕様上、返せる候補は最大25個まで
    return stages[:25]


class Splatoon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # /スプラ3 グループ
    splatoon_group = app_commands.Group(name="スプラ3", description="スプラトゥーン3に関するコマンドです")

    @splatoon_group.command(name="募集", description="スプラトゥーン3のメンバーを募集します")
    @app_commands.describe(
        mode="募集するモードを選択してください",
        rule="ルールを選択してください",
        人数="募集する人数を入力してください（例: 3）",
        stage1="1つ目のステージを選択してください",
        stage2="2つ目のステージを選択してください（任意）"
    )
    @app_commands.choices(
        mode=load_choices_from_csv("Spl3_mode.csv"),
        rule=load_choices_from_csv("Spl3_rule.csv")
    )
    async def recruit(
        self, 
        interaction: discord.Interaction, 
        mode: app_commands.Choice[str], 
        rule: app_commands.Choice[str], 
        人数: int, 
        stage1: str,
        stage2: Optional[str] = None
    ):
        # 選択・入力された値を取得
        mode_name = mode.name
        rule_name = rule.name
        
        # ステージ表示の整形（stage2がある場合は「ステージ1 〜 ステージ2」にする）
        if stage2:
            stage_display = f"{stage1} 〜 {stage2}"
        else:
            stage_display = stage1
        
        # 埋め込みメッセージの作成
        embed = discord.Embed(
            title="🎮 スプラ3 メンバー募集！",
            description=f"{interaction.user.mention} がメンバーを募集しています！\n参加したい方はVCへどうぞ！",
            color=discord.Color.blue()
        )
        
        # 募集内容をフィールドに分けて見やすく整理
        embed.add_field(name="📝 モード", value=mode_name, inline=True)
        embed.add_field(name="🏆 ルール", value=rule_name, inline=True)
        embed.add_field(name="👥 募集人数", value=f"**{人数}** 人", inline=True)
        embed.add_field(name="🗺️ ステージ", value=stage_display, inline=False)
        
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)

    # stage1の引数にオートコンプリートを適用
    @recruit.autocomplete("stage1")
    async def recruit_stage1_autocomplete(self, interaction: discord.Interaction, current: str):
        return await stage_autocomplete(interaction, current)

    # stage2の引数にオートコンプリートを適用
    @recruit.autocomplete("stage2")
    async def recruit_stage2_autocomplete(self, interaction: discord.Interaction, current: str):
        return await stage_autocomplete(interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(Splatoon(bot))

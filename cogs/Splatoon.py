import discord
from discord import app_commands
from discord.ext import commands
import os

# CSVから選択肢を読み込む関数（クラスの外で定義）
def get_mode_choices():
    modes = []
    # main.py から見た相対パス
    csv_path = os.path.join("CSV", "Spl3_mode.csv")
    
    if not os.path.exists(csv_path):
        return [app_commands.Choice(name="CSVファイルが見つかりません", value="none")]

    try:
        # utf-8-sig を使うことで、BOM付きのCSVも読み込めるようにします
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
            for line in lines:
                name = line.strip()
                if name:
                    # name と value に同じ値を設定
                    modes.append(app_commands.Choice(name=name, value=name))
    except Exception as e:
        print(f"CSV読み込みエラー: {e}")
        return [app_commands.Choice(name="読み込みエラー", value="error")]
    
    return modes if modes else [app_commands.Choice(name="選択肢が空です", value="empty")]

class Splatoon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # /スプラ3 グループ
    splatoon_group = app_commands.Group(name="スプラ3", description="スプラトゥーン3に関するコマンドです")

    @splatoon_group.command(name="募集", description="スプラトゥーン3のメンバーを募集します")
    @app_commands.describe(mode="募集するモードを選択してください")
    @app_commands.choices(mode=get_mode_choices()) # ここで直接読み込んだリストを渡す
    async def recruit(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        # Choiceオブジェクトを受け取るように変更
        mode_name = mode.name
        
        embed = discord.Embed(
            title="🎮 スプラ3 メンバー募集！",
            description=f"**モード:** {mode_name}\n\n{interaction.user.mention} がメンバーを募集しています！\n参加したい方はVCへどうぞ！",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Splatoon(bot))

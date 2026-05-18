import discord
from discord import app_commands
from discord.ext import commands
import os

# CSVから選択肢を汎用的に読み込む関数
def load_choices_from_csv(filename: str):
    choices = []
    # CSVフォルダ内のパスを生成（拡張子がない場合は .csv を付与）
    if not filename.endswith(".csv"):
        filename += ".csv"
    csv_path = os.path.join("CSV", filename)
    
    if not os.path.exists(csv_path):
        return [app_commands.Choice(name=f"ファイルが見つかりません ({filename})", value="none")]

    try:
        # utf-8-sig を使うことで、BOM付きのCSVも読み込めるようにします
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
            for line in lines:
                name = line.strip()
                if name:
                    # 選択肢の表示名と値を設定（上限25個制限に注意）
                    choices.append(app_commands.Choice(name=name, value=name))
    except Exception as e:
        print(f"CSV読み込みエラー ({filename}): {e}")
        return [app_commands.Choice(name="読み込みエラー", value="error")]
    
    return choices if choices else [app_commands.Choice(name="選択肢が空です", value="empty")]

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
        stage="ステージを選択してください"
    )
    # それぞれのCSVファイルから選択肢を動的に読み込み
    @app_commands.choices(
        mode=load_choices_from_csv("Spl3_mode.csv"),
        rule=load_choices_from_csv("Spl3_rule.csv"),
        stage=load_choices_from_csv("Spl3_stage_buttle.csv")
    )
    async def recruit(
        self, 
        interaction: discord.Interaction, 
        mode: app_commands.Choice[str], 
        rule: app_commands.Choice[str], 
        人数: int, 
        stage: app_commands.Choice[str]
    ):
        # 選択された値を取得
        mode_name = mode.name
        rule_name = rule.name
        stage_name = stage.name
        
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
        embed.add_field(name="🗺️ ステージ", value=stage_name, inline=False)
        
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Splatoon(bot))

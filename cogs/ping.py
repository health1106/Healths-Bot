from discord import app_commands
from discord.ext import commands

class Ping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="疎通確認用のコマンドです")
    async def ping(self, interaction):
        if not self.bot.is_authorized(interaction):
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return
        await interaction.response.send_message("Pong!")

async def setup(bot):
    await bot.add_cog(Ping(bot))

import discord
from discord import app_commands
from discord.ext import commands

class ColorCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------
    # コマンド1: カラー変更コマンド
    # ---------------------------------------------------------
    @app_commands.command(
        name="color_set",
        description="名前の色をHEXコード（例: #00ffff）で自由に変更するにゃ！",
    )
    @app_commands.describe(hex_code="設定したい色のHEXコード（例: #FF0000 や #00FFFF）を指定するにゃ")
    async def color_set(self, interaction: discord.Interaction, hex_code: str):
        try:
            clean_hex = hex_code.lstrip("#")
            color_value = discord.Color(int(clean_hex, 16))
        except ValueError:
            await interaction.response.send_message(
                "❌ 正しいカラーコードを入力するにゃ～。（例: #FF0000）",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        member = interaction.user
        role_name = f"color-{member.id}" 

        await interaction.response.defer(
            ephemeral=True
        ) 

        role = discord.utils.get(guild.roles, name=role_name)

        try:
            if role:
                # 既存のロールがあれば、色だけ更新
                await role.edit(color=color_value)
                message = f"🎨 名前の色を `{hex_code}` に更新したにゃ！"
            else:
                # なければ新しく作る
                role = await guild.create_role(name=role_name, color=color_value)
                await member.add_roles(role)
                message = f"🎨 名前の色を `{hex_code}` に更新したにゃ！"

            bot_member = guild.me
            bot_highest_role = bot_member.top_role
            await role.edit(position=bot_highest_role.position - 1)

            await interaction.followup.send(message, ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send(
                "権限が足りないにゃ。Botのロールをサーバー設定で一番上に移動するにゃ。",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"エラーが発生したにゃ: {e}", ephemeral=True
            )

    # ---------------------------------------------------------
    # コマンド2: カラー解除コマンド (/color reset)
    # ---------------------------------------------------------
    @app_commands.command(
        name="color_reset", description="設定した名前の色を消して元に戻しすにゃ"
    )
    async def color_reset(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        role_name = f"color-{member.id}"

        role = discord.utils.get(guild.roles, name=role_name)

        if role:
            await role.delete()
            await interaction.response.send_message(
                "カラーロールを削除して、名前の色を元に戻したにゃ。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "色変更ロールが設定されていないにゃ！", ephemeral=True
            )


# main.pyからこのファイルを読み込むための関数
async def setup(bot: commands.Bot):
    await bot.add_cog(ColorCog(bot))

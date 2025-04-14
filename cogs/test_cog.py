# cogs/test_cog.py (Cog再読み込みコマンド修正)
from discord.ext import commands
from discord import app_commands
import discord
import logging
import asyncio

logger = logging.getLogger(__name__)

# ★ Cogリストの定義を削除 ★

class TestCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Botの応答を確認します")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! ({latency}ms)", ephemeral=True)

    @app_commands.command(name="reload_cogs", description="すべてのCogを再読み込みします (オーナー限定)")
    @commands.is_owner() # Botオーナーのみ実行可能
    async def reload_cogs(self, interaction: discord.Interaction):
        """すべてのCogを再読み込みするコマンド"""
        await interaction.response.defer(ephemeral=True)
        logger.warning(f"Reloading all cogs initiated by {interaction.user} (ID: {interaction.user.id})")

        reload_results = []
        # ★ Botオブジェクトの属性からCogリストを取得 ★
        if not hasattr(self.bot, 'initial_extensions') or not self.bot.initial_extensions:
            await interaction.followup.send("エラー: 再読み込み対象のCogリストが見つかりません。", ephemeral=True)
            logger.error("Could not find initial_extensions attribute on bot object.")
            return

        for extension in self.bot.initial_extensions:
            try:
                await self.bot.reload_extension(extension)
                logger.info(f"Successfully reloaded extension {extension}")
                reload_results.append(f"✅ {extension}")
            except commands.ExtensionNotLoaded:
                try:
                    await self.bot.load_extension(extension)
                    logger.info(f"Successfully loaded extension {extension} (was not loaded)")
                    reload_results.append(f"🆗 {extension} (Loaded)")
                except Exception as e:
                    logger.error(f"Failed to load extension {extension}", exc_info=e)
                    reload_results.append(f"❌ {extension} (Load failed: {type(e).__name__})")
            except Exception as e:
                logger.error(f"Failed to reload extension {extension}", exc_info=e)
                reload_results.append(f"❌ {extension} (Reload failed: {type(e).__name__})")

        # スラッシュコマンドの再同期
        try:
            logger.info("Syncing commands after reloading cogs...")
            # 少し待機してから同期 (Cogのロード完了を待つため)
            await asyncio.sleep(1)
            synced = await self.bot.tree.sync()
            sync_message = f"\nSynced {len(synced)} command(s)."
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            sync_message = f"\n⚠️ Command sync failed: {e}"
            logger.error("Failed to sync commands after reload", exc_info=e)

        await interaction.followup.send(f"Cog reload results:\n" + "\n".join(reload_results) + sync_message, ephemeral=True)

    @reload_cogs.error
    async def reload_cogs_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """/reload_cogs コマンドのエラーハンドリング"""
        if isinstance(error, commands.NotOwner): # エラータイプを修正
            await interaction.followup.send("このコマンドはBotのオーナーのみ実行できます。", ephemeral=True)
        else:
            logger.error("An error occurred in /reload_cogs command", exc_info=error)
            # is_done() でチェックしてから応答を試みる
            if not interaction.response.is_done():
                 await interaction.response.send_message(f"コマンドの実行中にエラーが発生しました: {error}", ephemeral=True)
            else:
                 await interaction.followup.send(f"コマンドの実行中にエラーが発生しました: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TestCog(bot))
    logger.info("TestCog setup complete.")
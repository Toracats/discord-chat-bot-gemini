# main.py (SummarizeCog を追加)

import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import logging
from utils import config_manager

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger(__name__)

# 環境変数をロード
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    logger.critical("DISCORD_BOT_TOKEN not found in environment variables.")
    exit()

# Botの初期化
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Cogのリスト
INITIAL_EXTENSIONS = [
    'cogs.config_cog',
    'cogs.chat_cog',
    'cogs.history_cog',
    'cogs.random_dm_cog',
    'cogs.processing_cog',
    'cogs.test_cog',
    'cogs.weather_mood_cog',
    'cogs.summarize_cog', # ★ 追加
]
bot.initial_extensions = INITIAL_EXTENSIONS

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info('------')

    try:
        config_manager.load_all_configs()
    except Exception as e:
         logger.critical("Failed to load initial configurations!", exc_info=e)
         # await bot.close() # 必要なら
         # return

    # Cog のロード
    for extension in bot.initial_extensions:
        try:
            await bot.load_extension(extension)
            logger.info(f'Successfully loaded extension {extension}')
        except Exception as e:
            logger.error(f'Failed to load extension {extension}.', exc_info=e)

    # スラッシュコマンドの同期
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error("Failed to sync commands", exc_info=e)

    await bot.change_presence(activity=discord.Game(name="with Generative AI"))

async def main():
    # ★ Botインスタンスをループで参照可能にする（SummarizeCog取得用）
    loop = asyncio.get_running_loop()
    loop._bot_instance = bot
    async with bot:
        await bot.start(DISCORD_BOT_TOKEN)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt.")
    except Exception as e:
        logger.critical("Unhandled exception during bot execution", exc_info=e)
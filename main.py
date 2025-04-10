import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import logging
from utils import config_manager

# ロギング設定
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger(__name__)

# 環境変数をロード
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Botの初期化
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # ニックネーム取得等に必要になる可能性
bot = commands.Bot(command_prefix="!", intents=intents) # Prefixは現状不要だが念のため

# Cogのリスト
INITIAL_EXTENSIONS = [
    'cogs.config_cog',
    'cogs.chat_cog',
    'cogs.history_cog',
    'cogs.random_dm_cog',
    'cogs.processing_cog',
    'cogs.test_cog',
    'cogs.weather_mood_cog',
]

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info('------')

    # ★ Cogロード前に設定をロード
    try:
        config_manager.load_all_configs()
    except Exception as e:
         logger.critical("Failed to load initial configurations!", exc_info=e)
         # 必要であれば Bot を停止するなどの処理
         # await bot.close()
         # return

    # Cog のロード
    for extension in INITIAL_EXTENSIONS:
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

    # Botの状態をPlayingに設定（任意）
    await bot.change_presence(activity=discord.Game(name="with Generative AI"))

async def main():
    async with bot:
        await bot.start(DISCORD_BOT_TOKEN)

if __name__ == '__main__':
    asyncio.run(main())
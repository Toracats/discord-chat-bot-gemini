# bot_core.py (stop_event修正・Bot再作成対応 - 完全版)

import discord
from discord.ext import commands
import asyncio
import logging
import threading
from typing import Optional, Dict, Any
# config_manager をインポート
from utils import config_manager

# PubSub送信用のヘルパー関数を定義
def publish_status(status: str, message: Optional[str] = None):
    """BotのステータスをPubSubで送信する"""
    try:
        # log_forwarder が pub をインポートしている前提
        from utils.log_forwarder import pub # 関数内でインポート
        if pub:
            payload = {"status": status, "message": message}
            # sendMessage はスレッドセーフと仮定
            pub.sendMessage("bot_status_update", payload=payload)
            # logging.getLogger("GUI_Updater").debug(f"Status published: {payload}")
        else:
             logging.warning("PubSub not available, cannot publish status.")
    except Exception as e:
        logging.error(f"Failed to publish status update", exc_info=e)

logger = logging.getLogger(__name__)
bot: Optional[commands.Bot] = None # 現在アクティブなBotインスタンス (スレッドごとに再作成される)
# stop_event = asyncio.Event() # グローバル変数としては削除

INITIAL_EXTENSIONS = [
    'cogs.config_cog',
    'cogs.chat_cog',
    'cogs.history_cog',
    'cogs.random_dm_cog',
    'cogs.processing_cog',
    'cogs.test_cog',
    'cogs.weather_mood_cog',
    'cogs.summarize_cog',
]

async def initialize_bot() -> Optional[commands.Bot]:
    """Botインスタンスを毎回新しく初期化し、Cogをロードする"""
    global bot
    # ★ Botインスタンスを毎回新しく作成
    intents = discord.Intents.default(); intents.message_content = True; intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
        logger.info('------')
        logger.info("Loading extensions...")
        loaded_extensions = []; failed_extensions = []
        for extension in INITIAL_EXTENSIONS: # クラス外のリストを参照
            try: await bot.load_extension(extension); logger.info(f'Loaded: {extension}'); loaded_extensions.append(extension)
            except Exception as e: logger.error(f'Failed load: {extension}.', exc_info=True); failed_extensions.append(extension)
        if failed_extensions: logger.warning(f"Failed extensions: {failed_extensions}")
        if loaded_extensions:
            logger.info("Syncing slash commands...")
            try: synced = await bot.tree.sync(); logger.info(f"Synced {len(synced)} command(s)")
            except discord.errors.Forbidden: logger.error("Sync fail: Bot lacks 'applications.commands' scope.")
            except Exception as e: logger.error("Sync fail", exc_info=e)
        else: logger.info("No extensions loaded, skipping sync.")
        try: await bot.change_presence(activity=discord.Game(name="with Generative AI")); logger.info("Presence updated.")
        except Exception as e: logger.error("Presence set fail", exc_info=e)
        try: loop = asyncio.get_running_loop(); loop._bot_instance = bot; logger.info("Set bot instance on loop.")
        except Exception as e: logger.error("Set bot instance fail", exc_info=e)
        logger.info("Bot is ready.")
        publish_status("ready") # ★準備完了通知

    logger.info("Bot initialization function complete (listeners registered).")
    return bot

async def run_bot_until_stopped(token: str, stop_event_local: asyncio.Event): # ★ stop_event を引数で受け取る
    """Botを起動し、stop_event_localがセットされるまで実行する"""
    global bot
    if not bot: logger.error("Bot not initialized."); publish_status("error", "Bot not initialized"); return
    if not token: logger.error("Token missing."); publish_status("error", "Token missing"); return

    try:
        logger.info("Attempting to log in and run..."); publish_status("connecting")
        # ★ ローカルの stop_event を使う
        start_task = asyncio.create_task(bot.start(token), name="discord_bot_start")
        wait_task = asyncio.create_task(stop_event_local.wait(), name="stop_event_wait")
        done, pending = await asyncio.wait({start_task, wait_task}, return_when=asyncio.FIRST_COMPLETED)

        if wait_task in done: logger.info("Stop event received. Shutting down gracefully...")
        elif start_task in done:
            logger.warning("Bot task finished unexpectedly.");
            try: await start_task
            except discord.errors.LoginFailure: logger.error("Login failed: Improper token."); publish_status("error", "Login failed: Invalid token")
            except discord.errors.PrivilegedIntentsRequired: logger.error("Login failed: Intents not enabled."); publish_status("error", "Intents not enabled")
            except Exception as e: logger.error("Exception during bot exec:", exc_info=e); publish_status("error", f"Runtime error: {e}")

        await stop_bot_runner(stop_event_local) # ★ stop_event を渡す
    except asyncio.CancelledError: logger.info("run_bot_until_stopped cancelled."); await stop_bot_runner(stop_event_local); raise # ★ stop_event を渡す
    except Exception as e: logger.critical("Unhandled exception run_bot_until_stopped", exc_info=e); publish_status("critical_error", f"Unhandled exception: {e}"); await stop_bot_runner(stop_event_local) # ★ stop_event を渡す

async def stop_bot_runner(stop_event_local: asyncio.Event): # ★ stop_event を引数で受け取る
    """Botの接続を閉じ、インスタンスを破棄する"""
    global bot
    publish_status("stopping") # 先に通知
    stop_event_local.set() # ★ 渡されたイベントをセット (念のため)
    if bot and not bot.is_closed():
        logger.info("Closing Discord connection...");
        try: await bot.close(); logger.info("Discord connection closed.")
        except Exception as e: logger.error("Error during bot.close()", exc_info=e); publish_status("error", f"Error closing: {e}")
    else: logger.info("Bot not running or already closed.")
    logger.info("Bot runner signaled to stop completely.");
    # ★ botインスタンスをNoneに戻す (次の起動で新しいインスタンスを作るため)
    bot = None
    publish_status("stopped") # 最後に停止完了を通知

_bot_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()
_active_stop_event: Optional[asyncio.Event] = None # 現在アクティブな stop_event

def start_bot_thread() -> bool:
    """Botを別スレッドで起動する"""
    global _bot_thread, _active_stop_event, bot # bot も変更する可能性があるため global宣言
    with _thread_lock:
        if _bot_thread and _bot_thread.is_alive(): logger.warning("Bot thread already running."); return False
        token = config_manager.get_discord_token()
        if not token: logger.error("Cannot start: Token not found."); publish_status("error", "Token missing"); return False

        async def main_async_runner(stop_event_for_thread: asyncio.Event):
            """スレッド内で実行されるメインの非同期処理"""
            global bot # グローバルのbotインスタンスを操作する
            bot_instance = await initialize_bot() # ★ 毎回初期化
            if bot_instance:
                await run_bot_until_stopped(token, stop_event_for_thread) # ★ stop_event を渡す
            else: logger.error("Failed to initialize bot."); publish_status("error", "Initialization failed")

        def thread_target():
            """スレッドで実行される関数"""
            global _active_stop_event
            publish_status("starting")
            logger.info("Bot thread starting..."); new_loop = None
            try:
                new_loop = asyncio.new_event_loop(); asyncio.set_event_loop(new_loop)
                # ★ 新しいイベントループ内で stop_event を作成
                local_stop_event = asyncio.Event()
                _active_stop_event = local_stop_event # ★ アクティブなイベントとして記録
                new_loop.run_until_complete(main_async_runner(local_stop_event)) # ★ 作成したイベントを渡す
            except Exception as e: logger.critical("Exception in bot thread target:", exc_info=e); publish_status("critical_error", f"Thread exception: {e}")
            finally:
                 _active_stop_event = None # ★ 終了したらクリア
                 if new_loop:
                    try: logger.info("Closing event loop in bot thread..."); new_loop.run_until_complete(new_loop.shutdown_asyncgens()); new_loop.close(); logger.info("Event loop closed.")
                    except Exception as loop_close_e: logger.error("Error closing loop in thread:", exc_info=loop_close_e)
                 logger.info("Bot thread finished.")

        _bot_thread = threading.Thread(target=thread_target, name="DiscordBotThread", daemon=True)
        _bot_thread.start(); logger.info("Bot thread created and started."); return True

def signal_stop_bot():
    """外部からBotの停止を要求する"""
    global _bot_thread, _active_stop_event
    with _thread_lock:
        if not _bot_thread or not _bot_thread.is_alive(): logger.info("Stop ignored: Thread not running."); return
        logger.info("Signaling bot thread to stop...")
        if _active_stop_event:
             try:
                  # イベントが属するループを取得 (内部属性アクセス注意)
                  loop = asyncio.get_running_loop() # _active_stop_event._loop は不安定な場合があるため現在のループを取得試行
                  if loop.is_running(): # ループがまだ動いていれば
                       loop.call_soon_threadsafe(_active_stop_event.set) # ★ スレッドセーフにセット
                       logger.info("Stop event set via threadsafe call.")
                  else: # ループが終了している場合は直接セットを試みる
                       logger.warning("Target event loop is not running. Attempting direct set.")
                       _active_stop_event.set()
             except RuntimeError: # get_running_loop() がループを見つけられない場合など
                  logger.warning("Could not get running loop for stop event. Attempting direct set.")
                  try: _active_stop_event.set() # フォールバック
                  except Exception as e_set: logger.error(f"Failed to set stop_event directly: {e_set}")
             except Exception as e:
                  logger.error(f"Failed to set stop_event threadsafe: {e}. Falling back to direct set.")
                  try: _active_stop_event.set()
                  except Exception as e_set2: logger.error(f"Failed to set stop_event directly (fallback): {e_set2}")
        else:
             logger.warning("Could not find active stop event to set.")

def is_bot_running() -> bool:
     """Botスレッドが現在実行中かどうかを返す"""
     with _thread_lock: return _bot_thread is not None and _bot_thread.is_alive()
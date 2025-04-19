# bot_core.py (threading.Event 使用、動的反映対応 - 完全版)

import discord
from discord.ext import commands
import asyncio
import logging
import threading # ★ threading をインポート
from typing import Optional, Dict, Any
# config_manager をインポートして最新設定を取得
from utils import config_manager

# PubSub送信用のヘルパー関数
def publish_status(status: str, message: Optional[str] = None):
    """BotのステータスをPubSubで送信する"""
    try:
        from utils.log_forwarder import pub
        if pub:
            payload = {"status": status, "message": message}
            pub.sendMessage("bot_status_update", payload=payload)
        else:
             logger.warning("PubSub not available, cannot publish status.")
    except Exception as e:
        logger.error(f"Failed to publish status update", exc_info=e)

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG) # デバッグ時にコメント解除

bot: Optional[commands.Bot] = None # 現在アクティブなBotインスタンス
# ★ asyncio.Event の代わりに threading.Event を使用
_stop_event_thread = threading.Event()

INITIAL_EXTENSIONS = [
    'cogs.config_cog', 'cogs.chat_cog', 'cogs.history_cog', 'cogs.random_dm_cog',
    'cogs.processing_cog', 'cogs.test_cog', 'cogs.weather_mood_cog', 'cogs.summarize_cog',
]

async def initialize_bot() -> Optional[commands.Bot]:
    """Botインスタンスを毎回新しく初期化し、Cogをロードする"""
    global bot
    logger.info("Initializing new Bot instance...")
    intents = discord.Intents.default(); intents.message_content = True; intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
        logger.info('------')
        logger.info("Loading extensions...")
        loaded_extensions = []; failed_extensions = []
        for extension in INITIAL_EXTENSIONS:
            try:
                await bot.load_extension(extension); logger.info(f'Loaded: {extension}'); loaded_extensions.append(extension)
            except commands.ExtensionNotFound: logger.error(f"Ext not found: {extension}"); failed_extensions.append(extension)
            except commands.ExtensionAlreadyLoaded: logger.warning(f"Ext already loaded: {extension}")
            except commands.NoEntryPointError: logger.error(f"No setup func in: {extension}"); failed_extensions.append(extension)
            except commands.ExtensionFailed as e: logger.error(f'Failed load: {extension}. Reason: {e.original if e.original else e}', exc_info=False); failed_extensions.append(extension)
            except Exception as e: logger.error(f'Unexpected error loading: {extension}.', exc_info=True); failed_extensions.append(extension)

        # === 必須Cogのロード失敗チェック ===
        if failed_extensions:
             logger.warning(f"Failed to load some extensions: {failed_extensions}")
             required_cogs = ['cogs.chat_cog']
             missing_required = [cog for cog in required_cogs if cog in failed_extensions]
             if missing_required:
                first_failed_cog = missing_required[0].split('.')[-1]
                error_msg = f"{first_failed_cog} の初期化失敗 (APIキー未設定？)"
                logger.critical(f"必須Cogロード失敗: {missing_required}. Botは正常機能不可.")
                publish_status("critical_error", error_msg)
                # ★★★ Botの接続を閉じて終了処理を促す ★★★
                logger.warning("Closing bot connection due to critical cog failure...")
                await bot.close() # これにより run_bot_until_stopped も終了するはず
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★
                return # on_ready 処理中断

        # === Cogロード成功時の処理 ===
        if loaded_extensions:
            logger.info("Syncing slash commands...")
            try: synced = await bot.tree.sync(); logger.info(f"Synced {len(synced)} command(s)")
            except discord.errors.Forbidden: logger.error("Sync fail: 'applications.commands' scope missing?")
            except Exception as e: logger.error("Sync fail", exc_info=e)
        else:
            logger.warning("No extensions loaded successfully.")
            publish_status("error", "Cogが一つもロードされませんでした")
            return

        try: await bot.change_presence(activity=discord.Game(name="with Generative AI")); logger.info("Presence updated.")
        except Exception as e: logger.error("Presence set fail", exc_info=e)
        try: loop = asyncio.get_running_loop(); loop._bot_instance = bot; logger.info("Set bot instance on loop.")
        except Exception as e: logger.error("Set bot instance fail", exc_info=e)

        logger.info("Bot is ready.")
        publish_status("ready")

    logger.info("Bot initialization function complete (listeners registered).")
    return bot

# ★ stop_event_local 引数を削除
async def run_bot_until_stopped(token: str):
    """Botを起動し、_stop_event_threadがセットされるまで実行する"""
    global bot, _stop_event_thread
    if not bot: logger.error("Bot not initialized."); publish_status("error", "Bot not initialized"); return
    if not token: logger.error("Token missing."); publish_status("error", "Token missing"); return

    try:
        logger.info("Attempting to log in and run..."); publish_status("connecting")
        start_task = asyncio.create_task(bot.start(token), name="discord_bot_start")

        # ★ threading.Event をポーリングでチェック
        while not _stop_event_thread.is_set():
            if start_task.done():
                logger.warning("Bot task finished unexpectedly before stop signal.")
                break
            await asyncio.sleep(0.2) # ポーリング間隔

        # ループを抜けたら (停止要求 or タスク完了)
        if _stop_event_thread.is_set():
            logger.info("Stop event received. Shutting down gracefully...")
            if not start_task.done():
                start_task.cancel()
                try: await asyncio.wait_for(start_task, timeout=5.0)
                except asyncio.TimeoutError: logger.warning("Bot task cancel timeout.")
                except asyncio.CancelledError: logger.info("Bot task cancelled.")
                except Exception as e: logger.error(f"Exception cancelling task: {e}")
        # else: # start_task が先に完了した場合のログは done() 内で
        #     pass

        # 停止処理 (start_taskの結果を待つ必要はない)
        if start_task.done():
             try: await start_task # 例外が発生していればここで捕捉・ログ出力
             except discord.errors.LoginFailure: logger.error("Login failed: Improper token."); publish_status("error", "Login failed: Invalid token")
             except discord.errors.PrivilegedIntentsRequired: logger.error("Login failed: Intents not enabled."); publish_status("error", "Intents not enabled")
             except asyncio.CancelledError: pass # キャンセルは正常
             except Exception as e: logger.error("Exception during bot exec:", exc_info=e); publish_status("error", f"Runtime error: {e}")

        await stop_bot_runner() # ★ stop_event 引数削除

    except asyncio.CancelledError:
         logger.info("run_bot_until_stopped cancelled.");
         await stop_bot_runner() # ★ stop_event 引数削除
         # raise # キャンセルは上位に伝えない場合もある
    except Exception as e:
         logger.critical("Unhandled exception run_bot_until_stopped", exc_info=e)
         publish_status("critical_error", f"Unhandled exception: {e}")
         await stop_bot_runner() # ★ stop_event 引数削除

# ★ stop_event_local 引数を削除
async def stop_bot_runner():
    """Botの接続を閉じ、インスタンスを破棄する"""
    global bot, _stop_event_thread
    publish_status("stopping")
    _stop_event_thread.set() # ★ threading.Event をセット
    if bot and not bot.is_closed():
        logger.info("Closing Discord connection...");
        try:
            loaded_cogs = list(bot.extensions.keys())
            for extension in loaded_cogs:
                 try: await bot.unload_extension(extension); logger.info(f"Unloaded: {extension}")
                 except Exception as e_unload: logger.warning(f"Failed unload {extension}", exc_info=e_unload)
            await bot.close(); logger.info("Discord connection closed.")
        except Exception as e: logger.error("Error during bot.close()", exc_info=e); publish_status("error", f"Error closing: {e}")
    else: logger.info("Bot not running or already closed.")
    logger.info("Bot runner signaled to stop completely.");
    bot = None # インスタンス参照をクリア
    publish_status("stopped") # 停止完了を通知

_bot_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()
# _active_stop_event は不要になったので削除

def start_bot_thread() -> bool:
    """現在の設定値でBotを別スレッドで起動する"""
    global _bot_thread, bot, _stop_event_thread # stop_event を global に追加
    with _thread_lock:
        if _bot_thread and _bot_thread.is_alive(): logger.warning("Bot thread already running."); return False

        # ★★★ 起動直前に設定をリロード ★★★
        try: config_manager.reload_primary_config()
        except Exception as e_reload:
             logger.error("Failed reload config", exc_info=e_reload); publish_status("error", "設定リロード失敗"); return False

        discord_token = config_manager.get_discord_token()
        if not discord_token: logger.error("Cannot start: Token not found."); publish_status("error", "Token missing"); return False

        _stop_event_thread.clear() # ★ 開始前に threading.Event をクリア

        async def main_async_runner(): # ★ stop_event 引数削除
            global bot # グローバル bot を使う
            bot_instance = await initialize_bot() # 引数なし
            if bot_instance:
                await run_bot_until_stopped(discord_token) # ★ stop_event 引数削除
            else: logger.error("Failed to initialize bot."); publish_status("error", "Initialization failed")

        def thread_target():
            publish_status("starting")
            logger.info("Bot thread starting..."); new_loop = None
            try:
                new_loop = asyncio.new_event_loop(); asyncio.set_event_loop(new_loop)
                # ★ local_stop_event 作成は不要
                new_loop.run_until_complete(main_async_runner()) # ★ 引数なし
            except Exception as e: logger.critical("Exception in bot thread target:", exc_info=e); publish_status("critical_error", f"Thread exception: {e}")
            finally:
                 # ★ _active_stop_event クリアは不要
                 if new_loop:
                    try: logger.info("Closing event loop..."); new_loop.run_until_complete(new_loop.shutdown_asyncgens()); new_loop.close(); logger.info("Event loop closed.")
                    except Exception as loop_close_e: logger.error("Error closing loop:", exc_info=loop_close_e)
                 logger.info("Bot thread finished.")

        _bot_thread = threading.Thread(target=thread_target, name="DiscordBotThread", daemon=True)
        _bot_thread.start(); logger.info("Bot thread created and started."); return True

def signal_stop_bot():
    """外部からBotの停止を要求する"""
    global _bot_thread, _stop_event_thread # ★ _active_stop_event 削除
    with _thread_lock:
        if not _bot_thread or not _bot_thread.is_alive(): logger.info("Stop ignored: Thread not running."); return
        logger.info("Signaling bot thread to stop...")
        _stop_event_thread.set() # ★ threading.Event を直接セット (スレッドセーフ)
        logger.info("Stop event set.")

def is_bot_running() -> bool:
     """Botスレッドが現在実行中かどうかを返す"""
     with _thread_lock: return _bot_thread is not None and _bot_thread.is_alive()
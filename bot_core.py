# bot_core.py (main.pyの内容を移植)

import discord
from discord.ext import commands
import asyncio
import logging
import threading # threading をインポート
from typing import Optional # Optional をインポート
# config_manager をインポート (utilsパッケージから)
from utils import config_manager

logger = logging.getLogger(__name__)
bot: Optional[commands.Bot] = None
stop_event = asyncio.Event() # Botループを外部から停止するためのイベント

# Cogのリスト (main.pyから移動)
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

async def initialize_bot() -> Optional[commands.Bot]: # ★ 引数 token は削除
    """Botインスタンスを初期化し、Cogをロードする"""
    global bot
    if bot and bot.is_ready():
        logger.warning("Bot is already initialized and ready.")
        return bot
    if bot and bot.is_ws_ratelimited():
         logger.warning("Bot is rate limited. Waiting...")
         # レート制限時の再試行ロジックが必要ならここに追加
         return None # または適切な待機処理

    # Botの初期化 (main.py から移動)
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    # ★ コマンドプレフィックスは一旦固定にするか、configから取るように後で変更
    bot = commands.Bot(command_prefix="!", intents=intents)
    # ★ bot.initial_extensions は initialize_bot 関数外で定義済
    # bot.initial_extensions = INITIAL_EXTENSIONS # この行は不要

    @bot.event
    async def on_ready():
        """Bot準備完了時の処理 (main.py から移動・調整)"""
        logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
        logger.info('------')

        # ★ config_manager.load_all_configs() は main_gui.py 起動時に呼ぶ想定のため、ここでは呼ばない。
        # ★ config_manager は既にロードされている前提とする。

        # Cog のロード (main.py の on_ready 内から移動)
        logger.info("Loading extensions...")
        loaded_extensions = []
        failed_extensions = []
        for extension in INITIAL_EXTENSIONS: # 関数外のリストを参照
            try:
                await bot.load_extension(extension)
                logger.info(f'Successfully loaded extension {extension}')
                loaded_extensions.append(extension)
            except Exception as e:
                logger.error(f'Failed to load extension {extension}.', exc_info=e)
                failed_extensions.append(extension)

        if failed_extensions:
             logger.warning(f"Failed to load some extensions: {failed_extensions}")
             # ここでエラーをGUIに通知する処理を追加しても良い

        # スラッシュコマンドの同期 (main.py の on_ready 内から移動)
        if loaded_extensions: # Cogが少なくとも一つロードされたら同期を試みる
            logger.info("Syncing slash commands...")
            try:
                synced = await bot.tree.sync()
                logger.info(f"Synced {len(synced)} command(s)")
            except discord.errors.Forbidden:
                 logger.error("Failed to sync commands: Bot lacks 'applications.commands' scope in the guild(s).")
                 # GUIに通知
            except Exception as e:
                logger.error("Failed to sync commands", exc_info=e)
                # GUIに通知
        else:
             logger.info("No extensions loaded, skipping command sync.")

        # Presence の設定 (main.py の on_ready 内から移動)
        try:
            await bot.change_presence(activity=discord.Game(name="with Generative AI"))
            logger.info("Presence updated.")
        except Exception as e:
            logger.error("Failed to set presence", exc_info=e)

        # ★ ループへのBotインスタンス設定 (main.py の main 関数から移動)
        # これは SummarizeCog などが bot インスタンスを必要とする場合の回避策？
        # より良い方法があるかもしれないが、一旦そのまま移植
        try:
            loop = asyncio.get_running_loop()
            loop._bot_instance = bot
            logger.info("Set bot instance on running loop.")
        except Exception as e:
             logger.error("Failed to set bot instance on loop", exc_info=e)

        logger.info("Bot is ready.")
        # ===> GUI側に準備完了を通知する処理を追加 (例: PubSub経由) <===
        # publish_status("ready")

    logger.info("Bot initialization function complete (listeners registered).")
    return bot

async def run_bot_until_stopped(token: str):
    """Botを起動し、stop_eventがセットされるまで実行する"""
    global bot
    if not bot:
        logger.error("Bot is not initialized. Call initialize_bot first.")
        # ===> GUIにエラー通知 <===
        # publish_status("error", "Bot not initialized")
        return

    if not token:
        logger.error("Discord token is missing. Cannot start bot.")
        # ===> GUIにエラー通知 <===
        # publish_status("error", "Token missing")
        return

    try:
        logger.info("Attempting to log in and run the bot...")
        # ===> GUIにステータス通知 <===
        # publish_status("connecting")

        # bot.start() は内部でログインと on_ready 発火を行う
        start_task = asyncio.create_task(bot.start(token), name="discord_bot_start")
        wait_task = asyncio.create_task(stop_event.wait(), name="stop_event_wait")

        done, pending = await asyncio.wait(
            {start_task, wait_task},
            return_when=asyncio.FIRST_COMPLETED
        )

        if wait_task in done:
            logger.info("Stop event received. Shutting down the bot gracefully...")
            if start_task in pending:
                # Botがまだ動いている可能性があるのでキャンセルを試みる
                logger.info("Cancelling the main bot task...")
                start_task.cancel()
                # キャンセルが完了するのを少し待つ (任意)
                try:
                    await asyncio.wait_for(start_task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Bot task did not cancel within timeout.")
                except asyncio.CancelledError:
                    logger.info("Bot task successfully cancelled.")
                except Exception as e: # start_taskが他の例外で終わる可能性
                    logger.error(f"Exception while cancelling bot task: {e}", exc_info=True)

        elif start_task in done:
            logger.warning("Bot task finished unexpectedly (e.g., connection closed, kicked).")
            # start_task が正常終了することは通常ないはず (内部ループのため)
            # 例外が発生したか確認
            try:
                await start_task # 例外があればここで再送出される
            except discord.errors.LoginFailure:
                 logger.error("Login failed: Improper token provided.")
                 # ===> GUIにエラー通知 <===
                 # publish_status("error", "Login failed: Invalid token")
            except discord.errors.PrivilegedIntentsRequired:
                 logger.error("Login failed: Privileged intents (Members/Presence) are not enabled on the Developer Portal.")
                 # ===> GUIにエラー通知 <===
                 # publish_status("error", "Intents not enabled")
            except Exception as e:
                logger.error("Exception during bot execution:", exc_info=e)
                # ===> GUIにエラー通知 <===
                # publish_status("error", f"Runtime error: {e}")

        # --- 停止処理 ---
        # run_bot_until_stopped が終了する際には必ず停止処理を呼ぶ
        await stop_bot_runner() # wait_task完了時も、start_task完了時も呼ぶ

    except asyncio.CancelledError:
        # run_bot_until_stopped自体がキャンセルされた場合
        logger.info("run_bot_until_stopped task was cancelled.")
        await stop_bot_runner() # クリーンアップ実行
        raise # キャンセルを上位に伝える
    except Exception as e:
        logger.critical("Unhandled exception in run_bot_until_stopped", exc_info=e)
        # ===> GUIに重大エラー通知 <===
        # publish_status("critical_error", f"Unhandled exception: {e}")
        await stop_bot_runner() # クリーンアップ試行

async def stop_bot_runner():
    """Botの接続を閉じ、関連リソースを解放する"""
    global bot
    # ★ if 条件を修正 ★
    # botが存在し、かつ is_closed() が False (まだ閉じられていない) 場合に close() を試みる
    if bot and not bot.is_closed():
        logger.info("Closing Discord connection...")
        # ...(GUIへのステータス通知)...
        try:
            await bot.close() # Discord接続を切断
            logger.info("Discord connection closed.")
        except Exception as e:
            logger.error("Error during bot.close()", exc_info=e)
            # ===> GUIにエラー通知 <===
            # publish_status("error", f"Error closing: {e}")
    else:
        # bot が None の場合、または既に is_closed() が True の場合
        logger.info("Bot was not ready/running or already closed.")

    # botインスタンスをクリアするかどうかは設計による
    # bot = None
    stop_event.set() # イベントを確実にセット
    logger.info("Bot runner signaled to stop completely.")
    # ===> GUIにステータス通知 <===
    # publish_status("stopped")

# --- スレッド実行関連 ---
_bot_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()

def start_bot_thread() -> bool:
    """Botを別スレッドで起動する。成功すればTrueを返す。"""
    global _bot_thread
    with _thread_lock: # 複数回同時に呼ばれないようにロック
        if _bot_thread and _bot_thread.is_alive():
            logger.warning("Bot thread is already running.")
            return False

        token = config_manager.get_discord_token()
        if not token:
            logger.error("Cannot start bot: Discord Token not found in config.")
            # ===> GUIにエラー通知 <===
            # publish_status("error", "Token missing")
            return False

        stop_event.clear() # 停止イベントをリセット

        async def main_async_runner():
            """スレッド内で実行されるメインの非同期処理"""
            # ここで config_manager.load_all_configs() を呼ぶべきか？
            # GUI起動時にロード済みなら不要。毎回最新を読み込むなら呼ぶ。
            # → GUI起動時にロードし、設定変更はGUIから反映する方針なので、ここでは呼ばない。
            bot_instance = await initialize_bot() # ★ token引数削除
            if bot_instance:
                await run_bot_until_stopped(token) # ★ tokenはこちらに渡す
            else:
                logger.error("Failed to initialize bot.")
                # ===> GUIにエラー通知 <===
                # publish_status("error", "Initialization failed")

        def thread_target():
            """スレッドで実行される関数"""
            logger.info("Bot thread starting...")
            # ===> GUIにステータス通知 <===
            # publish_status("starting")
            new_loop = None
            try:
                # スレッド内で新しいイベントループを作成して設定
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                new_loop.run_until_complete(main_async_runner())
            except Exception as e:
                logger.critical("Exception in bot thread target:", exc_info=e)
                # ===> GUIに重大エラー通知 <===
                # publish_status("critical_error", f"Thread exception: {e}")
            finally:
                if new_loop:
                    try:
                        # イベントループを閉じる (関連タスクのクリーンアップ)
                        logger.info("Closing event loop in bot thread...")
                        # 残っているタスクを確認してキャンセルする処理を追加するとより堅牢
                        # all_tasks = asyncio.all_tasks(loop=new_loop)
                        # ... cancel tasks ...
                        new_loop.run_until_complete(new_loop.shutdown_asyncgens())
                        new_loop.close()
                        logger.info("Event loop closed.")
                    except Exception as loop_close_e:
                         logger.error("Error closing event loop in thread:", exc_info=loop_close_e)
                logger.info("Bot thread finished.")
                # スレッド終了時にGUIに通知しても良い
                # publish_status("stopped") # stop_bot_runnerでも通知しているので重複注意

        _bot_thread = threading.Thread(target=thread_target, name="DiscordBotThread", daemon=True)
        _bot_thread.start()
        logger.info("Bot thread created and started.")
        return True

def signal_stop_bot():
    """外部 (GUI) からBotの停止を要求する"""
    global _bot_thread
    with _thread_lock:
        if not _bot_thread or not _bot_thread.is_alive():
            logger.info("Stop signal ignored: Bot thread is not running.")
            return

        logger.info("Signaling bot thread to stop...")
        stop_event.set() # これで run_bot_until_stopped 内の待機が解除される

        # スレッドが終了するのを待つか、タイムアウトを設定するかはオプション
        # _bot_thread.join(timeout=10) # 例: 10秒待つ
        # if _bot_thread.is_alive():
        #     logger.warning("Bot thread did not stop within timeout.")

def is_bot_running() -> bool:
     """Botスレッドが現在実行中かどうかを返す"""
     with _thread_lock:
         return _bot_thread is not None and _bot_thread.is_alive()

# ===> ここにGUI連携用の PubSub 送信関数などを定義する (例) <===
# def publish_status(status: str, message: Optional[str] = None):
#     try:
#         from pubsub import pub # Flet推奨のpubsubライブラリを使う場合など
#         payload = {"status": status, "message": message}
#         # メインスレッドで実行されるようにスケジュールする
#         # Fletの page.run_in_thread() やそれに類する機能を使うか、
#         # pub.sendMessage がスレッドセーフなら直接呼ぶ
#         pub.sendMessage("bot_status_update", payload=payload)
#         logger.debug(f"Published status update: {payload}")
#     except Exception as e:
#         logger.error(f"Failed to publish status update", exc_info=e)
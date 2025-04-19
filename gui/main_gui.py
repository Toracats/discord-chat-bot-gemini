# gui/main_gui.py (async main, await load)

import flet as ft
import sys
import os
import logging
import logging.handlers
from pathlib import Path
import threading
from typing import Optional, Dict, Any
import asyncio # ★ asyncio をインポート

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG) # デバッグ時に設定

try: from pubsub import pub
except ImportError: print("Error: PyPubSub not found. pip install pypubsub"); pub = None

# --- プロジェクトルート ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.append(PROJECT_ROOT)
print(f"Project Root added to sys.path: {PROJECT_ROOT}")

# --- Bot Core と Config Manager ---
try:
    from utils import config_manager
    import bot_core
    from utils.log_forwarder import PubSubLogHandler, start_log_forwarding, stop_log_forwarding
except ImportError as e: print(f"Error importing modules: {e}"); sys.exit(1)
except Exception as general_e: print(f"Unexpected error during import: {general_e}"); sys.exit(1)

# --- ロギング設定 ---
try:
    LOG_FILE_PATH = config_manager.LOG_FILE; LOG_FORMAT = '%(asctime)s:%(levelname)s:%(name)s: %(message)s'; LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    root_logger = logging.getLogger()
    if not root_logger.hasHandlers():
        root_logger.setLevel(logging.INFO); formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        console_handler = logging.StreamHandler(sys.stdout); console_handler.setFormatter(formatter); console_handler.setLevel(logging.INFO); root_logger.addHandler(console_handler)
        try:
            LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
            file_handler.setFormatter(formatter); file_handler.setLevel(logging.DEBUG); root_logger.addHandler(file_handler); logger.info(f"File logging initialized: {LOG_FILE_PATH}")
        except Exception as e_file_log: logger.error(f"Failed file logging!", exc_info=e_file_log)
        if pub: pubsub_handler = PubSubLogHandler(); pubsub_handler.setFormatter(formatter); pubsub_handler.setLevel(logging.INFO); root_logger.addHandler(pubsub_handler); logger.info("PubSub log handler initialized.")
        else: logger.warning("PyPubSub not found, GUI log forwarding disabled.")
        logging.getLogger("flet").setLevel(logging.INFO); logging.getLogger("flet_core").setLevel(logging.INFO)
except Exception as e_log_init: print(f"CRITICAL ERROR during logging setup: {e_log_init}"); logging.basicConfig(level=logging.INFO); logging.critical(f"Logging setup failed: {e_log_init}", exc_info=True)

# --- ★★★ Flet アプリケーションの main 関数を async に変更 ★★★ ---
async def main(page: ft.Page):
    page.title = f"{config_manager.APP_NAME} Controller"; page.window_width = 800; page.window_height = 600
    page.vertical_alignment = ft.MainAxisAlignment.START; page.horizontal_alignment = ft.CrossAxisAlignment.START
    page.fonts = {
    "Noto Sans JP": "fonts/NotoSansJP-Regular.ttf",
    "Bebas Neue": "fonts/BebasNeue-Regular.ttf"
}
    page.theme = ft.Theme(font_family="Noto Sans JP"); page.dark_theme = ft.Theme(font_family="Noto Sans JP")

    is_in_critical_error_state = threading.Event(); is_in_critical_error_state.clear()

    # --- コントロール参照 (初期値は空またはデフォルト) ---
    status_icon = ft.Icon("fiber_manual_record", color="red", tooltip="停止中")
    status_text = ft.Text("初期化中...", size=16, weight=ft.FontWeight.BOLD) # ★ 初期表示変更
    start_stop_button = ft.ElevatedButton("起動", icon="play_arrow_rounded", tooltip="Botを起動します", disabled=True) # ★ 最初は無効
    log_output_list = ft.ListView(expand=True, spacing=5, auto_scroll=True, divider_thickness=1)
    MAX_LOG_LINES = 500
    log_panel = ft.ExpansionPanelList( expand_icon_color="amber", elevation=4, divider_color="amber_100",
        controls=[ft.ExpansionPanel( can_tap_header=True, header=ft.ListTile(title=ft.Text("ログ表示エリア")),
            content=ft.Container( content=log_output_list, padding=ft.padding.only(top=10, bottom=10, left=15, right=15), border_radius=5, height=300, border=ft.border.all(1, ft.colors.with_opacity(0.26, ft.colors.BLACK)) ) # ★ Colors修正
        )]
    )
    token_field = ft.TextField(label="Discord Bot Token", password=True, can_reveal_password=True, width=450, value="")
    gemini_api_key_field = ft.TextField(label="Gemini API Key", password=True, can_reveal_password=True, width=450, value="")
    weather_api_key_field = ft.TextField(label="OpenWeatherMap API Key (Optional)", password=True, can_reveal_password=True, width=450, value="")
    delete_password_field = ft.TextField(label="History Delete Password (Optional)", password=True, can_reveal_password=True, width=450, value="")
    weather_auto_update_switch = ft.Switch(label="天気自動更新を有効にする", value=False) # ★ 初期値False
    weather_interval_field = ft.TextField(label="自動更新間隔 (分)", value="", width=150, keyboard_type=ft.KeyboardType.NUMBER, input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]+"), tooltip="最低10分")
    save_button = ft.ElevatedButton("設定を保存", icon="save_rounded", disabled=True) # ★ 最初は無効
    status_snackbar = ft.SnackBar(content=ft.Text(""), open=False)
    loading_indicator = ft.ProgressRing(visible=True) # ★ 最初は表示

    # --- PubSub リスナー (ログ用) ---
    def on_log_message_received(log_entry: str):
        if page.client_storage is None: return # ページがまだ準備できていない場合
        if isinstance(log_entry, str):
             log_output_list.controls.append(ft.Text(log_entry, selectable=True, size=12, font_family="Consolas, monospace"))
             if len(log_output_list.controls) > MAX_LOG_LINES: del log_output_list.controls[0]
             try: page.update()
             except Exception as e: logger.warning(f"Error updating page for log: {e}")
        else: logger.warning(f"Received non-string log entry via PubSub: {type(log_entry)}")

    # --- GUI状態更新用関数 ---
    def update_gui_status(status: str, message: Optional[str] = None):
        if page.client_storage is None: return # ページがまだ準備できていない場合
        logger.debug(f"Updating GUI Status: {status}, Message: {message}")
        current_color = "red"; current_text = "停止中"; current_tooltip = "停止中"
        button_text = "起動"; button_icon = "play_arrow_rounded"; button_enabled = True

        if is_in_critical_error_state.is_set() and status != "critical_error":
            logger.debug(f"Maintaining critical error state despite receiving status: {status}")
            snackbar_msg = getattr(status_snackbar.content, 'value', '設定確認要') if status_snackbar.content else '設定確認要'
            tooltip_msg = snackbar_msg[:100] + ('...' if len(snackbar_msg) > 100 else '')
            current_color = "red"; current_text = "重大エラー"; current_tooltip = f"重大エラー: {tooltip_msg}";
            button_text = "エラー"; button_icon = "error_outline_rounded"; button_enabled = False
        elif status == "starting": current_color = "orange"; current_text = "起動処理中..."; current_tooltip = "Bot起動中..."; button_text = "処理中..."; button_icon = "hourglass_empty_rounded"; button_enabled = False; is_in_critical_error_state.clear()
        elif status == "connecting": current_color = "orange"; current_text = "Discord接続中..."; current_tooltip = "Discord接続中..."; button_text = "処理中..."; button_icon = "hourglass_empty_rounded"; button_enabled = False
        elif status == "ready": current_color = "green"; current_text = "動作中"; current_tooltip = "Bot動作中"; button_text = "停止"; button_icon = "stop_rounded"; button_enabled = True; is_in_critical_error_state.clear()
        elif status == "stopping": current_color = "orange"; current_text = "停止処理中..."; current_tooltip = "Bot停止中..."; button_text = "処理中..."; button_icon = "hourglass_empty_rounded"; button_enabled = False
        elif status == "stopped":
             if is_in_critical_error_state.is_set():
                  current_color = "red"; current_text = "エラー(停止済)"; snackbar_msg = getattr(status_snackbar.content, 'value', '設定を確認'); tooltip_msg = snackbar_msg[:100] + ('...' if len(snackbar_msg) > 100 else ''); current_tooltip = f"エラーのため停止: {tooltip_msg}"
                  button_text = "起動"; button_icon = "play_arrow_rounded"; button_enabled = True
             else: current_color = "red"; current_text = "停止中"; current_tooltip = "Bot停止中"; button_text = "起動"; button_icon = "play_arrow_rounded"; button_enabled = True; is_in_critical_error_state.clear()
        elif status == "error":
             current_color = "red"; current_text = "エラー発生"; current_tooltip = f"エラー: {message or '不明'}"; button_text = "起動"; button_icon = "play_arrow_rounded"; button_enabled = True; is_in_critical_error_state.clear()
             status_snackbar.content = ft.Text(f"エラー: {message or '不明'}"); status_snackbar.bgcolor = ft.colors.RED_700; status_snackbar.open = True # ★ Colors修正
        elif status == "critical_error":
             current_color = "red"; current_text = "重大エラー"; current_tooltip = f"重大エラー: {message or '不明'}. 設定確認要"; button_text = "エラー"; button_icon = "error_outline_rounded"; button_enabled = False; is_in_critical_error_state.set()
             status_snackbar.content = ft.Text(f"重大エラー: {message or '不明'}. 設定確認/保存要"); status_snackbar.bgcolor = ft.colors.RED_700; status_snackbar.open = True # ★ Colors修正
        else: logger.warning(f"Unknown status: {status}"); return

        status_icon.color = current_color; status_icon.tooltip = current_tooltip; status_text.value = current_text
        start_stop_button.text = button_text; start_stop_button.icon = button_icon; start_stop_button.disabled = not button_enabled
        try: page.update()
        except Exception as e: logger.warning(f"Failed page update in update_gui_status: {e}")

    # --- PubSub リスナー (Botステータス用) ---
    def on_bot_status_update(payload: Dict[str, Any]):
        if page.client_storage is None: return # ページ準備前
        logger.debug(f"Received bot_status_update payload: {payload}")
        status = payload.get("status"); message = payload.get("message")
        if status: update_gui_status(status, message)
        else: logger.warning(f"Invalid status update payload: {payload}")

    # --- PubSubの購読設定 ---
    if pub:
        try: pub.subscribe(on_log_message_received, 'log_message'); logger.info("Subscribed to 'log_message'."); pub.subscribe(on_bot_status_update, 'bot_status_update'); logger.info("Subscribed to 'bot_status_update'.")
        except Exception as e_pubsub_sub: logger.error("Failed pubsub subscribe", exc_info=e_pubsub_sub)
    else: logger.warning("PubSub not available.")

    # --- ログ転送スレッド開始 ---
    log_forward_thread = start_log_forwarding()

    # --- イベントハンドラ ---
    def save_settings_clicked(e):
        # (変更なし)
        logger.info("Save settings button clicked.")
        token = token_field.value.strip() if token_field.value else None; gemini_key = gemini_api_key_field.value.strip() if gemini_api_key_field.value else None
        weather_key = weather_api_key_field.value.strip() if weather_api_key_field.value else None; delete_pass = delete_password_field.value.strip() if delete_password_field.value else None
        weather_enabled = weather_auto_update_switch.value; weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
        try:
             weather_interval_str = weather_interval_field.value.strip() if weather_interval_field.value else ""; interval_val = int(weather_interval_str) if weather_interval_str.isdigit() else -1
             weather_interval = max(10, interval_val) if interval_val >= 1 else config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
             if weather_interval_field.value != str(weather_interval): weather_interval_field.value = str(weather_interval)
        except Exception as e_interval: logger.warning(f"Err interval: {e_interval}"); weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES; weather_interval_field.value = str(weather_interval)
        logger.debug(f"Saving: WeatherEnabled={weather_enabled}, Interval={weather_interval}")
        save_successful = False
        try:
            config_manager.app_config.setdefault("secrets", {})["discord_token"] = token; config_manager.app_config.setdefault("secrets", {})["gemini_api_key"] = gemini_key
            config_manager.app_config.setdefault("secrets", {})["weather_api_key"] = weather_key; config_manager.app_config.setdefault("secrets", {})["delete_history_password"] = delete_pass
            config_manager.update_weather_auto_update_enabled_in_memory(weather_enabled); config_manager.update_weather_auto_update_interval_in_memory(weather_interval)
            config_manager.save_app_config() # 保存は同期でOK
            is_in_critical_error_state.clear(); save_successful = True
            current_bot = getattr(bot_core, 'bot', None)
            if current_bot:
                 weather_cog = current_bot.get_cog("WeatherMoodCog")
                 if weather_cog and hasattr(weather_cog, 'update_auto_update_task_status'):
                     try: weather_cog.update_auto_update_task_status(); logger.info("Notified WeatherCog.")
                     except Exception as e_notify: logger.error("Error notifying WeatherCog", exc_info=e_notify)
                 elif bot_core.is_bot_running(): logger.warning("WeatherCog not found.")
            else: logger.info("Bot not running, WeatherCog task update deferred.")
            status_snackbar.content = ft.Text("設定を保存しました。"); status_snackbar.bgcolor = ft.colors.GREEN_700; logging.info("Settings saved.") # ★ Colors修正
        except Exception as e_save: logger.error("Failed to save settings...", exc_info=e_save); status_snackbar.content = ft.Text(f"保存エラー: {e_save}"); status_snackbar.bgcolor = ft.colors.RED_700 # ★ Colors修正
        if save_successful:
            logger.info("Settings saved successfully, updating GUI status based on current bot state.")
            if bot_core.is_bot_running(): update_gui_status("ready", "設定保存完了")
            else: update_gui_status("stopped", "設定保存完了")
        status_snackbar.open = True
        page.update() # ★ スナックバー表示のために update

    def start_stop_clicked(e):
        # (変更なし)
        is_running = bot_core.is_bot_running(); logger.info(f"Start/Stop clicked! Thread alive: {is_running}")
        if is_running:
            logger.info("Attempting to stop bot..."); update_gui_status("stopping")
            try: bot_core.signal_stop_bot()
            except Exception as e_stop: logger.error("Error signaling stop", exc_info=e_stop); update_gui_status("error", f"停止信号エラー: {e_stop}")
        else:
            if is_in_critical_error_state.is_set():
                logger.warning("Bot is in critical error state. Save settings first."); status_snackbar.content = ft.Text("重大エラー状態です。設定を保存してください。"); status_snackbar.bgcolor = ft.colors.ORANGE_700; status_snackbar.open = True; page.update(); return # ★ Colors修正
            logger.info("Attempting to start bot..."); update_gui_status("starting")
            try:
                success = bot_core.start_bot_thread() # スレッド開始は同期的
                if not success: logger.error("Failed start bot thread."); update_gui_status("error", "スレッド起動失敗")
            except Exception as e_start: logger.error("Error starting bot thread", exc_info=e_start); update_gui_status("critical_error", f"スレッド起動エラー: {e_start}")

    # --- 初期値読み込みと適用 (非同期化) ---
    async def load_initial_settings_and_apply():
        logger.info("Loading initial settings async...")
        error_message = None
        try:
            logger.debug("[GUI] Calling load_all_configs_async...")
            await config_manager.load_all_configs_async()
            logger.debug("[GUI] load_all_configs_async finished.")

            # config_manager から最新の値を取得してGUIに設定
            token_field.value = config_manager.get_discord_token() or ""
            gemini_api_key_field.value = config_manager.get_gemini_api_key() or ""
            weather_api_key_field.value = config_manager.get_weather_api_key() or ""
            delete_password_field.value = config_manager.get_delete_history_password() or ""
            weather_auto_update_switch.value = config_manager.get_weather_auto_update_enabled()
            weather_interval_field.value = str(config_manager.get_weather_auto_update_interval())

            logger.info("Initial settings loaded async and applied to GUI.")
            save_button.disabled = False
            start_stop_button.disabled = False
            status_text.value = "停止中" # ★ 初期化完了後のステータス

        except Exception as e_load:
            logger.error("Error loading initial settings async", exc_info=e_load)
            error_message = f"設定の読み込みに失敗: {e_load}. 設定ファイルを直接確認してください。"
            save_button.disabled = False # エラーでも保存はできるようにする
            start_stop_button.disabled = True # エラー時は起動不可
            status_text.value = "設定読込エラー"

        finally:
            loading_indicator.visible = False
            if error_message:
                status_snackbar.content = ft.Text(error_message)
                status_snackbar.bgcolor = ft.colors.RED_700
                status_snackbar.open = True

            page.update() # ★ 最終的なGUI状態を反映

    # --- アプリ終了処理 ---
    def on_window_event(e):
         # (変更なし)
         if e.data == "close":
             logger.info("Window close event received.")
             if pub: 
                 try: pub.unsubscribe(on_log_message_received, 'log_message'); pub.unsubscribe(on_bot_status_update, 'bot_status_update'); logger.info("Unsubscribed pubsub."); 
                 except Exception as unsub_e: logger.error(f"Error unsubscribing: {unsub_e}")
             if bot_core.is_bot_running(): logger.info("Signaling bot stop..."); bot_core.signal_stop_bot(); bot_thread = getattr(bot_core, '_bot_thread', None);
             if bot_thread and bot_thread.is_alive(): logger.info("Waiting for bot thread (max 5s)..."); bot_thread.join(timeout=5.0); logger.info(f"Bot thread alive after join: {bot_thread.is_alive()}")
             logger.info("Signaling log forwarder stop..."); stop_log_forwarding(); fwd_thread = log_forward_thread
             if fwd_thread and fwd_thread.is_alive(): logger.info("Waiting for log forwarder thread (max 2s)..."); fwd_thread.join(timeout=2.0); logger.info(f"Log forwarder alive after join: {fwd_thread.is_alive()}")
             logger.info("Exiting application."); page.window_destroy()

    page.window_prevent_close = True; page.on_window_event = on_window_event

    # --- イベントハンドラ割り当て ---
    save_button.on_click = save_settings_clicked; start_stop_button.on_click = start_stop_clicked

    # --- タブ作成 ---
    tabs = ft.Tabs( selected_index=0, animation_duration=300, expand=True,
        tabs=[
            ft.Tab( text="メイン", icon="home_rounded",
                content=ft.Container(padding=20, content=ft.Column( [
                    ft.Row([status_icon, status_text, loading_indicator], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10), # ★ loading_indicator をRowに追加
                    start_stop_button, ft.Divider(height=20), log_panel,
                ], spacing=15, scroll=ft.ScrollMode.AUTO))
            ),
            ft.Tab( text="基本設定", icon="settings_rounded",
                content=ft.Container(padding=20, content=ft.Column( [
                    token_field, gemini_api_key_field, weather_api_key_field, delete_password_field, ft.Divider(height=20),
                    ft.Text("天気自動更新設定", weight=ft.FontWeight.BOLD), weather_auto_update_switch, weather_interval_field, ft.Divider(height=30), save_button,
                ], spacing=20, horizontal_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO))
            ),
        ]
    )

    # --- ★★★ ページに要素を追加する前に非同期ロードを実行 ★★★ ---
    # 最初に最低限の要素（ローディング表示など）だけ表示する
    page.add(
        ft.Column([
            ft.Row([status_icon, status_text, loading_indicator], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            ft.Text("設定ファイルを読み込み中です..."),
        ])
    )
    await load_initial_settings_and_apply()

    # --- ★★★ ロード完了後にメインのUI要素を追加/更新 ★★★ ---
    page.clean() # 既存の要素をクリア
    page.add(tabs)
    page.add(status_snackbar)
    page.update() # 最終的なUIを表示

# --- アプリ実行 ---
if __name__ == "__main__":
    logger.info("Starting Flet application...")
    try:
        # ★ target を async 関数 main に変更 ★
        ft.app( target=main, view=ft.AppView.FLET_APP, assets_dir=os.path.join(PROJECT_ROOT, "gui/assets") )
    except Exception as e_app_start: logger.critical("Failed start Flet app", exc_info=e_app_start)
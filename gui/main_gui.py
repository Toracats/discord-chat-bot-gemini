# gui/main_gui.py (状態更新・DeprecationWarning修正 - 完全版)

import flet as ft
import sys
import os
import logging
import logging.handlers
from pathlib import Path
import threading
from typing import Optional, Dict, Any # ★ 型ヒント用

# pypubsub ライブラリが必要: pip install pypubsub
try:
    from pubsub import pub
except ImportError:
    print("Error: PyPubSub library not found. Please install it using: pip install pypubsub")
    pub = None

# --- プロジェクトルートをPythonパスに追加 ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.append(PROJECT_ROOT)
print(f"Project Root added to sys.path: {PROJECT_ROOT}")

# --- Bot Core と Config Manager のインポート ---
try:
    from utils import config_manager
    import bot_core
    from utils.log_forwarder import PubSubLogHandler, start_log_forwarding, stop_log_forwarding
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)
except Exception as general_e:
     print(f"An unexpected error occurred during import: {general_e}")
     sys.exit(1)


# --- ロギング設定 ---
try:
    LOG_FILE_PATH = config_manager.LOG_FILE
    LOG_FORMAT = '%(asctime)s:%(levelname)s:%(name)s: %(message)s'
    LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    root_logger = logging.getLogger()
    if not root_logger.hasHandlers(): # ハンドラがなければ基本設定を行う
         root_logger.setLevel(logging.INFO)
         formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
         # 1. Console Handler
         console_handler = logging.StreamHandler(sys.stdout)
         console_handler.setFormatter(formatter)
         console_handler.setLevel(logging.INFO)
         root_logger.addHandler(console_handler)
         # 2. File Handler
         try:
             LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
             file_handler = logging.handlers.RotatingFileHandler(
                 LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
             )
             file_handler.setFormatter(formatter)
             file_handler.setLevel(logging.DEBUG)
             root_logger.addHandler(file_handler)
             logging.info(f"File logging initialized. Log file: {LOG_FILE_PATH}")
         except Exception as e_file_log:
             logging.error(f"Failed to initialize file logging!", exc_info=e_file_log)
         # 3. PubSub Handler
         if pub:
             pubsub_handler = PubSubLogHandler()
             pubsub_handler.setFormatter(formatter)
             pubsub_handler.setLevel(logging.INFO)
             root_logger.addHandler(pubsub_handler)
             logging.info("PubSub log handler initialized.")
         else:
             logging.warning("PyPubSub not found, GUI log forwarding disabled.")
except Exception as e_log_init:
     print(f"CRITICAL ERROR during logging setup: {e_log_init}")
     logging.basicConfig(level=logging.INFO)
     logging.critical(f"Logging setup failed: {e_log_init}", exc_info=True)

# --- Flet アプリケーションの main 関数 ---
def main(page: ft.Page):
    # --- ページ設定 ---
    page.title = f"{config_manager.APP_NAME} Controller"
    page.window_width = 800; page.window_height = 600
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.START

    #--- フォント設定 ---
    page.fonts = {
        # "フォントファミリー名": "assetsディレクトリからの相対パス"
        "Noto Sans JP": "fonts/NotoSansJP-Regular.ttf" # 例: gui/assets/fonts/ に置いた場合
    }
    page.theme = ft.Theme(font_family="Noto Sans JP") # 登録したフォントファミリー名を指定
    page.dark_theme = ft.Theme(font_family="Noto Sans JP")

    # --- コントロール参照 ---
    status_icon = ft.Icon(ft.icons.CIRCLE, color="red", tooltip="停止中")
    status_text = ft.Text("停止中", size=16, weight=ft.FontWeight.BOLD)
    start_stop_button = ft.ElevatedButton("起動", icon=ft.icons.PLAY_ARROW_ROUNDED, tooltip="Botを起動します")
    log_output_list = ft.ListView(expand=True, spacing=5, auto_scroll=True, divider_thickness=1)
    MAX_LOG_LINES = 500
    log_panel = ft.ExpansionPanelList(
        expand_icon_color="amber", elevation=4, divider_color="amber_100",
        controls=[
            ft.ExpansionPanel(
                can_tap_header=True, header=ft.ListTile(title=ft.Text("ログ表示エリア (クリックで展開/格納)")),
                content=ft.Container(
                    content=log_output_list, padding=ft.padding.only(top=10, bottom=10, left=15, right=15),
                    border_radius=5, height=300, border=ft.border.all(1, ft.colors.with_opacity(0.26, "black"))
                )
            )
        ]
    )
    token_field = ft.TextField(label="Discord Bot Token", password=True, can_reveal_password=True, width=450)
    gemini_api_key_field = ft.TextField(label="Gemini API Key", password=True, can_reveal_password=True, width=450)
    weather_api_key_field = ft.TextField(label="OpenWeatherMap API Key (Optional)", password=True, can_reveal_password=True, width=450)
    delete_password_field = ft.TextField(label="History Delete Password (Optional)", password=True, can_reveal_password=True, width=450)
    weather_auto_update_switch = ft.Switch(label="天気自動更新を有効にする", value=True)
    weather_interval_field = ft.TextField(label="自動更新間隔 (分)", value="60", width=150, keyboard_type=ft.KeyboardType.NUMBER, input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]+"), tooltip="最低10分")
    save_button = ft.ElevatedButton("設定を保存", icon=ft.icons.SAVE_ROUNDED)
    status_snackbar = ft.SnackBar(content=ft.Text(""), open=False)

    # --- PubSub リスナー (ログ用) ---
    def on_log_message_received(log_entry: str):
        if isinstance(log_entry, str):
             log_output_list.controls.append(ft.Text(log_entry, selectable=True, size=12, font_family="Consolas, monospace"))
             if len(log_output_list.controls) > MAX_LOG_LINES: del log_output_list.controls[0]
             try: page.update()
             except Exception as e: print(f"Error updating page for log: {e}")
        else: logging.warning(f"Received non-string log entry via PubSub: {type(log_entry)}")

    # --- GUI状態更新用関数 ---
    def update_gui_status(status: str, message: Optional[str] = None):
        logging.debug(f"Updating GUI Status: {status}, Message: {message}")
        current_color = "red"; current_text = "停止中"; current_tooltip = "停止中"
        button_text = "起動"; button_icon = ft.icons.PLAY_ARROW_ROUNDED; button_enabled = True

        if status == "starting": current_color = "orange"; current_text = "起動処理中..."; current_tooltip = "Botを起動しています..."; button_text = "起動処理中..."; button_icon = ft.icons.HOURGLASS_EMPTY_ROUNDED; button_enabled = False
        elif status == "connecting": current_color = "orange"; current_text = "Discord接続中..."; current_tooltip = "Discordに接続しています..."; button_text = "起動処理中..."; button_icon = ft.icons.HOURGLASS_EMPTY_ROUNDED; button_enabled = False
        elif status == "ready": current_color = "green"; current_text = "動作中"; current_tooltip = "Botは正常に動作中です"; button_text = "停止"; button_icon = ft.icons.STOP_ROUNDED; button_enabled = True
        elif status == "stopping": current_color = "orange"; current_text = "停止処理中..."; current_tooltip = "Botを停止しています..."; button_text = "停止処理中..."; button_icon = ft.icons.HOURGLASS_EMPTY_ROUNDED; button_enabled = False
        elif status == "stopped": current_color = "red"; current_text = "停止中"; current_tooltip = "Botは停止しています"; button_text = "起動"; button_icon = ft.icons.PLAY_ARROW_ROUNDED; button_enabled = True
        elif status == "error":
             current_color = "red"; current_text = "エラー"; current_tooltip = f"エラー: {message or '詳細不明'}"; button_text = "起動"; button_icon = ft.icons.PLAY_ARROW_ROUNDED; button_enabled = True
             status_snackbar.content = ft.Text(f"エラー: {message or '不明なエラー'}"); status_snackbar.bgcolor = "red_700"; status_snackbar.open = True
        elif status == "critical_error":
             current_color = "red"; current_text = "重大エラー"; current_tooltip = f"重大エラー: {message or '詳細不明'}"; button_text = "起動"; button_icon = ft.icons.PLAY_ARROW_ROUNDED; button_enabled = True
             status_snackbar.content = ft.Text(f"重大エラー: {message or '不明'}. 再起動推奨"); status_snackbar.bgcolor = "red_700"; status_snackbar.open = True

        status_icon.color = current_color; status_icon.tooltip = current_tooltip; status_text.value = current_text
        start_stop_button.text = button_text; start_stop_button.icon = button_icon; start_stop_button.disabled = not button_enabled
        try: page.update()
        except Exception as e: logging.warning(f"Failed to update page in update_gui_status: {e}")

    # --- PubSub リスナー (Botステータス用) ---
    def on_bot_status_update(payload: Dict[str, Any]):
        status = payload.get("status"); message = payload.get("message")
        if status: update_gui_status(status, message)
        else: logging.warning(f"Received invalid status update payload: {payload}")

    # --- PubSubの購読設定 ---
    if pub:
        try:
            pub.subscribe(on_log_message_received, 'log_message'); logging.info("Subscribed to 'log_message'.")
            pub.subscribe(on_bot_status_update, 'bot_status_update'); logging.info("Subscribed to 'bot_status_update'.")
        except Exception as e_pubsub_sub: logging.error("Failed to subscribe pubsub topics", exc_info=e_pubsub_sub)
    else: logging.warning("PubSub not available.")

    # --- ログ転送スレッド開始 ---
    log_forward_thread = start_log_forwarding()

    # --- イベントハンドラ ---
    def save_settings_clicked(e):
        logging.info("Save settings button clicked.")
        token = token_field.value.strip() if token_field.value else None
        gemini_key = gemini_api_key_field.value.strip() if gemini_api_key_field.value else None
        weather_key = weather_api_key_field.value.strip() if weather_api_key_field.value else None
        delete_pass = delete_password_field.value.strip() if delete_password_field.value else None
        weather_enabled = weather_auto_update_switch.value
        weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
        try:
             weather_interval_str = weather_interval_field.value.strip() if weather_interval_field.value else ""
             if weather_interval_str.isdigit(): weather_interval = int(weather_interval_str)
             else: logging.warning(f"Invalid interval '{weather_interval_str}', using default."); weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
             weather_interval = max(10, weather_interval)
             if weather_interval_field.value != str(weather_interval): weather_interval_field.value = str(weather_interval)
        except Exception as e_interval:
             logging.warning(f"Error processing interval '{weather_interval_field.value}': {e_interval}. Using default.")
             weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES; weather_interval_field.value = str(weather_interval)
        logging.debug(f"Saving: WeatherEnabled={weather_enabled}, WeatherInterval={weather_interval}")
        try:
            config_manager.app_config.setdefault("secrets", {})["discord_token"] = token
            config_manager.app_config.setdefault("secrets", {})["gemini_api_key"] = gemini_key
            config_manager.app_config.setdefault("secrets", {})["weather_api_key"] = weather_key
            config_manager.app_config.setdefault("secrets", {})["delete_history_password"] = delete_pass
            config_manager.update_weather_auto_update_enabled_in_memory(weather_enabled)
            config_manager.update_weather_auto_update_interval_in_memory(weather_interval)
            config_manager.save_app_config()
            current_bot = getattr(bot_core, 'bot', None)
            if current_bot:
                 weather_cog = current_bot.get_cog("WeatherMoodCog")
                 if weather_cog and hasattr(weather_cog, 'update_auto_update_task_status'):
                     try: weather_cog.update_auto_update_task_status(); logging.info("Notified WeatherCog.")
                     except Exception as e_notify: logging.error("Error notifying WeatherCog", exc_info=e_notify)
                 elif bot_core.is_bot_running(): logging.warning("WeatherCog not found.")
            else: logging.info("Bot not running, WeatherCog task status update deferred.")
            status_snackbar.content = ft.Text("設定を保存し、天気タスクを更新しました。"); status_snackbar.bgcolor = "green_700"; logging.info("Settings saved.")
        except Exception as e_save:
            logging.error("Failed to save settings or update weather task", exc_info=e_save)
            status_snackbar.content = ft.Text(f"保存/適用エラー: {e_save}"); status_snackbar.bgcolor = "red_700"
        status_snackbar.open = True; page.update()

    def start_stop_clicked(e):
        is_running = bot_core.is_bot_running(); logging.info(f"Start/Stop clicked! Thread alive: {is_running}")
        if is_running:
            logging.info("Attempting to stop bot..."); update_gui_status("stopping")
            try: bot_core.signal_stop_bot()
            except Exception as e_stop: logging.error("Error signaling stop", exc_info=e_stop); update_gui_status("error", f"停止信号エラー: {e_stop}")
        else:
            logging.info("Attempting to start bot..."); update_gui_status("starting")
            try:
                success = bot_core.start_bot_thread()
                if not success: logging.error("Failed to start bot thread."); update_gui_status("error", "スレッド起動失敗")
            except Exception as e_start: logging.error("Error starting bot thread", exc_info=e_start); update_gui_status("critical_error", f"スレッド起動エラー: {e_start}")

    # --- 初期値読み込み ---
    def load_initial_settings():
        logging.info("Loading initial settings...")
        try:
            token_field.value = config_manager.get_discord_token() or ""
            gemini_api_key_field.value = config_manager.get_gemini_api_key() or ""
            weather_api_key_field.value = config_manager.get_weather_api_key() or ""
            delete_password_field.value = config_manager.get_delete_history_password() or ""
            weather_auto_update_switch.value = config_manager.get_weather_auto_update_enabled()
            weather_interval_field.value = str(config_manager.get_weather_auto_update_interval())
            logging.info("Initial settings loaded.")
        except Exception as e_load:
            logging.error("Error loading initial settings", exc_info=e_load)
            status_snackbar.content = ft.Text(f"設定読込エラー: {e_load}"); status_snackbar.open = True
            # page.update() は最後にまとめて

    # --- アプリ終了処理 ---
    def on_window_event(e):
         if e.data == "close":
             logging.info("Window close event received.")
             if pub:
                 try: pub.unsubscribe(on_log_message_received, 'log_message'); pub.unsubscribe(on_bot_status_update, 'bot_status_update'); logging.info("Unsubscribed pubsub topics.")
                 except Exception as unsub_e: logging.error(f"Error unsubscribing: {unsub_e}")
             if bot_core.is_bot_running():
                 logging.info("Signaling bot stop..."); bot_core.signal_stop_bot()
                 bot_thread = getattr(bot_core, '_bot_thread', None)
                 if bot_thread and bot_thread.is_alive(): logging.info("Waiting for bot thread (max 5s)..."); bot_thread.join(timeout=5.0); #...
             logging.info("Signaling log forwarder stop..."); stop_log_forwarding()
             fwd_thread = log_forward_thread
             if fwd_thread and fwd_thread.is_alive(): logging.info("Waiting for log forwarder thread (max 2s)..."); fwd_thread.join(timeout=2.0); #...
             logging.info("Exiting application."); page.window_destroy()

    page.window_prevent_close = True; page.on_window_event = on_window_event

    # --- イベントハンドラ割り当て ---
    save_button.on_click = save_settings_clicked; start_stop_button.on_click = start_stop_clicked

    # --- タブ作成 ---
    tabs = ft.Tabs(
        selected_index=0, animation_duration=300, expand=True,
        tabs=[
            ft.Tab(text="メイン", icon=ft.icons.HOME_ROUNDED,
                content=ft.Container(padding=20, content=ft.Column(
                    [ft.Row([status_icon, status_text], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                     start_stop_button, ft.Divider(height=20), log_panel,], spacing=15, scroll=ft.ScrollMode.AUTO))),
            ft.Tab(text="基本設定", icon=ft.icons.SETTINGS_ROUNDED,
                content=ft.Container(padding=20, content=ft.Column(
                     [token_field, gemini_api_key_field, weather_api_key_field, delete_password_field,
                      ft.Divider(height=20), ft.Text("天気自動更新設定", weight=ft.FontWeight.BOLD),
                      weather_auto_update_switch, weather_interval_field,
                      ft.Divider(height=30), save_button,], spacing=20, horizontal_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO))),
        ]
    )

    # --- ページ構成 ---
    page.add(tabs); page.add(status_snackbar)
    load_initial_settings(); page.update()

# --- アプリ実行 ---
if __name__ == "__main__":
    logging.info("Starting Flet application...")
    try:
        ft.app(
            target=main,
            view=ft.AppView.FLET_APP,
            assets_dir=os.path.join(PROJECT_ROOT, "gui/assets") # ★ assetsディレクトリを指定
        )
    except Exception as e_app_start: logging.critical("Failed start Flet app", exc_info=e_app_start)
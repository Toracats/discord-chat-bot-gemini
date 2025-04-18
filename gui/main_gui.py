# gui/main_gui.py (ログ機能実装済み)

import flet as ft
import sys
import os
import logging
import logging.handlers
from pathlib import Path
import threading # アプリ終了時のスレッド確認用 (任意)

# pypubsub ライブラリが必要: pip install pypubsub
try:
    from pubsub import pub
except ImportError:
    print("Error: PyPubSub library not found. Please install it using: pip install pypubsub")
    pub = None # pubsubがなければログ表示機能などが動作しない

# --- プロジェクトルートをPythonパスに追加 ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.append(PROJECT_ROOT)
print(f"Project Root added to sys.path: {PROJECT_ROOT}")

# --- Bot Core と Config Manager のインポート ---
try:
    from utils import config_manager
    import bot_core # bot_core.py がルートにあると仮定
    # ログフォワーダーをインポート
    from utils.log_forwarder import PubSubLogHandler, start_log_forwarding, stop_log_forwarding
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Please ensure gui/main_gui.py is in the correct location relative to utils/ and bot_core.py,")
    print("or adjust the sys.path modification.")
    sys.exit(1)
except Exception as general_e: # その他のインポートエラーも捕捉
     print(f"An unexpected error occurred during import: {general_e}")
     sys.exit(1)


# --- ロギング設定 ---
try:
    LOG_FILE_PATH = config_manager.LOG_FILE
    LOG_FORMAT = '%(asctime)s:%(levelname)s:%(name)s: %(message)s'
    LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

    root_logger = logging.getLogger()
    # 既存ハンドラがあればクリアする (複数回起動時の重複防止)
    # if root_logger.hasHandlers():
    #     root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO) # 最低レベルをINFOに (DEBUGにすると詳細すぎる可能性)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # 1. コンソールへの出力ハンドラ (デバッグ用)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO) # コンソールはINFO以上
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        root_logger.addHandler(console_handler)

    # 2. ファイルへの出力ハンドラ
    try:
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG) # ファイルにはDEBUG以上を記録
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root_logger.handlers):
            root_logger.addHandler(file_handler)
        logging.info(f"File logging initialized. Log file: {LOG_FILE_PATH}")
    except Exception as e_file_log:
        logging.error(f"Failed to initialize file logging!", exc_info=e_file_log)

    # 3. PubSub転送用ハンドラ (pubsubがインポートできていれば)
    if pub:
        pubsub_handler = PubSubLogHandler()
        pubsub_handler.setFormatter(formatter)
        pubsub_handler.setLevel(logging.INFO) # GUIにはINFO以上を転送
        if not any(isinstance(h, PubSubLogHandler) for h in root_logger.handlers):
            root_logger.addHandler(pubsub_handler)
        logging.info("PubSub log handler initialized.")
    else:
        logging.warning("PyPubSub not found, GUI log forwarding disabled.")

except Exception as e_log_init:
     print(f"CRITICAL ERROR during logging setup: {e_log_init}")
     # ロギング失敗は致命的な場合がある
     logging.basicConfig(level=logging.INFO) # 最低限のコンソールログは有効にする
     logging.critical(f"Logging setup failed: {e_log_init}", exc_info=True)

# --- Flet アプリケーションの main 関数 ---
def main(page: ft.Page):
    # --- ページ (ウィンドウ) の設定 ---
    page.title = f"{config_manager.APP_NAME} Controller"
    page.window_width = 800
    page.window_height = 600
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.START

    # --- コントロールの参照 ---
    # メインタブ
    status_icon = ft.Icon(ft.icons.CIRCLE, color=ft.colors.RED, tooltip="停止中") # ステータスアイコン
    status_text = ft.Text("停止中", size=16, weight=ft.FontWeight.BOLD)
    start_stop_button = ft.ElevatedButton("起動", icon=ft.icons.PLAY_ARROW, tooltip="Botを起動します")
    log_output_list = ft.ListView(expand=True, spacing=5, auto_scroll=True, divider_thickness=1)
    MAX_LOG_LINES = 500 # GUIに表示する最大ログ行数
    log_panel = ft.ExpansionPanelList(
        expand_icon_color=ft.colors.AMBER,
        elevation=4,
        divider_color=ft.colors.AMBER_100, # 少し薄い色に
        controls=[
            ft.ExpansionPanel(
                can_tap_header=True, # ヘッダー全体で開閉可能に
                header=ft.ListTile(title=ft.Text("ログ表示エリア (クリックで展開/格納)")),
                content=ft.Container(
                    content=log_output_list,
                    padding=ft.padding.only(top=10, bottom=10, left=15, right=15), # パディング調整
                    border_radius=5,
                    height=300, # 高さを指定
                    border=ft.border.all(1, ft.colors.BLACK26) # 枠線を追加
                )
            )
        ]
    )

    # 基本設定タブ
    token_field = ft.TextField(label="Discord Bot Token", password=True, can_reveal_password=True, width=450) # 幅調整
    gemini_api_key_field = ft.TextField(label="Gemini API Key", password=True, can_reveal_password=True, width=450)
    weather_api_key_field = ft.TextField(label="OpenWeatherMap API Key (Optional)", password=True, can_reveal_password=True, width=450)
    delete_password_field = ft.TextField(label="History Delete Password (Optional)", password=True, can_reveal_password=True, width=450)
    save_button = ft.ElevatedButton("設定を保存", icon=ft.icons.SAVE)
    status_snackbar = ft.SnackBar(content=ft.Text(""), open=False)

    # --- PubSub リスナー関数 ---
    def on_log_message_received(log_entry: str):
        """PubSubからログメッセージを受け取ったときの処理"""
        if isinstance(log_entry, str):
             # ListViewに新しいログエントリを追加
             log_output_list.controls.append(
                 ft.Text(log_entry, selectable=True, size=12, font_family="Consolas, monospace") # フォント指定
             )
             # 最大行数を超えたら古いものから削除
             if len(log_output_list.controls) > MAX_LOG_LINES:
                 del log_output_list.controls[0]

             # UI更新は page.update() でまとめて行う方が効率的な場合がある
             # ここで毎回 page.update() するとログが多い場合に重くなる可能性
             # -> 定期的に更新するか、重要なログの時だけ更新するなどの工夫も可能
             # -> まずは毎回更新で様子を見る
             try:
                page.update() # ListViewを更新
             except Exception as e:
                 # page.update() が失敗する場合 (ページが閉じられた後など)
                 print(f"Error updating page for log: {e}")
        else:
             logging.warning(f"Received non-string log entry via PubSub: {type(log_entry)}")

    # --- PubSubの購読設定 (pubsubがインポートできていれば) ---
    if pub:
        try:
            pub.subscribe(on_log_message_received, 'log_message')
            logging.info("Subscribed to 'log_message' topic for GUI updates.")
        except Exception as e_pubsub_sub:
            logging.error("Failed to subscribe to 'log_message' topic", exc_info=e_pubsub_sub)
    else:
        logging.warning("PubSub not available, GUI log updates will not work.")

    # --- ログ転送スレッドを開始 ---
    log_forward_thread = start_log_forwarding()

    # --- イベントハンドラ ---
    def save_settings_clicked(e):
        """「設定を保存」ボタンがクリックされたときの処理"""
        logging.info("Save settings button clicked.")

        # 1. GUIのフィールドから値を取得
        # .strip() で前後の空白を削除しておくと安全
        token = token_field.value.strip() if token_field.value else None
        gemini_key = gemini_api_key_field.value.strip() if gemini_api_key_field.value else None
        weather_key = weather_api_key_field.value.strip() if weather_api_key_field.value else None
        delete_pass = delete_password_field.value.strip() if delete_password_field.value else None

        logging.debug(f"Attempting to save settings:")
        # デバッグログには最初の数文字だけ表示 (マスク)
        logging.debug(f"  Token: {token[:5]}..." if token else "None")
        logging.debug(f"  Gemini Key: {gemini_key[:5]}..." if gemini_key else "None")
        logging.debug(f"  Weather Key: {weather_key[:5]}..." if weather_key else "None")
        logging.debug(f"  Delete Pass: {delete_pass[:5]}..." if delete_pass else "None")

        try:
            # 2. メモリ上の app_config を更新
            #    config_manager にヘルパー関数があればそれを使う。
            #    なければ、直接 app_config 辞書を更新する。
            #    ここでは config_manager に update_secret_in_memory がある前提で記述
            #    (もしなければ、config_manager.app_config["secrets"]["key"] = value のように直接更新)

            # config_manager.update_secret_in_memory("discord_token", token)
            # config_manager.update_secret_in_memory("gemini_api_key", gemini_key)
            # config_manager.update_secret_in_memory("weather_api_key", weather_key)
            # config_manager.update_secret_in_memory("delete_history_password", delete_pass)

            # --- 直接 app_config を更新する場合の例 ---
            config_manager.app_config.setdefault("secrets", {})["discord_token"] = token
            config_manager.app_config.setdefault("secrets", {})["gemini_api_key"] = gemini_key
            config_manager.app_config.setdefault("secrets", {})["weather_api_key"] = weather_key
            config_manager.app_config.setdefault("secrets", {})["delete_history_password"] = delete_pass
            # -----------------------------------------

            # 3. 変更をファイルに保存 (暗号化処理は save_app_config 内で行われる)
            config_manager.save_app_config()

            # 4. ユーザーにフィードバック
            status_snackbar.content = ft.Text("設定を保存しました。")
            status_snackbar.bgcolor = ft.colors.GREEN_700 # 成功時は緑色背景
            logging.info("Settings saved successfully.")

        except Exception as e_save:
            logging.error("Failed to save settings", exc_info=e_save)
            # 4. エラー時のフィードバック
            status_snackbar.content = ft.Text(f"設定の保存中にエラーが発生しました: {e_save}")
            status_snackbar.bgcolor = ft.colors.RED_700 # エラー時は赤色背景

        # 5. SnackBarを表示し、ページを更新
        status_snackbar.open = True
        page.update()

    def start_stop_clicked(e):
        # TODO: Botの状態をより正確に管理するフラグなどが必要
        is_running = bot_core.is_bot_running() # スレッドが生きているか確認
        logging.info(f"Start/Stop button clicked! Current thread state (alive): {is_running}")

        if is_running:
            logging.info("Attempting to stop the bot...")
            # ボタンを無効化して処理中を示す (任意)
            start_stop_button.disabled = True
            start_stop_button.text = "停止処理中..."
            start_stop_button.icon = ft.icons.HOURGLASS_EMPTY
            page.update()

            try:
                bot_core.signal_stop_bot()
                # ここで停止完了を待つか、PubSubで停止完了通知を受け取る
                # → 簡単なのは待たない。PubSubで状態更新するのが望ましい。
                status_text.value = "停止処理中..."
                status_icon.color = ft.colors.ORANGE # 処理中を示す色
                status_icon.tooltip = "停止処理中"
                # ボタンの状態は停止完了通知を受けてから元に戻す
            except Exception as e_stop:
                 logging.error("Error signaling bot stop", exc_info=e_stop)
                 status_snackbar.content = ft.Text(f"停止信号の送信に失敗: {e_stop}")
                 status_snackbar.open = True
                 # エラーが起きてもボタンは有効に戻す
                 start_stop_button.disabled = False
                 # ボタン表示は現状維持 (停止中のままのはず)
        else:
            logging.info("Attempting to start the bot...")
            # ボタンを無効化して処理中を示す
            start_stop_button.disabled = True
            start_stop_button.text = "起動処理中..."
            start_stop_button.icon = ft.icons.HOURGLASS_EMPTY
            status_text.value = "起動処理中..."
            status_icon.color = ft.colors.ORANGE
            status_icon.tooltip = "起動処理中"
            page.update()

            try:
                success = bot_core.start_bot_thread()
                if success:
                    # 起動成功 -> 状態は on_ready や PubSub で更新されるのを待つ
                    # ボタン表示は「停止」に変わるはず (PubSub経由で)
                     logging.info("Bot start thread initiated successfully.")
                else:
                    # スレッド起動自体に失敗した場合
                    logging.error("Failed to start bot thread.")
                    status_snackbar.content = ft.Text("Botの起動に失敗しました。ログを確認してください。")
                    status_snackbar.open = True
                    status_text.value = "起動失敗"
                    status_icon.color = ft.colors.RED
                    status_icon.tooltip = "起動失敗"
                    # ボタンを元に戻す
                    start_stop_button.disabled = False
                    start_stop_button.text = "起動"
                    start_stop_button.icon = ft.icons.PLAY_ARROW
            except Exception as e_start:
                 logging.error("Error starting bot thread", exc_info=e_start)
                 status_snackbar.content = ft.Text(f"Bot起動中にエラー: {e_start}")
                 status_snackbar.open = True
                 status_text.value = "起動エラー"
                 status_icon.color = ft.colors.RED
                 status_icon.tooltip = "起動エラー"
                 start_stop_button.disabled = False
                 start_stop_button.text = "起動"
                 start_stop_button.icon = ft.icons.PLAY_ARROW

        page.update() # ボタン、ステータスの更新を反映

    # --- 初期値の読み込み ---
    def load_initial_settings():
        logging.info("Loading initial settings...")
        try:
            token_field.value = config_manager.get_discord_token() or ""
            gemini_api_key_field.value = config_manager.get_gemini_api_key() or ""
            weather_api_key_field.value = config_manager.get_weather_api_key() or ""
            delete_password_field.value = config_manager.get_delete_history_password() or ""
            logging.info("Initial settings loaded into fields.")
        except Exception as e_load:
            logging.error("Error loading initial settings", exc_info=e_load)
            status_snackbar.content = ft.Text(f"設定の読み込みエラー: {e_load}")
            status_snackbar.open = True
        # page.update() は main の最後で行う

    # --- アプリ終了時の処理 ---
    def on_window_event(e):
         if e.data == "close":
             logging.info("Window close event received.")
             # PubSubの購読解除
             if pub:
                 try:
                     pub.unsubscribe(on_log_message_received, 'log_message')
                     logging.info("Unsubscribed from 'log_message' topic.")
                 except Exception as unsub_e:
                      logging.error(f"Error unsubscribing from pubsub: {unsub_e}")

             # Bot停止処理
             if bot_core.is_bot_running():
                 logging.info("Signaling bot stop before closing...")
                 bot_core.signal_stop_bot()
                 # Botスレッドの終了を待つ（オプションだが推奨）
                 bot_thread = getattr(bot_core, '_bot_thread', None) # bot_core内部のスレッド参照
                 if bot_thread and isinstance(bot_thread, threading.Thread) and bot_thread.is_alive():
                      logging.info("Waiting for bot thread to stop...")
                      bot_thread.join(timeout=5.0) # 5秒待つ
                      if bot_thread.is_alive():
                           logging.warning("Bot thread did not stop gracefully within timeout.")
                      else:
                           logging.info("Bot thread stopped.")

             # ログ転送スレッド停止処理
             logging.info("Signaling log forwarder stop...")
             stop_log_forwarding()
             # ログ転送スレッドの終了を待つ（オプション）
             fwd_thread = log_forward_thread # main関数スコープの変数を参照
             if fwd_thread and fwd_thread.is_alive():
                  logging.info("Waiting for log forwarder thread to stop...")
                  fwd_thread.join(timeout=2.0) # 2秒待つ
                  if fwd_thread.is_alive():
                       logging.warning("Log forwarder thread did not stop within timeout.")
                  else:
                       logging.info("Log forwarder thread stopped.")

             logging.info("Exiting application.")
             page.window_destroy() # ウィンドウを強制的に閉じる

    page.window_prevent_close = True # デフォルトの閉じる動作を防ぐ
    page.on_window_event = on_window_event # ハンドラを設定

    # --- コントロールにイベントハンドラを割り当て ---
    save_button.on_click = save_settings_clicked
    start_stop_button.on_click = start_stop_clicked

    # --- タブの作成 ---
    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[
            ft.Tab(
                text="メイン",
                icon=ft.icons.HOME,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    status_icon, # アイコンを追加
                                    status_text,
                                ],
                                alignment=ft.MainAxisAlignment.START, # 左寄せ
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=10
                            ),
                            start_stop_button, # ボタンをステータスの下に配置
                            ft.Divider(height=20),
                            log_panel,
                        ],
                        spacing=15, # コントロール間のスペース調整
                        scroll=ft.ScrollMode.AUTO # 必要に応じてスクロール可能に
                    )
                )
            ),
            ft.Tab(
                text="基本設定",
                icon=ft.icons.SETTINGS,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                         controls=[
                             token_field,
                             gemini_api_key_field,
                             weather_api_key_field,
                             delete_password_field,
                             ft.Divider(height=30),
                             save_button,
                         ],
                         spacing=20, # フィールド間のスペース調整
                         horizontal_alignment=ft.CrossAxisAlignment.CENTER
                     )
                )
            ),
        ],
        expand=True # タブ領域を広げる
    )

    # --- ページにコントロールを追加 ---
    page.add(tabs)
    page.add(status_snackbar) # SnackBarはaddしても見えないが動作する

    # --- 初期設定の読み込みを実行 ---
    load_initial_settings()

    # --- 初期UI更新 ---
    page.update()


# --- アプリケーションの実行 ---
if __name__ == "__main__":
    # アプリ起動前に基本的なログ設定が完了していることを確認
    logging.info("Starting Flet application...")
    try:
        ft.app(
            target=main,
            view=ft.AppView.FLET_APP, # デバッグ時はコンソール表示
            # assets_dir="../assets" # 画像などを使う場合は設定
        )
    except Exception as e_app_start:
         logging.critical("Failed to start Flet application", exc_info=e_app_start)
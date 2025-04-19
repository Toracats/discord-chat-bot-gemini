# gui/main_gui.py (run_task, DeprecationWarning 修正版)

import flet as ft
import sys
import os
import logging
import logging.handlers
from pathlib import Path
import threading
from typing import Optional, Dict, Any, List
import asyncio
import datetime
import math
import base64

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

try:
    from pubsub import pub
except ImportError:
    print("Error: PyPubSub library not found. pip install pypubsub")
    pub = None

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
    from utils import helpers
    import aiofiles
    import aiofiles.os
    import discord
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)
except Exception as general_e:
     print(f"Unexpected error during import: {general_e}")
     sys.exit(1)

# --- ロギング設定 ---
try:
    LOG_FILE_PATH = config_manager.LOG_FILE; LOG_FORMAT = '%(asctime)s:%(levelname)s:%(name)s: %(message)s'; LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'; root_logger = logging.getLogger();
    if not root_logger.hasHandlers():
        root_logger.setLevel(logging.INFO); formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT); console_handler = logging.StreamHandler(sys.stdout); console_handler.setFormatter(formatter); console_handler.setLevel(logging.INFO); root_logger.addHandler(console_handler);
        try: LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True); file_handler = logging.handlers.RotatingFileHandler(LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'); file_handler.setFormatter(formatter); file_handler.setLevel(logging.DEBUG); root_logger.addHandler(file_handler); logger.info(f"File logging initialized: {LOG_FILE_PATH}")
        except Exception as e_file_log: logger.error(f"Failed file logging!", exc_info=e_file_log)
        if pub: pubsub_handler = PubSubLogHandler(); pubsub_handler.setFormatter(formatter); pubsub_handler.setLevel(logging.INFO); root_logger.addHandler(pubsub_handler); logger.info("PubSub log handler initialized.")
        else: logger.warning("PyPubSub not found, GUI log forwarding disabled.")
        logging.getLogger("flet").setLevel(logging.INFO); logging.getLogger("flet_core").setLevel(logging.INFO)
except Exception as e_log_init: print(f"CRITICAL ERROR during logging setup: {e_log_init}"); logging.basicConfig(level=logging.INFO); logging.critical(f"Logging setup failed: {e_log_init}", exc_info=True)

MAX_LOG_LINES = 500

# --- Flet アプリケーション main 関数 ---
async def main(page: ft.Page):
    page.title = f"{config_manager.APP_NAME} Controller"; page.window_width = 900; page.window_height = 650
    page.vertical_alignment = ft.MainAxisAlignment.START; page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.padding = 0
    page.fonts = {"Noto Sans JP": "fonts/NotoSansJP-Regular.ttf", "Bebas Neue": "fonts/BebasNeue-Regular.ttf"}
    page.theme = ft.Theme(font_family="Noto Sans JP"); page.dark_theme = ft.Theme(font_family="Noto Sans JP")
    page.theme_mode = ft.ThemeMode.DARK

    is_in_critical_error_state = threading.Event(); is_in_critical_error_state.clear()

    # --- コントロール参照 ---
    bot_name_background = ft.Text("", size=130, font_family="Bebas Neue", weight=ft.FontWeight.BOLD,
        # ★ ft.Colors.with_opacity -> ft.Colors.with_opacity (小文字c), ft.Colors.WHITE (大文字C) ★
        color=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
        selectable=False, no_wrap=True, rotate=ft.transform.Rotate(angle=math.pi / 2))
    bot_icon_image = ft.Image(src="assets/default_avatar.png", width=200, height=200, fit=ft.ImageFit.COVER, border_radius=ft.border_radius.all(15))
    bot_icon_container = ft.Container(content=bot_icon_image, width=200, height=200, border_radius=ft.border_radius.all(15), clip_behavior=ft.ClipBehavior.ANTI_ALIAS, tooltip="クリックしてアイコンを変更", ink=True)
    bot_name_display_edit = ft.TextField(label="NAME", value="",label_style=ft.TextStyle(font_family="Bebas Neue", size=64), text_style=ft.TextStyle(font_family="Bebas Neue", size=48), border=ft.InputBorder.NONE, read_only=True, tooltip="クリックして編集")
    bot_status_text = ft.Text("INITIALIZING...", size=48, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE)
    bot_status_switch = ft.Switch(value=False, disabled=True, tooltip="Botを起動/停止")
    weather_text = ft.Text("Weather: ---", font_family="Bebas Neue", size=48)
    dm_next_time_text = ft.Text("Next DM: ---", font_family="Bebas Neue", size=48)
    dm_target_text = ft.Text("Target: ---", font_family="Bebas Neue", size=48)
    weather_auto_update_switch = ft.Switch(label="天気自動更新を有効にする", value=False)
    weather_interval_field = ft.TextField(label="自動更新間隔 (分)", value="", width=150, keyboard_type=ft.KeyboardType.NUMBER, input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]+"), tooltip="最低10分")
    settings_view_switcher = ft.AnimatedSwitcher(content=ft.Column([ft.ProgressRing(), ft.Text("設定を読み込み中...")]), transition=ft.AnimatedSwitcherTransition.FADE, duration=300, reverse_duration=100, expand=True)
    latest_log_text = ft.Text("ログ: ---", size=12, opacity=0.7, no_wrap=True)
    log_output_list = ft.ListView(expand=True, spacing=5, auto_scroll=True, divider_thickness=1)
    log_detail_container = ft.Container(content=log_output_list, padding=ft.padding.only(top=10, bottom=10, left=15, right=15),
        # ★ ft.Colors.with_opacity -> ft.Colors.with_opacity, ft.Colors.WHITE ★
        border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.WHITE)),
        border_radius=ft.border_radius.all(5), height=200, visible=False, animate_opacity=300, animate_size=300)
    log_area = ft.Container(ft.Column([ft.Container(content=latest_log_text, ink=True, tooltip="クリックで詳細ログを展開/格納", on_click=lambda e: toggle_log_detail(e)), log_detail_container], spacing=5),
        padding=ft.padding.symmetric(horizontal=20, vertical=5),
        # ★ ft.Colors.with_opacity -> ft.Colors.with_opacity, ft.Colors.WHITE ★
        border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.with_opacity(0.1, ft.Colors.WHITE))))
    def open_menu(e): menu_sheet.open = True; page.update()
    # ★ ft.Icons -> ft.Icons (大文字I) ★
    menu_button = ft.IconButton(ft.Icons.MENU_ROUNDED, tooltip="設定メニュー", on_click=open_menu)
    status_snackbar = ft.SnackBar(content=ft.Text(""), open=False)
    loading_overlay = ft.Container(content=ft.Column([ft.ProgressRing(), ft.Text("読み込み中...", size=16)], horizontal_alignment=ft.CrossAxisAlignment.CENTER), alignment=ft.alignment.center,
        # ★ ft.Colors.with_opacity -> ft.Colors.with_opacity, ft.Colors.BLACK ★
        bgcolor=ft.Colors.with_opacity(0.7, ft.Colors.BLACK), visible=True, expand=True)

    # --- PubSub リスナー (ログ用) ---
    def on_log_message_received(log_entry: str):
        if page.client_storage is None: return
        if isinstance(log_entry, str):
            now_str = datetime.datetime.now().strftime("%H:%M:%S");
            if ":INFO:" in log_entry or ":WARNING:" in log_entry or ":ERROR:" in log_entry or ":CRITICAL:" in log_entry: latest_log_text.value = f"{now_str} | {log_entry.split(':', 3)[-1].strip()}"; latest_log_text.update()
            log_output_list.controls.append(ft.Text(log_entry, selectable=True, size=12, font_family="Consolas, monospace"));
            if len(log_output_list.controls) > MAX_LOG_LINES: del log_output_list.controls[0]
            if log_detail_container.visible: log_output_list.update()
        else: logger.warning(f"Received non-string log entry: {type(log_entry)}")

    # --- GUI状態更新用関数 ---
    def update_gui_status(status: str, message: Optional[str] = None):
        if page.client_storage is None: return; logger.debug(f"Updating GUI Status: {status}, Message: {message}"); status_color = ft.Colors.RED; status_val = "STOPPED"; switch_enabled = True; switch_val = False; tooltip = "停止中"
        if is_in_critical_error_state.is_set() and status != "critical_error": logger.debug(f"Maintaining critical error state: {status}"); status_color = ft.Colors.RED; status_val = "CRITICAL ERROR"; switch_enabled = False; switch_val = False; snackbar_msg = getattr(status_snackbar.content, 'value', '設定確認要'); tooltip = f"重大エラー: {snackbar_msg[:100]}"
        elif status == "starting": status_color = ft.Colors.ORANGE; status_val = "STARTING..."; switch_enabled = False; switch_val = True; tooltip = "Bot起動中..." ; is_in_critical_error_state.clear()
        elif status == "connecting": status_color = ft.Colors.ORANGE; status_val = "CONNECTING..."; switch_enabled = False; switch_val = True; tooltip = "Discord接続中..."
        elif status == "ready": status_color = ft.Colors.GREEN; status_val = "ACTIVE"; switch_enabled = True; switch_val = True; tooltip = "Bot動作中"; is_in_critical_error_state.clear()
        elif status == "stopping": status_color = ft.Colors.ORANGE; status_val = "STOPPING..."; switch_enabled = False; switch_val = False; tooltip = "Bot停止中..."
        elif status == "stopped":
             if is_in_critical_error_state.is_set(): status_color = ft.Colors.RED; status_val = "ERROR (STOPPED)"; switch_enabled = True; switch_val = False; snackbar_msg = getattr(status_snackbar.content, 'value', '設定を確認'); tooltip = f"エラーのため停止: {snackbar_msg[:100]}"
             else: status_color = ft.Colors.RED; status_val = "STOPPED"; switch_enabled = True; switch_val = False; tooltip = "停止中"; is_in_critical_error_state.clear()
        elif status == "error": status_color = ft.Colors.RED; status_val = "ERROR"; switch_enabled = True; switch_val = False; tooltip = f"エラー: {message or '不明'}"; is_in_critical_error_state.clear(); status_snackbar.content = ft.Text(f"エラー: {message or '不明'}"); status_snackbar.bgcolor = ft.Colors.RED_700; status_snackbar.open = True
        elif status == "critical_error": status_color = ft.Colors.RED; status_val = "CRITICAL ERROR"; switch_enabled = False; switch_val = False; tooltip = f"重大エラー: {message or '不明'}. 設定確認要"; is_in_critical_error_state.set(); status_snackbar.content = ft.Text(f"重大エラー: {message or '不明'}. 設定確認/保存要"); status_snackbar.bgcolor = ft.Colors.RED_700; status_snackbar.open = True
        else: logger.warning(f"Unknown status: {status}"); return
        bot_status_text.value = status_val; bot_status_text.color = status_color; bot_status_switch.disabled = not switch_enabled; bot_status_switch.value = switch_val; bot_status_switch.tooltip = tooltip
        try: page.update()
        except Exception as e: logger.warning(f"Failed page update in update_gui_status: {e}")

    # --- PubSub リスナー (Botステータス用) ---
    def on_bot_status_update(payload: Optional[Dict[str, Any]]):
        if page.client_storage is None: return
        if payload is None: logger.warning("Received None payload in on_bot_status_update."); return
        logger.debug(f"Received bot_status_update payload: {payload}"); status = payload.get("status"); message = payload.get("message")
        if status: update_gui_status(status, message)
        else: logger.warning(f"Invalid status update payload (status is None or empty): {payload}")

    # --- PubSubの購読設定 ---
    if pub:
        try:
            pub.subscribe(on_log_message_received, 'log_message')
            logger.info("Subscribed to 'log_message'.")
            pub.subscribe(on_bot_status_update, 'bot_status_update')
            logger.info("Subscribed to 'bot_status_update'.")
        except Exception as e_pubsub_sub:
            logger.error("Failed pubsub subscribe", exc_info=e_pubsub_sub)
    else:
        logger.warning("PubSub not available.")

    # --- ログ転送スレッド開始 ---
    log_forward_thread = start_log_forwarding()

    # --- イベントハンドラ ---

    # Bot名編集関連
    def handle_bot_name_click(e):
        bot_name_display_edit.read_only = False
        bot_name_display_edit.border = ft.InputBorder.UNDERLINE
        bot_name_display_edit.update()
        bot_name_display_edit.focus()

    async def handle_bot_name_submit(e):
        new_name = bot_name_display_edit.value.strip()
        bot_name_display_edit.read_only = True
        bot_name_display_edit.border = ft.InputBorder.NONE
        bot_name_display_edit.update()
        current_name = ""
        if hasattr(bot_core, 'bot') and bot_core.bot and bot_core.bot.user:
            current_name = bot_core.bot.user.name
        if not new_name or new_name == current_name:
            logger.info("Bot name not changed.")
            bot_name_display_edit.value = current_name # Revert if unchanged
            bot_name_display_edit.update()
            return
        confirm_dialog_name = ft.AlertDialog(
            modal=True,
            title=ft.Text("Bot名変更の確認"),
            content=ft.Text(f"Bot名を「{new_name}」に変更しますか？\n(Discordのレート制限にご注意ください)"),
            actions=[
                ft.TextButton("はい", on_click=lambda _: page.run_task(close_dialog_and_change_name(new_name, confirm_dialog_name))),
                ft.TextButton("いいえ", on_click=lambda _: close_dialog(confirm_dialog_name)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.dialog = confirm_dialog_name
        confirm_dialog_name.open = True
        page.update()

    async def close_dialog_and_change_name(new_name, dialog):
        dialog.open = False
        page.update()
        await change_bot_profile(username=new_name)

    def close_dialog(dialog):
        dialog.open = False
        current_name = ""
        if hasattr(bot_core, 'bot') and bot_core.bot and bot_core.bot.user:
            current_name = bot_core.bot.user.name
        bot_name_display_edit.value = current_name
        bot_name_display_edit.read_only = True
        bot_name_display_edit.border = ft.InputBorder.NONE
        page.update()

    bot_name_display_edit.on_submit = handle_bot_name_submit # on_submit は非同期ハンドラを直接設定可
    bot_name_display_edit.on_blur = lambda e: page.run_task(handle_bot_name_submit(e)) # on_blur は page.run_task が必要
    bot_name_display_edit.on_click = handle_bot_name_click

    # Botアイコン変更関連
    # ★ FilePicker の on_result を修正 ★
    async def on_file_picker_result(e: ft.FilePickerResultEvent):
        await handle_icon_picked(e) # page.run_task は不要 (on_resultはasync関数を受け付ける)

    file_picker = ft.FilePicker(on_result=on_file_picker_result) # ★ on_result に async 関数を直接渡す
    page.overlay.append(file_picker)

    def handle_icon_container_click(e):
        if bot_core.is_bot_running():
             logger.debug("Icon container clicked, opening file picker...")
             file_picker.pick_files(
                 dialog_title="Botアイコンを選択",
                 allowed_extensions=["png", "jpg", "jpeg", "gif"],
                 allow_multiple=False
             )
        else:
             show_snackbar("Botが停止中のためアイコンを変更できません。", "orange")

    async def handle_icon_picked(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            selected_file = e.files[0]
            image_path = selected_file.path
            logger.info(f"Icon file selected: {image_path}")

            confirm_dialog_icon = ft.AlertDialog(
                modal=True,
                title=ft.Text("アイコン変更の確認"),
                content=ft.Column([
                    ft.Text(f"この画像でアイコンを変更しますか？\n(Discordのレート制限にご注意ください)"),
                    ft.Image(src=image_path, height=100, width=100, fit=ft.ImageFit.CONTAIN)
                ], tight=True),
                actions=[
                    # ★ AlertDialog の on_click も page.run_task が必要 ★
                    ft.TextButton("はい", on_click=lambda _: page.run_task(close_dialog_and_change_icon(image_path, confirm_dialog_icon))),
                    ft.TextButton("いいえ", on_click=lambda _: close_dialog(confirm_dialog_icon)), # 同期なので不要
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.dialog = confirm_dialog_icon
            confirm_dialog_icon.open = True
            page.update()
        else:
            logger.info("Icon selection cancelled.")

    async def close_dialog_and_change_icon(image_path, dialog):
        dialog.open = False
        page.update()
        try:
            async with aiofiles.open(image_path, "rb") as f:
                avatar_bytes = await f.read()
            await change_bot_profile(avatar=avatar_bytes)
        except Exception as img_e:
            logger.error(f"Error reading icon file: {image_path}", exc_info=img_e)
            show_snackbar(f"アイコンファイルの読み込みエラー: {img_e}", "red")

    bot_icon_container.on_click = handle_icon_container_click

    async def change_bot_profile(username: Optional[str] = None, avatar: Optional[bytes] = None):
        if not bot_core.is_bot_running() or not bot_core.bot:
            show_snackbar("Botが起動していないためプロフィールを変更できません。", "orange")
            return

        show_snackbar("プロフィールを変更中...", "blue")
        page.update()

        try:
            await bot_core.bot.edit_profile(username=username, avatar=avatar)
            logger.info(f"Bot profile updated successfully (username={username is not None}, avatar={avatar is not None}).")
            show_snackbar("プロフィールを変更しました。", "green")

            if username:
                bot_name_display_edit.value = username
                bot_name_background.value = username
                bot_name_display_edit.update()
                bot_name_background.update()
            if avatar:
                bot_icon_image.src_base64 = base64.b64encode(avatar).decode('utf-8')
                bot_icon_image.update()

        except discord.HTTPException as http_e:
            logger.error("Failed to update bot profile (HTTPException)", exc_info=http_e)
            error_msg = f"変更失敗({http_e.status}): {http_e.text}"
            if http_e.status == 429:
                error_msg = "変更失敗: Discord APIのレート制限です。しばらく待ってから再試行してください。"
            show_snackbar(error_msg, "red")
        except Exception as e:
            logger.error("Failed to update bot profile (Exception)", exc_info=e)
            show_snackbar(f"プロフィールの変更中に予期せぬエラーが発生しました: {e}", "red")
        finally:
            page.update()


    # 起動/停止スイッチ
    def handle_switch_change(e):
        logger.info(f"Switch changed: {e.control.value}")
        is_running = bot_core.is_bot_running()
        logger.debug(f"Current bot running state: {is_running}")

        if e.control.value and not is_running:
            logger.info("Switch turned ON while bot stopped. Starting bot...")
            start_bot()
        elif not e.control.value and is_running:
            logger.info("Switch turned OFF while bot running. Stopping bot...")
            stop_bot()
        else:
             logger.warning(f"Switch state ({e.control.value}) and bot state ({is_running}) mismatch or no action needed.")
             bot_status_switch.value = is_running # Sync switch display
             bot_status_switch.update()

    bot_status_switch.on_change = handle_switch_change

    def start_bot():
        if is_in_critical_error_state.is_set():
            logger.warning("Cannot start bot due to critical error state.")
            show_snackbar("重大エラー状態です。設定を保存・確認してください。", "orange")
            page.update()
            return
        logger.info("Attempting start bot thread...")
        update_gui_status("starting")
        try:
            success = bot_core.start_bot_thread()
            # Optionally check 'success' if start_bot_thread provides feedback
        except Exception as e_start:
            logger.error("Error during bot thread start attempt", exc_info=e_start)
            update_gui_status("critical_error", f"スレッド起動エラー: {e_start}")

    def stop_bot():
        logger.info("Attempting stop bot signal...")
        update_gui_status("stopping")
        try:
            bot_core.signal_stop_bot()
        except Exception as e_stop:
            logger.error("Error signaling bot stop", exc_info=e_stop)
            update_gui_status("error", f"停止信号エラー: {e_stop}")


    # ログ表示切り替え
    def toggle_log_detail(e):
        log_detail_container.visible = not log_detail_container.visible
        log_detail_container.update()
        if log_detail_container.visible:
            log_output_list.update()


    # Snackbar表示関数
    def show_snackbar(message: str, color: str):
        color_map = {"red": ft.Colors.RED_700, "green": ft.Colors.GREEN_700, "blue": ft.Colors.BLUE_700, "orange": ft.Colors.ORANGE_700}
        status_snackbar.content = ft.Text(message)
        status_snackbar.bgcolor = color_map.get(color, ft.Colors.BLACK)
        status_snackbar.open = True
        page.update()

    # --- 設定画面用のウィジェット ---
    # 基本設定
    token_field = ft.TextField(label="Discord Bot Token", password=True, can_reveal_password=True, value="", width=450)
    gemini_api_key_field = ft.TextField(label="Gemini API Key", password=True, can_reveal_password=True, value="", width=450)
    weather_api_key_field = ft.TextField(label="OpenWeatherMap API Key (Optional)", password=True, can_reveal_password=True, value="", width=450)
    delete_password_field = ft.TextField(label="History Delete Password (Optional)", password=True, can_reveal_password=True, value="", width=450)
    save_settings_button = ft.ElevatedButton("設定を保存", icon="save_rounded")

    def save_settings_clicked(e):
        logger.info("Save basic settings clicked.")
        token = token_field.value.strip() or None
        gemini_key = gemini_api_key_field.value.strip() or None
        weather_key = weather_api_key_field.value.strip() or None
        delete_pass = delete_password_field.value.strip() or None
        save_successful = False
        try:
            logger.debug("Updating secrets in memory...")
            config_manager.app_config.setdefault("secrets", {})["discord_token"] = token
            config_manager.app_config.setdefault("secrets", {})["gemini_api_key"] = gemini_key
            config_manager.app_config.setdefault("secrets", {})["weather_api_key"] = weather_key
            config_manager.app_config.setdefault("secrets", {})["delete_history_password"] = delete_pass
            logger.debug("Calling save_app_config()...")
            config_manager.save_app_config() # Synchronous save
            logger.debug("save_app_config() finished.")
            is_in_critical_error_state.clear() # Clear critical error on successful save
            save_successful = True
            show_snackbar("基本設定を保存しました。", "green")
        except Exception as e_save:
            logger.error("Failed save basic settings", exc_info=e_save)
            show_snackbar(f"保存エラー: {e_save}", "red")

        if save_successful:
            logger.info("Basic settings saved.")
            if bot_core.is_bot_running():
                update_gui_status("ready", "設定保存完了")
            else:
                update_gui_status("stopped", "設定保存完了")
        page.update()

    save_settings_button.on_click = save_settings_clicked
    basic_settings_view = ft.Container(
         padding=20,
         content=ft.Column(
             [
                 ft.Text("基本設定 (APIキー等)", size=18, weight=ft.FontWeight.BOLD),
                 token_field, gemini_api_key_field, weather_api_key_field, delete_password_field,
                 ft.Divider(height=30),
                 save_settings_button,
             ],
             spacing=20, horizontal_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO
         )
     )

    # 他の設定画面プレースホルダー
    def create_placeholder_view(title: str):
        return ft.Container(
            padding=20,
            content=ft.Column(
                [
                    ft.Text(title, size=18, weight=ft.FontWeight.BOLD),
                    ft.Text("未実装"), ft.Text("(/configを使用)"),
                ],
                spacing=15, horizontal_alignment=ft.CrossAxisAlignment.CENTER
            )
        )
    gemini_settings_view = create_placeholder_view("Gemini 設定")
    prompt_settings_view = create_placeholder_view("プロンプト設定")
    user_settings_view = create_placeholder_view("ユーザー設定")
    channel_settings_view = create_placeholder_view("チャンネル設定")
    random_dm_settings_view = create_placeholder_view("ランダムDM設定")
    response_settings_view = create_placeholder_view("応答設定")
    summary_settings_view = create_placeholder_view("要約設定")

    # 天気設定画面
    def save_weather_settings(e):
        logger.info("Save weather settings clicked.")
        weather_enabled = weather_auto_update_switch.value
        weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
        try:
            weather_interval_str = weather_interval_field.value.strip() or ""
            interval_val = int(weather_interval_str) if weather_interval_str.isdigit() else -1
            weather_interval = max(10, interval_val) if interval_val >= 1 else config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
            if weather_interval_field.value != str(weather_interval): weather_interval_field.value = str(weather_interval)
        except Exception as e_interval:
            logger.warning(f"Invalid weather interval: {e_interval}")
            weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
            weather_interval_field.value = str(weather_interval)
        try:
            config_manager.update_weather_auto_update_enabled_in_memory(weather_enabled)
            config_manager.update_weather_auto_update_interval_in_memory(weather_interval)
            config_manager.save_app_config()
            current_bot = getattr(bot_core, 'bot', None)
            if current_bot:
                weather_cog = current_bot.get_cog("WeatherMoodCog")
                if weather_cog and hasattr(weather_cog, 'update_auto_update_task_status'):
                    try: weather_cog.update_auto_update_task_status(); logger.info("Notified WeatherCog.")
                    except Exception as e_notify: logger.error("Error notifying WeatherCog", exc_info=e_notify)
                elif bot_core.is_bot_running(): logger.warning("WeatherCog not found/method missing.")
            show_snackbar("天気設定を保存しました。", "green")
            page.update()
        except Exception as e_save:
            logger.error("Failed save weather settings", exc_info=e_save)
            show_snackbar(f"天気設定の保存エラー: {e_save}", "red")
            page.update()
    weather_settings_view = ft.Container(
        padding=20,
        content=ft.Column(
            [
                ft.Text("天気・気分設定", size=18, weight=ft.FontWeight.BOLD),
                weather_auto_update_switch,
                weather_interval_field,
                ft.ElevatedButton("天気設定を保存", icon="save", on_click=save_weather_settings)
            ],
            spacing=15
        )
    )

    # --- 右端メニュー (BottomSheet) ---
    def change_view(view_key: str):
        logger.info(f"Changing view to: {view_key}")
        view_map = {
            "main": main_info_column, "basic": basic_settings_view, "gemini": gemini_settings_view,
            "prompt": prompt_settings_view, "user": user_settings_view, "channel": channel_settings_view,
            "random_dm": random_dm_settings_view, "response": response_settings_view,
            "summary": summary_settings_view, "weather": weather_settings_view,
        }
        new_content = view_map.get(view_key, main_info_column)
        settings_view_switcher.content = new_content
        menu_sheet.open = False
        page.update()
    # ★ ft.Icons -> ft.Icons ★
    menu_items = [
        ft.ListTile(title=ft.Text("メイン情報"), leading=ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED), on_click=lambda _: change_view("main")),
        ft.ListTile(title=ft.Text("基本設定 (APIキー等)"), leading=ft.Icon(ft.Icons.KEY_ROUNDED), on_click=lambda _: change_view("basic")),
        ft.Divider(height=1),
        ft.ListTile(title=ft.Text("Gemini 設定"), leading=ft.Icon(ft.Icons.SETTINGS_ROUNDED), on_click=lambda _: change_view("gemini")),
        ft.ListTile(title=ft.Text("天気 設定"), leading=ft.Icon(ft.Icons.SUNNY), on_click=lambda _: change_view("weather")),
        ft.ListTile(title=ft.Text("プロンプト設定"), leading=ft.Icon(ft.Icons.EDIT_NOTE_ROUNDED), on_click=lambda _: change_view("prompt")),
        # Add other menu items...
    ]
    menu_sheet = ft.BottomSheet(
        ft.Container(ft.Column(menu_items, tight=True, spacing=0), padding=ft.padding.symmetric(vertical=10)),
        open=False, enable_drag=True,
    )

    # --- 初期値読み込みと適用 (非同期化) ---
    async def load_initial_settings_and_apply():
        logger.info("Loading initial settings async...")
        error_message = None
        try:
            logger.debug("[GUI] Calling load_all_configs_async...")
            await config_manager.load_all_configs_async()
            logger.debug("[GUI] load_all_configs_async finished.")
            bot_name_initial = "テスト用" # Placeholder
            bot_name_display_edit.value = bot_name_initial
            bot_name_background.value = bot_name_initial
            bot_icon_image.src = "assets/default_avatar.png"
            token_field.value = config_manager.get_discord_token() or ""
            gemini_api_key_field.value = config_manager.get_gemini_api_key() or ""
            weather_api_key_field.value = config_manager.get_weather_api_key() or ""
            delete_password_field.value = config_manager.get_delete_history_password() or ""
            weather_auto_update_switch.value = config_manager.get_weather_auto_update_enabled()
            weather_interval_field.value = str(config_manager.get_weather_auto_update_interval())
            logger.info("Initial settings loaded async and applied.")
            bot_status_switch.disabled = False
            update_gui_status("stopped")
        except Exception as e_load:
            logger.error("Error loading initial settings", exc_info=e_load)
            error_message = f"設定の読み込み失敗: {e_load}."
            bot_status_switch.disabled = True
            update_gui_status("critical_error", "設定読込エラー")
        finally:
            loading_overlay.visible = False
            if error_message: show_snackbar(error_message, "red")
            page.update()

    # --- アプリ終了処理 ---
    def on_window_event(e):
        if e.data == "close":
            logger.info("Window close event received.")
            if pub:
                try: pub.unsubscribe(on_log_message_received, 'log_message'); pub.unsubscribe(on_bot_status_update, 'bot_status_update'); logger.info("Unsubscribed pubsub.")
                except Exception as unsub_e: logger.error(f"Error unsubscribing: {unsub_e}")
            if bot_core.is_bot_running():
                logger.info("Signaling bot stop..."); bot_core.signal_stop_bot(); bot_thread = getattr(bot_core, '_bot_thread', None)
                if bot_thread and bot_thread.is_alive(): logger.info("Waiting for bot thread (max 5s)..."); bot_thread.join(timeout=5.0); logger.info(f"Bot thread alive after join: {bot_thread.is_alive()}")
            logger.info("Signaling log forwarder stop..."); stop_log_forwarding(); fwd_thread = log_forward_thread
            if fwd_thread and fwd_thread.is_alive(): logger.info("Waiting for log forwarder thread (max 2s)..."); fwd_thread.join(timeout=2.0); logger.info(f"Log forwarder alive after join: {fwd_thread.is_alive()}")
            logger.info("Exiting application."); page.window_destroy()
    page.window_prevent_close = True; page.on_window_event = on_window_event

    # --- 右エリアのメイン情報表示用Column ---
    main_info_column = ft.Column(
        [
            bot_name_display_edit,
            ft.Row(
                [
                    ft.Text("Bot Status:", font_family="Bebas Neue", size=48), # ラベル
                    ft.Container(
                        content=bot_status_text,
                        margin=ft.margin.only(bottom=8)   # マージンで調整する場合 (例: 3ピクセル下にずらす)
                    ),
                    bot_status_switch
                ],
                spacing=10,
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER # ベースライン揃え (推奨)
            ),
            weather_text,
            dm_next_time_text,
            dm_target_text,
        ],
        spacing=15,
    )
    settings_view_switcher.content = main_info_column # 初期表示

    # --- 全体のレイアウト構成 ---
    page_content = ft.Row(
        [
            ft.Container(ft.Stack([ft.Container(bot_name_background, alignment=ft.alignment.center, expand=True), ft.Container(bot_icon_container, alignment=ft.alignment.center)]), width=300, padding=ft.padding.only(left=20, right=10, top=20, bottom=20)),
            ft.Container(ft.Column([ft.Row([ft.Container(expand=True), menu_button]), settings_view_switcher, log_area], expand=True, spacing=10), expand=True, padding=ft.padding.only(left=10, right=20, top=20, bottom=10)),
        ], vertical_alignment=ft.CrossAxisAlignment.START, expand=True,
    )

    # --- ページへの要素追加 ---
    page.add(ft.Stack([page_content, loading_overlay]))
    page.add(status_snackbar)
    page.overlay.append(menu_sheet)

    # --- 非同期初期化を実行 ---
    await load_initial_settings_and_apply()
    logger.info("GUI Initialized.")

# --- アプリ実行 ---
if __name__ == "__main__":
    logger.info("Starting Flet application...")
    try: ft.app( target=main, view=ft.AppView.FLET_APP, assets_dir=os.path.join(PROJECT_ROOT, "gui/assets") )
    except Exception as e_app_start: logger.critical("Failed start Flet app", exc_info=e_app_start)
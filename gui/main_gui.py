# gui/main_gui.py (Pillowで平行四辺形アイコン加工版)

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
from io import BytesIO # Pillowでのバイトデータ処理用

# ★ Pillow をインポート ★
try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:
    print("Pillow library not found. Please install it using: pip install Pillow")
    Image = None
    ImageDraw = None
    ImageOps = None


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
# (変更なし)
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

# --- ★ Pillow 画像加工関数 ★ ---
def create_parallelogram_avatar(image_bytes: bytes, width: int, height: int, skew_offset: int) -> Optional[str]:
    """Pillowを使って画像を平行四辺形に切り抜き、Base64 PNG文字列を返す"""
    if not Image or not ImageDraw or not ImageOps:
        logger.error("Pillow library is not available.")
        return None
    try:
        # 元画像を読み込み、指定サイズにリサイズ（アスペクト比維持、中央クロップ）
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        img = ImageOps.fit(img, (width, height), Image.Resampling.LANCZOS) # 中央クロップ＆リサイズ

        # 平行四辺形のマスクを作成
        mask = Image.new('L', (width, height), 0) # 黒背景のマスク
        draw = ImageDraw.Draw(mask)
        # 平行四辺形の頂点リスト（マスク用なので座標系は0,0から）
        polygon = [
            (skew_offset, 0),          # 左上 (ずらす)
            (width, 0),                # 右上 (ずらさない)
            (width - skew_offset, height), # 右下 (ずらす)
            (0, height)                # 左下 (ずらさない)
        ]
        draw.polygon(polygon, fill=255) # 白で平行四辺形を描画

        # 元画像にマスクを適用
        img.putalpha(mask)

        # # 傾きを補正（見た目上、傾いていない画像にする場合 - 必要なら）
        # # これは少し複雑なアフィン変換が必要になる
        # matrix = (1, skew_offset / height, 0, 0, 1, 0)
        # img = img.transform((width, height), Image.AFFINE, matrix, Image.BILINEAR)

        # Base64エンコードされたPNG文字列を返す
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
        return img_str

    except Exception as e:
        logger.error("Error processing image with Pillow", exc_info=e)
        return None

# --- Flet アプリケーション main 関数 ---
async def main(page: ft.Page):
    page.title = f"{config_manager.APP_NAME} Controller"; page.window_width = 900; page.window_height = 650
    page.vertical_alignment = ft.MainAxisAlignment.START; page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.padding = 0
    page.fonts = {"Noto Sans JP": "fonts/NotoSansJP-Regular.ttf", "Bebas Neue": "fonts/BebasNeue-Regular.ttf"}
    page.theme = ft.Theme(font_family="Noto Sans JP"); page.dark_theme = ft.Theme(font_family="Noto Sans JP")
    page.theme_mode = ft.ThemeMode.DARK

    is_in_critical_error_state = threading.Event(); is_in_critical_error_state.clear()

    # --- 定数定義 ---
    ICON_WIDTH = 200
    ICON_HEIGHT = 200
    SKEW_OFFSET = 40 # 平行四辺形の傾き

    # --- コントロール参照 ---
    # 左エリア
    bot_name_background = ft.Text("", size=130, font_family="Bebas Neue", weight=ft.FontWeight.BOLD, color=ft.Colors.with_opacity(0.08, ft.Colors.WHITE), selectable=False, no_wrap=True, rotate=ft.transform.Rotate(angle=math.pi / 2))
    # ★ アイコン表示を通常の ft.Image に変更 ★
    bot_icon_image = ft.Image(
        # src_base64 は初期ロード/更新時に設定
        width=ICON_WIDTH, # 平行四辺形の幅ではなく、元の画像の幅
        height=ICON_HEIGHT,
        fit=ft.ImageFit.CONTAIN, # CONTAIN の方が全体が見えるかも
    )
    # ★ クリック用の Container (形状は気にしない) ★
    bot_icon_container = ft.Container(
        content=bot_icon_image,
        width=ICON_WIDTH, # 見た目のサイズ
        height=ICON_HEIGHT,
        tooltip="クリックしてアイコンを変更",
        on_click=lambda e: handle_icon_container_click(e),
        # ink=True # なくてもOK
    )

    # 右エリア (メイン情報)
    bot_name_display_edit = ft.TextField(label="NAME", value="", label_style=ft.TextStyle(font_family="Bebas Neue", size=64), text_style=ft.TextStyle(font_family="Bebas Neue", size=48), border=ft.InputBorder.NONE, read_only=True, tooltip="クリックして編集")
    bot_status_label = ft.Text("Bot Status:", font_family="Bebas Neue", size=48)
    bot_status_text = ft.Text("INITIALIZING...", font_family="Bebas Neue", size=48, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE)
    bot_status_switch = ft.Switch(value=False, disabled=True, tooltip="Botを起動/停止")
    weather_text = ft.Text("Weather: ---", font_family="Bebas Neue", size=48)
    dm_next_time_text = ft.Text("Next DM: ---", font_family="Bebas Neue", size=48)
    dm_target_text = ft.Text("Target: ---", font_family="Bebas Neue", size=48)

    # 天気設定ウィジェット
    weather_auto_update_switch = ft.Switch(label="天気自動更新を有効にする", value=False)
    weather_interval_field = ft.TextField(label="自動更新間隔 (分)", value="", width=150, keyboard_type=ft.KeyboardType.NUMBER, input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]+"), tooltip="最低10分")

    # 右エリア (設定画面用)
    settings_view_switcher = ft.AnimatedSwitcher(content=ft.Column([ft.ProgressRing(), ft.Text("設定を読み込み中...")]), transition=ft.AnimatedSwitcherTransition.FADE, duration=300, reverse_duration=100, expand=True)

    # ログエリア
    latest_log_text = ft.Text("ログ: ---", size=12, opacity=0.7, no_wrap=True)
    log_output_list = ft.ListView(expand=True, spacing=5, auto_scroll=True, divider_thickness=1)
    log_detail_container = ft.Container(content=log_output_list, padding=ft.padding.only(top=10, bottom=10, left=15, right=15), border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.WHITE)), border_radius=ft.border_radius.all(5), height=200, visible=False, animate_opacity=300, animate_size=300)
    log_area = ft.Container(ft.Column([ft.Container(content=latest_log_text, ink=True, tooltip="クリックで詳細ログを展開/格納", on_click=lambda e: toggle_log_detail(e)), log_detail_container], spacing=5), padding=ft.padding.symmetric(horizontal=20, vertical=5), border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.with_opacity(0.1, ft.Colors.WHITE))))

    # 右端メニュー
    def open_menu(e): menu_sheet.open = True; page.update()
    menu_button = ft.IconButton(ft.Icons.MENU_ROUNDED, tooltip="設定メニュー", on_click=open_menu)

    # Snackbar
    status_snackbar = ft.SnackBar(content=ft.Text(""), open=False)

    # ローディングオーバーレイ
    loading_overlay = ft.Container(content=ft.Column([ft.ProgressRing(), ft.Text("読み込み中...", size=16)], horizontal_alignment=ft.CrossAxisAlignment.CENTER), alignment=ft.alignment.center, bgcolor=ft.Colors.with_opacity(0.7, ft.Colors.BLACK), visible=True, expand=True)

    # --- PubSub リスナー (ログ用) ---
    # (変更なし)
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
    # (変更なし)
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
    # (変更なし)
    def on_bot_status_update(payload: Optional[Dict[str, Any]]):
        if page.client_storage is None: return
        if payload is None: logger.warning("Received None payload in on_bot_status_update."); return
        logger.debug(f"Received bot_status_update payload: {payload}"); status = payload.get("status"); message = payload.get("message")
        if status: update_gui_status(status, message)
        else: logger.warning(f"Invalid status update payload (status is None or empty): {payload}")

    # --- PubSubの購読設定 ---
    # (変更なし)
    if pub: 
        try: pub.subscribe(on_log_message_received, 'log_message'); logger.info("Subscribed to 'log_message'."); pub.subscribe(on_bot_status_update, 'bot_status_update'); logger.info("Subscribed to 'bot_status_update'."); 
        except Exception as e_pubsub_sub: logger.error("Failed pubsub subscribe", exc_info=e_pubsub_sub)
    else: logger.warning("PubSub not available.")

    # --- ログ転送スレッド開始 ---
    # (変更なし)
    log_forward_thread = start_log_forwarding()

    # --- イベントハンドラ ---

    # Bot名編集関連
    # (変更なし)
    def handle_bot_name_click(e): bot_name_display_edit.read_only = False; bot_name_display_edit.border = ft.InputBorder.UNDERLINE; bot_name_display_edit.update(); bot_name_display_edit.focus()
    async def handle_bot_name_submit(e):
        new_name = bot_name_display_edit.value.strip(); bot_name_display_edit.read_only = True; bot_name_display_edit.border = ft.InputBorder.NONE; bot_name_display_edit.update(); current_name = "";
        if hasattr(bot_core, 'bot') and bot_core.bot and bot_core.bot.user: current_name = bot_core.bot.user.name
        if not new_name or new_name == current_name: logger.info("Bot name not changed."); bot_name_display_edit.value = current_name; bot_name_display_edit.update(); return
        confirm_dialog_name = ft.AlertDialog(modal=True, title=ft.Text("Bot名変更の確認"), content=ft.Text(f"Bot名を「{new_name}」に変更しますか？\n(Discordのレート制限にご注意ください)"), actions=[ft.TextButton("はい", on_click=lambda _: page.run_task(close_dialog_and_change_name(new_name, confirm_dialog_name))), ft.TextButton("いいえ", on_click=lambda _: close_dialog(confirm_dialog_name))], actions_alignment=ft.MainAxisAlignment.END); page.dialog = confirm_dialog_name; confirm_dialog_name.open = True; page.update()
    async def close_dialog_and_change_name(new_name, dialog): dialog.open = False; page.update(); await change_bot_profile(username=new_name)
    def close_dialog(dialog):
        dialog.open = False; current_name = "";
        if hasattr(bot_core, 'bot') and bot_core.bot and bot_core.bot.user: current_name = bot_core.bot.user.name
        bot_name_display_edit.value = current_name; bot_name_display_edit.read_only = True; bot_name_display_edit.border = ft.InputBorder.NONE; page.update()
    bot_name_display_edit.on_submit = handle_bot_name_submit; bot_name_display_edit.on_blur = lambda e: page.run_task(handle_bot_name_submit(e)); bot_name_display_edit.on_click = handle_bot_name_click

    # Botアイコン変更関連
    async def on_file_picker_result(e: ft.FilePickerResultEvent): await handle_icon_picked(e)
    file_picker = ft.FilePicker(on_result=on_file_picker_result); page.overlay.append(file_picker)
    # ★ Container の on_click でハンドラを設定 ★
    def handle_icon_container_click(e):
        if bot_core.is_bot_running(): logger.debug("Icon container clicked..."); file_picker.pick_files(dialog_title="Botアイコンを選択", allowed_extensions=["png", "jpg", "jpeg", "gif"], allow_multiple=False)
        else: show_snackbar("Botが停止中のためアイコンを変更できません。", "orange")
    bot_icon_container.on_click = handle_icon_container_click # ★ ハンドラ設定

    async def handle_icon_picked(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            selected_file = e.files[0]; image_path = selected_file.path; logger.info(f"Icon file selected: {image_path}")
            # ★ 元画像データを読み込む ★
            try:
                async with aiofiles.open(image_path, "rb") as f:
                    original_avatar_bytes = await f.read()
            except Exception as img_read_e:
                 logger.error(f"Error reading selected icon file: {image_path}", exc_info=img_read_e)
                 show_snackbar(f"アイコンファイルの読み込みエラー: {img_read_e}", "red")
                 return

            # ★ Pillowで加工して表示用データを作成 ★
            processed_avatar_b64 = create_parallelogram_avatar(original_avatar_bytes, ICON_WIDTH, ICON_HEIGHT, SKEW_OFFSET)

            confirm_dialog_icon = ft.AlertDialog(modal=True, title=ft.Text("アイコン変更の確認"),
                content=ft.Column([
                    ft.Text(f"この画像でアイコンを変更しますか？\n(レート制限注意)"),
                    # ★ 加工後のプレビューを表示 ★
                    ft.Image(src_base64=processed_avatar_b64 if processed_avatar_b64 else None, width=100, height=100, fit=ft.ImageFit.CONTAIN)
                ], tight=True),
                actions=[
                    # ★ 元画像のバイトデータを渡すように変更 ★
                    ft.TextButton("はい", on_click=lambda _: page.run_task(close_dialog_and_change_icon(original_avatar_bytes, confirm_dialog_icon))),
                    ft.TextButton("いいえ", on_click=lambda _: close_dialog(confirm_dialog_icon)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.dialog = confirm_dialog_icon
            confirm_dialog_icon.open = True
            page.update()
        else: logger.info("Icon selection cancelled.")

    # ★ 引数を avatar_bytes に変更 ★
    async def close_dialog_and_change_icon(avatar_bytes: bytes, dialog):
        dialog.open = False
        page.update()
        # ★ change_bot_profile に元画像のバイトデータを渡す ★
        await change_bot_profile(avatar=avatar_bytes)

    # ★ アイコン更新処理を Pillow 加工前提に修正 ★
    async def change_bot_profile(username: Optional[str] = None, avatar: Optional[bytes] = None):
        if not bot_core.is_bot_running() or not bot_core.bot: show_snackbar("Botが起動していないため変更できません。", "orange"); return
        show_snackbar("プロフィールを変更中...", "blue"); page.update()
        try:
            # ★ Discord APIには元画像を送信 ★
            await bot_core.bot.edit_profile(username=username, avatar=avatar)
            logger.info(f"Bot profile updated (username={username is not None}, avatar={avatar is not None})."); show_snackbar("プロフィールを変更しました。", "green");
            if username:
                bot_name_display_edit.value = username
                # 背景文字更新 (Textウィジェットを直接更新)
                if isinstance(bot_name_background, ft.Text): # RotateではなくText自体
                    bot_name_background.value = username
                    bot_name_background.update()
                bot_name_display_edit.update()
            if avatar:
                # ★ GUI表示は加工後の画像 ★
                processed_avatar_b64 = create_parallelogram_avatar(avatar, ICON_WIDTH, ICON_HEIGHT, SKEW_OFFSET)
                if processed_avatar_b64:
                    bot_icon_image.src_base64 = processed_avatar_b64
                    bot_icon_image.src = None
                    bot_icon_image.update()
                    logger.info("Updated icon display with processed image.")
                else:
                    logger.error("Failed to process avatar for display after successful API call.")
                    show_snackbar("API更新成功、表示用画像処理失敗", "orange") # エラー通知
        except discord.HTTPException as http_e:
            logger.error("Failed update profile (HTTPException)", exc_info=http_e)
            error_msg = f"変更失敗({http_e.status}): {http_e.text}"
            if http_e.status == 429:
                error_msg = "変更失敗: Discord APIレート制限です。しばらく待ってください。"
            show_snackbar(error_msg, "red")
        except Exception as e:
            logger.error("Failed update profile (Exception)", exc_info=e)
            show_snackbar(f"予期せぬエラー: {e}", "red")
        page.update()

    # (起動/停止スイッチ関連 - 変更なし)
    def handle_switch_change(e: ft.ControlEvent): # 型ヒントも付けるとより良い
        logger.info(f"Switch changed: {e.control.value}") # e.control でスイッチ自身にアクセス
        is_running = bot_core.is_bot_running()
        logger.debug(f"Current bot running state: {is_running}")

        if e.control.value and not is_running: # スイッチON かつ Bot停止中 -> 起動
            logger.info("Switch turned ON while bot stopped. Starting bot...")
            start_bot()
        elif not e.control.value and is_running: # スイッチOFF かつ Bot動作中 -> 停止
            logger.info("Switch turned OFF while bot running. Stopping bot...")
            stop_bot()
        else:
             logger.warning(f"Switch state ({e.control.value}) and bot state ({is_running}) mismatch or no action needed.")
             bot_status_switch.value = is_running # GUIのスイッチ表示を実際のBotの状態に強制的に合わせる
             bot_status_switch.update()

    bot_status_switch.on_change = handle_switch_change
    def start_bot():
        if is_in_critical_error_state.is_set(): logger.warning("Bot critical error."); show_snackbar("重大エラー。設定を保存・確認。", "orange"); page.update(); return
        logger.info("Attempting start bot..."); update_gui_status("starting");
        try: success = bot_core.start_bot_thread()
        except Exception as e_start: logger.error("Error starting bot thread", exc_info=e_start); update_gui_status("critical_error", f"スレッド起動エラー: {e_start}")
    def stop_bot():
        logger.info("Attempting stop bot..."); update_gui_status("stopping");
        try: bot_core.signal_stop_bot()
        except Exception as e_stop: logger.error("Error signaling stop", exc_info=e_stop); update_gui_status("error", f"停止信号エラー: {e_stop}")

    # (ログ表示切り替え - 変更なし)
    def toggle_log_detail(e): log_detail_container.visible = not log_detail_container.visible; log_detail_container.update();
    if log_detail_container.visible: log_output_list.update()

    # (Snackbar表示関数 - 変更なし)
    def show_snackbar(message: str, color: str): color_map = {"red": ft.Colors.RED_700, "green": ft.Colors.GREEN_700, "blue": ft.Colors.BLUE_700, "orange": ft.Colors.ORANGE_700}; status_snackbar.content = ft.Text(message); status_snackbar.bgcolor = color_map.get(color, ft.Colors.BLACK); status_snackbar.open = True; page.update()

    # --- 設定画面用のウィジェット ---
    # (基本設定 - 変更なし)
    token_field = ft.TextField(label="Discord Bot Token", password=True, can_reveal_password=True, value="", width=450); gemini_api_key_field = ft.TextField(label="Gemini API Key", password=True, can_reveal_password=True, value="", width=450); weather_api_key_field = ft.TextField(label="OpenWeatherMap API Key (Optional)", password=True, can_reveal_password=True, value="", width=450); delete_password_field = ft.TextField(label="History Delete Password (Optional)", password=True, can_reveal_password=True, value="", width=450); save_settings_button = ft.ElevatedButton("設定を保存", icon="save_rounded")
    def save_settings_clicked(e):
        logger.info("--- Save Basic Settings Button Clicked ---") # ★ 開始ログ
        save_successful = False
        token = None
        gemini_key = None
        weather_key = None
        delete_pass = None
        try:
            logger.debug("Getting values from fields...")
            token = token_field.value.strip() or None
            gemini_key = gemini_api_key_field.value.strip() or None
            weather_key = weather_api_key_field.value.strip() or None
            delete_pass = delete_password_field.value.strip() or None
            logger.debug(f"Values obtained: token_present={token is not None}, gemini_present={gemini_key is not None}, weather_present={weather_key is not None}, delete_pass_present={delete_pass is not None}")

            logger.debug("Updating secrets in memory (within config_manager.app_config)...")
            # setdefault を使ってキーが存在しない場合も安全に処理
            secrets_dict = config_manager.app_config.setdefault("secrets", {})
            secrets_dict["discord_token"] = token
            secrets_dict["gemini_api_key"] = gemini_key
            secrets_dict["weather_api_key"] = weather_key
            secrets_dict["delete_history_password"] = delete_pass
            logger.debug("Secrets updated in memory dict.")

            logger.debug("Calling config_manager.save_app_config()...")
            config_manager.save_app_config() # 同期保存を呼び出す
            logger.info("config_manager.save_app_config() call finished.") # ★ 呼び出し完了ログ

            is_in_critical_error_state.clear() # 成功したらエラー状態解除
            save_successful = True
            show_snackbar("基本設定を保存しました。", "green")

        except Exception as e_save:
            # ★ 保存処理中の予期せぬエラーを捕捉 ★
            logger.error("!!! Exception during save_settings_clicked !!!", exc_info=True)
            show_snackbar(f"設定の保存中にエラーが発生: {e_save}", "red")
        finally:
            # ★ 必ず実行されるログ ★
            logger.info(f"--- Save Basic Settings Finished (Success: {save_successful}) ---")

        # ★ 保存成否に関わらず、現在のBot状態に基づいてGUIステータスを更新 ★
        if save_successful:
            logger.info("Basic settings save reported as successful. Updating GUI status.")
            if bot_core.is_bot_running():
                update_gui_status("ready", "設定保存完了")
            else:
                update_gui_status("stopped", "設定保存完了") # エラー状態も解除されるはず
        else:
             logger.warning("Basic settings save reported as failed. GUI status not changed based on save.")
             # 保存失敗時は、現在のBot状態をそのまま表示し続ける
             # (例: もしクリティカルエラー中ならそのまま)
             if is_in_critical_error_state.is_set():
                  update_gui_status("critical_error", "設定保存失敗")
             elif bot_core.is_bot_running():
                  update_gui_status("ready", "設定保存失敗") # 動作中だった場合
             else:
                  update_gui_status("stopped", "設定保存失敗") # 停止中だった場合


        page.update() # 最後にページ更新
        
    save_settings_button.on_click = save_settings_clicked
    basic_settings_view = ft.Container(padding=20, content=ft.Column([ft.Text("基本設定 (APIキー等)", size=18, weight=ft.FontWeight.BOLD), token_field, gemini_api_key_field, weather_api_key_field, delete_password_field, ft.Divider(height=30), save_settings_button], spacing=20, horizontal_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO))

    # (他の設定画面プレースホルダー - 変更なし)
    def create_placeholder_view(title: str): return ft.Container(padding=20, content=ft.Column([ft.Text(title, size=18, weight=ft.FontWeight.BOLD), ft.Text("未実装"), ft.Text("(/configを使用)"), ], spacing=15, horizontal_alignment=ft.CrossAxisAlignment.CENTER))
    gemini_settings_view = create_placeholder_view("Gemini 設定"); prompt_settings_view = create_placeholder_view("プロンプト設定"); user_settings_view = create_placeholder_view("ユーザー設定"); channel_settings_view = create_placeholder_view("チャンネル設定"); random_dm_settings_view = create_placeholder_view("ランダムDM設定"); response_settings_view = create_placeholder_view("応答設定"); summary_settings_view = create_placeholder_view("要約設定")

    # (天気設定画面 - 変更なし)
    def save_weather_settings(e):
        logger.info("Save weather settings clicked.")
        weather_enabled = weather_auto_update_switch.value
        weather_interval = config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
        try:
            weather_interval_str = weather_interval_field.value.strip() or ""
            interval_val = int(weather_interval_str) if weather_interval_str.isdigit() else -1
            weather_interval = max(10, interval_val) if interval_val >= 1 else config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
            if weather_interval_field.value != str(weather_interval):
                weather_interval_field.value = str(weather_interval)
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
                    try:
                        weather_cog.update_auto_update_task_status()
                        logger.info("Notified WeatherCog.")
                    except Exception as e_notify:
                        logger.error("Error notifying WeatherCog", exc_info=e_notify)
                elif bot_core.is_bot_running():
                    logger.warning("WeatherCog not found/method missing.")
            show_snackbar("天気設定を保存しました。", "green")
            page.update()
        except Exception as e_save:
            logger.error("Failed save weather settings", exc_info=e_save)
            show_snackbar(f"天気設定の保存エラー: {e_save}", "red")
            page.update()
    weather_settings_view = ft.Container(
        padding=20,
        content=ft.Column([
            ft.Text("天気・気分設定", size=18, weight=ft.FontWeight.BOLD),
            weather_auto_update_switch,
            weather_interval_field,
            ft.ElevatedButton("天気設定を保存", icon="save", on_click=save_weather_settings)
        ], spacing=15)
    )

    # (右端メニュー - 変更なし)
    def change_view(view_key: str): logger.info(f"Changing view to: {view_key}"); view_map = {"main": main_info_column, "basic": basic_settings_view, "gemini": gemini_settings_view, "prompt": prompt_settings_view, "user": user_settings_view, "channel": channel_settings_view, "random_dm": random_dm_settings_view, "response": response_settings_view, "summary": summary_settings_view, "weather": weather_settings_view}; new_content = view_map.get(view_key, main_info_column); settings_view_switcher.content = new_content; menu_sheet.open = False; page.update()
    menu_items = [ft.ListTile(title=ft.Text("メイン情報"), leading=ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED), on_click=lambda _: change_view("main")), ft.ListTile(title=ft.Text("基本設定 (APIキー等)"), leading=ft.Icon(ft.Icons.KEY_ROUNDED), on_click=lambda _: change_view("basic")), ft.Divider(height=1), ft.ListTile(title=ft.Text("Gemini 設定"), leading=ft.Icon(ft.Icons.SETTINGS_ROUNDED), on_click=lambda _: change_view("gemini")), ft.ListTile(title=ft.Text("天気 設定"), leading=ft.Icon(ft.Icons.SUNNY), on_click=lambda _: change_view("weather")), ft.ListTile(title=ft.Text("プロンプト設定"), leading=ft.Icon(ft.Icons.EDIT_NOTE_ROUNDED), on_click=lambda _: change_view("prompt"))]
    menu_sheet = ft.BottomSheet(ft.Container(ft.Column(menu_items, tight=True, spacing=0), padding=ft.padding.symmetric(vertical=10)), open=False, enable_drag=True)

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

            # ★ デフォルトアイコンを加工して表示 ★
            try:
                default_icon_path = Path(PROJECT_ROOT) / "gui" / "assets" / "default_avatar.png"
                if default_icon_path.exists():
                    async with aiofiles.open(default_icon_path, "rb") as f:
                        default_icon_bytes = await f.read()
                    processed_b64 = create_parallelogram_avatar(default_icon_bytes, ICON_WIDTH, ICON_HEIGHT, SKEW_OFFSET)
                    if processed_b64:
                        bot_icon_image.src_base64 = processed_b64
                        bot_icon_image.src = None
                    else:
                        logger.error("Failed to process default avatar.")
                        bot_icon_image.src = "assets/default_avatar.png" # 加工失敗時は元画像
                else:
                    logger.warning(f"Default avatar not found at: {default_icon_path}")
                    bot_icon_image.src = None # 見つからない場合は何も表示しないか、別のプレースホルダー
            except Exception as icon_load_e:
                logger.error("Error loading/processing default avatar", exc_info=icon_load_e)
                bot_icon_image.src = None # エラー時

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
    # (変更なし)
    def on_window_event(e):
        if e.data == "close": logger.info("Window close event received.");
        if pub: 
            try: pub.unsubscribe(on_log_message_received, 'log_message'); pub.unsubscribe(on_bot_status_update, 'bot_status_update'); logger.info("Unsubscribed pubsub."); 
            except Exception as unsub_e: logger.error(f"Error unsubscribing: {unsub_e}")
        if bot_core.is_bot_running(): logger.info("Signaling bot stop..."); bot_core.signal_stop_bot(); bot_thread = getattr(bot_core, '_bot_thread', None);
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
                    bot_status_label,
                    ft.Container(content=bot_status_text), # 位置調整
                    bot_status_switch
                ],
                spacing=10, alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER
            ),
            weather_text, dm_next_time_text, dm_target_text,
        ], spacing=15,
    )
    settings_view_switcher.content = main_info_column # 初期表示

    # --- 全体のレイアウト構成 ---
    page_content = ft.Row(
        [
            ft.Container(
                ft.Stack([
                    ft.Container(bot_name_background, alignment=ft.alignment.center, expand=True),
                    # ★ GestureDetector でラップしたアイコンコンテナ ★
                    ft.Container(bot_icon_container, alignment=ft.alignment.center),
                ]),
                width=300, padding=ft.padding.only(left=20, right=10, top=20, bottom=20),
            ),
            ft.Container(
                ft.Column([
                    ft.Row([ft.Container(expand=True), menu_button]),
                    settings_view_switcher,
                    log_area
                ], expand=True, spacing=10),
                expand=True, padding=ft.padding.only(left=10, right=20, top=20, bottom=10),
            ),
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
    # ★ Pillowがなければエラーにする ★
    if not Image or not ImageDraw or not ImageOps:
        print("Pillow library is required but not installed. Please run: pip install Pillow")
        sys.exit(1)
    logger.info("Starting Flet application...")
    try: ft.app( target=main, view=ft.AppView.FLET_APP, assets_dir=os.path.join(PROJECT_ROOT, "gui/assets") )
    except Exception as e_app_start: logger.critical("Failed start Flet app", exc_info=e_app_start)
# utils/config_manager.py (定数定義追加、他は変更なし - 完全版)

import json
import os
from pathlib import Path
import logging
from collections import deque
import datetime
from typing import Dict, List, Any, Optional, Deque, Union
import asyncio
import uuid
from appdirs import user_config_dir, user_data_dir
from cryptography.fernet import Fernet, InvalidToken
import threading

logger = logging.getLogger(__name__)

# --- アプリケーション情報 ---
APP_NAME = "Discord_Chat_Bot" # ★ 要変更
APP_AUTHOR = "Toracats" # ★ 要変更

# --- パス設定 ---
CONFIG_BASE_DIR = Path(user_config_dir(APP_NAME, APP_AUTHOR))
PRIMARY_CONFIG_FILE = CONFIG_BASE_DIR / "app_config.json"
LOG_BASE_DIR = Path(user_data_dir(APP_NAME, APP_AUTHOR)) / "logs"
LOG_FILE = LOG_BASE_DIR / "bot.log"
LOCAL_PROMPTS_DIR = Path("prompts") # ★ 追加: ローカルのプロンプトディレクトリ
USER_DATA_DIR = Path(user_data_dir(APP_NAME, APP_AUTHOR)) # ★ 追加: ユーザーデータディレクトリ

# --- ユーザーデータディレクトリ内のファイル名 ---
HISTORY_FILENAME = "conversation_history.json" # ★ 追加
SUMMARIZED_HISTORY_FILENAME = "summarized_history.jsonl"
SUMMARIZED_HISTORY_FILE = USER_DATA_DIR / SUMMARIZED_HISTORY_FILENAME # ★ USER_DATA_DIRを使用

# --- 暗号化設定 ---
# 初回生成方法:
# 1. Pythonインタプリタを開く (python または python3)
# 2. from cryptography.fernet import Fernet
# 3. print(Fernet.generate_key().decode())
# 4. 表示されたキー文字列を下の ENCRYPTION_KEY の '' 内に貼り付ける
ENCRYPTION_KEY = b'TYptY24SJ9ZWuiN_4XRgGRSKXE0Wg9oUH4_HWuRamHI=' # 要変更
if ENCRYPTION_KEY == b'YOUR_GENERATED_FERNET_KEY_HERE': raise ValueError("Fernet key not configured.")
try: fernet = Fernet(ENCRYPTION_KEY)
except ValueError as e: logger.critical(f"Invalid Fernet key: {e}"); raise
SECRET_KEYS = ["discord_token", "gemini_api_key", "weather_api_key", "delete_history_password"]


# --- デフォルト設定値 ---
DEFAULT_MAX_HISTORY = 10; DEFAULT_MAX_RESPONSE_LENGTH = 1800
DEFAULT_PERSONA_PROMPT = "あなたは親切なAIアシスタントです。"; DEFAULT_RANDOM_DM_PROMPT = "最近どうですか？何か面白いことありましたか？"
DEFAULT_GENERATION_CONFIG = {"temperature": 0.9, "top_p": 1.0, "top_k": 1, "candidate_count": 1, "max_output_tokens": 1024}
DEFAULT_SAFETY_SETTINGS = [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_LOW_AND_ABOVE"}, {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE"}, {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_LOW_AND_ABOVE"}, {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_LOW_AND_ABOVE"}]
DEFAULT_GEMINI_CONFIG = {"model_name": "gemini-2.0-flash", "safety_settings": DEFAULT_SAFETY_SETTINGS}
DEFAULT_RANDOM_DM_CONFIG = {"enabled": False, "min_interval": 21600, "max_interval": 172800, "stop_start_hour": 23, "stop_end_hour": 7, "last_interaction": None, "next_send_time": None}
DEFAULT_SUMMARY_MODEL = "gemini-2.0-flash"; DEFAULT_SUMMARY_MAX_TOKENS = 4000
DEFAULT_SUMMARY_GENERATION_CONFIG = {"temperature": 0.5, "top_p": 1.0, "top_k": 1, "candidate_count": 1, "max_output_tokens": 512}
# ★↓↓↓ 天気自動更新のデフォルト値を追加 ↓↓↓★
DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES = 60 # デフォルト60分
DEFAULT_WEATHER_AUTO_UPDATE_ENABLED = True # デフォルトは有効
# ★↑↑↑ 天気自動更新のデフォルト値を追加 ↑↑↑★
GLOBAL_HISTORY_KEY = "global_history"

# --- 新しいデフォルト設定構造 ---
DEFAULT_APP_CONFIG = {
    "secrets": {"discord_token": None, "gemini_api_key": None, "weather_api_key": None, "delete_history_password": None},
    "bot_settings": {"max_history": DEFAULT_MAX_HISTORY, "max_response_length": DEFAULT_MAX_RESPONSE_LENGTH},
    "user_data": {}, "channel_settings": {}, "gemini_config": DEFAULT_GEMINI_CONFIG.copy(),
    "generation_config": DEFAULT_GENERATION_CONFIG.copy(),
    "summary_config": {"summary_model_name": DEFAULT_SUMMARY_MODEL, "summary_max_prompt_tokens": DEFAULT_SUMMARY_MAX_TOKENS, "summary_generation_config": DEFAULT_SUMMARY_GENERATION_CONFIG.copy()},
    "weather_config": { # ★ weather_config セクションを拡張
        "last_location": None,
        "auto_update_enabled": DEFAULT_WEATHER_AUTO_UPDATE_ENABLED, # ★ 自動更新有効フラグ
        "auto_update_interval": DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES # ★ 自動更新間隔(分)
    },
}

# --- データ保持用変数 ---
app_config: Dict[str, Any] = {}
conversation_history: Dict[str, Deque[Dict[str, Any]]] = {}
persona_prompt: str = ""
random_dm_prompt: str = ""
file_access_lock = asyncio.Lock()
user_data_lock = threading.Lock()

# --- 暗号化/復号ヘルパー (変更なし) ---
def encrypt_string(plain_text: Optional[str]) -> str:
    if not plain_text: return ""
    try: return fernet.encrypt(plain_text.encode('utf-8')).decode('utf-8')
    except Exception as e: logger.error("Encryption failed", exc_info=e); return ""
def decrypt_string(encrypted_text: Optional[str]) -> str:
    if not encrypted_text: return ""
    try: return fernet.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except InvalidToken: logger.warning(f"Decryption failed: Invalid token."); return ""
    except Exception as e: logger.error("Decryption failed", exc_info=e); return ""

# --- ロード関数 (変更なし) ---
def _load_primary_config() -> Dict[str, Any]:
    config = DEFAULT_APP_CONFIG.copy()
    try:
        if PRIMARY_CONFIG_FILE.exists() and PRIMARY_CONFIG_FILE.is_file():
            with open(PRIMARY_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                def _recursive_update(d, u):
                    for k, v in u.items(): d[k] = _recursive_update(d.get(k, {}), v) if isinstance(v, dict) else v
                    return d
                config = _recursive_update(config, loaded_data)
                logger.info(f"Loaded primary config: {PRIMARY_CONFIG_FILE}")
                if "secrets" in config and isinstance(config["secrets"], dict):
                    secrets_section = config["secrets"]
                    for key in SECRET_KEYS: secrets_section[key] = decrypt_string(secrets_section.get(key))
                else: logger.warning("'secrets' section missing/invalid."); config["secrets"] = DEFAULT_APP_CONFIG["secrets"].copy()
        else: logger.warning(f"Primary config not found: {PRIMARY_CONFIG_FILE}. Creating default."); _save_primary_config(config)
    except json.JSONDecodeError: logger.error(f"Decode error: {PRIMARY_CONFIG_FILE}. Using defaults."); config = DEFAULT_APP_CONFIG.copy()
    except Exception as e: logger.error(f"Load primary config error: {PRIMARY_CONFIG_FILE}", exc_info=e); config = DEFAULT_APP_CONFIG.copy()
    if "user_data" in config and isinstance(config["user_data"], dict):
        for uid, u_data in config["user_data"].items():
            if "random_dm" in u_data and isinstance(u_data["random_dm"], dict):
                rdm_conf = u_data["random_dm"]; default_rdm = DEFAULT_RANDOM_DM_CONFIG.copy(); default_rdm.update(rdm_conf); u_data["random_dm"] = default_rdm
                for key in ["last_interaction", "next_send_time"]:
                    iso_str = default_rdm.get(key)
                    if iso_str and isinstance(iso_str, str):
                        try: dt_obj = datetime.datetime.fromisoformat(iso_str); default_rdm[key] = dt_obj.astimezone() if dt_obj.tzinfo is None else dt_obj
                        except ValueError: logger.warning(f"Parse dt failed {key} user {uid}: {iso_str}"); default_rdm[key] = None
                    elif isinstance(iso_str, datetime.datetime): default_rdm[key] = iso_str.astimezone()
                    else: default_rdm[key] = None
    return config
def _load_text(filename: str, default: str = "") -> str:
    user_filepath = CONFIG_BASE_DIR / "prompts" / filename; content = None
    try:
        if user_filepath.exists() and user_filepath.is_file():
            with open(user_filepath, 'r', encoding='utf-8') as f: content = f.read(); logger.info(f"Loaded text user: {user_filepath}")
            return content
    except Exception as e: logger.error(f"Load text user error: {user_filepath}", exc_info=e)
    local_filepath = LOCAL_PROMPTS_DIR / filename
    try:
        if local_filepath.exists() and local_filepath.is_file():
            with open(local_filepath, 'r', encoding='utf-8') as f: content = f.read(); logger.info(f"Loaded text local: {local_filepath}")
            return content
        else: logger.warning(f"Prompt '{filename}' not found. Using default.");
        try: 
            user_filepath.parent.mkdir(parents=True, exist_ok=True);
            with open(user_filepath, 'w', encoding='utf-8') as f: f.write(default); logger.info(f"Saved default prompt user: {user_filepath}")
        except Exception as save_e: logger.error(f"Save default prompt error: {user_filepath}", exc_info=save_e)
        return default
    except Exception as e: logger.error(f"Load text local error: {local_filepath}", exc_info=e); return default
def _load_json(filename: str, default: Any = {}) -> Any:
    data_filepath = Path(user_data_dir(APP_NAME, APP_AUTHOR)) / filename
    try:
        if data_filepath.exists() and data_filepath.is_file():
            with open(data_filepath, 'r', encoding='utf-8') as f:
                try: data = json.load(f); logger.info(f"Loaded JSON: {data_filepath}"); return data
                except json.JSONDecodeError: logger.error(f"Decode error: {data_filepath}. Creating default."); _save_json(filename, default); return default.copy()
        else: logger.warning(f"Not found: {data_filepath}. Creating default."); _save_json(filename, default); return default.copy()
    except Exception as e: logger.error(f"Load JSON error: {data_filepath}", exc_info=e); return default.copy()
def load_all_configs():
    global app_config, conversation_history, persona_prompt, random_dm_prompt
    CONFIG_BASE_DIR.mkdir(parents=True, exist_ok=True); LOG_BASE_DIR.mkdir(parents=True, exist_ok=True)
    Path(user_data_dir(APP_NAME, APP_AUTHOR)).mkdir(parents=True, exist_ok=True); (CONFIG_BASE_DIR / "prompts").mkdir(parents=True, exist_ok=True)
    app_config = _load_primary_config()
    loaded_history_data = _load_json(HISTORY_FILENAME); max_hist = app_config.get("bot_settings", {}).get('max_history', DEFAULT_MAX_HISTORY)
    global_hist_list = loaded_history_data.get(GLOBAL_HISTORY_KEY, []); dq: Deque[Dict[str, Any]] = deque(maxlen=max_hist)
    if isinstance(global_hist_list, list):
        for entry in global_hist_list:
            if isinstance(entry, dict):
                try:
                    if "timestamp" in entry and isinstance(entry["timestamp"], str):
                        try: dt_obj = datetime.datetime.fromisoformat(entry["timestamp"]); entry["timestamp"] = dt_obj.astimezone() if dt_obj.tzinfo is None else dt_obj
                        except ValueError: logger.warning(f"Parse ts history failed: {entry.get('timestamp')}"); entry["timestamp"] = None
                    elif isinstance(entry.get("timestamp"), datetime.datetime): entry["timestamp"] = entry["timestamp"].astimezone()
                    else: entry["timestamp"] = None
                    entry.setdefault("role", None); entry.setdefault("parts", []); entry.setdefault("channel_id", None); entry.setdefault("interlocutor_id", None); entry.setdefault("current_interlocutor_id", None); entry.setdefault("entry_id", str(uuid.uuid4()))
                    if entry["role"] and entry["interlocutor_id"] is not None: dq.append(entry)
                    else: logger.warning(f"Skip history missing info: {entry.get('entry_id')}")
                except (ValueError, TypeError, KeyError) as e: logger.warning(f"Skip invalid history entry: {entry.get('entry_id')} - Error: {e}")
            else: logger.warning(f"Skip non-dict history entry: {entry}")
    else: logger.warning(f"Invalid history format: {type(loaded_history_data)}")
    conversation_history = {GLOBAL_HISTORY_KEY: dq}
    persona_prompt = _load_text("persona_prompt.txt", DEFAULT_PERSONA_PROMPT); random_dm_prompt = _load_text("random_dm_prompt.txt", DEFAULT_RANDOM_DM_PROMPT)
    if not SUMMARIZED_HISTORY_FILE.exists():
        try: SUMMARIZED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True); SUMMARIZED_HISTORY_FILE.touch(); logger.info(f"Created empty summary file: {SUMMARIZED_HISTORY_FILE}")
        except Exception as e: logger.error(f"Create summary file error: {SUMMARIZED_HISTORY_FILE}", exc_info=e)
    logger.info("All configurations and data loaded.")

# --- 保存関数 (変更なし) ---
def _save_primary_config(config_data: Dict[str, Any]):
    data_to_save = None
    with user_data_lock:
        def complex_serializer_encrypt(obj):
            if isinstance(obj, datetime.datetime): return obj.isoformat()
            if isinstance(obj, deque): return list(obj)
            if isinstance(obj, uuid.UUID): return str(obj)
            try: json.dumps(obj); return obj
            except TypeError: return str(obj)
        serializable_data = json.loads(json.dumps(config_data, default=complex_serializer_encrypt))
        if "secrets" in serializable_data and isinstance(serializable_data["secrets"], dict):
            secrets_section = serializable_data["secrets"]
            for key in SECRET_KEYS: plain_value = secrets_section.get(key); secrets_section[key] = encrypt_string(plain_value) if plain_value else ""
        else: logger.error("Invalid 'secrets' section during save."); serializable_data["secrets"] = {key: "" for key in SECRET_KEYS}
        data_to_save = serializable_data
    if data_to_save is None: logger.error("Failed to prepare data for saving."); return
    try:
        PRIMARY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True); temp_filepath = PRIMARY_CONFIG_FILE.with_suffix(PRIMARY_CONFIG_FILE.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f: json.dump(data_to_save, f, indent=4, ensure_ascii=False)
        os.replace(temp_filepath, PRIMARY_CONFIG_FILE); logger.info(f"Saved primary config: {PRIMARY_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Save primary config error: {PRIMARY_CONFIG_FILE}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists():
            try: os.remove(temp_filepath)
            except OSError as remove_e: logger.error(f"Remove temp file error {temp_filepath}", exc_info=remove_e)
def _save_json(filename: str, data: Any):
    data_filepath = Path(user_data_dir(APP_NAME, APP_AUTHOR)) / filename
    try:
        def complex_serializer(obj):
            if isinstance(obj, datetime.datetime): return obj.isoformat()
            if isinstance(obj, deque): return list(obj)
            if isinstance(obj, uuid.UUID): return str(obj)
            try: json.dumps(obj); return obj
            except TypeError: return str(obj)
        data_filepath.parent.mkdir(parents=True, exist_ok=True); temp_filepath = data_filepath.with_suffix(data_filepath.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False, default=complex_serializer)
        os.replace(temp_filepath, data_filepath); logger.debug(f"Saved JSON: {data_filepath}")
    except Exception as e:
        logger.error(f"Save JSON error: {data_filepath}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists():
             try: os.remove(temp_filepath)
             except OSError as remove_e: logger.error(f"Remove temp file error {temp_filepath}", exc_info=remove_e)
def _save_text(filename: str, text: str):
    user_filepath = CONFIG_BASE_DIR / "prompts" / filename
    try:
        user_filepath.parent.mkdir(parents=True, exist_ok=True); temp_filepath = user_filepath.with_suffix(user_filepath.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f: f.write(text)
        os.replace(temp_filepath, user_filepath); logger.debug(f"Saved text: {user_filepath}")
    except Exception as e:
        logger.error(f"Save text error: {user_filepath}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists():
             try: os.remove(temp_filepath)
             except OSError as remove_e: logger.error(f"Remove temp file error {temp_filepath}", exc_info=remove_e)
def save_app_config(): _save_primary_config(app_config)
def save_persona_prompt(): _save_text("persona_prompt.txt", persona_prompt)
def save_random_dm_prompt(): _save_text("random_dm_prompt.txt", random_dm_prompt)
async def save_conversation_history_nolock():
    try: 
        async with file_access_lock: _save_json(HISTORY_FILENAME, conversation_history)
    except Exception as e: logger.error("Save history error", exc_info=e)

# --- 設定ファイル再読み込み関数 ---
def reload_primary_config():
    """プライマリ設定ファイルを再読み込みし、app_config を更新する"""
    global app_config
    logger.info("Reloading primary configuration from file...")
    # ★ ロックは不要 (読み取りとメモリ更新のみ)
    reloaded_config = _load_primary_config() # 復号も内部で行われる
    # ★ user_data 部分はスレッドセーフに更新
    with user_data_lock:
         app_config = reloaded_config # ロードした内容でメモリを更新
    logger.info("Primary configuration reloaded.")

# --- 設定値取得関数 (★ 天気関連ゲッター追加) ---
def get_discord_token() -> Optional[str]: return app_config.get("secrets", {}).get("discord_token")
def get_gemini_api_key() -> Optional[str]: return app_config.get("secrets", {}).get("gemini_api_key")
def get_weather_api_key() -> Optional[str]: return app_config.get("secrets", {}).get("weather_api_key")
def get_delete_history_password() -> Optional[str]: return app_config.get("secrets", {}).get("delete_history_password")
def get_max_history() -> int: return app_config.get("bot_settings", {}).get('max_history', DEFAULT_MAX_HISTORY)
def get_max_response_length() -> int: return app_config.get("bot_settings", {}).get('max_response_length', DEFAULT_MAX_RESPONSE_LENGTH)
def get_nickname(user_id: int) -> Optional[str]:
    with user_data_lock: return app_config.get("user_data", {}).get(str(user_id), {}).get("nickname")
def get_all_user_data() -> Dict[str, Dict[str, Any]]:
     with user_data_lock: return app_config.get("user_data", {}).copy()
def get_allowed_channels(server_id: str) -> List[int]: return app_config.get("channel_settings", {}).get(str(server_id), [])
def get_all_channel_settings() -> Dict[str, List[int]]: return app_config.get("channel_settings", {}).copy()
def get_model_name() -> str: return app_config.get("gemini_config", {}).get('model_name', DEFAULT_GEMINI_CONFIG['model_name'])
def get_safety_settings_list() -> List[Dict[str, str]]: return app_config.get("gemini_config", {}).get('safety_settings', DEFAULT_SAFETY_SETTINGS.copy())
def get_generation_config_dict() -> Dict[str, Any]: return app_config.get("generation_config", {}).copy()
def get_persona_prompt() -> str: return persona_prompt
def get_random_dm_prompt() -> str: return random_dm_prompt
def get_default_random_dm_config() -> Dict[str, Any]: return DEFAULT_RANDOM_DM_CONFIG.copy()
def get_global_history() -> Deque[Dict[str, Any]]:
    max_hist = get_max_history(); global_deque = conversation_history.get(GLOBAL_HISTORY_KEY)
    if not isinstance(global_deque, deque) or global_deque.maxlen != max_hist:
        current_items = list(global_deque or []); conversation_history[GLOBAL_HISTORY_KEY] = deque(current_items, maxlen=max_hist)
    return conversation_history[GLOBAL_HISTORY_KEY]
def get_all_history() -> Dict[str, Deque[Dict[str, Any]]]: return conversation_history
def get_last_weather_location() -> Optional[str]: return app_config.get("weather_config", {}).get("last_location")
def get_weather_auto_update_enabled() -> bool: return app_config.get("weather_config", {}).get("auto_update_enabled", DEFAULT_WEATHER_AUTO_UPDATE_ENABLED) # ★ 追加
def get_weather_auto_update_interval() -> int: interval = app_config.get("weather_config", {}).get("auto_update_interval", DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES); return max(10, interval) if isinstance(interval, int) else DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES # ★ 追加
def get_all_user_identifiers() -> Dict[int, str]:
    identifiers = {};
    with user_data_lock: user_data_dict = app_config.get("user_data", {}).copy()
    for uid_str, u_data in user_data_dict.items():
        try: uid = int(uid_str); nickname = u_data.get("nickname"); identifiers[uid] = nickname if nickname else f"User {uid}"
        except ValueError: logger.warning(f"Invalid user ID: {uid_str}")
    return identifiers
def get_summary_model_name() -> str:
    # ★ app_config から取得するように修正 ★
    return app_config.get("summary_config", {}).get('summary_model_name', DEFAULT_SUMMARY_MODEL)
def get_summary_max_prompt_tokens() -> int:
    # ★ app_config から取得するように修正 ★
    return app_config.get("summary_config", {}).get('summary_max_prompt_tokens', DEFAULT_SUMMARY_MAX_TOKENS)
def get_summary_generation_config_dict() -> Dict[str, Any]:
     # ★ app_config から取得するように修正 ★
     return app_config.get("summary_config", {}).get('summary_generation_config', DEFAULT_SUMMARY_GENERATION_CONFIG.copy())

# --- 設定値更新関数 (メモリ上のapp_configを変更) (★ 天気関連追加) ---
def update_secret_in_memory(key: str, value: Optional[str]):
    if key in SECRET_KEYS: app_config.setdefault("secrets", {})[key] = value; logger.debug(f"Updated secret '{key}' in memory.")
    else: logger.warning(f"Attempt update non-secret key '{key}'")
def update_max_history_in_memory(new_length: int):
    global conversation_history
    if new_length >= 0:
        app_config.setdefault("bot_settings", {})['max_history'] = new_length; global_deque = conversation_history.get(GLOBAL_HISTORY_KEY)
        if isinstance(global_deque, deque): conversation_history[GLOBAL_HISTORY_KEY] = deque(global_deque, maxlen=new_length)
        else: conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=new_length)
        logger.debug(f"Updated max_history to {new_length} in memory.")
    else: logger.warning(f"Invalid max_history: {new_length}")
def update_nickname_in_memory(user_id: int, nickname: str):
    user_id_str = str(user_id); 
    with user_data_lock: app_config.setdefault("user_data", {}).setdefault(user_id_str, {})["nickname"] = nickname
    logger.debug(f"Updated nickname for user {user_id} in memory.")
def remove_nickname_in_memory(user_id: int) -> bool:
    user_id_str = str(user_id); removed = False
    with user_data_lock:
        user_data_dict = app_config.get("user_data", {});
        if user_id_str in user_data_dict and "nickname" in user_data_dict[user_id_str]:
            del user_data_dict[user_id_str]["nickname"];
            if not user_data_dict[user_id_str]: del user_data_dict[user_id_str]
            removed = True; logger.debug(f"Removed nickname for user {user_id} in memory.")
    return removed
def update_weather_auto_update_enabled_in_memory(enabled: bool): # ★ 追加
    if isinstance(enabled, bool): app_config.setdefault("weather_config", {})["auto_update_enabled"] = enabled; logger.debug(f"Updated weather auto update enabled to {enabled} in memory.")
    else: logger.warning(f"Invalid type for weather auto update enabled: {type(enabled)}")
def update_weather_auto_update_interval_in_memory(interval_minutes: int): # ★ 追加
    if isinstance(interval_minutes, int) and interval_minutes >= 1:
        if interval_minutes < 10: logger.warning(f"Weather auto update interval {interval_minutes} min might be too frequent.")
        app_config.setdefault("weather_config", {})["auto_update_interval"] = interval_minutes; logger.debug(f"Updated weather auto update interval to {interval_minutes} minutes in memory.")
    else: logger.warning(f"Invalid value for weather auto update interval: {interval_minutes}")

# --- 履歴操作 (変更なし) ---
async def add_history_entry_async( current_interlocutor_id: int, channel_id: Optional[int], role: str, parts_dict: List[Dict[str, Any]], entry_author_id: int ) -> Optional[Dict[str, Any]]:
    if role not in ["user", "model"]: logger.error(f"Invalid role '{role}'"); return None
    max_hist = get_max_history(); pushed_out_entry = None
    async with file_access_lock:
        global_deque = get_global_history();
        if len(global_deque) == max_hist and max_hist > 0: pushed_out_entry = global_deque[0].copy(); logger.debug(f"History full push out: {pushed_out_entry.get('entry_id')}")
        entry = {"entry_id": str(uuid.uuid4()), "role": role, "parts": parts_dict, "channel_id": channel_id, "interlocutor_id": entry_author_id, "current_interlocutor_id": current_interlocutor_id, "timestamp": datetime.datetime.now().astimezone()}
        global_deque.append(entry); logger.debug(f"Appended entry {entry['entry_id']}. History len: {len(global_deque)}")
        await save_conversation_history_nolock()
    return pushed_out_entry
async def clear_all_history_async():
    async with file_access_lock: get_global_history().clear(); await save_conversation_history_nolock()
    logger.warning("Cleared all global conversation history.")
async def clear_user_history_async(target_user_id: int) -> int:
    cleared_count = 0
    async with file_access_lock:
        global_deque = get_global_history();
        if not global_deque: return 0; original_len = len(global_deque)
        new_deque = deque(maxlen=global_deque.maxlen)
        for entry in list(global_deque):
             if entry.get("interlocutor_id") != target_user_id and entry.get("current_interlocutor_id") != target_user_id: new_deque.append(entry)
             else: logger.debug(f"Removing entry user {target_user_id}: {entry.get('entry_id')}")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0: conversation_history[GLOBAL_HISTORY_KEY] = new_deque; await save_conversation_history_nolock(); logger.info(f"Cleared {cleared_count} entries user {target_user_id}.")
        else: logger.debug(f"No entries user {target_user_id} found.")
    return cleared_count
async def clear_channel_history_async(channel_id: int) -> int:
    cleared_count = 0
    async with file_access_lock:
        global_deque = get_global_history();
        if not global_deque: return 0; original_len = len(global_deque)
        new_deque = deque(maxlen=global_deque.maxlen)
        for entry in list(global_deque):
            if entry.get("channel_id") != channel_id: new_deque.append(entry)
            else: logger.debug(f"Removing entry channel {channel_id}: {entry.get('entry_id')}")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0: conversation_history[GLOBAL_HISTORY_KEY] = new_deque; await save_conversation_history_nolock(); logger.info(f"Cleared {cleared_count} entries channel {channel_id}.")
        else: logger.debug(f"No entries channel {channel_id} found.")
    return cleared_count

# --- 要約DB操作関数 (変更なし) ---
async def load_summaries() -> List[Dict[str, Any]]:
    summaries = [];
    if not SUMMARIZED_HISTORY_FILE.exists(): return summaries
    try:
        async with file_access_lock:
            logger.debug(f"Loading summaries from {SUMMARIZED_HISTORY_FILE}...")
            temp_summaries = []
            with open(SUMMARIZED_HISTORY_FILE, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    try:
                        summary_entry = json.loads(line)
                        for ts_key in ["added_timestamp", "original_timestamp"]:
                             if ts_key in summary_entry and isinstance(summary_entry[ts_key], str):
                                 try: dt_obj = datetime.datetime.fromisoformat(summary_entry[ts_key]); summary_entry[ts_key] = dt_obj.astimezone() if dt_obj.tzinfo is None else dt_obj
                                 except ValueError: logger.warning(f"L{i+1}: Parse ts '{summary_entry[ts_key]}' key '{ts_key}'."); summary_entry[ts_key] = None
                        temp_summaries.append(summary_entry)
                    except json.JSONDecodeError: logger.warning(f"L{i+1}: Skip invalid JSON: {line.strip()}")
            summaries = temp_summaries
        logger.info(f"Loaded {len(summaries)} summaries from {SUMMARIZED_HISTORY_FILE}.")
        return summaries
    except Exception as e: logger.error(f"Load summaries error: {SUMMARIZED_HISTORY_FILE}", exc_info=e); return []
async def append_summary(summary_entry: Dict[str, Any]):
    try:
        async with file_access_lock:
            summary_id = summary_entry.get('summary_id', 'N/A'); logger.debug(f"Appending summary {summary_id} to {SUMMARIZED_HISTORY_FILE}...")
            def _serializer(obj):
                 if isinstance(obj, datetime.datetime): return obj.isoformat()
                 if isinstance(obj, uuid.UUID): return str(obj)
                 try: json.dumps(obj); return obj
                 except TypeError: return str(obj)
            SUMMARIZED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SUMMARIZED_HISTORY_FILE, 'a', encoding='utf-8') as f: json.dump(summary_entry, f, ensure_ascii=False, default=_serializer); f.write('\n')
        logger.info(f"Appended summary {summary_id} to {SUMMARIZED_HISTORY_FILE}.")
    except Exception as e: logger.error(f"Append summary error: {SUMMARIZED_HISTORY_FILE}", exc_info=e)
async def clear_summaries() -> bool:
    logger.warning(f"Attempt clear summary DB: {SUMMARIZED_HISTORY_FILE}")
    async with file_access_lock:
        try:
            if SUMMARIZED_HISTORY_FILE.exists(): os.remove(SUMMARIZED_HISTORY_FILE); SUMMARIZED_HISTORY_FILE.touch(); logger.warning(f"Cleared summary DB: {SUMMARIZED_HISTORY_FILE}"); return True
            else: logger.info("Summary DB not exist, nothing clear."); return True
        except Exception as e: logger.error(f"Clear summary DB error: {SUMMARIZED_HISTORY_FILE}", exc_info=e); return False

# --- 初期ロード ---
load_all_configs()
logger.info(f"Config Manager initialized. Config dir: {CONFIG_BASE_DIR}, Data dir: {user_data_dir(APP_NAME, APP_AUTHOR)}")
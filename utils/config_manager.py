# utils/config_manager.py (デフォルトファイル作成ロジック修正・ログ調整版)

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
import aiofiles
import aiofiles.os

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG) # 必要に応じて有効化

# --- アプリケーション情報 ---
APP_NAME = "Discord_Chat_Bot"
APP_AUTHOR = "Toracats"

# --- パス設定 ---
CONFIG_BASE_DIR = Path(user_config_dir(APP_NAME, APP_AUTHOR))
PRIMARY_CONFIG_FILE = CONFIG_BASE_DIR / "app_config.json"
LOG_BASE_DIR = Path(user_data_dir(APP_NAME, APP_AUTHOR)) / "logs"
LOG_FILE = LOG_BASE_DIR / "bot.log"
LOCAL_PROMPTS_DIR = Path("prompts")
USER_DATA_DIR = Path(user_data_dir(APP_NAME, APP_AUTHOR))

# --- ユーザーデータディレクトリ内のファイル名 ---
HISTORY_FILENAME = "conversation_history.json"
SUMMARIZED_HISTORY_FILENAME = "summarized_history.jsonl"
SUMMARIZED_HISTORY_FILE = USER_DATA_DIR / SUMMARIZED_HISTORY_FILENAME

# --- 暗号化設定 ---
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
DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES = 60
DEFAULT_WEATHER_AUTO_UPDATE_ENABLED = True
GLOBAL_HISTORY_KEY = "global_history"

DEFAULT_APP_CONFIG = {
    "secrets": {"discord_token": None, "gemini_api_key": None, "weather_api_key": None, "delete_history_password": None},
    "bot_settings": {"max_history": DEFAULT_MAX_HISTORY, "max_response_length": DEFAULT_MAX_RESPONSE_LENGTH},
    "user_data": {}, "channel_settings": {}, "gemini_config": DEFAULT_GEMINI_CONFIG.copy(),
    "generation_config": DEFAULT_GENERATION_CONFIG.copy(),
    "summary_config": {"summary_model_name": DEFAULT_SUMMARY_MODEL, "summary_max_prompt_tokens": DEFAULT_SUMMARY_MAX_TOKENS, "summary_generation_config": DEFAULT_SUMMARY_GENERATION_CONFIG.copy()},
    "weather_config": {
        "last_location": None,
        "auto_update_enabled": DEFAULT_WEATHER_AUTO_UPDATE_ENABLED,
        "auto_update_interval": DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
    },
}

# --- データ保持用変数 ---
app_config: Dict[str, Any] = {}
conversation_history: Dict[str, Deque[Dict[str, Any]]] = {}
persona_prompt: str = ""
random_dm_prompt: str = ""
file_access_lock = asyncio.Lock()
user_data_lock = threading.Lock()

# --- 暗号化/復号ヘルパー ---
def encrypt_string(plain_text: Optional[str]) -> str:
    if not plain_text: return ""
    try: return fernet.encrypt(plain_text.encode('utf-8')).decode('utf-8')
    except Exception as e: logger.error("Encryption failed", exc_info=e); return ""
def decrypt_string(encrypted_text: Optional[str]) -> str:
    if not encrypted_text: return ""
    try: return fernet.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except InvalidToken: logger.warning(f"Decryption failed: Invalid token."); return ""
    except Exception as e: logger.error("Decryption failed", exc_info=e); return ""

# --- 非同期JSON保存関数 ---
async def _save_json_async(filename: str, data: Any):
    data_filepath = USER_DATA_DIR / filename
    temp_filepath = data_filepath.with_suffix(data_filepath.suffix + '.tmp')
    logger.debug(f"[_save_json_async] Preparing to save {filename}...")
    try:
        def complex_serializer(obj):
            if isinstance(obj, datetime.datetime): return obj.isoformat()
            if isinstance(obj, deque): return list(obj)
            if isinstance(obj, uuid.UUID): return str(obj)
            try: json.dumps(obj); return obj
            except TypeError: return str(obj)
        json_string = json.dumps(data, indent=4, ensure_ascii=False, default=complex_serializer)
        logger.debug(f"[_save_json_async] Serialized data for {filename}.")
        data_filepath.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[_save_json_async] Opening temp file {temp_filepath}...")
        async with aiofiles.open(temp_filepath, mode='w', encoding='utf-8') as f:
            await f.write(json_string)
        logger.debug(f"[_save_json_async] Wrote to temp file {temp_filepath}.")
        await aiofiles.os.replace(temp_filepath, data_filepath)
        logger.info(f"Saved JSON async: {data_filepath}") # ★ INFOレベルに変更
    except Exception as e:
        logger.error(f"Save JSON async error: {data_filepath}", exc_info=e)
        if temp_filepath.exists():
             try: await aiofiles.os.remove(temp_filepath)
             except OSError as remove_e: logger.error(f"Remove temp file error {temp_filepath}", exc_info=remove_e)

# --- 非同期JSONロード関数 ---
async def _load_json_async(filename: str, default: Any = {}) -> Any:
    data_filepath = USER_DATA_DIR / filename
    logger.debug(f"[_load_json_async] Attempting to load {filename}...")
    try:
        # ★ ファイルが存在しない場合はデフォルトを返し、ファイル作成は行わない ★
        if not await aiofiles.os.path.exists(data_filepath) or not await aiofiles.os.path.isfile(data_filepath):
            logger.warning(f"File not found: {data_filepath}. Returning default value.")
            return default.copy()

        logger.debug(f"[_load_json_async] Opening file {data_filepath}...")
        async with aiofiles.open(data_filepath, mode='r', encoding='utf-8') as f:
            content = await f.read()
        logger.debug(f"[_load_json_async] Read file {data_filepath}.")
        try:
            data = json.loads(content)
            logger.info(f"Loaded JSON async: {data_filepath}")
            return data
        except json.JSONDecodeError as json_e:
            # ★ デコードエラーの場合もデフォルトを返し、ファイル上書きはしない ★
            logger.error(f"Decode error: {data_filepath}. Returning default value.", exc_info=json_e)
            return default.copy()
    except Exception as e:
        logger.error(f"Load JSON async error: {data_filepath}", exc_info=e)
        return default.copy()

# --- ロード関数 ---
def _load_primary_config() -> Dict[str, Any]:
    config = DEFAULT_APP_CONFIG.copy()
    logger.debug("[_load_primary_config] Starting load...")
    try:
        if PRIMARY_CONFIG_FILE.exists() and PRIMARY_CONFIG_FILE.is_file():
            logger.debug(f"[_load_primary_config] File exists: {PRIMARY_CONFIG_FILE}")
            with open(PRIMARY_CONFIG_FILE, 'r', encoding='utf-8') as f:
                content_peek = f.read(100); f.seek(0); logger.debug(f"[_load_primary_config] File content peek: {content_peek}...")
                loaded_data = json.load(f); logger.debug("[_load_primary_config] JSON loaded successfully.")
                def _recursive_update(d, u):
                    for k, v in u.items(): d[k] = _recursive_update(d.get(k, {}), v) if isinstance(v, dict) else v
                    return d
                config = _recursive_update(config, loaded_data); logger.info(f"Loaded primary config: {PRIMARY_CONFIG_FILE}")
                if "secrets" in config and isinstance(config["secrets"], dict):
                    secrets_section = config["secrets"]
                    for key in SECRET_KEYS: secrets_section[key] = decrypt_string(secrets_section.get(key))
                    # logger.debug(f"[_load_primary_config] Decrypted secrets peek: {config.get('secrets', {})}") # デバッグ時のみ
                else: logger.warning("'secrets' section missing/invalid."); config["secrets"] = DEFAULT_APP_CONFIG["secrets"].copy()
        else:
             logger.warning(f"Primary config not found: {PRIMARY_CONFIG_FILE}. Using default config values.")
             # ★ ファイルがない場合はデフォルトを使うだけで、ここでは作成しない ★
             # _save_primary_config(config)
    except json.JSONDecodeError as json_e: logger.error(f"Decode error in primary config: {PRIMARY_CONFIG_FILE}. Using defaults.", exc_info=json_e); config = DEFAULT_APP_CONFIG.copy()
    except Exception as e: logger.error(f"Load primary config error: {PRIMARY_CONFIG_FILE}. Using defaults.", exc_info=e); config = DEFAULT_APP_CONFIG.copy()
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
    logger.debug("[_load_primary_config] Load finished.")
    return config

def _load_text(filename: str, default: str = "") -> str:
    # (変更なし)
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

async def load_all_configs_async():
    # (変更なし)
    global app_config, conversation_history, persona_prompt, random_dm_prompt
    CONFIG_BASE_DIR.mkdir(parents=True, exist_ok=True); LOG_BASE_DIR.mkdir(parents=True, exist_ok=True)
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True); (CONFIG_BASE_DIR / "prompts").mkdir(parents=True, exist_ok=True)
    app_config = _load_primary_config()
    loaded_history_data = await _load_json_async(HISTORY_FILENAME)
    max_hist = app_config.get("bot_settings", {}).get('max_history', DEFAULT_MAX_HISTORY)
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
    if not await aiofiles.os.path.exists(SUMMARIZED_HISTORY_FILE):
        try:
            SUMMARIZED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(SUMMARIZED_HISTORY_FILE, mode='w') as f: await f.write("")
            logger.info(f"Created empty summary file: {SUMMARIZED_HISTORY_FILE}")
        except Exception as e: logger.error(f"Create summary file error: {SUMMARIZED_HISTORY_FILE}", exc_info=e)
    logger.info("All configurations and data loaded (async history/summary).")

# --- 保存関数 ---
def _save_primary_config(config_data: Dict[str, Any]):
    # (変更なし)
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

def _save_text(filename: str, text: str):
    # (変更なし)
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

async def save_conversation_history_nolock_async():
    # (変更なし)
    logger.debug("[save_conversation_history_nolock_async] Starting save...")
    try:
        await _save_json_async(HISTORY_FILENAME, conversation_history)
        logger.debug("[save_conversation_history_nolock_async] Save finished.")
    except Exception as e: logger.error("Save history async error", exc_info=e)

# --- 設定ファイル再読み込み関数 ---
def reload_primary_config():
    # (変更なし)
    global app_config
    logger.info("Reloading primary configuration from file...")
    reloaded_config = _load_primary_config()
    with user_data_lock:
         app_config = reloaded_config
    logger.info("Primary configuration reloaded.")

# --- 設定値取得関数 ---
# (変更なし - 全てのゲッターを記載)
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
def get_weather_auto_update_enabled() -> bool: return app_config.get("weather_config", {}).get("auto_update_enabled", DEFAULT_WEATHER_AUTO_UPDATE_ENABLED)
def get_weather_auto_update_interval() -> int: interval = app_config.get("weather_config", {}).get("auto_update_interval", DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES); return max(10, interval) if isinstance(interval, int) else DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES
def get_all_user_identifiers() -> Dict[int, str]:
    identifiers = {};
    with user_data_lock: user_data_dict = app_config.get("user_data", {}).copy()
    for uid_str, u_data in user_data_dict.items():
        try: uid = int(uid_str); nickname = u_data.get("nickname"); identifiers[uid] = nickname if nickname else f"User {uid}"
        except ValueError: logger.warning(f"Invalid user ID: {uid_str}")
    return identifiers
def get_summary_model_name() -> str: return app_config.get("summary_config", {}).get('summary_model_name', DEFAULT_SUMMARY_MODEL)
def get_summary_max_prompt_tokens() -> int: return app_config.get("summary_config", {}).get('summary_max_prompt_tokens', DEFAULT_SUMMARY_MAX_TOKENS)
def get_summary_generation_config_dict() -> Dict[str, Any]: return app_config.get("summary_config", {}).get('summary_generation_config', DEFAULT_SUMMARY_GENERATION_CONFIG.copy())

# --- 設定値更新関数 (メモリ上のapp_configを変更) ---
# (変更なし - 全てのセッターを記載)
def update_secret_in_memory(key: str, value: Optional[str]):
    if key in SECRET_KEYS: app_config.setdefault("secrets", {})[key] = value; logger.debug(f"Updated secret '{key}' in memory.")
    else: logger.warning(f"Attempt update non-secret key '{key}'")
async def update_max_history_async(new_length: int):
    global conversation_history
    if new_length >= 0:
        logger.debug(f"[update_max_history_async] Acquiring lock...")
        async with file_access_lock:
            logger.debug(f"[update_max_history_async] Lock acquired. Updating max_history to {new_length}.")
            app_config.setdefault("bot_settings", {})['max_history'] = new_length
            global_deque = conversation_history.get(GLOBAL_HISTORY_KEY)
            if isinstance(global_deque, deque): conversation_history[GLOBAL_HISTORY_KEY] = deque(global_deque, maxlen=new_length); logger.debug(f"[update_max_history_async] Resized existing deque.")
            else: conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=new_length); logger.debug(f"[update_max_history_async] Created new deque.")
            await save_conversation_history_nolock_async(); logger.debug(f"[update_max_history_async] History saved after update.")
        logger.debug(f"[update_max_history_async] Lock released.")
    else: logger.warning(f"Invalid max_history value: {new_length}")
async def update_nickname_async(user_id: int, nickname: str):
    user_id_str = str(user_id);
    with user_data_lock: app_config.setdefault("user_data", {}).setdefault(user_id_str, {})["nickname"] = nickname
    logger.debug(f"Updated nickname for user {user_id} in memory.")
    save_app_config()
async def remove_nickname_async(user_id: int) -> bool:
    user_id_str = str(user_id); removed = False
    with user_data_lock:
        user_data_dict = app_config.get("user_data", {});
        if user_id_str in user_data_dict and "nickname" in user_data_dict[user_id_str]:
            del user_data_dict[user_id_str]["nickname"];
            if not user_data_dict[user_id_str]: del user_data_dict[user_id_str]
            removed = True; logger.debug(f"Removed nickname for user {user_id} in memory.")
    if removed: save_app_config()
    return removed
def update_weather_auto_update_enabled_in_memory(enabled: bool):
    if isinstance(enabled, bool): app_config.setdefault("weather_config", {})["auto_update_enabled"] = enabled; logger.debug(f"Updated weather auto update enabled to {enabled} in memory.")
    else: logger.warning(f"Invalid type for weather auto update enabled: {type(enabled)}")
def update_weather_auto_update_interval_in_memory(interval_minutes: int):
    if isinstance(interval_minutes, int) and interval_minutes >= 1:
        if interval_minutes < 10: logger.warning(f"Weather auto update interval {interval_minutes} min might be too frequent.")
        app_config.setdefault("weather_config", {})["auto_update_interval"] = interval_minutes; logger.debug(f"Updated weather auto update interval to {interval_minutes} minutes in memory.")
    else: logger.warning(f"Invalid value for weather auto update interval: {interval_minutes}")
def update_safety_setting(category: str, threshold: str):
    gemini_conf = app_config.setdefault("gemini_config", DEFAULT_GEMINI_CONFIG.copy())
    safety_settings = gemini_conf.setdefault("safety_settings", DEFAULT_SAFETY_SETTINGS.copy())
    updated = False
    for setting in safety_settings:
        if setting.get("category") == category: setting["threshold"] = threshold; updated = True; break
    if not updated: safety_settings.append({"category": category, "threshold": threshold})
    logger.debug(f"Updated safety setting {category} to {threshold} in memory.")
    save_app_config()
def update_summary_model_name(model_name: str):
    summary_conf = app_config.setdefault("summary_config", {})
    summary_conf["summary_model_name"] = model_name
    logger.debug(f"Updated summary model name to {model_name} in memory.")
    save_app_config()
def update_summary_max_prompt_tokens(max_tokens: int):
    if isinstance(max_tokens, int) and max_tokens >= 0:
        summary_conf = app_config.setdefault("summary_config", {})
        summary_conf["summary_max_prompt_tokens"] = max_tokens
        logger.debug(f"Updated summary max prompt tokens to {max_tokens} in memory.")
        save_app_config()
    else: logger.warning(f"Invalid summary max prompt tokens value: {max_tokens}")
def update_summary_generation_config(key: str, value: Any):
     summary_conf = app_config.setdefault("summary_config", {})
     gen_conf = summary_conf.setdefault("summary_generation_config", DEFAULT_SUMMARY_GENERATION_CONFIG.copy())
     gen_conf[key] = value
     logger.debug(f"Updated summary generation config '{key}' to {value} in memory.")
     save_app_config()
async def update_random_dm_config_async(user_id: int, update_data: Dict[str, Any]):
    user_id_str = str(user_id)
    with user_data_lock:
        user_settings = app_config.setdefault("user_data", {}).setdefault(user_id_str, {}).setdefault("random_dm", DEFAULT_RANDOM_DM_CONFIG.copy())
        last_interaction_dt = update_data.pop("last_interaction", None)
        next_send_time_dt = update_data.pop("next_send_time", None)
        user_settings.update(update_data)
        if isinstance(last_interaction_dt, datetime.datetime): user_settings["last_interaction"] = last_interaction_dt.astimezone()
        if isinstance(next_send_time_dt, datetime.datetime): user_settings["next_send_time"] = next_send_time_dt.astimezone()
        elif next_send_time_dt is None: user_settings["next_send_time"] = None
    logger.debug(f"Updated random DM config for user {user_id} in memory.")
    save_app_config()

# --- 履歴操作 ---
async def add_history_entry_async( current_interlocutor_id: int, channel_id: Optional[int], role: str, parts_dict: List[Dict[str, Any]], entry_author_id: int ) -> Optional[Dict[str, Any]]:
    # (変更なし)
    if role not in ["user", "model"]: logger.error(f"Invalid role '{role}'"); return None
    max_hist = get_max_history(); pushed_out_entry = None
    entry_id = str(uuid.uuid4())
    entry = {"entry_id": entry_id, "role": role, "parts": parts_dict, "channel_id": channel_id, "interlocutor_id": entry_author_id, "current_interlocutor_id": current_interlocutor_id, "timestamp": datetime.datetime.now().astimezone()}
    logger.debug(f"[add_history_entry_async] Acquiring lock for entry {entry_id}...")
    async with file_access_lock:
        logger.debug(f"[add_history_entry_async] Lock acquired for entry {entry_id}.")
        global_deque = get_global_history();
        if len(global_deque) == max_hist and max_hist > 0: pushed_out_entry = global_deque[0].copy(); logger.debug(f"[add_history_entry_async] History full push out: {pushed_out_entry.get('entry_id')}")
        global_deque.append(entry); logger.debug(f"[add_history_entry_async] Appended entry {entry_id}. History len: {len(global_deque)}. Saving history...")
        await save_conversation_history_nolock_async(); logger.debug(f"[add_history_entry_async] History saved for entry {entry_id}.")
    logger.debug(f"[add_history_entry_async] Lock released for entry {entry_id}.")
    return pushed_out_entry

async def clear_all_history_async():
    # (変更なし)
    logger.debug("[clear_all_history_async] Acquiring lock...")
    async with file_access_lock:
        logger.debug("[clear_all_history_async] Lock acquired. Clearing deque..."); get_global_history().clear(); logger.debug("[clear_all_history_async] Deque cleared. Saving empty history...")
        await save_conversation_history_nolock_async(); logger.debug("[clear_all_history_async] Empty history saved.")
    logger.warning("Cleared all global conversation history."); logger.debug("[clear_all_history_async] Lock released.")

async def clear_user_history_async(target_user_id: int) -> int:
    # (変更なし)
    cleared_count = 0; logger.debug(f"[clear_user_history_async] Acquiring lock for user {target_user_id}...")
    async with file_access_lock:
        logger.debug(f"[clear_user_history_async] Lock acquired for user {target_user_id}."); global_deque = get_global_history();
        if not global_deque: logger.debug(f"[clear_user_history_async] Deque empty for user {target_user_id}."); return 0
        original_len = len(global_deque); new_deque = deque(maxlen=global_deque.maxlen); logger.debug(f"[clear_user_history_async] Filtering history for user {target_user_id}...")
        for entry in list(global_deque):
             if entry.get("interlocutor_id") != target_user_id and entry.get("current_interlocutor_id") != target_user_id: new_deque.append(entry)
             else: logger.debug(f"[clear_user_history_async] Removing entry {entry.get('entry_id')} for user {target_user_id}.")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0: conversation_history[GLOBAL_HISTORY_KEY] = new_deque; logger.debug(f"[clear_user_history_async] Filtered history. Saving new history for user {target_user_id}..."); await save_conversation_history_nolock_async(); logger.info(f"Cleared {cleared_count} entries user {target_user_id}.")
        else: logger.debug(f"No entries user {target_user_id} found.")
    logger.debug(f"[clear_user_history_async] Lock released for user {target_user_id}.")
    return cleared_count

async def clear_channel_history_async(channel_id: int) -> int:
    # (変更なし)
    cleared_count = 0; logger.debug(f"[clear_channel_history_async] Acquiring lock for channel {channel_id}...")
    async with file_access_lock:
        logger.debug(f"[clear_channel_history_async] Lock acquired for channel {channel_id}."); global_deque = get_global_history();
        if not global_deque: logger.debug(f"[clear_channel_history_async] Deque empty for channel {channel_id}."); return 0
        original_len = len(global_deque); new_deque = deque(maxlen=global_deque.maxlen); logger.debug(f"[clear_channel_history_async] Filtering history for channel {channel_id}...")
        for entry in list(global_deque):
            if entry.get("channel_id") != channel_id: new_deque.append(entry)
            else: logger.debug(f"[clear_channel_history_async] Removing entry {entry.get('entry_id')} for channel {channel_id}.")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0: conversation_history[GLOBAL_HISTORY_KEY] = new_deque; logger.debug(f"[clear_channel_history_async] Filtered history. Saving new history for channel {channel_id}..."); await save_conversation_history_nolock_async(); logger.info(f"Cleared {cleared_count} entries channel {channel_id}.")
        else: logger.debug(f"No entries channel {channel_id} found.")
    logger.debug(f"[clear_channel_history_async] Lock released for channel {channel_id}.")
    return cleared_count

# --- 要約DB操作関数 ---
async def load_summaries() -> List[Dict[str, Any]]:
    # (変更なし)
    summaries = []; logger.debug("[load_summaries] Checking summary file existence...")
    if not await aiofiles.os.path.exists(SUMMARIZED_HISTORY_FILE): logger.debug("[load_summaries] Summary file does not exist."); return summaries
    try:
        logger.debug("[load_summaries] Acquiring lock...");
        async with file_access_lock:
            logger.debug(f"[load_summaries] Lock acquired. Loading summaries async from {SUMMARIZED_HISTORY_FILE}..."); temp_summaries = []
            async with aiofiles.open(SUMMARIZED_HISTORY_FILE, mode='r', encoding='utf-8') as f:
                line_num = 0
                async for line in f:
                    line_num += 1
                    try:
                        summary_entry = json.loads(line)
                        for ts_key in ["added_timestamp", "original_timestamp"]:
                             if ts_key in summary_entry and isinstance(summary_entry[ts_key], str):
                                 try: dt_obj = datetime.datetime.fromisoformat(summary_entry[ts_key]); summary_entry[ts_key] = dt_obj.astimezone() if dt_obj.tzinfo is None else dt_obj
                                 except ValueError: logger.warning(f"L{line_num}: Parse ts '{summary_entry[ts_key]}' key '{ts_key}'."); summary_entry[ts_key] = None
                        temp_summaries.append(summary_entry)
                    except json.JSONDecodeError: logger.warning(f"L{line_num}: Skip invalid JSON: {line.strip()}")
            summaries = temp_summaries
        logger.info(f"Loaded {len(summaries)} summaries from {SUMMARIZED_HISTORY_FILE}."); logger.debug("[load_summaries] Lock released.")
        return summaries
    except Exception as e: logger.error(f"Load summaries error: {SUMMARIZED_HISTORY_FILE}", exc_info=e); return []

async def append_summary(summary_entry: Dict[str, Any]):
    # (変更なし)
    summary_id = summary_entry.get('summary_id', 'N/A'); logger.debug(f"[append_summary] Acquiring lock for summary {summary_id}...")
    try:
        async with file_access_lock:
            logger.debug(f"[append_summary] Lock acquired. Appending summary async {summary_id} to {SUMMARIZED_HISTORY_FILE}...")
            def _serializer(obj):
                 if isinstance(obj, datetime.datetime): return obj.isoformat()
                 if isinstance(obj, uuid.UUID): return str(obj)
                 try: json.dumps(obj); return obj
                 except TypeError: return str(obj)
            json_line = json.dumps(summary_entry, ensure_ascii=False, default=_serializer)
            SUMMARIZED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(SUMMARIZED_HISTORY_FILE, mode='a', encoding='utf-8') as f: await f.write(json_line + '\n')
            logger.info(f"Appended summary {summary_id} to {SUMMARIZED_HISTORY_FILE}.")
        logger.debug(f"[append_summary] Lock released for summary {summary_id}.")
    except Exception as e: logger.error(f"Append summary error: {SUMMARIZED_HISTORY_FILE}", exc_info=e)

async def clear_summaries() -> bool:
    # (変更なし)
    logger.warning(f"Attempt clear summary DB async: {SUMMARIZED_HISTORY_FILE}"); logger.debug("[clear_summaries] Acquiring lock...")
    async with file_access_lock:
        logger.debug("[clear_summaries] Lock acquired.")
        try:
            logger.debug("[clear_summaries] Checking existence...");
            if await aiofiles.os.path.exists(SUMMARIZED_HISTORY_FILE):
                logger.debug("[clear_summaries] Removing file..."); await aiofiles.os.remove(SUMMARIZED_HISTORY_FILE); logger.debug("[clear_summaries] Creating empty file...")
                async with aiofiles.open(SUMMARIZED_HISTORY_FILE, mode='w', encoding='utf-8') as f: await f.write("")
                logger.warning(f"Cleared summary DB async: {SUMMARIZED_HISTORY_FILE}"); logger.debug("[clear_summaries] Lock released."); return True
            else: logger.info("Summary DB not exist."); logger.debug("[clear_summaries] Lock released."); return True
        except Exception as e: logger.error(f"Clear summary DB async error: {SUMMARIZED_HISTORY_FILE}", exc_info=e); logger.debug("[clear_summaries] Lock released on error."); return False

# --- 初期ロード ---
# 呼び出し元で await load_all_configs_async() を実行
logger.info(f"Config Manager module loaded. Config dir: {CONFIG_BASE_DIR}, Data dir: {USER_DATA_DIR}")
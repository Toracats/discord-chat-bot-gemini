# utils/config_manager.py (ローカルタイムゾーン対応・全コード)

import json
import os
from pathlib import Path
import logging
from collections import deque
import datetime
# ★ timezone の代わりにローカルタイムゾーンを使うため、特別な import は不要
# from datetime import timezone
from typing import Dict, List, Any, Optional
import asyncio

logger = logging.getLogger(__name__)

CONFIG_DIR = Path("config")
PROMPTS_DIR = Path("prompts")
HISTORY_FILE = CONFIG_DIR / "conversation_history.json"
BOT_CONFIG_FILE = CONFIG_DIR / "bot_config.json"
USER_DATA_FILE = CONFIG_DIR / "user_data.json"
CHANNEL_SETTINGS_FILE = CONFIG_DIR / "channel_settings.json"
GEMINI_CONFIG_FILE = CONFIG_DIR / "gemini_config.json"
GENERATION_CONFIG_FILE = CONFIG_DIR / "generation_config.json"
WEATHER_CONFIG_FILE = CONFIG_DIR / "weather_config.json"

# --- デフォルト設定 ---
DEFAULT_MAX_HISTORY = 20
DEFAULT_MAX_RESPONSE_LENGTH = 1800
DEFAULT_RANDOM_DM_PROMPT = "最近どうですか？何か面白いことありましたか？"
DEFAULT_PERSONA_PROMPT = "あなたは親切なAIアシスタントです。"
DEFAULT_GENERATION_CONFIG = {
    "temperature": 0.9,
    "top_p": 1.0,
    "top_k": 1,
    "candidate_count": 1,
    "max_output_tokens": 1024,
}
DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]
DEFAULT_GEMINI_CONFIG = {
    "model_name": "gemini-2.0-flash",
    "safety_settings": DEFAULT_SAFETY_SETTINGS
}
DEFAULT_RANDOM_DM_CONFIG = {
    "enabled": False, "min_interval": 3600 * 6, "max_interval": 86400 * 2,
    "stop_start_hour": 23, "stop_end_hour": 7,
    "last_interaction": None,
    "next_send_time": None,
}
GLOBAL_HISTORY_KEY = "global_history"

# --- データ保持用変数 ---
bot_settings: Dict[str, Any] = {}
user_data: Dict[str, Dict[str, Any]] = {}
channel_settings: Dict[str, List[int]] = {}
gemini_config: Dict[str, Any] = {}
generation_config: Dict[str, Any] = {}
conversation_history: Dict[str, deque] = {GLOBAL_HISTORY_KEY: deque(maxlen=DEFAULT_MAX_HISTORY)}
persona_prompt: str = ""
random_dm_prompt: str = ""
weather_config: Dict[str, Any] = {}
data_lock = asyncio.Lock()

# --- ロード関数 ---
def _load_json(filepath: Path, default: Any = {}) -> Any:
    """JSONファイルを安全に読み込む"""
    try:
        if filepath.exists() and filepath.is_file():
            with open(filepath, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    logger.info(f"Successfully loaded JSON from: {filepath}")
                    return data
                except json.JSONDecodeError:
                    logger.error(f"Error decoding JSON from {filepath}. Creating with default value.")
                    _save_json(filepath, default)
                    return default.copy()
        else:
            logger.warning(f"{filepath} not found. Creating with default value.")
            _save_json(filepath, default)
            return default.copy()
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}. Returning default value.")
        try: _save_json(filepath, default)
        except Exception as save_e: logger.error(f"Failed to save default config to {filepath} after load error.", exc_info=save_e)
        return default.copy()

def _load_text(filepath: Path, default: str = "") -> str:
    """テキストファイルを安全に読み込む"""
    logger.debug(f"Attempting to load text from: {filepath}")
    try:
        if filepath.exists() and filepath.is_file():
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.info(f"Successfully loaded text from: {filepath}")
                return content
        else:
             logger.warning(f"{filepath} not found or is not a file. Creating with default value.")
             _save_text(filepath, default)
             return default
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}. Returning default value.")
        try: _save_text(filepath, default)
        except Exception as save_e: logger.error(f"Failed to save default text to {filepath} after load error.", exc_info=save_e)
        return default

def load_all_configs():
    """すべての設定とデータをロードする"""
    global bot_settings, user_data, channel_settings, gemini_config, generation_config
    global conversation_history, persona_prompt, random_dm_prompt, weather_config

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- 各設定ファイルのロード ---
    loaded_bot_config = _load_json(BOT_CONFIG_FILE, {"max_history": DEFAULT_MAX_HISTORY, "max_response_length": DEFAULT_MAX_RESPONSE_LENGTH})
    bot_settings['max_history'] = loaded_bot_config.get('max_history', DEFAULT_MAX_HISTORY)
    bot_settings['max_response_length'] = loaded_bot_config.get('max_response_length', DEFAULT_MAX_RESPONSE_LENGTH)

    # user_data のロード (datetime aware ローカルTZに)
    loaded_user_data = _load_json(USER_DATA_FILE)
    temp_user_data = {}
    for uid, u_data in loaded_user_data.items():
        new_u_data = {}
        new_u_data["nickname"] = u_data.get("nickname")
        rdm_conf_loaded = u_data.get("random_dm", {})
        rdm_conf_merged = DEFAULT_RANDOM_DM_CONFIG.copy()
        if isinstance(rdm_conf_loaded, dict): rdm_conf_merged.update(rdm_conf_loaded)
        for key in ["last_interaction", "next_send_time"]:
             loaded_val = rdm_conf_merged.get(key)
             if loaded_val and isinstance(loaded_val, str):
                 try:
                     # fromisoformat は aware を返す
                     dt_obj = datetime.datetime.fromisoformat(loaded_val)
                     # ローカルタイムゾーンに変換
                     rdm_conf_merged[key] = dt_obj.astimezone()
                 except (ValueError, TypeError):
                     logger.warning(f"Could not parse datetime string for {key} in user {uid}: {loaded_val}")
                     rdm_conf_merged[key] = None
             elif key not in rdm_conf_merged or not isinstance(rdm_conf_merged[key], datetime.datetime):
                 rdm_conf_merged[key] = None
             elif isinstance(rdm_conf_merged[key], datetime.datetime): # 既にdatetimeの場合
                 dt_obj = rdm_conf_merged[key]
                 # aware でなければローカルTZを付与、aware ならローカルTZに変換
                 if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
                      rdm_conf_merged[key] = dt_obj.astimezone() # naive をローカルTZの aware に
                 else:
                      rdm_conf_merged[key] = dt_obj.astimezone() # aware をローカルTZの aware に変換

        new_u_data["random_dm"] = rdm_conf_merged
        temp_user_data[uid] = new_u_data
    user_data = temp_user_data

    channel_settings = _load_json(CHANNEL_SETTINGS_FILE)
    gemini_config = _load_json(GEMINI_CONFIG_FILE, DEFAULT_GEMINI_CONFIG)
    generation_config = _load_json(GENERATION_CONFIG_FILE, DEFAULT_GENERATION_CONFIG)

    # 履歴のロード (datetime aware ローカルTZに)
    loaded_history_data = _load_json(HISTORY_FILE)
    max_hist = bot_settings['max_history']
    global_hist_list = loaded_history_data.get(GLOBAL_HISTORY_KEY, [])

    dq = deque(maxlen=max_hist)
    if isinstance(global_hist_list, list):
        for entry in global_hist_list:
            if isinstance(entry, dict):
                try:
                    if "timestamp" in entry and isinstance(entry["timestamp"], str):
                         dt_obj = datetime.datetime.fromisoformat(entry["timestamp"])
                         entry["timestamp"] = dt_obj.astimezone() # ローカルTZに変換
                    elif "timestamp" not in entry or not isinstance(entry["timestamp"], datetime.datetime):
                         entry["timestamp"] = None
                    elif isinstance(entry["timestamp"], datetime.datetime):
                         dt_obj = entry["timestamp"]
                         if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
                              entry["timestamp"] = dt_obj.astimezone()
                         else:
                              entry["timestamp"] = dt_obj.astimezone()

                    entry.setdefault("role", None); entry.setdefault("parts", [])
                    entry.setdefault("channel_id", None); entry.setdefault("interlocutor_id", None)
                    entry.setdefault("current_interlocutor_id", None)
                    if entry["role"] and entry["interlocutor_id"] is not None: dq.append(entry)
                    else: logger.warning(f"Skipping history entry with missing info: {entry}")
                except (ValueError, TypeError, KeyError) as e: logger.warning(f"Skip invalid history entry: {entry} - Error: {e}")
            else: logger.warning(f"Skipping non-dict entry in global history list: {entry}")
    else: logger.warning(f"Invalid history format (expected dict with key '{GLOBAL_HISTORY_KEY}'): {loaded_history_data}")

    conversation_history = {GLOBAL_HISTORY_KEY: dq}

    persona_prompt = _load_text(PROMPTS_DIR / "persona_prompt.txt", DEFAULT_PERSONA_PROMPT)
    random_dm_prompt = _load_text(PROMPTS_DIR / "random_dm_prompt.txt", DEFAULT_RANDOM_DM_PROMPT)
    weather_config = _load_json(WEATHER_CONFIG_FILE, {"last_location": None})

    logger.info("All configurations and data loaded.")

# --- 保存関数 ---
def _save_json(filepath: Path, data: Any):
    """JSONファイルに安全に書き込む (datetime/deque対応)"""
    try:
        def dt_serializer(obj):
            if isinstance(obj, datetime.datetime):
                # aware datetimeをisoformatに変換（TZ情報が含まれる）
                return obj.isoformat()
            if isinstance(obj, deque): return list(obj)
            try: json.dumps(obj); return obj
            except TypeError: logger.warning(f"Object type {type(obj)} not JSON serializable, converting to str."); return str(obj)

        filepath.parent.mkdir(parents=True, exist_ok=True)
        temp_filepath = filepath.with_suffix(filepath.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            # default引数を使ってシリアライズ
            json.dump(data, f, indent=4, ensure_ascii=False, default=dt_serializer)
        os.replace(temp_filepath, filepath)
        logger.debug(f"Successfully saved JSON to: {filepath}")
    except Exception as e:
        logger.error(f"Error saving data to {filepath}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists():
            try: os.remove(temp_filepath)
            except OSError: pass


def _save_text(filepath: Path, text: str):
    """テキストファイルに安全に書き込む"""
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        temp_filepath = filepath.with_suffix(filepath.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f: f.write(text)
        os.replace(temp_filepath, filepath)
        logger.debug(f"Successfully saved text to: {filepath}")
    except Exception as e:
        logger.error(f"Error saving text to {filepath}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists():
            try: os.remove(temp_filepath)
            except OSError: pass

# --- 非同期保存関数 ---
async def save_user_data_nolock():
    """ユーザーデータを保存 (ロックなし, datetimeは_save_jsonで処理)"""
    try:
        logger.debug("Executing _save_json for user_data (no lock)...")
        _save_json(USER_DATA_FILE, user_data)
        logger.debug("Finished _save_json for user_data (no lock).")
    except Exception as e:
        logger.error("Failed to save user_data.json", exc_info=e)

async def save_conversation_history_nolock():
     """グローバル会話履歴を保存 (ロックなし, datetimeは_save_jsonで処理)"""
     try:
         logger.debug("Executing _save_json for global conversation_history (no lock)...")
         _save_json(HISTORY_FILE, conversation_history)
         logger.debug("Finished _save_json for global conversation_history (no lock).")
     except Exception as e:
         logger.error("Failed to save conversation_history.json", exc_info=e)

# --- 同期保存関数 ---
def save_bot_settings(): _save_json(BOT_CONFIG_FILE, bot_settings)
def save_channel_settings(): _save_json(CHANNEL_SETTINGS_FILE, channel_settings)
def save_gemini_config(): _save_json(GEMINI_CONFIG_FILE, gemini_config)
def save_generation_config():
    config_to_save = generation_config.copy(); config_to_save.pop("safety_settings", None); config_to_save.pop("tools", None); config_to_save.pop("system_instruction", None); _save_json(GENERATION_CONFIG_FILE, config_to_save)
def save_persona_prompt(): _save_text(PROMPTS_DIR / "persona_prompt.txt", persona_prompt)
def save_random_dm_prompt(): _save_text(PROMPTS_DIR / "random_dm_prompt.txt", random_dm_prompt)
def save_weather_config(): _save_json(WEATHER_CONFIG_FILE, weather_config)


# --- 設定値取得関数 ---
def get_max_history() -> int: return bot_settings.get('max_history', DEFAULT_MAX_HISTORY)
def get_max_response_length() -> int: return bot_settings.get('max_response_length', DEFAULT_MAX_RESPONSE_LENGTH)
def get_nickname(user_id: int) -> Optional[str]: return user_data.get(str(user_id), {}).get("nickname")
def get_all_user_data() -> Dict[str, Dict[str, Any]]: return user_data.copy()
def get_allowed_channels(server_id: int) -> List[int]: return channel_settings.get(str(server_id), [])
def get_all_channel_settings() -> Dict[str, List[int]]: return channel_settings.copy()
def get_model_name() -> str: return gemini_config.get('model_name', DEFAULT_GEMINI_CONFIG['model_name'])
def get_safety_settings_list() -> List[Dict[str, str]]: return gemini_config.get('safety_settings', DEFAULT_SAFETY_SETTINGS.copy())
def get_generation_config_dict() -> Dict[str, Any]: return generation_config.copy()
def get_persona_prompt() -> str: return persona_prompt
def get_random_dm_prompt() -> str: return random_dm_prompt
def get_default_random_dm_config() -> Dict[str, Any]: return DEFAULT_RANDOM_DM_CONFIG.copy()
def get_global_history() -> deque:
    max_hist = get_max_history()
    if GLOBAL_HISTORY_KEY not in conversation_history: conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=max_hist)
    elif conversation_history[GLOBAL_HISTORY_KEY].maxlen != max_hist: conversation_history[GLOBAL_HISTORY_KEY] = deque(conversation_history[GLOBAL_HISTORY_KEY], maxlen=max_hist)
    return conversation_history[GLOBAL_HISTORY_KEY].copy()
def get_all_history() -> Dict[str, deque]: return conversation_history.copy()
def get_last_weather_location() -> Optional[str]: return weather_config.get("last_location")

# --- 設定値更新関数 (ロック付き) ---
async def update_max_history_async(new_length: int):
    global bot_settings, conversation_history
    if new_length >= 0:
        async with data_lock:
            bot_settings['max_history'] = new_length
            logger.debug(f"Updating maxlen for global history deque...")
            if GLOBAL_HISTORY_KEY in conversation_history:
                 conversation_history[GLOBAL_HISTORY_KEY] = deque(conversation_history[GLOBAL_HISTORY_KEY], maxlen=new_length)
            else:
                 conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=new_length)
            save_bot_settings()
            await save_conversation_history_nolock()
        logger.info(f"Updated max_history to {new_length}")

async def update_nickname_async(user_id: int, nickname: str):
    global user_data
    user_id_str = str(user_id)
    async with data_lock:
        user_data.setdefault(user_id_str, {})["nickname"] = nickname
        await save_user_data_nolock()
    logger.info(f"Updated nickname for user {user_id}")

async def remove_nickname_async(user_id: int) -> bool:
    global user_data
    user_id_str = str(user_id)
    removed = False
    async with data_lock:
        if user_id_str in user_data and "nickname" in user_data[user_id_str]:
            del user_data[user_id_str]["nickname"]
            if not user_data[user_id_str]: del user_data[user_id_str]
            await save_user_data_nolock()
            removed = True
    if removed: logger.info(f"Removed nickname for user {user_id}")
    return removed

async def update_random_dm_config_async(user_id: int, updates: Dict[str, Any]): # ★ 引数名を config から updates に変更
    """random_dm設定を更新し保存 (datetimeはaware ローカルTZを期待)"""
    global user_data
    user_id_str = str(user_id)
    async with data_lock:
        # ★ get_or_create のような形で現在の設定を取得
        user_settings = user_data.setdefault(user_id_str, {})
        current_dm_config = user_settings.setdefault("random_dm", get_default_random_dm_config())

        # ★ 渡された updates の内容で current_dm_config を更新 ★
        for key, value in updates.items():
             if key in ["last_interaction", "next_send_time"] and isinstance(value, datetime.datetime):
                  dt_obj = value
                  if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
                       logger.warning(f"update_random_dm_config received naive datetime for {key}. Assuming local timezone.")
                       current_dm_config[key] = dt_obj.astimezone()
                  else:
                       current_dm_config[key] = dt_obj.astimezone()
             elif key in current_dm_config: # 他のキー（enabledなど）も更新対象にする場合
                  current_dm_config[key] = value
             else: # 知らないキーは無視するか、ログを出す
                  logger.warning(f"Ignoring unknown key '{key}' in update_random_dm_config_async")


        # user_data の更新は setdefault で行われているので、あとは保存のみ
        await save_user_data_nolock()
    logger.info(f"Updated random DM config for user {user_id} with updates: {updates}")


def update_safety_setting(category_value: str, threshold_value: str):
    global gemini_config
    updated = False
    current_settings = gemini_config.get('safety_settings', DEFAULT_SAFETY_SETTINGS.copy())
    new_settings = []
    for setting in current_settings:
        if setting.get("category") == category_value: new_settings.append({"category": category_value, "threshold": threshold_value}); updated = True
        else: new_settings.append(setting)
    if not updated: new_settings.append({"category": category_value, "threshold": threshold_value})
    gemini_config['safety_settings'] = new_settings
    save_gemini_config()


async def update_last_weather_location_async(location: Optional[str]):
    """最後に指定された天気取得場所を更新・保存する"""
    global weather_config
    async with data_lock: # weather_configもロック対象にする
        weather_config["last_location"] = location
        save_weather_config() # 同期保存
    if location:
        logger.info(f"Updated last weather location to: {location}")
    else:
        logger.info("Cleared last weather location.")


# --- 履歴操作 ---
async def add_history_entry_async(
    current_interlocutor_id: int,
    channel_id: Optional[int],
    role: str,
    parts_dict: List[Dict[str, Any]],
    entry_author_id: int
):
    """グローバル会話履歴にエントリを追加・保存する (非同期, aware ローカルTZ)"""
    global conversation_history
    if role not in ["user", "model"]: logger.error(f"Invalid role '{role}'"); return
    max_hist = get_max_history()
    logger.debug(f"add_history_entry_async (Global): Attempting lock...")
    async with data_lock:
        logger.debug(f"add_history_entry_async (Global): Acquired lock.")
        if GLOBAL_HISTORY_KEY not in conversation_history:
            conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=max_hist)
        elif conversation_history[GLOBAL_HISTORY_KEY].maxlen != max_hist:
            conversation_history[GLOBAL_HISTORY_KEY] = deque(conversation_history[GLOBAL_HISTORY_KEY], maxlen=max_hist)
        entry = {
            "role": role, "parts": parts_dict, "channel_id": channel_id,
            "interlocutor_id": entry_author_id,
            "current_interlocutor_id": current_interlocutor_id,
            "timestamp": datetime.datetime.now().astimezone() # ★ aware ローカルTZ
        }
        logger.debug(f"Appending entry to global history: {entry}")
        conversation_history[GLOBAL_HISTORY_KEY].append(entry)
        logger.debug(f"Calling save_conversation_history_nolock for global history")
        await save_conversation_history_nolock() # 保存関数内でISO文字列化
    logger.debug(f"add_history_entry_async (Global): Released lock.")


async def clear_all_history_async():
    """グローバル履歴を完全にクリアする"""
    global conversation_history
    async with data_lock:
        if GLOBAL_HISTORY_KEY in conversation_history:
            conversation_history[GLOBAL_HISTORY_KEY].clear()
        else:
            conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=get_max_history())
        await save_conversation_history_nolock()
    logger.warning("Cleared all global conversation history.")

async def clear_user_history_async(target_user_id: int) -> int:
    """グローバル履歴から指定ユーザーが関与したエントリを削除する"""
    global conversation_history; cleared_count = 0; target_user_id_str = str(target_user_id);
    async with data_lock:
        if GLOBAL_HISTORY_KEY not in conversation_history: return 0
        new_deque = deque(maxlen=conversation_history[GLOBAL_HISTORY_KEY].maxlen)
        original_len = len(conversation_history[GLOBAL_HISTORY_KEY])
        logger.info(f"Clearing global history entries involving user {target_user_id_str}")
        for entry in list(conversation_history[GLOBAL_HISTORY_KEY]):
            if entry.get("interlocutor_id") != target_user_id and entry.get("current_interlocutor_id") != target_user_id:
                new_deque.append(entry)
            else: logger.debug(f"Removing entry involving user {target_user_id_str}: {entry}")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0:
            conversation_history[GLOBAL_HISTORY_KEY] = new_deque
            await save_conversation_history_nolock()
            logger.info(f"Cleared global history for user {target_user_id}. {cleared_count} entries removed.")
        else: logger.debug(f"No entries involving user {target_user_id_str} found to clear.")
    return cleared_count

async def clear_channel_history_async(channel_id: int) -> int:
    """グローバル履歴から指定チャンネルのエントリを削除する"""
    global conversation_history; cleared_count = 0;
    async with data_lock:
        if GLOBAL_HISTORY_KEY not in conversation_history: return 0
        new_deque = deque(maxlen=conversation_history[GLOBAL_HISTORY_KEY].maxlen)
        original_len = len(conversation_history[GLOBAL_HISTORY_KEY])
        logger.info(f"Clearing global history entries for channel {channel_id}")
        for entry in list(conversation_history[GLOBAL_HISTORY_KEY]):
            if entry.get("channel_id") != channel_id:
                new_deque.append(entry)
            else: logger.debug(f"Removing entry for channel {channel_id}: {entry}")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0:
            conversation_history[GLOBAL_HISTORY_KEY] = new_deque
            await save_conversation_history_nolock()
            logger.info(f"Cleared global history for channel {channel_id}. {cleared_count} entries removed.")
        else: logger.debug(f"No entries for channel {channel_id} found to clear.")
    return cleared_count
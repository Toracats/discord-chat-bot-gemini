# utils/config_manager.py (JSONL追記修正、初回ファイル作成追加)

import json
import os
from pathlib import Path
import logging
from collections import deque
import datetime
# from datetime import timezone
from typing import Dict, List, Any, Optional, Deque
import asyncio
import uuid # 要約ID用

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
SUMMARY_CONFIG_FILE = CONFIG_DIR / "summary_config.json"
SUMMARIZED_HISTORY_FILE = CONFIG_DIR / "summarized_history.jsonl" # ★ .jsonl

# --- デフォルト設定 (変更なし) ---
DEFAULT_MAX_HISTORY = 10
DEFAULT_MAX_RESPONSE_LENGTH = 1800
DEFAULT_RANDOM_DM_PROMPT = "最近どうですか？何か面白いことありましたか？"
DEFAULT_PERSONA_PROMPT = "あなたは親切なAIアシスタントです。"
DEFAULT_GENERATION_CONFIG = {
    "temperature": 0.9, "top_p": 1.0, "top_k": 1,
    "candidate_count": 1, "max_output_tokens": 1024,
}
DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]
DEFAULT_GEMINI_CONFIG = {
    "model_name": "gemini-1.5-flash",
    "safety_settings": DEFAULT_SAFETY_SETTINGS
}
DEFAULT_RANDOM_DM_CONFIG = {
    "enabled": False, "min_interval": 3600 * 6, "max_interval": 86400 * 2,
    "stop_start_hour": 23, "stop_end_hour": 7,
    "last_interaction": None, "next_send_time": None,
}
DEFAULT_SUMMARY_MODEL = "gemini-1.5-flash"
DEFAULT_SUMMARY_MAX_TOKENS = 4000
DEFAULT_SUMMARY_GENERATION_CONFIG = {
    "temperature": 0.5, "top_p": 1.0, "top_k": 1,
    "candidate_count": 1, "max_output_tokens": 512,
}
DEFAULT_SUMMARY_CONFIG = {
    "summary_model_name": DEFAULT_SUMMARY_MODEL,
    "summary_max_prompt_tokens": DEFAULT_SUMMARY_MAX_TOKENS,
    "summary_generation_config": DEFAULT_SUMMARY_GENERATION_CONFIG,
}
GLOBAL_HISTORY_KEY = "global_history"

# --- データ保持用変数 (変更なし) ---
bot_settings: Dict[str, Any] = {}
user_data: Dict[str, Dict[str, Any]] = {}
channel_settings: Dict[str, List[int]] = {}
gemini_config: Dict[str, Any] = {}
generation_config: Dict[str, Any] = {}
summary_config: Dict[str, Any] = {}
conversation_history: Dict[str, Deque[Dict[str, Any]]] = {GLOBAL_HISTORY_KEY: deque(maxlen=DEFAULT_MAX_HISTORY)}
persona_prompt: str = ""
random_dm_prompt: str = ""
weather_config: Dict[str, Any] = {}
data_lock = asyncio.Lock()

# --- ロード関数 (基本的なJSON/Textロードは変更なし) ---
def _load_json(filepath: Path, default: Any = {}) -> Any:
    try:
        if filepath.exists() and filepath.is_file():
            with open(filepath, 'r', encoding='utf-8') as f:
                try: data = json.load(f); logger.info(f"Loaded JSON: {filepath}"); return data
                except json.JSONDecodeError: logger.error(f"Decode error: {filepath}. Using default."); _save_json(filepath, default); return default.copy()
        else: logger.warning(f"Not found: {filepath}. Creating default."); _save_json(filepath, default); return default.copy()
    except Exception as e: logger.error(f"Load error: {filepath}", exc_info=e); return default.copy()

def _load_text(filepath: Path, default: str = "") -> str:
    try:
        if filepath.exists() and filepath.is_file():
            with open(filepath, 'r', encoding='utf-8') as f: content = f.read(); logger.info(f"Loaded text: {filepath}"); return content
        else: logger.warning(f"Not found: {filepath}. Creating default."); _save_text(filepath, default); return default
    except Exception as e: logger.error(f"Load error: {filepath}", exc_info=e); return default

def load_all_configs():
    """すべての設定とデータをロードする"""
    global bot_settings, user_data, channel_settings, gemini_config, generation_config
    global conversation_history, persona_prompt, random_dm_prompt, weather_config
    global summary_config

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- 各設定ファイルのロード (user_data, 履歴のロード処理は変更なし) ---
    bot_settings = _load_json(BOT_CONFIG_FILE, {"max_history": DEFAULT_MAX_HISTORY, "max_response_length": DEFAULT_MAX_RESPONSE_LENGTH})
    # user_data ロード (変更なし)
    loaded_user_data = _load_json(USER_DATA_FILE); temp_user_data = {}
    for uid, u_data in loaded_user_data.items():
        new_u_data = {"nickname": u_data.get("nickname")}; rdm_conf_loaded = u_data.get("random_dm", {}); rdm_conf_merged = DEFAULT_RANDOM_DM_CONFIG.copy()
        if isinstance(rdm_conf_loaded, dict): rdm_conf_merged.update(rdm_conf_loaded)
        for key in ["last_interaction", "next_send_time"]:
             loaded_val = rdm_conf_merged.get(key)
             if loaded_val and isinstance(loaded_val, str):
                 try: dt_obj = datetime.datetime.fromisoformat(loaded_val); rdm_conf_merged[key] = dt_obj.astimezone()
                 except (ValueError, TypeError): logger.warning(f"Parse error dt str {key} user {uid}: {loaded_val}"); rdm_conf_merged[key] = None
             elif isinstance(rdm_conf_merged[key], datetime.datetime):
                 dt_obj = rdm_conf_merged[key]; rdm_conf_merged[key] = dt_obj.astimezone() # TZ変換/付与
             else: rdm_conf_merged[key] = None
        new_u_data["random_dm"] = rdm_conf_merged; temp_user_data[uid] = new_u_data
    user_data = temp_user_data
    channel_settings = _load_json(CHANNEL_SETTINGS_FILE)
    gemini_config = _load_json(GEMINI_CONFIG_FILE, DEFAULT_GEMINI_CONFIG.copy())
    generation_config = _load_json(GENERATION_CONFIG_FILE, DEFAULT_GENERATION_CONFIG.copy())
    summary_config = _load_json(SUMMARY_CONFIG_FILE, DEFAULT_SUMMARY_CONFIG.copy())
    # 履歴ロード (変更なし)
    loaded_history_data = _load_json(HISTORY_FILE); max_hist = bot_settings.get('max_history', DEFAULT_MAX_HISTORY); global_hist_list = loaded_history_data.get(GLOBAL_HISTORY_KEY, [])
    dq: Deque[Dict[str, Any]] = deque(maxlen=max_hist)
    if isinstance(global_hist_list, list):
        for entry in global_hist_list:
            if isinstance(entry, dict):
                try:
                    if "timestamp" in entry and isinstance(entry["timestamp"], str): entry["timestamp"] = datetime.datetime.fromisoformat(entry["timestamp"]).astimezone()
                    elif isinstance(entry.get("timestamp"), datetime.datetime): entry["timestamp"] = entry["timestamp"].astimezone()
                    else: entry["timestamp"] = None
                    entry.setdefault("role", None); entry.setdefault("parts", []); entry.setdefault("channel_id", None); entry.setdefault("interlocutor_id", None); entry.setdefault("current_interlocutor_id", None)
                    if entry["role"] and entry["interlocutor_id"] is not None: dq.append(entry)
                    else: logger.warning(f"Skip history entry missing info: {entry.get('entry_id')}")
                except (ValueError, TypeError, KeyError) as e: logger.warning(f"Skip invalid history entry: {entry.get('entry_id')} - Error: {e}")
            else: logger.warning(f"Skip non-dict entry in global history: {entry}")
    else: logger.warning(f"Invalid history format: {loaded_history_data}")
    conversation_history = {GLOBAL_HISTORY_KEY: dq}
    persona_prompt = _load_text(PROMPTS_DIR / "persona_prompt.txt", DEFAULT_PERSONA_PROMPT)
    random_dm_prompt = _load_text(PROMPTS_DIR / "random_dm_prompt.txt", DEFAULT_RANDOM_DM_PROMPT)
    weather_config = _load_json(WEATHER_CONFIG_FILE, {"last_location": None})

    # ★ 要約DBファイルが存在しない場合に空ファイルを作成 ★
    if not SUMMARIZED_HISTORY_FILE.exists():
        try:
            SUMMARIZED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            SUMMARIZED_HISTORY_FILE.touch()
            logger.info(f"Created empty summary history file: {SUMMARIZED_HISTORY_FILE}")
        except Exception as e:
            logger.error(f"Failed to create summary history file: {SUMMARIZED_HISTORY_FILE}", exc_info=e)

    logger.info("All configurations and data loaded.")

# --- 保存関数 (基本的なJSON/Textセーブは変更なし) ---
def _save_json(filepath: Path, data: Any):
    try:
        def complex_serializer(obj):
            if isinstance(obj, datetime.datetime): return obj.isoformat()
            if isinstance(obj, deque): return list(obj)
            if isinstance(obj, uuid.UUID): return str(obj)
            try: json.dumps(obj); return obj
            except TypeError: return str(obj)
        filepath.parent.mkdir(parents=True, exist_ok=True); temp_filepath = filepath.with_suffix(filepath.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False, default=complex_serializer)
        os.replace(temp_filepath, filepath); logger.debug(f"Saved JSON: {filepath}")
    except Exception as e:
        logger.error(f"Save error: {filepath}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists(): 
            try: 
                os.remove(temp_filepath) 
            except OSError: 
                pass

def _save_text(filepath: Path, text: str):
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True); temp_filepath = filepath.with_suffix(filepath.suffix + '.tmp')
        with open(temp_filepath, 'w', encoding='utf-8') as f: f.write(text)
        os.replace(temp_filepath, filepath); logger.debug(f"Saved text: {filepath}")
    except Exception as e:
        logger.error(f"Save error: {filepath}", exc_info=e)
        if 'temp_filepath' in locals() and temp_filepath.exists():
            try:
                os.remove(temp_filepath)
            except OSError:
                pass

# --- 非同期保存関数 (変更なし) ---
async def save_user_data_nolock():
    try: _save_json(USER_DATA_FILE, user_data)
    except Exception as e: logger.error("Failed to save user_data.json", exc_info=e)

async def save_conversation_history_nolock():
     try: _save_json(HISTORY_FILE, conversation_history)
     except Exception as e: logger.error("Failed to save conversation_history.json", exc_info=e)

# --- 同期保存関数 (変更なし) ---
def save_bot_settings(): _save_json(BOT_CONFIG_FILE, bot_settings)
def save_channel_settings(): _save_json(CHANNEL_SETTINGS_FILE, channel_settings)
def save_gemini_config(): _save_json(GEMINI_CONFIG_FILE, gemini_config)
def save_generation_config(): config_to_save = generation_config.copy(); _save_json(GENERATION_CONFIG_FILE, config_to_save)
def save_persona_prompt(): _save_text(PROMPTS_DIR / "persona_prompt.txt", persona_prompt)
def save_random_dm_prompt(): _save_text(PROMPTS_DIR / "random_dm_prompt.txt", random_dm_prompt)
def save_weather_config(): _save_json(WEATHER_CONFIG_FILE, weather_config)
def save_summary_config(): _save_json(SUMMARY_CONFIG_FILE, summary_config)

# --- 設定値取得関数 (変更なし) ---
def get_max_history() -> int: return bot_settings.get('max_history', DEFAULT_MAX_HISTORY)
def get_max_response_length() -> int: return bot_settings.get('max_response_length', DEFAULT_MAX_RESPONSE_LENGTH)
def get_nickname(user_id: int) -> Optional[str]: return user_data.get(str(user_id), {}).get("nickname")
def get_all_user_data() -> Dict[str, Dict[str, Any]]: return user_data.copy()
def get_allowed_channels(server_id: str) -> List[int]: return channel_settings.get(server_id, [])
def get_all_channel_settings() -> Dict[str, List[int]]: return channel_settings.copy()
def get_model_name() -> str: return gemini_config.get('model_name', DEFAULT_GEMINI_CONFIG['model_name'])
def get_safety_settings_list() -> List[Dict[str, str]]: return gemini_config.get('safety_settings', DEFAULT_SAFETY_SETTINGS.copy())
def get_generation_config_dict() -> Dict[str, Any]: return generation_config.copy()
def get_persona_prompt() -> str: return persona_prompt
def get_random_dm_prompt() -> str: return random_dm_prompt
def get_default_random_dm_config() -> Dict[str, Any]: return DEFAULT_RANDOM_DM_CONFIG.copy()
def get_global_history() -> Deque[Dict[str, Any]]:
    max_hist = get_max_history()
    if GLOBAL_HISTORY_KEY not in conversation_history: conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=max_hist)
    elif not isinstance(conversation_history.get(GLOBAL_HISTORY_KEY), deque) or conversation_history[GLOBAL_HISTORY_KEY].maxlen != max_hist:
        current_items = list(conversation_history.get(GLOBAL_HISTORY_KEY, [])); conversation_history[GLOBAL_HISTORY_KEY] = deque(current_items, maxlen=max_hist)
    return conversation_history[GLOBAL_HISTORY_KEY]
def get_all_history() -> Dict[str, Deque[Dict[str, Any]]]: return conversation_history
def get_last_weather_location() -> Optional[str]: return weather_config.get("last_location")
def get_summary_model_name() -> str: return summary_config.get('summary_model_name', DEFAULT_SUMMARY_MODEL)
def get_summary_max_prompt_tokens() -> int: return summary_config.get('summary_max_prompt_tokens', DEFAULT_SUMMARY_MAX_TOKENS)
def get_summary_generation_config_dict() -> Dict[str, Any]: return summary_config.get('summary_generation_config', DEFAULT_SUMMARY_GENERATION_CONFIG.copy())

# --- 設定値更新関数 (変更なし) ---
async def update_max_history_async(new_length: int):
    global bot_settings, conversation_history
    if new_length >= 0:
        async with data_lock:
            bot_settings['max_history'] = new_length; logger.debug(f"Updating maxlen global history deque to {new_length}...")
            if GLOBAL_HISTORY_KEY in conversation_history and isinstance(conversation_history[GLOBAL_HISTORY_KEY], deque): conversation_history[GLOBAL_HISTORY_KEY] = deque(conversation_history[GLOBAL_HISTORY_KEY], maxlen=new_length)
            else: conversation_history[GLOBAL_HISTORY_KEY] = deque(maxlen=new_length)
            save_bot_settings()
        logger.info(f"Updated max_history to {new_length}")
async def update_nickname_async(user_id: int, nickname: str):
    global user_data; user_id_str = str(user_id)
    async with data_lock: user_data.setdefault(user_id_str, {})["nickname"] = nickname; await save_user_data_nolock()
    logger.info(f"Updated nickname for user {user_id}")
async def remove_nickname_async(user_id: int) -> bool:
    global user_data; user_id_str = str(user_id); removed = False
    async with data_lock:
        if user_id_str in user_data and "nickname" in user_data[user_id_str]:
            del user_data[user_id_str]["nickname"];
            if not user_data[user_id_str]: del user_data[user_id_str]
            await save_user_data_nolock(); removed = True
    if removed: logger.info(f"Removed nickname for user {user_id}"); return removed
async def update_random_dm_config_async(user_id: int, updates: Dict[str, Any]):
    global user_data; user_id_str = str(user_id); updated_keys = []
    async with data_lock:
        user_settings = user_data.setdefault(user_id_str, {}); current_dm_config = user_settings.setdefault("random_dm", get_default_random_dm_config())
        for key, value in updates.items():
             if key in current_dm_config:
                 if key in ["last_interaction", "next_send_time"] and isinstance(value, datetime.datetime):
                      dt_obj = value; current_dm_config[key] = dt_obj.astimezone(); updated_keys.append(key)
                 elif key in ["min_interval", "max_interval", "stop_start_hour", "stop_end_hour"] and (value is None or isinstance(value, int)): current_dm_config[key] = value; updated_keys.append(key)
                 elif key == "enabled" and isinstance(value, bool): current_dm_config[key] = value; updated_keys.append(key)
                 else: logger.warning(f"Ignore update key '{key}' invalid type/value: {value}")
             else: logger.warning(f"Ignore unknown key '{key}' in update_random_dm_config_async")
        if updated_keys: await save_user_data_nolock(); logger.info(f"Updated random DM config user {user_id}. Keys: {updated_keys}")
        else: logger.debug(f"No valid updates random DM config user {user_id}.")
def update_safety_setting(category_value: str, threshold_value: str):
    global gemini_config; updated = False; current_settings = gemini_config.get('safety_settings', DEFAULT_SAFETY_SETTINGS.copy()); new_settings = []
    for setting in current_settings:
        if setting.get("category") == category_value: new_settings.append({"category": category_value, "threshold": threshold_value}); updated = True
        else: new_settings.append(setting)
    if not updated: new_settings.append({"category": category_value, "threshold": threshold_value})
    gemini_config['safety_settings'] = new_settings; save_gemini_config()
async def update_last_weather_location_async(location: Optional[str]):
    global weather_config
    async with data_lock: weather_config["last_location"] = location; save_weather_config()
    if location: logger.info(f"Updated last weather location to: {location}")
    else: logger.info("Cleared last weather location.")
def update_summary_model_name(model_name: str):
     global summary_config; summary_config["summary_model_name"] = model_name; save_summary_config()
     logger.info(f"Updated summary model name to: {model_name}")
def update_summary_max_prompt_tokens(max_tokens: int):
    global summary_config
    if max_tokens >= 0: summary_config["summary_max_prompt_tokens"] = max_tokens; save_summary_config(); logger.info(f"Updated summary max prompt tokens to: {max_tokens}")
    else: logger.warning(f"Invalid summary max prompt tokens value: {max_tokens}.")
def update_summary_generation_config(key: str, value: Any):
    global summary_config; valid_keys = DEFAULT_SUMMARY_GENERATION_CONFIG.keys()
    if key in valid_keys:
        if key in ["temperature", "top_p"] and not isinstance(value, (int, float)): logger.warning(f"Invalid type for summary {key}"); return
        if key in ["top_k", "candidate_count", "max_output_tokens"] and not isinstance(value, int): logger.warning(f"Invalid type for summary {key}"); return
        current_gen_config = summary_config.setdefault("summary_generation_config", DEFAULT_SUMMARY_GENERATION_CONFIG.copy())
        current_gen_config[key] = value; save_summary_config(); logger.info(f"Updated summary generation config: {key} = {value}")
    else: logger.warning(f"Invalid key for summary generation config: {key}")

# --- 履歴操作 (変更なし) ---
async def add_history_entry_async( current_interlocutor_id: int, channel_id: Optional[int], role: str, parts_dict: List[Dict[str, Any]], entry_author_id: int ) -> Optional[Dict[str, Any]]:
    global conversation_history
    if role not in ["user", "model"]:
        logger.error(f"Invalid role '{role}'")
        return None
    max_hist = get_max_history()
    pushed_out_entry = None
    async with data_lock:
        if GLOBAL_HISTORY_KEY not in conversation_history or not isinstance(conversation_history.get(GLOBAL_HISTORY_KEY), deque) or conversation_history[GLOBAL_HISTORY_KEY].maxlen != max_hist:
            current_items = list(conversation_history.get(GLOBAL_HISTORY_KEY, [])); conversation_history[GLOBAL_HISTORY_KEY] = deque(current_items, maxlen=max_hist)
        current_deque = conversation_history[GLOBAL_HISTORY_KEY]
        if len(current_deque) == max_hist and max_hist > 0: pushed_out_entry = current_deque[0].copy(); logger.debug(f"History full. Entry to push out: {pushed_out_entry.get('entry_id')}")
        entry = { "entry_id": str(uuid.uuid4()), "role": role, "parts": parts_dict, "channel_id": channel_id, "interlocutor_id": entry_author_id, "current_interlocutor_id": current_interlocutor_id, "timestamp": datetime.datetime.now().astimezone() }
        current_deque.append(entry); logger.debug(f"Appended entry {entry['entry_id']}. History len: {len(current_deque)}")
        await save_conversation_history_nolock()
    return pushed_out_entry
async def clear_all_history_async():
    global conversation_history
    async with data_lock: conversation_history.setdefault(GLOBAL_HISTORY_KEY, deque(maxlen=get_max_history())).clear(); await save_conversation_history_nolock(); logger.warning("Cleared all global conversation history.")
async def clear_user_history_async(target_user_id: int) -> int:
    global conversation_history
    cleared_count = 0
    async with data_lock:
        if GLOBAL_HISTORY_KEY not in conversation_history:
            return 0
        current_deque = conversation_history[GLOBAL_HISTORY_KEY]
        max_len = current_deque.maxlen
        new_deque = deque(maxlen=max_len)
        original_len = len(current_deque)
        for entry in list(current_deque):
            if entry.get("interlocutor_id") != target_user_id and entry.get("current_interlocutor_id") != target_user_id:
                new_deque.append(entry)
            else:
                logger.debug(f"Removing entry involving user {target_user_id}: {entry.get('entry_id')}")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0:
            conversation_history[GLOBAL_HISTORY_KEY] = new_deque
            await save_conversation_history_nolock()
            logger.info(f"Cleared {cleared_count} entries for user {target_user_id}.")
    return cleared_count

async def clear_channel_history_async(channel_id: int) -> int:
    global conversation_history
    cleared_count = 0
    async with data_lock:
        if GLOBAL_HISTORY_KEY not in conversation_history:
            return 0
        current_deque = conversation_history[GLOBAL_HISTORY_KEY]
        max_len = current_deque.maxlen
        new_deque = deque(maxlen=max_len)
        original_len = len(current_deque)
        for entry in list(current_deque):
            if entry.get("channel_id") != channel_id: new_deque.append(entry)
            else: logger.debug(f"Removing entry for channel {channel_id}: {entry.get('entry_id')}")
        cleared_count = original_len - len(new_deque)
        if cleared_count > 0: conversation_history[GLOBAL_HISTORY_KEY] = new_deque; await save_conversation_history_nolock(); logger.info(f"Cleared {cleared_count} entries for channel {channel_id}.")
    return cleared_count

# --- 要約DB操作関数 ★★★ ---
async def load_summaries() -> List[Dict[str, Any]]:
    """要約DB (JSONL) から全件読み込む"""
    summaries = []
    if not SUMMARIZED_HISTORY_FILE.exists(): return summaries
    try:
        # ファイルI/Oは非同期ではないため、ロック内で同期的に実行
        async with data_lock: # ロックは念のため取得
            logger.debug(f"Loading summaries from {SUMMARIZED_HISTORY_FILE}...")
            with open(SUMMARIZED_HISTORY_FILE, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    try:
                        summary_entry = json.loads(line)
                        # タイムスタンプ文字列をdatetimeオブジェクトに変換
                        for ts_key in ["added_timestamp", "original_timestamp"]:
                            if ts_key in summary_entry and isinstance(summary_entry[ts_key], str):
                                try: summary_entry[ts_key] = datetime.datetime.fromisoformat(summary_entry[ts_key]).astimezone()
                                except ValueError: logger.warning(f"L{i+1}: Parse error ts '{summary_entry[ts_key]}' key '{ts_key}'."); summary_entry[ts_key] = None
                        summaries.append(summary_entry)
                    except json.JSONDecodeError: logger.warning(f"L{i+1}: Skip invalid JSON line: {line.strip()}")
        logger.info(f"Loaded {len(summaries)} summaries from {SUMMARIZED_HISTORY_FILE}.")
        return summaries
    except Exception as e: logger.error(f"Error loading summaries: {SUMMARIZED_HISTORY_FILE}", exc_info=e); return []

async def append_summary(summary_entry: Dict[str, Any]):
    """要約エントリをJSONLファイルに追記する"""
    try:
        # ファイルI/Oは非同期ではないため、ロック内で同期的に実行
        async with data_lock:
            summary_id = summary_entry.get('summary_id', 'N/A')
            logger.debug(f"Appending summary {summary_id} to {SUMMARIZED_HISTORY_FILE}...")
            def _serializer(obj): # シリアライザ
                 if isinstance(obj, datetime.datetime): return obj.isoformat()
                 if isinstance(obj, uuid.UUID): return str(obj)
                 try: json.dumps(obj); return obj
                 except TypeError: return str(obj)
            SUMMARIZED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True) # ディレクトリ作成
            # ★ 'a'モード (追記) で開く ★
            with open(SUMMARIZED_HISTORY_FILE, 'a', encoding='utf-8') as f:
                json.dump(summary_entry, f, ensure_ascii=False, default=_serializer)
                f.write('\n') # ★ 改行を追加 ★
        logger.info(f"Appended summary {summary_id} to {SUMMARIZED_HISTORY_FILE}.")
    except Exception as e: logger.error(f"Error appending summary to {SUMMARIZED_HISTORY_FILE}", exc_info=e)

async def clear_summaries() -> bool:
    """要約DBファイルを削除（クリア）する"""
    logger.warning(f"Attempting to clear summary database file: {SUMMARIZED_HISTORY_FILE}") # ★ ログ追加
    async with data_lock:
        try:
            if SUMMARIZED_HISTORY_FILE.exists():
                os.remove(SUMMARIZED_HISTORY_FILE)
                # ★ 空ファイルを作成し直す ★
                SUMMARIZED_HISTORY_FILE.touch()
                logger.warning(f"Cleared and recreated summary database file: {SUMMARIZED_HISTORY_FILE}")
                return True
            else:
                logger.info("Summary database file does not exist, nothing to clear.")
                return True
        except Exception as e:
            logger.error(f"Error clearing summary database file: {SUMMARIZED_HISTORY_FILE}", exc_info=e)
            return False
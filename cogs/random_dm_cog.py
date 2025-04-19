# cogs/random_dm_cog_GUI.py (generate_content 呼び出し修正版)
from __future__ import annotations # postponed evaluation of annotations
import discord
from discord.ext import commands, tasks
import logging
import datetime
import asyncio
import random
from typing import Optional, List, Dict, Any, TYPE_CHECKING
import os
import re
# import threading # GUI版では config_manager のロックを使用

from utils import config_manager
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
from utils import helpers

# HistoryCog を循環参照回避のため TYPE_CHECKING でインポート
if TYPE_CHECKING:
    from cogs.history_cog import HistoryCog
    from cogs.chat_cog import ChatCog # initialize_genai_client_if_needed のため
else:
    HistoryCog = None
    ChatCog = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # デバッグ用

class RandomDMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client: Optional[genai.Client] = None
        # ★ GUI版では config_manager のロックを使う
        self.user_data_lock = config_manager.user_data_lock
        self.dm_sender_loop.start()
        logger.info("RandomDMCog loaded and task started.")

    def cog_unload(self):
        self.dm_sender_loop.cancel()
        logger.info("RandomDMCog unloaded and task stopped.")

    def get_call_name(self, target_user_id: Optional[int]) -> str:
        """ユーザーIDに対応する呼び名を取得する (GUI版のロック方式を使用)"""
        if target_user_id is None: return "(不明な相手)"
        if target_user_id == self.bot.user.id: return self.bot.user.display_name
        with self.user_data_lock:
            nickname = config_manager.app_config.get("user_data", {}).get(str(target_user_id), {}).get("nickname")
        if nickname: return nickname
        user = self.bot.get_user(target_user_id)
        if user: return user.display_name
        return f"User {target_user_id}"

    async def initialize_genai_client_if_needed(self) -> bool:
        """GenAIクライアントを取得/初期化する (変更なし)"""
        if self.genai_client: return True
        logger.debug("Attempting to get GenAI client for RandomDMCog...")
        # ChatCogから取得試行
        chat_cog: Optional[ChatCog] = self.bot.get_cog("ChatCog")
        if chat_cog and hasattr(chat_cog, 'genai_client') and chat_cog.genai_client:
            self.genai_client = chat_cog.genai_client
            logger.info("Using GenAI client from ChatCog for Random DM.")
            return True
        # 独立して初期化試行
        logger.warning("Could not get GenAI client from ChatCog. Attempting independent initialization for RandomDMCog...")
        try:
            api_key = config_manager.get_gemini_api_key()
            if not api_key:
                logger.error("Gemini API Key not found. Random DM cannot use AI.")
                return False
            self.genai_client = genai.Client(api_key=api_key)
            logger.info("Gemini client initialized independently for RandomDMCog.")
            return True
        except Exception as e:
            logger.error("Failed to initialize Gemini client independently for RandomDMCog", exc_info=e)
            self.genai_client = None
            return False

    async def reset_user_timer(self, user_id: int):
        """ユーザーのランダムDMタイマーをリセットする (GUI版のロック方式)"""
        user_id_str = str(user_id)
        logger.debug(f"Resetting Random DM timer in memory for user {user_id_str} due to interaction.")
        # GUI版のロックを使用
        with self.user_data_lock:
            # app_config を直接参照・更新
            user_data_dict = config_manager.app_config.setdefault("user_data", {})
            if user_id_str in user_data_dict:
                # setdefault で random_dm キーがなければデフォルト値で作成
                user_settings = user_data_dict[user_id_str].setdefault("random_dm", config_manager.get_default_random_dm_config())
                if user_settings.get("enabled"):
                    now_aware = datetime.datetime.now().astimezone()
                    user_settings["last_interaction"] = now_aware
                    user_settings["next_send_time"] = None # 次回送信時刻をリセット
                    logger.info(f"Random DM timer reset in memory for user {user_id}. Config will be saved later.")
                    # 保存は trigger_config_save に任せる
            else:
                logger.debug(f"User {user_id_str} not found in user_data for timer reset.")

    @tasks.loop(seconds=10.0) # チェック間隔
    async def dm_sender_loop(self):
        """ランダムDMをチェック・送信するループ (GUI版のロック/保存方式)"""
        if not self.bot.is_ready():
            logger.debug("Bot not ready, skipping DM sender loop iteration.")
            return
        if not await self.initialize_genai_client_if_needed():
            logger.warning("GenAI client not available in RandomDMCog, skipping loop iteration.")
            await asyncio.sleep(60) # クライアントがない場合は待機
            return

        now = datetime.datetime.now().astimezone()
        logger.debug(f"Running dm_sender_loop check at {now.isoformat()}")
        users_to_dm: List[int] = []
        memory_updated = False # メモリ上の設定が更新されたか

        # GUI版のロックを使用
        with self.user_data_lock:
            # app_config を直接参照
            user_data_dict = config_manager.app_config.setdefault("user_data", {})
            # イテレーション中の変更を避けるためキーリストでループ
            for user_id_str in list(user_data_dict.keys()):
                u_data = user_data_dict.get(user_id_str, {})
                try:
                    user_id = int(user_id_str)
                    # setdefault で random_dm キーがなければデフォルト値で作成・取得
                    dm_config_current = u_data.setdefault("random_dm", config_manager.get_default_random_dm_config())

                    if not dm_config_current.get("enabled"):
                        continue # 無効ならスキップ

                    # 停止時間チェック
                    stop_start = dm_config_current.get("stop_start_hour")
                    stop_end = dm_config_current.get("stop_end_hour")
                    is_stopping_time = False
                    if stop_start is not None and stop_end is not None:
                        current_hour_local = now.hour
                        if stop_start > stop_end: # 日跨ぎ (例: 23-7時)
                            is_stopping_time = (current_hour_local >= stop_start or current_hour_local < stop_end)
                        else: # 日跨ぎなし (例: 9-17時)
                            is_stopping_time = (stop_start <= current_hour_local < stop_end)
                    if is_stopping_time:
                        logger.debug(f"Skipping DM check for user {user_id} (stopping time active)")
                        continue

                    # 次回送信時刻チェック
                    last_interact_dt_raw = dm_config_current.get("last_interaction")
                    next_send_time_dt_raw = dm_config_current.get("next_send_time")
                    default_tz = now.tzinfo

                    # last_interaction を datetime オブジェクトに変換
                    last_interact_dt = None
                    if isinstance(last_interact_dt_raw, datetime.datetime):
                        last_interact_dt = last_interact_dt_raw.astimezone(default_tz)
                    elif isinstance(last_interact_dt_raw, str): # ISO形式からの復元
                        try: last_interact_dt = datetime.datetime.fromisoformat(last_interact_dt_raw).astimezone(default_tz)
                        except ValueError: pass
                    if last_interact_dt is None: # なければ過去とみなす
                         last_interact_dt = datetime.datetime.min.replace(tzinfo=default_tz)

                    # next_send_time を datetime オブジェクトに変換
                    next_send_time_dt = None
                    if isinstance(next_send_time_dt_raw, datetime.datetime):
                         next_send_time_dt = next_send_time_dt_raw.astimezone(default_tz)
                    elif isinstance(next_send_time_dt_raw, str): # ISO形式からの復元
                         try: next_send_time_dt = datetime.datetime.fromisoformat(next_send_time_dt_raw).astimezone(default_tz)
                         except ValueError: pass

                    # 次回送信時刻が未設定なら計算して設定
                    if next_send_time_dt is None:
                        min_interval = dm_config_current.get("min_interval", 21600) # デフォルト6時間(秒)
                        max_interval = dm_config_current.get("max_interval", 172800) # デフォルト2日(秒)
                        if min_interval > max_interval: max_interval = min_interval
                        interval_sec = random.uniform(min_interval, max_interval)
                        calculated_next_send = last_interact_dt + datetime.timedelta(seconds=interval_sec)
                        dm_config_current["next_send_time"] = calculated_next_send # メモリ上のデータを更新
                        memory_updated = True # 更新フラグを立てる
                        logger.info(f"Calculated next DM time for user {user_id}: {calculated_next_send.isoformat()}")
                        continue # 今回は送信しない

                    # 送信時刻を過ぎていたらリストに追加
                    if now >= next_send_time_dt:
                        logger.info(f"Time condition met for user {user_id}: now={now.isoformat()}, next_send_time={next_send_time_dt.isoformat()}")
                        users_to_dm.append(user_id)
                        # 送信時刻と最終インタラクションを更新
                        dm_config_current["next_send_time"] = None
                        dm_config_current["last_interaction"] = now
                        memory_updated = True # 更新フラグを立てる
                    # else: logger.debug(f"Time condition NOT met user {user_id}.") # ログ抑制

                except ValueError:
                    logger.warning(f"Invalid user ID format encountered: {user_id_str}")
                except Exception as e:
                    logger.error(f"Error processing user {user_id_str} in dm_sender_loop check", exc_info=e)

        # --- ロック外 ---
        # メモリが更新されていたら設定保存をスケジュール
        if memory_updated:
            logger.info("Random DM config updated in memory. Scheduling configuration save...")
            try:
                # trigger_config_save は非同期関数なので create_task
                asyncio.create_task(self.trigger_config_save(), name=f"schedule_save_dm_config")
            except Exception as e:
                logger.error("Error scheduling config save task after DM loop check", exc_info=e)

        # DM送信対象がいれば実行
        if users_to_dm:
            logger.info(f"Preparing to send random DMs to {len(users_to_dm)} users: {users_to_dm}")
            history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
            if not history_cog:
                logger.error("HistoryCog not found! Cannot send random DMs with history context.")
                # HistoryCogがなくてもDMを送る場合は、send_random_dm の呼び出しを変更する
                # return

            # 各ユーザーへのDM送信を並行実行
            send_tasks = [self.send_random_dm(user_id, history_cog) for user_id in users_to_dm]
            if send_tasks:
                results = await asyncio.gather(*send_tasks, return_exceptions=True)
                # エラー結果のログ処理
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Error occurred while sending random DM to user {users_to_dm[i]}", exc_info=result)

            logger.info(f"Finished dm_sender_loop iteration sending DMs to {len(users_to_dm)} users.")

    async def trigger_config_save(self):
        """設定保存を少し遅延させて実行する"""
        await asyncio.sleep(1) # 他の操作と競合しないように少し待つ
        try:
            # ★ GUI版の保存関数を呼び出す
            config_manager.save_app_config()
            logger.info("Application configuration saved successfully after DM loop check.")
        except Exception as e:
            logger.error("Failed to save application config after DM loop check", exc_info=e)

    async def send_random_dm(self, user_id: int, history_cog: Optional[HistoryCog]):
        """指定ユーザーにランダムDMを送信する (generate_content 修正)"""
        # --- ユーザーオブジェクト取得 (変更なし) ---
        user = self.bot.get_user(user_id)
        if user is None:
            try: user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException) as e: logger.warning(f"Could not fetch user {user_id}: {e}"); return
        if user.bot: logger.info(f"Skipping DM to bot user: {user.name}"); return

        logger.info(f"Attempting random DM to {user.display_name} (ID: {user_id})")

        # --- Gemini クライアントチェック (変更なし) ---
        if not self.genai_client:
            logger.error("RandomDMCog: Gemini client not initialized. Cannot send AI-generated DM.")
            return # AIが使えない場合は中止

        try:
            # --- DMチャンネル取得/作成 (変更なし) ---
            try: dm_channel = user.dm_channel or await user.create_dm()
            except discord.Forbidden: logger.warning(f"Cannot create DM channel for user {user_id}."); return
            except discord.HTTPException as e: logger.error(f"Failed create DM channel for {user_id}", exc_info=e); return

            # --- プロンプトと呼び名の準備 (変更なし) ---
            dm_prompt_text_base = config_manager.get_random_dm_prompt()
            user_representation = user.display_name
            call_name = self.get_call_name(user_id)

            # --- 要約履歴準備 (変更なし) ---
            summaries_all = await config_manager.load_summaries()
            max_summary_tokens = config_manager.get_summary_max_prompt_tokens()
            filtered_summaries = []
            current_summary_tokens = 0
            for summary in reversed(summaries_all):
                summary_text = summary.get("summary_text", "")
                estimated_tokens = len(summary_text) * 1.5
                if current_summary_tokens + estimated_tokens <= max_summary_tokens:
                    filtered_summaries.append(summary)
                    current_summary_tokens += estimated_tokens
                else: break
            filtered_summaries.reverse()
            logger.info(f"Using {len(filtered_summaries)} summaries for random DM context to {user_id}.")

            # --- システムインストラクション生成 (変更なし) ---
            def create_system_prompt(summarized_history: List[Dict[str, Any]]) -> genai_types.Content:
                # (GUI版の省略なしのコードをそのまま使用)
                persona_prompt = config_manager.get_persona_prompt()
                sys_prompt = persona_prompt
                # 既知ユーザー
                sys_prompt += "\n\n--- 既知ユーザー ---"
                all_identifiers = config_manager.get_all_user_identifiers()
                if all_identifiers:
                    for uid, identifier in all_identifiers.items(): sys_prompt += f"\n- {identifier} (ID: {uid})"
                else: sys_prompt += "\n(なし)"
                sys_prompt += f"\n- {self.bot.user.display_name} (ID: {self.bot.user.id}) (Bot)"
                sys_prompt += "\n--------------------"
                # 重要情報
                sys_prompt += f"\n\n--- ★重要情報★ ---"
                sys_prompt += f"\nこれから以下ユーザーに**あなたから**DM送信:"
                sys_prompt += f"\n- 名前: {user_representation}"
                sys_prompt += f"\n- ID: {user_id}"
                sys_prompt += f"\n- ★★ 呼名: 「{call_name}」 ★★"
                sys_prompt += "\n--------------------"
                # 過去要約
                if summarized_history:
                    sys_prompt += "\n\n--- 過去要約(古順) ---"
                    user_id_pattern = re.compile(r'(?:User |ID:)(\d+)')
                    for summary in summarized_history:
                        ts_str = "(時刻不明)"
                        added_ts = summary.get("added_timestamp")
                        if isinstance(added_ts, datetime.datetime): ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                        speaker_name = summary.get("speaker_call_name_at_summary", "(不明)")
                        summary_text = summary.get("summary_text", "(要約なし)")
                        def replace_id(match):
                            try: return self.get_call_name(int(match.group(1)))
                            except: return match.group(0)
                        formatted_summary_text = user_id_pattern.sub(replace_id, summary_text)
                        sys_prompt += f"\n[{ts_str}] {speaker_name}: {formatted_summary_text}"
                    sys_prompt += "\n----------------------"
                else: sys_prompt += "\n\n(過去要約なし)"
                # 最重要指示
                sys_prompt += f"\n\n--- ★最重要指示★ ---"
                sys_prompt += f"\n1.呼名必須:「{call_name}」。あだ名も可。"
                sys_prompt += f"\n2.指示:「{dm_prompt_text_base}」に基づき自然な初DM作成。質問推奨。履歴参考程度。"
                sys_prompt += f"\n3.厳禁:[名前]:形式不可。"
                sys_prompt += f"\n4.[]引用符使用禁止。"
                sys_prompt += "\n----------------------\n"
                logger.debug(f"System Prompt generated for Random DM to {user_id}:\n{sys_prompt[:500]}...")
                # return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system") # role="system" は使わない
                return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)]) # role指定なし
            # --- システムプロンプトここまで ---

            system_instruction_content = create_system_prompt(filtered_summaries)

            # --- 履歴と開始メッセージの準備 (変更なし) ---
            history_list = []
            if history_cog: # HistoryCog が利用可能かチェック
                 history_list = await history_cog.get_global_history_for_prompt()
            else:
                 logger.warning("HistoryCog not available, sending random DM without history context.")
            # ダミーユーザーメッセージ
            start_message_content = genai_types.Content(role="user", parts=[genai_types.Part(text=f"（内部指示: {call_name}さんへのDM開始。テーマ: 「{dm_prompt_text_base}」）")])

            contents_for_api = []
            contents_for_api.extend(history_list)
            contents_for_api.append(start_message_content)

            # --- API呼び出し準備 (変更なし) ---
            model_name = config_manager.get_model_name()
            generation_config_dict = config_manager.get_generation_config_dict()
            safety_settings_list = config_manager.get_safety_settings_list()

            config_args = generation_config_dict.copy()
            if safety_settings_list:
                config_args['safety_settings'] = [genai_types.SafetySetting(**s) for s in safety_settings_list]
            config_args['system_instruction'] = system_instruction_content
            # ツールはDMでは使わない
            # config_args['tools'] = None
            final_config = genai_types.GenerateContentConfig(**config_args) if config_args else None

            logger.info(f"Sending random DM request to Gemini. Model: {model_name}, History: {len(history_list)}, Summaries: {len(filtered_summaries)}")
            if not contents_for_api:
                logger.error("Contents for random DM API call are empty. Aborting.")
                return

            # ★★★ generate_content 呼び出し (CUI版に合わせる) ★★★
            response = self.genai_client.models.generate_content(
                model=model_name, # ★ `models/` プレフィックスを削除 ★
                contents=contents_for_api,
                config=final_config
            )
            # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★

            logger.debug(f"Gemini Response for random DM ({user_id}): FinishReason={response.candidates[0].finish_reason if response.candidates else 'N/A'}")

            # --- 応答処理 (変更なし) ---
            response_text = ""
            response_parts = []
            finish_reason = None

            if response and response.candidates:
                candidate = response.candidates[0]
                finish_reason = candidate.finish_reason
                if candidate.content and candidate.content.parts:
                    response_parts = candidate.content.parts
                else:
                    logger.warning(f"No parts found in candidate for random DM to {user_id}. Finish reason: {finish_reason}")
                    if finish_reason == genai_types.FinishReason.SAFETY: response_text = f"({call_name}さん、メッセージ生成が安全フィルタでブロックされました。)"
                    elif finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、メッセージ生成が引用超過で停止しました。)"
            else:
                logger.warning(f"No valid response or candidates received for random DM to {user_id}.")

            if not response_text:
                raw_response_text = "".join(part.text for part in response_parts if hasattr(part, 'text') and part.text)
                response_text = raw_response_text.strip()

            if not response_text:
                logger.warning(f"Empty response text after processing parts for random DM to user {user_id}. Sending default.")
                response_text = f"{call_name}さん、こんにちは！" # 最終フォールバック

            # --- 応答整形・送信・履歴保存 (GUI版の part_to_dict を使用) ---
            text_after_citation = helpers.remove_citation_marks(response_text)
            text_after_prefixes = helpers.remove_all_prefixes(text_after_citation) # 送信前に除去
            max_len = config_manager.get_max_response_length()
            original_len_after_clean = len(text_after_prefixes)

            final_response_text = None
            if original_len_after_clean > max_len:
                final_response_text = text_after_prefixes[:max_len - 3] + "..."
            elif original_len_after_clean > 0:
                final_response_text = text_after_prefixes
            elif response_text.startswith("("): # エラー/情報メッセージ
                final_response_text = response_text

            logger.debug(f"Final random DM text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")

            if final_response_text:
                try:
                    await dm_channel.send(final_response_text)
                    logger.info(f"Sent random DM to {user.display_name} (ID: {user_id})")

                    # 履歴保存 (エラーメッセージでない場合、HistoryCogがあれば)
                    if history_cog and not final_response_text.startswith("("):
                        # GUI版の part_to_dict (プレフィックス含む)
                        def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                            data = {}
                            if hasattr(part, 'text') and part.text and part.text.strip():
                                data['text'] = part.text.strip()
                            # DMでは他のパートタイプは稀
                            return data if data else {}

                        bot_response_parts_dict = [p_dict for part in response_parts if (p_dict := part_to_dict(part, True))]
                        if bot_response_parts_dict:
                            await history_cog.add_history_entry_async(
                                current_interlocutor_id=user_id, channel_id=None, role="model",
                                parts_dict=bot_response_parts_dict, entry_author_id=self.bot.user.id
                            )
                            logger.info(f"Added random DM response to history for user {user_id}")
                        else:
                            logger.warning(f"No valid parts to add to history for random DM to user {user_id}")

                except discord.Forbidden:
                    logger.warning(f"Cannot send random DM to user {user_id} (Forbidden).")
                except discord.HTTPException as http_e:
                    logger.error(f"Failed to send random DM to {user_id} due to Discord API error", exc_info=http_e)
                except Exception as send_e:
                    logger.error(f"Unexpected error during sending random DM or saving history for user {user_id}", exc_info=send_e)
            else:
                logger.info(f"Skipped sending empty or invalid random DM to user {user_id}.")

        # --- エラーハンドリング (変更なし) ---
        except genai_errors.APIError as e:
            logger.error(f"Gemini API Error during random DM preparation/generation for user {user_id}.", exc_info=e)
        except Exception as e:
            logger.error(f"Unexpected error during preparing/sending random DM for user {user_id}", exc_info=True)

    @dm_sender_loop.before_loop
    async def before_dm_sender_loop(self):
        """ループ開始前に実行される処理 (変更なし)"""
        await self.bot.wait_until_ready()
        logger.info("Random DM sender loop is ready.")

# --- setup 関数 (変更なし) ---
async def setup(bot: commands.Bot):
    # Bot準備完了 & HistoryCog ロード後に Cog 追加
    await bot.wait_until_ready()
    # HistoryCog のインスタンスを確実に取得
    history_cog_instance = bot.get_cog("HistoryCog")
    if history_cog_instance:
        await bot.add_cog(RandomDMCog(bot))
        logger.info("RandomDMCog setup complete and added to bot.")
    else:
        logger.warning("HistoryCog not loaded when attempting to setup RandomDMCog. RandomDMCog will not be added.")
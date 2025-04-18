# cogs/random_dm_cog.py (型ヒントエラー修正 - 完全版)
from __future__ import annotations # ★ ファイルの先頭に追加

import discord
from discord.ext import commands, tasks
import logging
import datetime
import asyncio
import random
from typing import Optional, List, Dict, Any, TYPE_CHECKING # ★ TYPE_CHECKING をインポート
import os
import re
import threading

from utils import config_manager
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
from utils import helpers

# ★ HistoryCog のインポートサイクルの問題を避けるため TYPE_CHECKING を使う
if TYPE_CHECKING:
    from cogs.history_cog import HistoryCog

logger = logging.getLogger(__name__)

class RandomDMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client = None
        self.user_data_lock = config_manager.user_data_lock
        self.dm_sender_loop.start()
        logger.info("RandomDMCog loaded and task started.")

    def cog_unload(self):
        self.dm_sender_loop.cancel()
        logger.info("RandomDMCog unloaded and task stopped.")

    # --- 呼び名取得ヘルパー関数 ---
    def get_call_name(self, target_user_id: Optional[int]) -> str:
        if target_user_id is None: return "(不明な相手)"
        if target_user_id == self.bot.user.id: return self.bot.user.display_name
        with self.user_data_lock: nickname = config_manager.app_config.get("user_data", {}).get(str(target_user_id), {}).get("nickname")
        if nickname: return nickname
        user = self.bot.get_user(target_user_id)
        if user: return user.display_name
        return f"User {target_user_id}"

    async def initialize_genai_client_if_needed(self):
        if self.genai_client: return True
        logger.debug("Attempting to get GenAI client for RandomDMCog...")
        chat_cog = self.bot.get_cog("ChatCog")
        if chat_cog and hasattr(chat_cog, 'genai_client') and chat_cog.genai_client:
            self.genai_client = chat_cog.genai_client; logger.info("Using GenAI client from ChatCog for Random DM."); return True
        logger.warning("Could not get GenAI client from ChatCog. Attempting independent initialization...")
        try:
            api_key = config_manager.get_gemini_api_key()
            if not api_key: logger.error("GOOGLE_AI_KEY not found. Random DM cannot use AI."); return False
            self.genai_client = genai.Client(api_key=api_key); logger.info("Gemini client initialized independently for RandomDMCog."); return True
        except Exception as e: logger.error("Failed to init Gemini client for RandomDMCog", exc_info=e); self.genai_client = None; return False

    async def reset_user_timer(self, user_id: int):
        user_id_str = str(user_id); logger.debug(f"Resetting Random DM timer in memory for user {user_id_str} due to interaction.")
        with self.user_data_lock:
             user_data_dict = config_manager.app_config.setdefault("user_data", {})
             if user_id_str in user_data_dict:
                 user_settings = user_data_dict[user_id_str].setdefault("random_dm", config_manager.get_default_random_dm_config())
                 if user_settings.get("enabled"):
                     now_aware = datetime.datetime.now().astimezone(); user_settings["last_interaction"] = now_aware; user_settings["next_send_time"] = None
                     logger.info(f"Random DM timer reset in memory for user {user_id}.")
             else: logger.debug(f"User {user_id_str} not found for timer reset.")

    @tasks.loop(seconds=10.0)
    async def dm_sender_loop(self):
        if not self.bot.is_ready(): logger.debug("Bot not ready, skipping DM loop."); return
        if not await self.initialize_genai_client_if_needed(): logger.warning("GenAI client not available, skipping DM loop iteration."); await asyncio.sleep(60); return

        now = datetime.datetime.now().astimezone(); logger.debug(f"Running dm_sender_loop check at {now.isoformat()}")
        users_to_dm: List[int] = []; memory_updated = False

        with self.user_data_lock:
            user_data_dict = config_manager.app_config.setdefault("user_data", {})
            for user_id_str, u_data in list(user_data_dict.items()):
                try:
                    user_id = int(user_id_str)
                    dm_config_current = u_data.setdefault("random_dm", config_manager.get_default_random_dm_config())
                    if not dm_config_current.get("enabled"): continue
                    stop_start = dm_config_current.get("stop_start_hour"); stop_end = dm_config_current.get("stop_end_hour"); is_stopping_time = False
                    if stop_start is not None and stop_end is not None:
                        current_hour_local = now.hour
                        if stop_start > stop_end: is_stopping_time = (current_hour_local >= stop_start or current_hour_local < stop_end)
                        else: is_stopping_time = (stop_start <= current_hour_local < stop_end)
                    if is_stopping_time: logger.debug(f"Skip DM user {user_id} (stop time: {stop_start}-{stop_end} local)"); continue
                    last_interact_dt = dm_config_current.get("last_interaction"); next_send_time_dt = dm_config_current.get("next_send_time"); default_tz = now.tzinfo
                    if isinstance(last_interact_dt, datetime.datetime): last_interact_dt = last_interact_dt.astimezone(default_tz)
                    else: last_interact_dt = datetime.datetime.min.replace(tzinfo=default_tz)
                    if isinstance(next_send_time_dt, datetime.datetime): next_send_time_dt = next_send_time_dt.astimezone(default_tz)

                    if next_send_time_dt is None:
                        min_interval = dm_config_current.get("min_interval", 21600); max_interval = dm_config_current.get("max_interval", 172800)
                        interval_sec = random.uniform(min_interval, max_interval); calculated_next_send = last_interact_dt + datetime.timedelta(seconds=interval_sec)
                        dm_config_current["next_send_time"] = calculated_next_send; memory_updated = True
                        logger.info(f"Calculated next DM time user {user_id}: {calculated_next_send.isoformat()} (Interval: {interval_sec:.0f}s)")
                        continue

                    if now >= next_send_time_dt:
                        logger.info(f"Time condition met user {user_id}: now={now.isoformat()}, next={next_send_time_dt.isoformat()}")
                        users_to_dm.append(user_id); dm_config_current["next_send_time"] = None; dm_config_current["last_interaction"] = now; memory_updated = True

                except ValueError: logger.warning(f"Invalid user ID string: {user_id_str}")
                except Exception as e: logger.error(f"Error processing user {user_id_str} in loop", exc_info=e)

        if memory_updated:
            logger.info("Random DM config updated in memory. Scheduling save...")
            try: asyncio.create_task(self.trigger_config_save())
            except Exception as e: logger.error("Error scheduling config save", exc_info=e)

        if users_to_dm:
            logger.info(f"Preparing to send random DMs to {len(users_to_dm)} users: {users_to_dm}")
            # ★ 型ヒント修正 (文字列リテラル不要に)
            history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
            if not history_cog: logger.error("HistoryCog not found! Cannot access history."); return

            send_tasks = [self.send_random_dm(user_id, history_cog) for user_id in users_to_dm]
            if send_tasks:
                results = await asyncio.gather(*send_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception): logger.error(f"Error sending DM to user {users_to_dm[i]}", exc_info=result)
            logger.info(f"Finished dm_sender_loop iteration for {len(users_to_dm)} users.")

    async def trigger_config_save(self):
         await asyncio.sleep(1)
         try: config_manager.save_app_config(); logger.info("App config saved successfully after DM loop update.")
         except Exception as e: logger.error("Failed to save app config after DM loop update", exc_info=e)

    # ★ 型ヒント修正 (文字列リテラル不要に)
    async def send_random_dm(self, user_id: int, history_cog: HistoryCog):
        # (send_random_dm の中身は変更なし)
        user = self.bot.get_user(user_id)
        if user is None:
            try: user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException) as e: logger.warning(f"Could not fetch user {user_id}: {e}"); return
        if user.bot: logger.info(f"Skipping DM to bot user: {user.name}"); return
        logger.info(f"Attempting random DM to {user.display_name} (ID: {user_id})")
        try:
            try: dm_channel = user.dm_channel or await user.create_dm()
            except discord.Forbidden: logger.warning(f"Cannot create DM for {user_id}."); return
            except discord.HTTPException as e: logger.error(f"Failed create DM {user_id}", exc_info=e); return
            dm_prompt_text_base = config_manager.get_random_dm_prompt(); user_representation = user.display_name; call_name = self.get_call_name(user_id)
            summaries_all = await config_manager.load_summaries(); max_summary_tokens = config_manager.get_summary_max_prompt_tokens()
            filtered_summaries = []; current_summary_tokens = 0
            for summary in reversed(summaries_all):
                summary_text = summary.get("summary_text", ""); estimated_tokens = len(summary_text) * 1.5
                if current_summary_tokens + estimated_tokens <= max_summary_tokens: filtered_summaries.append(summary); current_summary_tokens += estimated_tokens
                else: logger.debug(f"Summary token limit ({max_summary_tokens}) reached."); break
            filtered_summaries.reverse(); logger.info(f"Loaded {len(summaries_all)} summaries, using {len(filtered_summaries)} for random DM to {user_id}.")
            def create_system_prompt(summarized_history: List[Dict[str, Any]]):
                persona_prompt = config_manager.get_persona_prompt(); sys_prompt_text = persona_prompt
                sys_prompt_text += "\n\n--- 既知のユーザーとその固有名 ---"; all_identifiers = config_manager.get_all_user_identifiers()
                if all_identifiers:
                    for uid, identifier in all_identifiers.items(): sys_prompt_text += f"\n- {identifier} (ID: {uid})"
                else: sys_prompt_text += "\n(既知のユーザーなし)"
                sys_prompt_text += f"\n- {self.bot.user.display_name} (ID: {self.bot.user.id}) (Bot自身)"; sys_prompt_text += "\n------------------------------------"
                sys_prompt_text += f"\n\n--- ★★★ 現在の最重要情報 ★★★ ---"; sys_prompt_text += f"\nあなたはこれから、以下の Discord ユーザーに**あなたから**ダイレクトメッセージ（DM）を送ります。"; sys_prompt_text += f"\n- ユーザー名(表示名): {user_representation}"; sys_prompt_text += f"\n- ユーザーID: {user_id}"; sys_prompt_text += f"\n- ★★ あなたが呼びかけるべき名前: 「{call_name}」 ★★"; sys_prompt_text += "\n---------------------------------"
                if summarized_history:
                    sys_prompt_text += "\n\n--- 過去の会話の要約 (古い順、参考情報) ---"; user_id_pattern = re.compile(r'(?:User |ID:)(\d+)')
                    for summary in summarized_history:
                        ts_str = "(時刻不明)"; added_ts = summary.get("added_timestamp");
                        if isinstance(added_ts, datetime.datetime): ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                        speaker_name = summary.get("speaker_call_name_at_summary", "(不明)"); summary_text = summary.get("summary_text", "(要約なし)")
                        def replace_user_id_with_name(match):
                            try: matched_id = int(match.group(1)); latest_call_name = self.get_call_name(matched_id); return latest_call_name if latest_call_name != match.group(0) else match.group(0)
                            except (ValueError, IndexError): return match.group(0)
                        formatted_summary_text = user_id_pattern.sub(replace_user_id_with_name, summary_text)
                        sys_prompt_text += f"\n[{ts_str}] {speaker_name}: {formatted_summary_text}"
                    sys_prompt_text += "\n---------------------------------------"
                else: sys_prompt_text += "\n\n(過去の会話の要約はありません)"
                sys_prompt_text += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"; sys_prompt_text += f"\n1. **最優先事項:** これはあなたからの最初のDM、または久しぶりの声かけです。応答する際は、**必ず、絶対に「{call_name}」という名前で呼びかけてください。**"; sys_prompt_text += f"\n2. **今回のあなたの発言指示:** 「{dm_prompt_text_base}」に基づき、フレンドリーで自然な最初のメッセージを作成してください。相手が返信しやすいような、オープンな質問を含めると良いでしょう。過去の要約や履歴は**参考程度**に留め、**今回のDMは新しい会話として自然に**始めてください。"; sys_prompt_text += f"\n3. **厳禁:** 応答のいかなる部分にも `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。"; sys_prompt_text += f"\n4. 引用符 `[]` は使用禁止です。"
                sys_prompt_text += "\n----------------------------------------\n"; logger.debug(f"Generated System Prompt for Random DM to {user_id}:\n{sys_prompt_text[:500]}..."); return genai_types.Content(parts=[genai_types.Part(text=sys_prompt_text)], role="system")
            system_instruction_content = create_system_prompt(filtered_summaries)
            history_list = [];
            if history_cog: history_list = await history_cog.get_global_history_for_prompt() # history_cog が None でないことを確認
            else: logger.warning("HistoryCog not available.")
            start_message_content = genai_types.Content(role="user", parts=[genai_types.Part(text=f"（{call_name}さんへのDM開始指示: {dm_prompt_text_base}）")])
            contents_for_api = []; contents_for_api.extend(history_list); contents_for_api.append(start_message_content)
            model_name = config_manager.get_model_name(); generation_config_dict = config_manager.get_generation_config_dict(); safety_settings_list = config_manager.get_safety_settings_list()
            safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]; tools_for_api = None
            final_generation_config = genai_types.GenerationConfig( temperature=generation_config_dict.get('temperature', 0.9), top_p=generation_config_dict.get('top_p', 1.0), top_k=generation_config_dict.get('top_k', 1), candidate_count=generation_config_dict.get('candidate_count', 1), max_output_tokens=generation_config_dict.get('max_output_tokens', 512))
            model = self.genai_client.GenerativeModel(model_name=model_name, safety_settings=safety_settings_for_api, generation_config=final_generation_config, system_instruction=system_instruction_content, tools=tools_for_api)
            logger.info(f"Sending random DM request to Gemini. Model: {model_name}, History: {len(history_list)}, Summaries: {len(filtered_summaries)}")
            if not contents_for_api: logger.error("Cannot send request random DM, contents_for_api empty."); return
            response = await model.generate_content_async(contents=contents_for_api)
            logger.debug(f"Gemini Response random DM ({user_id}): FinishReason={response.candidates[0].finish_reason if response.candidates else 'N/A'}")
            response_text = ""; response_parts = []
            if response and response.candidates: candidate = response.candidates[0];
            if candidate.content and candidate.content.parts: response_parts = candidate.content.parts
            else: finish_reason = candidate.finish_reason; logger.warning(f"No parts random DM {user_id}. Finish: {finish_reason}")
            if not response_text: response_text = "".join(part.text for part in response_parts if hasattr(part, 'text') and part.text).strip()
            if not response_text: logger.warning(f"Empty response text random DM user {user_id}."); return
            text_after_citation = helpers.remove_citation_marks(response_text); text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
            max_len = config_manager.get_max_response_length(); original_len_after_clean = len(text_after_prefixes)
            final_response_text = text_after_prefixes[:max_len - 3] + "..." if original_len_after_clean > max_len else text_after_prefixes if original_len_after_clean > 0 else None
            logger.debug(f"Final random DM text send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")
            if final_response_text:
                try:
                    await dm_channel.send(final_response_text); logger.info(f"Sent random DM to {user.display_name} (ID: {user_id})")
                    if history_cog and not final_response_text.startswith("("): # history_cog が None でないことを確認
                         def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                             data = {};
                             if hasattr(part, 'text') and part.text and part.text.strip(): data['text'] = part.text.strip()
                             return data if data.get('text') else {}
                         bot_response_parts_dict = [p_dict for part in response_parts if (p_dict := part_to_dict(part, is_model_response=True))]
                         if bot_response_parts_dict: await history_cog.add_history_entry_async( current_interlocutor_id=user_id, channel_id=None, role="model", parts_dict=bot_response_parts_dict, entry_author_id=self.bot.user.id ); logger.info(f"Added random DM response to history for user {user_id}")
                         else: logger.warning(f"No valid parts to add history for random DM user {user_id}")
                except discord.Forbidden: logger.warning(f"Cannot send random DM to {user_id}.")
                except discord.HTTPException as http_e: logger.error(f"Failed send DM {user_id}", exc_info=http_e)
                except Exception as send_e: logger.error(f"Unexpected error sending DM or history {user_id}", exc_info=send_e)
            elif response_text.startswith("("):
                 try: await dm_channel.send(response_text); logger.info(f"Sent info/error message random DM {user_id}: {response_text}")
                 except Exception as send_e: logger.error(f"Error sending info/error message random DM {user_id}", exc_info=send_e)
            else: logger.info(f"Skipped sending empty/invalid random DM to user {user_id}.")
        except genai_errors.GoogleAPIError as e: logger.error(f"Gemini API Error random DM prep {user_id}.", exc_info=e)
        except Exception as e: logger.error(f"Error preparing/sending random DM {user_id}", exc_info=True)

    @dm_sender_loop.before_loop
    async def before_dm_sender_loop(self):
        await self.bot.wait_until_ready()
        logger.info("Random DM sender loop is ready.")

async def setup(bot: commands.Bot):
    await bot.wait_until_ready()
    # ★ HistoryCog のインポート失敗チェックは不要 (TYPE_CHECKINGで処理)
    history_cog_instance = bot.get_cog("HistoryCog")
    if history_cog_instance:
        await bot.add_cog(RandomDMCog(bot)); logger.info("RandomDMCog setup complete.")
    else:
        logger.warning("HistoryCog not loaded. RandomDMCog will not be added.")
# cogs/random_dm_cog.py (要約読み込み・プロンプト追加)
import discord
from discord.ext import commands, tasks
import logging
import datetime
# from datetime import timezone
import asyncio
import random
from typing import Optional, List, Dict, Any
import os

# config_manager や genai 関連をインポート
from utils import config_manager
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
from utils import helpers
from cogs.history_cog import HistoryCog

logger = logging.getLogger(__name__)

class RandomDMCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client = None
        self.user_data_lock = config_manager.data_lock
        self.dm_sender_loop.start()
        logger.info("RandomDMCog loaded and task started.")

    def cog_unload(self):
        self.dm_sender_loop.cancel()
        logger.info("RandomDMCog unloaded and task stopped.")

    async def initialize_genai_client_if_needed(self):
        # (変更なし)
        if self.genai_client: return True
        chat_cog = self.bot.get_cog("ChatCog")
        if chat_cog and chat_cog.genai_client:
            self.genai_client = chat_cog.genai_client
            logger.info("Using GenAI client from ChatCog for Random DM."); return True
        else:
            logger.warning("Could not get GenAI client from ChatCog. Attempting independent initialization for RandomDMCog...")
            try:
                api_key = os.getenv("GOOGLE_AI_KEY")
                if not api_key: logger.error("GOOGLE_AI_KEY not found. Random DM cannot use AI."); return False
                self.genai_client = genai.Client(api_key=api_key)
                logger.info("Gemini client initialized independently for RandomDMCog."); return True
            except Exception as e: logger.error("Failed to init Gemini client for RandomDMCog", exc_info=e); self.genai_client = None; return False

    async def reset_user_timer(self, user_id: int):
        # (変更なし)
        user_id_str = str(user_id)
        logger.debug(f"Resetting Random DM timer in memory for user {user_id_str} due to interaction.")
        async with self.user_data_lock:
             if user_id_str in config_manager.user_data:
                 user_settings = config_manager.user_data[user_id_str].get("random_dm")
                 if user_settings and user_settings.get("enabled"):
                     now_aware = datetime.datetime.now().astimezone()
                     user_settings["last_interaction"] = now_aware
                     user_settings["next_send_time"] = None
                     logger.info(f"Random DM timer reset in memory for user {user_id}.")
             else: logger.debug(f"User {user_id_str} not found in user_data for timer reset.")

    @tasks.loop(seconds=10.0)
    async def dm_sender_loop(self):
        # (変更なし - ループのロジック自体は変更なし)
        if not await self.initialize_genai_client_if_needed(): logger.warning("GenAI client not available in RandomDMCog, skipping loop iteration."); await asyncio.sleep(60); return
        now = datetime.datetime.now().astimezone(); logger.debug(f"Running dm_sender_loop check at {now.isoformat()}")
        users_to_dm: List[int] = []; users_to_update_config: Dict[int, Dict[str, Any]] = {}
        async with self.user_data_lock:
            for user_id_str, u_data in config_manager.user_data.items():
                try:
                    user_id = int(user_id_str); dm_config_current = u_data.get("random_dm", config_manager.get_default_random_dm_config())
                    if not dm_config_current.get("enabled"): continue
                    stop_start = dm_config_current.get("stop_start_hour"); stop_end = dm_config_current.get("stop_end_hour"); is_stopping_time = False
                    if stop_start is not None and stop_end is not None:
                        current_hour_local = now.hour
                        if stop_start > stop_end: is_stopping_time = (current_hour_local >= stop_start or current_hour_local < stop_end)
                        else: is_stopping_time = (stop_start <= current_hour_local < stop_end)
                    if is_stopping_time: logger.debug(f"Skip DM user {user_id} (stop time: {stop_start}-{stop_end} local)"); continue
                    last_interact_dt = dm_config_current.get("last_interaction"); next_send_time_dt = dm_config_current.get("next_send_time"); default_tz = now.tzinfo
                    if isinstance(last_interact_dt, datetime.datetime):
                         if last_interact_dt.tzinfo is None: last_interact_dt = last_interact_dt.replace(tzinfo=default_tz)
                         else: last_interact_dt = last_interact_dt.astimezone(default_tz)
                    else: last_interact_dt = datetime.datetime.min.replace(tzinfo=default_tz)
                    if isinstance(next_send_time_dt, datetime.datetime):
                         if next_send_time_dt.tzinfo is None: next_send_time_dt = next_send_time_dt.replace(tzinfo=default_tz)
                         else: next_send_time_dt = next_send_time_dt.astimezone(default_tz)
                    logger.debug(f"Checking User {user_id}: Enabled={dm_config_current.get('enabled')}, LastInteract={last_interact_dt.isoformat() if last_interact_dt else 'None'}, NextSend={next_send_time_dt.isoformat() if next_send_time_dt else 'None'}, Now={now.isoformat()}")
                    if next_send_time_dt is None:
                        min_interval = dm_config_current.get("min_interval", 3600 * 6); max_interval = dm_config_current.get("max_interval", 86400 * 2)
                        interval_sec = random.uniform(min_interval, max_interval); calculated_next_send = last_interact_dt + datetime.timedelta(seconds=interval_sec)
                        dm_config_current["next_send_time"] = calculated_next_send; users_to_update_config[user_id] = dm_config_current
                        logger.info(f"Calculated next DM time for user {user_id}: {calculated_next_send.isoformat()} (Interval: {interval_sec:.0f}s)"); continue
                    if now >= next_send_time_dt:
                        logger.info(f"Time condition met for user {user_id}: now={now.isoformat()}, next_send_time={next_send_time_dt.isoformat()}")
                        users_to_dm.append(user_id); dm_config_current["next_send_time"] = None; dm_config_current["last_interaction"] = now; users_to_update_config[user_id] = dm_config_current
                    else: time_diff = next_send_time_dt - now; logger.debug(f"Time condition NOT met for user {user_id}. Send in {time_diff.total_seconds():.1f} seconds.")
                except Exception as e: logger.error(f"Error processing user {user_id_str} in dm_sender_loop", exc_info=e)
            if users_to_update_config:
                logger.debug(f"Updating random DM configs in memory and file for users: {list(users_to_update_config.keys())}")
                try: await config_manager.save_user_data_nolock(); logger.debug("Finished updating random DM configs in file.")
                except Exception as e: logger.error("Error during bulk save of random DM configs", exc_info=e)
        if users_to_dm:
            logger.info(f"Preparing to send random DMs to {len(users_to_dm)} users: {users_to_dm}")
            history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog");
            if not history_cog: logger.error("HistoryCog not found! Cannot send random DMs."); return
            send_tasks = [self.send_random_dm(user_id, history_cog) for user_id in users_to_dm]
            if send_tasks:
                results = await asyncio.gather(*send_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception): logger.error(f"Error occurred while sending DM to user {users_to_dm[i]}", exc_info=result)
            logger.info(f"Finished dm_sender_loop iteration for {len(users_to_dm)} users.")


    async def send_random_dm(self, user_id: int, history_cog: HistoryCog):
        """指定ユーザーにランダムDMを送信する"""
        user = self.bot.get_user(user_id)
        if user is None:
            try: user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException) as e: logger.warning(f"Could not find/fetch user {user_id} for random DM: {e}"); return
        if user.bot: logger.info(f"Skipping random DM to bot user: {user.name} (ID: {user_id})"); return

        logger.info(f"Attempting to send random DM to {user.display_name} (ID: {user_id})")
        try:
            try: dm_channel = user.dm_channel or await user.create_dm()
            except discord.Forbidden: logger.warning(f"Cannot create DM channel for user {user_id}. DMs might be disabled."); return
            except discord.HTTPException as e: logger.error(f"Failed to create DM channel for user {user_id}", exc_info=e); return

            dm_prompt_text_base = config_manager.get_random_dm_prompt()
            user_nickname = config_manager.get_nickname(user_id)
            user_representation = user.display_name
            call_name = user_nickname if user_nickname else user_representation

            # --- ★ 要約履歴の読み込みと調整 ★ ---
            summaries_all = await config_manager.load_summaries()
            max_summary_tokens = config_manager.get_summary_max_prompt_tokens()
            filtered_summaries = []
            current_summary_tokens = 0
            for summary in reversed(summaries_all):
                summary_text = summary.get("summary_text", "")
                estimated_tokens = len(summary_text) * 1.5 # 文字数で概算
                if current_summary_tokens + estimated_tokens <= max_summary_tokens:
                    filtered_summaries.append(summary)
                    current_summary_tokens += estimated_tokens
                else: logger.debug(f"Summary token limit ({max_summary_tokens}) reached for random DM."); break
            filtered_summaries.reverse()
            logger.info(f"Loaded {len(summaries_all)} summaries, using {len(filtered_summaries)} summaries ({current_summary_tokens:.0f} estimated tokens) for random DM prompt to {user_id}.")

            # --- ★ システムプロンプト生成関数 (要約情報受け取り) ★ ---
            def create_system_prompt(summarized_history: List[Dict[str, Any]]):
                persona_prompt = config_manager.get_persona_prompt()
                sys_prompt_text = persona_prompt
                sys_prompt_text += f"\n\n--- ★★★ 現在の最重要情報 ★★★ ---"
                sys_prompt_text += f"\nあなたはこれから、以下の Discord ユーザーに**あなたから**ダイレクトメッセージ（DM）を送ります。これは新しい会話の始まり、または久しぶりの声かけです。"
                sys_prompt_text += f"\n- ユーザー名: {user_representation} (Discord 表示名)"
                sys_prompt_text += f"\n- ユーザーID: {user_id}"
                sys_prompt_text += f"\n- ★★ あなたが呼びかけるべき名前: 「{call_name}」 ★★"
                sys_prompt_text += f"\n   (注: これは設定されたニックネーム、またはユーザー表示名です。)"
                sys_prompt_text += f"\n- 会話の場所: ダイレクトメッセージ (DM)"
                sys_prompt_text += "\n---------------------------------"

                # ★★★ 過去の会話の要約情報を追加 ★★★
                if summarized_history:
                    sys_prompt_text += "\n\n--- 過去の会話の要約 (古い順、参考情報) ---"
                    for summary in summarized_history:
                        ts_str = "(時刻不明)"
                        added_ts = summary.get("added_timestamp")
                        if isinstance(added_ts, datetime.datetime): ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                        speaker_name = summary.get("speaker_call_name_at_summary", "(不明)")
                        summary_text = summary.get("summary_text", "(要約なし)")
                        sys_prompt_text += f"\n[{ts_str}] {speaker_name}: {summary_text}"
                    sys_prompt_text += "\n---------------------------------------"
                else: sys_prompt_text += "\n\n(過去の会話の要約はありません)"

                sys_prompt_text += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"
                sys_prompt_text += f"\n1. **最優先事項:** これはあなたからの最初のDM、または久しぶりの声かけです。応答する際は、**必ず、絶対に「{call_name}」という名前で呼びかけてください。** 他の呼び方は**禁止**します。"
                sys_prompt_text += f"\n2. **注意:** 上記の「過去の会話の要約」や後述の「[最近の会話]」履歴は参考情報です。**今回のDMの内容は、現在の相手「{call_name}」さんとの新しい会話として自然なものにしてください。** 過去の他の会話に引きずられないように注意してください。"
                sys_prompt_text += f"\n3. **今回のあなたの発言指示:** 「{dm_prompt_text_base}」に基づき、フレンドリーで自然な最初のメッセージを作成してください。相手が返信しやすいような、オープンな質問を含めると良いでしょう。"
                sys_prompt_text += f"\n4. **厳禁:** あなたの応答の **いかなる部分にも** `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。あなたの応答は、会話本文のみで構成してください。"
                sys_prompt_text += f"\n5. 引用符 `[]` は使用禁止です。"
                sys_prompt_text += "\n----------------------------------------\n"
                logger.debug(f"Generated System Prompt for Random DM to {user_id} (summaries: {len(summarized_history)}):\n{sys_prompt_text[:500]}...")
                return genai_types.Content(parts=[genai_types.Part(text=sys_prompt_text)], role="system")
            # --- システムプロンプトここまで ---

            system_instruction_content = create_system_prompt(filtered_summaries) # ★ 要約情報を渡す
            history_list = await history_cog.get_global_history_for_prompt() # 直近履歴
            start_message_content = genai_types.Content(role="user", parts=[genai_types.Part(text=f"（{call_name}さんへのDM開始指示: {dm_prompt_text_base}）")])

            contents_for_api = []
            # contents_for_api.append(genai_types.Content(role="system", parts=[genai_types.Part(text="--- [最近の会話] ---")])) # ヘッダは任意
            contents_for_api.extend(history_list)
            contents_for_api.append(start_message_content)

            # --- Gemini API 呼び出し ---
            model_name = config_manager.get_model_name() # メインモデルを使用
            generation_config_dict = config_manager.get_generation_config_dict() # メイン設定を使用
            safety_settings_list = config_manager.get_safety_settings_list() # メイン設定を使用
            safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]
            tools_for_api = None

            final_generation_config = genai_types.GenerateContentConfig(
                 temperature=generation_config_dict.get('temperature', 0.9),
                 top_p=generation_config_dict.get('top_p', 1.0),
                 top_k=generation_config_dict.get('top_k', 1),
                 candidate_count=generation_config_dict.get('candidate_count', 1),
                 max_output_tokens=generation_config_dict.get('max_output_tokens', 512), # DM用に短く
                 safety_settings=safety_settings_for_api,
                 tools=tools_for_api,
                 system_instruction=system_instruction_content # ★ 更新されたシステムプロンプト
            )

            logger.info(f"Sending random DM request to Gemini. Model: {model_name}, History length: {len(history_list)}, Summaries: {len(filtered_summaries)}")
            if not contents_for_api: logger.error("Cannot send request to Gemini for random DM, contents_for_api is empty."); return

            # 【GenAI呼び出し箇所 1/1 (ランダムDM)】
            response = self.genai_client.models.generate_content(
                model=model_name, contents=contents_for_api, config=final_generation_config
            )
            logger.debug(f"Gemini Response for random DM ({user_id}): FinishReason={response.candidates[0].finish_reason if response.candidates else 'N/A'}")

            # --- 応答処理、送信、履歴保存 (変更なし) ---
            response_text = ""; response_parts = []
            if response and response.candidates:
                 candidate = response.candidates[0]
                 if candidate.content and candidate.content.parts: response_parts = candidate.content.parts
                 else:
                      finish_reason = candidate.finish_reason; logger.warning(f"No parts in candidate content for random DM to {user_id}. FinishReason: {finish_reason}")
                      block_reason_str="不明"; safety_reason="不明"
                      try:
                           if response.prompt_feedback: block_reason_str = str(response.prompt_feedback.block_reason or "理由なし")
                           if finish_reason == genai_types.FinishReason.SAFETY and candidate.safety_ratings: safety_categories = [str(r.category) for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]; safety_reason = f"安全性 ({', '.join(safety_categories)})" if safety_categories else "安全性"
                      except Exception: pass
                      if finish_reason == genai_types.FinishReason.SAFETY: response_text = f"(DMの内容が{safety_reason}によりブロックされました)"
                      elif finish_reason == genai_types.FinishReason.RECITATION: response_text = "(DMの内容が引用超過でブロックされました)"
                      elif block_reason_str != "不明" and block_reason_str != "理由なし": response_text = f"(DMのプロンプトが原因でブロックされました: {block_reason_str})"
                      else: response_text = f"(DM応答生成失敗: {finish_reason})"
                 if not response_text: response_text = "".join(part.text for part in response_parts if hasattr(part, 'text') and part.text)
            else: logger.warning(f"No valid response or candidates for random DM to user {user_id}."); return
            response_text = response_text.strip()
            if not response_text: logger.warning(f"Empty response text after processing parts for random DM to user {user_id}."); return
            text_after_citation = helpers.remove_citation_marks(response_text)
            text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
            max_len = config_manager.get_max_response_length()
            original_len_after_clean = len(text_after_prefixes)
            if original_len_after_clean > max_len: final_response_text = text_after_prefixes[:max_len - 3] + "..."
            elif original_len_after_clean == 0 and len(text_after_citation) > 0: final_response_text = None
            else: final_response_text = text_after_prefixes
            logger.debug(f"Final random DM text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")
            if final_response_text:
                try:
                    await dm_channel.send(final_response_text)
                    logger.info(f"Sent random DM to {user.display_name} (ID: {user_id})")
                    if not final_response_text.startswith("("):
                         def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                             data = {}
                             if hasattr(part, 'text') and part.text and part.text.strip():
                                 text_content = part.text.strip()
                                 if is_model_response: cleaned_text = helpers.remove_all_prefixes(text_content); data['text'] = cleaned_text if cleaned_text else ""
                                 else: data['text'] = text_content
                             return data if data.get('text') else {} # 空テキストは除外
                         bot_response_parts_dict_cleaned = [p_dict for part in response_parts if (p_dict := part_to_dict(part, is_model_response=True))]
                         if bot_response_parts_dict_cleaned:
                              await history_cog.add_history_entry_async( current_interlocutor_id=user_id, channel_id=None, role="model", parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id )
                              logger.info(f"Successfully added cleaned random DM response to global history for user {user_id}")
                         else: logger.warning(f"No valid parts to add to global history for random DM response to user {user_id}")
                except discord.Forbidden: logger.warning(f"Cannot send random DM to {user_id}. DMs may be closed or Bot lacks permission.")
                except discord.HTTPException as http_e: logger.error(f"Failed to send DM to {user_id}", exc_info=http_e)
                except Exception as send_e: logger.error(f"Unexpected error sending DM or adding history for {user_id}", exc_info=send_e)
            elif response_text.startswith("("):
                 try: await dm_channel.send(response_text); logger.info(f"Sent info/error message in random DM to {user_id}: {response_text}")
                 except Exception as send_e: logger.error(f"Error sending info/error message in random DM to {user_id}", exc_info=send_e)
            else: logger.info(f"Skipped sending empty or prefix-only random DM to user {user_id}.")

        except genai_errors.APIError as e: logger.error(f"Gemini API Error during random DM preparation for {user_id}: Code={e.code if hasattr(e, 'code') else 'N/A'}, Message={e.message}", exc_info=False)
        except Exception as e: logger.error(f"Error preparing or sending random DM to user {user_id}", exc_info=e)


    @dm_sender_loop.before_loop
    async def before_dm_sender_loop(self):
        await self.bot.wait_until_ready()
        logger.info("Random DM sender loop is ready.")

# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(RandomDMCog(bot))
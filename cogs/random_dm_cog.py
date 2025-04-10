# cogs/random_dm_cog.py (文字数制限の適用箇所変更)
import discord
from discord.ext import commands, tasks
import logging
import datetime
from datetime import timezone
import asyncio
import random
from typing import Optional, List, Dict, Any

# config_manager や genai 関連をインポート
from utils import config_manager
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
from utils import helpers # 引用符削除など
from cogs.history_cog import HistoryCog # HistoryCog をインポート

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
        if self.genai_client: return
        chat_cog = self.bot.get_cog("ChatCog")
        if chat_cog and chat_cog.genai_client: self.genai_client = chat_cog.genai_client; logger.info("Using GenAI client from ChatCog for Random DM.")
        else: logger.warning("Could not get GenAI client from ChatCog for RandomDMCog. DM sending may fail.")

    async def reset_user_timer(self, user_id: int):
        user_id_str = str(user_id)
        logger.debug(f"Resetting Random DM timer in memory for user {user_id_str} due to interaction.")
        async with self.user_data_lock:
             user_settings = config_manager.user_data.get(user_id_str, {}).get("random_dm")
             if user_settings and user_settings.get("enabled"):
                 now = datetime.datetime.now().astimezone()
                 user_settings["last_interaction"] = now
                 user_settings["next_send_time"] = None
                 logger.info(f"Random DM timer reset in memory for user {user_id}.")
                 # ファイル保存は dm_sender_loop で行う

    @tasks.loop(seconds=10.0)
    async def dm_sender_loop(self):
        await self.initialize_genai_client_if_needed()
        if not self.genai_client: logger.warning("GenAI client not available in RandomDMCog, skipping loop iteration."); return

        now = datetime.datetime.now().astimezone()
        logger.debug(f"Running dm_sender_loop at {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")

        users_to_dm = []
        users_to_update_config: Dict[int, Dict[str, Any]] = {}

        async with self.user_data_lock:
            all_user_data_copy = config_manager.get_all_user_data()

        for user_id_str, u_data in all_user_data_copy.items():
            try:
                user_id = int(user_id_str)
                dm_config_current = u_data.get("random_dm", config_manager.get_default_random_dm_config())
                if not dm_config_current.get("enabled"): continue

                stop_start = dm_config_current.get("stop_start_hour"); stop_end = dm_config_current.get("stop_end_hour")
                is_stopping_time = False
                if stop_start is not None and stop_end is not None:
                    current_hour_local = now.hour
                    if stop_start > stop_end: is_stopping_time = (current_hour_local >= stop_start or current_hour_local < stop_end)
                    else: is_stopping_time = (stop_start <= current_hour_local < stop_end)
                if is_stopping_time: logger.debug(f"Skip DM user {user_id} (stop time: {stop_start}-{stop_end} local)"); continue

                last_interact = dm_config_current.get("last_interaction") or datetime.datetime.min.replace(tzinfo=now.tzinfo)
                next_send_time = dm_config_current.get("next_send_time")

                default_tz = now.tzinfo
                if isinstance(last_interact, datetime.datetime):
                    if last_interact.tzinfo is None: last_interact = last_interact.replace(tzinfo=default_tz)
                    else: last_interact = last_interact.astimezone(default_tz)
                else: last_interact = datetime.datetime.min.replace(tzinfo=default_tz)
                if isinstance(next_send_time, datetime.datetime):
                    if next_send_time.tzinfo is None: next_send_time = next_send_time.replace(tzinfo=default_tz)
                    else: next_send_time = next_send_time.astimezone(default_tz)

                logger.debug(f"User {user_id}: (After TZ adjust) last_interact={last_interact}, next_send_time={next_send_time}")

                if next_send_time is None:
                    min_interval = dm_config_current.get("min_interval", 60); max_interval = dm_config_current.get("max_interval", 180)
                    interval_sec = random.uniform(min_interval, max_interval)
                    next_send_time = last_interact + datetime.timedelta(seconds=interval_sec)
                    users_to_update_config.setdefault(user_id, {})["next_send_time"] = next_send_time
                    logger.info(f"Calculated next DM time for user {user_id}: {next_send_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')} (Interval: {interval_sec:.0f}s)")

                if next_send_time and isinstance(next_send_time, datetime.datetime) and now >= next_send_time:
                     logger.info(f"Time condition met for user {user_id}: now={now}, next_send_time={next_send_time}")
                     users_to_dm.append(user_id)
                     users_to_update_config.setdefault(user_id, {})["next_send_time"] = None
                     users_to_update_config.setdefault(user_id, {})["last_interaction"] = now
                elif next_send_time:
                     time_diff = next_send_time - now
                     logger.debug(f"Time condition NOT met for user {user_id}. Send in {time_diff.total_seconds():.1f} seconds.")
            except Exception as e: logger.error(f"Error processing user {user_id_str} in dm_sender_loop", exc_info=e)

        if users_to_update_config:
            logger.debug(f"Updating random DM configs in memory and file for {len(users_to_update_config)} users.")
            update_tasks = [config_manager.update_random_dm_config_async(uid, updates) for uid, updates in users_to_update_config.items()]
            if update_tasks:
                 try: await asyncio.gather(*update_tasks); logger.debug("Finished updating random DM configs.")
                 except Exception as e: logger.error("Error during bulk update of random DM configs", exc_info=e)

        if users_to_dm:
            history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
            if not history_cog: logger.error("HistoryCog not found!"); return
            send_tasks = [asyncio.create_task(self.send_random_dm(user_id, history_cog)) for user_id in users_to_dm]
            if send_tasks: await asyncio.gather(*send_tasks) # 並列実行を待つ (間隔は不要かも)


    async def send_random_dm(self, user_id: int, history_cog: HistoryCog):
        user = self.bot.get_user(user_id)
        if user is None:
            try: user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException) as e: logger.warning(f"Could not find/fetch user {user_id} for random DM: {e}"); return

        logger.info(f"Attempting to send random DM to {user.display_name} (ID: {user_id})")

        try:
            dm_channel = user.dm_channel or await user.create_dm()

            dm_prompt_text = config_manager.get_random_dm_prompt()
            user_nickname = config_manager.get_nickname(user_id)
            user_representation = user.display_name
            call_name = user_nickname if user_nickname else user_representation

            history_list = await history_cog.get_global_history_for_prompt()

            persona_prompt = config_manager.get_persona_prompt()
            sys_prompt_text = persona_prompt
            sys_prompt_text += f"\n\n--- 重要: 現在の会話状況 ---"
            sys_prompt_text += f"\nあなたはこれから、以下のユーザーに**あなたから**ダイレクトメッセージ（DM）を送ります。これは会話の開始となります。"
            sys_prompt_text += f"\n- ユーザー名: {user_representation}"
            sys_prompt_text += f"\n- ユーザーID: {user_id}"
            sys_prompt_text += f"\n- 呼びかける名前: **{call_name}**"
            sys_prompt_text += f"\n\n--- 指示 ---"
            sys_prompt_text += f"\n1. **最重要:** あなたからの最初のDMです。応答する際は、**必ず指定された「呼びかける名前」({call_name}) を使用してください。**"
            sys_prompt_text += f"\n2. 過去の履歴には、あなたと他のユーザーとの会話や、現在の相手と他のユーザーとの会話、サーバーチャンネルでの会話が含まれている可能性があります。これらの履歴は参考程度に留め、**今回のDMの内容は、現在の相手「{call_name}」さんとの新しい会話として自然なものにしてください。** 過去の他の会話に引きずられないように注意してください。"
            sys_prompt_text += f"\n3. **今回のあなたの最初の発言指示:** {dm_prompt_text}"
            sys_prompt_text += f"\n4. 上記の指示に従い、フレンドリーで自然な最初のメッセージを作成してください。"
            sys_prompt_text += f"\n5. あなたの応答には、`[{self.bot.user.display_name}]:` のような発言者プレフィックスを絶対につけないでください。"
            sys_prompt_text += "\n------------------------\n"

            system_instruction_content = genai_types.Content(parts=[genai_types.Part(text=sys_prompt_text)], role="system")

            contents_for_api = []
            contents_for_api.extend(history_list)
            start_message_text = dm_prompt_text
            if start_message_text:
                 start_content = genai_types.Content(role="user", parts=[genai_types.Part(text=start_message_text)])
                 contents_for_api.append(start_content)
                 logger.debug(f"Added start message content for random DM: {start_message_text}")
            else: logger.error("Random DM prompt is empty!"); return

            model_name = config_manager.get_model_name()
            generation_config_dict = config_manager.get_generation_config_dict()
            safety_settings_list = config_manager.get_safety_settings_list()
            safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]
            tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]

            final_generation_config = genai_types.GenerateContentConfig(
                 **generation_config_dict,
                 safety_settings=safety_settings_for_api,
                 tools=tools_for_api,
                 system_instruction=system_instruction_content
            )

            logger.debug(f"Sending random DM request to Gemini. Model: {model_name}, Global history length: {len(history_list)}")
            if not contents_for_api: logger.error("Cannot send request to Gemini, contents_for_api is empty."); return

            response = self.genai_client.models.generate_content(
                model=model_name,
                contents=contents_for_api,
                config=final_generation_config
            )

            # --- 応答処理 & 送信 ---
            logger.debug(f"Gemini Response for random DM ({user_id}): {response}")

            response_text = ""
            response_parts = []
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                response_parts = response.candidates[0].content.parts
                logger.debug(f"Response parts for random DM ({user_id}): {response_parts}")
                for part in response_parts:
                    logger.debug(f"Processing part for random DM ({user_id}): {part}")
                    if hasattr(part, 'text') and part.text:
                        if part.text.strip(): response_text += part.text; logger.debug(f"Added text from part: '{part.text[:50]}...'")
                        else: logger.debug(f"Part has empty text.")
                    elif hasattr(part, 'function_call') and part.function_call is not None:
                        func_name = getattr(part.function_call, 'name', 'Unknown Function')
                        logger.warning(f"Detected function_call part (not handled): {func_name}")
                    else: logger.debug(f"Skipping non-text/non-fc part: {type(part)}")
            else: # 応答がないか空の場合
                block_reason = "N/A"; finish_reason = "N/A"
                try:
                    if hasattr(response, 'prompt_feedback') and response.prompt_feedback: block_reason = response.prompt_feedback.block_reason or "Not Blocked"
                    if response.candidates: finish_reason = response.candidates[0].finish_reason or "Unknown"
                    logger.warning(f"No valid response content for random DM to user {user_id}. Block: {block_reason}, Finish: {finish_reason}.")
                except Exception as e_resp: logger.error(f"Error accessing feedback/finish reason for random DM {user_id}", exc_info=e_resp)
                return # 送信しない

            response_text = response_text.strip()
            if not response_text:
                 logger.warning(f"Empty response text after processing parts for random DM to user {user_id}.")
                 return # 送信しない

            # --- 最終整形と送信 ---
            final_response_text = helpers.remove_citation_marks(response_text)
            bot_name = self.bot.user.display_name
            bot_prefix = f"[{bot_name}]:"
            cleaned_response = final_response_text.strip()
            prefix_removed_count = 0
            while cleaned_response.startswith(bot_prefix):
                cleaned_response = cleaned_response[len(bot_prefix):].strip()
                prefix_removed_count += 1
            if prefix_removed_count > 0: logger.info(f"Removed {prefix_removed_count} bot prefix(es) from random DM response.")
            final_response_text = cleaned_response

            # ★★★ 文字数制限はプレフィックス削除後に適用 ★★★
            max_len = config_manager.get_max_response_length()
            if len(final_response_text) > max_len:
                logger.info(f"Random DM response length ({len(final_response_text)}) exceeded max length ({max_len}). Truncating.")
                final_response_text = final_response_text[:max_len] + "..."
            elif len(final_response_text) == 0 and prefix_removed_count > 0:
                 logger.warning("Random DM response became empty after removing bot prefix(es). Not sending.")
                 final_response_text = None # 送信しないフラグとしてNoneを代入


            if final_response_text: # 送信するテキストがある場合
                try:
                    await dm_channel.send(final_response_text)
                    logger.info(f"Sent random DM to {user.display_name} (ID: {user_id})")

                    # --- 履歴保存 (プレフィックス除去済みテキストを使用) ---
                    logger.debug(f"Attempting to add random DM response to global history for user {user_id}")
                    def part_to_dict(part: genai_types.Part) -> dict:
                        data = {};
                        # ★★★ 履歴保存時もプレフィックスを除去したテキストを使う ★★★
                        if hasattr(part, 'text') and part.text and part.text.strip():
                            cleaned_part_text = part.text.strip()
                            while cleaned_part_text.startswith(bot_prefix):
                                cleaned_part_text = cleaned_part_text[len(bot_prefix):].strip()
                            if cleaned_part_text: # 空でなければ保存
                                data['text'] = cleaned_part_text
                        elif hasattr(part, 'function_call') and part.function_call: data['function_call'] = {'name': part.function_call.name, 'args': dict(part.function_call.args),}
                        elif hasattr(part, 'function_response') and part.function_response: data['function_response'] = {'name': part.function_response.name, 'response': dict(part.function_response.response),}
                        return data

                    bot_response_parts_dict_cleaned = [part_to_dict(part) for part in response_parts if part_to_dict(part)]

                    if bot_response_parts_dict_cleaned:
                         logger.debug(f"Calling add_history_entry_async (Global) with cleaned parts for random DM")
                         await history_cog.add_history_entry_async(
                             current_interlocutor_id=user_id, channel_id=None, role="model",
                             parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id
                         )
                         logger.info(f"Successfully added cleaned random DM response to global history for user {user_id}")
                    else: logger.warning(f"No valid parts to add to global history for random DM response to user {user_id}")

                except discord.Forbidden: logger.warning(f"Cannot send random DM to {user_id}. DMs may be closed or Bot lacks permission.")
                except discord.HTTPException as http_e: logger.error(f"Failed to send DM to {user_id}", exc_info=http_e)
                except Exception as send_e: logger.error(f"Unexpected error sending DM or adding history for {user_id}", exc_info=send_e)
            # else: # final_response_text が None (空になった) の場合はログのみ
            #    logger.info(f"Skipped sending empty random DM to user {user_id}.")

        except genai_errors.APIError as e: logger.error(f"Gemini API Error during random DM for {user_id}: Code={e.code}, Message={e.message}", exc_info=False)
        except Exception as e: logger.error(f"Error preparing or sending random DM to user {user_id}", exc_info=e)


    @dm_sender_loop.before_loop
    async def before_dm_sender_loop(self):
        await self.bot.wait_until_ready()
        logger.info("Random DM sender loop is ready.")

# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(RandomDMCog(bot))
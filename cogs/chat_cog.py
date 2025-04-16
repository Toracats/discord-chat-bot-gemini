# cogs/chat_cog.py (part_to_dict修正、省略なし)

import discord
from discord.ext import commands
import logging
import datetime
import os
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
import asyncio
from typing import Optional, List, Dict, Any
import re

# 他のCogやUtilsから必要なものをインポート
from utils import config_manager
from utils import helpers
from cogs.history_cog import HistoryCog
from cogs.processing_cog import ProcessingCog
from cogs.weather_mood_cog import WeatherMoodCog

logger = logging.getLogger(__name__)

class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client = None
        self.initialize_genai_client()
        logger.info("ChatCog loaded.")

    # --- 呼び名取得ヘルパー関数 ---
    def get_call_name(self, target_user_id: Optional[int]) -> str:
        if target_user_id is None: return "(不明な相手)"
        if target_user_id == self.bot.user.id: return self.bot.user.display_name
        identifier = config_manager.get_nickname(target_user_id)
        if identifier: return identifier
        user = self.bot.get_user(target_user_id)
        if user: return user.display_name
        return f"User {target_user_id}"

    def initialize_genai_client(self):
        try:
            api_key = os.getenv("GOOGLE_AI_KEY")
            if not api_key: logger.error("GOOGLE_AI_KEY not found."); self.genai_client = None; return
            self.genai_client = genai.Client(api_key=api_key); logger.info("Gemini client initialized.")
        except Exception as e: logger.error("Failed to initialize Gemini client", exc_info=e); self.genai_client = None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.author.bot: return
        if message.mention_everyone: return

        should_respond = False; is_dm = isinstance(message.channel, discord.DMChannel)
        if is_dm: should_respond = True
        elif message.guild:
            if self.bot.user.mentioned_in(message): should_respond = True
            else: server_id_str = str(message.guild.id); allowed_channels = config_manager.get_allowed_channels(server_id_str);
            if message.channel.id in allowed_channels: should_respond = True
        if not should_respond: return

        logger.info(f"Received message from {message.author.name} (ID: {message.author.id}) in {'DM' if is_dm else f'channel #{message.channel.name}'}")

        random_dm_cog = self.bot.get_cog("RandomDMCog")
        if random_dm_cog: 
            try: asyncio.create_task(random_dm_cog.reset_user_timer(message.author.id), name=f"reset_timer_{message.author.id}"); logger.debug(f"Created task reset timer user {message.author.id}") 
            except Exception as e: logger.error(f"Failed create task reset timer", exc_info=e)
        else: logger.warning("RandomDMCog not found for timer reset.")

        if not self.genai_client: logger.error("Gemini client not initialized."); return

        async with message.channel.typing():
            # ★★★ try ブロック開始 ★★★
            try:
                cleaned_text = helpers.clean_discord_message(message.content)
                user_id = message.author.id; channel_id = message.channel.id if not is_dm else None
                call_name = self.get_call_name(user_id)

                history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog"); processing_cog: Optional[ProcessingCog] = self.bot.get_cog("ProcessingCog"); weather_mood_cog: Optional[WeatherMoodCog] = self.bot.get_cog("WeatherMoodCog")
                if not history_cog or not processing_cog: logger.error("Required Cog(s) not found!"); await message.reply("エラー: 内部コンポーネントが見つかりません。", mention_author=False); return

                # --- 履歴と現在のメッセージ内容を準備 ---
                history_list = await history_cog.get_global_history_for_prompt()
                current_parts: List[genai_types.Part] = []
                if cleaned_text: current_parts.append(genai_types.Part(text=cleaned_text))
                logger.debug(f"Processing attachments...")
                attachment_parts = await processing_cog.process_attachments(message.attachments)
                if attachment_parts: current_parts.extend(attachment_parts); logger.debug(f"Added {len(attachment_parts)} parts from attachments.")
                logger.debug(f"Processing URL in message (if any)...")
                if not message.attachments:
                    logger.debug("No attachments found, proceeding with URL processing.")
                    url_content_parts = await processing_cog.process_url_in_message(cleaned_text)
                    if url_content_parts: logger.debug(f"Processed URL, found {len(url_content_parts)} parts. Adding to current_parts."); current_parts.extend(url_content_parts)
                    else: logger.debug("No content parts found from URL processing.")
                else: logger.debug("Attachments found, skipping URL processing.")
                logger.debug(f"Total current parts: {len(current_parts)}")
                if not current_parts: logger.warning("No processable content found."); return
                current_content = genai_types.Content(role="user", parts=current_parts)

                # --- 要約履歴の読み込みと調整 ---
                summaries_all = await config_manager.load_summaries(); max_summary_tokens = config_manager.get_summary_max_prompt_tokens()
                filtered_summaries = []; current_summary_tokens = 0
                for summary in reversed(summaries_all):
                    summary_text = summary.get("summary_text", ""); estimated_tokens = len(summary_text) * 1.5
                    if current_summary_tokens + estimated_tokens <= max_summary_tokens: filtered_summaries.append(summary); current_summary_tokens += estimated_tokens
                    else: logger.debug(f"Summary token limit ({max_summary_tokens}) reached."); break
                filtered_summaries.reverse()
                logger.info(f"Loaded {len(summaries_all)} summaries, using {len(filtered_summaries)} summaries ({current_summary_tokens:.0f} estimated tokens) for prompt.")

                # --- Gemini 設定 ---
                model_name = config_manager.get_model_name(); generation_config_dict = config_manager.get_generation_config_dict(); safety_settings_list = config_manager.get_safety_settings_list()

                # --- システムインストラクション生成関数 ---
                def create_system_prompt(summarized_history: List[Dict[str, Any]], add_recitation_warning=False):
                    persona_prompt_base = config_manager.get_persona_prompt(); sys_prompt = persona_prompt_base
                    # 既知のユーザーリスト
                    sys_prompt += "\n\n--- 既知のユーザーとその固有名 ---"; all_identifiers = config_manager.get_all_user_identifiers()
                    if all_identifiers:
                        for uid, identifier in all_identifiers.items(): sys_prompt += f"\n- {identifier} (ID: {uid})"
                    else: sys_prompt += "\n(Botが認識している固有名ユーザーなし)"
                    sys_prompt += f"\n- {self.bot.user.display_name} (ID: {self.bot.user.id}) (Bot自身)"; sys_prompt += "\n------------------------------------"
                    # 現在の対話相手情報
                    sys_prompt += f"\n\n--- ★★★ 現在の最重要情報 ★★★ ---"; sys_prompt += f"\nあなたは今、以下の Discord ユーザーと **直接** 会話しています。このユーザーに集中してください。"; sys_prompt += f"\n- ユーザー名(表示名): {message.author.display_name}"; sys_prompt += f"\n- ユーザーID: {user_id}"; sys_prompt += f"\n- ★★ あなたが呼びかけるべき名前: 「{call_name}」 ★★"; sys_prompt += f"\n   (注: これはBotが認識している固有名、またはユーザー表示名です。状況に応じて後述のあだ名も使用してください。)"
                    if channel_id: channel_name = message.channel.name if isinstance(message.channel, discord.TextChannel) else "不明"; sys_prompt += f"\n- 会話の場所: サーバーチャンネル「{channel_name}」(ID: {channel_id})"
                    else: sys_prompt += f"\n- 会話の場所: ダイレクトメッセージ (DM)"
                    sys_prompt += "\n---------------------------------"
                    # あなたの現在の状態
                    current_mood = "普通";
                    if weather_mood_cog: current_mood = weather_mood_cog.get_current_mood(); last_loc = weather_mood_cog.current_weather_location; last_desc = weather_mood_cog.current_weather_description;
                    if weather_mood_cog and last_loc and last_desc: sys_prompt += f"\n\n--- あなたの現在の状態 ---\nあなたは「{current_mood}」な気分です。これは {last_loc} の天気 ({last_desc}) に基づいています。応答には、この気分を自然に反映させてください。"
                    else: sys_prompt += f"\n\n--- あなたの現在の状態 ---\nあなたは「{current_mood}」な気分です。応答には、この気分を自然に反映させてください。"
                    # 過去の会話の要約 (呼び名置換)
                    if summarized_history:
                        sys_prompt += "\n\n--- 過去の会話の要約 (古い順) ---"
                        user_id_pattern = re.compile(r'(?:User |ID:)(\d+)')
                        for summary in summarized_history:
                            ts_str = "(時刻不明)"; added_ts = summary.get("added_timestamp");
                            if isinstance(added_ts, datetime.datetime): ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                            speaker_name = summary.get("speaker_call_name_at_summary", "(不明)")
                            summary_text = summary.get("summary_text", "(要約なし)")
                            def replace_user_id_with_name(match): 
                                try: matched_id = int(match.group(1)); latest_call_name = self.get_call_name(matched_id); return latest_call_name if latest_call_name != match.group(0) else match.group(0) 
                                except (ValueError, IndexError): return match.group(0)
                            formatted_summary_text = user_id_pattern.sub(replace_user_id_with_name, summary_text)
                            sys_prompt += f"\n[{ts_str}] {speaker_name}: {formatted_summary_text}"
                        sys_prompt += "\n------------------------------"
                    else: sys_prompt += "\n\n(過去の会話の要約はありません)"
                    # 応答生成指示
                    sys_prompt += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"; sys_prompt += f"\n1. **現在の対話相手:** ..."; sys_prompt += f"\n2. **名前の認識:** ..."; sys_prompt += f"\n3. **呼び名の学習と使用:** ..."; sys_prompt += f"\n4. **情報活用:** ..."; sys_prompt += f"\n5. **不明な呼び名の確認:** ..."; sys_prompt += f"\n6. **注意:** ..."; sys_prompt += f"\n7. [最近の会話]履歴内の各発言には..."; sys_prompt += f"\n8. **厳禁:** ..."; sys_prompt += f"\n9. **応答を生成する前に...**"
                    if add_recitation_warning: sys_prompt += f"\n10. **重要:** ..."
                    else: sys_prompt += f"\n10. ウェブ検索結果などを参照する場合は..."
                    sys_prompt += "\n----------------------------------------\n"; logger.debug(f"Generated System Prompt (summaries: {len(summarized_history)}, recitation warning: {add_recitation_warning}):\n{sys_prompt[:500]}..."); return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system")
                # --- システムインストラクションここまで ---

                system_instruction_content = create_system_prompt(filtered_summaries)
                safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]; tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
                final_generation_config = genai_types.GenerateContentConfig( temperature=generation_config_dict.get('temperature', 0.9), top_p=generation_config_dict.get('top_p', 1.0), top_k=generation_config_dict.get('top_k', 1), candidate_count=generation_config_dict.get('candidate_count', 1), max_output_tokens=generation_config_dict.get('max_output_tokens', 1024), safety_settings=safety_settings_for_api, tools=tools_for_api, system_instruction=system_instruction_content )
                contents_for_api = []; contents_for_api.extend(history_list); contents_for_api.append(current_content)

                logger.info(f"Sending request to Gemini. Model: {model_name}, History: {len(history_list)}, Summaries: {len(filtered_summaries)}, Current parts: {len(current_parts)}")
                response = self.genai_client.models.generate_content( model=model_name, contents=contents_for_api, config=final_generation_config, )
                logger.debug(f"Received response Gemini. Finish reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}")

                # --- 応答処理 ---
                response_text = ""; is_recitation_error = False; finish_reason = None; response_candidates_parts = []
                if response and response.candidates: 
                    candidate = response.candidates[0]; finish_reason = candidate.finish_reason;
                    if candidate.content and candidate.content.parts: response_candidates_parts = candidate.content.parts
                    else: logger.warning(f"Response candidate no parts. Finish: {finish_reason}")
                else: logger.warning("Response no candidates.")

                # --- Recitation エラー時のリトライ ---
                if finish_reason == genai_types.FinishReason.RECITATION:
                    is_recitation_error = True; await asyncio.sleep(1); logger.warning(f"Recitation error user {user_id}. Retrying...")
                    system_instruction_retry = create_system_prompt(filtered_summaries, add_recitation_warning=True)
                    final_generation_config_retry = genai_types.GenerateContentConfig( temperature=generation_config_dict.get('temperature', 0.9), top_p=generation_config_dict.get('top_p', 1.0), top_k=generation_config_dict.get('top_k', 1), candidate_count=generation_config_dict.get('candidate_count', 1), max_output_tokens=generation_config_dict.get('max_output_tokens', 1024), safety_settings=safety_settings_for_api, tools=tools_for_api, system_instruction=system_instruction_retry )
                    logger.debug("Sending retry request Gemini Recitation error...")
                    response = self.genai_client.models.generate_content( model=model_name, contents=contents_for_api, config=final_generation_config_retry )
                    logger.debug(f"Retry response finish_reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}")
                    if response and response.candidates: 
                        candidate = response.candidates[0]; finish_reason = candidate.finish_reason;
                        if candidate.content and candidate.content.parts: response_candidates_parts = candidate.content.parts
                        else: response_candidates_parts = []
                    else: response_candidates_parts = []

                # --- 応答テキスト抽出 & 空の場合の処理 ---
                raw_response_text = "".join(part.text for part in response_candidates_parts if hasattr(part, 'text') and part.text)
                response_text = raw_response_text.strip()
                if not response_text:
                    logger.warning("Response text empty after extraction.")
                    block_reason_str = "不明"; finish_reason_str = "不明"; safety_reason = "不明な理由"
                    try:
                        if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback: block_reason_str = str(response.prompt_feedback.block_reason or "ブロック理由なし")
                        if finish_reason: finish_reason_str = str(finish_reason)
                        if finish_reason == genai_types.FinishReason.SAFETY and response and response.candidates and response.candidates[0].safety_ratings: safety_categories = [str(r.category) for r in response.candidates[0].safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]; safety_reason = f"安全性フィルター ({', '.join(safety_categories)})" if safety_categories else "安全性フィルター"
                    except Exception as e_fb: logger.warning(f"Error accessing response feedback: {e_fb}")
                    if is_recitation_error and finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、応答が引用超過のため停止しました。再試行しましたが、再度停止しました。)"
                    elif finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、応答が引用超過のため停止しました。)"
                    elif finish_reason == genai_types.FinishReason.SAFETY: response_text = f"({call_name}さん、応答が{safety_reason}によりブロックされました)"
                    elif block_reason_str != "不明" and block_reason_str != "ブロック理由なし": response_text = f"({call_name}さん、プロンプトが原因で応答がブロックされました。理由: {block_reason_str})"
                    elif finish_reason == genai_types.FinishReason.MAX_TOKENS: response_text = f"({call_name}さん、応答が長くなりすぎたため途中で停止しました。)"
                    else: response_text = f"({call_name}さん、応答を生成できませんでした。終了理由: {finish_reason_str}, ブロック理由: {block_reason_str})"
                    logger.warning(f"No response generated. Finish: {finish_reason_str}, Block: {block_reason_str}")

                # --- 履歴保存 ---
                if response_text and not response_text.startswith("("):
                    logger.debug("Preparing to save conversation history.")
                    # ★★★ part_to_dict ヘルパー関数 (修正済み) ★★★
                    def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                        data = {};
                        if hasattr(part, 'text') and part.text and part.text.strip():
                            text_content = part.text.strip() # テキスト内容を取得
                            if is_model_response:
                                cleaned_text = helpers.remove_all_prefixes(text_content)
                                if cleaned_text: data['text'] = cleaned_text # 除去後も空でなければ追加
                            else: data['text'] = text_content # ユーザー発言はそのまま追加
                        elif hasattr(part, 'inline_data') and part.inline_data:
                             try: data['inline_data'] = {'mime_type': part.inline_data.mime_type, 'data': None }
                             except Exception: logger.warning("Could not serialize inline_data for history.")
                        elif hasattr(part, 'function_call') and part.function_call:
                             try: data['function_call'] = {'name': part.function_call.name, 'args': dict(part.function_call.args),}
                             except Exception: logger.warning("Could not serialize function_call for history.")
                        elif hasattr(part, 'function_response') and part.function_response:
                             try: data['function_response'] = {'name': part.function_response.name, 'response': dict(part.function_response.response),}
                             except Exception: logger.warning("Could not serialize function_response for history.")
                        return data if data else {} # 空辞書でなければ返す

                    user_parts_dict = [p_dict for part in current_parts if (p_dict := part_to_dict(part, is_model_response=False))]
                    if user_parts_dict: await history_cog.add_history_entry_async(current_interlocutor_id=self.bot.user.id, channel_id=channel_id, role="user", parts_dict=user_parts_dict, entry_author_id=user_id)
                    bot_response_parts_dict_cleaned = [p_dict for part in response_candidates_parts if (p_dict := part_to_dict(part, is_model_response=True))]
                    if bot_response_parts_dict_cleaned: await history_cog.add_history_entry_async(current_interlocutor_id=user_id, channel_id=channel_id, role="model", parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id); logger.info(f"Added cleaned bot response to global history.")
                    else: logger.warning("No valid parts to add to history after cleaning bot response.")
                elif response_text: logger.debug("Skipping history saving for error/info message.")

                # --- 応答送信 ---
                if response_text:
                    text_after_citation = helpers.remove_citation_marks(response_text); text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
                    max_len = config_manager.get_max_response_length(); original_len_after_clean = len(text_after_prefixes)
                    if original_len_after_clean > max_len: final_response_text = text_after_prefixes[:max_len - 3] + "..."
                    elif original_len_after_clean == 0 and len(text_after_citation) > 0: final_response_text = None
                    else: final_response_text = text_after_prefixes
                    logger.debug(f"Final text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")
                    if final_response_text: await helpers.split_and_send_messages(message, final_response_text, 1900); logger.info(f"Successfully sent response to user {user_id}.")
                    elif response_text.startswith("("):
                         try:
                              if is_dm: await message.channel.send(response_text)
                              else: await message.reply(response_text, mention_author=False)
                              logger.info(f"Sent error/stop message user {user_id}: {response_text}")
                         except Exception as send_e: logger.error(f"Error sending error/stop message user {user_id}", exc_info=send_e)
                    else: logger.info(f"Skipped sending empty message user {user_id}.")

            # ★★★ ここから except ブロック (修正済み) ★★★
            except genai_errors.APIError as e:
                 logger.error(f"Gemini API Error user {user_id}. Code: {e.code if hasattr(e, 'code') else 'N/A'}, Status: {e.status if hasattr(e, 'status') else 'N/A'}", exc_info=False)
                 reply_msg = f"({call_name}さん、AIとの通信中にAPIエラーが発生しました。)"
                 if hasattr(e, 'code') and e.code == 429: reply_msg = f"({call_name}さん、APIの利用上限に達したようです。しばらく待ってから試してください。)"
                 elif hasattr(e, 'status') and e.status == 'UNAVAILABLE': reply_msg = f"({call_name}さん、AIが現在混み合っているようです。少し時間をおいてから再度お試しください。)"
                 elif hasattr(e, 'message') and "API key not valid" in str(e.message): reply_msg = f"({call_name}さん、エラー: APIキーが無効です。設定を確認してください。)"
                 finish_reason_in_error = getattr(e, 'finish_reason', None)
                 if finish_reason_in_error:
                     logger.warning(f"Content generation stopped via APIError for user {user_id}. Reason: {finish_reason_in_error}", exc_info=False)
                     if finish_reason_in_error == genai_types.FinishReason.SAFETY: reply_msg = f"({call_name}さん、応答が安全性フィルターによりブロックされました)"
                     elif finish_reason_in_error == genai_types.FinishReason.RECITATION: reply_msg = f"({call_name}さん、応答が引用超過のため停止しました。)"
                     else: reply_msg = f"({call_name}さん、応答の生成が予期せず停止しました。理由: {finish_reason_in_error})"
                 try:
                      if is_dm: await message.channel.send(reply_msg)
                      else: await message.reply(reply_msg, mention_author=False)
                 except discord.HTTPException: logger.error("Failed send API error message Discord.")
            except discord.errors.NotFound:
                logger.warning(f"Message or channel not found (maybe deleted?). Message ID: {message.id}, Channel ID: {message.channel.id}")
            except Exception as e:
                logger.error(f"Error message processing user {user_id}", exc_info=True)
                reply_msg = f"({call_name}さん、予期せぬエラーが発生しました: {type(e).__name__})"
                try:
                     if is_dm: await message.channel.send(reply_msg)
                     else: await message.reply(reply_msg, mention_author=False)
                except discord.HTTPException: logger.error("Failed send unexpected error message Discord.")

# Cogセットアップ関数 (変更なし)
async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot))
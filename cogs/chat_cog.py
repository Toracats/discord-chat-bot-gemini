# cogs/chat_cog.py (ログ追加・エラーハンドリング強化版)

import discord
from discord.ext import commands
import logging
import datetime
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
import asyncio
from typing import Optional, List, Dict, Any
import re

from utils import config_manager
from utils import helpers
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cogs.history_cog import HistoryCog
    from cogs.processing_cog import ProcessingCog
    from cogs.weather_mood_cog import WeatherMoodCog
    from cogs.random_dm_cog import RandomDMCog
else:
    HistoryCog = None
    ProcessingCog = None
    WeatherMoodCog = None
    RandomDMCog = None

logger = logging.getLogger(__name__)
# ★ デバッグログを有効にする ★
logger.setLevel(logging.DEBUG)

class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client: Optional[genai.Client] = None

    def get_call_name(self, target_user_id: Optional[int]) -> str:
        # (変更なし)
        if target_user_id is None: return "(不明な相手)"
        if target_user_id == self.bot.user.id: return self.bot.user.display_name
        with config_manager.user_data_lock: nickname = config_manager.app_config.get("user_data", {}).get(str(target_user_id), {}).get("nickname")
        if nickname: return nickname
        user = self.bot.get_user(target_user_id); return user.display_name if user else f"User {target_user_id}"

    def initialize_genai_client(self) -> bool:
        # (変更なし)
        api_key = config_manager.get_gemini_api_key()
        if not api_key: logger.error("Gemini API Key not found."); self.genai_client = None; return False
        try: self.genai_client = genai.Client(api_key=api_key); logger.info("Gemini client initialized."); return True
        except Exception as e: logger.error("Failed Gemini client init", exc_info=e); self.genai_client = None; return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.author.bot: return
        if message.mention_everyone: return

        should_respond = False; is_dm = isinstance(message.channel, discord.DMChannel)
        if is_dm: should_respond = True
        elif message.guild:
            if self.bot.user.mentioned_in(message): should_respond = True
            else: server_id_str = str(message.guild.id); allowed_channels = config_manager.get_allowed_channels(server_id_str); should_respond = message.channel.id in allowed_channels
        if not should_respond: return

        logger.info(f"Received message from {message.author.name} (ID: {message.author.id}) in {'DM' if is_dm else f'channel #{message.channel.name}'}")

        random_dm_cog: Optional[RandomDMCog] = self.bot.get_cog("RandomDMCog")
        if random_dm_cog and hasattr(random_dm_cog, 'reset_user_timer'):
            try: asyncio.create_task(random_dm_cog.reset_user_timer(message.author.id), name=f"reset_timer_{message.author.id}"); logger.debug(f"Task reset timer user {message.author.id} created.")
            except Exception as e: logger.error(f"Failed create task reset timer", exc_info=e)
        elif not random_dm_cog: logger.warning("RandomDMCog not found.")

        if not self.genai_client: logger.error("ChatCog: Gemini client not initialized."); await message.reply("エラー: AI機能が利用できません。", mention_author=False); return

        logger.debug("Starting typing indicator...")
        async with message.channel.typing():
            logger.debug("Typing indicator shown.")
            response = None
            try:
                cleaned_text = helpers.clean_discord_message(message.content)
                user_id = message.author.id; channel_id = message.channel.id if not is_dm else None; call_name = self.get_call_name(user_id)

                history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
                processing_cog: Optional[ProcessingCog] = self.bot.get_cog("ProcessingCog")
                weather_mood_cog: Optional[WeatherMoodCog] = self.bot.get_cog("WeatherMoodCog")
                if not history_cog: logger.error("HistoryCog not found!"); await message.reply("エラー: 履歴管理機能が見つかりません。", mention_author=False); return

                history_list = await history_cog.get_global_history_for_prompt()
                current_parts: List[genai_types.Part] = []
                if cleaned_text: current_parts.append(genai_types.Part(text=cleaned_text))
                if processing_cog:
                    attachment_parts = await processing_cog.process_attachments(message.attachments)
                    if attachment_parts: current_parts.extend(attachment_parts); logger.debug(f"Added {len(attachment_parts)} parts attachments.")
                    if not message.attachments:
                        url_content_parts = await processing_cog.process_url_in_message(cleaned_text)
                        if url_content_parts: current_parts.extend(url_content_parts); logger.debug(f"Added {len(url_content_parts)} parts URL.")
                else: logger.warning("ProcessingCog not found.")
                if not current_parts: logger.warning("No processable content."); return

                current_content = genai_types.Content(role="user", parts=current_parts)
                summaries_all = await config_manager.load_summaries() # ★ 非同期ロード呼び出し
                max_summary_tokens = config_manager.get_summary_max_prompt_tokens()
                filtered_summaries = []; current_summary_tokens = 0
                for summary in reversed(summaries_all):
                    summary_text = summary.get("summary_text", ""); estimated_tokens = len(summary_text) * 1.5
                    if current_summary_tokens + estimated_tokens <= max_summary_tokens: filtered_summaries.append(summary); current_summary_tokens += estimated_tokens
                    else: break
                filtered_summaries.reverse(); logger.info(f"Using {len(filtered_summaries)} summaries ({current_summary_tokens:.0f} tokens).")

                def create_system_prompt(summarized_history: List[Dict[str, Any]], add_recitation_warning=False) -> genai_types.Content:
                    # (変更なし - 完全なコード)
                    persona_prompt_base = config_manager.get_persona_prompt(); sys_prompt = persona_prompt_base; sys_prompt += "\n\n--- 既知のユーザーとその固有名 ---"; all_identifiers = config_manager.get_all_user_identifiers()
                    if all_identifiers:
                        for uid, identifier in all_identifiers.items(): sys_prompt += f"\n- {identifier} (ID: {uid})"
                    else: sys_prompt += "\n(Botが認識している固有名ユーザーなし)"
                    sys_prompt += f"\n- {self.bot.user.display_name} (ID: {self.bot.user.id}) (Bot自身)"; sys_prompt += "\n------------------------------------"; sys_prompt += f"\n\n--- ★★★ 現在の最重要情報 ★★★ ---"; sys_prompt += f"\nあなたは今、以下の Discord ユーザーと **直接** 会話しています。このユーザーに集中してください。"; sys_prompt += f"\n- ユーザー名(表示名): {message.author.display_name}"; sys_prompt += f"\n- ユーザーID: {user_id}"; sys_prompt += f"\n- ★★ あなたが呼びかけるべき名前: 「{call_name}」 ★★"; sys_prompt += f"\n   (注: これはBotが認識している固有名、またはユーザー表示名です。状況に応じて後述のあだ名も使用してください。)"
                    if channel_id: channel = self.bot.get_channel(channel_id); channel_name = getattr(channel, 'name', '不明なチャンネル'); sys_prompt += f"\n- 会話の場所: サーバーチャンネル「{channel_name}」(ID:{channel_id})"
                    else: sys_prompt += f"\n- 会話の場所: ダイレクトメッセージ (DM)"
                    sys_prompt += "\n---------------------------------"; current_mood = "普通"
                    if weather_mood_cog:
                        current_mood = weather_mood_cog.get_current_mood(); last_loc = weather_mood_cog.current_weather_location; last_desc = weather_mood_cog.current_weather_description
                        if last_loc and last_desc: sys_prompt += f"\n\n--- あなたの現在の状態 ---\n気分「{current_mood}」({last_loc} の天気: {last_desc})。この気分を応答に自然に反映させてください。"
                        else: sys_prompt += f"\n\n--- あなたの現在の状態 ---\n気分「{current_mood}」。この気分を応答に自然に反映させてください。"
                    else: sys_prompt += f"\n\n--- あなたの現在の状態 ---\n気分「{current_mood}」。この気分を応答に自然に反映させてください。"
                    if summarized_history:
                        sys_prompt += "\n\n--- 過去の会話の要約 (古い順) ---"; user_id_pattern = re.compile(r'(?:User |ID:)(\d+)')
                        for summary in summarized_history:
                            ts_str = "(時刻不明)"; added_ts = summary.get("added_timestamp");
                            if isinstance(added_ts, datetime.datetime): ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                            speaker_name = summary.get("speaker_call_name_at_summary", "(不明な発言者)"); summary_text = summary.get("summary_text", "(要約内容なし)")
                            def replace_id(match):
                                try: return self.get_call_name(int(match.group(1)))
                                except: return match.group(0)
                            formatted_summary_text = user_id_pattern.sub(replace_id, summary_text); sys_prompt += f"\n[{ts_str}] {speaker_name}: {formatted_summary_text}"
                        sys_prompt += "\n----------------------"
                    else: sys_prompt += "\n\n(過去の会話の要約はありません)"
                    sys_prompt += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"; sys_prompt += f"\n1. **現在の対話相手:** あなたが応答すべき相手は「{call_name}」(ID: {user_id})です。"; sys_prompt += f"\n2. **名前の認識:** 会話履歴や要約内の `[ユーザー名]:` や `User <ID>` は発言者を示します。"; sys_prompt += f"\n3. **呼び名の学習と使用:** 過去の会話で、特定のユーザーIDに対して「{call_name}」以外の「あだ名」が使われていたら記憶し、文脈に合わせて使用しても構いません。"; sys_prompt += f"\n4. **情報活用:** あなた自身の現在の気分、会話履歴、ユーザーからのメッセージ（添付ファイルやURLの内容も含む）、過去の会話の要約を考慮して、自然で人間らしい応答を生成してください。"; sys_prompt += f"\n5. **不明な呼び名の確認:** もし会話相手の呼び名が「User <ID>」形式のままになっている場合、応答の中で「ところで、あなたのことは何とお呼びすればよいですか？」のように自然に尋ねてください。"; sys_prompt += f"\n6. **注意:** 他のユーザーの名前やあだ名を間違って現在の対話相手（{call_name}）に呼びかけないように細心の注意を払ってください。"; sys_prompt += f"\n7. [最近の会話]履歴内の各発言には発言者が `[ユーザー名]:` または `[Bot名]:` 形式で付与されています。応答に含めてはいけません。"; sys_prompt += f"\n8. **厳禁:** あなた自身の応答には、**いかなる部分にも** `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。"; sys_prompt += f"\n9. **応答を生成する前に、**あなたが今誰と会話していて、相手を何と呼ぶべきか（「{call_name}」または学習したあだ名）を再確認してください。"
                    if add_recitation_warning: sys_prompt += f"\n10. **重要:** 前回の応答は引用が多すぎたためブロックされました。今回は、より多くの部分をあなた自身の言葉で要約・説明してください。"
                    else: sys_prompt += f"\n10. ウェブ検索結果などを参照する場合は、情報源（URLなど）を応答に含めるようにしてください。"
                    sys_prompt += "\n----------------------------------------\n"; logger.debug(f"System Prompt generated (summaries: {len(summarized_history)}, recitation: {add_recitation_warning}):\n{sys_prompt[:500]}...");
                    # return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system") # role=system は使わない
                    return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)]) # role指定なし

                model_name = config_manager.get_model_name(); generation_config_dict = config_manager.get_generation_config_dict(); safety_settings_list = config_manager.get_safety_settings_list()
                system_instruction_content = create_system_prompt(filtered_summaries); config_args = generation_config_dict.copy()
                if safety_settings_list: config_args['safety_settings'] = [genai_types.SafetySetting(**s) for s in safety_settings_list]
                config_args['system_instruction'] = system_instruction_content; tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]; config_args['tools'] = tools_for_api
                final_config = genai_types.GenerateContentConfig(**config_args) if config_args else None; contents_for_api = []; contents_for_api.extend(history_list); contents_for_api.append(current_content)
                logger.info(f"Sending request to Gemini. Model: {model_name}, History: {len(history_list)}, Summaries: {len(filtered_summaries)}, Current parts: {len(current_parts)}")
                logger.debug(f"Sending contents: {contents_for_api}"); logger.debug(f"Generation config: {final_config}")

                try:
                    response = self.genai_client.models.generate_content(model=model_name, contents=contents_for_api, config=final_config)
                    logger.debug(f"Received response object from Gemini: {response}")
                except Exception as api_call_e: logger.error("Exception during Gemini API call", exc_info=api_call_e); await message.reply(f"({call_name}さん、AI通信エラー発生。)", mention_author=False); return

                response_text = ""; is_recitation_error = False; finish_reason = None; response_candidates_parts = []; safety_reason = "不明"; safety_ratings_info = "詳細不明"; block_reason_str = "不明"

                if response and response.candidates:
                    candidate = response.candidates[0]; finish_reason = candidate.finish_reason; logger.debug(f"Response candidate finish reason: {finish_reason}")
                    if candidate.safety_ratings:
                        triggered = [f"{r.category.name}:{r.probability.name}" for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                        if triggered: safety_reason = f"安全フィルタ({','.join([r.category.name for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE])})"; safety_ratings_info = ", ".join(triggered); logger.warning(f"Safety block detected: {safety_ratings_info}")
                        else: logger.debug("Safety ratings present but no category above NEGLIGIBLE.")
                    if candidate.content and candidate.content.parts:
                        response_candidates_parts = candidate.content.parts; logger.debug(f"Found {len(response_candidates_parts)} parts in response.")
                    else:
                        logger.warning(f"Response candidate has no content or parts. Finish reason: {finish_reason}")
                        if finish_reason == genai_types.FinishReason.SAFETY: response_text = f"({call_name}さん、応答が{safety_reason}によりブロック。\n詳細: {safety_ratings_info})"
                        elif finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、引用超過エラーで停止。)"
                        elif finish_reason == genai_types.FinishReason.MAX_TOKENS: response_text = f"({call_name}さん、応答長すぎで停止。)"
                        elif response.prompt_feedback and response.prompt_feedback.block_reason: block_reason_str = str(response.prompt_feedback.block_reason); response_text = f"({call_name}さん、入力が原因でブロック。理由: {block_reason_str})"
                        else: response_text = f"({call_name}さん、応答内容取得失敗。理由: {finish_reason})"
                else:
                    logger.warning("Response object or candidates list is empty.")
                    if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback and response.prompt_feedback.block_reason: block_reason_str = str(response.prompt_feedback.block_reason or "不明"); response_text = f"({call_name}さん、入力原因ブロック。理由: {block_reason_str})"
                    else: response_text = f"({call_name}さん、AI応答取得失敗。)"

                if finish_reason == genai_types.FinishReason.RECITATION and not response_text:
                    is_recitation_error = True; await asyncio.sleep(1); logger.warning(f"Recitation error for user {user_id}. Retrying..."); system_instruction_retry = create_system_prompt(filtered_summaries, add_recitation_warning=True); retry_config_args = config_args.copy(); retry_config_args['system_instruction'] = system_instruction_retry; final_config_retry = genai_types.GenerateContentConfig(**retry_config_args)
                    try:
                        response = self.genai_client.models.generate_content(model=model_name, contents=contents_for_api, config=final_config_retry)
                        logger.debug(f"Retry response object: {response}"); logger.debug(f"Retry response finish reason: {response.candidates[0].finish_reason if response and response.candidates else 'N/A'}")
                        if response and response.candidates:
                            candidate = response.candidates[0]; finish_reason = candidate.finish_reason
                            if candidate.content and candidate.content.parts: response_candidates_parts = candidate.content.parts
                            else: response_candidates_parts = []
                            if candidate.safety_ratings:
                                triggered = [f"{r.category.name}:{r.probability.name}" for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                                if triggered: safety_reason = f"安全フィルタ({','.join([r.category.name for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE])}) (再)"; safety_ratings_info = ", ".join(triggered) + " (再)"; logger.warning(f"Safety block after retry: {safety_ratings_info}")
                        else: response_candidates_parts = []
                    except Exception as retry_e: logger.error("Exception during retry API call", exc_info=retry_e); response_text = f"({call_name}さん、引用エラー後再試行中にエラー。)"

                if not response_text:
                    raw_response_text = "".join(part.text for part in response_candidates_parts if hasattr(part, 'text') and part.text); response_text = raw_response_text.strip(); logger.debug(f"Extracted text after potential retry: '{response_text[:200]}...'")

                if not response_text:
                    logger.warning(f"Response text is STILL empty. Finish: {finish_reason}, Block: {block_reason_str}")
                    if is_recitation_error and finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、引用超過エラーで停止(再試行後)。)"
                    elif finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、引用超過エラーで停止。)"
                    elif finish_reason == genai_types.FinishReason.SAFETY: response_text = f"({call_name}さん、応答が{safety_reason}によりブロック。\n詳細: {safety_ratings_info})"
                    elif block_reason_str != "不明" and block_reason_str != "なし": response_text = f"({call_name}さん、入力原因ブロック。理由: {block_reason_str})"
                    elif finish_reason == genai_types.FinishReason.MAX_TOKENS: response_text = f"({call_name}さん、応答長すぎで停止。)"
                    else: response_text = f"({call_name}さん、応答生成失敗。終了理由: {finish_reason}, ブロック理由: {block_reason_str})"
                    logger.warning(f"Generated fallback message: {response_text}")

                # --- 履歴保存 ---
                if history_cog and response_text and not response_text.startswith("("):
                    logger.debug("[ChatCog] Attempting to save conversation history...") # ★ 保存開始ログ
                    try:
                        def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                            data = {}
                            if hasattr(part, 'text') and part.text and part.text.strip(): data['text'] = part.text.strip()
                            elif hasattr(part, 'inline_data') and part.inline_data: 
                                try: data['inline_data'] = {'mime_type': part.inline_data.mime_type, 'data': '<omitted>' }; 
                                except Exception: pass
                            elif hasattr(part, 'function_call') and part.function_call: 
                                try: data['function_call'] = {'name': part.function_call.name, 'args': dict(part.function_call.args),}; 
                                except Exception: pass
                            elif hasattr(part, 'function_response') and part.function_response: 
                                try: data['function_response'] = {'name': part.function_response.name, 'response': dict(part.function_response.response),}; 
                                except Exception: pass
                            return data if data else {}

                        user_parts_dict = [p_dict for part in current_parts if (p_dict := part_to_dict(part, False))]
                        if user_parts_dict:
                            logger.debug("[ChatCog] Adding user message to history...")
                            await history_cog.add_history_entry_async( current_interlocutor_id=self.bot.user.id, channel_id=channel_id, role="user", parts_dict=user_parts_dict, entry_author_id=user_id )
                            logger.debug(f"[ChatCog] Added user message ({len(user_parts_dict)} parts) to history.")
                        else: logger.debug("[ChatCog] No user parts to add.")

                        if response_candidates_parts:
                             bot_response_parts_dict = [p_dict for part in response_candidates_parts if (p_dict := part_to_dict(part, True))]
                             if bot_response_parts_dict:
                                 logger.debug("[ChatCog] Adding bot response to history...")
                                 await history_cog.add_history_entry_async( current_interlocutor_id=user_id, channel_id=channel_id, role="model", parts_dict=bot_response_parts_dict, entry_author_id=self.bot.user.id )
                                 logger.info(f"[ChatCog] Added bot response ({len(bot_response_parts_dict)} parts) to history.")
                             else: logger.warning("[ChatCog] No valid bot response parts to add to history.")
                        else: logger.debug("[ChatCog] No bot response parts candidates.")
                        logger.debug("[ChatCog] Finished history saving attempt.") # ★ 保存終了ログ

                    except Exception as hist_e:
                        logger.error("Error during history saving", exc_info=hist_e)
                        # ここでユーザーに通知するかどうかは検討
                        # await message.reply(f"({call_name}さん、履歴の保存中にエラーが発生しました。)", mention_author=False)

                elif response_text: logger.debug("Skipping history saving for error/info message.")
                else: logger.error("History saving skipped because response_text is empty/None.")

                # --- 応答送信 ---
                if response_text:
                    text_after_citation = helpers.remove_citation_marks(response_text)
                    text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
                    max_len = config_manager.get_max_response_length()
                    original_len_after_clean = len(text_after_prefixes)
                    final_response_text = None
                    if original_len_after_clean > max_len: final_response_text = text_after_prefixes[:max_len - 3] + "..."
                    elif original_len_after_clean > 0: final_response_text = text_after_prefixes
                    elif response_text.startswith("("): final_response_text = response_text

                    logger.debug(f"Final text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")
                    if final_response_text:
                        if final_response_text.startswith("("):
                            try: await message.reply(final_response_text, mention_author=False) if not is_dm else await message.channel.send(final_response_text); logger.info(f"Sent error/stop message user {user_id}: {final_response_text}")
                            except Exception as send_e: logger.error(f"Error sending info/error message user {user_id}", exc_info=send_e)
                        else:
                            logger.debug("[ChatCog] Calling split_and_send_messages...") # ★ 送信開始ログ
                            await helpers.split_and_send_messages(message, final_response_text, 1900)
                            logger.info(f"[ChatCog] Sent response to user {user_id}.") # ★ 送信完了ログ
                    else:
                        logger.error(f"Failed generate final_response_text. Original: '{response_text[:100]}...'");
                        try: await message.reply(f"({call_name}さん、応答準備中に問題発生。)", mention_author=False)
                        except Exception: pass
                else:
                    logger.error("!!! CRITICAL: response_text is None or empty before sending !!!")
                    try: await message.reply(f"({call_name}さん、重大な内部エラーで応答不可。)", mention_author=False)
                    except Exception: pass

            except genai_errors.APIError as e:
                 logger.error(f"Gemini API Error user {user_id}.", exc_info=True); logger.error(f"Failed API call response: {response}")
                 reply_msg = f"({call_name}さん、AI通信APIエラー。)"
                 if hasattr(e, 'message') and "API key not valid" in str(e.message): reply_msg = f"({call_name}さん、エラー: APIキー無効。)"; logger.critical("Invalid Gemini API Key!")
                 finish_reason_in_error = getattr(e, 'finish_reason', None);
                 if finish_reason_in_error: logger.warning(f"Stopped via APIError user {user_id}. Reason: {finish_reason_in_error}"); reply_msg = f"({call_name}さん、応答生成停止(APIエラー経由): {finish_reason_in_error})"
                 try: await message.reply(reply_msg, mention_author=False) if not is_dm else await message.channel.send(reply_msg)
                 except discord.HTTPException: logger.error("Failed send API error message.")
            except discord.errors.NotFound: logger.warning(f"Message/channel not found. MsgID: {message.id}")
            except Exception as e:
                logger.error(f"Unexpected error user {user_id}", exc_info=True); logger.error(f"Error occurred response: {response}")
                reply_msg = f"({call_name}さん、予期せぬエラー: {type(e).__name__})"
                try: await message.reply(reply_msg, mention_author=False) if not is_dm else await message.channel.send(reply_msg)
                except discord.HTTPException: logger.error("Failed send unexpected error message.")
            finally:
                logger.debug("Exiting typing indicator context.") # Typing解除直前のログ

        logger.debug("Typing indicator should be released now.") # Typing解除後のログ

# --- setup 関数 ---
async def setup(bot: commands.Bot):
    cog = ChatCog(bot)
    if cog.initialize_genai_client():
        await bot.add_cog(cog)
        logger.info("ChatCog setup complete.")
    else:
        error_msg = "ChatCog setup failed Gemini client init fail."; logger.error(error_msg)
        # raise commands.ExtensionFailed(name="cogs.chat_cog", original=RuntimeError(error_msg))
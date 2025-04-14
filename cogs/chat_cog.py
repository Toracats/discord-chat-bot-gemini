# cogs/chat_cog.py (ログ出力確認・復元、履歴保存時に応答プレフィックス除去)

import discord
from discord.ext import commands
import logging
import datetime
import os
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
import asyncio # 再試行の遅延用
from typing import Optional, List, Dict, Any # ★ List, Dict, Any を追加

# 他のCogやUtilsから必要なものをインポート
from utils import config_manager
from utils import helpers # ★ helpers をインポート
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

    def initialize_genai_client(self):
        """Geminiクライアントを初期化/再初期化する"""
        try:
            api_key = os.getenv("GOOGLE_AI_KEY")
            if not api_key:
                logger.error("GOOGLE_AI_KEY not found in environment variables.")
                self.genai_client = None
                return
            self.genai_client = genai.Client(api_key=api_key)
            logger.info("Gemini client initialized successfully.")
        except Exception as e:
            logger.error("Failed to initialize Gemini client", exc_info=e)
            self.genai_client = None

    # --- イベントリスナー ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.author.bot: return
        if message.mention_everyone: return

        should_respond = False
        is_dm = isinstance(message.channel, discord.DMChannel)
        if is_dm: should_respond = True
        elif message.guild:
            if self.bot.user.mentioned_in(message): should_respond = True
            else:
                server_id_str = str(message.guild.id)
                # ★ get_allowed_channels のキーは str で取得
                allowed_channels = config_manager.get_allowed_channels(server_id_str) # 修正: int->str
                if message.channel.id in allowed_channels: should_respond = True
        if not should_respond: return

        logger.info(f"Received message from {message.author.name} (ID: {message.author.id}) in {'DM' if is_dm else f'channel #{message.channel.name} (ID: {message.channel.id})'}")

        # --- reset_user_timer ---
        random_dm_cog = self.bot.get_cog("RandomDMCog")
        if random_dm_cog:
            try:
                asyncio.create_task(random_dm_cog.reset_user_timer(message.author.id), name=f"reset_timer_{message.author.id}")
                logger.debug(f"Created background task to reset random DM timer for user {message.author.id}")
            except Exception as e:
                logger.error(f"Failed to create task for resetting random DM timer", exc_info=e)
        else:
            logger.warning("RandomDMCog not found when trying to reset timer.")
        # ------------------------

        if not self.genai_client:
             logger.error("Gemini client is not initialized. Cannot respond.")
             # 必要に応じてユーザーにエラーを通知
             # await message.reply("エラー: AIサービスに接続できません。", mention_author=False)
             return

        async with message.channel.typing():
            try:
                cleaned_text = helpers.clean_discord_message(message.content)
                logger.debug(f"Cleaned message content: '{cleaned_text}'") # ★ログ追加
                user_id = message.author.id
                channel_id = message.channel.id if not is_dm else None
                user_nickname = config_manager.get_nickname(user_id)
                call_name = user_nickname if user_nickname else message.author.display_name

                history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
                processing_cog: Optional[ProcessingCog] = self.bot.get_cog("ProcessingCog")
                weather_mood_cog: Optional[WeatherMoodCog] = self.bot.get_cog("WeatherMoodCog")

                logger.debug(f"Checking required cogs: HistoryCog={bool(history_cog)}, ProcessingCog={bool(processing_cog)}, WeatherMoodCog={bool(weather_mood_cog)}")

                if not history_cog or not processing_cog:
                     logger.error("Required Cog(s) (History/Processing) not found!")
                     # 応答前にエラーメッセージを送信
                     try:
                         if is_dm: await message.channel.send("エラー: 内部コンポーネントが見つかりません。")
                         else: await message.reply("エラー: 内部コンポーネントが見つかりません。", mention_author=False)
                     except discord.HTTPException as send_err:
                         logger.error("Failed to send internal component error message", exc_info=send_err)
                     return

                # --- 履歴と現在のメッセージ内容を準備 ---
                history_list = await history_cog.get_global_history_for_prompt()
                logger.debug(f"Retrieved global history (length: {len(history_list)}) for prompt.")
                # logger.debug(f"Formatted history for prompt: {history_list}") # 必要なら詳細ログ

                current_parts = []
                if cleaned_text: current_parts.append(genai_types.Part(text=cleaned_text))
                logger.debug(f"Processing attachments...")
                attachment_parts = await processing_cog.process_attachments(message.attachments)
                current_parts.extend(attachment_parts)
                logger.debug(f"Processing URL in message (if any)...")
                if not message.attachments: # 添付ファイルがない場合のみURL処理
                    url_content_parts = await processing_cog.process_url_in_message(cleaned_text)
                    if url_content_parts: current_parts.extend(url_content_parts)

                logger.debug(f"Current message parts check: cleaned_text='{cleaned_text}', current_parts has {len(current_parts)} parts.")

                if not current_parts:
                    logger.warning("No processable content found in the message. Stopping response.")
                    # ユーザーに何か応答した方が親切な場合もある
                    # await message.reply("うーん、何について話しましょうか？", mention_author=False)
                    return

                current_content = genai_types.Content(role="user", parts=current_parts)

                # --- Gemini 設定を取得 ---
                model_name = config_manager.get_model_name()
                generation_config_dict = config_manager.get_generation_config_dict()
                safety_settings_list = config_manager.get_safety_settings_list()
                logger.debug(f"Using model: {model_name}")
                logger.debug(f"Generation config: {generation_config_dict}")
                logger.debug(f"Safety settings: {safety_settings_list}")

                # --- システムインストラクション ---
                def create_system_prompt(add_recitation_warning=False):
                    persona_prompt_base = config_manager.get_persona_prompt()
                    sys_prompt = persona_prompt_base
                    sys_prompt += f"\n\n--- ★★★ 現在の最重要情報 ★★★ ---"
                    sys_prompt += f"\nあなたは今、以下の Discord ユーザーと **直接** 会話しています。このユーザーに集中してください。"
                    sys_prompt += f"\n- ユーザー名: {message.author.display_name} (Discord 表示名)"
                    sys_prompt += f"\n- ユーザーID: {user_id}"
                    sys_prompt += f"\n- ★★ あなたが呼びかけるべき名前: 「{call_name}」 ★★"
                    sys_prompt += f"\n   (注: これは設定されたニックネーム、またはユーザー表示名です。)"
                    if channel_id:
                        channel_name = message.channel.name if isinstance(message.channel, discord.TextChannel) else "不明なチャンネル"
                        sys_prompt += f"\n- 会話の場所: サーバーチャンネル「{channel_name}」(ID: {channel_id})"
                    else:
                        sys_prompt += f"\n- 会話の場所: ダイレクトメッセージ (DM)"
                    sys_prompt += "\n---------------------------------"
                    current_mood = "普通"
                    if weather_mood_cog:
                        current_mood = weather_mood_cog.get_current_mood()
                        last_loc = weather_mood_cog.current_weather_location
                        last_desc = weather_mood_cog.current_weather_description
                        if last_loc and last_desc:
                            sys_prompt += f"\n\n--- あなたの現在の状態 ---"
                            sys_prompt += f"\nあなたは「{current_mood}」な気分です。これは {last_loc} の天気 ({last_desc}) に基づいています。応答には、この気分を自然に反映させてください。"
                        else:
                            sys_prompt += f"\n\n--- あなたの現在の状態 ---"
                            sys_prompt += f"\nあなたは「{current_mood}」な気分です。応答には、この気分を自然に反映させてください。"
                    sys_prompt += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"
                    sys_prompt += f"\n1. **最優先事項:** 会話の相手は常に上記の「ユーザーID: {user_id}」を持つ人物です。応答する際は、**必ず、絶対に「{call_name}」という名前で呼びかけてください。** "
                    sys_prompt += f"\n2. **厳禁:** 過去の会話履歴には、他のユーザーとの会話や、あなた自身の過去の発言が含まれています。履歴を参照することは許可しますが、**現在の対話相手である「{call_name}」さんを、それ以外の会話相手の名前で絶対に呼ばないでください。** 過去の文脈に引きずられず、**現在の「{call_name}」さんとの対話にのみ集中してください。**"
                    sys_prompt += f"\n3. 履歴内の各発言には `[発言者名]:` というプレフィックスが付いています。これを注意深く確認し、現在の対話相手「{call_name}」さんとの文脈に関係のある情報か慎重に判断してください。"
                    sys_prompt += f"\n4. **厳禁:** あなたの応答の **いかなる部分にも** `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。あなたの応答は、会話本文のみで構成してください。"
                    sys_prompt += f"\n4. **応答を生成する前に、本当にあなたが今誰と会話しているのかを整理してください。**"
                    if add_recitation_warning:
                        sys_prompt += f"\n5. **重要:** 前回の応答はウェブ検索結果等の引用が多すぎたため停止しました。**今回は検索結果をそのまま引用せず、必ず自分の言葉で要約・説明するようにしてください。** 引用符 `[]` も使わないでください。"
                    else:
                        sys_prompt += f"\n5. ウェブ検索結果などを参照する場合は、その内容を**必ず自分の言葉で要約・説明**してください。検索結果のテキストをそのまま長文でコピー＆ペーストする行為や、引用符 `[]` を使用することは禁止します。"
                    sys_prompt += "\n----------------------------------------\n"
                    logger.debug(f"Generated System Prompt (recitation warning: {add_recitation_warning}):\n{sys_prompt[:500]}...") # ★ログ追加
                    return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system")
                # --- システムインストラクションここまで ---

                system_instruction_content = create_system_prompt()
                safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]
                tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]

                final_generation_config = genai_types.GenerateContentConfig(
                    temperature=generation_config_dict.get('temperature', 0.9),
                    top_p=generation_config_dict.get('top_p', 1.0),
                    top_k=generation_config_dict.get('top_k', 1),
                    candidate_count=generation_config_dict.get('candidate_count', 1),
                    max_output_tokens=generation_config_dict.get('max_output_tokens', 1024),
                    safety_settings=safety_settings_for_api,
                    tools=tools_for_api,
                    system_instruction=system_instruction_content
                )

                contents_for_api = []
                contents_for_api.extend(history_list)
                contents_for_api.append(current_content)
                # logger.debug(f"Contents for API: {contents_for_api}") # 必要なら詳細ログ

                logger.info(f"Sending request to Gemini. Model: {model_name}, History length: {len(history_list)}, Current parts: {len(current_parts)}") # ★ログ修正
                response = self.genai_client.models.generate_content(
                    model=model_name,
                    contents=contents_for_api,
                    config=final_generation_config,
                )
                logger.debug(f"Received response from Gemini. Finish reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}") # ★ログ追加

                response_text = ""
                is_recitation_error = False
                finish_reason = None
                response_candidates_parts = []

                if response and response.candidates:
                    candidate = response.candidates[0]
                    finish_reason = candidate.finish_reason
                    if candidate.content and candidate.content.parts:
                        response_candidates_parts = candidate.content.parts
                        logger.debug(f"Response has {len(response_candidates_parts)} part(s).")
                    else:
                        logger.warning(f"Response candidate has no content parts. Finish reason: {finish_reason}")
                else:
                     logger.warning("Response has no candidates.")


                # --- Recitation エラー時の再試行 ---
                if finish_reason == genai_types.FinishReason.RECITATION:
                    logger.warning(f"Recitation error detected for user {user_id}. Retrying...")
                    is_recitation_error = True
                    await asyncio.sleep(1)
                    system_instruction_retry = create_system_prompt(add_recitation_warning=True)
                    final_generation_config_retry = genai_types.GenerateContentConfig(
                         temperature=generation_config_dict.get('temperature', 0.9),
                         top_p=generation_config_dict.get('top_p', 1.0),
                         top_k=generation_config_dict.get('top_k', 1),
                         candidate_count=generation_config_dict.get('candidate_count', 1),
                         max_output_tokens=generation_config_dict.get('max_output_tokens', 1024),
                         safety_settings=safety_settings_for_api,
                         tools=tools_for_api,
                         system_instruction=system_instruction_retry
                     )
                    logger.debug("Sending retry request to Gemini due to Recitation error...")
                    response = self.genai_client.models.generate_content(
                        model=model_name, contents=contents_for_api, config=final_generation_config_retry
                    )
                    logger.debug(f"Retry response finish_reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}")
                    if response and response.candidates:
                         candidate = response.candidates[0]
                         finish_reason = candidate.finish_reason
                         if candidate.content and candidate.content.parts:
                              response_candidates_parts = candidate.content.parts
                         else: response_candidates_parts = []
                    else: response_candidates_parts = []

                # --- 応答テキストの抽出 ---
                raw_response_text = ""
                if response_candidates_parts:
                    for i, part in enumerate(response_candidates_parts):
                        if hasattr(part, 'text') and part.text:
                            logger.debug(f"Extracted text from part {i}: '{part.text[:100]}...'")
                            raw_response_text += part.text
                        else:
                            logger.debug(f"Part {i} does not contain text.")
                response_text = raw_response_text.strip()
                logger.debug(f"Raw response text combined (len={len(response_text)}): '{response_text[:100]}...'")

                # --- 応答がない場合の処理 ---
                if not response_text:
                    logger.warning("Response text is empty after extraction.")
                    block_reason_str = "不明"; finish_reason_str = "不明"; safety_reason = "不明な理由"
                    try:
                        if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                            block_reason_str = str(response.prompt_feedback.block_reason or "ブロック理由なし")
                            logger.debug(f"Prompt feedback block reason: {block_reason_str}")
                        if finish_reason:
                            finish_reason_str = str(finish_reason)
                            logger.debug(f"Finish reason: {finish_reason_str}")
                        if finish_reason == genai_types.FinishReason.SAFETY and response and response.candidates and response.candidates[0].safety_ratings:
                            safety_categories = [str(r.category) for r in response.candidates[0].safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                            safety_reason = f"安全性フィルター ({', '.join(safety_categories)})" if safety_categories else "安全性フィルター"
                            logger.debug(f"Safety filter reason: {safety_reason}")
                    except Exception as e_fb:
                        logger.warning(f"Error accessing response feedback/finish reason: {e_fb}")

                    if is_recitation_error and finish_reason == genai_types.FinishReason.RECITATION:
                        response_text = f"({call_name}さん、応答が引用超過のため停止しました。再試行しましたが、再度停止しました。)"
                        logger.error(f"Recitation error even after retry for user {user_id}.")
                    elif finish_reason == genai_types.FinishReason.RECITATION:
                        response_text = f"({call_name}さん、応答が引用超過のため停止しました。)"
                        logger.warning(f"Recitation error occurred for user {user_id} (first attempt).")
                    elif finish_reason == genai_types.FinishReason.SAFETY:
                        response_text = f"({call_name}さん、応答が{safety_reason}によりブロックされました)"
                        logger.warning(f"Response blocked by safety filter for user {user_id}. Reason: {safety_reason}")
                    elif block_reason_str != "不明" and block_reason_str != "ブロック理由なし":
                        response_text = f"({call_name}さん、プロンプトが原因で応答がブロックされました。理由: {block_reason_str})"
                        logger.warning(f"Response blocked by prompt feedback: {block_reason_str}")
                    elif finish_reason == genai_types.FinishReason.MAX_TOKENS:
                         response_text = f"({call_name}さん、応答が長くなりすぎたため途中で停止しました。)"
                         logger.warning(f"Response stopped due to MAX_TOKENS for user {user_id}.")
                    else:
                        response_text = f"({call_name}さん、応答を生成できませんでした。終了理由: {finish_reason_str})"
                        logger.warning(f"No response generated. Finish: {finish_reason_str}, Block: {block_reason_str}") # ★ログに理由追加

                # --- 履歴の保存 ---
                if response_text and not response_text.startswith("("): # 正常な応答のみ履歴に追加
                    logger.debug("Preparing to save conversation history.")
                    # ★★★ 履歴保存用の part_to_dict ヘルパー関数 ★★★
                    def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                        """Partオブジェクトを履歴保存用の辞書に変換する。モデル応答の場合はプレフィックスを除去"""
                        data = {}
                        if hasattr(part, 'text') and part.text and part.text.strip():
                            text_content = part.text.strip()
                            if is_model_response:
                                cleaned_text = helpers.remove_all_prefixes(text_content)
                                if cleaned_text:
                                    data['text'] = cleaned_text
                                else:
                                    logger.debug("Part text became empty after prefix removal for model response history, skipping.")
                                    return {} # 空辞書を返してスキップ
                            else:
                                data['text'] = text_content
                        elif hasattr(part, 'inline_data') and part.inline_data:
                             try: data['inline_data'] = {'mime_type': part.inline_data.mime_type, 'data': None }
                             except Exception: logger.warning("Could not serialize inline_data for history.")
                        elif hasattr(part, 'function_call') and part.function_call:
                             try: data['function_call'] = {'name': part.function_call.name, 'args': dict(part.function_call.args),}
                             except Exception: logger.warning("Could not serialize function_call for history.")
                        elif hasattr(part, 'function_response') and part.function_response:
                             try: data['function_response'] = {'name': part.function_response.name, 'response': dict(part.function_response.response),}
                             except Exception: logger.warning("Could not serialize function_response for history.")
                        # ★ data が空でなければ返す (プレフィックス除去で空になった場合を除く)
                        return data if data else {}

                    # ユーザーの発言を辞書化 (プレフィックス除去なし)
                    user_parts_dict = [p_dict for part in current_parts if (p_dict := part_to_dict(part, is_model_response=False))]
                    if user_parts_dict:
                        logger.debug(f"Adding user entry to history (Author: {user_id}): {user_parts_dict}")
                        await history_cog.add_history_entry_async(current_interlocutor_id=self.bot.user.id, channel_id=channel_id, role="user", parts_dict=user_parts_dict, entry_author_id=user_id)
                    else:
                        logger.debug("No valid user parts to add to history.")

                    # ★ Bot の応答を辞書化 (プレフィックス除去あり) ★
                    bot_response_parts_dict_cleaned = [p_dict for part in response_candidates_parts if (p_dict := part_to_dict(part, is_model_response=True))]
                    if bot_response_parts_dict_cleaned:
                        logger.debug(f"Adding cleaned bot response entry to history (Author: {self.bot.user.id}): {bot_response_parts_dict_cleaned}")
                        await history_cog.add_history_entry_async(current_interlocutor_id=user_id, channel_id=channel_id, role="model", parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id)
                        logger.info(f"Added cleaned bot response to global history.")
                    else:
                        logger.warning("No valid parts to add to history after cleaning bot response.")
                elif response_text: # 括弧で始まるエラーメッセージなどは履歴に保存しない
                     logger.debug("Skipping history saving for error/info message.")

                # --- 応答送信 ---
                if response_text:
                    # 1. 引用マーク削除
                    text_after_citation = helpers.remove_citation_marks(response_text)
                    logger.debug(f"Text after citation removal (len={len(text_after_citation)}): '{text_after_citation[:100]}...'")

                    # 2. ★ 全てのプレフィックス削除 ★
                    text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
                    logger.debug(f"Text after prefix removal (len={len(text_after_prefixes)}): '{text_after_prefixes[:100]}...'")

                    # 3. 最大応答文字数制限の適用
                    max_len = config_manager.get_max_response_length()
                    original_len_after_clean = len(text_after_prefixes)
                    if original_len_after_clean > max_len:
                         logger.info(f"Response length after cleaning ({original_len_after_clean}) exceeded max length ({max_len}). Truncating.")
                         final_response_text = text_after_prefixes[:max_len - 3] + "..."
                    elif original_len_after_clean == 0 and len(text_after_citation) > 0: # プレフィックス除去で空になった場合
                         logger.warning("Response became empty after removing prefix(es). Not sending.")
                         final_response_text = None
                    else:
                         final_response_text = text_after_prefixes

                    logger.debug(f"Final text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")

                    # 4. 送信
                    if final_response_text:
                         try:
                              await helpers.split_and_send_messages(message, final_response_text, 1900)
                              logger.info(f"Successfully sent response to user {user_id}.")
                         except Exception as send_e:
                              logger.error(f"Error in split_and_send_messages for user {user_id}", exc_info=send_e)
                    elif response_text.startswith("("): # エラーメッセージ
                         try:
                              if is_dm: await message.channel.send(response_text)
                              else: await message.reply(response_text, mention_author=False)
                              logger.info(f"Sent error/stop message to user {user_id}: {response_text}")
                         except Exception as send_e:
                              logger.error(f"Error sending error/stop message to user {user_id}", exc_info=send_e)
                    else:
                         logger.info(f"Skipped sending empty message to user {user_id}.")

            # --- エラーハンドリング ---
            except genai_errors.StopCandidateException as e:
                 logger.warning(f"Content generation stopped for user {user_id}. Reason: {e.finish_reason}", exc_info=False)
                 reply_msg = f"({call_name}さん、応答の生成が停止されました。理由: {e.finish_reason})"
                 if e.finish_reason == genai_types.FinishReason.SAFETY: reply_msg = f"({call_name}さん、応答が安全性フィルターによりブロックされました)"
                 elif e.finish_reason == genai_types.FinishReason.RECITATION: reply_msg = f"({call_name}さん、応答が引用超過のため停止しました。)"
                 try:
                      if is_dm: await message.channel.send(reply_msg)
                      else: await message.reply(reply_msg, mention_author=False)
                 except discord.HTTPException: logger.error("Failed to send StopCandidateException message to Discord.")
            except genai_errors.APIError as e:
                 logger.error(f"Gemini API Error for user {user_id}: Code={e.code if hasattr(e, 'code') else 'N/A'}, Status={e.status if hasattr(e, 'status') else 'N/A'}, Message={e.message}", exc_info=False)
                 reply_msg = f"({call_name}さん、AIとの通信中にAPIエラーが発生しました。)"
                 if hasattr(e, 'code') and e.code == 429: reply_msg = f"({call_name}さん、APIの利用上限に達したようです。しばらく待ってから試してください。)"
                 elif "API key not valid" in str(e.message): reply_msg = f"({call_name}さん、エラー: APIキーが無効です。設定を確認してください。)"
                 try:
                      if is_dm: await message.channel.send(reply_msg)
                      else: await message.reply(reply_msg, mention_author=False)
                 except discord.HTTPException: logger.error("Failed to send API error message to Discord.")
            except discord.errors.NotFound:
                logger.warning(f"Message {message.id} or channel {message.channel.id} not found. Maybe deleted?")
            except Exception as e:
                logger.error(f"Error during message processing for user {user_id}", exc_info=True)
                try:
                     reply_msg = f"({call_name}さん、予期せぬエラーが発生しました: {type(e).__name__})"
                     if is_dm: await message.channel.send(reply_msg)
                     else: await message.reply(reply_msg, mention_author=False)
                except discord.HTTPException: logger.error("Failed to send unexpected error message to Discord.")


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot))
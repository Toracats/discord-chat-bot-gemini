# cogs/chat_cog.py (文字数制限適用箇所変更 & デバッグログ追加)

import discord
from discord.ext import commands
import logging
import datetime
import os
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
import asyncio # 再試行の遅延用

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

    def initialize_genai_client(self):
        """Geminiクライアントを初期化/再初期化する"""
        try:
            api_key = os.getenv("GOOGLE_AI_KEY")
            if not api_key:
                logger.error("GOOGLE_AI_KEY not found in environment variables.")
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
                allowed_channels = config_manager.channel_settings.get(server_id_str, [])
                if message.channel.id in allowed_channels: should_respond = True
        if not should_respond: return

        logger.info(f"Received message from {message.author.name} (ID: {message.author.id}) in {'DM' if is_dm else f'channel #{message.channel.name} (ID: {message.channel.id})'}")

        # --- reset_user_timer をバックグラウンドタスクとして起動 ---
        random_dm_cog = self.bot.get_cog("RandomDMCog")
        if random_dm_cog:
            try:
                asyncio.create_task(random_dm_cog.reset_user_timer(message.author.id), name=f"reset_timer_{message.author.id}")
                logger.debug(f"Created background task to reset random DM timer for user {message.author.id}")
            except Exception as e:
                logger.error(f"Failed to create task for resetting random DM timer", exc_info=e)
        else:
            logger.warning("RandomDMCog not found when trying to reset timer.")
        # --------------------------------------------------------

        async with message.channel.typing():
            try:
                cleaned_text = helpers.clean_discord_message(message.content)
                user_id = message.author.id
                channel_id = message.channel.id if not is_dm else None
                user_nickname = config_manager.get_nickname(user_id)
                user_representation = message.author.display_name

                history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
                processing_cog: Optional[ProcessingCog] = self.bot.get_cog("ProcessingCog")
                weather_mood_cog: Optional[WeatherMoodCog] = self.bot.get_cog("WeatherMoodCog")

                # ★ デバッグログ追加 1 ★
                logger.debug(f"Checking required cogs: HistoryCog={bool(history_cog)}, ProcessingCog={bool(processing_cog)}")

                if not history_cog or not processing_cog:
                     logger.error("Required Cog(s) (History/Processing) not found!")
                     await message.reply("エラー: 内部コンポーネントが見つかりません。", mention_author=False)
                     return

                history_list = await history_cog.get_global_history_for_prompt()

                current_parts = []
                if cleaned_text: current_parts.append(genai_types.Part(text=cleaned_text))
                attachment_parts = await processing_cog.process_attachments(message.attachments)
                current_parts.extend(attachment_parts)
                if not message.attachments:
                    url_content_parts = await processing_cog.process_url_in_message(cleaned_text)
                    if url_content_parts: current_parts.extend(url_content_parts)

                # ★ デバッグログ追加 2 ★
                logger.debug(f"Content check: cleaned_text='{cleaned_text}', current_parts={current_parts}")

                if not current_parts:
                    logger.warning("No processable content found in the message. Stopping response.")
                    return

                # ★ デバッグログ追加 3 ★
                logger.debug("Proceeding to create Content object and call Gemini API.")

                current_content = genai_types.Content(role="user", parts=current_parts)

                if not self.genai_client:
                     logger.error("Gemini client is not initialized.")
                     await message.reply("エラー: AIクライアントが初期化されていません。", mention_author=False)
                     return

                model_name = config_manager.get_model_name()
                generation_config_dict = config_manager.get_generation_config_dict()
                safety_settings_list = config_manager.get_safety_settings_list()

                # --- システムインストラクションの構築 ---
                def create_system_prompt(add_recitation_warning=False):
                    persona_prompt_base = config_manager.get_persona_prompt()
                    sys_prompt = persona_prompt_base
                    sys_prompt += f"\n\n--- 重要: 現在の会話状況 ---"
                    sys_prompt += f"\nあなたは今、以下のユーザーと会話しています:"
                    sys_prompt += f"\n- ユーザー名: {user_representation}"
                    sys_prompt += f"\n- ユーザーID: {user_id}"
                    call_name = user_nickname if user_nickname else user_representation
                    sys_prompt += f"\n- 呼びかける名前: **{call_name}**"
                    if channel_id:
                        channel_name = message.channel.name if isinstance(message.channel, discord.TextChannel) else "Unknown Channel"
                        sys_prompt += f"\n- 場所: サーバーチャンネル「{channel_name}」(ID: {channel_id})"
                    else:
                        sys_prompt += f"\n- 場所: ダイレクトメッセージ (DM)"

                    current_mood = "普通"
                    if weather_mood_cog:
                        current_mood = weather_mood_cog.get_current_mood()
                    sys_prompt += f"\n\n--- あなたの現在の気分 ---"
                    sys_prompt += f"\nあなたは今「{current_mood}」な気分です。応答には、この気分を自然に反映させてください。"

                    sys_prompt += f"\n\n--- 指示 ---"
                    sys_prompt += f"\n1. **最重要:** 会話の相手は常に上記の「ユーザー名」「ユーザーID」を持つ人物です。応答する際は、**必ず指定された「呼びかける名前」({call_name}) を使用してください。** 過去の履歴に他のユーザーが登場しても、**絶対に現在の対話相手の名前と混同しないでください。**"
                    sys_prompt += f"\n2. 過去の履歴には、あなたと他のユーザーとの会話などが含まれています。履歴を参照する際は、各発言の前に付いている `[発言者名]:` を確認し、現在の「{call_name}」さんとの会話の文脈に本当に関係がある情報か、慎重に判断してください。"
                    sys_prompt += f"\n3. あなた自身の過去の発言も履歴に含まれますが、現在の状況と気分に合わせて応答を生成してください。"
                    sys_prompt += f"\n4. **絶対に守ってください:** あなたの応答の **いかなる部分にも** `[{self.bot.user.display_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。応答は本文のみで構成してください。"
                    if add_recitation_warning:
                        sys_prompt += f"\n5. **重要:** 前回の応答はウェブ検索結果の引用が多すぎたため停止しました。**今回は検索結果をそのまま引用せず、必ず自分の言葉で要約・説明するようにしてください。**"
                    else:
                         sys_prompt += f"\n5. ウェブ検索結果を参照する場合は、その内容を**必ず自分の言葉で要約・説明**し、検索結果のテキストをそのまま長文でコピー＆ペーストしないでください。"
                    sys_prompt += "\n------------------------\n"
                    return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system")

                system_instruction_content = create_system_prompt()
                safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]
                tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
                logger.info("Google Search tool (for grounding) is always enabled for requests.")

                final_generation_config = genai_types.GenerateContentConfig(
                    **generation_config_dict,
                    safety_settings=safety_settings_for_api,
                    tools=tools_for_api,
                    system_instruction=system_instruction_content
                )

                contents_for_api = []
                contents_for_api.extend(history_list)
                contents_for_api.append(current_content)

                logger.debug(f"Sending initial request to Gemini. Model: {model_name}, History length: {len(history_list)}")

                response = self.genai_client.models.generate_content(
                    model=model_name,
                    contents=contents_for_api,
                    config=final_generation_config
                )

                response_text = ""
                is_recitation_error = False
                finish_reason = None
                response_candidates_parts = []

                if response.candidates:
                    finish_reason = response.candidates[0].finish_reason
                    if response.candidates[0].content and response.candidates[0].content.parts:
                        response_candidates_parts = response.candidates[0].content.parts

                if finish_reason == genai_types.FinishReason.RECITATION:
                    logger.warning(f"Recitation error detected for user {user_id}. Retrying...")
                    is_recitation_error = True
                    await asyncio.sleep(1)

                    system_instruction_retry = create_system_prompt(add_recitation_warning=True)
                    final_generation_config_retry = genai_types.GenerateContentConfig(
                        **generation_config_dict,
                        safety_settings=safety_settings_for_api,
                        tools=tools_for_api,
                        system_instruction=system_instruction_retry
                    )

                    logger.debug("Sending retry request to Gemini due to Recitation error...")
                    response = self.genai_client.models.generate_content(
                        model=model_name,
                        contents=contents_for_api,
                        config=final_generation_config_retry
                    )
                    logger.debug(f"Retry response: {response}")
                    if response.candidates:
                        finish_reason = response.candidates[0].finish_reason
                        if response.candidates[0].content and response.candidates[0].content.parts:
                            response_candidates_parts = response.candidates[0].content.parts

                raw_response_text = ""
                if response_candidates_parts:
                    for part in response_candidates_parts:
                        if hasattr(part, 'text') and part.text:
                            raw_response_text += part.text

                response_text = raw_response_text.strip()

                if not response_text:
                    block_reason_str = "不明"; finish_reason_str = "不明"; safety_reason = "不明な理由"
                    if hasattr(response, 'prompt_feedback') and response.prompt_feedback and response.prompt_feedback.block_reason: block_reason_str = str(response.prompt_feedback.block_reason) or "Not Blocked"
                    if finish_reason: finish_reason_str = str(finish_reason)
                    if finish_reason == genai_types.FinishReason.SAFETY and response.candidates and response.candidates[0].safety_ratings:
                         safety_categories = [str(r.category) for r in response.candidates[0].safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                         if safety_categories: safety_reason = f"安全性フィルター ({', '.join(safety_categories)})"
                         else: safety_reason = "安全性フィルター"

                    if is_recitation_error and finish_reason == genai_types.FinishReason.RECITATION: response_text = f"(応答が引用超過 (Recitation) のため停止されました。内容を修正して再試行しましたが、再度停止しました。)" ; logger.error(f"Recitation error even after retry for user {user_id}.")
                    elif finish_reason == genai_types.FinishReason.RECITATION: response_text = f"(応答が引用超過 (Recitation) のため停止されました。)"; logger.warning(f"Recitation error occurred for user {user_id} (no retry executed?).")
                    elif finish_reason == genai_types.FinishReason.SAFETY: response_text = f"(応答が{safety_reason}によりブロックされました)"; logger.warning(f"Response blocked by safety filter for user {user_id}. Reason: {safety_reason}")
                    elif block_reason_str != "不明" and block_reason_str != "Not Blocked": response_text = f"(プロンプトが原因で応答がブロックされました。理由: {block_reason_str})" ; logger.warning(f"Response blocked by prompt feedback: {block_reason_str}")
                    else: response_text = f"(応答が生成されませんでした。終了理由: {finish_reason_str})"; logger.warning(f"No response generated. Finish: {finish_reason_str}")

                # --- 履歴の保存 ---
                if response_text and not response_text.startswith("("):
                    def part_to_dict(part: genai_types.Part) -> dict:
                        data = {}; bot_name_local = self.bot.user.display_name; bot_prefix_local = f"[{bot_name_local}]:"
                        if hasattr(part, 'text') and part.text and part.text.strip():
                            cleaned_part_text = part.text.strip()
                            while cleaned_part_text.startswith(bot_prefix_local): cleaned_part_text = cleaned_part_text[len(bot_prefix_local):].strip()
                            if cleaned_part_text: data['text'] = cleaned_part_text
                        elif hasattr(part, 'inline_data') and part.inline_data: data['inline_data'] = {'mime_type': part.inline_data.mime_type, 'data': None }
                        elif hasattr(part, 'function_call') and part.function_call: data['function_call'] = {'name': part.function_call.name, 'args': dict(part.function_call.args),}
                        elif hasattr(part, 'function_response') and part.function_response: data['function_response'] = {'name': part.function_response.name, 'response': dict(part.function_response.response),}
                        return data

                    user_parts_dict = [part_to_dict(part) for part in current_parts if part_to_dict(part)]
                    if user_parts_dict: await history_cog.add_history_entry_async(current_interlocutor_id=self.bot.user.id, channel_id=channel_id, role="user", parts_dict=user_parts_dict, entry_author_id=user_id)

                    bot_response_parts_dict_cleaned = [part_to_dict(part) for part in response_candidates_parts if part_to_dict(part)]
                    if bot_response_parts_dict_cleaned:
                        await history_cog.add_history_entry_async(current_interlocutor_id=user_id, channel_id=channel_id, role="model", parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id)
                        logger.info(f"Added cleaned bot response to global history.")
                    else: logger.warning("No valid parts to add to history after cleaning bot response.")

                # --- 応答送信 ---
                if response_text and not response_text.startswith("("):
                    # 1. 引用マーク削除
                    final_response_text = helpers.remove_citation_marks(response_text)
                    logger.debug(f"Text after citation removal (len={len(final_response_text)}): '{final_response_text[:100]}...'")

                    # 2. プレフィックス削除
                    bot_name = self.bot.user.display_name
                    bot_prefix = f"[{bot_name}]:"
                    cleaned_response = final_response_text.strip()
                    prefix_removed_count = 0
                    while cleaned_response.startswith(bot_prefix):
                        cleaned_response = cleaned_response[len(bot_prefix):].strip()
                        prefix_removed_count += 1
                    if prefix_removed_count > 0: logger.info(f"Removed {prefix_removed_count} bot prefix(es) from response for sending.")
                    final_response_text = cleaned_response
                    logger.debug(f"Text after prefix removal (len={len(final_response_text)}): '{final_response_text[:100]}...'")

                    # 3. 文字数制限適用
                    max_len = config_manager.get_max_response_length()
                    original_len_after_clean = len(final_response_text)
                    if original_len_after_clean > max_len:
                        logger.info(f"Response length after cleaning ({original_len_after_clean}) exceeded max length ({max_len}). Truncating.")
                        final_response_text = final_response_text[:max_len] + "..."
                    elif original_len_after_clean == 0:
                         logger.warning("Response became empty after removing bot prefix(es). Not sending.")
                         final_response_text = None

                    logger.debug(f"Final text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")

                    # 4. 送信 (テキストが None または空でない場合のみ)
                    if final_response_text:
                         try:
                              await helpers.split_and_send_messages(message, final_response_text, 1900)
                              logger.info(f"Successfully sent response to user {user_id}.")
                         except Exception as send_e:
                              logger.error(f"Error in split_and_send_messages for user {user_id}", exc_info=send_e)
                    else:
                         logger.info(f"Skipped sending empty message to user {user_id}.")

                elif response_text: # エラー/停止メッセージを送信
                     try:
                         await message.reply(response_text, mention_author=False)
                         logger.info(f"Sent error/stop message to user {user_id}: {response_text}")
                     except Exception as send_e:
                          logger.error(f"Error sending error/stop message to user {user_id}", exc_info=send_e)

            # --- エラーハンドリング ---
            except genai_errors.APIError as e:
                 logger.error(f"Gemini API Error for user {user_id}: Code={e.code}, Status={e.status}, Message={e.message}", exc_info=False)
                 reply_msg = f"AIとの通信中にAPIエラーが発生しました (Code: {e.code})。"
                 if e.code == 429: reply_msg = "APIの利用上限に達したようです。しばらくしてからもう一度お試しください。"
                 elif "API key not valid" in str(e.message): reply_msg = "エラー: APIキーが無効です。設定を確認してください。"
                 try: await message.reply(reply_msg, mention_author=False)
                 except discord.HTTPException: logger.error("Failed to send API error message to Discord.")
            except Exception as e:
                logger.error(f"Error during message processing for user {user_id}", exc_info=True)
                try: await message.reply(f"予期せぬエラーが発生しました: {type(e).__name__}", mention_author=False)
                except discord.HTTPException: logger.error("Failed to send unexpected error message to Discord.")


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot))
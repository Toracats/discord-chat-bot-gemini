# cogs/chat_cog.py (要約読み込み・プロンプト追加)

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
        # (変更なし)
        try:
            api_key = os.getenv("GOOGLE_AI_KEY")
            if not api_key:
                logger.error("GOOGLE_AI_KEY not found in environment variables.")
                self.genai_client = None; return
            self.genai_client = genai.Client(api_key=api_key)
            logger.info("Gemini client initialized successfully.")
        except Exception as e:
            logger.error("Failed to initialize Gemini client", exc_info=e)
            self.genai_client = None

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
                allowed_channels = config_manager.get_allowed_channels(server_id_str) # ★ キーはstr
                if message.channel.id in allowed_channels: should_respond = True
        if not should_respond: return

        logger.info(f"Received message from {message.author.name} (ID: {message.author.id}) in {'DM' if is_dm else f'channel #{message.channel.name} (ID: {message.channel.id})'}")

        # --- reset_user_timer (変更なし) ---
        random_dm_cog = self.bot.get_cog("RandomDMCog")
        if random_dm_cog:
            try:
                asyncio.create_task(random_dm_cog.reset_user_timer(message.author.id), name=f"reset_timer_{message.author.id}")
                logger.debug(f"Created background task to reset random DM timer for user {message.author.id}")
            except Exception as e: logger.error(f"Failed to create task for resetting random DM timer", exc_info=e)
        else: logger.warning("RandomDMCog not found when trying to reset timer.")
        # ------------------------

        if not self.genai_client:
             logger.error("Gemini client is not initialized. Cannot respond.")
             return

        async with message.channel.typing():
            try:
                cleaned_text = helpers.clean_discord_message(message.content)
                user_id = message.author.id
                channel_id = message.channel.id if not is_dm else None
                user_nickname = config_manager.get_nickname(user_id)
                call_name = user_nickname if user_nickname else message.author.display_name

                history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
                processing_cog: Optional[ProcessingCog] = self.bot.get_cog("ProcessingCog")
                weather_mood_cog: Optional[WeatherMoodCog] = self.bot.get_cog("WeatherMoodCog")

                if not history_cog or not processing_cog:
                     logger.error("Required Cog(s) (History/Processing) not found!")
                     try:
                         if is_dm: await message.channel.send("エラー: 内部コンポーネントが見つかりません。")
                         else: await message.reply("エラー: 内部コンポーネントが見つかりません。", mention_author=False)
                     except discord.HTTPException as send_err: logger.error("Failed to send internal component error message", exc_info=send_err)
                     return

                # --- 履歴と現在のメッセージ内容を準備 ---
                history_list = await history_cog.get_global_history_for_prompt() # 直近履歴
                current_parts = []
                if cleaned_text: current_parts.append(genai_types.Part(text=cleaned_text))
                attachment_parts = await processing_cog.process_attachments(message.attachments)
                current_parts.extend(attachment_parts)
                if not message.attachments:
                    url_content_parts = await processing_cog.process_url_in_message(cleaned_text)
                    if url_content_parts: current_parts.extend(url_content_parts)

                if not current_parts:
                    logger.warning("No processable content found in the message. Stopping response.")
                    return
                current_content = genai_types.Content(role="user", parts=current_parts)

                # --- ★ 要約履歴の読み込みと調整 ★ ---
                summaries_all = await config_manager.load_summaries()
                max_summary_tokens = config_manager.get_summary_max_prompt_tokens()
                filtered_summaries = []
                current_summary_tokens = 0
                # 新しい要約から遡ってトークン数上限まで追加
                for summary in reversed(summaries_all):
                    summary_text = summary.get("summary_text", "")
                    # トークン数を文字数で概算 (日本語1文字=約1.5トークンと仮定)
                    # 必要なら google.genai の count_tokens を使う（要APIキー、コスト増）
                    estimated_tokens = self.genai_client.models.count_tokens(summary_text)
                    if current_summary_tokens + estimated_tokens <= max_summary_tokens:
                        filtered_summaries.append(summary)
                        current_summary_tokens += estimated_tokens
                    else:
                        logger.debug(f"Summary token limit ({max_summary_tokens}) reached. Stopped adding older summaries.")
                        break
                filtered_summaries.reverse() # 元の時系列に戻す
                logger.info(f"Loaded {len(summaries_all)} summaries, using {len(filtered_summaries)} summaries ({current_summary_tokens:.0f} estimated tokens) for prompt.")

                # --- Gemini 設定を取得 ---
                model_name = config_manager.get_model_name()
                generation_config_dict = config_manager.get_generation_config_dict()
                safety_settings_list = config_manager.get_safety_settings_list()

                # --- ★ システムインストラクション生成関数 (要約情報受け取り) ★ ---
                def create_system_prompt(summarized_history: List[Dict[str, Any]], add_recitation_warning=False):
                    persona_prompt_base = config_manager.get_persona_prompt()
                    sys_prompt = persona_prompt_base

                    # === 現在の対話相手の情報 === (変更なし)
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

                    # === あなたの現在の状態 === (変更なし)
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

                    # ★★★ 過去の会話の要約情報を追加 ★★★
                    if summarized_history:
                        sys_prompt += "\n\n--- 過去の会話の要約 (古い順) ---"
                        for summary in summarized_history:
                            ts_str = "(時刻不明)"
                            added_ts = summary.get("added_timestamp")
                            if isinstance(added_ts, datetime.datetime):
                                ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                            speaker_name = summary.get("speaker_call_name_at_summary", "(不明な発言者)")
                            summary_text = summary.get("summary_text", "(要約なし)")
                            sys_prompt += f"\n[{ts_str}] {speaker_name}: {summary_text}"
                        sys_prompt += "\n------------------------------"
                    else:
                        sys_prompt += "\n\n(過去の会話の要約はありません)"


                    # === 会話履歴の扱いと応答生成の指示 === (変更なし)
                    sys_prompt += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"
                    sys_prompt += f"\n1. **最優先事項:** 会話の相手は常に上記の「ユーザーID: {user_id}」を持つ人物です。応答する際は、**必ず、絶対に「{call_name}」という名前で呼びかけてください。** "
                    sys_prompt += f"\n2. **注意:** 上記の「過去の会話の要約」と、後述の「[最近の会話]」履歴には、他のユーザーとの会話やあなた自身の過去の発言が含まれます。これらは参考情報ですが、**現在の対話相手である「{call_name}」さんを、それ以外の名前で絶対に呼ばないでください。** 過去の文脈に引きずられず、**現在の「{call_name}」さんとの対話にのみ集中してください。**"
                    sys_prompt += f"\n3. [最近の会話]履歴内の各発言には `[発言者名]:` プレフィックスが付いています。要約と合わせて、現在の対話相手「{call_name}」さんとの文脈を理解するために参照してください。"
                    sys_prompt += f"\n4. **厳禁:** あなたの応答の **いかなる部分にも** `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。あなたの応答は、会話本文のみで構成してください。"
                    sys_prompt += f"\n5. **応答を生成する前に、本当にあなたが今誰と会話しているのかを整理してください。**"
                    if add_recitation_warning:
                        sys_prompt += f"\n6. **重要:** 前回の応答はウェブ検索結果等の引用が多すぎたため停止しました。**今回は検索結果をそのまま引用せず、必ず自分の言葉で要約・説明するようにしてください。** 引用符 `[]` も使わないでください。"
                    else:
                        sys_prompt += f"\n6. ウェブ検索結果などを参照する場合は、その内容を**必ず自分の言葉で要約・説明**してください。検索結果のテキストをそのまま長文でコピー＆ペーストする行為や、引用符 `[]` を使用することは禁止します。"
                    sys_prompt += "\n----------------------------------------\n"
                    logger.debug(f"Generated System Prompt (summaries: {len(summarized_history)}, recitation warning: {add_recitation_warning}):\n{sys_prompt[:500]}...") # ★ログ追加
                    return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system")
                # --- システムインストラクションここまで ---

                # ★ フィルタリングした要約情報を渡してシステムプロンプト生成 ★
                system_instruction_content = create_system_prompt(filtered_summaries)
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
                    system_instruction=system_instruction_content # ★ 更新されたシステムプロンプト
                )

                contents_for_api = []
                # ★ プロンプトに含める直近履歴の前に [最近の会話] ヘッダを追加 (任意) ★
                # contents_for_api.append(genai_types.Content(role="system", parts=[genai_types.Part(text="--- [最近の会話] ---")]))
                contents_for_api.extend(history_list)
                contents_for_api.append(current_content)

                logger.info(f"Sending request to Gemini. Model: {model_name}, History length: {len(history_list)}, Summaries: {len(filtered_summaries)}, Current parts: {len(current_parts)}")
                # 【GenAI呼び出し箇所 1/2 (チャット応答生成)】
                response = self.genai_client.models.generate_content(
                    model=model_name,
                    contents=contents_for_api,
                    config=final_generation_config,
                )
                logger.debug(f"Received response from Gemini. Finish reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}")

                # --- 応答処理、履歴保存、送信 (変更なし) ---
                response_text = ""
                is_recitation_error = False
                finish_reason = None
                response_candidates_parts = []
                if response and response.candidates:
                    candidate = response.candidates[0]
                    finish_reason = candidate.finish_reason
                    if candidate.content and candidate.content.parts: response_candidates_parts = candidate.content.parts
                    else: logger.warning(f"Response candidate has no content parts. Finish reason: {finish_reason}")
                else: logger.warning("Response has no candidates.")

                if finish_reason == genai_types.FinishReason.RECITATION:
                    is_recitation_error = True; await asyncio.sleep(1)
                    logger.warning(f"Recitation error detected for user {user_id}. Retrying...")
                    system_instruction_retry = create_system_prompt(filtered_summaries, add_recitation_warning=True) # ★ 要約情報も渡す
                    final_generation_config_retry = genai_types.GenerateContentConfig(
                         # ... (他の設定は同じ) ...
                         system_instruction=system_instruction_retry )
                    logger.debug("Sending retry request to Gemini due to Recitation error...")
                    # 【GenAI呼び出し箇所 2/2 (リトライ)】
                    response = self.genai_client.models.generate_content( model=model_name, contents=contents_for_api, config=final_generation_config_retry )
                    logger.debug(f"Retry response finish_reason: {response.candidates[0].finish_reason if response.candidates else 'N/A'}")
                    if response and response.candidates:
                         candidate = response.candidates[0]; finish_reason = candidate.finish_reason
                         if candidate.content and candidate.content.parts: response_candidates_parts = candidate.content.parts
                         else: response_candidates_parts = []
                    else: response_candidates_parts = []

                raw_response_text = "".join(part.text for part in response_candidates_parts if hasattr(part, 'text') and part.text)
                response_text = raw_response_text.strip()

                if not response_text:
                    # (応答がない場合のメッセージ生成 - 変更なし)
                    logger.warning("Response text is empty after extraction.")
                    block_reason_str = "不明"; finish_reason_str = "不明"; safety_reason = "不明な理由"
                    try:
                        if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback: block_reason_str = str(response.prompt_feedback.block_reason or "ブロック理由なし")
                        if finish_reason: finish_reason_str = str(finish_reason)
                        if finish_reason == genai_types.FinishReason.SAFETY and response and response.candidates and response.candidates[0].safety_ratings: safety_categories = [str(r.category) for r in response.candidates[0].safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]; safety_reason = f"安全性フィルター ({', '.join(safety_categories)})" if safety_categories else "安全性フィルター"
                    except Exception as e_fb: logger.warning(f"Error accessing response feedback/finish reason: {e_fb}")
                    if is_recitation_error and finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、応答が引用超過のため停止しました。再試行しましたが、再度停止しました。)"
                    elif finish_reason == genai_types.FinishReason.RECITATION: response_text = f"({call_name}さん、応答が引用超過のため停止しました。)"
                    elif finish_reason == genai_types.FinishReason.SAFETY: response_text = f"({call_name}さん、応答が{safety_reason}によりブロックされました)"
                    elif block_reason_str != "不明" and block_reason_str != "ブロック理由なし": response_text = f"({call_name}さん、プロンプトが原因で応答がブロックされました。理由: {block_reason_str})"
                    elif finish_reason == genai_types.FinishReason.MAX_TOKENS: response_text = f"({call_name}さん、応答が長くなりすぎたため途中で停止しました。)"
                    else: response_text = f"({call_name}さん、応答を生成できませんでした。終了理由: {finish_reason_str}, ブロック理由: {block_reason_str})" # ★ 理由追加
                    logger.warning(f"No response generated. Finish: {finish_reason_str}, Block: {block_reason_str}")

                if response_text and not response_text.startswith("("):
                    logger.debug("Preparing to save conversation history.")
                    def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                        data = {}
                        if hasattr(part, 'text') and part.text and part.text.strip():
                            text_content = part.text.strip()
                            if is_model_response:
                                cleaned_text = helpers.remove_all_prefixes(text_content)
                                if cleaned_text: data['text'] = cleaned_text
                                else: return {}
                            else: data['text'] = text_content
                        # 他の Part タイプ処理省略
                        return data if data else {}
                    user_parts_dict = [p_dict for part in current_parts if (p_dict := part_to_dict(part, is_model_response=False))]
                    if user_parts_dict: await history_cog.add_history_entry_async(current_interlocutor_id=self.bot.user.id, channel_id=channel_id, role="user", parts_dict=user_parts_dict, entry_author_id=user_id)
                    bot_response_parts_dict_cleaned = [p_dict for part in response_candidates_parts if (p_dict := part_to_dict(part, is_model_response=True))]
                    if bot_response_parts_dict_cleaned: await history_cog.add_history_entry_async(current_interlocutor_id=user_id, channel_id=channel_id, role="model", parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id); logger.info(f"Added cleaned bot response to global history.")
                    else: logger.warning("No valid parts to add to history after cleaning bot response.")
                elif response_text: logger.debug("Skipping history saving for error/info message.")

                if response_text:
                    text_after_citation = helpers.remove_citation_marks(response_text)
                    text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
                    max_len = config_manager.get_max_response_length()
                    original_len_after_clean = len(text_after_prefixes)
                    if original_len_after_clean > max_len: final_response_text = text_after_prefixes[:max_len - 3] + "..."
                    elif original_len_after_clean == 0 and len(text_after_citation) > 0: final_response_text = None
                    else: final_response_text = text_after_prefixes
                    logger.debug(f"Final text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")
                    if final_response_text: await helpers.split_and_send_messages(message, final_response_text, 1900); logger.info(f"Successfully sent response to user {user_id}.")
                    elif response_text.startswith("("):
                         try:
                              if is_dm: await message.channel.send(response_text)
                              else: await message.reply(response_text, mention_author=False)
                              logger.info(f"Sent error/stop message to user {user_id}: {response_text}")
                         except Exception as send_e: logger.error(f"Error sending error/stop message to user {user_id}", exc_info=send_e)
                    else: logger.info(f"Skipped sending empty message to user {user_id}.")

            # --- エラーハンドリング (変更なし) ---
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
            except discord.errors.NotFound: logger.warning(f"Message {message.id} or channel {message.channel.id} not found. Maybe deleted?")
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
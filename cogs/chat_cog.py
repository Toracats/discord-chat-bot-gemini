# cogs/chat_cog.py (デバッグログ追加・エラーハンドリング強化版)

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

# 他のCogやUtilsから必要なものをインポート
from utils import config_manager
from utils import helpers
# HistoryCog などを TYPE_CHECKING でインポート (循環参照回避)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cogs.history_cog import HistoryCog
    from cogs.processing_cog import ProcessingCog
    from cogs.weather_mood_cog import WeatherMoodCog
    from cogs.random_dm_cog import RandomDMCog
else: # 実行時は get_cog で取得するため None でも可
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
        # initialize_genai_client は setup で呼び出す

    def get_call_name(self, target_user_id: Optional[int]) -> str:
        """ユーザーIDに対応する呼び名を取得する"""
        if target_user_id is None: return "(不明な相手)"
        if target_user_id == self.bot.user.id: return self.bot.user.display_name
        # GUI版のロック方式を使用
        with config_manager.user_data_lock:
            nickname = config_manager.app_config.get("user_data", {}).get(str(target_user_id), {}).get("nickname")
        if nickname: return nickname
        user = self.bot.get_user(target_user_id)
        if user: return user.display_name
        return f"User {target_user_id}"

    def initialize_genai_client(self) -> bool:
        """Geminiクライアントを初期化し、成功/失敗を返す"""
        api_key = config_manager.get_gemini_api_key()
        if not api_key:
            logger.error("Gemini API Key not found in config. ChatCog cannot function.")
            self.genai_client = None
            return False
        try:
            self.genai_client = genai.Client(api_key=api_key)
            logger.info("Gemini client initialized successfully for ChatCog.")
            return True
        except Exception as e:
            logger.error("Failed to initialize Gemini client for ChatCog", exc_info=e)
            self.genai_client = None
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """メッセージ受信時の処理"""
        # --- メッセージフィルタリング ---
        if message.author == self.bot.user or message.author.bot: return
        if message.mention_everyone: return

        # --- 応答判定 ---
        should_respond = False
        is_dm = isinstance(message.channel, discord.DMChannel)
        if is_dm:
            should_respond = True
        elif message.guild:
            if self.bot.user.mentioned_in(message):
                should_respond = True
            else:
                server_id_str = str(message.guild.id)
                allowed_channels = config_manager.get_allowed_channels(server_id_str)
                if message.channel.id in allowed_channels:
                    should_respond = True
        if not should_respond: return

        logger.info(f"Received message from {message.author.name} (ID: {message.author.id}) in {'DM' if is_dm else f'channel #{message.channel.name}'}")

        # --- RandomDMCog タイマーリセット呼び出し ---
        random_dm_cog: Optional[RandomDMCog] = self.bot.get_cog("RandomDMCog")
        if random_dm_cog and hasattr(random_dm_cog, 'reset_user_timer'):
            try:
                asyncio.create_task(random_dm_cog.reset_user_timer(message.author.id), name=f"reset_timer_{message.author.id}")
                logger.debug(f"Task to reset timer for user {message.author.id} created.")
            except Exception as e:
                logger.error(f"Failed create task reset timer", exc_info=e)
        elif not random_dm_cog:
             logger.warning("RandomDMCog not found, cannot reset timer.")

        # --- Gemini クライアントチェック ---
        if not self.genai_client:
            logger.error("ChatCog: Gemini client not initialized.")
            await message.reply("エラー: AI機能が利用できません。", mention_author=False)
            return

        # ★★★ 'typing' 開始 ★★★
        async with message.channel.typing():
            response = None # ★ response 変数を初期化
            try:
                # --- メッセージ内容とユーザー情報の準備 ---
                cleaned_text = helpers.clean_discord_message(message.content)
                user_id = message.author.id
                channel_id = message.channel.id if not is_dm else None
                call_name = self.get_call_name(user_id)

                # --- 依存Cogの取得 ---
                history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
                processing_cog: Optional[ProcessingCog] = self.bot.get_cog("ProcessingCog")
                weather_mood_cog: Optional[WeatherMoodCog] = self.bot.get_cog("WeatherMoodCog")

                if not history_cog:
                    logger.error("HistoryCog not found! Cannot proceed.")
                    await message.reply("エラー: 履歴管理機能が見つかりません。", mention_author=False)
                    return

                # --- 履歴と現在のメッセージパートの準備 ---
                history_list = await history_cog.get_global_history_for_prompt()

                current_parts: List[genai_types.Part] = []
                if cleaned_text:
                    current_parts.append(genai_types.Part(text=cleaned_text))

                if processing_cog:
                    attachment_parts = await processing_cog.process_attachments(message.attachments)
                    if attachment_parts:
                        current_parts.extend(attachment_parts)
                        logger.debug(f"Added {len(attachment_parts)} parts from attachments.")
                    if not message.attachments:
                        url_content_parts = await processing_cog.process_url_in_message(cleaned_text)
                        if url_content_parts:
                            current_parts.extend(url_content_parts)
                            logger.debug(f"Added {len(url_content_parts)} parts from URL.")
                else:
                    logger.warning("ProcessingCog not found, skipping attachment/URL processing.")

                if not current_parts:
                    logger.warning("No processable content found in the message.")
                    return # 処理する内容がない場合は応答しない

                current_content = genai_types.Content(role="user", parts=current_parts)

                # --- 要約履歴の準備 ---
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
                    else:
                        break # トークン上限
                filtered_summaries.reverse() # 古い順に戻す
                logger.info(f"Using {len(filtered_summaries)} summaries ({current_summary_tokens:.0f} estimated tokens) for context.")

                # --- システムインストラクション生成関数 ---
                def create_system_prompt(summarized_history: List[Dict[str, Any]], add_recitation_warning=False) -> genai_types.Content:
                    # (省略なしのコードをそのまま使用)
                    persona_prompt_base = config_manager.get_persona_prompt()
                    sys_prompt = persona_prompt_base
                    sys_prompt += "\n\n--- 既知のユーザーとその固有名 ---"
                    all_identifiers = config_manager.get_all_user_identifiers()
                    if all_identifiers:
                        for uid, identifier in all_identifiers.items(): sys_prompt += f"\n- {identifier} (ID: {uid})"
                    else: sys_prompt += "\n(Botが認識している固有名ユーザーなし)"
                    sys_prompt += f"\n- {self.bot.user.display_name} (ID: {self.bot.user.id}) (Bot自身)"
                    sys_prompt += "\n------------------------------------"
                    sys_prompt += f"\n\n--- ★★★ 現在の最重要情報 ★★★ ---"
                    sys_prompt += f"\nあなたは今、以下の Discord ユーザーと **直接** 会話しています。このユーザーに集中してください。"
                    sys_prompt += f"\n- ユーザー名(表示名): {message.author.display_name}"
                    sys_prompt += f"\n- ユーザーID: {user_id}"
                    sys_prompt += f"\n- ★★ あなたが呼びかけるべき名前: 「{call_name}」 ★★"
                    sys_prompt += f"\n   (注: これはBotが認識している固有名、またはユーザー表示名です。状況に応じて後述のあだ名も使用してください。)"
                    if channel_id:
                         channel = self.bot.get_channel(channel_id)
                         channel_name = getattr(channel, 'name', '不明なチャンネル')
                         sys_prompt += f"\n- 会話の場所: サーバーチャンネル「{channel_name}」(ID:{channel_id})"
                    else: sys_prompt += f"\n- 会話の場所: ダイレクトメッセージ (DM)"
                    sys_prompt += "\n---------------------------------"
                    current_mood = "普通"
                    if weather_mood_cog:
                        current_mood = weather_mood_cog.get_current_mood()
                        last_loc = weather_mood_cog.current_weather_location
                        last_desc = weather_mood_cog.current_weather_description
                        if last_loc and last_desc:
                            sys_prompt += f"\n\n--- あなたの現在の状態 ---\n気分「{current_mood}」({last_loc} の天気: {last_desc})。この気分を応答に自然に反映させてください。"
                        else:
                            sys_prompt += f"\n\n--- あなたの現在の状態 ---\n気分「{current_mood}」。この気分を応答に自然に反映させてください。"
                    else:
                         sys_prompt += f"\n\n--- あなたの現在の状態 ---\n気分「{current_mood}」。この気分を応答に自然に反映させてください。"
                    if summarized_history:
                        sys_prompt += "\n\n--- 過去の会話の要約 (古い順) ---"
                        user_id_pattern = re.compile(r'(?:User |ID:)(\d+)')
                        for summary in summarized_history:
                            ts_str = "(時刻不明)"
                            added_ts = summary.get("added_timestamp")
                            if isinstance(added_ts, datetime.datetime): ts_str = added_ts.strftime("%Y-%m-%d %H:%M")
                            speaker_name = summary.get("speaker_call_name_at_summary", "(不明な発言者)")
                            summary_text = summary.get("summary_text", "(要約内容なし)")
                            def replace_id(match):
                                try: return self.get_call_name(int(match.group(1)))
                                except: return match.group(0)
                            formatted_summary_text = user_id_pattern.sub(replace_id, summary_text)
                            sys_prompt += f"\n[{ts_str}] {speaker_name}: {formatted_summary_text}"
                        sys_prompt += "\n----------------------"
                    else: sys_prompt += "\n\n(過去の会話の要約はありません)"
                    sys_prompt += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"
                    sys_prompt += f"\n1. **現在の対話相手:** あなたが応答すべき相手は「{call_name}」(ID: {user_id})です。他のユーザー宛てのメッセージと混同しないでください。"
                    sys_prompt += f"\n2. **名前の認識:** 会話履歴や要約内の `[ユーザー名]:` や `User <ID>` は発言者を示します。"
                    sys_prompt += f"\n3. **呼び名の学習と使用:** 過去の会話で、特定のユーザーIDに対して「{call_name}」以外の「あだ名」が使われていたら記憶し、文脈に合わせて使用しても構いません。ただし、基本的には指示された「{call_name}」を使用してください。"
                    sys_prompt += f"\n4. **情報活用:** あなた自身の現在の気分、会話履歴、ユーザーからのメッセージ（添付ファイルやURLの内容も含む）、過去の会話の要約を考慮して、自然で人間らしい応答を生成してください。"
                    sys_prompt += f"\n5. **不明な呼び名の確認:** もし会話相手の呼び名が「User <ID>」形式のままになっている場合、応答の中で「ところで、あなたのことは何とお呼びすればよいですか？」のように自然に尋ねてください。"
                    sys_prompt += f"\n6. **注意:** 他のユーザーの名前やあだ名を間違って現在の対話相手（{call_name}）に呼びかけないように細心の注意を払ってください。"
                    sys_prompt += f"\n7. [最近の会話]履歴内の各発言には発言者が `[ユーザー名]:` または `[Bot名]:` 形式で付与されています。これは文脈理解のためであり、あなたの応答に含めてはいけません。"
                    sys_prompt += f"\n8. **厳禁:** あなた自身の応答には、**いかなる部分にも** `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。あなたの発言そのものだけを出力してください。"
                    sys_prompt += f"\n9. **応答を生成する前に、**あなたが今誰と会話していて、相手を何と呼ぶべきか（「{call_name}」または学習したあだ名）を再確認してください。"
                    if add_recitation_warning:
                        sys_prompt += f"\n10. **重要:** 前回の応答は引用が多すぎたためブロックされました。今回は、参照元を示す場合でも、より多くの部分をあなた自身の言葉で要約・説明するようにしてください。"
                    else:
                        sys_prompt += f"\n10. ウェブ検索結果などを参照する場合は、情報源（URLなど）を応答に含めるようにしてください。"
                    sys_prompt += "\n----------------------------------------\n"
                    logger.debug(f"System Prompt generated (summaries: {len(summarized_history)}, recitation warning: {add_recitation_warning}):\n{sys_prompt[:500]}...")
                    return genai_types.Content(parts=[genai_types.Part(text=sys_prompt)], role="system") # CUI版と同様にrole="system"は使わない
                # --- システムインストラクションここまで ---

                # --- API呼び出し準備 ---
                model_name = config_manager.get_model_name()
                generation_config_dict = config_manager.get_generation_config_dict()
                safety_settings_list = config_manager.get_safety_settings_list()
                system_instruction_content = create_system_prompt(filtered_summaries)

                config_args = generation_config_dict.copy()
                if safety_settings_list:
                    config_args['safety_settings'] = [genai_types.SafetySetting(**s) for s in safety_settings_list]
                config_args['system_instruction'] = system_instruction_content
                tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
                config_args['tools'] = tools_for_api
                final_config = genai_types.GenerateContentConfig(**config_args) if config_args else None

                contents_for_api = []
                contents_for_api.extend(history_list)
                contents_for_api.append(current_content)

                logger.info(f"Sending request to Gemini. Model: {model_name}, History: {len(history_list)}, Summaries: {len(filtered_summaries)}, Current parts: {len(current_parts)}")
                logger.debug(f"Sending contents: {contents_for_api}") # ★ 送信内容もログ出力
                logger.debug(f"Generation config: {final_config}") # ★ 設定内容もログ出力

                # ★★★ generate_content 呼び出し ★★★
                try:
                    response = self.genai_client.models.generate_content(
                        model=model_name,
                        contents=contents_for_api,
                        config=final_config
                    )
                    # ★ API応答オブジェクト全体をデバッグログに出力 ★
                    logger.debug(f"Received response object from Gemini: {response}")
                except Exception as api_call_e:
                    logger.error("Exception during Gemini API call", exc_info=api_call_e)
                    await message.reply(f"({call_name}さん、AIとの通信中にエラーが発生しました。)", mention_author=False)
                    return # ★★★ API呼び出し失敗時はここで処理終了 ★★★
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★

                # --- 応答処理 (Recitation リトライ含む) ---
                response_text = ""
                is_recitation_error = False
                finish_reason = None
                response_candidates_parts = []
                safety_reason = "不明"
                safety_ratings_info = "詳細不明"
                block_reason_str = "不明"

                # ★ 応答オブジェクトと候補の存在チェックを強化 ★
                if response and response.candidates:
                    candidate = response.candidates[0]
                    finish_reason = candidate.finish_reason
                    logger.debug(f"Response candidate finish reason: {finish_reason}") # ★ finish_reasonをログ出力

                    # ★ 安全性評価のチェック (応答パートがあるかに関わらず) ★
                    if candidate.safety_ratings:
                        triggered = [f"{r.category.name}:{r.probability.name}" for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                        if triggered:
                            safety_reason = f"安全フィルタ({','.join([r.category.name for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE])})"
                            safety_ratings_info = ", ".join(triggered)
                            logger.warning(f"Safety block detected: {safety_ratings_info}")
                        else:
                            logger.debug("Safety ratings present but no category above NEGLIGIBLE.")

                    # ★ コンテンツとパートの存在チェック ★
                    if candidate.content and candidate.content.parts:
                        response_candidates_parts = candidate.content.parts
                        logger.debug(f"Found {len(response_candidates_parts)} parts in response.")
                        # 各パートの内容もログに出力（必要なら）
                        # for i, part in enumerate(response_candidates_parts):
                        #     logger.debug(f" Part {i}: {part}")
                    else:
                        logger.warning(f"Response candidate has no content or parts. Finish reason: {finish_reason}")
                        # ここで finish_reason に応じたエラーメッセージを response_text に設定
                        if finish_reason == genai_types.FinishReason.SAFETY:
                             response_text = f"({call_name}さん、応答が{safety_reason}によりブロックされました。\n詳細: {safety_ratings_info})"
                        elif finish_reason == genai_types.FinishReason.RECITATION:
                             response_text = f"({call_name}さん、引用超過エラーのため応答を停止しました。)"
                        elif finish_reason == genai_types.FinishReason.MAX_TOKENS:
                             response_text = f"({call_name}さん、応答が長すぎるため途中で停止しました。)"
                        # プロンプトフィードバックも確認
                        elif response.prompt_feedback and response.prompt_feedback.block_reason:
                             block_reason_str = str(response.prompt_feedback.block_reason)
                             response_text = f"({call_name}さん、入力が原因で応答がブロックされました。理由: {block_reason_str})"
                        else:
                             response_text = f"({call_name}さん、応答内容を取得できませんでした。終了理由: {finish_reason})"

                else:
                    logger.warning("Response object or candidates list is empty.")
                    # プロンプトフィードバックを確認
                    if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback and response.prompt_feedback.block_reason:
                        block_reason_str = str(response.prompt_feedback.block_reason or "不明")
                        response_text = f"({call_name}さん、入力が原因で応答がブロックされました。理由: {block_reason_str})"
                    else:
                        response_text = f"({call_name}さん、AIからの有効な応答を取得できませんでした。)" # フォールバックメッセージ

                # Recitation エラーリトライ (response_text がまだ空の場合のみ)
                if finish_reason == genai_types.FinishReason.RECITATION and not response_text:
                    is_recitation_error = True
                    await asyncio.sleep(1)
                    logger.warning(f"Recitation error for user {user_id}. Retrying with warning prompt...")
                    system_instruction_retry = create_system_prompt(filtered_summaries, add_recitation_warning=True)
                    retry_config_args = config_args.copy()
                    retry_config_args['system_instruction'] = system_instruction_retry
                    final_config_retry = genai_types.GenerateContentConfig(**retry_config_args)

                    try:
                        # ★★★ generate_content 呼び出し (再試行) ★★★
                        response = self.genai_client.models.generate_content(
                            model=model_name,
                            contents=contents_for_api,
                            config=final_config_retry
                        )
                        logger.debug(f"Retry response object: {response}") # ★ 再試行の応答もログに
                        logger.debug(f"Retry response finish reason: {response.candidates[0].finish_reason if response and response.candidates else 'N/A'}")
                        # 再試行の結果を反映
                        if response and response.candidates:
                            candidate = response.candidates[0]
                            finish_reason = candidate.finish_reason
                            if candidate.content and candidate.content.parts:
                                response_candidates_parts = candidate.content.parts
                            else: response_candidates_parts = []
                            # 再試行後の安全性評価も確認
                            if candidate.safety_ratings:
                                triggered = [f"{r.category.name}:{r.probability.name}" for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                                if triggered:
                                    safety_reason = f"安全フィルタ({','.join([r.category.name for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE])}) (再試行)"
                                    safety_ratings_info = ", ".join(triggered) + " (再試行)"
                                    logger.warning(f"Safety block detected after retry: {safety_ratings_info}")
                        else: response_candidates_parts = []
                    except Exception as retry_e:
                        logger.error("Exception during retry API call", exc_info=retry_e)
                        response_text = f"({call_name}さん、引用エラー後の再試行中にエラーが発生しました。)"


                # --- 応答テキスト抽出 (response_textがまだ空の場合) & 空の場合の処理 ---
                if not response_text:
                    raw_response_text = "".join(part.text for part in response_candidates_parts if hasattr(part, 'text') and part.text)
                    response_text = raw_response_text.strip()
                    logger.debug(f"Extracted text after potential retry: '{response_text[:200]}...'") # ★ 抽出結果をログに

                # ★★★ 最終的な空応答チェックとフォールバック ★★★
                if not response_text:
                    logger.warning(f"Response text is STILL empty after extraction/retry. Finish reason: {finish_reason}, Block Reason: {block_reason_str}")
                    # is_recitation_error フラグも考慮
                    if is_recitation_error and finish_reason == genai_types.FinishReason.RECITATION:
                        response_text = f"({call_name}さん、引用超過エラーのため応答を停止しました(再試行後)。)"
                    elif finish_reason == genai_types.FinishReason.RECITATION:
                        response_text = f"({call_name}さん、引用超過エラーのため応答を停止しました。)"
                    elif finish_reason == genai_types.FinishReason.SAFETY:
                        response_text = f"({call_name}さん、応答が{safety_reason}によりブロックされました。\n詳細: {safety_ratings_info})"
                    elif block_reason_str != "不明" and block_reason_str != "なし": # プロンプトブロック
                        response_text = f"({call_name}さん、入力内容が原因で応答がブロックされました。理由: {block_reason_str})"
                    elif finish_reason == genai_types.FinishReason.MAX_TOKENS:
                        response_text = f"({call_name}さん、応答が長すぎるため途中で停止しました。)"
                    else: # その他の不明なケース
                        response_text = f"({call_name}さん、応答を生成できませんでした。終了理由: {finish_reason}, ブロック理由: {block_reason_str})"
                    logger.warning(f"Generated fallback message: {response_text}")


                # --- 履歴保存 ---
                if history_cog and response_text and not response_text.startswith("("):
                    logger.debug("Attempting to save conversation history.")
                    try:
                        def part_to_dict(part: genai_types.Part, is_model_response: bool = False) -> Dict[str, Any]:
                            data = {}
                            if hasattr(part, 'text') and part.text and part.text.strip():
                                text_content = part.text.strip()
                                data['text'] = text_content
                            elif hasattr(part, 'inline_data') and part.inline_data:
                                try: data['inline_data'] = {'mime_type': part.inline_data.mime_type, 'data': '<omitted>' }
                                except Exception: logger.warning("Could not serialize inline_data stub for history.")
                            elif hasattr(part, 'function_call') and part.function_call:
                                try: data['function_call'] = {'name': part.function_call.name, 'args': dict(part.function_call.args),}
                                except Exception: logger.warning("Could not serialize function_call for history.")
                            elif hasattr(part, 'function_response') and part.function_response:
                                try: data['function_response'] = {'name': part.function_response.name, 'response': dict(part.function_response.response),}
                                except Exception: logger.warning("Could not serialize function_response for history.")
                            return data if data else {}

                        user_parts_dict = [p_dict for part in current_parts if (p_dict := part_to_dict(part, False))]
                        if user_parts_dict:
                            await history_cog.add_history_entry_async(
                                current_interlocutor_id=self.bot.user.id, channel_id=channel_id,
                                role="user", parts_dict=user_parts_dict, entry_author_id=user_id
                            )
                            logger.debug(f"Added user message ({len(user_parts_dict)} parts) to history.")
                        else:
                             logger.debug("No user parts to add to history.")

                        # ★ 応答パートの保存は、応答が生成された場合のみ ★
                        if response_candidates_parts:
                             bot_response_parts_dict = [p_dict for part in response_candidates_parts if (p_dict := part_to_dict(part, True))]
                             if bot_response_parts_dict:
                                 await history_cog.add_history_entry_async(
                                     current_interlocutor_id=user_id, channel_id=channel_id,
                                     role="model", parts_dict=bot_response_parts_dict,
                                     entry_author_id=self.bot.user.id
                                 )
                                 logger.info(f"Added bot response ({len(bot_response_parts_dict)} parts) to history.")
                             else:
                                 logger.warning("No valid bot response parts found to add to history, though candidates existed.")
                        else:
                             logger.debug("No bot response parts candidates found, skipping history save for bot response.")

                    except Exception as hist_e:
                        logger.error("Error during history saving", exc_info=hist_e)
                        # 履歴保存エラーは応答送信を妨げないようにする

                elif response_text:
                    logger.debug("Skipping history saving for error/info message.")
                else:
                    logger.error("History saving skipped because response_text is still empty/None.") # 通常発生しないはず


                # --- 応答送信 ---
                if response_text:
                    text_after_citation = helpers.remove_citation_marks(response_text)
                    text_after_prefixes = helpers.remove_all_prefixes(text_after_citation)
                    max_len = config_manager.get_max_response_length()
                    original_len_after_clean = len(text_after_prefixes)

                    final_response_text = None
                    if original_len_after_clean > max_len:
                        final_response_text = text_after_prefixes[:max_len - 3] + "..."
                    elif original_len_after_clean > 0:
                        final_response_text = text_after_prefixes
                    elif response_text.startswith("("): # エラー/情報メッセージはそのまま
                        final_response_text = response_text

                    logger.debug(f"Final text to send (len={len(final_response_text) if final_response_text else 0}): '{final_response_text[:100] if final_response_text else 'None'}'")

                    if final_response_text:
                        # エラー/情報メッセージか通常の応答かで送信方法を分ける
                        if final_response_text.startswith("("):
                            try:
                                await message.reply(final_response_text, mention_author=False) if not is_dm else await message.channel.send(final_response_text)
                                logger.info(f"Sent error/stop message to user {user_id}: {final_response_text}")
                            except Exception as send_e:
                                logger.error(f"Error sending info/error message to user {user_id}", exc_info=send_e)
                        else:
                            # 通常の応答は分割送信
                            await helpers.split_and_send_messages(message, final_response_text, 1900)
                            logger.info(f"Sent response to user {user_id}.")
                    else: # 送信するテキストがない場合
                        logger.error(f"Failed to generate final_response_text. Original response was: '{response_text[:100]}...'")
                        # 念のためフォールバックメッセージを送信
                        try: await message.reply(f"({call_name}さん、応答の準備中に問題が発生しました。)", mention_author=False)
                        except Exception: pass

                else: # response_text が None や空のままの場合 (通常ここには来ないはず)
                    logger.error("!!! CRITICAL: response_text is None or empty before sending !!!")
                    try: await message.reply(f"({call_name}さん、重大な内部エラーにより応答できませんでした。)", mention_author=False)
                    except Exception: pass

            # --- エラーハンドリング ---
            except genai_errors.APIError as e: # google.api_core.exceptions も含みうる
                 logger.error(f"Gemini API Error occurred for user {user_id}.", exc_info=True) # 詳細をログに
                 logger.error(f"Failed API call response object (if available): {response}") # ★ エラー時のレスポンスも記録
                 reply_msg = f"({call_name}さん、AIとの通信中にAPIエラーが発生しました。)"
                 if hasattr(e, 'message') and "API key not valid" in str(e.message):
                     reply_msg = f"({call_name}さん、エラー: APIキーが無効です。)"
                     logger.critical("Invalid Gemini API Key detected!")
                 finish_reason_in_error = getattr(e, 'finish_reason', None) # APIエラーに含まれる場合
                 if finish_reason_in_error:
                     logger.warning(f"Content generation stopped via APIError for user {user_id}. Reason: {finish_reason_in_error}", exc_info=False)
                     if finish_reason_in_error == genai_types.FinishReason.SAFETY: reply_msg = f"({call_name}さん、応答が安全性によりブロックされました(APIエラー経由))"
                     elif finish_reason_in_error == genai_types.FinishReason.RECITATION: reply_msg = f"({call_name}さん、応答が引用超過により停止しました(APIエラー経由))"
                     else: reply_msg = f"({call_name}さん、応答の生成が予期せず停止しました(APIエラー経由): {finish_reason_in_error})"
                 try:
                     await message.reply(reply_msg, mention_author=False) if not is_dm else await message.channel.send(reply_msg)
                 except discord.HTTPException:
                     logger.error("Failed to send API error message notification to Discord.")
            except discord.errors.NotFound:
                logger.warning(f"Message or channel not found (maybe deleted?). Message ID: {message.id}, Channel ID: {message.channel.id if message.channel else 'N/A'}")
            except Exception as e:
                # ★ 汎用エラーハンドラでも response オブジェクトをログ出力 ★
                logger.error(f"An unexpected error occurred during message processing for user {user_id}", exc_info=True)
                logger.error(f"Error occurred with response object (if available): {response}")
                reply_msg = f"({call_name}さん、メッセージ処理中に予期せぬエラーが発生しました: {type(e).__name__})"
                try:
                    await message.reply(reply_msg, mention_author=False) if not is_dm else await message.channel.send(reply_msg)
                except discord.HTTPException:
                    logger.error("Failed to send unexpected error message notification to Discord.")
            # finally ブロックは不要 (async with typing() が抜ける際に自動で解除されるはず)

# --- setup 関数 (変更なし) ---
async def setup(bot: commands.Bot):
    cog = ChatCog(bot)
    # 初期化を試行し、成功した場合のみCogを追加
    if cog.initialize_genai_client():
        await bot.add_cog(cog)
        logger.info("ChatCog setup complete and added to bot.")
    else:
        error_msg = "ChatCog setup failed because Gemini client could not be initialized (check API Key?). The Cog will not be loaded."
        logger.error(error_msg)
        # raise commands.ExtensionFailed(name="cogs.chat_cog", original=RuntimeError(error_msg))
# cogs/random_dm_cog.py (システムプロンプト修正)
import discord
from discord.ext import commands, tasks
import logging
import datetime
# from datetime import timezone # aware datetime を使うため不要
import asyncio
import random
from typing import Optional, List, Dict, Any
import os

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
        self.genai_client = None # 初期状態は None
        self.user_data_lock = config_manager.data_lock
        self.dm_sender_loop.start()
        logger.info("RandomDMCog loaded and task started.")

    def cog_unload(self):
        self.dm_sender_loop.cancel()
        logger.info("RandomDMCog unloaded and task stopped.")

    async def initialize_genai_client_if_needed(self):
        """ChatCogからGenAIクライアントを取得、なければ初期化を試みる"""
        if self.genai_client: return True # 既に初期化済み

        chat_cog = self.bot.get_cog("ChatCog")
        if chat_cog and chat_cog.genai_client:
            self.genai_client = chat_cog.genai_client
            logger.info("Using GenAI client from ChatCog for Random DM.")
            return True
        else:
            logger.warning("Could not get GenAI client from ChatCog. Attempting independent initialization for RandomDMCog...")
            try:
                api_key = os.getenv("GOOGLE_AI_KEY")
                if not api_key:
                     logger.error("GOOGLE_AI_KEY not found in environment variables. Random DM cannot use AI.")
                     return False
                # ChatCog と同じ方法で初期化
                self.genai_client = genai.Client(api_key=api_key)
                logger.info("Gemini client initialized independently for RandomDMCog.")
                return True
            except Exception as e:
                logger.error("Failed to initialize Gemini client independently for RandomDMCog", exc_info=e)
                self.genai_client = None
                return False

    async def reset_user_timer(self, user_id: int):
        user_id_str = str(user_id)
        logger.debug(f"Resetting Random DM timer in memory for user {user_id_str} due to interaction.")
        async with self.user_data_lock:
             # get_all_user_data を使わず直接 user_data を参照・更新
             if user_id_str in config_manager.user_data:
                 user_settings = config_manager.user_data[user_id_str].get("random_dm")
                 if user_settings and user_settings.get("enabled"):
                     now_aware = datetime.datetime.now().astimezone() # ローカルタイムゾーンを付与
                     user_settings["last_interaction"] = now_aware
                     user_settings["next_send_time"] = None # 次回送信時刻をリセット
                     logger.info(f"Random DM timer reset in memory for user {user_id}.")
                     # ★ ファイル保存はループに任せる (頻繁な書き込みを避ける)
                     # await config_manager.save_user_data_nolock() # ここでは保存しない
             else:
                  logger.debug(f"User {user_id_str} not found in user_data for timer reset.")


    @tasks.loop(seconds=10.0) # ループ間隔は環境に合わせて調整
    async def dm_sender_loop(self):
        # ★ ループ開始時にクライアント初期化を確認/試行
        if not await self.initialize_genai_client_if_needed():
             logger.warning("GenAI client not available in RandomDMCog, skipping loop iteration.")
             await asyncio.sleep(60) # クライアントがない場合は少し長めに待つ
             return

        now = datetime.datetime.now().astimezone() # タイムゾーン付きで現在時刻を取得
        logger.debug(f"Running dm_sender_loop at {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")

        users_to_dm: List[int] = []
        users_to_update_config: Dict[int, Dict[str, Any]] = {} # 更新が必要なユーザー設定

        # ★ ロックを取得して user_data を直接操作 ★
        async with self.user_data_lock:
            # user_data を直接イテレート (コピーしない方が最新の状態を反映しやすい)
            for user_id_str, u_data in config_manager.user_data.items():
                try:
                    user_id = int(user_id_str)
                    # get_default_random_dm_config はデフォルト値を返すだけなので毎回呼んでもOK
                    dm_config_current = u_data.get("random_dm", config_manager.get_default_random_dm_config())

                    if not dm_config_current.get("enabled"):
                        continue

                    # --- 停止時間のチェック ---
                    stop_start = dm_config_current.get("stop_start_hour")
                    stop_end = dm_config_current.get("stop_end_hour")
                    is_stopping_time = False
                    if stop_start is not None and stop_end is not None:
                        current_hour_local = now.hour
                        if stop_start > stop_end: # 日付をまたぐ場合 (例: 23時～7時)
                            is_stopping_time = (current_hour_local >= stop_start or current_hour_local < stop_end)
                        else: # 日付をまたがない場合 (例: 9時～17時)
                            is_stopping_time = (stop_start <= current_hour_local < stop_end)

                    if is_stopping_time:
                        logger.debug(f"Skip DM user {user_id} (stop time: {stop_start}-{stop_end} local)")
                        continue

                    # --- 送信タイミングの決定 ---
                    last_interact_dt = dm_config_current.get("last_interaction")
                    next_send_time_dt = dm_config_current.get("next_send_time")

                    # タイムゾーン処理: aware でなければローカルTZを付与、awareならローカルTZに変換
                    default_tz = now.tzinfo
                    if isinstance(last_interact_dt, datetime.datetime):
                         if last_interact_dt.tzinfo is None: last_interact_dt = last_interact_dt.replace(tzinfo=default_tz)
                         else: last_interact_dt = last_interact_dt.astimezone(default_tz)
                    else: # last_interaction がなければ、非常に古い時刻として扱う
                         last_interact_dt = datetime.datetime.min.replace(tzinfo=default_tz)

                    if isinstance(next_send_time_dt, datetime.datetime):
                         if next_send_time_dt.tzinfo is None: next_send_time_dt = next_send_time_dt.replace(tzinfo=default_tz)
                         else: next_send_time_dt = next_send_time_dt.astimezone(default_tz)

                    logger.debug(f"User {user_id}: Enabled={dm_config_current.get('enabled')}, LastInteract={last_interact_dt}, NextSend={next_send_time_dt}")

                    if next_send_time_dt is None:
                        # 次回送信時刻が未設定の場合、計算して設定
                        min_interval = dm_config_current.get("min_interval", 3600 * 6) # 秒単位
                        max_interval = dm_config_current.get("max_interval", 86400 * 2) # 秒単位
                        # last_interaction からランダムな間隔を空ける
                        interval_sec = random.uniform(min_interval, max_interval)
                        calculated_next_send = last_interact_dt + datetime.timedelta(seconds=interval_sec)
                        dm_config_current["next_send_time"] = calculated_next_send # メモリ上の設定を更新
                        users_to_update_config[user_id] = dm_config_current # 保存対象に追加
                        logger.info(f"Calculated next DM time for user {user_id}: {calculated_next_send.strftime('%Y-%m-%d %H:%M:%S %Z%z')} (Interval: {interval_sec:.0f}s)")
                        # このループ iteration では送信しない
                        continue

                    # 次回送信時刻が設定されていて、現在時刻がそれを過ぎていたら送信対象
                    if now >= next_send_time_dt:
                        logger.info(f"Time condition met for user {user_id}: now={now.strftime('%H:%M:%S')}, next_send_time={next_send_time_dt.strftime('%H:%M:%S')}")
                        users_to_dm.append(user_id)
                        # 送信したら次回時刻をリセットし、最終インタラクションを更新
                        dm_config_current["next_send_time"] = None
                        dm_config_current["last_interaction"] = now # ★DM送信もインタラクションとみなす
                        users_to_update_config[user_id] = dm_config_current # 保存対象に追加
                    # else: # まだ送信時刻でない場合
                    #    time_diff = next_send_time_dt - now
                    #    logger.debug(f"Time condition NOT met for user {user_id}. Send in {time_diff.total_seconds():.1f} seconds.")

                except Exception as e:
                    logger.error(f"Error processing user {user_id_str} in dm_sender_loop", exc_info=e)

            # --- ループ後にまとめて設定を保存 ---
            if users_to_update_config:
                logger.debug(f"Updating random DM configs in memory and file for {len(users_to_update_config)} users.")
                # update_random_dm_config_async を使うとロック内で再度ロックしようとする可能性があるので注意
                # ここでは直接 config_manager.user_data を更新したので、保存関数を呼ぶ
                try:
                    await config_manager.save_user_data_nolock() # ロックは既に取得済み
                    logger.debug("Finished updating random DM configs in file.")
                except Exception as e:
                     logger.error("Error during bulk save of random DM configs", exc_info=e)


        # --- DM送信処理 (ロック外で行う) ---
        if users_to_dm:
            history_cog: Optional[HistoryCog] = self.bot.get_cog("HistoryCog")
            if not history_cog:
                logger.error("HistoryCog not found! Cannot send random DMs requiring history.")
                return

            # 送信タスクを作成して並列実行
            send_tasks = [self.send_random_dm(user_id, history_cog) for user_id in users_to_dm]
            if send_tasks:
                await asyncio.gather(*send_tasks)


    async def send_random_dm(self, user_id: int, history_cog: HistoryCog):
        """指定ユーザーにランダムDMを送信する"""
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException) as e:
                logger.warning(f"Could not find/fetch user {user_id} for random DM: {e}")
                return

        if user.bot: # ボットには送信しない
            logger.info(f"Skipping random DM to bot user: {user.name} (ID: {user_id})")
            return

        logger.info(f"Attempting to send random DM to {user.display_name} (ID: {user_id})")

        try:
            # ★ DM送信前にパーミッションチェック (任意だが推奨)
            # member = self.bot.get_guild(SOME_GUILD_ID).get_member(user_id) # 共通サーバーのメンバーか確認
            # if member and not member.dm_channel: # DMチャンネルがなければ作成試行
            try:
                dm_channel = user.dm_channel or await user.create_dm()
            except discord.Forbidden:
                logger.warning(f"Cannot create DM channel for user {user_id}. DMs might be disabled.")
                return
            except discord.HTTPException as e:
                logger.error(f"Failed to create DM channel for user {user_id}", exc_info=e)
                return

            # --- プロンプトと設定の準備 ---
            dm_prompt_text_base = config_manager.get_random_dm_prompt()
            user_nickname = config_manager.get_nickname(user_id)
            user_representation = user.display_name
            call_name = user_nickname if user_nickname else user_representation # ★ 呼びかけ名

            # --- ★ システムプロンプトの改善 ★ ---
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

            sys_prompt_text += f"\n\n--- ★★★ 応答生成時の最重要指示 ★★★ ---"
            sys_prompt_text += f"\n1. **最優先事項:** これはあなたからの最初のDM、または久しぶりの声かけです。応答する際は、**必ず、絶対に「{call_name}」という名前で呼びかけてください。** 他の呼び方は**禁止**します。"
            sys_prompt_text += f"\n2. **厳禁:** 過去の会話履歴には、他のユーザーとの会話や、現在の相手と他のユーザーとの会話、サーバーチャンネルでの会話が含まれている可能性があります。これらの履歴は参考程度に留め、**今回のDMの内容は、現在の相手「{call_name}」さんとの新しい会話として自然なものにしてください。** 過去の他の会話に引きずられないように注意してください。"
            sys_prompt_text += f"\n3. **今回のあなたの発言指示:** 「{dm_prompt_text_base}」に基づき、フレンドリーで自然な最初のメッセージを作成してください。相手が返信しやすいような、オープンな質問を含めると良いでしょう。"
            sys_prompt_text += f"\n4. **厳禁:** あなたの応答の **いかなる部分にも** `[{self.bot.user.display_name}]:` や `[{call_name}]:` のような角括弧で囲まれた発言者名を含めてはいけません。あなたの応答は、会話本文のみで構成してください。"
            sys_prompt_text += f"\n5. 引用符 `[]` は使用禁止です。"
            sys_prompt_text += "\n----------------------------------------\n"

            system_instruction_content = genai_types.Content(parts=[genai_types.Part(text=sys_prompt_text)], role="system")
            # --- ★ システムプロンプト改善ここまで ★ ---

            # --- 履歴を準備 (ランダムDMでは履歴を少なくするか、含めない選択肢も？) ---
            # ここでは ChatCog と同様にグローバル履歴を使うが、件数を絞る等の工夫も可能
            history_list = await history_cog.get_global_history_for_prompt()
            logger.debug(f"Using global history (length: {len(history_list)}) for random DM context to {user_id}")

            # ★ DM の開始メッセージを User Role として追加（AIに会話の開始点を意識させる）
            # parts=[genai_types.Part(text=f"（{call_name} さんにDMを送る）{dm_prompt_text_base}")] のように内部メモ的にしても良いかも
            start_message_content = genai_types.Content(role="user", parts=[genai_types.Part(text=f"こんにちは、{call_name}さん！{dm_prompt_text_base}")])

            contents_for_api = []
            contents_for_api.extend(history_list)
            contents_for_api.append(start_message_content) # ★ 会話開始のメッセージを追加

            # --- Gemini API 呼び出し ---
            model_name = config_manager.get_model_name()
            generation_config_dict = config_manager.get_generation_config_dict()
            safety_settings_list = config_manager.get_safety_settings_list()
            safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]
            # ランダムDMでは検索不要かもしれないので tools は外す選択肢も？
            # tools_for_api = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
            tools_for_api = None # ★ DMではToolを使わない例

            final_generation_config = genai_types.GenerateContentConfig(
                 temperature=generation_config_dict.get('temperature', 0.9),
                 top_p=generation_config_dict.get('top_p', 1.0),
                 top_k=generation_config_dict.get('top_k', 1),
                 candidate_count=generation_config_dict.get('candidate_count', 1),
                 max_output_tokens=generation_config_dict.get('max_output_tokens', 1024), # DMなので短くても良いかも
                 safety_settings=safety_settings_for_api,
                 tools=tools_for_api, # ★ None に設定
                 system_instruction=system_instruction_content
            )

            logger.debug(f"Sending random DM request to Gemini. Model: {model_name}, History length: {len(history_list)}")
            if not contents_for_api:
                logger.error("Cannot send request to Gemini for random DM, contents_for_api is empty.")
                return

            response = self.genai_client.models.generate_content(
                model=model_name,
                contents=contents_for_api,
                config=final_generation_config
            )

            # --- 応答処理 & 送信 ---
            logger.debug(f"Gemini Response for random DM ({user_id}): FinishReason={response.candidates[0].finish_reason if response.candidates else 'N/A'}")

            response_text = ""
            response_parts = []
            if response and response.candidates:
                 candidate = response.candidates[0]
                 if candidate.content and candidate.content.parts:
                      response_parts = candidate.content.parts
                      for part in response_parts:
                           if hasattr(part, 'text') and part.text:
                                response_text += part.text
                 else: # 応答はあるが Parts がない場合 (ブロックなど)
                      finish_reason = candidate.finish_reason
                      logger.warning(f"No parts in candidate content for random DM to {user_id}. FinishReason: {finish_reason}")
                      # ブロック理由などに応じたメッセージを生成
                      block_reason_str="不明"; safety_reason="不明"
                      try:
                           if response.prompt_feedback: block_reason_str = str(response.prompt_feedback.block_reason or "理由なし")
                           if finish_reason == genai_types.FinishReason.SAFETY and candidate.safety_ratings:
                                safety_categories = [str(r.category) for r in candidate.safety_ratings if r.probability != genai_types.HarmProbability.NEGLIGIBLE]
                                safety_reason = f"安全性 ({', '.join(safety_categories)})" if safety_categories else "安全性"
                      except Exception: pass
                      if finish_reason == genai_types.FinishReason.SAFETY: response_text = f"(DMの内容が{safety_reason}によりブロックされました)"
                      elif finish_reason == genai_types.FinishReason.RECITATION: response_text = "(DMの内容が引用超過でブロックされました)"
                      elif block_reason_str != "不明" and block_reason_str != "理由なし": response_text = f"(DMのプロンプトが原因でブロックされました: {block_reason_str})"
                      else: response_text = f"(DM応答生成失敗: {finish_reason})"
            else:
                 logger.warning(f"No valid response or candidates for random DM to user {user_id}.")
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

            # 最大応答文字数制限 (DM用に短くしても良いかも)
            max_len = config_manager.get_max_response_length()
            if len(final_response_text) > max_len:
                logger.info(f"Random DM response length ({len(final_response_text)}) exceeded max length ({max_len}). Truncating.")
                final_response_text = final_response_text[:max_len - 3] + "..."
            elif len(final_response_text) == 0 and prefix_removed_count > 0:
                 logger.warning("Random DM response became empty after removing bot prefix(es). Not sending.")
                 final_response_text = None # 送信しない

            # --- 送信処理 ---
            if final_response_text:
                try:
                    await dm_channel.send(final_response_text)
                    logger.info(f"Sent random DM to {user.display_name} (ID: {user_id})")

                    # --- 履歴保存 (正常送信後) ---
                    if not final_response_text.startswith("("): # エラーメッセージは保存しない
                         logger.debug(f"Attempting to add random DM response to global history for user {user_id}")
                         def part_to_dict(part: genai_types.Part) -> dict:
                              data = {}; bot_prefix_local = f"[{self.bot.user.display_name}]:" # ローカル変数化
                              if hasattr(part, 'text') and part.text and part.text.strip():
                                   cleaned_part_text = part.text.strip()
                                   while cleaned_part_text.startswith(bot_prefix_local): cleaned_part_text = cleaned_part_text[len(bot_prefix_local):].strip()
                                   if cleaned_part_text: data['text'] = cleaned_part_text
                              # 他の Part タイプ処理省略
                              return data

                         # ★ Bot の応答を辞書化して保存 ★
                         bot_response_parts_dict_cleaned = [part_to_dict(part) for part in response_parts if part_to_dict(part)]
                         if bot_response_parts_dict_cleaned:
                              logger.debug(f"Calling add_history_entry_async (Global) for random DM response")
                              await history_cog.add_history_entry_async(
                                  current_interlocutor_id=user_id, channel_id=None, role="model",
                                  parts_dict=bot_response_parts_dict_cleaned, entry_author_id=self.bot.user.id
                              )
                              logger.info(f"Successfully added cleaned random DM response to global history for user {user_id}")
                         else: logger.warning(f"No valid parts to add to global history for random DM response to user {user_id}")

                except discord.Forbidden:
                    logger.warning(f"Cannot send random DM to {user_id}. DMs may be closed or Bot lacks permission.")
                    # ★ TODO: DM送信失敗した場合、ユーザー設定を無効にするか検討 ★
                    # async with self.user_data_lock:
                    #    if user_id_str in config_manager.user_data and "random_dm" in config_manager.user_data[user_id_str]:
                    #        config_manager.user_data[user_id_str]["random_dm"]["enabled"] = False
                    #        await config_manager.save_user_data_nolock()
                    #        logger.info(f"Disabled random DM for user {user_id} due to Forbidden error.")
                except discord.HTTPException as http_e:
                    logger.error(f"Failed to send DM to {user_id}", exc_info=http_e)
                except Exception as send_e:
                    logger.error(f"Unexpected error sending DM or adding history for {user_id}", exc_info=send_e)
            # else: # final_response_text が None または空になった場合
            #     logger.info(f"Skipped sending empty or error random DM to user {user_id}.")


        except genai_errors.APIError as e:
             logger.error(f"Gemini API Error during random DM preparation for {user_id}: Code={e.code if hasattr(e, 'code') else 'N/A'}, Message={e.message}", exc_info=False)
        except Exception as e:
             logger.error(f"Error preparing or sending random DM to user {user_id}", exc_info=e)


    @dm_sender_loop.before_loop
    async def before_dm_sender_loop(self):
        await self.bot.wait_until_ready()
        logger.info("Random DM sender loop is ready.")

# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(RandomDMCog(bot))
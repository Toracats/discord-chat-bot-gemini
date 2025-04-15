# cogs/summarize_cog.py (プロンプトから呼び名指示を削除)

import discord
from discord.ext import commands
import logging
import datetime
import os
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
import asyncio
from typing import Optional, Dict, Any, List
import json
import uuid
import re # ★ reモジュールは不要になったので削除してもOK (呼び名置換はchat/random_dm側で行うため)

from utils import config_manager

logger = logging.getLogger(__name__)

class SummarizeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client = None
        self.bot.loop.create_task(self.initialize_genai_client_from_chatcog())
        logger.info("SummarizeCog loaded.")

    async def initialize_genai_client_from_chatcog(self):
        """ChatCogが初期化された後にGenAIクライアントを取得する"""
        await self.bot.wait_until_ready()
        chat_cog = self.bot.get_cog("ChatCog")
        retries = 5
        await asyncio.sleep(1) # 少し待機
        while retries > 0:
            if chat_cog and chat_cog.genai_client:
                self.genai_client = chat_cog.genai_client; logger.info("Obtained GenAI client from ChatCog for SummarizeCog."); return
            else: logger.debug(f"ChatCog client not ready. Retrying in 5s... ({retries} left)"); retries -= 1; await asyncio.sleep(5)
        logger.error("Failed to obtain GenAI client from ChatCog for SummarizeCog.")

    # --- 呼び名取得ヘルパー関数 (DB保存用) ---
    def get_call_name_for_db(self, target_user_id: Optional[int]) -> str:
        """DB保存用にユーザーIDに対応する呼び名を取得する"""
        if target_user_id is None: return "(不明な相手)"
        if target_user_id == self.bot.user.id: return self.bot.user.display_name
        nickname = config_manager.get_nickname(target_user_id)
        if nickname: return nickname
        user = self.bot.get_user(target_user_id)
        if user: return user.display_name
        # ★ DB保存時は User ID 形式ではなく、単に「不明なユーザー」などにする方が後処理で楽かもしれない
        # return f"User {target_user_id}"
        return "(不明なユーザー)" # または ID を返すなら f"ID:{target_user_id}" など区別しやすい形式

    async def summarize_and_save_entry(self, history_entry: Dict[str, Any]):
        """指定された履歴エントリを要約/構造化して保存する"""
        entry_id = history_entry.get("entry_id", "unknown")
        logger.info(f"Starting summarization for history entry ID: {entry_id}")
        if not self.genai_client: logger.error(f"GenAI client not initialized. Cannot summarize {entry_id}."); return

        try:
            model_name = config_manager.get_summary_model_name()
            generation_config_dict = config_manager.get_summary_generation_config_dict()
            safety_settings_for_api = None

            # --- 要約指示プロンプト (呼び名指示を削除) ---
            original_timestamp_iso = "N/A"
            original_ts_dt = history_entry.get("timestamp")
            if isinstance(original_ts_dt, datetime.datetime): original_timestamp_iso = original_ts_dt.isoformat()
            speaker_id = history_entry.get("interlocutor_id"); opponent_id = history_entry.get('current_interlocutor_id'); role = history_entry.get("role"); channel_id = history_entry.get("channel_id")
            parts_text = " ".join([p.get("text", "") for p in history_entry.get("parts", []) if p.get("text")]).strip()

            input_context = f"""
元の発言者ロール: {role}
元の発言者ID: {speaker_id}
元の会話相手ID: {opponent_id}
元のチャンネルID: {channel_id if channel_id is not None else 'DM'}
元の発言タイムスタンプ: {original_timestamp_iso}
元の発言内容:
{parts_text}
"""
            # ★ プロンプトから呼び名に関する指示を削除 ★
            prompt = f"""以下のDiscord会話履歴エントリの情報を分析し、指定されたJSON形式で構造化して出力してください。

入力情報:
{input_context}

出力JSON形式:
{{
  "speaker_id": <元の発言者ID (整数)>,
  "original_timestamp": "<元の発言タイムスタンプ (ISO 8601形式文字列)>",
  "channel_id": <元のチャンネルID (整数) または null>,
  "summary_text": "<発言内容の要約 (簡潔に、誰が誰に何を言ったか分かるように)>"
}}

重要:
- `summary_text` は、元の発言内容の要点を簡潔に記述してください。**ユーザーIDを含めても構いません。**
- 出力は上記のJSON形式のみとし、他のテキストは含めないでください。

出力JSON:
"""
            logger.debug(f"Summary prompt for entry {entry_id}:\n{prompt}")

            # --- Gemini API 呼び出し (変更なし) ---
            final_summary_generation_config = genai_types.GenerateContentConfig(
                temperature=generation_config_dict.get('temperature', 0.5),
                top_p=generation_config_dict.get('top_p'), top_k=generation_config_dict.get('top_k'),
                candidate_count=1, max_output_tokens=generation_config_dict.get('max_output_tokens', 512),
                safety_settings=safety_settings_for_api,
            )
            response = self.genai_client.models.generate_content(
                model=model_name, contents=[prompt], config=final_summary_generation_config
            )

            summary_json_str = ""
            if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                 summary_json_str = response.text.strip()
                 if summary_json_str.startswith("```json"): summary_json_str = summary_json_str[7:]
                 if summary_json_str.endswith("```"): summary_json_str = summary_json_str[:-3]
                 summary_json_str = summary_json_str.strip(); logger.debug(f"Raw summary response entry {entry_id}: {summary_json_str}")
            else:
                feedback = response.prompt_feedback if response else 'No response object'; logger.error(f"Failed valid summary response Gemini entry {entry_id}. Feedback: {feedback}"); return

            # JSONパースとエントリ作成 (呼び名取得を get_call_name_for_db に変更)
            try:
                summary_data = json.loads(summary_json_str)
                if not isinstance(summary_data, dict): raise json.JSONDecodeError("Decoded data is not a dictionary", summary_json_str, 0)
                required_keys = ["speaker_id", "original_timestamp", "channel_id", "summary_text"]
                if not all(key in summary_data for key in required_keys): logger.error(f"Summary JSON missing required keys {entry_id}. Data: {summary_data}"); return
                # 型チェック (簡易)
                if not isinstance(summary_data.get("speaker_id"), int): logger.warning(f"Invalid type speaker_id {entry_id}"); summary_data["speaker_id"] = None
                if not isinstance(summary_data.get("summary_text"), str): logger.warning(f"Invalid type summary_text {entry_id}"); summary_data["summary_text"] = "(要約失敗)"
                if not isinstance(summary_data.get("original_timestamp"), str): logger.warning(f"Invalid/missing original_timestamp {entry_id}"); summary_data["original_timestamp"] = None
                if summary_data.get("channel_id") is not None and not isinstance(summary_data.get("channel_id"), int): logger.warning(f"Invalid type channel_id {entry_id}"); summary_data["channel_id"] = None

                # ★ DB保存用の呼び名を取得 ★
                final_speaker_call_name = self.get_call_name_for_db(summary_data.get("speaker_id"))

                final_summary_entry = { "summary_id": str(uuid.uuid4()), "added_timestamp": datetime.datetime.now().astimezone(), "original_timestamp": summary_data["original_timestamp"], "speaker_id": summary_data["speaker_id"], "speaker_call_name_at_summary": final_speaker_call_name, "channel_id": summary_data["channel_id"], "summary_text": summary_data["summary_text"] } # ★ summary_textはGeminiが生成したまま
                await config_manager.append_summary(final_summary_entry)
                logger.info(f"Summarized and saved entry {entry_id} as summary {final_summary_entry['summary_id']}")

            except json.JSONDecodeError as json_e: logger.error(f"Decode summary JSON failed {entry_id}. Error: {json_e}. Response: {summary_json_str}")
            except Exception as e: logger.error(f"Error processing summary response {entry_id}", exc_info=e)

        # --- 例外処理 (変更なし) ---
        except genai_errors.StopCandidateException as e: logger.warning(f"Summarization stopped {entry_id}. Reason: {e.finish_reason}")
        except genai_errors.APIError as e: logger.error(f"Gemini APIError during summarization {entry_id}", exc_info=e)
        except Exception as e: logger.error(f"Unexpected error during summarization {entry_id}", exc_info=e)


# Cogセットアップ関数 (変更なし)
async def setup(bot: commands.Bot):
    await bot.add_cog(SummarizeCog(bot))
    logger.info("SummarizeCog setup complete.")
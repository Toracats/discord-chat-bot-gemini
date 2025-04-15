# cogs/summarize_cog.py (API呼び出しを chat_cog に合わせる)

import discord
from discord.ext import commands
import logging
import datetime
import os
from google import genai
from google.genai import types as genai_types # ★ types をインポート
from google.genai import errors as genai_errors
import asyncio
from typing import Optional, Dict, Any, List
import json
import uuid

from utils import config_manager

logger = logging.getLogger(__name__)

class SummarizeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client = None
        self.bot.loop.create_task(self.initialize_genai_client_from_chatcog())
        logger.info("SummarizeCog loaded.")

    async def initialize_genai_client_from_chatcog(self):
        # (変更なし)
        await self.bot.wait_until_ready()
        chat_cog = self.bot.get_cog("ChatCog")
        retries = 5
        while retries > 0:
            if chat_cog and chat_cog.genai_client:
                self.genai_client = chat_cog.genai_client; logger.info("Obtained GenAI client from ChatCog for SummarizeCog."); return
            else: logger.debug(f"ChatCog client not ready. Retrying in 5s... ({retries} left)"); retries -= 1; await asyncio.sleep(5)
        logger.error("Failed to obtain GenAI client from ChatCog for SummarizeCog.")

    async def summarize_and_save_entry(self, history_entry: Dict[str, Any]):
        """指定された履歴エントリを要約/構造化して保存する"""
        entry_id = history_entry.get("entry_id", "unknown")
        logger.info(f"Starting summarization for history entry ID: {entry_id}")
        if not self.genai_client: logger.error(f"GenAI client not initialized. Cannot summarize {entry_id}."); return

        try:
            model_name = config_manager.get_summary_model_name()
            generation_config_dict = config_manager.get_summary_generation_config_dict()
            # ★ 要約用のSafety Settingsは設定しない (None) とする例 ★
            #   必要であれば config_manager から取得・設定する
            safety_settings_for_api = None

            # --- 要約指示プロンプト (変更なし) ---
            original_timestamp_iso = "N/A"
            original_ts_dt = history_entry.get("timestamp")
            if isinstance(original_ts_dt, datetime.datetime): original_timestamp_iso = original_ts_dt.isoformat()
            speaker_id = history_entry.get("interlocutor_id"); role = history_entry.get("role"); channel_id = history_entry.get("channel_id")
            parts_text = " ".join([p.get("text", "") for p in history_entry.get("parts", []) if p.get("text")]).strip()
            input_context = f"""
元の発言者ロール: {role}
元の発言者ID: {speaker_id}
元の会話相手ID: {history_entry.get('current_interlocutor_id')}
元のチャンネルID: {channel_id if channel_id is not None else 'DM'}
元の発言タイムスタンプ: {original_timestamp_iso}
元の発言内容:
{parts_text}
"""
            prompt = f"""以下のDiscord会話履歴エントリの情報を分析し、指定されたJSON形式で構造化して出力してください。

入力情報:
{input_context}

出力JSON形式:
{{
  "speaker_id": <元の発言者ID (整数)>,
  "original_timestamp": "<元の発言タイムスタンプ (ISO 8601形式文字列)>",
  "channel_id": <元のチャンネルID (整数) または null>,
  "summary_text": "<発言内容の要約 (簡潔に)>"
}}

重要:
- `summary_text` は、元の発言内容の要点を、誰が誰に何を言ったかが分かるように簡潔に記述してください。元の発言内容をそのままコピーしないでください。
- 出力は上記のJSON形式のみとし、他のテキストは含めないでください。

出力JSON:
"""
            logger.debug(f"Summary prompt for entry {entry_id}:\n{prompt}")

            # --- ★★★ Gemini API 呼び出し (chat_cog に合わせる) ★★★ ---
            # genai_types.GenerateContentConfig を使用
            final_summary_generation_config = genai_types.GenerateContentConfig(
                temperature=generation_config_dict.get('temperature', 0.5),
                top_p=generation_config_dict.get('top_p'),
                top_k=generation_config_dict.get('top_k'),
                candidate_count=1, # 要約なので1つ
                max_output_tokens=generation_config_dict.get('max_output_tokens', 512),
                safety_settings=safety_settings_for_api, # None または緩い設定
                # 要約なので system_instruction や tools は不要
            )

            #【GenAI呼び出し箇所 1/1】要約生成
            # ★★★ 同期メソッド generate_content を使用 ★★★
            #     非同期タスク内で同期メソッドを呼び出す形になる
            response = self.genai_client.models.generate_content(
                model=model_name,
                contents=[prompt], # 要約プロンプトのみ
                config=final_summary_generation_config # ★ config 引数を使用
            )
            # ---------------------------------------------------------

            summary_json_str = ""
            if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                 summary_json_str = response.text.strip()
                 # ```json ... ``` 除去 (変更なし)
                 if summary_json_str.startswith("```json"): summary_json_str = summary_json_str[7:]
                 if summary_json_str.endswith("```"): summary_json_str = summary_json_str[:-3]
                 summary_json_str = summary_json_str.strip(); logger.debug(f"Raw summary response for entry {entry_id}: {summary_json_str}")
            else:
                feedback = response.prompt_feedback if response else 'No response object'; logger.error(f"Failed valid summary response Gemini entry {entry_id}. Feedback: {feedback}"); return

            # JSONパースとエントリ作成 (変更なし)
            try:
                summary_data = json.loads(summary_json_str)
                if not isinstance(summary_data, dict): raise json.JSONDecodeError("Decoded data is not a dictionary", summary_json_str, 0)
                required_keys = ["speaker_id", "original_timestamp", "channel_id", "summary_text"]
                if not all(key in summary_data for key in required_keys): logger.error(f"Summary JSON missing required keys {entry_id}. Data: {summary_data}"); return
                if not isinstance(summary_data["speaker_id"], int): logger.warning(f"Invalid type speaker_id {entry_id}"); summary_data["speaker_id"] = None
                if not isinstance(summary_data["summary_text"], str): logger.warning(f"Invalid type summary_text {entry_id}"); summary_data["summary_text"] = "(要約失敗)"
                if not isinstance(summary_data.get("original_timestamp"), str): logger.warning(f"Invalid/missing original_timestamp {entry_id}"); summary_data["original_timestamp"] = None
                if summary_data.get("channel_id") is not None and not isinstance(summary_data.get("channel_id"), int): logger.warning(f"Invalid type channel_id {entry_id}"); summary_data["channel_id"] = None

                speaker_call_name = "(不明)"
                if summary_data["speaker_id"] is not None:
                    speaker_call_name = config_manager.get_nickname(summary_data["speaker_id"])
                    if not speaker_call_name: user = self.bot.get_user(summary_data["speaker_id"]); speaker_call_name = user.display_name if user else f"User {summary_data['speaker_id']}"

                final_summary_entry = { "summary_id": str(uuid.uuid4()), "added_timestamp": datetime.datetime.now().astimezone(), "original_timestamp": summary_data["original_timestamp"], "speaker_id": summary_data["speaker_id"], "speaker_call_name_at_summary": speaker_call_name, "channel_id": summary_data["channel_id"], "summary_text": summary_data["summary_text"] }
                await config_manager.append_summary(final_summary_entry)
                logger.info(f"Summarized and saved entry {entry_id} as summary {final_summary_entry['summary_id']}")

            except json.JSONDecodeError as json_e: logger.error(f"Decode summary JSON failed {entry_id}. Error: {json_e}. Response: {summary_json_str}")
            except Exception as e: logger.error(f"Error processing summary response {entry_id}", exc_info=e)

        # ★★★ 例外処理を chat_cog に合わせる ★★★
        except genai_errors.StopCandidateException as e: # StopCandidateException を捕捉
            logger.warning(f"Summarization stopped for entry {entry_id}. Reason: {e.finish_reason}")
        except genai_errors.APIError as e: # APIError を捕捉
            logger.error(f"Gemini APIError during summarization for entry {entry_id}", exc_info=e)
        except Exception as e: # その他の予期せぬエラー
            logger.error(f"Unexpected error during summarization for entry {entry_id}", exc_info=e)


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(SummarizeCog(bot))
    logger.info("SummarizeCog setup complete.")
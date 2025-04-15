# cogs/summarize_cog.py (新規作成)

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
import json # 要約プロンプト用
import uuid

# config_manager や genai 関連をインポート
from utils import config_manager

logger = logging.getLogger(__name__)

class SummarizeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.genai_client = None # 初期状態は None
        # ★ ChatCogからクライアントを取得することを試みる (Bot起動後)
        self.bot.loop.create_task(self.initialize_genai_client_from_chatcog())
        logger.info("SummarizeCog loaded.")

    async def initialize_genai_client_from_chatcog(self):
        """ChatCogが初期化された後にGenAIクライアントを取得する"""
        await self.bot.wait_until_ready() # Bot全体の準備完了を待つ
        chat_cog = self.bot.get_cog("ChatCog")
        retries = 5
        while retries > 0:
            if chat_cog and chat_cog.genai_client:
                self.genai_client = chat_cog.genai_client
                logger.info("Successfully obtained GenAI client from ChatCog for SummarizeCog.")
                return
            else:
                logger.debug(f"ChatCog or its genai_client not ready yet for SummarizeCog. Retrying in 5 seconds... ({retries} left)")
                retries -= 1
                await asyncio.sleep(5)
        logger.error("Failed to obtain GenAI client from ChatCog for SummarizeCog after multiple retries.")


    async def summarize_and_save_entry(self, history_entry: Dict[str, Any]):
        """指定された履歴エントリを要約/構造化して保存する"""
        entry_id = history_entry.get("entry_id", "unknown")
        logger.info(f"Starting summarization for history entry ID: {entry_id}")

        if not self.genai_client:
            logger.error(f"SummarizeCog's genai_client is not initialized. Cannot summarize entry {entry_id}.")
            return

        try:
            # 要約用モデルと設定を取得
            model_name = config_manager.get_summary_model_name()
            generation_config_dict = config_manager.get_summary_generation_config_dict()
            # 要約にはSafety Settingsは緩めるか、設定しないことも検討
            # safety_settings_list = config_manager.get_safety_settings_list()
            # safety_settings_for_api = [genai_types.SafetySetting(**s) for s in safety_settings_list]
            safety_settings_for_api = None # または緩い設定

            # ★ 要約指示プロンプト (5W1H + α) ★
            #    元のエントリ情報をJSON形式で渡す
            #    元のタイムスタンプも明示的に含める
            original_timestamp_iso = "N/A"
            original_ts_dt = history_entry.get("timestamp")
            if isinstance(original_ts_dt, datetime.datetime):
                 original_timestamp_iso = original_ts_dt.isoformat()

            speaker_id = history_entry.get("interlocutor_id")
            role = history_entry.get("role")
            channel_id = history_entry.get("channel_id")
            parts_text = " ".join([p.get("text", "") for p in history_entry.get("parts", []) if p.get("text")]).strip()

            # Gemini に処理させやすいように、入力情報を整理
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

            # --- Gemini API 呼び出し ---
            #【GenAI呼び出し箇所 1/1】要約生成
            response = await self.genai_client.generate_content_async( # ★ generate_content_async を使用
                model=model_name,
                contents=[prompt], # 単純なテキストプロンプトとして送信
                generation_config=genai_types.GenerationConfig(
                    temperature=generation_config_dict.get('temperature', 0.5),
                    # top_p, top_k なども必要なら設定
                    max_output_tokens=generation_config_dict.get('max_output_tokens', 512),
                    # 要約なので候補は1つで良い
                    candidate_count=1,
                    # レスポンス形式をJSONに強制 (対応モデル/モードの場合)
                    # response_mime_type="application/json", # 必要に応じて
                ),
                safety_settings=safety_settings_for_api
            )

            summary_json_str = ""
            if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                 summary_json_str = response.text.strip()
                 # レスポンスが ```json ... ``` で囲まれている場合を考慮
                 if summary_json_str.startswith("```json"):
                     summary_json_str = summary_json_str[7:]
                 if summary_json_str.endswith("```"):
                     summary_json_str = summary_json_str[:-3]
                 summary_json_str = summary_json_str.strip()
                 logger.debug(f"Raw summary response for entry {entry_id}: {summary_json_str}")
            else:
                logger.error(f"Failed to get valid summary response from Gemini for entry {entry_id}. Reason: {response.prompt_feedback if response else 'No response'}")
                return

            # JSONとしてパース試行
            try:
                summary_data = json.loads(summary_json_str)
                if not isinstance(summary_data, dict):
                     raise json.JSONDecodeError("Decoded data is not a dictionary", summary_json_str, 0)

                # 必須キーの存在確認と型チェック（より厳密に）
                required_keys = ["speaker_id", "original_timestamp", "channel_id", "summary_text"]
                if not all(key in summary_data for key in required_keys):
                     logger.error(f"Summary JSON missing required keys for entry {entry_id}. Data: {summary_data}")
                     return
                if not isinstance(summary_data["speaker_id"], int): logger.warning(f"Invalid type for speaker_id in summary {entry_id}"); summary_data["speaker_id"] = None # 不正ならNone
                if not isinstance(summary_data["summary_text"], str): logger.warning(f"Invalid type for summary_text in summary {entry_id}"); summary_data["summary_text"] = "(要約抽出失敗)"

                # 呼び名を取得
                speaker_call_name = config_manager.get_nickname(summary_data["speaker_id"])
                if not speaker_call_name:
                     user = self.bot.get_user(summary_data["speaker_id"])
                     speaker_call_name = user.display_name if user else f"User {summary_data['speaker_id']}"

                # 最終的な要約エントリを作成
                final_summary_entry = {
                    "summary_id": str(uuid.uuid4()),
                    "added_timestamp": datetime.datetime.now().astimezone(),
                    "original_timestamp": summary_data["original_timestamp"], # Geminiから返された文字列をそのまま使うか、再度パースして格納
                    "speaker_id": summary_data["speaker_id"],
                    "speaker_call_name_at_summary": speaker_call_name,
                    "channel_id": summary_data["channel_id"], # Geminiがnullを返せるようにプロンプトで指示
                    "summary_text": summary_data["summary_text"]
                }

                # 要約DBに追記
                await config_manager.append_summary(final_summary_entry)
                logger.info(f"Successfully summarized and saved entry {entry_id} as summary {final_summary_entry['summary_id']}")

            except json.JSONDecodeError as json_e:
                 logger.error(f"Failed to decode summary JSON response for entry {entry_id}. Error: {json_e}. Response: {summary_json_str}")
            except Exception as e:
                 logger.error(f"Error processing summary response for entry {entry_id}", exc_info=e)

        except genai_errors.StopCandidateException as e:
            logger.warning(f"Summarization stopped for entry {entry_id}. Reason: {e.finish_reason}")
        except genai_errors.APIError as e:
            logger.error(f"Gemini API Error during summarization for entry {entry_id}: Code={e.code if hasattr(e, 'code') else 'N/A'}, Message={e.message}", exc_info=False)
        except Exception as e:
            logger.error(f"Unexpected error during summarization for entry {entry_id}", exc_info=e)


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    # ★ SummarizeCog をロードリストに追加する必要はない（HistoryCog から利用されるため）
    #    ただし、インスタンス化は必要
    await bot.add_cog(SummarizeCog(bot))
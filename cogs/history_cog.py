# cogs/history_cog.py (要約Cog呼び出し、要約DBクリアコマンド追加)

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List, Optional, Dict, Any, Deque # ★ Deque をインポート
from collections import deque
import os
import asyncio
import datetime # datetime をインポート

# config_manager や genai.types などをインポート
from utils import config_manager
from google.genai import types as genai_types
from cogs.summarize_cog import SummarizeCog # ★ SummarizeCog をインポート

logger = logging.getLogger(__name__)

# --- 削除確認用の View (変更なし) ---
class ConfirmClearView(discord.ui.View):
    def __init__(self, *, timeout=30.0):
        super().__init__(timeout=timeout)
        self.confirmed: Optional[bool] = None

    @discord.ui.button(label="削除実行", style=discord.ButtonStyle.danger, custom_id="confirm_clear")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for item in self.children:
            if isinstance(item, discord.ui.Button): item.disabled = True
        try:
            if not interaction.response.is_done(): await interaction.response.edit_message(content="削除を実行します...", view=self)
        except discord.NotFound: logger.warning("Original confirmation message not found on confirm.")
        except discord.HTTPException as e: logger.error("Failed to edit confirmation message on confirm.", exc_info=e)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="cancel_clear")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for item in self.children:
             if isinstance(item, discord.ui.Button): item.disabled = True
        try:
            if not interaction.response.is_done(): await interaction.response.edit_message(content="キャンセルしました。", view=self)
        except discord.NotFound: logger.warning("Original confirmation message not found on cancel.")
        except discord.HTTPException as e: logger.error("Failed to edit confirmation message on cancel.", exc_info=e)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
             if isinstance(item, discord.ui.Button): item.disabled = True
        logger.info("Confirm clear view timed out.")
        # タイムアウトメッセージ編集は呼び出し元で

# --- History Cog 本体 ---
class HistoryCog(commands.Cog):
    history_commands = app_commands.Group(name="history", description="会話履歴の管理")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("HistoryCog loaded.")

    async def get_global_history_for_prompt(self) -> List[genai_types.Content]:
        """グローバル履歴をAPIリクエスト用に整形し、発言者情報を付与して返す"""
        logger.debug(f"Getting global history for prompt")
        # ★ deque 本体を取得 (コピーしない) ★
        history_deque = config_manager.get_global_history()
        content_history = []
        if not history_deque:
            logger.debug(f"No global history found")
            return content_history
        logger.debug(f"Raw global history deque (len={len(history_deque)})") # ★ ログ修正

        bot_name = self.bot.user.display_name if self.bot.user else "Bot"

        # ★ dequeをそのままイテレート ★
        for entry in history_deque:
            try:
                role = entry.get("role")
                entry_parts = entry.get("parts", [])
                interlocutor_id = entry.get("interlocutor_id")

                if not role or not entry_parts or role not in ["user", "model"] or interlocutor_id is None:
                    logger.warning(f"Skip invalid global history entry (missing essential info): {entry.get('entry_id')}")
                    continue

                speaker_name = f"User {interlocutor_id}" # デフォルト
                if role == "model":
                    speaker_name = bot_name
                elif role == "user":
                    nickname = config_manager.get_nickname(interlocutor_id)
                    if nickname: speaker_name = nickname
                    else:
                        user = self.bot.get_user(interlocutor_id) # キャッシュから取得試行
                        if user: speaker_name = user.display_name
                        # else: logger.warning(f"Could not find user {interlocutor_id} in cache for history speaker name.")

                context_prefix = f"[{speaker_name}]: "
                processed_parts = []
                first_part = True
                for part_dict in entry_parts:
                    if 'text' in part_dict and isinstance(part_dict['text'], str) and part_dict['text'].strip():
                        text_content = part_dict['text'].strip()
                        # ★ プレフィックスは最初のテキストパートにのみ追加 ★
                        final_text = context_prefix + text_content if first_part else text_content
                        processed_parts.append(genai_types.Part(text=final_text))
                        first_part = False
                    elif 'inline_data' in part_dict: logger.warning("Skipping inline_data restoration in history formatting.")
                    elif 'function_call' in part_dict and isinstance(part_dict['function_call'], dict):
                        fc_data = part_dict['function_call']
                        if 'name' in fc_data and 'args' in fc_data:
                             processed_parts.append(genai_types.Part(function_call=genai_types.FunctionCall(name=fc_data.get('name'), args=fc_data.get('args'))))
                             first_part = False # ★ プレフィックス不要フラグ
                        else: logger.warning(f"Skipping invalid function_call dict in history: {entry.get('entry_id')}")
                    elif 'function_response' in part_dict and isinstance(part_dict['function_response'], dict):
                         fr_data = part_dict['function_response'];
                         if 'name' in fr_data and 'response' in fr_data:
                              processed_parts.append(genai_types.Part(function_response=genai_types.FunctionResponse(name=fr_data.get('name'), response=fr_data.get('response'))))
                              first_part = False # ★ プレフィックス不要フラグ
                         else: logger.warning(f"Skipping invalid function_response dict in history: {entry.get('entry_id')}")
                    else: logger.warning(f"Skipping unrecognized part dict in history: {entry.get('entry_id')}")

                if processed_parts:
                    content_history.append(genai_types.Content(role=role, parts=processed_parts))
                else:
                    logger.warning(f"No valid parts constructed for history entry: {entry.get('entry_id')}")
            except Exception as e:
                logger.error(f"Error converting global history entry dict: {entry.get('entry_id')}", exc_info=e)

        logger.debug(f"Formatted global history for prompt (returned {len(content_history)} entries)")
        return content_history

    async def add_history_entry_async(self, current_interlocutor_id: int, channel_id: Optional[int], role: str, parts_dict: List[Dict[str, Any]], entry_author_id: int):
        """グローバル会話履歴にエントリを追加・保存し、必要なら要約タスクを起動する"""
        if role not in ["user", "model"]: logger.error(f"Invalid role '{role}'"); return
        logger.debug(f"Adding history async (Global) - Current interlocutor: {current_interlocutor_id}, Channel: {channel_id}, Role: {role}, Entry author: {entry_author_id}")
        try:
            # ★ config_manager の関数を呼び出し、押し出されたエントリを取得 ★
            pushed_out_entry = await config_manager.add_history_entry_async(
                current_interlocutor_id=current_interlocutor_id,
                channel_id=channel_id,
                role=role,
                parts_dict=parts_dict,
                entry_author_id=entry_author_id
            )

            # ★ 押し出されたエントリがあれば要約タスクを起動 ★
            if pushed_out_entry:
                logger.info(f"History entry {pushed_out_entry.get('entry_id')} pushed out, initiating summarization.")
                summarize_cog: Optional[SummarizeCog] = self.bot.get_cog("SummarizeCog")
                if summarize_cog:
                    # 非同期タスクとして実行 (完了を待たない)
                    asyncio.create_task(
                        summarize_cog.summarize_and_save_entry(pushed_out_entry),
                        name=f"summarize_{pushed_out_entry.get('entry_id', 'unknown')}"
                    )
                    logger.debug(f"Created background task to summarize entry {pushed_out_entry.get('entry_id')}")
                else:
                    logger.error("SummarizeCog not found! Cannot summarize pushed out history entry.")

        except Exception as e:
            logger.error(f"Error calling config_manager.add_history_entry_async or initiating summarization", exc_info=e)

    # --- 履歴操作コマンド ---
    @history_commands.command(name="clear", description="会話履歴または要約履歴を削除します")
    @app_commands.describe(
        type="削除する範囲 (all, user, channel, my, summary)", # ★ summary を追加
        target_user="ユーザー関連削除の場合に対象ユーザーを指定",
        target_channel="チャンネル削除の場合に対象チャンネルを指定",
        password="全削除の場合に必要なパスワード"
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="All (全体会話履歴)", value="all"),
        app_commands.Choice(name="User (指定ユーザー関連の会話履歴)", value="user"),
        app_commands.Choice(name="Channel (指定チャンネルの会話履歴)", value="channel"),
        app_commands.Choice(name="My (自分関連の会話履歴)", value="my"),
        app_commands.Choice(name="Summary (要約履歴全体)", value="summary"), # ★ summary を追加
    ])
    async def history_clear(self,
                            interaction: discord.Interaction,
                            type: str,
                            target_user: Optional[discord.User] = None,
                            target_channel: Optional[discord.TextChannel] = None,
                            password: Optional[str] = None
                            ):
        """会話履歴または要約履歴を削除するスラッシュコマンド"""
        await interaction.response.defer(ephemeral=True)
        clear_type = type.lower()

        valid_types = ["all", "user", "channel", "my", "summary"] # ★ summary を追加
        if clear_type not in valid_types:
             await interaction.followup.send(f"無効な `type` です。{', '.join(valid_types)} のいずれかを指定してください。", ephemeral=True)
             return

        try:
            edit_kwargs = {"content": "", "view": None}
            view = None # view を初期化

            if clear_type == "all":
                required_password = os.getenv("DELETE_HISTORY_PASSWORD")
                if not required_password: await interaction.followup.send("❌ 全履歴削除用パスワード未設定。", ephemeral=True); return
                if password is None: await interaction.followup.send("⚠️ **全履歴削除**には`password`引数要。", ephemeral=True); return
                if password != required_password: await interaction.followup.send("❌ パスワード不一致。", ephemeral=True); return

                # ★ 確認メッセージを修正 ★
                view = ConfirmClearView(timeout=30.0); msg_content = "⚠️ **本当にすべての会話履歴 *および* 要約履歴を削除しますか？元に戻せません！**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    # ★ 会話履歴の削除 ★
                    await config_manager.clear_all_history_async()
                    logger.warning(f"All conversation history cleared by {interaction.user}")

                    # ★★★ 要約履歴の削除処理を追加 ★★★
                    cleared_summary_ok = await config_manager.clear_summaries()
                    if cleared_summary_ok:
                        logger.warning(f"All summary history cleared by {interaction.user}")
                        edit_kwargs["content"] = "✅ すべての会話履歴と要約履歴を削除しました。" # ★ 成功メッセージ修正
                    else:
                        logger.error("Failed to clear summary history during 'clear all' command.")
                        edit_kwargs["content"] = "✅ 会話履歴は削除しましたが、要約履歴の削除中にエラーが発生しました。" # ★ 一部失敗メッセージ

                elif view.confirmed is False:
                    edit_kwargs["content"] = "キャンセルしました。"
                # タイムアウト時のメッセージは view.wait() の後で設定 (変更なし

            elif clear_type == "user":
                 # (変更なし - 特定ユーザー関連の会話履歴削除)
                if target_user is None: await interaction.followup.send("`type=user`の場合`target_user`要。", ephemeral=True); return
                view = ConfirmClearView(timeout=30.0); msg_content = f"⚠️ **本当にユーザー {target_user.mention} 関連の会話履歴削除？**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_count = await config_manager.clear_user_history_async(target_user.id)
                    edit_kwargs["content"] = f"✅ ユーザー {target_user.mention} 関連の会話履歴 ({cleared_count}件) 削除。"
                    logger.info(f"Cleared global history entries involving user {target_user.id} by {interaction.user}")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"

            elif clear_type == "channel":
                # (変更なし - 特定チャンネルの会話履歴削除)
                target = target_channel or interaction.channel
                if not isinstance(target, discord.TextChannel): await interaction.followup.send("チャンネル会話履歴削除はサーバーのテキストチャンネルのみ指定可能です。", ephemeral=True); return
                view = ConfirmClearView(timeout=30.0); msg_content = f"⚠️ **本当にチャンネル {target.mention} の会話履歴削除？**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_count = await config_manager.clear_channel_history_async(target.id)
                    edit_kwargs["content"] = f"✅ チャンネル {target.mention} の会話履歴 ({cleared_count}件) 削除。"
                    logger.info(f"Cleared global history entries for channel {target.id} by {interaction.user}")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"

            elif clear_type == "my":
                # (変更なし - 自分の会話履歴削除)
                user_to_clear = interaction.user
                view = ConfirmClearView(timeout=30.0); msg_content = f"⚠️ **本当にあなた ({user_to_clear.mention}) が関与する会話履歴削除？**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_count = await config_manager.clear_user_history_async(user_to_clear.id)
                    edit_kwargs["content"] = f"✅ あなたが関与する会話履歴 ({cleared_count}件) 削除。"
                    logger.info(f"User {interaction.user} cleared global history entries involving themselves.")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"

            # ★ 要約履歴の削除処理を追加 ★
            elif clear_type == "summary":
                view = ConfirmClearView(timeout=30.0); msg_content = "⚠️ **本当にすべての要約履歴を削除しますか？元に戻せません！**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_ok = await config_manager.clear_summaries()
                    if cleared_ok:
                        edit_kwargs["content"] = "✅ すべての要約履歴を削除しました。"
                        logger.warning(f"All summary history cleared by {interaction.user}")
                    else:
                        edit_kwargs["content"] = "❌ 要約履歴の削除中にエラーが発生しました。"
                elif view.confirmed is False:
                    edit_kwargs["content"] = "キャンセルしました。"

             # --- 最終的な応答を編集 ---
            # (変更なし)
            if interaction.response.is_done():
                 try:
                     if view and view.confirmed is None: edit_kwargs["content"] = "タイムアウトしました。"
                     await interaction.edit_original_response(**edit_kwargs)
                 except discord.NotFound: logger.warning("Original interaction response not found for clear status.")
                 except discord.HTTPException as e: logger.error("Failed edit final clear status message.", exc_info=e)
            else:
                 logger.warning("Interaction not done after ConfirmClearView wait, sending followup.");
                 if view and view.confirmed is None: edit_kwargs["content"] = "タイムアウトしました." # 句点追加
                 await interaction.followup.send(edit_kwargs["content"], ephemeral=True)

        except Exception as e:
            # (エラーハンドリング部分は変更なし)
            logger.error(f"Error in /history clear (type={clear_type})", exc_info=e)
            error_msg = f"履歴の削除中にエラーが発生しました: {e}"
            try: await interaction.followup.send(error_msg, ephemeral=True)
            except discord.NotFound: logger.warning("Original interaction response not found on error.")
            except discord.HTTPException: logger.error("Failed send error message history clear.")


    @history_commands.command(name="set_length", description="保持する直近の会話履歴の最大件数を設定します")
    @app_commands.describe(length="保持する最大件数 (0以上)")
    async def history_set_length(self, interaction: discord.Interaction, length: int):
        await interaction.response.defer(ephemeral=True);
        if length < 0: await interaction.followup.send("履歴保持件数は0以上の整数で指定してください。", ephemeral=True); return
        try:
            await config_manager.update_max_history_async(length)
            await interaction.followup.send(f"直近の会話履歴の最大保持件数を `{length}` 件に設定しました。", ephemeral=True);
            logger.info(f"Max history length set to {length} by {interaction.user}")
        except Exception as e:
            logger.error("Error in /history set_length", exc_info=e);
            await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @history_commands.command(name="show_length", description="現在の直近の会話履歴の最大保持件数を表示します")
    async def history_show_length(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True);
        try:
            length = config_manager.get_max_history();
            await interaction.followup.send(f"現在の直近の会話履歴の最大保持件数は `{length}` 件です。", ephemeral=True)
        except Exception as e:
            logger.error("Error in /history show_length", exc_info=e);
            await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCog(bot))
    logger.info("HistoryCog setup complete.")
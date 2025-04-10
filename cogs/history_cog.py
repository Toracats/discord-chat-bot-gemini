import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List, Optional, Dict, Any
from collections import deque
import os
import asyncio
import datetime # datetime をインポート

# config_manager や genai.types などをインポート
from utils import config_manager
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

# --- 削除確認用の View ---
class ConfirmClearView(discord.ui.View):
    def __init__(self, *, timeout=30.0):
        super().__init__(timeout=timeout)
        self.confirmed: Optional[bool] = None # None: Timeout, True: Confirm, False: Cancel

    @discord.ui.button(label="削除実行", style=discord.ButtonStyle.danger, custom_id="confirm_clear")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for item in self.children:
            if isinstance(item, discord.ui.Button): item.disabled = True
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="削除を実行します...", view=self)
        except discord.NotFound: logger.warning("Original confirmation message not found on confirm.")
        except discord.HTTPException as e: logger.error("Failed to edit confirmation message on confirm.", exc_info=e)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="cancel_clear")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for item in self.children:
             if isinstance(item, discord.ui.Button): item.disabled = True
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="キャンセルしました。", view=self)
        except discord.NotFound: logger.warning("Original confirmation message not found on cancel.")
        except discord.HTTPException as e: logger.error("Failed to edit confirmation message on cancel.", exc_info=e)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
             if isinstance(item, discord.ui.Button): item.disabled = True
        logger.info("Confirm clear view timed out.")
        # タイムアウトメッセージの編集は呼び出し元で行う


# --- History Cog 本体 ---
class HistoryCog(commands.Cog):
    # Group定義をクラス変数としてクラス直下に移動
    history_commands = app_commands.Group(name="history", description="会話履歴の管理")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("HistoryCog loaded.")

    # ★ get_global_history_for_prompt を修正 ★
    async def get_global_history_for_prompt(self) -> List[genai_types.Content]:
        """グローバル履歴をAPIリクエスト用に整形し、発言者情報を付与して返す"""
        logger.debug(f"Getting global history for prompt")
        history_deque = config_manager.get_global_history() # グローバル履歴を取得
        content_history = []
        if not history_deque:
            logger.debug(f"No global history found")
            return content_history
        logger.debug(f"Raw global history deque (len={len(history_deque)}): {list(history_deque)}")

        # Bot自身の名前を事前に取得
        bot_name = self.bot.user.display_name if self.bot.user else "Bot"

        for entry in list(history_deque): # dequeをリスト化して安全にイテレート
            try:
                parts_obj_list = []
                role = entry.get("role")
                entry_parts = entry.get("parts", [])
                interlocutor_id = entry.get("interlocutor_id") # 発言者ID

                if not role or not entry_parts or role not in ["user", "model"] or interlocutor_id is None:
                    logger.warning(f"Skip invalid global history entry (missing essential info): {entry}")
                    continue

                # --- 発言者名の取得 ---
                speaker_name = f"User {interlocutor_id}" # デフォルト
                if role == "model":
                    speaker_name = bot_name # Botの発言
                elif role == "user":
                    # ニックネームを取得、なければフォールバック
                    nickname = config_manager.get_nickname(interlocutor_id)
                    if nickname:
                        speaker_name = nickname
                    else:
                        # Discordユーザー情報を取得試行 (キャッシュにあれば高速)
                        user = self.bot.get_user(interlocutor_id)
                        if user:
                            speaker_name = user.display_name
                        # else: # キャッシュになければAPIコールが必要になるため、ID表示に留める
                        #     logger.warning(f"Could not find user {interlocutor_id} in cache for history speaker name.")

                context_prefix = f"[{speaker_name}]: " # 発言者プレフィックス

                # --- Parts の整形 ---
                first_part = True
                processed_parts = [] # 整形後のPartを一時格納
                for part_dict in entry_parts:
                    if 'text' in part_dict and isinstance(part_dict['text'], str) and part_dict['text'].strip():
                        text_content = part_dict['text'].strip()
                        # 最初のテキストパートにのみプレフィックスを追加
                        final_text = context_prefix + text_content if first_part else text_content
                        processed_parts.append(genai_types.Part(text=final_text))
                        first_part = False # 2番目以降のテキストパートにはプレフィックス不要
                    elif 'inline_data' in part_dict:
                        logger.warning("Skipping inline_data restoration in history formatting.")
                        # Blobの復元は複雑なためスキップ or プレフィックス付きテキストで代替表現も検討
                        # if first_part:
                        #    processed_parts.append(genai_types.Part(text=f"{context_prefix}[添付データ: {part_dict['inline_data'].get('mime_type', '不明')}]"))
                        #    first_part = False
                        # else:
                        #    processed_parts.append(genai_types.Part(text=f"[添付データ: {part_dict['inline_data'].get('mime_type', '不明')}]"))
                    elif 'function_call' in part_dict and isinstance(part_dict['function_call'], dict):
                        fc_data = part_dict['function_call']
                        if 'name' in fc_data and 'args' in fc_data:
                             # 関数呼び出しにはプレフィックス不要
                             processed_parts.append(genai_types.Part(function_call=genai_types.FunctionCall(name=fc_data.get('name'), args=fc_data.get('args'))))
                             # もしテキストも同時に存在しうるなら、テキスト部分にプレフィックスを付ける
                        else: logger.warning(f"Skipping invalid function_call dict in history: {part_dict}")
                        first_part = False # 関数呼び出しがあれば、後続のテキストにはプレフィックス不要
                    elif 'function_response' in part_dict and isinstance(part_dict['function_response'], dict):
                         fr_data = part_dict['function_response'];
                         if 'name' in fr_data and 'response' in fr_data:
                              # 関数応答にもプレフィックス不要
                              processed_parts.append(genai_types.Part(function_response=genai_types.FunctionResponse(name=fr_data.get('name'), response=fr_data.get('response'))))
                         else: logger.warning(f"Skipping invalid function_response dict in history: {part_dict}")
                         first_part = False # 関数応答があれば、後続のテキストにはプレフィックス不要
                    else: logger.warning(f"Skipping unrecognized part dict in history: {part_dict}")

                if processed_parts: # 整形されたPartがあればContentに追加
                    content_history.append(genai_types.Content(role=role, parts=processed_parts))
                else:
                    logger.warning(f"No valid parts constructed for history entry: {entry}")
            except Exception as e:
                logger.error(f"Error converting global history entry dict: {entry}", exc_info=e)

        logger.debug(f"Formatted global history for prompt (returned {len(content_history)} entries)")
        return content_history


    # ★ add_history_entry_async (変更なし、config_manager側で処理) ★
    async def add_history_entry_async(self, current_interlocutor_id: int, channel_id: Optional[int], role: str, parts_dict: List[Dict[str, Any]], entry_author_id: int):
        """グローバル会話履歴にエントリを追加・保存する (config_managerを呼び出す)"""
        if role not in ["user", "model"]: logger.error(f"Invalid role '{role}'"); return
        logger.debug(f"Adding history async (Global) - Current interlocutor: {current_interlocutor_id}, Channel: {channel_id}, Role: {role}, Entry author: {entry_author_id}")
        try:
            await config_manager.add_history_entry_async(
                current_interlocutor_id=current_interlocutor_id,
                channel_id=channel_id,
                role=role,
                parts_dict=parts_dict,
                entry_author_id=entry_author_id
            )
        except Exception as e:
            logger.error(f"Error calling config_manager.add_history_entry_async", exc_info=e)

    # --- 履歴操作コマンド (対象がグローバル履歴になる) ---
    @history_commands.command(name="clear", description="会話履歴を削除します")
    @app_commands.describe(
        type="削除する範囲 (all, user, channel, my)",
        target_user="ユーザー関連削除の場合に対象ユーザーを指定",
        target_channel="チャンネル削除の場合に対象チャンネルを指定",
        password="全削除の場合に必要なパスワード"
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="All (全体)", value="all"),
        app_commands.Choice(name="User (指定ユーザー関連)", value="user"),
        app_commands.Choice(name="Channel (指定チャンネル)", value="channel"),
        app_commands.Choice(name="My (自分関連)", value="my"),
    ])
    async def history_clear(self,
                            interaction: discord.Interaction,
                            type: str,
                            target_user: Optional[discord.User] = None,
                            target_channel: Optional[discord.TextChannel] = None,
                            password: Optional[str] = None
                            ):
        """会話履歴を削除するスラッシュコマンド"""
        await interaction.response.defer(ephemeral=True)
        clear_type = type.lower()

        valid_types = ["all", "user", "channel", "my"]
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

                view = ConfirmClearView(timeout=30.0); msg_content = "⚠️ **本当にすべての会話履歴を削除しますか？元に戻せません！**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    await config_manager.clear_all_history_async()
                    edit_kwargs["content"] = "✅ すべての会話履歴を削除しました。"
                    logger.warning(f"All conversation history cleared by {interaction.user}")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"
                # タイムアウト時のメッセージは view.wait() の後で設定

            elif clear_type == "user":
                if target_user is None: await interaction.followup.send("`type=user`の場合`target_user`要。", ephemeral=True); return
                view = ConfirmClearView(timeout=30.0); msg_content = f"⚠️ **本当にユーザー {target_user.mention} 関連履歴削除？**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_count = await config_manager.clear_user_history_async(target_user.id)
                    edit_kwargs["content"] = f"✅ ユーザー {target_user.mention} 関連履歴 ({cleared_count}件) 削除。"
                    logger.info(f"Cleared global history entries involving user {target_user.id} by {interaction.user}")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"

            elif clear_type == "channel":
                target = target_channel or interaction.channel
                if not isinstance(target, discord.TextChannel):
                     await interaction.followup.send("チャンネル履歴削除はサーバーのテキストチャンネルのみ指定可能です。", ephemeral=True); return

                view = ConfirmClearView(timeout=30.0); msg_content = f"⚠️ **本当にチャンネル {target.mention} の履歴削除？**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_count = await config_manager.clear_channel_history_async(target.id)
                    edit_kwargs["content"] = f"✅ チャンネル {target.mention} 履歴 ({cleared_count}件) 削除。"
                    logger.info(f"Cleared global history entries for channel {target.id} by {interaction.user}")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"

            elif clear_type == "my":
                user_to_clear = interaction.user
                view = ConfirmClearView(timeout=30.0); msg_content = f"⚠️ **本当にあなた ({user_to_clear.mention}) が関与する履歴削除？**";
                await interaction.followup.send(msg_content, view=view, ephemeral=True); await view.wait();
                if view.confirmed:
                    cleared_count = await config_manager.clear_user_history_async(user_to_clear.id) # userタイプと同じ関数を呼び出す
                    edit_kwargs["content"] = f"✅ あなたが関与する履歴 ({cleared_count}件) 削除。"
                    logger.info(f"User {interaction.user} cleared global history entries involving themselves.")
                elif view.confirmed is False: edit_kwargs["content"] = "キャンセルしました。"

            # --- 最終的な応答を編集 ---
            if interaction.response.is_done(): # is_done チェックを修正
                 try:
                     if view and view.confirmed is None: # view が存在し、かつタイムアウトした場合
                         edit_kwargs["content"] = "タイムアウトしました。"
                     await interaction.edit_original_response(**edit_kwargs)
                 except discord.NotFound:
                     logger.warning("Original interaction response not found when editing final clear status.")
                 except discord.HTTPException as e:
                      logger.error("Failed to edit final clear status message.", exc_info=e)
            else:
                 logger.warning("Interaction was not done after ConfirmClearView wait, sending new message.")
                 if view and view.confirmed is None:
                      edit_kwargs["content"] = "タイムアウトしました。"
                 await interaction.response.send_message(edit_kwargs["content"], ephemeral=True)

        except Exception as e:
            logger.error(f"Error in /history clear (type={clear_type})", exc_info=e)
            error_msg = f"履歴の削除中にエラーが発生しました: {e}"
            try:
                 if interaction.response.is_done():
                      await interaction.edit_original_response(content=error_msg, view=None)
                 else:
                      if interaction.response.is_done():
                           await interaction.followup.send(error_msg, ephemeral=True)
                      else:
                           await interaction.response.send_message(error_msg, ephemeral=True)
            except discord.NotFound: logger.warning("Original interaction response not found on error.")
            except discord.HTTPException: logger.error("Failed to send error message for history clear.")


    @history_commands.command(name="set_length", description="保持する会話履歴の最大件数を設定します")
    @app_commands.describe(length="保持する最大件数 (0以上)")
    async def history_set_length(self, interaction: discord.Interaction, length: int):
        await interaction.response.defer(ephemeral=True);
        if length < 0: await interaction.followup.send("履歴保持件数は0以上の整数で指定してください。", ephemeral=True); return
        try:
            await config_manager.update_max_history_async(length)
            await interaction.followup.send(f"会話履歴の最大保持件数を `{length}` 件に設定しました。", ephemeral=True);
            logger.info(f"Max history length set to {length} by {interaction.user}")
        except Exception as e:
            logger.error("Error in /history set_length", exc_info=e);
            await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @history_commands.command(name="show_length", description="現在の会話履歴の最大保持件数を表示します")
    async def history_show_length(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True);
        try:
            length = config_manager.get_max_history();
            await interaction.followup.send(f"現在の会話履歴の最大保持件数は `{length}` 件です。", ephemeral=True)
        except Exception as e:
            logger.error("Error in /history show_length", exc_info=e);
            await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCog(bot))
    logger.info("HistoryCog setup complete.")
# cogs/config_cog.py (ランダムDMでfloat入力対応、秒変換時に切り捨て)

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Literal, Optional, Union
import json
import datetime
import math # 分表示、float->int変換のために追加

# config_manager モジュールから必要な関数・変数をインポート
from utils import config_manager

logger = logging.getLogger(__name__)

# トップレベルの /config グループを定義するCog
class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("ConfigCog loaded.")

    # --- グループ定義 ---
    config = app_commands.Group(name="config", description="Botの各種設定を行います")
    gemini = app_commands.Group(name="gemini", parent=config, description="メインのGeminiモデル関連の設定")
    prompt = app_commands.Group(name="prompt", parent=config, description="プロンプト関連の設定")
    user = app_commands.Group(name="user", parent=config, description="ユーザー関連の設定")
    channel = app_commands.Group(name="channel", parent=config, description="チャンネル関連の設定")
    random_dm = app_commands.Group(name="random_dm", parent=config, description="ランダムDM関連の設定")
    response = app_commands.Group(name="response", parent=config, description="応答関連の設定")
    summary = app_commands.Group(name="summary", parent=config, description="履歴要約機能関連の設定")

    # --- メイン Gemini 設定 (変更なし) ---
    @gemini.command(name="show", description="現在のメインGemini関連設定を表示します")
    async def gemini_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            model_name = config_manager.get_model_name()
            gen_config_dict = config_manager.get_generation_config_dict()
            safety_settings_list = config_manager.get_safety_settings_list()

            embed = discord.Embed(title="現在のメインGemini設定", color=discord.Color.blue())
            embed.add_field(name="モデル名 (Model Name)", value=f"`{model_name}`", inline=False)
            try:
                serializable_gen_config = {k: v for k, v in gen_config_dict.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                gen_config_str = json.dumps(serializable_gen_config, indent=2, ensure_ascii=False)
            except TypeError:
                gen_config_str = str(gen_config_dict) # フォールバック
            embed.add_field(name="生成設定 (Generation Config)", value=f"```json\n{gen_config_str}\n```", inline=False)
            safety_settings_str = "\n".join([f"- {s.get('category', 'N/A')}: `{s.get('threshold', 'N/A')}`" for s in safety_settings_list])
            if not safety_settings_str: safety_settings_str = "未設定"
            embed.add_field(name="安全性設定 (Safety Settings)", value=safety_settings_str, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error("Error in /config gemini show", exc_info=e)
            await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_temperature", description="[メイン] 応答のランダム性 (Temperature) を設定します (0.0 ~ 2.0)")
    @app_commands.describe(value="設定するTemperature値 (例: 0.9)")
    async def gemini_set_temperature(self, interaction: discord.Interaction, value: float):
        await interaction.response.defer(ephemeral=True)
        if not (0.0 <= value <= 2.0):
             await interaction.followup.send("Temperatureは 0.0 から 2.0 の間で設定してください。", ephemeral=True); return
        try:
            config_manager.generation_config["temperature"] = value
            config_manager.save_generation_config()
            await interaction.followup.send(f"メイン応答の Temperature を `{value}` に設定しました。", ephemeral=True)
            logger.info(f"Main Temperature set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_temperature", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_model", description="[メイン] 使用するGeminiモデルを設定します")
    @app_commands.describe(model_name="モデル名 (例: gemini-1.5-pro)")
    async def gemini_set_model(self, interaction: discord.Interaction, model_name: str):
         await interaction.response.defer(ephemeral=True)
         try:
             config_manager.gemini_config["model_name"] = model_name
             config_manager.save_gemini_config()
             await interaction.followup.send(f"メイン応答の使用モデルを `{model_name}` に設定しました。", ephemeral=True)
             logger.info(f"Main Model name set to {model_name} by {interaction.user}")
         except Exception as e: logger.error("Error in /config gemini set_model", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_safety", description="[メイン] 指定した危害カテゴリの安全しきい値を設定します")
    @app_commands.describe(category="設定するカテゴリ", threshold="設定するしきい値")
    @app_commands.choices(category=[ app_commands.Choice(name="Harassment", value="HARM_CATEGORY_HARASSMENT"), app_commands.Choice(name="Hate Speech", value="HARM_CATEGORY_HATE_SPEECH"), app_commands.Choice(name="Sexually Explicit", value="HARM_CATEGORY_SEXUALLY_EXPLICIT"), app_commands.Choice(name="Dangerous Content", value="HARM_CATEGORY_DANGEROUS_CONTENT"), ], threshold=[ app_commands.Choice(name="Block None", value="BLOCK_NONE"), app_commands.Choice(name="Block Only High", value="BLOCK_ONLY_HIGH"), app_commands.Choice(name="Block Medium and Above", value="BLOCK_MEDIUM_AND_ABOVE"), app_commands.Choice(name="Block Low and Above", value="BLOCK_LOW_AND_ABOVE"), ])
    async def gemini_set_safety(self, interaction: discord.Interaction, category: app_commands.Choice[str], threshold: app_commands.Choice[str]):
         await interaction.response.defer(ephemeral=True)
         try: config_manager.update_safety_setting(category.value, threshold.value); await interaction.followup.send(f"メイン応答の安全性設定 `{category.name}` のしきい値を `{threshold.name}` に設定しました。", ephemeral=True); logger.info(f"Main Safety setting {category.name} set to {threshold.name} by {interaction.user}")
         except Exception as e: logger.error("Error in /config gemini set_safety", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_top_k", description="[メイン] Top-K サンプリング値を設定します (整数)")
    @app_commands.describe(value="設定する Top-K 値 (例: 40)")
    async def gemini_set_top_k(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value <= 0: await interaction.followup.send("Top-K は 1 以上の整数で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["top_k"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"メイン応答の Top-K を `{value}` に設定しました。", ephemeral=True); logger.info(f"Main Top-K set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_top_k", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_top_p", description="[メイン] Top-P (Nucleus) サンプリング値を設定します (0.0 ~ 1.0)")
    @app_commands.describe(value="設定する Top-P 値 (例: 0.95)")
    async def gemini_set_top_p(self, interaction: discord.Interaction, value: float):
        await interaction.response.defer(ephemeral=True)
        if not (0.0 <= value <= 1.0): await interaction.followup.send("Top-P は 0.0 から 1.0 の間で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["top_p"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"メイン応答の Top-P を `{value}` に設定しました。", ephemeral=True); logger.info(f"Main Top-P set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_top_p", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_max_tokens", description="[メイン] 最大出力トークン数を設定します")
    @app_commands.describe(value="設定する最大トークン数 (例: 1024)")
    async def gemini_set_max_tokens(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value <= 0: await interaction.followup.send("最大出力トークン数は 1 以上の整数で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["max_output_tokens"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"メイン応答の最大出力トークン数を `{value}` に設定しました。", ephemeral=True); logger.info(f"Main Max output tokens set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_max_tokens", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)


    # --- ★ 要約 Gemini 設定 ★ (変更なし) ---
    @summary.command(name="show", description="現在の履歴要約機能のGemini関連設定を表示します")
    async def summary_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            model_name = config_manager.get_summary_model_name()
            gen_config_dict = config_manager.get_summary_generation_config_dict()
            max_prompt_tokens = config_manager.get_summary_max_prompt_tokens()

            embed = discord.Embed(title="現在の履歴要約機能の設定", color=discord.Color.dark_green())
            embed.add_field(name="要約用モデル名", value=f"`{model_name}`", inline=False)
            embed.add_field(name="プロンプトに含める要約の最大トークン数", value=f"`{max_prompt_tokens}`", inline=False)
            try:
                serializable_gen_config = {k: v for k, v in gen_config_dict.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                gen_config_str = json.dumps(serializable_gen_config, indent=2, ensure_ascii=False)
            except TypeError:
                gen_config_str = str(gen_config_dict) # フォールバック
            embed.add_field(name="要約生成設定 (Generation Config)", value=f"```json\n{gen_config_str}\n```", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error("Error in /config summary show", exc_info=e)
            await interaction.followup.send(f"要約設定の表示中にエラーが発生しました: {e}", ephemeral=True)

    @summary.command(name="set_model", description="[要約用] 使用するGeminiモデルを設定します")
    @app_commands.describe(model_name="モデル名 (例: gemini-1.5-flash)")
    async def summary_set_model(self, interaction: discord.Interaction, model_name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            config_manager.update_summary_model_name(model_name)
            await interaction.followup.send(f"要約用モデルを `{model_name}` に設定しました。", ephemeral=True)
            logger.info(f"Summary Model name set to {model_name} by {interaction.user}")
        except Exception as e: logger.error("Error in /config summary set_model", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @summary.command(name="set_max_prompt_tokens", description="[要約用] プロンプトに含める要約情報の最大トークン数を設定")
    @app_commands.describe(value="最大トークン数 (0以上)")
    async def summary_set_max_prompt_tokens(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value < 0: await interaction.followup.send("最大トークン数は0以上の整数で設定してください。", ephemeral=True); return
        try:
            config_manager.update_summary_max_prompt_tokens(value)
            await interaction.followup.send(f"プロンプトに含める要約の最大トークン数を `{value}` に設定しました。", ephemeral=True)
            logger.info(f"Summary max prompt tokens set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config summary set_max_prompt_tokens", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @summary.command(name="set_temperature", description="[要約用] 応答のランダム性 (Temperature) を設定します (0.0 ~ 2.0)")
    @app_commands.describe(value="設定するTemperature値 (例: 0.5)")
    async def summary_set_temperature(self, interaction: discord.Interaction, value: float):
        await interaction.response.defer(ephemeral=True)
        if not (0.0 <= value <= 2.0): await interaction.followup.send("Temperatureは 0.0 から 2.0 の間で設定してください。", ephemeral=True); return
        try:
            config_manager.update_summary_generation_config("temperature", value)
            await interaction.followup.send(f"要約生成の Temperature を `{value}` に設定しました。", ephemeral=True)
            logger.info(f"Summary Temperature set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config summary set_temperature", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @summary.command(name="set_top_p", description="[要約用] Top-P サンプリング値を設定します (0.0 ~ 1.0)")
    @app_commands.describe(value="設定する Top-P 値 (例: 0.95)")
    async def summary_set_top_p(self, interaction: discord.Interaction, value: float):
        await interaction.response.defer(ephemeral=True)
        if not (0.0 <= value <= 1.0): await interaction.followup.send("Top-P は 0.0 から 1.0 の間で設定してください。", ephemeral=True); return
        try:
            config_manager.update_summary_generation_config("top_p", value)
            await interaction.followup.send(f"要約生成の Top-P を `{value}` に設定しました。", ephemeral=True); logger.info(f"Summary Top-P set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config summary set_top_p", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @summary.command(name="set_top_k", description="[要約用] Top-K サンプリング値を設定します (整数)")
    @app_commands.describe(value="設定する Top-K 値 (例: 1)")
    async def summary_set_top_k(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value <= 0: await interaction.followup.send("Top-K は 1 以上の整数で設定してください。", ephemeral=True); return
        try:
            config_manager.update_summary_generation_config("top_k", value)
            await interaction.followup.send(f"要約生成の Top-K を `{value}` に設定しました。", ephemeral=True); logger.info(f"Summary Top-K set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config summary set_top_k", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @summary.command(name="set_max_output_tokens", description="[要約用] 要約結果の最大出力トークン数を設定します")
    @app_commands.describe(value="設定する最大トークン数 (例: 512)")
    async def summary_set_max_output_tokens(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value <= 0: await interaction.followup.send("最大出力トークン数は 1 以上の整数で設定してください。", ephemeral=True); return
        try:
            config_manager.update_summary_generation_config("max_output_tokens", value)
            await interaction.followup.send(f"要約生成の最大出力トークン数を `{value}` に設定しました。", ephemeral=True); logger.info(f"Summary Max output tokens set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config summary set_max_output_tokens", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)


    # --- プロンプト設定 (変更なし) ---
    @prompt.command(name="show", description="指定した種類のプロンプトを表示します")
    @app_commands.describe(type="表示するプロンプトの種類")
    @app_commands.choices(type=[ app_commands.Choice(name="Persona", value="persona"), app_commands.Choice(name="Random DM", value="random_dm"), ])
    async def prompt_show(self, interaction: discord.Interaction, type: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True);
        try:
            prompt_content = ""; title = "";
            if type.value == "persona": prompt_content = config_manager.get_persona_prompt(); title = "現在のペルソナプロンプト"
            elif type.value == "random_dm": prompt_content = config_manager.get_random_dm_prompt(); title = "現在のランダムDMプロンプト"
            else: await interaction.followup.send("無効なプロンプトの種類です。", ephemeral=True); return
            if not prompt_content: prompt_content = "(未設定)"
            max_len = 1900;
            if len(prompt_content) <= max_len: embed = discord.Embed(title=title, description=f"```\n{prompt_content}\n```", color=discord.Color.green()); await interaction.followup.send(embed=embed, ephemeral=True)
            else: await interaction.followup.send(f"**{title}**:", ephemeral=True);
            for i in range(0, len(prompt_content), max_len): chunk = prompt_content[i:i+max_len]; await interaction.followup.send(f"```\n{chunk}\n```", ephemeral=True)
        except Exception as e: logger.error(f"Error in /config prompt show {type.value}", exc_info=e); await interaction.followup.send(f"プロンプトの表示中にエラーが発生しました: {e}", ephemeral=True)

    @prompt.command(name="set", description="指定した種類のプロンプトを設定します (長文はモーダルを使用)")
    @app_commands.describe(type="設定するプロンプトの種類")
    @app_commands.choices(type=[ app_commands.Choice(name="Persona", value="persona"), app_commands.Choice(name="Random DM", value="random_dm"), ])
    async def prompt_set(self, interaction: discord.Interaction, type: app_commands.Choice[str]):
        prompt_type_value = type.value
        class PromptModal(discord.ui.Modal, title=f"{type.name} プロンプト設定"):
            prompt_input = discord.ui.TextInput(label=f"新しい {type.name} プロンプト", style=discord.TextStyle.paragraph, placeholder="ここにプロンプトを入力...", required=True, max_length=4000)
            def __init__(self, prompt_type_arg: str):
                super().__init__(); current_prompt = "";
                if prompt_type_arg == "persona": current_prompt = config_manager.get_persona_prompt()
                elif prompt_type_arg == "random_dm": current_prompt = config_manager.get_random_dm_prompt()
                self.prompt_input.default = current_prompt[:4000] if current_prompt else ""
            async def on_submit(self, interaction: discord.Interaction):
                new_prompt = self.prompt_input.value
                try:
                    if prompt_type_value == "persona":
                        config_manager.persona_prompt = new_prompt
                        config_manager.save_persona_prompt()
                    elif prompt_type_value == "random_dm":
                        config_manager.random_dm_prompt = new_prompt
                        config_manager.save_random_dm_prompt()
                    await interaction.response.send_message(f"{type.name} プロンプトを更新しました。", ephemeral=True)
                    logger.info(f"{type.name} prompt updated by {interaction.user}")
                except Exception as e:
                    logger.error(f"Error saving {type.name} prompt", exc_info=e)
                    error_message = f"プロンプトの保存中にエラーが発生しました: {e}"
                    if not interaction.response.is_done(): await interaction.response.send_message(error_message, ephemeral=True)
                    else:
                        try: await interaction.followup.send(error_message, ephemeral=True)
                        except discord.NotFound: logger.warning(f"Could not send error followup for prompt save ({type.name})")
            async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
                logger.error("Error in PromptModal internal processing", exc_info=error)
                error_message = f'モーダル処理で予期せぬエラーが発生しました: {error}'
                if not interaction.response.is_done(): await interaction.response.send_message(error_message, ephemeral=True)
                else:
                    try: await interaction.followup.send(error_message, ephemeral=True)
                    except discord.NotFound: logger.warning(f"Could not send error followup for modal internal error")
        await interaction.response.send_modal(PromptModal(prompt_type_value))

    # --- ユーザー関連コマンド (ID指定対応) (変更なし) ---
    @user.command(name="set_nickname", description="ユーザーのニックネームを設定します (ID指定)")
    @app_commands.describe(
        user_id="ニックネームを設定するユーザーのID (数字のみ)",
        nickname="設定するニックネーム"
    )
    async def user_set_nickname(self, interaction: discord.Interaction, user_id: str, nickname: str):
         await interaction.response.defer(ephemeral=True)
         try:
             try:
                 target_user_id = int(user_id)
             except ValueError:
                 await interaction.followup.send("ユーザーIDは数字で入力してください。", ephemeral=True)
                 return

             try:
                 target_user = await self.bot.fetch_user(target_user_id)
             except discord.NotFound:
                 await interaction.followup.send("指定されたIDのユーザーが見つかりませんでした。", ephemeral=True)
                 return
             except discord.HTTPException:
                 await interaction.followup.send("ユーザー情報の取得中にエラーが発生しました。", ephemeral=True)
                 return

             old_nickname = config_manager.get_nickname(target_user_id)
             await config_manager.update_nickname_async(target_user_id, nickname)
             response_message = f"{target_user.mention} (`{target_user.name}`) のニックネームを `{nickname}` に設定しました。"
             if old_nickname: response_message += f"\n(以前のニックネーム: `{old_nickname}`)"
             response_message += "\n\n**ヒント:** 変更を応答にすぐに反映させたい場合は、`/history clear type:my` でご自身の会話履歴をクリアしてください。"
             await interaction.followup.send(response_message, ephemeral=True)
             logger.info(f"Nickname for {target_user.name} (ID: {target_user_id}) set to '{nickname}' by {interaction.user}")
         except Exception as e:
             logger.error("Error in /config user set_nickname", exc_info=e)
             await interaction.followup.send(f"ニックネームの設定中にエラーが発生しました: {e}", ephemeral=True)

    @user.command(name="show_nickname", description="ユーザーのニックネームを表示します (ID指定も可)")
    @app_commands.describe(
        user_id="[DM用] ニックネームを表示するユーザーのID (数字)",
        user_mention="[サーバー用] ニックネームを表示するユーザーを選択 (IDより優先)"
    )
    async def user_show_nickname(self, interaction: discord.Interaction, user_id: Optional[str] = None, user_mention: Optional[discord.User] = None):
         await interaction.response.defer(ephemeral=True)
         target_user: Optional[discord.User] = None

         if user_mention:
            target_user = user_mention
         elif user_id:
            try:
                target_user_id_int = int(user_id)
                try:
                    target_user = await self.bot.fetch_user(target_user_id_int)
                except discord.NotFound:
                    await interaction.followup.send("指定されたIDのユーザーが見つかりませんでした。", ephemeral=True)
                    return
                except discord.HTTPException:
                    await interaction.followup.send("ユーザー情報の取得中にエラーが発生しました。", ephemeral=True)
                    return
            except ValueError:
                await interaction.followup.send("ユーザーIDは数字で入力してください。", ephemeral=True)
                return
         else:
            target_user = interaction.user

         if not target_user:
             await interaction.followup.send("対象ユーザーを特定できませんでした。", ephemeral=True); return

         try:
             nickname = config_manager.get_nickname(target_user.id)
             if nickname: await interaction.followup.send(f"{target_user.mention} (`{target_user.name}`) のニックネームは `{nickname}` です。", ephemeral=True)
             else: await interaction.followup.send(f"{target_user.mention} (`{target_user.name}`) にはニックネームが設定されていません。", ephemeral=True)
         except Exception as e:
             logger.error("Error in /config user show_nickname", exc_info=e)
             await interaction.followup.send(f"ニックネームの表示中にエラーが発生しました: {e}", ephemeral=True)

    @user.command(name="remove_nickname", description="ユーザーのニックネームを削除します (ID指定)")
    @app_commands.describe(
        user_id="ニックネームを削除するユーザーのID (数字)"
    )
    async def user_remove_nickname(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            try:
                target_user_id = int(user_id)
            except ValueError:
                await interaction.followup.send("ユーザーIDは数字で入力してください。", ephemeral=True)
                return

            try:
                target_user = await self.bot.fetch_user(target_user_id)
            except discord.NotFound:
                await interaction.followup.send("指定されたIDのユーザーが見つかりませんでした。", ephemeral=True)
                return
            except discord.HTTPException:
                await interaction.followup.send("ユーザー情報の取得中にエラーが発生しました。", ephemeral=True)
                return

            current_nickname = config_manager.get_nickname(target_user_id)
            if current_nickname:
                removed = await config_manager.remove_nickname_async(target_user_id)
                if removed: await interaction.followup.send(f"{target_user.mention} (`{target_user.name}`) のニックネーム (`{current_nickname}`) を削除しました。", ephemeral=True); logger.info(f"Nickname for {target_user.name} (ID: {target_user_id}) removed by {interaction.user}")
                else: await interaction.followup.send(f"ニックネームの削除に失敗しました（内部エラー）。", ephemeral=True)
            else: await interaction.followup.send(f"{target_user.mention} (`{target_user.name}`) にはニックネームが設定されていません。", ephemeral=True)
        except Exception as e: logger.error("Error in /config user remove_nickname", exc_info=e); await interaction.followup.send(f"ニックネームの削除中にエラーが発生しました: {e}", ephemeral=True)


    # --- チャンネル設定 (変更なし) ---
    @channel.command(name="add", description="自動応答を許可するチャンネルを追加します")
    @app_commands.guild_only()
    @app_commands.describe(channel="許可するテキストチャンネル")
    async def channel_add(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True); server_id_str = str(interaction.guild_id);
        try:
            allowed_list = config_manager.channel_settings.get(server_id_str, []);
            if channel.id not in allowed_list: allowed_list.append(channel.id); config_manager.channel_settings[server_id_str] = allowed_list; config_manager.save_channel_settings(); await interaction.followup.send(f"{channel.mention} を自動応答許可チャンネルに追加しました。", ephemeral=True); logger.info(f"Added channel {channel.name} (ID: {channel.id}) for server {interaction.guild.name} by {interaction.user}")
            else: await interaction.followup.send(f"{channel.mention} は既に許可されています。", ephemeral=True)
        except Exception as e: logger.error("Error in /config channel add", exc_info=e); await interaction.followup.send(f"チャンネル設定の更新中にエラーが発生しました: {e}", ephemeral=True)

    @channel.command(name="remove", description="自動応答を許可するチャンネルから削除します")
    @app_commands.guild_only()
    @app_commands.describe(channel="削除するテキストチャンネル")
    async def channel_remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True); server_id_str = str(interaction.guild_id);
        try:
            allowed_list = config_manager.channel_settings.get(server_id_str, []);
            if channel.id in allowed_list:
                allowed_list.remove(channel.id);
                if not allowed_list:
                    if server_id_str in config_manager.channel_settings: del config_manager.channel_settings[server_id_str]
                else: config_manager.channel_settings[server_id_str] = allowed_list
                config_manager.save_channel_settings(); await interaction.followup.send(f"{channel.mention} を自動応答許可チャンネルから削除しました。", ephemeral=True); logger.info(f"Removed channel {channel.name} (ID: {channel.id}) for server {interaction.guild.name} by {interaction.user}")
            else: await interaction.followup.send(f"{channel.mention} は許可チャンネルに登録されていません。", ephemeral=True)
        except Exception as e: logger.error("Error in /config channel remove", exc_info=e); await interaction.followup.send(f"チャンネル設定の更新中にエラーが発生しました: {e}", ephemeral=True)

    @channel.command(name="list", description="自動応答が許可されているチャンネルの一覧を表示します")
    @app_commands.guild_only()
    async def channel_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True); server_id_str = str(interaction.guild_id);
        try:
            allowed_ids = config_manager.get_allowed_channels(server_id_str)
            if not allowed_ids: await interaction.followup.send("現在、このサーバーで自動応答が許可されているチャンネルはありません。", ephemeral=True); return
            channel_mentions = [];
            for channel_id in allowed_ids: channel = interaction.guild.get_channel(channel_id); channel_mentions.append(channel.mention if channel else f"不明なチャンネル (ID: {channel_id})")
            embed = discord.Embed(title=f"{interaction.guild.name} の自動応答許可チャンネル", description="\n".join(channel_mentions), color=discord.Color.purple()); await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e: logger.error("Error in /config channel list", exc_info=e); await interaction.followup.send(f"チャンネルリストの表示中にエラーが発生しました: {e}", ephemeral=True)


    # --- ★★★ ランダムDM設定 (float入力対応) ★★★ ---
    @random_dm.command(name="set", description="自身のランダムDM設定を行います")
    @app_commands.describe(
        enabled="ランダムDMを有効にするか (true/false)",
        min_interval="最小送信間隔 (分, 0以上, 小数可)", # ★ 説明変更
        max_interval="最大送信間隔 (分, 最小以上, 小数可)", # ★ 説明変更
        stop_start_hour="送信停止開始時刻 (0-23時, 省略可)",
        stop_end_hour="送信停止終了時刻 (0-23時, 省略可)"
    )
    async def random_dm_set(self, interaction: discord.Interaction, enabled: bool,
                             min_interval: Optional[float] = None, # ★ float に変更
                             max_interval: Optional[float] = None, # ★ float に変更
                             stop_start_hour: Optional[int] = None,
                             stop_end_hour: Optional[int] = None):
        await interaction.response.defer(ephemeral=True); user_id = interaction.user.id;
        update_dict = {"enabled": enabled}

        min_interval_seconds: Optional[int] = None
        max_interval_seconds: Optional[int] = None

        if not enabled:
            if min_interval is not None or max_interval is not None or stop_start_hour is not None or stop_end_hour is not None:
                await interaction.followup.send("ランダムDMを無効にする場合、他のパラメータは指定できません。", ephemeral=True); return
        else:
            if min_interval is None or max_interval is None:
                await interaction.followup.send("ランダムDMを有効にする場合、最小・最大送信間隔 (分) を指定してください。", ephemeral=True); return
            # ★ float も受け付けるようにチェック変更 ★
            if not (isinstance(min_interval, (int, float)) and min_interval >= 0):
                await interaction.followup.send("最小送信間隔は0以上の数値 (分) で指定してください。", ephemeral=True); return
            if not (isinstance(max_interval, (int, float)) and max_interval >= min_interval):
                await interaction.followup.send(f"最大送信間隔は最小送信間隔 ({min_interval}分) 以上の数値 (分) で指定してください。", ephemeral=True); return
            if stop_start_hour is not None and not (0 <= stop_start_hour <= 23):
                await interaction.followup.send("送信停止開始時刻は 0 から 23 の間で指定してください。", ephemeral=True); return
            if stop_end_hour is not None and not (0 <= stop_end_hour <= 23):
                await interaction.followup.send("送信停止終了時刻は 0 から 23 の間で指定してください。", ephemeral=True); return

            # ★ 分を秒に変換し、整数に丸める (ここでは切り捨て) ★
            min_interval_seconds = int(min_interval * 60)
            max_interval_seconds = int(max_interval * 60)
            # --- ここから ---
            # 0秒にならないように最低1秒を保証する (任意)
            if min_interval > 0 and min_interval_seconds == 0:
                min_interval_seconds = 1
            if max_interval > 0 and max_interval_seconds == 0:
                max_interval_seconds = 1
            # 最大が最小より小さくならないように再チェック (丸めにより発生する可能性)
            if max_interval_seconds < min_interval_seconds:
                max_interval_seconds = min_interval_seconds # 最小値に合わせる
            # --- ここまで (任意) ---


            update_dict["min_interval"] = min_interval_seconds
            update_dict["max_interval"] = max_interval_seconds
            update_dict["stop_start_hour"] = stop_start_hour
            update_dict["stop_end_hour"] = stop_end_hour
            update_dict["last_interaction"] = datetime.datetime.now().astimezone()
            update_dict["next_send_time"] = None

        try:
            await config_manager.update_random_dm_config_async(user_id, update_dict);
            if enabled:
                 # ★ 応答メッセージでは入力された分 (float) を表示 ★
                 await interaction.followup.send(f"ランダムDMを有効にし、設定を更新しました:\n- 間隔: {min_interval}分 ～ {max_interval}分\n  (内部処理: {min_interval_seconds}秒 ～ {max_interval_seconds}秒)\n- 停止時間: {stop_start_hour if stop_start_hour is not None else '未設定'}時 ～ {stop_end_hour if stop_end_hour is not None else '未設定'}時", ephemeral=True)
            else:
                 await interaction.followup.send("ランダムDMを無効にしました。", ephemeral=True)
            logger.info(f"Random DM settings updated for user {interaction.user} (enabled={enabled})")
        except Exception as e:
            logger.error("Error in /config random_dm set", exc_info=e)
            await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @random_dm.command(name="show", description="自身の現在のランダムDM設定を表示します")
    async def random_dm_show(self, interaction: discord.Interaction):
         await interaction.response.defer(ephemeral=True); user_id_str = str(interaction.user.id);
         try:
             user_settings = config_manager.user_data.get(user_id_str, {}).get("random_dm", {}); enabled = user_settings.get("enabled", False);
             if not enabled: await interaction.followup.send("ランダムDMは現在無効です。", ephemeral=True); return

             min_interval_seconds = user_settings.get("min_interval")
             max_interval_seconds = user_settings.get("max_interval")

             # ★ 秒を分に変換 (小数点1位まで表示する例) ★
             min_interval_minutes_str = f"{min_interval_seconds / 60:.1f}" if isinstance(min_interval_seconds, int) else "未"
             max_interval_minutes_str = f"{max_interval_seconds / 60:.1f}" if isinstance(max_interval_seconds, int) else "未"
             # 整数なら .0 を消す (任意)
             if min_interval_minutes_str.endswith(".0"): min_interval_minutes_str = min_interval_minutes_str[:-2]
             if max_interval_minutes_str.endswith(".0"): max_interval_minutes_str = max_interval_minutes_str[:-2]


             stop_start = user_settings.get("stop_start_hour", "未設定"); stop_end = user_settings.get("stop_end_hour", "未設定");
             last_interact_dt = user_settings.get("last_interaction");
             last_interact_str = last_interact_dt.strftime('%Y-%m-%d %H:%M:%S %Z') if isinstance(last_interact_dt, datetime.datetime) else "記録なし";
             next_send_dt = user_settings.get("next_send_time");
             next_send_str = next_send_dt.strftime('%Y-%m-%d %H:%M:%S %Z') if isinstance(next_send_dt, datetime.datetime) else "未計算";

             embed = discord.Embed(title=f"{interaction.user.display_name} のランダムDM設定", color=discord.Color.orange());
             embed.add_field(name="状態", value="有効", inline=True);
             # ★ 分単位で表示 (小数点含む可能性あり) ★
             embed.add_field(name="送信間隔", value=f"{min_interval_minutes_str}分 ～ {max_interval_minutes_str}分", inline=True);
             embed.add_field(name="送信停止時間", value=f"{stop_start}時 ～ {stop_end}時", inline=True);
             embed.add_field(name="最終インタラクション", value=last_interact_str, inline=False);
             embed.add_field(name="次回送信予定", value=next_send_str, inline=False);
             await interaction.followup.send(embed=embed, ephemeral=True)
         except Exception as e:
             logger.error("Error in /config random_dm show", exc_info=e)
             await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)


    # --- 応答設定 (変更なし) ---
    @response.command(name="set_max_length", description="Botの応答の最大文字数を設定します")
    @app_commands.describe(length="最大文字数 (1以上)")
    async def response_set_max_length(self, interaction: discord.Interaction, length: int):
        await interaction.response.defer(ephemeral=True)
        if length <= 0: await interaction.followup.send("最大応答文字数は1以上の整数で指定してください。", ephemeral=True); return
        try: config_manager.bot_settings['max_response_length'] = length; config_manager.save_bot_settings(); await interaction.followup.send(f"最大応答文字数を `{length}` 文字に設定しました。", ephemeral=True); logger.info(f"Max response length set to {length} by {interaction.user}")
        except Exception as e: logger.error("Error in /config response set_max_length", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @response.command(name="show_max_length", description="現在のBotの応答の最大文字数を表示します")
    async def response_show_max_length(self, interaction: discord.Interaction):
         await interaction.response.defer(ephemeral=True)
         try: length = config_manager.get_max_response_length(); await interaction.followup.send(f"現在の最大応答文字数は `{length}` 文字です。", ephemeral=True)
         except Exception as e: logger.error("Error in /config response show_max_length", exc_info=e); await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))
    logger.info("ConfigCog setup complete.")
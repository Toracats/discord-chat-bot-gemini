import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Literal, Optional, Union
import json
import datetime
# import random # このCogでは不要に

# config_manager モジュールから必要な関数・変数をインポート
from utils import config_manager
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

# トップレベルの /config グループを定義するCog
class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("ConfigCog loaded.")

    # --- グループ定義 ---
    config = app_commands.Group(name="config", description="Botの各種設定を行います")
    gemini = app_commands.Group(name="gemini", parent=config, description="Geminiモデル関連の設定")
    prompt = app_commands.Group(name="prompt", parent=config, description="プロンプト関連の設定")
    user = app_commands.Group(name="user", parent=config, description="ユーザー関連の設定")
    channel = app_commands.Group(name="channel", parent=config, description="チャンネル関連の設定")
    random_dm = app_commands.Group(name="random_dm", parent=config, description="ランダムDM関連の設定")
    response = app_commands.Group(name="response", parent=config, description="応答関連の設定")

    # --- Gemini 設定 ---
    @gemini.command(name="show", description="現在のGemini関連設定を表示します")
    async def gemini_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            # config_managerから最新の設定を取得
            gen_config_dict = config_manager.get_generation_config_dict()
            safety_settings_list = config_manager.get_safety_settings_list()

            embed = discord.Embed(title="現在のGemini設定", color=discord.Color.blue())
            embed.add_field(name="モデル名 (Model Name)", value=f"`{config_manager.get_model_name()}`", inline=False)

            try:
                serializable_gen_config = {k: v for k, v in gen_config_dict.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                gen_config_str = json.dumps(serializable_gen_config, indent=2, ensure_ascii=False)
            except TypeError:
                 gen_config_str = str(gen_config_dict)
            embed.add_field(name="生成設定 (Generation Config)", value=f"```json\n{gen_config_str}\n```", inline=False)

            safety_settings_str = "\n".join([f"- {s.get('category', 'N/A')}: `{s.get('threshold', 'N/A')}`" for s in safety_settings_list])
            if not safety_settings_str: safety_settings_str = "未設定"
            embed.add_field(name="安全性設定 (Safety Settings)", value=safety_settings_str, inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error("Error in /config gemini show", exc_info=e)
            await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_temperature", description="応答のランダム性 (Temperature) を設定します (0.0 ~ 1.0)")
    @app_commands.describe(value="設定するTemperature値 (例: 0.7)")
    async def gemini_set_temperature(self, interaction: discord.Interaction, value: float):
        await interaction.response.defer(ephemeral=True)
        if not (0.0 <= value <= 1.0): await interaction.followup.send("Temperatureは 0.0 から 1.0 の間で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["temperature"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"Temperature を `{value}` に設定しました。", ephemeral=True); logger.info(f"Temperature set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_temperature", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_model", description="使用するGeminiモデルを設定します")
    @app_commands.describe(model_name="モデル名 (例: gemini-1.5-pro)")
    async def gemini_set_model(self, interaction: discord.Interaction, model_name: str):
         await interaction.response.defer(ephemeral=True)
         try: config_manager.gemini_config["model_name"] = model_name; config_manager.save_gemini_config(); await interaction.followup.send(f"使用モデルを `{model_name}` に設定しました。", ephemeral=True); logger.info(f"Model name set to {model_name} by {interaction.user}")
         except Exception as e: logger.error("Error in /config gemini set_model", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_safety", description="指定した危害カテゴリの安全しきい値を設定します")
    @app_commands.describe(category="設定するカテゴリ", threshold="設定するしきい値")
    @app_commands.choices(category=[ app_commands.Choice(name="Harassment", value="HARM_CATEGORY_HARASSMENT"), app_commands.Choice(name="Hate Speech", value="HARM_CATEGORY_HATE_SPEECH"), app_commands.Choice(name="Sexually Explicit", value="HARM_CATEGORY_SEXUALLY_EXPLICIT"), app_commands.Choice(name="Dangerous Content", value="HARM_CATEGORY_DANGEROUS_CONTENT"), ], threshold=[ app_commands.Choice(name="Block None", value="BLOCK_NONE"), app_commands.Choice(name="Block Only High", value="BLOCK_ONLY_HIGH"), app_commands.Choice(name="Block Medium and Above", value="BLOCK_MEDIUM_AND_ABOVE"), app_commands.Choice(name="Block Low and Above", value="BLOCK_LOW_AND_ABOVE"), ])
    async def gemini_set_safety(self, interaction: discord.Interaction, category: app_commands.Choice[str], threshold: app_commands.Choice[str]):
         await interaction.response.defer(ephemeral=True)
         try: config_manager.update_safety_setting(category.value, threshold.value); await interaction.followup.send(f"安全性設定 `{category.name}` のしきい値を `{threshold.name}` に設定しました。", ephemeral=True); logger.info(f"Safety setting {category.name} set to {threshold.name} by {interaction.user}")
         except Exception as e: logger.error("Error in /config gemini set_safety", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_top_k", description="Top-K サンプリング値を設定します (整数)")
    @app_commands.describe(value="設定する Top-K 値 (例: 40)")
    async def gemini_set_top_k(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value <= 0: await interaction.followup.send("Top-K は 1 以上の整数で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["top_k"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"Top-K を `{value}` に設定しました。", ephemeral=True); logger.info(f"Top-K set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_top_k", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_top_p", description="Top-P (Nucleus) サンプリング値を設定します (0.0 ~ 1.0)")
    @app_commands.describe(value="設定する Top-P 値 (例: 0.95)")
    async def gemini_set_top_p(self, interaction: discord.Interaction, value: float):
        await interaction.response.defer(ephemeral=True)
        if not (0.0 <= value <= 1.0): await interaction.followup.send("Top-P は 0.0 から 1.0 の間で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["top_p"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"Top-P を `{value}` に設定しました。", ephemeral=True); logger.info(f"Top-P set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_top_p", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @gemini.command(name="set_max_tokens", description="最大出力トークン数を設定します")
    @app_commands.describe(value="設定する最大トークン数 (例: 1024)")
    async def gemini_set_max_tokens(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        if value <= 0: await interaction.followup.send("最大出力トークン数は 1 以上の整数で設定してください。", ephemeral=True); return
        try: config_manager.generation_config["max_output_tokens"] = value; config_manager.save_generation_config(); await interaction.followup.send(f"最大出力トークン数を `{value}` に設定しました。", ephemeral=True); logger.info(f"Max output tokens set to {value} by {interaction.user}")
        except Exception as e: logger.error("Error in /config gemini set_max_tokens", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)


    # --- プロンプト設定 ---
    # (prompt_show, prompt_set は変更なし)
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
                    # 正常系の処理
                    if prompt_type_value == "persona":
                        config_manager.persona_prompt = new_prompt
                        config_manager.save_persona_prompt()
                    elif prompt_type_value == "random_dm":
                        config_manager.random_dm_prompt = new_prompt
                        config_manager.save_random_dm_prompt()

                    # 成功メッセージを送信
                    await interaction.response.send_message(f"{type.name} プロンプトを更新しました。", ephemeral=True)
                    logger.info(f"{type.name} prompt updated by {interaction.user}")

                except Exception as e:
                    # エラー発生時の処理
                    logger.error(f"Error saving {type.name} prompt", exc_info=e)
                    error_message = f"プロンプトの保存中にエラーが発生しました: {e}"
                    # try...except の中で応答する
                    if not interaction.response.is_done():
                        await interaction.response.send_message(error_message, ephemeral=True)
                    else:
                        # 応答済みの場合 (可能性は低いが念のため)
                        try:
                            await interaction.followup.send(error_message, ephemeral=True)
                        except discord.NotFound:
                             logger.warning(f"Could not send error followup for prompt save ({type.name})")

            async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
                # on_error はモーダル自体の内部エラー用 (通常 on_submit 内の例外はここで捕捉されない)
                logger.error("Error in PromptModal internal processing", exc_info=error)
                error_message = f'モーダル処理で予期せぬエラーが発生しました: {error}'
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_message, ephemeral=True)
                else:
                    try:
                        await interaction.followup.send(error_message, ephemeral=True)
                    except discord.NotFound:
                         logger.warning(f"Could not send error followup for modal internal error")
        await interaction.response.send_modal(PromptModal(prompt_type_value))

    # --- ユーザー設定 ---
    # (user_set_nickname, user_show_nickname, user_remove_nickname は変更なし)
    @user.command(name="set_nickname", description="ユーザーのニックネームを設定します")
    @app_commands.describe(user="ニックネームを設定するユーザー", nickname="設定するニックネーム")
    async def user_set_nickname(self, interaction: discord.Interaction, user: discord.User, nickname: str):
         await interaction.response.defer(ephemeral=True)
         try:
             old_nickname = config_manager.get_nickname(user.id)
             await config_manager.update_nickname_async(user.id, nickname)
             # 応答メッセージに追記
             response_message = f"{user.mention} のニックネームを `{nickname}` に設定しました。"
             if old_nickname:
                 response_message += f"\n(以前のニックネーム: `{old_nickname}`)"
             response_message += "\n\n**ヒント:** 変更を応答にすぐに反映させたい場合は、`/history clear type:my` でご自身の会話履歴をクリアしてください。" # ★追記

             await interaction.followup.send(response_message, ephemeral=True)
             logger.info(f"Nickname for {user.name} (ID: {user.id}) set to '{nickname}' by {interaction.user}")
         except Exception as e:
             logger.error("Error in /config user set_nickname", exc_info=e)
             await interaction.followup.send(f"ニックネームの設定中にエラーが発生しました: {e}", ephemeral=True)

    @user.command(name="show_nickname", description="ユーザーのニックネームを表示します")
    @app_commands.describe(user="ニックネームを表示するユーザー (省略時: 自分)")
    async def user_show_nickname(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
         await interaction.response.defer(ephemeral=True); target_user = user or interaction.user;
         try:
             nickname = config_manager.get_nickname(target_user.id)
             if nickname:
                 await interaction.followup.send(f"{target_user.mention} のニックネームは `{nickname}` です。", ephemeral=True)
             else:
                 await interaction.followup.send(f"{target_user.mention} にはニックネームが設定されていません。", ephemeral=True)
         except Exception as e:
             logger.error("Error in /config user show_nickname", exc_info=e)
             await interaction.followup.send(f"ニックネームの表示中にエラーが発生しました: {e}", ephemeral=True)

    @user.command(name="remove_nickname", description="ユーザーのニックネームを削除します")
    @app_commands.describe(user="ニックネームを削除するユーザー")
    async def user_remove_nickname(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        try:
            current_nickname = config_manager.get_nickname(user.id)
            if current_nickname: await config_manager.remove_nickname_async(user.id); await interaction.followup.send(f"{user.mention} のニックネーム (`{current_nickname}`) を削除しました。", ephemeral=True); logger.info(f"Nickname for {user.name} (ID: {user.id}) removed by {interaction.user}")
            else: await interaction.followup.send(f"{user.mention} にはニックネームが設定されていません。", ephemeral=True)
        except Exception as e: logger.error("Error in /config user remove_nickname", exc_info=e); await interaction.followup.send(f"ニックネームの削除中にエラーが発生しました: {e}", ephemeral=True)


    # --- チャンネル設定 ---
    # (channel_add, channel_remove, channel_list は変更なし)
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
            allowed_ids = config_manager.channel_settings.get(server_id_str, []);
            if not allowed_ids: await interaction.followup.send("現在、このサーバーで自動応答が許可されているチャンネルはありません。", ephemeral=True); return
            channel_mentions = [];
            for channel_id in allowed_ids: channel = interaction.guild.get_channel(channel_id); channel_mentions.append(channel.mention if channel else f"不明なチャンネル (ID: {channel_id})")
            embed = discord.Embed(title=f"{interaction.guild.name} の自動応答許可チャンネル", description="\n".join(channel_mentions), color=discord.Color.purple()); await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e: logger.error("Error in /config channel list", exc_info=e); await interaction.followup.send(f"チャンネルリストの表示中にエラーが発生しました: {e}", ephemeral=True)

# cogs/config_cog.py の random_dm_set

    @random_dm.command(name="set", description="自身のランダムDM設定を行います")
    @app_commands.describe(
        enabled="ランダムDMを有効にするか (true/false)",
        min_interval="最小送信間隔 (秒, 0以上)", # ★ 説明を秒に変更
        max_interval="最大送信間隔 (秒, 最小以上)", # ★ 説明を秒に変更
        stop_start_hour="送信停止開始時刻 (0-23時, 省略可)",
        stop_end_hour="送信停止終了時刻 (0-23時, 省略可)"
    )
    async def random_dm_set(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        min_interval: Optional[int] = None,
        max_interval: Optional[int] = None,
        stop_start_hour: Optional[int] = None,
        stop_end_hour: Optional[int] = None
    ):
        await interaction.response.defer(ephemeral=True); user_id = interaction.user.id; user_id_str = str(user_id);

        if not enabled:
            if min_interval is not None or max_interval is not None or stop_start_hour is not None or stop_end_hour is not None: await interaction.followup.send("ランダムDMを無効にする場合、他のパラメータは指定できません。", ephemeral=True); return
            async with config_manager.data_lock: # ★ ロック取得
                if user_id_str in config_manager.user_data and "random_dm" in config_manager.user_data[user_id_str]:
                    config_manager.user_data[user_id_str]["random_dm"]["enabled"] = False
                    await config_manager.save_user_data_nolock() # ★ ロック内で保存
                    await interaction.followup.send("ランダムDMを無効にしました。", ephemeral=True); logger.info(f"Random DM disabled for user {interaction.user}")
                else: await interaction.followup.send("ランダムDMは既に無効です。", ephemeral=True); return

        else:
            if min_interval is None or max_interval is None: await interaction.followup.send("ランダムDMを有効にする場合、最小・最大送信間隔 (秒) を指定してください。", ephemeral=True); return
            if not (isinstance(min_interval, int) and min_interval >= 0): await interaction.followup.send("最小送信間隔は0以上の整数 (秒) で指定してください。", ephemeral=True); return
            if not (isinstance(max_interval, int) and max_interval >= min_interval): await interaction.followup.send(f"最大送信間隔は最小送信間隔 ({min_interval}秒) 以上の整数 (秒) で指定してください。", ephemeral=True); return
            if stop_start_hour is not None and not (0 <= stop_start_hour <= 23): await interaction.followup.send("送信停止開始時刻は 0 から 23 の間で指定してください。", ephemeral=True); return
            if stop_end_hour is not None and not (0 <= stop_end_hour <= 23): await interaction.followup.send("送信停止終了時刻は 0 から 23 の間で指定してください。", ephemeral=True); return

            try:
                new_config = {"enabled": True, "min_interval": min_interval, "max_interval": max_interval, "stop_start_hour": stop_start_hour, "stop_end_hour": stop_end_hour, "last_interaction": datetime.datetime.now(), "next_send_time": None};
                await config_manager.update_random_dm_config_async(user_id, new_config);
                await interaction.followup.send(f"ランダムDMを有効にし、設定を更新しました:\n- 間隔: {min_interval}秒 ～ {max_interval}秒\n- 停止時間: {stop_start_hour if stop_start_hour is not None else '未設定'}時 ～ {stop_end_hour if stop_end_hour is not None else '未設定'}時", ephemeral=True);
                logger.info(f"Random DM settings updated for user {interaction.user}")
            except Exception as e: logger.error("Error in /config random_dm set", exc_info=e); await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)

    @random_dm.command(name="show", description="自身の現在のランダムDM設定を表示します")
    async def random_dm_show(self, interaction: discord.Interaction):
         await interaction.response.defer(ephemeral=True); user_id_str = str(interaction.user.id);
         try:
             user_settings = config_manager.user_data.get(user_id_str, {}).get("random_dm", {}); enabled = user_settings.get("enabled", False);
             if not enabled: await interaction.followup.send("ランダムDMは現在無効です。", ephemeral=True); return
             min_interval = user_settings.get("min_interval", 0) // 60; max_interval = user_settings.get("max_interval", 0) // 60; stop_start = user_settings.get("stop_start_hour", "未設定"); stop_end = user_settings.get("stop_end_hour", "未設定"); last_interact_dt = user_settings.get("last_interaction"); last_interact_str = last_interact_dt.strftime('%Y-%m-%d %H:%M:%S') if last_interact_dt else "なし";
             embed = discord.Embed(title=f"{interaction.user.display_name} のランダムDM設定", color=discord.Color.orange()); embed.add_field(name="状態", value="有効", inline=True); embed.add_field(name="送信間隔", value=f"{min_interval}分 ～ {max_interval}分", inline=True); embed.add_field(name="送信停止時間", value=f"{stop_start}時 ～ {stop_end}時", inline=True); embed.add_field(name="最終インタラクション", value=last_interact_str, inline=False);
             await interaction.followup.send(embed=embed, ephemeral=True)
         except Exception as e: logger.error("Error in /config random_dm show", exc_info=e); await interaction.followup.send(f"設定の表示中にエラーが発生しました: {e}", ephemeral=True)

    # --- 応答設定 ---
    # (response_set_max_length, response_show_max_length は変更なし)
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
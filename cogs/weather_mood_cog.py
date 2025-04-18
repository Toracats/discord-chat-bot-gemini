# cogs/weather_mood_cog.py (GUI設定対応・ログ追加・エラー修正 - 完全版)
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import random
import asyncio
import aiohttp
import datetime
from typing import Optional, Dict, Any

from utils import config_manager

logger = logging.getLogger(__name__)

WEATHER_API_URL = "http://api.openweathermap.org/data/2.5/weather"
WEATHER_MOOD_MAP = {
    "Clear": ["元気いっぱい☀️", "とても機嫌が良い✨", "清々しい気分！"], "Clouds": ["落ち着いている☁️", "まあまあかな", "少し考え事中..."],
    "Rain": ["少し憂鬱☔", "静かに過ごしたい気分...", "雨音を聞いている"], "Drizzle": ["ちょっとだけアンニュイ💧", "穏やかな気分", "濡れるのは嫌だなぁ"],
    "Thunderstorm": ["ちょっとびっくり⚡️", "ドキドキしてる", "家にいたい気分"], "Snow": ["わくわくする❄️", "静かで綺麗だね", "寒いけど楽しい！"],
    "Mist": ["幻想的な気分...", "周りがよく見えないね", "しっとりしてる"], "Smoke": ["空気が悪いね...🌫️", "ちょっと心配", "視界が悪いなぁ"],
    "Haze": ["もやっとしてる", "遠くが見えないね", "少し眠いかも"], "Dust": ["砂っぽいね...", "目がしょぼしょぼするかも", "早く収まらないかな"],
    "Fog": ["霧が深いね...", "迷子になりそう", "幻想的だけど少し不安"], "Default": ["普通かな", "特に変わりないよ", "いつも通り"]
}

class WeatherMoodCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self.current_weather_location: Optional[str] = None
        self.current_mood: str = random.choice(WEATHER_MOOD_MAP["Default"])
        self.current_weather_description: Optional[str] = None
        self.last_weather_update: Optional[datetime.datetime] = None
        self._api_key_error_logged = False
        self._cog_ready = asyncio.Event()
        # ★ tasks.loop は __init__ 内で初期化 (start は async_init で)
        self.auto_update_weather = tasks.loop(minutes=config_manager.DEFAULT_WEATHER_AUTO_UPDATE_INTERVAL_MINUTES)(self._auto_update_weather_task)
        self.bot.loop.create_task(self.async_init())

    async def async_init(self):
        await self.bot.wait_until_ready()
        logger.info("WeatherMoodCog performing async initialization...")
        self.current_weather_location = config_manager.get_last_weather_location()
        if self.current_weather_location: logger.info(f" Initial weather location loaded: {self.current_weather_location}")
        else: logger.info(" No initial weather location found in config.")
        self.update_auto_update_task_status() # 設定に基づいてタスクを開始/停止/間隔設定
        self._cog_ready.set(); logger.info("WeatherMoodCog initialized and ready.")

    async def cog_unload(self):
        self.auto_update_weather.cancel()
        await self.http_session.close()
        logger.info("WeatherMoodCog unloaded and resources released.")

    def update_auto_update_task_status(self):
        enabled = config_manager.get_weather_auto_update_enabled()
        interval = config_manager.get_weather_auto_update_interval()
        # ★ self.auto_update_weather を直接参照
        current_interval = self.auto_update_weather.minutes # 現在のループ間隔を取得
        is_running = self.auto_update_weather.is_running()

        if enabled:
            if not is_running:
                logger.info(f"Starting auto weather update task with interval {interval} minutes.")
                self.auto_update_weather.change_interval(minutes=interval) # 間隔を設定
                self.auto_update_weather.start() # タスク開始
            elif current_interval != interval: # 実行中だが間隔が違う場合
                 logger.info(f"Restarting auto weather update task with new interval {interval} minutes (was {current_interval}).")
                 self.auto_update_weather.restart(minutes=interval) # 新しい間隔で再起動
            # else: # 実行中で間隔も同じ場合は何もしない
            #      logger.debug(f"Auto weather update task already running interval {interval} min.")
        else: # 無効の場合
            if is_running:
                logger.info("Stopping auto weather update task as it is disabled.")
                self.auto_update_weather.cancel() # タスク停止
            # else: # 停止中で無効なら何もしない
            #      logger.debug("Auto weather update task is disabled and not running.")

    async def get_weather_data(self, location: str) -> Optional[Dict[str, Any]]:
        api_key = config_manager.get_weather_api_key()
        if not api_key:
            if not self._api_key_error_logged: logger.error("OpenWeatherMap API Key not found in config."); self._api_key_error_logged = True
            return None
        self._api_key_error_logged = False
        params = { 'q': location, 'appid': api_key, 'units': 'metric', 'lang': 'ja' }
        try:
            async with self.http_session.get(WEATHER_API_URL, params=params) as resp:
                if resp.status == 200: data = await resp.json(); weather_desc = data.get('weather', [{}])[0].get('description', 'N/A'); logger.info(f"Weather data fetched for {location}: {weather_desc}"); return data
                elif resp.status == 401: logger.error(f"Weather fetch failed {location} (401 Unauthorized). Check API key."); return None
                elif resp.status == 404: logger.warning(f"Weather fetch failed {location} (404 Not Found). Check location name."); return None
                else: logger.error(f"Weather fetch failed {location}. Status: {resp.status}, Response: {await resp.text()}"); return None
        except asyncio.TimeoutError: logger.warning(f"Timeout weather fetch {location}"); return None
        except aiohttp.ClientError as e: logger.error(f"Network error weather fetch {location}", exc_info=e); return None
        except Exception as e: logger.error(f"Unexpected error weather fetch {location}", exc_info=e); return None

    def determine_mood(self, weather_data: Optional[Dict[str, Any]]) -> str:
        if not weather_data or 'weather' not in weather_data or not weather_data['weather']: weather_condition = "Default"; self.current_weather_description = "不明"
        else: weather_condition = weather_data['weather'][0].get('main', "Default"); self.current_weather_description = weather_data['weather'][0].get('description', "不明")
        mood_options = WEATHER_MOOD_MAP.get(weather_condition, WEATHER_MOOD_MAP["Default"]); return random.choice(mood_options)

    def get_current_mood(self) -> str: return self.current_mood

    async def update_mood_based_on_location(self, location: str) -> bool:
        await self._cog_ready.wait()
        weather_data = await self.get_weather_data(location)
        if weather_data:
            new_mood = self.determine_mood(weather_data)
            if new_mood != self.current_mood: logger.info(f"Mood changed ({location}): {self.current_mood} -> {new_mood}"); self.current_mood = new_mood
            else: logger.debug(f"Mood remains '{self.current_mood}' ({location}).")
            self.current_weather_location = location; self.last_weather_update = datetime.datetime.now(datetime.timezone.utc)
            # メモリ上の最終地点を更新 (保存は別途)
            config_manager.app_config.setdefault("weather_config", {})["last_location"] = location
            return True
        else: logger.warning(f"Failed to update mood based on {location}."); return False

    # --- スラッシュコマンド ---
    weather = app_commands.Group(name="weather", description="天気と気分に関するコマンド")
    @weather.command(name="update", description="指定場所（省略可）の天気で気分を更新。")
    @app_commands.describe(location="場所 (例: Tokyo)。省略時: 前回場所。")
    async def update_weather_mood(self, interaction: discord.Interaction, location: Optional[str] = None):
        await self._cog_ready.wait()
        await interaction.response.defer(ephemeral=True)
        target_location = location or config_manager.get_last_weather_location()
        if not target_location: await interaction.followup.send("場所が指定されていません。", ephemeral=True); return
        target_location = target_location.strip()
        if not target_location: await interaction.followup.send("場所名が空です。", ephemeral=True); return
        success = await self.update_mood_based_on_location(target_location)
        if success and self.current_weather_location:
             config_manager.save_app_config() # ユーザー操作による更新なので設定保存
             weather_data_for_msg = await self.get_weather_data(self.current_weather_location)
             temp = weather_data_for_msg.get("main", {}).get("temp", "N/A") if weather_data_for_msg else "N/A"
             feels_like = weather_data_for_msg.get("main", {}).get("feels_like", "N/A") if weather_data_for_msg else "N/A"
             description = self.current_weather_description or "不明"
             await interaction.followup.send(f"{self.current_weather_location.capitalize()} の天気更新。\n概要: {description}\n気温: {temp}°C (体感: {feels_like}°C)\n気分:「{self.current_mood}」", ephemeral=True)
        else: self.current_mood = random.choice(WEATHER_MOOD_MAP["Default"]); await interaction.followup.send(f"{target_location} の天気情報取得失敗。", ephemeral=True)

    @weather.command(name="show", description="現在の気分と天気情報を表示。")
    async def show_mood(self, interaction: discord.Interaction):
        await self._cog_ready.wait()
        await interaction.response.defer(ephemeral=True); mood = self.get_current_mood(); message = f"今の気分は「{mood}」です。"
        last_location = config_manager.get_last_weather_location();
        if last_location and self.last_weather_update:
             time_diff = datetime.datetime.now(datetime.timezone.utc) - self.last_weather_update; minutes_ago = int(time_diff.total_seconds() // 60)
             description_text = f" ({self.current_weather_description})" if self.current_weather_description else ""
             message += f"\n（{minutes_ago}分前に確認した {last_location.capitalize()} の天気{description_text} に基づく）"
        elif last_location: message += f"\n（最後に設定された場所: {last_location.capitalize()} の天気は未確認）"
        else: message += "\n（特定の天気に基づきません）"
        await interaction.followup.send(message, ephemeral=True)

    # --- 自動更新タスク ---
    # loopデコレータをメソッド定義の直前に置く
    # @tasks.loop(minutes=config_manager.get_weather_auto_update_interval()) # ここで呼ぶと初期化前に呼ばれる
    async def _auto_update_weather_task(self): # ★ ループ本体を別メソッドに
        """自動更新ループの本体"""
        await self._cog_ready.wait() # Cog準備完了待機
        location_to_update = config_manager.get_last_weather_location()
        if location_to_update:
             logger.info(f"[AutoUpdate] Updating weather for {location_to_update}...")
             success = await self.update_mood_based_on_location(location_to_update)
             if success: logger.info(f"[AutoUpdate] Successfully updated weather for {location_to_update}.")
             else: logger.warning(f"[AutoUpdate] Failed to update weather for {location_to_update}.")
             # 自動更新成功時は設定を保存しない（last_locationの変更のみのため）
        else: logger.debug("[AutoUpdate] Skipping auto weather update, location not set.")

    @_auto_update_weather_task.before_loop # ★ before_loop を本体メソッドに適用
    async def before_auto_update_weather(self):
        await self.bot.wait_until_ready()
        await self._cog_ready.wait()
        logger.info("Auto weather update loop is ready to start.")

async def setup(bot: commands.Bot):
    await bot.add_cog(WeatherMoodCog(bot))
    # ロード完了ログは Cog の async_init 内に移動
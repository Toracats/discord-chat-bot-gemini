# cogs/weather_mood_cog.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import os
import random
import asyncio
import aiohttp # 非同期HTTPリクエスト用
import datetime # datetime をインポート
from typing import Optional, Dict, Any

# config_manager をインポート
from utils import config_manager

logger = logging.getLogger(__name__)

# 天気APIのエンドポイントとキー
WEATHER_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
WEATHER_API_URL = "http://api.openweathermap.org/data/2.5/weather"

# 天気と気分のマッピング例
WEATHER_MOOD_MAP = {
    "Clear": ["元気いっぱい☀️", "とても機嫌が良い✨", "清々しい気分！"],
    "Clouds": ["落ち着いている☁️", "まあまあかな", "少し考え事中..."],
    "Rain": ["少し憂鬱☔", "静かに過ごしたい気分...", "雨音を聞いている"],
    "Drizzle": ["ちょっとだけアンニュイ💧", "穏やかな気分", "濡れるのは嫌だなぁ"],
    "Thunderstorm": ["ちょっとびっくり⚡️", "ドキドキしてる", "家にいたい気分"],
    "Snow": ["わくわくする❄️", "静かで綺麗だね", "寒いけど楽しい！"],
    "Mist": ["幻想的な気分...", "周りがよく見えないね", "しっとりしてる"],
    "Smoke": ["空気が悪いね...🌫️", "ちょっと心配", "視界が悪いなぁ"],
    "Haze": ["もやっとしてる", "遠くが見えないね", "少し眠いかも"],
    "Dust": ["砂っぽいね...", "目がしょぼしょぼするかも", "早く収まらないかな"],
    "Fog": ["霧が深いね...", "迷子になりそう", "幻想的だけど少し不安"],
    "Default": ["普通かな", "特に変わりないよ", "いつも通り"]
}


class WeatherMoodCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # config_manager から最後に保存された場所を読み込む
        self.current_weather_location: Optional[str] = config_manager.get_last_weather_location()
        self.current_mood: str = random.choice(WEATHER_MOOD_MAP["Default"]) # 初期気分
        self.current_weather_description: Optional[str] = None
        self.last_weather_update: Optional[datetime.datetime] = None
        self._api_key_error_logged = False # APIキーエラーログ用フラグ

        # 起動時に保存された場所があれば天気と気分を更新するタスクを開始
        if self.current_weather_location:
            self.bot.loop.create_task(self.initial_weather_mood_update())

        # ★ 自動更新タスクを開始
        self.auto_update_weather.start()
        logger.info("WeatherMoodCog loaded.")
        if self.current_weather_location:
             logger.info(f" Initial weather location loaded: {self.current_weather_location}")

    async def initial_weather_mood_update(self):
        """Cogロード時に保存された場所の天気で気分を初期化"""
        await self.bot.wait_until_ready() # Botの準備完了を待つ
        if self.current_weather_location:
             logger.info(f"Performing initial weather update for {self.current_weather_location}...")
             await self.update_mood_based_on_location(self.current_weather_location)


    def cog_unload(self):
        # ★ 自動更新タスクを停止
        self.auto_update_weather.cancel()
        logger.info("WeatherMoodCog unloaded.")

    async def get_weather_data(self, location: str) -> Optional[Dict[str, Any]]:
        """指定された場所の天気データを取得する"""
        if not WEATHER_API_KEY:
            if not self._api_key_error_logged:
                logger.error("OpenWeatherMap API Key (OPENWEATHERMAP_API_KEY) not found in environment variables.")
                self._api_key_error_logged = True
            return None
        self._api_key_error_logged = False

        params = {
            'q': location,
            'appid': WEATHER_API_KEY,
            'units': 'metric',
            'lang': 'ja'
        }
        try:
            # タイムアウトを設定 (例: 10秒)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(WEATHER_API_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        weather_desc = data.get('weather', [{}])[0].get('description', 'N/A')
                        logger.info(f"Weather data fetched successfully for {location}: {weather_desc}")
                        return data
                    elif resp.status == 401:
                         logger.error(f"Failed to fetch weather data for {location}. Status: 401 Unauthorized. Please check your OPENWEATHERMAP_API_KEY.")
                         return None
                    elif resp.status == 404:
                         logger.warning(f"Failed to fetch weather data for {location}. Status: 404 Not Found. Check if the location name is correct: '{location}'")
                         return None
                    else:
                        logger.error(f"Failed to fetch weather data for {location}. Status: {resp.status}, Response: {await resp.text()}")
                        return None
        except asyncio.TimeoutError:
             logger.warning(f"Timeout error fetching weather data for {location}")
             return None
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching weather data for {location}", exc_info=e)
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching weather data for {location}", exc_info=e)
            return None

    def determine_mood(self, weather_data: Optional[Dict[str, Any]]) -> str:
        """天気データに基づいて気分を決定する"""
        if not weather_data or 'weather' not in weather_data or not weather_data['weather']:
            weather_condition = "Default"
            self.current_weather_description = "不明"
        else:
            weather_condition = weather_data['weather'][0].get('main', "Default")
            self.current_weather_description = weather_data['weather'][0].get('description', "不明")

        mood_options = WEATHER_MOOD_MAP.get(weather_condition, WEATHER_MOOD_MAP["Default"])
        return random.choice(mood_options)

    def get_current_mood(self) -> str:
        """現在の気分を返す"""
        return self.current_mood

    async def update_mood_based_on_location(self, location: str) -> bool:
        """指定された場所の天気で気分を更新し、成否を返す内部メソッド"""
        weather_data = await self.get_weather_data(location)
        if weather_data:
            new_mood = self.determine_mood(weather_data)
            if new_mood != self.current_mood:
                logger.info(f"Mood changed based on weather in {location}: {self.current_mood} -> {new_mood}")
                self.current_mood = new_mood
            else:
                logger.debug(f"Mood remains '{self.current_mood}' based on weather in {location}.")

            self.current_weather_location = location # APIが受け付けた場所を保存
            self.last_weather_update = datetime.datetime.now(datetime.timezone.utc)
            await config_manager.update_last_weather_location_async(location)
            return True
        else:
            logger.warning(f"Failed to update mood based on {location}.")
            # 失敗した場合、前回成功時の場所を維持する
            # await config_manager.update_last_weather_location_async(None) # 場所はリセットしない
            return False


    # --- スラッシュコマンド ---
    weather = app_commands.Group(name="weather", description="天気と気分に関するコマンド")

    @weather.command(name="update", description="指定した場所（省略時: 前回指定場所）の天気で気分を更新します。")
    @app_commands.describe(location="天気を取得する場所 (例: Tokyo)。省略すると前回指定した場所を使います。")
    async def update_weather_mood(self, interaction: discord.Interaction, location: Optional[str] = None):
        """天気情報を更新し、気分を設定するコマンド"""
        await interaction.response.defer(ephemeral=True)

        target_location = location or self.current_weather_location

        if not target_location:
            await interaction.followup.send("場所が指定されておらず、前回指定した場所もありません。`location`を指定してください。", ephemeral=True)
            return

        # target_location の前後の空白を除去
        target_location = target_location.strip()
        if not target_location:
            await interaction.followup.send("場所名が空です。有効な場所を指定してください。", ephemeral=True)
            return

        success = await self.update_mood_based_on_location(target_location)

        if success and self.current_weather_location:
             # 表示用の天気情報を取得 (更新直後のためキャッシュされたデータを使う)
             weather_data_for_msg = await self.get_weather_data(self.current_weather_location)
             temp = weather_data_for_msg.get("main", {}).get("temp", "N/A") if weather_data_for_msg else "N/A"
             feels_like = weather_data_for_msg.get("main", {}).get("feels_like", "N/A") if weather_data_for_msg else "N/A"
             description = self.current_weather_description or "不明"

             await interaction.followup.send(
                 f"{self.current_weather_location.capitalize()} の天気情報を取得・更新しました。\n"
                 f"概要: {description}\n"
                 f"気温: {temp}°C (体感: {feels_like}°C)\n"
                 f"今の気分は「{self.current_mood}」みたいです。",
                 ephemeral=True
             )
        else:
            self.current_mood = random.choice(WEATHER_MOOD_MAP["Default"]) # 失敗時はデフォルト気分に
            await interaction.followup.send(f"{target_location} の天気情報を取得できませんでした。場所名が正しいか、APIキーが有効か確認してください。気分はリセットされます。", ephemeral=True)


    @weather.command(name="show", description="Botの現在の気分と最後に参照した天気情報を表示します。")
    async def show_mood(self, interaction: discord.Interaction):
        """現在の気分と天気情報を表示するコマンド"""
        await interaction.response.defer(ephemeral=True)
        mood = self.get_current_mood()
        message = f"今の気分は「{mood}」です。"
        last_location = config_manager.get_last_weather_location() # ファイルから最新の場所を取得

        if last_location and self.last_weather_update:
             time_diff = datetime.datetime.now(datetime.timezone.utc) - self.last_weather_update
             minutes_ago = int(time_diff.total_seconds() // 60)
             description_text = f" ({self.current_weather_description})" if self.current_weather_description else ""
             message += f"\n（{minutes_ago}分前に確認した {last_location.capitalize()} の天気{description_text} に基づいています）"
        elif last_location:
             message += f"\n（最後に設定された場所: {last_location.capitalize()} の天気はまだ確認していません）"
        else:
            message += "\n（特定の天気には基づいていません）"
        await interaction.followup.send(message, ephemeral=True)

    # --- 自動更新タスク (毎分実行) ---
    # ★ 注意: API制限に達する可能性が非常に高いです！
    @tasks.loop(minutes=30)
    async def auto_update_weather(self):
        # config_manager から最新の保存場所を取得
        location_to_update = config_manager.get_last_weather_location()
        if location_to_update:
            logger.info(f"Auto-updating weather for {location_to_update}...")
            await self.update_mood_based_on_location(location_to_update)
        else:
            logger.debug("Skipping auto weather update, location not set in config.")

    @auto_update_weather.before_loop
    async def before_auto_update_weather(self):
        await self.bot.wait_until_ready()
        logger.info("Auto weather update loop is ready.")


# CogをBotに登録するためのセットアップ関数
async def setup(bot: commands.Bot):
    if not WEATHER_API_KEY:
        logger.error("OPENWEATHERMAP_API_KEY is not set. WeatherMoodCog will not be fully functional.")
    await bot.add_cog(WeatherMoodCog(bot))
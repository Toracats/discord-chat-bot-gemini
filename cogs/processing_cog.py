import discord
from discord.ext import commands
import logging
from typing import List, Optional
import io # バイトデータ処理用
import aiohttp # 非同期HTTPリクエスト用
from PIL import Image # 画像処理用
import PyPDF2 # PDF処理用
import requests # URL取得用 (同期的なので注意)
from bs4 import BeautifulSoup # HTMLパース用
from youtube_transcript_api import YouTubeTranscriptApi # YouTube文字起こし用
import urllib.parse as urlparse
import asyncio


# genai.types などをインポート
from google.genai import types as genai_types
from utils import helpers # URL抽出など

logger = logging.getLogger(__name__)

class ProcessingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("ProcessingCog loaded.")

    async def process_attachments(self, attachments: List[discord.Attachment]) -> List[genai_types.Part]:
        """添付ファイルを処理し、genai.types.Partのリストを返す"""
        parts = []
        if not attachments:
            return parts

        async with aiohttp.ClientSession() as session:
            for attachment in attachments:
                logger.info(f"Processing attachment: {attachment.filename} ({attachment.content_type})")
                try:
                    # --- 画像ファイル処理 ---
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                         async with session.get(attachment.url) as resp:
                             if resp.status == 200:
                                 image_bytes = await resp.read()
                                 # Pillowで開けるか確認 (任意)
                                 try:
                                     img = Image.open(io.BytesIO(image_bytes))
                                     img.verify() # 簡単な検証
                                     # MIMEタイプを discord から取得 or Pillow から推測
                                     mime_type = attachment.content_type or Image.MIME.get(img.format) or "image/png" # デフォルト
                                     parts.append(genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)))
                                     logger.info(f"Added image part: {attachment.filename}")
                                 except Exception as img_e:
                                     logger.warning(f"Could not process image file {attachment.filename} with Pillow.", exc_info=img_e)
                                     # Pillowで処理できなくても、そのまま渡してみる場合
                                     # mime_type = attachment.content_type or "application/octet-stream"
                                     # parts.append(genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)))
                             else:
                                 logger.warning(f"Failed to download image: {attachment.url} (status: {resp.status})")
                    # --- PDFファイル処理 ---
                    elif attachment.content_type == "application/pdf" or attachment.filename.lower().endswith(".pdf"):
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                pdf_bytes = await resp.read()
                                text = await self._extract_text_from_pdf_bytes_pypdf2(pdf_bytes)
                                if text:
                                    parts.append(genai_types.Part(text=f"--- PDFの内容 ({attachment.filename}) ---\n{text}\n--- PDFの内容ここまで ---"))
                                    logger.info(f"Added extracted PDF text part: {attachment.filename}")
                                else:
                                    logger.warning(f"Failed to extract text from PDF: {attachment.filename}")
                            else:
                                logger.warning(f"Failed to download PDF: {attachment.url} (status: {resp.status})")
                    # --- テキストファイル処理 (例) ---
                    elif attachment.content_type and attachment.content_type.startswith("text/"):
                         async with session.get(attachment.url) as resp:
                             if resp.status == 200:
                                 try:
                                     text_content = await resp.text(encoding='utf-8') # UTF-8でデコード試行
                                     parts.append(genai_types.Part(text=f"--- 添付ファイルの内容 ({attachment.filename}) ---\n{text_content}\n--- 添付ファイルの内容ここまで ---"))
                                     logger.info(f"Added text attachment part: {attachment.filename}")
                                 except UnicodeDecodeError:
                                      logger.warning(f"Could not decode text attachment {attachment.filename} as UTF-8.")
                                 except Exception as txt_e:
                                      logger.error(f"Error reading text attachment {attachment.filename}", exc_info=txt_e)

                             else:
                                 logger.warning(f"Failed to download text attachment: {attachment.url} (status: {resp.status})")
                    else:
                        logger.info(f"Skipping unsupported attachment type: {attachment.filename} ({attachment.content_type})")

                except Exception as e:
                    logger.error(f"Error processing attachment {attachment.filename}", exc_info=e)
        return parts

    async def _extract_text_from_pdf_bytes_pypdf2(self, pdf_bytes: bytes) -> Optional[str]:
        """PDFバイトデータからPyPDF2を使ってテキストを抽出 (非同期化は難しいので同期的に実行)"""
        text = ""
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            for page in pdf_reader.pages:
                try:
                    text += page.extract_text() or "" # extract_textがNoneを返す場合がある
                except Exception as page_e:
                    logger.warning(f"Error extracting text from a PDF page", exc_info=page_e)
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"PyPDF2 Error extracting text from PDF", exc_info=e)
            return None

    async def process_url_in_message(self, text: str) -> List[genai_types.Part]:
        """メッセージ内のURLを処理し、内容をPartとして返す"""
        parts = []
        url = helpers.extract_url(text) # ヘルパー関数でURL抽出
        if not url:
            return parts

        logger.info(f"Processing URL found in message: {url}")
        try:
            # --- YouTube URL処理 ---
            if helpers.is_youtube_url(url):
                 video_id = helpers.get_video_id(url)
                 if video_id:
                     # youtube_transcript_apiは同期的だが、量が少なければ許容範囲か
                     # 大量になる場合は asyncio.to_thread を検討
                     try:
                         transcript_list = await asyncio.to_thread(YouTubeTranscriptApi.get_transcript, video_id, languages=['ja', 'en']) # 日本語優先、英語も試す
                         transcript = ' '.join([t['text'] for t in transcript_list])
                         if transcript:
                             parts.append(genai_types.Part(text=f"--- YouTube動画の文字起こし ({url}) ---\n{transcript}\n--- 文字起こしここまで ---"))
                             logger.info(f"Added YouTube transcript part for video ID: {video_id}")
                         else:
                             logger.warning(f"Empty transcript for YouTube video ID: {video_id}")
                     except Exception as yt_e:
                         logger.error(f"Failed to get YouTube transcript for video ID: {video_id}", exc_info=yt_e)
                 else:
                     logger.warning(f"Could not extract video ID from YouTube URL: {url}")

            # --- 一般的なWebページ処理 ---
            else:
                # requestsは同期的ライブラリなので、非同期ループをブロックする
                # aiohttp を使うか、asyncio.to_thread を使う
                loop = asyncio.get_running_loop()
                extracted_text = await loop.run_in_executor(None, self._extract_text_from_general_url, url) # 同期関数を別スレッドで実行
                if extracted_text:
                    parts.append(genai_types.Part(text=f"--- Webページの内容 ({url}) ---\n{extracted_text[:2000]}\n--- Webページの内容ここまで ---")) # 長すぎる場合があるので切り詰める
                    logger.info(f"Added web page content part for URL: {url}")
                else:
                    logger.warning(f"Failed to extract text from URL: {url}")

        except Exception as e:
            logger.error(f"Error processing URL {url}", exc_info=e)

        return parts

    def _extract_text_from_general_url(self, url: str) -> Optional[str]:
         """一般的なURLからテキストを抽出する (同期関数)"""
         headers = {"User-Agent": "Mozilla/5.0"} # シンプルなUA
         try:
             # requestsはタイムアウトを設定する
             response = requests.get(url, headers=headers, timeout=10)
             response.raise_for_status() # エラーチェック
             soup = BeautifulSoup(response.text, 'html.parser')
             # scriptタグやstyleタグを除去
             for script_or_style in soup(["script", "style"]):
                 script_or_style.decompose()
             # 主要なテキスト要素を取得 (body全体を取得して不要な空白を除去する方が確実かも)
             # text = ' '.join(p.get_text() for p in soup.find_all('p'))
             text = soup.body.get_text(separator=' ', strip=True) if soup.body else ""
             return ' '.join(text.split()) # 連続する空白をまとめる
         except requests.exceptions.RequestException as req_e:
             logger.warning(f"Failed to retrieve URL {url}: {req_e}")
             return None
         except Exception as e:
             logger.error(f"Error scraping URL {url}", exc_info=e)
             return None


async def setup(bot: commands.Bot):
    await bot.add_cog(ProcessingCog(bot))
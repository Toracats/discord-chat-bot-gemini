# utils/helpers.py (remove_all_prefixes をテキスト全体対象に修正)

import re
import discord # split_and_send_messages で必要
import logging # logging をインポート
import urllib.parse as urlparse # is_youtube_url, get_video_id で必要
from typing import Optional, List
import asyncio # split_and_send_messages で必要

logger = logging.getLogger(__name__) # ロガーを取得

def clean_discord_message(input_string: str) -> str:
    """Discordメッセージからメンションなどを削除"""
    # <@...> や <#...> などを削除
    mention_pattern = re.compile(r'<[@#][!&]?\d+>')
    cleaned_content = mention_pattern.sub('', input_string).strip()
    # everyone/here も削除 (任意)
    cleaned_content = cleaned_content.replace('@everyone', '').replace('@here', '')
    return cleaned_content

def remove_citation_marks(text: str) -> str:
    """AI応答から引用符（例: [1], [2]）を削除する"""
    if not text: return "" # None や空文字の場合空文字を返す
    citation_pattern = re.compile(r'\[\d+\]')
    return citation_pattern.sub('', text).strip()

def remove_all_prefixes(text: str) -> str:
    """応答テキストから全ての [名前]: 形式のプレフィックスを除去する"""
    if not text: return ""
    # ★★★ 正規表現を修正: 行頭指定(^)を削除し、テキスト全体から検索 ★★★
    # \s*     : 任意個の空白 (プレフィックス前の空白も除去対象に)
    # \[.*?\] : 角括弧で囲まれた最短一致の任意文字列 (名前部分)
    # :       : コロン
    # \s*     : 任意個の空白 (プレフィックス後の空白も除去対象に)
    prefix_pattern = re.compile(r'\s*\[.*?]:\s*')
    # ★★★ テキスト全体から該当パターンを空文字に置換 ★★★
    # re.sub は一致する全ての箇所を置換するので、while ループは不要
    cleaned_text = prefix_pattern.sub('', text)
    # 念のため、処理後の前後の空白を除去
    return cleaned_text.strip()


async def split_and_send_messages(message_system: discord.Message, text: str, max_length: int):
    """メッセージを分割して送信 (エラーハンドリング強化)"""
    if not text or not text.strip(): # 空文字列や空白のみの場合は送信しない
        logger.warning(f"split_and_send_messages called with empty text for message {message_system.id}. Skipping.")
        return

    messages_to_send = []
    # max_length より小さい場合でもリストに追加
    if len(text) <= max_length:
        messages_to_send.append(text)
    else:
        # 長いメッセージを分割
        start = 0
        while start < len(text):
            # 区切り位置を探す（改行、句読点、スペースなど）
            end = start + max_length
            split_pos = -1 # 初期化
            if end < len(text):
                # 境界を探す範囲を限定
                search_start = max(start, end - 100) # 区切りは max_length 付近で見つける
                # 改行を最優先
                split_pos = text.rfind('\n', search_start, end)
                if split_pos == -1:
                    # 次に句読点（全角・半角）を探す
                    split_pos = max(text.rfind('。', search_start, end), text.rfind('、', search_start, end),
                                    text.rfind('！', search_start, end), text.rfind('？', search_start, end),
                                    text.rfind('.', search_start, end), text.rfind(',', search_start, end),
                                    text.rfind('!', search_start, end), text.rfind('?', search_start, end))
                if split_pos == -1:
                     # 次にスペース
                     split_pos = text.rfind(' ', search_start, end)

                # 適切な区切りが見つからない、または区切りが前すぎる場合はmax_lengthで強制分割
                if split_pos == -1 or split_pos < start :
                    split_pos = end -1 # max_lengthギリギリで区切る

                end = split_pos + 1 # 区切り文字の次を次の開始位置にする
            else: # 残りが max_length 以下の場合
                 end = len(text)

            sub_message = text[start:end]
            if sub_message.strip(): # 空白のみのチャンクは追加しない
                 messages_to_send.append(sub_message)
            start = end

    first_message = True
    for i, chunk in enumerate(messages_to_send):
        try:
            if not chunk or not chunk.strip(): # 再度空チェック
                 logger.warning(f"Skipping empty chunk {i+1}/{len(messages_to_send)} for message {message_system.id}")
                 continue

            logger.debug(f"Sending chunk {i+1}/{len(messages_to_send)} (length: {len(chunk)}) for message {message_system.id}")
            if first_message:
                if message_system.interaction is None:
                     if isinstance(message_system.channel, discord.DMChannel): await message_system.channel.send(chunk)
                     else: await message_system.reply(chunk, mention_author=False)
                else: await message_system.channel.send(chunk)
                first_message = False
            else:
                await message_system.channel.send(chunk)
            await asyncio.sleep(0.6) # 少し待機

        except discord.HTTPException as e:
            logger.error(f"HTTPException sending chunk {i+1}/{len(messages_to_send)}: {e.status} {e.code} {e.text}", exc_info=False)
            try: await message_system.channel.send(f"(メッセージの一部送信に失敗: Discord APIエラー {e.code})")
            except Exception: logger.error(f"Failed send error notification.")
            break
        except Exception as e:
            logger.error(f"Unexpected error sending chunk {i+1}/{len(messages_to_send)}", exc_info=True)
            try: await message_system.channel.send(f"(メッセージの一部送信中に予期せぬエラー)")
            except Exception: logger.error(f"Failed send unexpected error notification.")
            break


def extract_url(string: str) -> Optional[str]:
    """文字列からURLを抽出"""
    url_regex = re.compile(
        r'https?://' r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|' r'localhost|' r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' r'(?::\d+)?' r'(?:/?|[/?]\S+)', re.IGNORECASE)
    match = re.search(url_regex, string)
    if match:
        url = match.group(0)
        preceding_char = string[match.start()-1:match.start()] if match.start() > 0 else ""
        following_char = string[match.end():match.end()+1] if match.end() < len(string) else ""
        if preceding_char == '(' and following_char == ')':
             second_match = re.search(url_regex, string[match.end():])
             return second_match.group(0) if second_match else None
        return url
    return None


def is_youtube_url(url: Optional[str]) -> bool:
    """URLがYouTube URLか判定"""
    if url is None: return False
    youtube_regex = re.compile( r'(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})' )
    return bool(youtube_regex.match(url))

def get_video_id(url: Optional[str]) -> Optional[str]:
    """YouTube URLから動画IDを抽出"""
    if url is None: return None
    patterns = [ r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', r'(?:embed\/|shorts\/)([0-9A-Za-z_-]{11})', r'youtu\.be\/([0-9A-Za-z_-]{11})' ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match: return match.group(1)
    return None
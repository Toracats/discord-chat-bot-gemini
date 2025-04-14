# utils/helpers.py (プレフィックス除去強化、エラーハンドリング強化)

import re
import discord # split_and_send_messages で必要
import logging # logging をインポート
import urllib.parse as urlparse # is_youtube_url, get_video_id で必要
from typing import Optional, List # remove_all_prefixes で List を使用
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
    # 行頭の [任意の文字列]: を繰り返し除去する正規表現
    # ^: 行頭, \s*: 先頭の空白(任意), \[.*?\]: 角括弧で囲まれた任意の文字列(最短一致), :\s*: コロンと後続の空白(任意)
    prefix_pattern = re.compile(r'^\s*\[.*?]:\s*')
    cleaned_text = text
    # プレフィックスがなくなるまで繰り返し除去
    while True:
        new_text = prefix_pattern.sub('', cleaned_text, count=1) # count=1 で行頭の最初のものだけ置換
        # 変更がなければループ終了
        if new_text == cleaned_text:
            break
        cleaned_text = new_text.lstrip() # 除去後の先頭空白を削除
    return cleaned_text


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
                # (split_pos == -1 or split_pos < start) の条件を追加して、startより前にならないように
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
                # replyは一度しか使えないので、2回目以降はsend
                # ephemeralでない通常のメッセージへの応答と仮定
                # もしインタラクション応答なら interaction.followup.send を使う
                if message_system.interaction is None:
                     # DMチャンネルかサーバーチャンネルかで分岐
                     if isinstance(message_system.channel, discord.DMChannel):
                         await message_system.channel.send(chunk) # DMではreplyできないことがあるためsend
                     else:
                         await message_system.reply(chunk, mention_author=False)
                else:
                     # スラッシュコマンド等のインタラクションへの応答の場合
                     await message_system.channel.send(chunk) # replyの代わりにsendを使う

                first_message = False
            else:
                await message_system.channel.send(chunk)
            # Discord APIのレート制限を考慮して少し待機（任意だが推奨）
            await asyncio.sleep(0.6) # 少し長めに

        except discord.HTTPException as e:
            logger.error(f"HTTPException sending chunk {i+1}/{len(messages_to_send)} (length: {len(chunk)}): {e.status} {e.code} {e.text}", exc_info=False)
            try:
                 # エラーが発生したことを元のメッセージチャンネルに通知（失敗する可能性もある）
                 await message_system.channel.send(f"(メッセージの一部送信に失敗しました: Discord APIエラー {e.code})")
            except Exception:
                 logger.error(f"Failed to send error notification about chunk failure.")
            break # エラーが発生したら以降のチャンク送信を中止
        except Exception as e:
            logger.error(f"Unexpected error sending chunk {i+1}/{len(messages_to_send)}", exc_info=True)
            try:
                 await message_system.channel.send(f"(メッセージの一部送信中に予期せぬエラーが発生しました)")
            except Exception:
                 logger.error(f"Failed to send unexpected error notification about chunk failure.")
            break


def extract_url(string: str) -> Optional[str]:
    """文字列からURLを抽出"""
    # より多くの TLD を考慮し、括弧などでの終端を避けるように改良（完璧ではない）
    url_regex = re.compile(
        r'https?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|' # domain...
        r'localhost|' # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+)', re.IGNORECASE)
    match = re.search(url_regex, string)
    # Markdownリンク形式でないことを確認 (例: [text](url))
    if match:
        url = match.group(0)
        # URLの前後にMarkdownリンクの括弧がないかチェック
        preceding_char = string[match.start()-1:match.start()] if match.start() > 0 else ""
        following_char = string[match.end():match.end()+1] if match.end() < len(string) else ""
        if preceding_char == '(' and following_char == ')':
             # Markdownリンク内のURLの可能性があるため、再度検索
             second_match = re.search(url_regex, string[match.end():])
             return second_match.group(0) if second_match else None
        # URLの末尾が意図しない文字で終わっていないか少しチェック (例: 。、！)
        # url = url.rstrip('。、！)?,.;') # やりすぎると正規のURLを壊す可能性もあるので注意
        return url
    return None


def is_youtube_url(url: Optional[str]) -> bool:
    """URLがYouTube URLか判定"""
    if url is None:
        return False
    youtube_regex = re.compile(
        r'(?:https?://)?(?:www\.)?'
        r'(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)'
        r'([a-zA-Z0-9_-]{11})'
    )
    return bool(youtube_regex.match(url))

def get_video_id(url: Optional[str]) -> Optional[str]:
    """YouTube URLから動画IDを抽出"""
    if url is None:
        return None
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/|shorts\/)([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None
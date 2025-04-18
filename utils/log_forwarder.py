# utils/log_forwarder.py
import logging
import queue
import threading
import time
from typing import Optional

# pypubsub ライブラリが必要: pip install pypubsub
try:
    from pubsub import pub
except ImportError:
    print("Error: PyPubSub library not found. Please install it using: pip install pypubsub")
    pub = None # pubsubがなければ何もしないようにする

# GUIスレッドで処理するためのスレッドセーフなキュー
log_queue = queue.Queue()
_forwarding_thread = None
_stop_forwarding = threading.Event()

class PubSubLogHandler(logging.Handler):
    """
    ログレコードを受け取り、スレッドセーフなキューに入れるハンドラ。
    """
    def __init__(self, level=logging.NOTSET):
        super().__init__(level=level)

    def emit(self, record: logging.LogRecord):
        # ログレコードが停止イベントでなければキューに入れる
        if not _stop_forwarding.is_set():
            try:
                # format() で整形済みの文字列にする
                log_entry = self.format(record)
                log_queue.put_nowait(log_entry) # ノンブロッキングでキューに追加
            except queue.Full:
                 print("Log queue is full, discarding log message.") # キューが満杯の場合
            except Exception as e:
                 print(f"Error putting log into queue: {e}")


def start_log_forwarding() -> Optional[threading.Thread]:
    """
    バックグラウンドスレッドでキューを監視し、PubSubトピックに送信する。
    既に実行中の場合は何もしない。
    """
    global _forwarding_thread
    if _forwarding_thread and _forwarding_thread.is_alive():
        print("Log forwarding thread is already running.")
        return _forwarding_thread

    if pub is None:
         print("Cannot start log forwarding: PyPubSub library not loaded.")
         return None

    _stop_forwarding.clear() # 停止イベントをリセット

    def worker():
        print("Log forwarding worker thread started.")
        while not _stop_forwarding.is_set():
            try:
                # キューからログエントリを取得 (タイムアウト付きで待機)
                log_entry = log_queue.get(block=True, timeout=0.5) # 0.5秒ごとに停止を確認
                if log_entry is None: # 終了シグナル (stop_log_forwarding から送られる)
                    print("Log forwarding worker thread received None, preparing to exit.")
                    break # ループを抜ける

                # PubSubで 'log_message' トピックに送信 (pubがインポート成功していれば)
                pub.sendMessage('log_message', log_entry=log_entry)
                # print(f"Forwarded log via PubSub: {log_entry[:50]}...") # デバッグ用
                log_queue.task_done()

            except queue.Empty:
                # タイムアウトした場合は停止フラグを再確認してループ継続
                continue
            except Exception as e:
                 print(f"Error in log forwarding worker: {e}")
                 # エラー発生時は少し待機
                 time.sleep(0.5)

        # ループ終了後
        print("Log forwarding worker thread finished.")
        # 残っているキューの処理 (オプション)
        while not log_queue.empty():
             try:
                 remaining_entry = log_queue.get_nowait()
                 if remaining_entry is not None and pub:
                      pub.sendMessage('log_message', log_entry=f"[Queued] {remaining_entry}")
                 log_queue.task_done()
             except queue.Empty:
                 break
             except Exception as e:
                  print(f"Error processing remaining log queue: {e}")


    # デーモンスレッドとしてワーカーを開始
    _forwarding_thread = threading.Thread(target=worker, name="LogForwarderThread", daemon=True)
    _forwarding_thread.start()
    print("Log forwarding thread initiated.")
    return _forwarding_thread

def stop_log_forwarding():
     """ログ転送スレッドに終了を通知する"""
     print("Signaling log forwarding thread to stop...")
     _stop_forwarding.set() # 停止フラグをセット
     log_queue.put(None) # ワーカーループを確実に抜けるための終了シグナル

     # スレッドの終了を待つ (オプション、GUI終了時など)
     # global _forwarding_thread
     # if _forwarding_thread and _forwarding_thread.is_alive():
     #     _forwarding_thread.join(timeout=2.0) # 最大2秒待つ
     #     if _forwarding_thread.is_alive():
     #         print("Log forwarding thread did not stop within timeout.")
     #     else:
     #         print("Log forwarding thread stopped.")
     #     _forwarding_thread = None
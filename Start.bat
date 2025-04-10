@echo off

:: バッチファイル自身のディレクトリを取得
set BAT_DIR=%~dp0

:: 仮想環境をアクティベート
call "%BAT_DIR%..\venv\Scripts\activate.bat"

:: GeminiDiscordBot.py が存在するディレクトリへ移動
cd /d "%BAT_DIR%"

:: Pythonスクリプトを実行
python main.py

pause
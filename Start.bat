@echo off

:: バッチファイル自身のディレクトリを取得
set BAT_DIR=%~dp0

:: 仮想環境をアクティベート
call "%BAT_DIR%..\venv\Scripts\activate.bat"

:: GeminiDiscordBot.py が存在するディレクトリへ移動
cd /d "%BAT_DIR%\gui"

:: Pythonスクリプトを実行
flet run main_gui.py

pause
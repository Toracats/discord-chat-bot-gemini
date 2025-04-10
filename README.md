# Discord Chat Bot (Gemini API)

これは、Google Gemini API と discord.py を使用して構築された多機能DiscordチャットBotです。
<br>Discordのスラッシュコマンドを使用することで、Botの再起動なしにカスタマイズ可能です。
<br>改変・再配布などはご自由にどうぞ。(MITライセンス)
<br>バグがあればできる限り改善するので、Twitterの@New_Tora_Cats宛てにDMしてください。

## 主な機能

*   **AIチャット**
    *   Google Gemini API (例: gemini-2.0-flash) を利用した自然な会話応答。
    *   Botへのダイレクトメッセージ(DM)、あるいは指定したチャンネル内のメッセージに自動で応答します。
    *   ユーザーごとにニックネームを設定可能です。（時々呼び間違えます、指摘すると治ります）
*   **マルチモーダル対応**
    *   添付された画像の内容を認識して応答します。
    *   メッセージ内のURLを読み取り、内容を要約できます。
    <br>（YouTubeのURLを送信した場合、字幕・文字起こしに基づいた応答をします）
    *   添付されたPDFファイルのテキストを抽出して内容を理解します。
*   **会話履歴**
    *   Botが関与したすべての会話（DM、指定チャンネル）は、単一の履歴に保存されるので、会話相手や会話場所が変わってもシームレスに会話をつなげることができます。
    <br>（機密データや、他人に知られたくない内容は会話で扱わないでください）
    *   履歴の保持件数はコマンドで設定可能です。
    *   スラッシュコマンドでグローバル履歴のクリア（全体、ユーザー関連、チャンネル別）が可能です。
*   **ランダムDM機能**
    *   ユーザーは自身のランダムDM受信設定（有効/無効、インターバル、送信停止時間帯）を管理できます。
    *   設定に基づき、Botからユーザーへランダムな間隔でDMを送信して会話を開始します。
    <br>(例：定期的に「さみしいよ～」的なメッセージを送ってもらえる)
    *   ユーザーが何らかの形で返信をすると、そのユーザーへのDM送信タイマーはリセットされます。
*   **「今の気分」機能**
    *   コマンドで指定された場所の天気情報を外部API(OpenWeatherMap)から取得します。
    *   天気に応じてBotの「気分」がランダムに設定されます。
    *   設定された気分はシステムプロンプトに反映され、AIの応答に影響を与えます。
    *   天気情報は定期的に自動更新されます（デフォルトでは毎分）。
*   **設定管理**
    *   Botの動作に関するほぼすべての設定（AIモデル、生成パラメータ、安全性設定、応答文字数、許可チャンネル、プロンプト等）は、Discordのスラッシュコマンドで変更可能です。
    *   設定はJSONあるいはTXTファイルに保存され、Botを再起動しても保持されます。

## セットアップ方法

1.  **リポジトリのクローン:**
    ```bash
    git clone https://github.com/your-username/your-repository-name.git
    cd your-repository-name
    ```
2.  **Python環境の準備:**
    *   Python 3.10以降がインストールされていることを確認してください。
    *   仮想環境の作成と有効化を推奨します。
        ```bash
        python -m venv venv
        # Windows
        .\venv\Scripts\activate
        # macOS/Linux
        source venv/bin/activate
        ```
3.  **必要なライブラリのインストール:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **APIキーとトークンの設定:**
    *   リポジトリのルートディレクトリに `.env` という名前のファイルを作成します。
    *   以下の内容を `.env` ファイルに記述し、それぞれの値を実際のキーやトークン、パスワードに置き換えます。
        ```dotenv
        DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN
        GOOGLE_AI_KEY=YOUR_GEMINI_API_KEY
        OPENWEATHERMAP_API_KEY=YOUR_OPENWEATHERMAP_API_KEY
        DELETE_HISTORY_PASSWORD=SET_A_STRONG_PASSWORD_FOR_HISTORY_DELETION
        ```
    *   **Discord Bot Token:** Discord Developer Portal で取得します。Botに `Message Content Intent` と `Server Members Intent` の権限が必要です。
    *   **Google AI Key:** Google AI Studio (旧 MakerSuite) などで取得します。
    *   **OpenWeatherMap API Key:** [OpenWeatherMap](https://openweathermap.org/) でアカウントを作成し、APIキーを取得します（Current Weather Data APIが利用可能なキー）。
    *   **Delete History Password:** `/history clear type:all` コマンド実行時に要求されるパスワードです。任意の安全なパスワードを設定してください。（平文で保管します）
5.  **設定ファイルの確認 (任意):**
    *   `config/` ディレクトリ内の各種 `.json` ファイルや `prompts/` ディレクトリ内の `.txt` ファイルを必要に応じて編集できますが、Bot起動時にデフォルト値で自動生成もされます。スラッシュコマンドでの設定変更が推奨されます。
6.  **Botの起動:**
    ```bash
    python main.py
    ```
    または、`Start.bat` (もしあれば) を実行します。
    <br>`Start.bat`の例(main.pyと同じフォルダ内に配置する場合):
    ```
    @echo off

    :: バッチファイル自身のディレクトリを取得
    set BAT_DIR=%~dp0

    :: 仮想環境をアクティベート
    call "%BAT_DIR%..\venv\Scripts\activate.bat"

    :: main.py が存在するディレクトリへ移動
    cd /d "%BAT_DIR%"

    :: Pythonスクリプトを実行
    python main.py

    pause
    ```

## 主なコマンド一覧

Botの機能は主にスラッシュコマンド (`/`) を使用して操作します。
<br>**※注意:一部のコマンドは危険なので、場合に応じてDiscord側で使用制限を掛けてください。**

*   `/config` - Botの各種設定
    *   `/config gemini show` - 現在のGemini関連設定を表示
    *   `/config gemini set_model model_name:<モデル名>` - 使用するGeminiモデルを設定
    *   `/config gemini set_temperature value:<0.0~1.0>` - Temperatureを設定
    *   `/config gemini set_safety category:<カテゴリ> threshold:<閾値>` - 安全性設定
    *   `/config gemini set_top_k value:<整数>` - Top-Kを設定
    *   `/config gemini set_top_p value:<0.0~1.0>` - Top-Pを設定
    *   `/config gemini set_max_tokens value:<整数>` - 最大出力トークン数を設定
    *   `/config prompt show type:<persona|random_dm>` - 指定プロンプトを表示
    *   `/config prompt set type:<persona|random_dm>` - 指定プロンプトを設定 (モーダル表示)
    *   `/config user set_nickname user:<ユーザー> nickname:<ニックネーム>` - ユーザーのニックネームを設定
    *   `/config user show_nickname [user:<ユーザー>]` - ユーザーのニックネームを表示 (省略時: 自分)
    *   `/config user remove_nickname user:<ユーザー>` - ユーザーのニックネームを削除
    *   `/config channel add channel:<チャンネル>` - 自動応答許可チャンネルを追加 (サーバー内のみ)
    *   `/config channel remove channel:<チャンネル>` - 自動応答許可チャンネルから削除 (サーバー内のみ)
    *   `/config channel list` - 自動応答許可チャンネル一覧を表示 (サーバー内のみ)
    *   `/config random_dm set enabled:<bool> [min_interval:<秒>] [max_interval:<秒>] [stop_start_hour:<時>] [stop_end_hour:<時>]` - 自身のランダムDM設定
    *   `/config random_dm show` - 自身のランダムDM設定を表示
    *   `/config response set_max_length length:<整数>` - Bot応答の最大文字数を設定
    *   `/config response show_max_length` - Bot応答の最大文字数を表示
*   `/history` - 会話履歴の管理
    *   `/history clear type:<all|user|channel|my> [target_user:<ユーザー>] [target_channel:<チャンネル>] [password:<パスワード>]` - 会話履歴を削除
    *   `/history set_length length:<整数>` - 履歴の最大保持件数を設定
    *   `/history show_length` - 現在の履歴の最大保持件数を表示
*   `/weather` - 天気と気分の管理
    *   `/weather update [location:<場所>]` - 天気情報を更新し気分を設定 (場所省略時: 前回指定場所)
    *   `/weather show` - 現在の気分と天気情報を表示
*   `/ping` - Botの応答確認

## 注意事項

*   **履歴管理** 
<br>AIはすべての会話履歴を参照するため、時に関連性の低い過去の会話に影響されたり、対話相手を混同したりする可能性があります。システムプロンプトで制御を試みていますが、完璧ではありません。
*   **APIキーの管理** 
<br>`.env` ファイルは `.gitignore` に追加するなどして、誤ってリポジトリにコミットしないように注意してください。
*   **API制限** 
<br>OpenWeatherMapの無料プランなどにはAPI呼び出し回数制限があります。自動更新の間隔（デフォルト毎分）は、必要に応じて `cogs/weather_mood_cog.py` 内の `@tasks.loop` デコレータの引数を変更してください (例: `minutes=30`)。
*   **PyNaCl** 
<br>ログに `PyNaCl is not installed` という警告が出る場合がありますが、これはDiscordのボイスチャット機能を使用しない限り、Botのコア機能には影響ありません。気になる場合は `pip install pynacl` でインストールしてください。

# Lisence

This project is licensed under the MIT License, see the LICENSE file for details
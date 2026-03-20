# 識（しき）開発進捗

## Phase 1: 識が目を開ける - 完了 (2026-03-12)

### 実装済み
- [x] FastAPIサーバー (`main.py`)
- [x] LINE Bot Webhook受信 + 署名検証 (HMAC-SHA256)
- [x] Gemini API応答生成
- [x] screencaptureでスクショ取得 + LINEに画像返信
- [x] Cloudflare Tunnelでhttps化
- [x] SOUL.md（性格定義: セットアップウィザードで選択可能）
- [x] セキュリティ5層（1行目から稼働）

### セキュリティ（Phase 1で全層稼働）
- [x] Layer 1: user_idホワイトリスト + Webhook署名検証
- [x] Layer 2: Tool Effect Gate（4段階）+ パスホワイトリスト/ブラックリスト + 環境変数フィルタ
- [x] Layer 3: AI出力のcredentialスキャン（APIキー、JWT、秘密鍵等）
- [x] Layer 4: リアルタイム異常検知（OWASP閾値）+ 緊急停止
- [x] Layer 5: macOSセキュリティ監査（SIP、FileVault、Firewall）

---

## Phase 2: 識がPCを操る - 完了 (2026-03-12)

### 実装済み
- [x] Gemini Function Calling（ツール自律選択）
- [x] osascriptツール（Layer 1: コスト$0）
  - アプリ起動（ホワイトリスト制: Chrome, Finder, Slack等 30+アプリ）
  - URL開く
  - 音量変更
  - ダークモード切替
  - デスクトップ通知
  - テキスト入力（クリップボード経由、IMEバイパス、元クリップボード復元）
  - キー操作（ショートカット対応、F1-F12、Home/End/PageUp/PageDown）
- [x] URL安全性チェック（3段階）
- [x] Agent Loop改善
  - 複数ツール連続実行（1メッセージで複数操作）
  - 同一操作3回リトライで諦め報告
  - ツール実行中の進捗通知（5秒クールダウン、最大5回/タスク）
  - ReActループ120秒タイムアウト
  - 連続空レスポンス2回で即終了（Gemini障害対策）
- [x] Gemini 2.5 Pro（thinking_budget=-1: 動的推論モード）
- [x] 会話履歴保持（メモリ内20往復、永続化）
- [x] READレベルツールの並列実行（asyncio.gather）
- [x] スクショJPEG変換（PNG比3-5xトークン節約）
- [x] GUI操作後の自動スクリーンショット（Computer Use方式）
- [x] スマートウェイト（click=2s, type_text=0.3s, press_key(return)=1s）
- [x] 画面変更検知（MD5ハッシュ比較）
- [x] コンテキスト圧縮（8イテレーション後、古いスクショをテキスト化）
- [x] スクショキャッシュ（3秒TTL、GUI操作後に無効化）
- [x] 座標スケーリング（1024pxスクショ → 実画面サイズ）

---

## Phase 3: 識が記憶する - 完了 (2026-03-12)

### 実装済み
- [x] MEMORY.md + topics/ 管理 (`memory/manager.py`)
- [x] セッション要約の自動生成 (`memory/summarizer.py`)
- [x] 日次要約の自動生成（23:30スケジューラー `main.py`）
- [x] コンテキスト注入エンジン (`agent/context.py` — SOUL.md + MEMORY.md + 日次要約)
- [x] SOUL.md自動更新（学習した好み等）
- [x] 会話履歴の永続化（`.ritsu/current_session.json`）
- [x] Playbook自動記録（3ステップ以上の成功タスクをfew-shot化）
- [x] Skills（組み込みスキル13個 — 最速パスマッチ）
- [x] 失敗パターン記録・注入（直近5件をシステムプロンプトに注入）
- [x] スクラッチパッド（5イテレーションごとの中間状態保存、Manus AI方式）
- [x] 学習内容の重複チェック付き保存

---

## Phase 4: 識が自分で動く - 進行中 (2026-03-12〜)

### 実装済み
- [x] マウスクリック (`tools/mouse.py` — Quartz CGEvent)
  - 左クリック、ダブルクリック、右クリック（座標指定）
  - ドラッグ&ドロップ（10ステップ補間のスムーズドラッグ）
  - 画面サイズ取得
- [x] ファイル操作 (`tools/filesystem.py` — path_validator連携)
  - ファイル読み取り（100KB上限）
  - ファイル書き込み（50KB上限）
  - ディレクトリ一覧
  - ファイル移動/リネーム
  - セキュリティ: Desktop/Documents/Downloads のみアクセス可
- [x] ターミナル実行 (`tools/terminal.py` — ホワイトリスト制)
  - ls, grep, git, python3, curl, pip, brew等
  - パイプ対応（パイプ先もホワイトリスト検証）
  - 危険パターン自動ブロック（sudo, rm -rf, eval等）
  - 30秒タイムアウト
- [x] ブラウザ情報取得（URL・タイトル — Chrome/Safari/Arc対応）
- [x] ウィンドウ情報取得（アプリ名・タイトル・サイズ・位置）
- [x] **プロアクティブ行動（スケジューラー）** (`agent/scheduler.py`)
  - 朝のブリーフィング（毎朝8:00 — Geminiで自然な挨拶 + 予定通知生成）
  - リマインダー機能（add/list/delete — 1分ごとチェック、LINE push通知）
  - 繰り返しリマインダー（daily/weekly対応）
- [x] **コードリファクタリング**
  - `agent/loop.py` 1186行 → 499行（心臓部のReActループのみに集中）
  - `agent/tools_config.py` 新設（ツール定義・スキーマ・バリデーション・スケーリング）
  - `agent/history.py` 新設（会話履歴・セッション管理・スクラッチパッド・失敗パターン）
- [x] **MCP基盤の構築** (`mcp_ext/`)
  - MCPクライアント（stdio接続、ツール自動取得・実行）
  - MCP-Geminiブリッジ（MCPツール → Gemini Function Calling自動変換）
  - 設定ファイルベース（`mcp_servers.json` — サーバー追加 = 1行）
  - 4サーバー設定済み: fetch, GitHub, Google Calendar, Notion（enabled待ち）
  - 起動時に自動接続 → ツールを動的登録 → SecurityGateにも自動登録
- [x] **セキュリティ強化**
  - レートリミッター（30メッセージ/分、スライディングウィンドウ方式）
  - プロンプトインジェクション検知（6パターン）
  - セキュリティアラート永続化（`logs/security_alerts.jsonl`）
  - スクショセキュリティスキャン改善（10秒タイムアウト、10MBファイルサイズ制限）
  - AI応答長制限（5000文字 — LINE制限対応）
  - メッセージ入力長制限（10KB超拒否）
  - 異常検知にstats API追加（ヘルスチェック用）
- [x] **Playwrightブラウザ制御** (`tools/browser.py` — Layer 2)
  - browse_url: URLのページ内容をテキスト取得（スクショ不要、トークン効率的）
  - search_web: Google検索 → トップ10結果（タイトル・URL・スニペット）
  - get_page_text: 軽量版テキスト取得（記事本文向け）
  - **get_page_elements: Browser-Use方式の番号付き要素リスト取得（座標不要でWeb操作）**
  - **interact_page_element: 要素番号でクリック・入力・選択（DOM-first操作）**
  - **get_accessibility_tree: ARIA Snapshot取得（200-400トークン vs スクショ15,000+）**
  - セキュリティ: URL安全性チェック連携、30秒タイムアウト、100KBコンテンツ制限
  - **Webコンテンツ間接プロンプトインジェクション防御（sanitize_web_content）**
  - headless Chromium、遅延初期化（使用時のみブラウザ起動）
  - セッション管理（最大3ページ同時、LRU方式で自動クリーンアップ）
- [x] **Topic Patrol実Web検索化**
  - Playwright search_web → 上位記事テキスト取得 → Gemini要約
  - フォールバック: Gemini直接生成（ブラウザ使えない場合）
- [x] **Fetch MCPサーバー有効化** (Web URL取得、uvx経由)
- [x] **起動スクリプト改善** (`start.sh`)
  - cloudflaredも自動起動（別ターミナル不要）
  - トンネルURL自動表示
  - Ctrl+Cでサーバー+トンネル両方停止

### 世界最先端技術の統合（2026-03-13）
- [x] **スマートモデルルーティング** (`agent/router.py` — GenSpark Claw MoA inspired)
  - タスク複雑度を自動分類 → Flash（簡単）/ Pro（複雑）に振り分け
  - Flash で5イテレーション詰まったら Pro にエスカレート
  - 画像付きは常に Pro
- [x] **エピソード記憶** (`agent/episodic_memory.py` — Manus/Stanford CS329A inspired)
  - 「前回Xした時、Yで成功/失敗した」を記録
  - 関連エピソードをシステムプロンプトに自動注入
  - 成功/失敗/教訓をセットで保存
- [x] **Agentic Vision** (`tools/screenshot.py:crop_screenshot` — Gemini Agentic Vision inspired)
  - スクリーンショットの指定領域をクロップ・2倍拡大
  - 小さいテキストやボタンの詳細確認に使用
- [x] **プロンプトキャッシュ最適化** (`agent/context.py` 再構造化)
  - 安定部分（ルール・能力定義）を先頭に配置
  - 変動部分（日時・記憶）を末尾に配置
  - >80% キャッシュヒット率を目標
- [x] **スキル自動進化** (`agent/skill_evolver.py` — MetaClaw inspired)
  - セッション終了時に成功パターンからスキル自動生成
  - 失敗パターンから防御的スキルを自動生成
  - 品質スコアリング + 自動プルーニング
- [x] **Discord Bot** (`discord_client/` — LINE代替)
  - DM対話、画像送受信、スケジューラー統合

### コード最適化 + 世界最先端機能（2026-03-13）
- [x] **CodeAct — Pythonコード実行** (`tools/code_executor.py` — Manus AI CodeAct paradigm inspired)
  - 既存35ツールでは対応できない処理をPythonコードで解決
  - サンドボックス実行（サブプロセス隔離、モジュールホワイトリスト、10秒タイムアウト）
  - 危険モジュール（os/subprocess/socket等）完全ブロック
  - ファイルI/O・ネットワークは既存ツール経由のみ
- [x] **todo.mdチェックポイント** (`agent/history.py:update_plan` — Manus AI todo.md方式)
  - AIが複雑タスクの計画を自分で作成・更新
  - コンテキストウィンドウ切れても計画が残る
  - セッション復帰時に自動注入
- [x] **ツール定義の三重同期問題を解決**
  - TOOL_FUNCTIONS/GEMINI_TOOLS/TOOL_LEVELS の Single Source of Truth化
  - 起動時に自動同期チェック（不整合があればRuntimeError）
- [x] **パイプコマンド分割バグ修正** — 引用符内の`|`を正しく処理
- [x] **セキュリティ修正** — リーク検出順序、macOS監査、例外型の具体化
- [x] **パフォーマンス改善** — 画面サイズキャッシュ（30秒TTL）

### MCP連携（5/5サーバー接続完了 — 2026-03-20）
- [x] Google Calendar連携（MCP経由 — OAuth認証済み、13ツール）
- [x] Notion連携（MCP経由 — 22ツール、タスク自動実行エンジン付き）
- [x] GitHub連携（MCP経由 — 26ツール）
- [x] Fetch連携（MCP経由 — 1ツール）
- [x] Gmail連携（MCP経由 — 19ツール、検索・読み取り・下書きのみ。send_email/batch_deleteはDESTRUCTIVEブロック）

---

## プロジェクト構造（現在）

```
shiki/
├── main.py                     # FastAPI + LINE Webhook + スケジューラー統合
├── config.py                   # 環境変数管理（APIキーはここのみ）
├── agent/
│   ├── loop.py                 # ReActループ（心臓部、499行）
│   ├── tools_config.py         # ツール定義・Geminiスキーマ・バリデーション・同期検証（35ツール）
│   ├── history.py              # 会話履歴・セッション・スクラッチパッド管理
│   ├── context.py              # コンテキスト注入（SOUL.md + 記憶 + 日次要約）
│   ├── skills.py               # 組み込みスキル（14個、最速パスマッチ）
│   ├── skill_evolver.py        # スキル自動進化エンジン（MetaClaw inspired）
│   ├── episodic_memory.py      # エピソード記憶（タスク成功/失敗の経験学習）
│   ├── router.py               # スマートモデルルーティング（Pro/Flash自動選択）
│   ├── playbook.py             # 成功パターン自動記録・再利用
│   └── scheduler.py            # プロアクティブ行動（朝ブリーフィング + リマインダー）
├── tools/
│   ├── screenshot.py           # screencapture + JPEG変換 + 画面変更検知
│   ├── desktop.py              # osascript（アプリ起動、URL、音量、テキスト入力等）
│   ├── mouse.py                # Quartz CGEvent（クリック、ドラッグ）
│   ├── filesystem.py           # ファイル操作（読み書き、一覧、移動）
│   ├── terminal.py             # ターミナルコマンド実行（ホワイトリスト制）
│   ├── code_executor.py        # CodeAct — Pythonサンドボックス実行
│   └── browser.py              # Playwright headless（Web検索、ページ取得）
├── security/
│   ├── gate.py                 # Tool Effect Gate（4段階承認）+ ActionLogger
│   ├── env_filter.py           # 環境変数ホワイトリスト
│   ├── path_validator.py       # ファイルパス検証（ホワイト+ブラック）
│   ├── output_validator.py     # AI出力のcredentialスキャン
│   ├── anomaly_detector.py     # リアルタイム異常検知（OWASP準拠）+ アラート永続化
│   ├── rate_limiter.py         # レートリミッター（30msg/min）
│   ├── mac_hardening.py        # macOSセキュリティ監査
│   └── url_validator.py        # URL安全性3段階チェック
├── line_client/
│   └── messaging.py            # LINE SDK v3 wrapper（画像DL対応）
├── discord_client/
│   ├── bot.py                  # Discord Bot本体（DM対話 + スケジューラー統合）
│   └── messaging.py            # Discord messaging wrapper（LINE互換インターフェース）
├── memory/
│   ├── manager.py              # 記憶管理（セッション/日次/トピック）
│   └── summarizer.py           # Gemini Flashで要約生成
├── docs/
│   ├── SECURITY.md             # セキュリティ設計書
│   ├── THREAT_MODEL.md         # 脅威モデル
│   └── finetuning-guide.md     # ファインチューニングガイド
├── mcp_ext/
│   ├── client.py               # MCPクライアント（サーバー接続・ツール実行）
│   ├── bridge.py               # MCP→Gemini Function Calling自動変換
│   └── mcp_servers.json        # MCPサーバー設定（fetch/GitHub/Calendar/Notion）
├── .ritsu/
│   ├── SOUL.md                 # 性格定義
│   ├── MEMORY.md               # 長期記憶インデックス
│   ├── current_session.json    # 会話履歴（永続化）
│   ├── reminders.json          # リマインダーデータ
│   ├── skills.json             # 学習済みスキル
│   ├── playbooks.json          # プレイブック
│   ├── failure_log.json        # 失敗パターン
│   ├── sessions/               # セッション要約
│   ├── daily/                  # 日次要約
│   └── topics/                 # トピック別記憶
├── static/images/              # スクショ一時保存
├── logs/                       # 操作ログ + サーバーログ
├── requirements.txt
├── shiki                       # CLIランチャー（shiki / shiki discord / shiki "〇〇"）
├── cli.py                      # ターミナル対話モード
├── start.sh                    # 起動スクリプト（LINE版）
├── .env                        # 秘密情報（chmod 600）
└── .gitignore
```

---

## 技術スタック

| 要素 | 技術 |
|------|------|
| 言語 | Python 3.13 |
| Web Framework | FastAPI + uvicorn |
| AI | Gemini 2.5 Pro/Flash（Smart Routing + Function Calling + Dynamic Thinking + CodeAct） |
| LINE連携 | line-bot-sdk v3 (async) |
| Discord連携 | discord.py v2 (async, DM対話) |
| トンネル | Cloudflare Tunnel（無料） |
| スクリーンショット | screencapture → sips JPEG変換 |
| デスクトップ操作 | osascript (AppleScript) + Quartz (CGEvent) |
| 記憶 | ファイルベース (Markdown + JSON) |
| スケジューラー | asyncio（朝ブリーフィング + リマインダー） |

---

## ツール一覧（35個）

| ツール | 用途 | レベル |
|--------|------|--------|
| take_screenshot | 画面撮影（JPEG、1024px） | READ |
| crop_screenshot | 画面領域クロップ・拡大（Agentic Vision） | READ |
| open_app | アプリ起動 | ELEVATED |
| open_url | URL開く（安全性チェック付き） | ELEVATED |
| get_frontmost_app | 前面アプリ確認 | READ |
| get_running_apps | 実行中アプリ一覧 | READ |
| get_browser_info | ブラウザURL・タイトル取得 | READ |
| get_window_info | ウィンドウ詳細情報 | READ |
| set_volume | 音量変更 | WRITE |
| toggle_dark_mode | ダークモード切替 | WRITE |
| show_notification | デスクトップ通知 | WRITE |
| type_text | テキスト入力（IMEバイパス） | WRITE |
| press_key | キー操作（ショートカット対応） | WRITE |
| scroll | スクロール（Quartz CGEvent） | WRITE |
| click | 左クリック | WRITE |
| double_click | ダブルクリック | WRITE |
| right_click | 右クリック | WRITE |
| drag | ドラッグ&ドロップ | WRITE |
| get_screen_size | 画面サイズ | READ |
| read_file | ファイル読み取り | READ |
| write_file | ファイル書き込み | ELEVATED |
| list_directory | フォルダ一覧 | READ |
| move_file | ファイル移動 | ELEVATED |
| run_command | コマンド実行 | ELEVATED |
| browse_url | Webページ内容取得（Playwright） | ELEVATED |
| search_web | Google検索（Playwright） | ELEVATED |
| get_page_text | ページテキスト取得（軽量版） | READ |
| get_page_elements | Web要素リスト取得（Browser-Use方式） | ELEVATED |
| interact_page_element | Web要素操作（click/fill/select） | ELEVATED |
| get_accessibility_tree | Accessibility Tree取得（超軽量） | READ |
| add_reminder | リマインダー設定 | WRITE |
| list_reminders | リマインダー一覧 | READ |
| delete_reminder | リマインダー削除 | WRITE |
| execute_code | Pythonコード実行（CodeAct） | ELEVATED |
| update_plan | タスク計画の作成・更新 | WRITE |

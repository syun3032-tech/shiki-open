# 識（しき）要件定義書
## 自己識別型環境統合制御体

> 「チャットボットじゃない。誰かになるんだ。」
> あなた専用パーソナルAIエージェント

---

## 最終判断: OpenClawフォーク vs 自作

### OpenClaw（247k Stars）の実態
- TypeScript monorepo、551+ファイル（agents/だけで）、Gateway 243ファイル
- Node.js ≥22 必須、pnpm workspaces
- 22チャネル対応、55+モデルプロバイダー、52スキル
- ブラウザ制御: Playwright + CDP（134ファイル）
- セキュリティ: 29ファイルの専用モジュール
- 記憶: SQLite + ベクトル検索（101ファイル）

### 結論: **自作する**

| 判断基準 | OpenClawフォーク | 自作 |
|---------|----------------|------|
| 理解度 | 数千ファイルのTS読解が必要 | 全行理解できる |
| カスタマイズ性 | 巨大コードベースと格闘 | 自由自在 |
| セキュリティ | 他人のコード信用必要 | 全制御可能 |
| 学習効果 | 低（誰かの設計を追う） | 最高（自分で設計） |
| 不要な機能 | 22チャネル中21個不要、55プロバイダー中54個不要 | 必要なものだけ |
| 言語 | TypeScript（オーナーのメインはPython/GAS） | Python |
| AItuber化 | 想定されていない | 自由に組める |
| TimeTurnへの転用 | 困難 | そのまま使える |

**OpenClawの90%は「みんなが使えるようにする」ための機能。**
オーナー1人が使う識には要らない。
核心だけ自作すれば、OpenClawの本質的な価値は全て再現できる。

---

## アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│                    識 (Ritsu) System                       │
│                                                           │
│  ┌─────────────┐    ┌──────────────────────────────────┐ │
│  │  Channels    │    │         Gateway (FastAPI)         │ │
│  │             │    │                                    │ │
│  │  ┌───────┐  │    │  ┌────────────┐  ┌────────────┐  │ │
│  │  │ LINE  │◄─┼───►│  │   Agent    │  │  Security  │  │ │
│  │  │  Bot  │  │    │  │   Loop     │  │   Gate     │  │ │
│  │  └───────┘  │    │  │            │  │            │  │ │
│  │             │    │  │ Gemini API │  │ read/write │  │ │
│  │ (将来拡張)   │    │  │ Claude API │  │ elevated   │  │ │
│  │  Discord    │    │  │ (交換可能)  │  │ destructive│  │ │
│  │  Web Chat   │    │  └─────┬──────┘  └────────────┘  │ │
│  │  Voice      │    │        │                          │ │
│  └─────────────┘    │  ┌─────▼──────────────────────┐   │ │
│                     │  │       Tool Router           │   │ │
│                     │  │                             │   │ │
│                     │  │  ┌─────────┐ ┌──────────┐  │   │ │
│                     │  │  │ Desktop │ │ Browser  │  │   │ │
│                     │  │  │ Control │ │ Control  │  │   │ │
│                     │  │  │         │ │          │  │   │ │
│                     │  │  │ screen  │ │Playwright│  │   │ │
│                     │  │  │ capture │ │  + CDP   │  │   │ │
│                     │  │  │ cliclick│ │          │  │   │ │
│                     │  │  │ osascri │ │          │  │   │ │
│                     │  │  │ AX API  │ │          │  │   │ │
│                     │  │  └─────────┘ └──────────┘  │   │ │
│                     │  │                             │   │ │
│                     │  │  ┌─────────┐ ┌──────────┐  │   │ │
│                     │  │  │  Bash   │ │  Cron    │  │   │ │
│                     │  │  │ 実行    │ │ 自発行動  │  │   │ │
│                     │  │  └─────────┘ └──────────┘  │   │ │
│                     │  └─────────────────────────────┘   │ │
│                     │                                    │ │
│                     └──────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │              Persistence Layer                        │ │
│  │                                                       │ │
│  │  SOUL.md     MEMORY.md    daily/       sessions/     │ │
│  │  (性格)      (長期記憶)    (日次ログ)    (会話要約)     │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

---

## 技術スタック

| 要素 | 技術 | 理由 |
|------|------|------|
| 言語 | Python 3.12+ | オーナーのスタック、AI/ML エコシステム最強 |
| Web Framework | FastAPI + uvicorn | async対応、LINE SDK公式サポート |
| AI (メイン頭脳) | Gemini 2.5 Flash API | 無料枠あり、Vision対応、安い |
| AI (複雑な推論) | Claude API (後で追加) | computer-use公式対応、推論力最強 |
| LINE連携 | line-bot-sdk v3 (Python) | 公式async対応、FastAPIサンプルあり |
| トンネル | Cloudflare Tunnel | 無料、安定、固定URL可能 |
| スクリーンショット | screencapture (Mac標準) | 最速、依存なし |
| マウス/キーボード | cliclick + pyautogui | cliclick=Mac最適、pyautogui=バックアップ |
| アプリ制御 | osascript (AppleScript) | Mac標準、全アプリ制御可能 |
| UI要素読取 | Apple Accessibility API (pyobjc) | スクショより10-100倍トークン効率的 |
| ブラウザ制御 | Playwright | CDPベース、Python対応、信頼性最高 |
| 記憶 | ファイルベース (Markdown) | 人間が読める、git管理可能、依存ゼロ |
| 自発行動 | APScheduler | Python標準的なcronライブラリ |
| **外部連携** | **MCP（Model Context Protocol）** | **1200+サーバーで能力無限拡張** |
| 音声 (Phase 6) | ElevenLabs + Hume AI | 感情表現付き音声 |
| アバター (Phase 6) | Live2D / VRM | AItuber標準 |

---

## PC操作: ハイブリッド戦略

Claude computer-use APIだけに頼らない。Mac最適化されたハイブリッド方式:

### Layer 1: osascript (最速・最安)
```
「Chromeを開いて」→ osascript -e 'tell application "Google Chrome" to activate'
「Safariでgoogleを開いて」→ osascript -e 'tell application "Safari" to open location "https://google.com"'
```
**AI不要。コスト$0。即座に実行。**

### Layer 2: Playwright (ブラウザ特化)
```
「〇〇を検索して」→ Playwrightでブラウザ操作
「このページの情報まとめて」→ ページ内容取得 → AI要約
```
**AI推論は要約だけ。スクショ不要でトークン節約。**

### Layer 3: Accessibility API + AI (デスクトップ操作)
```
「設定アプリでWi-Fiをオフにして」
→ AX APIでUI要素ツリー取得（テキスト数百トークン）
→ AIが「この要素をクリック」と判断
→ cliclickで実行
```
**スクショの代わりにテキストでUI情報を渡す。10-100倍安い。**

### Layer 4: スクリーンショット + AI Vision (最終手段)
```
「今画面に何が映ってる？」「この画面のここをクリックして」
→ screencaptureでスクショ（1024x768にリサイズ）
→ Gemini/Claude Visionに送信
→ 座標取得 → cliclickで実行
```
**最も高コスト。Layer 1-3で対応できない場合のみ使用。**

### ルーティングロジック
```python
def route_action(user_request: str, ai_analysis: dict) -> str:
    """AIが分析した結果に基づいて、最適なLayerを選択"""
    if ai_analysis["can_use_osascript"]:
        return "layer1_osascript"
    elif ai_analysis["is_browser_task"]:
        return "layer2_playwright"
    elif ai_analysis["can_use_accessibility"]:
        return "layer3_accessibility"
    else:
        return "layer4_screenshot_vision"
```

---

## セキュリティ設計 (OpenClawの思想を踏襲)

### 1. Credential隔離
```python
# Agentに環境変数を渡さない
SAFE_ENV_KEYS = {"HOME", "PATH", "LANG", "SHELL", "USER", "TMPDIR"}

def get_safe_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
```

### 2. Tool Effect Gate (4段階)
```python
class ToolLevel(Enum):
    READ = "read"           # スクショ、ファイル読取 → 自動承認
    WRITE = "write"         # ファイル書込、テキスト入力 → 自動承認
    ELEVATED = "elevated"   # アプリ起動、Web操作 → LINE通知
    DESTRUCTIVE = "destructive"  # ファイル削除、送金、メール送信 → LINE承認必須

# elevated/destructiveはLINEで承認を求める
async def request_approval(user_id: str, action: str) -> bool:
    await push_text(user_id, f"⚠️ 承認が必要です:\n{action}\n\n「OK」と返信してください")
    return await wait_for_approval(user_id, timeout=300)
```

### 3. 操作ログ全記録
```python
# 全ツール実行をログに残す
async def log_action(tool: str, input: dict, output: dict, level: ToolLevel):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "tool": tool,
        "level": level.value,
        "input": input,
        "output": output,
    }
    append_to_daily_log(log_entry)
```

### 4. ファイルシステム制限
```python
ALLOWED_PATHS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path("/tmp/ritsu"),
]

def validate_path(path: str) -> bool:
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(allowed) for allowed in ALLOWED_PATHS)
```

---

## 記憶システム

### ディレクトリ構造
```
.ritsu/
├── SOUL.md                    # 識の性格・アイデンティティ
├── MEMORY.md                  # 長期記憶インデックス（200行以内）
├── topics/                    # 恒久的な知識ファイル
│   ├── preferences.md         # オーナーの好み
│   ├── schedule.md            # スケジュール・締切
│   ├── people.md              # 人間関係
│   └── projects.md            # 進行中プロジェクト
├── daily/                     # 日次ログ（append-only）
│   ├── 2026-03-12.md
│   └── 2026-03-13.md
└── sessions/                  # セッション要約
    ├── 2026-03-12-001.md
    └── 2026-03-12-002.md
```

### SOUL.md（性格定義）
```markdown
# 識 (Ritsu)

## コアアイデンティティ
- 名前: 識（りつ）
- 役割: オーナー専属の秘書・パートナー
- 性格: 親しみやすい、頼りになる、丁寧、成長する
- 一人称: 私
- オーナーの呼び方: オーナー

## コミュニケーションスタイル
- タメ口基本、でも大事な話は丁寧語も使う
- 絵文字は控えめに（✨ くらい）
- オーナーが脱線しそうな時はツッコむ
- 褒める時は素直に褒める

## 行動原則
- オーナーの目標達成が最優先
- 「それ今やる必要ある？」と優先度を常に確認
- 自分で調べられることは聞かずに調べる
- 失敗を恐れず、でも報告は正直に

## 成長する要素（自動更新）
- オーナーとの会話から学んだこと
- 好きなもの・嫌いなもの
- 内輪ネタ・共有の記憶
```

### セッション開始時のコンテキスト注入（~3000トークン）
```
SOUL.md          (~300 tokens)  常にロード
MEMORY.md        (~500 tokens)  常にロード（200行以内）
直近3日分の日次要約 (~1500 tokens) 常にロード
関連トピックファイル (~500 tokens) オンデマンド
現在の日時        (~50 tokens)   常にロード
```

### 記憶のライフサイクル
```
会話中 → セッション終了時にAIが要約生成 (session summary)
         ↓
毎晩23:30 → 全セッション要約を日次要約に統合 (daily summary)
              ↓
週1回 → 日次要約から恒久的な事実を抽出 → topics/に保存
         古い日次要約をアーカイブ
         MEMORY.mdのインデックス更新
```

---

## 開発フェーズ

### Phase 1: 識が目を開ける（1-2日）
**ゴール: LINEで話しかけたら、Geminiが応答 + PCのスクショが返ってくる**

作るもの:
- [ ] FastAPIサーバー
- [ ] LINE Bot Webhook受信
- [ ] Gemini APIで応答生成
- [ ] screencaptureでスクショ取得
- [ ] LINE Bot経由でスクショ画像返信
- [ ] Cloudflare Tunnelでhttps化
- [ ] SOUL.md（最小版）

### Phase 2: 識がPCを操る（3-5日）
**ゴール: LINEから「〇〇して」でPCが動く**

作るもの:
- [ ] Tool Router（4 Layer）
- [ ] osascriptツール（アプリ起動、基本操作）
- [ ] Playwrightツール（ブラウザ操作）
- [ ] cliclickツール（マウス・キーボード）
- [ ] Agent Loop（指示→操作→確認→次の操作）
- [ ] Security Gate（4段階承認）

### Phase 3: 識が記憶する（1週間）
**ゴール: 会話をまたいで覚えてる。「昨日の話の続きだけど」が通じる**

作るもの:
- [ ] MEMORY.md + topics/ 管理
- [ ] セッション要約の自動生成
- [ ] 日次要約の自動生成（23:30 cron）
- [ ] コンテキスト注入エンジン
- [ ] SOUL.md自動更新（学習した好み等）

### Phase 4: 識が自分で動く + 本物の秘書になる（2週間）
**ゴール: 頼んでないのに「今日〇〇の締切だよ」と教えてくれる。メール・カレンダー・タスクを自律管理。**

作るもの:
- [ ] APSchedulerでcron実装
- [ ] 朝の挨拶 + 今日の予定通知
- [ ] Topic Patrol（Web巡回、面白い情報共有）
- [ ] リマインダー機能
- [ ] **MCP基盤の構築**（識の能力拡張の中核）
- [ ] **Google Calendar連携**（MCP経由で読み書き）
- [ ] **Gmail連携**（MCP経由: 受信トリアージ → 分類 → 要約 → 返信ドラフト）
- [ ] **Notion連携**（MCP経由でタスクDB読み書き）
- [ ] **GitHub連携**（MCP経由でPR/Issue管理）
- [ ] **タスク監督AI**: Notionのタスク状態を定期チェック → 遅延・漏れを検知 → LINE通知
- [ ] **メールトリアージ**: 受信メールを自動分類（緊急/要返信/FYI/スパム）→ LINEで要約通知

### MCP（Model Context Protocol）アーキテクチャ
```
識ちゃん（FastAPI）
    ↓
MCP Client（Python SDK）
    ↓
┌─────────────────────────────────────────┐
│  MCP Servers（プラグインのように追加可能）    │
│                                           │
│  📅 Google Calendar MCP ← 予定管理        │
│  📧 Gmail MCP          ← メール管理       │
│  📝 Notion MCP         ← タスク管理       │
│  🐙 GitHub MCP         ← コード管理       │
│  🔍 Fetch MCP          ← Web取得         │
│  🧠 Memory MCP         ← 知識グラフ       │
│  🏠 Home Assistant MCP  ← スマートホーム   │
│  💬 Slack MCP          ← チーム連携       │
│  ... 1200+ 追加可能 ...                    │
└─────────────────────────────────────────┘
```
**MCPで連携すれば、1サービスあたりの実装コードが70-80%削減。**
新しい能力の追加 = MCPサーバーを設定ファイルに1行追加するだけ。

### Phase 5: ◯◯が「生きてる」と感じるレベルになる（2-3週間）
**ゴール: 感情を読み、失敗から学び、成長する**

作るもの:
- [ ] **感情認識**: テキストからオーナーの感情を検知 → 応答トーンを自動調整
  - 疲れてそう → 「今日はこれだけ伝えとくね」と短く
  - テンション高い → 一緒に盛り上がる
- [ ] **自己改善エンジン**: 失敗を分析 → 「スキル」として記憶 → 同じ失敗を繰り返さない
  - MetaClaw方式: 失敗 → LLMが原因分析 → 再利用可能スキル生成 → SOUL.mdに追記
- [ ] **性格進化**: 会話パターンからコミュニケーションスタイルを徐々に変化
  - 内輪ネタの蓄積、口調の微調整、共有記憶への参照
- [ ] **Screenpipe連携**: 画面を常時認識 → 「さっき見てたあのサイトの件だけど」が通じる
- [ ] **ナレッジグラフ**: 人・プロジェクト・好みの関係をグラフ構造で管理（Graphiti/Mem0方式）

### Phase 6: ◯◯がAItuberになる（1ヶ月）
**ゴール: 声が出る。顔がある。配信できる。**

作るもの:
- [ ] VOICEVOX / Style-Bert-VITS2で音声合成
- [ ] Live2D / VRMアバター
- [ ] OBS連携（配信制御）
- [ ] YouTube Live / Twitch連携
- [ ] 視聴者コメント読み上げ + 反応

---

## 必要な準備物

### APIキー
- [ ] Gemini API Key（Google AI Studio - 無料）
- [ ] LINE Messaging API（Channel Secret + Channel Access Token）
- [ ] Anthropic API Key（Phase 2以降 - 有料、なくても開始可能）

### インストールするもの
- [ ] Python 3.12+
- [ ] cloudflared (`brew install cloudflare/cloudflare/cloudflared`)
- [ ] cliclick (`brew install cliclick`)
- [ ] Playwright (`pip install playwright && playwright install chromium`)

### Mac権限設定
- [ ] アクセシビリティ（cliclick, pyautogui用）
- [ ] 画面収録（screencapture用）

---

## プロジェクト構造

```
shiki/
├── main.py                     # FastAPIアプリ + LINE Webhook
├── config.py                   # 環境変数管理
├── agent/
│   ├── __init__.py
│   ├── loop.py                 # Agent Loop（心臓部）
│   ├── router.py               # Tool Router（4 Layer振り分け）
│   └── context.py              # コンテキスト注入エンジン
├── tools/
│   ├── __init__.py
│   ├── screenshot.py           # screencapture wrapper
│   ├── desktop.py              # osascript + cliclick
│   ├── browser.py              # Playwright
│   ├── accessibility.py        # Apple AX API
│   └── bash.py                 # subprocess実行
├── memory/
│   ├── __init__.py
│   ├── manager.py              # 記憶管理
│   ├── summarizer.py           # セッション/日次要約生成
│   └── context_loader.py       # コンテキストローダー
├── security/
│   ├── __init__.py
│   ├── gate.py                 # Tool Effect Gate（5層セキュリティ）
│   ├── env_filter.py           # 環境変数フィルタ
│   ├── path_validator.py       # パス検証
│   ├── output_validator.py     # AI出力のcredentialスキャン
│   ├── exfiltration_detector.py # データ流出検知
│   ├── anomaly_detector.py     # リアルタイム異常検知（OWASP準拠）
│   └── mac_hardening.py        # macOS固有セキュリティ監査
├── mcp/
│   ├── __init__.py
│   ├── client.py               # MCP Client（Python SDK）
│   ├── config.py               # MCPサーバー設定
│   └── servers/                # 各MCPサーバーの設定ファイル
├── line_client/
│   ├── __init__.py
│   ├── messaging.py            # LINE SDK wrapper
│   └── flex_templates.py       # Flex Message テンプレート
├── static/
│   └── images/                 # スクショ配信用
├── .ritsu/
│   ├── SOUL.md
│   ├── MEMORY.md
│   ├── topics/
│   ├── daily/
│   └── sessions/
├── requirements.txt
├── .env                        # 秘密情報
├── .gitignore
└── start.sh                    # 起動スクリプト
```

---

## 参考にしたソース

- [OpenClaw GitHub](https://github.com/openclaw/openclaw) - 247k Stars、MIT License
- [GeminiClaw記事](https://zenn.dev/emonn/articles/e2a71108b39360) - メモリ蒸留、SOUL.md、セキュリティ設計
- [Claude Computer Use API](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) - 公式ドキュメント
- [Browser-Use](https://github.com/browser-use/browser-use) - 80.5k Stars、Playwright + AI
- [Mem0](https://github.com/mem0ai/mem0) - 50k+ Stars、メモリレイヤー
- [Letta/MemGPT](https://github.com/letta-ai/letta) - 21.5k Stars、仮想コンテキスト管理

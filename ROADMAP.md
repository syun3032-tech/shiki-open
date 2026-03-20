# 識（しき）進化ロードマップ

> OpenClaw / Browser-Use / Claude Computer Use / CrewAI 等を参考に、識ちゃんの能力を拡張する計画
> 作成日: 2026-03-14

---

## 現在の状態（Phase 4: Self-driving ~95%）

### 完了済み
- [x] 36ツール実装（6層ハイブリッド制御）
- [x] ReActループ + Gemini 2.5 Pro/Flash 動的ルーティング
- [x] LINE / Discord / CLI 3チャネル対応
- [x] 5層セキュリティ（gate, path, env, output, url, anomaly, rate_limit）
- [x] 記憶システム（SOUL, MEMORY, daily, sessions, episodes, skills, playbooks）
- [x] プロアクティブ行動（朝ブリ、リマインダー、日次要約）
- [x] Playwright stealth + セッション永続化
- [x] ベジェ曲線マウス（ghost-cursor inspired）
- [x] Claude Code委譲（Layer 6）
- [x] MCP統合（Notion, Fetch, Calendar, GitHub）
- [x] CodeAct（Pythonサンドボックス）
- [x] スキル自動進化（MetaClaw inspired）

### 未完了
- [ ] MCP OAuth設定（Calendar, Gmail）
- [ ] Screenpipe連携
- [ ] 感情認識 + トーン調整（Phase 5）
- [ ] 音声合成（Phase 6）
- [ ] Live2D / VRM アバター（Phase 6）

---

## Tier 1: すぐやれる（1-2日）— 効果デカい

### 1. Cronジョブ / 自律タスク実行
- **概要**: 識ちゃんが24時間自律で定期タスクを実行
- **実装**: `agent/scheduler.py` を拡張。ユーザー定義のCronタスク対応
- **例**:
  - 毎朝9時にランサーズで新着案件チェック → Notionに追加 → Discordで報告
  - 1時間おきにメール確認 → 重要なやつだけ通知
  - 毎日23:30に日次レポート生成（既存）
- **OpenClaw参考**: Cron jobs + webhook automation
- **ステータス**: [ ] 未着手

### 2. スキルマーケット（ClawHub相当）
- **概要**: 識ちゃんが自分で新しいスキルを書いて保存・再利用
- **実装**: `agent/skill_evolver.py` 強化。成功パターン → 自動スキル化
- **例**: 「ランサーズで案件探す」を1回成功 → スキル化 → 次から一発実行
- **OpenClaw参考**: 5,400+ skills on ClawHub
- **ステータス**: [ ] 未着手

### 3. Telegram対応
- **概要**: 海外クライアント対応。Bot APIで簡単に追加可能
- **実装**: `telegram_bot.py` 新規作成。discord_bot.pyをベースに
- **ステータス**: [ ] 未着手

---

## Tier 2: 1週間級 — 差別化ポイント

### 4. 音声インタラクション
- **概要**: 識ちゃんに喋らせる + 音声入力
- **実装**:
  - 出力: VOICEVOX / Style-Bert-VITS2（日本語特化）
  - 入力: Whisper（OpenAI） or Google Speech-to-Text
  - マイク常時監視 → ウェイクワード「識ちゃん」で起動
- **ステータス**: [ ] 未着手

### 5. マルチエージェント協調
- **概要**: 識ちゃん（司令塔）→ 専門エージェントに委譲
- **実装**:
  - リサーチャー（Web調査専門）
  - コーダー（Claude Code）← 既にある
  - ライター（記事・提案書作成）
  - 営業（案件応募文作成）
- **CrewAI参考**: ロール分担 + タスク委譲
- **ステータス**: [ ] 未着手

### 6. 動的ツール生成
- **概要**: エージェントが自分で新しいツールを作る
- **実装**: delegate_to_claude → ツールコード生成 → tools/ に保存 → 動的ロード
- **OpenClaw参考**: Self-improving skills
- **ステータス**: [ ] 未着手

### 7. 収益トラッカー
- **概要**: クラウドワークス/ランサーズの報酬を自動追跡
- **実装**: 定期巡回 → 収益データ保存 → Notionダッシュボード
- **ステータス**: [ ] 未着手

---

## Tier 3: 2-3週間級 — ガチ進化

### 8. エージェント間通信（A2A Protocol）
- **概要**: 識ちゃんが他のAIエージェントと通信
- **Google A2A Protocol対応**
- **ステータス**: [ ] 未着手

### 9. セッション永続ブラウザ（ログイン維持巡回）
- **概要**: CrowdWorks/ランサーズにログインしっぱなしで定期巡回
- **実装**: Playwright stealth + storage_state + Cronジョブ連携
- **ステータス**: [x] stealth + storage_state 完了。Cronジョブ連携は未着手

### 10. Screenpipe連携（常時画面監視）
- **概要**: PC画面を常時録画・OCR → 検索可能な「デジタル記憶」
- **ステータス**: [ ] 未着手

### 11. メニューバーアプリ（macOS常駐）
- **概要**: SwiftUIで識ちゃんアイコン常駐 → クリックで即チャット
- **ステータス**: [ ] 未着手

---

## Tier 4: 夢の機能

### 12. AItuber化
- Live2D / VRM + 音声合成 + OBS + YouTube/Twitch

### 13. 自己進化ループ
- タスク失敗 → 原因分析 → コード修正 → 自分自身をアップデート

### 14. モバイルノード
- iPhone Shortcuts → 識ちゃん連携

---

## 優先実装順（お金稼ぎ最適化）

1. **Cronジョブ** — ランサーズ/CW自動巡回の基盤
2. **セッション永続ブラウザ巡回** — ログイン維持で案件操作
3. **収益トラッカー** — モチベ維持 + 目標管理
4. **動的ツール生成** — 対応できる仕事の幅拡大
5. **マルチエージェント協調** — 作業の並列化

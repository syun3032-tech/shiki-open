# Notion連携 + タスク自動実行エンジン

## 概要

識ちゃんがNotionのプロジェクト・タスクDBを読み書きし、タスクを自律的に実行する仕組み。

**ワークフロー:**
1. オーナーがNotionにタスクをボコボコ入れる
2. 識ちゃんが10分ごとにNotionをスキャン → 「新しいタスク入ってるよ！やっとく？」
3. 「やって」→ 識ちゃんがガガガっと実行
4. 完了 → Notionステータス更新 + コメントで報告 + Discord通知
5. 成果物は `~/識ちゃん/{プロジェクト名}/` に自動保存
6. 途中でオーナーがNotionにコメント → 識ちゃんが拾って方向修正

---

## アーキテクチャ

```
[Notion DB] ← 10分ポーリング → [scheduler.py: notion_task_patrol_loop]
     ↓                                    ↓
  タスク検知                          Discord通知「新タスクあるよ」
     ↓                                    ↓
[notion_executor.py]              オーナー「やって」
     ↓                                    ↓
  タスク読取 → ステータス「進行中」   ← notion_execute_tasks ツール呼び出し
     ↓
  process_message() (既存ReActループ)
     ↓
  delegate_to_claude / search_web / etc.
     ↓
  コメントチェック（割り込み対応）
     ↓
  完了 → ステータス更新 + コメント + Discord + 成果物保存
```

## Notion DB構造

### プロジェクトDB (`d0d136d570bb44daa339efff76489d45`)
| プロパティ | 型 | 値 |
|---|---|---|
| プロジェクト名 | title | |
| カテゴリ | select | プロダクト / 受託開発 / フリーランス / 研修・セミナー |
| ステータス | select | 準備中 / 進行中 / 完了 / 保留 |
| メモ | rich_text | |
| 完了条件 | date | |
| タスク一覧 | relation | → タスクDB |

### タスクDB（各プロジェクトのインラインDB）
| プロパティ | 型 | 値 |
|---|---|---|
| タスク名 | title | |
| ステータス | select | 未着手 / 進行中 / レビュー / 完了 |
| 優先度 | select | 高 / 中 / 低 |
| メモ | rich_text | |
| 期限 | date | |
| 見積工数(h) | number | |
| 実績工数(h) | number | |
| 進捗率 | number (%) | |

**注意:** タスクDBは各プロジェクトページ内にインラインDBとして存在。DB IDは動的に検出される（`_find_task_db_id()`）。

## ツール一覧（20個）

### 読み取り系（READ — 自動承認）
| ツール名 | 用途 |
|---|---|
| `notion_list_projects` | プロジェクト一覧（フィルタ可） |
| `notion_get_project` | プロジェクト詳細 |
| `notion_list_tasks` | タスク一覧（プロジェクト/ステータス/優先度フィルタ） |
| `notion_search` | ワークスペース横断検索 |
| `notion_get_page_content` | ページ本文・ブロック取得 |
| `notion_list_comments` | コメント一覧 |
| `notion_execution_status` | タスク実行エンジンの状態確認 |
| `get_discord_history` | Discord DM履歴（識ちゃん自身の送信含む） |

### 書き込み系（ELEVATED — LINE通知付き）
| ツール名 | 用途 |
|---|---|
| `notion_create_project` | 新規プロジェクト作成 |
| `notion_update_project` | プロジェクト更新 |
| `notion_create_task` | タスク作成 |
| `notion_update_task` | タスク更新 |
| `notion_batch_create_tasks` | 複数タスク一括作成 |
| `notion_add_comment` | コメント追加 |
| `notion_update_block` | チェックボックスON/OFF、テキスト変更 |
| `notion_append_blocks` | ページにブロック追記 |
| `notion_execute_tasks` | 未着手タスクを自動実行 |
| `notion_execute_single_task` | 特定タスク1件を実行 |

## タスク自動実行エンジン

### 実行フロー（`notion_execute_tasks`）
1. 全プロジェクトから未着手タスクを収集
2. 優先度順にソート（高→中→低）
3. Discord開始通知
4. 各タスクを順番に実行:
   - ステータス「進行中」に更新
   - 開始コメント投稿
   - タスク本文 + コメント履歴からプロンプト構築
   - `process_message()`でReActループ実行
   - 完了後にコメントチェック（割り込み対応）
   - ステータス「完了」に更新
   - 完了コメント + Discord通知
   - 成果物を`~/識ちゃん/`に保存
5. 全完了後にサマリーDiscord通知

### 安全機構
| 機構 | 内容 |
|---|---|
| ファイルロック | `fcntl.flock`で二重実行防止 |
| タイムアウト | 1タスク10分上限 |
| 暴走防止 | 最大10タスク/回 |
| 連続失敗停止 | 2連続失敗でループ中止 |
| アトミック書き込み | ステートファイルはtmp→rename |
| エラーサニタイズ | Notionコメントに内部情報を出さない |
| ステート上限 | completed_task_ids最大100件 |
| メモリリーク防止 | _notified_task_idsは毎サイクルGC |

### 割り込み対応
タスク実行後にNotionコメントをチェック。オーナーから新しいコメントが来ていたら：
1. コメント内容を読み取り
2. 「対応します」コメント返信
3. 前回の作業結果 + 追加指示で再実行
4. 結果をマージして報告

## スケジューラー統合

`notion_task_patrol_loop`（`agent/scheduler.py`内）:
- **10分間隔**で全プロジェクトのタスクをスキャン
- 活動時間: 9:00〜23:00
- 新規未着手タスク検知 → Discord通知
- レビュー待ちタスク検知 → Discord通知
- `_notified_task_ids`でメモリリーク防止（毎サイクルGC）

## 成果物管理

保存先: `~/識ちゃん/{プロジェクト名}/{タイムスタンプ}_{タスク名}.md`

例:
```
~/識ちゃん/
  TimeTurn_AI/
    20260319_234500_API設計.md
    20260320_100000_テスト実装.md
  仮想通貨税AI/
    20260319_235000_税率計算ロジック.md
```

## セットアップ

### 前提条件
1. Notion Integration「識」が作成済み
2. プロジェクトDBに「識」インテグレーションが接続済み
3. コメント権限（読み取り + 挿入）が有効

### 環境変数（.env）
```
NOTION_API_KEY=ntn_xxxxx
```

### 変更ファイル一覧
| ファイル | 変更 |
|---|---|
| `tools/notion.py` | 新規: Notion API操作 |
| `tools/notion_executor.py` | 新規: タスク自動実行エンジン |
| `agent/tools_config.py` | 20ツール追加（TOOL_FUNCTIONS + GEMINI_TOOLS + REQUIRED_ARGS + STATUS_MESSAGES） |
| `agent/scheduler.py` | notion_task_patrol_loop追加 |
| `discord_client/messaging.py` | get_recent_messages追加 |
| `security/gate.py` | 20ツールのセキュリティレベル追加 |
| `security/path_validator.py` | ~/識ちゃん/ の読み書き権限追加 |

## セキュリティ監査結果（2026-03-19）

### 対応済み
- [x] レースコンディション → fcntl.flockで排他ロック
- [x] エラーハンドラの連鎖失敗 → try/exceptでキャッチ
- [x] ファイル名サニタイズ → 空文字/ドット/NULL対策
- [x] ステートファイル肥大化 → completed_task_ids上限100
- [x] メモリリーク(_notified_task_ids) → 毎サイクルGC
- [x] APIエラー情報漏洩 → _safe_error()でサニタイズ
- [x] 例外情報漏洩 → type(e).__name__のみコメント
- [x] float変換クラッシュ → try/except
- [x] アトミック書き込み → tmp→rename

## Skill Injection（Paperclipパターン）
`agent/context.py`のシステムプロンプトに、Notion操作の完全な手順書を注入。
Geminiがツール選択時に「どのNotionツールをどう使うか」を正確に判断できる。

## 朝ブリーフィングNotion統合
`agent/scheduler.py`の`generate_morning_briefing()`がNotionタスク状況を取得:
- アクティブプロジェクト一覧
- 未着手/進行中タスク数
- 高優先度タスクの名前
- 累計完了タスク数

## 自己振り返り（Reflexionパターン）
`tools/notion_executor.py`がタスク完了/失敗後に自動で振り返りを生成:
- Geminiで「うまくいった点/改善点/次回の教訓」を分析
- `.ritsu/reflections/{YYYY-MM-DD}.md`に日付別保存
- 次回のタスク実行プロンプトに過去の教訓を自動注入
- `notion_get_reflections`ツールで振り返りログを参照可能

### 既知の制限
- Notion APIレート制限（3req/s）の明示的対応なし（httpxのリトライに依存）
- ページネーション未対応（50件超のブロック/コメントは取得不可）
- notion_execute_tasksはELEVATEDレベル（process_message経由で他ツールを呼べるため、将来的にDESTRUCTIVEへの昇格を検討）

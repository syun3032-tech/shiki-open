"""Notionタスク自動実行エンジン

識ちゃんがNotionのタスクを読んで、自律的に実行する心臓部。

アーキテクチャ:
- OpenClawのHeartbeatパターン: Notionをカンバンとして定期ポーリング
- Manus AIのContext Offloading: 中間結果をファイルに退避
- LangGraphのInterrupt: ツール呼び出し合間にコメントチェック
- 既存のprocess_message()をReActエンジンとして再利用

フロー:
1. Notionから未着手タスクを取得（優先度順）
2. タスクを「進行中」に更新
3. タスク内容を読んでprocess_message()に投げる
4. 実行中にNotionコメントを監視（割り込み対応）
5. 完了→ステータス更新、コメントで報告、成果物保存
6. 次のタスクへ

セキュリティ:
- 各タスクにタイムアウト（10分）
- 異常検知でループ停止
- 成果物は許可ディレクトリのみ
"""

import asyncio
import fcntl
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.notion import (
    list_projects, list_tasks, update_task, add_comment,
    list_comments, create_task, get_page_content,
    _find_task_db_id,
)

logger = logging.getLogger("shiki.executor")

# === 設定 ===
# 成果物保存先
DELIVERABLES_DIR = Path.home() / "識ちゃん"
# タスク実行ステートファイル
_STATE_FILE = Path(__file__).parent.parent / ".ritsu" / "executor_state.json"
# 1タスクあたりのタイムアウト（秒）
TASK_TIMEOUT = 600  # 10分
# コメントチェック間隔（秒）— process_message内では直接チェックできないため、
# 実行前後でチェックする
COMMENT_CHECK_INTERVAL = 30
# 連続実行上限（暴走防止）
MAX_CONSECUTIVE_TASKS = 10


def _ensure_dirs():
    DELIVERABLES_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


_LOCK_FILE = _STATE_FILE.with_suffix(".lock")
# 完了タスクIDの保持上限
_MAX_COMPLETED_IDS = 100


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Executor state load failed (using default): {e}")
    return {
        "running": False,
        "current_task_id": None,
        "current_project_id": None,
        "completed_task_ids": [],
        "last_run": None,
        "total_completed": 0,
    }


def _save_state(state: dict):
    """アトミック書き込み + ファイルロック"""
    _ensure_dirs()
    state["updated_at"] = datetime.now().isoformat()
    # completed_task_idsの上限管理
    if len(state.get("completed_task_ids", [])) > _MAX_COMPLETED_IDS:
        state["completed_task_ids"] = state["completed_task_ids"][-_MAX_COMPLETED_IDS:]
    # アトミック書き込み（write-to-temp-then-rename）
    tmp_file = _STATE_FILE.with_suffix(".tmp")
    try:
        tmp_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_file.rename(_STATE_FILE)
    except Exception as e:
        logger.error(f"State save failed: {e}")
        if tmp_file.exists():
            tmp_file.unlink()


# ロック用ファイルディスクリプタ（_acquire_lockと_release_lockで共有）
_lock_fd = None


def _acquire_lock() -> bool:
    """排他ロックを取得（二重実行防止）"""
    global _lock_fd
    _ensure_dirs()
    try:
        _lock_fd = open(_LOCK_FILE, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (IOError, OSError):
        if _lock_fd:
            _lock_fd.close()
            _lock_fd = None
        return False


def _release_lock():
    """排他ロックを解放（_acquire_lockと同じfdを使用）"""
    global _lock_fd
    try:
        if _lock_fd and not _lock_fd.closed:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        _lock_fd = None
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        _lock_fd = None


def _save_deliverable(task_name: str, project_name: str, content: str) -> Path:
    """成果物をファイルに保存"""
    _ensure_dirs()
    # プロジェクト別フォルダ
    project_dir = DELIVERABLES_DIR / _sanitize_filename(project_name)
    project_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{_sanitize_filename(task_name)}.md"
    filepath = project_dir / filename

    filepath.write_text(content, encoding="utf-8")
    logger.info(f"Deliverable saved: {filepath}")
    return filepath


def _sanitize_filename(name: str) -> str:
    """ファイル名に使えない文字を除去（空文字・パストラバーサル防止）"""
    name = re.sub(r'[\\/*?:"<>|\x00]', "", name)
    name = name.replace(" ", "_").replace("　", "_")
    name = name.lstrip(".")  # 先頭ドット除去（隠しファイル防止）
    name = name.strip("_")
    name = name[:50]
    return name if name else "untitled"


async def _get_new_comments(page_id: str, since_count: int) -> list[dict]:
    """前回チェック以降の新しいコメントを取得"""
    result = await list_comments(page_id)
    if not result.get("success"):
        return []
    all_comments = result.get("comments", [])
    if len(all_comments) > since_count:
        return all_comments[since_count:]
    return []


async def _build_task_prompt(task: dict, project_name: str) -> str:
    """タスク情報からprocess_message用のプロンプトを構築"""
    task_name = task.get("タスク名", "不明なタスク")
    priority = task.get("優先度", "中")
    memo = task.get("メモ", "")
    deadline = task.get("期限", "")
    estimated = task.get("見積工数(h)", "")

    # タスクページの本文とコメントを並列取得
    content_result, comments_result = await asyncio.gather(
        get_page_content(task["id"]),
        list_comments(task["id"]),
    )

    page_content = ""
    if content_result.get("success"):
        blocks = content_result.get("blocks", [])
        lines = [b["content"] for b in blocks if b["content"]]
        page_content = "\n".join(lines)

    comments_text = ""
    if comments_result.get("success"):
        for c in comments_result.get("comments", []):
            comments_text += f"- {c['text']}\n"

    prompt = f"""以下のNotionタスクを実行して。

## タスク情報
- プロジェクト: {project_name}
- タスク名: {task_name}
- 優先度: {priority}
- 期限: {deadline or '未設定'}
- 見積工数: {estimated or '未設定'}時間
- メモ: {memo or 'なし'}

## タスク詳細
{page_content or '（本文なし）'}

## コメント履歴
{comments_text or '（コメントなし）'}

## 指示
1. タスクの内容を理解して、必要な作業を実行して
2. コーディングが必要ならdelegate_to_claudeを使って
3. 調査が必要ならsearch_webやbrowse_urlを使って
4. 作業が完了したら、何をやったか具体的に報告して
5. 成果物（コード、ドキュメント等）があれば内容を返して"""

    # 過去の振り返りから教訓を注入（Reflexion）
    try:
        reflections = await get_recent_reflections(days=2)
        if reflections.get("reflections"):
            latest = reflections["reflections"][0]["content"]
            # 最新の教訓だけ抽出（長すぎないように）
            lessons = [
                line for line in latest.split("\n")
                if "次回の教訓" in line or "改善点" in line
            ]
            if lessons:
                prompt += "\n\n## 過去の教訓（参考にすること）\n" + "\n".join(lessons[:5])
    except Exception:
        pass

    return prompt


async def execute_single_task(
    task: dict,
    project_name: str,
    push_fn=None,
) -> dict[str, Any]:
    """1つのタスクを実行する

    Returns:
        {
            "success": bool,
            "result": str,  # 実行結果テキスト
            "deliverable_path": str | None,  # 成果物パス
            "interrupted": bool,  # コメントで中断されたか
        }
    """
    from agent.loop import process_message

    task_id = task["id"]
    task_name = task.get("タスク名", "不明")
    state = _load_state()

    logger.info(f"=== Task execution start: {task_name} ===")
    import time as _time
    _task_start = _time.monotonic()

    # ステータスを「進行中」に更新
    await update_task(task_id, json.dumps({"ステータス": "進行中"}))

    # 開始コメント
    await add_comment(task_id, f"識ちゃん: タスク開始します — {task_name}")

    # ステート保存
    state["running"] = True
    state["current_task_id"] = task_id
    _save_state(state)

    # 実行前のコメント数を記録（新コメント検知用）
    comments_before = await list_comments(task_id)
    comment_count_before = comments_before.get("count", 0)

    # プロンプト構築
    prompt = await _build_task_prompt(task, project_name)

    # 実行中コメントチェック用のコールバック
    _last_comment_check = [0.0]  # mutable for closure
    _interrupt_comments: list[str] = []

    async def _check_comments_callback(iteration: int, tool_calls: list) -> str | None:
        """ReActループのイテレーションごとにNotionコメントをチェック"""
        import time as _t
        now = _t.monotonic()
        # コメントチェックは最低30秒間隔
        if now - _last_comment_check[0] < COMMENT_CHECK_INTERVAL:
            return None
        _last_comment_check[0] = now

        try:
            new_comments = await _get_new_comments(task_id, comment_count_before + len(_interrupt_comments))
            user_comments = [
                c for c in new_comments
                if not c["text"].startswith("識ちゃん:")
            ]
            if user_comments:
                interrupt_text = "\n".join(c["text"] for c in user_comments)
                _interrupt_comments.extend(c["text"] for c in user_comments)
                logger.info(f"Live interrupt at iteration {iteration}: {interrupt_text[:100]}")
                await add_comment(task_id, "識ちゃん: コメント確認、対応中！")
                return f"オーナーから追加指示: {interrupt_text}"
        except Exception as e:
            logger.warning(f"Comment check failed: {e}")
        return None

    try:
        # タスク実行（process_messageのReActループに委譲 + 割り込みコールバック付き）
        result = await asyncio.wait_for(
            process_message(prompt, iteration_callback=_check_comments_callback),
            timeout=TASK_TIMEOUT,
        )

        result_text = result.get("text", "完了")

        # 成果物保存
        deliverable_path = None
        if len(result_text) > 100:
            deliverable_path = str(_save_deliverable(
                task_name, project_name, result_text,
            ))

        # ステータスを「完了」に更新
        await update_task(task_id, json.dumps({
            "ステータス": "完了",
            "進捗率": 1.0,
        }))

        # 完了コメント（詳細な作業報告）
        _elapsed = _time.monotonic() - _task_start
        elapsed_str = f"{_elapsed:.0f}秒" if _elapsed < 60 else f"{_elapsed/60:.1f}分"
        summary = result_text[:500]
        if len(result_text) > 500:
            summary += "...(続きは成果物ファイルを参照)"

        completion_report = f"識ちゃん: 完了しました！（{elapsed_str}）\n\n"
        completion_report += f"【作業内容】\n{summary}\n"
        if deliverable_path:
            completion_report += f"\n【成果物】{deliverable_path}"
        if _interrupt_comments:
            completion_report += f"\n【割り込み対応】実行中にオーナーからの{len(_interrupt_comments)}件のコメントを反映済み"

        await add_comment(task_id, completion_report)

        # 自己振り返り（Reflexionパターン）
        _elapsed = _time.monotonic() - _task_start
        try:
            reflection = await _reflect_on_task(
                task_name=task_name,
                project_name=project_name,
                success=True,
                result_text=result_text,
                interrupted=bool(_interrupt_comments),
                execution_time_s=_elapsed,
            )
            logger.info(f"Reflection: {reflection[:100]}")
        except Exception as ref_err:
            logger.warning(f"Reflection failed: {ref_err}")

        # Discord通知
        if push_fn:
            from config import DISCORD_OWNER_ID
            notify_text = (
                f"[タスク完了] {project_name} / {task_name}\n"
                f"{result_text[:300]}"
            )
            if deliverable_path:
                notify_text += f"\n成果物: {deliverable_path}"
            try:
                await push_fn(str(DISCORD_OWNER_ID), notify_text)
            except Exception as e:
                logger.warning(f"Discord notify failed: {e}")

        # ステート更新
        state["current_task_id"] = None
        state["completed_task_ids"].append(task_id)
        state["total_completed"] = state.get("total_completed", 0) + 1
        state["last_run"] = datetime.now().isoformat()
        _save_state(state)

        logger.info(f"=== Task completed: {task_name} ===")

        return {
            "success": True,
            "result": result_text,
            "deliverable_path": deliverable_path,
            "interrupted": bool(user_comments),
        }

    except asyncio.TimeoutError:
        logger.error(f"Task timed out: {task_name}")
        _elapsed = _time.monotonic() - _task_start
        try:
            await _reflect_on_task(task_name, project_name, False, "タイムアウト", False, _elapsed)
        except Exception as ref_err:
            logger.warning(f"Reflection after timeout failed: {ref_err}")
        try:
            await update_task(task_id, json.dumps({"ステータス": "未着手"}))
            await add_comment(
                task_id,
                f"識ちゃん: タイムアウトしました。タスクを分割するか、もう少し具体的に書いてもらえると助かります。",
            )
        except Exception as cleanup_err:
            logger.error(f"Cleanup after timeout failed: {cleanup_err}")
        state["running"] = False
        state["current_task_id"] = None
        _save_state(state)
        return {"success": False, "result": "タイムアウト", "deliverable_path": None, "interrupted": False}

    except Exception as e:
        logger.error(f"Task execution failed: {task_name} — {e}", exc_info=True)
        _elapsed = _time.monotonic() - _task_start
        try:
            await _reflect_on_task(task_name, project_name, False, str(e)[:200], False, _elapsed)
        except Exception as ref_err:
            logger.warning(f"Reflection after error failed: {ref_err}")
        # エラー内容をサニタイズ（ファイルパスやスタック情報を除去）
        safe_error = type(e).__name__
        try:
            await update_task(task_id, json.dumps({"ステータス": "未着手"}))
            await add_comment(
                task_id,
                f"識ちゃん: エラーが発生しました（{safe_error}）。ログを確認します。",
            )
        except Exception as cleanup_err:
            logger.error(f"Cleanup after error failed: {cleanup_err}")
        state["running"] = False
        state["current_task_id"] = None
        _save_state(state)
        return {"success": False, "result": safe_error, "deliverable_path": None, "interrupted": False}


async def execute_project_tasks(
    project_id: str | None = None,
    max_tasks: int = MAX_CONSECUTIVE_TASKS,
    push_fn=None,
) -> dict[str, Any]:
    """プロジェクトの未着手タスクを順番に実行する

    Args:
        project_id: 特定プロジェクトのタスクのみ実行（省略で全プロジェクト）
        max_tasks: 最大実行数（暴走防止）
        push_fn: Discord通知用コールバック

    Returns:
        {"success": bool, "completed": int, "failed": int, "results": [...]}
    """
    # 排他ロック取得（二重実行防止）
    if not _acquire_lock():
        return {
            "success": False,
            "error": "既にタスク実行中です（ロック取得失敗）",
        }

    state = _load_state()
    state["running"] = True
    _save_state(state)

    results = []
    completed = 0
    failed = 0

    try:
        # プロジェクトとタスクを取得
        if project_id:
            # 特定プロジェクト
            from tools.notion import get_project
            proj_result = await get_project(project_id)
            if not proj_result.get("success"):
                return {"success": False, "error": "プロジェクト取得失敗"}
            project_name = proj_result["project"].get("プロジェクト名", "?")
            tasks_result = await list_tasks(project_id=project_id, status="未着手")
            if tasks_result.get("success"):
                pending_tasks = [
                    (t, project_name) for t in tasks_result.get("tasks", [])
                ]
            else:
                pending_tasks = []
        else:
            # 全プロジェクト横断
            proj_result = await list_projects()
            if not proj_result.get("success"):
                return {"success": False, "error": "プロジェクト一覧取得失敗"}

            pending_tasks = []
            for proj in proj_result["projects"]:
                pstatus = proj.get("ステータス", "")
                if pstatus in ("完了", "保留"):
                    continue
                pid = proj["id"]
                pname = proj.get("プロジェクト名", "?")
                tasks_result = await list_tasks(project_id=pid, status="未着手")
                if tasks_result.get("success"):
                    for t in tasks_result.get("tasks", []):
                        pending_tasks.append((t, pname))

        if not pending_tasks:
            state["running"] = False
            _save_state(state)
            return {
                "success": True,
                "completed": 0,
                "failed": 0,
                "results": [],
                "message": "未着手のタスクはありません",
            }

        # 優先度でソート（高→中→低）
        priority_order = {"高": 0, "中": 1, "低": 2}
        pending_tasks.sort(
            key=lambda x: priority_order.get(x[0].get("優先度", "中"), 1)
        )

        # 開始通知
        if push_fn:
            from config import DISCORD_OWNER_ID
            task_list = "\n".join(
                f"  {i+1}. [{pname}] {t.get('タスク名', '?')}（{t.get('優先度', '中')}）"
                for i, (t, pname) in enumerate(pending_tasks[:max_tasks])
            )
            try:
                await push_fn(
                    str(DISCORD_OWNER_ID),
                    f"タスク実行開始！{min(len(pending_tasks), max_tasks)}件やるね。\n{task_list}",
                )
            except Exception as e:
                logger.warning(f"Discord start notification failed: {e}")

        # タスクを順番に実行
        for i, (task, pname) in enumerate(pending_tasks[:max_tasks]):
            logger.info(f"Executing task {i+1}/{min(len(pending_tasks), max_tasks)}")

            result = await execute_single_task(task, pname, push_fn)
            results.append({
                "task_name": task.get("タスク名", "?"),
                "project_name": pname,
                **result,
            })

            if result["success"]:
                completed += 1
            else:
                failed += 1
                # 2連続失敗で中止（何かおかしい）
                if failed >= 2 and completed == 0:
                    logger.warning("2 consecutive failures, stopping execution")
                    break

            # 次のタスクの前に少し待つ（API負荷軽減）
            await asyncio.sleep(3)

    finally:
        state["running"] = False
        state["current_task_id"] = None
        _save_state(state)
        _release_lock()

    # 完了通知（Discord + Notion）
    if push_fn:
        from config import DISCORD_OWNER_ID
        summary = f"タスク実行完了！ 成功: {completed}件 / 失敗: {failed}件\n"
        for r in results:
            status = "OK" if r["success"] else "NG"
            summary += f"\n  [{status}] [{r['project_name']}] {r['task_name']}"
            if r.get("deliverable_path"):
                summary += f"\n    → {r['deliverable_path']}"
        try:
            await push_fn(str(DISCORD_OWNER_ID), summary)
        except Exception as e:
            logger.warning(f"Discord completion notification failed: {e}")

    return {
        "success": completed > 0,
        "completed": completed,
        "failed": failed,
        "results": results,
    }


# =============================================
# 自己振り返り（Reflexionパターン）
# =============================================

_REFLECTION_DIR = Path(__file__).parent.parent / ".ritsu" / "reflections"


async def _reflect_on_task(
    task_name: str,
    project_name: str,
    success: bool,
    result_text: str,
    interrupted: bool,
    execution_time_s: float,
) -> str:
    """タスク完了後の自己振り返りを生成・保存

    Reflexionパターン: 何がうまくいった/いかなかった/次回の改善点を記録。
    蓄積された振り返りは将来のタスク実行に活かされる。
    """
    import google.genai as genai
    from config import GEMINI_API_KEY, GEMINI_MODEL

    _REFLECTION_DIR.mkdir(parents=True, exist_ok=True)

    # Geminiに振り返りを生成させる
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""以下のタスク実行結果を振り返って、3行で分析して。

タスク: {task_name}（{project_name}）
結果: {"成功" if success else "失敗"}
実行時間: {execution_time_s:.0f}秒
割り込み: {"あり" if interrupted else "なし"}
出力概要: {result_text[:500]}

以下の形式で書いて:
- うまくいった点: ...
- 改善点: ...
- 次回の教訓: ..."""

        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=200,
            ),
        )
        reflection = (response.text or "").strip()
    except Exception as e:
        logger.warning(f"Reflection generation failed: {e}")
        reflection = f"- 結果: {'成功' if success else '失敗'}（{execution_time_s:.0f}秒）"

    # ファイルに保存（日付別）
    today = datetime.now().strftime("%Y-%m-%d")
    reflection_file = _REFLECTION_DIR / f"{today}.md"

    entry = (
        f"\n## {datetime.now().strftime('%H:%M')} — {task_name}（{project_name}）\n"
        f"**結果:** {'成功' if success else '失敗'} | "
        f"**時間:** {execution_time_s:.0f}秒 | "
        f"**割り込み:** {'あり' if interrupted else 'なし'}\n"
        f"{reflection}\n"
    )

    try:
        with open(reflection_file, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write(f"# 振り返りログ — {today}\n")
            f.write(entry)
        logger.info(f"Reflection saved: {reflection_file}")
    except Exception as e:
        logger.warning(f"Reflection save failed: {e}")

    return reflection


async def get_recent_reflections(days: int = 3) -> dict[str, Any]:
    """直近の振り返りログを取得"""
    _REFLECTION_DIR.mkdir(parents=True, exist_ok=True)
    reflections = []
    today = datetime.now().date()
    from datetime import timedelta
    for i in range(days):
        date = today - timedelta(days=i)
        filepath = _REFLECTION_DIR / f"{date.isoformat()}.md"
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            reflections.append({"date": date.isoformat(), "content": content})
    return {
        "success": True,
        "reflections": reflections,
        "count": len(reflections),
    }


async def get_execution_status() -> dict[str, Any]:
    """現在の実行状態を返す"""
    state = _load_state()
    return {
        "success": True,
        "running": state.get("running", False),
        "current_task_id": state.get("current_task_id"),
        "total_completed": state.get("total_completed", 0),
        "last_run": state.get("last_run"),
    }

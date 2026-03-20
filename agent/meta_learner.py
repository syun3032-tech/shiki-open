"""メタ学習エンジン — 学習の複利効果を生む

各学習ループ（スキル、エピソード記憶、プレイブック、自己修復、ワークフロー）を
横断的に分析し、学習を加速させる。

「学習する方法を学習する」= Meta-Learning

定期実行（1日1回、日次要約と一緒に）:
1. 今日の全学習データを収集
2. クロスシステム分析（あるシステムの学習が他のシステムを強化できるか）
3. パフォーマンス指標の追跡（速くなってるか、正確になってるか）
4. システムプロンプトの自動改善提案
5. 学習効率レポートをNotionに記録

複利効果の仕組み:
- 自己修復で直したバグ → 「こういうコードを書くな」スキル生成
- ワークフロー学習で検出したパターン → エピソード記憶に転記
- エピソード記憶の成功事例 → プレイブックに昇格
- プレイブックの使用実績 → スキルのスコア調整
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

from config import RITSU_DIR, GEMINI_API_KEY

logger = logging.getLogger("shiki.meta_learner")

# === パフォーマンス指標 ===
_METRICS_FILE = RITSU_DIR / "learning_metrics.json"


def _load_metrics() -> dict:
    if _METRICS_FILE.exists():
        try:
            return json.loads(_METRICS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"daily": {}, "cumulative": {}}


def _save_metrics(metrics: dict):
    RITSU_DIR.mkdir(parents=True, exist_ok=True)
    # 最新90日分のみ保持
    if "daily" in metrics:
        keys = sorted(metrics["daily"].keys())
        if len(keys) > 90:
            for old_key in keys[:-90]:
                del metrics["daily"][old_key]
    _METRICS_FILE.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def record_daily_metrics() -> dict:
    """今日の学習指標を記録"""
    today = date.today().isoformat()
    metrics = _load_metrics()

    # 各システムの今日の数値を収集
    day_data = {
        "date": today,
        "timestamp": datetime.now().isoformat(),
    }

    # スキル数
    try:
        from agent.skill_evolver import get_stats as skill_stats
        s = skill_stats()
        day_data["skills_total"] = s.get("total", 0)
        day_data["skills_usage"] = s.get("total_usage", 0)
    except Exception:
        pass

    # エピソード記憶
    try:
        from agent.episodic_memory import get_episode_count
        day_data["episodes_total"] = get_episode_count()
    except Exception:
        try:
            ep_dir = RITSU_DIR / "episodes"
            if ep_dir.exists():
                day_data["episodes_total"] = len(list(ep_dir.glob("*.json")))
        except Exception:
            pass

    # プレイブック
    try:
        from agent.playbook import get_playbook_count
        day_data["playbooks_total"] = get_playbook_count()
    except Exception:
        try:
            pb_file = RITSU_DIR / "playbooks.json"
            if pb_file.exists():
                pbs = json.loads(pb_file.read_text(encoding="utf-8"))
                day_data["playbooks_total"] = len(pbs)
        except Exception:
            pass

    # 自己修復
    try:
        from tools.self_heal import get_heal_stats
        h = get_heal_stats()
        day_data["heal_total"] = h.get("total_fixes", 0)
        day_data["heal_patterns"] = h.get("success_patterns", 0)
        day_data["heal_crystallized"] = h.get("crystallized", 0)
    except Exception:
        pass

    # ワークフロー
    try:
        from agent.continuous_observer import get_observer
        obs = get_observer()
        day_data["workflows_total"] = len(obs.workflows)
        day_data["apps_tracked"] = len(obs.app_usage)
    except Exception:
        pass

    # タスク実行
    try:
        from tools.notion_executor import get_execution_status
        import asyncio
        status = asyncio.get_event_loop().run_until_complete(get_execution_status())
        day_data["tasks_completed"] = status.get("total_completed", 0)
    except Exception:
        pass

    metrics["daily"][today] = day_data

    # 累積指標の計算
    _compute_cumulative(metrics)

    _save_metrics(metrics)
    logger.info(f"Daily metrics recorded: {day_data}")
    return day_data


def _compute_cumulative(metrics: dict):
    """累積パフォーマンス指標を計算"""
    daily = metrics.get("daily", {})
    if len(daily) < 2:
        return

    dates = sorted(daily.keys())
    latest = daily[dates[-1]]
    previous = daily[dates[-2]] if len(dates) >= 2 else {}
    week_ago_key = (date.today() - timedelta(days=7)).isoformat()
    week_ago = daily.get(week_ago_key, {})

    cumulative = {
        "updated_at": datetime.now().isoformat(),
        "total_days_tracked": len(dates),
    }

    # 成長率（前日比）
    for key in ("skills_total", "episodes_total", "playbooks_total", "heal_total", "workflows_total"):
        curr = latest.get(key, 0)
        prev = previous.get(key, 0)
        if prev > 0:
            cumulative[f"{key}_daily_growth"] = round((curr - prev) / prev * 100, 1)

    # 週間成長率
    for key in ("skills_total", "heal_total"):
        curr = latest.get(key, 0)
        week = week_ago.get(key, 0)
        if week > 0:
            cumulative[f"{key}_weekly_growth"] = round((curr - week) / week * 100, 1)

    # 学習速度（スキル増加ペース）
    if len(dates) >= 7:
        recent_7 = [daily[d].get("skills_total", 0) for d in dates[-7:]]
        if recent_7[0] > 0:
            cumulative["skill_velocity_7d"] = recent_7[-1] - recent_7[0]

    metrics["cumulative"] = cumulative


# === クロスシステム学習 ===

async def cross_system_learning() -> list[str]:
    """各学習システムの知見を相互に転用する

    Returns: 実行したアクションのリスト
    """
    actions = []

    # 1. 自己修復パターン → スキル化（self_heal.pyの_crystallize_patterns()）
    try:
        from tools.self_heal import _crystallize_patterns
        _crystallize_patterns()
        actions.append("自己修復パターンのスキル結晶化チェック完了")
    except Exception as e:
        logger.debug(f"Crystallization check failed: {e}")

    # 2. プレイブックの成功率でスキルスコア調整
    try:
        from agent.playbook import _load_playbooks
        from agent.skill_evolver import _load_evolved_skills, _save_evolved_skills

        playbooks = _load_playbooks()
        skills = _load_evolved_skills()

        # プレイブックで使われてるツール列 → 対応スキルのスコアUP
        pb_tools = set()
        for pb in playbooks:
            for step in pb.get("tool_calls", []):
                if isinstance(step, dict):
                    pb_tools.add(step.get("tool", ""))
                elif isinstance(step, str):
                    pb_tools.add(step)

        boosted = 0
        for skill in skills:
            tool_seq = skill.get("tool_sequence", [])
            if tool_seq and any(t in pb_tools for t in tool_seq):
                old_score = skill.get("score", 0.5)
                skill["score"] = min(0.95, old_score + 0.02)
                if skill["score"] > old_score:
                    boosted += 1

        if boosted > 0:
            _save_evolved_skills(skills)
            actions.append(f"プレイブック連携: {boosted}個のスキルスコアを向上")

    except Exception as e:
        logger.debug(f"Playbook-skill boost failed: {e}")

    # 3. スキルの使用実績でプルーニング
    try:
        from agent.skill_evolver import prune_skills
        prune_skills()
        actions.append("低品質スキルのプルーニング完了")
    except Exception as e:
        logger.debug(f"Skill pruning failed: {e}")

    return actions


# === 学習レポート ===

async def generate_learning_report() -> str:
    """学習状況レポートを生成（日次要約と一緒に使う）"""
    metrics = _load_metrics()
    cumulative = metrics.get("cumulative", {})
    today_data = metrics.get("daily", {}).get(date.today().isoformat(), {})

    if not today_data:
        today_data = record_daily_metrics()

    lines = ["# 学習レポート", ""]

    # 今日の数値
    lines.append("## 今日の状態")
    lines.append(f"- スキル数: {today_data.get('skills_total', '?')}")
    lines.append(f"- ワークフロー: {today_data.get('workflows_total', '?')}個")
    lines.append(f"- 自己修復: {today_data.get('heal_total', '?')}件（パターン: {today_data.get('heal_patterns', '?')}）")
    lines.append(f"- タスク完了: {today_data.get('tasks_completed', '?')}件")

    # 成長率
    if cumulative:
        lines.append("")
        lines.append("## 成長率")
        for key, label in [
            ("skills_total_daily_growth", "スキル（日次）"),
            ("skills_total_weekly_growth", "スキル（週次）"),
            ("heal_total_weekly_growth", "自己修復（週次）"),
        ]:
            val = cumulative.get(key)
            if val is not None:
                lines.append(f"- {label}: {val:+.1f}%")

        velocity = cumulative.get("skill_velocity_7d")
        if velocity is not None:
            lines.append(f"- スキル増加ペース（7日間）: +{velocity}個")

    return "\n".join(lines)


# === メタ学習サイクル（schedulerから呼ばれる） ===

async def meta_learning_cycle(push_fn=None) -> dict[str, Any]:
    """メタ学習サイクル（1日1回）

    1. 日次指標記録
    2. クロスシステム学習
    3. レポート生成
    """
    logger.info("=== Meta-learning cycle start ===")

    # 1. 指標記録
    day_data = record_daily_metrics()

    # 2. クロスシステム学習
    actions = await cross_system_learning()

    # 3. レポート生成
    report = await generate_learning_report()

    # 4. Discord通知（簡潔に）
    if push_fn:
        from config import DISCORD_OWNER_ID
        skills = day_data.get("skills_total", "?")
        heals = day_data.get("heal_total", "?")
        wfs = day_data.get("workflows_total", "?")
        msg = (
            f"[学習レポート] スキル: {skills} / 自己修復: {heals}件 / ワークフロー: {wfs}個\n"
            + (f"実行: {', '.join(actions[:3])}" if actions else "")
        )
        try:
            await push_fn(str(DISCORD_OWNER_ID), msg)
        except Exception:
            pass

    logger.info(f"=== Meta-learning done: {len(actions)} cross-system actions ===")
    return {
        "metrics": day_data,
        "actions": actions,
        "report_length": len(report),
    }


# === Claude Code壁打ち（自己改善セッション） ===

async def self_improvement_session(push_fn=None) -> dict[str, Any]:
    """Claude Codeとの壁打ちセッション

    メタ学習の結果をもとに、識ちゃんが自分のコードをClaude Codeに見せて
    改善提案をもらい、実装する。

    対象:
    - エラーが多いファイル（heal_logから検出）
    - 使われてないスキル（pruning対象）
    - パフォーマンスが悪い箇所
    """
    from tools.self_heal import (
        _load_heal_log, _is_protected, test_fix, _git_commit_fix, PROJECT_ROOT,
    )
    from tools.claude_code import delegate_to_claude

    logger.info("=== Self-improvement session start ===")

    improvements = []

    # 1. エラーが多いファイルを特定
    heal_log = _load_heal_log()
    file_error_counts: dict[str, int] = {}
    for entry in heal_log:
        f = entry.get("file", "")
        if f:
            file_error_counts[f] = file_error_counts.get(f, 0) + 1

    # エラーが2回以上あるファイル = 根本的な改善が必要
    problem_files = [
        (f, count) for f, count in file_error_counts.items()
        if count >= 2 and not _is_protected(str(PROJECT_ROOT / f))
    ]
    problem_files.sort(key=lambda x: x[1], reverse=True)

    # 2. 問題ファイルのコードレビューをClaude Codeに依頼
    for filepath_rel, error_count in problem_files[:2]:  # 1セッション最大2ファイル
        filepath = str(PROJECT_ROOT / filepath_rel)

        try:
            source = Path(filepath).read_text(encoding="utf-8")
            if len(source) > 15000:
                source = source[:15000] + "\n... (truncated)"

            # 過去のエラー内容も渡す
            related_errors = [
                e for e in heal_log if e.get("file") == filepath_rel
            ][-5:]
            error_summary = "\n".join(
                f"- {e.get('error', '?')}" for e in related_errors
            )

            task = f"""このファイルのコードレビューと改善をして。

ファイル: {filepath}
過去のエラー（{error_count}回）:
{error_summary}

改善の方針:
- エラーの根本原因を修正する（対症療法ではなく）
- エッジケースのハンドリングを強化
- 型チェック、None チェックの追加
- リファクタリングは最小限に
- 新しい機能は追加しない、既存の安定性を上げることに集中"""

            result = await asyncio.wait_for(
                delegate_to_claude(task=task, context=f"file: {filepath}", timeout=180),
                timeout=200,
            )

            if result.get("success"):
                # テスト
                ok, msg = await test_fix(filepath)
                if ok:
                    commit_hash = _git_commit_fix(
                        filepath, "self-improve", result.get("text", "")[:60],
                    )
                    improvements.append({
                        "file": filepath_rel,
                        "explanation": result.get("text", "")[:200],
                        "commit": commit_hash,
                    })
                    logger.info(f"Self-improvement applied: {filepath_rel}")
                else:
                    # テスト失敗 → 元に戻す
                    Path(filepath).write_text(source.replace("\n... (truncated)", ""), encoding="utf-8")
                    logger.warning(f"Self-improvement failed test: {filepath_rel}: {msg}")

        except Exception as e:
            logger.warning(f"Self-improvement session error for {filepath_rel}: {e}")

    # 3. 通知
    if push_fn and improvements:
        from config import DISCORD_OWNER_ID
        lines = ["[自己改善セッション]"]
        for imp in improvements:
            lines.append(f"- {imp['file']}: {imp['explanation'][:80]}")
            if imp.get("commit"):
                lines.append(f"  commit: {imp['commit']}")
        try:
            await push_fn(str(DISCORD_OWNER_ID), "\n".join(lines))
        except Exception:
            pass

    logger.info(f"=== Self-improvement session done: {len(improvements)} files improved ===")
    return {"improvements": len(improvements), "details": improvements}


# === 外部API ===

def get_learning_dashboard() -> dict:
    """学習ダッシュボード（全指標の概要）"""
    metrics = _load_metrics()
    cumulative = metrics.get("cumulative", {})
    daily = metrics.get("daily", {})
    dates = sorted(daily.keys())

    return {
        "total_days": len(dates),
        "cumulative": cumulative,
        "latest": daily.get(dates[-1], {}) if dates else {},
        "trend": [
            {"date": d, "skills": daily[d].get("skills_total", 0)}
            for d in dates[-14:]
        ],
    }

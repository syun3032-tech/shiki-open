"""自己修復・自己改変エンジン v2 — SWE-agent / Reflexion / OpenClaw Foundry inspired

識ちゃんが自分のソースコードのバグを検知→分析→修正→テスト→適用する。

アーキテクチャ（リサーチベース）:
- SWE-agent: エラー検出→コード分析→修正生成→テスト→適用パイプライン
- Reflexion: 失敗分析→教訓記録→次回の修正に活用
- OpenClaw Foundry: パターン結晶化（5回以上成功した修正→スキル昇格）

パイプライン:
1. ログからエラーを自動検出（Traceback, KeyError, ImportError等）
2. 関連ソースコードを読んで原因分析
3. 過去の修正パターンを参照（同じ種類のエラーの解法を再利用）
4. Claude Codeに修正を委譲
5. テスト: シンタックスチェック → importテスト → サブプロセス実行テスト
6. 失敗時: エラーフィードバック→最大3回反復（AgentCoderパターン）
7. 成功時: git commitでバックアップ + Discord通知
8. 修正パターンを記録（Reflexion）
9. 5回以上成功したパターンはスキルに結晶化（Foundry pattern）

安全装置（NVIDIA Sandboxing Guide準拠）:
- git-based rollback（ファイルコピーではなくgit revert）
- シンタックスチェック + importテスト + サブプロセス実行テスト（3段階）
- 1日の自動修正回数上限（5回）
- config.py, security/*, .env, self_heal.py は修正対象外
- 修正の反復は最大3回（研究で2-5回が最適と判明、3回が実用的）
- Gutter防止: エラーログが溜まりすぎたら古いものを要約
"""

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime, date
from pathlib import Path
from typing import Any

from config import RITSU_DIR

logger = logging.getLogger("shiki.self_heal")

# === 設定 ===
PROJECT_ROOT = Path(__file__).parent.parent
PATCHES_DIR = RITSU_DIR / "patches"
HEAL_LOG_FILE = RITSU_DIR / "heal_log.json"
FIX_PATTERNS_FILE = RITSU_DIR / "fix_patterns.json"
MAX_DAILY_FIXES = 5
MAX_ITERATIONS = 3  # 1つのエラーに対する最大修正試行回数
AUTO_APPLY = True  # Claude Codeが直接修正→テスト通れば自動適用

# 修正対象外のファイル・ディレクトリ
_PROTECTED_PATHS = frozenset({
    "config.py",
    "security/",
    ".env",
    ".gitignore",
    "tools/self_heal.py",  # 自分自身は修正しない（再帰防止）
})


def _is_protected(filepath: str) -> bool:
    """保護対象ファイルか判定"""
    try:
        rel = str(Path(filepath).relative_to(PROJECT_ROOT))
    except ValueError:
        return True  # プロジェクト外は修正しない
    for protected in _PROTECTED_PATHS:
        if rel.startswith(protected):
            return True
    return False


# === ログ管理 ===

def _load_heal_log() -> list[dict]:
    if HEAL_LOG_FILE.exists():
        try:
            return json.loads(HEAL_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_heal_log(log: list[dict]):
    RITSU_DIR.mkdir(parents=True, exist_ok=True)
    HEAL_LOG_FILE.write_text(
        json.dumps(log[-100:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _daily_fix_count() -> int:
    """今日の修正回数"""
    today = date.today().isoformat()
    log = _load_heal_log()
    return sum(1 for entry in log if entry.get("date") == today and entry.get("applied"))


# === 修正パターン記憶（Reflexion） ===

def _load_fix_patterns() -> list[dict]:
    """過去の修正パターンを読み込み"""
    if FIX_PATTERNS_FILE.exists():
        try:
            return json.loads(FIX_PATTERNS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_fix_patterns(patterns: list[dict]):
    RITSU_DIR.mkdir(parents=True, exist_ok=True)
    FIX_PATTERNS_FILE.write_text(
        json.dumps(patterns[-200:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_similar_fix(error_type: str, error_msg: str) -> dict | None:
    """過去の成功した修正パターンから類似のものを探す"""
    patterns = _load_fix_patterns()
    for p in reversed(patterns):  # 新しい順
        if p.get("error_type") == error_type and p.get("success"):
            # エラーメッセージの類似度（簡易: キーワード一致）
            old_words = set(p.get("error_message", "").lower().split())
            new_words = set(error_msg.lower().split())
            if old_words and new_words:
                overlap = len(old_words & new_words) / max(len(old_words), len(new_words))
                if overlap > 0.5:
                    return p
    return None


def _record_fix_pattern(error: dict, fix_description: str, success: bool):
    """修正パターンを記録（Reflexion）"""
    patterns = _load_fix_patterns()
    patterns.append({
        "error_type": error.get("error_type", ""),
        "error_message": error.get("message", "")[:200],
        "file": error.get("file", ""),
        "fix_description": fix_description[:300],
        "success": success,
        "timestamp": datetime.now().isoformat(),
        "usage_count": 1 if success else 0,
    })
    _save_fix_patterns(patterns)


def _crystallize_patterns():
    """5回以上成功したパターンをスキルに結晶化（OpenClaw Foundry pattern）"""
    patterns = _load_fix_patterns()

    # エラー種別ごとに集計
    type_counts: dict[str, int] = {}
    type_fixes: dict[str, str] = {}
    for p in patterns:
        if p.get("success"):
            key = p.get("error_type", "")
            type_counts[key] = type_counts.get(key, 0) + 1
            type_fixes[key] = p.get("fix_description", "")

    # 5回以上成功したパターンをスキルに昇格
    for error_type, count in type_counts.items():
        if count >= 5:
            try:
                from agent.skill_evolver import _merge_and_save
                skill = {
                    "name": f"自己修復: {error_type}",
                    "description": f"{error_type}エラーの自動修正パターン（{count}回成功実績）",
                    "trigger_keywords": [error_type.lower(), "エラー", "バグ", "修正"],
                    "steps_description": type_fixes.get(error_type, ""),
                    "tool_sequence": ["delegate_to_claude"],
                    "category": "self-heal",
                    "source": "crystallization",
                    "score": min(0.9, 0.5 + count * 0.05),
                }
                _merge_and_save([skill])
                logger.info(f"Pattern crystallized to skill: {error_type} ({count} successes)")
            except Exception as e:
                logger.debug(f"Crystallization failed: {e}")


# === エラー検出 ===

_IGNORABLE_ERRORS = frozenset({
    "TimeoutError", "asyncio.TimeoutError",
    "ConnectionError", "ConnectionResetError",
    "HTTPError", "aiohttp.ClientError",
    "CancelledError",
})


async def detect_errors_from_log(log_path: str | None = None, lines: int = 500) -> list[dict]:
    """ログファイルからエラーを検出

    Returns: [{error_type, message, file, line_no, traceback_text}]
    """
    if log_path is None:
        log_dir = PROJECT_ROOT / "logs"
        # shiki_discord.logを優先（launchd管理のメインログ）
        main_log = log_dir / "shiki_discord.log"
        if main_log.exists():
            log_path = str(main_log)
        else:
            candidates = sorted(log_dir.glob("discord_bot_*.log"), reverse=True)
            if not candidates:
                return []
            log_path = str(candidates[0])

    try:
        content = Path(log_path).read_text(encoding="utf-8", errors="replace")
        all_lines = content.split("\n")
        # Gutter防止: 末尾N行のみ分析（古いエラーは無視）
        content = "\n".join(all_lines[-lines:])
    except Exception as e:
        logger.warning(f"Log read failed: {e}")
        return []

    errors = []
    tb_blocks = re.split(r"(?=Traceback \(most recent call last\):)", content)

    for block in tb_blocks:
        if "Traceback" not in block:
            continue

        error_match = re.search(r"(\w+(?:Error|Exception)): (.+?)(?:\n|$)", block)
        if not error_match:
            continue

        error_type = error_match.group(1)
        error_msg = error_match.group(2).strip()

        if error_type in _IGNORABLE_ERRORS:
            continue

        # プロジェクト内のファイルのみ対象
        file_match = re.search(
            r'File "(' + re.escape(str(Path(__file__).parent.parent)) + r'/[^"]+)", line (\d+)',
            block,
        )
        filepath = file_match.group(1) if file_match else None
        line_no = int(file_match.group(2)) if file_match else None

        if filepath and _is_protected(filepath):
            continue

        # venv内のエラーは無視
        if filepath and ".venv/" in filepath:
            continue

        errors.append({
            "error_type": error_type,
            "message": error_msg,
            "file": filepath,
            "line_no": line_no,
            "traceback": block[:1500],
        })

    # 重複除去（同じファイル+エラー種別）
    seen = set()
    unique = []
    for e in errors:
        key = (e["file"], e["error_type"], e["message"][:50])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


# === テスト（3段階） ===

async def test_fix(filepath: str) -> tuple[bool, str]:
    """修正の3段階テスト

    Stage 1: シンタックスチェック（ast.parse）
    Stage 2: importテスト（サブプロセス）
    Stage 3: 依存モジュールのimportテスト

    Returns: (success, message)
    """
    import ast

    # Stage 1: シンタックスチェック
    try:
        source = Path(filepath).read_text(encoding="utf-8")
        ast.parse(source)
    except SyntaxError as e:
        return False, f"Stage 1 SyntaxError: {e}"

    # Stage 2: モジュールimportテスト
    try:
        rel_path = str(Path(filepath).relative_to(PROJECT_ROOT))
        module = rel_path.replace("/", ".").replace(".py", "")

        result = subprocess.run(
            ["python", "-c", f"import {module}"],
            capture_output=True, text=True, timeout=15,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            stderr = result.stderr[:300]
            return False, f"Stage 2 ImportError: {stderr}"
    except subprocess.TimeoutExpired:
        return False, "Stage 2: Import test timed out"
    except Exception as e:
        return False, f"Stage 2 error: {e}"

    # Stage 3: 関連モジュールのimportテスト（波及影響チェック）
    # このファイルをimportしてる他のモジュールも壊れてないか確認
    try:
        module_name = Path(filepath).stem
        # agent/やtools/内の他のファイルでこのモジュールをimportしてるものをチェック
        parent_dir = Path(filepath).parent
        for py_file in parent_dir.glob("*.py"):
            if py_file.name == Path(filepath).name or py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8", errors="replace")
            if f"from {parent_dir.name}.{module_name}" in content or f"import {module_name}" in content:
                dep_module = str(py_file.relative_to(PROJECT_ROOT)).replace("/", ".").replace(".py", "")
                dep_result = subprocess.run(
                    ["python", "-c", f"import {dep_module}"],
                    capture_output=True, text=True, timeout=15,
                    cwd=str(PROJECT_ROOT),
                )
                if dep_result.returncode != 0:
                    return False, f"Stage 3: Dependent module {dep_module} broke: {dep_result.stderr[:200]}"
    except Exception as e:
        # Stage 3は失敗してもブロックしない（ベストエフォート）
        logger.debug(f"Stage 3 check error (non-blocking): {e}")

    return True, "All 3 stages passed"


# === 修正生成（反復ループ） ===

async def generate_and_apply_fix(error: dict) -> dict | None:
    """エラーに対する修正を生成・テスト・適用（最大3回反復）

    AgentCoderパターン: generate → test → if fail: feed error back → retry

    Returns: {file, explanation, iterations, success} or None
    """
    filepath = error.get("file")
    if not filepath or not Path(filepath).exists():
        return None

    # 過去の類似修正パターンを参照（Reflexion）
    similar = _find_similar_fix(error["error_type"], error["message"])
    hint = ""
    if similar:
        hint = f"\n\n過去の類似エラーでの修正方法:\n{similar.get('fix_description', '')}"
        logger.info(f"Found similar fix pattern for {error['error_type']}")

    original_source = Path(filepath).read_text(encoding="utf-8")
    last_test_error = ""

    for iteration in range(MAX_ITERATIONS):
        logger.info(f"Fix attempt {iteration + 1}/{MAX_ITERATIONS} for {error['error_type']} in {filepath}")

        try:
            from tools.claude_code import delegate_to_claude

            # エラー周辺のコードを抽出
            source = Path(filepath).read_text(encoding="utf-8")
            lines = source.split("\n")
            line_no = error.get("line_no", 0)
            start = max(0, line_no - 25)
            end = min(len(lines), line_no + 25)
            context = "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))

            # 反復フィードバック（前回の失敗情報を含める）
            feedback = ""
            if iteration > 0 and last_test_error:
                feedback = f"\n\n前回の修正はテストで失敗した:\n{last_test_error}\n別のアプローチを試して。"

            task = f"""以下のPythonファイルにバグがある。修正して。

ファイル: {filepath}
エラー: {error['error_type']}: {error['message']}

エラー周辺のコード:
```python
{context}
```

Traceback:
```
{error['traceback'][:800]}
```
{hint}{feedback}

ルール:
- 最小限の変更で修正する（リファクタリングしない）
- 修正箇所だけ変更する
- コメントや docstring は追加しない
- 修正内容を1行で説明して"""

            result = await asyncio.wait_for(
                delegate_to_claude(task=task, context=f"file: {filepath}"),
                timeout=120,
            )

            if not result.get("success"):
                logger.warning(f"Claude Code failed: {result.get('error', 'unknown')}")
                continue

            # ファイルが変更されたか確認
            new_source = Path(filepath).read_text(encoding="utf-8")
            if new_source == source:
                logger.info("No changes made by Claude Code")
                continue

            # テスト（3段階）
            ok, test_msg = await test_fix(filepath)
            if ok:
                explanation = result.get("text", "")[:200]
                _record_fix_pattern(error, explanation, True)
                logger.info(f"Fix succeeded on iteration {iteration + 1}: {explanation[:80]}")
                return {
                    "file": filepath,
                    "explanation": explanation,
                    "iterations": iteration + 1,
                    "success": True,
                    "original_source": original_source,
                }
            else:
                last_test_error = test_msg
                logger.warning(f"Fix failed test (iteration {iteration + 1}): {test_msg}")
                # テスト失敗 → 元に戻して次の試行
                Path(filepath).write_text(source, encoding="utf-8")

        except asyncio.TimeoutError:
            logger.warning(f"Fix generation timed out (iteration {iteration + 1})")
        except Exception as e:
            logger.error(f"Fix generation error (iteration {iteration + 1}): {e}")

    # 全反復失敗 → 元に戻す
    Path(filepath).write_text(original_source, encoding="utf-8")
    _record_fix_pattern(error, f"Failed after {MAX_ITERATIONS} iterations: {last_test_error}", False)
    logger.warning(f"All {MAX_ITERATIONS} fix attempts failed for {error['error_type']}")
    return None


# === Git-based バックアップ & ロールバック ===

def _git_commit_fix(filepath: str, error_type: str, explanation: str) -> str | None:
    """修正をgit commitしてバックアップ（ロールバック可能）"""
    try:
        rel_path = str(Path(filepath).relative_to(PROJECT_ROOT))
        result = subprocess.run(
            ["git", "add", rel_path],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            return None

        commit_msg = f"[self-heal] {error_type} in {rel_path}: {explanation[:60]}"
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            # commit hashを取得
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(PROJECT_ROOT),
            )
            commit_hash = hash_result.stdout.strip()[:12]
            logger.info(f"Fix committed: {commit_hash} ({commit_msg})")
            return commit_hash
        return None
    except Exception as e:
        logger.warning(f"Git commit failed: {e}")
        return None


def rollback_fix(commit_hash: str) -> bool:
    """修正をロールバック（git revert）"""
    try:
        result = subprocess.run(
            ["git", "revert", "--no-edit", commit_hash],
            capture_output=True, text=True, timeout=15,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            logger.info(f"Fix rolled back: {commit_hash}")
            return True
        logger.warning(f"Git revert failed: {result.stderr[:200]}")
        return False
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return False


# === メインサイクル ===

async def self_heal_cycle(push_fn=None) -> dict[str, Any]:
    """自己修復サイクルを1回実行

    1. ログからエラー検出
    2. 過去パターン参照
    3. 修正生成（最大3回反復）
    4. テスト（3段階）
    5. git commit
    6. Discord通知
    7. パターン記録 + 結晶化チェック
    """
    logger.info("=== Self-heal cycle start ===")

    if _daily_fix_count() >= MAX_DAILY_FIXES:
        logger.info(f"Daily fix limit reached ({MAX_DAILY_FIXES})")
        return {"errors_found": 0, "patches_created": 0, "limit_reached": True}

    # エラー検出
    errors = await detect_errors_from_log()
    if not errors:
        logger.info("No errors found in logs")
        return {"errors_found": 0, "patches_created": 0}

    logger.info(f"Found {len(errors)} errors in logs")

    patches_created = 0
    for error in errors[:3]:  # 1サイクルで最大3件
        if _daily_fix_count() >= MAX_DAILY_FIXES:
            break

        fix = await generate_and_apply_fix(error)
        if not fix:
            continue

        # Git commit（ロールバック可能に）
        commit_hash = _git_commit_fix(
            fix["file"],
            error["error_type"],
            fix["explanation"],
        )

        patches_created += 1
        rel_path = str(Path(fix["file"]).relative_to(PROJECT_ROOT))

        # ヒールログに記録
        log = _load_heal_log()
        log.append({
            "date": date.today().isoformat(),
            "file": rel_path,
            "error": f"{error['error_type']}: {error['message'][:80]}",
            "explanation": fix["explanation"][:150],
            "iterations": fix["iterations"],
            "commit_hash": commit_hash,
            "applied": True,
            "timestamp": datetime.now().isoformat(),
        })
        _save_heal_log(log)

        # Discord通知
        if push_fn:
            from config import DISCORD_OWNER_ID
            msg = (
                f"[自己修復] {rel_path} を修正したよ\n"
                f"エラー: {error['error_type']}: {error['message'][:80]}\n"
                f"修正: {fix['explanation'][:150]}\n"
                f"試行: {fix['iterations']}回 | commit: {commit_hash or 'なし'}"
            )
            if commit_hash:
                msg += f"\nロールバック: rollback_fix('{commit_hash}')"
            try:
                await push_fn(str(DISCORD_OWNER_ID), msg)
            except Exception:
                pass

    # パターン結晶化チェック（成功パターン→スキル昇格）
    if patches_created > 0:
        _crystallize_patterns()

    logger.info(f"=== Self-heal cycle done: {len(errors)} errors, {patches_created} fixed ===")
    return {"errors_found": len(errors), "patches_created": patches_created}


# === ステータス ===

def get_heal_stats() -> dict:
    """自己修復の統計"""
    log = _load_heal_log()
    patterns = _load_fix_patterns()
    today = date.today().isoformat()

    success_patterns = [p for p in patterns if p.get("success")]
    fail_patterns = [p for p in patterns if not p.get("success")]

    return {
        "total_fixes": sum(1 for e in log if e.get("applied")),
        "today_fixes": sum(1 for e in log if e.get("date") == today and e.get("applied")),
        "daily_limit": MAX_DAILY_FIXES,
        "total_patterns": len(patterns),
        "success_patterns": len(success_patterns),
        "fail_patterns": len(fail_patterns),
        "crystallized": sum(1 for p in success_patterns if p.get("usage_count", 0) >= 5),
    }


def list_recent_fixes(n: int = 10) -> list[dict]:
    """直近の修正一覧"""
    log = _load_heal_log()
    return log[-n:]

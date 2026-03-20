"""Tool Effect Gate - 4段階承認

全ツール実行はこのゲートを通る。
READ: 自動承認
WRITE: パス検証付き自動承認
ELEVATED: LINE通知
DESTRUCTIVE: LINE承認必須
"""

import json
import logging
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

from security.path_validator import validate_file_access
from security.anomaly_detector import anomaly_detector

logger = logging.getLogger("shiki.security")


class ToolLevel(Enum):
    READ = "read"
    WRITE = "write"
    ELEVATED = "elevated"
    DESTRUCTIVE = "destructive"


# ツールごとのレベル定義（Single Source of Truth — 同期はtools_config.py起動時に自動検証）
TOOL_LEVELS: dict[str, ToolLevel] = {
    # READ — 自動承認
    "take_screenshot": ToolLevel.READ,
    "get_frontmost_app": ToolLevel.READ,
    "get_running_apps": ToolLevel.READ,
    "get_browser_info": ToolLevel.READ,
    "get_window_info": ToolLevel.READ,
    "get_screen_size": ToolLevel.READ,
    "read_file": ToolLevel.READ,
    "list_directory": ToolLevel.READ,
    # WRITE — パス検証付き自動承認
    "type_text": ToolLevel.WRITE,
    "press_key": ToolLevel.WRITE,
    "click": ToolLevel.WRITE,
    "double_click": ToolLevel.WRITE,
    "right_click": ToolLevel.WRITE,
    "scroll": ToolLevel.WRITE,
    "drag": ToolLevel.WRITE,
    "set_volume": ToolLevel.WRITE,
    "toggle_dark_mode": ToolLevel.WRITE,
    "show_notification": ToolLevel.WRITE,
    "write_file": ToolLevel.WRITE,
    # ELEVATED — LINE通知
    "open_app": ToolLevel.ELEVATED,
    "open_url": ToolLevel.ELEVATED,
    "open_url_with_profile": ToolLevel.ELEVATED,
    "run_command": ToolLevel.ELEVATED,
    "move_file": ToolLevel.ELEVATED,
    # リマインダー
    "add_reminder": ToolLevel.WRITE,
    "list_reminders": ToolLevel.READ,
    "delete_reminder": ToolLevel.WRITE,
    # ブラウザ
    "browse_url": ToolLevel.ELEVATED,
    "search_web": ToolLevel.ELEVATED,
    "get_page_text": ToolLevel.READ,
    "get_page_elements": ToolLevel.ELEVATED,
    "interact_page_element": ToolLevel.ELEVATED,
    "get_accessibility_tree": ToolLevel.READ,
    "crop_screenshot": ToolLevel.READ,
    # CodeAct
    "execute_code": ToolLevel.ELEVATED,
    # 計画ツール
    "update_plan": ToolLevel.WRITE,
    # Claude Code委譲
    "delegate_to_claude": ToolLevel.ELEVATED,
    # Cronジョブ
    "schedule_task": ToolLevel.ELEVATED,
    "list_scheduled_tasks": ToolLevel.READ,
    "delete_scheduled_task": ToolLevel.WRITE,
    # マルチエージェント
    "dispatch_agents": ToolLevel.ELEVATED,
    # 動的ツール生成
    "generate_tool": ToolLevel.ELEVATED,
    "list_dynamic_tools": ToolLevel.READ,
    "delete_dynamic_tool": ToolLevel.WRITE,
    # Discord履歴
    "get_discord_history": ToolLevel.READ,
    # Notion連携
    "notion_list_projects": ToolLevel.READ,
    "notion_get_project": ToolLevel.READ,
    "notion_update_project": ToolLevel.ELEVATED,
    "notion_create_project": ToolLevel.ELEVATED,
    "notion_list_tasks": ToolLevel.READ,
    "notion_create_task": ToolLevel.ELEVATED,
    "notion_update_task": ToolLevel.ELEVATED,
    "notion_batch_create_tasks": ToolLevel.ELEVATED,
    "notion_search": ToolLevel.READ,
    "notion_get_page_content": ToolLevel.READ,
    "notion_add_comment": ToolLevel.ELEVATED,
    "notion_list_comments": ToolLevel.READ,
    "notion_update_block": ToolLevel.ELEVATED,
    "notion_append_blocks": ToolLevel.ELEVATED,
    "notion_execute_tasks": ToolLevel.ELEVATED,
    "notion_execute_single_task": ToolLevel.ELEVATED,
    "notion_execution_status": ToolLevel.READ,
    "notion_get_reflections": ToolLevel.READ,
    # 常時指示
    "add_standing_order": ToolLevel.WRITE,
    "list_standing_orders": ToolLevel.READ,
    "remove_standing_order": ToolLevel.WRITE,
    # 自己進化
    "run_self_evolution": ToolLevel.ELEVATED,
    # 収益トラッカー
    "check_revenue": ToolLevel.ELEVATED,
    "get_revenue_summary": ToolLevel.READ,
}


def validate_tool_levels_sync():
    """起動時にTOOL_FUNCTIONSの全ツールがTOOL_LEVELSに定義されているか検証"""
    try:
        from agent.tools_config import TOOL_FUNCTIONS
        missing = set(TOOL_FUNCTIONS.keys()) - set(TOOL_LEVELS.keys())
        if missing:
            logger.warning(
                f"TOOL_LEVELS未定義のツール（DESTRUCTIVEにフォールバック）: {missing}"
            )
    except ImportError:
        pass  # 循環import回避


class ActionLogger:
    """全ツール実行を記録（7日間ローテーション付き）"""

    _MAX_LOG_AGE_DAYS = 7

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._last_rotation_date: str = ""

    def log(
        self,
        tool_name: str,
        level: ToolLevel,
        input_data: dict,
        output_summary: str,
        approved: bool,
        execution_time_ms: int,
    ):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "level": level.value,
            "input": self._redact_sensitive(input_data),
            "output_summary": output_summary[:500],
            "approved": approved,
            "execution_time_ms": execution_time_ms,
        }

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"{today}.jsonl"

        try:
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Action log write failed: {e}")

        # 1日1回ローテーション
        if today != self._last_rotation_date:
            self._last_rotation_date = today
            self._rotate_logs()

    def _rotate_logs(self):
        """古いログファイルを削除（7日以上前）"""
        try:
            cutoff = datetime.now() - __import__("datetime").timedelta(days=self._MAX_LOG_AGE_DAYS)
            for f in self.log_dir.glob("*.jsonl"):
                try:
                    date_str = f.stem  # "2026-03-10"
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")
                    if file_date < cutoff:
                        f.unlink()
                        logger.info(f"Action log rotated: {f.name}")
                except (ValueError, OSError):
                    pass
        except Exception as e:
            logger.error(f"Action log rotation failed: {e}")

    @staticmethod
    def _redact_sensitive(data: dict) -> dict:
        redacted = {}
        sensitive_words = {"token", "key", "secret", "password", "credential"}
        for k, v in data.items():
            if any(word in k.lower() for word in sensitive_words):
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = v
        return redacted


class SecurityGate:
    """ツール実行前の承認チェック + ログ記録"""

    def __init__(self, log_dir: Path):
        self.action_logger = ActionLogger(log_dir)
        self._notify_callback = None  # LINE通知コールバック
        self._approval_callback = None  # LINE承認コールバック

    def set_callbacks(self, notify_fn, approval_fn):
        """LINE通知・承認のコールバックを設定"""
        self._notify_callback = notify_fn
        self._approval_callback = approval_fn

    async def check_permission(
        self, tool_name: str, tool_input: dict
    ) -> tuple[bool, str]:
        """ツール実行前の承認チェック。(approved, reason)を返す"""
        # 異常検知チェック
        if anomaly_detector.should_shutdown:
            return False, "緊急停止中"

        anomaly_detector.record_event("tool_calls_per_minute", tool_name)

        level = TOOL_LEVELS.get(tool_name, ToolLevel.DESTRUCTIVE)

        if level == ToolLevel.READ:
            return True, "auto-approved (READ)"

        if level == ToolLevel.WRITE:
            for path_key in ("path", "src", "dst", "file_path", "filepath"):
                if path_key in tool_input:
                    if not validate_file_access(tool_input[path_key], "write"):
                        return False, f"パスが許可されていません: {tool_input[path_key]}"
            return True, "auto-approved (WRITE, path validated)"

        if level == ToolLevel.ELEVATED:
            if self._notify_callback:
                await self._notify_callback(tool_name, tool_input)
            logger.info(f"ELEVATED tool: {tool_name}")
            return True, "approved (ELEVATED, notified)"

        if level == ToolLevel.DESTRUCTIVE:
            if self._approval_callback:
                approved = await self._approval_callback(tool_name, tool_input)
                if not approved:
                    return False, "ユーザーが拒否"
                return True, "approved (DESTRUCTIVE, user confirmed)"
            return False, "承認コールバック未設定"

        return False, "unknown level"

    async def execute_with_gate(
        self, tool_name: str, tool_input: dict, execute_fn
    ) -> dict:
        """ゲートチェック → 実行 → ログ記録"""
        level = TOOL_LEVELS.get(tool_name, ToolLevel.DESTRUCTIVE)

        # 承認チェック
        approved, reason = await self.check_permission(tool_name, tool_input)

        if not approved:
            self.action_logger.log(
                tool_name, level, tool_input, reason, False, 0
            )
            return {"error": reason, "blocked": True}

        # 実行
        start = time.monotonic()
        try:
            result = await execute_fn()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self.action_logger.log(
                tool_name, level, tool_input,
                str(result)[:500], True, elapsed_ms
            )
            return result
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            anomaly_detector.record_event("failed_tool_calls", str(e))
            self.action_logger.log(
                tool_name, level, tool_input,
                f"ERROR: {e}", True, elapsed_ms
            )
            return {"error": str(e)}

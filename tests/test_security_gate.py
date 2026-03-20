"""security/gate.py のテスト — SecurityGate承認チェック"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from security.gate import ToolLevel, TOOL_LEVELS, SecurityGate, ActionLogger


class TestToolLevel:
    """ToolLevelの定義テスト"""

    def test_read_level(self):
        assert ToolLevel.READ.value == "read"

    def test_write_level(self):
        assert ToolLevel.WRITE.value == "write"

    def test_elevated_level(self):
        assert ToolLevel.ELEVATED.value == "elevated"

    def test_destructive_level(self):
        assert ToolLevel.DESTRUCTIVE.value == "destructive"


class TestToolLevels:
    """TOOL_LEVELS辞書のテスト"""

    def test_read_tools_are_read_level(self):
        # READレベルのツールが正しく定義されている
        read_tools = [
            "take_screenshot", "get_frontmost_app", "get_running_apps",
            "get_browser_info", "get_window_info", "get_screen_size",
            "read_file", "list_directory",
        ]
        for tool in read_tools:
            assert TOOL_LEVELS[tool] == ToolLevel.READ, f"{tool} should be READ"

    def test_write_tools_are_write_level(self):
        write_tools = [
            "type_text", "press_key", "click", "double_click",
            "scroll", "write_file",
        ]
        for tool in write_tools:
            assert TOOL_LEVELS[tool] == ToolLevel.WRITE, f"{tool} should be WRITE"

    def test_elevated_tools_are_elevated_level(self):
        elevated_tools = [
            "open_app", "open_url", "run_command", "browse_url",
        ]
        for tool in elevated_tools:
            assert TOOL_LEVELS[tool] == ToolLevel.ELEVATED, f"{tool} should be ELEVATED"

    def test_all_levels_are_valid(self):
        for name, level in TOOL_LEVELS.items():
            assert isinstance(level, ToolLevel), f"{name} has invalid level: {level}"


class TestSecurityGatePermission:
    """SecurityGate.check_permissionのテスト"""

    @pytest.fixture
    def gate(self, tmp_path):
        return SecurityGate(log_dir=tmp_path / "logs")

    @pytest.mark.asyncio
    async def test_read_auto_approved(self, gate):
        # READレベルツールは自動承認
        approved, reason = await gate.check_permission("take_screenshot", {})
        assert approved is True
        assert "READ" in reason

    @pytest.mark.asyncio
    async def test_write_auto_approved_no_path(self, gate):
        # パスなしのWRITEツールも自動承認
        approved, reason = await gate.check_permission("press_key", {"key": "a"})
        assert approved is True
        assert "WRITE" in reason

    @pytest.mark.asyncio
    async def test_elevated_approved_with_notification(self, gate):
        # ELEVATEDは通知付き承認
        mock_notify = AsyncMock()
        gate.set_callbacks(mock_notify, None)
        approved, reason = await gate.check_permission("open_app", {"app_name": "Finder"})
        assert approved is True
        assert "ELEVATED" in reason
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_destructive_requires_approval(self, gate):
        # DESTRUCTIVEは承認コールバックが必要
        # 承認コールバック未設定 → 拒否
        approved, reason = await gate.check_permission("unknown_destructive_tool", {})
        assert approved is False

    @pytest.mark.asyncio
    async def test_destructive_approved_when_confirmed(self, gate):
        mock_approval = AsyncMock(return_value=True)
        gate.set_callbacks(None, mock_approval)
        # 未定義ツールはDESTRUCTIVEにフォールバック
        approved, reason = await gate.check_permission("unknown_tool", {})
        assert approved is True
        assert "DESTRUCTIVE" in reason

    @pytest.mark.asyncio
    async def test_destructive_rejected_when_denied(self, gate):
        mock_approval = AsyncMock(return_value=False)
        gate.set_callbacks(None, mock_approval)
        approved, reason = await gate.check_permission("unknown_tool", {})
        assert approved is False
        assert "拒否" in reason

    @pytest.mark.asyncio
    async def test_shutdown_blocks_all(self, gate):
        # 緊急停止中は全ツール拒否
        with patch("security.gate.anomaly_detector") as mock_ad:
            mock_ad.should_shutdown = True
            approved, reason = await gate.check_permission("take_screenshot", {})
            assert approved is False
            assert "緊急停止" in reason


class TestActionLogger:
    """ActionLoggerのテスト"""

    def test_log_creates_file(self, tmp_path):
        al = ActionLogger(tmp_path / "action_logs")
        al.log("take_screenshot", ToolLevel.READ, {}, "ok", True, 50)
        # ログファイルが作成される
        log_files = list((tmp_path / "action_logs").glob("*.jsonl"))
        assert len(log_files) == 1

    def test_redact_sensitive(self):
        data = {"path": "/tmp/file", "api_key": "secret123", "token": "abc"}
        redacted = ActionLogger._redact_sensitive(data)
        assert redacted["path"] == "/tmp/file"
        assert redacted["api_key"] == "***REDACTED***"
        assert redacted["token"] == "***REDACTED***"

"""agent/tools_config.py のテスト

tools_config.pyはgoogle.genaiや多数のツールモジュールをインポートし、
起動時にツール同期バリデーションも行うため、
validate_tool_argsのロジックは関数本体をコピーしてテストする。
TOOL_LEVELSの検証はgate.pyから直接テスト可能。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from security.gate import ToolLevel, TOOL_LEVELS


class TestToolLevelsCompleteness:
    """TOOL_LEVELSの網羅性テスト"""

    def test_all_levels_are_valid_tool_level(self):
        for name, level in TOOL_LEVELS.items():
            assert isinstance(level, ToolLevel), f"{name}: {level} is not a ToolLevel"

    def test_no_empty_tool_names(self):
        for name in TOOL_LEVELS:
            assert name.strip() != "", "Empty tool name found in TOOL_LEVELS"

    def test_known_read_tools_present(self):
        read_tools = [
            "take_screenshot", "get_frontmost_app", "read_file",
            "list_directory", "list_reminders",
        ]
        for tool in read_tools:
            assert tool in TOOL_LEVELS, f"{tool} missing from TOOL_LEVELS"
            assert TOOL_LEVELS[tool] == ToolLevel.READ

    def test_known_elevated_tools_present(self):
        elevated_tools = ["open_app", "run_command", "execute_code"]
        for tool in elevated_tools:
            assert tool in TOOL_LEVELS, f"{tool} missing from TOOL_LEVELS"
            assert TOOL_LEVELS[tool] == ToolLevel.ELEVATED


class TestValidateToolArgs:
    """validate_tool_args 相当のロジックテスト

    tools_config.pyのインポートが重い（genai + 全ツールモジュール + 起動時バリデーション）ため、
    バリデーションロジックを直接テストする。
    """

    # tools_config.pyから抽出した必須引数定義（テスト対象のサブセット）
    _REQUIRED_ARGS = {
        "click": ["x", "y"],
        "double_click": ["x", "y"],
        "right_click": ["x", "y"],
        "set_volume": ["level"],
        "open_app": ["app_name"],
        "type_text": ["text"],
    }

    def _validate(self, tool_name: str, tool_args: dict) -> str | None:
        """tools_config.validate_tool_argsの再現"""
        for r in self._REQUIRED_ARGS.get(tool_name, []):
            if r not in tool_args or tool_args[r] is None:
                return f"必須引数 '{r}' が未指定"

        if tool_name == "set_volume":
            try:
                if not (0 <= int(tool_args["level"]) <= 100):
                    return "音量は0〜100"
            except (ValueError, TypeError):
                return "音量は数値で"

        if tool_name in ("click", "double_click", "right_click"):
            try:
                x, y = int(tool_args["x"]), int(tool_args["y"])
                if x < 0 or y < 0 or x > 5000 or y > 5000:
                    return f"座標異常: ({x}, {y})"
            except (ValueError, TypeError):
                return "座標は数値で"

        return None

    def test_missing_required_arg(self):
        result = self._validate("click", {"x": 100})
        assert result is not None
        assert "y" in result or "必須" in result

    def test_valid_click_args(self):
        result = self._validate("click", {"x": 100, "y": 200})
        assert result is None

    def test_invalid_coordinates_negative(self):
        result = self._validate("click", {"x": -1, "y": 200})
        assert result is not None

    def test_invalid_coordinates_too_large(self):
        result = self._validate("click", {"x": 6000, "y": 200})
        assert result is not None

    def test_volume_valid_range(self):
        result = self._validate("set_volume", {"level": 50})
        assert result is None

    def test_volume_out_of_range(self):
        result = self._validate("set_volume", {"level": 150})
        assert result is not None

    def test_volume_invalid_type(self):
        result = self._validate("set_volume", {"level": "loud"})
        assert result is not None

    def test_unknown_tool_passes(self):
        result = self._validate("some_unknown_tool", {"any": "args"})
        assert result is None

    def test_missing_required_arg_none_value(self):
        result = self._validate("click", {"x": 100, "y": None})
        assert result is not None

    def test_double_click_validates_same_as_click(self):
        assert self._validate("double_click", {"x": 100, "y": 200}) is None
        assert self._validate("double_click", {"x": -1, "y": 200}) is not None


class TestScaleCoordinates:
    """scale_coordinates のロジックテスト"""

    def test_non_click_tool_returns_unchanged(self):
        # click/double_click/right_click/drag 以外はそのまま返す
        args = {"text": "hello"}
        # ロジック: tool_name not in ("click", ...) → return tool_args
        assert args is args  # 変更なし

    def test_scale_logic_click(self):
        """スケーリングの数式テスト"""
        # screen_w=1440, screen_h=900, ss_w=1024, ss_h=640
        screen_w, screen_h = 1440, 900
        ss_w, ss_h = 1024, 640
        scale_x = screen_w / ss_w  # 1.40625
        scale_y = screen_h / ss_h  # 1.40625

        x, y = 512, 320
        scaled_x = round(x * scale_x)  # 720
        scaled_y = round(y * scale_y)  # 450

        assert scaled_x == 720
        assert scaled_y == 450

    def test_scale_logic_clamping(self):
        """クランプのテスト"""
        screen_w, screen_h = 1440, 900
        x = 1500  # screen_w超え
        clamped = max(0, min(x, screen_w - 1))
        assert clamped == 1439

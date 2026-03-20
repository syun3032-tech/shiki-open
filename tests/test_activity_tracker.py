"""agent/activity_tracker.py のテスト"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def activity_dir(tmp_path):
    """テスト用のアクティビティディレクトリ"""
    d = tmp_path / "activity"
    d.mkdir()
    return d


@pytest.fixture
def tracker(activity_dir, tmp_path):
    """パッチ済みのトラッカーモジュール"""
    log_file = activity_dir / "current_raw.jsonl"
    summary_file = activity_dir / "summaries.jsonl"
    profile_file = activity_dir / "profile.json"

    with patch("agent.activity_tracker.ACTIVITY_DIR", activity_dir), \
         patch("agent.activity_tracker.ACTIVITY_LOG_FILE", log_file), \
         patch("agent.activity_tracker.ACTIVITY_SUMMARY_FILE", summary_file), \
         patch("agent.activity_tracker.ACTIVITY_PROFILE_FILE", profile_file):
        import agent.activity_tracker as at
        yield at


class TestAppendAndLoadRaw:
    """_append_raw / _load_raw のラウンドトリップ"""

    def test_roundtrip(self, tracker):
        tracker._append_raw("10:00:00", "Cursor", "main.py")
        tracker._append_raw("10:00:05", "Chrome", "Google")
        entries = tracker._load_raw()
        assert len(entries) == 2
        assert entries[0]["app"] == "Cursor"
        assert entries[1]["app"] == "Chrome"

    def test_with_url(self, tracker):
        tracker._append_raw("10:00:00", "Chrome", "GitHub", url="https://github.com")
        entries = tracker._load_raw()
        assert entries[0]["url"] == "https://github.com"

    def test_load_empty(self, tracker):
        entries = tracker._load_raw()
        assert entries == []


class TestClearRaw:
    """_clear_raw のテスト"""

    def test_clear(self, tracker):
        tracker._append_raw("10:00:00", "Cursor", "test.py")
        assert len(tracker._load_raw()) == 1
        tracker._clear_raw()
        assert len(tracker._load_raw()) == 0


class TestFallbackSummary:
    """_fallback_summary のテスト（重複除去）"""

    def test_deduplicates(self, tracker):
        entries = [
            {"t": "10:00:00", "app": "Cursor", "title": "main.py"},
            {"t": "10:00:05", "app": "Cursor", "title": "main.py"},
            {"t": "10:00:10", "app": "Chrome", "title": "Google"},
            {"t": "10:00:15", "app": "Cursor", "title": "main.py"},
        ]
        result = tracker._fallback_summary(entries)
        lines = result.strip().split("\n")
        # Cursor|main.py と Chrome|Google の2つにまとめられる
        assert len(lines) == 2

    def test_empty_returns_empty(self, tracker):
        assert tracker._fallback_summary([]) == ""

    def test_limits_to_20_lines(self, tracker):
        entries = [
            {"t": f"10:00:{i:02d}", "app": f"App{i}", "title": f"Title{i}"}
            for i in range(30)
        ]
        result = tracker._fallback_summary(entries)
        lines = result.strip().split("\n")
        assert len(lines) <= 20


class TestUpdateProfile:
    """_update_profile のテスト"""

    def test_accumulates_app_usage(self, tracker):
        entries = [
            {"t": "10:00:00", "app": "Cursor", "title": "test.py"},
            {"t": "10:00:05", "app": "Cursor", "title": "main.py"},
            {"t": "10:00:10", "app": "Chrome", "title": "Google"},
        ]
        tracker._update_profile(entries)
        profile = tracker._load_profile()
        assert profile["app_usage"]["Cursor"] == 2
        assert profile["app_usage"]["Chrome"] == 1
        assert profile["total_observations"] == 3

    def test_accumulates_across_calls(self, tracker):
        entries1 = [{"t": "10:00:00", "app": "Cursor", "title": "a.py"}]
        entries2 = [{"t": "11:00:00", "app": "Cursor", "title": "b.py"}]
        tracker._update_profile(entries1)
        tracker._update_profile(entries2)
        profile = tracker._load_profile()
        assert profile["app_usage"]["Cursor"] == 2
        assert profile["total_observations"] == 2

    def test_tracks_urls(self, tracker):
        entries = [
            {"t": "10:00:00", "app": "Chrome", "title": "GitHub", "url": "https://github.com/test"},
        ]
        tracker._update_profile(entries)
        profile = tracker._load_profile()
        assert "github.com" in profile["frequent_sites"]


class TestGetRecentActivity:
    """get_recent_activity のテスト"""

    def test_returns_formatted_string(self, tracker):
        tracker._append_raw("10:00:00", "Cursor", "main.py")
        result = tracker.get_recent_activity()
        assert isinstance(result, str)
        assert "Cursor" in result

    def test_empty_returns_empty(self, tracker):
        result = tracker.get_recent_activity()
        assert result == ""


class TestGetUserProfileSummary:
    """get_user_profile_summary のテスト"""

    def test_returns_formatted_string(self, tracker):
        entries = [
            {"t": "10:00:00", "app": "Cursor", "title": "test.py"},
            {"t": "10:05:00", "app": "Chrome", "title": "Stack Overflow"},
        ]
        tracker._update_profile(entries)
        result = tracker.get_user_profile_summary()
        assert isinstance(result, str)
        assert "Cursor" in result

    def test_empty_profile_returns_empty(self, tracker):
        result = tracker.get_user_profile_summary()
        assert result == ""

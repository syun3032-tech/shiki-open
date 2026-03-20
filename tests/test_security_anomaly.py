"""security/anomaly_detector.py のテスト"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from security.anomaly_detector import AnomalyDetector, THRESHOLDS


@pytest.fixture
def detector():
    """各テストで新しいAnomalyDetectorを使う"""
    return AnomalyDetector()


class TestRecordEvent:
    """record_eventのテスト"""

    def test_normal_event_no_alert(self, detector):
        # 閾値未満のイベントはアラートなし
        result = detector.record_event("tool_calls_per_minute", "click")
        assert result is None

    def test_exceeding_threshold_triggers_alert(self, detector):
        # 閾値に達するとアラートを返す
        threshold = THRESHOLDS["failed_tool_calls"]
        for i in range(threshold - 1):
            result = detector.record_event("failed_tool_calls", f"error_{i}")
            assert result is None

        # 閾値到達
        with patch.object(detector, "_persist_alert"):
            result = detector.record_event("failed_tool_calls", "final_error")
        assert result is not None
        assert result["type"] == "failed_tool_calls"
        assert result["count"] >= threshold

    def test_injection_attempt_triggers_critical(self, detector):
        # injection_attempts は1回で CRITICAL
        assert THRESHOLDS["injection_attempts"] == 1
        with patch.object(detector, "_persist_alert"):
            result = detector.record_event("injection_attempts", "prompt injection")
        assert result is not None
        assert result["severity"] == "CRITICAL"

    def test_critical_alert_sets_shutdown(self, detector):
        with patch.object(detector, "_persist_alert"):
            detector.record_event("injection_attempts", "attack")
        assert detector.should_shutdown is True

    def test_non_critical_alert_no_shutdown(self, detector):
        # tool_calls_per_minute はHIGH severity → shutdown しない
        threshold = THRESHOLDS["tool_calls_per_minute"]
        with patch.object(detector, "_persist_alert"):
            for i in range(threshold):
                detector.record_event("tool_calls_per_minute", f"call_{i}")
        assert detector.should_shutdown is False


class TestShouldShutdown:
    """should_shutdownプロパティのテスト"""

    def test_initial_state_false(self, detector):
        assert detector.should_shutdown is False

    def test_after_critical_event(self, detector):
        with patch.object(detector, "_persist_alert"):
            detector.record_event("injection_attempts", "test")
        assert detector.should_shutdown is True


class TestResetShutdown:
    """reset_shutdownのテスト"""

    def test_reset_clears_shutdown(self, detector):
        detector._shutdown_requested = True
        detector._events["test"].append(time.monotonic())
        detector.reset_shutdown()
        assert detector.should_shutdown is False
        assert len(detector._events) == 0

    def test_reset_clears_events(self, detector):
        detector.record_event("tool_calls_per_minute", "click")
        detector.record_event("tool_calls_per_minute", "type")
        detector.reset_shutdown()
        assert len(detector._events) == 0


class TestGetStats:
    """get_statsのテスト"""

    def test_returns_correct_structure(self, detector):
        stats = detector.get_stats()
        assert "shutdown" in stats
        assert "counters" in stats
        assert "window_seconds" in stats
        assert "total_alerts" in stats

    def test_initial_stats(self, detector):
        stats = detector.get_stats()
        assert stats["shutdown"] is False
        assert stats["total_alerts"] == 0
        assert stats["window_seconds"] == 60

    def test_stats_after_events(self, detector):
        detector.record_event("tool_calls_per_minute", "click")
        detector.record_event("tool_calls_per_minute", "type")
        stats = detector.get_stats()
        assert stats["counters"]["tool_calls_per_minute"] == 2

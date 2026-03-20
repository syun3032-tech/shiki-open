"""リアルタイム異常検知 - OWASP AI Agent Security基準

閾値を超えたら即座にAgent Loopを停止。
アラート履歴は永続化し、インシデント追跡に使用。

スライディングウィンドウ方式: 直近60秒間のイベント数でカウント。
固定1分窓だとタイミング回避ができたが、スライディングで防止。
"""

import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("shiki.security")

THRESHOLDS = {
    "tool_calls_per_minute": 30,
    "failed_tool_calls": 5,
    "injection_attempts": 1,
    "sensitive_data_access": 3,
    "unique_files_accessed": 20,
    "outbound_requests": 10,
}

# スライディングウィンドウのサイズ（秒）
_WINDOW_SECONDS = 60

_ALERTS_FILE = Path(__file__).parent.parent / "logs" / "security_alerts.jsonl"


class AnomalyDetector:
    def __init__(self):
        # スライディングウィンドウ: イベントタイプ → タイムスタンプのdeque
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self.alerts: list[dict] = []
        self._shutdown_requested = False

    @property
    def should_shutdown(self) -> bool:
        return self._shutdown_requested

    def _prune_old_events(self, event_type: str):
        """ウィンドウ外の古いイベントを除去"""
        cutoff = time.monotonic() - _WINDOW_SECONDS
        q = self._events[event_type]
        while q and q[0] < cutoff:
            q.popleft()

    def record_event(self, event_type: str, details: str = "") -> dict | None:
        """イベント記録 + 閾値チェック。アラートがあればdictを返す"""
        now = time.monotonic()
        self._events[event_type].append(now)
        self._prune_old_events(event_type)

        count = len(self._events[event_type])
        threshold = THRESHOLDS.get(event_type)
        if threshold and count >= threshold:
            alert = {
                "type": event_type,
                "count": count,
                "threshold": threshold,
                "details": details[:500],
                "timestamp": datetime.now().isoformat(),
                "severity": "CRITICAL" if event_type == "injection_attempts" else "HIGH",
            }
            self.alerts.append(alert)
            logger.critical(f"ANOMALY DETECTED: {alert}")

            # アラートを永続化
            self._persist_alert(alert)

            if alert["severity"] == "CRITICAL":
                self._shutdown_requested = True

            return alert
        return None

    def _persist_alert(self, alert: dict):
        """アラートをファイルに追記（インシデント追跡用）"""
        try:
            _ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_ALERTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Alert persistence failed: {e}")

    def reset_shutdown(self):
        """緊急停止フラグをリセット（オーナー確認後）"""
        self._shutdown_requested = False
        self._events.clear()
        logger.info("Anomaly detector reset by owner")

    def get_stats(self) -> dict:
        """現在の状態を返す（ヘルスチェック用）"""
        # 各イベントタイプの直近60秒カウントを計算
        counts = {}
        for event_type in self._events:
            self._prune_old_events(event_type)
            counts[event_type] = len(self._events[event_type])
        return {
            "shutdown": self._shutdown_requested,
            "counters": counts,
            "window_seconds": _WINDOW_SECONDS,
            "total_alerts": len(self.alerts),
        }


# グローバルインスタンス
anomaly_detector = AnomalyDetector()

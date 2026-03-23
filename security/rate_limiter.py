"""レートリミッター

LINE Webhookのメッセージ頻度を制限。
異常な連続メッセージ（bot attack, replay等）を検知・ブロック。
"""

import time
import logging
from collections import defaultdict

logger = logging.getLogger("shiki.security")


class RateLimiter:
    """スライディングウィンドウ方式のレートリミッター"""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """リクエストを許可するかチェック"""
        now = time.time()
        cutoff = now - self.window_seconds

        # 古いタイムスタンプを除去
        self._timestamps[key] = [
            ts for ts in self._timestamps[key] if ts > cutoff
        ]

        if len(self._timestamps[key]) >= self.max_requests:
            logger.warning(
                f"Rate limit exceeded: {key} "
                f"({len(self._timestamps[key])}/{self.max_requests} in {self.window_seconds}s)"
            )
            return False

        self._timestamps[key].append(now)
        return True

    def get_remaining(self, key: str) -> int:
        """残りリクエスト数"""
        now = time.time()
        cutoff = now - self.window_seconds
        current = sum(1 for ts in self._timestamps.get(key, []) if ts > cutoff)
        return max(0, self.max_requests - current)


# メッセージレートリミッター（120メッセージ/分）
message_limiter = RateLimiter(max_requests=120, window_seconds=60)

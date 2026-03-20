"""環境変数フィルタ - Credential隔離

OpenClawのpickSafeEnvを更に厳格化。
ホワイトリスト方式: 明示的に許可したものだけ。
"""

import os

# Agentに渡してよい環境変数（ホワイトリスト）
SAFE_ENV_KEYS = frozenset({
    "HOME", "PATH", "LANG", "SHELL", "USER", "TMPDIR",
    "TERM", "LOGNAME", "PWD",
})


def get_safe_env() -> dict[str, str]:
    """Agentに渡す環境変数。APIキーは絶対に含まない"""
    return {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}

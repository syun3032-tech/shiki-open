"""識（しき）- 環境変数管理

全設定を一元管理。APIキーはここでのみ読み込み、
他のモジュールには絶対に渡さない。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# .envファイルを読み込み（UTF-16等の異常エンコーディングに対応）
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    try:
        _env_content = _env_path.read_bytes()
        # BOM付きUTF-16を検出してUTF-8に変換
        if _env_content[:2] in (b'\xff\xfe', b'\xfe\xff'):
            _env_path.write_text(
                _env_content.decode("utf-16").replace("\r\n", "\n"),
                encoding="utf-8",
            )
    except Exception:
        pass
load_dotenv(_env_path)

# === プロジェクトパス ===
PROJECT_ROOT = Path(__file__).parent
RITSU_DIR = PROJECT_ROOT / ".ritsu"
SOUL_PATH = RITSU_DIR / "SOUL.md"
MEMORY_PATH = RITSU_DIR / "MEMORY.md"
TOPICS_DIR = RITSU_DIR / "topics"
DAILY_DIR = RITSU_DIR / "daily"
SESSIONS_DIR = RITSU_DIR / "sessions"
LOG_DIR = PROJECT_ROOT / "logs"
STATIC_DIR = PROJECT_ROOT / "static" / "images"

# === API Keys (このモジュール外に漏らさない) ===
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY_BACKUP: str = os.environ.get("GEMINI_API_KEY_BACKUP", "")
LINE_CHANNEL_SECRET: str = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN: str = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# === オーナー設定 ===
OWNER_LINE_USER_ID: str = os.environ.get("OWNER_LINE_USER_ID", "")

# === Discord設定 ===
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
_discord_ids_raw = os.environ.get("DISCORD_OWNER_ID", "")
DISCORD_OWNER_IDS: set[int] = {
    int(x.strip()) for x in _discord_ids_raw.split(",")
    if x.strip().isdigit()
}
# 後方互換
DISCORD_OWNER_ID: int = next(iter(DISCORD_OWNER_IDS), 0)

# === サーバー設定 ===
HOST: str = os.environ.get("HOST", "127.0.0.1")
PORT: int = int(os.environ.get("PORT", "8000"))

# === 安全制限 ===
MAX_ITERATIONS: int = 50
MAX_TOKENS_PER_TASK: int = 100_000
MAX_COST_PER_DAY: float = 5.0
TOOL_TIMEOUT: int = 30
RATE_LIMIT: str = "60/minute"

# === Gemini設定 ===
GEMINI_MODEL: str = "gemini-2.5-pro"

# === LLMプロバイダー設定 ===
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "gemini")  # gemini, openai, anthropic, ollama

# OpenAI
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Anthropic
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Ollama
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "llama3.1")

# === Notion設定 ===
NOTION_API_KEY: str = os.environ.get("NOTION_API_KEY", "")

# === Google Calendar設定 ===
GOOGLE_CALENDAR_ID: str = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# === 起動時バリデーション ===
def validate_config(mode: str = "all") -> list[str]:
    """必須設定が揃っているか確認

    Args:
        mode: "all" = 全チェック, "line" = LINE必須, "discord" = Discord必須
    """
    issues = []
    # 常に必須
    if not GEMINI_API_KEY:
        issues.append("GEMINI_API_KEY が未設定")

    # LINE関連（LINEモードで必須）
    if mode in ("all", "line"):
        if not LINE_CHANNEL_SECRET:
            issues.append("LINE_CHANNEL_SECRET が未設定")
        if not LINE_CHANNEL_ACCESS_TOKEN:
            issues.append("LINE_CHANNEL_ACCESS_TOKEN が未設定")
        if not OWNER_LINE_USER_ID:
            issues.append("OWNER_LINE_USER_ID が未設定")

    # Discord関連（Discordモードで必須）
    if mode in ("all", "discord"):
        if not DISCORD_BOT_TOKEN:
            issues.append("DISCORD_BOT_TOKEN が未設定")
        if not DISCORD_OWNER_IDS:
            issues.append("DISCORD_OWNER_ID が未設定（カンマ区切りの数値ID）")

    return issues

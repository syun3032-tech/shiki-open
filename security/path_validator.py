"""ファイルパス検証 - アクセス制御

ホワイトリスト + ブラックリストの二重防御。
"""

import tempfile
from pathlib import Path

# 絶対にアクセスさせないパス
BLOCKED_PATHS = frozenset({
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path.home() / ".aws",
    Path.home() / ".config" / "gcloud",
    Path.home() / ".env",
    Path.home() / ".netrc",
    Path.home() / "Library" / "Keychains",
})

# 読み取り許可パス
_PROJECT_ROOT = Path(__file__).parent.parent

ALLOWED_READ_PATHS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Projects",
    Path.home() / "Dev",
    Path.home() / "Code",
    Path.home() / "src",
    Path(tempfile.gettempdir()) / "shiki",
    Path(tempfile.gettempdir()),
    _PROJECT_ROOT,
]

# 書き込み許可パス（読み取りより狭い）
ALLOWED_WRITE_PATHS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Projects",
    Path.home() / "Dev",
    Path(tempfile.gettempdir()) / "shiki",
    Path(tempfile.gettempdir()),
    _PROJECT_ROOT / ".ritsu",
    _PROJECT_ROOT / "static" / "images",
    _PROJECT_ROOT / "logs",
]

# ファイル名ブラックリスト
BLOCKED_FILENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials.json", "secrets.json", "service-account.json",
    "id_rsa", "id_ed25519", ".netrc",
})


def validate_file_access(path: str, operation: str = "read") -> bool:
    """ファイルアクセスの検証"""
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False

    # ブロックリストチェック（最優先）
    for blocked in BLOCKED_PATHS:
        if resolved == blocked or resolved.is_relative_to(blocked):
            return False

    # ファイル名ブラックリスト
    if resolved.name in BLOCKED_FILENAMES:
        return False

    # シンボリックリンク検出: 明示的にis_symlink()で検出（TOCTOU対策）
    try:
        target = Path(path)
        # パスの各コンポーネントにシンボリックリンクがないかチェック
        for parent in list(target.parents) + [target]:
            if parent.is_symlink():
                # シンボリックリンク先が安全か検証
                link_target = parent.resolve()
                for blocked in BLOCKED_PATHS:
                    if link_target == blocked or link_target.is_relative_to(blocked):
                        return False
    except (OSError, ValueError):
        return False  # エラー時は安全側に倒す

    # 許可リストチェック
    allowed = ALLOWED_READ_PATHS if operation == "read" else ALLOWED_WRITE_PATHS
    return any(resolved.is_relative_to(a) for a in allowed)

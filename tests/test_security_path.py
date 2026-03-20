"""security/path_validator.py のテスト"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from security.path_validator import (
    validate_file_access,
    BLOCKED_PATHS,
    BLOCKED_FILENAMES,
    ALLOWED_READ_PATHS,
    ALLOWED_WRITE_PATHS,
    _PROJECT_ROOT,
)


class TestAllowedPaths:
    """許可パスの定義テスト"""

    def test_project_root_in_allowed_read(self):
        # プロジェクトルートは読み取り許可に含まれる
        assert _PROJECT_ROOT in ALLOWED_READ_PATHS

    def test_desktop_in_allowed_read(self):
        assert Path.home() / "Desktop" in ALLOWED_READ_PATHS

    def test_documents_in_allowed_read(self):
        assert Path.home() / "Documents" in ALLOWED_READ_PATHS

    def test_downloads_in_allowed_read(self):
        assert Path.home() / "Downloads" in ALLOWED_READ_PATHS


class TestValidateFileAccess:
    """validate_file_access関数のテスト"""

    def test_allowed_read_desktop(self):
        path = str(Path.home() / "Desktop" / "test.txt")
        assert validate_file_access(path, "read") is True

    def test_allowed_read_documents(self):
        path = str(Path.home() / "Documents" / "report.pdf")
        assert validate_file_access(path, "read") is True

    def test_allowed_read_downloads(self):
        path = str(Path.home() / "Downloads" / "file.zip")
        assert validate_file_access(path, "read") is True

    def test_blocked_env_file(self):
        # .envファイルはブロックされる
        path = str(Path.home() / "Desktop" / ".env")
        assert validate_file_access(path, "read") is False

    def test_blocked_credentials_json(self):
        path = str(Path.home() / "Desktop" / "credentials.json")
        assert validate_file_access(path, "read") is False

    def test_blocked_ssh_dir(self):
        path = str(Path.home() / ".ssh" / "id_rsa")
        assert validate_file_access(path, "read") is False

    def test_blocked_aws_dir(self):
        path = str(Path.home() / ".aws" / "credentials")
        assert validate_file_access(path, "read") is False

    def test_etc_passwd_blocked(self):
        # /etc/passwdは許可リストに含まれない
        assert validate_file_access("/etc/passwd", "read") is False

    def test_path_traversal_blocked(self):
        # パストラバーサルは許可リスト外に解決される
        path = str(Path.home() / "Desktop" / ".." / ".." / "etc" / "passwd")
        assert validate_file_access(path, "read") is False

    def test_project_root_readable(self):
        path = str(_PROJECT_ROOT / "config.py")
        assert validate_file_access(path, "read") is True

    def test_write_to_ritsu_dir(self):
        path = str(_PROJECT_ROOT / ".ritsu" / "test.md")
        assert validate_file_access(path, "write") is True

    def test_write_outside_allowed_blocked(self):
        # 許可外パスへの書き込みは拒否
        assert validate_file_access("/usr/local/test.txt", "write") is False


class TestBlockedFilenames:
    """ファイル名ブラックリストのテスト"""

    def test_env_blocked(self):
        assert ".env" in BLOCKED_FILENAMES

    def test_credentials_blocked(self):
        assert "credentials.json" in BLOCKED_FILENAMES

    def test_id_rsa_blocked(self):
        assert "id_rsa" in BLOCKED_FILENAMES

    def test_netrc_blocked(self):
        assert ".netrc" in BLOCKED_FILENAMES

    def test_service_account_blocked(self):
        assert "service-account.json" in BLOCKED_FILENAMES

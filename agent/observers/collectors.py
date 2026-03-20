"""Tier 1 データコレクター — 11種のデータソースからAPIコスト$0で収集

各コレクターは独立しており、1つが失敗しても他に影響しない。
スタガード実行: 全部を毎サイクル呼ぶわけではなく、重いものは間引く。

セキュリティ:
- 機密アプリ/タイトル/URLはフィルタ済み前提（呼び出し側で判定）
- クリップボードはパスワード等の機密パターンをスキップ
- URLクエリパラメータは除去済み前提
"""

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("shiki.observer.collectors")


# =============================================================================
# ObservationSnapshot: 1サイクルの全データ
# =============================================================================

@dataclass
class ObservationSnapshot:
    """1回の観察で取得した全データ"""
    timestamp: str = ""
    hour: int = 0

    # Core（毎サイクル）
    frontmost_app: str = ""
    window_title: str = ""
    content_hash: str = ""        # hash(app+title) 変化検知用
    cpu_load: float = 0.0

    # 起動中アプリ（10秒ごと）
    running_apps: list[str] = field(default_factory=list)

    # ブラウザ（ブラウザ前面時 or 30秒ごと）
    browser_url: str = ""
    browser_title: str = ""
    all_tabs: list[dict] = field(default_factory=list)

    # エディタ（エディタ前面時）
    editor_file: str = ""
    editor_project: str = ""
    editor_language: str = ""
    git_branch: str = ""

    # ターミナル（ターミナル前面時）
    terminal_cwd: str = ""
    terminal_command: str = ""

    # クリップボード（10秒ごと）
    clipboard_changed: bool = False
    clipboard_preview: str = ""

    # 環境（30-60秒ごと）
    meeting_likely: bool = False
    meeting_app: str = ""
    display_count: int = 1
    recent_files: list[str] = field(default_factory=list)

    # 判定済み
    is_sensitive: bool = False
    activity_category: str = ""

    def to_event_dict(self) -> dict:
        """イベントログ用の辞書（変化があった項目だけ）"""
        d = {
            "ts": self.timestamp,
            "app": self.frontmost_app,
            "cat": self.activity_category,
        }
        if self.browser_url:
            d["url"] = self.browser_url[:120]
        if self.window_title:
            d["title"] = self.window_title[:100]
        if self.editor_file:
            d["file"] = self.editor_file
        if self.editor_project:
            d["project"] = self.editor_project
        if self.terminal_cwd:
            d["cwd"] = self.terminal_cwd
        if self.clipboard_changed:
            d["clipboard"] = self.clipboard_preview[:100]
        if self.meeting_likely:
            d["meeting"] = self.meeting_app
        if self.git_branch:
            d["branch"] = self.git_branch
        return d

    def to_log_line(self) -> str:
        """日次ログ用の1行テキスト"""
        parts = [self.timestamp[:19], self.frontmost_app, f"[{self.activity_category}]"]
        if self.browser_url:
            parts.append(self.browser_url[:80])
        elif self.editor_file:
            parts.append(f"{self.editor_project}/{self.editor_file}")
        elif self.terminal_cwd:
            parts.append(f"$ {self.terminal_cwd}")
        elif self.window_title:
            parts.append(self.window_title[:80])
        return " | ".join(parts)


# =============================================================================
# 個別コレクター
# =============================================================================

async def collect_core(platform) -> dict:
    """コア情報: フロントアプリ+ウィンドウタイトル+CPU"""
    app = await platform.get_frontmost_app()
    window = await platform.get_window_info()
    cpu = os.getloadavg()[0]

    title = window.get("title", "")
    content_hash = hashlib.md5(f"{app}:{title[:80]}".encode()).hexdigest()[:8]

    return {
        "frontmost_app": app or "",
        "window_title": title,
        "content_hash": content_hash,
        "cpu_load": round(cpu, 2),
    }


async def collect_running_apps(platform) -> list[str]:
    """起動中の全アプリ"""
    try:
        return await platform.get_running_apps()
    except Exception:
        return []


async def collect_browser(platform) -> dict:
    """ブラウザのアクティブタブ情報"""
    try:
        info = await platform.get_browser_info()
        return {
            "browser_url": info.get("url", ""),
            "browser_title": info.get("title", ""),
        }
    except Exception:
        return {}


async def collect_all_browser_tabs(platform) -> list[dict]:
    """ブラウザの全タブ（全ウィンドウ）"""
    try:
        if hasattr(platform, "get_all_browser_tabs"):
            return await platform.get_all_browser_tabs()
    except Exception as e:
        logger.debug(f"All tabs collection failed: {e}")
    return []


def extract_editor_context(app: str, title: str) -> dict:
    """エディタのウィンドウタイトルからファイル名・プロジェクト名・言語を抽出

    パターン例:
    - Cursor: "loop.py — MyProject"
    - VS Code: "loop.py - MyProject - Visual Studio Code"
    - Xcode: "AppDelegate.swift — MyApp"
    """
    _EDITOR_APPS = {"Cursor", "Visual Studio Code", "Xcode", "PyCharm",
                    "IntelliJ IDEA", "WebStorm", "Sublime Text", "Nova"}
    if app not in _EDITOR_APPS or not title:
        return {}

    result = {}

    # ファイル名とプロジェクト名を分割
    parts = re.split(r"\s*[—\-|]\s*", title)
    # 最後にアプリ名が来ることがある → 除去
    parts = [p.strip() for p in parts if p.strip() and p.strip() != app]

    if parts:
        first = parts[0]
        # ファイル名っぽいもの（拡張子あり）
        ext_match = re.search(r"\.(\w{1,8})$", first)
        if ext_match:
            result["editor_file"] = first
            result["editor_language"] = _ext_to_language(ext_match.group(1))
        # プロジェクト名（2番目以降）
        if len(parts) > 1:
            result["editor_project"] = parts[-1]
        elif not ext_match:
            result["editor_project"] = first

    return result


def _ext_to_language(ext: str) -> str:
    """拡張子→言語名"""
    _MAP = {
        "py": "Python", "js": "JavaScript", "ts": "TypeScript",
        "tsx": "TypeScript", "jsx": "JavaScript",
        "rs": "Rust", "go": "Go", "rb": "Ruby",
        "swift": "Swift", "kt": "Kotlin", "java": "Java",
        "c": "C", "cpp": "C++", "h": "C/C++",
        "html": "HTML", "css": "CSS", "scss": "SCSS",
        "json": "JSON", "yaml": "YAML", "yml": "YAML",
        "md": "Markdown", "sql": "SQL", "sh": "Shell",
    }
    return _MAP.get(ext.lower(), ext)


def extract_terminal_context(app: str, title: str) -> dict:
    """ターミナルのタイトルからCWD+コマンドを抽出

    パターン例:
    - Terminal.app: "user@hostname: ~/projects/shiki — python main.py"
    - iTerm2: "~/projects/shiki (python)"
    - ターミナル全般: "zsh" だけの場合もある
    """
    _TERMINAL_APPS = {"Terminal", "iTerm2", "iTerm", "Alacritty", "Warp", "Hyper", "kitty"}
    if app not in _TERMINAL_APPS or not title:
        return {}

    result = {}

    # CWDの抽出: ~/path or /path パターン
    cwd_match = re.search(r"(~?/[\w/.\-]+)", title)
    if cwd_match:
        result["terminal_cwd"] = cwd_match.group(1)

    # コマンドの抽出: "— command" or "(command)" パターン
    cmd_match = re.search(r"[—\-]\s*(.+?)$", title)
    if cmd_match:
        cmd = cmd_match.group(1).strip()
        # zsh/bash自体はスキップ
        if cmd not in ("zsh", "bash", "fish", "-zsh", "-bash"):
            result["terminal_command"] = cmd[:60]
    else:
        paren_match = re.search(r"\(([^)]+)\)", title)
        if paren_match:
            result["terminal_command"] = paren_match.group(1)[:60]

    return result


# クリップボード監視用の状態
_last_clipboard_hash: str = ""

# 機密パターン（クリップボード用）
_CLIPBOARD_SENSITIVE = re.compile(
    r"(?:sk-|AIza|ghp_|gho_|xox[bsrpa]-|glpat-|ntn_|secret_)[A-Za-z0-9_\-]{10,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"  # JWT
    r"|\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"  # credit card
    r"|password\s*[:=]\s*\S+"
    r"|token\s*[:=]\s*\S+",
    re.IGNORECASE,
)


async def collect_clipboard(platform) -> dict:
    """クリップボード変化検知（機密フィルタ付き）"""
    global _last_clipboard_hash
    try:
        text = await platform.get_clipboard()
        if not text or len(text) > 5000:
            return {}

        h = hashlib.md5(text.encode(errors="replace")).hexdigest()[:12]
        if h == _last_clipboard_hash:
            return {}
        _last_clipboard_hash = h

        # 機密チェック
        if _CLIPBOARD_SENSITIVE.search(text):
            return {"clipboard_changed": True, "clipboard_preview": "[機密データ]"}

        # 先頭200文字だけ記録
        preview = text.replace("\n", " ").strip()[:200]
        return {"clipboard_changed": True, "clipboard_preview": preview}

    except Exception:
        return {}


def detect_meeting(running_apps: list[str], browser_url: str) -> dict:
    """ミーティング状態を推定"""
    _MEETING_APPS = {"zoom.us", "Microsoft Teams", "FaceTime", "Webex"}
    _MEETING_URLS = ("meet.google.com", "zoom.us/j/", "teams.microsoft.com/l/meetup")

    for app in running_apps:
        if app in _MEETING_APPS:
            return {"meeting_likely": True, "meeting_app": app}

    if browser_url:
        for url_pattern in _MEETING_URLS:
            if url_pattern in browser_url.lower():
                return {"meeting_likely": True, "meeting_app": "Browser"}

    return {"meeting_likely": False, "meeting_app": ""}


async def collect_display_count() -> int:
    """接続ディスプレイ数"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "system_profiler", "SPDisplaysDataType", "-detailLevel", "mini",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = stdout.decode(errors="replace")
        # "Resolution:" の出現回数 ≈ ディスプレイ数
        return max(1, text.count("Resolution:"))
    except Exception:
        return 1


async def collect_recent_files(project_path: str, since_seconds: int = 300) -> list[str]:
    """プロジェクトディレクトリ内の最近変更されたファイル"""
    if not project_path:
        return []

    try:
        path = Path(project_path).expanduser()
        if not path.is_dir():
            return []

        import time
        cutoff = time.time() - since_seconds
        recent = []
        # 走査深度制限（パフォーマンス）
        for f in path.rglob("*"):
            if len(recent) >= 20:
                break
            if f.is_file() and f.stat().st_mtime > cutoff:
                # 隠しファイル/ディレクトリはスキップ
                rel = f.relative_to(path)
                if not any(part.startswith(".") for part in rel.parts):
                    ext = f.suffix.lower()
                    if ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go",
                               ".swift", ".java", ".html", ".css", ".json", ".yaml",
                               ".md", ".sql", ".sh", ".toml", ".cfg"):
                        recent.append(str(rel))

        return recent
    except Exception:
        return []


async def collect_git_branch(project_path: str) -> str:
    """gitブランチ名"""
    if not project_path:
        return ""
    try:
        path = Path(project_path).expanduser()
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return ""


# =============================================================================
# 統合スナップショット収集
# =============================================================================

async def collect_full_snapshot(
    platform,
    cycle: int,
    last_content_hash: str = "",
    project_path: str = "",
) -> ObservationSnapshot:
    """1サイクル分の全データを収集（スタガード実行）

    cycle: ループカウンタ（スタガードのタイミング制御に使用）
    """
    snap = ObservationSnapshot()
    now = datetime.now()
    snap.timestamp = now.isoformat()
    snap.hour = now.hour

    # --- 毎サイクル（5秒ごと） ---
    core = await collect_core(platform)
    snap.frontmost_app = core.get("frontmost_app", "")
    snap.window_title = core.get("window_title", "")
    snap.content_hash = core.get("content_hash", "")
    snap.cpu_load = core.get("cpu_load", 0.0)

    # エディタコンテキスト（パースのみ、APIコスト0）
    editor = extract_editor_context(snap.frontmost_app, snap.window_title)
    if editor:
        snap.editor_file = editor.get("editor_file", "")
        snap.editor_project = editor.get("editor_project", "")
        snap.editor_language = editor.get("editor_language", "")

    # ターミナルコンテキスト（パースのみ）
    terminal = extract_terminal_context(snap.frontmost_app, snap.window_title)
    if terminal:
        snap.terminal_cwd = terminal.get("terminal_cwd", "")
        snap.terminal_command = terminal.get("terminal_command", "")

    # --- 2サイクルに1回（10秒ごと） ---
    if cycle % 2 == 0:
        snap.running_apps = await collect_running_apps(platform)

        clipboard = await collect_clipboard(platform)
        if clipboard:
            snap.clipboard_changed = clipboard.get("clipboard_changed", False)
            snap.clipboard_preview = clipboard.get("clipboard_preview", "")

    # --- 6サイクルに1回（30秒ごと） ---
    if cycle % 6 == 0:
        browser = await collect_browser(platform)
        snap.browser_url = browser.get("browser_url", "")
        snap.browser_title = browser.get("browser_title", "")

        snap.all_tabs = await collect_all_browser_tabs(platform)

        if snap.running_apps:
            meeting = detect_meeting(snap.running_apps, snap.browser_url)
            snap.meeting_likely = meeting.get("meeting_likely", False)
            snap.meeting_app = meeting.get("meeting_app", "")

        # gitブランチ（エディタのプロジェクトパスから）
        git_path = project_path or ""
        if not git_path and snap.editor_project:
            # プロジェクト名からパス推定（ホームディレクトリ配下を探す）
            home = Path.home()
            for candidate in [home / snap.editor_project, home / "Desktop" / snap.editor_project,
                              home / "Documents" / snap.editor_project]:
                if candidate.is_dir() and (candidate / ".git").exists():
                    git_path = str(candidate)
                    break
        snap.git_branch = await collect_git_branch(git_path)
        snap.recent_files = await collect_recent_files(git_path)
    elif snap.frontmost_app in ("Google Chrome", "Safari", "Arc", "Firefox"):
        # ブラウザが前面の時は毎サイクルURLを取る
        browser = await collect_browser(platform)
        snap.browser_url = browser.get("browser_url", "")
        snap.browser_title = browser.get("browser_title", "")

    # --- 12サイクルに1回（60秒ごと） ---
    if cycle % 12 == 0:
        snap.display_count = await collect_display_count()

    return snap

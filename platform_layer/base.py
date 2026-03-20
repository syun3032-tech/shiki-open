"""プラットフォーム抽象基底クラス

全OS共通のインターフェースを定義。
各OSの実装はこのクラスを継承して具体的な処理を実装する。
"""

from abc import ABC, abstractmethod
from pathlib import Path


class PlatformBase(ABC):
    """OS操作の抽象インターフェース"""

    @property
    @abstractmethod
    def os_name(self) -> str:
        """OS名を返す ("macos", "windows", "linux")"""
        ...

    # === スクリーンショット ===

    @abstractmethod
    async def take_screenshot(self, output_path: str) -> bool:
        """スクリーンショットを撮影してファイルに保存
        Returns: 成功したらTrue
        """
        ...

    @abstractmethod
    async def get_image_dimensions(self, filepath: str) -> tuple[int, int]:
        """画像のピクセルサイズを取得 (width, height)"""
        ...

    @abstractmethod
    async def resize_image(self, filepath: str, output_path: str, width: int) -> bool:
        """画像を指定幅にリサイズ（アスペクト比維持）"""
        ...

    @abstractmethod
    async def convert_to_jpeg(self, filepath: str, output_path: str, quality: int = 80) -> bool:
        """画像をJPEGに変換"""
        ...

    @abstractmethod
    async def crop_image(
        self, filepath: str, output_path: str,
        x: int, y: int, width: int, height: int
    ) -> bool:
        """画像の指定領域をクロップ"""
        ...

    # === マウス操作 ===

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """メインディスプレイのサイズ (width, height)"""
        ...

    @abstractmethod
    def get_cursor_position(self) -> tuple[int, int]:
        """現在のカーソル位置 (x, y)"""
        ...

    @abstractmethod
    async def move_mouse(self, x: int, y: int):
        """マウスカーソルを移動"""
        ...

    @abstractmethod
    async def click(self, x: int, y: int):
        """左クリック"""
        ...

    @abstractmethod
    async def double_click(self, x: int, y: int):
        """ダブルクリック"""
        ...

    @abstractmethod
    async def right_click(self, x: int, y: int):
        """右クリック"""
        ...

    @abstractmethod
    async def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5):
        """ドラッグ操作"""
        ...

    @abstractmethod
    async def scroll(self, direction: str = "down", amount: int = 15):
        """スクロール (direction: "up" or "down")"""
        ...

    # === キーボード操作 ===

    @abstractmethod
    async def type_text(self, text: str):
        """テキストを入力（クリップボード経由でIMEバイパス）"""
        ...

    @abstractmethod
    async def press_key(self, key: str, modifiers: list[str] | None = None):
        """キーを押す（ショートカット対応）
        key: "return", "tab", "escape", "a"-"z", "f1"-"f12" etc.
        modifiers: ["command"/"ctrl", "shift", "alt"/"option", "control"]
        """
        ...

    # === デスクトップ制御 ===

    @abstractmethod
    async def open_app(self, app_name: str) -> bool:
        """アプリを起動"""
        ...

    @abstractmethod
    async def open_url(self, url: str, browser: str | None = None) -> bool:
        """URLをブラウザで開く"""
        ...

    @abstractmethod
    async def get_frontmost_app(self) -> str:
        """最前面のアプリ名を取得"""
        ...

    @abstractmethod
    async def get_running_apps(self) -> list[str]:
        """実行中のアプリ一覧"""
        ...

    @abstractmethod
    async def get_browser_info(self) -> dict:
        """最前面ブラウザのURL・タイトル
        Returns: {"browser": str, "url": str, "title": str}
        """
        ...

    @abstractmethod
    async def get_window_info(self) -> dict:
        """最前面ウィンドウの情報
        Returns: {"app": str, "title": str, "position": {"x", "y"}, "size": {"width", "height"}}
        """
        ...

    @abstractmethod
    async def set_volume(self, level: int):
        """音量を設定 (0-100)"""
        ...

    @abstractmethod
    async def toggle_dark_mode(self):
        """ダークモード切替"""
        ...

    @abstractmethod
    async def show_notification(self, title: str, message: str):
        """デスクトップ通知を表示"""
        ...

    # === クリップボード ===

    @abstractmethod
    async def get_clipboard(self) -> str:
        """クリップボードの内容を取得"""
        ...

    @abstractmethod
    async def set_clipboard(self, text: str):
        """クリップボードにテキストを設定"""
        ...

    # === セキュリティ監査 ===

    @abstractmethod
    def security_audit(self) -> dict[str, bool]:
        """OS固有のセキュリティ監査
        Returns: {"check_name": passed_bool, ...}
        """
        ...

    # === ユーティリティ ===

    def get_home_dir(self) -> Path:
        """ユーザーのホームディレクトリ"""
        return Path.home()

    def get_default_allowed_paths(self) -> list[Path]:
        """デフォルトのアクセス許可パス"""
        home = self.get_home_dir()
        return [
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
            Path("/tmp"),
        ]

    @abstractmethod
    def get_allowed_commands(self) -> frozenset[str]:
        """OSごとの許可コマンドセット"""
        ...

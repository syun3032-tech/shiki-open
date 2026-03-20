"""Linux プラットフォーム実装

xdotool + scrot/gnome-screenshot + Pillow ベース。
X11/Wayland両対応を目指す。

必要パッケージ:
  pip install pyautogui Pillow pyperclip
  apt install xdotool scrot xclip  # Ubuntu/Debian
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from platform_layer.base import PlatformBase

logger = logging.getLogger("shiki.platform.linux")


class LinuxPlatform(PlatformBase):

    @property
    def os_name(self) -> str:
        return "linux"

    def _is_wayland(self) -> bool:
        return os.environ.get("XDG_SESSION_TYPE") == "wayland"

    def _get_pyautogui(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            return pyautogui
        except ImportError:
            raise RuntimeError("pyautogui が必要です: pip install pyautogui")

    def _get_pil(self):
        try:
            from PIL import Image
            return Image
        except ImportError:
            raise RuntimeError("Pillow が必要です: pip install Pillow")

    # === スクリーンショット ===

    async def take_screenshot(self, output_path: str) -> bool:
        # Wayland: grim, X11: scrot → pyautogui fallback
        if self._is_wayland():
            try:
                proc = await asyncio.create_subprocess_exec(
                    "grim", output_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                if Path(output_path).exists():
                    return True
            except Exception:
                pass

        # X11: scrot
        try:
            proc = await asyncio.create_subprocess_exec(
                "scrot", output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if Path(output_path).exists():
                return True
        except Exception:
            pass

        # フォールバック: gnome-screenshot
        try:
            proc = await asyncio.create_subprocess_exec(
                "gnome-screenshot", "-f", output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if Path(output_path).exists():
                return True
        except Exception:
            pass

        # 最終フォールバック: pyautogui
        try:
            pag = self._get_pyautogui()
            screenshot = pag.screenshot()
            screenshot.save(output_path)
            return Path(output_path).exists()
        except Exception:
            return False

    async def get_image_dimensions(self, filepath: str) -> tuple[int, int]:
        try:
            Image = self._get_pil()
            with Image.open(filepath) as img:
                return img.size
        except Exception:
            return 0, 0

    async def resize_image(self, filepath: str, output_path: str, width: int) -> bool:
        try:
            Image = self._get_pil()
            with Image.open(filepath) as img:
                ratio = width / img.width
                new_height = int(img.height * ratio)
                resized = img.resize((width, new_height), Image.LANCZOS)
                resized.save(output_path)
            return Path(output_path).exists()
        except Exception:
            return False

    async def convert_to_jpeg(self, filepath: str, output_path: str, quality: int = 80) -> bool:
        try:
            Image = self._get_pil()
            with Image.open(filepath) as img:
                rgb = img.convert("RGB")
                rgb.save(output_path, "JPEG", quality=quality)
            return Path(output_path).exists()
        except Exception:
            return False

    async def crop_image(
        self, filepath: str, output_path: str,
        x: int, y: int, width: int, height: int
    ) -> bool:
        try:
            Image = self._get_pil()
            with Image.open(filepath) as img:
                cropped = img.crop((x, y, x + width, y + height))
                cropped.save(output_path)
            return Path(output_path).exists()
        except Exception:
            return False

    # === マウス操作 ===

    def get_screen_size(self) -> tuple[int, int]:
        try:
            pag = self._get_pyautogui()
            return pag.size()
        except Exception:
            # xdotoolフォールバック
            try:
                r = subprocess.run(
                    ["xdotool", "getdisplaygeometry"],
                    capture_output=True, text=True, timeout=5,
                )
                parts = r.stdout.strip().split()
                return (int(parts[0]), int(parts[1]))
            except Exception:
                return (1920, 1080)

    def get_cursor_position(self) -> tuple[int, int]:
        try:
            pag = self._get_pyautogui()
            pos = pag.position()
            return (pos.x, pos.y)
        except Exception:
            return (0, 0)

    async def move_mouse(self, x: int, y: int):
        pag = self._get_pyautogui()
        pag.moveTo(x, y, duration=0.3)

    async def click(self, x: int, y: int):
        pag = self._get_pyautogui()
        pag.click(x, y)

    async def double_click(self, x: int, y: int):
        pag = self._get_pyautogui()
        pag.doubleClick(x, y)

    async def right_click(self, x: int, y: int):
        pag = self._get_pyautogui()
        pag.rightClick(x, y)

    async def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5):
        pag = self._get_pyautogui()
        pag.moveTo(x1, y1)
        pag.drag(x2 - x1, y2 - y1, duration=duration)

    async def scroll(self, direction: str = "down", amount: int = 15):
        pag = self._get_pyautogui()
        clicks = -amount if direction == "down" else amount
        pag.scroll(clicks)

    # === キーボード操作 ===

    _KEY_MAP = {
        "return": "enter", "escape": "esc", "delete": "backspace",
        "command": "ctrl",  # macOS Command → Linux Ctrl
        "option": "alt",
    }

    async def type_text(self, text: str):
        try:
            import pyperclip
            old = pyperclip.paste()
            pyperclip.copy(text[:1000])
            pag = self._get_pyautogui()
            pag.hotkey("ctrl", "v")
            await asyncio.sleep(0.2)
            pyperclip.copy(old)
        except ImportError:
            pag = self._get_pyautogui()
            pag.typewrite(text[:1000], interval=0.02)

    async def press_key(self, key: str, modifiers: list[str] | None = None):
        pag = self._get_pyautogui()
        mapped_key = self._KEY_MAP.get(key.lower(), key.lower())

        if modifiers:
            mapped_mods = [self._KEY_MAP.get(m.lower(), m.lower()) for m in modifiers]
            pag.hotkey(*mapped_mods, mapped_key)
        else:
            pag.press(mapped_key)

    # === デスクトップ制御 ===

    async def open_app(self, app_name: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdg-open", app_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return True
        except Exception:
            return False

    async def open_url(self, url: str, browser: str | None = None) -> bool:
        try:
            cmd = browser if browser else "xdg-open"
            proc = await asyncio.create_subprocess_exec(
                cmd, url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return True
        except Exception:
            return False

    async def get_frontmost_app(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "getactivewindow", "getwindowname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip()
        except Exception:
            return ""

    async def get_running_apps(self) -> list[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "wmctrl", "-l",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            lines = stdout.decode().strip().split("\n")
            return [line.split(None, 3)[-1] for line in lines if line]
        except Exception:
            return []

    async def get_browser_info(self) -> dict:
        return {"browser": "", "url": "", "title": ""}

    async def get_window_info(self) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "getactivewindow", "getwindowname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            title = stdout.decode().strip()
            return {"app": "", "title": title, "position": {}, "size": {}}
        except Exception:
            return {"app": "", "title": "", "position": {}, "size": {}}

    async def set_volume(self, level: int):
        level = max(0, min(100, level))
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            # amixerフォールバック
            try:
                proc = await asyncio.create_subprocess_exec(
                    "amixer", "sset", "Master", f"{level}%",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                logger.warning("音量変更失敗: pactl/amixer が必要")

    async def toggle_dark_mode(self):
        # GNOME
        try:
            proc = await asyncio.create_subprocess_exec(
                "gsettings", "get", "org.gnome.desktop.interface", "color-scheme",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            current = stdout.decode().strip()
            new_scheme = "prefer-light" if "dark" in current else "prefer-dark"
            proc = await asyncio.create_subprocess_exec(
                "gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            logger.warning("ダークモード切替失敗（GNOME以外は未対応）")

    async def show_notification(self, title: str, message: str):
        try:
            proc = await asyncio.create_subprocess_exec(
                "notify-send", title[:100], message[:500],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            logger.warning("通知表示失敗: notify-send が必要")

    # === クリップボード ===

    async def get_clipboard(self) -> str:
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            proc = await asyncio.create_subprocess_exec(
                "xclip", "-selection", "clipboard", "-o",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode("utf-8", errors="replace")

    async def set_clipboard(self, text: str):
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            proc = await asyncio.create_subprocess_exec(
                "xclip", "-selection", "clipboard",
                stdin=asyncio.subprocess.PIPE,
            )
            await proc.communicate(input=text.encode("utf-8"))

    # === セキュリティ監査 ===

    def security_audit(self) -> dict[str, bool]:
        results = {}

        # UFW Firewall
        try:
            r = subprocess.run(
                ["ufw", "status"], capture_output=True, text=True, timeout=5,
            )
            results["firewall_enabled"] = "active" in r.stdout.lower()
        except Exception:
            results["firewall_enabled"] = False

        # SELinux / AppArmor
        try:
            r = subprocess.run(
                ["getenforce"], capture_output=True, text=True, timeout=5,
            )
            results["selinux_enforcing"] = "enforcing" in r.stdout.lower()
        except Exception:
            results["selinux_enforcing"] = False
            # AppArmorフォールバック
            try:
                r = subprocess.run(
                    ["aa-status", "--enabled"], capture_output=True, text=True, timeout=5,
                )
                results["apparmor_enabled"] = r.returncode == 0
            except Exception:
                results["apparmor_enabled"] = False

        # ディスク暗号化（LUKS）
        try:
            r = subprocess.run(
                ["lsblk", "-o", "TYPE"], capture_output=True, text=True, timeout=5,
            )
            results["disk_encrypted"] = "crypt" in r.stdout.lower()
        except Exception:
            results["disk_encrypted"] = False

        return results

    def get_allowed_commands(self) -> frozenset[str]:
        return frozenset({
            "ls", "find", "cat", "head", "tail", "wc", "file", "du", "df",
            "stat", "md5sum", "sha256sum",
            "grep", "awk", "sed", "sort", "uniq", "cut", "tr", "diff",
            "ps", "top", "lsof", "which", "whoami", "hostname",
            "git",
            "uname", "date", "cal", "uptime", "free",
            "mkdir", "touch", "cp", "mv",
            "echo", "printf", "xdg-open",
            "xclip",
        })

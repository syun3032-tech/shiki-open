"""Windows プラットフォーム実装

pyautogui + Pillow ベースのクロスプラットフォーム実装。
Windows固有のAPI（Win32 API）も活用。

必要パッケージ:
  pip install pyautogui Pillow pyperclip
  (オプション) pip install pywinauto  # ウィンドウ情報取得強化
"""

import asyncio
import logging
import subprocess
from pathlib import Path

from platform_layer.base import PlatformBase

logger = logging.getLogger("shiki.platform.windows")


class WindowsPlatform(PlatformBase):

    @property
    def os_name(self) -> str:
        return "windows"

    def _get_pyautogui(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            return pyautogui
        except ImportError:
            raise RuntimeError(
                "pyautogui が必要です: pip install pyautogui"
            )

    def _get_pil(self):
        try:
            from PIL import Image
            return Image
        except ImportError:
            raise RuntimeError(
                "Pillow が必要です: pip install Pillow"
            )

    # === スクリーンショット ===

    async def take_screenshot(self, output_path: str) -> bool:
        try:
            pag = self._get_pyautogui()
            screenshot = pag.screenshot()
            screenshot.save(output_path)
            return Path(output_path).exists()
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
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

    # === マウス操作（pyautogui） ===

    def get_screen_size(self) -> tuple[int, int]:
        pag = self._get_pyautogui()
        return pag.size()

    def get_cursor_position(self) -> tuple[int, int]:
        pag = self._get_pyautogui()
        pos = pag.position()
        return (pos.x, pos.y)

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
        "command": "win",  # macOS Command → Windows Win key
        "option": "alt",
    }

    async def type_text(self, text: str):
        """pyperclipでクリップボード経由入力"""
        try:
            import pyperclip
            old = pyperclip.paste()
            pyperclip.copy(text[:1000])
            pag = self._get_pyautogui()
            pag.hotkey("ctrl", "v")
            await asyncio.sleep(0.2)
            pyperclip.copy(old)
        except ImportError:
            # pyperclipがなければ直接入力
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
                "cmd", "/c", "start", "", app_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return True
        except Exception:
            return False

    async def open_url(self, url: str, browser: str | None = None) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "cmd", "/c", "start", "", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return True
        except Exception:
            return False

    async def get_frontmost_app(self) -> str:
        try:
            # PowerShellで最前面アプリを取得
            script = '(Get-Process | Where-Object {$_.MainWindowHandle -ne 0} | Sort-Object -Property CPU -Descending | Select-Object -First 1).ProcessName'
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return stdout.decode().strip()
        except Exception:
            return ""

    async def get_running_apps(self) -> list[str]:
        try:
            script = 'Get-Process | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -ExpandProperty ProcessName'
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return [line.strip() for line in stdout.decode().split("\n") if line.strip()]
        except Exception:
            return []

    async def get_browser_info(self) -> dict:
        # WindowsではPlaywright経由またはWin32 API経由で取得
        return {"browser": "", "url": "", "title": ""}

    async def get_window_info(self) -> dict:
        try:
            script = '''
$fw = Get-Process | Where-Object {$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne ''} | Select-Object -First 1
if ($fw) { "$($fw.ProcessName)|$($fw.MainWindowTitle)" } else { "|" }
'''
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            parts = stdout.decode().strip().split("|")
            return {
                "app": parts[0] if parts else "",
                "title": parts[1] if len(parts) > 1 else "",
                "position": {},
                "size": {},
            }
        except Exception:
            return {"app": "", "title": "", "position": {}, "size": {}}

    async def set_volume(self, level: int):
        level = max(0, min(100, level))
        # nircmdが必要、なければPowerShell経由
        try:
            script = f'''
$vol = [math]::Round({level} / 100 * 65535)
$obj = New-Object -ComObject WScript.Shell
'''
            # 簡易実装: nircmd使用
            proc = await asyncio.create_subprocess_exec(
                "nircmd", "setsysvolume", str(int(level / 100 * 65535)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            logger.warning("音量変更にはnircmdが必要です")

    async def toggle_dark_mode(self):
        try:
            script = '''
$path = 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize'
$current = (Get-ItemProperty -Path $path).AppsUseLightTheme
Set-ItemProperty -Path $path -Name AppsUseLightTheme -Value ([int](!$current))
Set-ItemProperty -Path $path -Name SystemUsesLightTheme -Value ([int](!$current))
'''
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception:
            logger.warning("ダークモード切替失敗")

    async def show_notification(self, title: str, message: str):
        try:
            # PowerShellでトースト通知
            safe_title = title[:100].replace("'", "''")
            safe_msg = message[:500].replace("'", "''")
            script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName('text')
$textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null
$textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_msg}')) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Shiki').Show($toast)
"""
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception:
            logger.warning("通知表示失敗")

    # === クリップボード ===

    async def get_clipboard(self) -> str:
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", "Get-Clipboard",
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
                "powershell", "-Command", f"Set-Clipboard -Value '{text}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)

    # === セキュリティ監査 ===

    def security_audit(self) -> dict[str, bool]:
        results = {}

        # Windows Defender
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-MpComputerStatus).RealTimeProtectionEnabled"],
                capture_output=True, text=True, timeout=10,
            )
            results["defender_enabled"] = "True" in r.stdout
        except Exception:
            results["defender_enabled"] = False

        # BitLocker
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-BitLockerVolume -MountPoint C:).ProtectionStatus"],
                capture_output=True, text=True, timeout=10,
            )
            results["bitlocker_enabled"] = "On" in r.stdout
        except Exception:
            results["bitlocker_enabled"] = False

        # Windows Firewall
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-NetFirewallProfile -Profile Domain,Public,Private).Enabled"],
                capture_output=True, text=True, timeout=10,
            )
            results["firewall_enabled"] = "True" in r.stdout
        except Exception:
            results["firewall_enabled"] = False

        return results

    def get_allowed_commands(self) -> frozenset[str]:
        return frozenset({
            "dir", "type", "find", "findstr", "where",
            "sort", "more", "fc",
            "tasklist", "whoami", "hostname", "systeminfo",
            "git",
            "ver", "date", "time",
            "mkdir", "copy", "move", "ren",
            "echo", "start",
            # PowerShell
            "powershell",
        })

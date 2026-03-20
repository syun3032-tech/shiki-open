"""macOS プラットフォーム実装

既存のtools/desktop.py, tools/mouse.py, tools/screenshot.pyの
Mac固有ロジックをここに集約。
"""

import asyncio
import logging
import math
import random
import subprocess
import time
from pathlib import Path

from platform_layer.base import PlatformBase

logger = logging.getLogger("shiki.platform.macos")

# 画面サイズキャッシュ
_screen_size_cache: tuple[int, int] | None = None
_screen_size_cache_time: float = 0.0
_SCREEN_SIZE_TTL = 30.0

# ベジェ曲線パラメータ
_OVERSHOOT_THRESHOLD = 300


class MacOSPlatform(PlatformBase):

    @property
    def os_name(self) -> str:
        return "macos"

    # === ヘルパー ===

    async def _run_osascript(self, script: str, timeout: int = 10) -> dict:
        """osascriptを安全に実行"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0:
                return {"success": True, "output": stdout.decode().strip()}
            return {"success": False, "error": stderr.decode().strip()}
        except asyncio.TimeoutError:
            return {"success": False, "error": "タイムアウト"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _escape_applescript(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    # === スクリーンショット ===

    async def take_screenshot(self, output_path: str) -> bool:
        """ScreenCaptureKit (shiki_capture) またはscreencaptureでスクショ"""
        capture_app = Path(__file__).parent.parent / "tools" / "ShikiCapture.app"
        capture_bin = Path(__file__).parent.parent / "tools" / "shiki_capture"
        filepath = Path(output_path)

        # 方式1: .appバンドル
        if capture_app.exists():
            ok = await self._capture_via_app(capture_app, filepath)
            if ok:
                return True

        # 方式2: 直接バイナリ
        if capture_bin.exists():
            ok = await self._capture_direct(capture_bin, filepath)
            if ok:
                return True

        # 方式3: 標準screencapture（フォールバック）
        try:
            proc = await asyncio.create_subprocess_exec(
                "screencapture", "-x", str(filepath),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return filepath.exists()
        except Exception:
            return False

    async def _capture_via_app(self, app_path: Path, filepath: Path) -> bool:
        """ShikiCapture.appで撮影"""
        import tempfile
        import os
        try:
            req_fd, req_path = tempfile.mkstemp(prefix="shiki_cap_", suffix=".req")
            with os.fdopen(req_fd, "w") as f:
                f.write(str(filepath))
            done_path = req_path + ".done"

            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/open", "-a", str(app_path),
                "--args", "--request", req_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)

            for _ in range(120):
                if Path(done_path).exists():
                    result = Path(done_path).read_text().strip()
                    Path(req_path).unlink(missing_ok=True)
                    Path(done_path).unlink(missing_ok=True)
                    return result == "OK" and filepath.exists()
                await asyncio.sleep(0.1)

            Path(req_path).unlink(missing_ok=True)
            Path(done_path).unlink(missing_ok=True)
            return False
        except Exception:
            return False

    async def _capture_direct(self, binary_path: Path, filepath: Path) -> bool:
        """直接バイナリ実行"""
        try:
            proc = await asyncio.create_subprocess_exec(
                str(binary_path), str(filepath),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
            return proc.returncode == 0 and filepath.exists()
        except Exception:
            return False

    async def get_image_dimensions(self, filepath: str) -> tuple[int, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            output = stdout.decode()
            w, h = 0, 0
            for line in output.split("\n"):
                if "pixelWidth" in line:
                    w = int(line.split(":")[-1].strip())
                elif "pixelHeight" in line:
                    h = int(line.split(":")[-1].strip())
            return w, h
        except Exception:
            return 0, 0

    async def resize_image(self, filepath: str, output_path: str, width: int) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/sips", "--resampleWidth", str(width),
                filepath, "--out", output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return Path(output_path).exists()
        except Exception:
            return False

    async def convert_to_jpeg(self, filepath: str, output_path: str, quality: int = 80) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/sips", "--setProperty", "format", "jpeg",
                "--setProperty", "formatOptions", str(quality),
                filepath, "--out", output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return Path(output_path).exists()
        except Exception:
            return False

    async def crop_image(
        self, filepath: str, output_path: str,
        x: int, y: int, width: int, height: int
    ) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/sips",
                "--cropS", str(height), str(width),
                "--cropOffset", str(y), str(x),
                filepath, "--out", output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return Path(output_path).exists()
        except Exception:
            return False

    # === マウス操作（Quartz CGEvent） ===

    def get_screen_size(self) -> tuple[int, int]:
        global _screen_size_cache, _screen_size_cache_time
        now = time.monotonic()
        if _screen_size_cache and (now - _screen_size_cache_time) < _SCREEN_SIZE_TTL:
            return _screen_size_cache

        from Quartz import CGDisplayBounds, CGMainDisplayID
        bounds = CGDisplayBounds(CGMainDisplayID())
        _screen_size_cache = (int(bounds.size.width), int(bounds.size.height))
        _screen_size_cache_time = now
        return _screen_size_cache

    def get_cursor_position(self) -> tuple[int, int]:
        from Quartz import CGEventCreate, CGEventGetLocation
        event = CGEventCreate(None)
        point = CGEventGetLocation(event)
        return (int(point.x), int(point.y))

    def _bezier_point(self, t: float, p0: float, p1: float, p2: float, p3: float) -> float:
        u = 1 - t
        return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3

    def _generate_bezier_path(
        self, sx: int, sy: int, ex: int, ey: int, steps: int | None = None
    ) -> list[tuple[int, int]]:
        dist = math.hypot(ex - sx, ey - sy)
        if dist < 5:
            return [(ex, ey)]
        if steps is None:
            steps = max(8, min(int(dist / 8), 40))

        dx, dy = ex - sx, ey - sy
        nx, ny = -dy, dx
        norm = math.hypot(nx, ny) or 1
        nx, ny = nx / norm, ny / norm

        offset = random.uniform(0.1, 0.35) * dist
        side = random.choice([-1, 1])

        cp1x = sx + dx * 0.25 + nx * offset * side * random.uniform(0.5, 1.0)
        cp1y = sy + dy * 0.25 + ny * offset * side * random.uniform(0.5, 1.0)
        cp2x = sx + dx * 0.75 + nx * offset * side * random.uniform(0.3, 0.8)
        cp2y = sy + dy * 0.75 + ny * offset * side * random.uniform(0.3, 0.8)

        points = []
        for i in range(1, steps + 1):
            t = i / steps
            x = self._bezier_point(t, sx, cp1x, cp2x, ex)
            y = self._bezier_point(t, sy, cp1y, cp2y, ey)
            points.append((int(x), int(y)))
        return points

    async def _move_along_path(self, path: list[tuple[int, int]]):
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGPointMake,
            kCGEventMouseMoved, kCGHIDEventTap, kCGMouseButtonLeft,
        )
        for px, py in path:
            point = CGPointMake(px, py)
            event = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, event)
            await asyncio.sleep(random.uniform(0.003, 0.012))

    async def _human_move_to(self, x: int, y: int):
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGPointMake,
            kCGEventMouseMoved, kCGHIDEventTap, kCGMouseButtonLeft,
        )
        cx, cy = self.get_cursor_position()
        dist = math.hypot(x - cx, y - cy)

        if dist < 3:
            point = CGPointMake(x, y)
            event = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, event)
            return

        if dist > _OVERSHOOT_THRESHOLD:
            overshoot = random.uniform(5, 15)
            angle = math.atan2(y - cy, x - cx)
            ox = int(x + overshoot * math.cos(angle))
            oy = int(y + overshoot * math.sin(angle))
            w, h = self.get_screen_size()
            ox = max(0, min(ox, w - 1))
            oy = max(0, min(oy, h - 1))

            path1 = self._generate_bezier_path(cx, cy, ox, oy)
            await self._move_along_path(path1)
            await asyncio.sleep(random.uniform(0.02, 0.06))
            path2 = self._generate_bezier_path(ox, oy, x, y, steps=6)
            await self._move_along_path(path2)
        else:
            path = self._generate_bezier_path(cx, cy, x, y)
            await self._move_along_path(path)

    async def move_mouse(self, x: int, y: int):
        await self._human_move_to(x, y)

    async def click(self, x: int, y: int):
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGPointMake,
            kCGEventLeftMouseDown, kCGEventLeftMouseUp,
            kCGHIDEventTap, kCGMouseButtonLeft,
        )
        await self._human_move_to(x, y)
        await asyncio.sleep(random.uniform(0.03, 0.08))

        point = CGPointMake(x, y)
        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, down)
        await asyncio.sleep(random.uniform(0.04, 0.10))
        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, up)

    async def double_click(self, x: int, y: int):
        import Quartz as Q
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGPointMake,
            kCGEventLeftMouseDown, kCGEventLeftMouseUp,
            kCGHIDEventTap, kCGMouseButtonLeft,
        )
        await self._human_move_to(x, y)
        await asyncio.sleep(random.uniform(0.03, 0.07))

        point = CGPointMake(x, y)
        for click_num in range(1, 3):
            down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, kCGMouseButtonLeft)
            Q.CGEventSetIntegerValueField(down, Q.kCGMouseEventClickState, click_num)
            CGEventPost(kCGHIDEventTap, down)
            await asyncio.sleep(random.uniform(0.01, 0.03))
            up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, kCGMouseButtonLeft)
            Q.CGEventSetIntegerValueField(up, Q.kCGMouseEventClickState, click_num)
            CGEventPost(kCGHIDEventTap, up)
            await asyncio.sleep(random.uniform(0.01, 0.03))

    async def right_click(self, x: int, y: int):
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGPointMake,
            kCGEventRightMouseDown, kCGEventRightMouseUp,
            kCGHIDEventTap, kCGMouseButtonRight,
        )
        await self._human_move_to(x, y)
        await asyncio.sleep(random.uniform(0.03, 0.08))

        point = CGPointMake(x, y)
        down = CGEventCreateMouseEvent(None, kCGEventRightMouseDown, point, kCGMouseButtonRight)
        CGEventPost(kCGHIDEventTap, down)
        await asyncio.sleep(random.uniform(0.04, 0.10))
        up = CGEventCreateMouseEvent(None, kCGEventRightMouseUp, point, kCGMouseButtonRight)
        CGEventPost(kCGHIDEventTap, up)

    async def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5):
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGPointMake,
            kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventLeftMouseDragged,
            kCGHIDEventTap, kCGMouseButtonLeft,
        )
        await self._human_move_to(x1, y1)
        await asyncio.sleep(random.uniform(0.03, 0.08))

        start = CGPointMake(x1, y1)
        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, start, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, down)
        await asyncio.sleep(random.uniform(0.03, 0.07))

        path = self._generate_bezier_path(x1, y1, x2, y2, steps=max(10, int(duration * 20)))
        for px, py in path:
            pt = CGPointMake(px, py)
            drag_ev = CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, pt, kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, drag_ev)
            await asyncio.sleep(duration / len(path))

        end = CGPointMake(x2, y2)
        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, end, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, up)

    async def scroll(self, direction: str = "down", amount: int = 15):
        import Quartz
        amount = max(1, min(50, amount))
        scroll_value = -amount if direction == "down" else amount
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine, 1, scroll_value
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    # === キーボード操作 ===

    _KEY_CODES = {
        "return": 36, "tab": 48, "escape": 53, "space": 49, "delete": 51,
        "left": 123, "right": 124, "up": 126, "down": 125,
        "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
        "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
        "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    }

    async def type_text(self, text: str):
        text = text[:1000]
        # 元のクリップボードを保存
        original_clipboard = None
        try:
            save_proc = await asyncio.create_subprocess_exec(
                "pbpaste",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(save_proc.communicate(), timeout=5)
            if save_proc.returncode == 0:
                original_clipboard = stdout
        except Exception:
            pass

        # クリップボードにコピー
        proc = await asyncio.create_subprocess_exec(
            "pbcopy", stdin=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=text.encode("utf-8"))

        # Cmd+Vでペースト
        await asyncio.sleep(0.1)
        script = 'tell application "System Events" to keystroke "v" using {command down}'
        await self._run_osascript(script)

        # 元のクリップボードを復元
        if original_clipboard is not None:
            try:
                await asyncio.sleep(0.2)
                restore_proc = await asyncio.create_subprocess_exec(
                    "pbcopy", stdin=asyncio.subprocess.PIPE,
                )
                await restore_proc.communicate(input=original_clipboard)
            except Exception:
                pass

    async def press_key(self, key: str, modifiers: list[str] | None = None):
        key_lower = key.lower()

        if modifiers:
            mod_str = " down, ".join(m for m in modifiers) + " down"
            if len(key) == 1:
                safe_key = self._escape_applescript(key)
                script = f'tell application "System Events" to keystroke "{safe_key}" using {{{mod_str}}}'
            else:
                code = self._KEY_CODES.get(key_lower, 0)
                script = f'tell application "System Events" to key code {code} using {{{mod_str}}}'
        else:
            if len(key) == 1:
                safe_key = self._escape_applescript(key)
                script = f'tell application "System Events" to keystroke "{safe_key}"'
            else:
                code = self._KEY_CODES.get(key_lower, 0)
                script = f'tell application "System Events" to key code {code}'

        await self._run_osascript(script)

    # === デスクトップ制御 ===

    async def open_app(self, app_name: str) -> bool:
        safe_name = self._escape_applescript(app_name)
        script = f'tell application "{safe_name}" to activate'
        result = await self._run_osascript(script)
        return result.get("success", False)

    async def open_url(self, url: str, browser: str | None = None) -> bool:
        browser = browser or "Google Chrome"
        safe_browser = self._escape_applescript(browser)
        safe_url = self._escape_applescript(url)
        script = f'tell application "{safe_browser}" to open location "{safe_url}"'
        result = await self._run_osascript(script)
        return result.get("success", False)

    async def get_frontmost_app(self) -> str:
        script = 'tell application "System Events" to get name of first application process whose frontmost is true'
        result = await self._run_osascript(script)
        return result.get("output", "")

    async def get_running_apps(self) -> list[str]:
        script = 'tell application "System Events" to get name of every application process whose visible is true'
        result = await self._run_osascript(script)
        output = result.get("output", "")
        if output:
            return [app.strip() for app in output.split(",")]
        return []

    async def get_browser_info(self) -> dict:
        front = await self.get_frontmost_app()
        browsers = {
            "Google Chrome": {
                "url": 'tell application "Google Chrome" to get URL of active tab of front window',
                "title": 'tell application "Google Chrome" to get title of active tab of front window',
            },
            "Safari": {
                "url": 'tell application "Safari" to get URL of front document',
                "title": 'tell application "Safari" to get name of front document',
            },
            "Arc": {
                "url": 'tell application "Arc" to get URL of active tab of front window',
                "title": 'tell application "Arc" to get title of active tab of front window',
            },
        }

        browser = browsers.get(front)
        if not browser:
            for name, scripts in browsers.items():
                result = await self._run_osascript(scripts["url"])
                if result.get("success"):
                    browser = scripts
                    front = name
                    break
            if not browser:
                return {"browser": "", "url": "", "title": ""}

        url_result = await self._run_osascript(browser["url"])
        title_result = await self._run_osascript(browser["title"])

        return {
            "browser": front,
            "url": url_result.get("output", ""),
            "title": title_result.get("output", ""),
        }

    async def get_window_info(self) -> dict:
        script = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set frontWin to front window of frontApp
        set winTitle to name of frontWin
        set winPos to position of frontWin
        set winSize to size of frontWin
        return appName & "|" & winTitle & "|" & (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "|" & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
    on error
        return appName & "|no window|||"
    end try
end tell'''
        result = await self._run_osascript(script)
        if not result.get("success"):
            return {"app": "", "title": "", "position": {}, "size": {}}

        parts = result["output"].split("|")
        if len(parts) >= 4:
            pos = parts[2].split(",") if parts[2] else ["0", "0"]
            size = parts[3].split(",") if parts[3] else ["0", "0"]
            return {
                "app": parts[0],
                "title": parts[1],
                "position": {"x": int(pos[0]) if pos[0] else 0, "y": int(pos[1]) if pos[1] else 0},
                "size": {"width": int(size[0]) if size[0] else 0, "height": int(size[1]) if size[1] else 0},
            }
        return {"app": parts[0] if parts else "", "title": "", "position": {}, "size": {}}

    async def set_volume(self, level: int):
        level = max(0, min(100, level))
        await self._run_osascript(f"set volume output volume {level}")

    async def toggle_dark_mode(self):
        await self._run_osascript(
            'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode'
        )

    async def show_notification(self, title: str, message: str):
        safe_title = self._escape_applescript(title[:100])
        safe_message = self._escape_applescript(message[:500])
        await self._run_osascript(
            f'display notification "{safe_message}" with title "{safe_title}"'
        )

    # === クリップボード ===

    async def get_clipboard(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "pbpaste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return stdout.decode("utf-8", errors="replace")

    async def set_clipboard(self, text: str):
        proc = await asyncio.create_subprocess_exec(
            "pbcopy", stdin=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=text.encode("utf-8"))

    # === セキュリティ監査 ===

    def security_audit(self) -> dict[str, bool]:
        results = {}

        # SIP
        try:
            r = subprocess.run(["csrutil", "status"], capture_output=True, text=True, timeout=5)
            results["sip_enabled"] = r.returncode == 0 and "enabled" in r.stdout.lower()
        except Exception:
            results["sip_enabled"] = False

        # Firewall
        try:
            r = subprocess.run(
                ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                capture_output=True, text=True, timeout=5,
            )
            results["firewall_enabled"] = r.returncode == 0 and "enabled" in r.stdout.lower()
        except Exception:
            results["firewall_enabled"] = False

        # FileVault
        try:
            r = subprocess.run(["fdesetup", "status"], capture_output=True, text=True, timeout=5)
            results["filevault_enabled"] = r.returncode == 0 and "on" in r.stdout.lower()
        except Exception:
            results["filevault_enabled"] = False

        return results

    # === ユーティリティ ===

    def get_allowed_commands(self) -> frozenset[str]:
        return frozenset({
            "ls", "find", "cat", "head", "tail", "wc", "file", "du", "df",
            "stat", "md5", "shasum",
            "grep", "awk", "sed", "sort", "uniq", "cut", "tr", "diff",
            "ps", "top", "lsof", "which", "whoami", "hostname",
            "git",
            "uname", "sw_vers", "sysctl", "date", "cal", "uptime",
            "mkdir", "touch", "cp", "mv",
            "screencapture",
            "echo", "printf", "open", "pbcopy", "pbpaste",
        })

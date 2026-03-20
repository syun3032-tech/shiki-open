"""継続観察・学習システム（Continuous Observer）

バックグラウンドで常時ユーザーの作業を観察し、行動パターンを学習する。
識ちゃんに話しかけなくても、普通に作業してるだけで勝手に学習。
「いつも通りやって」で再現できる状態を作る。

## 学習フロー
1. 10秒間隔でアプリ名+ウィンドウタイトル+URL取得（APIコスト$0）
2. コンテキスト変化（アプリ切替等）時にリッチログ記録
3. アプリ切替時のみ、必要に応じてVision AI（オプション、機密フィルタ通過後のみ）
4. n-gram方式でワークフロー（3〜6ステップの繰り返し連鎖）を自動検出
5. 頻出ワークフローを実行可能なスキルに自動変換
6. 「いつも通りやって」で時間帯を考慮して再現

## セキュリティ
- 機密アプリ前面時はログスキップ（1Password, Keychain等）
- ウィンドウタイトルの機密パターンをマスク
- URLのクエリパラメータ除去（トークン/セッション漏洩防止）
- ログファイルはパーミッション0o600
- Vision AIはオプション（デフォルトOFF）、使う場合もローカルスキャン後のみ
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from config import RITSU_DIR, GEMINI_API_KEY

logger = logging.getLogger("shiki.observer")

# === 永続化パス ===
WORKFLOWS_FILE = RITSU_DIR / "learned_workflows.json"
WORK_PROFILE_FILE = RITSU_DIR / "work_profile.json"
ACTIVITY_LOG_DIR = RITSU_DIR / "activity_logs"
OBSERVATION_DIR = RITSU_DIR / "observations"  # Vision用スクショ（一時）

# === セキュリティ: 機密フィルタ ===
_SENSITIVE_APPS_BASELINE = frozenset({
    "1Password", "Keychain Access", "LastPass", "Bitwarden", "KeePassXC",
    "Dashlane", "Authy",
})

_SENSITIVE_TITLE_PATTERNS = [
    re.compile(r"\.env\b", re.IGNORECASE),
    re.compile(r"\b(password|passwd|secret|token|credential|api.?key)\b", re.IGNORECASE),
    re.compile(r"\b(ssh.?key|id_rsa|id_ed25519|private.?key)\b", re.IGNORECASE),
    re.compile(r"\b(bank|credit.?card|口座|暗証)\b", re.IGNORECASE),
]

# === コンテキスト正規化 ===
# チャット系アプリ: チャンネル名の変化を無視してアプリ名だけにする
_CHAT_APPS = frozenset({"Slack", "Discord", "LINE", "Messages", "Telegram"})
# エディタ系: プロジェクト名/ファイル拡張子レベルに抽象化
_EDITOR_APPS = frozenset({"Cursor", "Visual Studio Code", "Xcode", "IntelliJ IDEA", "PyCharm"})

# 観察状態
_observer_running = False
_observation_task: asyncio.Task | None = None


def _get_sensitive_apps() -> frozenset:
    """機密アプリセット（ベースライン + ユーザー設定）"""
    try:
        import user_config
        user_apps = user_config.get("observation.sensitive_apps", [])
        return _SENSITIVE_APPS_BASELINE | frozenset(user_apps)
    except Exception:
        return _SENSITIVE_APPS_BASELINE


def _strip_url_query(url: str) -> str:
    """URLからクエリパラメータとフラグメントを除去（トークン漏洩防止）"""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return url


def _is_sensitive_context(app: str, title: str, url: str) -> bool:
    """機密コンテキストかどうか判定"""
    if app in _get_sensitive_apps():
        return True
    for pat in _SENSITIVE_TITLE_PATTERNS:
        if pat.search(title) or pat.search(url):
            return True
    return False


def _normalize_context_key(app: str, title: str, url: str) -> str:
    """コンテキストキーを正規化（ノイズ除去・抽象化）"""
    if url:
        try:
            parsed = urlparse(url)
            return f"{app}:{parsed.netloc}"
        except Exception:
            return f"{app}:{url[:50]}"

    if app in _CHAT_APPS:
        return app  # チャンネル名の変化を無視

    if app in _EDITOR_APPS and title:
        # "loop.py — ProjectName" → "ProjectName" (プロジェクト名)
        parts = re.split(r"\s*[—\-|]\s*", title)
        project = parts[-1].strip() if len(parts) > 1 else parts[0].strip()
        # ファイル拡張子があればそれも付ける
        ext_match = re.search(r"\.\w{1,5}\b", title)
        ext = ext_match.group() if ext_match else ""
        return f"{app}:{project}{ext}"

    if title:
        return f"{app}:{title[:60]}"

    return app


# =============================================================================
# Workflow: 学習されたワークフロー
# =============================================================================

class Workflow:
    """再現可能な作業フロー"""

    def __init__(self, name: str, steps: list[dict], frequency: int = 1,
                 time_of_day: str = "", last_seen: str = "",
                 workflow_id: str = ""):
        self.name = name
        self.steps = steps          # [{"app": "Chrome", "url": "...", "title": "..."}, ...]
        self.frequency = frequency
        self.time_of_day = time_of_day  # "morning" / "afternoon" / "evening" / ""
        self.last_seen = last_seen or datetime.now().isoformat()
        self.workflow_id = workflow_id or self._compute_id()

    def _compute_id(self) -> str:
        """ステップ列からユニークIDを生成"""
        key = json.dumps(self.steps, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "steps": self.steps,
            "frequency": self.frequency,
            "time_of_day": self.time_of_day,
            "last_seen": self.last_seen,
            "workflow_id": self.workflow_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Workflow":
        return cls(**d)

    def to_skill_steps(self) -> list[dict]:
        """スキルシステム用のツール呼び出し列に変換"""
        skill_steps = []
        for step in self.steps:
            url = step.get("url", "")
            app = step.get("app", "")
            if url and url.startswith("http"):
                skill_steps.append({"tool": "open_url", "args": {"url": url}})
            elif app:
                skill_steps.append({"tool": "open_app", "args": {"app_name": app}})
        return skill_steps

    def describe(self) -> str:
        """人間向けの説明文"""
        apps = []
        for s in self.steps:
            app = s.get("app", "?")
            url = s.get("url", "")
            if url:
                try:
                    domain = urlparse(url).netloc
                    apps.append(f"{app}({domain})")
                except Exception:
                    apps.append(app)
            else:
                apps.append(app)
        return " → ".join(apps)


# =============================================================================
# ContinuousObserver: 継続観察エンジン
# =============================================================================

class ContinuousObserver:

    def __init__(self, interval_seconds: int = 10):
        self.interval = interval_seconds

        # セッション内データ
        self._context_sequence: list[tuple[str, float, dict]] = []  # (context_key, timestamp, step_dict)
        self._last_context: str = ""
        self._last_screenshot_hash: str = ""
        self._session_start = time.time()

        # 統計（永続化される）
        self.app_usage: Counter = Counter()
        self.time_slots: defaultdict = defaultdict(Counter)

        # ワークフロー（永続化される）
        self.workflows: list[Workflow] = []

        # 読み込み
        self._load_workflows()
        self._load_work_profile()

    # === 永続化 ===

    def _load_workflows(self):
        if WORKFLOWS_FILE.exists():
            try:
                data = json.loads(WORKFLOWS_FILE.read_text(encoding="utf-8"))
                self.workflows = [Workflow.from_dict(w) for w in data]
                logger.info(f"Loaded {len(self.workflows)} learned workflows")
            except Exception as e:
                logger.warning(f"Failed to load workflows: {e}")

    def _save_workflows(self):
        RITSU_DIR.mkdir(parents=True, exist_ok=True)
        data = [w.to_dict() for w in self.workflows]
        WORKFLOWS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_work_profile(self):
        if WORK_PROFILE_FILE.exists():
            try:
                data = json.loads(WORK_PROFILE_FILE.read_text(encoding="utf-8"))
                self.app_usage = Counter(data.get("app_usage", {}))
                self.time_slots = defaultdict(Counter, {
                    k: Counter(v) for k, v in data.get("time_slots", {}).items()
                })
            except Exception:
                pass

    def _save_work_profile(self):
        RITSU_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "app_usage": dict(self.app_usage),
            "time_slots": {k: dict(v) for k, v in self.time_slots.items()},
            "last_updated": datetime.now().isoformat(),
            "total_workflows": len(self.workflows),
        }
        WORK_PROFILE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_activity_log(self, app: str, title: str, url: str):
        """日次アクティビティログに追記（セキュア）"""
        ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            ACTIVITY_LOG_DIR.chmod(0o700)
        except Exception:
            pass

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = ACTIVITY_LOG_DIR / f"{today}.log"

        safe_url = _strip_url_query(url)
        line = f"{datetime.now().strftime('%H:%M:%S')} | {app}"
        if safe_url:
            line += f" | {safe_url[:100]}"
        elif title:
            line += f" | {title[:100]}"
        line += "\n"

        try:
            fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.debug(f"Activity log write failed: {e}")

    # === 観察（3ティア統合） ===

    # Tier 1 スナップショットバッファ（直近30分 = 360個 @ 5秒間隔）
    _snapshot_buffer: list = []
    _MAX_BUFFER = 360

    # Tier 2 Vision結果バッファ
    _vision_results: list[dict] = []
    _vision_count_this_hour: int = 0
    _vision_hour: int = -1
    _VISION_MAX_PER_HOUR = 30

    async def observe_once(self, cycle: int = 0) -> dict | None:
        """Tier 1: フルスナップショット収集（APIコスト$0）

        collectors.pyの全11データソースからスタガード収集。
        コンテキスト変化時だけでなく、毎サイクル記録する。
        """
        try:
            from platform_layer import get_platform
            from agent.observers.collectors import collect_full_snapshot
            from agent.observers.categorizer import categorize_activity

            platform = get_platform()
            snap = await collect_full_snapshot(
                platform, cycle,
                last_content_hash=self._last_context,
                project_path="",
            )

            if not snap.frontmost_app:
                return None

            # アクティビティ分類
            snap.activity_category = categorize_activity(
                snap.frontmost_app, snap.browser_url, snap.window_title,
            )

            # 統計更新（常に）
            hour = str(snap.hour)
            self.app_usage[snap.frontmost_app] += 1
            self.time_slots[hour][snap.frontmost_app] += 1

            # 機密チェック
            if _is_sensitive_context(snap.frontmost_app, snap.window_title, snap.browser_url):
                snap.is_sensitive = True
                return {"app": snap.frontmost_app, "sensitive": True}

            # バッファに追加（常に、コンテキスト変化関係なく）
            self._snapshot_buffer.append(snap)
            if len(self._snapshot_buffer) > self._MAX_BUFFER:
                self._snapshot_buffer = self._snapshot_buffer[-self._MAX_BUFFER:]

            # コンテキスト遷移チェック（ワークフロー検出用）
            ctx_key = _normalize_context_key(snap.frontmost_app, snap.window_title, snap.browser_url)
            if ctx_key != self._last_context:
                self._last_context = ctx_key
                now = time.monotonic()

                step = {"app": snap.frontmost_app}
                if snap.browser_url:
                    step["url"] = _strip_url_query(snap.browser_url)
                if snap.window_title:
                    step["title"] = snap.window_title[:80]
                if snap.editor_project:
                    step["project"] = snap.editor_project

                self._context_sequence.append((ctx_key, now, step))
                self._append_activity_log(
                    snap.frontmost_app, snap.window_title, snap.browser_url,
                )

            # コンテンツ変化（同じアプリ内でのタブ切替等）もログ
            elif snap.content_hash != getattr(self, '_last_content_hash', ''):
                self._append_activity_log(
                    snap.frontmost_app, snap.window_title, snap.browser_url,
                )
            self._last_content_hash = snap.content_hash

            return {
                "app": snap.frontmost_app,
                "title": snap.window_title,
                "url": snap.browser_url,
                "category": snap.activity_category,
                "content_hash": snap.content_hash,
            }

        except Exception as e:
            logger.warning(f"Observation failed: {e}")
            return None

    async def observe_tier2(self) -> dict | None:
        """Tier 2: スクショ → Vision AI → 構造化抽出 → 画像即削除

        レート制限: 30回/時。画面変化なしならスキップ。
        """
        # レート制限チェック
        current_hour = datetime.now().hour
        if current_hour != self._vision_hour:
            self._vision_count_this_hour = 0
            self._vision_hour = current_hour
        if self._vision_count_this_hour >= self._VISION_MAX_PER_HOUR:
            return None

        from platform_layer import get_platform
        platform = get_platform()

        # 機密チェック
        front_app = await platform.get_frontmost_app()
        window = await platform.get_window_info()
        browser = await platform.get_browser_info()
        if _is_sensitive_context(front_app, window.get("title", ""), browser.get("url", "")):
            return None

        OBSERVATION_DIR.mkdir(parents=True, exist_ok=True)
        ss_path = str(OBSERVATION_DIR / f"t2_{int(time.time())}.jpg")

        try:
            captured = await platform.take_screenshot(ss_path)
            if not captured:
                return None

            # MD5変化チェック
            img_bytes = Path(ss_path).read_bytes()
            img_hash = hashlib.md5(img_bytes).hexdigest()
            if img_hash == self._last_screenshot_hash:
                Path(ss_path).unlink(missing_ok=True)
                return None
            self._last_screenshot_hash = img_hash

            # リサイズ（トークン節約+セキュリティ）
            resized = ss_path.replace(".jpg", "_s.jpg")
            ok = await platform.resize_image(ss_path, resized, 512)
            if ok:
                Path(ss_path).unlink(missing_ok=True)
                ss_path = resized

            # 構造化Vision抽出
            result = await self._vision_structured_extract(ss_path)

            # 画像即削除
            Path(ss_path).unlink(missing_ok=True)

            if result:
                self._vision_count_this_hour += 1
                self._vision_results.append(result)
                # 直近20件のみ保持
                if len(self._vision_results) > 20:
                    self._vision_results = self._vision_results[-20:]
                return result

        except Exception as e:
            logger.warning(f"Tier 2 observation failed: {e}")
            Path(ss_path).unlink(missing_ok=True)

        return None

    async def _vision_structured_extract(self, screenshot_path: str) -> dict | None:
        """スクショから構造化データを抽出"""
        if not GEMINI_API_KEY:
            return None

        try:
            import google.genai as genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            img_bytes = Path(screenshot_path).read_bytes()

            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                        genai.types.Part(text=(
                            "Analyze this macOS screenshot. Extract structured info as JSON:\n"
                            '{"activity":"what user is doing (1 sentence)",'
                            '"app_context":"specific feature/panel being used",'
                            '"content_topic":"subject/topic visible on screen",'
                            '"code_context":"if coding: language, file, function. else null",'
                            '"communication_context":"if chatting: platform, person (NOT message content). else null",'
                            '"actionable_items":"visible TODOs, deadlines, notifications. else null"}\n'
                            "Rules: NEVER include passwords, API keys, or message content. "
                            "Output ONLY valid JSON. Japanese for activity description."
                        )),
                    ],
                    config=genai.types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=250,
                    ),
                ),
                timeout=15,
            )

            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)
            result["_ts"] = datetime.now().isoformat()
            logger.info(f"Tier 2 vision: {result.get('activity', '')[:50]}")
            return result

        except Exception as e:
            logger.debug(f"Vision extract failed: {e}")
            return None

    async def run_tier3_analysis(self) -> dict | None:
        """Tier 3: 蓄積データのAI深層分析（30分ごと）"""
        if not self._snapshot_buffer:
            return None

        try:
            from agent.observers.analyzer import (
                build_analysis_payload, run_analysis, persist_insights,
            )

            payload = build_analysis_payload(
                snapshots=self._snapshot_buffer,
                vision_results=self._vision_results,
                app_usage=self.app_usage,
                period_minutes=len(self._snapshot_buffer) * self.interval / 60,
            )

            if not payload:
                return None

            analysis = await run_analysis(payload)
            if analysis:
                persist_insights(analysis)
                return analysis

        except Exception as e:
            logger.warning(f"Tier 3 analysis failed: {e}")

        return None

    async def observe_with_screenshot(self) -> dict | None:
        """スクショ付き詳細観察（オプション、アプリ切替時のみ）

        セキュリティ:
        - 機密コンテキストではスキップ
        - スクショはVision処理後に即削除
        - 画面変化なし（MD5同一）ならスキップ
        """
        from platform_layer import get_platform
        platform = get_platform()

        front_app = await platform.get_frontmost_app()
        if not front_app:
            return None

        window_info = await platform.get_window_info()
        title = window_info.get("title", "")
        browser_info = await platform.get_browser_info()
        url = browser_info.get("url", "")

        if _is_sensitive_context(front_app, title, url):
            return None

        OBSERVATION_DIR.mkdir(parents=True, exist_ok=True)
        ss_path = str(OBSERVATION_DIR / f"obs_{int(time.time())}.jpg")

        try:
            captured = await platform.take_screenshot(ss_path)
            if not captured:
                return None

            # MD5で画面変化チェック
            img_bytes = Path(ss_path).read_bytes()
            img_hash = hashlib.md5(img_bytes).hexdigest()
            if img_hash == self._last_screenshot_hash:
                Path(ss_path).unlink(missing_ok=True)
                return None
            self._last_screenshot_hash = img_hash

            # リサイズ（トークン節約 + 解像度下げてセキュリティ向上）
            resized = ss_path.replace(".jpg", "_small.jpg")
            ok = await platform.resize_image(ss_path, resized, 512)
            if ok:
                Path(ss_path).unlink(missing_ok=True)
                ss_path = resized

            # Vision AIで作業内容を認識
            activity = await self._recognize_activity(ss_path)

            # スクショは即削除（テキストだけ残す）
            Path(ss_path).unlink(missing_ok=True)

            if activity:
                return {"app": front_app, "title": title, "activity": activity}

        except Exception as e:
            logger.warning(f"Screenshot observation failed: {e}")
            # エラーでもスクショは必ず消す
            Path(ss_path).unlink(missing_ok=True)

        return None

    async def _recognize_activity(self, screenshot_path: str) -> str | None:
        """スクショからユーザーの作業内容をAIで1行認識"""
        if not GEMINI_API_KEY:
            return None

        try:
            import google.genai as genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            img_bytes = Path(screenshot_path).read_bytes()

            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                        genai.types.Part(text=(
                            "この画面のスクリーンショットから、ユーザーが何の作業をしているか"
                            "を1文で簡潔に説明してください。アプリ名、作業内容、"
                            "開いているファイルやURL等の情報を含めてください。"
                            "機密情報（パスワード、APIキー等）は絶対に含めないでください。"
                            "日本語で回答してください。"
                        )),
                    ],
                    config=genai.types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=80,
                    ),
                ),
                timeout=15,
            )

            if response and response.text:
                return response.text.strip()

        except Exception as e:
            logger.debug(f"Activity recognition failed: {e}")

        return None

    # === ワークフロー検出 ===

    def detect_workflows(self) -> list[Workflow]:
        """コンテキスト遷移列からワークフローを検出（n-gram + 時間近接フィルタ）"""
        if len(self._context_sequence) < 6:
            return []

        new_workflows = []

        # 時間近接フィルタ: 30分以内に完結した遷移のみ対象
        MAX_FLOW_SECONDS = 30 * 60

        # 3〜6ステップのn-gramを生成
        all_candidates: list[tuple[tuple, int, list[dict]]] = []  # (gram_keys, count, steps)

        for n in range(3, min(7, len(self._context_sequence))):
            ngram_counts: Counter = Counter()
            ngram_steps: dict[tuple, list[dict]] = {}

            for i in range(len(self._context_sequence) - n + 1):
                window = self._context_sequence[i:i + n]
                # 時間近接チェック
                time_span = window[-1][1] - window[0][1]
                if time_span > MAX_FLOW_SECONDS:
                    continue

                gram = tuple(entry[0] for entry in window)
                ngram_counts[gram] += 1

                if gram not in ngram_steps:
                    ngram_steps[gram] = [entry[2] for entry in window]

            for gram, count in ngram_counts.most_common(20):
                if count >= 3:
                    all_candidates.append((gram, count, ngram_steps[gram]))

        # サブシーケンス抑制: 長いn-gramに含まれる短いn-gramを除去
        suppressed = set()
        sorted_candidates = sorted(all_candidates, key=lambda x: len(x[0]), reverse=True)
        for i, (gram_a, count_a, _) in enumerate(sorted_candidates):
            for j, (gram_b, count_b, _) in enumerate(sorted_candidates):
                if i != j and len(gram_b) < len(gram_a) and count_b <= count_a:
                    # gram_bがgram_aの部分列かチェック
                    a_str = " ".join(gram_a)
                    b_str = " ".join(gram_b)
                    if b_str in a_str:
                        suppressed.add(gram_b)

        # ワークフロー登録
        for gram, count, steps in all_candidates:
            if gram in suppressed:
                continue

            wf_id = hashlib.md5(json.dumps(steps, sort_keys=True).encode()).hexdigest()[:12]

            # 既存ワークフローとの重複チェック
            existing = next((w for w in self.workflows if w.workflow_id == wf_id), None)
            if existing:
                existing.frequency += count
                existing.last_seen = datetime.now().isoformat()
                continue

            time_of_day = self._estimate_time_of_day()
            apps = [s.get("app", "?") for s in steps]
            name = " → ".join(apps)

            wf = Workflow(
                name=name,
                steps=steps,
                frequency=count,
                time_of_day=time_of_day,
                workflow_id=wf_id,
            )
            new_workflows.append(wf)
            self.workflows.append(wf)
            logger.info(f"Workflow detected: {name} (freq={count}, time={time_of_day})")

        if new_workflows:
            self._save_workflows()
            self._save_work_profile()
            self._promote_workflows_to_skills()

        return new_workflows

    @staticmethod
    def _estimate_time_of_day() -> str:
        hour = datetime.now().hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 22:
            return "evening"
        return "night"

    # === スキル変換 ===

    def _promote_workflows_to_skills(self):
        """頻出ワークフローをスキルに自動変換"""
        from agent.skills import save_learned_skill, _load_learned_skills

        existing_skills = _load_learned_skills()
        promoted = 0

        for wf in self.workflows:
            if wf.frequency < 3 or len(wf.steps) < 2:
                continue

            skill_id = f"wf_{wf.workflow_id}"
            if skill_id in existing_skills:
                continue

            steps = wf.to_skill_steps()
            if not steps:
                continue

            # トリガー生成（時間帯別 + アプリ名ベース）
            first_app = wf.steps[0].get("app", "")
            triggers = [f"いつもの{first_app}"]

            if wf.time_of_day == "morning":
                triggers.extend(["朝のルーティン", "朝いつもの", "モーニングルーティン"])
            elif wf.time_of_day == "afternoon":
                triggers.append("午後のルーティン")
            elif wf.time_of_day == "evening":
                triggers.extend(["夜のルーティン", "夜いつもの"])

            save_learned_skill(
                skill_id=skill_id,
                triggers=triggers,
                steps=steps,
                response=f"いつものフローを再現するよ: {wf.describe()}",
            )
            promoted += 1
            logger.info(f"Workflow → skill: {wf.name} (freq={wf.frequency})")

        if promoted:
            logger.info(f"Promoted {promoted} workflows to skills")

    # === 「いつも通りやって」===

    def get_usual_workflow(self) -> Workflow | None:
        """現在の時間帯に合うワークフローを返す"""
        if not self.workflows:
            return None

        current_tod = self._estimate_time_of_day()

        # 1. 同じ時間帯 + 頻度3以上
        candidates = [
            w for w in self.workflows
            if w.time_of_day == current_tod and w.frequency >= 3
        ]
        # 2. フォールバック: 時間帯問わず頻度3以上
        if not candidates:
            candidates = [w for w in self.workflows if w.frequency >= 3]
        # 3. さらにフォールバック: 何でもいいから一番頻度高いやつ
        if not candidates:
            candidates = sorted(self.workflows, key=lambda w: w.frequency, reverse=True)

        if not candidates:
            return None
        return max(candidates, key=lambda w: w.frequency)

    # === ワークフロー管理 ===

    def list_workflows(self) -> list[dict]:
        """学習済みワークフロー一覧"""
        return [
            {
                "id": wf.workflow_id,
                "name": wf.name,
                "description": wf.describe(),
                "frequency": wf.frequency,
                "time_of_day": wf.time_of_day,
                "steps": len(wf.steps),
                "last_seen": wf.last_seen,
            }
            for wf in sorted(self.workflows, key=lambda w: w.frequency, reverse=True)
        ]

    def delete_workflow(self, workflow_id: str) -> bool:
        """ワークフローを削除"""
        before = len(self.workflows)
        self.workflows = [w for w in self.workflows if w.workflow_id != workflow_id]
        if len(self.workflows) < before:
            self._save_workflows()
            logger.info(f"Workflow deleted: {workflow_id}")
            return True
        return False

    # === コンテキスト注入 ===

    def get_context_injection(self) -> str:
        """システムプロンプトに注入する作業コンテキスト"""
        lines = []

        # 今の時間帯のよく使うアプリ
        current_hour = str(datetime.now().hour)
        typical_now = self.time_slots.get(current_hour, Counter()).most_common(3)
        if typical_now:
            now_str = ", ".join(app for app, _ in typical_now)
            lines.append(f"- この時間帯によく使うアプリ: {now_str}")

        # 学習済みワークフロー
        if self.workflows:
            top = sorted(self.workflows, key=lambda w: w.frequency, reverse=True)[:3]
            lines.append(f"- 学習済みワークフロー: {len(self.workflows)}個")
            for wf in top:
                lines.append(f"  - {wf.describe()} ({wf.frequency}回, {wf.time_of_day})")

        # 直前の作業
        if self._context_sequence:
            last_ctx, _, last_step = self._context_sequence[-1]
            app = last_step.get("app", "")
            url = last_step.get("url", "")
            title = last_step.get("title", "")
            lines.append(f"- 直前の作業: {app}")
            if url:
                lines.append(f"  URL: {url[:60]}")
            elif title:
                lines.append(f"  ウィンドウ: {title[:60]}")

        if not lines:
            return ""
        return "# ユーザーの作業パターン（バックグラウンド学習）\n" + "\n".join(lines)

    # === プロアクティブアシスタント（先回り行動） ===

    async def analyze_and_suggest(self, push_fn=None) -> str | None:
        """現在の作業コンテキストを分析して、先回り提案を生成

        呼ばれるタイミング: アプリ切替時、または一定間隔
        """
        if not self._context_sequence:
            return None

        _, _, last_step = self._context_sequence[-1]
        app = last_step.get("app", "")
        title = last_step.get("title", "")
        url = last_step.get("url", "")

        # 機密コンテキストでは提案しない
        if _is_sensitive_context(app, title, url):
            return None

        # コンテキストに応じた先回り行動ルール
        suggestion = await self._context_based_suggestion(app, title, url)

        if suggestion and push_fn:
            try:
                from config import DISCORD_OWNER_ID
                await push_fn(str(DISCORD_OWNER_ID), suggestion)
                logger.info(f"Proactive suggestion sent: {suggestion[:50]}")
            except Exception as e:
                logger.debug(f"Proactive push failed: {e}")

        return suggestion

    async def _context_based_suggestion(self, app: str, title: str, url: str) -> str | None:
        """コンテキストに基づいた提案を生成（ルールベース + AI）"""
        import google.genai as genai

        # 直近の作業時間を計算
        if len(self._context_sequence) >= 2:
            _, first_ts, _ = self._context_sequence[-min(10, len(self._context_sequence))]
            _, last_ts, _ = self._context_sequence[-1]
            work_minutes = (last_ts - first_ts) / 60
        else:
            work_minutes = 0

        # ルールベースの即時提案（APIコスト$0）
        rule_suggestion = self._rule_based_suggestion(app, title, url, work_minutes)
        if rule_suggestion:
            return rule_suggestion

        # AI提案は5分に1回まで（コスト抑制）
        now = time.time()
        if hasattr(self, '_last_ai_suggest') and now - self._last_ai_suggest < 300:
            return None
        self._last_ai_suggest = now

        # 直近のコンテキスト列を整理
        recent = self._context_sequence[-10:] if len(self._context_sequence) > 10 else self._context_sequence
        context_text = "\n".join(
            f"- {step.get('app', '?')}: {step.get('title', step.get('url', ''))[:60]}"
            for _, _, step in recent
        )

        try:
            import user_config
            owner = user_config.get_display_name()
        except Exception:
            owner = "ユーザー"

        # カレンダーの次の予定も取得（あれば）
        next_event_text = ""
        try:
            from agent.scheduler import _fetch_upcoming_events
            events = await _fetch_upcoming_events(minutes_ahead=60)
            if events:
                for ev in events[:1]:
                    summary = ev.get("summary", "")
                    start = ev.get("start", {})
                    start_time = start.get("dateTime", "") if isinstance(start, dict) else ""
                    if summary:
                        next_event_text = f"\n次の予定: {summary}（{start_time}）"
        except Exception:
            pass

        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            prompt = f"""{owner}のAI秘書として、{owner}の作業を観察して、本当に役立つ提案を1つだけ送る。

現在の作業:
- アプリ: {app}
- タイトル: {title[:80]}
- URL: {url[:80] if url else 'なし'}
- 作業時間: {work_minutes:.0f}分{next_event_text}

直近の作業コンテキスト:
{context_text}

ルール:
- 本当に役立つ提案がある時だけ返す。なければ「なし」とだけ返す
- 絵文字は使わない
- 1-2文で簡潔に
- 次の予定が近い場合はそれに言及する
- 作業の流れを邪魔しない。提案であって命令ではない
- 例: 「30分後にミーティングあるよ。そろそろ準備した方がいいかも」
- 例: 「2時間連続でコーディングしてるね。そろそろ休憩した方がいいかも」
- 例: 「案件ページ見てるね。応募文のテンプレート作っとこうか？」"""

            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0.7,
                        max_output_tokens=100,
                    ),
                ),
                timeout=10,
            )

            text = (response.text or "").strip()
            if text and text != "なし" and len(text) > 5:
                return text

        except Exception as e:
            logger.debug(f"AI suggestion failed: {e}")

        return None

    # 同じ提案を繰り返さないためのクールダウン（提案キー → 最終提案時刻）
    _suggestion_cooldowns: dict[str, float] = {}
    _SUGGESTION_COOLDOWN_SEC = 30 * 60  # 同じ種類の提案は30分空ける

    def _check_cooldown(self, suggestion_key: str) -> bool:
        """クールダウン中ならTrue（提案しない）"""
        last = self._suggestion_cooldowns.get(suggestion_key, 0)
        if time.time() - last < self._SUGGESTION_COOLDOWN_SEC:
            return True
        return False

    def _mark_suggested(self, suggestion_key: str):
        """提案済みとしてマーク"""
        self._suggestion_cooldowns[suggestion_key] = time.time()
        # 古いエントリを削除（メモリリーク防止）
        if len(self._suggestion_cooldowns) > 50:
            oldest = sorted(self._suggestion_cooldowns.items(), key=lambda x: x[1])
            for k, _ in oldest[:20]:
                del self._suggestion_cooldowns[k]

    def _rule_based_suggestion(self, app: str, title: str, url: str, work_minutes: float) -> str | None:
        """ルールベースの即時提案（APIコスト$0、クールダウン付き）"""
        title_lower = title.lower()
        url_lower = url.lower()
        app_lower = app.lower()

        # --- 健康・集中 ---

        # 長時間作業の休憩提案（90分以上）
        if work_minutes >= 90 and not self._check_cooldown("break"):
            self._mark_suggested("break")
            return f"{work_minutes:.0f}分連続で作業してるね。そろそろ休憩した方がいいかも。"

        # --- ミーティング ---

        # ミーティングツールを開いた → 議事録準備
        meet_urls = ("meet.google.com", "zoom.us", "teams.microsoft.com")
        meet_apps = ("zoom.us", "Microsoft Teams", "FaceTime")
        if (any(u in url_lower for u in meet_urls) or app in meet_apps) and not self._check_cooldown("meeting"):
            self._mark_suggested("meeting")
            return "ミーティング始まりそうだね。議事録テンプレート作っとこうか？"

        # --- フリーランス・案件 ---

        # CrowdWorks/ランサーズの案件ページ
        if ("crowdworks.jp" in url_lower or "lancers.jp" in url_lower) and not self._check_cooldown("freelance"):
            if any(kw in url_lower for kw in ("job", "detail", "search")) or "案件" in title_lower:
                self._mark_suggested("freelance")
                return "案件見てるね。気になるのあったら応募文作るの手伝うよ。"

        # --- 開発 ---

        # エラーページ/Stack Overflow → デバッグ支援
        if ("stackoverflow.com" in url_lower or "github.com" in url_lower and "issues" in url_lower) and not self._check_cooldown("debug"):
            self._mark_suggested("debug")
            return "エラー調べてる？コード見せてくれたらデバッグ手伝うよ。"

        # GitHub PR → レビュー支援
        if "github.com" in url_lower and "pull" in url_lower and not self._check_cooldown("pr_review"):
            self._mark_suggested("pr_review")
            return "PR見てるね。レビューポイントまとめようか？"

        # IDE/エディタで長時間 → コーディング支援
        if app_lower in ("cursor", "visual studio code", "xcode", "pycharm") and work_minutes >= 45 and not self._check_cooldown("coding"):
            self._mark_suggested("coding")
            return "コーディング中だね。テスト書いたりリファクタリングの相談あればいつでも。"

        # --- ビジネス ---

        # Notion → タスク管理支援
        if "notion.so" in url_lower and not self._check_cooldown("notion"):
            self._mark_suggested("notion")
            return "Notion開いてるね。タスクの整理とか優先度の見直し、手伝おうか？"

        # Google Docs/Slides → ドキュメント支援
        if ("docs.google.com" in url_lower or "slides.google.com" in url_lower) and not self._check_cooldown("docs"):
            self._mark_suggested("docs")
            return "ドキュメント作ってるね。構成の相談とか校正とか手伝えるよ。"

        # Figma/デザインツール → デザインフィードバック
        if ("figma.com" in url_lower or app_lower == "figma") and not self._check_cooldown("design"):
            self._mark_suggested("design")
            return "デザイン作業中だね。UIの改善点とかフィードバックほしかったら言って。"

        # Canva → デザイン支援
        if "canva.com" in url_lower and not self._check_cooldown("canva"):
            self._mark_suggested("canva")
            return "Canva使ってるね。コピーライティングとか色の提案とか手伝えるよ。"

        # --- メール ---

        # Gmail → メール処理支援
        if "mail.google.com" in url_lower and not self._check_cooldown("email"):
            self._mark_suggested("email")
            return "メール確認してるね。返信の下書き作ったり、重要なメールのピックアップ手伝おうか？"

        # --- 学習・リサーチ ---

        # YouTube → 学習中かも
        if "youtube.com" in url_lower and "/watch" in url_lower and work_minutes >= 20 and not self._check_cooldown("youtube_learn"):
            self._mark_suggested("youtube_learn")
            return "動画見てるね。内容のメモとかまとめが必要だったら言って。"

        return None

    def get_work_summary(self) -> dict:
        """作業プロファイルのサマリー"""
        top_apps = self.app_usage.most_common(5)
        return {
            "top_apps": [{"app": app, "count": count} for app, count in top_apps],
            "total_observations": len(self._context_sequence),
            "workflows_learned": len(self.workflows),
        }

    # === ログ管理 ===

    def cleanup_old_data(self, max_log_days: int = 30, max_screenshot_hours: int = 1):
        """古いログとスクショを削除"""
        now_ts = datetime.now().timestamp()
        # アクティビティログ
        if ACTIVITY_LOG_DIR.exists():
            for f in ACTIVITY_LOG_DIR.glob("*.log"):
                if now_ts - f.stat().st_mtime > max_log_days * 86400:
                    f.unlink(missing_ok=True)
        # スクショ（Vision用一時ファイル）
        if OBSERVATION_DIR.exists():
            for f in OBSERVATION_DIR.glob("*.jpg"):
                if now_ts - f.stat().st_mtime > max_screenshot_hours * 3600:
                    f.unlink(missing_ok=True)

    def flush(self):
        """セッションデータを永続化 + メモリ管理"""
        self._save_workflows()
        self._save_work_profile()
        if len(self._context_sequence) > 500:
            self._context_sequence = self._context_sequence[-200:]


# =============================================================================
# Notion連携: 観察ステータスページ
# =============================================================================

_notion_status_page_id: str | None = None

# 既知の観察モニターページID（検索コスト削減）
_KNOWN_MONITOR_PAGE_ID = "329a57aa-8f28-8169-98b9-ed879502bb78"


async def _find_or_create_notion_status() -> str | None:
    """Notionに「観察ステータス」ページを探す or 作る。ページIDを返す。"""
    global _notion_status_page_id
    if _notion_status_page_id:
        return _notion_status_page_id

    # 既知のページIDを先に試す
    try:
        from tools.notion import get_project
        result = await get_project(_KNOWN_MONITOR_PAGE_ID)
        if result.get("success"):
            _notion_status_page_id = _KNOWN_MONITOR_PAGE_ID
            return _notion_status_page_id
    except Exception:
        pass

    # フォールバック: 検索
    try:
        from tools.notion import list_projects, create_project
        result = await list_projects()
        if not result.get("success"):
            return None

        for p in result["projects"]:
            name = p.get("プロジェクト名", "")
            if "観察" in name and "モニター" in name:
                _notion_status_page_id = p["id"]
                return _notion_status_page_id

        # なければ作成
        new_proj = await create_project(
            name="識ちゃん観察モニター",
            category="プロダクト",
            status="進行中",
            memo="バックグラウンド観察・学習システムのステータス。ステータスを「停止」にすると観察を停止、「進行中」で再開。",
        )
        if new_proj.get("success"):
            _notion_status_page_id = new_proj.get("id")
            logger.info(f"Created Notion observation status page: {_notion_status_page_id}")
            return _notion_status_page_id

    except Exception as e:
        logger.warning(f"Notion status page setup failed: {e}")
    return None


async def _update_notion_status(observer: "ContinuousObserver", running: bool):
    """Notionの観察ステータスを更新"""
    page_id = await _find_or_create_notion_status()
    if not page_id:
        return

    try:
        from tools.notion import add_comment

        obs_count = len(observer._context_sequence)
        wf_count = len(observer.workflows)
        top_apps = observer.app_usage.most_common(3)
        apps_str = ", ".join(app for app, _ in top_apps) if top_apps else "なし"

        status = "記録中" if running else "停止中"
        now = datetime.now().strftime("%H:%M")

        text = (
            f"[{now}] {status} | "
            f"観察: {obs_count}件 | "
            f"ワークフロー: {wf_count}個学習済み | "
            f"よく使うアプリ: {apps_str}"
        )

        await add_comment(page_id, text)

    except Exception as e:
        logger.debug(f"Notion status update failed: {e}")


async def _check_notion_toggle() -> bool | None:
    """Notionのステータスを確認して、観察のオンオフを判定

    Returns:
        True = 続行, False = 停止, None = 判定不能（現状維持）
    """
    page_id = await _find_or_create_notion_status()
    if not page_id:
        return None

    try:
        from tools.notion import get_project
        result = await get_project(page_id)
        if not result.get("success"):
            return None

        status = result["project"].get("ステータス", "")
        if status in ("停止", "保留", "完了"):
            return False
        return True

    except Exception:
        return None


# =============================================================================
# グローバルインスタンス + ループ管理
# =============================================================================

_observer: ContinuousObserver | None = None


def get_observer() -> ContinuousObserver:
    global _observer
    if _observer is None:
        try:
            import user_config
            interval = user_config.get("observation.interval_seconds", 10)
        except Exception:
            interval = 10
        _observer = ContinuousObserver(interval_seconds=interval)
    return _observer


async def start_observation_loop(push_callback=None) -> asyncio.Task | None:
    """観察ループを開始（バックグラウンド）"""
    try:
        import user_config
        if not user_config.get("observation.enabled", False):
            logger.info("Observation disabled in user_config")
            return None
    except Exception:
        return None

    global _observer_running, _observation_task
    if _observer_running:
        return _observation_task

    observer = get_observer()
    _observer_running = True

    # Vision有効かどうか
    try:
        import user_config as _uc
        vision_enabled = _uc.get("observation.vision_enabled", False)
    except Exception:
        vision_enabled = False

    # Tier 3有効かどうか
    try:
        import user_config as _uc2
        tier3_enabled = _uc2.get("observation.tier3_enabled", True)
    except Exception:
        tier3_enabled = True

    async def _loop():
        global _observer_running
        cycle = 0
        last_app = ""
        last_content_hash = ""
        paused_by_notion = False

        # 起動時にNotionにステータス報告
        await _update_notion_status(observer, running=True)

        while _observer_running:
            try:
                # === Notionトグルチェック（100サイクル≈17分ごと） ===
                if cycle % 100 == 0 and cycle > 0:
                    toggle = await _check_notion_toggle()
                    if toggle is False and not paused_by_notion:
                        paused_by_notion = True
                        logger.info("Observation paused by Notion toggle")
                        await _update_notion_status(observer, running=False)
                        if push_callback:
                            try:
                                await push_callback("観察を一時停止したよ（Notionから停止指示）。再開するにはNotionのステータスを「進行中」に戻してね。")
                            except Exception:
                                pass
                    elif toggle is True and paused_by_notion:
                        paused_by_notion = False
                        logger.info("Observation resumed by Notion toggle")
                        await _update_notion_status(observer, running=True)
                        if push_callback:
                            try:
                                await push_callback("観察を再開したよ！")
                            except Exception:
                                pass

                # 一時停止中はスリープだけ
                if paused_by_notion:
                    await asyncio.sleep(observer.interval)
                    cycle += 1
                    continue

                # === Tier 1: フルスナップショット収集 ===
                result = await observer.observe_once(cycle)
                cycle += 1

                if result and not result.get("sensitive"):
                    current_app = result.get("app", "")
                    content_hash = result.get("content_hash", "")

                    # === Tier 2: Vision（アプリ切替 or コンテンツ変化時） ===
                    app_switched = current_app and current_app != last_app
                    content_changed = content_hash and content_hash != last_content_hash

                    if vision_enabled and (app_switched or (content_changed and cycle % 6 == 0)):
                        try:
                            await observer.observe_tier2()
                        except Exception as t2_err:
                            logger.debug(f"Tier 2 error: {t2_err}")

                    # 先回り提案（アプリ切替時）
                    if app_switched and push_callback:
                        try:
                            suggestion = await observer._context_based_suggestion(
                                current_app,
                                result.get("title", ""),
                                result.get("url", ""),
                            )
                            if suggestion:
                                await push_callback(suggestion)
                        except Exception as _proactive_err:
                            logger.debug(f"Proactive suggestion error: {_proactive_err}")

                    last_app = current_app
                    last_content_hash = content_hash

                # === ワークフロー検出（50サイクル≈4分@5秒） ===
                if cycle % 50 == 0:
                    new_wfs = observer.detect_workflows()
                    if new_wfs and push_callback:
                        for wf in new_wfs[:2]:
                            try:
                                await push_callback(
                                    f"新しい作業パターンを覚えたよ: {wf.describe()}\n"
                                    f"（{wf.frequency}回観測、{wf.time_of_day}）"
                                )
                            except Exception:
                                pass

                # === Tier 3: AI深層分析（360サイクル≈30分@5秒） ===
                if tier3_enabled and cycle % 360 == 0 and cycle > 0:
                    try:
                        analysis = await observer.run_tier3_analysis()
                        if analysis and push_callback:
                            # 高信頼度の提案をDiscordに送信
                            suggestions = analysis.get("suggestions", [])
                            for s in suggestions[:2]:
                                if s.get("confidence", 0) >= 0.7:
                                    try:
                                        await push_callback(s["message"])
                                    except Exception:
                                        pass
                            # 作業モード通知（deep_work以外の時）
                            mode = analysis.get("work_mode", "")
                            score = analysis.get("focus_score", 0)
                            if mode == "context_switching" and score < 40:
                                try:
                                    await push_callback(
                                        f"コンテキストスイッチ多めだね（集中スコア: {score}/100）。"
                                        f"1つのタスクに絞ってみる？"
                                    )
                                except Exception:
                                    pass
                    except Exception as t3_err:
                        logger.warning(f"Tier 3 error: {t3_err}")

                # === データ永続化 + Notionステータス（300サイクル≈25分） ===
                if cycle % 300 == 0:
                    observer.flush()
                    observer.cleanup_old_data()
                    await _update_notion_status(observer, running=True)

                await asyncio.sleep(observer.interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Observation loop error: {e}")
                await asyncio.sleep(60)

        # 終了時にNotionにステータス報告
        await _update_notion_status(observer, running=False)
        _observer_running = False

    _observation_task = asyncio.create_task(_loop())
    mode = "アプリ情報 + Vision(アプリ切替時)" if vision_enabled else "アプリ情報のみ"
    logger.info(f"Observation loop started (interval: {observer.interval}s, mode: {mode})")
    return _observation_task


async def stop_observation():
    """観察ループを停止"""
    global _observer_running, _observation_task
    _observer_running = False
    if _observation_task:
        _observation_task.cancel()
        _observation_task = None

    observer = get_observer()
    observer.detect_workflows()
    observer.flush()
    await _update_notion_status(observer, running=False)
    logger.info("Observation stopped, data saved")

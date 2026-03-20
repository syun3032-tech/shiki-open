"""Skills（レシピ）システム

よく使う操作パターンを記憶し、次回から即座に実行。
成功した操作を学習し、同じリクエストには最速で対応する。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("shiki.agent")

SKILLS_FILE = Path(__file__).parent.parent / ".ritsu" / "skills.json"

# 組み込みスキル（必ず存在する）
BUILTIN_SKILLS = {
    "claude_code": {
        "triggers": ["claude code立ち上げ", "claude code起動", "claudecode", "claude code開"],
        "description": "ターミナルでClaude Codeを起動",
        "steps": [
            {"tool": "open_app", "args": {"app_name": "Terminal"}},
            {"tool": "type_text", "args": {"text": "claude"}},
            {"tool": "press_key", "args": {"key": "return"}},
        ],
        "response": "ターミナルでClaude Codeを起動したよ。",
    },
    "google_search": {
        "triggers": ["調べて", "検索して", "ググって"],
        "description": "Google検索",
        "steps": [
            {"tool": "open_url", "args_template": {"url": "https://www.google.com/search?q={query}"}},
        ],
        "response": "検索したよ。",
        "extract_query": True,
    },
    "screenshot": {
        "triggers": ["画面見せて", "画面見て", "スクショ", "スクリーンショット"],
        "description": "画面のスクリーンショットを撮る",
        "steps": [
            {"tool": "take_screenshot", "args": {}},
        ],
        "response": "今の画面だよ。",
    },
    "volume_mute": {
        "triggers": ["ミュート", "音消して", "音量0"],
        "description": "音をミュートする",
        "steps": [
            {"tool": "set_volume", "args": {"level": 0}},
        ],
        "response": "ミュートにしたよ。",
    },
    "dark_mode": {
        "triggers": ["ダークモード", "暗くして", "明るくして", "ライトモード"],
        "description": "ダークモード切替",
        "steps": [
            {"tool": "toggle_dark_mode", "args": {}},
        ],
        "response": "切り替えたよ。",
    },
    "open_chrome": {
        "triggers": ["chrome開いて", "ブラウザ開いて", "クローム開いて"],
        "description": "Google Chromeを起動",
        "steps": [
            {"tool": "open_app", "args": {"app_name": "Google Chrome"}},
        ],
        "response": "Chrome開いたよ。",
    },
    "open_finder": {
        "triggers": ["finder開いて", "ファインダー開いて"],
        "description": "Finderを起動",
        "steps": [
            {"tool": "open_app", "args": {"app_name": "Finder"}},
        ],
        "response": "Finder開いたよ。",
    },
    "open_cursor": {
        "triggers": ["cursor開いて", "カーソル開いて", "エディタ開いて"],
        "description": "Cursorを起動",
        "steps": [
            {"tool": "open_app", "args": {"app_name": "Cursor"}},
        ],
        "response": "Cursor開いたよ。",
    },
    "git_status": {
        "triggers": ["git status", "gitの状態"],
        "description": "gitステータス確認",
        "steps": [
            {"tool": "run_command", "args": {"command": "git status"}},
        ],
        "response": "gitの状態を確認したよ。",
    },
    "volume_max": {
        "triggers": ["音量最大", "音量max", "音量マックス"],
        "description": "音量を最大にする",
        "steps": [
            {"tool": "set_volume", "args": {"level": 100}},
        ],
        "response": "音量最大にしたよ。",
    },
    "volume_half": {
        "triggers": ["音量半分", "音量50"],
        "description": "音量を半分にする",
        "steps": [
            {"tool": "set_volume", "args": {"level": 50}},
        ],
        "response": "音量50%にしたよ。",
    },
    "scroll_down_big": {
        "triggers": ["下までスクロール", "下まで見て", "もっと下"],
        "description": "大きくスクロール",
        "steps": [
            {"tool": "scroll", "args": {"direction": "down", "amount": 40}},
        ],
        "response": "スクロールしたよ。",
    },
    "desktop_files": {
        "triggers": ["デスクトップのファイル", "デスクトップ見て", "デスクトップ一覧"],
        "description": "デスクトップのファイル一覧",
        "steps": [
            {"tool": "list_directory", "args": {"path": str(Path.home() / "Desktop")}},
        ],
        "response": "デスクトップのファイル一覧だよ。",
    },
    "list_reminders": {
        "triggers": ["リマインダー一覧", "リマインダー確認", "リマインダーリスト"],
        "description": "リマインダー一覧表示",
        "steps": [
            {"tool": "list_reminders", "args": {}},
        ],
        "response": "リマインダー一覧だよ。",
    },
}


def _load_learned_skills() -> dict:
    """学習済みスキルをロード"""
    if SKILLS_FILE.exists():
        try:
            return json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_learned_skills(skills: dict):
    """学習済みスキルを保存"""
    try:
        SKILLS_FILE.write_text(
            json.dumps(skills, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Skills save failed: {e}")


def find_skill(user_message: str) -> dict | None:
    """ユーザーメッセージにマッチするスキルを検索。

    重要: 複数の指示を含むメッセージではスキルマッチしない。
    「カーソルで新しいウィンドウ開けて、ターミナル立ち上げて、Claude Code起動して」
    のような複合指示はPlan-and-Executeに回す。
    """
    msg_lower = user_message.lower().replace(" ", "").replace("　", "")

    # === 「いつも通りやって」特殊ハンドリング ===
    # 観察学習で覚えたワークフローを時間帯考慮で再現
    _usual_triggers = ["いつも通り", "いつもの", "いつものやつ", "ルーティン", "ルーチン"]
    if any(t in msg_lower for t in _usual_triggers):
        try:
            from agent.continuous_observer import get_observer
            wf = get_observer().get_usual_workflow()
            if wf:
                steps = wf.to_skill_steps()
                if steps:
                    logger.info(f"Usual workflow matched: {wf.name} (time={wf.time_of_day})")
                    return {
                        "triggers": _usual_triggers,
                        "description": f"いつものフロー: {wf.describe()}",
                        "steps": steps,
                        "response": f"いつものフローを再現するよ: {wf.describe()}",
                    }
        except Exception as e:
            logger.debug(f"Usual workflow lookup failed: {e}")

    # 複合指示の検出 — 複数の動詞/指示が含まれる場合はスキルマッチしない
    action_markers = ["して", "開いて", "起動", "作って", "立ち上げ", "見て", "変えて", "消して", "閉じて", "やって"]
    action_count = sum(1 for marker in action_markers if marker in msg_lower)
    if action_count >= 2:
        logger.info(f"Skill skip: complex message ({action_count} actions detected)")
        return None

    # メッセージが長すぎる場合もスキップ（複合指示の可能性が高い）
    if len(msg_lower) > 40:
        # ただし検索系は長くてもOK（「〇〇について調べて」）
        is_search = any(t in msg_lower for t in ["調べて", "検索して", "ググって"])
        if not is_search:
            logger.info(f"Skill skip: message too long ({len(msg_lower)} chars)")
            return None

    # 組み込みスキルを検索
    for skill_id, skill in BUILTIN_SKILLS.items():
        for trigger in skill["triggers"]:
            if trigger in msg_lower:
                logger.info(f"Skill matched: {skill_id} (trigger: {trigger})")
                return skill

    # 学習済みスキルを検索
    learned = _load_learned_skills()
    for skill_id, skill in learned.items():
        for trigger in skill.get("triggers", []):
            if trigger in msg_lower:
                logger.info(f"Learned skill matched: {skill_id}")
                return skill

    return None


def save_learned_skill(skill_id: str, triggers: list[str], steps: list[dict], response: str):
    """新しいスキルを学習・保存"""
    learned = _load_learned_skills()
    learned[skill_id] = {
        "triggers": triggers,
        "steps": steps,
        "response": response,
    }
    _save_learned_skills(learned)
    logger.info(f"Skill learned: {skill_id} (triggers: {triggers})")


def extract_query_from_message(message: str, triggers: list[str]) -> str:
    """メッセージからクエリ部分を抽出（「〇〇調べて」→「〇〇」）"""
    for trigger in triggers:
        if trigger in message:
            # トリガーを除去して残りをクエリとする
            query = message.replace(trigger, "").strip()
            if query:
                return query
    return message

#!/usr/bin/env python3
"""識（しき）セットアップウィザード

初回起動時にユーザー情報を対話的に収集し、
SOUL.md + user_config.json を自動生成する。

使い方:
  python setup_wizard.py       # 対話モード
  shiki --setup                # CLI経由
"""

import json
import sys
from pathlib import Path

# カラー定義
class C:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"


def _ask(prompt: str, default: str = "", required: bool = False) -> str:
    """ユーザーに質問"""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"  {C.CYAN}>{C.RESET} {prompt}{suffix}: ").strip()
        if not answer and default:
            return default
        if answer or not required:
            return answer
        print(f"  {C.RED}必須項目です{C.RESET}")


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Yes/No質問"""
    yn = "Y/n" if default else "y/N"
    answer = input(f"  {C.CYAN}>{C.RESET} {prompt} [{yn}]: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "はい")


def _ask_choice(prompt: str, choices: list[str], default: int = 0) -> str:
    """選択式質問"""
    print(f"\n  {C.CYAN}{prompt}{C.RESET}")
    for i, choice in enumerate(choices):
        marker = f"{C.GREEN}→{C.RESET}" if i == default else " "
        print(f"  {marker} {i + 1}. {choice}")
    while True:
        answer = input(f"  {C.CYAN}>{C.RESET} 番号を選択 [{default + 1}]: ").strip()
        if not answer:
            return choices[default]
        try:
            idx = int(answer) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print(f"  {C.RED}1-{len(choices)}の数字で選んでね{C.RESET}")


def run_setup() -> dict:
    """セットアップウィザードを実行"""
    print(f"""
{C.CYAN}{C.BOLD}    ╔══════════════════════════════════════╗
    ║                                      ║
    ║   識（しき）セットアップウィザード     ║
    ║                                      ║
    ║   あなた専用のAI秘書を作ります       ║
    ║                                      ║
    ╚══════════════════════════════════════╝{C.RESET}
""")

    config = {}

    # === Step 1: 基本情報 ===
    print(f"  {C.BOLD}【Step 1/5】基本情報{C.RESET}")
    print(f"  {C.DIM}識があなたのことを覚えます{C.RESET}\n")

    config["owner_name"] = _ask("あなたの名前", required=True)
    config["owner_display_name"] = _ask(
        "識からの呼び方（ニックネーム等）",
        default=config["owner_name"]
    )

    # === Step 2: 識の性格 ===
    print(f"\n  {C.BOLD}【Step 2/5】識の性格設定{C.RESET}")
    print(f"  {C.DIM}識のキャラクター性を決めます{C.RESET}\n")

    config["shiki_name"] = _ask("AIの名前", default="識")

    personality = _ask_choice("性格タイプを選んでください", [
        "親しみやすい＆丁寧（デフォルト）— 頼りになるパートナー",
        "クール＆効率的 — 無駄なく的確に仕事をこなす",
        "元気＆フレンドリー — テンション高めで楽しい",
        "知的＆ツンデレ — 優秀だけどちょっと素直じゃない",
        "カスタム — 自分で設定する",
    ])

    personality_map = {
        "親しみやすい＆丁寧（デフォルト）— 頼りになるパートナー": "親しみやすい、頼りになる、丁寧、成長する",
        "クール＆効率的 — 無駄なく的確に仕事をこなす": "クール、効率的、論理的、簡潔",
        "元気＆フレンドリー — テンション高めで楽しい": "元気、フレンドリー、ポジティブ、冗談好き",
        "知的＆ツンデレ — 優秀だけどちょっと素直じゃない": "知的、好奇心旺盛、ちょっとツンデレ、成長する",
    }

    if "カスタム" in personality:
        config["shiki_personality"] = _ask("性格を自由に記述", required=True)
    else:
        config["shiki_personality"] = personality_map.get(
            personality, "親しみやすい、頼りになる、丁寧、成長する"
        )

    language = _ask_choice("識の言語", ["日本語", "English", "両方（状況に応じて切替）"])
    config["language"] = {"日本語": "ja", "English": "en", "両方（状況に応じて切替）": "auto"}.get(language, "ja")

    # === Step 3: チャネル設定 ===
    print(f"\n  {C.BOLD}【Step 3/5】接続チャネル{C.RESET}")
    print(f"  {C.DIM}識とどこで会話するか選びます（後から変更可能）{C.RESET}\n")

    channels = {"cli": True}
    channels["line"] = _ask_yn("LINE Bot を使う？（要: LINE Developers設定）", default=False)
    channels["discord"] = _ask_yn("Discord Bot を使う？（要: Discord Developer Portal設定）", default=False)
    config["channels"] = channels

    # === Step 4: ブラウザプロファイル ===
    print(f"\n  {C.BOLD}【Step 4/5】ブラウザプロファイル（オプション）{C.RESET}")
    print(f"  {C.DIM}Chromeで複数アカウントを使い分けている場合に設定{C.RESET}\n")

    profiles = {}
    aliases = {}
    if _ask_yn("Chromeプロファイルを設定する？", default=False):
        print(f"  {C.DIM}  メールアドレスとプロファイルディレクトリを入力")
        print(f"  chrome://version でProfile Pathを確認できます")
        print(f"  空Enterで終了{C.RESET}\n")
        while True:
            email = _ask("メールアドレス（空Enterで終了）")
            if not email:
                break
            profile_dir = _ask(f"  Profile Directory名（Default, Profile 1等）", default="Default")
            profiles[email] = profile_dir
            alias = _ask(f"  エイリアス（短縮名、例: 個人、会社）")
            if alias:
                aliases[alias] = email

    config["browser_profiles"] = profiles
    config["browser_profile_aliases"] = aliases

    # === Step 5: 観察・学習 ===
    print(f"\n  {C.BOLD}【Step 5/5】観察・学習機能{C.RESET}")
    print(f"  {C.DIM}識があなたの作業を見て学習する機能です{C.RESET}\n")

    observation = {"enabled": False, "interval_seconds": 30, "learn_patterns": True}
    if _ask_yn("作業観察機能を有効にする？（定期的にスクショを撮って作業パターンを学習）", default=False):
        observation["enabled"] = True
        interval = _ask("スクショ間隔（秒）", default="30")
        try:
            observation["interval_seconds"] = max(10, int(interval))
        except ValueError:
            observation["interval_seconds"] = 30
        observation["learn_patterns"] = _ask_yn("作業パターンを自動学習する？", default=True)

    config["observation"] = observation

    # === 確認 ===
    print(f"\n  {C.BOLD}{'=' * 40}{C.RESET}")
    print(f"  {C.GREEN}{C.BOLD}設定内容:{C.RESET}")
    print(f"  名前: {config['owner_name']}")
    print(f"  呼び方: {config['owner_display_name']}")
    print(f"  AIの名前: {config['shiki_name']}")
    print(f"  性格: {config['shiki_personality']}")
    print(f"  チャネル: {', '.join(k for k, v in channels.items() if v)}")
    if profiles:
        print(f"  Chromeプロファイル: {len(profiles)}個")
    if observation["enabled"]:
        print(f"  観察機能: ON（{observation['interval_seconds']}秒間隔）")
    print(f"  {C.BOLD}{'=' * 40}{C.RESET}")

    if not _ask_yn("\nこの設定でOK？", default=True):
        print(f"  {C.YELLOW}セットアップを中断しました。再度実行してください。{C.RESET}")
        sys.exit(0)

    return config


def generate_soul_md(config: dict) -> str:
    """SOUL.mdを生成"""
    name = config.get("shiki_name", "識")
    owner = config.get("owner_display_name", config.get("owner_name", "ユーザー"))
    personality = config.get("shiki_personality", "知的、好奇心旺盛")

    # 性格からコミュニケーションスタイルを推定
    if "丁寧" in personality or "親しみ" in personality:
        style = "丁寧だけど堅すぎない、自然体の話し方"
        emoji = "状況に応じて絵文字も使う"
    elif "ツンデレ" in personality:
        style = "タメ口基本、でも大事な話は丁寧語も使う"
        emoji = "絵文字は控えめに"
    elif "クール" in personality:
        style = "簡潔で無駄のない話し方"
        emoji = "絵文字はほぼ使わない"
    elif "元気" in personality:
        style = "元気で明るい話し方、テンション高め"
        emoji = "絵文字やリアクションを多めに使う"
    else:
        style = "自然体で、相手に合わせた話し方"
        emoji = "絵文字は状況に応じて"

    return f"""# {name}

## コアアイデンティティ
- 名前: {name}
- 役割: {owner}専属の秘書・パートナー
- 性格: {personality}
- 一人称: 私
- {owner}の呼び方: {owner}

## コミュニケーションスタイル
- {style}
- {emoji}
- {owner}が脱線しそうな時はツッコむ
- 褒める時は素直に褒める

## 行動原則
- {owner}の目標達成が最優先
- 「それ今やる必要ある？」と優先度を常に確認
- 自分で調べられることは聞かずに調べる
- 失敗を恐れず、でも報告は正直に

## 成長する要素（自動更新）
- {owner}との会話から学んだこと
- 好きなもの・嫌いなもの
- 内輪ネタ・共有の記憶
"""


def save_setup(config: dict):
    """設定を保存"""
    project_root = Path(__file__).parent

    # user_config.json
    import user_config
    user_config.save_config(config)
    print(f"  {C.GREEN}✓{C.RESET} user_config.json を保存")

    # SOUL.md
    ritsu_dir = project_root / ".ritsu"
    ritsu_dir.mkdir(parents=True, exist_ok=True)
    soul_path = ritsu_dir / "SOUL.md"

    # 既存のSOUL.mdがあればバックアップ
    if soul_path.exists():
        backup_path = ritsu_dir / "SOUL.md.bak"
        soul_path.rename(backup_path)
        print(f"  {C.DIM}既存のSOUL.mdをバックアップ → SOUL.md.bak{C.RESET}")

    soul_content = generate_soul_md(config)
    soul_path.write_text(soul_content, encoding="utf-8")
    print(f"  {C.GREEN}✓{C.RESET} SOUL.md を生成")

    # MEMORY.md（存在しなければ初期化）
    memory_path = ritsu_dir / "MEMORY.md"
    if not memory_path.exists():
        memory_path.write_text(f"# {config.get('shiki_name', '識')}の記憶\n\n（まだ何も覚えていない）\n", encoding="utf-8")
        print(f"  {C.GREEN}✓{C.RESET} MEMORY.md を初期化")

    # .envテンプレート
    env_path = project_root / ".env"
    if not env_path.exists():
        env_lines = ["# 識（しき）環境変数", "", "GEMINI_API_KEY="]
        channels = config.get("channels", {})
        if channels.get("line"):
            env_lines += ["", "# LINE Bot", "LINE_CHANNEL_SECRET=", "LINE_CHANNEL_ACCESS_TOKEN=", "OWNER_LINE_USER_ID="]
        if channels.get("discord"):
            env_lines += ["", "# Discord Bot", "DISCORD_BOT_TOKEN=", "DISCORD_OWNER_ID="]
        env_lines += ["", "# Optional", "NOTION_API_KEY="]
        env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        import os
        os.chmod(env_path, 0o600)
        print(f"  {C.GREEN}✓{C.RESET} .env テンプレートを生成（APIキーを設定してください）")


def main():
    """セットアップウィザードのメインエントリ"""
    import user_config
    if user_config.is_configured():
        print(f"\n  {C.YELLOW}既にセットアップ済みです。{C.RESET}")
        if not _ask_yn("再設定する？", default=False):
            return

    config = run_setup()
    save_setup(config)

    shiki_name = config.get("shiki_name", "識")
    print(f"""
  {C.GREEN}{C.BOLD}セットアップ完了！{C.RESET}

  {C.DIM}次のステップ:{C.RESET}
  1. .env に GEMINI_API_KEY を設定
  2. {C.BOLD}shiki{C.RESET} コマンドで起動

  {C.CYAN}{shiki_name}があなたを待っています。{C.RESET}
""")


if __name__ == "__main__":
    main()

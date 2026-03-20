#!/usr/bin/env python3
"""識（しき）CLI — ターミナルから直接対話

使い方:
  shiki              → 対話モード起動
  shiki "〇〇して"   → ワンショット実行

LINE不要。Gemini + 全ツールをターミナルから直接叩く。
"""

import asyncio
import sys
import os
import logging

# ログを抑制（CLIでは不要）
logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
)

# カラー定義
class C:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"
    RESET = "\033[0m"
    BG_DARK = "\033[48;5;235m"


def print_banner():
    print(f"""
{C.CYAN}{C.BOLD}    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║     ███████╗██╗  ██╗██╗██╗  ██╗██╗              ║
    ║     ██╔════╝██║  ██║██║██║ ██╔╝██║              ║
    ║     ███████╗███████║██║█████╔╝ ██║              ║
    ║     ╚════██║██╔══██║██║██╔═██╗ ██║              ║
    ║     ███████║██║  ██║██║██║  ██╗██║              ║
    ║     ╚══════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═╝              ║
    ║                                                  ║
    ║  {C.WHITE}識 — 自己識別型環境統合制御体{C.CYAN}                    ║
    ║  {C.DIM}Jiko-Shikibetsu-gata Kankyou Tougou Seigyotai{C.CYAN}{C.BOLD}  ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝{C.RESET}

  {C.DIM}Tools: 35  |  Layers: 5 + CodeAct  |  Model: Gemini 2.5 Pro/Flash (Smart Routing){C.RESET}
  {C.DIM}終了: Ctrl+C / "exit"{C.RESET}
""")


async def init_agent():
    """エージェント初期化（Geminiクライアント + セキュリティ）"""
    print(f"  {C.DIM}初期化中...{C.RESET}", end="", flush=True)

    # 環境変数チェック
    from config import GEMINI_API_KEY
    if not GEMINI_API_KEY:
        print(f"\n{C.RED}エラー: GEMINI_API_KEY が .env に未設定{C.RESET}")
        sys.exit(1)

    # セキュリティ監査（静かに）
    try:
        from security.mac_hardening import run_security_audit
        run_security_audit()
    except Exception:
        pass

    # MCP接続（あれば）
    mcp_status = ""
    try:
        from mcp_ext.client import connect_all_servers
        connected = await connect_all_servers()
        if connected > 0:
            mcp_status = f"  MCP: {connected} servers"
    except Exception:
        pass

    # エージェントループのimportを事前に行う（初回遅延回避）
    try:
        from agent.loop import process_message
    except Exception as e:
        print(f"\n{C.RED}エージェント初期化失敗: {e}{C.RESET}")
        sys.exit(1)

    print(f"\r  {C.GREEN}準備完了{C.RESET} {C.DIM}{mcp_status}{C.RESET}      \n")


async def process_and_print(message: str):
    """メッセージを処理して結果を表示"""
    from agent.loop import process_message, set_progress_callback

    # 進捗表示コールバック
    async def show_progress(msg: str):
        print(f"  {C.DIM}  → {msg}{C.RESET}", flush=True)

    set_progress_callback(show_progress)

    try:
        result = await process_message(message)
        text = result.get("text", "")
        image_path = result.get("image_path")

        if text:
            print(f"\n  {C.GREEN}{C.BOLD}識:{C.RESET} {C.GREEN}{text}{C.RESET}")

        if image_path:
            print(f"  {C.DIM}  📸 {image_path}{C.RESET}")

    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}中断{C.RESET}")
    except Exception as e:
        print(f"\n  {C.RED}エラー: {e}{C.RESET}")


async def interactive_mode():
    """対話モード"""
    print_banner()
    await init_agent()

    while True:
        try:
            import user_config as _uc
            _name = _uc.get_display_name()
            user_input = input(f"  {C.CYAN}{C.BOLD}{_name}:{C.RESET} ").strip()

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "bye", "終了"):
                print(f"\n  {C.GREEN}{C.BOLD}識:{C.RESET} {C.GREEN}またね。{C.RESET}\n")
                break

            await process_and_print(user_input)
            print()  # 空行

        except KeyboardInterrupt:
            print(f"\n\n  {C.GREEN}{C.BOLD}識:{C.RESET} {C.GREEN}またね。{C.RESET}\n")
            break
        except EOFError:
            break


async def oneshot_mode(message: str):
    """ワンショットモード"""
    print(f"\n  {C.DIM}識 — 自己識別型環境統合制御体{C.RESET}")
    await init_agent()
    await process_and_print(message)
    print()


async def discord_mode():
    """Discord Botモード"""
    from discord_bot import start_bot
    await start_bot()


async def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "discord":
            await discord_mode()
            return
        if sys.argv[1] in ("--setup", "setup"):
            from setup_wizard import main as setup_main
            setup_main()
            return
        # ワンショット: shiki "〇〇して"
        message = " ".join(sys.argv[1:])
        await oneshot_mode(message)
    else:
        # 初回セットアップチェック
        import user_config
        if not user_config.is_configured():
            print(f"\n  {C.YELLOW}初回セットアップが必要です。{C.RESET}")
            print(f"  {C.DIM}セットアップウィザードを起動します...{C.RESET}\n")
            from setup_wizard import main as setup_main
            setup_main()
            return

        # 対話モード: shiki
        await interactive_mode()

    # クリーンアップ
    try:
        from tools.browser import close_browser
        await close_browser()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

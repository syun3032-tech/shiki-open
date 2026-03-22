"""ターミナルコマンド実行ツール

セキュリティ:
- コマンドホワイトリスト制
- シェルメタ文字を完全ブロック（create_subprocess_exec使用）
- python3/node等のインタプリタ禁止（任意コード実行防止）
- osascript禁止（desktop.pyで安全にラップ済み）
"""

import asyncio
import logging
import shlex
import re

logger = logging.getLogger("shiki.tools")

# 許可するコマンド（先頭の実行ファイル名）
# 重要: python3は任意コード実行できるため禁止（execute_codeツール経由で安全に実行）
# 重要: osascriptはdesktop.pyで安全にラップ済みのため禁止
# npm/npx/node/pip: パッケージ管理・開発ツールとして許可
ALLOWED_COMMANDS = frozenset({
    # ファイル操作（読み取り系）
    "ls", "find", "cat", "head", "tail", "wc", "file", "du", "df",
    "stat", "md5", "shasum",
    # テキスト処理
    "grep", "awk", "sed", "sort", "uniq", "cut", "tr", "diff",
    # プロセス
    "ps", "top", "lsof", "which", "whoami", "hostname",
    # 開発
    "git", "npm", "npx", "node", "pip", "pip3", "python", "python3",
    # システム情報
    "uname", "sw_vers", "sysctl", "date", "cal", "uptime",
    # ファイル操作（書き込み系）
    "mkdir", "touch", "cp", "mv",
    # macOS固有（安全なもののみ）
    "screencapture",
    # Windows固有（安全なもののみ）
    "dir", "type", "where", "tasklist", "systeminfo", "ver",
    "findstr", "more", "tree", "attrib", "icacls",
    # その他便利
    "echo", "printf", "open", "pbcopy", "pbpaste",
})

# シェルメタ文字（これらが含まれるコマンドは全拒否）
_SHELL_METACHAR_PATTERN = re.compile(r'[;`${}]|&&|\|\||<<|>>|\$\(')

# 絶対に実行させないパターン
BLOCKED_PATTERNS = [
    "sudo", "su ",
    "rm ", "rm\t", "rmdir",  # 削除系は全ブロック
    "mkfs", "dd if=", "chmod 777", "> /dev/",
    "curl ", "wget ",  # ネットワーク送受信はブロック
    "eval ", "exec ",
    "launchctl", "defaults write", "defaults delete",
    "networksetup -set",
    "security ", "keychain",
    "git push", "git remote add", "git remote set",
    "git config --global",
]

# gitのサブコマンドホワイトリスト（読み取り系のみ）
_GIT_ALLOWED_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "tag",
    "ls-files", "ls-tree", "rev-parse", "describe",
    "blame", "shortlog", "stash", "stash list",
    "add", "commit", "checkout", "switch", "merge",
    "fetch", "pull", "rebase", "cherry-pick", "reset",
    "init", "clone",
})

# パイプの組み合わせで危険になるパターン
# 個別コマンドは安全でも、パイプで繋ぐと破壊的になるケースをブロック
_DANGEROUS_PIPE_PATTERNS = [
    # xargs + 破壊的コマンド
    ({"find", "ls"}, {"xargs"}, r"xargs\s+.*(chmod|chown|rm|mv|cp\s+-r)"),
    # 任意データをcrontabに流し込む
    (None, {"crontab"}, r"crontab"),
    # 出力をシェルスクリプトとして実行
    (None, {"sh", "bash", "zsh"}, r"(sh|bash|zsh)(\s+|$)"),
]

MAX_OUTPUT = 10_000  # 出力上限
TIMEOUT = 30  # 秒


def _split_pipe_segments(command: str) -> list[str]:
    """パイプ区切りでコマンドを分割（引用符内の|は無視）

    shlex.splitだと|もトークン化されるが、実行時にはシェル構文として
    分割する必要がある。引用符内の|はパイプではないので保護する。
    """
    segments = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == '\\' and in_double and i + 1 < len(command):
            current.append(ch)
            current.append(command[i + 1])
            i += 1
        elif ch == '|' and not in_single and not in_double:
            seg = ''.join(current).strip()
            if seg:
                segments.append(seg)
            current = []
        else:
            current.append(ch)
        i += 1
    seg = ''.join(current).strip()
    if seg:
        segments.append(seg)
    return segments


def _validate_command(command: str) -> tuple[bool, str]:
    """コマンドの安全性を検証。(ok, error_message)"""
    # シェルメタ文字チェック（インジェクション防止の最重要防御）
    if _SHELL_METACHAR_PATTERN.search(command):
        return False, "セキュリティ: シェルメタ文字は使用できない"

    # コマンドを解析
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return False, f"コマンド解析エラー: {e}"

    if not parts:
        return False, "空のコマンド"

    # パイプがある場合、全セグメントをチェック（引用符内の|は無視）
    segments = []
    current = []
    for part in parts:
        if part == "|":
            if current:
                segments.append(current)
            current = []
        else:
            current.append(part)
    if current:
        segments.append(current)

    for segment in segments:
        if not segment:
            continue
        cmd_name = segment[0].split("/")[-1]

        # ホワイトリストチェック
        if cmd_name not in ALLOWED_COMMANDS:
            return False, f"許可されていないコマンド: {cmd_name}"

        # gitサブコマンド制限
        if cmd_name == "git" and len(segment) > 1:
            sub = segment[1]
            if sub.startswith("-"):
                # git --version 等は許可
                pass
            elif sub not in _GIT_ALLOWED_SUBCOMMANDS:
                return False, f"許可されていないgitサブコマンド: {sub}"

    # ブロックパターンチェック
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return False, "セキュリティ: 危険なパターン検出"

    # パイプの組み合わせチェック（個別は安全でも組み合わせると危険なケース）
    if len(segments) >= 2:
        cmd_names_in_pipe = []
        for seg in segments:
            if seg:
                cmd_names_in_pipe.append(seg[0].split("/")[-1])

        pipe_full = " | ".join(" ".join(seg) for seg in segments).lower()

        for source_cmds, sink_cmds, pattern in _DANGEROUS_PIPE_PATTERNS:
            # source_cmds=None は「どのコマンドからでも」
            has_source = source_cmds is None or any(c in source_cmds for c in cmd_names_in_pipe)
            has_sink = any(c in sink_cmds for c in cmd_names_in_pipe)
            if has_source and has_sink and re.search(pattern, pipe_full):
                return False, f"セキュリティ: 危険なパイプの組み合わせ（{pattern}）"

    return True, ""


# cwdとして許可するディレクトリ（動的生成）
def _get_allowed_cwd_prefixes() -> tuple[str, ...]:
    """ユーザー設定 + プロジェクトルートから許可パスを動的生成"""
    try:
        import user_config
        paths = user_config.get_allowed_paths()
    except Exception:
        from pathlib import Path
        home = str(Path.home())
        paths = [f"{home}/Desktop", f"{home}/Documents", f"{home}/Downloads"]

    # プロジェクトルートも常に許可
    project_root = str(__import__("config").PROJECT_ROOT)
    all_paths = list(paths) + [project_root, "/tmp"]
    return tuple(all_paths)


def _validate_cwd(cwd: str | None) -> str:
    """cwdの安全性チェック。許可されたディレクトリのみ"""
    if not cwd:
        return str(__import__("config").PROJECT_ROOT)

    from pathlib import Path
    resolved = str(Path(cwd).resolve())

    for prefix in _get_allowed_cwd_prefixes():
        if resolved.startswith(prefix):
            if Path(resolved).is_dir():
                return resolved
            return str(__import__("config").PROJECT_ROOT)

    logger.warning(f"CWD blocked: {cwd}")
    return str(__import__("config").PROJECT_ROOT)


async def run_command(command: str, cwd: str | None = None) -> dict:
    """コマンドを安全に実行（create_subprocess_exec使用）"""
    ok, error = _validate_command(command)
    if not ok:
        return {"success": False, "error": error}

    work_dir = _validate_cwd(cwd)
    logger.info(f"Running command: {command} (cwd: {work_dir})")

    try:
        pipe_segments = _split_pipe_segments(command)
        if len(pipe_segments) > 1:
            # 最初のコマンド
            first_parts = shlex.split(pipe_segments[0])
            prev_proc = await asyncio.create_subprocess_exec(
                *first_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )

            # 中間〜最後のコマンドをパイプで繋ぐ
            for seg in pipe_segments[1:]:
                seg_parts = shlex.split(seg)
                proc = await asyncio.create_subprocess_exec(
                    *seg_parts,
                    stdin=prev_proc.stdout,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                )
                prev_proc.stdout.close()  # 前段のstdoutを閉じてSIGPIPEを正しく伝播
                prev_proc = proc

            final_proc = prev_proc
        else:
            parts = shlex.split(pipe_segments[0])
            final_proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
        stdout, stderr = await asyncio.wait_for(
            final_proc.communicate(), timeout=TIMEOUT
        )
        output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
        error = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT]

        if final_proc.returncode == 0:
            logger.info(f"Command OK: {command} ({len(output)} chars)")
            return {"success": True, "output": output or "(出力なし)", "exit_code": 0}
        else:
            logger.warning(f"Command failed: {command} (exit {final_proc.returncode})")
            return {
                "success": False,
                "output": output,
                "error": error or f"終了コード: {final_proc.returncode}",
                "exit_code": final_proc.returncode,
            }
    except asyncio.TimeoutError:
        return {"success": False, "error": f"タイムアウト（{TIMEOUT}秒）"}
    except Exception as e:
        return {"success": False, "error": str(e)}

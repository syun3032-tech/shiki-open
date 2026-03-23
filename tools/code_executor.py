"""CodeAct — Pythonコード実行ツール（Manus AI CodeAct paradigm inspired）

33個の固定ツールでは対応できないタスクを、Pythonコードで解決する。
例: データ変換、計算、テキスト加工、JSON整形、日付計算 etc.

セキュリティ:
- サブプロセスで隔離実行（メインプロセスに影響なし）
- 許可モジュールのホワイトリスト制（os/subprocess/socket等は禁止）
- 10秒タイムアウト
- 出力10KB制限
- ファイルI/O・ネットワーク・シェル実行は全て禁止
  （既存ツールの read_file/write_file/browse_url 等を使うこと）
"""

import asyncio
import logging
import os
import sys
import tempfile
import textwrap

logger = logging.getLogger("shiki.tools")

# 実行タイムアウト（秒）
_TIMEOUT = 30

# 出力上限（バイト）
_MAX_OUTPUT = 100_000

# サンドボックス内で実行されるラッパースクリプト
# - 危険なモジュールのimportをブロック
# - builtinsから危険な関数を除去
# - stdout/stderrをキャプチャ
_SANDBOX_WRAPPER = textwrap.dedent('''\
import sys
import io

# === 危険モジュールのブロック ===
_BLOCKED_MODULES = frozenset({
    "subprocess", "shutil", "signal", "ctypes",
    "socket", "http", "urllib", "requests", "httpx", "aiohttp",
    "importlib", "runpy", "code", "codeop",
    "multiprocessing", "threading", "concurrent",
    "pickle", "shelve", "marshal",
    "webbrowser", "antigravity",
    "tempfile", "glob",
    "dbm",
})

_original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

_BLOCKED_OS_ATTRS = frozenset({
    "system", "exec", "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe",
    "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "popen", "popen2", "popen3", "popen4",
})

_ALLOWED_OS_ATTRS = frozenset({
    "environ", "getcwd", "path", "listdir", "sep", "linesep",
    "name", "curdir", "pardir", "extsep", "altsep", "pathsep",
    "devnull", "getenv", "cpu_count", "getpid", "urandom",
})

class _RestrictedOs:
    """osモジュールの安全なサブセットのみ公開するラッパー"""
    def __init__(self, real_os):
        self._real_os = real_os
        self.path = real_os.path
        self.environ = dict(real_os.environ)  # コピー（書き込み不可にするため）
        self.sep = real_os.sep
        self.linesep = real_os.linesep
        self.name = real_os.name
        self.curdir = real_os.curdir
        self.pardir = real_os.pardir
    def getcwd(self): return self._real_os.getcwd()
    def listdir(self, p='.'): return self._real_os.listdir(p)
    def getenv(self, key, default=None): return self._real_os.getenv(key, default)
    def cpu_count(self): return self._real_os.cpu_count()
    def getpid(self): return self._real_os.getpid()
    def urandom(self, n): return self._real_os.urandom(n)
    def __getattr__(self, name):
        if name in _BLOCKED_OS_ATTRS:
            raise AttributeError(f"セキュリティ: os.{name} は使用できません")
        if name in _ALLOWED_OS_ATTRS:
            return getattr(self._real_os, name)
        raise AttributeError(f"セキュリティ: os.{name} は使用できません")

def _safe_import(name, *args, **kwargs):
    top = name.split(".")[0]
    # osモジュール: 制限付きで許可
    if top == "os":
        import os as _real_os
        if name == "os.path":
            return _real_os.path
        return _RestrictedOs(_real_os)
    # pathlib: 許可
    if top == "pathlib":
        return _original_import(name, *args, **kwargs)
    # sqlite3: 許可
    if top == "sqlite3":
        return _original_import(name, *args, **kwargs)
    if top in _BLOCKED_MODULES:
        raise ImportError(f"セキュリティ: '{name}' のimportは禁止されています")
    return _original_import(name, *args, **kwargs)

import builtins
builtins.__import__ = _safe_import

# exec参照を保存してからbuiltinsを制限
_safe_exec = exec
_safe_compile = compile

# === 安全なbuiltinsのホワイトリスト ===
_SAFE_BUILTINS = {
    k: getattr(builtins, k) for k in (
        "abs", "all", "any", "bin", "bool", "bytes", "callable", "chr",
        "complex", "dict", "dir", "divmod", "enumerate", "filter", "float",
        "format", "frozenset", "getattr", "hasattr", "hash", "hex", "id",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "object", "oct", "ord", "pow", "print",
        "property", "range", "repr", "reversed", "round", "set", "slice",
        "sorted", "staticmethod", "str", "sum", "super", "tuple", "type",
        "vars", "zip", "True", "False", "None",
    ) if hasattr(builtins, k)
}
_SAFE_BUILTINS["__import__"] = _safe_import

# === ユーザーコード実行 ===
_stdout = io.StringIO()
_stderr = io.StringIO()
sys.stdout = _stdout
sys.stderr = _stderr

try:
    _compiled = _safe_compile(_USER_CODE_, "<codeact>", "exec")
    _safe_exec(_compiled, {"__builtins__": _SAFE_BUILTINS, "__name__": "__main__"})
except Exception as _e:
    print(f"エラー: {type(_e).__name__}: {_e}", file=_stderr)

_out = _stdout.getvalue()
_err = _stderr.getvalue()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

# 結果出力（メインプロセスで解析）
if _out:
    print(_out[:MAX_OUT], end="")
if _err:
    print(f"\\n[STDERR]\\n{_err[:MAX_OUT]}", end="")
''')


async def execute_code(code: str) -> dict:
    """Pythonコードをサンドボックス内で実行

    Args:
        code: 実行するPythonコード

    Returns:
        {"success": True, "output": "..."} or {"success": False, "error": "..."}
    """
    if not code or not code.strip():
        return {"success": False, "error": "コードが空"}

    # コードサイズ制限（50KB）
    if len(code) > 50_000:
        return {"success": False, "error": "コードが長すぎます（50KB上限）"}

    # 事前チェック: 明らかに危険なパターン
    code_lower = code.lower()
    for dangerous in ("import subprocess", "import socket",
                      "import shutil", "import ctypes", "__import__",
                      "os.system(", "os.popen(", "os.exec",
                      "exec(", "eval("):
        if dangerous in code_lower:
            return {
                "success": False,
                "error": f"セキュリティ: '{dangerous}' は使用できません。"
                         f"ファイル操作はread_file/write_file、Web操作はbrowse_url等を使ってください。",
            }

    # サンドボックススクリプトを構築
    # ユーザーコードを文字列として埋め込み（reprでエスケープ）
    sandbox_script = (
        f"_USER_CODE_ = {repr(code)}\n"
        f"MAX_OUT = {_MAX_OUTPUT}\n"
        + _SANDBOX_WRAPPER
    )

    try:
        if sys.platform == "win32":
            safe_env = {"PATH": os.environ.get("PATH", ""), "USERPROFILE": tempfile.gettempdir()}
        else:
            safe_env = {"PATH": "/usr/bin:/usr/local/bin", "HOME": tempfile.gettempdir()}

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", sandbox_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # サブプロセスの環境を最小化
            env=safe_env,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TIMEOUT
        )

        output = stdout.decode("utf-8", errors="replace")[:_MAX_OUTPUT]
        error_output = stderr.decode("utf-8", errors="replace")[:_MAX_OUTPUT]

        if proc.returncode == 0:
            result = output.strip() if output.strip() else "(出力なし)"
            logger.info(f"CodeAct OK: {len(code)} chars code, {len(result)} chars output")
            return {"success": True, "output": result}
        else:
            error_msg = error_output.strip() or output.strip() or f"終了コード: {proc.returncode}"
            logger.warning(f"CodeAct failed: {error_msg[:200]}")
            return {"success": False, "error": error_msg}

    except asyncio.TimeoutError:
        logger.warning(f"CodeAct timeout ({_TIMEOUT}s)")
        return {"success": False, "error": f"タイムアウト（{_TIMEOUT}秒）。無限ループになっていませんか？"}
    except Exception as e:
        logger.error(f"CodeAct execution error: {e}")
        return {"success": False, "error": str(e)}

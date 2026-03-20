"""URL安全性検証

3段階: 信頼済み → 怪しいパターン検出 → 未知はブロック
"""

import ipaddress
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger("shiki.security")

# === 信頼済みドメイン（無条件で開く）===
TRUSTED_DOMAINS = frozenset({
    # 検索エンジン
    "google.com", "google.co.jp", "bing.com", "duckduckgo.com",
    # 動画・音楽
    "youtube.com", "youtu.be", "spotify.com", "music.apple.com",
    "soundcloud.com", "nicovideo.jp", "live.nicovideo.jp",
    "twitch.tv", "abema.tv", "tver.jp",
    # SNS
    "twitter.com", "x.com", "instagram.com", "facebook.com",
    "threads.net", "linkedin.com", "reddit.com",
    # メッセージ・コミュニケーション
    "discord.com", "slack.com", "line.me", "zoom.us",
    # 開発
    "github.com", "gitlab.com", "stackoverflow.com",
    "npmjs.com", "pypi.org", "docs.python.org",
    "developer.apple.com", "developer.mozilla.org",
    "zenn.dev", "qiita.com",
    # Google系
    "docs.google.com", "drive.google.com", "mail.google.com",
    "calendar.google.com", "meet.google.com", "maps.google.com",
    "translate.google.com", "cloud.google.com",
    # AI
    "claude.ai", "anthropic.com", "openai.com", "chatgpt.com",
    "huggingface.co", "kaggle.com",
    # 生産性
    "notion.so", "notion.site", "obsidian.md",
    "figma.com", "canva.com", "miro.com",
    "trello.com", "asana.com",
    # ショッピング
    "amazon.co.jp", "amazon.com", "rakuten.co.jp",
    "mercari.com", "yahoo.co.jp",
    # ニュース・メディア
    "nhk.or.jp", "nikkei.com", "asahi.com",
    "bbc.com", "cnn.com", "reuters.com",
    "techcrunch.com", "theverge.com", "wired.com", "wired.jp",
    # 学習
    "wikipedia.org", "wikimedia.org",
    "udemy.com", "coursera.org",
    # クラウド
    "aws.amazon.com", "azure.microsoft.com",
    "console.cloud.google.com", "vercel.com", "netlify.com",
    "cloudflare.com", "heroku.com",
    # Apple
    "apple.com", "icloud.com",
    # Microsoft
    "microsoft.com", "office.com", "live.com", "outlook.com",
    # クラウドソーシング・副業
    "crowdworks.jp", "lancers.jp", "coconala.com",
    "bizseek.jp", "shufti.jp",
    # その他安全
    "archive.org", "medium.com", "substack.com",
    "note.com", "hatena.ne.jp", "hatenablog.com",
})

# === 危険パターン（即ブロック）===
DANGEROUS_PATTERNS = [
    r"\.exe$",                    # 実行ファイル直リンク
    r"\.msi$",
    r"\.dmg$",                    # macOSインストーラ
    r"\.pkg$",
    r"\.scr$",
    r"\.bat$",
    r"\.cmd$",
    r"\.ps1$",                    # PowerShell
    r"\.sh$",                     # シェルスクリプト
    r"data:",                     # data URL
    r"javascript:",               # XSS
    r"@.*@",                      # 二重@（フィッシング手法）
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",  # IPアドレス直接（ほぼ怪しい）
    r"bit\.ly|tinyurl|t\.co|goo\.gl|is\.gd|shorturl",  # 短縮URL（中身が分からない）
]

# === 怪しいドメインパターン ===
SUSPICIOUS_PATTERNS = [
    r"login|signin|account|verify|secure|update|confirm",  # フィッシング語
    r"free.*gift|prize|winner|congratulation",               # スパム語
    r"-{3,}",                     # ハイフン多すぎ
    r"\.(tk|ml|ga|cf|gq|buzz|top|xyz|club|icu|cam)$",  # 悪用多いTLD
]


def _extract_domain(url: str) -> str | None:
    """URLからドメインを抽出"""
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return None
        return hostname.lower()
    except Exception:
        return None


def _is_subdomain_of(hostname: str, trusted: str) -> bool:
    """hostnameがtrustedのサブドメインか"""
    return hostname == trusted or hostname.endswith("." + trusted)


def _check_ssrf(hostname: str) -> str | None:
    """SSRF対策: プライベートIP/localhost/メタデータエンドポイント検出

    Returns: ブロック理由（文字列）。安全ならNone。
    """
    # localhost検出
    if hostname in ("localhost", "localhost.localdomain"):
        return "localhost"

    # IPアドレスとして解析（IPv4/IPv6両対応）
    try:
        # ブラケット除去 [::1] → ::1
        ip_str = hostname.strip("[]")
        ip = ipaddress.ip_address(ip_str)

        if ip.is_loopback:
            return f"loopback ({ip})"
        if ip.is_private:
            return f"private network ({ip})"
        if ip.is_reserved:
            return f"reserved ({ip})"
        if ip.is_link_local:
            return f"link-local ({ip})"

        # AWS/GCPメタデータエンドポイント
        metadata_ips = {"169.254.169.254", "169.254.170.2", "fd00:ec2::254"}
        if str(ip) in metadata_ips:
            return f"cloud metadata ({ip})"
    except ValueError:
        pass  # ドメイン名の場合はIPアドレスとして解析できない（正常）

    # ドメイン名ベースのSSRF検出
    ssrf_domains = {
        "metadata.google.internal",
        "metadata.google",
        "instance-data",
        "169.254.169.254",
    }
    if hostname in ssrf_domains:
        return f"cloud metadata domain ({hostname})"

    return None


def validate_url(url: str) -> dict:
    """URL安全性チェック

    Returns:
        {"safe": bool, "reason": str, "level": "trusted"|"blocked"|"unknown"}
    """
    url_lower = url.lower()

    # 1. 危険パターンチェック（即ブロック）
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, url_lower):
            logger.warning(f"URL BLOCKED (dangerous pattern): {url}")
            return {
                "safe": False,
                "reason": f"危険なURLパターンを検出",
                "level": "blocked",
            }

    # 2. ドメイン抽出
    domain = _extract_domain(url)
    if not domain:
        return {
            "safe": False,
            "reason": "URLを解析できない",
            "level": "blocked",
        }

    # 2.5. SSRF対策: プライベートIP/localhost検出（IPv4 + IPv6）
    ssrf_check = _check_ssrf(domain)
    if ssrf_check:
        logger.warning(f"URL BLOCKED (SSRF): {url} - {ssrf_check}")
        return {
            "safe": False,
            "reason": f"内部ネットワークへのアクセスはブロック: {ssrf_check}",
            "level": "blocked",
        }

    # 3. 信頼済みドメインチェック
    for trusted in TRUSTED_DOMAINS:
        if _is_subdomain_of(domain, trusted):
            return {
                "safe": True,
                "reason": f"信頼済みサイト ({trusted})",
                "level": "trusted",
            }

    # 4. 怪しいパターンチェック
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, domain):
            logger.warning(f"URL BLOCKED (suspicious): {url}")
            return {
                "safe": False,
                "reason": f"怪しいドメインパターンを検出: {domain}",
                "level": "blocked",
            }

    # 5. 未知のドメイン → ブロック（オーナーに確認）
    logger.info(f"URL UNKNOWN: {url} (domain: {domain})")
    return {
        "safe": False,
        "reason": f"未知のサイト: {domain}",
        "level": "unknown",
    }

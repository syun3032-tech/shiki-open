"""アクティビティ自動分類 + 作業モード判定

URLのドメインとアプリ名から活動カテゴリを判定。
コンテキストスイッチ頻度から作業モード（deep_work等）と集中スコアを算出。
"""

import logging
from collections import Counter, deque
from urllib.parse import urlparse

logger = logging.getLogger("shiki.observer.categorizer")


# =============================================================================
# URL/ドメイン → カテゴリ分類（200+ ドメイン）
# =============================================================================

_DOMAIN_CATEGORIES: dict[str, str] = {}

# 開発
for d in [
    "github.com", "gitlab.com", "bitbucket.org",
    "stackoverflow.com", "stackexchange.com",
    "docs.python.org", "docs.rs", "doc.rust-lang.org",
    "developer.mozilla.org", "developer.apple.com",
    "developer.android.com", "developer.chrome.com",
    "npmjs.com", "pypi.org", "crates.io", "rubygems.org",
    "pkg.go.dev", "packagist.org",
    "vercel.com", "netlify.com", "heroku.com", "railway.app",
    "render.com", "fly.io",
    "aws.amazon.com", "console.cloud.google.com", "azure.microsoft.com",
    "cloudflare.com", "digitalocean.com",
    "docker.com", "hub.docker.com", "kubernetes.io",
    "zenn.dev", "qiita.com", "dev.to", "hashnode.com",
    "replit.com", "codepen.io", "codesandbox.io",
    "leetcode.com", "atcoder.jp", "hackerrank.com",
]:
    _DOMAIN_CATEGORIES[d] = "dev"

# AI / ML
for d in [
    "chat.openai.com", "openai.com", "platform.openai.com",
    "claude.ai", "anthropic.com", "console.anthropic.com",
    "gemini.google.com", "ai.google.dev", "aistudio.google.com",
    "huggingface.co", "kaggle.com",
    "arxiv.org", "paperswithcode.com",
    "colab.research.google.com",
    "perplexity.ai", "you.com",
    "midjourney.com", "civitai.com",
]:
    _DOMAIN_CATEGORIES[d] = "ai"

# 生産性 / ビジネスツール
for d in [
    "notion.so", "notion.site",
    "docs.google.com", "sheets.google.com", "slides.google.com", "forms.google.com",
    "drive.google.com",
    "figma.com", "canva.com", "miro.com",
    "trello.com", "asana.com", "linear.app", "clickup.com",
    "monday.com", "basecamp.com",
    "airtable.com", "coda.io",
    "obsidian.md",
    "1password.com",
    "calendar.google.com",
    "todoist.com", "ticktick.com",
]:
    _DOMAIN_CATEGORIES[d] = "productivity"

# コミュニケーション
for d in [
    "mail.google.com", "outlook.live.com", "outlook.office.com",
    "slack.com", "app.slack.com",
    "discord.com", "discord.gg",
    "web.telegram.org",
    "teams.microsoft.com",
    "messenger.com",
    "chatwork.com",
]:
    _DOMAIN_CATEGORIES[d] = "communication"

# ミーティング
for d in [
    "meet.google.com", "zoom.us",
    "teams.microsoft.com",
    "gather.town", "around.co",
]:
    _DOMAIN_CATEGORIES[d] = "meeting"

# SNS
for d in [
    "twitter.com", "x.com",
    "instagram.com", "facebook.com", "threads.net",
    "linkedin.com",
    "reddit.com",
    "tiktok.com",
    "bsky.app", "mastodon.social",
    "note.com", "medium.com", "substack.com",
]:
    _DOMAIN_CATEGORIES[d] = "social"

# メディア / エンタメ
for d in [
    "youtube.com", "youtu.be",
    "netflix.com", "amazon.co.jp", "primevideo.com",
    "disneyplus.com", "hulu.jp",
    "abema.tv", "tver.jp",
    "nicovideo.jp", "live.nicovideo.jp",
    "twitch.tv",
    "spotify.com", "music.apple.com", "soundcloud.com",
    "podcasts.apple.com",
]:
    _DOMAIN_CATEGORIES[d] = "media"

# ニュース / 情報
for d in [
    "nhk.or.jp", "nikkei.com", "asahi.com", "yomiuri.co.jp", "mainichi.jp",
    "bbc.com", "cnn.com", "reuters.com",
    "techcrunch.com", "theverge.com", "wired.com", "wired.jp",
    "engadget.com", "arstechnica.com", "gizmodo.jp",
    "itmedia.co.jp", "impress.co.jp",
    "gigazine.net", "publickey1.jp",
    "wikipedia.org",
    "hatena.ne.jp", "hatenablog.com",
]:
    _DOMAIN_CATEGORIES[d] = "news"

# ショッピング
for d in [
    "amazon.co.jp", "amazon.com",
    "rakuten.co.jp", "shopping.yahoo.co.jp",
    "mercari.com", "zozo.jp",
    "kakaku.com",
]:
    _DOMAIN_CATEGORIES[d] = "shopping"

# フリーランス / 副業
for d in [
    "crowdworks.jp", "lancers.jp", "coconala.com",
    "bizseek.jp", "shufti.jp",
    "upwork.com", "fiverr.com", "freelancer.com",
]:
    _DOMAIN_CATEGORIES[d] = "freelance"

# 学習
for d in [
    "udemy.com", "coursera.org", "edx.org",
    "skillshare.com", "pluralsight.com",
    "schoo.jp", "progate.com", "paiza.jp",
]:
    _DOMAIN_CATEGORIES[d] = "learning"

# アプリ名 → カテゴリ
_APP_CATEGORIES: dict[str, str] = {
    "Cursor": "coding", "Visual Studio Code": "coding",
    "Xcode": "coding", "PyCharm": "coding",
    "IntelliJ IDEA": "coding", "WebStorm": "coding",
    "Sublime Text": "coding", "Nova": "coding",
    "Android Studio": "coding",
    "Terminal": "terminal", "iTerm2": "terminal",
    "iTerm": "terminal", "Alacritty": "terminal",
    "Warp": "terminal", "kitty": "terminal",
    "Slack": "communication", "Discord": "communication",
    "LINE": "communication", "Messages": "communication",
    "Telegram": "communication", "Microsoft Teams": "communication",
    "Mail": "communication", "Spark": "communication",
    "zoom.us": "meeting", "FaceTime": "meeting",
    "Figma": "design", "Sketch": "design",
    "Adobe Photoshop": "design", "Adobe Illustrator": "design",
    "Affinity Designer": "design", "Affinity Photo": "design",
    "Notion": "productivity", "Obsidian": "productivity",
    "Notes": "productivity", "Reminders": "productivity",
    "Calendar": "productivity", "Fantastical": "productivity",
    "Finder": "file_management",
    "Preview": "file_management",
    "Music": "media", "Spotify": "media",
    "QuickTime Player": "media", "IINA": "media",
    "Photos": "media",
    "System Settings": "system", "Activity Monitor": "system",
}


def categorize_url(url: str) -> str:
    """URLからカテゴリを判定"""
    if not url:
        return "unknown"
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        # 完全一致
        if hostname in _DOMAIN_CATEGORIES:
            return _DOMAIN_CATEGORIES[hostname]

        # サブドメイン一致（www.github.com → github.com）
        for domain, cat in _DOMAIN_CATEGORIES.items():
            if hostname.endswith("." + domain):
                return cat

        # パスヒント
        path_lower = parsed.path.lower()
        if any(kw in path_lower for kw in ("/api/", "/docs/", "/sdk/", "/reference/")):
            return "dev"
        if any(kw in path_lower for kw in ("/blog/", "/article/", "/post/")):
            return "news"

    except Exception:
        pass
    return "unknown"


def categorize_activity(app: str, url: str = "", title: str = "") -> str:
    """アプリ+URL+タイトルからアクティビティカテゴリを判定"""
    # URL優先（ブラウザの場合、URLの方がアプリ名より情報量が多い）
    if url:
        url_cat = categorize_url(url)
        if url_cat != "unknown":
            return url_cat

    # アプリ名
    if app in _APP_CATEGORIES:
        return _APP_CATEGORIES[app]

    # ブラウザだけどURL不明
    if app in ("Google Chrome", "Safari", "Firefox", "Arc"):
        return "browsing"

    return "unknown"


# =============================================================================
# 作業モード判定 + 集中スコア
# =============================================================================

def detect_work_mode(
    content_hashes: list[str],
    app_switches: int,
    unique_apps: int,
    duration_minutes: float,
) -> dict:
    """直近のデータから作業モードと集中スコアを算出

    Args:
        content_hashes: 直近の content_hash 列（変化頻度の計測用）
        app_switches: アプリ切替回数
        unique_apps: 使用アプリ数
        duration_minutes: 分析対象の時間幅（分）

    Returns:
        {"mode": str, "focus_score": int, "description": str}
    """
    if duration_minutes < 1:
        return {"mode": "starting", "focus_score": 50, "description": "作業開始直後"}

    # コンテンツ変化頻度（同じアプリ内でのタブ切替等）
    hash_changes = sum(
        1 for i in range(1, len(content_hashes))
        if content_hashes[i] != content_hashes[i - 1]
    )

    # 1分あたりの指標
    switches_per_min = app_switches / max(duration_minutes, 1)
    changes_per_min = hash_changes / max(duration_minutes, 1)

    # 集中スコア算出（100 = 完全集中、0 = 散漫）
    # アプリ切替が少ないほど集中
    switch_penalty = min(switches_per_min * 15, 60)
    # コンテンツ変化が適度なら集中（多すぎると散漫、少なすぎるとアイドル）
    if changes_per_min < 0.5:
        change_penalty = 10  # ほぼ変化なし→アイドル気味
    elif changes_per_min > 4:
        change_penalty = 20  # 変化多すぎ→散漫
    else:
        change_penalty = 0   # 適度な変化→集中
    # アプリ数が多いほど散漫
    app_penalty = max(0, (unique_apps - 2) * 8)

    focus_score = max(0, min(100, int(100 - switch_penalty - change_penalty - app_penalty)))

    # モード判定
    if app_switches <= 2 and unique_apps <= 2 and duration_minutes >= 10:
        mode = "deep_work"
        desc = "深い集中状態"
    elif switches_per_min < 0.5 and unique_apps <= 3:
        mode = "focused"
        desc = "集中して作業中"
    elif switches_per_min >= 1.0 and unique_apps >= 4:
        mode = "context_switching"
        desc = "頻繁にアプリを切り替え中"
    elif any(cat in ("communication", "meeting") for cat in []):
        mode = "collaborative"
        desc = "コミュニケーション中心"
    elif changes_per_min < 0.3 and switches_per_min < 0.3:
        mode = "idle"
        desc = "作業ペース低め"
    else:
        mode = "active"
        desc = "通常の作業ペース"

    return {"mode": mode, "focus_score": focus_score, "description": desc}


def summarize_app_usage(app_counts: Counter, total_seconds: float) -> list[dict]:
    """アプリ使用時間の要約（Tier 3分析用）"""
    result = []
    for app, count in app_counts.most_common(10):
        # countはスナップショット数、1スナップショット≈5秒
        seconds = count * 5
        pct = (seconds / max(total_seconds, 1)) * 100
        result.append({
            "app": app,
            "seconds": seconds,
            "percentage": round(pct, 1),
        })
    return result

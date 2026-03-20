#!/bin/bash
# 識（しき）起動スクリプト

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

TUNNEL_LOG="/tmp/cloudflare_tunnel_current.log"

echo "========================================="
echo "  識（しき）- 自己識別型環境統合制御体"
echo "========================================="

# .envチェック
if [ ! -f .env ]; then
    echo "ERROR: .envファイルが見つかりません"
    exit 1
fi

# パーミッション修正
chmod 600 .env
chmod 700 .ritsu 2>/dev/null || true

# 仮想環境チェック
if [ ! -d "venv" ]; then
    echo "仮想環境を作成中..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# 既存のcloudflaredを停止
pkill -f "cloudflared tunnel --url" 2>/dev/null || true
sleep 1

# Cloudflare Tunnelをバックグラウンドで起動
echo "Cloudflare Tunnel 起動中..."
nohup cloudflared tunnel --url http://localhost:8000 > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# トンネルURLが取得できるまで待機（最大15秒）
TUNNEL_URL=""
for i in $(seq 1 15); do
    TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
    sleep 1
done

echo ""
if [ -n "$TUNNEL_URL" ]; then
    echo "========================================="
    echo "  Tunnel URL: $TUNNEL_URL"
    echo "  Webhook:    ${TUNNEL_URL}/webhook"
    echo "========================================="
    echo ""
    echo "↑ LINE DevelopersのWebhook URLに設定してね"
else
    echo "WARNING: Tunnel URLの取得に失敗。ログ確認: cat $TUNNEL_LOG"
fi
echo ""
echo "ローカル: http://127.0.0.1:8000"
echo "ヘルス:   http://127.0.0.1:8000/health"
echo "Tunnel PID: $TUNNEL_PID"
echo ""

# クリーンアップ（Ctrl+Cでトンネルも停止）
cleanup() {
    echo ""
    echo "シャットダウン中..."
    kill $TUNNEL_PID 2>/dev/null
    wait $TUNNEL_PID 2>/dev/null
    echo "トンネル停止完了"
}
trap cleanup EXIT INT TERM

python main.py

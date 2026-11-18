#!/usr/bin/env bash
# =====================================================================
# Cài weather-assistant lên server mrbread.
# Chạy SAU KHI đã clone repo vào /var/www/projects/weather-assistant:
#     sudo bash /var/www/projects/weather-assistant/deploy/setup.sh
#
# Script lo: cài gói anthropic, tạo file secret từ mẫu, cài + bật systemd units.
# Phần cần làm tay: điền key/topic vào file secret, và mở Tailscale (xem cuối).
# =====================================================================
set -euo pipefail

REPO=/var/www/projects/weather-assistant
ENVFILE=/etc/weather-assistant.env
HERE=$(cd "$(dirname "$0")/.." && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "Hãy chạy bằng sudo: sudo bash $0"
    exit 1
fi

echo "==> 1/4 Cài gói anthropic (cho phần gợi ý AI)"
if python3 -c "import anthropic" 2>/dev/null; then
    echo "    Đã có sẵn anthropic."
elif pip3 install -q --break-system-packages anthropic 2>/dev/null \
     || pip3 install -q anthropic 2>/dev/null; then
    echo "    Cài anthropic xong."
else
    echo "    (không cài được anthropic — app vẫn chạy phần thời tiết, cài sau cũng được)"
fi

echo "==> 2/4 Tạo file secret nếu chưa có"
if [ -f "$ENVFILE" ]; then
    echo "    Đã có $ENVFILE, giữ nguyên."
else
    cp "$HERE/weather-assistant.env.example" "$ENVFILE"
    chmod 600 "$ENVFILE"
    echo "    Đã tạo $ENVFILE từ mẫu. NHỚ sửa điền NTFY_TOPIC và ANTHROPIC_API_KEY."
fi

echo "==> 3/4 Cài systemd units"
cp "$HERE/deploy/weather-app.service" \
   "$HERE/deploy/weather-notify.service" \
   "$HERE/deploy/weather-notify.timer" \
   "$HERE/deploy/weather-autopull.service" \
   "$HERE/deploy/weather-autopull.timer" /etc/systemd/system/
systemctl daemon-reload

echo "==> 4/4 Bật web + job nhắc mưa + auto-pull"
systemctl enable --now weather-app.service weather-notify.timer weather-autopull.timer

echo ""
echo "===================================================================="
echo "XONG phần tự động. Còn 2 việc làm tay:"
echo "  1) Điền secret:   sudo nano $ENVFILE"
echo "     rồi áp dụng:   sudo systemctl restart weather-app"
echo "  2) Mở HTTPS cho điện thoại (định vị GPS cần HTTPS):"
echo "        tailscale serve --bg 8100"
echo "        tailscale serve status"
echo "Kiểm tra web:        systemctl status weather-app --no-pager"
echo "===================================================================="

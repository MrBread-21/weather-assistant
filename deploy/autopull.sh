#!/usr/bin/env bash
# =====================================================================
# Auto-pull deploy cho app "weather-assistant" (mrbread).
# Chạy định kỳ qua systemd timer: weather-autopull.timer (mỗi 1 phút).
#
# Logic: fetch GitHub -> nếu main có commit mới -> git reset --hard
#        -> restart weather-app. State (vị trí, trạng thái cảnh báo) nằm ở
#        /var/lib/weather-assistant nên KHÔNG bị đụng.
#
# >>> Muốn đổi hành vi auto-pull: sửa file NÀY trên laptop rồi push lên main,
#     server sẽ tự cập nhật chính nó ở lần pull kế tiếp.
# =====================================================================
set -euo pipefail

REPO=/var/www/projects/weather-assistant
BRANCH=main
RUNAS=mrbread

runuser -u "$RUNAS" -- git -C "$REPO" fetch origin "$BRANCH" --quiet

LOCAL=$(runuser -u "$RUNAS" -- git -C "$REPO" rev-parse HEAD)
REMOTE=$(runuser -u "$RUNAS" -- git -C "$REPO" rev-parse "origin/$BRANCH")

if [ "$LOCAL" != "$REMOTE" ]; then
    runuser -u "$RUNAS" -- git -C "$REPO" reset --hard "origin/$BRANCH"
    systemctl restart weather-app
    logger -t autopull-weather "updated $LOCAL -> $REMOTE; restarted weather-app"
fi

# Weather Assistant (mrbread)

Trợ lý thời tiết chạy trên home server **mrbread**:

- **Thời tiết theo định vị**: điện thoại mở web, lấy GPS, hiện thời tiết hiện tại cùng dự báo (nguồn [Open-Meteo](https://open-meteo.com), miễn phí, không cần key).
- **Suy luận và gợi ý**: Claude gợi ý nên mặc gì, nên đi đâu, làm gì cho hợp thời tiết.
- **Nhắc nhở sắp mưa**: job nền kiểm tra mỗi 15 phút, nếu sắp mưa thì đẩy thông báo về điện thoại qua [ntfy](https://ntfy.sh), kèm bản tin sáng mỗi ngày.

## Cấu trúc

| File | Vai trò |
|---|---|
| `weatherlib.py` | Lõi: lấy thời tiết, phân tích mưa, gọi Claude, gửi ntfy |
| `app.py` | Web server (dashboard và API), cổng `8100` |
| `notify.py` | Job nền nhắc mưa (systemd timer gọi) |
| `static/` | Giao diện web (HTML/CSS/JS) |
| `deploy/` | systemd units, auto-pull, ví dụ nginx |
| `weather-assistant.env.example` | Mẫu file secret (API key, ntfy topic) |

## Chạy thử trên laptop

```bash
python app.py
# mở http://127.0.0.1:8100  (localhost được phép lấy GPS)
```

Muốn bật gợi ý AI khi thử thì đặt key trước khi chạy:
`export ANTHROPIC_API_KEY=...` (Windows PowerShell: `$env:ANTHROPIC_API_KEY="..."`).

## Cài trên server mrbread

```bash
# 1) Clone repo vào thư mục riêng, không nằm trong repo diem-danh
sudo git clone https://github.com/<bạn>/weather-assistant.git /var/www/projects/weather-assistant
sudo chown -R mrbread:mrbread /var/www/projects/weather-assistant

# 2) (tuỳ chọn) cài SDK để bật gợi ý AI
sudo apt install -y python3-pip && pip3 install anthropic

# 3) Tạo file secret
sudo cp /var/www/projects/weather-assistant/weather-assistant.env.example /etc/weather-assistant.env
sudo nano /etc/weather-assistant.env     # điền NTFY_TOPIC (bắt buộc), ANTHROPIC_API_KEY (nếu muốn AI)

# 4) Cài systemd units (web, job nhắc mưa, auto-pull)
cd /var/www/projects/weather-assistant/deploy
sudo cp weather-app.service weather-notify.service weather-notify.timer \
        weather-autopull.service weather-autopull.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now weather-app.service weather-notify.timer weather-autopull.timer

# 5) Mở HTTPS cho điện thoại (định vị GPS cần HTTPS), dễ nhất dùng Tailscale:
tailscale serve --bg 8100
tailscale serve status    # lấy link https://<máy>.<tailnet>.ts.net
```

## Trên điện thoại

1. Cài app **ntfy** (Android/iOS), chọn **Subscribe**, nhập đúng `NTFY_TOPIC` đã đặt ở bước 3.
2. Mở link HTTPS của web, cho phép **truy cập vị trí**, xem thời tiết và gợi ý.
3. Bấm **Bật nhắc nhở** để server lưu vị trí và nhắc bạn khi sắp mưa.

## Kiểm tra và vận hành

```bash
systemctl status weather-app            # web đang chạy?
journalctl -u weather-app -f            # log web
systemctl list-timers | grep weather    # timer nhắc mưa và auto-pull
sudo systemctl start weather-notify     # chạy thử 1 lần job nhắc mưa
journalctl -u weather-notify -n 20      # xem kết quả lần chạy
```

## Sửa và cập nhật

Sửa code trên laptop rồi `git push` lên `main`, server tự `git pull` và restart trong vòng 1 phút (nhờ `weather-autopull.timer`). Secret và state nằm ngoài repo nên không bị đụng.

> Nguồn thời tiết: Open-Meteo. Gợi ý: Claude (Anthropic). Thông báo: ntfy.

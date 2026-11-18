# App Android (file APK tải về cài)

App Android mỏng, bọc giao diện thời tiết chạy trên server mrbread bằng một WebView.
Nhờ vậy có icon riêng trong máy, mở phát vào thẳng, và lấy được GPS. Toàn bộ xử lý
(thời tiết, suy luận AI, nhắc mưa) vẫn nằm ở server; app chỉ là cửa sổ xem.

## Lấy file APK về

Không cần cài Android Studio. Mỗi lần có thay đổi trong `android/` và push lên GitHub,
GitHub Actions tự build ra file APK.

1. Vào repo trên GitHub, mở tab **Actions**.
2. Chọn lần chạy mới nhất của workflow **Build Android APK**, đợi nó xong (có dấu tick xanh).
3. Kéo xuống mục **Artifacts**, tải **weather-assistant-apk** về (là file zip chứa `app-debug.apk`).
4. Giải nén lấy `app-debug.apk`, chuyển sang điện thoại (hoặc tải thẳng trên điện thoại).

## Cài trên điện thoại Android

1. Mở file `app-debug.apk`, máy sẽ hỏi cho phép **cài app từ nguồn này** thì bật lên.
2. Cài xong, mở app **Thời tiết mrbread**, cho phép **truy cập vị trí**.
3. Điện thoại cần đang bật **Tailscale** (vì app mở nội dung từ server qua Tailscale).

## Ghi chú

- Thông báo mưa vẫn đến qua app **ntfy** như cũ, không nằm trong app này.
- Đổi địa chỉ server: sửa `APP_URL` trong
  `app/src/main/java/com/mrbread/weather/MainActivity.java` rồi push lại để build APK mới.
- Đây là bản **debug** (ký bằng khoá debug), cài cho cá nhân thì tốt; muốn phát hành
  rộng thì cần ký bằng khoá release riêng.

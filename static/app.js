/* Trợ lý thời tiết — logic phía trình duyệt (điện thoại).
   Luồng: lấy GPS, gọi /api/weather hiện ngay, rồi gọi /api/suggest (AI, chậm hơn).
   Nút "Bật nhắc nhở": gửi vị trí lên server để job nền kiểm tra mưa và đẩy ntfy.
   Lưu ý: navigator.geolocation chỉ chạy trên HTTPS (hoặc localhost).            */
"use strict";

let lastCoords = null;

const $ = (id) => document.getElementById(id);
const show = (id) => $(id).hidden = false;
const setStatus = (t) => $("status").textContent = t || "";

function fmtTemp(v) { return v == null ? "–" : Math.round(v); }

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  return r.json();
}

function renderWeather(w) {
  const n = w.now;
  $("temp").textContent = fmtTemp(n.temp);
  $("desc").textContent = n.desc || "";
  $("feels").textContent = n.feels != null ? `cảm giác ${Math.round(n.feels)}°C` : "";
  $("humidity").textContent = n.humidity != null ? `${n.humidity}%` : "–";
  $("wind").textContent = n.wind != null ? `${n.wind} km/h` : "–";
  $("summary").textContent = w.summary || "";
  show("nowCard");

  if (w.rain_soon && w.soon_text) {
    $("alertText").textContent = w.soon_text;
    show("alertCard");
  }
  if (w.windows && w.windows.length) {
    $("windows").innerHTML = w.windows
      .map((x) => `<li>${x.start}–${x.end} · xác suất ~${x.prob}%</li>`).join("");
    show("windowsCard");
  }
}

async function loadSuggestion(lat, lon) {
  show("suggestCard");
  try {
    const s = await getJSON(`/api/suggest?lat=${lat}&lon=${lon}`);
    if (s.ai_on && s.suggestion) {
      $("suggestText").textContent = s.suggestion;
    } else {
      $("suggestText").textContent =
        "Gợi ý AI đang tắt trên server (chưa đặt ANTHROPIC_API_KEY).";
    }
  } catch (e) {
    $("suggestText").textContent = "Chưa lấy được gợi ý (" + e.message + ").";
  }
}

async function loadFor(lat, lon) {
  lastCoords = { lat, lon };
  $("place").textContent = `Vị trí: ${lat.toFixed(3)}, ${lon.toFixed(3)}`;
  try {
    const w = await getJSON(`/api/weather?lat=${lat}&lon=${lon}`);
    renderWeather(w);
    show("enableBtn");
    loadSuggestion(lat, lon);  // không chặn, chạy song song
  } catch (e) {
    setStatus("Lỗi lấy thời tiết: " + e.message);
  }
}

function locate() {
  setStatus("");
  $("retryBtn").hidden = true;
  if (!navigator.geolocation) {
    $("place").textContent = "Trình duyệt không hỗ trợ định vị.";
    return;
  }
  $("place").textContent = "Đang xác định vị trí…";
  navigator.geolocation.getCurrentPosition(
    (pos) => loadFor(pos.coords.latitude, pos.coords.longitude),
    (err) => {
      $("place").textContent = "Không lấy được vị trí.";
      setStatus(err.code === 1
        ? "Bạn cần cho phép truy cập vị trí (và mở bằng HTTPS)."
        : "Lỗi định vị: " + err.message);
      $("retryBtn").hidden = false;
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
}

$("enableBtn").addEventListener("click", async () => {
  if (!lastCoords) return;
  setStatus("Đang lưu vị trí…");
  try {
    await getJSON("/api/location", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastCoords),
    });
    setStatus("Đã bật. Server sẽ nhắc bạn khi sắp mưa. Nhớ đăng ký topic ntfy trên điện thoại.");
  } catch (e) {
    setStatus("Lỗi lưu vị trí: " + e.message);
  }
});

$("retryBtn").addEventListener("click", locate);
locate();

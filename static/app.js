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

// Biểu tượng thời tiết (SVG nét, theo mã WMO) — không dùng emoji
function iconFor(code) {
  code = Number(code);
  const a = 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"';
  const sun = `<svg viewBox="0 0 24 24" ${a}><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>`;
  const cloud = `<svg viewBox="0 0 24 24" ${a}><path d="M7 18a4 4 0 0 1 .7-7.95 5.5 5.5 0 0 1 10.6 1.45A3.5 3.5 0 0 1 17.5 18H7z"/></svg>`;
  const rain = `<svg viewBox="0 0 24 24" ${a}><path d="M7 15a4 4 0 0 1 .7-7.95 5.5 5.5 0 0 1 10.6 1.45A3.5 3.5 0 0 1 17.5 15H7z"/><path d="M8 18l-1 2.5M12 18l-1 2.5M16 18l-1 2.5"/></svg>`;
  const storm = `<svg viewBox="0 0 24 24" ${a}><path d="M7 15a4 4 0 0 1 .7-7.95 5.5 5.5 0 0 1 10.6 1.45A3.5 3.5 0 0 1 17.5 15H7z"/><path d="M12 15l-2.2 4H13l-2.2 4"/></svg>`;
  if (code >= 95) return storm;
  if ((code >= 51 && code <= 67) || (code >= 71 && code <= 86)) return rain;
  if (code === 0 || code === 1) return sun;
  return cloud;
}

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
  $("wxIcon").innerHTML = iconFor(n.code);
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
    loadCalEvents(lat, lon);   // nạp sự kiện lịch nếu đã nối
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

// ---- Thói quen sinh hoạt ----
async function loadHabits() {
  try {
    const h = await getJSON("/api/habits");
    $("habitsText").value = h.habits || "";
  } catch (e) {
    // không sao, để trống
  }
  $("habitsCard").hidden = false;
}

$("saveHabitsBtn").addEventListener("click", async () => {
  const text = $("habitsText").value.trim();
  $("habitsStatus").textContent = "Đang lưu…";
  try {
    await getJSON("/api/habits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ habits: text }),
    });
    $("habitsStatus").textContent = "Đã lưu. Gợi ý sẽ bám theo thói quen của bạn.";
    if (lastCoords) loadSuggestion(lastCoords.lat, lastCoords.lon);  // làm mới gợi ý
  } catch (e) {
    $("habitsStatus").textContent = "Lỗi lưu: " + e.message;
  }
});

// ---- Lịch Google Calendar ----
let calConnected = false;

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderEvents(events) {
  const ul = $("calEvents");
  if (!events.length) {
    ul.innerHTML = "";
    $("calEmpty").hidden = false;
    return;
  }
  $("calEmpty").hidden = true;
  ul.innerHTML = events.map((e) => {
    const when = e.all_day ? "Cả ngày" : e.when_text;
    const loc = e.location ? " @ " + escapeHtml(e.location) : "";
    const wx = e.wx ? ` <span class="muted">— ${escapeHtml(e.wx)}</span>` : "";
    return `<li><b>${escapeHtml(when)}</b> ${escapeHtml(e.summary)}${loc}${wx}</li>`;
  }).join("");
}

async function loadCalEvents(lat, lon) {
  if (!calConnected) return;
  try {
    const d = await getJSON(`/api/gcal/events?lat=${lat}&lon=${lon}`);
    renderEvents(d.events || []);
  } catch (e) {
    // bỏ qua, không chặn luồng chính
  }
}

async function initCalendar() {
  // báo kết quả vừa đăng nhập (sau khi Google chuyển hướng về)
  const q = new URLSearchParams(location.search).get("gcal");
  if (q === "ok") $("calStatus").textContent = "Đã nối lịch thành công.";
  else if (q === "fail") $("calStatus").textContent = "Nối lịch thất bại, thử lại nhé.";

  let s;
  try {
    s = await getJSON("/api/gcal/status");
  } catch (e) {
    return;  // không lấy được trạng thái, ẩn luôn phần lịch
  }
  if (!s.configured) return;         // server chưa bật tính năng lịch, ẩn hẳn

  $("calCard").hidden = false;
  calConnected = s.connected;
  $("calConnected").hidden = !s.connected;
  $("calDisconnected").hidden = s.connected;
  if (s.connected && lastCoords) loadCalEvents(lastCoords.lat, lastCoords.lon);
}

$("calDisconnectBtn").addEventListener("click", async () => {
  try {
    await getJSON("/api/gcal/disconnect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    calConnected = false;
    $("calConnected").hidden = true;
    $("calDisconnected").hidden = false;
    $("calStatus").textContent = "Đã ngắt kết nối lịch.";
  } catch (e) {
    $("calStatus").textContent = "Lỗi ngắt kết nối: " + e.message;
  }
});

$("retryBtn").addEventListener("click", locate);
locate();
loadHabits();
initCalendar();

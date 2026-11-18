#!/usr/bin/env python3
"""Thư viện lõi cho app "Nhắc nhở thời tiết" (mrbread).

Gồm 4 việc, tách riêng để dễ đọc và dễ sửa:
  1) fetch_weather(lat, lon):  lấy thời tiết từ Open-Meteo (miễn phí, KHÔNG cần key).
  2) analyze(weather):         suy ra sắp mưa trong 1-2h chưa, hôm nay mưa khung giờ nào.
  3) ai_suggest(...):          gọi Claude gợi ý "nên đi đâu, làm gì" (cần ANTHROPIC_API_KEY).
  4) send_ntfy(...):           đẩy thông báo về điện thoại qua ntfy.

Chỉ dùng thư viện chuẩn của Python (urllib...) cho phần thời tiết + thông báo.
Riêng phần gợi ý AI dùng gói 'anthropic' (tuỳ chọn): thiếu gói hoặc thiếu key
thì app vẫn chạy bình thường, chỉ bỏ qua gợi ý AI.

Biến môi trường liên quan:
  ANTHROPIC_API_KEY   key gọi Claude (để trống = tắt gợi ý AI)
  ANTHROPIC_MODEL     model dùng (mặc định claude-opus-5-5)
  NTFY_SERVER         máy chủ ntfy (mặc định https://ntfy.sh)
  NTFY_TOPIC          "kênh" ntfy để nhận thông báo trên điện thoại
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------ cấu hình
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# Ngưỡng coi là "sắp mưa": xác suất mưa cao HOẶC lượng mưa đáng kể.
RAIN_PROB = 60       # % xác suất mưa
RAIN_MM = 0.3        # mm lượng mưa trong 15' / 1h
SOON_HOURS = 2       # "sắp tới" = trong vòng 2 giờ

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300     # nhớ tạm kết quả thời tiết 5' để khỏi gọi Open-Meteo liên tục


# ------------------------------------------------------- 1) lấy thời tiết
def fetch_weather(lat: float, lon: float) -> dict:
    """Gọi Open-Meteo lấy thời tiết hiện tại + dự báo theo giờ cho hôm nay.

    Trả về dict JSON thô của Open-Meteo (đã thêm khoá tiện dùng ở analyze()).
    Có nhớ tạm theo toạ độ (làm tròn) trong 5 phút.
    """
    key = f"{round(lat, 3)},{round(lon, 3)}"
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    query = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "precipitation,weather_code,wind_speed_10m",
        "hourly": "precipitation,precipitation_probability,temperature_2m,weather_code",
        "minutely_15": "precipitation,precipitation_probability",
        "forecast_days": 2,
        "timezone": "auto",
    })
    url = f"{OPEN_METEO}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "mrbread-weather/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    _cache[key] = (time.time(), data)
    return data


# --------------------------------------------------- mã thời tiết WMO sang chữ
_WMO = {
    0: "trời quang", 1: "ít mây", 2: "có mây", 3: "nhiều mây",
    45: "sương mù", 48: "sương mù đóng băng",
    51: "mưa phùn nhẹ", 53: "mưa phùn", 55: "mưa phùn nặng",
    61: "mưa nhẹ", 63: "mưa vừa", 65: "mưa to",
    66: "mưa lạnh", 67: "mưa lạnh nặng",
    71: "tuyết nhẹ", 73: "tuyết", 75: "tuyết dày",
    80: "mưa rào nhẹ", 81: "mưa rào", 82: "mưa rào dữ dội",
    95: "giông", 96: "giông kèm mưa đá", 99: "giông mưa đá lớn",
}


def describe_code(code: int) -> str:
    return _WMO.get(int(code), f"mã {code}")


def _parse(t: str) -> datetime:
    """'2026-11-18T14:00' thành datetime (giờ địa phương, Open-Meteo đã quy đổi)."""
    return datetime.fromisoformat(t)


# ------------------------------------------------------- 2) phân tích
def analyze(weather: dict) -> dict:
    """Suy ra các thông tin hữu ích từ dữ liệu Open-Meteo.

    Trả về:
      now:        mô tả hiện tại (nhiệt độ, trời ra sao...)
      rain_soon:  True nếu 1-2h tới sắp mưa (để bắn cảnh báo)
      soon_text:  câu mô tả ngắn chuyện sắp mưa (hoặc chuỗi rỗng)
      windows:    danh sách khung giờ mưa hôm nay [{start,end,prob}]
      signature:  "dấu vân tay" đợt mưa gần nhất (để chống gửi trùng)
    """
    cur = weather.get("current", {})
    now_dt = _parse(cur["time"]) if cur.get("time") else datetime.now()
    now = {
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "code": cur.get("weather_code"),
        "desc": describe_code(cur.get("weather_code", 0)),
        "precip": cur.get("precipitation"),
        "time": cur.get("time"),
    }

    # --- sắp mưa chưa? Ưu tiên dữ liệu 15 phút, không có thì dùng theo giờ ---
    rain_soon = False
    soon_text = ""
    m = weather.get("minutely_15") or {}
    times = m.get("time") or []
    precs = m.get("precipitation") or []
    probs = m.get("precipitation_probability") or []
    horizon = SOON_HOURS * 4  # mỗi 15', tức 4 ô mỗi giờ
    for t, p, pr in zip(times, precs, probs):
        dt = _parse(t)
        if dt < now_dt:
            continue
        if (dt - now_dt).total_seconds() > SOON_HOURS * 3600:
            break
        if (pr or 0) >= RAIN_PROB or (p or 0) >= RAIN_MM:
            rain_soon = True
            mins = max(0, round((dt - now_dt).total_seconds() / 60))
            when = "ngay bây giờ" if mins < 10 else f"khoảng {mins} phút nữa"
            soon_text = f"Sắp mưa {when} (xác suất ~{pr or 0}%)."
            break

    # --- các khung giờ mưa trong ~24h tới (gộp các giờ liền nhau) ---
    windows = []
    h = weather.get("hourly") or {}
    h_times = h.get("time") or []
    h_prob = h.get("precipitation_probability") or []
    h_prec = h.get("precipitation") or []
    open_w = None
    for t, pr, p in zip(h_times, h_prob, h_prec):
        dt = _parse(t)
        if dt < now_dt.replace(minute=0, second=0, microsecond=0):
            continue
        if (dt - now_dt).total_seconds() > 24 * 3600:
            break
        wet = (pr or 0) >= 50 or (p or 0) >= 0.2
        if wet:
            if open_w is None:
                open_w = {"start": dt, "end": dt, "prob": pr or 0}
            else:
                open_w["end"] = dt
                open_w["prob"] = max(open_w["prob"], pr or 0)
        elif open_w is not None:
            windows.append(open_w)
            open_w = None
    if open_w is not None:
        windows.append(open_w)

    windows_out = [{
        "start": w["start"].strftime("%Hh"),
        "end": (w["end"]).strftime("%Hh"),
        "start_iso": w["start"].isoformat(),
        "prob": w["prob"],
    } for w in windows]

    signature = windows_out[0]["start_iso"] if windows_out else "kho-rao"

    return {
        "now": now,
        "rain_soon": rain_soon,
        "soon_text": soon_text,
        "windows": windows_out,
        "signature": signature,
    }


def summary_text(analysis: dict) -> str:
    """Tóm tắt thời tiết thành 1 câu (cho bản tin sáng / log)."""
    now = analysis["now"]
    head = f"{now['desc']}, {now['temp']}°C (cảm giác {now['feels']}°C)."
    if analysis["windows"]:
        ws = ", ".join(f"{w['start']}–{w['end']} (~{w['prob']}%)" for w in analysis["windows"][:3])
        return f"{head} Hôm nay có thể mưa: {ws}. Nên mang ô."
    return f"{head} Hôm nay nhìn chung khô ráo."


def weather_at(weather: dict, dt) -> dict | None:
    """Thời tiết (xác suất mưa, lượng mưa, mô tả) tại giờ chứa dt (giờ địa phương).

    Trả None nếu giờ đó nằm ngoài tầm dự báo đang có.
    """
    h = weather.get("hourly") or {}
    times = h.get("time") or []
    prob = h.get("precipitation_probability") or []
    prec = h.get("precipitation") or []
    codes = h.get("weather_code") or []
    key = dt.strftime("%Y-%m-%dT%H")  # so khớp theo giờ
    for i, t in enumerate(times):
        if t.startswith(key):
            return {
                "prob": prob[i] if i < len(prob) else None,
                "precip": prec[i] if i < len(prec) else None,
                "desc": describe_code(codes[i]) if i < len(codes) else "",
            }
    return None


def event_wx_note(weather: dict, dt) -> str:
    """Câu ngắn mô tả thời tiết quanh giờ sự kiện, ví dụ 'mưa ~70%'. Rỗng nếu không rõ."""
    w = weather_at(weather, dt)
    if not w:
        return ""
    prob = w["prob"] or 0
    if prob >= 50 or (w["precip"] or 0) >= 0.3:
        return f"{w['desc']}, khả năng mưa ~{prob}%"
    return f"{w['desc']}"


# ---------------------------------- geocode địa điểm sự kiện (Nominatim, miễn phí)
_GEO_CACHE_FILE = Path(os.environ.get(
    "STATE_DIR", Path(__file__).resolve().parent / "state")) / "geocache.json"
_geo_mem: dict | None = None


def _geo_load() -> dict:
    global _geo_mem
    if _geo_mem is None:
        try:
            _geo_mem = json.loads(_GEO_CACHE_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            _geo_mem = {}
    return _geo_mem


def _geo_save(cache: dict) -> None:
    try:
        _GEO_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GEO_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def geocode(place: str):
    """Đổi địa chỉ/tên nơi thành (lat, lon, tên_ngắn). None nếu không tìm ra.

    Dùng Nominatim của OpenStreetMap (miễn phí). Có nhớ tạm ra file để đỡ gọi lại.
    """
    place = (place or "").strip()
    if not place:
        return None
    cache = _geo_load()
    if place in cache:
        c = cache[place]
        return tuple(c) if c else None

    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": place, "format": "json", "limit": 1, "accept-language": "vi"})
    req = urllib.request.Request(url, headers={"User-Agent": "mrbread-weather/1.0"})
    result = None
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data:
            it = data[0]
            short = (it.get("display_name", "").split(",")[0] or place).strip()
            result = (float(it["lat"]), float(it["lon"]), short)
    except Exception as e:
        print(f"[geocode] lỗi tra '{place}': {e}", flush=True)

    cache[place] = list(result) if result else None
    _geo_save(cache)
    return result


def event_weather(event: dict, fallback_weather: dict):
    """Thời tiết tại ĐÚNG địa điểm + giờ của sự kiện.

    Nếu sự kiện có địa điểm và geocode được thì lấy thời tiết tại nơi đó;
    nếu không thì dùng fallback_weather (vị trí đã lưu của người dùng).
    Trả dict {prob, precip, desc, place} hoặc None (sự kiện cả ngày / ngoài tầm).
    """
    if event.get("all_day"):
        return None
    dt = event["start"]
    loc = (event.get("location") or "").strip()
    if loc:
        geo = geocode(loc)
        if geo:
            try:
                w = fetch_weather(geo[0], geo[1])
                wx = weather_at(w, dt)
                if wx:
                    return {**wx, "place": geo[2]}
            except Exception as e:
                print(f"[event_weather] lỗi lấy thời tiết nơi sự kiện: {e}", flush=True)
    wx = weather_at(fallback_weather, dt)
    return {**wx, "place": "vị trí của bạn"} if wx else None


def wx_note_text(wx: dict | None) -> str:
    """Định dạng thông tin thời tiết sự kiện thành câu ngắn kèm tên nơi."""
    if not wx:
        return ""
    prob = wx.get("prob") or 0
    base = wx["desc"]
    if prob >= 50 or (wx.get("precip") or 0) >= 0.3:
        base += f", khả năng mưa ~{prob}%"
    place = wx.get("place")
    return base + (f" ({place})" if place else "")


# ------------------------------------------------------- 3) gợi ý bằng Claude
_SYSTEM = (
    "Bạn là trợ lý thời tiết thân thiện cho người dùng ở Việt Nam. "
    "Dựa trên thời tiết được cung cấp, đưa ra gợi ý NGẮN GỌN, thực tế: "
    "nên mặc hoặc mang gì, nên hay không nên ra ngoài, và 1-2 ý 'nên đi đâu, làm gì' "
    "hợp với thời tiết này. Nếu có thói quen sinh hoạt của người dùng, hãy cá nhân hoá: "
    "đối chiếu giờ họ hay ra ngoài hoặc hoạt động với lúc trời mưa và nhắc cụ thể. "
    "Nếu có lịch sắp tới, nêu rõ sự kiện nào bị thời tiết ảnh hưởng (mưa, nắng gắt) "
    "và nên chuẩn bị gì cho sự kiện đó. "
    "Viết tiếng Việt, giọng gần gũi, không lan man, không bịa số liệu ngoài dữ liệu đã cho."
)


def ai_suggest(analysis: dict, mode: str = "full", habits: str = "",
               events: list | None = None) -> str | None:
    """Gọi Claude để gợi ý 'nên đi đâu, làm gì'.

    mode='brief':  1-2 câu, hợp cho thông báo đẩy.
    mode='full':   vài câu kèm 1-2 gạch đầu dòng, hợp cho trang web.
    habits:        thói quen sinh hoạt của người dùng (chữ tự do) để cá nhân hoá.
    events:        danh sách sự kiện sắp tới, mỗi cái {when_text, summary, location?, wx?}.
    Trả về chuỗi gợi ý, hoặc None nếu không có key / chưa cài 'anthropic' / lỗi.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None

    now = analysis["now"]
    windows = analysis["windows"]
    facts = [
        f"Hiện tại: {now['desc']}, nhiệt độ {now['temp']}°C (cảm giác {now['feels']}°C), "
        f"độ ẩm {now['humidity']}%, gió {now['wind']} km/h.",
        f"Sắp mưa trong 1-2h tới: {'CÓ' if analysis['rain_soon'] else 'không'}."
        + (f" {analysis['soon_text']}" if analysis["soon_text"] else ""),
    ]
    if windows:
        facts.append("Khung giờ có thể mưa hôm nay: "
                     + ", ".join(f"{w['start']}–{w['end']} (~{w['prob']}%)" for w in windows[:4]) + ".")
    else:
        facts.append("Hôm nay nhìn chung khô ráo.")

    if habits and habits.strip():
        facts.append("Thói quen sinh hoạt của người dùng: " + habits.strip())

    if events:
        lines = []
        for e in events[:8]:
            loc = f" tại {e['location']}" if e.get("location") else ""
            wx = f" — thời tiết lúc đó: {e['wx']}" if e.get("wx") else ""
            lines.append(f"{e.get('when_text', '')} {e.get('summary', '')}{loc}{wx}")
        facts.append("Lịch sắp tới của người dùng:\n- " + "\n- ".join(lines))

    length = ("Trả lời tối đa 2 câu." if mode == "brief"
              else "Trả lời 2-3 câu, có thể kèm 1-2 gạch đầu dòng gợi ý nơi đến hoặc việc làm.")
    prompt = "\n".join(facts) + "\n\n" + length

    try:
        client = anthropic.Anthropic()  # tự đọc ANTHROPIC_API_KEY
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=400 if mode == "brief" else 1200,  # đủ chỗ để không bị cụt câu
            system=_SYSTEM,
            output_config={"effort": "low"},  # việc đơn giản nên để nhanh, rẻ
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:  # không để lỗi AI làm sập luồng thời tiết
        print(f"[ai_suggest] lỗi gọi Claude: {e}", flush=True)
        return None


# ------------------------------------------------------- 4) gửi ntfy
def send_ntfy(title: str, message: str, *, priority: str = "default",
              tags: str = "", topic: str | None = None) -> bool:
    """Đẩy 1 thông báo về điện thoại qua ntfy. Trả True nếu gửi được.

    title: tiêu đề (ntfy yêu cầu header ASCII nên để không dấu).
    message: nội dung (UTF-8, có dấu thoải mái).
    """
    topic = topic or NTFY_TOPIC
    if not topic:
        print("[ntfy] chưa đặt NTFY_TOPIC nên bỏ qua gửi", flush=True)
        return False
    url = f"{NTFY_SERVER}/{topic}"
    headers = {
        "Title": title.encode("ascii", "ignore").decode() or "Thoi tiet",
        "Priority": priority,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(url, data=message.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"[ntfy] lỗi gửi: {e}", flush=True)
        return False

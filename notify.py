#!/usr/bin/env python3
"""Job nền "nhắc nhở thời tiết" (mrbread) — chạy định kỳ qua systemd timer.

Mỗi lần chạy:
  1) Đọc vị trí mới nhất người dùng đã lưu (từ web, qua app.py).
  2) Lấy thời tiết rồi phân tích.
  3) Nếu SẮP MƯA (1-2h tới) và chưa báo đợt này thì đẩy cảnh báo kèm gợi ý về điện thoại.
  4) Buổi sáng (06-09h) mỗi ngày 1 lần thì đẩy "bản tin sáng" tóm tắt hôm nay.

Chống spam bằng file state: nhớ "dấu vân tay" đợt mưa đã báo và ngày đã gửi bản tin.
Chạy 1 lần rồi thoát (Type=oneshot). Không có vị trí thì thoát im lặng.

Biến môi trường: STATE_DIR (./state) + các biến NTFY_*/ANTHROPIC_* (xem weatherlib).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gcal
import weatherlib

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).resolve().parent / "state"))
LOCATION_FILE = STATE_DIR / "location.json"
ALERT_FILE = STATE_DIR / "alert_state.json"


def load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_alert_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ALERT_FILE)


def main() -> None:
    loc = load_json(LOCATION_FILE, {})
    if "lat" not in loc or "lon" not in loc:
        print("[notify] chưa có vị trí (mở web trên điện thoại để lưu) rồi thoát", flush=True)
        return

    weather = weatherlib.fetch_weather(loc["lat"], loc["lon"])
    analysis = weatherlib.analyze(weather)
    state = load_json(ALERT_FILE, {})
    habits = load_json(STATE_DIR / "habits.json", {}).get("habits", "")

    # Sự kiện lịch sắp tới (nếu đã nối Google Calendar), kèm ghi chú thời tiết
    raw_events = gcal.upcoming_events(hours_ahead=36) if gcal.is_connected() else []
    day_events = [{
        "when_text": e["when_text"], "summary": e["summary"],
        "location": e.get("location", ""),
        "wx": weatherlib.wx_note_text(weatherlib.event_weather(e, weather)),
    } for e in raw_events]

    now = analysis["now"]
    now_dt = weatherlib._parse(now["time"]) if now.get("time") else weatherlib.datetime.now()
    today = now_dt.date().isoformat()

    # ---------------------------------------------- 1) cảnh báo sắp mưa
    if analysis["rain_soon"]:
        sig = analysis["signature"]
        if state.get("last_signature") != sig:   # đợt mưa mới thì báo 1 lần
            tip = weatherlib.ai_suggest(analysis, mode="brief", habits=habits) or \
                "Mang theo áo mưa hoặc ô và tránh ra ngoài lúc mưa to nhé."
            msg = f"{analysis['soon_text']}\n\n{tip}"
            ok = weatherlib.send_ntfy("Sap mua", msg, priority="high",
                                      tags="umbrella,rain")
            print(f"[notify] cảnh báo mưa (sig={sig}) gửi={'OK' if ok else 'FAIL'}", flush=True)
            state["last_signature"] = sig
    else:
        # hết mưa thì xoá dấu để đợt sau còn báo lại
        state.pop("last_signature", None)

    # ---------------------------------------------- 2) bản tin sáng
    if 6 <= now_dt.hour < 9 and state.get("last_morning_date") != today:
        summary = weatherlib.summary_text(analysis)
        tip = weatherlib.ai_suggest(analysis, mode="full", habits=habits,
                                    events=day_events or None)
        body = summary + (f"\n\n{tip}" if tip else "")
        ok = weatherlib.send_ntfy("Ban tin sang", body, tags="sunny,calendar")
        print(f"[notify] bản tin sáng gửi={'OK' if ok else 'FAIL'}", flush=True)
        state["last_morning_date"] = today

    # ---------------------------------------------- 3) nhắc theo lịch (sắp mưa)
    now_utc = datetime.now(timezone.utc)
    notified = state.get("notified_events", {})
    for e in raw_events:
        if e["all_day"]:
            continue
        hours = (e["start"] - now_utc).total_seconds() / 3600
        if not (0 <= hours <= 12):          # chỉ nhắc sự kiện trong 12h tới
            continue
        wx = weatherlib.event_weather(e, weather)   # thời tiết tại đúng nơi sự kiện
        if not wx or ((wx.get("prob") or 0) < 50 and (wx.get("precip") or 0) < 0.3):
            continue                        # thời tiết lúc đó không đáng lo
        sig = e["start_iso"] + "|" + e["summary"]
        if sig in notified:
            continue                        # đã nhắc sự kiện này rồi
        wx_note = weatherlib.wx_note_text(wx)
        ev = [{"when_text": e["when_text"], "summary": e["summary"],
               "location": e.get("location", ""), "wx": wx_note}]
        tip = weatherlib.ai_suggest(analysis, mode="brief", habits=habits, events=ev)
        msg = tip or (f"Sự kiện \"{e['summary']}\" lúc {e['when_text']} có thể gặp mưa "
                      f"({wx_note}). Nhớ mang ô hoặc áo mưa.")
        ok = weatherlib.send_ntfy("Lich sap toi", msg, priority="high",
                                  tags="calendar,umbrella")
        print(f"[notify] nhắc sự kiện {sig} gửi={'OK' if ok else 'FAIL'}", flush=True)
        notified[sig] = today
    # chỉ giữ lại các lần đã nhắc trong 2 ngày gần nhất cho gọn
    cutoff = (now_dt.date() - timedelta(days=2)).isoformat()
    state["notified_events"] = {k: v for k, v in notified.items() if v >= cutoff}

    save_alert_state(state)


if __name__ == "__main__":
    main()
